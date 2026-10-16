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
#include "hlvvis_shaders/expand.spv.h"
#include "hlvvis_shaders/mergetest.spv.h"
#include "hlvvis_shaders/pipe_refill.spv.h"
#include "hlvvis_shaders/pipe_compact.spv.h"
#include "hlvvis_shaders/pipe_scan1.spv.h"
#include "hlvvis_shaders/pipe_snap.spv.h"
#include "hlvvis_shaders/pipe_scan2.spv.h"
#include "hlvvis_shaders/pipe_expand.spv.h"
#include "hlvvis_shaders/pipe_args.spv.h"
#include "hlvvis_shaders/pipe_merge.spv.h"
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
    X(vkCmdPushConstants) X(vkCmdDispatch) X(vkCmdDispatchIndirect) X(vkCmdFillBuffer) \
    X(vkCreateQueryPool) X(vkCmdResetQueryPool) X(vkCmdWriteTimestamp) X(vkGetQueryPoolResults)
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
static int g_stats;                   /* (dev) HL_GPU_STATS=1: the driver's statistics of each program (registers, memory) */
static PFN_vkGetPipelineExecutablePropertiesKHR vkGetPipelineExecutablePropertiesKHR_;
static PFN_vkGetPipelineExecutableStatisticsKHR vkGetPipelineExecutableStatisticsKHR_;

#define TRYR(x, r) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { printf("Warning: GPU: %s failed (%d)\n", #x, (int)r_); \
    g_failed = 1; return r; } } while (0)

/* ------------------------------------------------------------------ buffers */
typedef struct { VkBuffer buf; VkDeviceMemory mem; void *map; VkDeviceSize size; } gbuf_t;
#define HOST (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)
#define USAGE (VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT | \
               VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT)

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
    VkDescriptorSetLayoutBinding b[24];
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
    if (g_stats) cpci.flags = VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR;
    TRYR(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpci, NULL, &p.pipe), p);
    if (g_stats) {
        VkPipelineInfoKHR pi = {VK_STRUCTURE_TYPE_PIPELINE_INFO_KHR};
        pi.pipeline = p.pipe;
        uint32_t ne = 0;
        vkGetPipelineExecutablePropertiesKHR_(dev, &pi, &ne, NULL);
        for (uint32_t e = 0; e < ne; e++) {
            VkPipelineExecutableInfoKHR ei = {VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_INFO_KHR};
            ei.pipeline = p.pipe, ei.executableIndex = e;
            VkPipelineExecutableStatisticKHR st[32];
            uint32_t n = 32;
            for (uint32_t k = 0; k < n; k++) st[k].sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_STATISTIC_KHR, st[k].pNext = NULL;
            vkGetPipelineExecutableStatisticsKHR_(dev, &ei, &n, st);
            printf("GPU program (%u words):", (unsigned)(codesize / 4));
            for (uint32_t k = 0; k < n; k++) {
                if (st[k].format == VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_UINT64_KHR) printf(" %s %llu;", st[k].name, (unsigned long long)st[k].value.u64);
                else if (st[k].format == VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_INT64_KHR) printf(" %s %lld;", st[k].name, (long long)st[k].value.i64);
                else if (st[k].format == VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_FLOAT64_KHR) printf(" %s %g;", st[k].name, st[k].value.f64);
            }
            printf("\n");
        }
    }
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
    VkDescriptorBufferInfo bi[24];
    VkWriteDescriptorSet w[24];
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
static program_t prog_basevis, prog_fp64, prog_expand, prog_mtest;
#define PIPE_NBIND 23
enum { PK_REFILL, PK_COMPACT, PK_SCAN1, PK_SNAP, PK_SCAN2, PK_EXPAND, PK_ARGS, PK_MERGE, PK_N };
static program_t prog_pipe[PK_N];
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
    if (getenv("HL_GPU_ROBUST")) want.robustBufferAccess = have.robustBufferAccess;   /* (dev) out-of-range reads 0, writes dropped */
    g_fp64 = have.shaderFloat64;
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dci.queueCreateInfoCount = 1, dci.pQueueCreateInfos = &qci, dci.pEnabledFeatures = &want;
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR pef = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    const char *ext_stats = VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME;
    if (getenv("HL_GPU_STATS")) {
        pef.pipelineExecutableInfo = VK_TRUE;
        dci.pNext = &pef, dci.enabledExtensionCount = 1, dci.ppEnabledExtensionNames = &ext_stats;
        g_stats = 1;
    }
    if (vkCreateDevice(phys, &dci, NULL, &dev) != VK_SUCCESS) { printf("Warning: the graphics card didn't start: vis on the CPU\n"); return 0; }
