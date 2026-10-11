/* hlvvis: Hammerless's visibility compiler, a drop-in for L4D2's vvis.exe.
 *
 *   hlvvis [-fast] [-exact] [-threads N] [-radius_override R] [-game <dir>] <map>     (reads <map>.bsp and <map>.prt, writes <map>.bsp)
 *
 * By default the exact pass merges windows (see "merged-window flow" below): several times faster, about 1% looser,
 * never hiding a pair seen through a real opening. -exact runs vvis's own path search and gives its output.
 *
 * The method is the portal-flow visibility id Software published with Quake's vis tool (GPL): every
 * one-way portal floods through chains of portals, clipping what can be seen at each step with
 * separating planes. The details that make the output byte-identical to L4D2's vvis (epsilon 0.01
 * compared in double, 12-point chop windings, the source-side radius test, portal order by
 * might-see count, the symmetric crosscheck, the compression, the file layout) were measured
 * against vvis's own output; this is our code.
 *
 * Results match vvis on the same map within vvis's own run-to-run variation: with several threads, a
 * portal sometimes uses a neighbour's finished result and sometimes its rough bound, which can flip a
 * borderline pair or two (vvis does the same). -threads 1 is fully repeatable.
 *
 * Radial visibility (an env_fog_controller with a far Z): like L4D2's vvis, the rough pass also drops
 * portals farther than the fog's far Z (its nearest point from the source's centre, then a sampled
 * distance between the two windings), and every leaf is flagged radial. Same float operations as vvis.
 *
 * Speed (on top of the same method): 64-bit visibility words limited to their non-empty range,
 * separating planes built once per step and 4 at a time (SSE2, same float operations), chops 4 points
 * at a time, and steps that can't reveal anything new beyond their opening not descended into.
 * Two more speed-ups were measured and left out on purpose: exploring each cluster's portals
 * narrowest-first and handing branches of big portals to idle threads. Neither changes a portal's
 * result, but both change when portals finish, and with threads that decides borderline pairs
 * differently from vvis's usual timing (a heavy map: 26 cells off vvis instead of 0-4).
 *
 * Build: python native/build.py   (zig cc; -ffp-contract=off keeps the float arithmetic exact)
 */
#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <emmintrin.h>

static void fail(const char *fmt, const char *arg) {
    printf("Error: ");
    printf(fmt, arg);
    printf("\n");
    fflush(stdout);
    exit(1);
}
static void *xmalloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) fail("out of memory%s", "");
    return p;
}
static void *xcalloc(size_t c, size_t n) {
    void *p = calloc(c ? c : 1, n ? n : 1);
    if (!p) fail("out of memory%s", "");
    return p;
}

/* ================================================================== geometry */
static const double ON_EPS = 0.01;        /* compared as double, like vvis (float d > 0.01) */
static float EPS_UP;                      /* d > 0.01  <=>  d >= EPS_UP (the first float above 0.01) */
static float LEN_LO;                      /* len < 0.01  <=>  len <= LEN_LO (the last float below 0.01) */
#define MAXPTS 64                         /* winding buffers (merged hulls) */
static int g_maxpts = 12;                 /* chopped windings: a chop needing more keeps its input (vvis: 12) */
#define MAXIN 64                          /* portal windings as read */

typedef struct { float x, y, z; } V;
typedef struct { V n; float d; } Plane;
typedef struct { int n; V p[]; } Win;
typedef struct { int n; V p[MAXPTS]; } WinBuf;   /* same layout, fixed size */

static inline float dotv(V a, V b) { return a.x * b.x + a.y * b.y + a.z * b.z; }

typedef struct {
    Win *w;
    Plane pl;              /* normal points into `leaf` */
    int leaf;              /* the cluster it leads into */
    V origin; float radius;
    uint8_t *front, *flood, *vis;   /* PW 64-bit words each */
    int flo, fhi;          /* non-empty word range of flood */
    int nmight;
    volatile LONG status;  /* 0 waiting, 1 working, 2 done */
} Portal;

static int NPD, NCL, PW;   /* one-way portals, clusters, 64-bit words per portal bitset */
static Portal *P;
static int *lf_first, *lf_count, *lf_list;   /* portals leaving each cluster */

typedef struct Stack Stack;
struct Stack {
    Stack *next;
    Win *source, *pass;
    Plane portalplane;
    uint64_t *mightsee; int lo, hi;    /* words lo..hi-1 may be non-zero */
    WinBuf wins[3];
    int freew[3];
};

static Win *alloc_w(Stack *s) {
    for (int i = 0; i < 3; i++) if (s->freew[i]) { s->freew[i] = 0; return (Win *)&s->wins[i]; }
    fail("winding stack overflow%s", "");
    return NULL;
}
static void free_w(Win *w, Stack *s) {
    int i = (int)((WinBuf *)w - s->wins);
    if (i >= 0 && i < 3) s->freew[i] = 1;   /* windings not from this frame aren't freed */
}

/* keep the part of `in` in front of `split` (points on the plane are kept) */
static Win *chop(Win *in, Stack *s, const Plane *split) {
    float dists[MAXIN + 4]; int sides[MAXIN + 4];
    const int n = in->n;
    const __m128 nx = _mm_set1_ps(split->n.x), ny = _mm_set1_ps(split->n.y), nz = _mm_set1_ps(split->n.z),
                 sd = _mm_set1_ps(split->d), up = _mm_set1_ps(EPS_UP), dn = _mm_set1_ps(-EPS_UP);
    uint64_t fm = 0, bm = 0;
    for (int i0 = 0; i0 < n; i0 += 4) {
        int c = n - i0 < 4 ? n - i0 : 4;
        const V *q = in->p + i0;
        __m128 X = _mm_set_ps(c > 3 ? q[3].x : 0, c > 2 ? q[2].x : 0, c > 1 ? q[1].x : 0, q[0].x);
        __m128 Y = _mm_set_ps(c > 3 ? q[3].y : 0, c > 2 ? q[2].y : 0, c > 1 ? q[1].y : 0, q[0].y);
        __m128 Z = _mm_set_ps(c > 3 ? q[3].z : 0, c > 2 ? q[2].z : 0, c > 1 ? q[1].z : 0, q[0].z);
        __m128 d = _mm_sub_ps(_mm_add_ps(_mm_add_ps(_mm_mul_ps(X, nx), _mm_mul_ps(Y, ny)), _mm_mul_ps(Z, nz)), sd);
        _mm_storeu_ps(dists + i0, d);
        int lanes = (1 << c) - 1;
        fm |= (uint64_t)(_mm_movemask_ps(_mm_cmpge_ps(d, up)) & lanes) << i0;
        bm |= (uint64_t)(_mm_movemask_ps(_mm_cmple_ps(d, dn)) & lanes) << i0;
    }
    if (!bm) return in;                                  /* all in front */
    if (!fm) { free_w(in, s); return NULL; }            /* all behind */
    for (int i = 0; i < n; i++) sides[i] = (fm >> i) & 1 ? 0 : (bm >> i) & 1 ? 1 : 2;
    sides[n] = sides[0]; dists[n] = dists[0];
    Win *nw = alloc_w(s);
    nw->n = 0;
    for (int i = 0; i < n; i++) {
        V p1 = in->p[i];
        if (nw->n == g_maxpts) { free_w(nw, s); return in; }
        if (sides[i] == 2) { nw->p[nw->n++] = p1; continue; }
        if (sides[i] == 0) nw->p[nw->n++] = p1;
        if (sides[i + 1] == 2 || sides[i + 1] == sides[i]) continue;
        if (nw->n == g_maxpts) { free_w(nw, s); return in; }
        V p2 = in->p[(i + 1) % n], mid;
        float t = dists[i] / (dists[i] - dists[i + 1]);
        mid.x = split->n.x == 1 ? split->d : split->n.x == -1 ? -split->d : p1.x + t * (p2.x - p1.x);
        mid.y = split->n.y == 1 ? split->d : split->n.y == -1 ? -split->d : p1.y + t * (p2.y - p1.y);
        mid.z = split->n.z == 1 ? split->d : split->n.z == -1 ? -split->d : p1.z + t * (p2.z - p1.z);
        nw->p[nw->n++] = mid;
    }
    free_w(in, s);
    return nw;
}

/* Separating planes: through an edge of `source` and a point of `pass`, with source on one side and
 * pass on the other. Built 4 pass points at a time with exactly the float operations (and order) of
 * the one-at-a-time version, so planes and decisions are bit-identical. */
