/* Vulkan scaffolding adapted from ../hlvrad/gpu.c; isolated preview process. */
#define VK_NO_PROTOTYPES
#include <windows.h>
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include <io.h>
#include <fcntl.h>
#include "preview.spv.h"
static void Error(const char *fmt, ...) { va_list a; va_start(a,fmt); vfprintf(stderr,fmt,a); va_end(a); fputc(10,stderr); fflush(stderr); exit(2); }
static void Msg(const char *fmt, ...) { va_list a; va_start(a,fmt); vfprintf(stderr,fmt,a); va_end(a); fflush(stderr); }
static void *xalloc(size_t n) { void *p=calloc(1,n?n:1); if(!p)Error("Out of memory"); return p; }


int g_gpuCheck;                   /* (debugging, HLGPUCHK: the GPU's answers checked against the CPU's, single threaded) */

/* ------------------------------------------------------------------ Vulkan's functions */
static PFN_vkGetInstanceProcAddr gipa;
#define VK_DECLARE(f) static PFN_##f f;
#define VK_INSTANCE_FUNCS(X) X(vkEnumeratePhysicalDevices) X(vkGetPhysicalDeviceProperties) \
    X(vkGetPhysicalDeviceQueueFamilyProperties) X(vkGetPhysicalDeviceMemoryProperties) X(vkCreateDevice) \
    X(vkGetDeviceProcAddr) X(vkGetPhysicalDeviceProperties2) X(vkEnumerateDeviceExtensionProperties) \
    X(vkGetPhysicalDeviceFeatures2)
#define VK_DEVICE_FUNCS(X) X(vkGetDeviceQueue) X(vkCreateBuffer) X(vkDestroyBuffer) X(vkGetBufferMemoryRequirements) \
    X(vkAllocateMemory) X(vkFreeMemory) X(vkBindBufferMemory) X(vkMapMemory) X(vkGetBufferDeviceAddress) \
    X(vkCreateCommandPool) X(vkAllocateCommandBuffers) X(vkBeginCommandBuffer) X(vkEndCommandBuffer) X(vkQueueSubmit) \
    X(vkQueueWaitIdle) X(vkCmdCopyBuffer) X(vkResetCommandBuffer) X(vkCmdPipelineBarrier) \
    X(vkGetAccelerationStructureBuildSizesKHR) X(vkCreateAccelerationStructureKHR) X(vkDestroyAccelerationStructureKHR) \
    X(vkCmdBuildAccelerationStructuresKHR) X(vkGetAccelerationStructureDeviceAddressKHR) X(vkCreateShaderModule) \
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
static VkDeviceSize scratch_align = 256;
static VkDeviceSize storage_limit;
static uint64_t primitive_limit;

#define CHECK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) Error("GPU: %s failed (%d)", #x, (int)r_); } while (0)

/* ------------------------------------------------------------------ buffers */
typedef struct { VkBuffer buf; VkDeviceMemory mem; VkDeviceAddress addr; void *map; VkDeviceSize size; } gbuf_t;

#define HOST (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)
#define USAGE_ALL (VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT | \
                   VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT | VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR)

static gbuf_t NewBuffer(VkDeviceSize size, VkBufferUsageFlags usage, VkMemoryPropertyFlags want) {
    gbuf_t b = {0};
    b.size = size ? size : 16;
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = b.size;
    bi.usage = usage | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
    CHECK(vkCreateBuffer(dev, &bi, NULL, &b.buf));
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(dev, b.buf, &req);
    uint32_t type = ~0u;
    for (int pass = 0; pass < 2 && type == ~0u; pass++)        /* (host memory: cached first, for reading back) */
        for (uint32_t i = 0; i < memprops.memoryTypeCount; i++) {
            VkMemoryPropertyFlags f = memprops.memoryTypes[i].propertyFlags;
            if (!(req.memoryTypeBits & (1u << i)) || (f & want) != want) continue;
            if (pass == 0 && (want & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) && !(f & VK_MEMORY_PROPERTY_HOST_CACHED_BIT)) continue;
            type = i;
            break;
        }
    if (type == ~0u) Error("GPU: no memory for a %llu byte buffer", (unsigned long long)size);
    VkMemoryAllocateFlagsInfo fi = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO};
    fi.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT;
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &fi};
    ai.allocationSize = req.size, ai.memoryTypeIndex = type;
    CHECK(vkAllocateMemory(dev, &ai, NULL, &b.mem));
    CHECK(vkBindBufferMemory(dev, b.buf, b.mem, 0));
    VkBufferDeviceAddressInfo da = {VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO};
    da.buffer = b.buf;
    b.addr = vkGetBufferDeviceAddress(dev, &da);
    if (want & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) CHECK(vkMapMemory(dev, b.mem, 0, VK_WHOLE_SIZE, 0, &b.map));
    return b;
}

