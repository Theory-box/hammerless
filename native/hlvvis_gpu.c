/* hlvvis -gpu: parts of the vis on the GPU (Vulkan compute, any card with Vulkan 1.2; no ray tracing needed).
 * Vulkan comes with the graphics driver (vulkan-1.dll) and is loaded only when asked for; anything missing or failing
 * returns 0 and the caller does the work on the CPU. The GPU programs are in hlvvis_shaders/ (compiled into C headers
 * there by build.py). Every answer is the CPU's, bit for bit (checked by the tests): the shaders do the same float
 * operations in the same order with fused multiply-adds forbidden.
 * Work goes in chunks, each waited for, so no single GPU job runs long (Windows resets a GPU stuck over 2 seconds). */
#define VK_NO_PROTOTYPES
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>
#include "hlvvis_shaders/basevis.spv.h"
#include "hlvvis_shaders/fp64test.spv.h"
#include <math.h>

#include "hlvvis_gpu.h"

static PFN_vkGetInstanceProcAddr gipa;
#define VK_DECLARE(f) static PFN_##f f;
#define VK_INSTANCE_FUNCS(X) X(vkEnumeratePhysicalDevices) X(vkGetPhysicalDeviceProperties) \
    X(vkGetPhysicalDeviceQueueFamilyProperties) X(vkGetPhysicalDeviceMemoryProperties) X(vkCreateDevice) \
    X(vkGetDeviceProcAddr) X(vkGetPhysicalDeviceFeatures)
#define VK_DEVICE_FUNCS(X) X(vkGetDeviceQueue) X(vkCreateBuffer) X(vkDestroyBuffer) X(vkGetBufferMemoryRequirements) \
    X(vkAllocateMemory) X(vkFreeMemory) X(vkBindBufferMemory) X(vkMapMemory) \
    X(vkCreateCommandPool) X(vkAllocateCommandBuffers) X(vkBeginCommandBuffer) X(vkEndCommandBuffer) X(vkQueueSubmit) \
    X(vkQueueWaitIdle) X(vkCmdCopyBuffer) X(vkResetCommandBuffer) X(vkCmdPipelineBarrier) X(vkCreateShaderModule) \
    X(vkCreateDescriptorSetLayout) X(vkCreatePipelineLayout) X(vkCreateComputePipelines) X(vkCreateDescriptorPool) \
    X(vkAllocateDescriptorSets) X(vkUpdateDescriptorSets) X(vkCmdBindPipeline) X(vkCmdBindDescriptorSets) \
    X(vkCmdPushConstants) X(vkCmdDispatch)
static PFN_vkCreateInstance vkCreateInstance_;
VK_INSTANCE_FUNCS(VK_DECLARE)
VK_DEVICE_FUNCS(VK_DECLARE)

static VkDevice dev;
static VkPhysicalDevice phys;
static VkPhysicalDeviceMemoryProperties memprops;
static VkQueue queue;
static VkCommandBuffer cmd;
static char g_name[256];
static int g_failed;

#define TRYR(x, r) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { printf("Warning: GPU: %s failed (%d)\n", #x, (int)r_); \
    g_failed = 1; return r; } } while (0)

/* ------------------------------------------------------------------ buffers */
typedef struct { VkBuffer buf; VkDeviceMemory mem; void *map; VkDeviceSize size; } gbuf_t;
#define HOST (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)
#define USAGE (VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT)

