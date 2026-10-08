/* func_occluder: brushes that hide what's behind them at run time. vbsp clips all occluder brushes
 * against each other in a BSP of their own, makes faces of it (whose vertices go into the map's
 * vertex list) and stores, per occluder, the faces that came from its own sides (lump 9, version 2),
 * then the area each occluder is in. The occluder entities keep no brushes. As vbsp. */
#include "hlvbsp.h"
#include <float.h>

typedef struct { int flags, firstpoly, polycount; vec3_t mins, maxs; int area; } doccluderdata_t;
typedef struct { int firstvertexindex, vertexcount, planenum; } doccluderpolydata_t;

static doccluderdata_t *occluders;
static int numoccluders;
static int *occluder_entity;
static doccluderpolydata_t *polys;
static int numpolys;
static int *vertindices;
static int numvertindices;

void MarkVisibleSidesList(tree_t *tree, mapbrush_t **brushes, int count);
bspbrush_t *ChopBrushes(bspbrush_t *head);
bspbrush_t *CreateClippedBrush(mapbrush_t *mb, const vec3_t clipmins, const vec3_t clipmaxs);
void ComputeBoundingPlanes(const vec3_t mins, const vec3_t maxs);

static int IsFuncOccluder(int e) { return !strcmp(ValueForKey(&entities[e], "classname"), "func_occluder"); }

static tree_t *ClipOccluderBrushes(mapbrush_t ***brushes_out, int *count_out) {
    mapbrush_t **brushes = NULL;
    int count = 0;
    for (entity_num = 0; entity_num < num_entities; ++entity_num) {
        if (!IsFuncOccluder(entity_num)) continue;
        entity_t *e = &entities[entity_num];
        for (int i = e->firstbrush; i < e->firstbrush + e->numbrushes; ++i) {
            brushes = realloc(brushes, sizeof(mapbrush_t *) * (count + 1));
            brushes[count++] = &mapbrushes[i];
        }
    }
    *brushes_out = brushes;
    *count_out = count;
    if (!count) return NULL;
    vec3_t mins = {MIN_COORD_INTEGER, MIN_COORD_INTEGER, MIN_COORD_INTEGER};
    vec3_t maxs = {MAX_COORD_INTEGER, MAX_COORD_INTEGER, MAX_COORD_INTEGER};
    ComputeBoundingPlanes(mins, maxs);
    bspbrush_t *list = NULL;
    for (int i = 0; i < count; i++) {
        bspbrush_t *nb = CreateClippedBrush(brushes[i], mins, maxs);
        if (nb) {
            nb->next = list;
            list = nb;
        }
    }
    list = ChopBrushes(list);
    tree_t *tree = BrushBSP(list, mins, maxs);
    MakeTreePortals(tree);
    MarkVisibleSidesList(tree, brushes, count);
    MakeFaces(tree->headnode);
    FixTjuncs(tree->headnode, NULL);          /* (this writes the occluder faces' vertices) */
    return tree;
}

static void GenerateOccluderFaceList(node_t *node, face_t ***list, int *n, int *cap) {
    if (node->planenum == PLANENUM_LEAF) return;
    for (face_t *f = node->faces; f; f = f->next) {
        if (*n == *cap) {
            *cap = *cap ? *cap * 2 : 256;
            *list = realloc(*list, sizeof(face_t *) * *cap);
        }
        (*list)[(*n)++] = f;
    }
    GenerateOccluderFaceList(node->children[0], list, n, cap);
    GenerateOccluderFaceList(node->children[1], list, n, cap);
}

