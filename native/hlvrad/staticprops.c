/* Static props in the ray tracer (vrad's CVradStaticPropMgr: Init and AddPolysForRayTrace).
 *
 * Each prop casts shadows with its model's collision model (the first solid of its .phy, triangulated by
 * the game's vphysics.dll like vrad does), placed by its origin and angles. A model without one gets one
 * built like L4D2's vrad does: a convex hull around each mesh's vertices. Only a model that can't be read
 * casts a box shadow from its hull bounds. Props flagged "no shadow" cast none. The model files come
 * from -modeldir (the Python side copies them out of the game's VPKs). */
#include <windows.h>
#include <process.h>
#include <float.h>
#include "hlvrad.h"

#define STATIC_PROP_NO_SHADOW 0x10
#define PROP_RECORD 72

const char *g_modeldir;
const char *g_gamedir;
unsigned char *ReadModelFile(const char *model, const char *ext, int *len);

/* VMatrix::SetupMatrixOrgAngles (sines and cosines of float radians) */
static void MatrixOrgAngles(const vec3_t origin, const vec3_t angles, float m[3][4]) {
    const float d2r = (float)(3.14159265358979323846 / 180.0);
    double y = angles[1] * d2r, p = angles[0] * d2r, r = angles[2] * d2r;
    float sy = (float)sin(y), cy = (float)cos(y), sp = (float)sin(p), cp = (float)cos(p), sr = (float)sin(r), cr = (float)cos(r);
    m[0][0] = cp * cy;
    m[1][0] = cp * sy;
    m[2][0] = -sp;
    float srsp = sr * sp, crsp = cr * sp;      /* (VMatrix::SetupMatrixOrgAngles' grouping) */
    m[0][1] = srsp * cy - cr * sy;
    m[1][1] = srsp * sy + cr * cy;
    m[2][1] = sr * cp;
    m[0][2] = crsp * cy + sr * sy;
    m[1][2] = crsp * sy - sr * cy;
    m[2][2] = cr * cp;
    for (int k = 0; k < 3; k++) m[k][3] = origin[k];
}

static void Transform(float m[3][4], const float *v, vec3_t out) {
    for (int k = 0; k < 3; k++) out[k] = ((m[k][0] * v[0] + m[k][1] * v[1]) + m[k][2] * v[2]) + m[k][3];
}

static void AddQuad(int id, const vec3_t a, const vec3_t b, const vec3_t c, const vec3_t d) {
    RT_AddTriangle(id, a, b, c);
    RT_AddTriangle(id + 1, a, c, d);       /* (Valve's AddQuad: the second triangle gets id + 1) */
}

static void AddBox(int id, const vec3_t mn, const vec3_t mx) {
    vec3_t p[8];
    /* far, near, left, right, top, bottom, as RayTracingEnvironment::AddAxisAlignedRectangularSolid */
#define V(i, x, y, z) (p[i][0] = (x), p[i][1] = (y), p[i][2] = (z))
    V(0, mn[0], mx[1], mx[2]); V(1, mx[0], mx[1], mx[2]); V(2, mx[0], mn[1], mx[2]); V(3, mn[0], mn[1], mx[2]);
    AddQuad(id, p[0], p[1], p[2], p[3]);
    V(0, mn[0], mx[1], mn[2]); V(1, mx[0], mx[1], mn[2]); V(2, mx[0], mn[1], mn[2]); V(3, mn[0], mn[1], mn[2]);
    AddQuad(id, p[0], p[1], p[2], p[3]);
    V(0, mn[0], mx[1], mx[2]); V(1, mn[0], mx[1], mn[2]); V(2, mn[0], mn[1], mn[2]); V(3, mn[0], mn[1], mx[2]);
    AddQuad(id, p[0], p[1], p[2], p[3]);
    V(0, mx[0], mx[1], mx[2]); V(1, mx[0], mx[1], mn[2]); V(2, mx[0], mn[1], mn[2]); V(3, mx[0], mn[1], mx[2]);
    AddQuad(id, p[0], p[1], p[2], p[3]);
    V(0, mn[0], mx[1], mx[2]); V(1, mx[0], mx[1], mx[2]); V(2, mx[0], mx[1], mn[2]); V(3, mn[0], mx[1], mn[2]);
    AddQuad(id, p[0], p[1], p[2], p[3]);
    V(0, mn[0], mn[1], mx[2]); V(1, mx[0], mn[1], mx[2]); V(2, mx[0], mn[1], mn[2]); V(3, mn[0], mn[1], mn[2]);
    AddQuad(id, p[0], p[1], p[2], p[3]);
#undef V
}

