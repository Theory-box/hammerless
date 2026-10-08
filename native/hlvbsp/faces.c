/* Faces: the portals between solid and empty that make visible surfaces, merged where they can be,
 * cut to the lightmap size limit, welded to shared vertices and with T-junctions fixed. */
#include "hlvbsp.h"

#define INTEGRAL_EPSILON 0.01
#define POINT_EPSILON 0.1
#define OFF_EPSILON 0.25
#define CONTINUOUS_EPSILON 0.001
#define EQUAL_EPSILON 0.001
#define MAX_SUPERVERTS 512
#define HASH_BITS 7
#define HASH_SIZE (COORD_EXTENT >> HASH_BITS)

static int superverts[MAX_SUPERVERTS], numsuperverts;
int firstmodeledge = 1;
float g_maxLightmapDimension = 32;
face_t **edgefaces[2];

static int *vertexchain;          /* next vertex in a hash chain */
static int hashverts[HASH_SIZE * HASH_SIZE];
static vec3_t edge_dir, edge_start;
static int num_edge_verts, *edge_verts;

/* primitives (triangle lists for faces whose T-junction fix left no good start vertex) */
typedef struct { unsigned char type; unsigned short firstindex, numindices, firstvert, numverts; } dprimitive_t;
dprimitive_t g_primitives[32768];
int g_numprimitives;
unsigned short g_primindices[65536 * 2];
int g_numprimindices;
int g_numprimverts;

/* per-vertex list of edges (sorted), for edge sharing */
typedef struct { int *e, n, cap; } intlist_t;
static intlist_t *vert_edges;
static int max_vert_edges;

static void ensure_vertex_arrays(void) {
    if (!vertexchain) {
        vertexchain = xalloc(sizeof(int) * 65536 * 4);
        edge_verts = xalloc(sizeof(int) * 65536 * 4);
    }
}

static unsigned HashVec(const vec3_t vec) {
    int x = (MAX_COORD_INTEGER + (int)(vec[0] + 0.5)) >> HASH_BITS;
    int y = (MAX_COORD_INTEGER + (int)(vec[1] + 0.5)) >> HASH_BITS;
    if (x < 0 || x >= HASH_SIZE || y < 0 || y >= HASH_SIZE) Error("HashVec: point outside valid range");
    return y * HASH_SIZE + x;
}

/* Find or add a vertex: near-integer coordinates are rounded, points within 0.1 are the same. */
int GetVertexnum(const vec3_t in) {
    vec3_t vert;
    ensure_vertex_arrays();
    for (int i = 0; i < 3; i++) {
        if (fabs(in[i] - (int)(in[i] + 0.5)) < INTEGRAL_EPSILON) vert[i] = (vec_t)(int)(in[i] + 0.5);
        else vert[i] = in[i];
    }
    unsigned h = HashVec(vert);
    for (int vnum = hashverts[h]; vnum; vnum = vertexchain[vnum]) {
        vec_t *p = dvertexes[vnum].point;
        if (fabs(p[0] - vert[0]) < POINT_EPSILON && fabs(p[1] - vert[1]) < POINT_EPSILON && fabs(p[2] - vert[2]) < POINT_EPSILON)
            return vnum;
    }
    if (numvertexes == 65536) Error("Too many unique verts, max = %d (map has too much brush geometry)\n", 65536);
    VectorCopy(vert, dvertexes[numvertexes].point);
    vertexchain[numvertexes] = hashverts[h];
    hashverts[h] = numvertexes;
    numvertexes++;
    return numvertexes - 1;
}

face_t *AllocFace(void) {
    static int s_id = 0;
    face_t *f = xalloc(sizeof(*f));
    f->id = s_id++;
    return f;
}

face_t *NewFaceFromFace(face_t *f) {
    face_t *n = AllocFace();
    int id = n->id;
    *n = *f;
    n->id = id;
    n->merged = NULL;
    n->split[0] = n->split[1] = NULL;
    n->w = NULL;
    return n;
}

void FreeFace(face_t *f) {
    if (f->w) FreeWinding(f->w);
    free(f);
}

void FreeFaceList(face_t *f) {
    while (f) {
        face_t *next = f->next;
        FreeFace(f);
        f = next;
    }
}

