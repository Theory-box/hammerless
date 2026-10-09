/* Leaf ambient lighting (vrad's ComputePerLeafAmbientLighting): what lights models moving through the map.
 *
 * Each non-solid leaf gets up to 16 sample points (more for big leaves, placed with Valve's random stream;
 * a point must be clear of the leaf's planes and see out to its box). At each, 162 rays look for the
 * nearest lit surface (walking the BSP tree, testing faces by their lightmap area, displacements by their
 * triangles) and take its lightmap colour (times its reflectivity; the sky ambient for sky); the colours
 * are folded into a cube of 6 directions, plus small light-emitting surfaces seen directly. Samples that
 * the others predict well are dropped. A leaf without samples points at its nearest neighbour that has. */
#include <float.h>
#include <windows.h>
#include "hlvrad.h"

#define MAX_SAMPLES 16
#define DIST_EPSILON 0.03125f
#define TEST_EPSILON 0.03125f
#define DWL_FLAGS_INAMBIENTCUBE 0x1
#define VERTEXNORMAL_CONE_INNER_ANGLE (7.275 * 3.14159265358979323846 / 180.0)
#define COORD_EXTENT (2 * 16384)

typedef struct { vec3_t reflectivity; int name, width, height, view_width, view_height; } dtexdata_t;
static const dtexdata_t *dtexdata;

/* ------------------------------------------------------------------ Valve's uniform random stream (vstdlib) */
#define NTAB 32
typedef struct { int idum, iy, iv[NTAB]; } rng_t;

static void RngSeed(rng_t *r, int seed) {
    r->idum = seed < 0 ? seed : -seed;
    r->iy = 0;
}

static int RngNext(rng_t *r) {
    const int IA = 16807, IM = 2147483647, IQ = 127773, IR = 2836, NDIV = 1 + (IM - 1) / NTAB;
    int j, k;
    if (r->idum <= 0 || !r->iy) {
        if (-(r->idum) < 1) r->idum = 1;
        else r->idum = -(r->idum);
        for (j = NTAB + 7; j >= 0; j--) {
            k = r->idum / IQ;
            r->idum = IA * (r->idum - k * IQ) - IR * k;
            if (r->idum < 0) r->idum += IM;
            if (j < NTAB) r->iv[j] = r->idum;
        }
        r->iy = r->iv[0];
    }
    k = r->idum / IQ;
    r->idum = IA * (r->idum - k * IQ) - IR * k;
    if (r->idum < 0) r->idum += IM;
    j = r->iy / NDIV;
    if (j >= NTAB || j < 0) j &= NTAB - 1;
    r->iy = r->iv[j];
    r->iv[j] = r->idum;
    return r->iy;
}

static float RngFloat(rng_t *r, float lo, float hi) {
    float fl = (float)((1.0 / 2147483647) * RngNext(r));
    if (fl > 1.0 - 1.2e-7) fl = (float)(1.0 - 1.2e-7);
    return fl * (hi - lo) + lo;
}

/* ------------------------------------------------------------------ the tree */
static int *leafparent, *nodeparent;

static void MakeParents_r(int node, int parent) {
    nodeparent[node] = parent;
    for (int c = 0; c < 2; c++) {
        int ch = dnodes[node].children[c];
        if (ch < 0) leafparent[-1 - ch] = node;
        else MakeParents_r(ch, node);
    }
}

/* vrad's MakeParents: only the world's tree, so a brush entity's leaves keep parent 0 (their "boundary" is then the
 * back of the root's plane, which usually fails every sample position and leaves the one at the leaf's centre) */
static void BuildParents(void) {
    leafparent = xalloc(sizeof(int) * (numleafs + 1));
    nodeparent = xalloc(sizeof(int) * (numnodes + 1));
    memset(leafparent, 0, sizeof(int) * (numleafs + 1));
    memset(nodeparent, 0, sizeof(int) * (numnodes + 1));
    if (numnodes) MakeParents_r(0, -1);
}

typedef struct { vec3_t normal; float dist; } plane_t;

/* the planes that bound a leaf, facing in */
static int LeafBoundaryPlanes(int leaf, plane_t *list) {
    int n = 0, node = leafparent[leaf], child = -(leaf + 1);
    while (node >= 0) {
        const dplane_t *p = &dplanes[dnodes[node].planenum];
        if (dnodes[node].children[0] == child) VectorCopy(p->normal, list[n].normal), list[n].dist = p->dist;
        else {
            for (int k = 0; k < 3; k++) list[n].normal[k] = -p->normal[k];
            list[n].dist = -p->dist;
        }
        n++;
        child = node;
        node = nodeparent[child];
    }
    return n;
}

/* ------------------------------------------------------------------ displacements: rays against their triangles */
typedef struct {
    int face;
    vec3_t mins, maxs;
    float (*luxel)[2];                 /* per vertex lightmap coordinate */
    unsigned short (*tris)[3];
    int ntris;
    /* (speed only: boxes around the whole and around each band of BAND triangles, a little bigger than
     * they are, so a ray that misses a box can't hit its triangles; the triangles still go in order) */
    vec3_t bmins, bmaxs;
    struct dispnode *nodes;             /* (a little tree of boxes over the triangles; node 0 the root) */
    unsigned short *order;              /* (the leaves' triangles) */
} dispcoll_t;
typedef struct dispnode { float box[6]; int left, right, first, count; } dispnode_t;     /* (leaf: left -1) */
static dispcoll_t *dcoll;
static int **leafdisps, *nleafdisps;
/* per thread: the ray number, and the ray each displacement was last tested against */
static __thread int rayenum;
static __thread int *tested;

/* CCoreDispSurface::CalcLuxelCoords: the corners' lightmap coordinates from the quad's longest sides */
static void DispLuxelCoords(const dispsurf_t *d, dispcoll_t *c) {
    const texinfo_t *tx = &texinfo[g_pFaces[d->face].texinfo];
    const float *lu = tx->lightmapVecsLuxelsPerWorldUnits[0];
    int luxels = (int)(1.0f / sqrtf(lu[0] * lu[0] + lu[1] * lu[1] + lu[2] * lu[2]));
    float lc[4][2] = {{0.5f, 0.5f}, {0.5f, 0.5f}, {0.5f, 0.5f}, {0.5f, 0.5f}};
    if (luxels > 0) {
        vec3_t t;
        VectorSubtract(d->points[3], d->points[0], t);
        float ul = sqrtf(DotProduct(t, t));
        VectorSubtract(d->points[2], d->points[1], t);
        float l2 = sqrtf(DotProduct(t, t));
        if (l2 > ul) ul = l2;
        VectorSubtract(d->points[1], d->points[0], t);
        float vl = sqrtf(DotProduct(t, t));
        VectorSubtract(d->points[2], d->points[3], t);
        l2 = sqrtf(DotProduct(t, t));
        if (l2 > vl) vl = l2;
        float oo = 1.0f / (float)luxels;
        float uval = (float)((int)(ul * oo) + 1), vval = (float)((int)(vl * oo) + 1);
        if (uval > 125) uval = 125;
        if (vval > 125) vval = 125;
        lc[1][1] = (float)(vval + 0.5);
        lc[2][0] = (float)(uval + 0.5), lc[2][1] = (float)(vval + 0.5);
        lc[3][0] = (float)(uval + 0.5);
    }
    int ps = (1 << d->power) + 1;
    float ooint = 1.0f / (float)(ps - 1), e0[2], e1[2];
    for (int k = 0; k < 2; k++) e0[k] = (lc[1][k] - lc[0][k]) * ooint, e1[k] = (lc[2][k] - lc[3][k]) * ooint;
    c->luxel = xalloc(sizeof(float[2]) * ps * ps);
    for (int i = 0; i < ps; i++) {
        float end0[2], end1[2], segint[2];
        for (int k = 0; k < 2; k++) {
            end0[k] = e0[k] * (float)i + lc[0][k];
            end1[k] = e1[k] * (float)i + lc[3][k];
            segint[k] = (end1[k] - end0[k]) * ooint;
        }
        for (int j = 0; j < ps; j++)
            for (int k = 0; k < 2; k++) c->luxel[i * ps + j][k] = end0[k] + segint[k] * (float)j;
    }
}

