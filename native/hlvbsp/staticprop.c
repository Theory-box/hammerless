/* Static props (game lump "sprp", version 9 as L4D2 writes it): each prop_static becomes a record
 * with its model (from a dictionary), placement, flags, fades and the BSP leaves it touches. The
 * leaves are the non-solid ones whose volume overlaps the model's convex hull (one convex piece per
 * mesh): built and tested with the game's vphysics, as vbsp does. The Python side lists what vbsp
 * reads from the models (-props <file>): whether each may be static, and its meshes' vertices. */
#include <windows.h>
#include <ctype.h>
#include "hlvbsp.h"

#define THISCALL __attribute__((thiscall))
#define VT(obj, i) ((*(void ***)(obj))[i])
typedef void *(THISCALL *ConvexFromVerts_t)(void *, float **verts, int count);
typedef void *(THISCALL *ConvexFromPlanes_t)(void *, float *planes, int count, float merge);
typedef void *(THISCALL *ConvertConvexToCollide_t)(void *, void **convex, int count);
typedef void (THISCALL *DestroyCollide_t)(void *, void *collide);
typedef void (THISCALL *CollideGetAABB_t)(void *, float *mins, float *maxs, const void *collide, const float *origin, const float *angles);
typedef void (THISCALL *TraceCollide_t)(void *, const float *start, const float *end, const void *sweep, const float *sweepangles,
                                        const void *collide, const float *origin, const float *angles, void *trace);

extern void *PhysCollision(void);
const char *g_props_file;

#define STATIC_PROP_FLAG_FADES 0x1
#define STATIC_PROP_USE_LIGHTING_ORIGIN 0x2
#define STATIC_PROP_IGNORE_NORMALS 0x8
#define STATIC_PROP_NO_SHADOW 0x10
#define STATIC_PROP_SCREEN_SPACE_FADE 0x20
#define STATIC_PROP_NO_PER_VERTEX_LIGHTING 0x40
#define STATIC_PROP_NO_SELF_SHADOWING 0x80

typedef struct {
    char name[260];
    int status;           /* 0 ok, 1 missing, 2 not static, 3 dynamic */
    int nmeshes;
    int *counts;
    float **verts;
    void *collide;
    int built, checked;
} propmodel_t;

static propmodel_t *models;
static int nummodels_, maxmodels;

static void LoadPropTable(void) {
    static int loaded;
    if (loaded || !g_props_file) return;
    loaded = 1;
    FILE *f = fopen(g_props_file, "rb");
    if (!f) return;
    static char line[1 << 20];
    char *big = NULL;
    size_t bigcap = 0;
    for (;;) {
        /* lines can be long (all of a mesh's vertices): read them whole */
        size_t n = 0;
        int c;
        while ((c = fgetc(f)) != EOF && c != '\n') {
            if (n + 2 > bigcap) {
                bigcap = bigcap ? bigcap * 2 : sizeof(line);
                big = realloc(big, bigcap);
            }
            big[n++] = (char)c;
        }
        if (c == EOF && n == 0) break;
        if (!big) continue;
        big[n] = 0;
        if (!strncmp(big, "model ", 6)) {
            if (nummodels_ == maxmodels) {
                maxmodels = maxmodels ? maxmodels * 2 : 64;
                models = realloc(models, sizeof(propmodel_t) * maxmodels);
            }
            propmodel_t *m = &models[nummodels_++];
            memset(m, 0, sizeof(*m));
            char *sp = strrchr(big, ' ');
            *sp = 0;
            strncpy(m->name, big + 6, sizeof(m->name) - 1);
            const char *st = sp + 1;
            m->status = !strcmp(st, "ok") ? 0 : !strcmp(st, "notstatic") ? 2 : !strcmp(st, "dynamic") ? 3 : 1;
        } else if (!strncmp(big, "mesh ", 5) && nummodels_) {
            propmodel_t *m = &models[nummodels_ - 1];
            char *p = big + 5;
            int count = (int)strtol(p, &p, 10);
            m->counts = realloc(m->counts, sizeof(int) * (m->nmeshes + 1));
            m->verts = realloc(m->verts, sizeof(float *) * (m->nmeshes + 1));
            float *v = xalloc(sizeof(float) * 3 * (count + 1));
            for (int i = 0; i < 3 * count; i++) v[i] = strtof(p, &p);
            m->counts[m->nmeshes] = count;
            m->verts[m->nmeshes] = v;
            m->nmeshes++;
        }
        if (c == EOF) break;
    }
    free(big);
    fclose(f);
}