/* A face can hold MAXEDGES points; more get split off into a chain of faces. */
static void FaceFromSuperverts(face_t **list, face_t *f, int base) {
    int remaining = numsuperverts;
    while (remaining > MAXEDGES) {
        face_t *newf = NewFaceFromFace(f);
        f->split[0] = newf;
        newf->next = *list;
        *list = newf;
        newf->numpoints = MAXEDGES;
        for (int i = 0; i < MAXEDGES; i++) newf->vertexnums[i] = superverts[(i + base) % numsuperverts];
        f->split[1] = NewFaceFromFace(f);
        f = f->split[1];
        f->next = *list;
        *list = f;
        remaining -= (MAXEDGES - 2);
        base = (base + MAXEDGES - 1) % numsuperverts;
    }
    f->numpoints = remaining;
    for (int i = 0; i < remaining; i++) f->vertexnums[i] = superverts[(i + base) % numsuperverts];
}

static void EmitFaceVertexes(face_t **list, face_t *f) {
    if (f->merged || f->split[0] || f->split[1]) return;
    winding_t *w = f->w;
    for (int i = 0; i < w->numpoints; i++) superverts[i] = GetVertexnum(w->p[i]);
    numsuperverts = w->numpoints;
    FaceFromSuperverts(list, f, 0);
}

void EmitDispFaceVertexes(face_t *f) { EmitFaceVertexes(NULL, f); }

static void EmitNodeFaceVertexes_r(node_t *node) {
    if (node->planenum == PLANENUM_LEAF) return;
    for (face_t *f = node->faces; f; f = f->next) EmitFaceVertexes(&node->faces, f);
    EmitNodeFaceVertexes_r(node->children[0]);
    EmitNodeFaceVertexes_r(node->children[1]);
}

static void EmitLeafFaceVertexes(face_t **list) {
    for (face_t *f = *list; f; f = f->next) EmitFaceVertexes(list, f);
}

/* The vertices in the hash cells the edge's box covers. */
static void FindEdgeVerts(const vec3_t v1, const vec3_t v2) {
    int x1 = (MAX_COORD_INTEGER + (int)(v1[0] + 0.5)) >> HASH_BITS;
    int y1 = (MAX_COORD_INTEGER + (int)(v1[1] + 0.5)) >> HASH_BITS;
    int x2 = (MAX_COORD_INTEGER + (int)(v2[0] + 0.5)) >> HASH_BITS;
    int y2 = (MAX_COORD_INTEGER + (int)(v2[1] + 0.5)) >> HASH_BITS;
    int t;
    if (x1 > x2) { t = x1; x1 = x2; x2 = t; }
    if (y1 > y2) { t = y1; y1 = y2; y2 = t; }
    num_edge_verts = 0;
    for (int x = x1; x <= x2; x++)
        for (int y = y1; y <= y2; y++)
            for (int vnum = hashverts[y * HASH_SIZE + x]; vnum; vnum = vertexchain[vnum]) edge_verts[num_edge_verts++] = vnum;
}

/* Split edge p1-p2 at every vertex lying on it (within 0.25), recursively. */
static void TestEdge(vec_t start, vec_t end, int p1, int p2, int startvert) {
    if (p1 == p2) return;      /* degenerate edge */
    for (int k = startvert; k < num_edge_verts; k++) {
        int j = edge_verts[k];
        if (j == p1 || j == p2) continue;
        vec3_t p, delta, exact, off;
        VectorCopy(dvertexes[j].point, p);
        VectorSubtract(p, edge_start, delta);
        vec_t dist = DotProduct(delta, edge_dir);
        if (dist <= start || dist >= end) continue;
        VectorMA(edge_start, dist, edge_dir, exact);
        VectorSubtract(p, exact, off);
        vec_t error = sqrtf((off[1] * off[1] + off[2] * off[2]) + off[0] * off[0]);
        if (error > OFF_EPSILON) continue;
        TestEdge(start, dist, p1, j, k + 1);
        TestEdge(dist, end, j, p2, k + 1);
        return;
    }
    if (numsuperverts >= MAX_SUPERVERTS) Error("Edge with too many vertices due to t-junctions.  Max %d verts along an edge!\n", MAX_SUPERVERTS);
    superverts[numsuperverts++] = p1;
}