static void LeavesInBox_r(int node, const vec3_t mins, const vec3_t maxs, int disp) {
    while (node >= 0) {
        const dplane_t *p = &dplanes[dnodes[node].planenum];
        vec3_t cmin, cmax;
        for (int k = 0; k < 3; k++) {
            if (p->normal[k] >= 0) cmin[k] = mins[k], cmax[k] = maxs[k];
            else cmin[k] = maxs[k], cmax[k] = mins[k];
        }
        if (DotProduct(p->normal, cmax) - p->dist <= -TEST_EPSILON) node = dnodes[node].children[1];
        else if (DotProduct(p->normal, cmin) - p->dist >= TEST_EPSILON) node = dnodes[node].children[0];
        else {
            LeavesInBox_r(dnodes[node].children[0], mins, maxs, disp);
            node = dnodes[node].children[1];
        }
    }
    int leaf = -1 - node;
    leafdisps[leaf] = realloc(leafdisps[leaf], sizeof(int) * (nleafdisps[leaf] + 1));
    leafdisps[leaf][nleafdisps[leaf]++] = disp;
}

/* (speed only) the triangles split in halves along their centres' longest spread, down to 4; each node's box a
 * unit bigger than its triangles, so a ray that misses it can't hit them */
static const dispsurf_t *bt_d;
static const dispcoll_t *bt_c;
static int bt_axis;
static float TriCentre(int t, int axis) {
    const unsigned short *tr = bt_c->tris[t];
    return (bt_d->verts[tr[0]][axis] + bt_d->verts[tr[1]][axis]) + bt_d->verts[tr[2]][axis];
}
static int CompareTri(const void *a, const void *b) {
    float x = TriCentre(*(const unsigned short *)a, bt_axis), y = TriCentre(*(const unsigned short *)b, bt_axis);
    return x < y ? -1 : x > y ? 1 : (int)*(const unsigned short *)a - (int)*(const unsigned short *)b;
}
static int BuildDispNode(dispcoll_t *c, int *nn, int first, int count) {
    int n = (*nn)++;
    dispnode_t *node = &c->nodes[n];
    float lo[3] = {FLT_MAX, FLT_MAX, FLT_MAX}, hi[3] = {-FLT_MAX, -FLT_MAX, -FLT_MAX};
    float clo[3] = {FLT_MAX, FLT_MAX, FLT_MAX}, chi[3] = {-FLT_MAX, -FLT_MAX, -FLT_MAX};
    for (int i = first; i < first + count; i++) {
        const unsigned short *tr = c->tris[c->order[i]];
        for (int j = 0; j < 3; j++)
            for (int k = 0; k < 3; k++) {
                float v = bt_d->verts[tr[j]][k];
                if (v < lo[k]) lo[k] = v;
                if (v > hi[k]) hi[k] = v;
            }
        for (int k = 0; k < 3; k++) {
            float m = TriCentre(c->order[i], k);
            if (m < clo[k]) clo[k] = m;
            if (m > chi[k]) chi[k] = m;
        }
    }
    for (int k = 0; k < 3; k++) node->box[k] = lo[k] - 1.0f, node->box[3 + k] = hi[k] + 1.0f;
    node->first = first, node->count = count, node->left = node->right = -1;
    if (count <= 4) return n;
    int axis = 0;
    for (int k = 1; k < 3; k++)
        if (chi[k] - clo[k] > chi[axis] - clo[axis]) axis = k;
    bt_axis = axis;
    qsort(c->order + first, count, sizeof(unsigned short), CompareTri);
    int half = count / 2;
    int l = BuildDispNode(c, nn, first, half);
    int r = BuildDispNode(c, nn, first + half, count - half);
    c->nodes[n].left = l, c->nodes[n].right = r;
    return n;
}
static void BuildDispTree(const dispsurf_t *d, dispcoll_t *c) {
    bt_d = d, bt_c = c;
    c->order = xalloc(sizeof(unsigned short) * (c->ntris + 1));
    for (int t = 0; t < c->ntris; t++) c->order[t] = (unsigned short)t;
    c->nodes = xalloc(sizeof(dispnode_t) * (2 * c->ntris + 2));
    int nn = 0;
    if (c->ntris) BuildDispNode(c, &nn, 0, c->ntris);
}

static void BuildDispCollision(void) {
    dcoll = xalloc(sizeof(dispcoll_t) * (numdispsurfs + 1));
    leafdisps = xalloc(sizeof(int *) * (numleafs + 1));
    nleafdisps = xalloc(sizeof(int) * (numleafs + 1));
    for (int i = 0; i < numdispsurfs; i++) {
        const dispsurf_t *d = &dispsurfs[i];
        dispcoll_t *c = &dcoll[i];
        c->face = d->face;
        if (d->face < 0) continue;
        c->tris = xalloc(sizeof(unsigned short[3]) * 512);
        c->ntris = DispTriangles(d, c->tris);
        DispLuxelCoords(d, c);
        VectorCopy(d->verts[0], c->mins);
        VectorCopy(d->verts[0], c->maxs);
        for (int v = 1; v < d->numverts; v++)
            for (int k = 0; k < 3; k++) {
                if (d->verts[v][k] < c->mins[k]) c->mins[k] = d->verts[v][k];
                if (d->verts[v][k] > c->maxs[k]) c->maxs[k] = d->verts[v][k];
            }
        for (int k = 0; k < 3; k++) c->mins[k] -= 1.0f, c->maxs[k] += 1.0f;     /* (bloated a little, as vrad) */
        for (int k = 0; k < 3; k++) c->bmins[k] = c->mins[k] - 1.0f, c->bmaxs[k] = c->maxs[k] + 1.0f;
        BuildDispTree(d, c);
        LeavesInBox_r(0, c->mins, c->maxs, i);
    }
}

static void Cross(const vec3_t a, const vec3_t b, vec3_t c) {
    c[0] = a[1] * b[2] - a[2] * b[1];
    c[1] = a[2] * b[0] - a[0] * b[2];
    c[2] = a[0] * b[1] - a[1] * b[0];
}

/* ComputeIntersectionBarycentricCoordinates (a ray, not a box: t may be 0.001 outside 0..1) */
static int RayTriangle(const vec3_t start, const vec3_t delta, const vec3_t v1, const vec3_t v2, const vec3_t v3, float *u,
                       float *v, float *t) {
    vec3_t e1, e2, org, dxe2, oxe1;
    VectorSubtract(v2, v1, e1);
    VectorSubtract(v3, v1, e2);
    Cross(delta, e2, dxe2);
    float denom = DotProduct(dxe2, e1);
    if (fabsf(denom) < 1e-6) return 0;
    denom = 1.0f / denom;
    VectorSubtract(start, v1, org);
    *u = DotProduct(dxe2, org) * denom;
    Cross(org, e1, oxe1);
    *v = DotProduct(oxe1, delta) * denom;
    *t = DotProduct(oxe1, e2) * denom;
    if (*t < -1e-3f || *t > 1.0f + 1e-3f) return 0;
    return 1;
}

/* can the ray (start + t delta, t from a little before 0 to a little past 1) touch the box? (the boxes are a unit
 * bigger than their triangles: far more than this test's rounding, so it never wrongly says no) */
typedef struct { float s[3], inv[3]; int zero[3]; } boxray_t;
static void BoxRay(const vec3_t start, const vec3_t delta, boxray_t *r) {
    for (int k = 0; k < 3; k++) r->s[k] = start[k], r->zero[k] = delta[k] == 0.0f, r->inv[k] = r->zero[k] ? 0.0f : 1.0f / delta[k];
}
static int RayMayHitBox(const boxray_t *r, const float *mins, const float *maxs) {
    float t0 = -0.01f, t1 = 1.01f;
    for (int k = 0; k < 3; k++) {
        if (r->zero[k]) {
            if (r->s[k] < mins[k] || r->s[k] > maxs[k]) return 0;
            continue;
        }
        float a = (mins[k] - r->s[k]) * r->inv[k], b = (maxs[k] - r->s[k]) * r->inv[k];
        if (a > b) { float x = a; a = b; b = x; }
        if (a > t0) t0 = a;
        if (b < t1) t1 = b;
        if (t0 > t1) return 0;
    }
    return 1;
}