static inline __m128 dot4(__m128 nx, __m128 ny, __m128 nz, V p) {
    return _mm_add_ps(_mm_add_ps(_mm_mul_ps(_mm_set1_ps(p.x), nx), _mm_mul_ps(_mm_set1_ps(p.y), ny)),
                      _mm_mul_ps(_mm_set1_ps(p.z), nz));
}
static int sep_planes(const Win *source, const Win *pass, int flipclip, Plane *out) {
    int n = 0;
    const __m128 up = _mm_set1_ps(EPS_UP), dn = _mm_set1_ps(-EPS_UP), signbit = _mm_set1_ps(-0.0f);
    for (int i = 0; i < source->n; i++) {
        int l = (i + 1) % source->n;
        V si = source->p[i];
        __m128 V1x = _mm_set1_ps(source->p[l].x - si.x), V1y = _mm_set1_ps(source->p[l].y - si.y),
               V1z = _mm_set1_ps(source->p[l].z - si.z);
        for (int j0 = 0; j0 < pass->n; j0 += 4) {
            int cnt = pass->n - j0 < 4 ? pass->n - j0 : 4;
            float px[4] = {0}, py[4] = {0}, pz[4] = {0};
            for (int t = 0; t < cnt; t++) { px[t] = pass->p[j0 + t].x; py[t] = pass->p[j0 + t].y; pz[t] = pass->p[j0 + t].z; }
            __m128 Px = _mm_loadu_ps(px), Py = _mm_loadu_ps(py), Pz = _mm_loadu_ps(pz);
            __m128 v2x = _mm_sub_ps(Px, _mm_set1_ps(si.x)), v2y = _mm_sub_ps(Py, _mm_set1_ps(si.y)),
                   v2z = _mm_sub_ps(Pz, _mm_set1_ps(si.z));
            __m128 nx = _mm_sub_ps(_mm_mul_ps(V1y, v2z), _mm_mul_ps(V1z, v2y));
            __m128 ny = _mm_sub_ps(_mm_mul_ps(V1z, v2x), _mm_mul_ps(V1x, v2z));
            __m128 nz = _mm_sub_ps(_mm_mul_ps(V1x, v2y), _mm_mul_ps(V1y, v2x));
            __m128 len = _mm_add_ps(_mm_add_ps(_mm_mul_ps(nx, nx), _mm_mul_ps(ny, ny)), _mm_mul_ps(nz, nz));
            int valid = (~_mm_movemask_ps(_mm_cmple_ps(len, _mm_set1_ps(LEN_LO)))) & ((1 << cnt) - 1);
            if (!valid) continue;
            __m128d lo = _mm_cvtps_pd(len), hi = _mm_cvtps_pd(_mm_movehl_ps(len, len));
            lo = _mm_div_pd(_mm_set1_pd(1.0), _mm_sqrt_pd(lo));     /* 1 / sqrt in double, as vvis */
            hi = _mm_div_pd(_mm_set1_pd(1.0), _mm_sqrt_pd(hi));
            __m128 inv = _mm_movelh_ps(_mm_cvtpd_ps(lo), _mm_cvtpd_ps(hi));
            nx = _mm_mul_ps(nx, inv); ny = _mm_mul_ps(ny, inv); nz = _mm_mul_ps(nz, inv);
            __m128 dist = _mm_add_ps(_mm_add_ps(_mm_mul_ps(Px, nx), _mm_mul_ps(Py, ny)), _mm_mul_ps(Pz, nz));
            /* which side is the source on: its first decisive point (not the edge's two) */
            int decided = 0, flip = 0;
            for (int k = 0; k < source->n && decided != valid; k++) {
                if (k == i || k == l) continue;
                __m128 d = _mm_sub_ps(dot4(nx, ny, nz, source->p[k]), dist);
                int neg = _mm_movemask_ps(_mm_cmple_ps(d, dn)) & valid & ~decided;
                int pos = _mm_movemask_ps(_mm_cmpge_ps(d, up)) & valid & ~decided;
                flip |= pos;
                decided |= neg | pos;
            }
            valid &= decided;                                 /* planar with source: skip */
            if (!valid) continue;
            __m128 fm = _mm_castsi128_ps(_mm_set_epi32(flip & 8 ? -1 : 0, flip & 4 ? -1 : 0, flip & 2 ? -1 : 0,
                                                       flip & 1 ? -1 : 0));
            __m128 fsign = _mm_and_ps(fm, signbit);
            nx = _mm_xor_ps(nx, fsign); ny = _mm_xor_ps(ny, fsign); nz = _mm_xor_ps(nz, fsign);
            dist = _mm_xor_ps(dist, fsign);
            /* every other pass point on or in front, at least one in front */
            int bad = 0, front = 0;
            for (int k = 0; k < pass->n; k++) {
                __m128 d = _mm_sub_ps(dot4(nx, ny, nz, pass->p[k]), dist);
                int self = (k >= j0 && k < j0 + 4) ? (1 << (k - j0)) : 0;
                bad |= _mm_movemask_ps(_mm_cmple_ps(d, dn)) & ~self;
                front |= _mm_movemask_ps(_mm_cmpge_ps(d, up)) & ~self;
            }
            valid &= ~bad & front;
            if (!valid) continue;
            float ox[4], oy[4], oz[4], od[4];
            _mm_storeu_ps(ox, nx); _mm_storeu_ps(oy, ny); _mm_storeu_ps(oz, nz); _mm_storeu_ps(od, dist);
            for (int t = 0; t < cnt; t++) {
                if (!(valid & (1 << t))) continue;
                Plane pl = {{ox[t], oy[t], oz[t]}, od[t]};
                if (flipclip) { pl.n.x = -pl.n.x; pl.n.y = -pl.n.y; pl.n.z = -pl.n.z; pl.d = -pl.d; }
                out[n++] = pl;
            }
        }
    }
    return n;
}
static Win *apply_planes(const Plane *pl, int n, Win *target, Stack *s) {
    for (int i = 0; i < n; i++) {
        target = chop(target, s, &pl[i]);
        if (!target) return NULL;
    }
    return target;
}
/* the uncached path (source clipped for this target): the same planes, built on the spot */
static Win *clip_sep(Win *source, Win *pass, Win *target, int flipclip, Stack *s) {
    Plane *pl = _alloca(((size_t)source->n * pass->n + 4) * sizeof(Plane));
    int n = sep_planes(source, pass, flipclip, pl);
    return apply_planes(pl, n, target, s);
}

/* ================================================================== flow */
typedef struct { Portal *base; Stack head; } Thread;

static volatile LONG g_next;
static int *g_order;

static void leaf_flow(int leaf, Thread *th, Stack *prev) {
    Stack st;
    Plane *sep0 = NULL, *sep1 = NULL;
    int nsep0 = -1, nsep1 = 0;           /* planes for (prev->source, prev->pass), built on first use */
    prev->next = &st;
    st.next = NULL;
    uint64_t mightbuf[PW > 0 ? PW : 1];
    st.mightsee = mightbuf;
    uint8_t *vis = th->base->vis;
    const uint64_t *vis64 = (const uint64_t *)vis;
    const uint8_t *pm8 = (const uint8_t *)prev->mightsee;
    for (int i = 0; i < lf_count[leaf]; i++) {
        int pnum = lf_list[lf_first[leaf] + i];
        Portal *p = &P[pnum];
        if ((pnum >> 6) < prev->lo || (pnum >> 6) >= prev->hi || !(pm8[pnum >> 3] & (1 << (pnum & 7)))) continue;
        /* a finished portal's own result is a tighter bound than its rough one */
        const uint64_t *test = (const uint64_t *)(p->status == 2 ? p->vis : p->flood);
        uint64_t more = 0;
        int lo = prev->lo > p->flo ? prev->lo : p->flo, hi = prev->hi < p->fhi ? prev->hi : p->fhi;
        int nlo = hi, nhi = lo;
        for (int j = lo; j < hi; j++) {
            uint64_t m = prev->mightsee[j] & test[j];
            mightbuf[j] = m;
            more |= m & ~vis64[j];
            if (m) { if (j < nlo) nlo = j; nhi = j + 1; }
        }
        st.lo = nlo; st.hi = nhi;
        if (!more && (vis[pnum >> 3] & (1 << (pnum & 7)))) continue;   /* can't see anything new */
        int descend = more != 0;    /* nothing new beyond this opening: test it, don't go deeper */
        st.portalplane = p->pl;
        Plane back = {{-p->pl.n.x, -p->pl.n.y, -p->pl.n.z}, -p->pl.d};
        st.freew[0] = st.freew[1] = st.freew[2] = 1;
        {
            float d = dotv(p->origin, th->head.portalplane.n) - th->head.portalplane.d;
            if (d < -p->radius) continue;
            else if (d > p->radius) st.pass = p->w;
            else { st.pass = chop(p->w, &st, &th->head.portalplane); if (!st.pass) continue; }
        }
        {
            float d = dotv(th->base->origin, p->pl.n) - p->pl.d;
            if (d > th->base->radius) continue;
            else if (d < -th->base->radius) st.source = prev->source;
            else { st.source = chop(prev->source, &st, &back); if (!st.source) continue; }
        }
        if (!prev->pass) {                     /* the first opening can only be blocked if coplanar */
            vis[pnum >> 3] |= 1 << (pnum & 7);
            if (descend) leaf_flow(p->leaf, th, &st);
            continue;
        }
        if (st.source == prev->source) {
            if (nsep0 < 0) {
                size_t cap = (size_t)prev->source->n * prev->pass->n + 4;
                sep0 = _alloca(cap * sizeof(Plane)); sep1 = _alloca(cap * sizeof(Plane));
                nsep0 = sep_planes(prev->source, prev->pass, 0, sep0);
                nsep1 = sep_planes(prev->pass, prev->source, 1, sep1);
            }
            st.pass = apply_planes(sep0, nsep0, st.pass, &st);
            if (!st.pass) continue;
            st.pass = apply_planes(sep1, nsep1, st.pass, &st);
            if (!st.pass) continue;
        } else {
            st.pass = clip_sep(st.source, prev->pass, st.pass, 0, &st);
            if (!st.pass) continue;
            st.pass = clip_sep(prev->pass, st.source, st.pass, 1, &st);
            if (!st.pass) continue;
        }
        vis[pnum >> 3] |= 1 << (pnum & 7);
        if (descend) leaf_flow(p->leaf, th, &st);
    }
}

static double *g_pdur;                 /* seconds each portal took (for <map>.viscost) */
static volatile LONG g_done;
static LARGE_INTEGER g_freq;
static void portal_flow(int pi) {
    Portal *p = &P[pi];
    LARGE_INTEGER t0, t1;
    QueryPerformanceCounter(&t0);
    Thread th;
    memset(&th, 0, sizeof th);
    p->status = 1;
    th.base = p;
    th.head.source = p->w;
    th.head.pass = NULL;
    th.head.portalplane = p->pl;
    th.head.mightsee = (uint64_t *)p->flood; th.head.lo = p->flo; th.head.hi = p->fhi;
    leaf_flow(p->leaf, &th, &th.head);
    MemoryBarrier();
    p->status = 2;
    QueryPerformanceCounter(&t1);
    g_pdur[pi] = (double)(t1.QuadPart - t0.QuadPart) / g_freq.QuadPart;
    InterlockedIncrement(&g_done);
}

