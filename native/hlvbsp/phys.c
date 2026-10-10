/* The physics collision lumps (29: per brush model, 28: displacements). Collision models are IVP
 * "compact surfaces", built by the game's own physics library: like vbsp, we load the game's
 * vphysics.dll and ask it (which is why hlvbsp is a 32-bit program). What goes in - which brushes,
 * their planes, the surface properties, the text around the binary data - is ours. */
#include <windows.h>
#include <float.h>
#include "hlvbsp.h"
#include "disp.h"

#define THISCALL __attribute__((thiscall))
#define VT(obj, i) ((*(void ***)(obj))[i])

/* L4D2's masks (its MASK_SOLID has no CONTENTS_GRATE) */
#define CONTENTS_MOVEABLE_ 0x4000
#define CONTENTS_MONSTER_ 0x2000000
#define L4D2_MASK_SOLID (CONTENTS_SOLID | CONTENTS_WINDOW | CONTENTS_MOVEABLE_ | CONTENTS_MONSTER_)
#define L4D2_MASK_WATER (CONTENTS_WATER | CONTENTS_MOVEABLE_ | CONTENTS_SLIME)
#define VPHYSICS_SHRINK 0.5f
#define VPHYSICS_MERGE 0.01f
#define METERS_PER_INCH 0.0254f
#define CUBIC_METERS_PER_CUBIC_INCH (METERS_PER_INCH * METERS_PER_INCH * METERS_PER_INCH)
#define VPHYSICS_MAX_MASS 50000.0f

typedef void *(*CreateInterfaceFn)(const char *name, int *ret);
static void *physcollision, *physprops;
const char *g_gamedir;
const char *g_surfaceprops_file;

/* IPhysicsCollision (VPhysicsCollision007). L4D2's vtable matches the 2013 SDK up to CollideGetExtent (22);
 * after that it has five more methods (measured from vphysics.dll's code: 48 CreateQueryModel,
 * 49 DestroyQueryModel, 50 ThreadContextCreate "mov eax,ecx", 52 CreateVirtualMesh, 53 SupportsVirtualMesh
 * "mov al,1"). */
typedef void *(THISCALL *ConvexFromPlanes_t)(void *, float *planes, int count, float merge);
typedef float (THISCALL *ConvexVolume_t)(void *, void *convex);
typedef void (THISCALL *SetConvexGameData_t)(void *, void *convex, unsigned data);
typedef void *(THISCALL *ConvertConvexToCollide_t)(void *, void **convex, int count);
typedef struct { int outer_hull, drag_axis_areas, optimized_trace; float drag_epsilon; void *forced_hull; } convertparams_raw_t;
typedef void *(THISCALL *ConvertConvexToCollideParams_t)(void *, void **convex, int count, const void *params);
typedef void (THISCALL *DestroyCollide_t)(void *, void *collide);
typedef int (THISCALL *CollideSize_t)(void *, void *collide);
typedef int (THISCALL *CollideWrite_t)(void *, char *dest, void *collide, int swap);
typedef float (THISCALL *CollideVolume_t)(void *, void *collide);
typedef float *(THISCALL *CollideGetExtent_t)(void *, float *ret, const void *collide, const float *origin, const float *angles, const float *dir);
typedef void *(THISCALL *CreateQueryModel_t)(void *, void *collide);
typedef void (THISCALL *DestroyQueryModel_t)(void *, void *query);
typedef void *(THISCALL *CreateVirtualMesh_t)(void *, const void *params);
typedef int (THISCALL *SupportsVirtualMesh_t)(void *);
typedef void *(THISCALL *PolysoupCreate_t)(void *);
typedef void (THISCALL *PolysoupDestroy_t)(void *, void *soup);
typedef void (THISCALL *PolysoupAddTriangle_t)(void *, void *soup, const float *a, const float *b, const float *c, int mat);
typedef void *(THISCALL *ConvertPolysoupToCollide_t)(void *, void *soup, int mopp);
/* ICollisionQuery */
typedef int (THISCALL *ConvexCount_t)(void *);
typedef int (THISCALL *TriangleCount_t)(void *, int convex);
typedef unsigned (THISCALL *GetGameData_t)(void *, int convex);
typedef void (THISCALL *GetTriangleVerts_t)(void *, int convex, int tri, float *verts);
typedef void (THISCALL *SetTriangleMaterialIndex_t)(void *, int convex, int tri, int index);
/* IPhysicsSurfaceProps (VPhysicsSurfaceProps001) */
typedef int (THISCALL *ParseSurfaceData_t)(void *, const char *file, const char *text);
typedef int (THISCALL *GetSurfaceIndex_t)(void *, const char *name);
typedef void (THISCALL *GetPhysicsProperties_t)(void *, int index, float *density, float *thickness, float *friction, float *elasticity);
typedef const char *(THISCALL *GetPropName_t)(void *, int index);