/* the nearest displacement hit in a leaf (each displacement tested once per ray) */
static float ClipRayToDispInLeaf(const vec3_t start, const vec3_t delta, int leaf, int *face, float luxel[2], vec3_t normal) {
    boxray_t br;
    int haveray = 0;
    float best = 1.0f;
    *face = -1;
    for (int k = 0; k < nleafdisps[leaf]; k++) {
        int di = leafdisps[leaf][k];
        dispcoll_t *c = &dcoll[di];
        if (!tested) tested = xalloc(sizeof(int) * (numdispsurfs + 1));
        if (tested[di] == rayenum) continue;
        tested[di] = rayenum;
        const dispsurf_t *d = &dispsurfs[di];
        if (!(d->contents & MASK_OPAQUE)) continue;
        if (!haveray) BoxRay(start, delta, &br), haveray = 1;
        if (!RayMayHitBox(&br, c->bmins, c->bmaxs)) continue;
        float dist = FLT_MAX, bu = 0, bv = 0;
        int bt = -1;
        int stack[64], sp = 0;
        if (c->ntris) stack[sp++] = 0;
        while (sp) {
            const dispnode_t *node = &c->nodes[stack[--sp]];
            if (!RayMayHitBox(&br, node->box, node->box + 3)) continue;
            if (node->left >= 0) {
                stack[sp++] = node->right, stack[sp++] = node->left;
                continue;
            }
            for (int i = node->first; i < node->first + node->count; i++) {
                int t = c->order[i];
                const unsigned short *tr = c->tris[t];
                float u, v, tt;
                if (!RayTriangle(start, delta, d->verts[tr[0]], d->verts[tr[2]], d->verts[tr[1]], &u, &v, &tt)) continue;
                if (u >= 0.0f && v >= 0.0f && u + v <= 1.0f && tt > 0.0f && (tt < dist || (tt == dist && t < bt)))
                    dist = tt, bu = u, bv = v, bt = t;
            }
        }
        if (bt < 0 || !(dist < best)) continue;
        best = dist;
        *face = c->face;
        const unsigned short *tr = c->tris[bt];
        int i0 = tr[0], i1 = tr[2], i2 = tr[1];
        for (int k2 = 0; k2 < 2; k2++) {
            float eu = c->luxel[i1][k2] - c->luxel[i0][k2], ev = c->luxel[i2][k2] - c->luxel[i0][k2];
            luxel[k2] = c->luxel[i0][k2] + bu * eu;
            luxel[k2] = luxel[k2] + bv * ev;
        }
        vec3_t e0, e1;
        VectorSubtract(d->verts[i1], d->verts[i0], e0);
        VectorSubtract(d->verts[i2], d->verts[i0], e1);
        Cross(e0, e1, normal);
        VectorNormalize(normal);
    }
    return best;
}

/* ------------------------------------------------------------------ rays inside a leaf: brushes and displacements */
static float TraceLeafBrushes(int leaf, const vec3_t p1, const vec3_t p2, vec3_t hitnormal) {
    const dleaf_t *l = &dleafs[leaf];
    float fraction = 1.0f;
    for (int i = 0; i < l->numleafbrushes; i++) {
        const dbrush_t *b = &dbrushes[dleafbrushes[l->firstleafbrush + i]];
        if (!(b->contents & MASK_OPAQUE) || !b->numsides) continue;
        float enterfrac = -9999, leavefrac = 1.f;
        const dplane_t *clip = NULL;
        int getout = 0, startout = 0, out = 0;
        for (int s = 0; s < b->numsides; s++) {
            const dbrushside_t *side = &dbrushsides[b->firstside + s];
            if (side->bevel == 1) continue;
            const dplane_t *p = &dplanes[side->planenum];
            float d1 = DotProduct(p1, p->normal) - p->dist, d2 = DotProduct(p2, p->normal) - p->dist;
            if (d1 > 0 && d2 > 0) {
                out = 1;
                break;
            }
            if (d2 > 0) getout = 1;
            if (d1 > 0) startout = 1;
            if (d1 <= 0 && d2 <= 0) continue;
            if (d1 > d2) {
                float f = (d1 - DIST_EPSILON) / (d1 - d2);
                if (f > enterfrac) enterfrac = f, clip = p;
            } else {
                float f = (d1 + DIST_EPSILON) / (d1 - d2);
                if (f < leavefrac) leavefrac = f;
            }
        }
        if (out) continue;
        if (!startout) return 0.0f;                 /* (starts inside: vrad takes it as blocked at once) */
        (void)getout;
        if (enterfrac < leavefrac && enterfrac > -9999 && enterfrac < fraction) {
            if (enterfrac < 0) enterfrac = 0;
            fraction = enterfrac;
            VectorCopy(clip->normal, hitnormal);
            return fraction;
        }
    }
    return 1.0f;
}

static float CastRayInLeaf(const vec3_t start, const vec3_t end, int leaf, vec3_t normal) {
    float frac = 1.0f;
    vec3_t n;
    float t = TraceLeafBrushes(leaf, start, end, n);
    if (t != 1.0f) frac = t, VectorCopy(n, normal);
    rayenum++;
    vec3_t delta;
    VectorSubtract(end, start, delta);
    int face;
    float lux[2];
    float d = ClipRayToDispInLeaf(start, delta, leaf, &face, lux, n);
    if (d < frac) frac = d, VectorCopy(n, normal);
    return frac;
}

/* ------------------------------------------------------------------ rays through the tree: the surface they meet */
typedef struct {
    vec3_t start, delta;
    int surface;
    float hitfrac;
    float luxel[2];
    int hasluxel;
} lightsurf_t;

/* each face's winding, made once (before the threads start) */
static winding_t **facewindings;
static void BuildFaceWindings(void) {
    vec3_t zero = {0, 0, 0};
    facewindings = xalloc(sizeof(winding_t *) * (numfaces + 1));
    for (int i = 0; i < numfaces; i++) facewindings[i] = WindingFromFace(&g_pFaces[i], zero);
}

static int PointInFaceWinding(const vec3_t pt, const dface_t *f) {
    const winding_t *w = facewindings[f - g_pFaces];
    int ok = 1;
    vec3_t edge, topt, cross, test;
    VectorSubtract(pt, w->p[0], topt);
    VectorSubtract(w->p[1], w->p[0], edge);
    Cross(edge, topt, test);
    VectorNormalize(test);
    for (int i = 1; i < w->numpoints && ok; i++) {
        VectorSubtract(pt, w->p[i], topt);
        VectorSubtract(w->p[(i + 1) % w->numpoints], w->p[i], edge);
        Cross(edge, topt, cross);
        VectorNormalize(cross);
        if (DotProduct(cross, test) < 0.0f) ok = 0;
    }
    return ok;
}

static int PointOnSurface(const vec3_t pt, const dface_t *f, lightsurf_t *ls) {
    const texinfo_t *tx = &texinfo[f->texinfo];
    if (tx->flags & SURF_NOLIGHT) return 0;
    float s = DotProduct(pt, tx->lightmapVecsLuxelsPerWorldUnits[0]) + tx->lightmapVecsLuxelsPerWorldUnits[0][3];
    float t = DotProduct(pt, tx->lightmapVecsLuxelsPerWorldUnits[1]) + tx->lightmapVecsLuxelsPerWorldUnits[1][3];
    if (s < f->m_LightmapTextureMinsInLuxels[0] || t < f->m_LightmapTextureMinsInLuxels[1]) return 0;
    float ds = s - f->m_LightmapTextureMinsInLuxels[0], dt = t - f->m_LightmapTextureMinsInLuxels[1];
    if (ds > f->m_LightmapTextureSizeInLuxels[0] || dt > f->m_LightmapTextureSizeInLuxels[1]) return 0;
    ls->luxel[0] = ds, ls->luxel[1] = dt;
    return 1;
}

static int walkdbg;              /* (debugging, HLWALKDBG: the walk's nodes and leaves) */
static int EnumerateNode(lightsurf_t *ls, int node, float f) {
    vec3_t pt;
    for (int k = 0; k < 3; k++) pt[k] = ls->start[k] + f * ls->delta[k];
    if (walkdbg) Msg("  node %d at frac %.5f (%.1f %.1f %.1f), faces %d..%d\n", node, f, pt[0], pt[1], pt[2], dnodes[node].firstface, dnodes[node].firstface + dnodes[node].numfaces - 1);
    int sky = -1;
    const dnode_t *n = &dnodes[node];
    for (int i = 0; i < n->numfaces; i++) {
        int fi = n->firstface + i;
        const dface_t *face = &g_pFaces[fi];
        if (!face->onnode || face->dispinfo != -1) continue;
        if (texinfo[face->texinfo].flags & SURF_SKY) {
            if (PointInFaceWinding(pt, face)) sky = fi;
            continue;
        }
        if (PointOnSurface(pt, face, ls)) {
            ls->hitfrac = f, ls->surface = fi, ls->hasluxel = 1;
            return 0;
        }
    }
    ls->surface = sky;
    return sky < 0;
}

