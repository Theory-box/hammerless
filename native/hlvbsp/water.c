/* Water volumes. After a model's faces are written, vbsp gathers its water leaves into connected
 * volumes (split where a portal crosses a volume's surface), each with the surface plane, the
 * lowest point and the surface's texinfo. Each volume gets a one-off material
 * maps/<map>/<material>_depth_<depth> (a patch of the water material with $waterdepth, written to the
 * pakfile; its texinfo is never used, so the compaction drops it again), a leaf water data entry
 * (lump 36), and for the world a fluid physics model; its leaves point at the water data. */
#include "hlvbsp.h"
#include <ctype.h>

#define L4D2_MASK_SOLID 33570819          /* (L4D2's MASK_SOLID: no grates) */

typedef struct {
    vec3_t normal;
    float dist, minz;
    int has_surface;
    int leaf, texinfo, outside;
    node_t *node;
} waterleaf_t;

typedef struct {
    int model, contents;
    waterleaf_t data;
    int depthtexinfo, first, count, fog;
} watermodel_t;

typedef struct { float surfacez, minz; short texinfo, pad; } dleafwaterdata_t;

static watermodel_t *watermodels;
static int numwatermodels;
static int *waterleaves, numwaterleaves;
static dleafwaterdata_t *leafwater;
static int numleafwater;
static char **depthnames;          /* full names of the depth materials made so far */
static int *depthtexinfos, numdepth;

const char *g_mapbase = "";
int g_has_water;                   /* a water or slime material is used (vbsp then adds a water_lod_control) */

static int WindingOnPlaneSideF(const winding_t *w, const vec3_t normal, float dist) {
    int front = 0, back = 0;
    for (int i = 0; i < w->numpoints; i++) {
        float d = DotProduct(w->p[i], normal) - dist;
        if (d < -ON_EPSILON) {
            if (front) return SIDE_CROSS;
            back = 1;
            continue;
        }
        if (d > ON_EPSILON) {
            if (back) return SIDE_CROSS;
            front = 1;
        }
    }
    if (back) return SIDE_BACK;
    if (front) return SIDE_FRONT;
    return SIDE_ON;
}

static void EnumLeaves_r(node_t *node, int mask, node_t ***list, int *n, int *cap) {
    if (node->planenum != PLANENUM_LEAF) {
        EnumLeaves_r(node->children[0], mask, list, n, cap);
        EnumLeaves_r(node->children[1], mask, list, n, cap);
        return;
    }
    if (!(node->contents & mask)) return;
    if (*n == *cap) {
        *cap = *cap ? *cap * 2 : 64;
        *list = realloc(*list, sizeof(node_t *) * *cap);
    }
    (*list)[(*n)++] = node;
}

/* The leaf's surface: its portal into air with the most upward normal (then the highest). */
static void BuildWaterLeaf(node_t *leaf, waterleaf_t *out) {
    out->node = leaf;
    out->leaf = leaf->diskid;
    out->outside = -1;
    out->has_surface = 0;
    out->dist = MAX_COORD_INTEGER;
    out->normal[0] = 0.f;
    out->normal[1] = 0.f;
    out->normal[2] = 1.f;
    out->texinfo = -1;
    out->minz = MAX_COORD_INTEGER;
    int opposite = 0;
    for (portal_t *p = leaf->portals; p; p = p->next[!opposite]) {
        opposite = p->nodes[0] == leaf ? 1 : 0;
        if (!p->side) continue;
        node_t *other = p->nodes[opposite];
        if (!(other->contents & MASK_WATER) && !(other->contents & L4D2_MASK_SOLID)) {
            plane_t *plane = &mapplanes[p->side->planenum];
            if (out->has_surface) {
                if (out->normal[2] > plane->normal[2]) continue;
                if (out->normal[2] == plane->normal[2] && out->dist >= plane->dist) continue;
            }
            if (plane->normal[2] <= 0) continue;
            out->dist = plane->dist;
            VectorCopy(plane->normal, out->normal);
            out->has_surface = 1;
            out->outside = other->diskid;
            out->texinfo = p->side->texinfo;
        }
    }
}

/* Should a come before b? (surfaces first, the most upward first) */
static int IsLowerLeaf(const waterleaf_t *a, const waterleaf_t *b) {
    if (a->has_surface && b->has_surface) {
        if (b->normal[2] > a->normal[2]) return 0;
        return 1;
    }
    return a->has_surface;
}

