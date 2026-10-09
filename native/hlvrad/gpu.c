/* -gpu: the lighting's rays on the GPU (Vulkan ray queries: the ray tracing cores of NVIDIA RTX, AMD RX 6000+ and
 * Intel Arc cards), much faster than the CPU; the result looks the same as vrad's but isn't the same to the bit.
 *
 * Vulkan comes with the graphics driver (vulkan-1.dll), loaded here when asked for: without a card that can do it,
 * hlvrad lights on the CPU as usual. The GPU programs are in shaders/ (compiled into C headers there by build.py):
 * gather (direct light at points), ambient (leaf ambient cubes), propind (static props' bounced light).
 * The last two find surfaces with vrad's own tree walk (walk.glsl), not the ray tracing cores: the same surfaces.
 *
 * The shadow scene: the ray tracer's triangles (the world, the sky's, the static props', apart so one prop can be
 * skipped). Work goes in
 * chunks, each waited for, so no single GPU job runs long (Windows resets a GPU stuck over 2 seconds). */
#define VK_NO_PROTOTYPES
#include <windows.h>
#include <vulkan/vulkan.h>
#include "hlvrad.h"
#include "shaders/gather.spv.h"
#include "shaders/ambient.spv.h"
#include "shaders/propind.spv.h"

int g_bGPU;
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