#define VK_LOAD_D(f) if (!(f = (PFN_##f)vkGetDeviceProcAddr(dev, #f))) { printf("Warning: Vulkan has no " #f ": vis on the CPU\n"); return 0; }
    VK_DEVICE_FUNCS(VK_LOAD_D)
    if (g_stats) {
        vkGetPipelineExecutablePropertiesKHR_ = (PFN_vkGetPipelineExecutablePropertiesKHR)vkGetDeviceProcAddr(dev, "vkGetPipelineExecutablePropertiesKHR");
        vkGetPipelineExecutableStatisticsKHR_ = (PFN_vkGetPipelineExecutableStatisticsKHR)vkGetDeviceProcAddr(dev, "vkGetPipelineExecutableStatisticsKHR");
        if (!vkGetPipelineExecutablePropertiesKHR_ || !vkGetPipelineExecutableStatisticsKHR_) g_stats = 0;
    }
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
    if (g_fp64) prog_expand = program(spv_expand, sizeof(spv_expand), 8, 24);
    if (g_fp64) prog_mtest = program(spv_mergetest, sizeof(spv_mergetest), 3, 8);
    if (g_fp64) prog_pipe[PK_REFILL] = program(spv_pipe_refill, sizeof(spv_pipe_refill), PIPE_NBIND, 64);
    if (g_fp64) prog_pipe[PK_COMPACT] = program(spv_pipe_compact, sizeof(spv_pipe_compact), PIPE_NBIND, 64);
    if (g_fp64) prog_pipe[PK_SCAN1] = program(spv_pipe_scan1, sizeof(spv_pipe_scan1), PIPE_NBIND, 64);
    if (g_fp64) prog_pipe[PK_SNAP] = program(spv_pipe_snap, sizeof(spv_pipe_snap), PIPE_NBIND, 64);
    if (g_fp64) prog_pipe[PK_SCAN2] = program(spv_pipe_scan2, sizeof(spv_pipe_scan2), PIPE_NBIND, 64);
    if (g_fp64) prog_pipe[PK_EXPAND] = program(spv_pipe_expand, sizeof(spv_pipe_expand), PIPE_NBIND, 64);
    if (g_fp64) prog_pipe[PK_ARGS] = program(spv_pipe_args, sizeof(spv_pipe_args), PIPE_NBIND, 64);
    if (g_fp64) prog_pipe[PK_MERGE] = program(spv_pipe_merge, sizeof(spv_pipe_merge), PIPE_NBIND, 64);
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

/* ------------------------------------------------------------------ -jacobi's expansion steps */
static gbuf_t e_pts, e_portals;                       /* (device: once) */
static gbuf_t e_items, e_snappts, e_snapn, e_src, e_pass, e_n;   /* (host: grown as needed) */
static float e_eps_up, e_len_lo;
static uint32_t e_robust, e_maxchop;

static int grow(gbuf_t *b, VkDeviceSize size) {
    if (b->buf && b->size >= size) return 1;
    free_buffer(b);
    return new_buffer(b, size + size / 2 + 4096, HOST);
}