static int EnumerateLeaf(lightsurf_t *ls, int leaf, float start, float end) {
    int hit = 0;
    if (walkdbg) Msg("  leaf %d %.5f..%.5f\n", leaf, start, end);
    const dleaf_t *l = &dleafs[leaf];
    for (int i = 0; i < l->numleaffaces; i++) {
        int fi = dleaffaces[l->firstleafface + i];
        const dface_t *face = &g_pFaces[fi];
        if (face->dispinfo != -1 || face->onnode) continue;
        const dplane_t *p = &dplanes[face->planenum];
        if (DotProduct(p->normal, ls->delta) > 0) continue;
        float sdn = DotProduct(ls->start, p->normal), ddn = DotProduct(ls->delta, p->normal);
        float front = sdn + start * ddn - p->dist, back = sdn + end * ddn - p->dist;
        int side = front < 0;
        if ((back < 0) == side) continue;
        float f = front / (front - back);
        float mid = start * (1.0f - f) + end * f;
        if (mid >= ls->hitfrac) continue;
        vec3_t pt;
        for (int k = 0; k < 3; k++) pt[k] = ls->start[k] + mid * ls->delta[k];
        if (PointOnSurface(pt, face, ls)) ls->hitfrac = mid, ls->surface = fi, hit = 1, ls->hasluxel = 1;
    }
    int dface;
    float lux[2];
    vec3_t n;
    float dist = ClipRayToDispInLeaf(ls->start, ls->delta, leaf, &dface, lux, n);
    if (dist < ls->hitfrac) {
        ls->hitfrac = dist, ls->surface = dface;
        ls->luxel[0] = lux[0], ls->luxel[1] = lux[1];
        hit = 1, ls->hasluxel = 1;
    }
    return !hit;
}

static int EnumerateNodesAlongRay_r(lightsurf_t *ls, int node, float start, float end) {
    while (node >= 0) {
        const dnode_t *n = &dnodes[node];
        const dplane_t *p = &dplanes[n->planenum];
        float sdn, ddn;
        if (p->type <= 2) sdn = ls->start[p->type], ddn = ls->delta[p->type];
        else sdn = DotProduct(ls->start, p->normal), ddn = DotProduct(ls->delta, p->normal);
        float front = sdn + start * ddn - p->dist, back = sdn + end * ddn - p->dist;
        if (front <= -TEST_EPSILON && back <= -TEST_EPSILON) node = n->children[1];
        else if (front >= TEST_EPSILON && back >= TEST_EPSILON) node = n->children[0];
        else {
            int side = front < 0;
            float split;
            if (ddn == 0.0f) split = 1.0f;
            else {
                split = (p->dist - sdn) / ddn;
                if (split < 0.0f) split = 0.0f;
                else if (split > 1.0f) split = 1.0f;
            }
            if (!EnumerateNodesAlongRay_r(ls, n->children[side], start, split)) return 0;
            if (!EnumerateNode(ls, node, split)) return 0;
            return EnumerateNodesAlongRay_r(ls, n->children[!side], split, end);
        }
    }
    return EnumerateLeaf(ls, -1 - node, start, end);
}

/* ------------------------------------------------------------------ the colour a ray finds */
/* 2^e / 255 for a lightmap colour's exponent (a table: ldexp was a quarter of the time) */
static float power2_table[256];
static float power2_n(int e) { return power2_table[(e + 128) & 255]; }
float Power2Over255(int e) { return power2_table[(e + 128) & 255]; }

/* the sky ambient light's intensity, as the world lights lump has it */
static const float *skylight;

static __thread const float *raydir;      /* (the ray being followed: the sky map's colour in its direction) */
static void SkyColor(vec3_t c) {
    if (HaveSkyMap() && raydir) SkyMapColor(raydir, Luminance(skylight), c);
    else VectorCopy(skylight, c);
}

static void AmbientFromSurface(const dface_t *f, vec3_t c) {
    const texinfo_t *tx = &texinfo[f->texinfo];
    if (tx->flags & SURF_SKY) {
        if (skylight) SkyColor(c);
    } else {
        for (int k = 0; k < 3; k++) c[k] = c[k] * dtexdata[tx->texdata].reflectivity[k];
    }
}

/* (colors: one per light style, styles from nstyles on not kept) */
static void ColorFromAverage(const dface_t *f, float scale, vec3_t *colors, int nstyles) {
    if (texinfo[f->texinfo].flags & SURF_SKY) {
        if (skylight) {
            vec3_t c;
            SkyColor(c);
            for (int k = 0; k < 3; k++) colors[0][k] += c[k] * scale;
        }
        return;
    }
    for (int m = 0; m < 4 && f->styles[m] != 255; m++) {
        if (f->styles[m] >= nstyles) continue;
        const unsigned char *avg = dlightdata + f->lightofs - 4 * (m + 1);
        vec3_t c;
        for (int k = 0; k < 3; k++) c[k] = (float)avg[k] * power2_n((signed char)avg[3]);
        AmbientFromSurface(f, c);
        for (int k = 0; k < 3; k++) colors[f->styles[m]][k] += c[k] * scale;
    }
}

static void ColorPointSample(const dface_t *f, const float luv[2], float scale, vec3_t *colors, int nstyles) {
    if (f->lightofs == -1) return;
    int smax = f->m_LightmapTextureSizeInLuxels[0] + 1, tmax = f->m_LightmapTextureSizeInLuxels[1] + 1;
    int ds = (int)luv[0], dt = (int)luv[1];
    ds = ds < 0 ? 0 : ds > smax - 1 ? smax - 1 : ds;
    dt = dt < 0 ? 0 : dt > tmax - 1 ? tmax - 1 : dt;
    int offset = smax * tmax;
    const texinfo_t *tx = &texinfo[f->texinfo];
    if ((tx->flags & SURF_BUMPLIGHT) && !(tx->flags & SURF_NOLIGHT)) offset *= 4;
    const unsigned char *lm = dlightdata + f->lightofs + 4 * (dt * smax + ds);
    for (int m = 0; m < 4 && f->styles[m] != 255; m++, lm += 4 * offset) {
        vec3_t c;
        for (int k = 0; k < 3; k++) c[k] = (float)lm[k] * power2_n((signed char)lm[3]);
        AmbientFromSurface(f, c);
        if (f->styles[m] < nstyles)
            for (int k = 0; k < 3; k++) colors[f->styles[m]][k] += c[k] * scale;
    }
}

static __thread int g_dbgSurf, g_dbgHasLux;          /* (the last ray's hit, for AMBRAYS) */
static __thread float g_dbgFrac, g_dbgLux[2];
__thread float g_lastHitFrac;      /* (debugging: FindLightSurface's hit fraction) */
/* the surface a ray (start + delta) meets, as vrad's CLightSurface::FindIntersection: -1 for none */
int FindLightSurface(const vec3_t start, const vec3_t delta, int *hasluxel, float luxel[2]) {
    lightsurf_t ls;
    VectorCopy(start, ls.start);
    VectorCopy(delta, ls.delta);
    ls.surface = -1, ls.hitfrac = 1.0f, ls.hasluxel = 0;
    rayenum++;
    if (EnumerateNodesAlongRay_r(&ls, 0, 0.0f, 1.0f)) return -1;
    *hasluxel = ls.hasluxel;
    luxel[0] = ls.luxel[0], luxel[1] = ls.luxel[1];
    g_lastHitFrac = ls.hitfrac;
    return ls.surface;
}

/* the same with one surface finder kept across rays (as the static props' bounced light keeps vrad's
 * CLightSurface for all of a vertex's rays): its nearest hit so far stays, so later rays find only nearer
 * displacements and leaf faces (a node's face is taken at any distance), and a miss leaves the old luxel */
static __thread lightsurf_t keep;
void LightSurfaceBegin(void) { keep.surface = -1, keep.hitfrac = 1.0f, keep.hasluxel = 0; }
int FindLightSurfaceKept(const vec3_t start, const vec3_t delta, int *hasluxel, float luxel[2]) {
    VectorCopy(start, keep.start);
    VectorCopy(delta, keep.delta);
    rayenum++;
    if (EnumerateNodesAlongRay_r(&keep, 0, 0.0f, 1.0f)) return -1;
    *hasluxel = keep.hasluxel;
    luxel[0] = keep.luxel[0], luxel[1] = keep.luxel[1];
    g_lastHitFrac = keep.hitfrac;
    return keep.surface;
}

