/* Displacements (terrain) as vrad builds them from the BSP (CCoreDispInfo / CVRADDispColl).
 *
 * The base quad is the face's 4 corners, turned to start at the corner nearest the stored start position;
 * a vertex sits on the flat quad (interpolated row by row) moved by its stored direction times distance.
 * The full-detail triangles (each quad cut corner to corner, the diagonal alternating with the vertex
 * index) block light in the ray tracer. */
#include "hlvrad.h"

#pragma pack(push, 1)
typedef struct { vec3_t vec; float dist, alpha; } dispvert_t;
#pragma pack(pop)

typedef struct { unsigned short neighbor; unsigned char orientation, span, nbspan; } subneighbor_t;
typedef struct { subneighbor_t sub[2]; } edgeneighbor_t;
typedef struct { unsigned short n[4]; unsigned char count; } cornerneighbors_t;

typedef struct {
    vec3_t startpos;
    int vert_start, tri_start, power, mintess;
    float smoothing;
    int contents;
    unsigned short mapface;
    int lmalpha_start, lmsample_start;
    edgeneighbor_t edge[4];
    cornerneighbors_t corner[4];
    uint32_t allowed[10];
} ddispinfo_t;
_Static_assert(sizeof(ddispinfo_t) == 176, "ddispinfo_t is 176 bytes");

dispsurf_t *dispsurfs;
int numdispsurfs;
static const ddispinfo_t *dispinfos;

static float LengthSqr(const vec3_t v) { return v[0] * v[0] + v[1] * v[1] + v[2] * v[2]; }

static void GenerateDispSurf(dispsurf_t *d, const dispvert_t *dv) {
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
            vec3_t t;
            VectorScale(segint, (float)j, t);
            VectorAdd(end0, t, d->flat[ndx]);
            VectorCopy(d->flat[ndx], d->verts[ndx]);
            d->verts[ndx][0] += 0.0f;          /* (+ the subdivision position: zero) */
            d->verts[ndx][1] += 0.0f;
            d->verts[ndx][2] += 0.0f;
            VectorScale(dv[ndx].vec, dv[ndx].dist, t);
            VectorAdd(d->verts[ndx], t, d->verts[ndx]);
            d->alpha[ndx] = dv[ndx].alpha;
        }
    }
}

/* ------------------------------------------------------------------ vertex normals (CCoreDispInfo) */
static void Cross(const vec3_t a, const vec3_t b, vec3_t c) {
    c[0] = a[1] * b[2] - a[2] * b[1];
    c[1] = a[2] * b[0] - a[0] * b[2];
    c[2] = a[0] * b[1] - a[1] * b[0];
}

/* one triangle's unit normal: (c - a) x (b - a) as CalcNormalFromEdges takes it */
static void TriNormalAdd(const vec3_t *v, int a, int b, int c, vec3_t accum) {
    vec3_t t0, t1, n;
    VectorSubtract(v[b], v[a], t0);
    VectorSubtract(v[c], v[a], t1);
    Cross(t1, t0, n);
    VectorNormalize(n);
    VectorAdd(accum, n, accum);
}

/* the mean of the unit normals of the (up to 8) triangles around each vertex */
static void GenerateDispSurfNormals(dispsurf_t *d) {
    int ps = (1 << d->power) + 1;
    const vec3_t *v = d->verts;
    d->normals = xalloc(sizeof(vec3_t) * d->numverts);
    for (int i = 0; i < ps; i++) {           /* i: column (y), j: row (x) */
        for (int j = 0; j < ps; j++) {
            int e0 = j - 1 >= 0, e1 = i + 1 <= ps - 1, e2 = j + 1 <= ps - 1, e3 = i - 1 >= 0;
            vec3_t accum = {0, 0, 0};
            int n = 0;
#define P(col, row) ((col) * ps + (row))
            if (e1 && e2) {
                TriNormalAdd(v, P(i, j), P(i + 1, j), P(i, j + 1), accum);
                TriNormalAdd(v, P(i, j + 1), P(i + 1, j), P(i + 1, j + 1), accum);
                n += 2;
            }
            if (e0 && e1) {
                TriNormalAdd(v, P(i, j - 1), P(i + 1, j - 1), P(i, j), accum);
                TriNormalAdd(v, P(i, j), P(i + 1, j - 1), P(i + 1, j), accum);
                n += 2;
            }
            if (e0 && e3) {
                TriNormalAdd(v, P(i - 1, j - 1), P(i, j - 1), P(i - 1, j), accum);
                TriNormalAdd(v, P(i - 1, j), P(i, j - 1), P(i, j), accum);
                n += 2;
            }
            if (e2 && e3) {
                TriNormalAdd(v, P(i - 1, j), P(i, j), P(i - 1, j + 1), accum);
                TriNormalAdd(v, P(i - 1, j + 1), P(i, j), P(i, j + 1), accum);
                n += 2;
            }
#undef P
            VectorScale(accum, 1.0f / (float)n, d->normals[i * ps + j]);
        }
    }
}