#define DBG(msg) do { if (getenv("HLVBSP_DEBUG_PHYS")) { puts(msg); fflush(stdout); } } while (0)
#define PC(i, T) ((T)VT(physcollision, i))
#define PP(i, T) ((T)VT(physprops, i))

/* Load vphysics.dll from the game's bin folder and the surface properties the Python side gathered. */
static int LoadPhysics(void) {
    if (physcollision) return 1;
    if (!g_gamedir) return 0;
    char bin[1100], dll[1200];
    snprintf(bin, sizeof(bin), "%s\\..\\bin", g_gamedir);
    SetDllDirectoryA(bin);
    snprintf(dll, sizeof(dll), "%s\\vphysics.dll", bin);
    HMODULE h = LoadLibraryA(dll);
    if (!h) Error("Can't load %s: no collision data", dll);
    CreateInterfaceFn ci = (CreateInterfaceFn)GetProcAddress(h, "CreateInterface");
    if (!ci) Error("%s has no CreateInterface: no collision data", dll);
    physcollision = ci("VPhysicsCollision007", NULL);
    /* vphysics' maths runs on the x87 FPU: use the precision an MSVC program starts with (53-bit), as
     * vbsp does, not our runtime's 64-bit default */
    _controlfp(_PC_53, _MCW_PC);
    physprops = ci("VPhysicsSurfaceProps001", NULL);
    if (!physcollision) Error("Can't build collision data (VPhysicsCollision007 not found)");
    if (physprops && g_surfaceprops_file) {
        /* blocks of: <file name> \n <byte count> \n <text> */
        FILE *f = fopen(g_surfaceprops_file, "rb");
        if (f) {
            char name[1024];
            while (fgets(name, sizeof(name), f)) {
                name[strcspn(name, "\r\n")] = 0;
                int len = 0;
                if (fscanf(f, "%d", &len) != 1) break;
                fgetc(f);
                char *text = xalloc(len + 1);
                fread(text, 1, len, f);
                text[len] = 0;
                PP(1, ParseSurfaceData_t)(physprops, name, text);
                free(text);
            }
            fclose(f);
        }
    }
    return 1;
}

void *physprops_loaded(void) { return physprops; }

void *PhysCollision(void) { return LoadPhysics() ? physcollision : NULL; }

/* A material's surface property index in vphysics (vbsp's g_SurfaceProperties). */
extern const char *MaterialSurfacePropName(int texdata);
int SurfacePropIndex(int texdata) {
    const char *name = MaterialSurfacePropName(texdata);
    if (!name || !name[0] || !physprops) return -1;
    int i = PP(3, GetSurfaceIndex_t)(physprops, name);
    if (i < 0) i = PP(3, GetSurfaceIndex_t)(physprops, "default");
    return i;
}

/* ------------------------------------------------------------------ text */
typedef struct { char *data; int len, cap; } textbuf_t;
static void tb_put(textbuf_t *t, const char *s, int n) {
    while (t->len + n > t->cap) {
        t->cap = t->cap ? t->cap * 2 : 1024;
        t->data = realloc(t->data, t->cap);
    }
    memcpy(t->data + t->len, s, n);
    t->len += n;
}
static void tb_text(textbuf_t *t, const char *s) { tb_put(t, s, (int)strlen(s)); }
static void tb_int(textbuf_t *t, const char *k, int v) {
    char tmp[1100];
    sprintf(tmp, "\"%s\" \"%d\"\n", k, v);
    tb_text(t, tmp);
}
static void tb_float(textbuf_t *t, const char *k, float v) {
    char tmp[1100];
    sprintf(tmp, "\"%s\" \"%s\"\n", k, FmtF(v));
    tb_text(t, tmp);
}
static void tb_string(textbuf_t *t, const char *k, const char *v) {
    tb_text(t, "\"");
    tb_text(t, k);
    tb_text(t, "\" \"");
    tb_text(t, v);
    tb_text(t, "\"\n");
}