/* ================================================================== merged-window flow (the default)
   One (source, pass) state per portal per flow instead of one per path: a portal's source is the convex hull
   of every source that reached it (on the base portal's plane), its pass the hull of every pass (on its own
   plane), its might-see the union. A portal is re-expanded only when its state grew. Superset of the path
   search in exact arithmetic (bigger windows give bigger separator regions), so it never hides what the
   path search shows except where that search only sees through its own tolerance (measured: such pairs have no
   straight line through the portals at all). About 1% looser than vvis, a fraction of the work: a portal is
   reached a few times per flow instead of once per path (urban: 22M steps instead of 534M).
   A hull of more than MAXPTS points becomes the MAXPTS-gon of its supporting lines (a superset). A portal that
   keeps growing (MAX_GROW times) opens to the whole base and the whole portal (supersets: still safe). */
static int g_merge = 1;           /* 0: -exact (vvis's path search) */
typedef struct { int gen, queued, has, grows; WinBuf S, W; double areaS, areaW; } MState;
#define MAX_GROW 64
static volatile LONG64 g_capped;
static _Thread_local MState *t_ms;
static _Thread_local uint64_t *t_mm;      /* NPD rows of PW words: each portal's merged might-see */
static _Thread_local int *t_queue, t_mgen, t_qtail, t_qlen;   /* (a ring of NPD + 1: wrapped indices, no overflow) */
static volatile LONG64 g_msteps, g_mpush;

static double poly_area(const Win *w, V n) {
    double sx = 0, sy = 0, sz = 0;
    for (int i = 0; i < w->n; i++) {
        V p = w->p[i], q = w->p[(i + 1) % w->n];
        sx += (double)p.y * q.z - (double)p.z * q.y; sy += (double)p.z * q.x - (double)p.x * q.z; sz += (double)p.x * q.y - (double)p.y * q.x;
    }
    return fabs(sx * n.x + sy * n.y + sz * n.z) * 0.5;
}

/* convex hull of a's and b's points (coplanar, plane normal n), wound like `like` (around n the same way) */
static void hull2(const Win *a, const Win *b, V n, int ccw, WinBuf *out) {
    V pts[2 * MAXPTS]; int np = 0;
    for (int i = 0; i < a->n; i++) pts[np++] = a->p[i];
    for (int i = 0; i < b->n; i++) pts[np++] = b->p[i];
    V u = fabs(n.x) < 0.9f ? (V){0, -n.z, n.y} : (V){n.z, 0, -n.x};
    double ul = sqrt((double)u.x * u.x + (double)u.y * u.y + (double)u.z * u.z);
    double ux = u.x / ul, uy = u.y / ul, uz = u.z / ul;
    double vx = n.y * uz - n.z * uy, vy = n.z * ux - n.x * uz, vz = n.x * uy - n.y * ux;
    double px[2 * MAXPTS], py[2 * MAXPTS]; int idx[2 * MAXPTS];
    for (int i = 0; i < np; i++) { px[i] = pts[i].x * ux + pts[i].y * uy + pts[i].z * uz; py[i] = pts[i].x * vx + pts[i].y * vy + pts[i].z * vz; idx[i] = i; }
    for (int i = 1; i < np; i++) { int k = idx[i], j = i - 1;
        while (j >= 0 && (px[idx[j]] > px[k] || (px[idx[j]] == px[k] && py[idx[j]] > py[k]))) { idx[j + 1] = idx[j]; j--; }
        idx[j + 1] = k; }
    int h[4 * MAXPTS], m = 0;
    for (int pass = 0; pass < 2; pass++) {
        int start = m;
        for (int ii = 0; ii < np; ii++) {
            int i = pass ? idx[np - 1 - ii] : idx[ii];
            while (m >= start + 2) {
                int a0 = h[m - 2], b0 = h[m - 1];
                double cr = (px[b0] - px[a0]) * (py[i] - py[a0]) - (py[b0] - py[a0]) * (px[i] - px[a0]);
                if (cr <= 0) m--; else break;
            }
            h[m++] = i;
        }
        m--;
    }
    if (m > MAXPTS) {
        /* too many corners: the 64-gon of supporting lines in 64 directions around the hull (contains it) */
        static const int K = MAXPTS;
        double lx[MAXPTS], ly[MAXPTS], ld[MAXPTS];
        for (int k = 0; k < K; k++) {
            double a = 2 * 3.14159265358979323846 * k / K;
            lx[k] = cos(a); ly[k] = sin(a); ld[k] = -1e300;
            for (int i = 0; i < m; i++) { double d = px[h[i]] * lx[k] + py[h[i]] * ly[k]; if (d > ld[k]) ld[k] = d; }
            ld[k] += 1e-2;                                   /* (outside by more than float corners can drift) */
        }
        double ox = 0, oy = 0, oz = 0;                       /* the plane's offset along n */
        for (int i = 0; i < np; i++) { ox += pts[i].x; oy += pts[i].y; oz += pts[i].z; }
        ox /= np; oy /= np; oz /= np;
        double on = ox * n.x + oy * n.y + oz * n.z;
        out->n = K;
        for (int k = 0; k < K; k++) {                        /* corner k: lines k and k+1 meet (counter-clockwise) */
            int j = (k + 1) % K;
            double det = lx[k] * ly[j] - ly[k] * lx[j];
            double cu = (ld[k] * ly[j] - ly[k] * ld[j]) / det, cv = (lx[k] * ld[j] - ld[k] * lx[j]) / det;
            V q = {(float)(cu * ux + cv * vx + on * n.x), (float)(cu * uy + cv * vy + on * n.y),
                   (float)(cu * uz + cv * vz + on * n.z)};
            out->p[ccw ? k : K - 1 - k] = q;
        }
        return;
    }
    out->n = m;
    /* hull is counter-clockwise in (u, v): around u x v = n; flip if the original winding goes the other way */
    for (int i = 0; i < m; i++) out->p[i] = pts[h[ccw ? i : m - 1 - i]];
}

static int winding_ccw(const Win *w, V n) {
    double sx = 0, sy = 0, sz = 0;
    for (int i = 0; i < w->n; i++) {
        V p = w->p[i], q = w->p[(i + 1) % w->n];
        sx += (double)p.y * q.z - (double)p.z * q.y; sy += (double)p.z * q.x - (double)p.x * q.z; sz += (double)p.x * q.y - (double)p.y * q.x;
    }
    return sx * n.x + sy * n.y + sz * n.z > 0;
}

/* some point of a lies outside convex polygon h (both on the plane with normal n), by any margin */
static int outside_hull(const Win *a, const Win *h, V n) {
    double sx = 0, sy = 0, sz = 0;
    for (int i = 0; i < h->n; i++) {
        V p = h->p[i], q = h->p[(i + 1) % h->n];
        sx += (double)p.y * q.z - (double)p.z * q.y; sy += (double)p.z * q.x - (double)p.x * q.z; sz += (double)p.x * q.y - (double)p.y * q.x;
    }
    double o = sx * n.x + sy * n.y + sz * n.z;
    if (o == 0) return 1;
    for (int i = 0; i < h->n; i++) {
        V p = h->p[i], q = h->p[(i + 1) % h->n];
        double ex = (double)q.x - p.x, ey = (double)q.y - p.y, ez = (double)q.z - p.z;
        for (int k = 0; k < a->n; k++) {
            double ax = (double)a->p[k].x - p.x, ay = (double)a->p[k].y - p.y, az = (double)a->p[k].z - p.z;
            double c = (ey * az - ez * ay) * n.x + (ez * ax - ex * az) * n.y + (ex * ay - ey * ax) * n.z;
            double el = sqrt(ex * ex + ey * ey + ez * ez);
            if ((o > 0 && c < -0.01 * el * fabs(o) / (fabs(o) > 0 ? fabs(o) : 1)) || (o < 0 && c > 0.01 * el)) return 1;
        }
    }
    return 0;
}

/* w widened in its plane by d: contains every point within d of w. Worked in 2D doubles on w's convex hull (float
   windings with tiny edges aren't quite convex: their edge directions are noise): each hull edge moves out by d; a
   corner turning up to 90 degrees moves along its miter (at most d*sqrt(2)); a sharper one gets a bevel of two points
   tangent to the rounded corner (a convex polygon has at most 3). Too many points, or no area: the 64-gon of lines in
   64 directions, each d and a hair out from w's farthest point that way. Lifted back onto w's mean plane. */