static void FreeBuffer(gbuf_t *b) {
    if (b->buf) vkDestroyBuffer(dev, b->buf, NULL), vkFreeMemory(dev, b->mem, NULL);
    memset(b, 0, sizeof(*b));
}

/* a host buffer at least size big (kept and grown: the work's inputs and outputs) */
static void Ensure(gbuf_t *b, VkDeviceSize size) {
    if (b->buf && b->size >= size) return;
    FreeBuffer(b);
    *b = NewBuffer(size + size / 4 + 1024, USAGE_ALL, HOST);
}

static void Begin(void) {
    VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    CHECK(vkResetCommandBuffer(cmd, 0));
    CHECK(vkBeginCommandBuffer(cmd, &bi));
}
static void Submit(void) {
    CHECK(vkEndCommandBuffer(cmd));
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1, si.pCommandBuffers = &cmd;
    CHECK(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE));
    CHECK(vkQueueWaitIdle(queue));
}

static void Barrier(VkPipelineStageFlags src, VkPipelineStageFlags dst, VkAccessFlags from, VkAccessFlags to) {
    VkMemoryBarrier b = {VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    b.srcAccessMask = from; b.dstAccessMask = to;
    vkCmdPipelineBarrier(cmd, src, dst, 0, 1, &b, 0, NULL, 0, NULL);
}

/* data the GPU keeps (in its own memory, through a host buffer) */
static gbuf_t Upload(const void *data, VkDeviceSize size) {
    gbuf_t st = NewBuffer(size, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, HOST);
    if (data && size) memcpy(st.map, data, size);
    else memset(st.map, 0, st.size);
    gbuf_t b = NewBuffer(size, USAGE_ALL, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    Begin();
    VkBufferCopy c = {0, 0, st.size};
    vkCmdCopyBuffer(cmd, st.buf, b.buf, 1, &c);
    Barrier(VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_ACCELERATION_STRUCTURE_BUILD_BIT_KHR | VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_ACCELERATION_STRUCTURE_READ_BIT_KHR | VK_ACCESS_SHADER_READ_BIT);
    Submit();
    FreeBuffer(&st);
    return b;
}

/* ------------------------------------------------------------------ acceleration structures */
typedef struct { VkAccelerationStructureKHR as; VkDeviceAddress addr; gbuf_t store; } accel_t;

static accel_t Build(VkAccelerationStructureTypeKHR type, const VkAccelerationStructureGeometryKHR *geom, uint32_t count) {
    accel_t a = {0};
    VkAccelerationStructureBuildGeometryInfoKHR bi = {VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_GEOMETRY_INFO_KHR};
    bi.type = type;
    bi.flags = VK_BUILD_ACCELERATION_STRUCTURE_PREFER_FAST_TRACE_BIT_KHR;
    bi.mode = VK_BUILD_ACCELERATION_STRUCTURE_MODE_BUILD_KHR;
    bi.geometryCount = 1, bi.pGeometries = geom;
    VkAccelerationStructureBuildSizesInfoKHR sz = {VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_SIZES_INFO_KHR};
    vkGetAccelerationStructureBuildSizesKHR(dev, VK_ACCELERATION_STRUCTURE_BUILD_TYPE_DEVICE_KHR, &bi, &count, &sz);
    a.store = NewBuffer(sz.accelerationStructureSize, VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_STORAGE_BIT_KHR,
                        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    VkAccelerationStructureCreateInfoKHR ci = {VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_CREATE_INFO_KHR};
    ci.buffer = a.store.buf, ci.size = sz.accelerationStructureSize, ci.type = type;
    CHECK(vkCreateAccelerationStructureKHR(dev, &ci, NULL, &a.as));
    gbuf_t scratch = NewBuffer(sz.buildScratchSize + scratch_align, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
                               VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    bi.dstAccelerationStructure = a.as;
    bi.scratchData.deviceAddress = (scratch.addr + scratch_align - 1) & ~(scratch_align - 1);
    VkAccelerationStructureBuildRangeInfoKHR range = {count, 0, 0, 0};
    const VkAccelerationStructureBuildRangeInfoKHR *pr = &range;
    Begin();
    vkCmdBuildAccelerationStructuresKHR(cmd, 1, &bi, &pr);
    Barrier(VK_PIPELINE_STAGE_ACCELERATION_STRUCTURE_BUILD_BIT_KHR, VK_PIPELINE_STAGE_ACCELERATION_STRUCTURE_BUILD_BIT_KHR | VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_ACCELERATION_STRUCTURE_WRITE_BIT_KHR, VK_ACCESS_ACCELERATION_STRUCTURE_READ_BIT_KHR);
    Submit();
    FreeBuffer(&scratch);
    VkAccelerationStructureDeviceAddressInfoKHR ai = {VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_DEVICE_ADDRESS_INFO_KHR};
    ai.accelerationStructure = a.as;
    a.addr = vkGetAccelerationStructureDeviceAddressKHR(dev, &ai);
    return a;
}

/* triangles (9 floats each) as a bottom level structure; opaque or not (not: the shader decides about each hit) */
static accel_t TriangleBLAS(const float *verts, uint32_t ntris, int opaque, gbuf_t *vb) {
    *vb = Upload(verts, (VkDeviceSize)ntris * 36);
    VkAccelerationStructureGeometryKHR g = {VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR};
    g.geometryType = VK_GEOMETRY_TYPE_TRIANGLES_KHR;
    g.flags = opaque ? VK_GEOMETRY_OPAQUE_BIT_KHR : 0;
    VkAccelerationStructureGeometryTrianglesDataKHR *t = &g.geometry.triangles;
    t->sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_TRIANGLES_DATA_KHR;
    t->vertexFormat = VK_FORMAT_R32G32B32_SFLOAT;
    t->vertexData.deviceAddress = vb->addr;
    t->vertexStride = 12;
    t->maxVertex = ntris * 3 - 1;
    t->indexType = VK_INDEX_TYPE_NONE_KHR;
    return Build(VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR, &g, ntris);
}

/* a top level structure of up to 4 instances (custom index = their number; masks 1, the 4th's 2: -textureshadows'
 * see-through triangles) */
static accel_t TopLevel(accel_t *const *blas, int n, gbuf_t *ib) {
    VkAccelerationStructureInstanceKHR inst[4];
    memset(inst, 0, sizeof(inst));
    int k = 0;
    for (int i = 0; i < n; i++) {
        if (!blas[i]) continue;
        inst[k].transform.matrix[0][0] = inst[k].transform.matrix[1][1] = inst[k].transform.matrix[2][2] = 1;
        inst[k].instanceCustomIndex = (uint32_t)i;
        inst[k].mask = i == 3 ? 0x02 : 0x01;
        inst[k].accelerationStructureReference = blas[i]->addr;
        k++;
    }
    *ib = Upload(inst, sizeof(inst[0]) * (k ? k : 1));
    VkAccelerationStructureGeometryKHR g = {VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR};
    g.geometryType = VK_GEOMETRY_TYPE_INSTANCES_KHR;
    g.geometry.instances.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_INSTANCES_DATA_KHR;
    g.geometry.instances.data.deviceAddress = ib->addr;
    return Build(VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR, &g, (uint32_t)k);
}

/* ------------------------------------------------------------------ compute programs */
typedef struct {
    VkDescriptorSetLayout dl;
    VkPipelineLayout pl;
    VkPipeline pipe;
    VkDescriptorSet ds;
    int nbind, ok;
} program_t;

/* (while starting: a failure is a warning, and the lighting goes to the CPU) */
#define TRY(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { Msg("Warning: GPU: %s failed (%d)\n", #x, (int)r_); return p; } } while (0)

/* binding 0 the scene, 1 .. nbind-1 storage buffers */
static program_t Program(const uint32_t *code, size_t codesize, int nbind, uint32_t pcsize) {
    if (nbind > 24) Error("GPU: too many bindings");
    program_t p = {0};
    p.nbind = nbind;
    VkShaderModuleCreateInfo smi = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    smi.codeSize = codesize, smi.pCode = code;
    VkShaderModule sm;
    TRY(vkCreateShaderModule(dev, &smi, NULL, &sm));
    VkDescriptorSetLayoutBinding b[24];
    for (int i = 0; i < nbind; i++) {
        b[i].binding = (uint32_t)i;
        b[i].descriptorType = i ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
        b[i].descriptorCount = 1, b[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT, b[i].pImmutableSamplers = NULL;
    }
    VkDescriptorSetLayoutCreateInfo dli = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    dli.bindingCount = (uint32_t)nbind, dli.pBindings = b;
    TRY(vkCreateDescriptorSetLayout(dev, &dli, NULL, &p.dl));
    VkPushConstantRange pcr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, pcsize};
    VkPipelineLayoutCreateInfo pli = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pli.setLayoutCount = 1, pli.pSetLayouts = &p.dl, pli.pushConstantRangeCount = 1, pli.pPushConstantRanges = &pcr;
    TRY(vkCreatePipelineLayout(dev, &pli, NULL, &p.pl));
    VkComputePipelineCreateInfo cpci = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    cpci.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    cpci.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT, cpci.stage.module = sm, cpci.stage.pName = "main";
    cpci.layout = p.pl;
    TRY(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpci, NULL, &p.pipe));
    VkDescriptorPoolSize ps[2] = {{VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR, 1},
                                  {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, (uint32_t)(nbind - 1)}};
    VkDescriptorPoolCreateInfo dpi = {VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dpi.maxSets = 1, dpi.poolSizeCount = 2, dpi.pPoolSizes = ps;
    VkDescriptorPool pool;
    TRY(vkCreateDescriptorPool(dev, &dpi, NULL, &pool));
    VkDescriptorSetAllocateInfo dsa = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    dsa.descriptorPool = pool, dsa.descriptorSetCount = 1, dsa.pSetLayouts = &p.dl;
    TRY(vkAllocateDescriptorSets(dev, &dsa, &p.ds));
    p.ok = 1;
    return p;
}

/* point the program at its scene and buffers, and run it over n threads (64 a group) */
static void Run(program_t *p, const accel_t *scene, gbuf_t *const *bufs, const void *pc, uint32_t pcsize, uint32_t n) {
    VkWriteDescriptorSetAccelerationStructureKHR was = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET_ACCELERATION_STRUCTURE_KHR};
    was.accelerationStructureCount = 1, was.pAccelerationStructures = &scene->as;
    VkDescriptorBufferInfo bi[24];
    VkWriteDescriptorSet w[24];
    memset(w, 0, sizeof(w));
    for (int i = 0; i < p->nbind; i++) {
        w[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        w[i].dstSet = p->ds, w[i].dstBinding = (uint32_t)i, w[i].descriptorCount = 1;
        if (!i) {
            w[i].pNext = &was;
            w[i].descriptorType = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
        } else {
            bi[i].buffer = bufs[i]->buf, bi[i].offset = 0, bi[i].range = VK_WHOLE_SIZE;
            w[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            w[i].pBufferInfo = &bi[i];
        }
    }
    vkUpdateDescriptorSets(dev, (uint32_t)p->nbind, w, 0, NULL);
    Begin();
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, p->pipe);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, p->pl, 0, 1, &p->ds, 0, NULL);
    vkCmdPushConstants(cmd, p->pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, pcsize, pc);
    vkCmdDispatch(cmd, (n + 63) / 64, 1, 1);
    Submit();
}

/* Run, with the outputs (bufs[outs[k]]: host buffers, sizes[k] bytes) written in GPU memory and copied back after:
 * a shader's scattered writes straight to host memory cross the bus one by one (measured: -layers' bigger answers
 * made the gather 4 times slower that way) */
static gbuf_t devout[4];
static void RunOut(program_t *p, const accel_t *scene, gbuf_t *const *bufs, const void *pc, uint32_t pcsize, uint32_t n,
                   const int *outs, const VkDeviceSize *sizes, int nouts) {
    gbuf_t *b2[24];
    memcpy(b2, bufs, sizeof(gbuf_t *) * p->nbind);
    for (int k = 0; k < nouts; k++) {
        if (!devout[k].buf || devout[k].size < sizes[k]) {
            FreeBuffer(&devout[k]);
            devout[k] = NewBuffer(sizes[k] + sizes[k] / 4 + 1024, USAGE_ALL, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        }
        b2[outs[k]] = &devout[k];
    }
    VkWriteDescriptorSetAccelerationStructureKHR was = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET_ACCELERATION_STRUCTURE_KHR};
    was.accelerationStructureCount = 1, was.pAccelerationStructures = &scene->as;
    VkDescriptorBufferInfo bi[24];
    VkWriteDescriptorSet w[24];
    memset(w, 0, sizeof(w));
    for (int i = 0; i < p->nbind; i++) {
        w[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        w[i].dstSet = p->ds, w[i].dstBinding = (uint32_t)i, w[i].descriptorCount = 1;
        if (!i) {
            w[i].pNext = &was;
            w[i].descriptorType = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
        } else {
            if(b2[i]->size>storage_limit)Error("Storage buffer exceeds device limit");
            bi[i].buffer = b2[i]->buf, bi[i].offset = 0, bi[i].range = VK_WHOLE_SIZE;
            w[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            w[i].pBufferInfo = &bi[i];
        }
    }
    vkUpdateDescriptorSets(dev, (uint32_t)p->nbind, w, 0, NULL);
    Begin();
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, p->pipe);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, p->pl, 0, 1, &p->ds, 0, NULL);
    vkCmdPushConstants(cmd, p->pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, pcsize, pc);
    vkCmdDispatch(cmd, (n + 63) / 64, 1, 1);
    VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    mb.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT, mb.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
    for (int k = 0; k < nouts; k++) {
        VkBufferCopy c = {0, 0, sizes[k]};
        vkCmdCopyBuffer(cmd, devout[k].buf, bufs[outs[k]]->buf, 1, &c);
    }
    Barrier(VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT);
    Submit();
}


static program_t prog;
static int HasExtension(VkPhysicalDevice p, const char *name) {
    uint32_t n = 0;
    vkEnumerateDeviceExtensionProperties(p, NULL, &n, NULL);
    VkExtensionProperties *e = xalloc(sizeof(*e) * (n + 1));
    vkEnumerateDeviceExtensionProperties(p, NULL, &n, e);
    int found = 0;
    for (uint32_t i = 0; i < n && !found; i++) found = !strcmp(e[i].extensionName, name);
    free(e);
    return found;
}

/* a GPU that can trace rays (a discrete one first): 0, with a warning, when there's none */
static int StartGPU(void) {
    HMODULE vk = LoadLibraryA("vulkan-1.dll");
    if (!vk) {
        Msg("Warning: no Vulkan (vulkan-1.dll): native preview unavailable\n");
        return 0;
    }
    gipa = (PFN_vkGetInstanceProcAddr)(void *)GetProcAddress(vk, "vkGetInstanceProcAddr");
    vkCreateInstance_ = gipa ? (PFN_vkCreateInstance)gipa(NULL, "vkCreateInstance") : NULL;
    if (!vkCreateInstance_) {
        Msg("Warning: Vulkan didn't start: native preview unavailable\n");
        return 0;
    }
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.pApplicationName = "Hammerless preview worker";
    app.apiVersion = VK_API_VERSION_1_2;
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ici.pApplicationInfo = &app;
    VkInstance inst;
    if (vkCreateInstance_(&ici, NULL, &inst) != VK_SUCCESS) {
        Msg("Warning: Vulkan didn't start: native preview unavailable\n");
        return 0;
    }
#define VK_LOAD_I(f) f = (PFN_##f)gipa(inst, #f);
    VK_INSTANCE_FUNCS(VK_LOAD_I)
    uint32_t np = 0;
    vkEnumeratePhysicalDevices(inst, &np, NULL);
    VkPhysicalDevice *pds = xalloc(sizeof(*pds) * (np + 1));
    vkEnumeratePhysicalDevices(inst, &np, pds);
    int best = -1, bestScore = -1;
    for (uint32_t i = 0; i < np; i++) {
        VkPhysicalDeviceProperties p;
        vkGetPhysicalDeviceProperties(pds[i], &p);
        if (p.apiVersion < VK_API_VERSION_1_2 || !HasExtension(pds[i], "VK_KHR_ray_query") ||
            !HasExtension(pds[i], "VK_KHR_acceleration_structure") || !HasExtension(pds[i], "VK_KHR_deferred_host_operations"))
            continue;
        VkPhysicalDeviceRayQueryFeaturesKHR rq = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAY_QUERY_FEATURES_KHR};
        VkPhysicalDeviceAccelerationStructureFeaturesKHR asf = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_FEATURES_KHR, &rq};
        VkPhysicalDeviceVulkan12Features v12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, &asf};
        VkPhysicalDeviceFeatures2 f2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &v12};
        vkGetPhysicalDeviceFeatures2(pds[i], &f2);
        if (!rq.rayQuery || !asf.accelerationStructure || !v12.bufferDeviceAddress) continue;
        int score = p.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ? 2 : 1;
        if (score > bestScore) best = (int)i, bestScore = score;
    }
    if (best < 0) {
        free(pds);
        Msg("Warning: no graphics card here can trace rays (Vulkan ray queries): native preview unavailable\n");
        return 0;
    }
    phys = pds[best];
    free(pds);
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(phys, &props);
    vkGetPhysicalDeviceMemoryProperties(phys, &memprops);
    VkPhysicalDeviceAccelerationStructurePropertiesKHR asp = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_PROPERTIES_KHR};
    VkPhysicalDeviceProperties2 p2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &asp};
    vkGetPhysicalDeviceProperties2(phys, &p2);
    storage_limit = p2.properties.limits.maxStorageBufferRange;
    primitive_limit = asp.maxPrimitiveCount;
    if (asp.minAccelerationStructureScratchOffsetAlignment) scratch_align = asp.minAccelerationStructureScratchOffsetAlignment;
    uint32_t nq = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, NULL);
    VkQueueFamilyProperties *qf = xalloc(sizeof(*qf) * (nq + 1));
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, qf);
    uint32_t qfam = 0;
    for (uint32_t i = 0; i < nq; i++)
        if (qf[i].queueFlags & VK_QUEUE_COMPUTE_BIT) {
            qfam = i;
            break;
        }
    free(qf);
    float prio = 1;
    VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qci.queueFamilyIndex = qfam, qci.queueCount = 1, qci.pQueuePriorities = &prio;
    VkPhysicalDeviceRayQueryFeaturesKHR rq = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAY_QUERY_FEATURES_KHR};
    rq.rayQuery = VK_TRUE;
    VkPhysicalDeviceAccelerationStructureFeaturesKHR asf = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_FEATURES_KHR, &rq};
    asf.accelerationStructure = VK_TRUE;
    VkPhysicalDeviceVulkan12Features v12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, &asf};
    v12.bufferDeviceAddress = VK_TRUE;
    const char *exts[] = {"VK_KHR_acceleration_structure", "VK_KHR_ray_query", "VK_KHR_deferred_host_operations"};
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &v12};
    dci.queueCreateInfoCount = 1, dci.pQueueCreateInfos = &qci;
    dci.enabledExtensionCount = 3, dci.ppEnabledExtensionNames = exts;
    if (vkCreateDevice(phys, &dci, NULL, &dev) != VK_SUCCESS) {
        Msg("Warning: the graphics card didn't start for ray tracing: native preview unavailable\n");
        return 0;
    }