static int new_buffer(gbuf_t *b, VkDeviceSize size, VkMemoryPropertyFlags want) {
    memset(b, 0, sizeof *b);
    b->size = size ? size : 16;
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = b->size, bi.usage = USAGE;
    TRYR(vkCreateBuffer(dev, &bi, NULL, &b->buf), 0);
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(dev, b->buf, &req);
    uint32_t type = ~0u;
    for (int pass = 0; pass < 2 && type == ~0u; pass++)        /* (host memory: cached first, for reading back) */
        for (uint32_t i = 0; i < memprops.memoryTypeCount; i++) {
            VkMemoryPropertyFlags f = memprops.memoryTypes[i].propertyFlags;
            if (!(req.memoryTypeBits & (1u << i)) || (f & want) != want) continue;
            if (pass == 0 && (want & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) && !(f & VK_MEMORY_PROPERTY_HOST_CACHED_BIT)) continue;
            type = i;
            break;
        }
    if (type == ~0u) { printf("Warning: GPU: no memory for a %llu byte buffer\n", (unsigned long long)size); g_failed = 1; return 0; }
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = req.size, ai.memoryTypeIndex = type;
    TRYR(vkAllocateMemory(dev, &ai, NULL, &b->mem), 0);
    TRYR(vkBindBufferMemory(dev, b->buf, b->mem, 0), 0);
    if (want & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) TRYR(vkMapMemory(dev, b->mem, 0, VK_WHOLE_SIZE, 0, &b->map), 0);
    return 1;
}
static void free_buffer(gbuf_t *b) {
    if (b->buf) vkDestroyBuffer(dev, b->buf, NULL), vkFreeMemory(dev, b->mem, NULL);
    memset(b, 0, sizeof *b);
}
static int begin(void) {
    VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    TRYR(vkResetCommandBuffer(cmd, 0), 0);
    TRYR(vkBeginCommandBuffer(cmd, &bi), 0);
    return 1;
}
static int submit(void) {
    TRYR(vkEndCommandBuffer(cmd), 0);
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1, si.pCommandBuffers = &cmd;
    TRYR(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE), 0);
    TRYR(vkQueueWaitIdle(queue), 0);
    return 1;
}
/* data the GPU keeps in its own memory (through a host buffer) */
static int upload(gbuf_t *b, const void *data, VkDeviceSize size) {
    gbuf_t st;
    if (!new_buffer(&st, size, HOST)) return 0;
    memcpy(st.map, data, size);
    if (!new_buffer(b, size, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) return 0;
    if (!begin()) return 0;
    VkBufferCopy c = {0, 0, size};
    vkCmdCopyBuffer(cmd, st.buf, b->buf, 1, &c);
    if (!submit()) return 0;
    free_buffer(&st);
    return 1;
}

/* ------------------------------------------------------------------ programs: storage buffers 0 .. nbind-1 */
typedef struct { VkDescriptorSetLayout dl; VkPipelineLayout pl; VkPipeline pipe; VkDescriptorSet ds; int nbind, ok; } program_t;
static program_t program(const uint32_t *code, size_t codesize, int nbind, uint32_t pcsize) {
    program_t p = {0};
    p.nbind = nbind;
    VkShaderModuleCreateInfo smi = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    smi.codeSize = codesize, smi.pCode = code;
    VkShaderModule sm;
    TRYR(vkCreateShaderModule(dev, &smi, NULL, &sm), p);
    VkDescriptorSetLayoutBinding b[16];
    for (int i = 0; i < nbind; i++) {
        b[i].binding = (uint32_t)i, b[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        b[i].descriptorCount = 1, b[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT, b[i].pImmutableSamplers = NULL;
    }
    VkDescriptorSetLayoutCreateInfo dli = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    dli.bindingCount = (uint32_t)nbind, dli.pBindings = b;
    TRYR(vkCreateDescriptorSetLayout(dev, &dli, NULL, &p.dl), p);
    VkPushConstantRange pcr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, pcsize};
    VkPipelineLayoutCreateInfo pli = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pli.setLayoutCount = 1, pli.pSetLayouts = &p.dl, pli.pushConstantRangeCount = 1, pli.pPushConstantRanges = &pcr;
    TRYR(vkCreatePipelineLayout(dev, &pli, NULL, &p.pl), p);
    VkComputePipelineCreateInfo cpci = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    cpci.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    cpci.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT, cpci.stage.module = sm, cpci.stage.pName = "main";
    cpci.layout = p.pl;
    TRYR(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpci, NULL, &p.pipe), p);
    VkDescriptorPoolSize ps = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, (uint32_t)nbind};
    VkDescriptorPoolCreateInfo dpi = {VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dpi.maxSets = 1, dpi.poolSizeCount = 1, dpi.pPoolSizes = &ps;
    VkDescriptorPool pool;
    TRYR(vkCreateDescriptorPool(dev, &dpi, NULL, &pool), p);
    VkDescriptorSetAllocateInfo dsa = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    dsa.descriptorPool = pool, dsa.descriptorSetCount = 1, dsa.pSetLayouts = &p.dl;
    TRYR(vkAllocateDescriptorSets(dev, &dsa, &p.ds), p);
    p.ok = 1;
    return p;
}
static void bind_buffers(program_t *p, gbuf_t *const *bufs) {
    VkDescriptorBufferInfo bi[16];
    VkWriteDescriptorSet w[16];
    memset(w, 0, sizeof w);
    for (int i = 0; i < p->nbind; i++) {
        bi[i].buffer = bufs[i]->buf, bi[i].offset = 0, bi[i].range = VK_WHOLE_SIZE;
        w[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        w[i].dstSet = p->ds, w[i].dstBinding = (uint32_t)i, w[i].descriptorCount = 1;
        w[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, w[i].pBufferInfo = &bi[i];
    }
    vkUpdateDescriptorSets(dev, (uint32_t)p->nbind, w, 0, NULL);
}

/* ------------------------------------------------------------------ start */
static program_t prog_basevis, prog_fp64;
static int g_fp64;

int vgpu_init(void) {
    static int tried, ok;
    if (tried) return ok;
    tried = 1;
    HMODULE vk = LoadLibraryA("vulkan-1.dll");
    if (!vk) { printf("Warning: no Vulkan (vulkan-1.dll): vis on the CPU\n"); return 0; }
    gipa = (PFN_vkGetInstanceProcAddr)(void *)GetProcAddress(vk, "vkGetInstanceProcAddr");
    vkCreateInstance_ = gipa ? (PFN_vkCreateInstance)gipa(NULL, "vkCreateInstance") : NULL;
    if (!vkCreateInstance_) { printf("Warning: Vulkan didn't start: vis on the CPU\n"); return 0; }
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.pApplicationName = "hlvvis", app.apiVersion = VK_API_VERSION_1_2;
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ici.pApplicationInfo = &app;
    VkInstance inst;
    if (vkCreateInstance_(&ici, NULL, &inst) != VK_SUCCESS) { printf("Warning: Vulkan didn't start: vis on the CPU\n"); return 0; }
#define VK_LOAD_I(f) f = (PFN_##f)gipa(inst, #f);
    VK_INSTANCE_FUNCS(VK_LOAD_I)
    uint32_t np = 0;
    vkEnumeratePhysicalDevices(inst, &np, NULL);
    VkPhysicalDevice *pds = malloc(sizeof(*pds) * (np + 1));
    vkEnumeratePhysicalDevices(inst, &np, pds);
    int best = -1, bestScore = -1;
    for (uint32_t i = 0; i < np; i++) {
        VkPhysicalDeviceProperties p;
        vkGetPhysicalDeviceProperties(pds[i], &p);
        if (p.apiVersion < VK_API_VERSION_1_2) continue;
        int score = p.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ? 2 : 1;
        if (score > bestScore) best = (int)i, bestScore = score;
    }
    if (best < 0) { free(pds); printf("Warning: no Vulkan 1.2 graphics card: vis on the CPU\n"); return 0; }
    phys = pds[best];
    free(pds);
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(phys, &props);
    snprintf(g_name, sizeof g_name, "%s", props.deviceName);
    vkGetPhysicalDeviceMemoryProperties(phys, &memprops);
    uint32_t nq = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, NULL);
    VkQueueFamilyProperties *qf = malloc(sizeof(*qf) * (nq + 1));
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, qf);
    uint32_t qfam = 0;
    for (uint32_t i = 0; i < nq; i++) if (qf[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { qfam = i; break; }
    free(qf);
    float prio = 1;
    VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qci.queueFamilyIndex = qfam, qci.queueCount = 1, qci.pQueuePriorities = &prio;
    VkPhysicalDeviceFeatures have, want;
    vkGetPhysicalDeviceFeatures(phys, &have);
    memset(&want, 0, sizeof want);
    want.shaderFloat64 = have.shaderFloat64;             /* (the clipping's doubles) */
    g_fp64 = have.shaderFloat64;
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dci.queueCreateInfoCount = 1, dci.pQueueCreateInfos = &qci, dci.pEnabledFeatures = &want;
    if (vkCreateDevice(phys, &dci, NULL, &dev) != VK_SUCCESS) { printf("Warning: the graphics card didn't start: vis on the CPU\n"); return 0; }
#define VK_LOAD_D(f) if (!(f = (PFN_##f)vkGetDeviceProcAddr(dev, #f))) { printf("Warning: Vulkan has no " #f ": vis on the CPU\n"); return 0; }
    VK_DEVICE_FUNCS(VK_LOAD_D)
    vkGetDeviceQueue(dev, qfam, 0, &queue);
    VkCommandPoolCreateInfo cpi = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cpi.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, cpi.queueFamilyIndex = qfam;
    VkCommandPool pool;
    TRYR(vkCreateCommandPool(dev, &cpi, NULL, &pool), 0);
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    cai.commandPool = pool, cai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, cai.commandBufferCount = 1;
    TRYR(vkAllocateCommandBuffers(dev, &cai, &cmd), 0);
    prog_basevis = program(spv_basevis, sizeof(spv_basevis), 3, 20);
    if (!prog_basevis.ok) { printf("Warning: the graphics card's driver didn't take hlvvis's GPU programs: vis on the CPU\n"); return 0; }
    if (g_fp64) prog_fp64 = program(spv_fp64test, sizeof(spv_fp64test), 4, 4);
    ok = 1;
    return 1;
}

const char *vgpu_name(void) { return g_name; }

/* ------------------------------------------------------------------ the rough pass's plane tests */
int vgpu_front(int npd, const float *pts, int npts, const vgpu_portal_t *portals, float eps_up, int w32,
               void (*store)(int row, const uint32_t *words, void *ctx), void *ctx) {
    if (!vgpu_init()) return 0;
    gbuf_t gpts, gportals, out, back;
    if (!upload(&gpts, pts, (VkDeviceSize)npts * 16) || !upload(&gportals, portals, (VkDeviceSize)npd * sizeof(vgpu_portal_t)))
        return 0;
    /* rows per job: at most 64 MB of answers, and few enough tests per job to stay well under a second */
    long long per_row = (long long)npd * 8;                    /* (about: tests of 8 points per pair) */
    int rows = (int)(((64ll << 20) / 4) / (w32 > 0 ? w32 : 1));
    long long cap = 4000000000ll / (per_row > 0 ? per_row : 1);
    if (rows > cap) rows = (int)cap;
    if (rows < 1) rows = 1;
    if (rows > npd) rows = npd;
    VkDeviceSize bytes = (VkDeviceSize)rows * w32 * 4;
    if (!new_buffer(&out, bytes, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) || !new_buffer(&back, bytes, HOST)) return 0;
    gbuf_t *bufs[3] = {&gpts, &gportals, &out};
    bind_buffers(&prog_basevis, bufs);
    for (int row0 = 0; row0 < npd; row0 += rows) {
        int n = npd - row0 < rows ? npd - row0 : rows;
        struct { uint32_t npd, row0, rows, w32; float eps_up; } pc = {(uint32_t)npd, (uint32_t)row0, (uint32_t)n, (uint32_t)w32, eps_up};
        if (!begin()) return 0;
        vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_basevis.pipe);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_basevis.pl, 0, 1, &prog_basevis.ds, 0, NULL);
        vkCmdPushConstants(cmd, prog_basevis.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof pc, &pc);
        uint32_t items = (uint32_t)n * (uint32_t)w32;
        vkCmdDispatch(cmd, (items + 63) / 64, 1, 1);
        VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        mb.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT, mb.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
        VkBufferCopy c = {0, 0, (VkDeviceSize)n * w32 * 4};
        vkCmdCopyBuffer(cmd, out.buf, back.buf, 1, &c);
        if (!submit()) return 0;
        for (int r = 0; r < n; r++) store(row0 + r, (const uint32_t *)back.map + (size_t)r * w32, ctx);
    }
    free_buffer(&gpts); free_buffer(&gportals); free_buffer(&out); free_buffer(&back);
    return !g_failed;
}

/* (self-test) the GPU's double a / b, sqrt(a) and (float)(1 / sqrt(len)) against the CPU's, bit for bit, over n random
   inputs; prints the mismatches. Returns the number of mismatches, -1 if it couldn't run. */
int vgpu_fp64_selftest(int n) {
    if (!vgpu_init()) return -1;
    if (!g_fp64 || !prog_fp64.ok) { printf("GPU self-test: no double precision on this card\n"); return -1; }
    double *ab = malloc((size_t)n * 16);
    float *lens = malloc((size_t)n * 4);
    uint64_t x = 88172645463325252ull;
    for (int i = 0; i < n; i++) {
        #define RND() (x ^= x << 13, x ^= x >> 7, x ^= x << 17, x)
        double a = ldexp((double)(RND() >> 11) / 9007199254740992.0 + 0.5, (int)(RND() % 40) - 20);
        double b = ldexp((double)(RND() >> 11) / 9007199254740992.0 + 0.5, (int)(RND() % 40) - 20);
        if (RND() & 1) b = -b;
        ab[2 * i] = a, ab[2 * i + 1] = b;
        lens[i] = (float)ldexp((double)(RND() >> 40) / 16777216.0 + 0.01, (int)(RND() % 30) - 10);
    }
    gbuf_t gin, ginf, gout, goutf, back, backf;
    if (!upload(&gin, ab, (VkDeviceSize)n * 16) || !upload(&ginf, lens, (VkDeviceSize)n * 4) ||
        !new_buffer(&gout, (VkDeviceSize)n * 16, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) ||
        !new_buffer(&goutf, (VkDeviceSize)n * 4, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) ||
        !new_buffer(&back, (VkDeviceSize)n * 16, HOST) || !new_buffer(&backf, (VkDeviceSize)n * 4, HOST)) return -1;
    gbuf_t *bufs[4] = {&gin, &ginf, &gout, &goutf};
    bind_buffers(&prog_fp64, bufs);
    uint32_t pc = (uint32_t)n;
    if (!begin()) return -1;
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_fp64.pipe);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_fp64.pl, 0, 1, &prog_fp64.ds, 0, NULL);
    vkCmdPushConstants(cmd, prog_fp64.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, 4, &pc);
    vkCmdDispatch(cmd, ((uint32_t)n + 63) / 64, 1, 1);
    VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    mb.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT, mb.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
    VkBufferCopy c1 = {0, 0, (VkDeviceSize)n * 16}, c2 = {0, 0, (VkDeviceSize)n * 4};
    vkCmdCopyBuffer(cmd, gout.buf, back.buf, 1, &c1);
    vkCmdCopyBuffer(cmd, goutf.buf, backf.buf, 1, &c2);
    if (!submit()) return -1;
    const double *q = back.map; const float *inv = backf.map;
    long long bad_div = 0, bad_sqrt = 0, bad_inv = 0;
    for (int i = 0; i < n; i++) {
        double d = ab[2 * i] / ab[2 * i + 1], sq = sqrt(ab[2 * i]);
        float iv = (float)(1.0 / sqrt((double)lens[i]));
        if (memcmp(&d, &q[2 * i], 8)) { if (bad_div++ < 3) printf("  div %.17g / %.17g: CPU %.17g GPU %.17g\n", ab[2*i], ab[2*i+1], d, q[2*i]); }
        if (memcmp(&sq, &q[2 * i + 1], 8)) { if (bad_sqrt++ < 3) printf("  sqrt %.17g: CPU %.17g GPU %.17g\n", ab[2*i], sq, q[2*i+1]); }
        if (memcmp(&iv, &inv[i], 4)) { if (bad_inv++ < 3) printf("  1/sqrt %.9g: CPU %.9g GPU %.9g\n", lens[i], iv, inv[i]); }
    }
    printf("GPU self-test (%s), %d random inputs: a / b %lld differ, sqrt %lld differ, (float)(1 / sqrt(len)) %lld differ\n",
           g_name, n, bad_div, bad_sqrt, bad_inv);
    free_buffer(&gin); free_buffer(&ginf); free_buffer(&gout); free_buffer(&goutf); free_buffer(&back); free_buffer(&backf);
    free(ab); free(lens);
    return (int)(bad_div + bad_sqrt + bad_inv);
}
