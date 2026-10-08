#include <float.h>
/* Displacements (terrain): reading them from the .vmf, their records in the BSP (dispinfo, vertices,
 * triangle tags), lightmap sizes and sample positions, which displacements neighbour which, and the
 * vertices each may drop for level of detail (so neighbours of different sizes don't crack).
 *
 * The layout tables (per size: each vertex's LOD node, its dependencies, the triangle order) follow
 * the displacement layout the engine uses; the details match vbsp's output. */
#include "hlvbsp.h"
#include "disp.h"

int nummapdisps;
mapdisp_t *mapdisps;
static int max_mapdisps;

/* ------------------------------------------------------------------ layout tables per power */
typedef struct { int x, y; } vi_t;
typedef struct { vi_t vert; short neighbor; } vdep_t;
typedef struct { vdep_t deps[2]; vdep_t rdeps[4]; short nodelevel; vi_t parent; } vertinfo_t;
typedef struct {
    vertinfo_t *vinfo;
    vi_t (*sideverts)[4], (*childverts)[4], (*sidecorners)[4];
    unsigned short (*erroredges)[2];
    unsigned short (*tris)[3];
    int ntris, power, side, sidem1, mid, maxverts, nodecount;
    int node_inc[5];
    vi_t root, corner[4];
} powerinfo_t;

static powerinfo_t powerinfos[5];
static int powerinfo_ready;

enum { EDGE_LEFT, EDGE_TOP, EDGE_RIGHT, EDGE_BOTTOM };
enum { CORNER_TO_CORNER, CORNER_TO_MIDPOINT, MIDPOINT_TO_CORNER };
enum { CORNER_LOWER_LEFT, CORNER_UPPER_LEFT, CORNER_UPPER_RIGHT, CORNER_LOWER_RIGHT };
enum { CHILD_UPPER_RIGHT, CHILD_UPPER_LEFT, CHILD_LOWER_LEFT, CHILD_LOWER_RIGHT };

static const int side_mul[4][2] = {{1, 0}, {0, 1}, {-1, 0}, {0, -1}};
static const int side_corners[4][2][2] = {{{1, -1}, {1, 1}}, {{1, 1}, {-1, 1}}, {{-1, 1}, {-1, -1}}, {{-1, -1}, {1, -1}}};
static const vi_t child_mul[4] = {{1, 1}, {-1, 1}, {-1, -1}, {1, -1}};
static const vi_t child_deps[4][2] = {{{1, 0}, {0, 1}}, {{0, 1}, {-1, 0}}, {{-1, 0}, {0, -1}}, {{0, -1}, {1, 0}}};
/* the winding around a node: offsets and which child node sits there (-1: an edge midpoint) */
static const struct { vi_t index; int node; } twinding[9] = {
    {{1, -1}, CHILD_LOWER_RIGHT}, {{0, -1}, -1}, {{-1, -1}, CHILD_LOWER_LEFT}, {{-1, 0}, -1}, {{-1, 1}, CHILD_UPPER_LEFT},
    {{0, 1}, -1}, {{1, 1}, CHILD_UPPER_RIGHT}, {{1, 0}, -1}, {{1, -1}, CHILD_LOWER_RIGHT}};

static int VertIndex(vi_t v, int maxpower) { return v.y * ((1 << maxpower) + 1) + v.x; }

static int GetEdgeIndexFromPoint(vi_t index, int maxpower) {
    int m = 1 << maxpower;
    if (index.x == 0) return EDGE_LEFT;
    if (index.y == m) return EDGE_TOP;
    if (index.x == m) return EDGE_RIGHT;
    if (index.y == 0) return EDGE_BOTTOM;
    return -1;
}

static vi_t WrapVertIndex(vi_t in, int side) {
    int v[2] = {in.x, in.y}, out[2];
    for (int i = 0; i < 2; i++) {
        if (v[i] < 0) out[i] = side - 1 - (-v[i] % side);
        else if (v[i] >= side) out[i] = v[i] % side;
        else out[i] = v[i];
    }
    return (vi_t){out[0], out[1]};
}

static int GetFreeDependency(vdep_t *d, int n) {
    for (int i = 0; i < n; i++)
        if (d[i].vert.x == -1) return i;
    return 0;     /* (full: vbsp overwrites the first) */
}

static void AddDependency(vertinfo_t *deps, vi_t node, vi_t dep, int maxpower, int check_neighbor, int reverse) {
    vertinfo_t *pn = &deps[VertIndex(node, maxpower)];
    int i = GetFreeDependency(pn->deps, 2);
    pn->deps[i].vert = dep;
    pn->deps[i].neighbor = -1;
    if (reverse) {
        vertinfo_t *pd = &deps[VertIndex(dep, maxpower)];
        i = GetFreeDependency(pd->rdeps, 4);
        pd->rdeps[i].vert = node;
        pd->rdeps[i].neighbor = -1;
    }
    if (check_neighbor) {
        int conn = GetEdgeIndexFromPoint(node, maxpower);
        if (conn != -1) {
            vi_t delta = {node.x - dep.x, node.y - dep.y};
            vi_t n = {node.x + delta.x, node.y + delta.y};
            pn->deps[1].vert = WrapVertIndex(n, (1 << maxpower) + 1);
            pn->deps[1].neighbor = (short)conn;
        }
    }
}

static void InitPowerInfoTriInfos_R(powerinfo_t *pi, vi_t node, int *ntri, int maxpower, int level) {
    int inode = VertIndex(node, maxpower);
    if (level + 1 < maxpower) {
        for (int c = 0; c < 4; c++) InitPowerInfoTriInfos_R(pi, pi->childverts[inode][c], ntri, maxpower, level + 1);
        return;
    }
    unsigned short first = 0;
    int inc = 1 << ((maxpower - level) - 1), cur = 0;
    for (int v = 0; v < 9; v++) {
        vi_t sv = {node.x + twinding[v].index.x * inc, node.y + twinding[v].index.y * inc};
        if (cur == 1) {
            pi->tris[*ntri][0] = first;
            pi->tris[*ntri][1] = (unsigned short)VertIndex(sv, maxpower);
            pi->tris[*ntri][2] = (unsigned short)inode;
            (*ntri)++;
        }
        first = (unsigned short)VertIndex(sv, maxpower);
        cur = 1;
    }
}

static void InitPowerInfo_R(powerinfo_t *pi, int maxpower, vi_t node, vi_t dep1, vi_t dep2, vi_t edge1, vi_t edge2, vi_t parent, int level) {
    int inode = VertIndex(node, maxpower);
    pi->vinfo[inode].parent = parent;
    pi->vinfo[inode].nodelevel = (short)(level + 1);
    pi->erroredges[inode][0] = (unsigned short)VertIndex(edge1, maxpower);
    pi->erroredges[inode][1] = (unsigned short)VertIndex(edge2, maxpower);
    AddDependency(pi->vinfo, node, dep1, maxpower, 0, 1);
    AddDependency(pi->vinfo, node, dep2, maxpower, 0, 1);
    int inc = 1 << ((maxpower - level) - 1);
    for (int s = 0; s < 4; s++) {
        vi_t sv = {node.x + side_mul[s][0] * inc, node.y + side_mul[s][1] * inc};
        int isv = VertIndex(sv, maxpower);
        pi->sideverts[inode][s] = sv;
        vi_t c0 = {node.x + side_corners[s][0][0] * inc, node.y + side_corners[s][0][1] * inc};
        vi_t c1 = {node.x + side_corners[s][1][0] * inc, node.y + side_corners[s][1][1] * inc};
        pi->sidecorners[inode][s] = c0;
        pi->erroredges[isv][0] = (unsigned short)VertIndex(c0, maxpower);
        pi->erroredges[isv][1] = (unsigned short)VertIndex(c1, maxpower);
        AddDependency(pi->vinfo, sv, node, maxpower, 1, 1);
    }
    int ninc = inc >> 1;
    if (ninc) {
        for (int c = 0; c < 4; c++) {
            vi_t cv = {node.x + child_mul[c].x * ninc, node.y + child_mul[c].y * ninc};
            pi->childverts[inode][c] = cv;
            InitPowerInfo_R(pi, maxpower, cv,
                            (vi_t){node.x + child_deps[c][0].x * inc, node.y + child_deps[c][0].y * inc},
                            (vi_t){node.x + child_deps[c][1].x * inc, node.y + child_deps[c][1].y * inc}, node,
                            (vi_t){node.x + child_mul[c].x * inc, node.y + child_mul[c].y * inc}, node, level + 1);
        }
    }
}