#define VK_LOAD_D(f) if (!(f = (PFN_##f)vkGetDeviceProcAddr(dev, #f))) { Msg("Warning: Vulkan has no " #f ": native preview unavailable\n"); return 0; }
    VK_DEVICE_FUNCS(VK_LOAD_D)
    vkGetDeviceQueue(dev, qfam, 0, &queue);
    VkCommandPoolCreateInfo cpi = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cpi.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, cpi.queueFamilyIndex = qfam;
    VkCommandPool pool;
    if (vkCreateCommandPool(dev, &cpi, NULL, &pool) != VK_SUCCESS) {
        Msg("Warning: the graphics card didn't start for ray tracing: native preview unavailable\n");
        return 0;
    }
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    cai.commandPool = pool, cai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, cai.commandBufferCount = 1;
    if (vkAllocateCommandBuffers(dev, &cai, &cmd) != VK_SUCCESS) {
        Msg("Warning: the graphics card didn't start for ray tracing: native preview unavailable\n");
        return 0;
    }
    prog = Program(spv_preview,sizeof(spv_preview),5,48);
    if(!prog.ok)return 0;
    Msg("Lighting on the GPU: %s\n", props.deviceName);
    return 1;
}


/* Binary protocol v1. stdout is reserved for responses. */
#define MAGIC 0x48504c31u
#define MAX_TRIANGLES 8000000u
#define MAX_LIGHTS 65536u
#define MAX_POINTS 8388608u
static accel_t world_blas,world_tlas;
static gbuf_t vertices,instance_buffer,metadata,light_buffer,input_points,output_points;
static uint32_t triangle_count,light_count;
static int have_scene;
static void FreeAccel(accel_t *a) {
    if(a->as)vkDestroyAccelerationStructureKHR(dev,a->as,NULL);
    FreeBuffer(&a->store);memset(a,0,sizeof(*a));
}
static void Read(void *p,size_t n) {
    if(n && fread(p,1,n,stdin)!=n)Error("Truncated input payload");
}
static void Write(const void *p,size_t n) {
    if(n && fwrite(p,1,n,stdout)!=n)Error("Output pipe failed");
}
static void Ack(uint32_t count) {
    uint32_t h[4]={MAGIC,0,count,0};Write(h,sizeof(h));
}
static float *ReadFloats(uint32_t count,size_t stride) {
    if(count && stride>SIZE_MAX/count/sizeof(float))Error("Payload size overflow");
    size_t n=(size_t)count*stride;float *p=xalloc(n*sizeof(float));Read(p,n*sizeof(float));
    for(size_t i=0;i<n;i++)if(!isfinite(p[i]))Error("Non-finite input");
    return p;
}
static void LoadScene(uint32_t count) {
    if(count>MAX_TRIANGLES || count>primitive_limit || (uint64_t)count*32>storage_limit)Error("Triangle/device buffer cap exceeded");
    float *v=ReadFloats(count,9),*m=ReadFloats(count,8);
    FreeAccel(&world_tlas);FreeAccel(&world_blas);
    FreeBuffer(&vertices);FreeBuffer(&instance_buffer);FreeBuffer(&metadata);
    float dummy[9]={0};
    world_blas=TriangleBLAS(count?v:dummy,count?count:1,1,&vertices);
    accel_t *blas[1]={&world_blas};world_tlas=TopLevel(blas,1,&instance_buffer);
    metadata=Upload(m,(VkDeviceSize)count*32);free(v);free(m);
    triangle_count=count;have_scene=1;Ack(count);fflush(stdout);
}
static void LoadLights(uint32_t count) {
    if(count>MAX_LIGHTS)Error("Light cap exceeded");
    float *v=ReadFloats(count,20);FreeBuffer(&light_buffer);
    light_buffer=Upload(v,(VkDeviceSize)count*80);free(v);light_count=count;Ack(count);fflush(stdout);
}
typedef struct {uint32_t rays,depth,seed,flags;float unit_scale,sky[3];} params_t;
typedef struct {uint32_t count,lights,triangles,pad;params_t params;} push_t;
_Static_assert(sizeof(params_t)==32,"Protocol parameter packing");
_Static_assert(sizeof(push_t)==48,"Shader push constant packing");
static double Milliseconds(void) {
    LARGE_INTEGER value,frequency;
    QueryPerformanceCounter(&value);QueryPerformanceFrequency(&frequency);
    return 1000.0*(double)value.QuadPart/(double)frequency.QuadPart;
}
static void Shade(uint32_t count) {
    double shade_started=Milliseconds(),dispatch_ms=0,max_dispatch_ms=0;
    uint32_t dispatches=0;

    if(!have_scene)Error("Load scene before shading");
    if(count>MAX_POINTS)Error("Receiver cap exceeded");
    params_t p;Read(&p,sizeof(p));
    if(p.rays>128 || p.depth<1 || p.depth>8 || p.seed>1000000 || p.flags>3 ||
       !isfinite(p.unit_scale) || p.unit_scale<=0 || p.unit_scale>1e6f)Error("Invalid shading parameters");
    for(int k=0;k<3;k++)if(!isfinite(p.sky[k]))Error("Invalid sky");
    float *points=ReadFloats(count,8);
    if(!light_buffer.buf)light_buffer=Upload(NULL,16);
    /* Bound each job by direct evaluation work, including sampled emitter loops.
       Ray queries use hardware, but the loop still visits every light entry. */
    uint64_t cost=((uint64_t)light_count+162u)*(1u+(uint64_t)p.rays*p.depth);
    if(cost>2000000u)Error("Per-receiver lighting work exceeds safety budget");
    /* Enough independent invocations to occupy a modern GPU. A tiny total-work
       budget previously serialized this map into 73-point, two-workgroup jobs. */
    uint64_t budget=128000000u;
    const char *override=getenv("HL_PREVIEW_DISPATCH_BUDGET");
    if(override) {
        char *end;unsigned long long value=strtoull(override,&end,10);
        if(*end || value<2000000u || value>128000000u)Error("Invalid dispatch budget override");
        budget=value;
    }
    uint32_t chunk=(uint32_t)(budget/(cost?cost:1));
    if(chunk<1)chunk=1;if(chunk>4096)chunk=4096;
    Ack(count);
    for(uint32_t first=0;first<count;first+=chunk) {
        uint32_t n=count-first<chunk?count-first:chunk;
        Ensure(&input_points,(VkDeviceSize)n*32);Ensure(&output_points,(VkDeviceSize)n*16);
        memcpy(input_points.map,points+(size_t)first*8,(size_t)n*32);
        push_t pc={n,light_count,triangle_count,0,p};
        gbuf_t *bufs[5]={NULL,&metadata,&light_buffer,&input_points,&output_points};
        int outs[1]={4};VkDeviceSize sizes[1]={(VkDeviceSize)n*16};
        double tick=Milliseconds();
        RunOut(&prog,&world_tlas,bufs,&pc,sizeof(pc),n,outs,sizes,1);
        double elapsed=Milliseconds()-tick;dispatch_ms+=elapsed;
        if(elapsed>max_dispatch_ms)max_dispatch_ms=elapsed;
        dispatches++;
        const float *values=output_points.map;
        for(size_t k=0;k<(size_t)n*4;k++)if(!isfinite(values[k]))Error("Non-finite lighting output");
        Write(output_points.map,(size_t)n*16);
    }
    free(points);if(fflush(stdout))Error("Output pipe flush failed");
    if(getenv("HL_PREVIEW_PROFILE"))Msg("PREVIEW_SHADE points=%u lights=%u rays=%u depth=%u chunk=%u dispatches=%u dispatch_ms=%.3f max_dispatch_ms=%.3f total_ms=%.3f\n",count,light_count,p.rays,p.depth,chunk,dispatches,dispatch_ms,max_dispatch_ms,Milliseconds()-shade_started);
}
int main(void) {
    _setmode(_fileno(stdin),_O_BINARY);_setmode(_fileno(stdout),_O_BINARY);
    if(!StartGPU())return 3;
    for(;;) {
        uint32_t h[4];size_t n=fread(h,1,sizeof(h),stdin);
        if(n==0 && feof(stdin))break;
        if(n!=sizeof(h))Error("Truncated command header");
        if(h[0]!=MAGIC || h[3])Error("Bad protocol header");
        if(h[1]==1)LoadScene(h[2]);
        else if(h[1]==2)LoadLights(h[2]);
        else if(h[1]==3)Shade(h[2]);
        else if(h[1]==9) {if(h[2])Error("Bad quit command");Ack(0);fflush(stdout);break;}
        else Error("Unknown command");
    }
    /* Process isolation owns Vulkan lifetime; OS tears down device at exit. */
    return 0;
}