static int PortalCrossesWater(const waterleaf_t *base, const portal_t *p) {
    if (!base->has_surface) return 0;
    int side = WindingOnPlaneSideF(p->winding, base->normal, base->dist);
    return side == SIDE_CROSS || side == SIDE_FRONT;
}

static void Flood_r(node_t *leaf, waterleaf_t *base, unsigned char *visited, int **list, int *n, int *cap) {
    if (leaf->diskid < 0 || visited[leaf->diskid] || !(leaf->contents & (base->node->contents & MASK_WATER))) return;
    int opposite = 0;
    for (portal_t *p = leaf->portals; p; p = p->next[!opposite]) {
        opposite = p->nodes[0] == leaf ? 1 : 0;
        if (PortalCrossesWater(base, p)) return;
    }
    visited[leaf->diskid] = 1;
    if (*n == *cap) {
        *cap = *cap ? *cap * 2 : 64;
        *list = realloc(*list, sizeof(int) * *cap);
    }
    (*list)[(*n)++] = leaf->diskid;
    if (leaf->mins[2] < base->minz) base->minz = leaf->mins[2];
    for (portal_t *p = leaf->portals; p; p = p->next[!opposite]) {
        opposite = p->nodes[0] == leaf ? 1 : 0;
        Flood_r(p->nodes[opposite], base, visited, list, n, cap);
    }
}

static int FirstWaterTexinfo(bspbrush_t *list, int contents) {
    for (; list; list = list->next) {
        if (!(list->original->contents & contents)) continue;
        for (int i = 0; i < list->original->numsides; i++)
            if (list->original->original_sides[i].contents & contents) return list->original->original_sides[i].texinfo;
    }
    return 0;
}

int AliasedTexData(const char *name, int source);
int FindOrCreateTexInfoPublic(const texinfo_t *t);
const char *TexDataName(int texdata);
void AddFileToPak(const char *name, unsigned char *data, int len);

/* maps/<map>/<material>_depth_<n>, lower case */
static void WaterTextureName(const char *material, int depth, char *out) {
    snprintf(out, 600, "maps/%s/%s_depth_%i", g_mapbase, material, depth);
    for (char *c = out; *c; c++) *c = (char)tolower((unsigned char)*c);
}

static int FindOrCreateWaterTexInfo(int texinfo, float depth) {
    char full[600], material[512];
    const char *name = TexDataName(texinfos[texinfo].texdata);
    WaterTextureName(name, (int)depth, full);
    for (int i = 0; i < numdepth; i++)
        if (!strcmp(depthnames[i], full)) return depthtexinfos[i];
    strncpy(material, name, sizeof(material) - 1);
    material[sizeof(material) - 1] = 0;
    for (char *c = material; *c; c++) *c = (char)tolower((unsigned char)*c);
    texinfo_t t = texinfos[texinfo];
    t.texdata = AliasedTexData(full, texinfos[texinfo].texdata);
    int ti = FindOrCreateTexInfoPublic(&t);
    depthnames = realloc(depthnames, sizeof(char *) * (numdepth + 1));
    depthtexinfos = realloc(depthtexinfos, sizeof(int) * (numdepth + 1));
    depthnames[numdepth] = copystring(full);
    depthtexinfos[numdepth++] = ti;
    /* the patch: the water material with $waterdepth inserted */
    char path[700], text[1400];
    snprintf(path, sizeof(path), "materials/%s.vmt", full);
    int len = snprintf(text, sizeof(text), "\"patch\"\r\n{\r\n\t\"include\"\t\t\"materials/%s.vmt\"\r\n\t\"insert\"\r\n\t{\r\n\t\t\"$waterdepth\"\t\t\"%i\"\r\n\t}\r\n}\r\n",
                      material, (int)depth);
    if (len >= (int)sizeof(text)) len = (int)sizeof(text) - 1;
    unsigned char *data = xalloc(len + 1);
    memcpy(data, text, len);
    AddFileToPak(path, data, len);
    return ti;
}

static int FindOrCreateLeafWaterData(float surfacez, float minz, int texinfo) {
    for (int i = 0; i < numleafwater; i++)
        if (leafwater[i].surfacez == surfacez && leafwater[i].minz == minz && leafwater[i].texinfo == texinfo) return i;
    if (numleafwater % 64 == 0) leafwater = realloc(leafwater, sizeof(dleafwaterdata_t) * (numleafwater + 64));
    dleafwaterdata_t *d = &leafwater[numleafwater];
    memset(d, 0, sizeof(*d));
    d->surfacez = surfacez;
    d->minz = minz;
    d->texinfo = (short)texinfo;
    return numleafwater++;
}