/* ------------------------------------------------------------------ brushes into convex pieces */
static int world_props[128], num_world_props;

static int RemapWorldMaterial(int prop) {
    for (int i = 0; i < num_world_props; i++)
        if (world_props[i] == prop) return i + 1;
    if (num_world_props < 126) {
        world_props[num_world_props++] = prop;
        return num_world_props;
    }
    return 0;
}

typedef struct {
    void **convex;
    int count, cap;
    int contents_mask;
    float shrink, merge, total_volume;
    char *added;
    int *leaves, nleaves, leafcap;
} planelist_t;

static void pl_init(planelist_t *pl, float shrink, float merge) {
    memset(pl, 0, sizeof(*pl));
    pl->shrink = shrink;
    pl->merge = merge;
    pl->contents_mask = L4D2_MASK_SOLID;
    pl->added = xalloc(numbrushes + 1);
}

static void pl_free(planelist_t *pl) {
    free(pl->convex);
    free(pl->added);
    free(pl->leaves);
}

static void pl_add_convex(planelist_t *pl, void *convex) {
    if (!convex) return;
    DBG("volume");
    pl->total_volume += PC(3, ConvexVolume_t)(physcollision, convex);
    DBG("volume ok");
    if (pl->count == pl->cap) {
        pl->cap = pl->cap ? pl->cap * 2 : 64;
        pl->convex = realloc(pl->convex, sizeof(void *) * pl->cap);
    }
    pl->convex[pl->count++] = convex;
}

static int pl_leaf_referenced(planelist_t *pl, int leaf) {
    if (!pl->nleaves) return 1;
    for (int i = 0; i < pl->nleaves; i++)
        if (pl->leaves[i] == leaf) return 1;
    return 0;
}

static void VisitLeaves_r(planelist_t *pl, int node) {
    if (node < 0) {
        int leaf = -1 - node;
        if (pl_leaf_referenced(pl, leaf))
            for (int i = 0; i < dleafs[leaf].numleafbrushes; i++) {
                int b = dleafbrushes[dleafs[leaf].firstleafbrush + i];
                if (dbrushes[b].contents & pl->contents_mask) pl->added[b] = 1;
            }
        return;
    }
    VisitLeaves_r(pl, dnodes[node].children[0]);
    VisitLeaves_r(pl, dnodes[node].children[1]);
}

/* The brush's (non-bevel) planes, pushed in by `shrink` where the side is visible and thick enough. */
static void *BuildConvexForBrush(planelist_t *pl, int b, float shrink, void *collidetest, float shrink_min) {
    int n = dbrushes[b].numsides;
    float *planes = xalloc(sizeof(float) * 4 * (n + 1));
    int count = 0;
    for (int i = 0; i < n; i++) {
        dbrushside_t *side = dbrushsides + i + dbrushes[b].firstside;
        if (side->bevel) continue;
        plane_t *plane = &mapplanes[side->planenum];
        float s = shrink;
        if (i < mapbrushes[b].numsides && !mapbrushes[b].original_sides[i].visible) s = 0;
        if (collidetest && s != 0) {
            float start[3], end[3], neg[3] = {-plane->normal[0], -plane->normal[1], -plane->normal[2]};
            static const float zero[3] = {0, 0, 0};
            PC(22, CollideGetExtent_t)(physcollision, start, collidetest, zero, zero, plane->normal);
            PC(22, CollideGetExtent_t)(physcollision, end, collidetest, zero, zero, neg);
            float d[3] = {end[0] - start[0], end[1] - start[1], end[2] - start[2]};
            float thick = DotProduct(d, plane->normal);
            if (fabsf(thick) < shrink_min) s = 0;
        }
        planes[4 * count + 0] = plane->normal[0];
        planes[4 * count + 1] = plane->normal[1];
        planes[4 * count + 2] = plane->normal[2];
        planes[4 * count + 3] = plane->dist - s;
        count++;
    }
    DBG("convex from planes");
    void *convex = PC(2, ConvexFromPlanes_t)(physcollision, planes, count, pl->merge);
    DBG("done convex");
    free(planes);
    return convex;
}