static void InitPowerInfo(powerinfo_t *pi, int maxpower) {
    int side = (1 << maxpower) + 1, n = side * side;
    pi->vinfo = xalloc(sizeof(vertinfo_t) * n);
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < 2; k++) { pi->vinfo[i].deps[k].vert = (vi_t){-1, -1}; pi->vinfo[i].deps[k].neighbor = -1; }
        for (int k = 0; k < 4; k++) { pi->vinfo[i].rdeps[k].vert = (vi_t){-1, -1}; pi->vinfo[i].rdeps[k].neighbor = -1; }
        pi->vinfo[i].parent = (vi_t){-1, -1};
        pi->vinfo[i].nodelevel = -1;
    }
    pi->sideverts = xalloc(sizeof(vi_t) * 4 * n);
    pi->childverts = xalloc(sizeof(vi_t) * 4 * n);
    pi->sidecorners = xalloc(sizeof(vi_t) * 4 * n);
    pi->erroredges = xalloc(sizeof(unsigned short) * 2 * n);
    pi->tris = xalloc(sizeof(unsigned short) * 3 * (side - 1) * (side - 1) * 2);
    pi->root = (vi_t){side / 2, side / 2};
    pi->side = side;
    pi->sidem1 = side - 1;
    pi->mid = side / 2;
    pi->maxverts = n;
    pi->corner[CORNER_LOWER_LEFT] = (vi_t){0, 0};
    pi->corner[CORNER_UPPER_LEFT] = (vi_t){0, side - 1};
    pi->corner[CORNER_UPPER_RIGHT] = (vi_t){side - 1, side - 1};
    pi->corner[CORNER_LOWER_RIGHT] = (vi_t){side - 1, 0};
    InitPowerInfo_R(pi, maxpower, pi->root, (vi_t){side - 1, side - 1}, (vi_t){0, 0}, (vi_t){0, 0},
                    (vi_t){side - 1, side - 1}, (vi_t){-1, -1}, 0);
    pi->power = maxpower;
    int ntri = 0;
    InitPowerInfoTriInfos_R(pi, pi->root, &ntri, maxpower, 0);
    int pow4 = 1, total = 0;
    for (int i = 0; i < maxpower - 1; i++) {
        total += pow4;
        pi->node_inc[maxpower - i - 2] = total;
        pow4 *= 4;
    }
    pi->nodecount = total + pow4;
    pi->ntris = (1 << maxpower) * (1 << maxpower) * 2;
}

static const powerinfo_t *GetPowerInfo(int power) {
    if (!powerinfo_ready) {
        for (int p = 2; p <= 4; p++) InitPowerInfo(&powerinfos[p], p);
        powerinfo_ready = 1;
    }
    if (power < 2 || power > 4) Error("displacement power %d not supported (2 to 4)", power);
    return &powerinfos[power];
}

/* ------------------------------------------------------------------ the displacement while building */
typedef struct {
    unsigned short neighbor;
    unsigned char orientation, span, nbspan;
} subneighbor_t;
typedef struct { subneighbor_t sub[2]; } edgeneighbor_t;
typedef struct { unsigned short n[4]; unsigned char count; } cornerneighbors_t;

typedef struct coredisp_s {
    int power, index;
    vec3_t points[4];          /* the base quad, starting at the start position */
    float luxel[4][4][2];      /* [bump][point] */
    int luxel_u, luxel_v;
    vec3_t *verts;             /* positions */
    vec3_t *flat;              /* positions on the flat quad */
    float (*vluxel)[2];        /* per vertex lightmap coordinate (bump 0) */
    edgeneighbor_t edge[4];
    cornerneighbors_t corner[4];
    uint32_t allowed[10];
    const powerinfo_t *pi;
} coredisp_t;

static coredisp_t *cores;

static int VertIndexToInt(const coredisp_t *d, vi_t i) { return i.y * d->pi->side + i.x; }

static void SubVec(const vec3_t a, const vec3_t b, vec3_t c) { VectorSubtract(a, b, c); }

static int LongestInU(coredisp_t *d, const vec3_t u, const vec3_t v) {
    vec3_t nu, nv;
    VectorCopy(u, nu);
    VectorCopy(v, nv);
    VectorNormalize(nu);
    VectorNormalize(nv);
    float du[4], dv[4];
    for (int i = 0; i < 4; ++i) {
        du[i] = DotProduct(nu, d->points[i]);
        dv[i] = DotProduct(nv, d->points[i]);
    }
    float ul = 0.0f, vl = 0.0f;
    for (int i = 0; i < 4; ++i) {
        float t = fabsf(du[(i + 1) % 4] - du[i]);
        if (t > ul) ul = t;
        t = fabsf(dv[(i + 1) % 4] - dv[i]);
        if (t > vl) vl = t;
    }
    return !(ul < vl);
}

static float Dist(const vec3_t a, const vec3_t b) {
    vec3_t d;
    SubVec(a, b, d);
    return VectorLength(d);
}

/* Lightmap size from the quad's longest sides; true when u and v must be swapped. */
static int CalcLuxelCoords(coredisp_t *d, int luxels, const vec3_t vu, const vec3_t vv) {
    if (luxels <= 0.0f) return 0;
    int longu = LongestInU(d, vu, vv);
    float ul = Dist(d->points[3], d->points[0]), t = Dist(d->points[2], d->points[1]);
    if (t > ul) ul = t;
    float vl = Dist(d->points[1], d->points[0]);
    t = Dist(d->points[2], d->points[3]);
    if (t > vl) vl = t;
    float oo = 1.0f / (float)luxels;
    float uval = (float)((int)(ul * oo) + 1);
    if (uval > 125) uval = 125;
    float vval = (float)((int)(vl * oo) + 1);
    if (vval > 125) vval = 125;
    int swapped = longu ? vval > uval : uval > vval;
    d->luxel_u = (int)uval;
    d->luxel_v = (int)vval;
    for (int b = 0; b < 4; ++b) {
        d->luxel[b][0][0] = 0.5f; d->luxel[b][0][1] = 0.5f;
        d->luxel[b][1][0] = 0.5f; d->luxel[b][1][1] = (float)(vval + 0.5);
        d->luxel[b][2][0] = (float)(uval + 0.5); d->luxel[b][2][1] = (float)(vval + 0.5);
        d->luxel[b][3][0] = (float)(uval + 0.5); d->luxel[b][3][1] = 0.5f;
    }
    return swapped;
}