/* No collision model: one hull per mesh (all body parts and models), around the mesh's vertices as the
 * .vvd stores them (CreatePhysCollide in L4D2's vrad). */
typedef struct {
    char name[128];
    float *tris;                   /* (the collision triangles, model space; ntris -1: no collision model) */
    int ntris;
    vec3_t mins, maxs;
    int have_bounds;
} propdict_t;

#ifdef _WIN64
/* the collision triangles of every model, from hlphys.exe (next to this program): it runs vphysics, which only
 * comes 32-bit */
static void CollisionFromHelper(propdict_t *dict, int numdict) {
    char exe[MAX_PATH], helper[MAX_PATH + 16], out[MAX_PATH + 32], list[MAX_PATH + 32];
    GetModuleFileNameA(NULL, exe, sizeof(exe));
    char *slash = strrchr(exe, 92);
    if (slash) *slash = 0;
    snprintf(helper, sizeof(helper), "%s\\hlphys.exe", exe);
    char tmp[MAX_PATH];
    GetTempPathA(sizeof(tmp), tmp);
    snprintf(out, sizeof(out), "%shlphys_%lu.bin", tmp, GetCurrentProcessId());
    snprintf(list, sizeof(list), "%shlphys_%lu.txt", tmp, GetCurrentProcessId());
    FILE *lf = fopen(list, "w");
    for (int i = 0; i < numdict; i++) fprintf(lf, "%s\n", dict[i].name);
    fclose(lf);
    /* (_spawnl joins the arguments with spaces as they are: paths with spaces need quotes) */
    char qgame[MAX_PATH + 8], qmodel[MAX_PATH + 8], qlist[MAX_PATH + 40], qout[MAX_PATH + 40];
    snprintf(qgame, sizeof(qgame), "\"%s\"", g_gamedir ? g_gamedir : ".");
    snprintf(qmodel, sizeof(qmodel), "\"%s\"", g_modeldir ? g_modeldir : ".");
    snprintf(qlist, sizeof(qlist), "\"%s\"", list);
    snprintf(qout, sizeof(qout), "\"%s\"", out);
    intptr_t rc = _spawnl(_P_WAIT, helper, "hlphys.exe", "-game", qgame, "-modeldir", qmodel, "-list", qlist,
                          "-out", qout, NULL);
    FILE *f = rc == 0 ? fopen(out, "rb") : NULL;
    if (!f) Msg("Warning: hlphys.exe didn't give the props' collision models: they cast box shadows\n");
    for (int i = 0; i < numdict; i++) {
        dict[i].ntris = -1;
        if (!f || fread(&dict[i].ntris, 4, 1, f) != 1) continue;
        if (dict[i].ntris > 0) {
            dict[i].tris = xalloc(sizeof(float) * 9 * dict[i].ntris);
            if (fread(dict[i].tris, sizeof(float) * 9, dict[i].ntris, f) != (size_t)dict[i].ntris) dict[i].ntris = -1;
        }
    }
    if (f) fclose(f);
    remove(out), remove(list);
}
#else
int PropCollisionTris(const char *name, float **tris);
void *LoadPhysics(void);
#endif

/* the model's LOD 0 triangles from its .vtx strips (vrad -StaticPropPolys), placed as mathlib's AngleMatrix and
 * VectorTransform place them; meshes whose material has a lights.rad "noshadow" name in it cast none */