static int pl_add_brushes(planelist_t *pl) {
    int count = 0;
    for (int b = 0; b < numbrushes; b++) {
        if (!pl->added[b]) continue;
        void *convex;
        if (pl->shrink != 0) {
            void *c0 = BuildConvexForBrush(pl, b, 0, NULL, 0);
            void *unshrunk = PC(14, ConvertConvexToCollide_t)(physcollision, &c0, 1);
            convex = BuildConvexForBrush(pl, b, pl->shrink, unshrunk, pl->shrink * 3);
            PC(16, DestroyCollide_t)(physcollision, unshrunk);
        } else convex = BuildConvexForBrush(pl, b, pl->shrink, NULL, 1.0);
        if (convex) {
            count++;
            PC(5, SetConvexGameData_t)(physcollision, convex, (unsigned)b);
            pl_add_convex(pl, convex);
        }
    }
    return count;
}

/* ------------------------------------------------------------------ collision entries */
typedef struct {
    void *collide;
    int kind;              /* 0 static solid, 1 solid (brush entity), 2 static mesh, 3 fluid */
    int contents;
    float mass, volume;
    const char *material;
    float normal[3], dist; /* a fluid's surface plane */
} entry_t;
typedef struct { entry_t *e; int n, cap; } entrylist_t;

static void add_entry(entrylist_t *l, entry_t e) {
    if (l->n == l->cap) {
        l->cap = l->cap ? l->cap * 2 : 8;
        l->e = realloc(l->e, sizeof(entry_t) * l->cap);
    }
    l->e[l->n++] = e;
}

static void TriangleNormal(const float *p0, const float *p1, const float *p2, vec3_t n) {
    vec3_t e0, e1;
    VectorSubtract(p1, p0, e0);
    VectorSubtract(p2, p0, e1);
    CrossProduct(e1, e0, n);
    VectorNormalize(n);
}

static dbrushside_t *FindBrushSide(int b, const vec3_t normal) {
    dbrushside_t *out = NULL;
    float best = -1.f;
    for (int i = 0; i < dbrushes[b].numsides; i++) {
        dbrushside_t *s = dbrushsides + i + dbrushes[b].firstside;
        float dot = DotProduct(normal, mapplanes[s->planenum].normal);
        if (dot > best) {
            best = dot;
            out = s;
        }
    }
    return out;
}

static void ConvertWorldBrushes(entrylist_t *list, float shrink, float merge, int mask) {
    planelist_t pl;
    pl_init(&pl, shrink, merge);
    pl.contents_mask = mask;
    VisitLeaves_r(&pl, dmodels[0].headnode);
    DBG("add brushes");
    pl_add_brushes(&pl);
    DBG("added");
    if (pl.count) {
        void *collide = PC(14, ConvertConvexToCollide_t)(physcollision, pl.convex, pl.count);
        DBG("converted");
        void *q = PC(48, CreateQueryModel_t)(physcollision, collide);
        DBG("query");
        int nconvex = ((ConvexCount_t)VT(q, 1))(q);
        for (int i = 0; i < nconvex; i++) {
            int ntri = ((TriangleCount_t)VT(q, 2))(q, i);
            int b = (int)((GetGameData_t)VT(q, 3))(q, i);
            float pts[9];
            for (int j = 0; j < ntri; j++) {
                ((GetTriangleVerts_t)VT(q, 4))(q, i, j, pts);
                vec3_t n;
                TriangleNormal(pts, pts + 3, pts + 6, n);
                dbrushside_t *side = FindBrushSide(b, n);
                if (side->texinfo != TEXINFO_NODE) {
                    int prop = SurfacePropIndex(texinfos[side->texinfo].texdata);
                    ((SetTriangleMaterialIndex_t)VT(q, 7))(q, i, j, RemapWorldMaterial(prop));
                }
            }
        }
        PC(49, DestroyQueryModel_t)(physcollision, q);
        entry_t e = {collide, 0, mask, 0, 0, NULL};
        add_entry(list, e);
    }
    pl_free(&pl);
}

