/* Per piece: what brushfit.CutScorer.__init__ builds (see hlgeneral.h). */
#include "hlgeneral.h"
#ifdef HLG_PROF
#include <windows.h>
extern double hlg_prof[16];
static double now(void) { LARGE_INTEGER t, f; QueryPerformanceCounter(&t); QueryPerformanceFrequency(&f); return (double)t.QuadPart / (double)f.QuadPart; }
#define TICK(slot) do { double t_ = now(); hlg_prof[slot] += t_ - t_last; t_last = t_; } while (0)
#define TSTART() double t_last = now()
#else
#define TICK(slot) do {} while (0)
#define TSTART() do {} while (0)
#endif

int cmp_ki(const void *a, const void *b) {
    const KI *x = a, *y = b;
    if (x->key != y->key) return x->key < y->key ? -1 : 1;
    return x->idx < y->idx ? -1 : (x->idx > y->idx);
}
int cmp_kd(const void *a, const void *b) {
    const KD *x = a, *y = b;
    if (x->key != y->key) return x->key < y->key ? -1 : 1;
    return x->idx < y->idx ? -1 : (x->idx > y->idx);
}
int cmp_i64(const void *a, const void *b) {
    i64 x = *(const i64 *)a, y = *(const i64 *)b;
    return x < y ? -1 : (x > y);
}

/* KI pairs by (key, idx), keys >= 0: LSD radix sort, 11 bits a pass over the bits the keys use (idx order kept) */
static int radix_ki(KI *a, i64 n) {
    if (n < 2) return 1;
    i64 mx = 0;
    for (i64 i = 0; i < n; i++) if (a[i].key > mx) mx = a[i].key;
    KI *scratch = xmalloc(n * sizeof *scratch);
    if (!scratch) return 0;
    KI *src = a, *dst = scratch;
    i64 cnt[2049];
    for (int shift = 0; shift < 64 && (mx >> shift); shift += 11) {
        memset(cnt, 0, sizeof cnt);
        for (i64 i = 0; i < n; i++) cnt[((src[i].key >> shift) & 2047) + 1]++;
        for (int k = 0; k < 2048; k++) cnt[k + 1] += cnt[k];
        for (i64 i = 0; i < n; i++) dst[cnt[(src[i].key >> shift) & 2047]++] = src[i];
        KI *t = src; src = dst; dst = t;
    }
    if (src != a) memcpy(a, src, n * sizeof *a);
    free(scratch);
    return 1;
}

int straddle(const double *lo, const double *hi, const double *n, double d, double tol) {
    double np0 = n[0] > 0 ? n[0] : 0, np1 = n[1] > 0 ? n[1] : 0, np2 = n[2] > 0 ? n[2] : 0;
    double nn0 = n[0] < 0 ? n[0] : 0, nn1 = n[1] < 0 ? n[1] : 0, nn2 = n[2] < 0 ? n[2] : 0;
    double smin = (lo[0] * np0 + lo[1] * np1 + lo[2] * np2) + (hi[0] * nn0 + hi[1] * nn1 + hi[2] * nn2) - d;
    double smax = (hi[0] * np0 + hi[1] * np1 + hi[2] * np2) + (lo[0] * nn0 + lo[1] * nn1 + lo[2] * nn2) - d;
    return smin <= tol && smax >= -tol;
}

static i64 parent_key(i64 c, int LB, int sh) {
    i64 mask = ((i64)1 << LB) - 1;
    return (((c >> (2 * LB)) >> sh) << (2 * LB)) | ((((c >> LB) & mask) >> sh) << LB) | ((c & mask) >> sh);
}