/* Triangulation for faces with a bad start vertex: corner -> its (up to two) original edges. */
typedef struct { int e0, e1; } vert_edges_t;
static int HasEdge(const vert_edges_t *v, int e) { return e >= 0 && (v->e0 == e || v->e1 == e); }
static int IsDiagonal(const vert_edges_t *a, const vert_edges_t *b) {
    return !(HasEdge(b, a->e0) || HasEdge(b, a->e1));
}

static void Triangulate_r(int *out, int *nout, const int *in, int nin, const vert_edges_t *poly) {
    if (nin == 3) {
        for (int i = 0; i < 3; i++) out[(*nout)++] = in[i];
        return;
    }
    for (int i = 0; i < nin; i++) {
        for (int j = 2; j < nin - 1; j++) {
            int index = in[i], nexta = (i + j) % nin, nexti = in[nexta];
            if (IsDiagonal(&poly[index], &poly[nexti])) {
                int *in1 = xalloc(sizeof(int) * (nin + 1)), *in2 = xalloc(sizeof(int) * (nin + 1)), n1 = 0, n2 = 0;
                for (int k = i; k != nexta; k = (k + 1) % nin) in1[n1++] = in[k];
                in1[n1++] = nexti;
                in2[n2++] = index;
                for (int l = nexta; l != i; l = (l + 1) % nin) in2[n2++] = in[l];
                Triangulate_r(out, nout, in1, n1, poly);
                Triangulate_r(out, nout, in2, n2, poly);
                free(in1);
                free(in2);
                return;
            }
        }
    }
}

static void FixFaceEdges(face_t **list, face_t *f) {
    int count[MAX_SUPERVERTS], start[MAX_SUPERVERTS];
    if (f->merged || f->split[0] || f->split[1]) return;
    numsuperverts = 0;
    int original_points = f->numpoints;
    for (int i = 0; i < f->numpoints; i++) {
        int p1 = f->vertexnums[i], p2 = f->vertexnums[(i + 1) % f->numpoints];
        vec3_t e2;
        VectorCopy(dvertexes[p1].point, edge_start);
        VectorCopy(dvertexes[p2].point, e2);
        FindEdgeVerts(edge_start, e2);
        VectorSubtract(e2, edge_start, edge_dir);
        vec_t len = VectorNormalizeX87(edge_dir);     /* (x87, like vbsp) */
        start[i] = numsuperverts;
        TestEdge(0, len, p1, p2, 0);
        count[i] = numsuperverts - start[i];
    }
    if (numsuperverts < 3) {        /* the whole face collapsed */
        f->numpoints = 0;
        return;
    }
    int i, base;
    /* start at a corner that has no added points on either side, if there is one */
    for (i = 0; i < f->numpoints; i++)
        if (count[i] == 1 && count[(i + f->numpoints - 1) % f->numpoints] == 1) break;
    if (i == f->numpoints) {
        f->badstartvert = 1;
        base = 0;
    } else base = start[i];
    FaceFromSuperverts(list, f, base);
    if (f->badstartvert && entity_num == 0) {
        vert_edges_t *poly = xalloc(sizeof(vert_edges_t) * numsuperverts);
        for (int k = 0; k < numsuperverts; k++) poly[k].e0 = poly[k].e1 = -1;
        for (i = 0; i < original_points; i++) {
            if (!count[i]) continue;
            for (int j = 0; j <= count[i]; j++) {
                vert_edges_t *v = &poly[(start[i] + j) % numsuperverts];
                if (v->e0 == -1) v->e0 = i;
                else v->e1 = i;
            }
        }
        int *in = xalloc(sizeof(int) * numsuperverts), *out = xalloc(sizeof(int) * numsuperverts * 3), nout = 0;
        for (int k = 0; k < numsuperverts; k++) in[k] = k;
        Triangulate_r(out, &nout, in, numsuperverts, poly);
        dprimitive_t *prim = &g_primitives[g_numprimitives];
        f->firstprim = g_numprimitives;
        g_numprimitives++;
        f->numprims = 1;
        prim->firstindex = (unsigned short)g_numprimindices;
        prim->firstvert = (unsigned short)g_numprimverts;
        prim->numindices = (unsigned short)nout;
        prim->numverts = 0;
        prim->type = 0;     /* triangle list */
        for (int k = 0; k < nout; k++) g_primindices[g_numprimindices + k] = (unsigned short)out[k];
        g_numprimindices += nout;
        free(poly);
        free(in);
        free(out);
    }
}