void CalcRayAmbientLighting(const vec3_t start, const vec3_t end, float tanTheta, vec3_t *colors, int nstyles) {
    lightsurf_t ls;
    VectorCopy(start, ls.start);
    VectorSubtract(end, start, ls.delta);
    ls.surface = -1, ls.hitfrac = 1.0f, ls.hasluxel = 0;
    g_dbgSurf = -1, g_dbgFrac = 1, g_dbgHasLux = 0;
    rayenum++;
    if (EnumerateNodesAlongRay_r(&ls, 0, 0.0f, 1.0f)) return;
    g_dbgSurf = ls.surface, g_dbgFrac = ls.hitfrac, g_dbgHasLux = ls.hasluxel, g_dbgLux[0] = ls.luxel[0], g_dbgLux[1] = ls.luxel[1];
    if (ls.surface < 0) return;
    /* (L4D2's order: z first, and the remap as a multiply by 1/20) */
    float len = sqrtf((ls.delta[2] * ls.delta[2] + ls.delta[1] * ls.delta[1]) + ls.delta[0] * ls.delta[0]);
    float avg = (len * tanTheta * ls.hitfrac - 20.0f) * 0.05f;
    avg = avg < 0 ? 0.0f : avg > 1.0f ? 1.0f : avg;
    if (!ls.hasluxel) avg = 1.0f;
    float point = 1.0f - avg;
    const dface_t *f = &g_pFaces[ls.surface];
    raydir = ls.delta;
    if (avg != 0) ColorFromAverage(f, avg, colors, nstyles);
    if (point != 0) ColorPointSample(f, ls.luxel, point, colors, nstyles);
}

/* ------------------------------------------------------------------ a sample's cube */
static const float boxdir[6][3] = {{1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}};
static unsigned char *worldlights;
static int numworldlights;

static void AddEmitSurfaceLights(const vec3_t start, vec3_t cube[6]) {
    if (!numworldlights) return;
    for (int i = 0; i < numworldlights; i++) {
        unsigned char *wl = worldlights + 100 * i;
        int flags, type;
        memcpy(&flags, wl + 88, 4);
        memcpy(&type, wl + 52, 4);
        if (!(flags & DWL_FLAGS_INAMBIENTCUBE)) continue;
        vec3_t origin, intensity, normal;
        memcpy(origin, wl, 12), memcpy(intensity, wl + 12, 12), memcpy(normal, wl + 24, 12);
        float radius;
        memcpy(&radius, wl + 72, 4);
        float vis = TestLine(start, origin, -1);
        if (!(vis > 0)) continue;
        vec3_t delta, dn;
        VectorSubtract(origin, start, delta);
        float dd = DotProduct(delta, delta);
        float dscale = (radius != 0 && dd > radius * radius) ? 0.0f : 1.0f / dd;
        VectorCopy(delta, dn);
        VectorNormalize(dn);
        float dot = DotProduct(dn, dn), dot2 = -DotProduct(dn, normal);
        float ascale = dot < 0 ? 0 : dot2 <= 0.1f / 10 ? 0 : dot * dot2;
        float ratio = dscale * ascale * vis;
        if (ratio == 0) continue;
        for (int s = 0; s < 6; s++) {
            float t = DotProduct(boxdir[s], dn);
            if (t > 0)
                for (int k = 0; k < 3; k++) cube[s][k] += intensity[k] * (t * ratio);
        }
    }
}

/* -gpu: the leaves run twice. The first time each cube is a question (its point recorded, the cube 0 for now: where the
 * points go doesn't depend on the cubes); the GPU answers them all; the second time each leaf's cubes are its answers,
 * in order. The surface lights (the CPU's) are added then. */
static int gpuamb_phase;                  /* 1 recording, 2 replaying */
static float **gpuamb_pts, **gpuamb_res;  /* per leaf: the points (3 floats), then the cubes (18) */
static int *gpuamb_n;
static __thread int gpuamb_leaf, gpuamb_cursor, gpuamb_poscursor;

static void AddEmitSurfaceLights(const vec3_t start, vec3_t cube[6]);
static void AmbientFromSphericalSamples(const vec3_t start, vec3_t cube[6]);
static int GpuAmbient(const vec3_t start, vec3_t cube[6]) {
    if (gpuamb_phase == 1) {
        int n = gpuamb_n[gpuamb_leaf]++;
        gpuamb_pts[gpuamb_leaf] = realloc(gpuamb_pts[gpuamb_leaf], sizeof(float) * 3 * (n + 1));
        memcpy(gpuamb_pts[gpuamb_leaf] + 3 * n, start, 12);
        memset(cube, 0, sizeof(vec3_t) * 6);
        return 1;
    }
    if (gpuamb_phase == 2) {
        memcpy(cube, gpuamb_res[gpuamb_leaf] + 18 * gpuamb_cursor++, sizeof(vec3_t) * 6);
        static volatile LONG checked;           /* (debugging, HLGPUCHK: a few cubes against the CPU's) */
        if (g_gpuCheck) {
            vec3_t c[6];
            gpuamb_phase = 0;
            AmbientFromSphericalSamples(start, c);
            gpuamb_phase = 2;
            float worst = 0;
            for (int k = 0; k < 6; k++)
                for (int j = 0; j < 3; j++) {
                    float d = fabsf(c[k][j] - cube[k][j]) / (fabsf(c[k][j]) + 0.01f);
                    if (d > worst) worst = d;
                }
            if (worst > 0.05f && InterlockedIncrement(&checked) <= 2) {
                Msg("at %.1f %.1f %.1f (leaf %d)\n", start[0], start[1], start[2], gpuamb_leaf);
                for (int k = 0; k < 6; k++) Msg("   cpu %8.3f %8.3f %8.3f   gpu %8.3f %8.3f %8.3f\n", c[k][0], c[k][1], c[k][2], cube[k][0], cube[k][1], cube[k][2]);
            }
        }
        AddEmitSurfaceLights(start, cube);
        return 1;
    }
    return 0;
}

static void AmbientFromSphericalSamples(const vec3_t start, vec3_t cube[6]) {
    if (GpuAmbient(start, cube)) return;
    vec3_t rad[NUMVERTEXNORMALS];
    float tanTheta = (float)tan(VERTEXNORMAL_CONE_INNER_ANGLE);
    for (int i = 0; i < NUMVERTEXNORMALS; i++) {
        vec3_t end;
        for (int k = 0; k < 3; k++) end[k] = start[k] + g_anorms[i][k] * (float)(COORD_EXTENT * 1.74);
        VectorClear(rad[i]);
        CalcRayAmbientLighting(start, end, tanTheta, &rad[i], 1);
    }
    for (int j = 6; --j >= 0;) {
        float t = 0;
        VectorClear(cube[j]);
        for (int i = 0; i < NUMVERTEXNORMALS; i++) {
            float c = DotProduct(g_anorms[i], boxdir[j]);
            if (c > 0) {
                t += c;
                for (int k = 0; k < 3; k++) cube[j][k] += rad[i][k] * c;
            }
        }
        float s = 1 / t;
        VectorScale(cube[j], s, cube[j]);
    }
    AddEmitSurfaceLights(start, cube);
}

/* ------------------------------------------------------------------ samples per leaf */
typedef struct { vec3_t pos; vec3_t cube[6]; } ambsample_t;

static void SamplePosition(int leaf, rng_t *rng, const plane_t *planes, int nplanes, vec3_t pos) {
    if (gpuamb_phase == 2) {           /* (-gpu, replaying: where the recording put it) */
        memcpy(pos, gpuamb_pts[leaf] + 3 * gpuamb_poscursor++, 12);
        return;
    }
    const dleaf_t *l = &dleafs[leaf];
    float dx = (float)(l->maxs[0] - l->mins[0]), dy = (float)(l->maxs[1] - l->mins[1]), dz = (float)(l->maxs[2] - l->mins[2]);
    int valid = 0;
    for (int i = 0; i < 1000 && !valid; i++) {
        pos[0] = l->mins[0] + RngFloat(rng, 0, dx);
        pos[1] = l->mins[1] + RngFloat(rng, 0, dy);
        pos[2] = l->mins[2] + RngFloat(rng, 0, dz);
        valid = 1;
        for (int j = nplanes; --j >= 0 && valid;) {
            float d = DotProduct(planes[j].normal, pos) - planes[j].dist;
            if (d < DIST_EPSILON) valid = 0;
        }
        if (!valid) continue;
        for (int j = 0; j < 6; j++) {
            vec3_t start, normal;
            VectorCopy(pos, start);
            int axis = j % 3;
            start[axis] = j < 3 ? l->mins[axis] : l->maxs[axis];
            float t = CastRayInLeaf(pos, start, leaf, normal);
            if (t == 0.0f) {
                valid = 0;
                break;
            }
            if (t != 1.0f) {
                vec3_t delta;
                VectorSubtract(start, pos, delta);
                if (DotProduct(delta, normal) > 0) {
                    valid = 0;
                    break;
                }
            }
        }
    }
    if (!valid)
        for (int k = 0; k < 3; k++) pos[k] = ((float)l->mins[k] + (float)l->maxs[k]) * 0.5f;
}