/* data the GPU keeps (in its own memory, through a host buffer) */
static gbuf_t Upload(const void *data, VkDeviceSize size) {
    gbuf_t st = NewBuffer(size, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, HOST);
    if (data && size) memcpy(st.map, data, size);
    else memset(st.map, 0, st.size);
    gbuf_t b = NewBuffer(size, USAGE_ALL, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    Begin();
    VkBufferCopy c = {0, 0, st.size};
    vkCmdCopyBuffer(cmd, st.buf, b.buf, 1, &c);
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
    Submit();
    FreeBuffer(&scratch);
    VkAccelerationStructureDeviceAddressInfoKHR ai = {VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_DEVICE_ADDRESS_INFO_KHR};
    ai.accelerationStructure = a.as;
    a.addr = vkGetAccelerationStructureDeviceAddressKHR(dev, &ai);
    return a;
}

/* triangles (9 floats each) as a bottom level structure; opaque or not (not: the shader decides about each hit) */
static accel_t TriangleBLAS(const float *verts, uint32_t ntris, int opaque, gbuf_t *vb) {
    *vb = Upload(verts, (VkDeviceSize)ntris * 36 + 16);
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

/* a top level structure of up to 3 instances (custom index = their number, all masks 0xff) */
static accel_t TopLevel(accel_t *const *blas, int n, gbuf_t *ib) {
    VkAccelerationStructureInstanceKHR inst[3];
    memset(inst, 0, sizeof(inst));
    int k = 0;
    for (int i = 0; i < n; i++) {
        if (!blas[i]) continue;
        inst[k].transform.matrix[0][0] = inst[k].transform.matrix[1][1] = inst[k].transform.matrix[2][2] = 1;
        inst[k].instanceCustomIndex = (uint32_t)i;
        inst[k].mask = 0xff;
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

/* ------------------------------------------------------------------ start */
static program_t prog_gather, prog_ambient, prog_propind;

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
        Msg("Warning: no Vulkan (vulkan-1.dll): lighting on the CPU\n");
        return 0;
    }
    gipa = (PFN_vkGetInstanceProcAddr)(void *)GetProcAddress(vk, "vkGetInstanceProcAddr");
    vkCreateInstance_ = gipa ? (PFN_vkCreateInstance)gipa(NULL, "vkCreateInstance") : NULL;
    if (!vkCreateInstance_) {
        Msg("Warning: Vulkan didn't start: lighting on the CPU\n");
        return 0;
    }
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.pApplicationName = "hlvrad";
    app.apiVersion = VK_API_VERSION_1_2;
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ici.pApplicationInfo = &app;
    VkInstance inst;
    if (vkCreateInstance_(&ici, NULL, &inst) != VK_SUCCESS) {
        Msg("Warning: Vulkan didn't start: lighting on the CPU\n");
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
        Msg("Warning: no graphics card here can trace rays (Vulkan ray queries): lighting on the CPU\n");
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
        Msg("Warning: the graphics card didn't start for ray tracing: lighting on the CPU\n");
        return 0;
    }
#define VK_LOAD_D(f) if (!(f = (PFN_##f)vkGetDeviceProcAddr(dev, #f))) { Msg("Warning: Vulkan has no " #f ": lighting on the CPU\n"); return 0; }
    VK_DEVICE_FUNCS(VK_LOAD_D)
    vkGetDeviceQueue(dev, qfam, 0, &queue);
    VkCommandPoolCreateInfo cpi = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cpi.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, cpi.queueFamilyIndex = qfam;
    VkCommandPool pool;
    if (vkCreateCommandPool(dev, &cpi, NULL, &pool) != VK_SUCCESS) {
        Msg("Warning: the graphics card didn't start for ray tracing: lighting on the CPU\n");
        return 0;
    }
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    cai.commandPool = pool, cai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, cai.commandBufferCount = 1;
    if (vkAllocateCommandBuffers(dev, &cai, &cmd) != VK_SUCCESS) {
        Msg("Warning: the graphics card didn't start for ray tracing: lighting on the CPU\n");
        return 0;
    }
    prog_gather = Program(spv_gather, sizeof(spv_gather), 8, 36);
    prog_ambient = Program(spv_ambient, sizeof(spv_ambient), 19, 36);
    prog_propind = Program(spv_propind, sizeof(spv_propind), 19, 16);
    if (!prog_gather.ok || !prog_ambient.ok || !prog_propind.ok) {
        Msg("Warning: the graphics card's driver didn't take hlvrad's GPU programs: lighting on the CPU\n");
        return 0;
    }
    Msg("Lighting on the GPU: %s\n", props.deviceName);
    return 1;
}

/* (the start takes a few tenths of a second: it runs on its own thread while the map loads) */
static HANDLE init_thread;
static int init_ok;
static DWORD WINAPI InitThread(LPVOID arg) {
    (void)arg;
    init_ok = StartGPU();
    return 0;
}
void GPU_StartInit(void) {
    g_gpuCheck = getenv("HLGPUCHK") != NULL;
    init_thread = CreateThread(NULL, 0, InitThread, NULL, 0, NULL);
}
int GPU_Init(void) {
    if (!init_thread) GPU_StartInit();
    WaitForSingleObject(init_thread, INFINITE);
    return init_ok;
}

/* ------------------------------------------------------------------ the shadow scene and the lights */
static accel_t shadow_tlas;
static gbuf_t propids, lightbuf, pvsbuf, skydirs, skymapbuf;
static int nlights;
static int slotstyle[256], nslots, styleslot[256];

/* the ray tracer's triangles: the world's (brushes, displacements), the sky's, and the props' (their numbers kept, so
 * a prop can skip its own) */
void GPU_ShadowScene(void) {
    int n;
    const float *v = RT_Triangles(&n);
    float *world = xalloc(36 * (size_t)(n + 1)), *sky = xalloc(36 * (size_t)(n + 1)), *props = xalloc(36 * (size_t)(n + 1));
    int *ids = xalloc(sizeof(int) * (n + 1)), nw = 0, ns = 0, np = 0;
    for (int i = 0; i < n; i++) {
        int id = RT_TriangleID(i);
        if (id & TRACE_ID_SKY) memcpy(sky + 9 * ns++, v + 9 * i, 36);
        else if (id & TRACE_ID_STATICPROP) ids[np] = id & ~TRACE_ID_STATICPROP, memcpy(props + 9 * np++, v + 9 * i, 36);
        else memcpy(world + 9 * nw++, v + 9 * i, 36);
    }
    static accel_t bw, bs, bp;
    static gbuf_t vw, vs, vp, ib;
    accel_t *blas[3] = {NULL, NULL, NULL};
    if (nw) bw = TriangleBLAS(world, (uint32_t)nw, 1, &vw), blas[0] = &bw;
    if (ns) bs = TriangleBLAS(sky, (uint32_t)ns, 1, &vs), blas[1] = &bs;
    if (np) bp = TriangleBLAS(props, (uint32_t)np, 0, &vp), blas[2] = &bp;
    shadow_tlas = TopLevel(blas, 3, &ib);
    propids = Upload(ids, sizeof(int) * (np ? np : 1));
    free(world), free(sky), free(props), free(ids);
}

/* (gather.comp's Light) */
typedef struct { float origin[4], intensity[4], normal[4], a[4], b[4], c[4]; int m[4]; } gpulight_t;

int GPU_NumSlots(void) { return nslots; }
int GPU_SlotStyle(int slot) { return slotstyle[slot]; }
int GPU_StyleSlot(int style) { return style >= 0 && style < 256 ? styleslot[style] : -1; }

/* the lights (in vrad's order; each light style gets a slot, worked through one at a time), what they see, the sky's
 * directions and picture */
void GPU_Lights(void) {
    for (int i = 0; i < 256; i++) styleslot[i] = -1;
    nslots = 0, nlights = 0;
    for (directlight_t *dl = activelights; dl; dl = dl->next) nlights++;
    int rowwords = (VisRowBytes() + 3) / 4;
    gpulight_t *L = xalloc(sizeof(gpulight_t) * (nlights + 1));
    uint32_t *pvs = xalloc(4 * (size_t)rowwords * (nlights + 1));
    int i = 0;
    for (directlight_t *dl = activelights; dl; dl = dl->next, i++) {
        int style = dl->light.style & 255;
        if (styleslot[style] < 0) styleslot[style] = nslots, slotstyle[nslots++] = style;
        gpulight_t *g = &L[i];
        memcpy(g->origin, dl->light.origin, 12), memcpy(g->intensity, dl->light.intensity, 12), memcpy(g->normal, dl->light.normal, 12);
        g->a[0] = dl->light.stopdot, g->a[1] = dl->light.stopdot2, g->a[2] = dl->light.exponent, g->a[3] = dl->light.constant_attn;
        g->b[0] = dl->light.linear_attn, g->b[1] = dl->light.quadratic_attn;
        g->b[2] = dl->m_flStartFadeDistance, g->b[3] = dl->m_flEndFadeDistance;
        g->c[0] = dl->m_flCapDist, g->c[1] = dl->has_sun_extent ? dl->sun_extent : g_SunAngularExtent;
        g->m[0] = dl->light.type, g->m[1] = styleslot[style], g->m[2] = i * rowwords;
        if (getenv("HLGPUTYPES") && !(atoi(getenv("HLGPUTYPES")) & (1 << dl->light.type))) g->m[1] = -1;   /* (debugging: these types only) */
        if (dl->pvs) memcpy(pvs + (size_t)i * rowwords, dl->pvs, VisRowBytes());
        else memset(pvs + (size_t)i * rowwords, 0xff, 4 * (size_t)rowwords);
    }
    lightbuf = Upload(L, sizeof(gpulight_t) * (nlights ? nlights : 1));
    pvsbuf = Upload(pvs, 4 * (size_t)rowwords * (nlights ? nlights : 1));
    free(L), free(pvs);
    int nd;
    const vec3_t *d = SkyDirections(&nd);
    float *d4 = xalloc(16 * (size_t)(nd + 1));
    for (int k = 0; k < nd; k++) memcpy(d4 + 4 * k, d[k], 12);
    skydirs = Upload(d4, 16 * (size_t)(nd ? nd : 1));
    free(d4);
    int w = 0, h = 0;
    const float *sm = SkyMapData(&w, &h);
    skymapbuf = Upload(sm, sm ? 12 * (size_t)w * h : 16);
}

/* ------------------------------------------------------------------ work */
#define GATHER_CHUNK 16384            /* groups a job at most (4 points each) */
/* (a job's size from its work: Windows resets a GPU busy for 2 seconds, and a game may share the card. Budgets:
 * hardware rays a job, and tree walks, which are much slower) */
#define RAY_BUDGET 100000000.0
#define WALK_BUDGET 30000000.0
static int JobSize(double per_item, double budget, int most) {
    double k = budget / (per_item > 1 ? per_item : 1);
    return k < 64 ? 64 : k > most ? most : (int)k;
}

static gbuf_t in_buf, out_buf;

/* the lights of one style slot at groups of points: per point 16 floats (4 normals' colours, then "lit") */
void GPU_Gather(const gpugroup_t *groups, int n, int slot, float *out) {
    int w = 0, h = 0;
    SkyMapData(&w, &h);
    struct { int ngroups, slot, nlights, fast, nskyFull, skymapW, skymapH, haveSkyMap; float sunExtent; } pc;
    pc.slot = slot, pc.nlights = nlights, pc.fast = g_bFast, pc.nskyFull = (int)(g_flSkySampleScale * 162.0f);
    pc.skymapW = w, pc.skymapH = h, pc.haveSkyMap = HaveSkyMap(), pc.sunExtent = g_SunAngularExtent;
    int chunk = JobSize(4.0 * (pc.nskyFull + 30 + nlights), RAY_BUDGET, GATHER_CHUNK);
    for (int at = 0; at < n; at += chunk) {
        int k = n - at < chunk ? n - at : chunk;
        Ensure(&in_buf, sizeof(gpugroup_t) * (size_t)k);
        Ensure(&out_buf, 64 * 4 * (size_t)k);
        memcpy(in_buf.map, groups + at, sizeof(gpugroup_t) * (size_t)k);
        pc.ngroups = k;
        gbuf_t *bufs[8] = {NULL, &in_buf, &lightbuf, &pvsbuf, &skydirs, &skymapbuf, &propids, &out_buf};
        Run(&prog_gather, &shadow_tlas, bufs, &pc, sizeof(pc), 4 * (uint32_t)k);
        memcpy(out + 64 * (size_t)at, out_buf.map, 64 * 4 * (size_t)k);
    }
}

/* ------------------------------------------------------------------ the map for the surface finder */
static gbuf_t walkbufs[14], lightdata, anorms;      /* (walk.glsl's bindings 1-14: the last the lightmaps) */

void GPU_WalkScene(const gpuwalk_t *w) {
    const void *src[13] = {w->planes, w->nodes, w->leaves, w->leaffaces, w->faces, w->windings, w->disps, w->dverts, w->dtris,
                           w->dnodes, w->dorder, w->dlux, w->leafdisps};
    size_t size[13] = {16 * (size_t)w->nplanes, 16 * (size_t)w->nnodes, 16 * (size_t)w->nleaves, 4 * (size_t)w->nleaffaces,
                       sizeof(gpuwface_t) * w->nfaces, 16 * (size_t)w->nwindings, sizeof(gpuwdisp_t) * w->ndisps,
                       16 * (size_t)w->ndverts, 16 * (size_t)w->ndtris, sizeof(gpuwnode_t) * w->ndnodes, 4 * (size_t)w->ndorder,
                       8 * (size_t)w->ndlux, 4 * (size_t)w->nleafdisps};
    for (int i = 0; i < 13; i++) walkbufs[i] = Upload(size[i] ? src[i] : NULL, size[i] ? size[i] : 16);
    float a[NUMVERTEXNORMALS][4];
    for (int i = 0; i < NUMVERTEXNORMALS; i++) memcpy(a[i], g_anorms[i], 12), a[i][3] = 0;
    anorms = Upload(a, sizeof(a));
}

/* the lightmaps as they are now (after the faces are lit) */
void GPU_Lightmaps(void) {
    FreeBuffer(&lightdata);
    lightdata = Upload(dlightdata, ((size_t)lightdatasize + 3) & ~(size_t)3);
}

/* (the walk's buffers, then the sky map, the directions, the input and output) */
static void WalkBuffers(gbuf_t **bufs, gbuf_t *dirs) {
    bufs[0] = NULL;
    for (int i = 0; i < 13; i++) bufs[1 + i] = &walkbufs[i];
    bufs[14] = &lightdata, bufs[15] = &skymapbuf, bufs[16] = dirs, bufs[17] = &in_buf, bufs[18] = &out_buf;
}

#define POINT_CHUNK 65536

void GPU_Ambient(const float *points, int n, const float skylight[4], float *out) {
    int w = 0, h = 0;
    SkyMapData(&w, &h);
    struct { int npoints, haveSkyMap, skymapW, skymapH; float skylight[4]; int fix; } pc;
    pc.haveSkyMap = HaveSkyMap(), pc.skymapW = w, pc.skymapH = h, pc.fix = g_bFixQuirks;
    memcpy(pc.skylight, skylight, 16);
    int chunk = JobSize(NUMVERTEXNORMALS, WALK_BUDGET, POINT_CHUNK);
    for (int at = 0; at < n; at += chunk) {
        int k = n - at < chunk ? n - at : chunk;
        Ensure(&in_buf, 12 * (size_t)k);
        Ensure(&out_buf, 72 * (size_t)k);
        memcpy(in_buf.map, points + 3 * (size_t)at, 12 * (size_t)k);
        pc.npoints = k;
        gbuf_t *bufs[19];
        WalkBuffers(bufs, &anorms);
        Run(&prog_ambient, &shadow_tlas, bufs, &pc, sizeof(pc), (uint32_t)k);
        memcpy(out + 18 * (size_t)at, out_buf.map, 72 * (size_t)k);
    }
}

void GPU_PropIndirect(const float *points, int n, float *out) {
    struct { int npoints, nskyFull, fast, fix; } pc = {0, (int)(g_flSkySampleScale * 162.0f), g_bFast, g_bFixQuirks};
    int chunk = JobSize(pc.nskyFull, WALK_BUDGET, POINT_CHUNK);
    for (int at = 0; at < n; at += chunk) {
        int k = n - at < chunk ? n - at : chunk;
        Ensure(&in_buf, 28 * (size_t)k);
        Ensure(&out_buf, 12 * (size_t)k);
        memcpy(in_buf.map, points + 7 * (size_t)at, 28 * (size_t)k);
        pc.npoints = k;
        gbuf_t *bufs[19];
        WalkBuffers(bufs, &skydirs);
        Run(&prog_propind, &shadow_tlas, bufs, &pc, sizeof(pc), (uint32_t)k);
        memcpy(out + 3 * (size_t)at, out_buf.map, 12 * (size_t)k);
    }
}
