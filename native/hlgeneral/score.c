/* Per cut: what brushfit.CutScorer.score / _score / _side answer (see hlgeneral.h). The structure follows the
 * Python line by line where the answer depends on it (which pieces, which points in which order, which crumb
 * folds where); the sums are made in the same order so the volumes agree to rounding. */
#include "hlgeneral.h"
#ifdef HLG_PROF
#include <windows.h>
double hlg_prof[16];
static double now(void) { LARGE_INTEGER t, f; QueryPerformanceCounter(&t); QueryPerformanceFrequency(&f); return (double)t.QuadPart / (double)f.QuadPart; }
#define TICK(slot) do { double t_ = now(); hlg_prof[slot] += t_ - t_last; t_last = t_; } while (0)
#define TSTART() double t_last = now()
#else
#define TICK(slot) do {} while (0)
#define TSTART() do {} while (0)
#endif
#ifdef HLG_DEBUG
#include <stdio.h>
#define DBG(...) do { fprintf(stderr, __VA_ARGS__); fflush(stderr); } while (0)
#else
#define DBG(...) do {} while (0)
#endif

/* every allocation of one call, freed together */
typedef struct { void **p; i64 n, cap; int oom; } Pool;
static void *pal(Pool *P, size_t n) {
    if (P->oom) return NULL;
    if (P->n == P->cap) {
        i64 cap = P->cap ? 2 * P->cap : 64;
        void **q = realloc(P->p, cap * sizeof(void *));
        if (!q) { P->oom = 1; return NULL; }
        P->p = q; P->cap = cap;
    }
    void *m = xmalloc(n);
    if (!m) { P->oom = 1; return NULL; }
    P->p[P->n++] = m;
    return m;
}
static void pool_free(Pool *P) { for (i64 i = 0; i < P->n; i++) free(P->p[i]); free(P->p); memset(P, 0, sizeof *P); }

#define DET3(a, b, c) ((a)[0] * ((b)[1] * (c)[2] - (b)[2] * (c)[1]) + (a)[1] * ((b)[2] * (c)[0] - (b)[0] * (c)[2]) + (a)[2] * ((b)[0] * (c)[1] - (b)[1] * (c)[0]))

/* an answer: ng groups, each a volume, an air, its points [off[g], off[g+1]) in hull order, hull 1 if the air
 * came from our hull (0: the caller hulls the points itself) */
typedef struct { i64 ng, ngcap; double *vol, *air; i64 *off; int *hull; double *pts; i64 npts, ptscap; } Res;

static int res_groups(Res *r, i64 ng) {
    if (ng > r->ngcap) {
        i64 cap = ng + 8;
        double *v = realloc(r->vol, cap * sizeof(double)), *a = realloc(r->air, cap * sizeof(double));
        i64 *o = realloc(r->off, (cap + 1) * sizeof(i64)); int *hl = realloc(r->hull, cap * sizeof(int));
        if (v) r->vol = v; if (a) r->air = a; if (o) r->off = o; if (hl) r->hull = hl;
        if (!v || !a || !o || !hl) return 0;
        r->ngcap = cap;
    }
    r->ng = ng;
    return 1;
}
static int res_points(Res *r, i64 npts) {
    if (npts > r->ptscap) {
        i64 cap = npts + npts / 4 + 64;
        double *p = realloc(r->pts, 3 * cap * sizeof(double));
        if (!p) return 0;
        r->pts = p; r->ptscap = cap;
    }
    r->npts = npts;
    return 1;
}

/* the label along the tour from a laminar family of intervals (_events): breakpoints (pos, lab), the label at
 * tour position x is that of the last breakpoint at or before x (-1 before the first) */
typedef struct { i64 tin, tout, lab; } Iv;
static int cmp_iv(const void *a, const void *b) {
    const Iv *x = a, *y = b;
    if (x->tin != y->tin) return x->tin < y->tin ? -1 : 1;
    return x->tout > y->tout ? -1 : (x->tout < y->tout);        /* (equal starts: the outer first) */
}
typedef struct { i64 *pos, *lab, n; } Ev;
static int events(Pool *P, Iv *iv, i64 k, Ev *ev) {
    qsort(iv, k, sizeof *iv, cmp_iv);
    ev->pos = pal(P, (2 * k + 1) * sizeof(i64)); ev->lab = pal(P, (2 * k + 1) * sizeof(i64));
    i64 *st = pal(P, (k + 1) * sizeof(i64));
    if (!ev->pos || !ev->lab || !st) return 0;
    i64 sp = 0, n = 0;
    for (i64 i = 0; i < k; i++) {
        while (sp && iv[st[sp - 1]].tout <= iv[i].tin) {
            sp--;
            ev->pos[n] = iv[st[sp]].tout; ev->lab[n] = sp ? iv[st[sp - 1]].lab : -1; n++;
        }
        st[sp++] = i;
        ev->pos[n] = iv[i].tin; ev->lab[n] = iv[i].lab; n++;
    }
    while (sp) { sp--; ev->pos[n] = iv[st[sp]].tout; ev->lab[n] = sp ? iv[st[sp - 1]].lab : -1; n++; }
    ev->n = n;
    return 1;
}
static i64 lab_at(const Ev *ev, i64 x) {
    i64 lo = 0, hi = ev->n;                 /* the last pos <= x */
    while (lo < hi) { i64 mid = (lo + hi) / 2; if (ev->pos[mid] <= x) lo = mid + 1; else hi = mid; }
    return lo ? ev->lab[lo - 1] : -1;
}

static i64 uf_find(i64 *p, i64 x) { while (p[x] != x) { p[x] = p[p[x]]; x = p[x]; } return x; }
static void uf_union(i64 *p, i64 a, i64 b) { a = uf_find(p, a); b = uf_find(p, b); if (a != b) p[a > b ? a : b] = a > b ? b : a; }

typedef struct {
    int sgn;
    i64 nc;
    double *vol;        /* nc */
    i64 *cc;            /* per crossed triangle: its piece */
    i64 *shells;        /* nc */
    i64 *uf;            /* nc */
    i64 *vlab_s;        /* nv, hull order: piece or -1 */
    i64 *xcomp;         /* per crossing edge */
} Side;

typedef struct {
    HG *h; Pool *P;
    const double *n; double d;
    i64 nct; i64 *ct; i64 *Tr; i64 *Er; unsigned char *one;     /* crossed triangles, rotated */
    i64 k; i64 *cross; double *X;                               /* crossing edges and points */
    double *wp, *wm, *wcap; i64 *c_from, *c_to;
    i64 nl; i64 *seg_loop, *loop_seg; double *area, *fu, *fv, *gu, *gv;
    i64 nntc; i64 *ntc;
} Cut;