/* luxel samples gather from this far: 2.2 luxel diagonals (at most 512 units), squared */
static void CalcSampleRadius2(dispsurf_t *d, const dface_t *face) {
    const float *lv = texinfo[face->texinfo].lightmapVecsLuxelsPerWorldUnits[0];
    float w = 1.0f / (float)sqrt(lv[0] * lv[0] + lv[1] * lv[1] + lv[2] * lv[2]);
    float r = (float)(sqrt(w * w + w * w) * 2.2f);
    if (r > 512.0f) r = 512.0f;
    d->sample_radius2 = r * r;
    d->sample_width = w;
    float pr = w * dispchop * 2.2f;            /* (bounced light: dispchop luxels per patch) */
    if (pr > 1500.0f) {
        pr = 1500.0f;
        Msg("Warning: Patch Sample Radius Clamped!\n");
    }
    d->patch_radius2 = pr * pr;
}

/* ------------------------------------------------------------------ smoothing across neighbours (disp_vrad.cpp) */
typedef struct { int x, y; } vi_t;
enum { CORNER_TO_CORNER, CORNER_TO_MIDPOINT, MIDPOINT_TO_CORNER };
static const int edge_dims[4] = {0, 1, 0, 1};
static const int shift_power[3][3] = {{0, -1, -1}, {1, 0, 0}, {1, 0, 0}};

static int Side(const dispsurf_t *d) { return (1 << d->power) + 1; }
static int VI(const dispsurf_t *d, vi_t v) { return v.y * Side(d) + v.x; }
static int Comp(vi_t v, int dim) { return dim ? v.y : v.x; }
static void SetComp(vi_t *v, int dim, int val) { if (dim) v->y = val; else v->x = val; }

static vi_t CornerIndex(const dispsurf_t *d, int corner) {
    int e = Side(d) - 1;
    static const int cx[4] = {0, 0, 1, 1}, cy[4] = {0, 1, 1, 0};   /* lower left, upper left, upper right, lower right */
    return (vi_t){cx[corner] * e, cy[corner] * e};
}

static vi_t EdgeMidPoint(const dispsurf_t *d, int edge) {
    int e = Side(d) - 1, m = Side(d) / 2;
    static const vi_t f[4] = {{0, -1}, {-1, 1}, {1, -1}, {-1, 0}};    /* -1: the midpoint, 1: the far end */
    vi_t r = f[edge];
    r.x = r.x == -1 ? m : r.x * e;
    r.y = r.y == -1 ? m : r.y * e;
    return r;
}

static void SetupSpan(const dispsurf_t *d, int iedge, int span, vi_t *start, vi_t *end) {
    int fd = !edge_dims[iedge], mid = Side(d) / 2;
    *start = CornerIndex(d, iedge);
    *end = CornerIndex(d, (iedge + 1) & 3);
    int *s = fd ? &start->y : &start->x, *e = fd ? &end->y : &end->x;
    if (iedge == 2 || iedge == 3) {          /* right, bottom: the corners run the other way */
        if (span == CORNER_TO_MIDPOINT) *s = mid;
        else if (span == MIDPOINT_TO_CORNER) *e = mid;
    } else {
        if (span == CORNER_TO_MIDPOINT) *e = mid;
        else if (span == MIDPOINT_TO_CORNER) *s = mid;
    }
}