static int AddSampleToList(ambsample_t *list, int n, const vec3_t pos, vec3_t cube[6]) {
    VectorCopy(pos, list[n].pos);
    memcpy(list[n].cube, cube, sizeof(vec3_t) * 6);
    n++;
    if (n <= MAX_SAMPLES) return n;
    int nearest = 0;
    float nearestDist = FLT_MAX, nearestTotal = 0;
    for (int i = 0; i < n; i++) {
        float closest = FLT_MAX, totalDC = 0;
        for (int j = 0; j < n; j++) {
            if (j == i) continue;
            vec3_t d;
            VectorSubtract(list[i].pos, list[j].pos, d);
            float dist = sqrtf((d[2] * d[2] + d[1] * d[1]) + d[0] * d[0]), maxDC = 0;     /* (L4D2's order) */
            for (int k = 0; k < 6; k++) {
                for (int s = 0; s < 3; s++) {
                    float dc = fabsf(list[i].cube[k][s] - list[j].cube[k][s]);
                    maxDC = maxDC > dc ? maxDC : dc;
                }
                totalDC += maxDC;
            }
            if (maxDC < 1e-4f) maxDC = 0;
            else if (maxDC > 1.0f) maxDC = 1.0f;
            dist *= 0.1f + maxDC * 0.9f;
            if (dist < closest) closest = dist;
        }
        if (closest < nearestDist || (closest == nearestDist && totalDC < nearestTotal)) {
            nearestDist = closest;     /* (vrad never updates nearestTotal: the tie-break never applies) */
            nearest = i;
        }
    }
    list[nearest] = list[n - 1];                /* (FastRemove) */
    return n - 1;
}

static int lineartoscreen[1024];

static void BuildGammaTable(void) {
    float g = (float)(1.0 / 2.2f), g3 = 0.125f;
    for (int i = 0; i < 1024; i++) {
        float f = (float)(i / 1023.0);
        if (f <= g3) f = (f / g3) * 0.125f;
        else f = (float)(0.125 + ((f - g3) / (1.0 - g3)) * 0.875);
        int inf = (int)(255 * pow(f, g));
        lineartoscreen[i] = inf < 0 ? 0 : inf > 255 ? 255 : inf;
    }
}

static int LinearToScreenGamma(float f) {
    int i = (int)(f * 1023.f);              /* (truncated, as L4D2) */
    i = i < 0 ? 0 : i > 1023 ? 1023 : i;
    return lineartoscreen[i];
}

static int CompressSamples(ambsample_t *list, int n) {
    vec3_t test[6];
    for (int i = 0; i < n; i++) {
        if (n <= 1) continue;
        float total = 0;
        for (int k = 0; k < 6; k++) VectorClear(test[k]);
        for (int j = 0; j < n; j++) {
            if (j == i) continue;
            vec3_t d;
            VectorSubtract(list[j].pos, list[i].pos, d);
            float factor = 1.0f / (DotProduct(d, d) + 1.0f);
            total += factor;
            for (int k = 0; k < 6; k++)
                for (int s = 0; s < 3; s++) test[k][s] += list[j].cube[k][s] * factor;
        }
        for (int k = 0; k < 6; k++) VectorScale(test[k], 1.0f / total, test[k]);
        int maxDelta = 0;
        for (int k = 0; k < 6; k++)
            for (int s = 0; s < 3; s++) {
                int delta = abs(LinearToScreenGamma(test[k][s]) - LinearToScreenGamma(list[i].cube[k][s]));
                if (delta > maxDelta) maxDelta = delta;
            }
        if (maxDelta < 3) {
            list[i] = list[n - 1];
            n--;
            i--;
        }
    }
    return n;
}

static int AmbientForLeaf(int leaf, ambsample_t *list, plane_t *planes) {
    int nplanes = LeafBoundaryPlanes(leaf, planes);
    const dleaf_t *l = &dleafs[leaf];
    int xs = (l->maxs[0] - l->mins[0]) / 32;
    xs = xs > 1 ? xs : 1;                       /* (vrad uses the x count for y and z too) */
    int count = xs * xs * xs;
    count = count < 1 ? 1 : count > 128 ? 128 : count;
    if (l->contents & CONTENTS_SOLID) return 0;
    rng_t rng;
    RngSeed(&rng, 0);
    int n = 0;
    for (int i = 0; i < count; i++) {
        vec3_t pos, cube[6];
        SamplePosition(leaf, &rng, planes, nplanes, pos);
        AmbientFromSphericalSamples(pos, cube);
        n = AddSampleToList(list, n, pos, cube);
    }
    return CompressSamples(list, n);
}

static ambsample_t *leafresults;
static int *leafcounts;
static int *leaforder;           /* (the leaves with the most samples first: no big leaf left to the end) */
static int LeafCost(int leaf) {
    const dleaf_t *l = &dleafs[leaf];
    int xs = (l->maxs[0] - l->mins[0]) / 32;
    xs = xs > 1 ? xs : 1;
    int count = xs * xs * xs;
    return (l->contents & CONTENTS_SOLID) ? 0 : count > 128 ? 128 : count;
}
static int CompareCost(const void *a, const void *b) {
    int ca = LeafCost(*(const int *)a), cb = LeafCost(*(const int *)b);
    return ca != cb ? cb - ca : *(const int *)a - *(const int *)b;
}
static void LeafWork(int item, int thread) {
    static __thread plane_t *planes;
    int leaf = leaforder[item];
    (void)thread;
    gpuamb_leaf = leaf, gpuamb_cursor = gpuamb_poscursor = 0;
    if (!planes) planes = xalloc(sizeof(plane_t) * (numnodes + 1));
    leafcounts[leaf] = AmbientForLeaf(leaf, leafresults + (MAX_SAMPLES + 1) * leaf, planes);
}

static unsigned char Fixed8Fraction(float t, float tmin, float tmax) {
    if (tmax <= tmin) return 0;
    float frac = (t - tmin) / (tmax - tmin) * 255.0f;
    frac = frac < 0 ? 0 : frac > 255.0f ? 255.0f : frac;
    return (unsigned char)(frac + 0.5f);
}

static int NearestNeighbourWithLight(int leaf, const unsigned short (*index)[2]) {
    const dleaf_t *l = &dleafs[leaf];
    float best = FLT_MAX;
    int bestIndex = leaf;
    vec3_t mins, maxs, size, bmin, bmax;
    for (int k = 0; k < 3; k++) mins[k] = l->mins[k], maxs[k] = l->maxs[k], size[k] = maxs[k] - mins[k];
    for (int k = 0; k < 3; k++) bmin[k] = mins[k] - size[k], bmax[k] = maxs[k] + size[k];
    /* the leaves the grown box touches, in tree order */
    int stack[1024], sp = 0, *found = xalloc(sizeof(int) * (numleafs + 1)), nf = 0;
    stack[sp++] = 0;
    while (sp) {
        int node = stack[--sp];
        if (node < 0) {
            found[nf++] = -1 - node;
            continue;
        }
        const dplane_t *p = &dplanes[dnodes[node].planenum];
        float dmin = 0, dmax = 0;
        for (int k = 0; k < 3; k++) {
            if (p->normal[k] >= 0) dmin += p->normal[k] * bmin[k], dmax += p->normal[k] * bmax[k];
            else dmin += p->normal[k] * bmax[k], dmax += p->normal[k] * bmin[k];
        }
        if (dmin >= p->dist) stack[sp++] = dnodes[node].children[0];
        else if (dmax < p->dist) stack[sp++] = dnodes[node].children[1];
        else {
            stack[sp++] = dnodes[node].children[1];
            stack[sp++] = dnodes[node].children[0];
        }
    }
    for (int i = 0; i < nf; i++) {
        int t = found[i];
        if (!index[t][0]) continue;
        const dleaf_t *tl = &dleafs[t];
        vec3_t delta;
        for (int k = 0; k < 3; k++) {
            float gmin = mins[k] > tl->mins[k] ? mins[k] : tl->mins[k];
            float lmax = maxs[k] < tl->maxs[k] ? maxs[k] : tl->maxs[k];
            delta[k] = gmin < lmax ? 0 : lmax - gmin;
        }
        float dist = sqrtf(DotProduct(delta, delta));
        if (dist < best) best = dist, bestIndex = t;
    }
    free(found);
    return bestIndex;
}