int vgpu_expand_setup(int npd, const float *pts, int npts, const vgpu_eportal_t *portals, float eps_up, float len_lo,
                      int robust, int maxchop) {
    if (!vgpu_init() || !prog_expand.ok) return 0;
    if (!upload(&e_pts, pts, (VkDeviceSize)npts * 16) || !upload(&e_portals, portals, (VkDeviceSize)npd * sizeof(vgpu_eportal_t)))
        return 0;
    e_eps_up = eps_up, e_len_lo = len_lo, e_robust = (uint32_t)robust, e_maxchop = (uint32_t)maxchop;
    return 1;
}

int vgpu_expand(int nitems, const vgpu_item_t *items, int nsnap, const float *snap_pts, const uint32_t *snap_n,
                float *out_src, float *out_pass, uint32_t *out_n) {
    if (!grow(&e_items, (VkDeviceSize)nitems * sizeof(vgpu_item_t)) ||
        !grow(&e_snappts, (VkDeviceSize)nsnap * 2 * VGPU_SNAPCAP * 16) || !grow(&e_snapn, (VkDeviceSize)nsnap * 2 * 4) ||
        !grow(&e_src, (VkDeviceSize)nitems * VGPU_MAXW * 16) || !grow(&e_pass, (VkDeviceSize)nitems * VGPU_MAXW * 16) ||
        !grow(&e_n, (VkDeviceSize)nitems * 16)) return 0;
    memcpy(e_items.map, items, (size_t)nitems * sizeof(vgpu_item_t));
    memcpy(e_snappts.map, snap_pts, (size_t)nsnap * 2 * VGPU_SNAPCAP * 16);
    memcpy(e_snapn.map, snap_n, (size_t)nsnap * 2 * 4);
    gbuf_t *bufs[8] = {&e_pts, &e_portals, &e_items, &e_snappts, &e_snapn, &e_src, &e_pass, &e_n};
    bind_buffers(&prog_expand, bufs);
    struct { uint32_t nitems; float eps_up, len_lo; uint32_t robust, snapcap, maxchop; } pc =
        {(uint32_t)nitems, e_eps_up, e_len_lo, e_robust, VGPU_SNAPCAP, e_maxchop};
    if (!begin()) return 0;
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_expand.pipe);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_expand.pl, 0, 1, &prog_expand.ds, 0, NULL);
    vkCmdPushConstants(cmd, prog_expand.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof pc, &pc);
    vkCmdDispatch(cmd, ((uint32_t)nitems + 63) / 64, 1, 1);
    if (!submit()) return 0;
    memcpy(out_n, e_n.map, (size_t)nitems * 16);
    for (int k = 0; k < nitems; k++) {
        if (!out_n[4 * k]) continue;
        memcpy(out_src + (size_t)k * VGPU_MAXW * 4, (const float *)e_src.map + (size_t)k * VGPU_MAXW * 4, (size_t)out_n[4 * k + 1] * 16);
        memcpy(out_pass + (size_t)k * VGPU_MAXW * 4, (const float *)e_pass.map + (size_t)k * VGPU_MAXW * 4, (size_t)out_n[4 * k + 2] * 16);
    }
    return 1;
}

int vgpu_merge_test(int n, const vgpu_mrec_t *recs, vgpu_mres_t *out, const double *cap_dir, int cap) {
    if (!vgpu_init() || !prog_mtest.ok) return 0;
    gbuf_t in, res, dirs;
    if (!new_buffer(&in, (VkDeviceSize)n * sizeof(vgpu_mrec_t), HOST) || !new_buffer(&res, (VkDeviceSize)n * sizeof(vgpu_mres_t), HOST) ||
        !new_buffer(&dirs, (VkDeviceSize)cap * 16, HOST)) return 0;
    memcpy(in.map, recs, (size_t)n * sizeof(vgpu_mrec_t));
    memcpy(dirs.map, cap_dir, (size_t)cap * 16);
    gbuf_t *bufs[3] = {&in, &res, &dirs};
    bind_buffers(&prog_mtest, bufs);
    struct { uint32_t n, cap; } pc = {(uint32_t)n, (uint32_t)cap};
    if (!begin()) return 0;
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_mtest.pipe);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_mtest.pl, 0, 1, &prog_mtest.ds, 0, NULL);
    vkCmdPushConstants(cmd, prog_mtest.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof pc, &pc);
    vkCmdDispatch(cmd, ((uint32_t)n + 63) / 64, 1, 1);
    if (!submit()) return 0;
    memcpy(out, res.map, (size_t)n * sizeof(vgpu_mres_t));
    free_buffer(&in); free_buffer(&res); free_buffer(&dirs);
    return 1;
}