static void MatrixAngles(const float angles[3], const float *origin, float m[3][4]) {
    const float d2r = (float)(3.14159265358979323846 / 180.0);
    float y = angles[1] * d2r, p = angles[0] * d2r, r = angles[2] * d2r;
    float sy = (float)sin(y), cy = (float)cos(y), sp = (float)sin(p), cp = (float)cos(p), sr = (float)sin(r),
          cr = (float)cos(r);
    m[0][0] = cp * cy, m[1][0] = cp * sy, m[2][0] = -sp;
    float srsp = sr * sp, crsp = cr * sp;
    m[0][1] = srsp * cy - cr * sy;
    m[1][1] = srsp * sy + cr * cy;
    m[2][1] = sr * cp;
    m[0][2] = crsp * cy + sr * sy;
    m[1][2] = crsp * sy - sr * cy;
    m[2][2] = cr * cp;
    for (int k = 0; k < 3; k++) m[k][3] = origin[k];
}

static void TransformPoint(const float *in, float m[3][4], vec3_t out) {
    for (int k = 0; k < 3; k++) out[k] = ((m[k][1] * in[1] + m[k][0] * in[0]) + m[k][2] * in[2]) + m[k][3];
}

static int I32(const unsigned char *p) { int v; memcpy(&v, p, 4); return v; }
static int U16(const unsigned char *p) { unsigned short v; memcpy(&v, p, 2); return v; }

static int MeshCastsNoShadow(const unsigned char *mdl, int mlen, int material) {
    if (!g_numnoshadow) return 0;
    int tex = I32(mdl + 0xd0) + 64 * material;
    if (tex < 0 || tex + 64 > mlen) return 0;
    const char *name = (const char *)mdl + tex + I32(mdl + tex);
    for (int i = 0; i < g_numnoshadow; i++) {
        size_t n = strlen(g_noshadow[i]);
        for (const char *q = name; *q; q++)
            if (!_strnicmp(q, g_noshadow[i], n)) return 1;
    }
    return 0;
}

static int AddPropPolys(int id, const char *name, const vec3_t origin, const vec3_t angles) {
    int mlen, vlen, xlen;
    unsigned char *mdl = ReadModelFile(name, ".mdl", &mlen), *vvd = ReadModelFile(name, ".vvd", &vlen),
                  *vtx = ReadModelFile(name, ".dx90.vtx", &xlen);
    int ok = mdl && vvd && vtx && mlen >= 240 && vlen >= 64 && xlen >= 36;
    if (ok) {
        float m[3][4];
        MatrixAngles(angles, origin, m);
        int vstart = I32(vvd + 56), numbp = I32(mdl + 232), bpindex = I32(mdl + 236), vtxbp = I32(vtx + 32);
        for (int b = 0; b < numbp; b++) {
            int bp = bpindex + 16 * b, nmodels = I32(mdl + bp + 4), modelindex = I32(mdl + bp + 12);
            int xbp = vtxbp + 8 * b;
            for (int mi = 0; mi < nmodels; mi++) {
                int sub = bp + modelindex + 148 * mi;
                int nm = I32(mdl + sub + 72), meshindex = I32(mdl + sub + 76), first = I32(mdl + sub + 84) / 48;
                int xmodel = xbp + I32(vtx + xbp + 4) + 8 * mi;
                int xlod = xmodel + I32(vtx + xmodel + 4);                      /* (LOD 0) */
                for (int mm = 0; mm < nm; mm++) {
                    int mesh = sub + meshindex + 116 * mm, vofs = I32(mdl + mesh + 12);
                    if (MeshCastsNoShadow(mdl, mlen, I32(mdl + mesh))) continue;
                    int xmesh = xlod + I32(vtx + xlod + 4) + 9 * mm;
                    int ngroups = I32(vtx + xmesh);
                    for (int g = 0; g < ngroups; g++) {
                        int xsg = xmesh + I32(vtx + xmesh + 4) + 25 * g;
                        int xv = xsg + I32(vtx + xsg + 4), xi = xsg + I32(vtx + xsg + 12);
                        int nstrips = I32(vtx + xsg + 16), xs = xsg + I32(vtx + xsg + 20);
                        for (int st = 0; st < nstrips; st++) {
                            int strip = xs + 27 * st, nidx = I32(vtx + strip), iofs = I32(vtx + strip + 4);
                            for (int i = 0; i < nidx; i += 3) {
                                vec3_t w[3];
                                for (int k = 0; k < 3; k++) {
                                    int index = U16(vtx + xi + 2 * (iofs + i + k));
                                    int vert = U16(vtx + xv + 9 * index + 4);
                                    const float *pos = (const float *)(vvd + vstart + 48 * (first + vofs + vert) + 16);
                                    TransformPoint(pos, m, w[k]);
                                }
                                RT_AddTriangle(id, w[0], w[1], w[2]);
                            }
                        }
                    }
                }
            }
        }
    } else {
        Msg("Error! Can't get static prop model or vtx for '%s'\n", name);
    }
    free(mdl), free(vvd), free(vtx);
    return ok;
}