/* ------------------------------------------------------------------ displacements: virtual meshes */
typedef struct { float *verts; int indexcount, trianglecount, vertexcount, surfaceprop; void *hull; unsigned short indices[1024 * 3]; } vmeshlist_t;
typedef struct { int trianglecount; unsigned short indices[1024 * 3]; } vmeshtris_t;

typedef struct meshevent_s {
    void **vtable;
    float *verts;
    int nverts;
    unsigned short *indices;
    int nindices;
} meshevent_t;

static void THISCALL ev_GetVirtualMesh(meshevent_t *self, void *user, vmeshlist_t *list) {
    (void)user;
    list->verts = self->verts;
    list->indexcount = self->nindices;
    list->trianglecount = self->nindices / 3;
    list->vertexcount = self->nverts;
    list->surfaceprop = 0;
    list->hull = NULL;
    int n = self->nindices < 1024 * 3 ? self->nindices : 1024 * 3;
    memcpy(list->indices, self->indices, sizeof(unsigned short) * n);
}
static void THISCALL ev_GetWorldspaceBounds(meshevent_t *self, void *user, float *mins, float *maxs) {
    (void)user;
    ClearBounds(mins, maxs);
    for (int i = 0; i < self->nverts; i++) AddPointToBounds(self->verts + 3 * i, mins, maxs);
}
static void THISCALL ev_GetTrianglesInSphere(meshevent_t *self, void *user, const float *center, float radius, vmeshtris_t *list) {
    (void)user; (void)center; (void)radius;
    list->trianglecount = self->nindices / 3;
    int n = self->nindices < 1024 * 3 ? self->nindices : 1024 * 3;
    memcpy(list->indices, self->indices, sizeof(unsigned short) * n);
}
static void *mesh_vtable[3] = {(void *)ev_GetVirtualMesh, (void *)ev_GetWorldspaceBounds, (void *)ev_GetTrianglesInSphere};

extern int DispTesselate(int disp, unsigned short **indices);
extern const float *DispVert(int disp, int index);
extern int DispPower(int disp);

unsigned char *phys_disp;
int phys_disp_len;
extern unsigned char *phys_collide;
extern int phys_collide_len;

static void Disp_BuildVirtualMesh(int mask) {
    void **meshes = xalloc(sizeof(void *) * (nummapdisps + 1));
    meshevent_t *events = xalloc(sizeof(meshevent_t) * (nummapdisps + 1));
    for (int i = 0; i < nummapdisps; i++) {
        if (!(mapdisps[i].contents & mask)) continue;
        unsigned short *idx;
        int n = DispTesselate(i, &idx);
        for (int j = 0; j < n / 3; j++) {
            const float *v0 = DispVert(i, idx[3 * j]), *v1 = DispVert(i, idx[3 * j + 1]), *v2 = DispVert(i, idx[3 * j + 2]);
            if (!memcmp(v0, v1, 12) || !memcmp(v1, v2, 12) || !memcmp(v2, v0, 12)) {
                Warning("Displacement %d has bad geometry near %.2f %.2f %.2f\n", i, v0[0], v0[1], v0[2]);
                Error("Can't compile displacement physics, exiting.");
            }
        }
        int maxindex = 0;
        for (int k = 0; k < n; k++)
            if (idx[k] > maxindex) maxindex = idx[k];
        for (int k = 0; k < n / 2; k++) {       /* the winding is reversed for the mesh */
            unsigned short t = idx[k];
            idx[k] = idx[n - k - 1];
            idx[n - k - 1] = t;
        }
        meshevent_t *ev = &events[i];
        ev->vtable = mesh_vtable;
        ev->nverts = maxindex + 1;
        ev->verts = xalloc(sizeof(float) * 3 * ev->nverts);
        for (int k = 0; k < ev->nverts; k++) memcpy(ev->verts + 3 * k, DispVert(i, k), 12);
        ev->indices = idx;
        ev->nindices = n;
        struct { void *handler; void *user; int outer; } params = {ev, ev, 1};
        meshes[i] = PC(52, CreateVirtualMesh_t)(physcollision, &params);
    }
    /* header: count; per displacement its size (or -1); then the data */
    int total = 2 + 2 * nummapdisps;
    for (int i = 0; i < nummapdisps; i++)
        if (meshes[i]) total += PC(17, CollideSize_t)(physcollision, meshes[i]);
    phys_disp = xalloc(total);
    unsigned short count = (unsigned short)nummapdisps;
    memcpy(phys_disp, &count, 2);
    int at = 2;
    for (int i = 0; i < nummapdisps; i++) {
        short size = meshes[i] ? (short)PC(17, CollideSize_t)(physcollision, meshes[i]) : -1;
        memcpy(phys_disp + at, &size, 2);
        at += 2;
    }
    for (int i = 0; i < nummapdisps; i++) {
        if (!meshes[i]) continue;
        int size = PC(17, CollideSize_t)(physcollision, meshes[i]);
        PC(18, CollideWrite_t)(physcollision, (char *)phys_disp + at, meshes[i], 0);
        at += size;
    }
    phys_disp_len = at;
    free(meshes);
}