int cellidx_build(CellIdx *c, int LB, i64 n, const i64 *cell, const double *lo, const double *hi) {
    memset(c, 0, sizeof *c);
    c->n = n;
    KI *a = xmalloc(n * sizeof *a);
    KI *b = xmalloc(n * sizeof *b);
    if (!a || !b) { free(a); free(b); return 0; }
    for (i64 i = 0; i < n; i++) { a[i].key = cell[i]; a[i].idx = i; }
    qsort(a, n, sizeof *a, cmp_ki);                               /* by cell, then idx */
    for (i64 i = 0; i < n; i++) { b[i].key = parent_key(cell[a[i].idx], LB, 2); b[i].idx = i; }
    qsort(b, n, sizeof *b, cmp_ki);                               /* by parent, then (cell, idx): stable */
    c->items = xmalloc(n * sizeof(i64));
    c->start = xmalloc((n + 1) * sizeof(i64));
    c->keys = xmalloc(n * sizeof(i64));
    c->lo = xmalloc(n * 3 * sizeof(double));
    c->hi = xmalloc(n * 3 * sizeof(double));
    c->pstart = xmalloc((n + 1) * sizeof(i64));
    c->plo = xmalloc(n * 3 * sizeof(double));
    c->phi = xmalloc(n * 3 * sizeof(double));
    if (!c->items || !c->start || !c->keys || !c->lo || !c->hi || !c->pstart || !c->plo || !c->phi) {
        free(a); free(b); cellidx_free(c); return 0;
    }
    i64 nc = 0, np = 0;
    for (i64 i = 0; i < n; i++) {
        i64 it = a[b[i].idx].idx;
        i64 ck = cell[it], pk = b[i].key;
        c->items[i] = it;
        if (i == 0 || ck != c->keys[nc - 1] || pk != b[i - 1].key) {
            if (i == 0 || pk != b[i - 1].key) c->pstart[np++] = nc;
            c->keys[nc] = ck;
            c->start[nc] = i;
            for (int k = 0; k < 3; k++) { c->lo[3 * nc + k] = lo[3 * it + k]; c->hi[3 * nc + k] = hi[3 * it + k]; }
            nc++;
        } else {
            for (int k = 0; k < 3; k++) {
                if (lo[3 * it + k] < c->lo[3 * (nc - 1) + k]) c->lo[3 * (nc - 1) + k] = lo[3 * it + k];
                if (hi[3 * it + k] > c->hi[3 * (nc - 1) + k]) c->hi[3 * (nc - 1) + k] = hi[3 * it + k];
            }
        }
    }
    c->start[nc] = n;
    c->pstart[np] = nc;
    c->ncell = nc; c->npar = np;
    for (i64 p = 0; p < np; p++) {
        for (int k = 0; k < 3; k++) { c->plo[3 * p + k] = INFINITY; c->phi[3 * p + k] = -INFINITY; }
        for (i64 q = c->pstart[p]; q < c->pstart[p + 1]; q++)
            for (int k = 0; k < 3; k++) {
                if (c->lo[3 * q + k] < c->plo[3 * p + k]) c->plo[3 * p + k] = c->lo[3 * q + k];
                if (c->hi[3 * q + k] > c->phi[3 * p + k]) c->phi[3 * p + k] = c->hi[3 * q + k];
            }
    }
    free(a); free(b);
    return 1;
}

void cellidx_free(CellIdx *c) {
    free(c->items); free(c->start); free(c->keys); free(c->lo); free(c->hi); free(c->pstart); free(c->plo); free(c->phi);
    memset(c, 0, sizeof *c);
}

i64 cellidx_near(const CellIdx *c, const double *n, double d, double tol, i64 **out, int sorted) {
    i64 cnt = 0, cap = 64;
    i64 *o = xmalloc(cap * sizeof(i64));
    if (!o) return -1;
    for (i64 p = 0; p < c->npar; p++) {
        if (!straddle(c->plo + 3 * p, c->phi + 3 * p, n, d, tol)) continue;
        for (i64 q = c->pstart[p]; q < c->pstart[p + 1]; q++) {
            if (!straddle(c->lo + 3 * q, c->hi + 3 * q, n, d, tol)) continue;
            i64 m = c->start[q + 1] - c->start[q];
            if (cnt + m > cap) {
                while (cnt + m > cap) cap *= 2;
                i64 *o2 = realloc(o, cap * sizeof(i64));
                if (!o2) { free(o); return -1; }
                o = o2;
            }
            memcpy(o + cnt, c->items + c->start[q], m * sizeof(i64));
            cnt += m;
        }
    }
    if (sorted) qsort(o, cnt, sizeof(i64), cmp_i64);
    *out = o;
    return cnt;
}

/* ------------------------------------------------------------------ union-find */
static i64 uf_find(i64 *p, i64 x) {
    while (p[x] != x) { p[x] = p[p[x]]; x = p[x]; }
    return x;
}

/* ------------------------------------------------------------------ the sheet test (_sheet_area) */
typedef struct { i64 r[4]; i64 t; } PK;
static int cmp_pk(const void *a, const void *b) {
    const PK *x = a, *y = b;
    for (int k = 0; k < 4; k++) if (x->r[k] != y->r[k]) return x->r[k] < y->r[k] ? -1 : 1;
    return x->t < y->t ? -1 : (x->t > y->t);
}
static int cmp_pk_key(const void *a, const void *b) {
    const PK *x = a, *y = b;
    for (int k = 0; k < 4; k++) if (x->r[k] != y->r[k]) return x->r[k] < y->r[k] ? -1 : 1;
    return 0;
}

