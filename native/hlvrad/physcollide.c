/* Prop collision through the game's vphysics.dll (32-bit): what vrad casts prop shadows with. Shared by a 32-bit
 * hlvrad and hlphys.exe, the helper a 64-bit hlvrad asks (the DLL can't load into a 64-bit program).
 *
 * PropCollisionTris: a model's collision triangles in model space, in vphysics' order (convex by convex): the
 * first solid of its .phy, or, without one, a convex hull round each mesh (built like L4D2's vrad builds it). */
#include <windows.h>
#include <float.h>
#include "hlvrad.h"

#define THISCALL __attribute__((thiscall))
#define VT(obj, i) ((*(void ***)(obj))[i])
typedef void *(*CreateInterfaceFn)(const char *name, int *ret);
typedef void *(THISCALL *ConvexFromVerts_t)(void *, float **verts, int count);
typedef void *(THISCALL *ConvertConvexToCollide_t)(void *, void **convex, int count);
typedef void *(THISCALL *UnserializeCollide_t)(void *, char *buffer, int size, int index);
typedef void *(THISCALL *CreateQueryModel_t)(void *, void *collide);
typedef void (THISCALL *DestroyQueryModel_t)(void *, void *query);
typedef int (THISCALL *ConvexCount_t)(void *);
typedef int (THISCALL *TriangleCount_t)(void *, int convex);
typedef void (THISCALL *GetTriangleVerts_t)(void *, int convex, int tri, float *verts);

static void *physcollision;

void *LoadPhysics(void) {
    if (physcollision || !g_gamedir) return physcollision;
    char bin[1100], dll[1200];
    snprintf(bin, sizeof(bin), "%s\\..\\bin", g_gamedir);
    SetDllDirectoryA(bin);
    snprintf(dll, sizeof(dll), "%s\\vphysics.dll", bin);
    HMODULE h = LoadLibraryA(dll);
    if (!h) {
        Msg("Warning: can't load %s: static props cast no shadows\n", dll);
        return NULL;
    }
    CreateInterfaceFn ci = (CreateInterfaceFn)GetProcAddress(h, "CreateInterface");
    if (ci) physcollision = ci("VPhysicsCollision007", NULL);
    /* vphysics' maths runs on the x87 FPU at the precision an MSVC program starts with (53-bit) */
    _controlfp(_PC_53, _MCW_PC);
    return physcollision;
}

unsigned char *ReadModelFile(const char *model, const char *ext, int *len);

static void *CollideFromMeshes(void *pc, const unsigned char *mdl, int mlen, const char *name) {
    int vlen;
    unsigned char *vvd = ReadModelFile(name, ".vvd", &vlen);
    if (!vvd || vlen < 64 || memcmp(vvd, "IDSV", 4)) {
        free(vvd);
        return NULL;
    }
    int vstart, tstart, numbp, bpindex;
    memcpy(&vstart, vvd + 56, 4);
    memcpy(&tstart, vvd + 60, 4);
    int rawcount = (tstart > vstart ? tstart - vstart : vlen - vstart) / 48;
    memcpy(&numbp, mdl + 232, 4);
    memcpy(&bpindex, mdl + 236, 4);
    void **hulls = NULL;
    int nhulls = 0;
    for (int b = 0; b < numbp; b++) {
        int bp = bpindex + 16 * b, nmodels, modelindex;
        if (bp + 16 > mlen) break;
        memcpy(&nmodels, mdl + bp + 4, 4);
        memcpy(&modelindex, mdl + bp + 12, 4);
        for (int k = 0; k < nmodels; k++) {
            int sub = bp + modelindex + 148 * k, nmeshes, meshindex, vertexindex;
            if (sub + 88 > mlen) break;
            memcpy(&nmeshes, mdl + sub + 72, 4);
            memcpy(&meshindex, mdl + sub + 76, 4);
            memcpy(&vertexindex, mdl + sub + 84, 4);
            int first = vertexindex / 48;
            for (int mm = 0; mm < nmeshes; mm++) {
                int mesh = sub + meshindex + 116 * mm, nverts, vofs;
                if (mesh + 16 > mlen) break;
                memcpy(&nverts, mdl + mesh + 8, 4);
                memcpy(&vofs, mdl + mesh + 12, 4);
                float **pts = xalloc(sizeof(float *) * (nverts + 1));
                int n = 0;
                for (int i = 0; i < nverts && first + vofs + i < rawcount; i++)
                    pts[n++] = (float *)(vvd + vstart + 48 * (first + vofs + i) + 16);
                hulls = realloc(hulls, sizeof(void *) * (nhulls + 1));
                hulls[nhulls++] = ((ConvexFromVerts_t)VT(pc, 1))(pc, pts, n);
                free(pts);
            }
        }
    }
    void *collide = nhulls ? ((ConvertConvexToCollide_t)VT(pc, 14))(pc, hulls, nhulls) : NULL;
    free(hulls);
    free(vvd);
    return collide;
}

/* -1: no collision model (the caller may use the hull box); else the number of triangles, 9 floats each */
int PropCollisionTris(const char *name, float **tris) {
    *tris = NULL;
    void *pc = LoadPhysics();
    int n, mlen = 0, have_bounds = 0;
    unsigned char *mdl = ReadModelFile(name, ".mdl", &n);
    if (mdl && n >= 240) have_bounds = 1, mlen = n;
    void *collide = NULL;
    unsigned char *phy = ReadModelFile(name, ".phy", &n);
    if (phy && pc && n >= 20) {
        int hsize, solids, ssize;
        memcpy(&hsize, phy, 4);
        memcpy(&solids, phy + 8, 4);
        memcpy(&ssize, phy + 16, 4);
        if (hsize == 16 && solids > 0 && 20 + ssize <= n)
            collide = ((UnserializeCollide_t)VT(pc, 19))(pc, (char *)phy + 20, ssize, 0);
    }
    free(phy);
    if (!collide && have_bounds && pc) collide = CollideFromMeshes(pc, mdl, mlen, name);
    free(mdl);
    if (!collide) return -1;
    void *q = ((CreateQueryModel_t)VT(pc, 48))(pc, collide);
    int nconvex = ((ConvexCount_t)VT(q, 1))(q), count = 0, cap = 0;
    for (int c = 0; c < nconvex; c++) {
        int ntri = ((TriangleCount_t)VT(q, 2))(q, c);
        for (int t = 0; t < ntri; t++) {
            if (count == cap) cap = cap ? cap * 2 : 256, *tris = realloc(*tris, sizeof(float) * 9 * cap);
            ((GetTriangleVerts_t)VT(q, 4))(q, c, t, *tris + 9 * count);
            count++;
        }
    }
    ((DestroyQueryModel_t)VT(pc, 49))(pc, q);
    return count;
}