/* Positions: the flat quad interpolated row by row, plus each vertex's displacement. */
static void GenerateDispSurf(coredisp_t *d, const vec3_t *field, const float *dists) {
    int ps = (1 << d->power) + 1;
    float ooint = 1.0f / (float)(ps - 1);
    vec3_t e0, e1;
    VectorSubtract(d->points[1], d->points[0], e0);
    VectorScale(e0, ooint, e0);
    VectorSubtract(d->points[2], d->points[3], e1);
    VectorScale(e1, ooint, e1);
    for (int i = 0; i < ps; i++) {
        vec3_t end0, end1, seg, segint;
        VectorScale(e0, (float)i, end0);
        VectorAdd(end0, d->points[0], end0);
        VectorScale(e1, (float)i, end1);
        VectorAdd(end1, d->points[3], end1);
        VectorSubtract(end1, end0, seg);
        VectorScale(seg, ooint, segint);
        for (int j = 0; j < ps; j++) {
            int ndx = i * ps + j;
            vec3_t flat, t;
            VectorScale(segint, (float)j, t);
            VectorAdd(end0, t, flat);
            vec_t *v = d->verts[ndx];
            if (d->flat) VectorCopy(flat, d->flat[ndx]);
            VectorCopy(flat, v);
            /* (+ subdivision position, zero here) */
            v[0] += 0.0f; v[1] += 0.0f; v[2] += 0.0f;
            VectorScale(field[ndx], dists[ndx], t);
            VectorAdd(v, t, v);
        }
    }
}

static void CalcDispLuxelCoords(coredisp_t *d) {
    int ps = (1 << d->power) + 1;
    float ooint = (1.0f / (float)(ps - 1));
    float lc[4][2], e0[2], e1[2];
    for (int i = 0; i < 4; i++) { lc[i][0] = d->luxel[0][i][0]; lc[i][1] = d->luxel[0][i][1]; }
    e0[0] = lc[1][0] - lc[0][0]; e0[1] = lc[1][1] - lc[0][1];
    e1[0] = lc[2][0] - lc[3][0]; e1[1] = lc[2][1] - lc[3][1];
    e0[0] *= ooint; e0[1] *= ooint;
    e1[0] *= ooint; e1[1] *= ooint;
    for (int i = 0; i < ps; i++) {
        float end0[2] = {e0[0] * (float)i, e0[1] * (float)i}, end1[2] = {e1[0] * (float)i, e1[1] * (float)i};
        end0[0] += lc[0][0]; end0[1] += lc[0][1];
        end1[0] += lc[3][0]; end1[1] += lc[3][1];
        float seg[2] = {end1[0] - end0[0], end1[1] - end0[1]}, segint[2] = {seg[0] * ooint, seg[1] * ooint};
        for (int j = 0; j < ps; j++) {
            seg[0] = segint[0] * (float)j;
            seg[1] = segint[1] * (float)j;
            d->vluxel[i * ps + j][0] = end0[0] + seg[0];
            d->vluxel[i * ps + j][1] = end0[1] + seg[1];
        }
    }
}

/* The quad (rotated to start nearest the start position), vertex positions and lightmap coordinates.
 * Returns whether the lightmap axes are swapped. */
static int MapToCore(mapdisp_t *md, coredisp_t *d, dface_t *face, int *swapped_texinfos) {
    winding_t *w = md->face.originalface->winding;
    texinfo_t *tex = &texinfos[md->face.texinfo];
    md->contents = md->face.contents;
    if (!(md->contents & (ALL_VISIBLE_CONTENTS | CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP))) md->contents |= CONTENTS_SOLID;
    vec3_t pts[4];
    for (int i = 0; i < 4; i++) VectorCopy(w->p[i], pts[i]);
    int start = -1;
    float best = 999999999.0f;
    for (int i = 0; i < 4; i++) {
        vec3_t seg;
        VectorSubtract(md->startpos, pts[i], seg);
        float d2 = seg[0] * seg[0] + seg[1] * seg[1] + seg[2] * seg[2];
        if (d2 < best) {
            best = d2;
            start = i;
        }
    }
    for (int i = 0; i < 4; i++) VectorCopy(pts[(i + start) % 4], d->points[i]);
    d->power = md->power;
    d->pi = GetPowerInfo(md->power);
    vec3_t vu = {tex->lmvecs[0][0], tex->lmvecs[0][1], tex->lmvecs[0][2]};
    vec3_t vv = {tex->lmvecs[1][0], tex->lmvecs[1][1], tex->lmvecs[1][2]};
    int luxels = (int)(1.0f / VectorLength(vu));
    int swap = CalcLuxelCoords(d, luxels, vu, vv);
    if (face) {
        face->lm_size[0] = d->luxel_u;
        face->lm_size[1] = d->luxel_v;
        if (swap) {
            if (swapped_texinfos[md->face.texinfo] < 0) {
                texinfo_t t = *tex;
                memcpy(t.lmvecs[0], tex->lmvecs[1], 16);
                memcpy(t.lmvecs[1], tex->lmvecs[0], 16);
                for (int k = 0; k < 4; k++) t.lmvecs[1][k] *= -1.0f;
                swapped_texinfos[md->face.texinfo] = AppendTexinfo(&t);
            }
            md->face.texinfo = swapped_texinfos[md->face.texinfo];
        }
    }
    int n = d->pi->maxverts;
    vec3_t *field = xalloc(sizeof(vec3_t) * n);
    float *dists = xalloc(sizeof(float) * n);
    for (int j = 0; j < n; j++) {
        vec3_t v;
        VectorScale(md->normals[j], md->dists[j], v);
        VectorAdd(v, md->offsets[j], v);
        float dist = sqrtf((v[1] * v[1] + v[2] * v[2]) + v[0] * v[0]);
        VectorNormalizeX87(v);     /* (the length as VectorLength, the direction by mathlib's x87 normalize) */
        VectorCopy(v, field[j]);
        dists[j] = dist;
    }
    if (!d->verts) {
        d->verts = xalloc(sizeof(vec3_t) * n);
        d->vluxel = xalloc(sizeof(float) * 2 * n);
    }
    GenerateDispSurf(d, field, dists);
    CalcDispLuxelCoords(d);
    free(field);
    free(dists);
    return swap;
}

/* ------------------------------------------------------------------ neighbours */
static const int edge_dims[4] = {0, 1, 0, 1};
static const struct { int midscale, powershift, valid; } shiftinfos[3][3] = {
    {{0, 0, 1}, {0, -1, 1}, {2, -1, 1}}, {{0, 1, 1}, {0, 0, 0}, {0, 0, 0}}, {{-1, 1, 1}, {0, 0, 0}, {0, 0, 0}}};
static const int span_flip[3] = {CORNER_TO_CORNER, MIDPOINT_TO_CORNER, CORNER_TO_MIDPOINT};
static const int edge_flip[4] = {0, 0, 1, 1};
static const int orientation_map[4][4] = {{2, 3, 0, 1}, {1, 2, 3, 0}, {0, 1, 2, 3}, {3, 0, 1, 2}};

static int VectorsAreEqual(const vec3_t a, const vec3_t b, float tol) {
    if (fabsf(a[0] - b[0]) > tol) return 0;
    if (fabsf(a[1] - b[1]) > tol) return 0;
    return fabsf(a[2] - b[2]) <= tol;
}

static int FindEdge(coredisp_t *d, const vec3_t p1, const vec3_t p2, int *edge) {
    for (int e = 0; e < 4; e++)
        if (VectorsAreEqual(p1, d->points[e], 0.01f) && VectorsAreEqual(p2, d->points[(e + 1) & 3], 0.01f)) {
            *edge = e;
            return 1;
        }
    return 0;
}