static void inflate(const Win *w, V n, double d, WinBuf *out) {
    const int N = w->n;
    V u0 = fabs(n.x) < 0.9f ? (V){0, -n.z, n.y} : (V){n.z, 0, -n.x};
    double ul = sqrt((double)u0.x * u0.x + (double)u0.y * u0.y + (double)u0.z * u0.z);
    double ux = u0.x / ul, uy = u0.y / ul, uz = u0.z / ul;
    double vx = n.y * uz - n.z * uy, vy = n.z * ux - n.x * uz, vz = n.x * uy - n.y * ux;   /* v = n x u: (u, v) turns about n */
    double px[MAXPTS], py[MAXPTS], on = 0, sx = 0, sy = 0, sz = 0;
    int idx[MAXPTS];
    for (int i = 0; i < N; i++) {
        V q = w->p[i], r = w->p[(i + 1) % N];
        px[i] = q.x * ux + q.y * uy + q.z * uz; py[i] = q.x * vx + q.y * vy + q.z * vz; idx[i] = i;
        on += (double)q.x * n.x + (double)q.y * n.y + (double)q.z * n.z;
        sx += (double)q.y * r.z - (double)q.z * r.y; sy += (double)q.z * r.x - (double)q.x * r.z; sz += (double)q.x * r.y - (double)q.y * r.x;
    }
    on /= N;
    int ccw = sx * n.x + sy * n.y + sz * n.z >= 0;      /* w's own winding, kept */
    for (int i = 1; i < N; i++) { int k = idx[i], j = i - 1;
        while (j >= 0 && (px[idx[j]] > px[k] || (px[idx[j]] == px[k] && py[idx[j]] > py[k]))) { idx[j + 1] = idx[j]; j--; }
        idx[j + 1] = k; }
    int h[2 * MAXPTS + 2], m = 0;                       /* the hull, counter-clockwise in (u, v) */
    for (int pass = 0; pass < 2; pass++) {
        int start = m;
        for (int ii = 0; ii < N; ii++) {
            int i = pass ? idx[N - 1 - ii] : idx[ii];
            while (m >= start + 2) {
                int a0 = h[m - 2], b0 = h[m - 1];
                if ((px[b0] - px[a0]) * (py[i] - py[a0]) - (py[b0] - py[a0]) * (px[i] - px[a0]) <= 0) m--; else break;
            }
            h[m++] = i;
        }
        m--;
    }
    double cu[MAXPTS + 4], cv[MAXPTS + 4]; int c = 0;
    if (m >= 3) {
        double nxs[MAXPTS], nys[MAXPTS];                /* outward normal of hull edge i -> i+1 */
        int sharp = 0;
        for (int i = 0; i < m; i++) {
            double ex = px[h[(i + 1) % m]] - px[h[i]], ey = py[h[(i + 1) % m]] - py[h[i]], l = sqrt(ex * ex + ey * ey);
            nxs[i] = ey / l; nys[i] = -ex / l;
        }
        for (int i = 0; i < m; i++) { int a = (i + m - 1) % m; if (nxs[a] * nxs[i] + nys[a] * nys[i] < 0) sharp++; }
        if (m + sharp <= MAXPTS) {
            for (int i = 0; i < m; i++) {
                int a = (i + m - 1) % m;
                double X = px[h[i]], Y = py[h[i]], dot = nxs[a] * nxs[i] + nys[a] * nys[i];
                if (dot >= 0) {
                    double k = d / (1 + dot);
                    cu[c] = X + k * (nxs[a] + nxs[i]); cv[c++] = Y + k * (nys[a] + nys[i]);
                    continue;
                }
                double bx = nxs[a] + nxs[i], by = nys[a] + nys[i], bl = sqrt(bx * bx + by * by);
                if (bl < 1e-12) { bx = -nys[a]; by = nxs[a]; bl = 1; }      /* (turning right back: along edge a) */
                bx /= bl; by /= bl;
                double t = d * tan(acos(dot < -1 ? -1 : dot) / 4);
                double tx = -by, ty = bx;                                    /* across b; edge a's side first */
                if (tx * nxs[a] + ty * nys[a] < 0) { tx = -tx; ty = -ty; }
                cu[c] = X + d * bx + t * tx; cv[c++] = Y + d * by + t * ty;
                cu[c] = X + d * bx - t * tx; cv[c++] = Y + d * by - t * ty;
            }
        }
    }
    if (c == 0) {                                       /* the 64-gon */
        double lx[MAXPTS], ly[MAXPTS], ld[MAXPTS];
        for (int k = 0; k < MAXPTS; k++) {
            double ang = 2 * 3.14159265358979323846 * k / MAXPTS;
            lx[k] = cos(ang); ly[k] = sin(ang); ld[k] = -1e300;
            for (int i = 0; i < N; i++) { double dd = px[i] * lx[k] + py[i] * ly[k]; if (dd > ld[k]) ld[k] = dd; }
            ld[k] += d + 1e-2;
        }
        for (int k = 0; k < MAXPTS; k++) {
            int j = (k + 1) % MAXPTS;
            double det = lx[k] * ly[j] - ly[k] * lx[j];
            cu[c] = (ld[k] * ly[j] - ly[k] * ld[j]) / det; cv[c++] = (lx[k] * ld[j] - ld[k] * lx[j]) / det;
        }
    }
    out->n = c;
    for (int k = 0; k < c; k++)
        out->p[ccw ? k : c - 1 - k] = (V){(float)(cu[k] * ux + cv[k] * vx + on * n.x), (float)(cu[k] * uy + cv[k] * vy + on * n.y),
                                          (float)(cu[k] * uz + cv[k] * vz + on * n.z)};
}

static void m_merge(Thread *th, int pnum, Win *source, Win *pass, const uint64_t *might) {
    MState *ms = &t_ms[pnum];
    uint64_t *mm = t_mm + (size_t)pnum * PW;
    Portal *p = &P[pnum];
    int grew = 0, bits = 0;
    if (ms->gen != t_mgen) {
        ms->gen = t_mgen; ms->queued = 0; ms->has = 1; ms->grows = 0;
        memcpy(&ms->S, source, sizeof(int) + source->n * sizeof(V));
        memcpy(&ms->W, pass, sizeof(int) + pass->n * sizeof(V));
        ms->areaS = poly_area(source, th->base->pl.n); ms->areaW = poly_area(pass, p->pl.n);
        memcpy(mm, might, PW * 8);
        grew = 1;
    } else {
        for (int k = 0; k < PW; k++) { uint64_t nb = might[k] & ~mm[k]; if (nb) { mm[k] |= nb; grew = bits = 1; } }
        /* grown: any new point outside the old hull at all (not by an area threshold: a sliver counts) */
        if (outside_hull(source, (Win *)&ms->S, th->base->pl.n)) {
            WinBuf ns; hull2((Win *)&ms->S, source, th->base->pl.n, winding_ccw(th->base->w, th->base->pl.n), &ns);
            ms->S = ns; grew = 1;
        }
        if (outside_hull(pass, (Win *)&ms->W, p->pl.n)) {
            WinBuf nw; hull2((Win *)&ms->W, pass, p->pl.n, winding_ccw(p->w, p->pl.n), &nw);
            ms->W = nw; grew = 1;
        }
    }
    if (grew && ++ms->grows > MAX_GROW && ms->grows < 1000000) {
        /* (it keeps growing: open it fully, once; nothing can grow it after that) */
        memcpy(&ms->S, th->base->w, sizeof(int) + th->base->w->n * sizeof(V));
        memcpy(&ms->W, p->w, sizeof(int) + p->w->n * sizeof(V));
        ms->grows = 1000000;
        InterlockedIncrement64(&g_capped);
    } else if (ms->grows >= 1000000) {
        /* (opened: its windows can't grow any more, but new might-see bits must still be passed on; bits are only
           ever added, so this ends) */
        grew = bits;
        ms->grows = 1000001;
    }
    if (grew && !ms->queued) {             /* (a ring: each portal is in it at most once at a time) */
        ms->queued = 1; t_queue[t_qtail] = pnum; t_qtail = (t_qtail + 1) % (NPD + 1); t_qlen++; InterlockedIncrement64(&g_mpush);
    }
}

static void merged_flow(int pi) {
    Portal *b = &P[pi];
    LARGE_INTEGER t0, t1;
    QueryPerformanceCounter(&t0);
    b->status = 1;
    Thread th; memset(&th, 0, sizeof th);
    th.base = b; th.head.source = b->w; th.head.pass = NULL; th.head.portalplane = b->pl;
    if (!t_ms) {
        t_ms = calloc(NPD, sizeof(MState));
        t_mm = malloc((size_t)NPD * PW * 8);
        t_queue = malloc(((size_t)NPD + 1) * sizeof(int));
    }
    t_mgen++;
    t_qtail = t_qlen = 0;
    int qhead = 0;
    uint8_t *vis = b->vis;
    const uint64_t *vis64 = (const uint64_t *)vis;
    uint64_t *might = malloc(PW * 8), *cur = malloc(PW * 8);
    /* first openings: as the path search (no separators yet) */
    {
        const uint64_t *bf = (const uint64_t *)b->flood;
        int leaf = b->leaf;
        for (int i = 0; i < lf_count[leaf]; i++) {
            int pnum = lf_list[lf_first[leaf] + i];
            Portal *p = &P[pnum];
            if (!((bf[pnum >> 6] >> (pnum & 63)) & 1)) continue;
            const uint64_t *test = (const uint64_t *)(p->status == 2 ? p->vis : p->flood);
            uint64_t more = 0;
            for (int k = 0; k < PW; k++) { might[k] = bf[k] & test[k]; more |= might[k] & ~vis64[k]; }
            Stack st; memset(&st, 0, sizeof st); st.freew[0] = st.freew[1] = st.freew[2] = 1;
            Plane back = {{-p->pl.n.x, -p->pl.n.y, -p->pl.n.z}, -p->pl.d};
            float d = dotv(p->origin, th.head.portalplane.n) - th.head.portalplane.d;
            if (d < -p->radius) continue;
            Win *pass = d > p->radius ? p->w : chop(p->w, &st, &th.head.portalplane);
            if (!pass) continue;
            d = dotv(b->origin, p->pl.n) - p->pl.d;
            if (d > b->radius) continue;
            Win *source = d < -b->radius ? b->w : chop(b->w, &st, &back);
            if (!source) continue;
            vis[pnum >> 3] |= 1 << (pnum & 7);
            InterlockedIncrement64(&g_msteps);
            if (more) m_merge(&th, pnum, source, pass, might);
        }
    }
    while (t_qlen > 0) {
        int xn = t_queue[qhead]; qhead = (qhead + 1) % (NPD + 1); t_qlen--;
        MState *xs = &t_ms[xn];
        xs->queued = 0;
        WinBuf S, W;                       /* (a snapshot: merging may grow them meanwhile; widened, see inflate) */
        inflate((Win *)&xs->S, b->pl.n, 0.02, &S);
        inflate((Win *)&xs->W, P[xn].pl.n, 0.02, &W);
        memcpy(cur, t_mm + (size_t)xn * PW, PW * 8);
        Portal *x = &P[xn];
        int leaf = x->leaf;
        Plane sep0[4 * MAXPTS * MAXPTS / 4 + 8], sep1[4 * MAXPTS * MAXPTS / 4 + 8];
        int nsep0 = -1, nsep1 = 0;
        for (int i = 0; i < lf_count[leaf]; i++) {
            int pnum = lf_list[lf_first[leaf] + i];
            Portal *p = &P[pnum];
            if (!((cur[pnum >> 6] >> (pnum & 63)) & 1)) continue;
            const uint64_t *test = (const uint64_t *)(p->status == 2 ? p->vis : p->flood);
            uint64_t more = 0;
            for (int k = 0; k < PW; k++) { might[k] = cur[k] & test[k]; more |= might[k] & ~vis64[k]; }
            if (!more && (vis[pnum >> 3] & (1 << (pnum & 7)))) continue;
            Stack st; memset(&st, 0, sizeof st); st.freew[0] = st.freew[1] = st.freew[2] = 1;
            Plane back = {{-p->pl.n.x, -p->pl.n.y, -p->pl.n.z}, -p->pl.d};
            float d = dotv(p->origin, th.head.portalplane.n) - th.head.portalplane.d;
            if (d < -p->radius) continue;
            Win *pass = d > p->radius ? p->w : chop(p->w, &st, &th.head.portalplane);
            if (!pass) continue;
            d = dotv(b->origin, p->pl.n) - p->pl.d;
            if (d > b->radius) continue;
            Win *source = d < -b->radius ? (Win *)&S : chop((Win *)&S, &st, &back);
            if (!source) continue;
            if (source == (Win *)&S) {
                if (nsep0 < 0) {
                    nsep0 = sep_planes((Win *)&S, (Win *)&W, 0, sep0);
                    nsep1 = sep_planes((Win *)&W, (Win *)&S, 1, sep1);
                }
                pass = apply_planes(sep0, nsep0, pass, &st); if (!pass) continue;
                pass = apply_planes(sep1, nsep1, pass, &st); if (!pass) continue;
            } else {
                pass = clip_sep(source, (Win *)&W, pass, 0, &st); if (!pass) continue;
                pass = clip_sep((Win *)&W, source, pass, 1, &st); if (!pass) continue;
            }
            vis[pnum >> 3] |= 1 << (pnum & 7);
            InterlockedIncrement64(&g_msteps);
            if (more) m_merge(&th, pnum, source, pass, might);
        }
    }
    free(might); free(cur);
    MemoryBarrier();
    b->status = 2;
    QueryPerformanceCounter(&t1);
    g_pdur[pi] = (double)(t1.QuadPart - t0.QuadPart) / g_freq.QuadPart;
    InterlockedIncrement(&g_done);
}