/* ------------------------------------------------------------------ -jacobi's whole flow (pipe_*.comp) */
enum { B_PTS, B_PORTALS, B_LEAVES, B_LFLIST, B_CAPDIR, B_SLOTS, B_FLOOD, B_PREFIX, B_VIS, B_DIRTY, B_STATE, B_PERRANK,
       B_FRONT, B_PAIRS, B_TOUCHED, B_CANDS, B_CPTS, B_COUNTERS, B_REFILL, B_SS, B_SW, B_SPS, B_SPW };
static gbuf_t p_buf[PIPE_NBIND];
static gbuf_t p_back;                                    /* (host) counters, slots, vis rows */
static struct { uint32_t nslots, fmax, pw32, npd, round, item_start; float eps_up, len_lo;
                uint32_t robust, maxchop, hullcap, nrefill, candcap, ptcap, saturate, maxarr; } p_pc;
#define C_N 0
#define C_T 1
#define C_FAIL 4
#define C_ARGS1 8
#define C_ARGS3 12
#define C_ARGS4 16
#define P_COUNTERS 32                                    /* (uint32 words) */

static void p_bind_all(void) {
    gbuf_t *bufs[PIPE_NBIND];
    for (int i = 0; i < PIPE_NBIND; i++) bufs[i] = &p_buf[i];
    for (int k = 0; k < PK_N; k++) bind_buffers(&prog_pipe[k], bufs);
}