/* After a model's faces: its water volumes (vbsp's EmitWaterVolumesForBSP). */
void EmitWaterVolumesForBSP(node_t *headnode) {
    node_t **leaves = NULL;
    int nleaves = 0, cap = 0;
    EnumLeaves_r(headnode, MASK_WATER, &leaves, &nleaves, &cap);
    waterleaf_t *list = xalloc(sizeof(waterleaf_t) * (nleaves + 1));
    int n = 0;
    for (int i = 0; i < nleaves; i++) {
        waterleaf_t w;
        BuildWaterLeaf(leaves[i], &w);
        int k;
        for (k = 0; k < n; k++)
            if (IsLowerLeaf(&w, &list[k])) break;
        memmove(&list[k + 1], &list[k], sizeof(waterleaf_t) * (n - k));
        list[k] = w;
        n++;
    }
    unsigned char *visited = xalloc(numleafs + 1);
    int *area = NULL, narea = 0, areacap = 0;
    for (int i = 0; i < n; i++) {
        Flood_r(list[i].node, &list[i], visited, &area, &narea, &areacap);
        if (!narea) continue;
        if (numwatermodels % 16 == 0) watermodels = realloc(watermodels, sizeof(watermodel_t) * (numwatermodels + 16));
        watermodel_t *m = &watermodels[numwatermodels++];
        m->model = nummodels;
        m->contents = list[i].node->contents;
        m->data = list[i];
        m->first = numwaterleaves;
        m->count = narea;
        float depth = m->data.dist - m->data.minz;
        if (m->data.texinfo < 0) m->data.texinfo = FirstWaterTexinfo(list[i].node->brushlist, m->contents);
        m->depthtexinfo = FindOrCreateWaterTexInfo(m->data.texinfo, depth);
        m->fog = FindOrCreateLeafWaterData(m->data.dist, m->data.minz, m->data.texinfo);
        waterleaves = realloc(waterleaves, sizeof(int) * (numwaterleaves + narea));
        memcpy(waterleaves + numwaterleaves, area, sizeof(int) * narea);
        numwaterleaves += narea;
        narea = 0;
    }
    free(area);
    free(visited);
    free(list);
    free(leaves);
}

/* For the physics: the world's water models (model 0), in order. The leaves get their water data. */
int WaterModelCount(int model) {
    int c = 0;
    for (int i = 0; i < numwatermodels; i++)
        if (watermodels[i].model == model) c++;
    return c;
}

int WaterModelInfo(int model, int k, int *contents, int **leaves, int *nleaves, float *normal, float *dist, int *has_surface) {
    for (int i = 0; i < numwatermodels; i++) {
        watermodel_t *m = &watermodels[i];
        if (m->model != model || k--) continue;
        for (int j = 0; j < m->count; j++) dleafs[waterleaves[m->first + j]].leafwaterdata = (short)m->fog;
        *contents = m->contents;
        *leaves = waterleaves + m->first;
        *nleaves = m->count;
        VectorCopy(m->data.normal, normal);
        *dist = m->data.dist;
        *has_surface = m->data.has_surface;
        return 1;
    }
    return 0;
}

/* CompactTexinfos */
void Water_CountTexinfos(int *refcount) {
    for (int i = 0; i < numleafwater; i++)
        if (leafwater[i].texinfo >= 0) refcount[leafwater[i].texinfo]++;
}

void Water_RemapTexinfos(const int *newindex) {
    for (int i = 0; i < numleafwater; i++)
        if (leafwater[i].texinfo >= 0) leafwater[i].texinfo = (short)newindex[leafwater[i].texinfo];
}

unsigned char *Water_Lump(int *len) {
    *len = (int)sizeof(dleafwaterdata_t) * numleafwater;
    return (unsigned char *)leafwater;
}

/* vbsp adds a water_lod_control when the map has water and none of its own. */
void EnsurePresenceOfWaterLODControlEntity(void) {
    if (!g_has_water) return;
    for (int i = 0; i < num_entities; i++)
        if (!_stricmp(ValueForKey(&entities[i], "classname"), "water_lod_control")) return;
    Warning("Water found with no water_lod_control entity, creating a default one.\n");
    extern entity_t *AllocEntity(void);
    entity_t *e = AllocEntity();
    e->firstbrush = nummapbrushes;
    SetKeyValue(e, "classname", "water_lod_control");
    SetKeyValue(e, "cheapwaterstartdistance", "1000");
    SetKeyValue(e, "cheapwaterenddistance", "2000");
}