static void FixEdges_r(node_t *node) {
    if (node->planenum == PLANENUM_LEAF) return;
    for (face_t *f = node->faces; f; f = f->next) FixFaceEdges(&node->faces, f);
    FixEdges_r(node->children[0]);
    FixEdges_r(node->children[1]);
}

face_t *FixTjuncs(node_t *headnode, face_t *leaffaces) {
    ensure_vertex_arrays();
    memset(hashverts, 0, sizeof(hashverts));
    memset(vertexchain, 0, sizeof(int) * 65536 * 4);
    EmitNodeFaceVertexes_r(headnode);
    EmitLeafFaceVertexes(&leaffaces);
    FixEdges_r(headnode);
    for (face_t *f = leaffaces; f; f = f->next) FixFaceEdges(&leaffaces, f);
    return leaffaces;
}

/* ------------------------------------------------------------------ edges */
void ResetEdgeLists(void) {
    for (int i = 0; i < max_vert_edges; i++) vert_edges[i].n = 0;
}

static void IntSort(intlist_t *l) {
    for (int i = 0; i < l->n - 1; i++) {
        if (l->e[i] > l->e[i + 1]) {
            int t = l->e[i];
            l->e[i] = l->e[i + 1];
            l->e[i + 1] = t;
            i = i > 0 ? i - 2 : -1;
        }
    }
}

static void list_add(int v, int e) {
    if (v >= max_vert_edges) {
        int n = max_vert_edges ? max_vert_edges : 1024;
        while (n <= v) n *= 2;
        vert_edges = realloc(vert_edges, sizeof(intlist_t) * n);
        memset(vert_edges + max_vert_edges, 0, sizeof(intlist_t) * (n - max_vert_edges));
        max_vert_edges = n;
    }
    intlist_t *l = &vert_edges[v];
    if (l->n == l->cap) {
        l->cap = l->cap ? l->cap * 2 : 8;
        l->e = realloc(l->e, sizeof(int) * l->cap);
    }
    l->e[l->n++] = e;
    IntSort(l);
}

int AddEdge(int v1, int v2, face_t *f) {
    if (numedges >= 256000) Error("Too many edges in map, max == %d", 256000);
    list_add(v1, numedges);
    list_add(v2, numedges);
    dedges[numedges].v[0] = (unsigned short)v1;
    dedges[numedges].v[1] = (unsigned short)v2;
    edgefaces[0][numedges] = f;
    edgefaces[1][numedges] = NULL;
    numedges++;
    return numedges - 1;
}

/* Share an edge already used the other way round by one face of the same contents. */
int GetEdge2(int v1, int v2, face_t *f) {
    if (v1 < max_vert_edges) {
        intlist_t *l = &vert_edges[v1];
        for (int i = 0; i < l->n; i++) {
            int e = l->e[i];
            dedge_t *edge = &dedges[e];
            if (v1 == edge->v[1] && v2 == edge->v[0] && edgefaces[0][e]->contents == f->contents) {
                if (edgefaces[1][e]) continue;
                edgefaces[1][e] = f;
                return -e;
            }
        }
    }
    return AddEdge(v1, v2, f);
}