static double sheet_area(HG *h, const double *area2, int *fail) {
    i64 nt = h->nt;
    const double *V = h->V; const i64 *T = h->T;
    PK *keys = xmalloc(nt * sizeof *keys);
    if (!keys) { *fail = 1; return 0; }
    i64 nk = 0;
    for (i64 t = 0; t < nt; t++) {
        if (!(area2[t] > 1e-12)) continue;
        double n0 = h->nrm[3 * t] / area2[t], n1 = h->nrm[3 * t + 1] / area2[t], n2 = h->nrm[3 * t + 2] / area2[t];
        const double *a = V + 3 * T[3 * t];
        double d = n0 * a[0] + n1 * a[1] + n2 * a[2];
        keys[nk].r[0] = (i64)nearbyint(n0 * 1e4); keys[nk].r[1] = (i64)nearbyint(n1 * 1e4);
        keys[nk].r[2] = (i64)nearbyint(n2 * 1e4); keys[nk].r[3] = (i64)nearbyint(d * 1e3);
        keys[nk].t = t;
        nk++;
    }
    if (nk == 0) { free(keys); return 0; }
    qsort(keys, nk, sizeof *keys, cmp_pk);
    double total = 0;
    double *cu = NULL, *cv = NULL, *pu = NULL, *pv = NULL;
    i64 capA = 0, capB = 0;
    for (i64 g0 = 0; g0 < nk;) {
        i64 g1 = g0 + 1;
        while (g1 < nk && cmp_pk_key(&keys[g0], &keys[g1]) == 0) g1++;
        PK opp; for (int k = 0; k < 4; k++) opp.r[k] = -keys[g0].r[k];
        PK *o = bsearch(&opp, keys, nk, sizeof *keys, cmp_pk_key);
        if (o) {
            i64 b0 = o - keys, b1 = b0;
            while (b0 > 0 && cmp_pk_key(&keys[b0 - 1], &opp) == 0) b0--;
            while (b1 < nk && cmp_pk_key(&keys[b1], &opp) == 0) b1++;
            i64 nA = g1 - g0, nB = b1 - b0;
            if (nA > capA) { free(cu); free(cv); capA = nA; cu = xmalloc(nA * sizeof(double)); cv = xmalloc(nA * sizeof(double)); }
            if (nB > capB) { free(pu); free(pv); capB = nB; pu = xmalloc(nB * 3 * sizeof(double)); pv = xmalloc(nB * 3 * sizeof(double)); }
            if (!cu || !cv || !pu || !pv) { *fail = 1; break; }
            i64 t0 = keys[g0].t;
            double nn[3] = {h->nrm[3 * t0] / area2[t0], h->nrm[3 * t0 + 1] / area2[t0], h->nrm[3 * t0 + 2] / area2[t0]};
            double ax[3] = {1, 0, 0};
            if (!(fabs(nn[0]) < 0.9)) { ax[0] = 0; ax[1] = 1; }
            double u[3] = {nn[1] * ax[2] - nn[2] * ax[1], nn[2] * ax[0] - nn[0] * ax[2], nn[0] * ax[1] - nn[1] * ax[0]};
            double un = sqrt((u[0] * u[0] + u[1] * u[1]) + u[2] * u[2]);
            u[0] /= un; u[1] /= un; u[2] /= un;
            double v[3] = {nn[1] * u[2] - nn[2] * u[1], nn[2] * u[0] - nn[0] * u[2], nn[0] * u[1] - nn[1] * u[0]};
            for (i64 i = 0; i < nA; i++) {
                i64 t = keys[g0 + i].t;
                const double *p0 = V + 3 * T[3 * t], *p1 = V + 3 * T[3 * t + 1], *p2 = V + 3 * T[3 * t + 2];
                double c[3];
                for (int k = 0; k < 3; k++) c[k] = ((p0[k] + p1[k]) + p2[k]) / 3.0;
                cu[i] = c[0] * u[0] + c[1] * u[1] + c[2] * u[2];
                cv[i] = c[0] * v[0] + c[1] * v[1] + c[2] * v[2];
            }
            for (i64 j = 0; j < nB; j++) {
                i64 t = keys[b0 + j].t;
                for (int k = 0; k < 3; k++) {
                    const double *p = V + 3 * T[3 * t + k];
                    pu[3 * j + k] = p[0] * u[0] + p[1] * u[1] + p[2] * u[2];
                    pv[3 * j + k] = p[0] * v[0] + p[1] * v[1] + p[2] * v[2];
                }
            }
            for (i64 i = 0; i < nA; i++) {
                int inside = 0;
                for (i64 j = 0; j < nB && !inside; j++) {
                    double e[3];
                    for (int k = 0; k < 3; k++) {
                        int l = (k + 1) % 3;
                        e[k] = (pu[3 * j + l] - pu[3 * j + k]) * (cv[i] - pv[3 * j + k]) - (pv[3 * j + l] - pv[3 * j + k]) * (cu[i] - pu[3 * j + k]);
                    }
                    if ((e[0] >= 0 && e[1] >= 0 && e[2] >= 0) || (e[0] <= 0 && e[1] <= 0 && e[2] <= 0)) inside = 1;
                }
                if (inside) total += area2[keys[g0 + i].t] / 2;   /* (Python: sum(area2[inside]) / 2 per group) */
            }
        }
        g0 = g1;
    }
    free(cu); free(cv); free(pu); free(pv); free(keys);
    return total;
}