static int names_equal(const char *a, const char *b) {
    for (; *a && *b; a++, b++) {
        char x = (char)tolower((unsigned char)*a), y = (char)tolower((unsigned char)*b);
        if (x == '\\') x = '/';
        if (y == '\\') y = '/';
        if (x != y) return 0;
    }
    return *a == *b;
}

/* The model's hull (cached per model, like vbsp's collision cache); NULL if it can't be a static prop. */
static void *GetCollisionModel(const char *name) {
    void *pc = PhysCollision();
    for (int i = 0; i < nummodels_; i++) {
        propmodel_t *m = &models[i];
        if (!names_equal(m->name, name)) continue;
        if (m->built) return m->collide;
        m->built = 1;
        if (m->status == 2) Warning("Error! To use model \"%s\"\n      with prop_static, it must be compiled with $staticprop!\n", name);
        if (m->status == 3) Warning("Error! prop_static using model \"%s\", which must be used on a dynamic entity (i.e. prop_physics). Deleted.\n", name);
        if (m->status != 0) {
            Warning("Error loading studio model \"%s\"!\n", name);
            return NULL;
        }
        void **hulls = xalloc(sizeof(void *) * (m->nmeshes + 1));
        for (int k = 0; k < m->nmeshes; k++) {
            float **pts = xalloc(sizeof(float *) * (m->counts[k] + 1));
            for (int j = 0; j < m->counts[k]; j++) pts[j] = m->verts[k] + 3 * j;
            hulls[k] = ((ConvexFromVerts_t)VT(pc, 1))(pc, pts, m->counts[k]);
            free(pts);
        }
        m->collide = ((ConvertConvexToCollide_t)VT(pc, 14))(pc, hulls, m->nmeshes);
        free(hulls);
        if (!m->collide) Warning("Bad geometry on \"%s\"!\n", name);
        return m->collide;
    }
    Warning("Error loading studio model \"%s\"!\n", name);
    return NULL;
}

/* Can the model be a static (or detail) prop? vbsp's checks, reported once per model like vbsp. */
int PropModelValid(const char *name, const char *what) {
    LoadPropTable();
    for (int i = 0; i < nummodels_; i++) {
        propmodel_t *m = &models[i];
        if (!names_equal(m->name, name)) continue;
        if (m->status == 0) return 1;
        if (!m->checked) {
            m->checked = 1;
            if (m->status == 2) Warning("Error! To use model \"%s\"\n      with %s, it must be compiled with $staticprop!\n", name, what);
            if (m->status == 3) Warning("Error! %s using model \"%s\", which must be used on a dynamic entity (i.e. prop_physics). Deleted.\n", what, name);
            Warning("Error loading studio model \"%s\"!\n", name);
        }
        return 0;
    }
    Warning("Error loading studio model \"%s\"!\n", name);
    return 0;
}