int vgpu_pipe_setup(int npd, const float *pts, int npts, const vgpu_pportal_t *portals, int nleaves, const uint32_t *leaves,
                    int nlf, const uint32_t *lflist, const double *cap_dir, int cap, int fmax, int pw32, float eps_up,
                    float len_lo, int robust, int maxchop, int saturate, int maxarr, int want_slots, vgpu_pipe_info_t *info) {
    if (!vgpu_init() || !g_fp64) return 0;
    for (int k = 0; k < PK_N; k++) if (!prog_pipe[k].ok) return 0;
    if (fmax < 1) fmax = 1;
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(phys, &props);
    /* entries (a flood portal of a running flow: its windows, snapshot, list, frontier place): as many as fit a memory
       budget and the biggest buffers (one window set per entry, 256 bytes); slots: as many flows as could run */
    VkDeviceSize heap = 0;
    for (uint32_t i = 0; i < memprops.memoryHeapCount; i++)
        if ((memprops.memoryHeaps[i].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT) && memprops.memoryHeaps[i].size > heap) heap = memprops.memoryHeaps[i].size;
    long long candcap = 4 << 20, ptcap = 32 << 20;
    long long range = (long long)props.limits.maxStorageBufferRange;
    if (range > (1ll << 31) - 1) range = (1ll << 31) - 1;   /* (byte offsets stay in 31 bits) */
    int ns = want_slots > 0 ? want_slots : 4096;
    long long per_slot = (long long)pw32 * 16 + 64;
    long long budget = (long long)(heap / 2) - (candcap * 16 + ptcap * 16) - (long long)ns * per_slot;
    long long per_entry = 4 * 256 + 16 + 16 + 4 + 16 + 8;
    long long ne = budget / per_entry;
    if (ne > range / 256) ne = range / 256;
    if (getenv("HL_GPU_ENTRIES")) ne = atoll(getenv("HL_GPU_ENTRIES"));
    if (ne < fmax) { printf("Warning: GPU: not enough memory for the largest flow\n"); return 0; }
    VkDeviceSize sizes[PIPE_NBIND] = {0};
    sizes[B_SLOTS] = (VkDeviceSize)ns * 32;
    sizes[B_FLOOD] = sizes[B_PREFIX] = sizes[B_VIS] = sizes[B_DIRTY] = (VkDeviceSize)ns * pw32 * 4;
    sizes[B_STATE] = (VkDeviceSize)ne * 16;
    sizes[B_SS] = sizes[B_SW] = sizes[B_SPS] = sizes[B_SPW] = (VkDeviceSize)ne * 256;
    sizes[B_PERRANK] = (VkDeviceSize)ne * 16;
    sizes[B_FRONT] = (VkDeviceSize)ne * 4;
    sizes[B_PAIRS] = (VkDeviceSize)ne * 16;
    sizes[B_TOUCHED] = (VkDeviceSize)ne * 8;
    sizes[B_CANDS] = (VkDeviceSize)candcap * 16;
    sizes[B_CPTS] = (VkDeviceSize)ptcap * 16;
    sizes[B_COUNTERS] = P_COUNTERS * 4;
    if (!upload(&p_buf[B_PTS], pts, (VkDeviceSize)npts * 16) || !upload(&p_buf[B_PORTALS], portals, (VkDeviceSize)npd * sizeof(vgpu_pportal_t)) ||
        !upload(&p_buf[B_LEAVES], leaves, (VkDeviceSize)nleaves * 8) || !upload(&p_buf[B_LFLIST], lflist, (VkDeviceSize)(nlf ? nlf : 1) * 4) ||
        !upload(&p_buf[B_CAPDIR], cap_dir, (VkDeviceSize)cap * 16)) return 0;
    long long total = 0;
    for (int i = B_SLOTS; i < PIPE_NBIND; i++) {
        if (i == B_REFILL) continue;
        if (!new_buffer(&p_buf[i], sizes[i], VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) return 0;
        total += (long long)sizes[i];
    }
    if (!new_buffer(&p_buf[B_REFILL], 1 << 20, HOST)) return 0;
    if (!new_buffer(&p_back, (VkDeviceSize)P_COUNTERS * 4 + (VkDeviceSize)ns * 32 + (VkDeviceSize)ns * pw32 * 4, HOST)) return 0;
    /* clean start: no slot live, every list empty, nothing dirty */
    if (!begin()) return 0;
    for (int i = B_SLOTS; i < PIPE_NBIND; i++)
        if (i != B_REFILL) vkCmdFillBuffer(cmd, p_buf[i].buf, 0, VK_WHOLE_SIZE, i == B_PERRANK ? 0xffffffffu : 0u);
    if (!submit()) return 0;
    p_bind_all();
    p_pc.nslots = (uint32_t)ns, p_pc.fmax = (uint32_t)fmax, p_pc.pw32 = (uint32_t)pw32, p_pc.npd = (uint32_t)npd;
    p_pc.eps_up = eps_up, p_pc.len_lo = len_lo, p_pc.robust = (uint32_t)robust, p_pc.maxchop = (uint32_t)maxchop;
    p_pc.hullcap = (uint32_t)cap, p_pc.candcap = (uint32_t)candcap, p_pc.ptcap = (uint32_t)ptcap;
    p_pc.saturate = (uint32_t)saturate, p_pc.maxarr = (uint32_t)maxarr;
    if (info) info->nslots = ns, info->fmax = fmax, info->bytes = total, info->entries = ne;
    return ns;
}

static void p_barrier(void) {
    VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    mb.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_TRANSFER_WRITE_BIT;
    mb.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_INDIRECT_COMMAND_READ_BIT |
                       VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT,
                         VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_DRAW_INDIRECT_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 1, &mb, 0, NULL, 0, NULL);
}
static void p_use(int k) {
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_pipe[k].pipe);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prog_pipe[k].pl, 0, 1, &prog_pipe[k].ds, 0, NULL);
    vkCmdPushConstants(cmd, prog_pipe[k].pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof p_pc, &p_pc);
}
/* (dev) HL_GPU_PROFILE=1: the GPU's time per kernel (timestamps after each), and the time the host waits */
static int p_prof = -1, p_nq;
static VkQueryPool p_qpool;
static int p_qkind[64];
static double p_ktime[PK_N + 1], p_wall, p_period;
static long long p_kcount[PK_N + 1];
static void p_stamp(int k) {
    if (p_prof <= 0 || p_nq >= 64) return;
    vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, p_qpool, (uint32_t)p_nq);
    p_qkind[p_nq++] = k;
}
static void p_prof_read(void) {
    if (p_prof <= 0 || p_nq < 2) return;
    uint64_t ts[64];
    if (vkGetQueryPoolResults(dev, p_qpool, 0, (uint32_t)p_nq, sizeof ts, ts, 8, VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT) != VK_SUCCESS) return;
    for (int i = 1; i < p_nq; i++) { p_ktime[p_qkind[i]] += (double)(ts[i] - ts[i - 1]) * p_period * 1e-9; p_kcount[p_qkind[i]]++; }
}
static void p_prof_begin(void) {
    if (p_prof < 0) {
        p_prof = getenv("HL_GPU_PROFILE") != NULL;
        if (p_prof) {
            VkQueryPoolCreateInfo qi = {VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};
            qi.queryType = VK_QUERY_TYPE_TIMESTAMP, qi.queryCount = 64;
            if (vkCreateQueryPool(dev, &qi, NULL, &p_qpool) != VK_SUCCESS) p_prof = 0;
            VkPhysicalDeviceProperties props;
            vkGetPhysicalDeviceProperties(phys, &props);
            p_period = props.limits.timestampPeriod;
        }
    }
    if (p_prof <= 0) return;
    vkCmdResetQueryPool(cmd, p_qpool, 0, 64);
    p_nq = 0;
    p_stamp(PK_N);
}
void vgpu_pipe_report(void) {
    if (p_prof <= 0) return;
    static const char *names[PK_N + 1] = {"refill", "compact", "scan1", "snap", "scan2", "expand", "args", "merge", "copies"};
    double sum = 0;
    for (int k = 0; k <= PK_N; k++) sum += p_ktime[k];
    printf("GPU time per kernel (total %.2f s, host waited %.2f s):", sum, p_wall);
    for (int k = 0; k <= PK_N; k++) if (p_kcount[k]) printf(" %s %.2f s;", names[k], p_ktime[k]);
    printf("\n");
}
static void p_run(int k, uint32_t gx, uint32_t gy) { p_use(k); if (gx && gy) vkCmdDispatch(cmd, gx, gy, 1); p_barrier(); p_stamp(k); }
static void p_run_indirect(int k, int at) { p_use(k); vkCmdDispatchIndirect(cmd, p_buf[B_COUNTERS].buf, (VkDeviceSize)at * 4); p_barrier(); p_stamp(k); }
/* the expansion (items from item_start on), its dispatch size, the merge, the counters back */
static void p_expand_merge(uint32_t item_start, uint32_t nitems_direct) {
    p_pc.item_start = item_start;
    if (nitems_direct) {
        uint32_t gr = (nitems_direct + 63) / 64;
        p_run(PK_EXPAND, gr < 32768 ? gr : 32768, gr < 32768 ? 1 : (gr + 32767) / 32768);
    } else p_run_indirect(PK_EXPAND, C_ARGS3);
    p_run(PK_ARGS, 1, 1);
    p_run_indirect(PK_MERGE, C_ARGS4);
    p_pc.item_start = 0;
    VkBufferCopy c = {0, 0, P_COUNTERS * 4};
    vkCmdCopyBuffer(cmd, p_buf[B_COUNTERS].buf, p_back.buf, 1, &c);
}