static void AddNeighbor(coredisp_t *main, int iedge, int isub, int span, coredisp_t *other, int nbedge, int nbspan) {
    if (edge_flip[iedge]) span = span_flip[span];
    if (edge_flip[nbedge]) nbspan = span_flip[nbspan];
    subneighbor_t *s = &main->edge[iedge].sub[isub];
    subneighbor_t *ns = &other->edge[nbedge].sub[nbspan == MIDPOINT_TO_CORNER ? 1 : 0];
    if (s->neighbor != 0xFFFF || ns->neighbor != 0xFFFF) {
        static int once = 0;
        if (!once++) Warning("Found a displacement edge abutting multiple other edges.\n");
        return;
    }
    s->neighbor = (unsigned short)other->index;
    s->orientation = (unsigned char)orientation_map[iedge][nbedge];
    s->span = (unsigned char)span;
    s->nbspan = (unsigned char)nbspan;
    ns->neighbor = (unsigned short)main->index;
    ns->orientation = (unsigned char)orientation_map[nbedge][iedge];
    ns->span = (unsigned char)nbspan;
    ns->nbspan = (unsigned char)span;
}

static void SetupEdgeNeighbors(coredisp_t *main, coredisp_t *other) {
    for (int e = 0; e < 4; e++) {
        vec3_t p0, p1, mid, t;
        VectorCopy(main->points[e], p0);
        VectorCopy(main->points[(e + 1) & 3], p1);
        VectorAdd(p0, p1, mid);
        VectorScale(mid, 0.5f, mid);
        int nb;
        if (FindEdge(other, p1, p0, &nb)) {
            AddNeighbor(main, e, 0, CORNER_TO_CORNER, other, nb, CORNER_TO_CORNER);
            continue;
        }
        vec3_t a;
        VectorScale(p0, 2, a); VectorSubtract(a, p1, t);
        if (FindEdge(other, p1, t, &nb)) {
            AddNeighbor(main, e, 0, CORNER_TO_CORNER, other, nb, CORNER_TO_MIDPOINT);
            continue;
        }
        VectorScale(p1, 2, a); VectorSubtract(a, p0, t);
        if (FindEdge(other, t, p0, &nb)) {
            AddNeighbor(main, e, 0, CORNER_TO_CORNER, other, nb, MIDPOINT_TO_CORNER);
            continue;
        }
        if (FindEdge(other, mid, p0, &nb)) AddNeighbor(main, e, edge_flip[e], CORNER_TO_MIDPOINT, other, nb, CORNER_TO_CORNER);
        if (FindEdge(other, p1, mid, &nb)) AddNeighbor(main, e, !edge_flip[e], MIDPOINT_TO_CORNER, other, nb, CORNER_TO_CORNER);
    }
}

static int HasEdgeNeighbor(const coredisp_t *main, int nb) {
    for (int i = 0; i < 4; i++) {
        for (int k = 0; k < main->corner[i].count; k++)
            if (main->corner[i].n[k] == nb) return 1;
        if (main->edge[i].sub[0].neighbor == nb || main->edge[i].sub[1].neighbor == nb) return 1;
    }
    return 0;
}

static const vec_t *CornerPoint(const coredisp_t *d, int corner) {
    return d->verts[VertIndexToInt(d, d->pi->corner[corner])];
}

static void SetupCornerNeighbors(coredisp_t *main, coredisp_t *other, int *overflows) {
    if (HasEdgeNeighbor(main, other->index)) return;
    int shared = 0, mc = -1, oc = -1;
    for (int i = 0; i < 4; i++)
        for (int j = 0; j < 4; j++)
            if (VectorsAreEqual(CornerPoint(main, i), CornerPoint(other, j), 0.001f)) {
                mc = i;
                oc = j;
                ++shared;
            }
    if (shared == 1) {
        cornerneighbors_t *a = &main->corner[mc], *b = &other->corner[oc];
        if (a->count < 4 && b->count < 4) {
            a->n[a->count++] = (unsigned short)other->index;
            b->n[b->count++] = (unsigned short)main->index;
        } else ++(*overflows);
    }
}

/* -- walking from a vertex on an edge into the neighbour */
static void SetupSpan(int power, int iedge, int span, vi_t *start, vi_t *end) {
    int fd = !edge_dims[iedge];
    const powerinfo_t *pi = GetPowerInfo(power);
    *start = pi->corner[iedge];
    *end = pi->corner[(iedge + 1) & 3];
    int *s = fd ? &start->y : &start->x, *e = fd ? &end->y : &end->x;
    if (iedge == EDGE_RIGHT || iedge == EDGE_BOTTOM) {
        if (span == CORNER_TO_MIDPOINT) *s = pi->mid;
        else if (span == MIDPOINT_TO_CORNER) *e = pi->mid;
    } else {
        if (span == CORNER_TO_MIDPOINT) *e = pi->mid;
        else if (span == MIDPOINT_TO_CORNER) *s = pi->mid;
    }
}

static int Comp(vi_t v, int dim) { return dim ? v.y : v.x; }
static void SetComp(vi_t *v, int dim, int val) { if (dim) v->y = val; else v->x = val; }

static coredisp_t *TransformIntoSubNeighbor(coredisp_t *d, int iedge, int isub, vi_t node, vi_t *out) {
    const subneighbor_t *s = &d->edge[iedge].sub[isub];
    vi_t ss, se, ds, de;
    SetupSpan(d->power, iedge, s->span, &ss, &se);
    coredisp_t *nb = &cores[s->neighbor];
    int nbedge = (iedge + 2 + s->orientation) & 3;
    SetupSpan(nb->power, nbedge, s->nbspan, &de, &ds);
    int fd = !edge_dims[iedge];
    int fixed = ((Comp(node, fd) - Comp(ss, fd)) * (1 << 16)) / (Comp(se, fd) - Comp(ss, fd));
    int nbdim = edge_dims[nbedge];
    SetComp(out, nbdim, Comp(ds, nbdim));
    SetComp(out, !nbdim, Comp(ds, !nbdim) + ((Comp(de, !nbdim) - Comp(ds, !nbdim)) * fixed) / (1 << 16));
    return nb;
}

static int GetSubNeighborIndex(coredisp_t *d, int iedge, vi_t node) {
    const powerinfo_t *pi = d->pi;
    const edgeneighbor_t *side = &d->edge[iedge];
    int fd = !edge_dims[iedge], free_index = Comp(node, fd), isub = 0;
    if (free_index == pi->mid) {
        if (side->sub[0].span != CORNER_TO_CORNER) return -1;
    } else if (free_index > pi->mid) isub = 1;
    if (side->sub[isub].neighbor == 0xFFFF) {
        if (isub == 1 && side->sub[0].neighbor != 0xFFFF && side->sub[0].span == CORNER_TO_CORNER) isub = 0;
        else return -1;
    }
    return isub;
}

static coredisp_t *TransformIntoNeighbor(coredisp_t *d, int iedge, vi_t node, vi_t *out) {
    if (iedge == -1) iedge = GetEdgeIndexFromPoint(node, d->power);
    int isub = GetSubNeighborIndex(d, iedge, node);
    if (isub == -1) return NULL;
    return TransformIntoSubNeighbor(d, iedge, isub, node, out);
}

/* the sub-edge iterator (vertices along one sub-neighbour's span, with the neighbour's matching ones) */
typedef struct {
    coredisp_t *nb;
    vi_t index, inc, nbindex, nbinc;
    int end, freedim;
} subiter_t;

static void RotateVertIncrement(int orient, vi_t in, vi_t *out) {
    if (orient == 0) *out = in;
    else if (orient == 1) { out->x = in.y; out->y = -in.x; }
    else if (orient == 2) { out->x = -in.x; out->y = -in.y; }
    else { out->x = -in.y; out->y = in.x; }
}