static void TransformIntoSubNeighbor(const dispsurf_t *d, const subneighbor_t *s, int iedge, vi_t node, vi_t *out) {
    vi_t ss, se, ds, de;
    SetupSpan(d, iedge, s->span, &ss, &se);
    const dispsurf_t *nb = &dispsurfs[s->neighbor];
    int nbedge = (iedge + 2 + s->orientation) & 3;
    SetupSpan(nb, nbedge, s->nbspan, &de, &ds);
    int fd = !edge_dims[iedge];
    int fixed = ((Comp(node, fd) - Comp(ss, fd)) * (1 << 16)) / (Comp(se, fd) - Comp(ss, fd));
    int nbdim = edge_dims[nbedge];
    SetComp(out, nbdim, Comp(ds, nbdim));
    SetComp(out, !nbdim, Comp(ds, !nbdim) + ((Comp(de, !nbdim) - Comp(ds, !nbdim)) * fixed) / (1 << 16));
}

static void RotateVertIncrement(int orient, vi_t in, vi_t *out) {
    if (orient == 0) *out = in;
    else if (orient == 1) out->x = in.y, out->y = -in.x;
    else if (orient == 2) out->x = -in.x, out->y = -in.y;
    else out->x = -in.y, out->y = in.x;
}

/* CDispSubEdgeIterator, started touching the corners */
typedef struct {
    vi_t index, inc, nbindex, nbinc;
    int end, freedim;
} subiter_t;

static int SubIterStart(subiter_t *it, const dispsurf_t *d, const subneighbor_t *s, int iedge, int isub) {
    memset(it, 0, sizeof(*it));
    if (s->neighbor == 0xFFFF) return 0;
    int ed = edge_dims[iedge], fd = !ed, side = Side(d);
    it->freedim = fd;
    const dispsurf_t *nb = &dispsurfs[s->neighbor];
    static const int sidelen_mul[4] = {0, 1, 1, 0};
    vi_t my = {0, 0}, tmp = {0, 0}, myinc = {0, 0};
    SetComp(&my, ed, sidelen_mul[iedge] * (side - 1));
    SetComp(&my, fd, side / 2 * isub);
    TransformIntoSubNeighbor(d, s, iedge, my, &it->nbindex);
    int mypow = d->power, nbpow = nb->power + shift_power[s->span][s->nbspan];
    if (nbpow > mypow) {
        SetComp(&myinc, fd, 1);
        SetComp(&tmp, fd, 1 << (nbpow - mypow));
    } else {
        SetComp(&myinc, fd, 1 << (mypow - nbpow));
        SetComp(&tmp, fd, 1);
    }
    RotateVertIncrement(s->orientation, tmp, &it->nbinc);
    it->end = s->span == CORNER_TO_MIDPOINT ? side >> 1 : side - 1;
    it->index = my;
    it->inc = myinc;
    /* touch the corners: start one step back, end one step further */
    it->index.x -= it->inc.x, it->index.y -= it->inc.y;
    it->nbindex.x -= it->nbinc.x, it->nbindex.y -= it->nbinc.y;
    it->end += Comp(it->inc, fd);
    return 1;
}

static int SubIterNext(subiter_t *it) {
    it->index.x += it->inc.x, it->index.y += it->inc.y;
    it->nbindex.x += it->nbinc.x, it->nbindex.y += it->nbinc.y;
    return Comp(it->index, it->freedim) < it->end;
}

static int SubIterIsLast(const subiter_t *it) { return Comp(it->index, it->freedim) + Comp(it->inc, it->freedim) >= it->end; }

static int FindNeighborCornerVert(const dispsurf_t *nb, const vec3_t test) {
    int best = 0;
    float bestd = 1e24f;
    for (int c = 0; c < 4; c++) {
        vec3_t delta;
        VectorSubtract(nb->verts[VI(nb, CornerIndex(nb, c))], test, delta);
        float dist = sqrtf(delta[0] * delta[0] + delta[1] * delta[1] + delta[2] * delta[2]);
        if (dist < bestd) best = c, bestd = dist;
    }
    return bestd <= 0.1f ? best : -1;
}

static int Valid(int i) { return dispsurfs[i].face >= 0 && dispsurfs[i].normals; }