int vgpu_pipe_round(uint32_t round, int nrefill, const uint32_t *refill, int nwords, int nvis, const int *vis_slots,
                    uint32_t *vis_out, vgpu_slot_t *slots_out, long long *n_pairs, long long *n_items, int *n_passes) {
    uint32_t ns = p_pc.nslots, pw32 = p_pc.pw32;
    if (nwords > 0 && p_buf[B_REFILL].size < (VkDeviceSize)nwords * 4) {
        free_buffer(&p_buf[B_REFILL]);
        if (!new_buffer(&p_buf[B_REFILL], (VkDeviceSize)nwords * 4 * 2, HOST)) return 0;
        p_bind_all();
    }
    if (nwords > 0) memcpy(p_buf[B_REFILL].map, refill, (size_t)nwords * 4);
    p_pc.round = round, p_pc.nrefill = (uint32_t)nrefill;
    VkDeviceSize vis_at = (VkDeviceSize)P_COUNTERS * 4 + (VkDeviceSize)ns * 32;
    if (!begin()) return 0;
    p_prof_begin();
    for (int i = 0; i < nvis; i++) {
        VkBufferCopy c = {(VkDeviceSize)vis_slots[i] * pw32 * 4, vis_at + (VkDeviceSize)i * pw32 * 4, (VkDeviceSize)pw32 * 4};
        vkCmdCopyBuffer(cmd, p_buf[B_VIS].buf, p_back.buf, 1, &c);
    }
    p_barrier();
    if (nrefill) p_run(PK_REFILL, (uint32_t)nrefill, 1);
    p_run(PK_COMPACT, ns, 1);
    p_run(PK_SCAN1, 1, 1);
    p_run_indirect(PK_SNAP, C_ARGS1);
    p_run(PK_SCAN2, 1, 1);
    p_expand_merge(0, 0);
    VkBufferCopy cs = {0, (VkDeviceSize)P_COUNTERS * 4, (VkDeviceSize)ns * 32};
    vkCmdCopyBuffer(cmd, p_buf[B_SLOTS].buf, p_back.buf, 1, &cs);
    p_stamp(PK_N);
    LARGE_INTEGER w0, w1, wf;
    QueryPerformanceCounter(&w0);
    if (!submit()) return 0;
    QueryPerformanceCounter(&w1); QueryPerformanceFrequency(&wf);
    p_wall += (double)(w1.QuadPart - w0.QuadPart) / wf.QuadPart;
    p_prof_read();
    const uint32_t *cn = p_back.map;
    for (int i = 0; i < nvis; i++) memcpy(vis_out + (size_t)i * pw32, (const char *)p_back.map + vis_at + (size_t)i * pw32 * 4, (size_t)pw32 * 4);
    *n_pairs = cn[C_N], *n_items = cn[C_T], *n_passes = 1;
    if (getenv("HL_PIPE_DEBUG")) printf("round %u: refill %d, pairs %u, items %u, candidates %u, points %u, fail %u, lists %u\n",
                                        round, nrefill, cn[C_N], cn[C_T], cn[2], cn[3], cn[C_FAIL], cn[5]);
    /* out of room for what came through: the merge took the items before the first that found none; go on from it */
    while (cn[C_FAIL] < cn[C_T]) {
        uint32_t from = cn[C_FAIL], T = cn[C_T];
        if (!begin()) return 0;
        vkCmdFillBuffer(cmd, p_buf[B_COUNTERS].buf, 8, 8, 0u);              /* candidates, points used */
        vkCmdFillBuffer(cmd, p_buf[B_COUNTERS].buf, 16, 4, 0xffffffffu);    /* no item failed */
        vkCmdFillBuffer(cmd, p_buf[B_COUNTERS].buf, 20, 4, 0u);             /* no list started */
        p_barrier();
        p_expand_merge(from, T - from);
        VkBufferCopy cs2 = {0, (VkDeviceSize)P_COUNTERS * 4, (VkDeviceSize)ns * 32};
        vkCmdCopyBuffer(cmd, p_buf[B_SLOTS].buf, p_back.buf, 1, &cs2);
        if (!submit()) return 0;
        if (cn[C_FAIL] == from) { printf("Warning: GPU: one expansion's windows don't fit\n"); return 0; }
        (*n_passes)++;
    }
    memcpy(slots_out, (const char *)p_back.map + P_COUNTERS * 4, (size_t)ns * 32);
    return !g_failed;
}