static void SubIterStart(subiter_t *it, coredisp_t *d, int iedge, int isub) {
    memset(it, 0, sizeof(*it));
    int ed = edge_dims[iedge], fd = !ed;
    it->freedim = fd;
    subneighbor_t *s = &d->edge[iedge].sub[isub];
    if (s->neighbor == 0xFFFF) return;
    coredisp_t *nb = &cores[s->neighbor];
    static const int sidelen_mul[4] = {0, 1, 1, 0};
    vi_t my = {0, 0}, tmp = {0, 0};
    SetComp(&my, ed, sidelen_mul[iedge] * d->pi->sidem1);
    SetComp(&my, fd, d->pi->mid * isub);
    TransformIntoSubNeighbor(d, iedge, isub, my, &it->nbindex);
    int mypow = d->power, nbpow = nb->power + shiftinfos[s->span][s->nbspan].powershift;
    vi_t myinc = {0, 0};
    if (nbpow > mypow) {
        SetComp(&myinc, fd, 1);
        SetComp(&tmp, fd, 1 << (nbpow - mypow));
    } else {
        SetComp(&myinc, fd, 1 << (mypow - nbpow));
        SetComp(&tmp, fd, 1);
    }
    RotateVertIncrement(s->orientation, tmp, &it->nbinc);
    it->end = s->span == CORNER_TO_MIDPOINT ? d->pi->side >> 1 : d->pi->side - 1;
    it->index = my;
    it->inc = myinc;
    it->nb = nb;
}

static int SubIterNext(subiter_t *it) {
    it->index.x += it->inc.x;
    it->index.y += it->inc.y;
    it->nbindex.x += it->nbinc.x;
    it->nbindex.y += it->nbinc.y;
    return Comp(it->index, it->freedim) < it->end;
}

static int VerifyNeighborVertConnection(coredisp_t *d, vi_t node, coredisp_t *testnb, vi_t testindex, int myside) {
    vi_t nbindex = {-1, -1};
    coredisp_t *nb = TransformIntoNeighbor(d, myside, node, &nbindex);
    if (nb) {
        if (testnb != nb || nbindex.x != testindex.x || nbindex.y != testindex.y) return 0;
        vi_t back = {-1, -1};
        int iside = GetEdgeIndexFromPoint(nbindex, nb->power);
        if (iside == -1) return 0;
        coredisp_t *t = TransformIntoNeighbor(nb, iside, nbindex, &back);
        if (t != d || node.x != back.x || node.y != back.y) return 0;
    }
    return 1;
}

static void VerifyNeighborConnections(int n) {
    for (;;) {
        int happy = 1;
        for (int i = 0; i < n; ++i) {
            coredisp_t *d = &cores[i];
            for (int e = 0; e < 4; e++) {
                for (int sub = 0; sub < 2; sub++) {
                    subiter_t it;
                    SubIterStart(&it, d, e, sub);
                    if (!it.nb) continue;
                    while (SubIterNext(&it)) {
                        if (!VerifyNeighborVertConnection(d, it.index, it.nb, it.nbindex, e)) {
                            d->edge[e].sub[0].neighbor = d->edge[e].sub[1].neighbor = 0xFFFF;
                            Warning("Warning: invalid neighbor connection on displacement near (%.2f %.2f %.2f)\n",
                                    CornerPoint(d, 0)[0], CornerPoint(d, 0)[1], CornerPoint(d, 0)[2]);
                            happy = 0;
                            break;
                        }
                    }
                    if (d->edge[e].sub[0].neighbor == 0xFFFF && d->edge[e].sub[1].neighbor == 0xFFFF) break;
                }
            }
        }
        if (happy) break;
    }
}

static void FindNeighboringDispSurfs(int n) {
    for (int i = 0; i < n; ++i) {
        for (int k = 0; k < 4; k++) {
            cores[i].edge[k].sub[0].neighbor = cores[i].edge[k].sub[1].neighbor = 0xFFFF;
            cores[i].corner[k].count = 0;
        }
    }
    vec3_t (*bmin)[1] = xalloc(sizeof(vec3_t) * n), (*bmax)[1] = xalloc(sizeof(vec3_t) * n);
    for (int i = 0; i < n; ++i) {
        vec3_t lo = {1e24f, 1e24f, 1e24f}, hi = {-1e24f, -1e24f, -1e24f};
        for (int v = 0; v < 4; v++)
            for (int k = 0; k < 3; k++) {
                if (cores[i].points[v][k] < lo[k]) lo[k] = cores[i].points[v][k];
                if (cores[i].points[v][k] > hi[k]) hi[k] = cores[i].points[v][k];
            }
        for (int k = 0; k < 3; k++) {
            bmin[i][0][k] = lo[k] - 0.1f;
            bmax[i][0][k] = hi[k] + 0.1f;
        }
    }
    int overflows = 0;
    for (int i = 0; i < n; ++i) {
        for (int j = i + 1; j < n; ++j) {
            int touch = 1;
            for (int k = 0; k < 3; k++)
                if (bmax[i][0][k] < bmin[j][0][k] || bmin[i][0][k] > bmax[j][0][k]) touch = 0;
            if (!touch) continue;
            SetupEdgeNeighbors(&cores[i], &cores[j]);
            SetupCornerNeighbors(&cores[i], &cores[j], &overflows);
        }
    }
    if (overflows) Warning("Warning: overflowed %d displacement corner-neighbor lists.", overflows);
    free(bmin);
    free(bmax);
    VerifyNeighborConnections(n);
}

/* -- allowed vertices */
static int BitGet(const uint32_t *b, int i) { return (b[i >> 5] >> (i & 31)) & 1; }
static void BitClear(uint32_t *b, int i) { b[i >> 5] &= ~(1u << (i & 31)); }

static int IsCorner(vi_t i, int side) {
    return (i.x == 0 || i.x == side - 1) && (i.y == 0 || i.y == side - 1);
}

static int IsVertAllowed(coredisp_t *d, vi_t sv, int level) {
    if (IsCorner(sv, d->pi->side)) return 1;
    int iside = GetEdgeIndexFromPoint(sv, d->power);
    if (iside == -1) return 1;
    int isub = GetSubNeighborIndex(d, iside, sv);
    if (isub == -1) return 1;
    subneighbor_t *s = &d->edge[iside].sub[isub];
    coredisp_t *nb = &cores[s->neighbor];
    if (nb->power + shiftinfos[s->span][s->nbspan].powershift < level + 1) return 0;
    vi_t nbindex;
    TransformIntoSubNeighbor(d, iside, isub, sv, &nbindex);
    return BitGet(nb->allowed, VertIndexToInt(nb, nbindex));
}

static void UnallowVerts_R(coredisp_t *d, vi_t node, int *n) {
    int inode = VertIndexToInt(d, node);
    if (!BitGet(d->allowed, inode)) return;
    (*n)++;
    BitClear(d->allowed, inode);
    for (int i = 0; i < 4; i++) {
        vdep_t *dep = &d->pi->vinfo[inode].rdeps[i];
        if (dep->vert.x != -1 && dep->neighbor == -1) UnallowVerts_R(d, dep->vert, n);
    }
}

static void DisableUnallowedVerts_R(coredisp_t *d, vi_t node, int level, int *n) {
    int inode = VertIndexToInt(d, node);
    for (int s = 0; s < 4; s++) {
        vi_t sv = d->pi->sideverts[inode][s];
        if (!IsVertAllowed(d, sv, level)) UnallowVerts_R(d, sv, n);
    }
    if (level + 1 < d->power)
        for (int c = 0; c < 4; c++) DisableUnallowedVerts_R(d, d->pi->childverts[inode][c], level + 1, n);
}