static int side_build(Cut *C, const unsigned char *posS, const unsigned char *posS_s, const double *wpart, int capsign, Side *S) {
    HG *h = C->h; Pool *P = C->P;
    i64 nv = h->nv, ne = h->ne, nct = C->nct;
    const i64 *T = h->T, *ea = h->ea, *eb = h->eb, *et = h->et, *tin = h->tin, *tout = h->tout, *tree_edge = h->tree_edge;
    const double *pre = h->pre;
#define EACT(e) (posS[ea[e]] || posS[eb[e]])
    /* roots and hangers */
    i64 nR = 0, nH = 0;
    i64 *R = pal(P, (nct + h->nshell) * sizeof(i64));
    i64 *H = pal(P, 3 * nct * sizeof(i64)), *Hc = pal(P, 3 * nct * sizeof(i64));
    if (!R || !H || !Hc) return -3;
    for (i64 i = 0; i < nct; i++) {
        i64 t = C->ct[i], tec = tree_edge[t];
        if (tec == ne || !EACT(tec)) R[nR++] = t;
        for (int j = 0; j < 3; j++) {
            i64 e = h->eid[3 * t + j];
            i64 other = et[2 * e] + et[2 * e + 1] - t;
            if (!EACT(e) && tree_edge[other] == e) { H[nH] = other; Hc[nH] = i; nH++; }
        }
    }
    for (i64 r = 0; r < h->nshell; r++) {
        i64 t = h->roots[r];
        if (posS[T[3 * t]] && posS[T[3 * t + 1]] && posS[T[3 * t + 2]]) R[nR++] = t;
    }
    {
        KI *o = pal(P, (nR ? nR : 1) * sizeof(KI));
        if (!o) return -3;
        for (i64 i = 0; i < nR; i++) { o[i].key = tin[R[i]]; o[i].idx = R[i]; }
        qsort(o, nR, sizeof *o, cmp_ki);
        for (i64 i = 0; i < nR; i++) R[i] = o[i].idx;
    }
    Iv *iv = pal(P, (nR + nH + 1) * sizeof(Iv));
    if (!iv) return -3;
    i64 niv = 0;
    for (i64 r = 0; r < nR; r++) { iv[niv].tin = tin[R[r]]; iv[niv].tout = tout[R[r]]; iv[niv].lab = r; niv++; }
    for (i64 i = 0; i < nH; i++) {
        i64 t = H[i];
        if (posS[T[3 * t]] || posS[T[3 * t + 1]] || posS[T[3 * t + 2]]) continue;
        iv[niv].tin = tin[t]; iv[niv].tout = tout[t]; iv[niv].lab = -1; niv++;
    }
    Ev ev;
    if (!events(P, iv, niv, &ev)) return -3;
    i64 *root_c = pal(P, (nct + 1) * sizeof(i64));
    if (!root_c) return -3;
    for (i64 i = 0; i < nct; i++) {
        root_c[i] = lab_at(&ev, tin[C->ct[i]]);
        if (root_c[i] < 0) return -2;                      /* (a crossed triangle without a root) */
    }
    /* joins across the non-tree edges whose tree cycle reaches the plane */
    i64 *ufr = pal(P, (nR + 1) * sizeof(i64));
    if (!ufr) return -3;
    for (i64 r = 0; r < nR; r++) ufr[r] = r;
    for (i64 i = 0; i < C->nntc; i++) {
        i64 e = C->ntc[i];
        if (!EACT(e)) continue;
        i64 ra = lab_at(&ev, tin[et[2 * e]]), rb = lab_at(&ev, tin[et[2 * e + 1]]);
        if (ra < 0 || rb < 0 || ra == rb) continue;
        uf_union(ufr, ra, rb);
    }
#ifdef HLG_DEBUG
    {   /* every non-tree edge with an end on this side whose triangles' regions differ: is it among ntc? */
        i64 missed = 0, joins = 0;
        unsigned char *inntc = calloc(ne, 1);
        for (i64 i = 0; i < C->nntc; i++) inntc[C->ntc[i]] = 1;
        for (i64 q = 0; q < h->nnt; q++) {
            i64 e = h->nontree[q];
            if (!EACT(e)) continue;
            i64 ra = lab_at(&ev, tin[et[2 * e]]), rb = lab_at(&ev, tin[et[2 * e + 1]]);
            if (ra != rb) { joins++; if (!inntc[e]) { missed++; if (missed <= 5) DBG("  join edge %lld (%lld,%lld) regions %lld %lld not in ntc; box %.3f..%.3f straddle %d\n", (long long)e, (long long)et[2*e], (long long)et[2*e+1], (long long)ra, (long long)rb, h->plo[3*q], h->phi[3*q], straddle(h->plo + 3 * q, h->phi + 3 * q, C->n, C->d, 1e-9 * (h->scale > 1.0 ? h->scale : 1.0))); } }
        }
        DBG("  joins %lld, missed by ntc %lld\n", (long long)joins, (long long)missed);
        free(inntc);
    }
#endif
    i64 *cmin = pal(P, (nR + 1) * sizeof(i64)), *compR = pal(P, (nR + 1) * sizeof(i64));
    if (!cmin || !compR) return -3;
    for (i64 r = 0; r < nR; r++) cmin[r] = -1;
    for (i64 r = 0; r < nR; r++) { i64 g = uf_find(ufr, r); if (cmin[g] < 0 || R[r] < cmin[g]) cmin[g] = R[r]; }
    i64 *comps = pal(P, (nR + 1) * sizeof(i64));
    if (!comps) return -3;
    i64 nc = 0;
    for (i64 r = 0; r < nR; r++) if (uf_find(ufr, r) == r) comps[nc++] = cmin[r];
    qsort(comps, nc, sizeof(i64), cmp_i64);
    for (i64 r = 0; r < nR; r++) {
        i64 key = cmin[uf_find(ufr, r)];
        i64 *p = bsearch(&key, comps, nc, sizeof(i64), cmp_i64);
        if (!p) { DBG("compR: no comp\n"); return -2; }
        compR[r] = p - comps;
    }
    /* volumes */
    double *Sv = pal(P, (nR + 1) * sizeof(double)), *hang = pal(P, (nR + 1) * sizeof(double)), *crs = pal(P, (nR + 1) * sizeof(double));
    i64 *ntri = pal(P, (nR + 1) * sizeof(i64));
    if (!Sv || !hang || !crs || !ntri) return -3;
    for (i64 r = 0; r < nR; r++) { Sv[r] = pre[tout[R[r]]] - pre[tin[R[r]]]; hang[r] = 0; crs[r] = 0; ntri[r] = tout[R[r]] - tin[R[r]]; }
    for (i64 i = 0; i < nH; i++) { i64 r = root_c[Hc[i]]; hang[r] += pre[tout[H[i]]] - pre[tin[H[i]]]; ntri[r] -= tout[H[i]] - tin[H[i]]; }
    for (i64 i = 0; i < nct; i++) { i64 r = root_c[i]; crs[r] += h->w[C->ct[i]]; ntri[r] -= 1; }
    for (i64 r = 0; r < nR; r++) { Sv[r] = (Sv[r] - hang[r]) - crs[r]; if (ntri[r] == 0) Sv[r] = 0.0; }
    S->nc = nc;
    S->vol = pal(P, (nc + 1) * sizeof(double)); S->cc = pal(P, (nct + 1) * sizeof(i64)); S->shells = pal(P, (nc + 1) * sizeof(i64));
    S->uf = pal(P, (nc + 1) * sizeof(i64)); S->vlab_s = pal(P, (nv + 1) * sizeof(i64)); S->xcomp = pal(P, (C->k + 1) * sizeof(i64));
    double *a = pal(P, (nc + 1) * sizeof(double)), *b = pal(P, (nc + 1) * sizeof(double)), *c = pal(P, (nc + 1) * sizeof(double));
    if (!S->vol || !S->cc || !S->shells || !S->uf || !S->vlab_s || !S->xcomp || !a || !b || !c) return -3;
    for (i64 q = 0; q < nc; q++) { a[q] = 0; b[q] = 0; c[q] = 0; S->uf[q] = q; S->shells[q] = h->shell[comps[q]]; }
    for (i64 r = 0; r < nR; r++) a[compR[r]] += Sv[r];
    for (i64 i = 0; i < nct; i++) { S->cc[i] = compR[root_c[i]]; b[S->cc[i]] += wpart[i]; }
    for (i64 i = 0; i < nct; i++) c[S->cc[i]] += capsign > 0 ? C->wcap[i] : -C->wcap[i];
    for (i64 q = 0; q < nc; q++) S->vol[q] = ((0.0 + a[q]) + b[q]) + c[q];
#ifdef HLG_DEBUG
    for (i64 q = 0; q < nc; q++) DBG("  piece %lld: vol %.6f (root tri %lld)\n", (long long)q, S->vol[q], (long long)comps[q]);
#endif
    /* the vertices' pieces, in hull order */
    {
        i64 j = 0;      /* (the breakpoint in force) */
        for (i64 i = 0; i < nv; i++) {
            i64 x = h->vtin_s[i];
            while (j < ev.n && ev.pos[j] <= x) j++;
            i64 l = j ? ev.lab[j - 1] : -1;
            S->vlab_s[h->tour_to_sorted[i]] = l >= 0 ? compR[l] : -1;
        }
        for (i64 i = 0; i < nv; i++) if (!posS_s[i]) S->vlab_s[i] = -1;
#ifdef HLG_DEBUG
        { i64 miss = 0; for (i64 i = 0; i < nv; i++) if (posS_s[i] && S->vlab_s[i] < 0) miss++;
          DBG("side: nR %lld nH %lld nc %lld, vertices on the side without a label: %lld\n", (long long)nR, (long long)nH, (long long)nc, (long long)miss); }
#endif
    }
    for (i64 j = 0; j < C->k; j++) {
        i64 t = et[2 * C->cross[j]];
        i64 *p = bsearch(&t, C->ct, nct, sizeof(i64), cmp_i64);
        if (!p) { DBG("xcomp: triangle %lld of crossing edge %lld not crossed\n", (long long)t, (long long)C->cross[j]); return -2; }
        S->xcomp[j] = S->cc[p - C->ct];
    }
#undef EACT
    return 0;
}

