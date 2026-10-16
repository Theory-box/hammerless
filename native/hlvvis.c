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
#include "hlvvis_gpu.h"

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
static int g_two;           /* the default's two GPU passes are running (see g_auto) */
static uint8_t *g_jv1;      /* (experiment, HL_JACOBI_X=2 / HL_TWO) a first pass's vis rows, PW words per portal */
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
static int g_robust_sep = 0;     /* merged / round-based modes: check every source point (see sep_planes) */
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
            int bad = 0, front = 0;
            if (g_robust_sep) {
                /* (merged windows) every source point behind or on the plane, not just the first decisive one: a merged
                   hull's near-duplicate corners make hair-length edges whose planes tilt anyhow and can cut the source,
                   and such a plane would clip away real sight lines. Dropping it only clips less (vvis's own -exact
                   path keeps the one-point test, for its output) */
                for (int k = 0; k < source->n; k++) {
                    if (k == i || k == l) continue;
                    __m128 d = _mm_sub_ps(dot4(nx, ny, nz, source->p[k]), dist);
                    bad |= _mm_movemask_ps(_mm_cmpge_ps(d, up));
                }
            }
            /* every other pass point on or in front, at least one in front */
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
typedef struct { int gen, queued, has, grows, dirty, popped; WinBuf S, W, PS, PW_; double areaS, areaW; } MState;
static double g_merge_eps = 1e-4;   /* a point this far outside the stored hull is merged (float noise is less) */
static double g_grow_eps = 0.01;    /* a point this far outside the hull of the last pop re-expands the portal now */
static double g_inflate = 0;        /* widening of the windows at a pop (0: none; the sweeps make it unnecessary) */
static int g_max_sweeps = 1000;     /* rounds re-expanding silently grown portals; past that they open fully */
static volatile LONG64 g_sweeps, g_sweep_pops;
static _Thread_local int *t_touched, t_ntouched;   /* portals with a state in this flow */
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

/* the most corners a merged hull keeps (more: the polygon of supporting lines in that many directions around it, a
   superset). 64 for the default; -jacobi uses 16 (what the GPU holds per window), set before the flow */