/* ------------------------------------------------------------------ merging */
/* Two polygons sharing an edge, joined when the result stays convex; NULL otherwise. */
static winding_t *TryMergeWinding(winding_t *f1, winding_t *f2, const vec3_t planenormal) {
    vec_t *p1 = NULL, *p2 = NULL, *back;
    int i, j = 0, k, l;
    for (i = 0; i < f1->numpoints; i++) {
        p1 = f1->p[i];
        p2 = f1->p[(i + 1) % f1->numpoints];
        for (j = 0; j < f2->numpoints; j++) {
            vec_t *p3 = f2->p[j], *p4 = f2->p[(j + 1) % f2->numpoints];
            for (k = 0; k < 3; k++) {
                if (fabsf(p1[k] - p4[k]) > EQUAL_EPSILON) break;
                if (fabsf(p2[k] - p3[k]) > EQUAL_EPSILON) break;
            }
            if (k == 3) break;
        }
        if (j < f2->numpoints) break;
    }
    if (i == f1->numpoints) return NULL;
    vec3_t normal, delta;
    back = f1->p[(i + f1->numpoints - 1) % f1->numpoints];
    VectorSubtract(p1, back, delta);
    CrossProduct(planenormal, delta, normal);
    VectorNormalize(normal);
    back = f2->p[(j + 2) % f2->numpoints];
    VectorSubtract(back, p1, delta);
    vec_t dot = DotProduct(delta, normal);
    if (dot > CONTINUOUS_EPSILON) return NULL;
    int keep1 = dot < -CONTINUOUS_EPSILON;
    back = f1->p[(i + 2) % f1->numpoints];
    VectorSubtract(back, p2, delta);
    CrossProduct(planenormal, delta, normal);
    VectorNormalize(normal);
    back = f2->p[(j + f2->numpoints - 1) % f2->numpoints];
    VectorSubtract(back, p2, delta);
    dot = DotProduct(delta, normal);
    if (dot > CONTINUOUS_EPSILON) return NULL;
    int keep2 = dot < -CONTINUOUS_EPSILON;
    winding_t *newf = AllocWinding(f1->numpoints + f2->numpoints);
    for (k = (i + 1) % f1->numpoints; k != i; k = (k + 1) % f1->numpoints) {
        if (k == (i + 1) % f1->numpoints && !keep2) continue;
        VectorCopy(f1->p[k], newf->p[newf->numpoints]);
        newf->numpoints++;
    }
    for (l = (j + 1) % f2->numpoints; l != j; l = (l + 1) % f2->numpoints) {
        if (l == (j + 1) % f2->numpoints && !keep1) continue;
        VectorCopy(f2->p[l], newf->p[newf->numpoints]);
        newf->numpoints++;
    }
    return newf;
}

static face_t *TryMerge(face_t *f1, face_t *f2, const vec3_t planenormal) {
    if (!f1->w || !f2->w) return NULL;
    if (f1->texinfo != f2->texinfo || f1->planenum != f2->planenum || f1->contents != f2->contents) return NULL;
    if (f1->originalface->smoothing != f2->originalface->smoothing) return NULL;
    if (!OverlaysAreEqual(f1->originalface, f2->originalface)) return NULL;
    winding_t *nw = TryMergeWinding(f1->w, f2->w, planenormal);
    if (!nw) return NULL;
    face_t *newf = NewFaceFromFace(f1);
    newf->w = nw;
    f1->merged = newf;
    f2->merged = newf;
    return newf;
}

void MergeFaceList(face_t **list) {
    for (face_t *f1 = *list; f1; f1 = f1->next) {
        if (f1->merged || f1->split[0] || f1->split[1]) continue;
        for (face_t *f2 = *list; f2 != f1; f2 = f2->next) {
            if (f2->merged || f2->split[0] || f2->split[1]) continue;
            face_t *merged = TryMerge(f1, f2, mapplanes[f1->planenum].normal);
            if (!merged) continue;
            face_t *end;
            for (end = *list; end->next; end = end->next);
            merged->next = NULL;
            end->next = merged;
            break;
        }
    }
}