/* ------------------------------------------------------------------ setup */
static void hg_free(HG *h) {
    if (!h) return;
    free(h->V); free(h->T); free(h->ea); free(h->eb); free(h->et); free(h->eid); free(h->vt);
    free(h->nrm); free(h->w); free(h->tlo); free(h->thi); free(h->cell); free(h->nontree);
    free(h->parent); free(h->tree_edge); free(h->tin); free(h->tout); free(h->shell); free(h->roots); free(h->pre);
    free(h->plo); free(h->phi); cellidx_free(&h->tc); cellidx_free(&h->ec);
    free(h->vkey); free(h->vsorted); free(h->vkey_s); free(h->vorder); free(h->vtin_s); free(h->tour_to_sorted); free(h->Vs);
    free(h->s); free(h->zero); free(h->pos); free(h->posS); free(h->vs); free(h->cid); free(h->vlab);
    free(h->s_s); free(h->zero_s); free(h->pos_s); free(h->posS_s); free(h->vs_s);
    hlg_free_results(h);
    free(h);
}

EXPORT void hlg_free(void *p) { hg_free(p); }

static int bits(i64 x) { int b = 0; while (x) { b++; x >>= 1; } return b; }

/* -> the handle, or NULL (out of memory). *ok: the mesh is closed (else nothing else is built); *sheet: the piece
 * carries a zero-thickness sheet (the kernel's to score) */