static int g_hull_cap = MAXPTS;
static double g_cap_dir[MAXPTS][2];       /* the cap's directions (one table, so the GPU needs no sin / cos of its own) */
static void set_hull_cap(int cap) {
    g_hull_cap = cap;
    for (int k = 0; k < cap; k++) {
        double a = 2 * 3.14159265358979323846 * k / cap;
        g_cap_dir[k][0] = cos(a); g_cap_dir[k][1] = sin(a);
    }
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
    if (m > g_hull_cap) {
        /* too many corners: the K-gon of supporting lines in K directions around the hull (contains it) */
        const int K = g_hull_cap;
        double lx[MAXPTS], ly[MAXPTS], ld[MAXPTS];
        for (int k = 0; k < K; k++) {
            lx[k] = g_cap_dir[k][0]; ly[k] = g_cap_dir[k][1]; ld[k] = -1e300;
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
static int outside_hull(const Win *a, const Win *h, V n, double eps) {
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
            if ((o > 0 && c < -eps * el) || (o < 0 && c > eps * el)) return 1;
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
        ms->gen = t_mgen; ms->queued = 0; ms->has = 1; ms->grows = 0; ms->dirty = 0; ms->popped = 0;
        memcpy(&ms->S, source, sizeof(int) + source->n * sizeof(V));
        memcpy(&ms->W, pass, sizeof(int) + pass->n * sizeof(V));
        ms->areaS = poly_area(source, th->base->pl.n); ms->areaW = poly_area(pass, p->pl.n);
        memcpy(mm, might, PW * 8);
        t_touched[t_ntouched++] = pnum;
        grew = 1;
    } else {
        for (int k = 0; k < PW; k++) { uint64_t nb = might[k] & ~mm[k]; if (nb) { mm[k] |= nb; grew = bits = 1; } }
        /* the stored hulls are the union of everything that arrived (a point outside by more than float noise is
           merged). The portal is re-expanded now only when a point lies clearly (g_grow_eps) outside the hull it was
           last expanded with; smaller growth marks it dirty, and the sweeps at the end re-expand dirty portals
           (with the exact hull, no widening), so no growth is ever left out of the separators. */
        if (ms->grows < 1000000 && outside_hull(source, (Win *)&ms->S, th->base->pl.n, g_merge_eps)) {
            WinBuf ns; hull2((Win *)&ms->S, source, th->base->pl.n, winding_ccw(th->base->w, th->base->pl.n), &ns);
            ms->S = ns; ms->dirty = 1;
            if (!ms->popped || outside_hull(source, (Win *)&ms->PS, th->base->pl.n, g_grow_eps)) grew = 1;
        }
        if (ms->grows < 1000000 && outside_hull(pass, (Win *)&ms->W, p->pl.n, g_merge_eps)) {
            WinBuf nw; hull2((Win *)&ms->W, pass, p->pl.n, winding_ccw(p->w, p->pl.n), &nw);
            ms->W = nw; ms->dirty = 1;
            if (!ms->popped || outside_hull(pass, (Win *)&ms->PW_, p->pl.n, g_grow_eps)) grew = 1;
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
    if (grew) ms->dirty = 1;
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
        t_touched = malloc(((size_t)NPD + 1) * sizeof(int));
    }
    t_mgen++;
    t_ntouched = 0;
    int sweeps = 0;
    t_qtail = t_qlen = 0;
    int qhead = 0;
    uint8_t *vis = b->vis;
    const uint64_t *vis64 = (const uint64_t *)vis;
    uint64_t *might = malloc(PW * 8), *cur = malloc(PW * 8);
    /* first openings: as the path search (no separators yet) */
    {
        const uint64_t *bf = (const uint64_t *)(g_jv1 ? g_jv1 + (size_t)pi * PW * 8 : b->flood);
        int leaf = b->leaf;
        for (int i = 0; i < lf_count[leaf]; i++) {
            int pnum = lf_list[lf_first[leaf] + i];
            Portal *p = &P[pnum];
            if (!((bf[pnum >> 6] >> (pnum & 63)) & 1)) continue;
            const uint64_t *test = (const uint64_t *)(g_jv1 ? g_jv1 + (size_t)pnum * PW * 8 : p->status == 2 ? p->vis : p->flood);
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
    for (;;) {
      if (t_qlen == 0) {
        /* the queue ran dry: portals whose hulls grew since they were last expanded (by less than g_grow_eps, or
           they would have been re-queued) are expanded once more with their exact hulls. Repeated until nothing
           grew; past g_max_sweeps rounds the rest open fully (a superset, so still safe). */
        int pushed = 0;
        for (int i = 0; i < t_ntouched; i++) {
            MState *ms = &t_ms[t_touched[i]];
            if (!ms->dirty || ms->queued) continue;
            if (sweeps >= g_max_sweeps && ms->grows < 1000000) {
                Portal *p = &P[t_touched[i]];
                memcpy(&ms->S, b->w, sizeof(int) + b->w->n * sizeof(V));
                memcpy(&ms->W, p->w, sizeof(int) + p->w->n * sizeof(V));
                ms->grows = 1000000;
                InterlockedIncrement64(&g_capped);
            }
            ms->queued = 1; t_queue[t_qtail] = t_touched[i]; t_qtail = (t_qtail + 1) % (NPD + 1); t_qlen++;
            pushed++;
        }
        if (!pushed) break;
        sweeps++;
        InterlockedIncrement64(&g_sweeps); InterlockedAdd64(&g_sweep_pops, pushed);
      }
        int xn = t_queue[qhead]; qhead = (qhead + 1) % (NPD + 1); t_qlen--;
        MState *xs = &t_ms[xn];
        xs->queued = 0; xs->dirty = 0; xs->popped = 1;
        xs->PS = xs->S; xs->PW_ = xs->W;   /* (the hulls this expansion is made with) */
        WinBuf S, W;
        if (g_inflate > 0) { inflate((Win *)&xs->S, b->pl.n, g_inflate, &S); inflate((Win *)&xs->W, P[xn].pl.n, g_inflate, &W); }
        else { S = xs->S; W = xs->W; }
        memcpy(cur, t_mm + (size_t)xn * PW, PW * 8);
        Portal *x = &P[xn];
        int leaf = x->leaf;
        Plane sep0[4 * MAXPTS * MAXPTS / 4 + 8], sep1[4 * MAXPTS * MAXPTS / 4 + 8];
        int nsep0 = -1, nsep1 = 0;
        for (int i = 0; i < lf_count[leaf]; i++) {
            int pnum = lf_list[lf_first[leaf] + i];
            Portal *p = &P[pnum];
            if (!((cur[pnum >> 6] >> (pnum & 63)) & 1)) continue;
            const uint64_t *test = (const uint64_t *)(g_jv1 ? g_jv1 + (size_t)pnum * PW * 8 : p->status == 2 ? p->vis : p->flood);
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


/* ================================================================== round-based merged flow (-jacobi)
   The merged-window idea without might-see sets, in rounds: every portal whose windows grew is expanded once per
   round with the windows it had when the round began, and everything the round reaches is merged afterwards in a
   fixed order (by portal, then by the portal it came through). Each base portal's result depends only on its own
   flow (no other portal's result is read), so it is the same on any number of threads, and the rounds are the
   shape a GPU runs. Pruning comes only from the base's rough flood and the clipping.
   No growth thresholds: a window that grows by any amount past float noise is expanded again (a hair of growth near
   the base can swing a separator a long way off, so a threshold would hide real openings). A portal expanded
   JACOBI_SATURATE times opens to the whole base and the whole portal (a superset: still safe), so it ends. */
static int g_jacobi = 0;
/* (experiment) HL_JACOBI_X=1: a step from portal x into q needs q in x's own flood (any sight line through x then q
   is one x sees); =2 (two passes): q in x's vis from a first pass (a superset of what x sees: still safe) */
static int g_jx;
static inline int j_xok(int x, int q) {
    if (!g_jx) return 1;
    const uint8_t *row = g_jx == 2 && g_jv1 ? g_jv1 + (size_t)x * PW * 8 : P[x].flood;
    return (row[q >> 3] >> (q & 7)) & 1;
}
static double JACOBI_NOISE = 1e-4;   /* float noise in the windings (coordinates of thousands of units) */
static int JACOBI_SATURATE = 16;
static double JACOBI_WIDEN = 0;      /* (dev) windows widened before separators; 0: off (widening made things worse, measured) */
typedef struct { int gen, grows, opened, expanded, dirty, wgrew; WinBuf S, W, PS, PW; } JState;   /* PS/PW: last expansion's */
typedef struct { int p, seq, nomerge; WinBuf src, pass; } JCand;
static _Thread_local JState *t_js;
static _Thread_local int *t_front, *t_next, t_nfront, t_nnext, t_jgen;
static _Thread_local WinBuf *t_snapS, *t_snapW;
static _Thread_local JCand *t_cand; static _Thread_local int t_ncand, t_capcand;
/* (experiment, HL_JMIGHT / HL_TWO=2) might-see sets as the merged flow keeps them, in rounds: each portal's (t_jm), each
   candidate's (t_cmight), the snapshot's (t_snapM); a window that brings nothing the base doesn't already see (as the
   round began: t_visr) isn't merged */
static int g_jm;
/* (HL_JM_CL=1) the sets over clusters instead of portals: g_jw words each; a portal q stands for the cluster it leads
   into (if a portal p sees q, p's cluster set has q's cluster: a superset, safe) */
static int g_jcl, g_jw, g_jnofilter, g_jnomore, g_jmw;   /* (g_jmw: the GPU's words per set) */
static int *g_jclrank;      /* (the GPU pass: a base's clusters' ranks) */
static uint64_t *g_jv1cl;   /* per portal: its first pass's vis as clusters */
static _Thread_local uint64_t *t_jm, *t_cmight, *t_snapM, *t_visr, *t_seencl;
static inline int j_bit(int q) { return g_jcl ? P[q].leaf : q; }
static _Thread_local const uint64_t *t_addmight; static _Thread_local int t_addnomerge;
static inline const uint64_t *j_test(int q) {
    if (g_jcl) return g_jv1cl + (size_t)q * g_jw;
    return (const uint64_t *)(g_jv1 ? g_jv1 + (size_t)q * PW * 8 : P[q].flood);
}
static _Thread_local int *t_order;
static volatile LONG64 g_jrounds, g_jexp, g_jreach, g_jopened, g_jbitsonly;
/* (GPU planning) per base portal: its rounds and its widest round; how many bases took how many rounds */
static int *g_jbase_rounds, *g_jbase_maxfront;

static void j_add(int pnum, const Win *source, const Win *pass) {
    if (t_ncand == t_capcand) {
        t_capcand = t_capcand ? t_capcand * 2 : 4096;
        t_cand = realloc(t_cand, (size_t)t_capcand * sizeof(JCand));
        t_order = realloc(t_order, (size_t)t_capcand * sizeof(int));
        if (g_jm) t_cmight = realloc(t_cmight, (size_t)t_capcand * g_jw * 8);
        if (!t_cand || !t_order) fail("out of memory%s", "");
    }
    if (g_jm && !t_cmight && t_capcand) t_cmight = xmalloc((size_t)t_capcand * g_jw * 8);   /* (sets came after the list) */
    JCand *c = &t_cand[t_ncand];
    c->p = pnum; c->seq = t_ncand; c->nomerge = 0;
    if (g_jm) { memcpy(t_cmight + (size_t)t_ncand * g_jw, t_addmight, (size_t)g_jw * 8); c->nomerge = t_addnomerge; }
    memcpy(&c->src, source, sizeof(int) + source->n * sizeof(V));
    memcpy(&c->pass, pass, sizeof(int) + pass->n * sizeof(V));
    t_ncand++;
}

static int cmp_cand(const void *a, const void *b) {
    const JCand *x = &t_cand[*(const int *)a], *y = &t_cand[*(const int *)b];
    if (x->p != y->p) return (x->p > y->p) - (x->p < y->p);
    return (x->seq > y->seq) - (x->seq < y->seq);
}

/* merge one candidate into its portal's stored windows; returns 1 if the portal must be expanded (again) */
static int j_merge_core(Portal *b, const JCand *c, int *seen) {
    int pnum = c->p;
    JState *js = &t_js[pnum];
    Portal *p = &P[pnum];
    uint8_t *vis = b->vis;
    InterlockedIncrement64(&g_jreach);
    if (!(vis[pnum >> 3] & (1 << (pnum & 7)))) { vis[pnum >> 3] |= 1 << (pnum & 7); (*seen)++; }
    if (g_jm && c->nomerge) return 0;
    if (js->gen != t_jgen) {
        js->gen = t_jgen; js->grows = 0; js->opened = 0; js->expanded = 0; js->dirty = 0; js->wgrew = 1;
        if (g_jm) memcpy(t_jm + (size_t)pnum * g_jw, t_cmight + (size_t)c->seq * g_jw, (size_t)g_jw * 8);
        js->S = c->src; js->W = c->pass;
        if (js->S.n > g_hull_cap) { WinBuf t; hull2((Win *)&js->S, (Win *)&js->S, b->pl.n, winding_ccw(b->w, b->pl.n), &t); js->S = t; }
        if (js->W.n > g_hull_cap) { WinBuf t; hull2((Win *)&js->W, (Win *)&js->W, p->pl.n, winding_ccw(p->w, p->pl.n), &t); js->W = t; }
    } else {
        int bits = 0;
        if (g_jm) {
            uint64_t *mm = t_jm + (size_t)pnum * g_jw; const uint64_t *mi = t_cmight + (size_t)c->seq * g_jw;
            for (int k = 0; k < g_jw; k++) { uint64_t nb = mi[k] & ~mm[k]; if (nb) { mm[k] |= nb; bits = 1; } }
        }
        if (js->opened && !bits) return 0;
        int grew = 0;
        if (!js->opened) {
        if (outside_hull((const Win *)&c->src, (Win *)&js->S, b->pl.n, JACOBI_NOISE)) {
            WinBuf ns; hull2((Win *)&js->S, (const Win *)&c->src, b->pl.n, winding_ccw(b->w, b->pl.n), &ns);
            js->S = ns; grew = 1;
        }
        if (outside_hull((const Win *)&c->pass, (Win *)&js->W, p->pl.n, JACOBI_NOISE)) {
            WinBuf nw; hull2((Win *)&js->W, (const Win *)&c->pass, p->pl.n, winding_ccw(p->w, p->pl.n), &nw);
            js->W = nw; grew = 1;
        }
        }
        if (grew) js->wgrew = 1;
        if (!grew && !bits) return 0;
    }
    if (!js->dirty) { js->dirty = 1; t_next[t_nnext++] = pnum; }
    return 1;
}

/* (self-test, HL_MERGE_TEST=n with -threads 1) the first n merges, before and after, for the GPU's merge to redo */
static int g_mtest_cap, g_mtest_n, g_mtest_every;
static vgpu_mrec_t *g_mtest_rec;
static vgpu_mres_t *g_mtest_res;
static void put_pts(float *o, const Win *w, int cap) {
    for (int k = 0; k < cap; k++) {
        V q = k < w->n ? w->p[k] : (V){0, 0, 0};
        o[4 * k] = q.x; o[4 * k + 1] = q.y; o[4 * k + 2] = q.z; o[4 * k + 3] = 0;
    }
}
static void j_merge(Portal *b, const JCand *c, int *seen) {
    static long long every_n;
    if (g_mtest_n >= g_mtest_cap || (g_mtest_every > 1 && every_n++ % g_mtest_every)) { j_merge_core(b, c, seen); return; }
    JState *js = &t_js[c->p];
    Portal *p = &P[c->p];
    vgpu_mrec_t *r = &g_mtest_rec[g_mtest_n];
    int inited = js->gen == t_jgen;
    r->flags[0] = inited; r->flags[1] = inited ? js->opened : 0; r->flags[2] = inited ? js->grows : 0; r->flags[3] = 0;
    r->bn[0] = b->pl.n.x; r->bn[1] = b->pl.n.y; r->bn[2] = b->pl.n.z; r->bn[3] = (float)winding_ccw(b->w, b->pl.n);
    r->pn[0] = p->pl.n.x; r->pn[1] = p->pl.n.y; r->pn[2] = p->pl.n.z; r->pn[3] = (float)winding_ccw(p->w, p->pl.n);
    r->counts[0] = inited ? js->S.n : 0; r->counts[1] = inited ? js->W.n : 0; r->counts[2] = c->src.n; r->counts[3] = c->pass.n;
    if (inited) { put_pts(r->S, (Win *)&js->S, 16); put_pts(r->W, (Win *)&js->W, 16); }
    else { memset(r->S, 0, sizeof r->S); memset(r->W, 0, sizeof r->W); }
    put_pts(r->src, (const Win *)&c->src, 64); put_pts(r->pas, (const Win *)&c->pass, 64);
    int req = j_merge_core(b, c, seen);
    vgpu_mres_t *o = &g_mtest_res[g_mtest_n++];
    o->flags[0] = req; o->flags[1] = js->opened; o->flags[2] = js->grows; o->flags[3] = 1;
    o->counts[0] = js->S.n; o->counts[1] = js->W.n; o->counts[2] = o->counts[3] = 0;
    put_pts(o->S, (Win *)&js->S, 16); put_pts(o->W, (Win *)&js->W, 16);
}

#define JACOBI_MAX_ARRIVALS 64   /* windows reaching one portal in one round, at most (the GPU's list); more: it opens */
static volatile LONG64 g_joverflow;

/* open portal pnum fully (the whole base and the whole portal, capped): a superset, so always safe */
static void j_open(Portal *b, int pnum) {
    JState *js = &t_js[pnum];
    Portal *q = &P[pnum];
    memcpy(&js->S, b->w, sizeof(int) + b->w->n * sizeof(V));
    memcpy(&js->W, q->w, sizeof(int) + q->w->n * sizeof(V));
    if (js->S.n > g_hull_cap) { WinBuf t; hull2((Win *)&js->S, (Win *)&js->S, b->pl.n, winding_ccw(b->w, b->pl.n), &t); js->S = t; }
    if (js->W.n > g_hull_cap) { WinBuf t; hull2((Win *)&js->W, (Win *)&js->W, q->pl.n, winding_ccw(q->w, q->pl.n), &t); js->W = t; }
    js->opened = 1;
    InterlockedIncrement64(&g_jopened);
}

static void j_merge_all(Portal *b, int *seen) {
    for (int i = 0; i < t_ncand; i++) t_order[i] = i;
    qsort(t_order, t_ncand, sizeof(int), cmp_cand);
    for (int i = 0; i < t_ncand;) {
        int j = i;
        while (j < t_ncand && t_cand[t_order[j]].p == t_cand[t_order[i]].p) j++;
        if (j - i > JACOBI_MAX_ARRIVALS) {
            /* (more windows than the GPU holds for one portal in a round: the first one sets it up as usual, then
               it opens fully; the same on CPU and GPU) */
            int pnum = t_cand[t_order[i]].p;
            j_merge(b, &t_cand[t_order[i]], seen);
            if (!t_js[pnum].opened) {
                j_open(b, pnum);
                if (!t_js[pnum].dirty) { t_js[pnum].dirty = 1; t_next[t_nnext++] = pnum; }
            }
            if (g_jm) for (int k = i + 1; k < j; k++) j_merge(b, &t_cand[t_order[k]], seen);   /* (opened: bits only) */
            InterlockedIncrement64(&g_joverflow);
        } else for (int k = i; k < j; k++) j_merge(b, &t_cand[t_order[k]], seen);
        i = j;
    }
    t_ncand = 0;
}


/* one expansion step: what comes through the snapshot (S, W) of a frontier portal into portal pnum (the CPU's way,
   and the GPU's reference): 1 with the source and pass windings, 0 if nothing gets through */
static int j_expand_one(Portal *b, WinBuf *S, WinBuf *W, int pnum, WinBuf *src_out, WinBuf *pass_out) {
    Portal *p = &P[pnum];
    Plane bplane = b->pl;
    Stack st; memset(&st, 0, sizeof st); st.freew[0] = st.freew[1] = st.freew[2] = 1;
    Plane back = {{-p->pl.n.x, -p->pl.n.y, -p->pl.n.z}, -p->pl.d};
    float d = dotv(p->origin, bplane.n) - bplane.d;
    if (d < -p->radius) return 0;
    Win *pass = d > p->radius ? p->w : chop(p->w, &st, &bplane);
    if (!pass) return 0;
    d = dotv(b->origin, p->pl.n) - p->pl.d;
    if (d > b->radius) return 0;
    Win *source = d < -b->radius ? (Win *)S : chop((Win *)S, &st, &back);
    if (!source) return 0;
    pass = clip_sep(source, (Win *)W, pass, 0, &st); if (!pass) return 0;
    pass = clip_sep((Win *)W, source, pass, 1, &st); if (!pass) return 0;
    memcpy(src_out, source, sizeof(int) + source->n * sizeof(V));
    memcpy(pass_out, pass, sizeof(int) + pass->n * sizeof(V));
    return 1;
}

/* -gpu: the round's expansion steps on the GPU (hlvvis_gpu.c, hlvvis_shaders/expand.comp), in the CPU's order;
   HL_GPU_CHECK=1 also does each on the CPU and counts any difference (they must be the same to the bit) */
static int g_gpu_expand, g_gpu_check;
static volatile LONG64 g_gpu_items, g_gpu_diff;
static CRITICAL_SECTION g_gpu_lock;
static _Thread_local vgpu_item_t *t_items; static _Thread_local int t_capitems;
static _Thread_local float *t_snappts; static _Thread_local uint32_t *t_snapn; static _Thread_local int t_capsnap;
static _Thread_local float *t_osrc, *t_opass; static _Thread_local uint32_t *t_on;
static void j_expand_gpu(Portal *b, const uint64_t *bf) {
    int n = 0;
    for (int f = 0; f < t_nfront; f++) {
        Portal *x = &P[t_front[f]];
        InterlockedIncrement64(&g_jexp);
        for (int i = 0; i < lf_count[x->leaf]; i++) {
            int pnum = lf_list[lf_first[x->leaf] + i];
            if (!((bf[pnum >> 6] >> (pnum & 63)) & 1)) continue;
            if (n == t_capitems) {
                t_capitems = t_capitems ? t_capitems * 2 : 4096;
                t_items = realloc(t_items, (size_t)t_capitems * sizeof(vgpu_item_t));
                t_osrc = realloc(t_osrc, (size_t)t_capitems * VGPU_MAXW * 16);
                t_opass = realloc(t_opass, (size_t)t_capitems * VGPU_MAXW * 16);
                t_on = realloc(t_on, (size_t)t_capitems * 16);
            }
            t_items[n].base = (uint32_t)(b - P); t_items[n].snap = (uint32_t)f; t_items[n].q = (uint32_t)pnum; t_items[n].pad = 0;
            n++;
        }
    }
    if (!n) return;
    if (t_nfront > t_capsnap) {
        t_capsnap = t_nfront * 2;
        t_snappts = realloc(t_snappts, (size_t)t_capsnap * 2 * VGPU_SNAPCAP * 16);
        t_snapn = realloc(t_snapn, (size_t)t_capsnap * 2 * 4);
    }
    for (int f = 0; f < t_nfront; f++) {
        WinBuf *ws[2] = {&t_snapS[f], &t_snapW[f]};
        for (int h = 0; h < 2; h++) {
            if (ws[h]->n > VGPU_SNAPCAP) fail("a window with more corners than the GPU holds%s", "");
            t_snapn[2 * f + h] = (uint32_t)ws[h]->n;
            float *o = t_snappts + ((size_t)f * 2 + h) * VGPU_SNAPCAP * 4;
            for (int k = 0; k < ws[h]->n; k++) { o[4 * k] = ws[h]->p[k].x; o[4 * k + 1] = ws[h]->p[k].y; o[4 * k + 2] = ws[h]->p[k].z; o[4 * k + 3] = 0; }
        }
    }
    EnterCriticalSection(&g_gpu_lock);
    int ok = vgpu_expand(n, t_items, t_nfront, t_snappts, t_snapn, t_osrc, t_opass, t_on);
    LeaveCriticalSection(&g_gpu_lock);
    if (!ok) fail("the GPU's expansion step failed%s", "");
    InterlockedAdd64(&g_gpu_items, n);
    for (int k = 0; k < n; k++) {
        int pnum = (int)t_items[k].q;
        WinBuf src, pass;
        int got = t_on[4 * k] != 0;
        if (got) {
            src.n = (int)t_on[4 * k + 1]; pass.n = (int)t_on[4 * k + 2];
            for (int i = 0; i < src.n; i++) src.p[i] = (V){t_osrc[((size_t)k * VGPU_MAXW + i) * 4], t_osrc[((size_t)k * VGPU_MAXW + i) * 4 + 1], t_osrc[((size_t)k * VGPU_MAXW + i) * 4 + 2]};
            for (int i = 0; i < pass.n; i++) pass.p[i] = (V){t_opass[((size_t)k * VGPU_MAXW + i) * 4], t_opass[((size_t)k * VGPU_MAXW + i) * 4 + 1], t_opass[((size_t)k * VGPU_MAXW + i) * 4 + 2]};
        }
        if (g_gpu_check) {
            WinBuf cs, cp;
            int cgot = j_expand_one(b, &t_snapS[t_items[k].snap], &t_snapW[t_items[k].snap], pnum, &cs, &cp);
            int same = cgot == got && (!got || (cs.n == src.n && cp.n == pass.n && !memcmp(cs.p, src.p, src.n * sizeof(V)) &&
                                                 !memcmp(cp.p, pass.p, pass.n * sizeof(V))));
            if (!same && InterlockedIncrement64(&g_gpu_diff) <= 5)
                printf("GPU/CPU differ: base %d, into %d: CPU %s (src %d, pass %d pts), GPU %s (src %d, pass %d pts)\n",
                       (int)(b - P), pnum, cgot ? "through" : "blocked", cgot ? cs.n : 0, cgot ? cp.n : 0,
                       got ? "through" : "blocked", got ? src.n : 0, got ? pass.n : 0);
        }
        if (got) j_add(pnum, (Win *)&src, (Win *)&pass);
    }
}

/* -gpu: the portals for the expansion steps (once) */
static int gpu_expand_setup(void) {
    int npts = 0;
    for (int i = 0; i < NPD; i++) npts += P[i].w->n;
    float *pts = xmalloc((size_t)npts * 16 + 16);
    vgpu_eportal_t *gp = xmalloc((size_t)NPD * sizeof(vgpu_eportal_t) + 16);
    int at = 0;
    for (int i = 0; i < NPD; i++) {
        Portal *p = &P[i];
        gp[i].plane[0] = p->pl.n.x; gp[i].plane[1] = p->pl.n.y; gp[i].plane[2] = p->pl.n.z; gp[i].plane[3] = p->pl.d;
        gp[i].origin[0] = p->origin.x; gp[i].origin[1] = p->origin.y; gp[i].origin[2] = p->origin.z; gp[i].origin[3] = p->radius;
        gp[i].first = (uint32_t)at; gp[i].count = (uint32_t)p->w->n; gp[i].pad0 = gp[i].pad1 = 0;
        for (int k = 0; k < p->w->n; k++, at++) {
            pts[4 * at] = p->w->p[k].x; pts[4 * at + 1] = p->w->p[k].y; pts[4 * at + 2] = p->w->p[k].z; pts[4 * at + 3] = 0;
        }
    }
    InitializeCriticalSection(&g_gpu_lock);
    if (g_maxpts > VGPU_MAXW) fail("the GPU holds fewer points per winding than the chop makes%s", "");
    int ok = vgpu_expand_setup(NPD, pts, npts, gp, EPS_UP, LEN_LO, g_robust_sep, g_maxpts);
    free(pts); free(gp);
    return ok;
}

static int cmp_int(const void *a, const void *b) { int x = *(const int *)a, y = *(const int *)b; return (x > y) - (x < y); }

/* the start of base portal b's flow: its first openings (no separators yet), merged (t_next: the portals to expand
   first, t_js their windows). Returns the size of its flood; *seen: how many of those it already sees */
static int j_begin(Portal *b, int *seen_out) {
    if (!t_js) {
        t_js = calloc(NPD, sizeof(JState));
        t_front = malloc(((size_t)NPD + 1) * sizeof(int)); t_next = malloc(((size_t)NPD + 1) * sizeof(int));
        t_snapS = malloc(((size_t)NPD + 1) * sizeof(WinBuf)); t_snapW = malloc(((size_t)NPD + 1) * sizeof(WinBuf));
    }
    if (g_jm && !t_jm) {
        t_jm = malloc((size_t)NPD * g_jw * 8); t_snapM = malloc(((size_t)NPD + 1) * g_jw * 8);
        t_visr = malloc((size_t)(PW + g_jw) * 8 + 16); t_seencl = malloc((size_t)g_jw * 8 + 8);
    }
    t_jgen++;
    t_nnext = 0; t_ncand = 0;
    const uint64_t *bf = (const uint64_t *)b->flood;
    int floodn = 0, seen = 0;
    for (int k = 0; k < PW; k++) floodn += (int)__builtin_popcountll(bf[k]);
    Plane bplane = b->pl;
    /* first openings: no separators yet */
    for (int i = 0; i < lf_count[b->leaf]; i++) {
        int pnum = lf_list[lf_first[b->leaf] + i];
        Portal *p = &P[pnum];
        if (!((bf[pnum >> 6] >> (pnum & 63)) & 1)) continue;
        Stack st; memset(&st, 0, sizeof st); st.freew[0] = st.freew[1] = st.freew[2] = 1;
        Plane back = {{-p->pl.n.x, -p->pl.n.y, -p->pl.n.z}, -p->pl.d};
        float d = dotv(p->origin, bplane.n) - bplane.d;
        if (d < -p->radius) continue;
        Win *pass = d > p->radius ? p->w : chop(p->w, &st, &bplane);
        if (!pass) continue;
        d = dotv(b->origin, p->pl.n) - p->pl.d;
        if (d > b->radius) continue;
        Win *source = d < -b->radius ? b->w : chop(b->w, &st, &back);
        if (!source) continue;
        if (g_jm) {
            const uint64_t *tq = j_test(pnum), *bs = g_jcl ? g_jv1cl + (size_t)(b - P) * g_jw :
                                                     g_jv1 ? (const uint64_t *)(g_jv1 + (size_t)(b - P) * PW * 8) : bf;
            uint64_t more = 0;
            for (int k = 0; k < g_jw; k++) { t_visr[k] = bs[k] & tq[k]; more |= t_visr[k]; }
            t_addmight = t_visr; t_addnomerge = !more;
        }
        j_add(pnum, source, pass);
    }
    j_merge_all(b, &seen);
    *seen_out = seen;
    return floodn;
}

static void jacobi_flow(int pi) {
    Portal *b = &P[pi];
    LARGE_INTEGER t0, t1;
    QueryPerformanceCounter(&t0);
    b->status = 1;
    int seen, floodn = j_begin(b, &seen);
    const uint64_t *bf = (const uint64_t *)b->flood;
    Plane bplane = b->pl;
    while (t_nnext && seen < floodn) {
        InterlockedIncrement64(&g_jrounds);
        if (g_jbase_rounds) { g_jbase_rounds[pi]++; if (t_nnext > g_jbase_maxfront[pi]) g_jbase_maxfront[pi] = t_nnext; }
        int *tmp = t_front; t_front = t_next; t_next = tmp;
        t_nfront = t_nnext; t_nnext = 0;
        qsort(t_front, t_nfront, sizeof(int), cmp_int);
        for (int f = 0; f < t_nfront; f++) {
            JState *js = &t_js[t_front[f]];
            js->dirty = 0;
            if (g_jm) memcpy(t_snapM + (size_t)f * g_jw, t_jm + (size_t)t_front[f] * g_jw, (size_t)g_jw * 8);
            if (g_jm && !js->wgrew) InterlockedIncrement64(&g_jbitsonly);
            if ((!g_jm || js->wgrew) && ++js->grows > JACOBI_SATURATE && !js->opened) {    /* (it keeps creeping: open it fully) */
                memcpy(&js->S, b->w, sizeof(int) + b->w->n * sizeof(V));
                memcpy(&js->W, P[t_front[f]].w, sizeof(int) + P[t_front[f]].w->n * sizeof(V));
                if (js->S.n > g_hull_cap) { WinBuf t; hull2((Win *)&js->S, (Win *)&js->S, b->pl.n, winding_ccw(b->w, b->pl.n), &t); js->S = t; }
                if (js->W.n > g_hull_cap) {
                    WinBuf t; Portal *q = &P[t_front[f]];
                    hull2((Win *)&js->W, (Win *)&js->W, q->pl.n, winding_ccw(q->w, q->pl.n), &t); js->W = t;
                }
                js->opened = 1;
                InterlockedIncrement64(&g_jopened);
            }
            js->expanded = 1; js->wgrew = 0;
            js->PS = js->S; js->PW = js->W;
            if (JACOBI_WIDEN > 0 && !js->opened) {     /* (the hulls ignore growth under the noise: widen by it, safe side) */
                inflate((Win *)&js->S, b->pl.n, JACOBI_WIDEN, &t_snapS[f]);
                inflate((Win *)&js->W, P[t_front[f]].pl.n, JACOBI_WIDEN, &t_snapW[f]);
            } else { t_snapS[f] = js->S; t_snapW[f] = js->W; }
        }
        uint64_t *visr = t_visr + g_jw, *jmight = t_visr, *seen_m = visr;
        if (g_jm) {
            memcpy(visr, b->vis, (size_t)PW * 8);
            if (g_jcl) {                    /* (the clusters the base sees as the round begins) */
                memset(t_seencl, 0, (size_t)g_jw * 8);
                for (int k = 0; k < PW; k++)
                    for (uint64_t w = visr[k]; w; w &= w - 1) { int q = k * 64 + __builtin_ctzll(w); t_seencl[P[q].leaf >> 6] |= 1ull << (P[q].leaf & 63); }
                seen_m = t_seencl;
            }
        }
        if (g_gpu_expand) j_expand_gpu(b, bf);
        else
        for (int f = 0; f < t_nfront; f++) {
            int xn = t_front[f];
            Portal *x = &P[xn];
            WinBuf *S = &t_snapS[f], *W = &t_snapW[f];
            InterlockedIncrement64(&g_jexp);
            Plane sep0[4 * MAXPTS * MAXPTS / 4 + 8], sep1[4 * MAXPTS * MAXPTS / 4 + 8];
            int nsep0 = -1, nsep1 = 0;
            int leaf = x->leaf;
            for (int i = 0; i < lf_count[leaf]; i++) {
                int pnum = lf_list[lf_first[leaf] + i];
                Portal *p = &P[pnum];
                if (!((bf[pnum >> 6] >> (pnum & 63)) & 1) || !j_xok(xn, pnum)) continue;
                if (g_jm) {
                    const uint64_t *cur = t_snapM + (size_t)f * g_jw, *tq = j_test(pnum);
                    int jb = j_bit(pnum);
                    int nofilter = g_jnofilter, nomore = g_jnomore;
                    if (!nofilter && !((cur[jb >> 6] >> (jb & 63)) & 1)) continue;
                    uint64_t more = 0;
                    for (int k = 0; k < g_jw; k++) { jmight[k] = cur[k] & tq[k]; more |= jmight[k] & ~seen_m[k]; }
                    if (nomore) more = 1;
                    if (!more && ((visr[pnum >> 6] >> (pnum & 63)) & 1)) continue;
                    t_addmight = jmight; t_addnomerge = !more;
                }
                Stack st; memset(&st, 0, sizeof st); st.freew[0] = st.freew[1] = st.freew[2] = 1;
                Plane back = {{-p->pl.n.x, -p->pl.n.y, -p->pl.n.z}, -p->pl.d};
                float d = dotv(p->origin, bplane.n) - bplane.d;
                if (d < -p->radius) continue;
                Win *pass = d > p->radius ? p->w : chop(p->w, &st, &bplane);
                if (!pass) continue;
                d = dotv(b->origin, p->pl.n) - p->pl.d;
                if (d > b->radius) continue;
                Win *source = d < -b->radius ? (Win *)S : chop((Win *)S, &st, &back);
                if (!source) continue;
                if (source == (Win *)S) {
                    if (nsep0 < 0) {
                        nsep0 = sep_planes((Win *)S, (Win *)W, 0, sep0);
                        nsep1 = sep_planes((Win *)W, (Win *)S, 1, sep1);
                    }
                    pass = apply_planes(sep0, nsep0, pass, &st); if (!pass) continue;
                    pass = apply_planes(sep1, nsep1, pass, &st); if (!pass) continue;
                } else {
                    pass = clip_sep(source, (Win *)W, pass, 0, &st); if (!pass) continue;
                    pass = clip_sep((Win *)W, source, pass, 1, &st); if (!pass) continue;
                }
                j_add(pnum, source, pass);
            }
        }
        j_merge_all(b, &seen);
    }
    MemoryBarrier();
    b->status = 2;
    QueryPerformanceCounter(&t1);
    g_pdur[pi] = (double)(t1.QuadPart - t0.QuadPart) / g_freq.QuadPart;
    InterlockedIncrement(&g_done);
}


/* -gpu -jacobi: the whole flow on the GPU (hlvvis_gpu.c, hlvvis_shaders/pipe_*.comp), many base portals at once, one
   per slot, every live slot a round per call; the same results as the CPU's -jacobi to the bit (the same steps in the
   same order). The CPU does each base's first openings (when it fills a slot) and the bases those settle. */
static int g_gpu_pipe, g_gpu_pipe_slots;
static long long g_gpu_pipe_entries;
static int g_gpu_pipe_fmax;
/* the entries for the running flows: blocks of powers of two (a flow takes the smallest that holds its flood), split
   from bigger free ones when none is free (the flows come biggest first, so no merging back is needed); all of them
   back at once when nothing runs */
#define POOL_CLASSES 40
typedef struct { long long top, cap, *free[POOL_CLASSES]; int nfree[POOL_CLASSES], capfree[POOL_CLASSES]; } Pool;
static Pool g_epool, g_wpool;            /* the entries; (the second pass) the sets' words */
static int pool_class(long long n) { int k = 0; while ((1ll << k) < n) k++; return k; }
static void pool_put(Pool *pl, int k, long long off) {
    if (pl->nfree[k] == pl->capfree[k]) {
        pl->capfree[k] = pl->capfree[k] ? pl->capfree[k] * 2 : 64;
        pl->free[k] = realloc(pl->free[k], (size_t)pl->capfree[k] * sizeof(long long));
        if (!pl->free[k]) fail("out of memory%s", "");
    }
    pl->free[k][pl->nfree[k]++] = off;
}
static long long pool_get(Pool *pl, long long n) {
    int k = pool_class(n);
    if (pl->nfree[k]) return pl->free[k][--pl->nfree[k]];
    if (pl->top + (1ll << k) <= pl->cap) { long long off = pl->top; pl->top += 1ll << k; return off; }
    for (int j = k + 1; j < POOL_CLASSES; j++)
        if (pl->nfree[j]) {
            long long off = pl->free[j][--pl->nfree[j]];
            for (int i = j - 1; i >= k; i--) pool_put(pl, i, off + (1ll << i));   /* (the upper halves) */
            return off;
        }
    return -1;
}
static void pool_reset(Pool *pl) { pl->top = 0; for (int k = 0; k < POOL_CLASSES; k++) pl->nfree[k] = 0; }
static int *g_pipe_order;
static int cmp_flood_desc(const void *a, const void *b) {
    int x = P[*(const int *)a].nmight, y = P[*(const int *)b].nmight;
    return (x < y) - (x > y);
}
static int gpu_pipe_setup(void) {
    if (JACOBI_WIDEN > 0 || JACOBI_NOISE != 1e-4 || JACOBI_SATURATE != 16 || g_mtest_cap || g_jbase_rounds) return 0;
    if (g_hull_cap > VGPU_SNAPCAP || JACOBI_MAX_ARRIVALS != 64 || g_jx || g_jm) return 0;   /* (the GPU's fixed sizes) */
    int npts = 0, fmax = 0, nlf = 0;
    for (int i = 0; i < NPD; i++) {
        if (P[i].w->n > VGPU_MAXW) return 0;
        npts += P[i].w->n;
        int n = 0;
        for (int k = 0; k < PW; k++) n += (int)__builtin_popcountll(((const uint64_t *)P[i].flood)[k]);
        if (n > fmax) fmax = n;
    }
    for (int c = 0; c < NCL; c++) if (lf_first[c] + lf_count[c] > nlf) nlf = lf_first[c] + lf_count[c];
    float *pts = xmalloc((size_t)npts * 16 + 16);
    vgpu_pportal_t *pp = xmalloc((size_t)NPD * sizeof(vgpu_pportal_t) + 16);
    uint32_t *leaves = xmalloc((size_t)NCL * 8 + 16), *lfl = xmalloc((size_t)nlf * 4 + 16);
    int at = 0;
    for (int i = 0; i < NPD; i++) {
        Portal *p = &P[i];
        pp[i].plane[0] = p->pl.n.x; pp[i].plane[1] = p->pl.n.y; pp[i].plane[2] = p->pl.n.z; pp[i].plane[3] = p->pl.d;
        pp[i].origin[0] = p->origin.x; pp[i].origin[1] = p->origin.y; pp[i].origin[2] = p->origin.z; pp[i].origin[3] = p->radius;
        pp[i].first = (uint32_t)at; pp[i].count = (uint32_t)p->w->n;
        pp[i].ccw = (uint32_t)winding_ccw(p->w, p->pl.n); pp[i].leaf = (uint32_t)p->leaf;
        for (int k = 0; k < p->w->n; k++, at++) {
            pts[4 * at] = p->w->p[k].x; pts[4 * at + 1] = p->w->p[k].y; pts[4 * at + 2] = p->w->p[k].z; pts[4 * at + 3] = 0;
        }
    }
    for (int c = 0; c < NCL; c++) { leaves[2 * c] = (uint32_t)lf_first[c]; leaves[2 * c + 1] = (uint32_t)lf_count[c]; }
    for (int i = 0; i < nlf; i++) lfl[i] = (uint32_t)lf_list[i];
    vgpu_pipe_info_t info = {0};
    int want = getenv("HL_GPU_SLOTS") ? atoi(getenv("HL_GPU_SLOTS")) : 0;
    int ns = vgpu_pipe_setup(NPD, pts, npts, pp, NCL, leaves, nlf, lfl, &g_cap_dir[0][0], g_hull_cap, fmax, PW * 2, EPS_UP,
                             LEN_LO, g_robust_sep, g_maxpts, JACOBI_SATURATE, JACOBI_MAX_ARRIVALS, want, &info);
    free(pts); free(pp); free(leaves); free(lfl);
    if (ns) printf("(the flow on the GPU: up to %d at once, largest flood %d, room for %lld, %.1f GB) ", ns, fmax,
                   info.entries, info.bytes / 1e9);
    g_gpu_pipe_slots = ns;
    g_gpu_pipe_entries = info.entries;
    g_gpu_pipe_fmax = fmax;
    if (ns && (1ll << pool_class(fmax)) > info.entries) { printf("(the GPU has too little room for the largest flow) "); ns = 0; }
    g_gpu_pipe_slots = ns;
    return ns > 0;
}

/* returns 0 if the GPU failed on the way (the caller then does it all on the CPU) */
static int gpu_pipe_flow(double total_w) {
    int ns = g_gpu_pipe_slots, pw32 = PW * 2;
    g_pipe_order = xmalloc((size_t)NPD * sizeof(int) + 4);
    for (int i = 0; i < NPD; i++) g_pipe_order[i] = i;
    qsort(g_pipe_order, NPD, sizeof(int), cmp_flood_desc);     /* the longest first: a short tail */
    int *slot_base = xmalloc((size_t)ns * sizeof(int) + 4), *visq = xmalloc((size_t)ns * sizeof(int) + 4);
    int *visq_base = xmalloc((size_t)ns * sizeof(int) + 4);
    long long *slot_off = xmalloc((size_t)ns * sizeof(long long) + 8);
    int *slot_class = xmalloc((size_t)ns * sizeof(int) + 4), *slot_wclass = xmalloc((size_t)ns * sizeof(int) + 4);
    long long *slot_woff = xmalloc((size_t)ns * sizeof(long long) + 8);
    for (int k = 0; k < ns; k++) slot_base[k] = -1;
    pool_reset(&g_epool); pool_reset(&g_wpool);
    g_epool.cap = g_gpu_pipe_entries;
    int pending = -1;                       /* a base whose first openings are done but found no room yet */
    int pending_floodn = 0, pending_seen = 0;
    int mw = g_jm ? g_jmw : 0;
    if (g_jm && !g_jclrank) g_jclrank = xmalloc((size_t)NCL * sizeof(int) + 4);
    uint32_t *visout = xmalloc((size_t)ns * pw32 * 4 + 16);
    vgpu_slot_t *so = xmalloc((size_t)ns * sizeof(vgpu_slot_t) + 16);
    size_t capw = (size_t)ns + 4096;
    uint32_t *rf = xmalloc(capw * 4);
    int next = 0, active = 0, nvisq = 0, last_pct = -1;
    uint32_t round = 0;
    long long passes = 0;
    double done_w = 0;
    LARGE_INTEGER fs, fn, r0, r1;
    QueryPerformanceCounter(&fs);
    r0 = fs;
    int max_rounds = getenv("HL_PIPE_ROUNDS") ? atoi(getenv("HL_PIPE_ROUNDS")) : 0;   /* (dev) stop after n rounds */
    while (next < NPD || active > 0 || nvisq > 0 || pending >= 0) {
        if (max_rounds && (int)round >= max_rounds) break;
        round++;
        /* fill the free slots: each new base's first openings on the CPU (some bases end there) */
        int nrec = 0;
        size_t nw = (size_t)ns;
        memset(rf, 0, (size_t)ns * 4);
        if (!active && pending >= 0) { pool_reset(&g_epool); pool_reset(&g_wpool); }   /* (nothing runs: all free) */
        for (int k = 0; k < ns && (next < NPD || pending >= 0) && nrec < 1024; k++) {   /* (a share per round) */
            if (slot_base[k] >= 0) continue;
            int placed = 0;
            while (next < NPD || pending >= 0) {
                int pi, floodn, seen;
                if (pending >= 0) pi = pending, floodn = pending_floodn, seen = pending_seen;
                else {
                    pi = g_pipe_order[next++];
                    P[pi].status = 1;
                    floodn = j_begin(&P[pi], &seen);
                    if (!t_nnext || seen >= floodn) {
                        P[pi].status = 2; g_done++;
                        done_w += (double)P[pi].nmight * P[pi].nmight + 1;
                        continue;
                    }
                }
                Portal *b = &P[pi];
                int mws = 0;
                long long woff = 0;
                if (mw) {                           /* (its sets: as many words as its clusters need) */
                    const uint64_t *bc = g_jv1cl + (size_t)pi * g_jw;
                    int ncl_b = 0;
                    for (int k2 = 0; k2 < g_jw; k2++) ncl_b += (int)__builtin_popcountll(bc[k2]);
                    mws = (ncl_b + 31) / 32;
                    if (mws < 1) mws = 1;
                }
                long long off = pool_get(&g_epool, floodn);
                if (off >= 0 && mw) {
                    woff = pool_get(&g_wpool, (long long)floodn * mws);
                    if (woff < 0) { pool_put(&g_epool, pool_class(floodn), off); off = -1; }
                }
                if (off < 0) { pending = pi, pending_floodn = floodn, pending_seen = seen; break; }   /* (waits for room; t_js keeps it) */
                pending = -1;
                slot_off[k] = off; slot_class[k] = pool_class(floodn);
                slot_woff[k] = woff; slot_wclass[k] = pool_class((long long)floodn * (mws ? mws : 1));
                size_t need = nw + 8 + 2 * (size_t)pw32 + (size_t)t_nnext * (132 + (size_t)mws);
                if (need > capw) { capw = need * 2; rf = realloc(rf, capw * 4); if (!rf) fail("out of memory%s", ""); }
                rf[nrec++] = (uint32_t)nw;
                rf[nw] = (uint32_t)k; rf[nw + 1] = (uint32_t)pi; rf[nw + 2] = (uint32_t)t_nnext; rf[nw + 3] = (uint32_t)floodn;
                rf[nw + 4] = (uint32_t)off; rf[nw + 5] = (uint32_t)seen; rf[nw + 6] = (uint32_t)mws; rf[nw + 7] = (uint32_t)woff;
                memcpy(rf + nw + 8, b->flood, (size_t)pw32 * 4);
                memcpy(rf + nw + 8 + pw32, b->vis, (size_t)pw32 * 4);
                nw += 8 + 2 * (size_t)pw32;
                if (mw) {                           /* (the base's clusters by rank) */
                    const uint64_t *bc = g_jv1cl + (size_t)pi * g_jw;
                    for (int cl = 0, r = 0; cl < NCL; cl++) g_jclrank[cl] = (bc[cl >> 6] >> (cl & 63)) & 1 ? r++ : -1;
                }
                for (int e = 0; e < t_nnext; e++) {
                    int q = t_next[e];
                    JState *js = &t_js[q];
                    uint32_t *o = rf + nw;
                    memset(o, 0, (132 + (size_t)mws) * 4);
                    o[0] = (uint32_t)q; o[1] = (uint32_t)js->S.n; o[2] = (uint32_t)js->W.n;
                    if (js->S.n > VGPU_SNAPCAP || js->W.n > VGPU_SNAPCAP) fail("a window with more corners than the GPU holds%s", "");
                    for (int i = 0; i < js->S.n; i++) memcpy(o + 4 + 4 * i, &js->S.p[i], 12);
                    for (int i = 0; i < js->W.n; i++) memcpy(o + 68 + 4 * i, &js->W.p[i], 12);
                    if (mw) {
                        const uint64_t *m = t_jm + (size_t)q * g_jw;
                        for (int k = 0; k < g_jw; k++)
                            for (uint64_t wd = m[k]; wd; wd &= wd - 1) {
                                int r = g_jclrank[k * 64 + __builtin_ctzll(wd)];
                                if (r < 0) fail("a might-see bit outside the base's clusters%s", "");
                                o[132 + (r >> 5)] |= 1u << (r & 31);
                            }
                    }
                    nw += 132 + (size_t)mws;
                }
                slot_base[k] = pi; active++;
                placed = 1;
                break;
            }
            if (!placed) break;
        }
        long long np, ni; int npass;
        static int fail_at = -1;                /* (dev) HL_GPU_FAIL_AT=n: act as if the GPU failed in round n */
        if (fail_at < 0) fail_at = getenv("HL_GPU_FAIL_AT") ? atoi(getenv("HL_GPU_FAIL_AT")) : 0;
        int fail_here = fail_at && (int)round == fail_at && (g_jm != 0) == (getenv("HL_GPU_FAIL_PASS2") != NULL);
        if (fail_here || !vgpu_pipe_round(round, nrec, rf, (int)nw, nvisq, visq, visout, so, &np, &ni, &npass)) {
            free(slot_base); free(visq); free(visq_base); free(visout); free(so); free(rf);
            return 0;
        }
        for (int i = 0; i < nvisq; i++) {
            Portal *b = &P[visq_base[i]];
            memcpy(b->vis, visout + (size_t)i * pw32, (size_t)PW * 8);
            MemoryBarrier();
            b->status = 2; g_done++;
            done_w += (double)b->nmight * b->nmight + 1;
        }
        nvisq = 0;
        QueryPerformanceCounter(&r1);
        double dt = (double)(r1.QuadPart - r0.QuadPart) / g_freq.QuadPart;
        r0 = r1;
        int live = 0;
        for (int k = 0; k < ns; k++) if (slot_base[k] >= 0 && so[k].live) live++;
        for (int k = 0; k < ns; k++) {
            if (slot_base[k] < 0) continue;
            g_pdur[slot_base[k]] += live ? dt / live : 0;      /* (the round's time shared by the flows in it) */
            if (!so[k].live) {
                visq[nvisq] = k; visq_base[nvisq++] = slot_base[k]; slot_base[k] = -1; active--;
                pool_put(&g_epool, slot_class[k], slot_off[k]);
                if (mw) pool_put(&g_wpool, slot_wclass[k], slot_woff[k]);
            }
        }
        g_jrounds += live; g_jexp += np; g_gpu_items += ni; passes += npass;
        QueryPerformanceCounter(&fn);
        double el = (double)(fn.QuadPart - fs.QuadPart) / g_freq.QuadPart;
        /* (the default's two passes show as one: the first about 38% of the time, the second, about 1.6 times
           as long, the rest) */
        double fr = done_w / total_w, lo = 0, span = 100, after = 0;
        if (g_two && !g_jm) span = 38, after = 1.6;
        else if (g_two) lo = 38, span = 62;
        int pct = (int)(lo + span * fr);
        if (pct != last_pct && pct < 100 && done_w > 0 && el > 0.5) {
            double left = el * (1 - fr) / fr + after * el / fr;
            printf("vis %d%%, about %.0f s left\n", pct, left < 1 ? 1 : left);
            last_pct = pct;
        }
    }
    printf("GPU flow: %u rounds of up to %d flows (%lld passes)\n", round, ns, passes);
    vgpu_pipe_report();
    free(slot_base); free(visq); free(visq_base); free(visout); free(so); free(rf); free(slot_off); free(slot_class);
    free(slot_woff); free(slot_wclass);
    return 1;
}


/* (dev) how far point q lies outside convex polygon h on its plane (normal n): <= 0 inside */
static double out_dist(V q, const Win *h, V n) {
    double sx = 0, sy = 0, sz = 0;
    for (int i = 0; i < h->n; i++) {
        V p = h->p[i], r = h->p[(i + 1) % h->n];
        sx += (double)p.y * r.z - (double)p.z * r.y; sy += (double)p.z * r.x - (double)p.x * r.z; sz += (double)p.x * r.y - (double)p.y * r.x;
    }
    double o = sx * n.x + sy * n.y + sz * n.z, worst = -1e30;
    if (o == 0 || h->n < 3) return 1e30;
    for (int i = 0; i < h->n; i++) {
        V p = h->p[i], r = h->p[(i + 1) % h->n];
        double ex = (double)r.x - p.x, ey = (double)r.y - p.y, ez = (double)r.z - p.z;
        double ax = (double)q.x - p.x, ay = (double)q.y - p.y, az = (double)q.z - p.z;
        double c = (ey * az - ez * ay) * n.x + (ez * ax - ex * az) * n.y + (ex * ay - ey * ax) * n.z;
        double el = sqrt(ex * ex + ey * ey + ez * ez);
        double dist = -(o > 0 ? c : -c) / (el > 0 ? el : 1);     /* > 0: outside this edge */
        if (dist > worst) worst = dist;
    }
    return worst;
}

/* (dev) HL_REPLAY=file: a sight line's portal chain (tests/compile chain_walk.py): exact one-chain clipping along it,
   then the merged / round-based flow of its base portal, and where the line first falls outside the stored windows */
static void replay(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) fail("can't read %s", path);
    int base, chain[512], n = 0; V pt[512], start = {0, 0, 0};
    char word[64];
    fscanf(f, "%d", &base);
    while (fscanf(f, "%63s", word) == 1) {
        if (!strcmp(word, "start")) { fscanf(f, "%f %f %f", &start.x, &start.y, &start.z); break; }
        chain[n] = atoi(word); fscanf(f, "%f %f %f", &pt[n].x, &pt[n].y, &pt[n].z); n++;
    }
    fclose(f);
    Portal *b = &P[base];
    printf("replay: base %d (into cluster %d), %d portals, start outside base winding by %.5f\n", base, b->leaf, n,
           out_dist(start, b->w, b->pl.n));
    /* exact single-chain clipping, as the path search does it */
    {
        WinBuf srcb, passb; Win *source = b->w, *prevpass = NULL; WinBuf prevb;
        for (int k = 0; k < n; k++) {
            Portal *p = &P[chain[k]];
            int okleaf = k == 0 ? (P[base].leaf == P[chain[0]].leaf ? 0 : 1) : 1;
            (void)okleaf;
            Stack st; memset(&st, 0, sizeof st); st.freew[0] = st.freew[1] = st.freew[2] = 1;
            Plane back = {{-p->pl.n.x, -p->pl.n.y, -p->pl.n.z}, -p->pl.d};
            Win *pass = chop(p->w, &st, &b->pl);
            if (!pass) { printf("  exact %2d portal %d: pass chopped away by the base plane\n", k, chain[k]); break; }
            Win *src = chop(source, &st, &back);
            if (!src) { printf("  exact %2d portal %d: source chopped away by its back plane\n", k, chain[k]); break; }
            if (prevpass) {
                pass = clip_sep(src, prevpass, pass, 0, &st);
                if (pass) pass = clip_sep(prevpass, src, pass, 1, &st);
                if (!pass) { printf("  exact %2d portal %d: CLIPPED AWAY by separators\n", k, chain[k]); break; }
            }
            printf("  exact %2d portal %d: point outside pass by %.5f, start outside source by %.5f\n", k, chain[k],
                   out_dist(pt[k], pass, p->pl.n), out_dist(start, src, b->pl.n));
            memcpy(&srcb, src, sizeof(int) + src->n * sizeof(V)); memcpy(&passb, pass, sizeof(int) + pass->n * sizeof(V));
            prevb = passb; prevpass = (Win *)&prevb;
            WinBuf *keep = malloc(sizeof(WinBuf)); *keep = srcb; source = (Win *)keep;
        }
    }
    /* the flow, then each chain portal's stored windows */
    memset(b->vis, 0, (size_t)PW * 8);
    if (g_jacobi) jacobi_flow(base); else merged_flow(base);
    printf("  %s flow of the base:\n", g_jacobi ? "round-based" : "merged");
    for (int k = 0; k < n; k++) {
        int q = chain[k];
        int reached = (b->vis[q >> 3] >> (q & 7)) & 1;
        printf("  flood %2d portal %d: in the base's flood %d, in front of the base %d\n", k, q, (b->flood[q >> 3] >> (q & 7)) & 1,
               (b->front[q >> 3] >> (q & 7)) & 1);
        if (g_jacobi) {
            JState *js = &t_js[q];
            if (js->gen != t_jgen) {
                printf("  flow  %2d portal %d: NEVER REACHED (vis bit %d)\n", k, q, reached);
                if (k > 0 && t_js[chain[k - 1]].gen == t_jgen) {
                    /* the step from the previous chain portal, as an expansion does it, with its last windows */
                    JState *px = &t_js[chain[k - 1]];
                    Portal *p = &P[q], *x = &P[chain[k - 1]];
                    int inleaf = 0;
                    for (int i = 0; i < lf_count[x->leaf]; i++) if (lf_list[lf_first[x->leaf] + i] == q) inleaf = 1;
                    printf("    step from %d: %d is %sa portal leaving cluster %d\n", chain[k - 1], q, inleaf ? "" : "NOT ", x->leaf);
                    Stack st; memset(&st, 0, sizeof st); st.freew[0] = st.freew[1] = st.freew[2] = 1;
                    Plane back = {{-p->pl.n.x, -p->pl.n.y, -p->pl.n.z}, -p->pl.d};
                    float d = dotv(p->origin, b->pl.n) - b->pl.d;
                    printf("    portal origin %.3f in front of the base plane (radius %.3f)\n", d, p->radius);
                    Win *pass = d > p->radius ? p->w : chop(p->w, &st, &b->pl);
                    if (!pass) { printf("    pass: chopped away by the base plane\n"); continue; }
                    printf("    pass after the base plane: point outside by %.5f (%d points)\n", out_dist(pt[k], pass, p->pl.n), pass->n);
                    d = dotv(b->origin, p->pl.n) - p->pl.d;
                    printf("    base origin %.3f in front of the portal plane (base radius %.3f)\n", d, b->radius);
                    Win *S = (Win *)&px->PS, *W = (Win *)&px->PW;
                    Win *source = d < -b->radius ? S : chop(S, &st, &back);
                    if (!source) { printf("    source: chopped away by the portal's back plane\n"); continue; }
                    printf("    source: start outside by %.5f (%d points, %s)\n", out_dist(start, source, b->pl.n), source->n,
                           source == S ? "whole S" : "chopped");
                    Win *p1 = clip_sep(source, W, pass, 0, &st);
                    if (!p1) { printf("    separators source->pass: CLIPPED AWAY\n"); continue; }
                    printf("    after separators source->pass: point outside by %.5f\n", out_dist(pt[k], p1, p->pl.n));
                    {
                        printf("    S (%d points):", source->n);
                        for (int i = 0; i < source->n; i++) printf(" (%.3f %.3f %.3f)", source->p[i].x, source->p[i].y, source->p[i].z);
                        printf("\n    W (%d points):", W->n);
                        for (int i = 0; i < W->n; i++) printf(" (%.3f %.3f %.3f)", W->p[i].x, W->p[i].y, W->p[i].z);
                        printf("\n    point (%.3f %.3f %.3f), start (%.3f %.3f %.3f)\n", pt[k].x, pt[k].y, pt[k].z, start.x, start.y, start.z);
                        Plane *pl = malloc(((size_t)source->n * W->n + 8) * sizeof(Plane));
                        int np_ = sep_planes(W, source, 1, pl);
                        for (int i = 0; i < np_; i++) {
                            float dp = dotv(pt[k], pl[i].n) - pl[i].d, ds = dotv(start, pl[i].n) - pl[i].d;
                            if (dp < 0.01f) printf("    plane %d (%.5f %.5f %.5f | %.3f): point %.4f, start %.4f\n", i, pl[i].n.x,
                                                   pl[i].n.y, pl[i].n.z, pl[i].d, dp, ds);
                        }
                        free(pl);
                    }
                    Win *p2 = clip_sep(W, source, p1, 1, &st);
                    if (!p2) { printf("    separators pass->source: CLIPPED AWAY\n"); continue; }
                    printf("    after separators pass->source: point outside by %.5f\n", out_dist(pt[k], p2, p->pl.n));
                }
                continue;
            }
            printf("  flow  %2d portal %d: point outside W %.5f (at last expansion %.5f), start outside S %.5f "
                   "(at last expansion %.5f)%s%s\n", k, q, out_dist(pt[k], (Win *)&js->W, P[q].pl.n),
                   js->expanded ? out_dist(pt[k], (Win *)&js->PW, P[q].pl.n) : 99.0,
                   out_dist(start, (Win *)&js->S, b->pl.n), js->expanded ? out_dist(start, (Win *)&js->PS, b->pl.n) : 99.0,
                   js->opened ? " OPENED" : "", js->expanded ? "" : " (never expanded)");
        } else {
            MState *ms = &t_ms[q];
            if (ms->gen != t_mgen) { printf("  flow  %2d portal %d: NEVER REACHED (vis bit %d)\n", k, q, reached); continue; }
            printf("  flow  %2d portal %d: point outside W %.5f (at last pop %.5f), start outside S %.5f "
                   "(at last pop %.5f)%s\n", k, q, out_dist(pt[k], (Win *)&ms->W, P[q].pl.n),
                   ms->popped ? out_dist(pt[k], (Win *)&ms->PW_, P[q].pl.n) : 99.0, out_dist(start, (Win *)&ms->S, b->pl.n),
                   ms->popped ? out_dist(start, (Win *)&ms->PS, b->pl.n) : 99.0, ms->grows >= 1000000 ? " OPENED" : "");
        }
    }
}

static DWORD WINAPI flow_worker(LPVOID arg) {
    (void)arg;
    for (;;) {
        LONG k = InterlockedIncrement(&g_next) - 1;
        if (k >= NPD) break;
        if (P[g_order[k]].status == 2) continue;          /* (done already: by the GPU before it failed) */
        if (g_jacobi) jacobi_flow(g_order[k]);
        else if (g_merge) merged_flow(g_order[k]); else portal_flow(g_order[k]);
    }
    return 0;
}

/* ---- radial vis (L4D2): the distance between two windings, sampled. Each is cut into a fan of triangles
   from its first point (the second one's fan wraps all the way round, as vvis's does) and points are
   taken on every pair of triangles at barycentric steps of 0.25: the smallest squared distance found */
static int g_radial;
static int g_gpu;                 /* -gpu: the rough pass's plane tests on the graphics card (hlvvis_gpu.c) */
/* the default (no -exact / -jacobi / -gpu / -cpu): on a graphics card that can, -jacobi on it twice, the second
   pass with might-see sets pruned by the first's vis (g_two); else the merged windows on the CPU */
static int g_auto = 0;     /* (-gpu2 turns it on; the default is the merged windows on the CPU) */
static int g_front_done;          /* the front bits are already in (from the GPU) */
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
        for (int j = 0; j < NPD && !g_front_done; j++) {
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


/* the GPU's rows of "in front" bits into each portal's front bitset */
static void store_front(int row, const uint32_t *words, void *ctx) {
    (void)ctx;
    memcpy(P[row].front, words, (size_t)PW * 8);
}
/* -gpu: the rough pass's plane tests on the graphics card; 0 if it couldn't (the CPU does them then) */
static int gpu_front(void) {
    int npts = 0;
    for (int i = 0; i < NPD; i++) npts += P[i].w->n;
    float *pts = xmalloc((size_t)npts * 16 + 16);
    vgpu_portal_t *gp = xmalloc((size_t)NPD * sizeof(vgpu_portal_t) + 16);
    int at = 0;
    for (int i = 0; i < NPD; i++) {
        Portal *p = &P[i];
        gp[i].plane[0] = p->pl.n.x; gp[i].plane[1] = p->pl.n.y; gp[i].plane[2] = p->pl.n.z; gp[i].plane[3] = p->pl.d;
        gp[i].first = (uint32_t)at; gp[i].count = (uint32_t)p->w->n; gp[i].pad0 = gp[i].pad1 = 0;
        for (int k = 0; k < p->w->n; k++, at++) {
            pts[4 * at] = p->w->p[k].x; pts[4 * at + 1] = p->w->p[k].y; pts[4 * at + 2] = p->w->p[k].z; pts[4 * at + 3] = 0;
        }
    }
    int ok = vgpu_front(NPD, pts, npts, gp, EPS_UP, PW * 2, store_front, NULL);
    free(pts); free(gp);
    if (!ok) for (int i = 0; i < NPD; i++) memset(P[i].front, 0, (size_t)PW * 8);    /* (a part may have arrived) */
    return ok;
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
/* every base portal's flow on the CPU's threads (those not done yet), with the progress lines Hammerless reads */
static void cpu_flows(int threads, double total_w, LARGE_INTEGER fs) {
    LARGE_INTEGER fn;
    g_next = 0;
    HANDLE *hs = xmalloc(threads * sizeof(HANDLE));
    for (int t = 0; t < threads; t++) {
        hs[t] = CreateThread(NULL, 64 << 20, flow_worker, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
        if (!hs[t]) fail("couldn't start a thread%s", "");
    }
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
}

int main(int argc, char **argv) {
    if (getenv("HL_UNBUF")) setvbuf(stdout, NULL, _IONBF, 0);   /* (dev) every line out at once */
    setvbuf(stdout, NULL, _IONBF, 0);
    int threads = 0, fast = 0;
    const char *radius_override = NULL;
    const char *map = NULL;
    for (int i = 1; i < argc; i++) {
        if (!_stricmp(argv[i], "-threads") && i + 1 < argc) threads = atoi(argv[++i]);
        else if (!_stricmp(argv[i], "-fast")) fast = 1;
        else if (!_stricmp(argv[i], "-exact")) g_merge = 0, g_auto = 0;
        else if (!_stricmp(argv[i], "-jacobi")) g_jacobi = 1, g_auto = 0;
        else if (!_stricmp(argv[i], "-gpu")) g_gpu = 1, g_auto = 0;
        else if (!_stricmp(argv[i], "-cpu")) g_auto = 0;       /* (the merged windows on the CPU, no graphics card) */
        else if (!_stricmp(argv[i], "-gpu2")) g_auto = 1;      /* (the two GPU passes; still being tested) */
        else if (!_stricmp(argv[i], "-gpuselftest")) return vgpu_fp64_selftest(4000000) == 0 ? 0 : 1;
        else if (!_stricmp(argv[i], "-game") && i + 1 < argc) i++;
        else if (!_stricmp(argv[i], "-radius_override") && i + 1 < argc) radius_override = argv[++i];
        else if (argv[i][0] == '-') { printf("Error: unsupported option %s\n", argv[i]); return 2; }
        else map = argv[i];
    }
    if (!map) { printf("usage: hlvvis [-fast] [-exact | -cpu | -jacobi [-gpu]] [-threads N] [-radius_override R] [-game dir] <map>\n"); return 2; }
    if (getenv("HL_TWO")) g_auto = 0;          /* (the experiments choose for themselves) */
    if (g_auto) g_gpu = 1;
    if (g_merge) g_maxpts = MAXPTS;
    if (getenv("HL_MERGE_EPS")) g_merge_eps = atof(getenv("HL_MERGE_EPS"));
    if (getenv("HL_GROW_EPS")) g_grow_eps = atof(getenv("HL_GROW_EPS"));
    if (getenv("HL_INFLATE")) g_inflate = atof(getenv("HL_INFLATE"));
    if (getenv("HL_SWEEPS")) g_max_sweeps = atoi(getenv("HL_SWEEPS"));
    if (getenv("HL_JACOBI_NOISE")) JACOBI_NOISE = atof(getenv("HL_JACOBI_NOISE"));
    if (g_jacobi) g_robust_sep = 1;
    set_hull_cap(g_jacobi ? 16 : MAXPTS);
    if (getenv("HL_HULL_CAP")) set_hull_cap(atoi(getenv("HL_HULL_CAP")));
    if (getenv("HL_ROBUST_SEP")) g_robust_sep = atoi(getenv("HL_ROBUST_SEP"));
    if (getenv("HL_JACOBI_SATURATE")) JACOBI_SATURATE = atoi(getenv("HL_JACOBI_SATURATE"));
    if (getenv("HL_JACOBI_WIDEN")) JACOBI_WIDEN = atof(getenv("HL_JACOBI_WIDEN"));
    if (getenv("HL_JACOBI_X")) g_jx = atoi(getenv("HL_JACOBI_X"));
    g_jnofilter = getenv("HL_JM_NOFILTER") != NULL; g_jnomore = getenv("HL_JM_NOMORE") != NULL;
    if (getenv("HL_JMIGHT")) { g_jm = 1; g_jcl = getenv("HL_JM_CL") != NULL; }   /* (experiment) one pass, tests = floods */
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
    if (getenv("HL_ROUND_STATS")) { g_jbase_rounds = xcalloc(NPD, sizeof(int)); g_jbase_maxfront = xcalloc(NPD, sizeof(int)); }
    printf("%d portalclusters\n%d numportals\n", NCL, NP_PRT);

    /* ---- rough pass, then the exact one */
    printf("BasePortalVis:       ");
    g_rough_next = 0;
    if (g_gpu && !g_radial) {
        LARGE_INTEGER f0, f1, fq; QueryPerformanceCounter(&f0); QueryPerformanceFrequency(&fq);
        g_front_done = gpu_front();
        QueryPerformanceCounter(&f1);
        if (g_front_done) printf("(rough pass's plane tests on the GPU, %s: %.2f s) ", vgpu_name(),
                                 (double)(f1.QuadPart - f0.QuadPart) / fq.QuadPart);
        else printf("(the GPU couldn't: the CPU does it) ");
    }
    run_threads(rough_worker, threads);
    if (g_jm) {
        g_jw = g_jcl ? (NCL + 63) / 64 : PW;
        if (g_jcl) {                /* (the floods as clusters) */
            g_jv1cl = xcalloc((size_t)NPD * g_jw, 8);
            for (int i = 0; i < NPD; i++) {
                const uint64_t *r = (const uint64_t *)P[i].flood;
                uint64_t *o = g_jv1cl + (size_t)i * g_jw;
                for (int k = 0; k < PW; k++)
                    for (uint64_t w = r[k]; w; w &= w - 1) { int q = k * 64 + __builtin_ctzll(w); o[P[q].leaf >> 6] |= 1ull << (P[q].leaf & 63); }
            }
        }
    }
    if (getenv("HL_MERGE_TEST")) {
        if (threads != 1) fail("HL_MERGE_TEST needs -threads 1%s", "");
        g_mtest_cap = atoi(getenv("HL_MERGE_TEST"));
        if (getenv("HL_MERGE_TEST_EVERY")) g_mtest_every = atoi(getenv("HL_MERGE_TEST_EVERY"));
        g_mtest_rec = xmalloc((size_t)g_mtest_cap * sizeof(vgpu_mrec_t) + 16);
        g_mtest_res = xmalloc((size_t)g_mtest_cap * sizeof(vgpu_mres_t) + 16);
    }
    if (g_auto && !fast && g_gpu) {          /* (fog radius: only the rough pass knows it; the flows read its floods) */
        /* the default: the two GPU passes when the card takes them, else the merged windows on the CPU */
        g_jacobi = 1; g_robust_sep = 1; set_hull_cap(16);
        g_gpu_pipe = gpu_pipe_setup();
        if (g_gpu_pipe) g_two = 1;
        else { g_jacobi = 0; g_robust_sep = 0; set_hull_cap(MAXPTS); printf("(no GPU flow: the merged windows on the CPU) "); }
    } else if (g_gpu && g_jacobi && !fast) {
        if (getenv("HL_GPU_STEPS") || getenv("HL_GPU_CHECK")) {
            /* (dev) only the expansion steps on the GPU, a round at a time (HL_GPU_CHECK=1: each checked on the CPU) */
            g_gpu_check = getenv("HL_GPU_CHECK") != NULL;
            g_gpu_expand = gpu_expand_setup();
            if (!g_gpu_expand) printf("(the GPU couldn't take the expansion steps: the CPU does them) ");
        } else {
            g_gpu_pipe = gpu_pipe_setup();
            if (!g_gpu_pipe) printf("\nWarning: the GPU couldn't take the flow: the CPU does it\n");
        }
    }
    printf("done\n");
    if (fast) {
        for (int i = 0; i < NPD; i++) { memcpy(P[i].vis, P[i].flood, (size_t)PW * 8); P[i].status = 2; }
    } else if (getenv("HL_REPLAY")) {
        g_pdur = xcalloc(NPD, sizeof(double));
        QueryPerformanceFrequency(&g_freq);
        replay(getenv("HL_REPLAY"));
        return 0;
    } else if (getenv("HL_DEBUG_PAIR")) {
        /* (dev) only the flows of the portals leaving clusters A and B: does each side reach the other? (the pair is
           visible only if both do) */
        int A, B;
        sscanf(getenv("HL_DEBUG_PAIR"), "%d %d", &A, &B);
        g_pdur = xcalloc(NPD, sizeof(double));
        QueryPerformanceFrequency(&g_freq);
        int sides[2] = {0, 0};
        for (int side = 0; side < 2; side++) {
            int from = side ? B : A, to = side ? A : B;
            for (int i = 0; i < lf_count[from]; i++) {
                int pi = lf_list[lf_first[from] + i];
                if (g_jacobi) jacobi_flow(pi); else if (g_merge) merged_flow(pi); else portal_flow(pi);
                for (int q = 0; q < NPD; q++)
                    if (P[q].leaf == to && (P[pi].vis[q >> 3] & (1 << (q & 7)))) sides[side] = 1;
            }
        }
        printf("pair %d-%d: %s (%d sees %d: %s, %d sees %d: %s)\n", A, B, sides[0] && sides[1] ? "VISIBLE" : "hidden",
               A, B, sides[0] ? "yes" : "no", B, A, sides[1] ? "yes" : "no");
        return 0;
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
        LARGE_INTEGER fs, fn;
        QueryPerformanceCounter(&fs);
        int gpu_ok = g_gpu_pipe ? gpu_pipe_flow(total_w) : 1;
        if (!gpu_ok && g_two) {
            /* (the default: the merged windows on the CPU, all of it) */
            printf("Warning: the GPU failed during the flow: the merged windows on the CPU do it\n");
            for (int i = 0; i < NPD; i++) { P[i].status = 0; g_pdur[i] = 0; memset(P[i].vis, 0, (size_t)PW * 8); }
            g_done = 0; g_gpu_pipe = 0; g_two = 0;
            g_jacobi = 0; g_robust_sep = 0; set_hull_cap(MAXPTS);
            g_jrounds = g_jexp = g_gpu_items = 0;
        } else if (!gpu_ok) {
            printf("Warning: the GPU failed during the flow: the CPU does the rest\n");
            /* (the bases it finished are right; the rest start again) */
            g_done = 0;
            for (int i = 0; i < NPD; i++)
                if (P[i].status == 2) g_done++;
                else { P[i].status = 0; g_pdur[i] = 0; memset(P[i].vis, 0, (size_t)PW * 8); }
            g_gpu_pipe = 0;
            g_jrounds = g_jexp = g_gpu_items = 0;
        }
        if (!g_gpu_pipe) cpu_flows(threads, total_w, fs);
        if (g_gpu_pipe && (g_two || (getenv("HL_TWO") && atoi(getenv("HL_TWO")) == 4)) && !g_jv1) {
            /* (experiment) the second pass on the GPU: -jacobi with might-see sets over clusters, tests = the first
               pass's vis (the CPU's: HL_TWO=2 HL_JM_CL=1 HL_JM_NOMORE=1) */
            LARGE_INTEGER p1; QueryPerformanceCounter(&p1);
            printf("first pass: %.1f s\n", (double)(p1.QuadPart - fs.QuadPart) / g_freq.QuadPart);
            g_jv1 = xmalloc((size_t)NPD * PW * 8 + 8);
            for (int i = 0; i < NPD; i++) memcpy(g_jv1 + (size_t)i * PW * 8, P[i].vis, (size_t)PW * 8);
            g_jm = 1; g_jcl = 1; g_jnomore = 1; g_jw = (NCL + 63) / 64;
            g_jv1cl = xcalloc((size_t)NPD * g_jw, 8);
            int maxcl = 0;
            for (int i = 0; i < NPD; i++) {
                const uint64_t *r = (const uint64_t *)(g_jv1 + (size_t)i * PW * 8);
                uint64_t *o = g_jv1cl + (size_t)i * g_jw;
                for (int k = 0; k < PW; k++)
                    for (uint64_t w = r[k]; w; w &= w - 1) { int q = k * 64 + __builtin_ctzll(w); o[P[q].leaf >> 6] |= 1ull << (P[q].leaf & 63); }
                int n = 0;
                for (int k = 0; k < g_jw; k++) n += (int)__builtin_popcountll(o[k]);
                if (n > maxcl) maxcl = n;
            }
            g_jmw = (maxcl + 31) / 32;
            if (g_jmw < 1) g_jmw = 1;
            long long nwords = vgpu_pipe_jm(g_jmw, (const uint32_t *)g_jv1cl, NPD, g_jw * 2, NCL);
            printf("second pass on the GPU (sets of up to %d words, room for %lld words)\n", g_jmw, nwords);
            if (nwords <= 0 || (1ll << pool_class((long long)g_gpu_pipe_fmax * g_jmw)) > nwords) fail("the GPU couldn't take the second pass%s", "");
            g_wpool.cap = nwords;
            for (int i = 0; i < NPD; i++) { memset(P[i].vis, 0, (size_t)PW * 8); P[i].status = 0; g_pdur[i] = 0; }
            g_done = 0;
            if (!gpu_pipe_flow(total_w)) {
                /* (the merged windows on the CPU do it all: the fast, known path) */
                printf("Warning: the GPU failed in the second pass: the merged windows on the CPU do the vis\n");
                g_jm = 0; g_jcl = 0; g_jnomore = 0; g_two = 0; g_gpu_pipe = 0;
                free(g_jv1); g_jv1 = NULL;
                g_jacobi = 0; g_robust_sep = 0; set_hull_cap(MAXPTS);
                for (int i = 0; i < NPD; i++) { P[i].status = 0; g_pdur[i] = 0; memset(P[i].vis, 0, (size_t)PW * 8); }
                g_done = 0;
                LARGE_INTEGER f2; QueryPerformanceCounter(&f2);
                cpu_flows(threads, total_w, f2);
            }
            goto passes_done;
        }
        if (getenv("HL_TWO") && !g_jv1) { g_jx = 2; }
        if (g_jacobi && g_jx == 2 && !g_jv1) {
            LARGE_INTEGER p1; QueryPerformanceCounter(&p1);
            printf("first pass: %.1f s\n", (double)(p1.QuadPart - fs.QuadPart) / g_freq.QuadPart);
            if (getenv("HL_TWO") && atoi(getenv("HL_TWO")) == 3) {
                /* (experiment) plain -jacobi again, each base's flood = its first pass's vis; HL_PASSES=n times */
                int npass = getenv("HL_PASSES") ? atoi(getenv("HL_PASSES")) : 1;
                for (int ps = 0; ps < npass; ps++) {
                    long long vis_bits = 0;
                    for (int i = 0; i < NPD; i++) {
                        memcpy(P[i].flood, P[i].vis, (size_t)PW * 8);
                        for (int k = 0; k < PW; k++) vis_bits += __builtin_popcountll(((uint64_t *)P[i].vis)[k]);
                        memset(P[i].vis, 0, (size_t)PW * 8); P[i].status = 0;
                    }
                    g_done = 0; g_next = 0;
                    printf("pass %d (floods = the last pass's vis, %lld bits)\n", ps + 2, vis_bits);
                    HANDLE *hs2 = xmalloc(threads * sizeof(HANDLE));
                    for (int t = 0; t < threads; t++) hs2[t] = CreateThread(NULL, 64 << 20, flow_worker, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
                    for (int t = 0; t < threads; t += 64) WaitForMultipleObjects(threads - t < 64 ? threads - t : 64, hs2 + t, TRUE, INFINITE);
                    for (int t = 0; t < threads; t++) CloseHandle(hs2[t]);
                    free(hs2);
                    LARGE_INTEGER p2; QueryPerformanceCounter(&p2);
                    printf("  done at %.1f s\n", (double)(p2.QuadPart - fs.QuadPart) / g_freq.QuadPart);
                }
                g_jv1 = (uint8_t *)1;   /* (skip the generic second pass below) */
            }
            if (getenv("HL_TWO") && atoi(getenv("HL_TWO")) == 2) {
                g_jm = 1;
                g_jcl = getenv("HL_JM_CL") != NULL;
                g_jw = g_jcl ? (NCL + 63) / 64 : PW;
            }
            else if (getenv("HL_TWO")) { g_jacobi = 0; g_merge = 1; }
            g_gpu_pipe = 0;
            /* (experiment) the second pass, pruned by the first's vis */
            if (g_jv1) goto passes_done;
            g_jv1 = xmalloc((size_t)NPD * PW * 8 + 8);
            for (int i = 0; i < NPD; i++) { memcpy(g_jv1 + (size_t)i * PW * 8, P[i].vis, (size_t)PW * 8); memset(P[i].vis, 0, (size_t)PW * 8); P[i].status = 0; }
            if (getenv("HL_V1_FILE")) {          /* (dev) the tests from a file (HL_DUMP_PVIS's layout) */
                FILE *f = fopen(getenv("HL_V1_FILE"), "rb");
                if (!f || fread(g_jv1, 8, (size_t)NPD * PW, f) != (size_t)NPD * PW) fail("can't read HL_V1_FILE%s", "");
                fclose(f);
            }
            if (g_jcl) {
                g_jv1cl = xcalloc((size_t)NPD * g_jw, 8);
                for (int i = 0; i < NPD; i++) {
                    const uint64_t *r = (const uint64_t *)(g_jv1 + (size_t)i * PW * 8);
                    uint64_t *o = g_jv1cl + (size_t)i * g_jw;
                    for (int k = 0; k < PW; k++)
                        for (uint64_t w = r[k]; w; w &= w - 1) { int q = k * 64 + __builtin_ctzll(w); o[P[q].leaf >> 6] |= 1ull << (P[q].leaf & 63); }
                }
            }
            g_done = 0; g_next = 0;
            printf("second pass (pruned by the first's vis)\n");
            HANDLE *hs = xmalloc(threads * sizeof(HANDLE));
            for (int t = 0; t < threads; t++) {
                hs[t] = CreateThread(NULL, 64 << 20, flow_worker, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
                if (!hs[t]) fail("couldn't start a thread%s", "");
            }
            for (int t = 0; t < threads; t += 64) WaitForMultipleObjects(threads - t < 64 ? threads - t : 64, hs + t, TRUE, INFINITE);
            for (int t = 0; t < threads; t++) CloseHandle(hs[t]);
            free(hs);
            passes_done:;
        }
        if (getenv("HL_DUMP_PVIS")) {         /* (dev) every portal's vis row, PW 64-bit words each */
            FILE *f = fopen(getenv("HL_DUMP_PVIS"), "wb");
            if (f) { for (int i = 0; i < NPD; i++) fwrite(P[i].vis, 8, (size_t)PW, f); fclose(f); }
        }
        QueryPerformanceCounter(&fn);
        double flow_wall = (double)(fn.QuadPart - fs.QuadPart) / g_freq.QuadPart;
        printf("PortalFlow:          done (%.1f s)\n", flow_wall);
        if (g_jacobi && g_jbase_rounds) {
            /* (GPU planning) rounds per base portal and frontier widths, as percentiles */
            int *r = xmalloc(NPD * sizeof(int) + 4), *w = xmalloc(NPD * sizeof(int) + 4);
            memcpy(r, g_jbase_rounds, NPD * sizeof(int)); memcpy(w, g_jbase_maxfront, NPD * sizeof(int));
            qsort(r, NPD, sizeof(int), cmp_int); qsort(w, NPD, sizeof(int), cmp_int);
            printf("rounds per base portal: median %d, 90%% %d, 99%% %d, max %d; widest round per base: median %d, 90%% %d, "
                   "99%% %d, max %d; average round width %.1f\n", r[NPD / 2], r[NPD * 9 / 10], r[NPD * 99 / 100], r[NPD - 1],
                   w[NPD / 2], w[NPD * 9 / 10], w[NPD * 99 / 100], w[NPD - 1], g_jrounds ? (double)g_jexp / g_jrounds : 0);
            free(r); free(w);
        }
        if (g_mtest_cap) {
            /* (self-test) the recorded merges redone on the GPU, compared bit for bit */
            vgpu_mres_t *gres = xmalloc((size_t)g_mtest_n * sizeof(vgpu_mres_t) + 16);
            if (!vgpu_merge_test(g_mtest_n, g_mtest_rec, gres, &g_cap_dir[0][0], g_hull_cap)) printf("merge test: the GPU couldn't run it\n");
            else {
                int bad = 0, firsts = 0, opens = 0, grows = 0, capped = 0;
                for (int i = 0; i < g_mtest_n; i++) {
                    vgpu_mres_t *a = &g_mtest_res[i], *b = &gres[i];
                    if (!g_mtest_rec[i].flags[0]) firsts++;
                    if (a->flags[0]) grows++;
                    if (a->flags[1]) opens++;
                    if (a->counts[0] == g_hull_cap || a->counts[1] == g_hull_cap) capped++;
                    int same = a->flags[0] == b->flags[0] && a->flags[1] == b->flags[1] && a->flags[2] == b->flags[2] &&
                               a->counts[0] == b->counts[0] && a->counts[1] == b->counts[1] &&
                               !memcmp(a->S, b->S, (size_t)a->counts[0] * 16) && !memcmp(a->W, b->W, (size_t)a->counts[1] * 16);
                    if (!same && bad++ < 5)
                        printf("merge %d differs: CPU grew %d opened %d grows %d S %d W %d, GPU grew %d opened %d grows %d S %d W %d\n",
                               i, a->flags[0], a->flags[1], a->flags[2], a->counts[0], a->counts[1],
                               b->flags[0], b->flags[1], b->flags[2], b->counts[0], b->counts[1]);
                }
                printf("merge test: %d merges (%d first arrivals, %d asked to expand, %d opened, %d at the corner cap), "
                       "%d differ from the CPU's\n", g_mtest_n, firsts, grows, opens, capped, bad);
            }
            free(gres);
        }
        if (g_gpu_expand) printf("GPU expansion steps: %lld%s", (long long)g_gpu_items,
                                 g_gpu_check ? "" : "\n");
        if (g_gpu_expand && g_gpu_check) printf(", %lld differ from the CPU's\n", (long long)g_gpu_diff);
        if (g_jm) printf("(might-see sets: %lld expansions with no window growth)\n", (long long)g_jbitsonly);
        if (g_jacobi) printf("rounds: %lld rounds, %lld expansions, %lld portals reached, %lld opened fully (%lld by too "
                             "many arrivals in a round)\n", (long long)g_jrounds, (long long)g_jexp, (long long)g_jreach,
                             (long long)g_jopened, (long long)g_joverflow);
        else if (g_merge) printf("merged windows: %lld steps, %lld pushes, %lld portals opened fully, %lld sweep rounds re-expanding %lld\n",
                            (long long)g_msteps, (long long)g_mpush, (long long)g_capped, (long long)g_sweeps, (long long)g_sweep_pops);
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