static void BlendTJuncs(void) {
    for (int i = 0; i < numdispsurfs; i++) {
        if (!Valid(i)) continue;
        dispsurf_t *d = &dispsurfs[i];
        for (int e = 0; e < 4; e++) {
            const edgeneighbor_t *edge = &dispinfos[i].edge[e];
            if (edge->sub[0].neighbor == 0xFFFF || edge->sub[1].neighbor == 0xFFFF) continue;
            int mid = VI(d, EdgeMidPoint(d, e));
            dispsurf_t *n1 = &dispsurfs[edge->sub[0].neighbor], *n2 = &dispsurfs[edge->sub[1].neighbor];
            int c1 = FindNeighborCornerVert(n1, d->verts[mid]), c2 = FindNeighborCornerVert(n2, d->verts[mid]);
            if (c1 == -1 || c2 == -1) continue;
            int v1 = VI(n1, CornerIndex(n1, c1)), v2 = VI(n2, CornerIndex(n2, c2));
            vec3_t avg;
            VectorCopy(d->normals[mid], avg);
            VectorAdd(avg, n1->normals[v1], avg);
            VectorAdd(avg, n2->normals[v2], avg);
            VectorNormalize(avg);
            VectorCopy(avg, d->normals[mid]);
            VectorCopy(avg, n1->normals[v1]);
            VectorCopy(avg, n2->normals[v2]);
        }
    }
}

static void BlendCorners(void) {
    int nbs[512], nbvert[512];
    for (int i = 0; i < numdispsurfs; i++) {
        if (!Valid(i)) continue;
        dispsurf_t *d = &dispsurfs[i];
        int n = 0;
        for (int c = 0; c < 4; c++)
            for (int k = 0; k < dispinfos[i].corner[c].count && k < 4; k++)
                if (n < 512) nbs[n++] = dispinfos[i].corner[c].n[k];
        for (int e = 0; e < 4; e++)
            for (int s = 0; s < 2; s++)
                if (dispinfos[i].edge[e].sub[s].neighbor != 0xFFFF && n < 512) nbs[n++] = dispinfos[i].edge[e].sub[s].neighbor;
        for (int c = 0; c < 4; c++) {
            int cv = VI(d, CornerIndex(d, c));
            vec3_t avg;
            VectorCopy(d->normals[cv], avg);
            for (int k = 0; k < n; k++) {
                dispsurf_t *nb = &dispsurfs[nbs[k]];
                int nc = FindNeighborCornerVert(nb, d->verts[cv]);
                if (nc == -1) nbvert[k] = -1;
                else {
                    nbvert[k] = VI(nb, CornerIndex(nb, nc));
                    VectorAdd(avg, nb->normals[nbvert[k]], avg);
                }
            }
            VectorNormalize(avg);
            VectorCopy(avg, d->normals[cv]);
            for (int k = 0; k < n; k++)
                if (nbvert[k] != -1) VectorCopy(avg, dispsurfs[nbs[k]].normals[nbvert[k]]);
        }
    }
}

static void BlendEdges(void) {
    for (int i = 0; i < numdispsurfs; i++) {
        if (!Valid(i)) continue;
        dispsurf_t *d = &dispsurfs[i];
        for (int e = 0; e < 4; e++) {
            for (int s = 0; s < 2; s++) {
                const subneighbor_t *sub = &dispinfos[i].edge[e].sub[s];
                if (sub->neighbor == 0xFFFF) continue;
                dispsurf_t *nb = &dispsurfs[sub->neighbor];
                int ed = edge_dims[e];
                subiter_t it;
                SubIterStart(&it, d, sub, e, s);
                SubIterNext(&it);
                vi_t prev = it.index;
                while (SubIterNext(&it)) {
                    int cur = VI(d, it.index);
                    if (!SubIterIsLast(&it)) {
                        int nv = VI(nb, it.nbindex);
                        vec3_t avg;
                        VectorAdd(d->normals[cur], nb->normals[nv], avg);
                        VectorNormalize(avg);
                        VectorCopy(avg, d->normals[cur]);
                        VectorCopy(avg, nb->normals[nv]);
                    }
                    /* the vertices in between (this side finer than the neighbour's) */
                    int a = Comp(prev, !ed), b = Comp(it.index, !ed);
                    for (int t = a + 1; t < b; t++) {
                        float pct = (float)(t - a) / (float)(b - a);
                        const float *n0 = d->normals[VI(d, prev)], *n1 = d->normals[cur];
                        vec3_t nrm;
                        for (int k = 0; k < 3; k++) nrm[k] = n0[k] + (n1[k] - n0[k]) * pct;
                        VectorNormalize(nrm);
                        vi_t tw;
                        SetComp(&tw, ed, Comp(it.index, ed));
                        SetComp(&tw, !ed, t);
                        VectorCopy(nrm, d->normals[VI(d, tw)]);
                    }
                    prev = it.index;
                }
            }
        }
    }
}