void VectorToColorRGBExp32(const vec3_t v, unsigned char *c);

/* -gpu: the map as the walk above sees it, for the GPU's copy of the walk (walk.glsl) */
static void GpuWalkScene(void) {
    gpuwalk_t w;
    memset(&w, 0, sizeof(w));
    float *planes = xalloc(16 * (size_t)(numplanes + 1));
    for (int i = 0; i < numplanes; i++) memcpy(planes + 4 * i, dplanes[i].normal, 12), planes[4 * i + 3] = dplanes[i].dist;
    int *nodes = xalloc(16 * (size_t)(numnodes + 1));
    for (int i = 0; i < numnodes; i++) {
        nodes[4 * i] = dnodes[i].planenum, nodes[4 * i + 1] = dnodes[i].children[0], nodes[4 * i + 2] = dnodes[i].children[1];
        nodes[4 * i + 3] = (int)((unsigned)dnodes[i].firstface | ((unsigned)dnodes[i].numfaces << 16));
    }
    int nld = 0;
    for (int i = 0; i < numleafs; i++) nld += nleafdisps[i];
    int *leaves = xalloc(16 * (size_t)(numleafs + 1)), *ld = xalloc(4 * (size_t)(nld + 1));
    for (int i = 0, at = 0; i < numleafs; i++) {
        leaves[4 * i] = dleafs[i].firstleafface, leaves[4 * i + 1] = dleafs[i].numleaffaces;
        leaves[4 * i + 2] = at, leaves[4 * i + 3] = nleafdisps[i];
        memcpy(ld + at, leafdisps[i], 4 * (size_t)nleafdisps[i]), at += nleafdisps[i];
    }
    int *lf = xalloc(4 * (size_t)(numleaffaces + 1));
    for (int i = 0; i < numleaffaces; i++) lf[i] = dleaffaces[i];
    int nwind = 0;
    for (int i = 0; i < numfaces; i++) nwind += facewindings[i]->numpoints;
    gpuwface_t *faces = xalloc(sizeof(gpuwface_t) * (numfaces + 1));
    float *wind = xalloc(16 * (size_t)(nwind + 1));
    for (int i = 0, at = 0; i < numfaces; i++) {
        const dface_t *f = &g_pFaces[i];
        const texinfo_t *tx = &texinfo[f->texinfo];
        gpuwface_t *g = &faces[i];
        memcpy(g->lmS, tx->lightmapVecsLuxelsPerWorldUnits[0], 16), memcpy(g->lmT, tx->lightmapVecsLuxelsPerWorldUnits[1], 16);
        g->a[0] = f->lightofs;
        memcpy(&g->a[1], f->styles, 4);
        g->a[2] = f->m_LightmapTextureSizeInLuxels[0] + 1, g->a[3] = f->m_LightmapTextureSizeInLuxels[1] + 1;
        g->b[0] = f->m_LightmapTextureMinsInLuxels[0], g->b[1] = f->m_LightmapTextureMinsInLuxels[1];
        g->b[2] = (tx->flags & SURF_BUMPLIGHT) && !(tx->flags & SURF_NOLIGHT) ? 4 : 1;
        g->b[3] = tx->flags;
        g->c[0] = f->planenum, g->c[1] = (f->onnode ? 1 : 0) | (f->dispinfo != -1 ? 2 : 0);
        g->c[2] = at, g->c[3] = facewindings[i]->numpoints;
        for (int k = 0; k < facewindings[i]->numpoints; k++, at++) memcpy(wind + 4 * at, facewindings[i]->p[k], 12);
        memcpy(g->refl, dtexdata[tx->texdata].reflectivity, 12);
    }
    int nv = 0, nt = 0, nn = 0;
    for (int i = 0; i < numdispsurfs; i++)
        if (dcoll[i].face >= 0) nv += dispsurfs[i].numverts, nt += dcoll[i].ntris, nn += 2 * dcoll[i].ntris + 2;
    gpuwdisp_t *disps = xalloc(sizeof(gpuwdisp_t) * (numdispsurfs + 1));
    float *dv = xalloc(16 * (size_t)(nv + 1)), *dl = xalloc(8 * (size_t)(nv + 1));
    int *dt = xalloc(16 * (size_t)(nt + 1)), *dor = xalloc(4 * (size_t)(nt + 1));
    gpuwnode_t *dn = xalloc(sizeof(gpuwnode_t) * (nn + 1));
    for (int i = 0, av = 0, at = 0, an = 0; i < numdispsurfs; i++) {
        const dispsurf_t *d = &dispsurfs[i];
        const dispcoll_t *c = &dcoll[i];
        gpuwdisp_t *g = &disps[i];
        g->a[0] = c->face;
        if (c->face < 0) continue;
        g->a[1] = c->ntris, g->a[2] = an, g->a[3] = (d->contents & MASK_OPAQUE) != 0;
        g->b[0] = av, g->b[1] = at, g->b[2] = at;
        memcpy(g->bmins, c->bmins, 12), memcpy(g->bmaxs, c->bmaxs, 12);
        for (int k = 0; k < d->numverts; k++) memcpy(dv + 4 * (av + k), d->verts[k], 12), memcpy(dl + 2 * (av + k), c->luxel[k], 8);
        for (int k = 0; k < c->ntris; k++) {
            for (int j = 0; j < 3; j++) dt[4 * (at + k) + j] = c->tris[k][j];
            dor[at + k] = c->order[k];
        }
        for (int k = 0; k < 2 * c->ntris + 2; k++) {
            const dispnode_t *s = &c->nodes[k];
            memcpy(dn[an + k].lo, s->box, 12), memcpy(dn[an + k].hi, s->box + 3, 12);
            dn[an + k].i[0] = s->left, dn[an + k].i[1] = s->right, dn[an + k].i[2] = s->first, dn[an + k].i[3] = s->count;
        }
        av += d->numverts, at += c->ntris, an += 2 * c->ntris + 2;
    }
    w.planes = planes, w.nplanes = numplanes, w.nodes = nodes, w.nnodes = numnodes, w.leaves = leaves, w.nleaves = numleafs;
    w.leaffaces = lf, w.nleaffaces = numleaffaces, w.faces = faces, w.nfaces = numfaces, w.windings = wind, w.nwindings = nwind;
    w.disps = disps, w.ndisps = numdispsurfs, w.dverts = dv, w.ndverts = nv, w.dtris = dt, w.ndtris = nt, w.dnodes = dn;
    w.ndnodes = nn, w.dorder = dor, w.ndorder = nt, w.dlux = dl, w.ndlux = nv, w.leafdisps = ld, w.nleafdisps = nld;
    GPU_WalkScene(&w);
    free(planes), free(nodes), free(leaves), free(ld), free(lf), free(faces), free(wind), free(disps), free(dv), free(dl);
    free(dt), free(dor), free(dn);
}

/* what the rays need (also for detail props): the tree's parents, displacements' triangles, the sky light */
void AmbientSetup(void) {
    static int done;
    if (done) return;
    done = 1;
    dtexdata = (const dtexdata_t *)lumps[LUMP_TEXDATA].data;
    BuildParents();
    BuildDispCollision();
    BuildFaceWindings();
    for (int e = -128; e < 128; e++) power2_table[e + 128] = (float)(ldexp(1.0, e) / 255.0);
    BuildGammaTable();
    if (g_bGPU) GpuWalkScene();
    if (getenv("HLWALKDBG")) {
        vec3_t s, d;
        int has;
        float lux[2];
        sscanf(getenv("HLWALKDBG"), "%f %f %f %f %f %f", &s[0], &s[1], &s[2], &d[0], &d[1], &d[2]);
        for (int k = 0; k < 3; k++) d[k] = (s[k] + d[k] * (float)(COORD_EXTENT * 1.74)) - s[k];
        walkdbg = 1;
        int f = FindLightSurface(s, d, &has, lux);
        walkdbg = 0;
        Msg("walk: face %d frac %.5f\n", f, g_lastHitFrac);
    }
    int wlump = g_bHDR ? LUMP_WORLDLIGHTS_HDR : LUMP_WORLDLIGHTS;
    worldlights = lumps[wlump].data;
    numworldlights = lumps[wlump].len / 100;
    skylight = NULL;
    for (int i = 0; i < numworldlights && !skylight; i++) {
        int type;
        memcpy(&type, worldlights + 100 * i + 52, 4);
        if (type == emit_skyambient) skylight = (const float *)(worldlights + 100 * i + 12);
    }
}

