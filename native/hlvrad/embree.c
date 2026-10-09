/* Intel Embree (-embree): a much faster ray tracer than vrad's, for when the lighting needn't match vrad to the bit.
 * Its hits differ from vrad's tracer only in rays that graze an edge, which no one can see.
 *
 * embree4.dll (and its tbb12.dll) sit next to hlvrad.exe and are loaded when asked for, so without them hlvrad
 * still runs (vrad's tracer, with a warning). Triangles are 9 floats each (3 corners); a hit gives the triangle's
 * number, its distance and where on it (u, v: the hit is v0 + u (v1 - v0) + v (v2 - v0)). */
#include <windows.h>
#include <xmmintrin.h>
#include "hlvrad.h"
#include "embree4/rtcore.h"

int g_bEmbree;

#define EMBREE_FUNCS(X) X(rtcNewDevice) X(rtcGetDeviceError) X(rtcNewScene) X(rtcSetSceneFlags) \
    X(rtcSetSceneBuildQuality) X(rtcNewGeometry) X(rtcSetSharedGeometryBuffer) X(rtcCommitGeometry) \
    X(rtcAttachGeometry) X(rtcReleaseGeometry) X(rtcCommitScene) X(rtcIntersect1) X(rtcIntersect4) X(rtcOccluded4)
#define DECLARE(f) static __typeof__(f) *p_##f;
EMBREE_FUNCS(DECLARE)

static RTCDevice device;

/* (Embree wants denormals flushed to zero while it traces, or it slows to a crawl; hlvrad's own maths keeps them) */
#define FTZ_BEGIN unsigned csr = _mm_getcsr(); _mm_setcsr(csr | 0x8040)
#define FTZ_END _mm_setcsr(csr)

/* load embree4.dll from hlvrad's folder: 0 (and a warning) when it can't be */
int EM_Init(void) {
    static int tried, ok;
    if (tried) return ok;
    tried = 1;
    char path[MAX_PATH];
    GetModuleFileNameA(NULL, path, sizeof(path));
    char *slash = strrchr(path, 92);
    snprintf(slash ? slash + 1 : path, sizeof(path) - (slash ? (size_t)(slash + 1 - path) : 0), "embree4.dll");
    /* (its tbb12.dll is found in the same folder: LoadLibraryEx with the DLL's own folder searched first) */
    HMODULE m = LoadLibraryExA(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!m) {
        Msg("Warning: can't load %s: using vrad's ray tracer\n", path);
        return 0;
    }
#define LOAD(f) if (!(p_##f = (__typeof__(f) *)(void *)GetProcAddress(m, #f))) { Msg("Warning: embree4.dll has no " #f "\n"); return 0; }
    EMBREE_FUNCS(LOAD)
    device = p_rtcNewDevice(NULL);
    if (!device) {
        Msg("Warning: Embree didn't start: using vrad's ray tracer\n");
        return 0;
    }
    ok = 1;
    return 1;
}

/* a scene of triangles (verts: 9 floats per triangle, with 16 bytes readable past the end) */
void *EM_NewScene(const float *verts, int ntris) {
    RTCScene scene = p_rtcNewScene(device);
    /* (not RTC_SCENE_FLAG_ROBUST: measured 15-45% slower on det, for edge-grazing rays no one can see) */
    p_rtcSetSceneFlags(scene, RTC_SCENE_FLAG_FILTER_FUNCTION_IN_ARGUMENTS);
    p_rtcSetSceneBuildQuality(scene, RTC_BUILD_QUALITY_HIGH);
    if (ntris > 0) {
        RTCGeometry g = p_rtcNewGeometry(device, RTC_GEOMETRY_TYPE_TRIANGLE);
        unsigned *idx = xalloc(sizeof(unsigned) * 3 * ntris);    /* (kept: Embree reads it while tracing) */
        for (int i = 0; i < 3 * ntris; i++) idx[i] = (unsigned)i;
        p_rtcSetSharedGeometryBuffer(g, RTC_BUFFER_TYPE_VERTEX, 0, RTC_FORMAT_FLOAT3, verts, 0, 12, 3 * (size_t)ntris);
        p_rtcSetSharedGeometryBuffer(g, RTC_BUFFER_TYPE_INDEX, 0, RTC_FORMAT_UINT3, idx, 0, 12, (size_t)ntris);
        p_rtcCommitGeometry(g);
        p_rtcAttachGeometry(scene, g);
        p_rtcReleaseGeometry(g);
    }
    p_rtcCommitScene(scene);
    if (p_rtcGetDeviceError(device) != RTC_ERROR_NONE) Error("Embree couldn't build the scene");
    return scene;
}

/* (a triangle a ray should pass through: skip(tri, data) says) */
typedef struct {
    struct RTCRayQueryContext ctx;     /* (first: Embree hands the filter this pointer) */
    int (*skip)(int tri, void *data);
    void *data;
} skipctx_t;

/* (the hits are N of each field in turn: Ng x, y, z, u, v, primID, ...) */
static void SkipFilter(const struct RTCFilterFunctionNArguments *a) {
    const skipctx_t *c = (const skipctx_t *)a->context;
    for (unsigned i = 0; i < a->N; i++)
        if (a->valid[i] && c->skip((int)((const unsigned *)a->hit)[5 * a->N + i], c->data)) a->valid[i] = 0;
}