/* Does the leaf (the half-spaces of the nodes above it) overlap the hull? */
static int TestLeafAgainstCollide(int depth, int *nodelist, const float *origin, const float *angles, void *collide) {
    void *pc = PhysCollision();
    float *planes = xalloc(sizeof(float) * 4 * (depth + 1));
    int idx = 0;
    for (int i = depth; --i >= 0; ++idx) {
        int sign = nodelist[i] < 0 ? -1 : 1;
        int node = sign < 0 ? -nodelist[i] - 1 : nodelist[i];
        plane_t *plane = &mapplanes[dnodes[node].planenum];
        planes[idx * 4] = sign * plane->normal[0];
        planes[idx * 4 + 1] = sign * plane->normal[1];
        planes[idx * 4 + 2] = sign * plane->normal[2];
        planes[idx * 4 + 3] = sign * plane->dist;
    }
    void *convex = ((ConvexFromPlanes_t)VT(pc, 2))(pc, planes, depth, 0.0f);
    free(planes);
    if (!convex) return 0;
    void *leafcollide = ((ConvertConvexToCollide_t)VT(pc, 14))(pc, &convex, 1);
    unsigned char trace[256];
    memset(trace, 0, sizeof(trace));
    static const float zero[3] = {0, 0, 0};
    ((TraceCollide_t)VT(pc, 39))(pc, zero, zero, leafcollide, zero, collide, origin, angles, trace);
    ((DestroyCollide_t)VT(pc, 16))(pc, leafcollide);
    return trace[55] != 0;       /* startsolid */
}

typedef struct { unsigned short *l; int n, cap; } leaflist_t;

static void ComputeConvexHullLeaves_R(int node, int depth, int *nodelist, const float *mins, const float *maxs,
                                      const float *origin, const float *angles, void *collide, leaflist_t *out) {
    float cmin[3], cmax[3];
    while (node >= 0) {
        dnode_t *n = &dnodes[node];
        plane_t *plane = &mapplanes[n->planenum];
        for (int i = 0; i < 3; ++i) {
            if (plane->normal[i] >= 0) {
                cmin[i] = mins[i];
                cmax[i] = maxs[i];
            } else {
                cmin[i] = maxs[i];
                cmax[i] = mins[i];
            }
        }
        if (DotProduct(plane->normal, cmax) <= plane->dist) {
            nodelist[depth++] = node;
            node = n->children[1];
        } else if (DotProduct(plane->normal, cmin) >= plane->dist) {
            nodelist[depth++] = -node - 1;
            node = n->children[0];
        } else {
            nodelist[depth++] = node;
            ComputeConvexHullLeaves_R(n->children[1], depth, nodelist, mins, maxs, origin, angles, collide, out);
            nodelist[depth - 1] = -node - 1;
            ComputeConvexHullLeaves_R(n->children[0], depth, nodelist, mins, maxs, origin, angles, collide, out);
            return;
        }
    }
    int leaf = -node - 1;
    if (!(dleafs[leaf].contents & CONTENTS_SOLID) && TestLeafAgainstCollide(depth, nodelist, origin, angles, collide)) {
        if (out->n == out->cap) {
            out->cap = out->cap ? out->cap * 2 : 64;
            out->l = realloc(out->l, sizeof(unsigned short) * out->cap);
        }
        out->l[out->n++] = (unsigned short)leaf;
    }
}

/* ------------------------------------------------------------------ the lump */
#pragma pack(push, 1)
typedef struct { char name[128]; } propdict_t;
#pragma pack(pop)
typedef struct {
    vec3_t origin, angles;
    unsigned short proptype, firstleaf, leafcount;
    unsigned char solid, flags;
    int skin;
    float fademin, fademax;
    vec3_t lightingorigin;
    float forcedfadescale;
    unsigned char mincpu, maxcpu, mingpu, maxgpu;
    unsigned char diffuse[4];
    unsigned char disablex360;
} sprop_t;

static propdict_t *dict;
static int numdict;
static sprop_t *props;
static int numprops, maxprops;
static unsigned short *propleaves;
static int numpropleaves, maxpropleaves;

static int AddDict(const char *name) {
    propdict_t d;
    memset(&d, 0, sizeof(d));
    strncpy(d.name, name, sizeof(d.name));
    for (int i = numdict; --i >= 0;)
        if (!memcmp(&dict[i], &d, sizeof(d))) return i;
    dict = realloc(dict, sizeof(propdict_t) * (numdict + 1));
    dict[numdict] = d;
    return numdict++;
}