void ComputePerLeafAmbientLighting(void) {
    AmbientSetup();
    /* surface lights small enough go into the cubes */
    int insurf = 0, nsurf = 0;
    for (int i = 0; i < numworldlights; i++) {
        unsigned char *wl = worldlights + 100 * i;
        int type, style, flags;
        float in[3];
        memcpy(&type, wl + 52, 4), memcpy(&style, wl + 56, 4), memcpy(&flags, wl + 88, 4), memcpy(in, wl + 12, 12);
        float m = in[0] > in[1] ? in[0] : in[1];
        m = m > in[2] ? m : in[2];
        int inCube = type == emit_surface && style == 0 && m * (1.0f / (512.0f * 512.0f)) < 0.005f;
        if (inCube) flags |= DWL_FLAGS_INAMBIENTCUBE;
        else flags &= ~DWL_FLAGS_INAMBIENTCUBE;
        memcpy(wl + 88, &flags, 4);
        if (type == emit_surface) nsurf++;
        if (flags & DWL_FLAGS_INAMBIENTCUBE) insurf++;
    }
    Msg("%d of %d (%d%% of) surface lights went in leaf ambient cubes.\n", insurf, nsurf, nsurf ? insurf * 100 / nsurf : 0);
    if (getenv("AMBRAYS")) {     /* (debugging: rays' colours: 9 floats each, start, end, colour; out adds surface, frac, luxel) */
        FILE *in = fopen(getenv("AMBRAYS"), "rb"), *of = fopen("raysout.bin", "wb");
        float rec[9], tanTheta = (float)tan(VERTEXNORMAL_CONE_INNER_ANGLE);
        while (fread(rec, 4, 9, in) == 9) {
            float o[13];
            VectorClear(o + 6);
            CalcRayAmbientLighting(rec, rec + 3, tanTheta, (vec3_t *)(o + 6), 1);
            memcpy(o, rec, 24);
            o[9] = (float)g_dbgSurf, o[10] = g_dbgFrac, o[11] = g_dbgHasLux ? g_dbgLux[0] : -1, o[12] = g_dbgLux[1];
            fwrite(o, 4, 13, of);
        }
        fclose(of), fclose(in);
        exit(0);
    }
    if (getenv("AMBPOS")) {      /* (debugging: the cubes at given points: 21 floats each, pos then cube, as vradhook logs) */
        if (getenv("AMBLM")) {   /* (and another bsp's lightmaps, to test this part alone) */
            FILE *bf = fopen(getenv("AMBLM"), "rb");
            int ofs[2];
            fseek(bf, 12 + 16 * LUMP_LIGHTING_HDR, SEEK_SET);
            fread(ofs, 4, 2, bf);
            if (ofs[1] == lightdatasize) fseek(bf, ofs[0], SEEK_SET), fread(dlightdata, 1, ofs[1], bf);
            else Error("AMBLM: lightmap sizes differ");
            fclose(bf);
        }
        FILE *in = fopen(getenv("AMBPOS"), "rb"), *of = fopen("ambout.bin", "wb");
        float rec[21];
        while (fread(rec, 4, 21, in) == 21) {
            vec3_t cube[6];
            AmbientFromSphericalSamples(rec, cube);
            memcpy(rec + 3, cube, 72);
            fwrite(rec, 4, 21, of);
        }
        fclose(of), fclose(in);
        exit(0);
    }
    unsigned short (*index)[2] = xalloc(sizeof(*index) * (numleafs + 1));
    unsigned char *out = xalloc(28 * MAX_SAMPLES * (numleafs + 1));
    int nout = 0;
    leafresults = xalloc(sizeof(ambsample_t) * (MAX_SAMPLES + 1) * (numleafs + 1));
    leafcounts = xalloc(sizeof(int) * (numleafs + 1));
    leaforder = xalloc(sizeof(int) * (numleafs + 1));
    for (int i = 0; i < numleafs; i++) leaforder[i] = i;
    qsort(leaforder, numleafs, sizeof(int), CompareCost);
    if (g_bGPU) {
        GPU_Lightmaps();
        gpuamb_pts = xalloc(sizeof(float *) * (numleafs + 1));
        gpuamb_res = xalloc(sizeof(float *) * (numleafs + 1));
        gpuamb_n = xalloc(sizeof(int) * (numleafs + 1));
        LARGE_INTEGER qf, q0, q1, q2, q3;     /* (HLGPUDBG: how long each part takes) */
        QueryPerformanceFrequency(&qf), QueryPerformanceCounter(&q0);
        gpuamb_phase = 1;
        RunThreadsOn(numleafs, LeafWork);
        QueryPerformanceCounter(&q1);
        int total = 0;
        for (int i = 0; i < numleafs; i++) total += gpuamb_n[i];
        float *pts = xalloc(sizeof(float) * 3 * (total + 1)), *res = xalloc(sizeof(float) * 18 * (total + 1));
        for (int i = 0, at = 0; i < numleafs; i++) memcpy(pts + 3 * at, gpuamb_pts[i], sizeof(float) * 3 * gpuamb_n[i]), at += gpuamb_n[i];
        float sky[4] = {0, 0, 0, 0};
        if (skylight) memcpy(sky, skylight, 12), sky[3] = 1;
        GPU_Ambient(pts, total, sky, res);
        QueryPerformanceCounter(&q2);
        for (int i = 0, at = 0; i < numleafs; i++) gpuamb_res[i] = res + 18 * at, at += gpuamb_n[i];
        gpuamb_phase = 2;
        RunThreadsOn(numleafs, LeafWork);
        gpuamb_phase = 0;
        QueryPerformanceCounter(&q3);
        if (getenv("HLGPUDBG"))
            Msg("leaf ambient: %d points; recording %.3f s, GPU %.3f s, replaying %.3f s\n", total,
                (double)(q1.QuadPart - q0.QuadPart) / qf.QuadPart, (double)(q2.QuadPart - q1.QuadPart) / qf.QuadPart,
                (double)(q3.QuadPart - q2.QuadPart) / qf.QuadPart);
        for (int i = 0; i < numleafs; i++) free(gpuamb_pts[i]);
        free(pts), free(res), free(gpuamb_pts), free(gpuamb_res), free(gpuamb_n);
    } else
        RunThreadsOn(numleafs, LeafWork);
    free(leaforder);
    for (int leaf = 0; leaf < numleafs; leaf++) {
        int n = leafcounts[leaf];
        const ambsample_t *list = leafresults + (MAX_SAMPLES + 1) * leaf;
        index[leaf][0] = (unsigned short)n;
        index[leaf][1] = n ? (unsigned short)nout : 0;
        const dleaf_t *l = &dleafs[leaf];
        for (int i = 0; i < n; i++, nout++) {
            unsigned char *o = out + 28 * nout;
            for (int s = 0; s < 6; s++) VectorToColorRGBExp32(list[i].cube[s], o + 4 * s);
            o[24] = Fixed8Fraction(list[i].pos[0], l->mins[0], l->maxs[0]);
            o[25] = Fixed8Fraction(list[i].pos[1], l->mins[1], l->maxs[1]);
            o[26] = Fixed8Fraction(list[i].pos[2], l->mins[2], l->maxs[2]);
            o[27] = 0;
        }
    }
    for (int i = 0; i < numleafs; i++)
        if (index[i][0] == 0) {
            if (!(dleafs[i].contents & CONTENTS_SOLID)) Msg("Bad leaf ambient for leaf %d\n", i);
            index[i][1] = (unsigned short)NearestNeighbourWithLight(i, (const unsigned short(*)[2])index);
        }
    SetLump(g_bHDR ? LUMP_LEAF_AMBIENT_INDEX_HDR : LUMP_LEAF_AMBIENT_INDEX, index, 4 * numleafs, 0);   /* (vrad: version 0 for the index, 1 for the samples) */
    SetLump(g_bHDR ? LUMP_LEAF_AMBIENT_LIGHTING_HDR : LUMP_LEAF_AMBIENT_LIGHTING, out, 28 * nout, 1);
    free(leafresults), free(leafcounts);
}
