/* hlvrad command line (vrad's): hlvrad [options] -game <dir> <map>
 *
 * Done so far: the HDR faces, the smoothed vertex normals, the lightmap layout (values still zero). */
#include <float.h>
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

int main(int argc, char **argv) {
    const char *map = NULL, *designer_lights = NULL;
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
        else if (!_stricmp(a, "-threads") ||
                 !_stricmp(a, "-extrasky") || !_stricmp(a, "-chop") ||
                 !_stricmp(a, "-maxchop") || !_stricmp(a, "-dispchop")) {
            if (++i >= argc) Error("expected a value after '%s'", a);
        } else if (!_stricmp(a, "-smooth")) {
            if (++i >= argc) Error("expected an angle after '-smooth'");
            smoothing_threshold = (float)cos(atof(argv[i]) * (3.14159265358979323846 / 180.0));
        } else if (!_stricmp(a, "-fast")) {
            g_bFast = 1;
        } else if (!_stricmp(a, "-StaticPropLighting")) {
            g_bStaticPropLighting = 1;
        } else if (!_stricmp(a, "-noextra")) {
            g_bExtra = 0;
        } else if (!_stricmp(a, "-extra")) {
            g_bExtra = 1;
        } else if (a[0] == '-') {
            /* -fast, -final, -StaticPropLighting, ...: accepted (not all done yet) */
        } else map = a;
    }
    if (!map) Error("usage: hlvrad [options] -game <gamedir> <map>");
    if (g_bLDR) Error("LDR lighting isn't supported yet (L4D2 uses HDR)");
    char path[1024];
    snprintf(path, sizeof(path), "%s", map);
    size_t n = strlen(path);
    if (n < 4 || _stricmp(path + n - 4, ".bsp")) strncat(path, ".bsp", sizeof(path) - n - 1);
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
    MapVis();
    ParseEntities();
    FindFacePatches();
    LoadDisplacements();
    LoadTexLights(g_gamedir, path, designer_lights);
    MakePatches();
    PairEdges();
    SaveVertexNormals();
    SubdividePatches();
    AddDispsToClusterTable();
    CreateDirectLights();
    if (getenv("SKYDBG")) {      /* (debugging: a point's cluster and whether each light's PVS has it) */
        vec3_t p;
        sscanf(getenv("SKYDBG"), "%f %f %f", &p[0], &p[1], &p[2]);
        int c = ClusterFromPoint(p);
        Msg("cluster %d\n", c);
        for (directlight_t *dl = activelights; dl; dl = dl->next) Msg("light type %d cluster %d sees %d\n", dl->light.type, dl->light.cluster, PVSCheck(dl->pvs, c));
    }
    AddBrushesForRayTrace();
    AddDispsForRayTrace();
    AddStaticPropsForRayTrace();
    RT_SetupAccelerationStructure();
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
    for (int i = 0; i < numfaces; i++) BuildFacelights(i);
    PrecompLightmapOffsets();
    if (g_numbounce > 0) {
        MakeAllScales();
        BounceLight();
    }
    for (int i = 0; i < numfaces; i++) FinalLightFace(i);
    ExportDirectLightsToWorldLights();
    ComputeDetailPropLighting();
    ComputePerLeafAmbientLighting();
    ComputeStaticPropLighting();
    SetLump(LUMP_LIGHTING_HDR, dlightdata, lightdatasize, 1);
    WriteBSPFile(path);
    Msg("done\n");
    return 0;
}