EXPORT void *hlg_new(i64 nv, const double *Vin, i64 nt, const i64 *Tin, int composed, int *ok, int *sheet) {
    HG *h = xcalloc(1, sizeof *h);
    if (!h) return NULL;
    *ok = 0; *sheet = 0;
    h->nv = nv; h->nt = nt; h->composed = composed;
    h->V = xmalloc(nv * 3 * sizeof(double)); h->T = xmalloc(nt * 3 * sizeof(i64));
    if (!h->V || !h->T) { hg_free(h); return NULL; }
    memcpy(h->V, Vin, nv * 3 * sizeof(double)); memcpy(h->T, Tin, nt * 3 * sizeof(i64));
    const double *V = h->V; const i64 *T = h->T;
    if (nt == 0) return h;
    TSTART();
    /* edges: ids by (min, max) vertex, a triangle's edges (a,b) (b,c) (c,a), an edge's two triangles in index order */
    KI *corn = xmalloc(3 * nt * sizeof *corn);
    h->eid = xmalloc(3 * nt * sizeof(i64));
    h->ea = xmalloc(3 * nt * sizeof(i64)); h->eb = xmalloc(3 * nt * sizeof(i64)); h->et = xmalloc(6 * nt * sizeof(i64));
    if (!corn || !h->eid || !h->ea || !h->eb || !h->et) { free(corn); hg_free(h); return NULL; }
    for (i64 t = 0; t < nt; t++)
        for (int j = 0; j < 3; j++) {
            i64 a = T[3 * t + j], b = T[3 * t + (j + 1) % 3];
            i64 lo = a < b ? a : b, hi = a < b ? b : a;
            corn[3 * t + j].key = lo * nv + hi; corn[3 * t + j].idx = 3 * t + j;
        }
    if (!radix_ki(corn, 3 * nt)) { free(corn); hg_free(h); return NULL; }
    i64 ne = 0; int closed = 1;
    for (i64 i = 0; i < 3 * nt;) {
        i64 j = i;
        while (j < 3 * nt && corn[j].key == corn[i].key) j++;
        if (j - i != 2) closed = 0;
        h->ea[ne] = corn[i].key / nv; h->eb[ne] = corn[i].key % nv;
        h->et[2 * ne] = corn[i].idx / 3; h->et[2 * ne + 1] = (j - i > 1 ? corn[i + 1].idx : corn[i].idx) / 3;
        for (i64 q = i; q < j; q++) h->eid[corn[q].idx] = ne;
        ne++;
        i = j;
    }
    free(corn);
    h->ne = ne;
    TICK(12);
    if (!closed) return h;
    h->ok = 1; *ok = 1;
    /* a triangle of each vertex (numpy's last assignment wins: corner 2, then 1, then 0) */
    h->vt = xcalloc(nv, sizeof(i64));
    h->nrm = xmalloc(3 * nt * sizeof(double)); h->w = xmalloc(nt * sizeof(double));
    h->tlo = xmalloc(3 * nt * sizeof(double)); h->thi = xmalloc(3 * nt * sizeof(double));
    if (!h->vt || !h->nrm || !h->w || !h->tlo || !h->thi) { hg_free(h); return NULL; }
    for (int j = 2; j >= 0; j--) for (i64 t = 0; t < nt; t++) h->vt[T[3 * t + j]] = t;
    double wabs = 0;
    for (i64 t = 0; t < nt; t++) {
        const double *A = V + 3 * T[3 * t], *B = V + 3 * T[3 * t + 1], *C = V + 3 * T[3 * t + 2];
        double ba[3] = {B[0] - A[0], B[1] - A[1], B[2] - A[2]}, ca[3] = {C[0] - A[0], C[1] - A[1], C[2] - A[2]};
        double *n = h->nrm + 3 * t;
        n[0] = ba[1] * ca[2] - ba[2] * ca[1]; n[1] = ba[2] * ca[0] - ba[0] * ca[2]; n[2] = ba[0] * ca[1] - ba[1] * ca[0];
        h->w[t] = ((A[0] * n[0] + A[1] * n[1]) + A[2] * n[2]) / 6.0;
        wabs += fabs(h->w[t]);
        for (int k = 0; k < 3; k++) {
            double lo = A[k] < B[k] ? A[k] : B[k]; if (C[k] < lo) lo = C[k];
            double hi = A[k] > B[k] ? A[k] : B[k]; if (C[k] > hi) hi = C[k];
            h->tlo[3 * t + k] = lo; h->thi[3 * t + k] = hi;
        }
    }
    h->vnoise = 1e-12 * wabs;
    double vlo[3] = {INFINITY, INFINITY, INFINITY}, vhi[3] = {-INFINITY, -INFINITY, -INFINITY};
    for (i64 v = 0; v < nv; v++) for (int k = 0; k < 3; k++) { if (V[3 * v + k] < vlo[k]) vlo[k] = V[3 * v + k]; if (V[3 * v + k] > vhi[k]) vhi[k] = V[3 * v + k]; }
    {
        double e0 = vhi[0] - vlo[0], e1 = vhi[1] - vlo[1], e2 = vhi[2] - vlo[2];
        h->scale = sqrt((e0 * e0 + e1 * e1) + e2 * e2);
    }
    TICK(13);
    /* ---- the spanning forest, grown cell by cell (_forest): Kruskal in the order of the level whose cell first
     * holds both triangles of an edge; every edge's join level is that level, and a non-tree edge's tree cycle
     * lies inside that cell */
    double clo[3] = {INFINITY, INFINITY, INFINITY}, chi[3] = {-INFINITY, -INFINITY, -INFINITY};
    for (i64 t = 0; t < nt; t++) for (int k = 0; k < 3; k++) {
        double c = (h->tlo[3 * t + k] + h->thi[3 * t + k]) / 2.0;
        if (c < clo[k]) clo[k] = c; if (c > chi[k]) chi[k] = c;
    }
    {
        double r = sqrt((double)nt / 32.0); if (r < 2.0) r = 2.0;
        int LB = (int)ceil(log2(r)); if (LB < 2) LB = 2;
        h->LB = LB;
    }
    int LB = h->LB; i64 G = (i64)1 << LB;
    i64 *q = xmalloc(3 * nt * sizeof(i64));
    h->cell = xmalloc(nt * sizeof(i64));
    i64 *join = xmalloc(ne * sizeof(i64));
    unsigned char *istree = xcalloc(ne, 1);
    i64 *comp = xmalloc(nt * sizeof(i64));
    if (!q || !h->cell || !join || !istree || !comp) { free(q); free(join); free(istree); free(comp); hg_free(h); return NULL; }
    for (i64 t = 0; t < nt; t++) for (int k = 0; k < 3; k++) {
        double c = (h->tlo[3 * t + k] + h->thi[3 * t + k]) / 2.0;
        double ext = chi[k] - clo[k]; if (ext < 1e-12) ext = 1e-12;
        i64 qi = (i64)((c - clo[k]) / ext * (double)G);
        if (qi < 0) qi = 0; if (qi > G - 1) qi = G - 1;
        q[3 * t + k] = qi;
    }
    for (i64 t = 0; t < nt; t++) h->cell[t] = (q[3 * t] << (2 * LB)) | (q[3 * t + 1] << LB) | q[3 * t + 2];
    i64 *cnt = xcalloc(LB + 2, sizeof(i64));
    i64 *order = xmalloc(ne * sizeof(i64));
    if (!cnt || !order) { free(cnt); free(order); free(q); free(join); free(istree); free(comp); hg_free(h); return NULL; }
    for (i64 e = 0; e < ne; e++) {
        i64 a = h->et[2 * e], b = h->et[2 * e + 1];
        int L = 0;
        for (int k = 0; k < 3; k++) { int bb = bits(q[3 * a + k] ^ q[3 * b + k]); if (bb > L) L = bb; }
        join[e] = L;
        cnt[L + 1]++;
    }
    for (int L = 0; L <= LB; L++) cnt[L + 1] += cnt[L];
    for (i64 e = 0; e < ne; e++) order[cnt[join[e]]++] = e;        /* (by level, then edge id) */
    for (i64 t = 0; t < nt; t++) comp[t] = t;
    i64 nnt = 0;
    for (i64 i = 0; i < ne; i++) {
        i64 e = order[i];
        i64 ra = uf_find(comp, h->et[2 * e]), rb = uf_find(comp, h->et[2 * e + 1]);
        if (ra != rb) { comp[ra > rb ? ra : rb] = ra > rb ? rb : ra; istree[e] = 1; } else nnt++;
    }
    free(cnt); free(order); free(q);
    h->nontree = xmalloc(nnt * sizeof(i64));
    if (!h->nontree) { free(join); free(istree); free(comp); hg_free(h); return NULL; }
    h->nnt = 0;
    for (i64 e = 0; e < ne; e++) if (!istree[e]) h->nontree[h->nnt++] = e;
    /* shells: a shell's label is its smallest triangle, shells numbered by it */
    i64 *lab = xmalloc(nt * sizeof(i64));
    h->shell = xmalloc(nt * sizeof(i64));
    if (!lab || !h->shell) { free(lab); free(join); free(istree); free(comp); hg_free(h); return NULL; }
    for (i64 t = 0; t < nt; t++) lab[t] = -1;
    for (i64 t = 0; t < nt; t++) { i64 r = uf_find(comp, t); if (lab[r] < 0) lab[r] = t; }
    i64 nshell = 0;
    for (i64 t = 0; t < nt; t++) if (uf_find(comp, t) == t) nshell++;
    h->roots = xmalloc(nshell * sizeof(i64));
    if (!h->roots) { free(lab); free(join); free(istree); free(comp); hg_free(h); return NULL; }
    nshell = 0;
    for (i64 t = 0; t < nt; t++) if (uf_find(comp, t) == t) h->roots[nshell++] = lab[t];    /* (ascending: lab[r] <= r) */
    qsort(h->roots, nshell, sizeof(i64), cmp_i64);
    h->nshell = nshell;
    for (i64 t = 0; t < nt; t++) {
        i64 l = lab[uf_find(comp, t)];
        i64 *p = bsearch(&l, h->roots, nshell, sizeof(i64), cmp_i64);
        h->shell[t] = p - h->roots;
    }
    free(lab); free(comp);
    TICK(14);
    /* ---- the Euler tour (_tour): parent, tree_edge, tin/tout, prefix sums of the cones along the tour */
    i64 *deg = xcalloc(nt + 1, sizeof(i64));
    i64 *adj = xmalloc(4 * (ne - nnt > 0 ? ne - nnt : 1) * sizeof(i64));   /* (two darts a tree edge: node, edge) */
    h->parent = xmalloc(nt * sizeof(i64)); h->tree_edge = xmalloc(nt * sizeof(i64));
    h->tin = xmalloc(nt * sizeof(i64)); h->tout = xmalloc(nt * sizeof(i64));
    i64 *tour = xmalloc(nt * sizeof(i64));
    i64 *stack = xmalloc(2 * nt * sizeof(i64));
    h->pre = xmalloc((nt + 1) * sizeof(double));
    if (!deg || !adj || !h->parent || !h->tree_edge || !h->tin || !h->tout || !tour || !stack || !h->pre) {
        free(deg); free(adj); free(tour); free(stack); free(join); free(istree); hg_free(h); return NULL;
    }
    for (i64 e = 0; e < ne; e++) if (istree[e]) { deg[h->et[2 * e] + 1]++; deg[h->et[2 * e + 1] + 1]++; }
    for (i64 t = 0; t < nt; t++) deg[t + 1] += deg[t];
    {
        i64 *fill = xcalloc(nt, sizeof(i64));
        if (!fill) { free(deg); free(adj); free(tour); free(stack); free(join); free(istree); hg_free(h); return NULL; }
        for (i64 e = 0; e < ne; e++) if (istree[e]) {
            i64 a = h->et[2 * e], b = h->et[2 * e + 1];
            adj[2 * (deg[a] + fill[a])] = b; adj[2 * (deg[a] + fill[a]) + 1] = e; fill[a]++;
            adj[2 * (deg[b] + fill[b])] = a; adj[2 * (deg[b] + fill[b]) + 1] = e; fill[b]++;
        }
        free(fill);
    }
    for (i64 t = 0; t < nt; t++) { h->parent[t] = t; h->tree_edge[t] = ne; h->tin[t] = -1; }
    i64 pos = 0;
    for (i64 r = 0; r < nshell; r++) {
        i64 root = h->roots[r];
        i64 sp = 0;
        stack[sp++] = root; stack[sp++] = 0;              /* (node, next adjacency slot) */
        h->tin[root] = pos; tour[pos++] = root;
        while (sp) {
            i64 node = stack[sp - 2], slot = stack[sp - 1];
            if (slot < deg[node + 1] - deg[node]) {
                stack[sp - 1] = slot + 1;
                i64 nb = adj[2 * (deg[node] + slot)], e = adj[2 * (deg[node] + slot) + 1];
                if (h->tin[nb] >= 0) continue;
                h->parent[nb] = node; h->tree_edge[nb] = e;
                h->tin[nb] = pos; tour[pos++] = nb;
                stack[sp++] = nb; stack[sp++] = 0;
            } else {
                h->tout[node] = pos;
                sp -= 2;
            }
        }
    }
    h->pre[0] = 0;
    for (i64 i = 0; i < nt; i++) h->pre[i + 1] = h->pre[i] + h->w[tour[i]];
    free(deg); free(adj); free(stack);
    TICK(15);
    /* ---- grid cells of the triangles (_cells) and the cycle boxes of the non-tree edges (_path_boxes) */
    if (!cellidx_build(&h->tc, LB, nt, h->cell, h->tlo, h->thi)) { free(tour); free(join); free(istree); hg_free(h); return NULL; }
    h->plo = xmalloc(3 * nnt * sizeof(double)); h->phi = xmalloc(3 * nnt * sizeof(double));
    i64 *ecell = xmalloc(nnt * sizeof(i64));
    if (!h->plo || !h->phi || !ecell) { free(ecell); free(tour); free(join); free(istree); hg_free(h); return NULL; }
    {
        /* boxes per cell at each level: level 0 from the index, each next level by grouping parents */
        i64 nk = h->tc.ncell;
        i64 *keys = xmalloc(nk * sizeof(i64)); double *lo = xmalloc(3 * nk * sizeof(double)), *hi = xmalloc(3 * nk * sizeof(double));
        KI *o = xmalloc(nk * sizeof *o);
        i64 *keys2 = xmalloc(nk * sizeof(i64)); double *lo2 = xmalloc(3 * nk * sizeof(double)), *hi2 = xmalloc(3 * nk * sizeof(double));
        if (!keys || !lo || !hi || !o || !keys2 || !lo2 || !hi2) {
            free(keys); free(lo); free(hi); free(o); free(keys2); free(lo2); free(hi2); free(ecell); free(tour); free(join); free(istree); hg_free(h); return NULL;
        }
        for (i64 i = 0; i < nk; i++) { o[i].key = h->tc.keys[i]; o[i].idx = i; }
        qsort(o, nk, sizeof *o, cmp_ki);
        for (i64 i = 0; i < nk; i++) { keys[i] = o[i].key; memcpy(lo + 3 * i, h->tc.lo + 3 * o[i].idx, 3 * sizeof(double)); memcpy(hi + 3 * i, h->tc.hi + 3 * o[i].idx, 3 * sizeof(double)); }
        for (int L = 0; L <= LB; L++) {
            if (L) {
                for (i64 i = 0; i < nk; i++) { o[i].key = parent_key(keys[i], LB, 1); o[i].idx = i; }
                qsort(o, nk, sizeof *o, cmp_ki);
                i64 nk2 = 0;
                for (i64 i = 0; i < nk; i++) {
                    if (i == 0 || o[i].key != o[i - 1].key) {
                        keys2[nk2] = o[i].key;
                        memcpy(lo2 + 3 * nk2, lo + 3 * o[i].idx, 3 * sizeof(double)); memcpy(hi2 + 3 * nk2, hi + 3 * o[i].idx, 3 * sizeof(double));
                        nk2++;
                    } else for (int k = 0; k < 3; k++) {
                        if (lo[3 * o[i].idx + k] < lo2[3 * (nk2 - 1) + k]) lo2[3 * (nk2 - 1) + k] = lo[3 * o[i].idx + k];
                        if (hi[3 * o[i].idx + k] > hi2[3 * (nk2 - 1) + k]) hi2[3 * (nk2 - 1) + k] = hi[3 * o[i].idx + k];
                    }
                }
                memcpy(keys, keys2, nk2 * sizeof(i64)); memcpy(lo, lo2, 3 * nk2 * sizeof(double)); memcpy(hi, hi2, 3 * nk2 * sizeof(double));
                nk = nk2;
            }
            for (i64 i = 0; i < nnt; i++) {
                i64 e = h->nontree[i];
                if (join[e] != L) continue;
                i64 key = parent_key(h->cell[h->et[2 * e]], LB, L);
                i64 *p = bsearch(&key, keys, nk, sizeof(i64), cmp_i64);
                i64 c = p - keys;
                memcpy(h->plo + 3 * i, lo + 3 * c, 3 * sizeof(double)); memcpy(h->phi + 3 * i, hi + 3 * c, 3 * sizeof(double));
            }
        }
        free(keys); free(lo); free(hi); free(o); free(keys2); free(lo2); free(hi2);
    }
    for (i64 i = 0; i < nnt; i++) ecell[i] = h->cell[h->et[2 * h->nontree[i]]];
    int okc = cellidx_build(&h->ec, LB, nnt, ecell, h->plo, h->phi);
    free(ecell); free(join); free(istree);
    if (!okc) { free(tour); hg_free(h); return NULL; }
    TICK(12);
    /* ---- the vertices in hull order and in tour order */
    h->vkey = xmalloc(nv * sizeof(double)); h->vsorted = xmalloc(nv * sizeof(i64)); h->vkey_s = xmalloc(nv * sizeof(double));
    h->vorder = xmalloc(nv * sizeof(i64)); h->vtin_s = xmalloc(nv * sizeof(i64)); h->tour_to_sorted = xmalloc(nv * sizeof(i64));
    h->Vs = xmalloc(3 * nv * sizeof(double));
    KD *kd = xmalloc(nv * sizeof *kd); KI *ki = xmalloc(nv * sizeof *ki);
    i64 *inv = xmalloc(nv * sizeof(i64));
    if (!h->vkey || !h->vsorted || !h->vkey_s || !h->vorder || !h->vtin_s || !h->tour_to_sorted || !h->Vs || !kd || !ki || !inv) {
        free(kd); free(ki); free(inv); free(tour); hg_free(h); return NULL;
    }
    for (i64 v = 0; v < nv; v++) {
        h->vkey[v] = (V[3 * v] * SORT0 + V[3 * v + 1] * SORT1) + V[3 * v + 2] * SORT2;
        kd[v].key = h->vkey[v]; kd[v].idx = v;
        ki[v].key = h->tin[h->vt[v]]; ki[v].idx = v;
    }
    qsort(kd, nv, sizeof *kd, cmp_kd);
    qsort(ki, nv, sizeof *ki, cmp_ki);
    for (i64 i = 0; i < nv; i++) {
        h->vsorted[i] = kd[i].idx; h->vkey_s[i] = kd[i].key; inv[kd[i].idx] = i;
        memcpy(h->Vs + 3 * i, V + 3 * kd[i].idx, 3 * sizeof(double));
        h->vorder[i] = ki[i].idx; h->vtin_s[i] = ki[i].key;
    }
    for (i64 i = 0; i < nv; i++) h->tour_to_sorted[i] = inv[h->vorder[i]];
    free(kd); free(ki); free(inv); free(tour);
    TICK(13);
    /* ---- scratch */
    h->s = xmalloc(nv * sizeof(double)); h->zero = xmalloc(nv); h->pos = xmalloc(nv); h->posS = xmalloc(nv); h->vs = xmalloc(nv);
    h->cid = xmalloc(ne * sizeof(i64)); h->vlab = xmalloc(nv * sizeof(i64));
    h->s_s = xmalloc(nv * sizeof(double)); h->zero_s = xmalloc(nv); h->pos_s = xmalloc(nv); h->posS_s = xmalloc(nv); h->vs_s = xmalloc(nv);
    if (!h->s || !h->zero || !h->pos || !h->posS || !h->vs || !h->cid || !h->vlab || !h->s_s || !h->zero_s || !h->pos_s || !h->posS_s || !h->vs_s) { hg_free(h); return NULL; }
    for (i64 e = 0; e < ne; e++) h->cid[e] = -1;
    /* ---- the sheet test */
    double *area2 = xmalloc(nt * sizeof(double));
    if (!area2) { hg_free(h); return NULL; }
    double asum = 0;
    for (i64 t = 0; t < nt; t++) {
        const double *n = h->nrm + 3 * t;
        area2[t] = sqrt((n[0] * n[0] + n[1] * n[1]) + n[2] * n[2]);
        asum += area2[t];
    }
    int fail = 0;
    double sa = sheet_area(h, area2, &fail);
    free(area2);
    if (fail) { hg_free(h); return NULL; }
    h->sheet = sa > 0.01 * asum / 2;
    TICK(14);
    *sheet = h->sheet;
    return h;
}

EXPORT i64 hlg_info(void *p, i64 what) {
    HG *h = p;
    switch (what) {
        case 0: return h->ne; case 1: return h->nshell; case 2: return h->nnt; case 3: return h->LB;
        case 4: return h->tc.ncell; case 5: return h->ok; case 6: return h->sheet;
    }
    return -1;
}