static void SmoothNeighboringDispSurfNormals(void) {
    BlendTJuncs();
    BlendCorners();
    BlendEdges();
}

/* ------------------------------------------------------------------ points on the surface (CVRADDispColl) */
/* (u, v) in [0, 1] across the base quad -> the point on the full-detail triangle there, pushed along the
 * triangle's normal */
void DispUVToSurfPoint(const dispsurf_t *d, float u, float v, float push, vec3_t out) {
    if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) return;
    int w = (1 << d->power) + 1, h = w;
    float fu = u * (float)(w - 1.000001f), fv = v * (float)(h - 1.000001f);
    int su = (int)fu, sv = (int)fv;
    int nu = su + 1 == w ? su : su + 1, nv = sv + 1 == h ? sv : sv + 1;
    float fracu = fu - (float)su, fracv = fv - (float)sv;
    const vec3_t *V = d->verts;
    vec3_t eu, ev, n;
    int base;
    float a, b;
    int flip;                                /* normal = eu x ev (0) or ev x eu (1) */
    if ((sv * w + su) % 2 == 1) {            /* top left to bottom right */
        if (fracu + fracv >= 1.0f + 0.001f) {          /* (TRIEDGE_EPSILON) */
            int i0 = nv * w + su, i1 = nv * w + nu, i2 = sv * w + nu;
            VectorSubtract(V[i0], V[i1], eu);
            VectorSubtract(V[i2], V[i1], ev);
            base = i1, a = 1.0f - fracu, b = 1.0f - fracv;
        } else {
            int i0 = sv * w + su, i1 = nv * w + su, i2 = sv * w + nu;
            VectorSubtract(V[i2], V[i0], eu);
            VectorSubtract(V[i1], V[i0], ev);
            base = i0, a = fracu, b = fracv;
        }
        flip = 0;
    } else {                                 /* bottom left to top right */
        if (fracu < fracv) {
            int i0 = sv * w + su, i1 = nv * w + su, i2 = nv * w + nu;
            VectorSubtract(V[i2], V[i1], eu);
            VectorSubtract(V[i0], V[i1], ev);
            base = i1, a = fracu, b = 1.0f - fracv;
        } else {
            int i0 = sv * w + su, i1 = nv * w + nu, i2 = sv * w + nu;
            VectorSubtract(V[i0], V[i2], eu);
            VectorSubtract(V[i1], V[i2], ev);
            base = i2, a = 1.0f - fracu, b = fracv;
        }
        flip = 1;
    }
    for (int k = 0; k < 3; k++) out[k] = (V[base][k] + eu[k] * a) + ev[k] * b;
    if (push != 0.0f) {
        if (flip) Cross(ev, eu, n);
        else Cross(eu, ev, n);
        VectorNormalize(n);
        for (int k = 0; k < 3; k++) out[k] += n[k] * push;
    }
}

/* the vertex normals blended bilinearly (each step renormalised) */
void DispUVToSurfNormal(const dispsurf_t *d, float u, float v, vec3_t out) {
    if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) return;
    int w = (1 << d->power) + 1, h = w;
    float fu = u * (float)(w - 1.000001f), fv = v * (float)(h - 1.000001f);
    int su = (int)fu, sv = (int)fv;
    int nu = su + 1 == w ? su : su + 1, nv = sv + 1 == h ? sv : sv + 1;
    float fracu = fu - (float)su, fracv = fv - (float)sv;
    const float *n0 = d->normals[sv * w + su], *n1 = d->normals[nv * w + su], *n2 = d->normals[nv * w + nu],
                *n3 = d->normals[sv * w + nu];
    vec3_t b0, b1;
    for (int k = 0; k < 3; k++) b0[k] = n0[k] * (1.0f - fracu) + n3[k] * fracu;
    VectorNormalize(b0);
    for (int k = 0; k < 3; k++) b1[k] = n1[k] * (1.0f - fracu) + n2[k] * fracu;
    VectorNormalize(b1);
    for (int k = 0; k < 3; k++) out[k] = b0[k] * (1.0f - fracv) + b1[k] * fracv;
    VectorNormalize(out);
}