void AddStaticPropsForRayTrace(void) {
    int len;
    const unsigned char *g = GameLump(0x73707270 /* 'sprp' */, &len);
    if (!g || len < 12) return;
    int numdict;
    memcpy(&numdict, g, 4);
    const unsigned char *p = g + 4;
    propdict_t *dict = xalloc(sizeof(propdict_t) * (numdict + 1));
    for (int i = 0; i < numdict; i++, p += 128) {
        memcpy(dict[i].name, p, 127);
        int n;
        unsigned char *mdl = ReadModelFile(dict[i].name, ".mdl", &n);
        if (mdl && n >= 240) {
            memcpy(dict[i].mins, mdl + 104, 12);          /* studiohdr_t hull_min, hull_max */
            memcpy(dict[i].maxs, mdl + 116, 12);
            dict[i].have_bounds = 1;
        }
        free(mdl);
        dict[i].ntris = -1;
#ifndef _WIN64
        if (!g_bStaticPropPolys) dict[i].ntris = PropCollisionTris(dict[i].name, &dict[i].tris);
#endif
    }
#ifndef _WIN64
    LoadPhysics();         /* (as before even with -StaticPropPolys: loading it sets the x87's precision, 53-bit) */
#endif
#ifdef _WIN64
    if (!g_bStaticPropPolys && numdict) CollisionFromHelper(dict, numdict);
#endif
    int numleaves;
    memcpy(&numleaves, p, 4);
    p += 4 + 2 * numleaves;
    int numprops;
    memcpy(&numprops, p, 4);
    p += 4;
    for (int i = 0; i < numprops; i++, p += PROP_RECORD) {
        vec3_t origin, angles;
        memcpy(origin, p, 12);
        memcpy(angles, p + 12, 12);
        unsigned short model;
        memcpy(&model, p + 24, 2);
        unsigned char flags = p[31];
        if (flags & STATIC_PROP_NO_SHADOW) continue;
        if (model >= numdict) continue;
        int id = TRACE_ID_STATICPROP | i;
        if (g_bStaticPropPolys) {
            AddPropPolys(id, dict[model].name, origin, angles);
        } else if (dict[model].ntris >= 0) {
            float m[3][4];
            MatrixOrgAngles(origin, angles, m);
            for (int t = 0; t < dict[model].ntris; t++) {
                vec3_t w[3];
                for (int k = 0; k < 3; k++) Transform(m, dict[model].tris + 9 * t + 3 * k, w[k]);
                RT_AddTriangle(id, w[0], w[1], w[2]);
            }
        } else if (dict[model].have_bounds) {
            vec3_t mn, mx;
            VectorAdd(dict[model].mins, origin, mn);
            VectorAdd(dict[model].maxs, origin, mx);
            AddBox(id, mn, mx);
        }
    }
    for (int i = 0; i < numdict; i++) free(dict[i].tris);
    free(dict);
}
