/* hlvrad command line (vrad's): hlvrad [options] -game <dir> <map>
 *
 * Done so far: the HDR faces, the smoothed vertex normals, the lightmap layout (values still zero). */
#include <float.h>
#include <windows.h>
#include "hlvrad.h"

int g_bHDR = 1;
int g_bFast;
int g_bExtra = 1;
static int g_bLDR = 0;

vec_t VectorNormalize(vec3_t v) {
    /* mathlib's (x87): the squares summed in double, the length rounded to float */
    float radius = (float)sqrt((double)v[0] * v[0] + (double)v[1] * v[1] + (double)v[2] * v[2]);
    float iradius = 1.f / (radius + FLT_EPSILON);
    v[0] *= iradius;
    v[1] *= iradius;
    v[2] *= iradius;
    return radius;
}

/* the same, returning the length unrounded (as x87 code gets it) */
double VectorNormalizeD(vec3_t v) {
    double r = sqrt((double)v[0] * v[0] + (double)v[1] * v[1] + (double)v[2] * v[2]);
    float iradius = 1.f / ((float)r + FLT_EPSILON);
    v[0] *= iradius;
    v[1] *= iradius;
    v[2] *= iradius;
    return r;
}

/* each stage's time (-timing) */
static int g_timing;
static double Now(void) {
    LARGE_INTEGER f, c;
    QueryPerformanceFrequency(&f), QueryPerformanceCounter(&c);
    return (double)c.QuadPart / (double)f.QuadPart;
}
static double t_last;
static void Stage(const char *name) {
    double t = Now();
    if (g_timing && name) Msg("[time] %-28s %7.2f s\n", name, t - t_last);
    t_last = t;
}

static void FacelightsWork(int face, int thread) { (void)thread; BuildFacelights(face); }

/* -visfrom <map>: light the map while vis runs on a copy of it. The faces' light and bounce (GPU ray traced)
 * don't need vis: vis only spares rays a light can't reach (measured: the same lightmaps to the byte). What
 * comes after does (props' and detail props' lights, the sky's leaves): before it, wait for <map>.visdone
 * (the build writes it when vis has finished; <map>.visfailed: give up), take the vis from <map> and make the
 * lights again with it. The written map is then the one lighting after vis would give. */
static const char *g_visFrom;
static int g_tookVis, g_visThreadsAfter = -1;     /* (-visthreads n: threads while vis runs; then -threads' own) */
static void TakeVis(void) {
    if (g_tookVis) return;
    g_tookVis = 1;
    char done[1100], failed[1100];
    snprintf(done, sizeof(done), "%s.visdone", g_visFrom);
    snprintf(failed, sizeof(failed), "%s.visfailed", g_visFrom);
    for (;;) {
        if (GetFileAttributesA(done) != INVALID_FILE_ATTRIBUTES) break;
        if (GetFileAttributesA(failed) != INVALID_FILE_ATTRIBUTES) {
            Msg("Vis failed: not lighting\n");
            exit(3);
        }
        Sleep(20);
    }
    /* what vis writes: the visibility, leaves' distance to water and its leaf ambient (lighting rewrites the HDR) */
    static const int vis_lumps[] = {LUMP_VISIBILITY, 46, 51, 52, 55, 56};
    for (int k = 0; k < (int)(sizeof(vis_lumps) / sizeof(vis_lumps[0])); k++) {
        unsigned char *data;
        int len, version;
        if (!ReadLumpFrom(g_visFrom, vis_lumps[k], &data, &len, &version)) Error("Can't read the vis from %s", g_visFrom);
        SetLump(vis_lumps[k], data, len, version);
    }
    MapVis();
    CreateDirectLights();
    if (g_bGPU) GPU_Lights();
    /* (vis is done: the rest of the build waits on this, so every core, at normal priority) */
    if (g_visThreadsAfter >= 0) g_numthreads = g_visThreadsAfter;
    SetPriorityClass(GetCurrentProcess(), NORMAL_PRIORITY_CLASS);
}
static void FinalLightWork(int face, int thread) { (void)thread; FinalLightFace(face); }