typedef struct { double vol; i64 off, n; double ext; int sgn; i64 gkey; i64 kidx; } Part;

static uint64_t gridkey(const double *p) {
    uint64_t g0 = (uint64_t)(i64)nearbyint(p[0] * 1e6), g1 = (uint64_t)(i64)nearbyint(p[1] * 1e6), g2 = (uint64_t)(i64)nearbyint(p[2] * 1e6);
    return (g0 * 1000003ULL + g1) * 1000033ULL + g2;
}

/* _score: the answer with each vertex's side given. -> 1 parts, 0 None, -2 error, -3 out of memory */
static int score_sides(HG *h, const double *n, double d, const unsigned char *pos, const unsigned char *pos_s, const i64 *nearT, i64 nnear,
                       double D, double sliver, double pvol, i64 max_shards, Res *res) {
    Pool pool; memset(&pool, 0, sizeof pool);
    Pool *P = &pool;
    Cut C; memset(&C, 0, sizeof C);
    C.h = h; C.P = P; C.n = n; C.d = d;
    const double *V = h->V, *s = h->s; const i64 *T = h->T, *ea = h->ea, *eb = h->eb;
    i64 nv = h->nv;
    int rc = -3;
    double *ppts = NULL; i64 nppts = 0, cppts = 0;        /* the points of all parts, in order */
    TSTART();
#define FAIL(code) do { rc = (code); goto out; } while (0)
#define NEED(p) do { if (!(p)) FAIL(-3); } while (0)
    /* the crossed triangles, turned so the pattern is + - - or + + - */
    C.ct = pal(P, (nnear + 1) * sizeof(i64)); C.Tr = pal(P, 3 * (nnear + 1) * sizeof(i64)); C.Er = pal(P, 3 * (nnear + 1) * sizeof(i64));
    C.one = pal(P, nnear + 1);
    NEED(C.ct && C.Tr && C.Er && C.one);
    i64 nct = 0;
    for (i64 q = 0; q < nnear; q++) {
        i64 t = nearT[q];
        int pa[3] = {pos[T[3 * t]], pos[T[3 * t + 1]], pos[T[3 * t + 2]]};
        int np = pa[0] + pa[1] + pa[2];
        if (np != 1 && np != 2) continue;
        int r;
        if (np == 1) r = pa[0] ? 0 : (pa[1] ? 1 : 2);
        else r = ((!pa[0] ? 0 : (!pa[1] ? 1 : 2)) + 1) % 3;
        C.ct[nct] = t; C.one[nct] = np == 1;
        for (int j = 0; j < 3; j++) { C.Tr[3 * nct + j] = T[3 * t + (r + j) % 3]; C.Er[3 * nct + j] = h->eid[3 * t + (r + j) % 3]; }
        nct++;
    }
    C.nct = nct;
    if (nct == 0) FAIL(0);
    double tol = 1e-9 * (h->scale > 1.0 ? h->scale : 1.0);
    TICK(0);
    /* the non-tree edges whose cycle box reaches the plane */
    {
        i64 *nti = NULL;
        i64 nn = cellidx_near(&h->ec, n, d, tol, &nti, 0);
        NEED(nn >= 0);
        C.ntc = pal(P, (nn + 1) * sizeof(i64));
        if (!C.ntc) { free(nti); FAIL(-3); }
        C.nntc = 0;
        for (i64 i = 0; i < nn; i++) {
            i64 q = nti[i];
            if (straddle(h->plo + 3 * q, h->phi + 3 * q, n, d, tol)) C.ntc[C.nntc++] = h->nontree[q];
        }
        free(nti);
    }
    TICK(1);
    /* the crossing edges (sorted, unique) and their points */
    i64 *cand = pal(P, 3 * nct * sizeof(i64));
    NEED(cand);
    i64 ncand = 0;
    for (i64 i = 0; i < nct; i++) for (int j = 0; j < 3; j++) {
        i64 e = h->eid[3 * C.ct[i] + j];
        if (pos[ea[e]] != pos[eb[e]]) cand[ncand++] = e;
    }
    qsort(cand, ncand, sizeof(i64), cmp_i64);
    C.cross = pal(P, (ncand + 1) * sizeof(i64));
    NEED(C.cross);
    i64 k = 0;
    for (i64 i = 0; i < ncand; i++) if (i == 0 || cand[i] != cand[i - 1]) C.cross[k++] = cand[i];
    C.k = k;
    for (i64 j = 0; j < k; j++) h->cid[C.cross[j]] = j;
    C.X = pal(P, 3 * (k + 1) * sizeof(double));
    NEED(C.X);
    for (i64 j = 0; j < k; j++) {
        i64 e = C.cross[j], a = ea[e], b = eb[e];
        double sa = s[a], sb = s[b], den = sa - sb;
        double t = den != 0 ? sa / den : (pos[a] ? 0.0 : 1.0);
        for (int q = 0; q < 3; q++) C.X[3 * j + q] = V[3 * a + q] + t * (V[3 * b + q] - V[3 * a + q]);
    }
    C.wp = pal(P, (nct + 1) * sizeof(double)); C.wm = pal(P, (nct + 1) * sizeof(double)); C.wcap = pal(P, (nct + 1) * sizeof(double));
    C.c_from = pal(P, (nct + 1) * sizeof(i64)); C.c_to = pal(P, (nct + 1) * sizeof(i64));
    NEED(C.wp && C.wm && C.wcap && C.c_from && C.c_to);
    double o[3] = {n[0] * d, n[1] * d, n[2] * d};
    for (i64 i = 0; i < nct; i++) {
        const double *Qa = V + 3 * C.Tr[3 * i], *Qb = V + 3 * C.Tr[3 * i + 1], *Qc = V + 3 * C.Tr[3 * i + 2];
        i64 e0 = h->cid[C.Er[3 * i]], e1 = h->cid[C.Er[3 * i + 1]], e2 = h->cid[C.Er[3 * i + 2]];
        if (e0 < 0) e0 = k - 1; if (e1 < 0) e1 = k - 1; if (e2 < 0) e2 = k - 1;    /* (an uncrossed edge's slot is never used) */
        const double *Xab = C.X + 3 * e0, *Xbc = C.X + 3 * e1, *Xca = C.X + 3 * e2;
        if (C.one[i]) {
            C.wp[i] = DET3(Qa, Xab, Xca) / 6.0;
            C.wm[i] = (DET3(Qb, Qc, Xca) + DET3(Qb, Xca, Xab)) / 6.0;
            C.c_from[i] = e0;
        } else {
            C.wp[i] = (DET3(Qa, Qb, Xbc) + DET3(Qa, Xbc, Xca)) / 6.0;
            C.wm[i] = DET3(Qc, Xca, Xbc) / 6.0;
            C.c_from[i] = e1;
        }
        C.c_to[i] = e2;
        const double *Xf = C.X + 3 * C.c_from[i], *Xt = C.X + 3 * C.c_to[i];
        double cr[3] = {Xt[1] * Xf[2] - Xt[2] * Xf[1], Xt[2] * Xf[0] - Xt[0] * Xf[2], Xt[0] * Xf[1] - Xt[1] * Xf[0]};
        C.wcap[i] = ((o[0] * cr[0] + o[1] * cr[1]) + o[2] * cr[2]) / 6.0;
    }
    TICK(2);
    /* the section loops: crossing edges chained by the segments, labelled by their least edge */
    {
        i64 *uf = pal(P, (k + 1) * sizeof(i64)), *lmin = pal(P, (k + 1) * sizeof(i64)), *labs = pal(P, (nct + 1) * sizeof(i64));
        NEED(uf && lmin && labs);
        for (i64 j = 0; j < k; j++) uf[j] = j;
        for (i64 i = 0; i < nct; i++) uf_union(uf, C.c_from[i], C.c_to[i]);
        for (i64 j = 0; j < k; j++) lmin[j] = -1;
        for (i64 j = 0; j < k; j++) { i64 g = uf_find(uf, j); if (lmin[g] < 0) lmin[g] = j; }   /* (ascending: the first is the least) */
        for (i64 i = 0; i < nct; i++) labs[i] = lmin[uf_find(uf, C.c_from[i])];
        i64 *u = pal(P, (nct + 1) * sizeof(i64));
        NEED(u);
        memcpy(u, labs, nct * sizeof(i64));
        qsort(u, nct, sizeof(i64), cmp_i64);
        i64 nl = 0;
        for (i64 i = 0; i < nct; i++) if (i == 0 || u[i] != u[i - 1]) u[nl++] = u[i];
        C.nl = nl;
        C.seg_loop = pal(P, (nct + 1) * sizeof(i64)); C.loop_seg = pal(P, (nl + 1) * sizeof(i64));
        NEED(C.seg_loop && C.loop_seg);
        for (i64 L = 0; L < nl; L++) C.loop_seg[L] = -1;
        for (i64 i = 0; i < nct; i++) {
            i64 *p = bsearch(&labs[i], u, nl, sizeof(i64), cmp_i64);
            if (!p) { DBG("seg_loop: no loop\n"); FAIL(-2); }
            C.seg_loop[i] = p - u;
            if (C.loop_seg[C.seg_loop[i]] < 0) C.loop_seg[C.seg_loop[i]] = i;
        }
    }
    i64 nl = C.nl;
    double ax[3] = {1, 0, 0};
    if (!(fabs(n[0]) < 0.9)) { ax[0] = 0; ax[1] = 1; }
    double u[3] = {n[1] * ax[2] - n[2] * ax[1], n[2] * ax[0] - n[0] * ax[2], n[0] * ax[1] - n[1] * ax[0]};
    { double un = sqrt((u[0] * u[0] + u[1] * u[1]) + u[2] * u[2]); u[0] /= un; u[1] /= un; u[2] /= un; }
    double v[3] = {n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]};
    C.fu = pal(P, (nct + 1) * sizeof(double)); C.fv = pal(P, (nct + 1) * sizeof(double));
    C.gu = pal(P, (nct + 1) * sizeof(double)); C.gv = pal(P, (nct + 1) * sizeof(double));
    C.area = pal(P, (nl + 1) * sizeof(double));
    NEED(C.fu && C.fv && C.gu && C.gv && C.area);
    for (i64 L = 0; L < nl; L++) C.area[L] = 0;
    for (i64 i = 0; i < nct; i++) {
        const double *Xf = C.X + 3 * C.c_from[i], *Xt = C.X + 3 * C.c_to[i];
        double F[3] = {Xf[0] - o[0], Xf[1] - o[1], Xf[2] - o[2]}, G[3] = {Xt[0] - o[0], Xt[1] - o[1], Xt[2] - o[2]};
        C.fu[i] = (F[0] * u[0] + F[1] * u[1]) + F[2] * u[2]; C.fv[i] = (F[0] * v[0] + F[1] * v[1]) + F[2] * v[2];
        C.gu[i] = (G[0] * u[0] + G[1] * u[1]) + G[2] * u[2]; C.gv[i] = (G[0] * v[0] + G[1] * v[1]) + G[2] * v[2];
        C.area[C.seg_loop[i]] += (C.fu[i] * C.gv[i] - C.fv[i] * C.gu[i]) / 2.0;
    }
    DBG("nct %lld k %lld nl %lld nntc %lld\n", (long long)nct, (long long)k, (long long)nl, (long long)C.nntc);
    TICK(3);
    /* each side's pieces */
    Side sides[2];
    memset(sides, 0, sizeof sides);
    unsigned char *neg = pal(P, nv + 1), *neg_s = pal(P, nv + 1);
    NEED(neg && neg_s);
    for (i64 i = 0; i < nv; i++) { neg[i] = !pos[i]; neg_s[i] = !pos_s[i]; }
    for (int sd = 0; sd < 2; sd++) {
        sides[sd].sgn = sd == 0 ? 1 : -1;
        int r = side_build(&C, sd == 0 ? pos : neg, sd == 0 ? pos_s : neg_s, sd == 0 ? C.wp : C.wm, sd == 0 ? 1 : -1, &sides[sd]);
        if (r) FAIL(r);
    }
    TICK(4);
    DBG("sides nc %lld %lld\n", (long long)sides[0].nc, (long long)sides[1].nc);
    i64 *lp = pal(P, (nl + 1) * sizeof(i64)), *lm = pal(P, (nl + 1) * sizeof(i64));
    NEED(lp && lm);
    for (i64 L = 0; L < nl; L++) { lp[L] = sides[0].cc[C.loop_seg[L]]; lm[L] = sides[1].cc[C.loop_seg[L]]; }
    /* holes: loops winding against the outermost; each joins the smallest outer loop around it */
    {
        i64 big = 0;
        for (i64 L = 1; L < nl; L++) if (fabs(C.area[L]) > fabs(C.area[big])) big = L;
        double osg = C.area[big] >= 0 ? 1.0 : -1.0;
        double sc = h->scale > 1.0 ? h->scale : 1.0;
        double eps_a = 1e-12 * sc * sc;
        i64 nholes = 0, nouters = 0;
        i64 *holes = pal(P, (nl + 1) * sizeof(i64)), *outers = pal(P, (nl + 1) * sizeof(i64)), *cnt = pal(P, (nl + 1) * sizeof(i64));
        NEED(holes && outers && cnt);
        for (i64 L = 0; L < nl; L++) if (C.area[L] * osg < -eps_a) holes[nholes++] = L;
        if (nholes) {
            for (i64 L = 0; L < nl; L++) if (C.area[L] * osg > eps_a) outers[nouters++] = L;
            for (i64 hi = 0; hi < nholes; hi++) {
                i64 hL = holes[hi], oo = -1;
                if (nouters == 1) oo = outers[0];
                else if (nouters > 1) {
                    double qu = C.fu[C.loop_seg[hL]], qv = C.fv[C.loop_seg[hL]];
                    for (i64 L = 0; L < nl; L++) cnt[L] = 0;
                    for (i64 i = 0; i < nct; i++) {
                        double y0 = C.fv[i], y1 = C.gv[i], x0 = C.fu[i], x1 = C.gu[i];
                        if ((y0 > qv) != (y1 > qv)) {
                            double xi = x0 + (qv - y0) * (x1 - x0) / (y1 - y0);
                            if (xi > qu) cnt[C.seg_loop[i]]++;
                        }
                    }
                    for (i64 q = 0; q < nouters; q++) {
                        i64 L = outers[q];
                        if (cnt[L] % 2 == 1 && (oo < 0 || fabs(C.area[L]) < fabs(C.area[oo]))) oo = L;
                    }
                }
                if (oo >= 0) { uf_union(sides[0].uf, lp[hL], lp[oo]); uf_union(sides[1].uf, lm[hL], lm[oo]); }
            }
        }
    }
    TICK(5);
    /* the parts: per side, the groups of pieces */
    i64 maxparts = sides[0].nc + sides[1].nc;
    Part *parts = pal(P, (maxparts + 1) * sizeof(Part));
    i64 *part_of[2];
    part_of[0] = pal(P, (sides[0].nc + 1) * sizeof(i64)); part_of[1] = pal(P, (sides[1].nc + 1) * sizeof(i64));
    NEED(parts && part_of[0] && part_of[1]);
    i64 nparts = 0;
    i64 *xo = pal(P, (k + 1) * sizeof(i64)); double *xkey = pal(P, (k + 1) * sizeof(double));
    NEED(xo && xkey);
    {
        KD *kd = pal(P, (k + 1) * sizeof(KD));
        NEED(kd);
        for (i64 j = 0; j < k; j++) { xkey[j] = (C.X[3 * j] * SORT0 + C.X[3 * j + 1] * SORT1) + C.X[3 * j + 2] * SORT2; kd[j].key = xkey[j]; kd[j].idx = j; }
        qsort(kd, k, sizeof *kd, cmp_kd);
        for (i64 j = 0; j < k; j++) xo[j] = kd[j].idx;
    }
    for (int sd = 0; sd < 2; sd++) {
        Side *S = &sides[sd];
        i64 nc = S->nc;
        for (i64 c = 0; c < nc; c++) part_of[sd][c] = -1;
        /* groups in order of first appearance: key, members (a list), dead */
        i64 *gkey = pal(P, (nc + 1) * sizeof(i64)), *gn = pal(P, (nc + 1) * sizeof(i64)), *gmem = pal(P, (nc + 1) * (nc + 1) * sizeof(i64));
        unsigned char *gdead = pal(P, nc + 1);
        NEED(gkey && gn && gmem && gdead);
        i64 ng = 0;
        i64 *gof = pal(P, (nc + 1) * sizeof(i64));      /* group index of a uf root */
        NEED(gof);
        for (i64 c = 0; c < nc; c++) gof[c] = -1;
        for (i64 c = 0; c < nc; c++) {
            i64 g = uf_find(S->uf, c);
            if (gof[g] < 0) { gof[g] = ng; gkey[ng] = g; gn[ng] = 0; gdead[ng] = 0; ng++; }
            i64 gi = gof[g];
            gmem[gi * (nc + 1) + gn[gi]++] = c;
        }
        int anyneg = 0;
        for (i64 c = 0; c < nc; c++) if (S->vol[c] < -h->vnoise) anyneg = 1;
        if (anyneg) {
            double *lo = pal(P, 3 * (nc + 1) * sizeof(double)), *hi = pal(P, 3 * (nc + 1) * sizeof(double));
            NEED(lo && hi);
            for (i64 c = 0; c < nc; c++) for (int q = 0; q < 3; q++) { lo[3 * c + q] = INFINITY; hi[3 * c + q] = -INFINITY; }
            for (i64 i = 0; i < nv; i++) {
                i64 l = S->vlab_s[i];
                if (l < 0) continue;
                const double *p = h->Vs + 3 * i;
                for (int q = 0; q < 3; q++) { if (p[q] < lo[3 * l + q]) lo[3 * l + q] = p[q]; if (p[q] > hi[3 * l + q]) hi[3 * l + q] = p[q]; }
            }
            for (i64 j = 0; j < k; j++) {
                i64 l = S->xcomp[j]; const double *p = C.X + 3 * j;
                for (int q = 0; q < 3; q++) { if (p[q] < lo[3 * l + q]) lo[3 * l + q] = p[q]; if (p[q] > hi[3 * l + q]) hi[3 * l + q] = p[q]; }
            }
            for (i64 c = 0; c < nc; c++) {
                if (!(S->vol[c] < -h->vnoise)) continue;
                i64 g = uf_find(S->uf, c), gi = gof[g];
                if (gi >= 0 && !gdead[gi] && gn[gi] > 1) continue;      /* (already joined through the section) */
                double bestv = 0; i64 bestg = -1;
                for (i64 g2 = 0; g2 < ng; g2++) {
                    if (g2 == gi || gdead[g2]) continue;
                    double sv = 0; double glo[3] = {INFINITY, INFINITY, INFINITY}, ghi[3] = {-INFINITY, -INFINITY, -INFINITY};
                    for (i64 m = 0; m < gn[g2]; m++) {
                        i64 cm = gmem[g2 * (nc + 1) + m];
                        sv += S->vol[cm];
                        for (int q = 0; q < 3; q++) { if (lo[3 * cm + q] < glo[q]) glo[q] = lo[3 * cm + q]; if (hi[3 * cm + q] > ghi[q]) ghi[q] = hi[3 * cm + q]; }
                    }
                    if (sv <= 0) continue;
                    int holds = 1;
                    for (int q = 0; q < 3; q++) if (!(glo[q] <= lo[3 * c + q] + 1e-6) || !(ghi[q] >= hi[3 * c + q] - 1e-6)) holds = 0;
                    if (!holds) continue;
                    if (bestg < 0 || sv < bestv || (sv == bestv && gkey[g2] < gkey[bestg])) { bestv = sv; bestg = g2; }
                }
                if (gi >= 0) gdead[gi] = 1;
                if (bestg >= 0) gmem[bestg * (nc + 1) + gn[bestg]++] = c;
            }
        }
        i64 *gid = pal(P, (nc + 1) * sizeof(i64));
        NEED(gid);
        for (i64 c = 0; c < nc; c++) gid[c] = -1;
        i64 nlive = 0;
        for (i64 g = 0; g < ng; g++) {
            if (gdead[g]) continue;
            nlive++;
            for (i64 m = 0; m < gn[g]; m++) gid[gmem[g * (nc + 1) + m]] = gkey[g];
        }
        /* the live groups worth a part, in order; each piece's part slot (every labelled piece's when there is
         * one group: Python takes every vertex of the side then, dropped hollows included) */
        i64 *gslot = pal(P, (ng + 1) * sizeof(i64)), *pslot = pal(P, (nc + 1) * sizeof(i64));
        double *gvol = pal(P, (ng + 1) * sizeof(double));
        NEED(gslot && pslot && gvol);
        i64 nslots = 0, only = -1;
        for (i64 g = 0; g < ng; g++) {
            gslot[g] = -1;
            if (gdead[g]) continue;
            double v_ = 0;
            for (i64 m = 0; m < gn[g]; m++) v_ += S->vol[gmem[g * (nc + 1) + m]];
            gvol[g] = v_;
            if (v_ <= 1e-3) continue;
            gslot[g] = nslots++;
            only = g;
        }
        for (i64 c = 0; c < nc; c++) pslot[c] = nlive == 1 ? gslot[only] : (gid[c] >= 0 ? gslot[gof[gid[c]]] : -1);
        i64 *cntv = pal(P, (nslots + 1) * sizeof(i64)), *cntx = pal(P, (nslots + 1) * sizeof(i64));
        i64 *offv = pal(P, (nslots + 2) * sizeof(i64)), *offx = pal(P, (nslots + 2) * sizeof(i64));
        NEED(cntv && cntx && offv && offx);
        for (i64 q = 0; q < nslots; q++) { cntv[q] = 0; cntx[q] = 0; }
        for (i64 i = 0; i < nv; i++) { i64 l = S->vlab_s[i]; if (l >= 0 && pslot[l] >= 0) cntv[pslot[l]]++; }
        for (i64 q = 0; q < k; q++) { i64 c = S->xcomp[xo[q]]; if (pslot[c] >= 0) cntx[pslot[c]]++; }
        offv[0] = 0; offx[0] = 0;
        for (i64 q = 0; q < nslots; q++) { offv[q + 1] = offv[q] + cntv[q]; offx[q + 1] = offx[q] + cntx[q]; }
        i64 *selv = pal(P, (offv[nslots] + 1) * sizeof(i64)), *selx = pal(P, (offx[nslots] + 1) * sizeof(i64));
        NEED(selv && selx);
        for (i64 q = 0; q < nslots; q++) { cntv[q] = offv[q]; cntx[q] = offx[q]; }
        for (i64 i = 0; i < nv; i++) { i64 l = S->vlab_s[i]; if (l >= 0 && pslot[l] >= 0) selv[cntv[pslot[l]]++] = i; }
        for (i64 q = 0; q < k; q++) { i64 j = xo[q], c = S->xcomp[j]; if (pslot[c] >= 0) selx[cntx[pslot[c]]++] = j; }
        if (nppts + offv[nslots] + offx[nslots] > cppts) {
            i64 cap = cppts ? cppts : 1024;
            while (nppts + offv[nslots] + offx[nslots] > cap) cap *= 2;
            double *np = realloc(ppts, 3 * cap * sizeof(double));
            NEED(np);
            ppts = np; cppts = cap;
        }
        for (i64 g = 0; g < ng; g++) {
            if (gslot[g] < 0) continue;
            i64 slot = gslot[g], key = gkey[g];
            /* the part's points in hull order: its vertices, the crossing points merged in behind any vertex with
             * the same key */
            double ext = 0; int anysel = 0;
            i64 iv_ = offv[slot], ix = offx[slot], off = nppts;
            while (iv_ < offv[slot + 1] || ix < offx[slot + 1]) {
                if (ix >= offx[slot + 1] || (iv_ < offv[slot + 1] && h->vkey_s[selv[iv_]] <= xkey[selx[ix]])) {
                    i64 i = selv[iv_++];
                    memcpy(ppts + 3 * nppts, h->Vs + 3 * i, 3 * sizeof(double));
                    double ss = S->sgn > 0 ? h->s_s[i] : -h->s_s[i];
                    if (!anysel || ss > ext) ext = ss;
                    anysel = 1;
                } else {
                    memcpy(ppts + 3 * nppts, C.X + 3 * selx[ix++], 3 * sizeof(double));
                }
                nppts++;
            }
            if (ext < 0) ext = 0;
            Part *pt = &parts[nparts];
            pt->vol = gvol[g]; pt->off = off; pt->n = nppts - off; pt->ext = ext; pt->sgn = S->sgn; pt->gkey = key; pt->kidx = -1;
            for (i64 m = 0; m < gn[g]; m++) part_of[sd][gmem[g * (nc + 1) + m]] = nparts;
            nparts++;
        }
    }
    DBG("nparts %lld\n", (long long)nparts);
    if (nparts < 2 || nparts > max_shards) FAIL(0);
    TICK(6);
    /* touching: across the plane (the two parts of a section loop) */
    unsigned char *touch = pal(P, (nparts * nparts + 1));
    NEED(touch);
    memset(touch, 0, nparts * nparts);
    for (i64 L = 0; L < nl; L++) {
        i64 a = part_of[0][lp[L]], b = part_of[1][lm[L]];
        if (a >= 0 && b >= 0) { touch[a * nparts + b] = 1; touch[b * nparts + a] = 1; }
    }
    double thin = D > sliver ? D : sliver;
    unsigned char *isbig = pal(P, nparts + 1);
    NEED(isbig);
    i64 nbig = 0, ncrumb = 0;
    for (i64 p = 0; p < nparts; p++) {
        isbig[p] = !(parts[p].vol < 0.05 * pvol && parts[p].ext < thin);
        if (isbig[p]) nbig++; else ncrumb++;
    }
    if (nbig < 2) FAIL(0);
    if (ncrumb) {
        int trishell = h->composed && h->nshell > 1;
        uint64_t *shmask = NULL;
        if (trishell) {
            /* (the shells of each part's pieces, as a set; a part of one shell only is "the same" as itself) */
            i64 nwords = (h->nshell + 63) / 64;
            shmask = pal(P, (nparts * nwords + 1) * sizeof(uint64_t));
            NEED(shmask);
            memset(shmask, 0, nparts * nwords * sizeof(uint64_t));
            for (int sd = 0; sd < 2; sd++)
                for (i64 c = 0; c < sides[sd].nc; c++) {
                    i64 p = part_of[sd][c];
                    if (p < 0) continue;
                    i64 sh = sides[sd].shells[c];
                    shmask[p * nwords + sh / 64] |= 1ULL << (sh % 64);
                }
        }
        /* (a crumb's points on a 1e-6 grid, as a hash set; the big part's points are probed against it) */
        uint64_t **pk = pal(P, (nparts + 1) * sizeof(uint64_t *));
        unsigned char **pku = pal(P, (nparts + 1) * sizeof(unsigned char *));
        i64 *pkm = pal(P, (nparts + 1) * sizeof(i64));
        NEED(pk && pku && pkm);
        for (i64 p = 0; p < nparts; p++) pk[p] = NULL;
        for (i64 cm = 0; cm < nparts; cm++) {
            if (isbig[cm]) continue;
            for (i64 q = 0; q < nparts; q++) {
                if (!isbig[q] || parts[q].sgn != parts[cm].sgn || touch[cm * nparts + q]) continue;
                int hit = 0;
                if (trishell) {
                    i64 nwords = (h->nshell + 63) / 64;
                    /* any shell of cm differing from any shell of q: unless both sets are the same single shell */
                    int single = 1; i64 only = -1;
                    for (i64 w = 0; w < nwords && single; w++) {
                        uint64_t m = shmask[cm * nwords + w] | shmask[q * nwords + w];
                        while (m) { int b = __builtin_ctzll(m); m &= m - 1; if (only >= 0) { single = 0; break; } only = w * 64 + b; }
                    }
                    if (!single) hit = 1;
                }
                if (!hit && parts[cm].n > 0) {
                    if (!pk[cm]) {
                        i64 m = 8;
                        while (m < 2 * parts[cm].n) m *= 2;
                        pk[cm] = pal(P, m * sizeof(uint64_t)); pku[cm] = pal(P, m); pkm[cm] = m;
                        NEED(pk[cm] && pku[cm]);
                        memset(pku[cm], 0, m);
                        for (i64 i = 0; i < parts[cm].n; i++) {
                            uint64_t key = gridkey(ppts + 3 * (parts[cm].off + i));
                            i64 slot = (i64)((key * 0x9E3779B97F4A7C15ULL) >> 20) & (m - 1);
                            while (pku[cm][slot] && pk[cm][slot] != key) slot = (slot + 1) & (m - 1);
                            pku[cm][slot] = 1; pk[cm][slot] = key;
                        }
                    }
                    if (!pk[q]) {                 /* (the big part's keys, once) */
                        pk[q] = pal(P, (parts[q].n + 1) * sizeof(uint64_t));
                        NEED(pk[q]);
                        for (i64 i = 0; i < parts[q].n; i++) pk[q][i] = gridkey(ppts + 3 * (parts[q].off + i));
                    }
                    i64 m = pkm[cm];
                    for (i64 i = 0; i < parts[q].n && !hit; i++) {
                        uint64_t key = pk[q][i];
                        i64 slot = (i64)((key * 0x9E3779B97F4A7C15ULL) >> 20) & (m - 1);
                        while (pku[cm][slot]) { if (pk[cm][slot] == key) { hit = 1; break; } slot = (slot + 1) & (m - 1); }
                    }
                }
                if (hit) { touch[cm * nparts + q] = 1; touch[q * nparts + cm] = 1; }
            }
        }
    }
    TICK(7);
    /* fold: a crumb joins the biggest part it touches; one touching none is a part of its own */
    i64 *gfirst = pal(P, (nparts + 1) * sizeof(i64)), *gnext = pal(P, (nparts + 1) * sizeof(i64)), *glast = pal(P, (nparts + 1) * sizeof(i64));
    NEED(gfirst && gnext && glast);
    i64 ngroups = 0;
    i64 *bigidx = pal(P, (nparts + 1) * sizeof(i64));
    NEED(bigidx);
    for (i64 p = 0; p < nparts; p++) { gnext[p] = -1; bigidx[p] = -1; }
    for (i64 p = 0; p < nparts; p++) if (isbig[p]) { bigidx[p] = ngroups; gfirst[ngroups] = p; glast[ngroups] = p; ngroups++; }
    for (i64 cm = 0; cm < nparts; cm++) {
        if (isbig[cm]) continue;
        i64 j = -1;
        for (i64 q = 0; q < nparts; q++)
            if (isbig[q] && touch[cm * nparts + q] && (j < 0 || parts[q].vol > parts[j].vol)) j = q;
        if (j >= 0) { i64 g = bigidx[j]; gnext[glast[g]] = cm; glast[g] = cm; }
        else { gfirst[ngroups] = cm; glast[ngroups] = cm; ngroups++; }
    }
    DBG("ngroups %lld\n", (long long)ngroups);
    /* the answer: per group its volume, its points (sorted along the slanted direction when several parts) and
     * the air of their hull */
    NEED(res_groups(res, ngroups));
    i64 total = 0;
    for (i64 g = 0; g < ngroups; g++) for (i64 p = gfirst[g]; p >= 0; p = gnext[p]) total += parts[p].n;
    NEED(res_points(res, total));
    i64 at = 0;
    for (i64 g = 0; g < ngroups; g++) {
        double vol = 0; i64 cnt = 0, members = 0;
        res->off[g] = at;
        for (i64 p = gfirst[g]; p >= 0; p = gnext[p]) {
            vol += parts[p].vol;
            memcpy(res->pts + 3 * (at + cnt), ppts + 3 * parts[p].off, 3 * parts[p].n * sizeof(double));
            cnt += parts[p].n; members++;
        }
        if (members > 1) {
            /* (numpy's stable argsort of the concatenation: a stable merge of the parts, each already in key order) */
            double *tmp = pal(P, 3 * (cnt + 1) * sizeof(double));
            i64 *mo = pal(P, (members + 1) * sizeof(i64)), *mn = pal(P, (members + 1) * sizeof(i64)), *mp = pal(P, (members + 1) * sizeof(i64));
            double *mk = pal(P, (members + 1) * sizeof(double));
            NEED(tmp && mo && mn && mp && mk);
            i64 mm = 0;
            for (i64 p = gfirst[g]; p >= 0; p = gnext[p]) { mo[mm] = parts[p].off; mn[mm] = parts[p].n; mp[mm] = 0; mm++; }
            for (i64 m = 0; m < mm; m++) if (mn[m]) { const double *q = ppts + 3 * mo[m]; mk[m] = (q[0] * SORT0 + q[1] * SORT1) + q[2] * SORT2; }
            for (i64 i = 0; i < cnt; i++) {
                i64 best = -1;
                for (i64 m = 0; m < mm; m++) if (mp[m] < mn[m] && (best < 0 || mk[m] < mk[best])) best = m;
                memcpy(tmp + 3 * i, ppts + 3 * (mo[best] + mp[best]), 3 * sizeof(double));
                mp[best]++;
                if (mp[best] < mn[best]) { const double *q = ppts + 3 * (mo[best] + mp[best]); mk[best] = (q[0] * SORT0 + q[1] * SORT1) + q[2] * SORT2; }
            }
            memcpy(res->pts + 3 * at, tmp, 3 * cnt * sizeof(double));
        }
        TICK(8);
        int failed = 0;
        double hv = hull_volume(cnt, res->pts + 3 * at, &failed);
        TICK(9);
        res->vol[g] = vol;
        res->hull[g] = !failed;
        res->air[g] = failed ? 0.0 : (hv - vol > 0.0 ? hv - vol : 0.0);
        at += cnt;
    }
    res->off[ngroups] = at;
    rc = 1;
    TICK(8);