/* ------------------------------------------------------------------ brush entities */
static void ConvertModelToPhysCollide(entrylist_t *list, int model, int contents, float shrink, float merge) {
    planelist_t pl;
    pl_init(&pl, shrink, merge);
    pl.contents_mask = contents;
    dmodel_t *m = dmodels + model;
    VisitLeaves_r(&pl, m->headnode);
    pl_add_brushes(&pl);
    convertparams_raw_t params;
    memset(&params, 0, sizeof(params));
    float size[3] = {m->maxs[0] - m->mins[0], m->maxs[1] - m->mins[1], m->maxs[2] - m->mins[2]};
    float minarea = -1.0f;
    for (int i = 0; i < 3; i++) {
        float a = size[(i + 1) % 3] * size[(i + 2) % 3];
        if (minarea < 0 || a < minarea) minarea = a;
    }
    float eps = minarea * 1e-2f;
    if (eps < 1.0f) eps = 1.0f;
    if (eps > 1024.0f) eps = 1024.0f;
    /* convertconvexparams_t: bools buildOuterConvexHull, buildDragAxisAreas, buildOptimizedTraceTables
     * (1 byte each), then dragAreaEpsilon and pForcedOuterHull */
    unsigned char raw[16];
    memset(raw, 0, sizeof(raw));
    raw[0] = pl.count > 1;
    raw[1] = 1;
    raw[2] = 0;
    memcpy(raw + 4, &eps, 4);
    void *collide = PC(15, ConvertConvexToCollideParams_t)(physcollision, pl.convex, pl.count, raw);
    if (!collide) {
        pl_free(&pl);
        return;
    }
    struct { int prop; float area; } props[256];
    int nprops = 1;
    props[0].prop = -1;
    props[0].area = 1;
    if (!m->numfaces) {
        int side = 0;
        for (int b = 0; b < numbrushes && !side; b++) {
            if (!pl.added[b]) continue;
            for (int i = 0; i < dbrushes[b].numsides; i++) {
                int si = i + dbrushes[b].firstside;
                if (dbrushsides[si].bevel) continue;
                side = si;
                break;
            }
            break;
        }
        props[nprops].prop = SurfacePropIndex(texinfos[dbrushsides[side].texinfo].texdata);
        props[nprops].area = 2;
        nprops++;
    }
    for (int i = 0; i < m->numfaces; i++) {
        dface_t *f = dfaces + i + m->firstface;
        int prop = SurfacePropIndex(texinfos[f->texinfo].texdata), j;
        for (j = 0; j < nprops; j++)
            if (props[j].prop == prop) {
                props[j].area += f->area;
                break;
            }
        if ((!nprops || j >= nprops) && nprops < 256) {
            props[nprops].prop = prop;
            props[nprops].area = f->area;
            nprops++;
        }
    }
    int maxi = -1;
    float maxarea = 0, total = 0;
    for (int i = 0; i < nprops; i++) {
        if (props[i].area > maxarea) {
            maxi = i;
            maxarea = props[i].area;
        }
        total += props[i].area;
    }
    float mass = 1.0f;
    const char *material = "default";
    if (maxi >= 0 && physprops) {
        int prop = props[maxi].prop;
        if (prop < 0) prop = 0;
        material = PP(7, GetPropName_t)(physprops, prop);
        float density = 0, thickness = 0;
        PP(4, GetPhysicsProperties_t)(physprops, prop, &density, &thickness, NULL, NULL);
        if (thickness != 0) mass = total * thickness * density * CUBIC_METERS_PER_CUBIC_INCH;
        else mass = pl.total_volume * density * CUBIC_METERS_PER_CUBIC_INCH;
    }
    if (mass > VPHYSICS_MAX_MASS) mass = VPHYSICS_MAX_MASS;
    entry_t e = {collide, 1, 0, mass, PC(20, CollideVolume_t)(physcollision, collide), material};
    add_entry(list, e);
    pl_free(&pl);
}