static void SetupAllowedVerts(int n) {
    for (int i = 0; i < n; ++i) memset(cores[i].allowed, 0xFF, sizeof(cores[i].allowed));
    int more;
    do {
        more = 0;
        for (int i = 0; i < n; ++i) {
            int un = 0;
            DisableUnallowedVerts_R(&cores[i], cores[i].pi->root, 0, &un);
            if (un) more = 1;
        }
    } while (more);
}

/* -- tesselation with the allowed vertices, and moving the dropped ones onto it */
typedef struct {
    const uint32_t *active;
    const powerinfo_t *pi;
    unsigned short *indices;
    int n, cap;
    unsigned short temp[6];
} tess_t;

static void tess_end(tess_t *t, vi_t node, int *cur) {
    t->temp[2] = (unsigned short)(node.y * t->pi->side + node.x);
    if (t->n + 3 > t->cap) {
        t->cap = t->cap ? t->cap * 2 : 1024;
        t->indices = realloc(t->indices, sizeof(unsigned short) * t->cap);
    }
    t->indices[t->n++] = t->temp[0];
    t->indices[t->n++] = t->temp[1];
    t->indices[t->n++] = t->temp[2];
    t->temp[0] = t->temp[1];
    *cur = 1;
}

static void TesselateNode(tess_t *t, vi_t node, int level, const int *active_children) {
    int inc = 1 << ((t->pi->power - level) - 1), cur = 0;
    for (int v = 0; v < 9; v++) {
        vi_t sv = {node.x + twinding[v].index.x * inc, node.y + twinding[v].index.y * inc};
        int vn = twinding[v].node;
        if (vn != -1 && active_children[vn]) {
            if (cur == 2) tess_end(t, node, &cur);
            cur = 0;
        } else {
            int bit = sv.y * t->pi->side + sv.x;
            if (t->active[bit >> 5] & (1u << (bit & 31))) {
                t->temp[cur] = (unsigned short)bit;
                cur++;
                if (cur == 2) tess_end(t, node, &cur);
            }
        }
    }
}

static void Tesselate_R(tess_t *t, vi_t node, int level) {
    int active[4] = {0, 0, 0, 0};
    if (level < t->pi->power - 1) {
        int inode = node.y * t->pi->side + node.x;
        for (int c = 0; c < 4; c++) {
            vi_t cn = t->pi->childverts[inode][c];
            int bit = cn.y * t->pi->side + cn.x;
            active[c] = (t->active[bit >> 5] & (1u << (bit & 31))) != 0;
            if (active[c]) Tesselate_R(t, cn, level + 1);
        }
    }
    TesselateNode(t, node, level, active);
}

static void Barycentric2D(const float *a, const float *b, const float *c, const float *pt, float bc[3]) {
#define AREA2(A, B, C) (((B)[0] - (A)[0]) * ((C)[1] - (A)[1]) - ((B)[1] - (A)[1]) * ((C)[0] - (A)[0]))
    float inv = 1.0f / AREA2(a, b, c);
    bc[0] = AREA2(b, c, pt) * inv;
    bc[1] = AREA2(c, a, pt) * inv;
    bc[2] = AREA2(a, b, pt) * inv;
#undef AREA2
}

/* ------------------------------------------------------------------ the BSP records */
ddispinfo_t *g_dispinfo;
dispvert_t *g_dispverts;
int g_numdispverts;
unsigned short *g_disptris;
int g_numdisptris;
unsigned char *g_lmsamples;
int g_numlmsamples, g_maxlmsamples;

static void SnapRemainingVertsToSurface(coredisp_t *d, ddispinfo_t *di) {
    tess_t t;
    memset(&t, 0, sizeof(t));
    t.active = d->allowed;
    t.pi = d->pi;
    Tesselate_R(&t, d->pi->root, 0);
    int size = d->pi->maxverts, w = d->pi->side;
    char *touched = xalloc(size);
    for (int i = 0; i < t.n; i++) touched[t.indices[i]] = 1;
    for (int y = 0; y < w; y++) {
        for (int x = 0; x < w; x++) {
            int index = y * w + x;
            if (touched[index]) continue;
            float vert[2] = {(float)x, (float)y}, bc[3];
            int found = -1;
            for (int i = 0; i < t.n; i += 3) {
                float a[2] = {(float)(t.indices[i] % w), (float)(t.indices[i] / w)};
                float b[2] = {(float)(t.indices[i + 1] % w), (float)(t.indices[i + 1] / w)};
                float c[2] = {(float)(t.indices[i + 2] % w), (float)(t.indices[i + 2] / w)};
                Barycentric2D(a, b, c, vert, bc);
                if (bc[0] >= 0 && bc[0] <= 1 && bc[1] >= 0 && bc[1] <= 1 && bc[2] >= 0 && bc[2] <= 1) {
                    found = i;
                    break;
                }
            }
            if (found < 0) continue;
            vec_t *A = d->verts[t.indices[found]], *B = d->verts[t.indices[found + 1]], *C = d->verts[t.indices[found + 2]];
            vec3_t np, off;
            for (int k = 0; k < 3; k++) np[k] = A[k] * bc[0] + B[k] * bc[1] + C[k] * bc[2];
            VectorSubtract(np, d->verts[index], off);
            dispvert_t *dv = &g_dispverts[di->vert_start + index];
            for (int k = 0; k < 3; k++) dv->vec[k] = (dv->vec[k] * dv->dist) + off[k];
            dv->dist = 1;
            VectorCopy(np, d->verts[index]);
        }
    }
    free(touched);
    free(t.indices);
}

static void lm_push(unsigned char v) {
    if (g_numlmsamples == g_maxlmsamples) {
        g_maxlmsamples = g_maxlmsamples ? g_maxlmsamples * 2 : 65536;
        g_lmsamples = realloc(g_lmsamples, g_maxlmsamples);
    }
    g_lmsamples[g_numlmsamples++] = v;
}

/* For each lightmap texel: the triangle it falls in and where (barycentric, 0-255). */
static void CalculateLightmapSamplePositions(coredisp_t *d, const dface_t *face) {
    int width = face->lm_size[0] + 1, height = face->lm_size[1] + 1;
    const powerinfo_t *pi = d->pi;
    for (int y = 0; y < height; y++) {
        float lm[2];
        lm[1] = y + 0.5f;
        for (int x = 0; x < width; x++) {
            lm[0] = x + 0.5f;
            int tri = -1;
            float bc[3];
            for (int i = 0; i < pi->ntris; ++i) {
                const unsigned short *v = pi->tris[i];
                Barycentric2D(d->vluxel[v[0]], d->vluxel[v[1]], d->vluxel[v[2]], lm, bc);
                if (bc[0] >= 0.0f && bc[0] <= 1.0f && bc[1] >= 0.0f && bc[1] <= 1.0f && bc[2] >= 0.0f && bc[2] <= 1.0f) {
                    tri = i;
                    break;
                }
            }
            if (tri >= 0) {
                if (tri < 255) lm_push((unsigned char)tri);
                else {
                    lm_push(255);
                    lm_push((unsigned char)(tri - 255));
                }
                lm_push((unsigned char)(bc[0] * 255.9f));
                lm_push((unsigned char)(bc[1] * 255.9f));
                lm_push((unsigned char)(bc[2] * 255.9f));
            } else {
                lm_push(0); lm_push(0); lm_push(0); lm_push(0);
            }
        }
    }
}