out:
    for (i64 j = 0; j < C.k; j++) h->cid[C.cross[j]] = -1;
    free(ppts);
    pool_free(P);
    return rc;
#undef FAIL
#undef NEED
}

/* the handle keeps its two answer buffers in these (allocated lazily) */
typedef struct { Res a, b; } ResPair;

static ResPair *rp_of(HG *h) {
    if (!h->results) h->results = xcalloc(1, sizeof(ResPair));
    return (ResPair *)h->results;
}

static int cmp_va(const void *a, const void *b) {
    const double *x = a, *y = b;
    if (x[0] != y[0]) return x[0] < y[0] ? -1 : 1;
    if (x[1] != y[1]) return x[1] < y[1] ? -1 : 1;
    return 0;
}

/* -> 1 parts (ng groups: vols, airs, hull flags, points by off), 0 None, -1 KERNEL, -2 internal error,
 * -3 out of memory. rule: 0 none, 1 "plus", 2 "solid", 3 "minus". The pointers stay valid until the next call. */
EXPORT int hlg_score(void *hp, const double *n, double d, double D, double sliver, double pvol, i64 max_shards, int rule,
                     i64 *ng, const double **vols, const double **airs, const i64 **offs, const int **hulls, const double **points) {
    HG *h = hp;
    *ng = 0;
    if (!h->ok) return -2;
    if (h->sheet && rule == 0) return -1;
    ResPair *rp = rp_of(h);
    if (!rp) return -3;
    Res *A = &rp->a, *B = &rp->b;
    i64 nv = h->nv;
    const i64 *T = h->T;
    double tol = 1e-9 * (h->scale > 1.0 ? h->scale : 1.0);
    TSTART();
    int anyzero = 0;
    const double *Vs = h->Vs;
    for (i64 i = 0; i < nv; i++) {                      /* (in hull order: sequential; then scattered) */
        double s = ((Vs[3 * i] * n[0] + Vs[3 * i + 1] * n[1]) + Vs[3 * i + 2] * n[2]) - d;
        h->s_s[i] = s;
        h->zero_s[i] = fabs(s) <= tol;
        h->pos_s[i] = s > tol;
        if (h->zero_s[i]) anyzero = 1;
    }
    for (i64 i = 0; i < nv; i++) { i64 v = h->vsorted[i]; h->s[v] = h->s_s[i]; h->zero[v] = h->zero_s[i]; h->pos[v] = h->pos_s[i]; }
    TICK(10);
    i64 *nearT = NULL;
    i64 nnear = cellidx_near(&h->tc, n, d, tol, &nearT, 1);
    if (nnear < 0) return -3;
    TICK(11);
    int rc;
#define ANSWER(res) do { *ng = (res)->ng; *vols = (res)->vol; *airs = (res)->air; *offs = (res)->off; *hulls = (res)->hull; *points = (res)->pts; } while (0)
    DBG("anyzero %d nnear %lld\n", anyzero, (long long)nnear);
    if (!anyzero) {
        rc = score_sides(h, n, d, h->pos, h->pos_s, nearT, nnear, D, sliver, pvol, max_shards, A);
        free(nearT);
        if (rc == 1) ANSWER(A);
        return rc;
    }
    for (i64 v = 0; v < nv; v++) if (h->zero[v]) h->s[v] = 0.0;
    for (i64 i = 0; i < nv; i++) if (h->zero_s[i]) h->s_s[i] = 0.0;
    for (i64 v = 0; v < nv; v++) h->posS[v] = h->pos[v] || h->zero[v];
    for (i64 i = 0; i < nv; i++) h->posS_s[i] = h->pos_s[i] || h->zero_s[i];
    i64 nz3 = 0; int allside = 1;
    for (i64 q = 0; q < nnear; q++) {
        i64 t = nearT[q];
        if (h->zero[T[3 * t]] && h->zero[T[3 * t + 1]] && h->zero[T[3 * t + 2]]) {
            nz3++;
            const double *nr = h->nrm + 3 * t;
            if (!(((nr[0] * n[0] + nr[1] * n[1]) + nr[2] * n[2]) < 0)) allside = 0;
        }
    }
    DBG("nz3 %lld allside %d\n", (long long)nz3, allside);
    if (nz3 == 0 || rule == 1 || (allside && rule == 0)) {
        rc = score_sides(h, n, d, h->posS, h->posS_s, nearT, nnear, D, sliver, pvol, max_shards, A);
        free(nearT);
        if (rc == 1) ANSWER(A);
        return rc;
    }
    if (rule == 3) {
        rc = score_sides(h, n, d, h->pos, h->pos_s, nearT, nnear, D, sliver, pvol, max_shards, A);
        free(nearT);
        if (rc == 1) ANSWER(A);
        return rc;
    }
    /* vertices on the plane take the side of the face's solid (numpy: the last face wins) */
    memset(h->vs, 0, nv);
    for (i64 q = 0; q < nnear; q++) {
        i64 t = nearT[q];
        if (!(h->zero[T[3 * t]] && h->zero[T[3 * t + 1]] && h->zero[T[3 * t + 2]])) continue;
        const double *nr = h->nrm + 3 * t;
        unsigned char side = (((nr[0] * n[0] + nr[1] * n[1]) + nr[2] * n[2]) < 0) ? 1 : 2;      /* (1: +1, 2: -1) */
        for (int j = 0; j < 3; j++) h->vs[T[3 * t + j]] = side;
    }
    unsigned char *solidpos = h->vs;     /* (reuse: vs -> the "solid" side array) */
    for (i64 v = 0; v < nv; v++) solidpos[v] = h->zero[v] ? (h->vs[v] != 2) : h->pos[v];
    unsigned char *solidpos_s = h->vs_s;
    for (i64 i = 0; i < nv; i++) solidpos_s[i] = solidpos[h->vsorted[i]];
    int rs = score_sides(h, n, d, solidpos, solidpos_s, nearT, nnear, D, sliver, pvol, max_shards, B);
    if (rs < 0) { free(nearT); return rs; }
    if (rule == 2) { free(nearT); if (rs == 1) ANSWER(B); return rs; }
    int rpl = score_sides(h, n, d, h->posS, h->posS_s, nearT, nnear, D, sliver, pvol, max_shards, A);
    free(nearT);
    if (rpl < 0) return rpl;
    if ((rpl == 1) != (rs == 1)) return -1;
    if (rpl == 0) return 0;
    if (A->ng != B->ng) return -1;
    for (i64 g = 0; g < A->ng; g++) if (!A->hull[g] || !B->hull[g]) return -1;    /* (a hull we couldn't build: the kernel's) */
    i64 m = A->ng;
    double *pa = xmalloc(2 * m * sizeof(double)), *pb = xmalloc(2 * m * sizeof(double));
    if (!pa || !pb) { free(pa); free(pb); return -3; }
    for (i64 g = 0; g < m; g++) { pa[2 * g] = A->vol[g]; pa[2 * g + 1] = A->air[g]; pb[2 * g] = B->vol[g]; pb[2 * g + 1] = B->air[g]; }
    qsort(pa, m, 2 * sizeof(double), cmp_va); qsort(pb, m, 2 * sizeof(double), cmp_va);
    int kern = 0;
    for (i64 g = 0; g < m && !kern; g++) {
        double v1 = pa[2 * g], a1 = pa[2 * g + 1], v2 = pb[2 * g], a2 = pb[2 * g + 1];
        double mx = a1 > a2 ? a1 : a2;
        if (fabs(v1 - v2) > 1e-6 * pvol || fabs(a1 - a2) > 0.01 * mx + 1e-6 * pvol) kern = 1;
    }
    free(pa); free(pb);
    if (kern) return -1;
    ANSWER(A);
    return 1;
#undef ANSWER
}

/* frees the answer buffers (hlg_free) */
void hlg_free_results(HG *h) {
    ResPair *rp = (ResPair *)h->results;
    if (!rp) return;
    free(rp->a.vol); free(rp->a.air); free(rp->a.off); free(rp->a.hull); free(rp->a.pts);
    free(rp->b.vol); free(rp->b.air); free(rp->b.off); free(rp->b.hull); free(rp->b.pts);
    free(rp);
    h->results = NULL;
}