/* After the displacements, before the models: the occluder data; the occluders lose their brushes. */
void EmitOccluderBrushes(void) {
    numoccluders = numpolys = numvertindices = 0;
    mapbrush_t **brushes;
    int nbrushes;
    tree_t *tree = ClipOccluderBrushes(&brushes, &nbrushes);
    if (!tree) {
        free(brushes);
        return;
    }
    face_t **faces = NULL;
    int nfaces = 0, cap = 0;
    GenerateOccluderFaceList(tree->headnode, &faces, &nfaces, &cap);
    for (entity_num = 1; entity_num < num_entities; ++entity_num) {
        if (!IsFuncOccluder(entity_num)) continue;
        occluders = realloc(occluders, sizeof(doccluderdata_t) * (numoccluders + 1));
        occluder_entity = realloc(occluder_entity, sizeof(int) * (numoccluders + 1));
        doccluderdata_t *o = &occluders[numoccluders];
        occluder_entity[numoccluders] = entity_num;
        o->firstpoly = numpolys;
        o->mins[0] = o->mins[1] = o->mins[2] = FLT_MAX;
        o->maxs[0] = o->maxs[1] = o->maxs[2] = -FLT_MAX;
        o->flags = 0;
        o->area = -1;
        char str[64];
        sprintf(str, "%i", numoccluders);
        SetKeyValue(&entities[entity_num], "occludernumber", str);
        numoccluders++;
        entity_t *e = &entities[entity_num];
        int first = e->firstbrush, end = e->firstbrush + e->numbrushes;
        for (int i = nfaces; --i >= 0;) {
            face_t *f = faces[i];
            int flags = texinfos[f->texinfo].flags;
            if ((flags & SURF_NODRAW) && !(flags & SURF_TRIGGER)) continue;   /* (triggers marked nodraw do count) */
            int mine = 0;
            for (int b = end; --b >= first && !mine;)
                for (int s = mapbrushes[b].numsides; --s >= 0;)
                    if (&mapbrushes[b].original_sides[s] == f->originalface) {
                        mine = 1;
                        break;
                    }
            if (!mine || f->numpoints < 3) continue;
            polys = realloc(polys, sizeof(doccluderpolydata_t) * (numpolys + 1));
            doccluderpolydata_t *p = &polys[numpolys++];
            p->planenum = f->planenum;
            p->vertexcount = f->numpoints;
            p->firstvertexindex = numvertindices;
            vertindices = realloc(vertindices, sizeof(int) * (numvertindices + f->numpoints));
            for (int k = 0; k < f->numpoints; ++k) {
                vertindices[numvertindices++] = f->vertexnums[k];
                const float *pt = dvertexes[f->vertexnums[k]].point;
                for (int a = 0; a < 3; a++) {
                    if (pt[a] < o->mins[a]) o->mins[a] = pt[a];
                    if (pt[a] > o->maxs[a]) o->maxs[a] = pt[a];
                }
            }
        }
        o->polycount = numpolys - o->firstpoly;
        entities[entity_num].numbrushes = 0;
    }
    free(faces);
    free(brushes);
    FreeTree(tree);
}

/* ------------------------------------------------------------------ areas */
static node_t *NodeForPoint(node_t *node, const vec3_t p) {
    while (node->planenum != PLANENUM_LEAF) {
        plane_t *plane = &mapplanes[node->planenum];
        vec_t d = DotProduct(p, plane->normal) - plane->dist;
        node = d >= 0 ? node->children[0] : node->children[1];
    }
    return node;
}

static void SetOccluderArea(int o, int area, int entnum) {
    if (occluders[o].area <= 0) occluders[o].area = area;
    else if (area != 0 && occluders[o].area != area) {
        const char *name = ValueForKey(&entities[entnum], "targetname");
        Warning("Occluder \"%s\" straddles multiple areas. This is invalid!\n", name[0] ? name : "");
    }
}

static void AssignAreaToOccluder(int o, tree_t *tree, int cross_areaportals) {
    for (int j = 0; j < occluders[o].polycount; ++j) {
        doccluderpolydata_t *p = &polys[occluders[o].firstpoly + j];
        for (int k = 0; k < p->vertexcount; ++k) {
            node_t *node = NodeForPoint(tree->headnode, dvertexes[vertindices[p->firstvertexindex + k]].point);
            SetOccluderArea(o, node->area, occluder_entity[o]);
            int other;
            for (portal_t *pp = node->portals; pp; pp = pp->next[!other]) {
                other = pp->nodes[0] == node ? 1 : 0;
                if (!pp->onnode) continue;
                if (!cross_areaportals && (pp->nodes[other]->contents & CONTENTS_AREAPORTAL)) continue;
                SetOccluderArea(o, pp->nodes[other] ? pp->nodes[other]->area : 0, occluder_entity[o]);
            }
        }
    }
}

/* After the world's faces are made. */
void AssignOccluderAreas(tree_t *tree) {
    for (int i = 0; i < numoccluders; ++i) {
        AssignAreaToOccluder(i, tree, 0);
        if (occluders[i].area <= 0) AssignAreaToOccluder(i, tree, 1);
    }
}

/* Lump 9 (version 2): the occluders, their polygons, their vertex indices. */
unsigned char *Occluder_Lump(int *len) {
    int size = 4 + 40 * numoccluders + 4 + 12 * numpolys + 4 + 4 * numvertindices;
    unsigned char *b = xalloc(size + 1), *p = b;
    memcpy(p, &numoccluders, 4); p += 4;
    for (int i = 0; i < numoccluders; i++) { memcpy(p, &occluders[i], 40); p += 40; }
    memcpy(p, &numpolys, 4); p += 4;
    for (int i = 0; i < numpolys; i++) { memcpy(p, &polys[i], 12); p += 12; }
    memcpy(p, &numvertindices, 4); p += 4;
    memcpy(p, vertindices, 4 * numvertindices);
    *len = size;
    return b;
}