/* At the start of the BSP: the records straight from the .vmf. */
void EmitInitialDispInfos(void) {
    int nverts = 0, ntris = 0;
    for (int i = 0; i < nummapdisps; i++) {
        int s = (1 << mapdisps[i].power) + 1;
        nverts += s * s;
        ntris += (1 << mapdisps[i].power) * (1 << mapdisps[i].power) * 2;
    }
    g_dispinfo = xalloc(sizeof(ddispinfo_t) * (nummapdisps + 1));
    g_dispverts = xalloc(sizeof(dispvert_t) * (nverts + 1));
    g_disptris = xalloc(sizeof(unsigned short) * (ntris + 1));
    int curvert = 0, curtri = 0;
    for (int i = 0; i < nummapdisps; i++) {
        ddispinfo_t *di = &g_dispinfo[i];
        mapdisp_t *md = &mapdisps[i];
        dispvert_t *ov = &g_dispverts[curvert];
        unsigned short *ot = &g_disptris[curtri];
        di->vert_start = curvert;
        di->tri_start = curtri;
        int s = (1 << md->power) + 1, nt = (1 << md->power) * (1 << md->power) * 2;
        curvert += s * s;
        curtri += nt;
        di->power = md->power;
        di->mintess = md->flags | (int)0x80000000;
        di->smoothing = md->smooth;
        di->mapface = (unsigned short)-2;
        di->contents = md->face.contents;
        VectorCopy(md->startpos, di->startpos);
        for (int j = 0; j < s * s; j++) {
            vec3_t v;
            VectorScale(md->normals[j], md->dists[j], v);
            VectorAdd(v, md->offsets[j], v);
            float dist = sqrtf((v[1] * v[1] + v[2] * v[2]) + v[0] * v[0]);     /* (vbsp's order) */
            VectorNormalizeX87(v);
            VectorCopy(v, ov[j].vec);
            ov[j].dist = dist;
            ov[j].alpha = md->alphas[j];
        }
        for (int t = 0; t < nt; ++t) ot[t] = md->tritags[t];
        md->face.dispinfo = i;
    }
    g_numdispverts = nverts;
    g_numdisptris = ntris;
}

/* After the faces: lightmap sizes, neighbours, allowed vertices, sample positions. */
void EmitDispLMAlphaAndNeighbors(void) {
    if (!nummapdisps) return;
    Msg("Finding displacement neighbors...\n");
    cores = xalloc(sizeof(coredisp_t) * nummapdisps);
    for (int i = 0; i < nummapdisps; i++) cores[i].index = i;
    dface_t **faces = xalloc(sizeof(dface_t *) * nummapdisps);
    int *swapped = xalloc(sizeof(int) * (numtexinfo + 1));
    for (int i = 0; i <= numtexinfo; i++) swapped[i] = -1;
    int base_texinfos = numtexinfo;
    for (int i = 0; i < numfaces; i++) {
        dface_t *f = &dfaces[i];
        if (f->dispinfo == -1) continue;
        mapdisp_t *md = &mapdisps[f->dispinfo];
        g_dispinfo[f->dispinfo].mapface = (unsigned short)i;
        (void)base_texinfos;
        MapToCore(md, &cores[f->dispinfo], f, swapped);
        faces[f->dispinfo] = f;
    }
    free(swapped);
    FindNeighboringDispSurfs(nummapdisps);
    for (int i = 0; i < nummapdisps; i++) {
        for (int e = 0; e < 4; e++) {
            for (int s = 0; s < 2; s++) {
                g_dispinfo[i].edge[e].sub[s].neighbor = cores[i].edge[e].sub[s].neighbor;
                g_dispinfo[i].edge[e].sub[s].orientation = cores[i].edge[e].sub[s].orientation;
                g_dispinfo[i].edge[e].sub[s].span = cores[i].edge[e].sub[s].span;
                g_dispinfo[i].edge[e].sub[s].nbspan = cores[i].edge[e].sub[s].nbspan;
            }
            memcpy(g_dispinfo[i].corner[e].n, cores[i].corner[e].n, sizeof(cores[i].corner[e].n));
            g_dispinfo[i].corner[e].count = cores[i].corner[e].count;
        }
    }
    SetupAllowedVerts(nummapdisps);
    for (int i = 0; i < nummapdisps; i++) memcpy(g_dispinfo[i].allowed, cores[i].allowed, sizeof(cores[i].allowed));
    for (int i = 0; i < nummapdisps; i++) SnapRemainingVertsToSurface(&cores[i], &g_dispinfo[i]);
    Msg("Finding lightmap sample positions...\n");
    for (int i = 0; i < nummapdisps; i++) {
        dface_t *f = faces[i];
        g_dispinfo[f->dispinfo].lmsample_start = g_numlmsamples;
        CalculateLightmapSamplePositions(&cores[i], f);
    }
    for (int i = 0; i < nummapdisps; i++) g_dispinfo[i].lmalpha_start = 0;
    free(faces);
}

/* The box of a displacement's base quad (0.1 larger), as vbsp's world bounds use it. */
void DispBounds(int i, vec3_t mins, vec3_t maxs) {
    mapdisp_t *md = &mapdisps[i];
    winding_t *w = md->face.originalface->winding;
    vec3_t lo = {1e24f, 1e24f, 1e24f}, hi = {-1e24f, -1e24f, -1e24f};
    for (int v = 0; v < 4; v++)
        for (int k = 0; k < 3; k++) {
            if (w->p[v][k] < lo[k]) lo[k] = w->p[v][k];
            if (w->p[v][k] > hi[k]) hi[k] = w->p[v][k];
        }
    for (int k = 0; k < 3; k++) {
        mins[k] = lo[k] - 0.1f;
        maxs[k] = hi[k] + 0.1f;
    }
}

/* ------------------------------------------------------------------ reading from the .vmf */
mapdisp_t *NewMapDisp(void) {
    if (nummapdisps == max_mapdisps) {
        max_mapdisps = max_mapdisps ? max_mapdisps * 2 : 256;
        mapdisps = realloc(mapdisps, sizeof(mapdisp_t) * max_mapdisps);
    }
    mapdisp_t *md = &mapdisps[nummapdisps++];
    memset(md, 0, sizeof(*md));
    return md;
}