/* ------------------------------------------------------------------ the lump */
/* The world's water volumes as fluids (vbsp's ConvertWaterModelToPhysCollide). */
static void ConvertWaterModelToPhysCollide(entrylist_t *list, int model) {
    int n = WaterModelCount(model);
    for (int k = 0; k < n; k++) {
        int contents, *leaves, nleaves, has_surface;
        float normal[3], dist;
        WaterModelInfo(model, k, &contents, &leaves, &nleaves, normal, &dist, &has_surface);
        planelist_t pl;
        pl_init(&pl, 0.0f, VPHYSICS_MERGE);
        pl.contents_mask = contents;
        pl.leaves = xalloc(sizeof(int) * (nleaves + 1));
        memcpy(pl.leaves, leaves, sizeof(int) * nleaves);
        pl.nleaves = nleaves;
        VisitLeaves_r(&pl, dmodels[model].headnode);
        pl_add_brushes(&pl);
        if (pl.count) {
            void *collide = PC(14, ConvertConvexToCollide_t)(physcollision, pl.convex, pl.count);
            if (collide) {
                if (!has_surface) {
                    float top[3];
                    static const float zero[3] = {0, 0, 0};
                    normal[0] = normal[1] = 0;
                    normal[2] = 1;
                    PC(22, CollideGetExtent_t)(physcollision, top, collide, zero, zero, normal);
                    dist = top[2];
                }
                entry_t e;
                memset(&e, 0, sizeof(e));
                e.collide = collide;
                e.kind = 3;
                e.contents = contents;
                memcpy(e.normal, normal, sizeof(e.normal));
                e.dist = dist;
                add_entry(list, e);
            }
        }
        pl_free(&pl);
    }
}