static DWORD WINAPI flow_worker(LPVOID arg) {
    (void)arg;
    for (;;) {
        LONG k = InterlockedIncrement(&g_next) - 1;
        if (k >= NPD) break;
        if (g_merge) merged_flow(g_order[k]); else portal_flow(g_order[k]);
    }
    return 0;
}

/* ---- radial vis (L4D2): the distance between two windings, sampled. Each is cut into a fan of triangles
   from its first point (the second one's fan wraps all the way round, as vvis's does) and points are
   taken on every pair of triangles at barycentric steps of 0.25: the smallest squared distance found */
static int g_radial;
static double g_vis_radius2;      /* farz squared */
static float tri_dist2(const V *a, const V *b) {
    float best = FLT_MAX;
    for (float u = 0; 1.0f - u >= 0; u += 0.25f) {
        float c = 1.0f - u;
        float aux = a[0].x * u, auy = a[0].y * u, auz = a[0].z * u;
        float v = 0;
        do {
            float w = c - v;
            float px = (aux + a[1].x * v) + a[2].x * w;
            float py = (auy + a[1].y * v) + a[2].y * w;
            float pz = (auz + a[1].z * v) + a[2].z * w;
            for (float s = 0; 1.0f - s >= 0; s += 0.25f) {
                float c2 = 1.0f - s;
                float bsx = b[0].x * s, bsy = b[0].y * s, bsz = b[0].z * s;
                float t = 0;
                do {
                    float w2 = c2 - t;
                    float dx = px - ((bsx + b[1].x * t) + b[2].x * w2);
                    float dy = py - ((bsy + b[1].y * t) + b[2].y * w2);
                    float dz = pz - ((bsz + b[1].z * t) + b[2].z * w2);
                    float d = (dy * dy + dx * dx) + dz * dz;
                    if (d <= best) best = d;
                    t += 0.25f;
                } while (c2 >= t);
            }
            v += 0.25f;
        } while (c >= v);
    }
    return best;
}
static float winding_dist2(const Win *a, const Win *b) {
    float best = FLT_MAX;
    for (int i = 2; i < a->n; i++) {
        V ta[3] = {a->p[0], a->p[(i - 1) % a->n], a->p[i % a->n]};
        for (int j = 2; j < b->n + 2; j++) {
            V tb[3] = {b->p[0], b->p[(j - 1) % b->n], b->p[j % b->n]};
            float d = tri_dist2(ta, tb);
            if (d <= best) best = d;
        }
    }
    return best;
}
/* is portal t (seen from p) within the fog's far Z? */
static int within_radius(const Portal *p, const Portal *t) {
    double md = 1024000000.0;
    for (int k = 0; k < t->w->n; k++) {
        double dx = (double)t->w->p[k].x - p->origin.x, dy = (double)t->w->p[k].y - p->origin.y,
               dz = (double)t->w->p[k].z - p->origin.z;
        double d = (dx * dx + dy * dy) + dz * dz;
        if (d < md) md = d;
    }
    if (md <= g_vis_radius2) return 1;
    /* the samples lie on the windings, so they're no closer than the windings' bounding spheres: when
       those are clearly beyond the fog, sampling can't bring the portal back (a shortcut, same result) */
    double ox = (double)t->origin.x - p->origin.x, oy = (double)t->origin.y - p->origin.y,
           oz = (double)t->origin.z - p->origin.z;
    double gap = sqrt(ox * ox + oy * oy + oz * oz) - p->radius - t->radius - 1.0;
    if (gap > 0 && gap * gap > g_vis_radius2 * 1.001) return 0;
    double d = winding_dist2(t->w, p->w);
    if (d < md) md = d;
    return md <= g_vis_radius2;
}

/* ---- rough pass */
static void flood(Portal *src, int leaf) {
    for (int i = 0; i < lf_count[leaf]; i++) {
        int pn = lf_list[lf_first[leaf] + i];
        if (!(src->front[pn >> 3] & (1 << (pn & 7)))) continue;
        if (src->flood[pn >> 3] & (1 << (pn & 7))) continue;
        src->flood[pn >> 3] |= 1 << (pn & 7);
        flood(src, P[pn].leaf);
    }
}
static volatile LONG g_rough_next;
static DWORD WINAPI rough_worker(LPVOID arg) {
    (void)arg;
    for (;;) {
        int i = InterlockedIncrement(&g_rough_next) - 1;
        if (i >= NPD) break;
        Portal *p = &P[i];
        for (int j = 0; j < NPD; j++) {
            if (j == i) continue;
            Portal *t = &P[j];
            int k;
            for (k = 0; k < t->w->n; k++) if (dotv(t->w->p[k], p->pl.n) - p->pl.d > ON_EPS) break;
            if (k == t->w->n) continue;            /* no points in front */
            for (k = 0; k < p->w->n; k++) if (dotv(p->w->p[k], t->pl.n) - t->pl.d < -ON_EPS) break;
            if (k == p->w->n) continue;
            if (g_radial && !within_radius(p, t)) continue;
            p->front[j >> 3] |= 1 << (j & 7);
        }
        flood(p, p->leaf);
        int c = 0;
        const uint64_t *f64 = (const uint64_t *)p->flood;
        p->flo = PW; p->fhi = 0;
        for (int j = 0; j < PW; j++) {
            c += __builtin_popcountll(f64[j]);
            if (f64[j]) { if (j < p->flo) p->flo = j; p->fhi = j + 1; }
        }
        if (p->fhi == 0) p->flo = 0;
        p->nmight = c;
    }
    return 0;
}