/* Cut faces wider than the lightmap limit (32 luxels) along the lightmap axes. */
static void SubdivideFace(face_t **list, face_t *f) {
    if (f->merged || f->split[0] || f->split[1]) return;
    texinfo_t *tex = &texinfos[f->texinfo];
    if (tex->flags & SURF_NOLIGHT) return;
    for (int axis = 0; axis < 2; axis++) {
        for (;;) {
            float mins = 999999, maxs = -999999;
            vec3_t temp;
            VectorCopy(tex->lmvecs[axis], temp);
            winding_t *w = f->w;
            for (int i = 0; i < w->numpoints; i++) {
                vec_t v = DotProduct(w->p[i], temp);
                if (v < mins) mins = v;
                if (v > maxs) maxs = v;
            }
            if (maxs - mins <= g_maxLightmapDimension) break;
            double luxels_per_unit = VectorNormalizeX87d(temp);
            /* (x87: ((32 + mins) - 1) / the unrounded length, rounded once - measured) */
            vec_t dist = (float)(((double)g_maxLightmapDimension + mins - 1.0) / luxels_per_unit);
            winding_t *frontw, *backw;
            ClipWindingEpsilon(w, temp, dist, ON_EPSILON, &frontw, &backw);
            if (!frontw || !backw) Error("SubdivideFace: didn't split the polygon");
            f->split[0] = NewFaceFromFace(f);
            f->split[0]->w = frontw;
            f->split[0]->next = *list;
            *list = f->split[0];
            f->split[1] = NewFaceFromFace(f);
            f->split[1]->w = backw;
            f->split[1]->next = *list;
            *list = f->split[1];
            SubdivideFace(list, f->split[0]);
            SubdivideFace(list, f->split[1]);
            return;
        }
    }
}

void SubdivideFaceList(face_t **list) {
    for (face_t *f = *list; f; f = f->next) SubdivideFace(list, f);
}

/* ------------------------------------------------------------------ faces from portals */
static face_t *FaceFromPortal(portal_t *p, int pside) {
    side_t *side = p->side;
    if (!side) return NULL;
    face_t *f = AllocFace();
    f->originalface = side;
    f->texinfo = side->texinfo;
    f->dispinfo = -1;
    f->smoothing = side->smoothing;
    f->planenum = (side->planenum & ~1) | pside;
    if (entity_num != 0) {
        if (p->nodes[pside]->contents & (CONTENTS_WATER | CONTENTS_SLIME)) f->planenum = (side->planenum & ~1) | pside;
        else f->planenum = side->planenum;
    }
    f->portal = p;
    int delta = VisibleContents(p->nodes[!pside]->contents ^ p->nodes[pside]->contents);
    /* no faces between two windows or two grates */
    if (((p->nodes[pside]->contents & CONTENTS_WINDOW) && delta == CONTENTS_WINDOW) ||
        ((p->nodes[pside]->contents & CONTENTS_GRATE) && delta == CONTENTS_GRATE)) {
        FreeFace(f);
        return NULL;
    }
    if (p->nodes[pside]->contents & MASK_WATER) f->fogleaf = p->nodes[pside];
    else if (p->nodes[!pside]->contents & MASK_WATER) f->fogleaf = p->nodes[!pside];
    /* a water surface seen from below takes its $bottommaterial */
    if ((p->nodes[pside]->contents & CONTENTS_WATER) && delta == CONTENTS_WATER) {
        extern int BottomWaterTexinfo(int texinfo);
        int t = BottomWaterTexinfo(f->texinfo);
        if (t < 0) {
            FreeFace(f);
            return NULL;
        }
        f->texinfo = t;
    }
    if (pside) {
        f->w = ReverseWinding(p->winding);
        f->contents = p->nodes[1]->contents;
    } else {
        f->w = CopyWinding(p->winding);
        f->contents = p->nodes[0]->contents;
    }
    return f;
}

static void MakeFaces_r(node_t *node) {
    if (node->planenum != PLANENUM_LEAF) {
        MakeFaces_r(node->children[0]);
        MakeFaces_r(node->children[1]);
        MergeFaceList(&node->faces);
        SubdivideFaceList(&node->faces);
        return;
    }
    if (node->contents & CONTENTS_SOLID) return;
    int s;
    for (portal_t *p = node->portals; p; p = p->next[s]) {
        s = (p->nodes[1] == node);
        p->face[s] = FaceFromPortal(p, s);
        if (p->face[s]) {
            p->face[s]->next = p->onnode->faces;
            p->onnode->faces = p->face[s];
        }
    }
}

void MakeFaces(node_t *node) {
    Msg("Building Faces...");
    MakeFaces_r(node);
    Msg("done (0)\n");
}