void LoadDisplacements(void) {
    const ddispinfo_t *di = (const ddispinfo_t *)lumps[LUMP_DISPINFO].data;
    dispinfos = di;
    numdispsurfs = lumps[LUMP_DISPINFO].len / (int)sizeof(ddispinfo_t);
    if (lumps[LUMP_DISPINFO].len % (int)sizeof(ddispinfo_t)) Error("dispinfo lump has an odd size");
    const dispvert_t *verts = (const dispvert_t *)lumps[LUMP_DISP_VERTS].data;
    dispsurfs = xalloc(sizeof(dispsurf_t) * (numdispsurfs + 1));
    for (int i = 0; i < numdispsurfs; i++) dispsurfs[i].face = -1;
    for (int f = 0; f < numfaces; f++) {
        const dface_t *face = &g_pFaces[f];
        if (face->dispinfo == -1) continue;
        if (face->dispinfo < 0 || face->dispinfo >= numdispsurfs) Error("face %d: displacement %d isn't in the map", f, face->dispinfo);
        dispsurf_t *d = &dispsurfs[face->dispinfo];
        const ddispinfo_t *info = &di[face->dispinfo];
        if (info->power < 2 || info->power > 4) Error("displacement %d: power %d (2 to 4)", face->dispinfo, info->power);
        if (info->vert_start < 0 || (long long)info->vert_start + ((1 << info->power) + 1) * ((1 << info->power) + 1) >
                                        (long long)(lumps[LUMP_DISP_VERTS].len / (int)sizeof(dispvert_t)))
            Error("displacement %d: its vertexes run past the lump", face->dispinfo);
        d->face = f;
        d->power = info->power;
        d->contents = info->contents;
        vec3_t pts[4];
        for (int k = 0; k < 4; k++) VectorCopy(dvertexes[EdgeVertex(face, k)].point, pts[k]);
        int start = -1;
        float best = 999999999.0f;
        for (int k = 0; k < 4; k++) {
            vec3_t seg;
            VectorSubtract(info->startpos, pts[k], seg);
            float d2 = LengthSqr(seg);
            if (d2 < best) {
                best = d2;
                start = k;
            }
        }
        for (int k = 0; k < 4; k++) VectorCopy(pts[(k + start) % 4], d->points[k]);
        int n = ((1 << d->power) + 1) * ((1 << d->power) + 1);
        d->numverts = n;
        d->verts = xalloc(sizeof(vec3_t) * n);
        d->flat = xalloc(sizeof(vec3_t) * n);
        d->alpha = xalloc(sizeof(float) * n);
        GenerateDispSurf(d, verts + info->vert_start);
        GenerateDispSurfNormals(d);
        CalcSampleRadius2(d, face);
    }
    SmoothNeighboringDispSurfNormals();
}

/* the full-detail triangles, as CCoreDispInfo's collision surface lists them */
int DispTriangles(const dispsurf_t *d, unsigned short (*tris)[3]) {
    int w = (1 << d->power) + 1, n = 0;
    for (int v = 0; v < w - 1; v++) {
        for (int u = 0; u < w - 1; u++) {
            int ndx = v * w + u;
            if (ndx % 2 == 1) {        /* top left to bottom right */
                tris[n][0] = ndx, tris[n][1] = ndx + w, tris[n][2] = ndx + 1, n++;
                tris[n][0] = ndx + 1, tris[n][1] = ndx + w, tris[n][2] = ndx + w + 1, n++;
            } else {                   /* bottom left to top right */
                tris[n][0] = ndx, tris[n][1] = ndx + w, tris[n][2] = ndx + w + 1, n++;
                tris[n][0] = ndx, tris[n][1] = ndx + w + 1, tris[n][2] = ndx + 1, n++;
            }
        }
    }
    return n;
}

void AddDispsForRayTrace(void) {
    unsigned short tris[512][3];
    for (int i = 0; i < numdispsurfs; i++) {
        const dispsurf_t *d = &dispsurfs[i];
        if (d->face < 0 || !(d->contents & MASK_OPAQUE)) continue;
        int n = DispTriangles(d, tris);
        for (int t = 0; t < n; t++)
            RT_AddTriangle(TRACE_ID_OPAQUE, d->verts[tris[t][0]], d->verts[tris[t][1]], d->verts[tris[t][2]]);
    }
}
