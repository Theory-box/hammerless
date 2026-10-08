/* hlvrad command line (vrad's): hlvrad [options] -game <dir> <map>
 *
 * Done so far: the HDR faces, the smoothed vertex normals, the lightmap layout (values still zero). */
#include <float.h>
#include "hlvrad.h"

int g_bHDR = 1;
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

int main(int argc, char **argv) {
    const char *map = NULL;
    Msg("Hammerless hlvrad\n");
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (!_stricmp(a, "-hdr")) g_bHDR = 1, g_bLDR = 0;
        else if (!_stricmp(a, "-ldr")) g_bHDR = 0, g_bLDR = 1;
        else if (!_stricmp(a, "-both")) g_bHDR = 1, g_bLDR = 1;
        else if (!_stricmp(a, "-game") || !_stricmp(a, "-threads") || !_stricmp(a, "-bounce") ||
                 !_stricmp(a, "-extrasky") || !_stricmp(a, "-lights") || !_stricmp(a, "-chop") ||
                 !_stricmp(a, "-maxchop") || !_stricmp(a, "-dispchop")) {
            if (++i >= argc) Error("expected a value after '%s'", a);
        } else if (!_stricmp(a, "-smooth")) {
            if (++i >= argc) Error("expected an angle after '-smooth'");
            smoothing_threshold = (float)cos(atof(argv[i]) * (3.14159265358979323846 / 180.0));
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
    if (!lumps[LUMP_FACES_HDR].len) {
        unsigned char *copy = xalloc(lumps[LUMP_FACES].len);
        memcpy(copy, lumps[LUMP_FACES].data, lumps[LUMP_FACES].len);
        SetLump(LUMP_FACES_HDR, copy, lumps[LUMP_FACES].len, lumps[LUMP_FACES].version);
    }
    g_pFaces = (dface_t *)lumps[LUMP_FACES_HDR].data;
    MapVis();
    ParseEntities();
    PairEdges();
    SaveVertexNormals();
    CreateDirectLights();
    AssignLightStyles();
    PrecompLightmapOffsets();
    ExportDirectLightsToWorldLights();
    SetLump(LUMP_LIGHTING_HDR, dlightdata, lightdatasize, 1);
    WriteBSPFile(path);
    Msg("done\n");
    return 0;
}