static void run_threads(LPTHREAD_START_ROUTINE fn, int nt) {
    HANDLE *hs = xmalloc(nt * sizeof(HANDLE));
    for (int t = 0; t < nt; t++) {
        hs[t] = CreateThread(NULL, 64 << 20, fn, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
        if (!hs[t]) fail("couldn't start a thread%s", "");
    }
    for (int t = 0; t < nt; t += 64) WaitForMultipleObjects(nt - t < 64 ? nt - t : 64, hs + t, TRUE, INFINITE);
    for (int t = 0; t < nt; t++) CloseHandle(hs[t]);
    free(hs);
}

static int cmp_might(const void *a, const void *b) {
    int x = P[*(const int *)a].nmight, y = P[*(const int *)b].nmight;
    if (x != y) return x < y ? -1 : 1;
    return *(const int *)a - *(const int *)b;
}

/* ================================================================== files */
typedef struct { uint8_t *data; size_t len; } Buf;
static Buf read_file(const char *path) {
    Buf b = {0, 0};
    FILE *f = fopen(path, "rb");
    if (!f) fail("can't open %s", path);
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    b.data = xmalloc((size_t)n + 1);
    b.len = fread(b.data, 1, (size_t)n, f);
    b.data[b.len] = 0;
    fclose(f);
    if (b.len != (size_t)n) fail("can't read %s", path);
    return b;
}

#define NLUMPS 64
typedef struct { int32_t version, offset, length, fourcc; } Lump;   /* L4D2 order */
typedef struct { char ident[4]; int32_t version; Lump l[NLUMPS]; int32_t revision; } Header;
static Header H;
static Buf BSP;
static uint8_t *ldata[NLUMPS]; static int32_t llen[NLUMPS];        /* lumps as they'll be written */
static uint8_t *lump(int i, int *len) { *len = H.l[i].length; return BSP.data + H.l[i].offset; }

enum { L_ENTITIES = 0, L_PLANES = 1, L_VERTEXES = 3, L_VISIBILITY = 4, L_TEXINFO = 6, L_FACES = 7, L_LEAFS = 10,
       L_EDGES = 12, L_SURFEDGES = 13, L_LEAFFACES = 16, L_GAME = 35, L_MINDISTWATER = 46,
       L_AMB_INDEX_HDR = 51, L_AMB_INDEX = 52, L_AMB_LIGHT_HDR = 55, L_AMB_LIGHT = 56 };
#define CONTENTS_SOLID 0x1
#define CONTENTS_SLIME 0x10
#define CONTENTS_TESTFOGVOLUME 0x100
#define SURF_WARP 0x8
#define LEAF_SIZE 32           /* dleaf_t version 1 */
#define FACE_SIZE 56
#define TEXINFO_SIZE 72

/* the largest farz of any env_fog_controller (vvis's radial vis); -1 if none */
static float fog_farz(void) {
    int n; const char *e = (const char *)lump(L_ENTITIES, &n);
    float best = -1.0f;
    const char *p = e, *end = e + n;
    while (p < end && (p = memchr(p, '{', end - p)) != NULL) {
        const char *q = memchr(p, '}', end - p);
        if (!q) break;
        int is_fog = 0; float farz = 0; int has = 0;
        const char *s = p;
        while (s < q) {           /* "key" "value" pairs */
            const char *k0 = memchr(s, '"', q - s); if (!k0) break;
            const char *k1 = memchr(k0 + 1, '"', q - k0 - 1); if (!k1) break;
            const char *v0 = memchr(k1 + 1, '"', q - k1 - 1); if (!v0) break;
            const char *v1 = memchr(v0 + 1, '"', q - v0 - 1); if (!v1) break;
            int kl = (int)(k1 - k0 - 1), vl = (int)(v1 - v0 - 1);
            if (kl == 9 && !_strnicmp(k0 + 1, "classname", 9) && vl == 18 && !_strnicmp(v0 + 1, "env_fog_controller", 18)) is_fog = 1;
            if (kl == 4 && !_strnicmp(k0 + 1, "farz", 4)) { char tmp[64]; int m = vl < 63 ? vl : 63; memcpy(tmp, v0 + 1, m); tmp[m] = 0; farz = (float)atof(tmp); has = 1; }
            s = v1 + 1;
        }
        if (is_fog && has) {
            if (farz == 0.0f) farz = -1.0f;
            if (farz > best) best = farz;
        }
        p = q + 1;
    }
    return best;
}

/* the portal file vbsp writes: PRT1, clusters, portals, then "npoints c0 c1 (x y z ) ..." per portal */
static int NC_PRT, NP_PRT;
static void load_portals(const char *path) {
    Buf b = read_file(path);
    char *s = (char *)b.data, *e;
    if (strncmp(s, "PRT1", 4)) fail("%s isn't a portal file", path);
    s += 4;
    NC_PRT = (int)strtol(s, &e, 10); s = e;
    NP_PRT = (int)strtol(s, &e, 10); s = e;
    if (NC_PRT <= 0 || NP_PRT < 0) fail("bad portal file %s", path);
    NPD = NP_PRT * 2; NCL = NC_PRT; PW = (NPD + 63) / 64; if (!PW) PW = 1;
    P = xcalloc(NPD, sizeof(Portal));
    lf_count = xcalloc(NCL, sizeof(int));
    int *c0 = xmalloc(NP_PRT * sizeof(int) + 1), *c1 = xmalloc(NP_PRT * sizeof(int) + 1);
    for (int i = 0; i < NP_PRT; i++) {
        int n = (int)strtol(s, &e, 10); if (e == s) fail("reading portal file %s", path); s = e;
        c0[i] = (int)strtol(s, &e, 10); s = e;
        c1[i] = (int)strtol(s, &e, 10); s = e;
        if (n < 3 || n > MAXIN || c0[i] < 0 || c1[i] < 0 || c0[i] >= NCL || c1[i] >= NCL) fail("bad portal in %s", path);
        Win *w = xmalloc(sizeof(Win) + n * sizeof(V)), *wb = xmalloc(sizeof(Win) + n * sizeof(V));
        w->n = wb->n = n;
        for (int j = 0; j < n; j++) {
            while (*s && *s != '(') s++;
            if (!*s) fail("reading portal file %s", path);
            s++;
            double x = strtod(s, &e); s = e;
            double y = strtod(s, &e); s = e;
            double z = strtod(s, &e); s = e;
            while (*s && *s != ')') s++;
            if (*s) s++;
            w->p[j].x = (float)x; w->p[j].y = (float)y; w->p[j].z = (float)z;
        }
        for (int j = 0; j < n; j++) wb->p[j] = w->p[n - 1 - j];
        /* plane from the first three points: normal = (p0 - p1) x (p2 - p1), normalized */
        V a = {w->p[0].x - w->p[1].x, w->p[0].y - w->p[1].y, w->p[0].z - w->p[1].z};
        V bb = {w->p[2].x - w->p[1].x, w->p[2].y - w->p[1].y, w->p[2].z - w->p[1].z};
        Plane pl;
        pl.n.x = a.y * bb.z - a.z * bb.y; pl.n.y = a.z * bb.x - a.x * bb.z; pl.n.z = a.x * bb.y - a.y * bb.x;
        float l = sqrtf(dotv(pl.n, pl.n));
        if (l > 0) { pl.n.x /= l; pl.n.y /= l; pl.n.z /= l; }
        pl.d = dotv(w->p[0], pl.n);
        Portal *f = &P[2 * i], *r = &P[2 * i + 1];
        f->w = w; f->leaf = c1[i];                          /* forward: c0 -> c1, plane flipped */
        f->pl.n.x = -pl.n.x; f->pl.n.y = -pl.n.y; f->pl.n.z = -pl.n.z; f->pl.d = -pl.d;
        r->w = wb; r->leaf = c0[i]; r->pl = pl;             /* backward: c1 -> c0 */
        lf_count[c0[i]]++; lf_count[c1[i]]++;
    }
    lf_first = xcalloc(NCL, sizeof(int)); lf_list = xmalloc(NPD * sizeof(int) + 1);
    for (int c = 1; c < NCL; c++) lf_first[c] = lf_first[c - 1] + lf_count[c - 1];
    int *fill = xcalloc(NCL, sizeof(int));
    for (int i = 0; i < NP_PRT; i++) {
        lf_list[lf_first[c0[i]] + fill[c0[i]]++] = 2 * i;
        lf_list[lf_first[c1[i]] + fill[c1[i]]++] = 2 * i + 1;
    }
    free(fill); free(c0); free(c1); free(b.data);
    for (int i = 0; i < NPD; i++) {                         /* bounding sphere of each winding */
        Portal *p = &P[i];
        V c = {0, 0, 0};
        for (int j = 0; j < p->w->n; j++) { c.x += p->w->p[j].x; c.y += p->w->p[j].y; c.z += p->w->p[j].z; }
        c.x /= p->w->n; c.y /= p->w->n; c.z /= p->w->n;
        float r = 0;
        for (int j = 0; j < p->w->n; j++) {
            V d = {p->w->p[j].x - c.x, p->w->p[j].y - c.y, p->w->p[j].z - c.z};
            float l = sqrtf(dotv(d, d));
            if (l > r) r = l;
        }
        p->origin = c; p->radius = r;
        p->front = xcalloc(PW, 8); p->flood = xcalloc(PW, 8); p->vis = xcalloc(PW, 8);
    }
}

/* Quake's run-length compression: zero bytes become (0, count up to 255) */
static int compress_row(const uint8_t *vis, int rowbytes, uint8_t *dest) {
    uint8_t *d = dest;
    for (int j = 0; j < rowbytes; j++) {
        *d++ = vis[j];
        if (vis[j]) continue;
        int rep = 1;
        for (j++; j < rowbytes; j++) {
            if (vis[j] || rep == 255) break;
            rep++;
        }
        *d++ = (uint8_t)rep;
        j--;
    }
    return (int)(d - dest);
}

/* ================================================================== main */
int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    int threads = 0, fast = 0;
    const char *radius_override = NULL;
    const char *map = NULL;
    for (int i = 1; i < argc; i++) {
        if (!_stricmp(argv[i], "-threads") && i + 1 < argc) threads = atoi(argv[++i]);
        else if (!_stricmp(argv[i], "-fast")) fast = 1;
        else if (!_stricmp(argv[i], "-exact")) g_merge = 0;
        else if (!_stricmp(argv[i], "-game") && i + 1 < argc) i++;
        else if (!_stricmp(argv[i], "-radius_override") && i + 1 < argc) radius_override = argv[++i];
        else if (argv[i][0] == '-') { printf("Error: unsupported option %s\n", argv[i]); return 2; }
        else map = argv[i];
    }
    if (!map) { printf("usage: hlvvis [-fast] [-exact] [-threads N] [-radius_override R] [-game dir] <map>\n"); return 2; }
    if (g_merge) g_maxpts = MAXPTS;
    printf(g_merge ? "visibility: merged windows\n"
                   : "visibility: exact (vvis's path search, its output)\n");
    if (threads <= 0) {
        threads = (int)GetActiveProcessorCount(ALL_PROCESSOR_GROUPS);
        if (threads <= 0) threads = 1;
    }
    LARGE_INTEGER f0, t0, t1;
    QueryPerformanceFrequency(&f0); QueryPerformanceCounter(&t0);
    printf("Hammerless vis (hlvvis)\n%d threads\n", threads);
    if (fast) printf("fastvis = true\n");

    {   /* exact float thresholds for the double epsilon */
        float f = (float)ON_EPS;
        while ((double)f <= ON_EPS) f = nextafterf(f, 1.0f);
        EPS_UP = f;
        f = (float)ON_EPS;
        while ((double)f >= ON_EPS) f = nextafterf(f, 0.0f);
        LEN_LO = f;
    }

    char base[MAX_PATH], path[MAX_PATH + 8];
    snprintf(base, sizeof base, "%s", map);
    size_t bl = strlen(base);
    if (bl > 4 && !_stricmp(base + bl - 4, ".bsp")) base[bl - 4] = 0;
    snprintf(path, sizeof path, "%s.bsp", base);
    printf("reading %s\n", path);
    BSP = read_file(path);
    if (BSP.len < sizeof(Header)) fail("%s is too small to be a map", path);
    memcpy(&H, BSP.data, sizeof H);
    if (memcmp(H.ident, "VBSP", 4) || H.version != 21) fail("%s isn't an L4D2 map (VBSP version 21)", path);
    for (int i = 0; i < NLUMPS; i++) {
        if (H.l[i].length < 0 || H.l[i].offset < 0 || (size_t)H.l[i].offset + H.l[i].length > BSP.len)
            fail("%s is damaged (a lump runs past the end)", path);
        if (H.l[i].fourcc) fail("%s has compressed lumps (not supported)", path);
        ldata[i] = BSP.data + H.l[i].offset; llen[i] = H.l[i].length;
    }
    int nl; uint8_t *leafs0 = lump(L_LEAFS, &nl);
    if (H.l[L_LEAFS].version != 1 || nl % LEAF_SIZE) fail("unexpected leaf format in %s", path);
    int numleafs = nl / LEAF_SIZE;
    uint8_t *leafs = xmalloc(nl); memcpy(leafs, leafs0, nl);

    /* radial vis: portals beyond a radius are culled, and every leaf is marked radial. The radius is
       -radius_override's (as vvis: any value turns it on), else the largest env_fog_controller far Z */
    if (radius_override) {
        double r = atof(radius_override);
        printf("Vis Radius = %4.2f\n", r);
        g_radial = 1;
        g_vis_radius2 = r * r;
    } else {
        float farz = fog_farz();
        printf("max farz in all env_fog_controller entities: %f (used for radial vis)\n", farz);
        if (farz > 0) {
            g_radial = 1;
            g_vis_radius2 = (double)farz * farz;
        }
    }
    if (g_radial)
        for (int i = 0; i < numleafs; i++) leafs[i * LEAF_SIZE + 7] |= 0x400 >> 8;   /* LEAF_FLAGS_RADIAL */
    snprintf(path, sizeof path, "%s.prt", base);
    printf("reading %s\n", path);
    load_portals(path);
    printf("%d portalclusters\n%d numportals\n", NCL, NP_PRT);

    /* ---- rough pass, then the exact one */
    printf("BasePortalVis:       ");
    g_rough_next = 0;
    run_threads(rough_worker, threads);
    printf("done\n");
    if (fast) {
        for (int i = 0; i < NPD; i++) { memcpy(P[i].vis, P[i].flood, (size_t)PW * 8); P[i].status = 2; }
    } else {
        g_order = xmalloc(NPD * sizeof(int) + 1);
        for (int i = 0; i < NPD; i++) g_order[i] = i;
        qsort(g_order, NPD, sizeof(int), cmp_might);   /* fewest might-see first: their results tighten the rest */
        g_next = 0;
        printf("PortalFlow:          started\n");
        /* progress: portals done, weighted by an estimate of their cost (might-see squared), and the
           time left from the rate so far. One line per percent, read by Hammerless's build status. */
        g_pdur = xcalloc(NPD, sizeof(double));
        QueryPerformanceFrequency(&g_freq);
        double total_w = 0;
        for (int i = 0; i < NPD; i++) total_w += (double)P[i].nmight * P[i].nmight + 1;
        HANDLE *hs = xmalloc(threads * sizeof(HANDLE));
        for (int t = 0; t < threads; t++) {
            hs[t] = CreateThread(NULL, 64 << 20, flow_worker, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
            if (!hs[t]) fail("couldn't start a thread%s", "");
        }
        LARGE_INTEGER fs, fn;
        QueryPerformanceCounter(&fs);
        int last_pct = -1;
        while (g_done < NPD) {
            Sleep(250);
            double done_w = 0;
            for (int i = 0; i < NPD; i++) if (P[i].status == 2) done_w += (double)P[i].nmight * P[i].nmight + 1;
            int pct = (int)(100.0 * done_w / total_w);
            QueryPerformanceCounter(&fn);
            double el = (double)(fn.QuadPart - fs.QuadPart) / g_freq.QuadPart;
            if (pct != last_pct && pct < 100 && done_w > 0 && el > 0.5) {
                double left = el * (total_w - done_w) / done_w;
                printf("vis %d%%, about %.0f s left\n", pct, left < 1 ? 1 : left);
                last_pct = pct;
            }
        }
        for (int t = 0; t < threads; t += 64) WaitForMultipleObjects(threads - t < 64 ? threads - t : 64, hs + t, TRUE, INFINITE);
        for (int t = 0; t < threads; t++) CloseHandle(hs[t]);
        free(hs);
        QueryPerformanceCounter(&fn);
        double flow_wall = (double)(fn.QuadPart - fs.QuadPart) / g_freq.QuadPart;
        printf("PortalFlow:          done (%.1f s)\n", flow_wall);
        if (g_merge) printf("merged windows: %lld steps, %lld pushes, %lld portals opened fully\n", (long long)g_msteps, (long long)g_mpush, (long long)g_capped);
        /* where the time went, per portal (both directions), for Hammerless's vis cost view */
        snprintf(path, sizeof path, "%s.viscost", base);
        FILE *cf = fopen(path, "w");
        if (cf) {
            fprintf(cf, "hlvvis-cost 1\nportals %d threads %d flow_seconds %.4f\n", NP_PRT, threads, flow_wall);
            for (int i = 0; i < NP_PRT; i++) fprintf(cf, "%.6f %.6f\n", g_pdur[2 * i], g_pdur[2 * i + 1]);
            fclose(cf);
        }
    }

    /* ---- per-cluster visibility (the portals leaving it, and what they see), then the crosscheck */
    int leafbytes = ((NCL + 63) & ~63) >> 3, rowbytes = (NCL + 7) >> 3;
    uint8_t *pvs = xcalloc((size_t)NCL, leafbytes);
    uint8_t *pvec = xmalloc((size_t)PW * 8);
    long long totalvis = 0;
    for (int c = 0; c < NCL; c++) {
        memset(pvec, 0, (size_t)PW * 8);
        for (int i = 0; i < lf_count[c]; i++) {
            int pn = lf_list[lf_first[c] + i];
            const uint64_t *v = (const uint64_t *)P[pn].vis;
            for (int j = 0; j < PW; j++) ((uint64_t *)pvec)[j] |= v[j];
            pvec[pn >> 3] |= 1 << (pn & 7);
        }
        uint8_t *row = pvs + (size_t)c * leafbytes;
        for (int j = 0; j < NPD; j++) if (pvec[j >> 3] & (1 << (j & 7))) row[P[j].leaf >> 3] |= 1 << (P[j].leaf & 7);
        for (int j = 0; j < rowbytes; j++) totalvis += __builtin_popcount(row[j]);
        row[c >> 3] |= 1 << (c & 7);
        totalvis++;                          /* (vvis counts the cluster itself on top) */
    }
    long long optimized = 0;
    for (int c = 0; c < NCL; c++) {         /* keep a pair only if each side sees the other */
        uint8_t *row = pvs + (size_t)c * leafbytes;
        for (int i = 0; i < NCL; i++) {
            if (i == c || !(row[i >> 3] & (1 << (i & 7)))) continue;
            const uint8_t *other = pvs + (size_t)i * leafbytes;
            if (!(other[c >> 3] & (1 << (c & 7)))) { row[i >> 3] &= ~(1 << (i & 7)); optimized++; }
        }
    }
    printf("Optimized: %lld visible clusters (%.2f%%)\n", optimized, totalvis ? optimized * 100.0 / totalvis : 0.0);
    printf("Total clusters visible: %lld\nAverage clusters visible: %lld\n", totalvis, totalvis / NCL);

    /* ---- the visibility lump: header, every cluster's PVS, then every cluster's PAS */
    size_t cap = 4 + (size_t)8 * NCL + (size_t)2 * NCL * (rowbytes + rowbytes / 2 + 2) + 16;
    uint8_t *vl = xcalloc(cap, 1);
    int32_t *hdr = (int32_t *)vl;
    hdr[0] = NCL;
    size_t pos = 4 + (size_t)8 * NCL;
    for (int c = 0; c < NCL; c++) {
        hdr[1 + 2 * c] = (int32_t)pos;
        pos += compress_row(pvs + (size_t)c * leafbytes, rowbytes, vl + pos);
    }
    printf("Building PAS...\n");
    uint8_t *pas = xmalloc(leafbytes);
    long long audible = 0;
    for (int c = 0; c < NCL; c++) {
        const uint8_t *row = pvs + (size_t)c * leafbytes;
        memcpy(pas, row, leafbytes);
        for (int j = 0; j < NCL; j++)
            if (row[j >> 3] & (1 << (j & 7))) {
                const uint64_t *src = (const uint64_t *)(pvs + (size_t)j * leafbytes);
                for (int k = 0; k < leafbytes / 8; k++) ((uint64_t *)pas)[k] |= src[k];
            }
        for (int j = 0; j < rowbytes; j++) audible += __builtin_popcount(pas[j]);
        hdr[2 + 2 * c] = (int32_t)pos;
        pos += compress_row(pas, rowbytes, vl + pos);
    }
    printf("Average clusters audible: %lld\n", audible / NCL);
    printf("visdatasize:%d  compressed from %d\n", (int)pos, NCL * leafbytes * 2);
    ldata[L_VISIBILITY] = vl; llen[L_VISIBILITY] = (int32_t)pos;

    /* ---- fog volumes and distance to water (only matters for maps with water) */
    {
        int nf, nti, nlf, ne, nse, nv;
        uint8_t *faces = lump(L_FACES, &nf), *texinfo = lump(L_TEXINFO, &nti);
        uint16_t *leaffaces = (uint16_t *)lump(L_LEAFFACES, &nlf);
        uint16_t *edges = (uint16_t *)lump(L_EDGES, &ne);
        int32_t *surfedges = (int32_t *)lump(L_SURFEDGES, &nse);
        float *verts = (float *)lump(L_VERTEXES, &nv);
        int *clcount = xcalloc(NCL, sizeof(int)), *clfirst = xcalloc(NCL + 1, sizeof(int)), *cllist = xmalloc(numleafs * sizeof(int) + 1);
        #define LF_CONTENTS(i) (*(int32_t *)(leafs + (i) * LEAF_SIZE))
        #define LF_CLUSTER(i) (*(int16_t *)(leafs + (i) * LEAF_SIZE + 4))
        #define LF_WATER(i) (*(int16_t *)(leafs + (i) * LEAF_SIZE + 28))
        for (int i = 0; i < numleafs; i++) if (LF_CLUSTER(i) >= 0 && LF_CLUSTER(i) < NCL) clcount[LF_CLUSTER(i)]++;
        for (int c = 0; c < NCL; c++) clfirst[c + 1] = clfirst[c] + clcount[c];
        memset(clcount, 0, NCL * sizeof(int));
        for (int i = 0; i < numleafs; i++) { int c = LF_CLUSTER(i); if (c >= 0 && c < NCL) cllist[clfirst[c] + clcount[c]++] = i; }
        uint16_t *mindist = xmalloc(numleafs * sizeof(uint16_t) + 2);
        for (int i = 0; i < numleafs; i++) { LF_CONTENTS(i) &= ~CONTENTS_TESTFOGVOLUME; mindist[i] = 65535; }
        for (int i = 0; i < numleafs; i++) {
            if (LF_CONTENTS(i) & CONTENTS_TESTFOGVOLUME) continue;
            if (LF_CONTENTS(i) & CONTENTS_SOLID) continue;
            if (LF_WATER(i) == -1) continue;
            if (LF_CONTENTS(i) & CONTENTS_SLIME) continue;
            int c = LF_CLUSTER(i);
            if (c < 0 || c >= NCL) continue;
            const uint8_t *row = pvs + (size_t)c * leafbytes;
            for (int j = 0; j < NCL; j++) {
                if (j == c || !(row[j >> 3] & (1 << (j & 7)))) continue;
                for (int k = clfirst[j]; k < clfirst[j + 1]; k++) {
                    int lk = cllist[k];
                    if (LF_CONTENTS(lk) & CONTENTS_SOLID) continue;
                    if (LF_WATER(lk) != -1) continue;
                    LF_CONTENTS(lk) |= CONTENTS_TESTFOGVOLUME;   /* a dry leaf seen from water */
                }
            }
        }
        for (int i = 0; i < numleafs; i++) {
            if (!(LF_CONTENTS(i) & CONTENTS_TESTFOGVOLUME) && LF_WATER(i) == -1) continue;
            int c = LF_CLUSTER(i);
            if (c < 0 || c >= NCL) continue;
            const int16_t *lmn = (const int16_t *)(leafs + i * LEAF_SIZE + 8), *lmx = lmn + 3;
            float lmin[3] = {(float)lmn[0], (float)lmn[1], (float)lmn[2]}, lmax[3] = {(float)lmx[0], (float)lmx[1], (float)lmx[2]};
            float best = 65535.0f;
            const uint8_t *row = pvs + (size_t)c * leafbytes;
            for (int j = 0; j < NCL; j++) {
                if (j == c || !(row[j >> 3] & (1 << (j & 7)))) continue;
                for (int k = clfirst[j]; k < clfirst[j + 1]; k++) {
                    int lk = cllist[k];
                    if (!(LF_CONTENTS(lk) & CONTENTS_TESTFOGVOLUME) && LF_WATER(lk) == -1) continue;
                    uint16_t first, cnt;
                    memcpy(&first, leafs + lk * LEAF_SIZE + 20, 2); memcpy(&cnt, leafs + lk * LEAF_SIZE + 22, 2);
                    for (int f = 0; f < cnt; f++) {
                        if ((first + f) * 2 >= nlf) break;
                        int face = leaffaces[first + f];
                        if ((face + 1) * FACE_SIZE > nf) continue;
                        const uint8_t *fp = faces + face * FACE_SIZE;
                        int16_t ti; memcpy(&ti, fp + 10, 2);
                        if (ti == -1 || (ti + 1) * TEXINFO_SIZE > nti) continue;
                        int32_t tflags; memcpy(&tflags, texinfo + ti * TEXINFO_SIZE + 64, 4);
                        if (!(tflags & SURF_WARP)) continue;
                        int32_t fe; int16_t fn; memcpy(&fe, fp + 4, 4); memcpy(&fn, fp + 8, 2);
                        float fmin[3] = {99999, 99999, 99999}, fmax[3] = {-99999, -99999, -99999};
                        for (int e = fe; e < fe + fn; e++) {
                            if ((e + 1) * 4 > nse) break;
                            int id = surfedges[e]; if (id < 0) id = -id;
                            if ((id + 1) * 4 > ne) break;
                            for (int vv = 0; vv < 2; vv++) {
                                int vi = edges[id * 2 + vv];
                                if ((vi + 1) * 12 > nv) continue;
                                for (int a = 0; a < 3; a++) {
                                    float x = verts[vi * 3 + a];
                                    if (x < fmin[a]) fmin[a] = x;
                                    if (x > fmax[a]) fmax[a] = x;
                                }
                            }
                        }
                        /* distance between the leaf's box and the water face's box */
                        float dist;
                        if (lmin[0] <= fmax[0] && lmax[0] >= fmin[0] && lmin[1] <= fmax[1] && lmax[1] >= fmin[1] &&
                            lmin[2] <= fmax[2] && lmax[2] >= fmin[2]) dist = 0.0f;
                        else {
                            float ax[3];
                            for (int a = 0; a < 3; a++) {
                                if (lmin[a] <= fmax[a] && lmax[a] >= fmin[a]) ax[a] = 0.0f;
                                else { float d1 = lmin[a] - fmax[a], d2 = fmin[a] - lmax[a]; ax[a] = d1 > d2 ? d1 : d2; }
                            }
                            dist = sqrtf(ax[0] * ax[0] + ax[1] * ax[1] + ax[2] * ax[2]);
                        }
                        if (dist < best) best = dist;
                    }
                }
            }
            mindist[i] = (uint16_t)best;
        }
        ldata[L_LEAFS] = leafs; llen[L_LEAFS] = nl;
        ldata[L_MINDISTWATER] = (uint8_t *)mindist; llen[L_MINDISTWATER] = numleafs * 2;
    }

    /* ---- ambient lighting placeholders (vrad fills them in): one empty sample per leaf */
    if (!H.l[L_AMB_INDEX].length && !H.l[L_AMB_INDEX_HDR].length && !H.l[L_AMB_LIGHT].length && !H.l[L_AMB_LIGHT_HDR].length) {
        uint16_t *idx = xmalloc(numleafs * 4 + 4);
        for (int i = 0; i < numleafs; i++) { idx[2 * i] = 1; idx[2 * i + 1] = (uint16_t)i; }
        uint8_t *amb = xcalloc(numleafs, 28);
        ldata[L_AMB_INDEX] = ldata[L_AMB_INDEX_HDR] = (uint8_t *)idx; llen[L_AMB_INDEX] = llen[L_AMB_INDEX_HDR] = numleafs * 4;
        ldata[L_AMB_LIGHT] = ldata[L_AMB_LIGHT_HDR] = amb; llen[L_AMB_LIGHT] = llen[L_AMB_LIGHT_HDR] = numleafs * 28;
    }

    /* ---- write: the lumps in the order the map was written (empty ones sit just before the next
       written lump), 4-byte aligned, the game lump's directory moved with it */
    int order[NLUMPS];
    static const int tie[NLUMPS] = {[56] = -4, [52] = -3, [51] = -2, [55] = -1};   /* vvis's order there */
    for (int i = 0; i < NLUMPS; i++) order[i] = i;
    for (int a = 1; a < NLUMPS; a++) {
        int v = order[a], b = a - 1;
        #define KEY_LESS(x, y) (H.l[x].offset != H.l[y].offset ? H.l[x].offset < H.l[y].offset : \
            ((H.l[x].length > 0) != (H.l[y].length > 0) ? H.l[x].length == 0 : \
             (tie[x] != tie[y] ? tie[x] < tie[y] : x < y)))
        while (b >= 0 && KEY_LESS(v, order[b])) { order[b + 1] = order[b]; b--; }
        order[b + 1] = v;
    }
    size_t total = sizeof(Header);
    for (int i = 0; i < NLUMPS; i++) total += (size_t)llen[i] + 4;
    uint8_t *out = xcalloc(total + 16, 1);
    Header nh = H;
    size_t at = sizeof(Header);
    for (int k = 0; k < NLUMPS; k++) {
        int i = order[k];
        if (!H.l[i].offset && !H.l[i].length && !llen[i]) continue;   /* never written: stays at 0 */
        at = (at + 3) & ~(size_t)3;
        nh.l[i].offset = (int32_t)at;
        nh.l[i].length = llen[i];
        if (llen[i]) memcpy(out + at, ldata[i], llen[i]);
        at += llen[i];
    }
    at = (at + 3) & ~(size_t)3;          /* the file ends 4-byte aligned too */
    {   /* game lump directory: absolute file offsets */
        uint8_t *g = out + nh.l[L_GAME].offset;
        int32_t delta = nh.l[L_GAME].offset - H.l[L_GAME].offset, count = 0;
        if (nh.l[L_GAME].length >= 4) memcpy(&count, g, 4);
        for (int i = 0; i < count && 4 + (i + 1) * 16 <= nh.l[L_GAME].length; i++) {
            int32_t ofs; memcpy(&ofs, g + 4 + i * 16 + 8, 4);
            if (ofs) { ofs += delta; memcpy(g + 4 + i * 16 + 8, &ofs, 4); }
        }
    }
    memcpy(out, &nh, sizeof nh);
    snprintf(path, sizeof path, "%s.bsp", base);
    printf("writing %s\n", path);
    char tmp[MAX_PATH + 16];
    snprintf(tmp, sizeof tmp, "%s.hlvvis.tmp", base);
    FILE *f = fopen(tmp, "wb");
    if (!f) fail("can't write %s", tmp);
    if (fwrite(out, 1, at, f) != at) { fclose(f); remove(tmp); fail("can't write %s", tmp); }
    fclose(f);
    if (!MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING)) { remove(tmp); fail("can't replace %s", path); }
    QueryPerformanceCounter(&t1);
    printf("%.1f seconds elapsed\n", (double)(t1.QuadPart - t0.QuadPart) / f0.QuadPart);
    return 0;
}