/* "rowN" "a b c ..." into values[N * cols ...], `per` numbers per element. */
void ParseDispRow(const char *key, const char *value, float *out, int cols, int per) {
    int row = atoi(key + 3);
    char buf[16384];
    strncpy(buf, value, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    int index = row * cols * per;
    for (char *tok = strtok(buf, " "); tok; tok = strtok(NULL, " ")) {
        if (index >= MAX_DISPVERTS * 3) break;
        out[index++] = (float)atof(tok);
    }
}

void ParseDispTriTags(const char *key, const char *value, mapdisp_t *md) {
    int cols = 1 << md->power, row = atoi(key + 3);
    int tri = row * cols * 2;
    char buf[16384];
    strncpy(buf, value, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    for (char *tok = strtok(buf, " "); tok; tok = strtok(NULL, " ")) {
        unsigned short tags = (unsigned short)atoi(tok);
        int walkable = (tags & (1 << 0)) != 0;
        if (tags & (1 << 1)) walkable = (tags & (1 << 2)) != 0;
        int buildable = (tags & (1 << 3)) != 0;
        if (tags & (1 << 4)) buildable = (tags & (1 << 5)) != 0;
        tags = 0;
        if (walkable) tags |= 1 << 1;
        if (buildable) tags |= 1 << 2;
        if (tri < MAX_DISPTRIS) md->tritags[tri] = tags;
        tri++;
    }
}

/* ------------------------------------------------------------------ for the physics */
int DispPower(int disp) { return mapdisps[disp].power; }

const float *DispVert(int disp, int index) { return cores[disp].verts[index]; }

/* The displacement's triangles with its allowed vertices (the caller frees the indices). */
int DispTesselate(int disp, unsigned short **indices) {
    tess_t t;
    memset(&t, 0, sizeof(t));
    t.active = cores[disp].allowed;
    t.pi = cores[disp].pi;
    Tesselate_R(&t, cores[disp].pi->root, 0);
    *indices = t.indices;
    return t.n;
}


/* ------------------------------------------------------------------ a point on the surface (detail props) */
static int CalcBarycentricCooefs(const vec3_t v0, const vec3_t v1, const vec3_t v2, const vec3_t pt, float *c) {
    vec3_t s0, s1, x;
    VectorSubtract(v1, v0, s0);
    VectorSubtract(v2, v0, s1);
    CrossProduct(s0, s1, x);
    float total = VectorLength(x) * 0.5f;
    float oo = total ? 1.0f / total : 0.0f;
    VectorSubtract(v1, pt, s0); VectorSubtract(v2, pt, s1); CrossProduct(s0, s1, x);
    c[0] = VectorLength(x) * 0.5f * oo;
    VectorSubtract(v2, pt, s0); VectorSubtract(v0, pt, s1); CrossProduct(s0, s1, x);
    c[1] = VectorLength(x) * 0.5f * oo;
    VectorSubtract(v0, pt, s0); VectorSubtract(v1, pt, s1); CrossProduct(s0, s1, x);
    c[2] = VectorLength(x) * 0.5f * oo;
    float t = c[0] + c[1] + c[2];
    return fabsf(1.0f - t) < 1e-3;
}

static void Lerp3(const vec3_t a, const vec3_t b, float t, vec3_t out) {
    for (int k = 0; k < 3; k++) out[k] = a[k] + (b[k] - a[k]) * t;
}

/* One of the triangles of a quad of the grid: idx = its three vertices; nflip picks the normal's order. */
typedef struct { const coredisp_t *d; const float *alphas; } surfctx_t;

static void tri_normal(const vec3_t a, const vec3_t b, const vec3_t o, int swap, vec3_t n) {
    vec3_t eu, ev;
    VectorSubtract(a, o, eu);
    VectorSubtract(b, o, ev);
    if (swap) CrossProduct(ev, eu, n);
    else CrossProduct(eu, ev, n);
    /* mathlib's VectorNormalize: the length in x87 (double), rounded to float, times 1/(length + FLT_EPSILON) */
    float len = (float)sqrt((double)n[0] * n[0] + (double)n[1] * n[1] + (double)n[2] * n[2]);
    float oo = 1.0f / (len + FLT_EPSILON);
    n[0] *= oo; n[1] *= oo; n[2] *= oo;
}

/* kind: 0 TLtoBR_1, 1 TLtoBR_2, 2 BLtoTR_1, 3 BLtoTR_2 (vbsp's four triangle cases) */
static void UVToSurfTri(surfctx_t *c, int kind, const vec3_t hit, int su, int nu, int sv, int nv, vec3_t pt, vec3_t normal,
                        float *alpha, int backup) {
    int w = c->d->pi->side, idx[3];
    switch (kind) {
    case 0: idx[0] = nv * w + su; idx[1] = nv * w + nu; idx[2] = sv * w + nu; break;
    case 1: idx[0] = sv * w + su; idx[1] = nv * w + su; idx[2] = sv * w + nu; break;
    case 2: idx[0] = sv * w + su; idx[1] = nv * w + su; idx[2] = nv * w + nu; break;
    default: idx[0] = sv * w + su; idx[1] = nv * w + nu; idx[2] = sv * w + nu; break;
    }
    const float *f[3], *v[3];
    float a[3];
    for (int i = 0; i < 3; i++) {
        f[i] = c->d->flat[idx[i]];
        v[i] = c->d->verts[idx[i]];
        a[i] = c->alphas[idx[i]];
    }
    /* the normal of each case (vbsp's edge choices) */
    vec3_t n;
    switch (kind) {
    case 0: tri_normal(v[0], v[2], v[1], 0, n); break;          /* (v0-v1) x (v2-v1) */
    case 1: tri_normal(v[2], v[1], v[0], 0, n); break;          /* (v2-v0) x (v1-v0) */
    case 2: tri_normal(v[2], v[0], v[1], 1, n); break;          /* (v0-v1) x (v2-v1) */
    default: tri_normal(v[0], v[1], v[2], 1, n); break;         /* (v1-v2) x (v0-v2) */
    }
    /* the edge vertex used along a snapped row or column */
    int far = (kind == 1 || kind == 3) && su == nu ? 1 : 2;
    if (su == nu || sv == nv) {
        if (su == nu && sv == nv) {
            VectorCopy(v[0], pt);
            *alpha = a[0];
        } else {
            if (su != nu) far = 2;           /* (the snapped-v cases all use the third vertex) */
            vec3_t d1, d2;
            VectorSubtract(hit, f[0], d1);
            VectorSubtract(f[far], f[0], d2);
            float frac = VectorLength(d1) / VectorLength(d2);
            vec3_t e;
            VectorSubtract(v[far], v[0], e);
            for (int k = 0; k < 3; k++) pt[k] = v[0][k] + frac * e[k];
            *alpha = a[0] + frac * (a[far] - a[0]);
        }
        if (kind == 2 && su == nu) tri_normal(v[2], v[0], v[1], 0, n);     /* (BLtoTR_1: u snapped: eu x ev) */
        VectorCopy(n, normal);
        return;
    }
    float cf[3];
    if (CalcBarycentricCooefs(f[0], f[1], f[2], hit, cf)) {
        for (int k = 0; k < 3; k++) pt[k] = (v[0][k] * cf[0]) + (v[1][k] * cf[1]) + (v[2][k] * cf[2]);
        *alpha = (a[0] * cf[0]) + (a[1] * cf[1]) + (a[2] * cf[2]);
        VectorCopy(n, normal);
    } else if (!backup) {
        int other = kind == 0 ? 1 : kind == 1 ? 0 : kind == 2 ? 3 : 2;
        UVToSurfTri(c, other, hit, su, nu, sv, nv, pt, normal, alpha, 1);
    }
}

/* Where (u, v) in [0,1] lands on displacement i's surface, its triangle's normal and the blend alpha. */
void DispPositionOnSurface(int i, float u, float v, vec3_t pt, vec3_t normal, float *alpha) {
    static coredisp_t d;
    static int built = -1;
    if (built != i) {
        free(d.verts); free(d.vluxel); free(d.flat);
        memset(&d, 0, sizeof(d));
        int n = GetPowerInfo(mapdisps[i].power)->maxverts;
        d.flat = xalloc(sizeof(vec3_t) * n);
        MapToCore(&mapdisps[i], &d, NULL, NULL);
        built = i;
    }
    if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) return;
    surfctx_t c = {&d, mapdisps[i].alphas};
    /* the point on the flat quad */
    vec3_t p0, p1, hit;
    Lerp3(d.points[0], d.points[1], v, p0);
    Lerp3(d.points[3], d.points[2], v, p1);
    Lerp3(p0, p1, u, hit);
    int w = d.pi->side;
    float fu = u * ((float)w - 1.000001f), fv = v * ((float)w - 1.000001f);
    int su = (int)fu, sv = (int)fv;
    int odd = ((sv * w) + su) % 2 == 1;
    int nu = su + 1, nv = sv + 1;
    if (nu == w) --nu;
    if (nv == w) --nv;
    float fracu = fu - (float)su, fracv = fv - (float)sv;
    if (odd) UVToSurfTri(&c, (fracu + fracv) >= (1.0f + 0.00001f) ? 0 : 1, hit, su, nu, sv, nv, pt, normal, alpha, 0);
    else UVToSurfTri(&c, fracu < fracv ? 2 : 3, hit, su, nu, sv, nv, pt, normal, alpha, 0);
}