void EmitPhysCollision(void) {
    for (int i = 0; i < numleafs; i++) {
        dleafs[i].leafwaterdata = -1;
        dleafs[i].contents &= ~CONTENTS_TESTFOGVOLUME;
    }
    if (!LoadPhysics()) return;
    /* vphysics' maths runs on the x87 FPU: use the precision an MSVC program starts with (53-bit), as
     * vbsp does, not our runtime's 64-bit default */
    _controlfp(_PC_53, _MCW_PC);
    if (getenv("HLVBSP_FPU_PC")) {
        int pc = atoi(getenv("HLVBSP_FPU_PC"));
        _controlfp(pc == 24 ? _PC_24 : pc == 64 ? _PC_64 : _PC_53, _MCW_PC);
    }
    Msg("Building Physics collision data...\n");
    DBG("supports virtual mesh?");
    int virtualmesh = PC(53, SupportsVirtualMesh_t)(physcollision);
    DBG("ok");
    /* (L4D2's vbsp has no power-4 fallback: virtual meshes whenever vphysics supports them) */
    entrylist_t *lists = xalloc(sizeof(entrylist_t) * (nummodels + 1));
    textbuf_t *texts = xalloc(sizeof(textbuf_t) * (nummodels + 1));
    int total = 0, physmodels = 0;
    for (int i = 0; i < nummodels; i++) {
        if (i == 0) {
            /* (L4D2: solid, then grates on their own - its MASK_SOLID has none - then the clips) */
            ConvertWorldBrushes(&lists[0], 0.0f, VPHYSICS_MERGE, L4D2_MASK_SOLID);
            ConvertWorldBrushes(&lists[0], 0.0f, VPHYSICS_MERGE, CONTENTS_GRATE);
            ConvertWorldBrushes(&lists[0], 0.0f, VPHYSICS_MERGE, CONTENTS_PLAYERCLIP);
            ConvertWorldBrushes(&lists[0], 0.0f, VPHYSICS_MERGE, CONTENTS_MONSTERCLIP);
            if (virtualmesh) Disp_BuildVirtualMesh(L4D2_MASK_SOLID | CONTENTS_GRATE);
            else if (nummapdisps) Warning("(displacement collision without virtual meshes: not yet)\n");
            ConvertWaterModelToPhysCollide(&lists[0], 0);
        } else {
            ConvertModelToPhysCollide(&lists[i], i, L4D2_MASK_SOLID | CONTENTS_GRATE | CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP | L4D2_MASK_WATER,
                                      VPHYSICS_SHRINK, VPHYSICS_MERGE);
        }
        if (!lists[i].n) continue;
        textbuf_t *t = &texts[i];
        for (int j = 0; j < lists[i].n; j++) {
            entry_t *e = &lists[i].e[j];
            if (e->kind == 3) {
                char tmp[256];
                tb_text(t, "fluid {\n");
                tb_int(t, "index", j);
                tb_string(t, "surfaceprop", "water");
                tb_float(t, "damping", 0.01f);
                tb_int(t, "contents", e->contents);
                sprintf(tmp, "\"surfaceplane\" \"%s %s %s %s \"\n", FmtF(e->normal[0]), FmtF(e->normal[1]), FmtF(e->normal[2]),
                        FmtF(e->dist));
                tb_text(t, tmp);
                tb_text(t, "\"currentvelocity\" \"0.000000 0.000000 0.000000 \"\n");
                tb_text(t, "}\n");
            } else if (e->kind == 1) {
                tb_text(t, "solid {\n");
                tb_int(t, "index", j);
                tb_float(t, "mass", e->mass);
                if (e->material) tb_string(t, "surfaceprop", e->material);
                if (e->volume != 0.f) tb_float(t, "volume", e->volume);
                tb_text(t, "}\n");
            } else {
                tb_text(t, "staticsolid {\n");
                tb_int(t, "index", j);
                if (e->kind == 0) tb_int(t, "contents", e->contents);
                tb_text(t, "}\n");
            }
            total += PC(17, CollideSize_t)(physcollision, e->collide) + 4;
        }
        if (i == 0) {
            if (virtualmesh) tb_text(t, "virtualterrain {}\n");
            if (num_world_props) {
                tb_text(t, "materialtable {\n");
                for (int j = 0; j < num_world_props; j++) {
                    const char *name = world_props[j] < 0 || !physprops ? "default" : PP(7, GetPropName_t)(physprops, world_props[j]);
                    tb_int(t, name, j + 1);
                }
                tb_text(t, "}\n");
            }
        }
        tb_put(t, "", 1);
        total += t->len;
        physmodels++;
    }
    physmodels++;
    phys_collide_len = total + physmodels * 16;
    phys_collide = xalloc(phys_collide_len + 4);
    unsigned char *ptr = phys_collide;
    for (int i = 0; i < nummodels; i++) {
        if (!texts[i].len) continue;
        int header[4] = {i, 4 * lists[i].n, texts[i].len, lists[i].n};
        for (int j = 0; j < lists[i].n; j++) header[1] += PC(17, CollideSize_t)(physcollision, lists[i].e[j].collide);
        memcpy(ptr, header, 16);
        ptr += 16;
        for (int j = 0; j < lists[i].n; j++) {
            int size = PC(17, CollideSize_t)(physcollision, lists[i].e[j].collide);
            memcpy(ptr, &size, 4);
            ptr += 4;
            PC(18, CollideWrite_t)(physcollision, (char *)ptr, lists[i].e[j].collide, 0);
            ptr += size;
        }
        memcpy(ptr, texts[i].data, texts[i].len);
        ptr += texts[i].len;
    }
    int end[4] = {-1, -1, 0, 0};
    memcpy(ptr, end, 16);
    ptr += 16;
    Msg("done (0) (%d bytes)\n", phys_collide_len);
}