static float FloatForKey(const entity_t *e, const char *k) { return (float)atof(ValueForKey(e, k)); }
static int IntForKey(const entity_t *e, const char *k) { return atoi(ValueForKey(e, k)); }

unsigned char *g_sprp;
int g_sprp_len;

void EmitStaticProps(void) {
    if (!PhysCollision()) {
        for (int i = 0; i < num_entities; ++i)        /* (props without collision: never silently none) */
            if (!strcmp(ValueForKey(&entities[i], "classname"), "prop_static") || !strcmp(ValueForKey(&entities[i], "classname"), "static_prop"))
                Error("Can't load the game's vphysics.dll: static props can't be made");
        return;
    }
    LoadPropTable();
    int *lighting = xalloc(sizeof(int) * (num_entities + 1)), nlighting = 0;
    for (int i = 0; i < num_entities; ++i)
        if (!strcmp(ValueForKey(&entities[i], "classname"), "info_lighting")) lighting[nlighting++] = i;
    for (int i = 0; i < num_entities; ++i) {
        entity_t *e = &entities[i];
        const char *cls = ValueForKey(e, "classname");
        if (strcmp(cls, "static_prop") && strcmp(cls, "prop_static")) continue;
        vec3_t origin, angles;
        GetVectorForKey(e, "origin", origin);
        GetVectorForKey(e, "angles", angles);
        const char *model = ValueForKey(e, "model");
        sprop_t p;
        memset(&p, 0, sizeof(p));
        p.solid = (unsigned char)IntForKey(e, "solid");
        p.skin = IntForKey(e, "skin");
        p.fademax = FloatForKey(e, "fademaxdist");
        if (IntForKey(e, "ignorenormals") == 1) p.flags |= STATIC_PROP_IGNORE_NORMALS;
        if (IntForKey(e, "disableshadows") == 1) p.flags |= STATIC_PROP_NO_SHADOW;
        if (IntForKey(e, "disablevertexlighting") == 1) p.flags |= STATIC_PROP_NO_PER_VERTEX_LIGHTING;
        if (IntForKey(e, "disableselfshadowing") == 1) p.flags |= STATIC_PROP_NO_SELF_SHADOWING;
        if (IntForKey(e, "screenspacefade") == 1)      /* L4D2: obsolete, reported and ignored */
            Warning("Encountered obsolete static prop option to do its fade in screen space @ %.2f %.2f %.2f\n",
                    origin[0], origin[1], origin[2]);
        p.forcedfadescale = ValueForKey(e, "fadescale")[0] ? FloatForKey(e, "fadescale") : 1;
        int fades = p.fademax > 0;
        if (fades) {
            p.fademin = FloatForKey(e, "fademindist");
            if (p.fademin < 0) p.fademin = p.fademax;
            p.flags |= STATIC_PROP_FLAG_FADES;
        } else p.fademin = 0;
        p.mincpu = (unsigned char)IntForKey(e, "mincpulevel");
        p.maxcpu = (unsigned char)IntForKey(e, "maxcpulevel");
        p.mingpu = (unsigned char)IntForKey(e, "mingpulevel");
        p.maxgpu = (unsigned char)IntForKey(e, "maxgpulevel");
        /* diffuse modulation: rendercolor and renderamt (255 when unset) */
        int r = 255, g = 255, b = 255, a = 255;
        const char *rc = ValueForKey(e, "rendercolor");
        if (rc[0]) sscanf(rc, "%d %d %d", &r, &g, &b);
        if (ValueForKey(e, "renderamt")[0]) a = IntForKey(e, "renderamt");
        p.diffuse[0] = (unsigned char)r; p.diffuse[1] = (unsigned char)g;
        p.diffuse[2] = (unsigned char)b; p.diffuse[3] = (unsigned char)a;
        p.disablex360 = (unsigned char)(IntForKey(e, "disableX360") != 0);
        const char *lo = ValueForKey(e, "lightingorigin");

        void *collide = GetCollisionModel(model);
        e->epairs = NULL;
        if (!collide) continue;
        void *pc = PhysCollision();
        float mins[3], maxs[3];
        ((CollideGetAABB_t)VT(pc, 23))(pc, mins, maxs, collide, origin, angles);
        int nodelist[1024];
        leaflist_t leaves = {0};
        ComputeConvexHullLeaves_R(0, 0, nodelist, mins, maxs, origin, angles, collide, &leaves);
        if (!leaves.n) {
            Warning("Static prop %s outside the map (%.2f, %.2f, %.2f)\n", model, origin[0], origin[1], origin[2]);
            continue;
        }
        p.proptype = (unsigned short)AddDict(model);
        VectorCopy(origin, p.origin);
        VectorCopy(angles, p.angles);
        if (numpropleaves + leaves.n > 65535)
            Error("MAX_MAP_LEAFS: static props touch more than 65535 leaves in all (too many props)");
        p.firstleaf = (unsigned short)numpropleaves;
        p.leafcount = (unsigned short)leaves.n;
        if (lo[0]) {
            for (int k = nlighting; --k >= 0;) {
                entity_t *le = &entities[lighting[k]];
                if (!strcmp(ValueForKey(le, "targetname"), lo)) {
                    GetVectorForKey(le, "origin", p.lightingorigin);
                    p.flags |= STATIC_PROP_USE_LIGHTING_ORIGIN;
                    break;
                }
            }
        }
        if (numprops == maxprops) {
            maxprops = maxprops ? maxprops * 2 : 256;
            props = realloc(props, sizeof(sprop_t) * maxprops);
        }
        props[numprops++] = p;
        for (int k = 0; k < leaves.n; k++) {
            if (numpropleaves == maxpropleaves) {
                maxpropleaves = maxpropleaves ? maxpropleaves * 2 : 1024;
                propleaves = realloc(propleaves, sizeof(unsigned short) * maxpropleaves);
            }
            propleaves[numpropleaves++] = leaves.l[k];
        }
        free(leaves.l);
    }
    for (int k = nlighting; --k >= 0;) entities[lighting[k]].epairs = NULL;
    free(lighting);
    /* dictionary, leaves, props */
    int size = 12 + 128 * numdict + 2 * numpropleaves + 72 * numprops;
    g_sprp = xalloc(size);
    unsigned char *q = g_sprp;
    memcpy(q, &numdict, 4); q += 4;
    memcpy(q, dict, 128 * numdict); q += 128 * numdict;
    memcpy(q, &numpropleaves, 4); q += 4;
    memcpy(q, propleaves, 2 * numpropleaves); q += 2 * numpropleaves;
    memcpy(q, &numprops, 4); q += 4;
    for (int i = 0; i < numprops; i++, q += 72) {
        sprop_t *p = &props[i];
        memcpy(q, p->origin, 12);
        memcpy(q + 12, p->angles, 12);
        memcpy(q + 24, &p->proptype, 2);
        memcpy(q + 26, &p->firstleaf, 2);
        memcpy(q + 28, &p->leafcount, 2);
        q[30] = p->solid;
        q[31] = p->flags;
        memcpy(q + 32, &p->skin, 4);
        memcpy(q + 36, &p->fademin, 4);
        memcpy(q + 40, &p->fademax, 4);
        memcpy(q + 44, p->lightingorigin, 12);
        memcpy(q + 56, &p->forcedfadescale, 4);
        q[60] = p->mincpu; q[61] = p->maxcpu; q[62] = p->mingpu; q[63] = p->maxgpu;
        memcpy(q + 64, p->diffuse, 4);
        q[68] = p->disablex360;
    }
    g_sprp_len = size;
}