int main(int argc, char **argv) {
    const char *map = NULL, *designer_lights = NULL, *skymap_path = NULL;
    Msg("Hammerless hlvrad\n");
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (!_stricmp(a, "-hdr")) g_bHDR = 1, g_bLDR = 0;
        else if (!_stricmp(a, "-ldr")) g_bHDR = 0, g_bLDR = 1;
        else if (!_stricmp(a, "-both")) g_bHDR = 1, g_bLDR = 1;
        else if (!_stricmp(a, "-game") && i + 1 < argc) g_gamedir = argv[++i];
        else if (!_stricmp(a, "-modeldir") && i + 1 < argc) g_modeldir = argv[++i];
        else if (!_stricmp(a, "-lights") && i + 1 < argc) designer_lights = argv[++i];
        else if (!_stricmp(a, "-bounce") && i + 1 < argc) g_numbounce = atoi(argv[++i]);
        else if (!_stricmp(a, "-threads") && i + 1 < argc) g_numthreads = atoi(argv[++i]);
        else if (!_stricmp(a, "-embree")) g_bEmbree = 1;         /* (Embree's tracer: faster, not vrad's to the bit) */
        else if (!_stricmp(a, "-gpu")) g_bGPU = 1;               /* (the GPU's ray tracing: faster, not vrad's to the bit) */
        else if (!_stricmp(a, "-gi") && i + 1 < argc) g_giPasses = atoi(argv[++i]);    /* (bounces by final gathering: -gpu) */
        else if (!_stricmp(a, "-girays") && i + 1 < argc) g_giRays = atoi(argv[++i]);            /* (its rays a sample) */
        else if (!_stricmp(a, "-chop") && i + 1 < argc) minchop = (float)atof(argv[++i]);     /* (patches at a face's edges) */
        else if (!_stricmp(a, "-maxchop") && i + 1 < argc) maxchop = (float)atof(argv[++i]);  /* (and inside it) */
        else if (!_stricmp(a, "-dispchop")) {
            if (++i >= argc) Error("expected a value after '%s'", a);
        }
        /* beyond vrad (Hammerless's own): supersampling's points across a luxel (4: vrad's 4 x 4), passes and the
         * brightness step that triggers it; -fixquirks: vrad's oddities left out (see direct.c, leafambient.c) */
        else if (!_stricmp(a, "-sspoints") && i + 1 < argc) g_ssPoints = atoi(argv[++i]);
        else if (!_stricmp(a, "-sspasses") && i + 1 < argc) g_ssPasses = atoi(argv[++i]);
        else if (!_stricmp(a, "-ssthreshold") && i + 1 < argc) g_ssThreshold = (float)atof(argv[++i]);
        else if (!_stricmp(a, "-fixquirks")) g_bFixQuirks = 1; else if (!_stricmp(a, "-smooth")) {
            if (++i >= argc) Error("expected an angle after '-smooth'");
            smoothing_threshold = (float)cos(atof(argv[i]) * (3.14159265358979323846 / 180.0));
        } else if (!_stricmp(a, "-fast")) {
            g_bFast = 1;
        } else if (!_stricmp(a, "-skymap") && i + 1 < argc) {
            skymap_path = argv[++i];
        } else if (!_stricmp(a, "-final")) {
            g_flSkySampleScale = 16.0f;
        } else if (!_stricmp(a, "-extrasky") && i + 1 < argc) {
            g_flSkySampleScale = (float)atof(argv[++i]);
        } else if (!_stricmp(a, "-StaticPropPolys")) {
            g_bStaticPropPolys = 1;
        } else if (!_stricmp(a, "-textureshadows")) {
            Error("-textureshadows isn't supported yet");
        } else if (!_stricmp(a, "-StaticPropLighting")) {
            g_bStaticPropLighting = 1;
        } else if (!_stricmp(a, "-visfrom") && i + 1 < argc) {
            g_visFrom = argv[++i];
        } else if (!_stricmp(a, "-visthreads") && i + 1 < argc) {
            g_visThreadsAfter = atoi(argv[++i]);          /* (swapped in below, once the options are read) */
        } else if (!_stricmp(a, "-timing")) {
            g_timing = 1;
        } else if (!_stricmp(a, "-noextra")) {
            g_bExtra = 0;
        } else if (!_stricmp(a, "-extra")) {
            g_bExtra = 1;
        } else if (a[0] == '-') {
            /* -fast, -final, -StaticPropLighting, ...: accepted (not all done yet) */
        } else map = a;
    }
    if (!map) Error("usage: hlvrad [options] -game <gamedir> <map>");
    if (g_visFrom && g_visThreadsAfter >= 0) {      /* (few threads while vis runs: it has the rest of the CPU) */
        int during = g_visThreadsAfter;
        g_visThreadsAfter = g_numthreads;
        g_numthreads = during;
    } else g_visThreadsAfter = -1;
    if (g_bLDR) Error("LDR lighting isn't supported yet (L4D2 uses HDR)");
    if (g_ssPoints < 1 || g_ssPoints > 16) Error("-sspoints: 1 to 16");
    if (maxchop < minchop) maxchop = minchop;
    char path[1024];
    snprintf(path, sizeof(path), "%s", map);
    size_t n = strlen(path);
    if (n < 4 || _stricmp(path + n - 4, ".bsp")) strncat(path, ".bsp", sizeof(path) - n - 1);
    Stage(NULL);
    if (g_bGPU) GPU_StartInit();
    if (skymap_path) LoadSkyMap(skymap_path);
    LoadBSPFile(path);
    MapArrays();
    /* light the HDR copy of the faces (made from the faces the first time) */
    if (!lumps[LUMP_FACES_HDR].len && lumps[LUMP_FACES].len) {      /* (no faces: the lump stays as it was) */
        unsigned char *copy = xalloc(lumps[LUMP_FACES].len);
        memcpy(copy, lumps[LUMP_FACES].data, lumps[LUMP_FACES].len);
        SetLump(LUMP_FACES_HDR, copy, lumps[LUMP_FACES].len, lumps[LUMP_FACES].version);
    }
    g_pFaces = (dface_t *)lumps[LUMP_FACES_HDR].data;
    /* the map's flags (lump 59) say whether static props have baked vertex light (bit 1 LDR, bit 2 HDR) */
    {
        unsigned int flags = 0;
        if (lumps[59].len >= 4) memcpy(&flags, lumps[59].data, 4);
        if (g_bStaticPropLighting) flags |= g_bHDR ? 2u : 1u;
        else flags &= ~3u;
        if (lumps[59].len >= 4 || flags) {
            unsigned char *d = xalloc(4);
            memcpy(d, &flags, 4);
            SetLump(59, d, 4, lumps[59].version);
        }
    }
    Stage("load");
    MapVis();
    if (!HaveVis() && g_numbounce > 0 && !g_visFrom) {         /* (as vrad: without vis, every patch would see every other) */
        Msg("No vis information, direct lighting only.\n");
        g_numbounce = 0;
    }
    ParseEntities();
    FindFacePatches();
    LoadDisplacements();
    LoadTexLights(g_gamedir, path, designer_lights);
    MakePatches();
    PairEdges();
    SaveVertexNormals();
    SubdividePatches();
    AddDispsToClusterTable();
    Stage("setup, patches");
    {
        int n = (int)(g_flSkySampleScale * 162.0f);
        BuildSkyDirections(n > 162 ? n : 162);
    }
    CreateDirectLights();
    Stage("direct lights");
    if (getenv("SKYDBG")) {      /* (debugging: a point's cluster and whether each light's PVS has it) */
        vec3_t p;
        sscanf(getenv("SKYDBG"), "%f %f %f", &p[0], &p[1], &p[2]);
        int c = ClusterFromPoint(p);
        Msg("cluster %d\n", c);
        for (directlight_t *dl = activelights; dl; dl = dl->next) Msg("light type %d cluster %d sees %d\n", dl->light.type, dl->light.cluster, PVSCheck(dl->pvs, c));
    }
    AddBrushesForRayTrace();
    Stage("tracer: brushes");
    AddDispsForRayTrace();
    Stage("tracer: displacements");
    AddStaticPropsForRayTrace();
    Stage("tracer: props");
    RT_SetupAccelerationStructure();
    Stage("ray tracer");
    if (g_bGPU && !GPU_Init()) g_bGPU = 0;
    if (g_bGPU) {
        GPU_ShadowScene();
        GPU_Lights();
        Stage("gpu: setup");
    }
    if (getenv("RTTEST")) {       /* (debugging: trace packets of 4 rays, as vradhook does with vrad's tracer) */
        FILE *rf = fopen(getenv("RTTEST"), "rb"), *of = fopen("raysout_ours.bin", "wb");
        float pk[32];
        int mask, hit[4];
        float dist[4];
        while (fread(pk, 4, 32, rf) == 32 && fread(&mask, 4, 1, rf) == 1) {
            RT_Trace4Mask((const float(*)[4])pk, (const float(*)[4])(pk + 12), pk + 28, mask, hit, dist);
            for (int i = 0; i < 4; i++) hit[i] = hit[i] == -1 ? -1 : RT_TriangleID(hit[i]);
            fwrite(hit, 4, 4, of), fwrite(dist, 4, 4, of);
        }
        fclose(of), fclose(rf);
        return 0;
    }
    AllocFacelights();
    if (g_bGPU) BuildFacelightsGPU();
    else RunThreadsOn(numfaces, FacelightsWork);
    Stage("direct lighting (faces)");
    PrecompLightmapOffsets();
    if (g_giPasses && !g_bGPU) {
        Msg("Warning: -gi needs the GPU: vrad's bounced light instead\n");
        g_giPasses = 0;
    }
    if (g_visFrom && g_numbounce > 0 && !g_giPasses) {   /* (vrad's bounce needs vis: wait for it here) */
        TakeVis();
        Stage("waiting for vis");
        if (!HaveVis()) {
            Msg("No vis information, direct lighting only.\n");
            g_numbounce = 0;
        }
    }
    if (g_numbounce > 0 && !g_giPasses) {
        Stage("lightmap offsets");
        MakeAllScales();
        Stage("transfers");
        BounceLight();
        Stage("bounce");
    }
    PrepareFinalLight();
    RunThreadsOn(numfaces, FinalLightWork);
    Stage("final light");
    if (g_giPasses) {
        BuildIndirectGPU();
        Stage("gi (bounced light)");
    }
    if (g_visFrom) {
        TakeVis();
        Stage("waiting for vis");
    }
    ExportDirectLightsToWorldLights();
    ComputeDetailPropLighting();
    Stage("detail props");
    ComputePerLeafAmbientLighting();
    Stage("leaf ambient");
    ComputeStaticPropLighting();
    Stage("static prop lighting");
    SetLump(LUMP_LIGHTING_HDR, dlightdata, lightdatasize, 1);
    WriteBSPFile(path);
    Stage("write");
    Msg("done\n");
    return 0;
}