static void SetSkip(skipctx_t *c, int (*skip)(int tri, void *data), void *data, enum RTCRayQueryFlags *flags,
                    struct RTCRayQueryContext **ctx, RTCFilterFunctionN *filter) {
    if (!skip) return;
    rtcInitRayQueryContext(&c->ctx);
    c->skip = skip, c->data = data;
    *ctx = &c->ctx;
    *filter = SkipFilter;
    *flags = (enum RTCRayQueryFlags)(*flags | RTC_RAY_QUERY_FLAG_INVOKE_ARGUMENT_FILTER);
}

/* the nearest triangle on o + t d, tnear < t < tfar (-1: none) */
int EM_Nearest(void *scene, const float o[3], const float d[3], float tnear, float tfar, int (*skip)(int tri, void *data),
               void *data, float *t, float *u, float *v) {
    struct RTCRayHit rh;
    memset(&rh, 0, sizeof(rh));
    rh.ray.org_x = o[0], rh.ray.org_y = o[1], rh.ray.org_z = o[2];
    rh.ray.dir_x = d[0], rh.ray.dir_y = d[1], rh.ray.dir_z = d[2];
    rh.ray.tnear = tnear, rh.ray.tfar = tfar;
    rh.ray.mask = 0xffffffffu;
    rh.hit.geomID = RTC_INVALID_GEOMETRY_ID;
    struct RTCIntersectArguments args;
    rtcInitIntersectArguments(&args);
    skipctx_t c;
    SetSkip(&c, skip, data, &args.flags, &args.context, &args.filter);
    FTZ_BEGIN;
    p_rtcIntersect1((RTCScene)scene, &rh, &args);
    FTZ_END;
    if (rh.hit.geomID == RTC_INVALID_GEOMETRY_ID) return -1;
    *t = rh.ray.tfar;
    if (u) *u = rh.hit.u, *v = rh.hit.v;
    return (int)rh.hit.primID;
}

/* 4 rays at once (rays with tfar < tnear are left out): each one's nearest triangle (-1: none) and distance */
void EM_Nearest4(void *scene, const float o[3][4], const float d[3][4], const float tnear[4], const float tfar[4],
                 int (*skip)(int tri, void *data), void *data, int hit[4], float t[4]) {
    struct RTCRayHit4 rh;
    int valid[4];
    memset(&rh, 0, sizeof(rh));
    for (int i = 0; i < 4; i++) {
        rh.ray.org_x[i] = o[0][i], rh.ray.org_y[i] = o[1][i], rh.ray.org_z[i] = o[2][i];
        rh.ray.dir_x[i] = d[0][i], rh.ray.dir_y[i] = d[1][i], rh.ray.dir_z[i] = d[2][i];
        rh.ray.tnear[i] = tnear[i], rh.ray.tfar[i] = tfar[i];
        rh.ray.mask[i] = 0xffffffffu;
        rh.hit.geomID[i] = RTC_INVALID_GEOMETRY_ID;
        valid[i] = tnear[i] <= tfar[i] ? -1 : 0;
    }
    struct RTCIntersectArguments args;
    rtcInitIntersectArguments(&args);
    args.flags = RTC_RAY_QUERY_FLAG_COHERENT;     /* (the 4 rays go out from about the same place) */
    skipctx_t c;
    SetSkip(&c, skip, data, &args.flags, &args.context, &args.filter);
    FTZ_BEGIN;
    p_rtcIntersect4(valid, (RTCScene)scene, &rh, &args);
    FTZ_END;
    for (int i = 0; i < 4; i++) {
        int h = valid[i] && rh.hit.geomID[i] != RTC_INVALID_GEOMETRY_ID;
        hit[i] = h ? (int)rh.hit.primID[i] : -1;
        t[i] = h ? rh.ray.tfar[i] : 1.0e23f;
    }
}

/* is anything on o + t d, tnear < t < tfar? (4 rays; rays with tfar < tnear are left out: not blocked) */
void EM_Blocked4(void *scene, const float o[3][4], const float d[3][4], const float tnear[4], const float tfar[4],
                 int (*skip)(int tri, void *data), void *data, int blocked[4]) {
    struct RTCRay4 r;
    int valid[4];
    memset(&r, 0, sizeof(r));
    for (int i = 0; i < 4; i++) {
        r.org_x[i] = o[0][i], r.org_y[i] = o[1][i], r.org_z[i] = o[2][i];
        r.dir_x[i] = d[0][i], r.dir_y[i] = d[1][i], r.dir_z[i] = d[2][i];
        r.tnear[i] = tnear[i], r.tfar[i] = tfar[i];
        r.mask[i] = 0xffffffffu;
        valid[i] = tnear[i] <= tfar[i] ? -1 : 0;
    }
    struct RTCOccludedArguments args;
    rtcInitOccludedArguments(&args);
    args.flags = RTC_RAY_QUERY_FLAG_COHERENT;
    skipctx_t c;
    SetSkip(&c, skip, data, &args.flags, &args.context, &args.filter);
    FTZ_BEGIN;
    p_rtcOccluded4(valid, (RTCScene)scene, &r, &args);
    FTZ_END;
    for (int i = 0; i < 4; i++) blocked[i] = valid[i] && r.tfar[i] < 0;      /* (a blocked ray's tfar becomes -inf) */
}
