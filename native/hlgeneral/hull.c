/* The exact volume of the convex hull of a point set: quickhull (Barber, Dobkin, Huhdanpaa) with conflict lists.
 * Points within eps (1e-9 of the box) of a face count as not outside it, so the volume is short by at most that
 * sliver; measured against qhull on real parts: 1e-9 relative. The kernel's own hull (manifold3d's hull_points)
 * was found up to 6% too big on real parts (112 of 465 off by more than 1e-9), so this is the scorer's hull.
 * Robustness: the faces a point sees are found by a scan of every face (not by walking neighbours, which can miss
 * some when the point is nearly in a face's plane), and a face the point is coplanar with (to eps) counts as seen,
 * so the point replaces it (left in place, the new faces fold back over it: measured 0.13% too much volume on a
 * box-like part with near-duplicate points); a horizon that still isn't one simple loop is tried again with the
 * coplanar band 10x and 100x wider, and failing that the whole hull is built again with eps 100x (twice). The
 * volume by face planes is checked against the volume by winding (a fold shows as a difference). A build that
 * still fails reports it and the caller hulls those points with the kernel instead. */
#include "hlgeneral.h"
#ifdef HLG_DEBUG
#include <stdio.h>
#define HLOG(...) do { fprintf(stderr, __VA_ARGS__); } while (0)
#else
#define HLOG(...) do {} while (0)
#endif

typedef struct {
    i64 v[3];
    i64 adj[3];            /* the face across edge (v[k], v[k+1]) */
    double n[3], off;
    int alive, mark, degen;   /* (degen: no area to speak of; never seen, never given points) */
    i64 *pts; i64 npts, cap;
} Face;

typedef struct { Face *f; i64 nf, cap; const double *P; i64 n; double eps, area_min; double c0[3]; int fail, oom; } Hull;

static double fdist(const Face *f, const double *p) { return f->degen ? -INFINITY : (f->n[0] * p[0] + f->n[1] * p[1] + f->n[2] * p[2]) - f->off; }

static void set_plane(Hull *H, Face *f) {
    const double *a = H->P + 3 * f->v[0], *b = H->P + 3 * f->v[1], *c = H->P + 3 * f->v[2];
    double ba[3] = {b[0] - a[0], b[1] - a[1], b[2] - a[2]}, ca[3] = {c[0] - a[0], c[1] - a[1], c[2] - a[2]};
    double n[3] = {ba[1] * ca[2] - ba[2] * ca[1], ba[2] * ca[0] - ba[0] * ca[2], ba[0] * ca[1] - ba[1] * ca[0]};
    double l = sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
    f->degen = !(l > H->area_min);
    if (l > 0) { n[0] /= l; n[1] /= l; n[2] /= l; }
    f->n[0] = n[0]; f->n[1] = n[1]; f->n[2] = n[2];
    f->off = n[0] * a[0] + n[1] * a[1] + n[2] * a[2];
}

/* the plane oriented with the hull's centre below it (a flat face, p in the plane of a b, may come out either way) */
static void orient(Hull *H, Face *f) {
    if (fdist(f, H->c0) > 0) { f->n[0] = -f->n[0]; f->n[1] = -f->n[1]; f->n[2] = -f->n[2]; f->off = -f->off; }
}

static i64 new_face(Hull *H, i64 a, i64 b, i64 c) {
    if (H->nf == H->cap) {
        i64 cap = H->cap ? 2 * H->cap : 64;
        Face *f = realloc(H->f, cap * sizeof *f);
        if (!f) { H->oom = 1; return -1; }
        H->f = f; H->cap = cap;
    }
    Face *f = &H->f[H->nf];
    memset(f, 0, sizeof *f);
    f->v[0] = a; f->v[1] = b; f->v[2] = c;
    f->adj[0] = f->adj[1] = f->adj[2] = -1;
    f->alive = 1;
    set_plane(H, f);
    return H->nf++;
}

static int push_pt(Face *f, i64 p) {
    if (f->npts == f->cap) {
        i64 cap = f->cap ? 2 * f->cap : 8;
        i64 *q = realloc(f->pts, cap * sizeof(i64));
        if (!q) return 0;
        f->pts = q; f->cap = cap;
    }
    f->pts[f->npts++] = p;
    return 1;
}

/* the slot of edge (a, b) in face f, -1 if none */
static int edge_slot(const Face *f, i64 a, i64 b) {
    for (int k = 0; k < 3; k++) if (f->v[k] == a && f->v[(k + 1) % 3] == b) return k;
    return -1;
}

typedef struct { i64 a, b, nb; int slot; } HE;
static int cmp_he(const void *x, const void *y) {
    const HE *p = x, *q = y;
    return p->a < q->a ? -1 : (p->a > q->a);
}

typedef struct { i64 *v; i64 n, cap; } Vec;
static int vpush(Vec *v, i64 x) {
    if (v->n == v->cap) {
        i64 cap = v->cap ? 2 * v->cap : 64;
        i64 *q = realloc(v->v, cap * sizeof(i64));
        if (!q) return 0;
        v->v = q; v->cap = cap;
    }
    v->v[v->n++] = x;
    return 1;
}

/* the faces that see p from face fi (dist > thr), and their horizon as one simple loop (in loop order);
 * -> 1 ok, 0 the horizon isn't simple, -1 out of memory */
static int horizon(Hull *H, i64 fi, const double *pp, double thr, int stamp, int scan, Vec *vis, HE **hor, i64 *nhor, i64 *hcap, i64 **loop, i64 *lcap) {
    vis->n = 0;
    if (scan) {
        for (i64 f = 0; f < H->nf; f++) {       /* (every live face that sees p: nothing missed, at a cost) */
            Face *g = &H->f[f];
            if (!g->alive || g->degen) continue;
            if (fdist(g, pp) > thr) { g->mark = stamp; if (!vpush(vis, f)) return -1; }
        }
        if (vis->n == 0) return 0;
    } else {
        if (!vpush(vis, fi)) return -1;
        H->f[fi].mark = stamp;
        for (i64 q = 0; q < vis->n; q++) {
            Face *g = &H->f[vis->v[q]];
            for (int k = 0; k < 3; k++) {
                i64 nb = g->adj[k];
                Face *gn = &H->f[nb];
                if (gn->mark == stamp) continue;
                if (fdist(gn, pp) > thr) { gn->mark = stamp; if (!vpush(vis, nb)) return -1; }
            }
        }
    }
    i64 nh = 0;
    for (i64 q = 0; q < vis->n; q++) {
        Face *g = &H->f[vis->v[q]];
        for (int k = 0; k < 3; k++) {
            i64 nb = g->adj[k];
            if (H->f[nb].mark == stamp) continue;
            if (nh == *hcap) {
                i64 cap = *hcap ? 2 * *hcap : 64;
                HE *h2 = realloc(*hor, cap * sizeof(HE));
                if (!h2) return -1;
                *hor = h2; *hcap = cap;
            }
            HE *e = &(*hor)[nh];
            e->a = g->v[k]; e->b = g->v[(k + 1) % 3]; e->nb = nb;
            e->slot = edge_slot(&H->f[nb], e->b, e->a);
            if (e->slot < 0) return 0;
            nh++;
        }
    }
    *nhor = nh;
    if (nh < 3) return 0;
    qsort(*hor, nh, sizeof(HE), cmp_he);
    for (i64 i = 1; i < nh; i++) if ((*hor)[i].a == (*hor)[i - 1].a) return 0;    /* (a vertex twice: not a simple loop) */
    if (nh > *lcap) { free(*loop); *lcap = nh; *loop = xmalloc(nh * sizeof(i64)); if (!*loop) return -1; }
    i64 cur = 0;
    for (i64 i = 0; i < nh; i++) {
        (*loop)[i] = cur;
        HE key = {(*hor)[cur].b, 0, 0, 0};
        HE *nx = bsearch(&key, *hor, nh, sizeof(HE), cmp_he);
        if (!nx) return 0;
        cur = nx - *hor;
        if (cur == 0 && i + 1 < nh) return 0;                  /* (back at the start too early: several loops) */
    }
    return cur == 0;
}

static double build(i64 n, const double *P, double eps_rel, int scan_all, int *failed) {
    *failed = 0;
    double lo[3] = {INFINITY, INFINITY, INFINITY}, hi[3] = {-INFINITY, -INFINITY, -INFINITY};
    i64 imin[3] = {0, 0, 0}, imax[3] = {0, 0, 0};
    for (i64 i = 0; i < n; i++) for (int k = 0; k < 3; k++) {
        if (P[3 * i + k] < lo[k]) { lo[k] = P[3 * i + k]; imin[k] = i; }
        if (P[3 * i + k] > hi[k]) { hi[k] = P[3 * i + k]; imax[k] = i; }
    }
    double scale = 0; int ax = 0;
    for (int k = 0; k < 3; k++) if (hi[k] - lo[k] > scale) { scale = hi[k] - lo[k]; ax = k; }
    if (!(scale > 0)) return 0.0;
    double eps = eps_rel * scale;
    i64 i0 = imin[ax], i1 = imax[ax];
    const double *p0 = P + 3 * i0, *p1 = P + 3 * i1;
    double dir[3] = {p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]};
    double dl = sqrt(dir[0] * dir[0] + dir[1] * dir[1] + dir[2] * dir[2]);
    if (dl <= eps) return 0.0;
    i64 i2 = -1; double best = eps;
    for (i64 i = 0; i < n; i++) {
        const double *p = P + 3 * i;
        double q[3] = {p[0] - p0[0], p[1] - p0[1], p[2] - p0[2]};
        double c[3] = {q[1] * dir[2] - q[2] * dir[1], q[2] * dir[0] - q[0] * dir[2], q[0] * dir[1] - q[1] * dir[0]};
        double d = sqrt(c[0] * c[0] + c[1] * c[1] + c[2] * c[2]) / dl;
        if (d > best) { best = d; i2 = i; }
    }
    if (i2 < 0) return 0.0;
    Hull H; memset(&H, 0, sizeof H);
    H.P = P; H.n = n; H.eps = eps; H.area_min = 1e-14 * scale * scale;
    double vol = 0, volw = 0;
    Vec vis = {0}, work = {0};
    HE *hor = NULL; i64 nhor = 0, hcap = 0;
    i64 *loop = NULL, lcap = 0, *newf = NULL, ncap = 0;
    if (new_face(&H, i0, i1, i2) < 0) goto done;
    i64 i3 = -1; best = eps;
    for (i64 i = 0; i < n; i++) { double d = fabs(fdist(&H.f[0], P + 3 * i)); if (d > best) { best = d; i3 = i; } }
    if (i3 < 0) { free(H.f); return 0.0; }
    {
        const double *p2 = P + 3 * i2, *p3 = P + 3 * i3;
        for (int k = 0; k < 3; k++) H.c0[k] = ((p0[k] + p1[k]) + (p2[k] + p3[k])) / 4.0;
        if (fdist(&H.f[0], p3) > 0) { H.f[0].v[1] = i2; H.f[0].v[2] = i1; set_plane(&H, &H.f[0]); }
        i64 a = H.f[0].v[0], b = H.f[0].v[1], c = H.f[0].v[2];
        new_face(&H, a, i3, b); new_face(&H, b, i3, c); new_face(&H, c, i3, a);
        if (H.oom) goto done;
    }
    for (i64 f = 0; f < 4; f++) orient(&H, &H.f[f]);
    for (i64 f = 0; f < 4; f++)
        for (int k = 0; k < 3; k++) {
            i64 a = H.f[f].v[k], b = H.f[f].v[(k + 1) % 3];
            for (i64 g = 0; g < 4 && H.f[f].adj[k] < 0; g++) if (g != f && edge_slot(&H.f[g], b, a) >= 0) H.f[f].adj[k] = g;
            if (H.f[f].adj[k] < 0) { H.fail = 1; goto done; }
        }
    for (i64 i = 0; i < n; i++) {
        if (i == i0 || i == i1 || i == i2 || i == i3) continue;
        for (i64 f = 0; f < 4; f++) if (fdist(&H.f[f], P + 3 * i) > eps) { if (!push_pt(&H.f[f], i)) { H.oom = 1; goto done; } break; }
    }
    for (i64 f = 0; f < 4; f++) if (H.f[f].npts && !vpush(&work, f)) { H.oom = 1; goto done; }
    int stamp = 0;
    while (work.n) {
        i64 fi = work.v[--work.n];
        Face *f = &H.f[fi];
        if (!f->alive || f->npts == 0) continue;
        i64 p = -1; double far = -1;
        for (i64 i = 0; i < f->npts; i++) { double d = fdist(f, P + 3 * f->pts[i]); if (d > far) { far = d; p = f->pts[i]; } }
        const double *pp = P + 3 * p;
        int okh = 0;
        /* (the faces p sees, walking neighbours; the faces p is coplanar with (to eps) count as seen - a point on a
         *  face's plane must replace it, or the surface folds (measured on a box-like part: 0.13% too much volume
         *  with p above by eps required); when that isn't a disk, or the volume check below finds a fold (then
         *  the hull is built again with scan_all), by a scan of every face, with the band 10x and 100x wider
         *  after that) */
        static const struct { int scan; double mult; } tries[4] = {{0, -1.0}, {1, -1.0}, {1, -10.0}, {1, -100.0}};
        for (int attempt = scan_all ? 1 : 0; attempt < 4 && !okh; attempt++) {
            double thr = eps * tries[attempt].mult;
            okh = horizon(&H, fi, pp, thr, ++stamp, tries[attempt].scan, &vis, &hor, &nhor, &hcap, &loop, &lcap);
            if (okh < 0) { H.oom = 1; goto done; }
            if (!okh) HLOG("hull: horizon of point %lld not simple at %g eps\n", (long long)p, thr / eps);
        }
        if (!okh) { H.fail = 1; goto done; }
        if (nhor > ncap) { free(newf); ncap = nhor; newf = xmalloc(ncap * sizeof(i64)); if (!newf) { H.oom = 1; goto done; } }
        for (i64 i = 0; i < nhor; i++) {
            HE *e = &hor[loop[i]];
            i64 nf = new_face(&H, e->a, e->b, p);
            if (nf < 0) goto done;
            newf[i] = nf;
        }
        for (i64 i = 0; i < nhor; i++) {
            HE *e = &hor[loop[i]];
            Face *g = &H.f[newf[i]];
            g->adj[0] = e->nb; H.f[e->nb].adj[e->slot] = newf[i];
            g->adj[1] = newf[(i + 1) % nhor];
            g->adj[2] = newf[(i + nhor - 1) % nhor];
            orient(&H, g);
        }
        for (i64 q = 0; q < vis.n; q++) {
            Face *g = &H.f[vis.v[q]];
            for (i64 i = 0; i < g->npts; i++) {
                i64 pt = g->pts[i];
                if (pt == p) continue;
                const double *pq = P + 3 * pt;
                for (i64 j = 0; j < nhor; j++) {
                    Face *nfc = &H.f[newf[j]];
                    if (fdist(nfc, pq) > eps) { if (!push_pt(nfc, pt)) { H.oom = 1; goto done; } break; }
                }
            }
            g->alive = 0; free(g->pts); g->pts = NULL; g->npts = g->cap = 0;
        }
        for (i64 i = 0; i < nhor; i++) if (H.f[newf[i]].npts && !vpush(&work, newf[i])) { H.oom = 1; goto done; }
    }
#ifdef HLG_DEBUG
    if (getenv("HLG_FACES")) for (i64 f = 0; f < H.nf; f++) if (H.f[f].alive) HLOG("FACE %lld %lld %lld\n", (long long)H.f[f].v[0], (long long)H.f[f].v[1], (long long)H.f[f].v[2]);
#endif
    /* the volume: each face's pyramid from the centre (by the plane, so a flat face turned either way is right) */
    for (i64 f = 0; f < H.nf; f++) {
        Face *g = &H.f[f];
        if (!g->alive || g->degen) continue;             /* (a degenerate face has no volume) */
        const double *a = P + 3 * g->v[0], *b = P + 3 * g->v[1], *c = P + 3 * g->v[2];
        double ba[3] = {b[0] - a[0], b[1] - a[1], b[2] - a[2]}, ca[3] = {c[0] - a[0], c[1] - a[1], c[2] - a[2]};
        double cr[3] = {ba[1] * ca[2] - ba[2] * ca[1], ba[2] * ca[0] - ba[0] * ca[2], ba[0] * ca[1] - ba[1] * ca[0]};
        double area2 = sqrt(cr[0] * cr[0] + cr[1] * cr[1] + cr[2] * cr[2]);
        double A[3] = {a[0] - H.c0[0], a[1] - H.c0[1], a[2] - H.c0[2]}, B[3] = {b[0] - H.c0[0], b[1] - H.c0[1], b[2] - H.c0[2]}, C[3] = {c[0] - H.c0[0], c[1] - H.c0[1], c[2] - H.c0[2]};
        vol += -fdist(g, H.c0) * area2 / 6.0;
        volw += (A[0] * (B[1] * C[2] - B[2] * C[1]) + A[1] * (B[2] * C[0] - B[0] * C[2]) + A[2] * (B[0] * C[1] - B[1] * C[0])) / 6.0;
    }
    if (!isfinite(vol) || !isfinite(volw) || !(fabs(vol - volw) <= 1e-8 * fabs(vol) + 1e-12)) { HLOG("hull: volume by planes %.9g, by winding %.9g: folded\n", vol, volw); H.fail = 1; }
done:
    for (i64 f = 0; f < H.nf; f++) free(H.f[f].pts);
    free(H.f); free(work.v); free(vis.v); free(hor); free(loop); free(newf);
    if (H.oom || H.fail) { *failed = 1; return -1; }
    return vol;
}

double hull_volume(i64 n, const double *P, int *failed) {
    *failed = 0;
    if (n < 4) return 0.0;
    double v = build(n, P, 1e-9, 0, failed);
    if (*failed) { HLOG("hull: built again scanning every face\n"); v = build(n, P, 1e-9, 1, failed); }
    if (*failed) { HLOG("hull: built again with eps 1e-7\n"); v = build(n, P, 1e-7, 1, failed); }
    if (*failed) { HLOG("hull: built again with eps 1e-5\n"); v = build(n, P, 1e-5, 1, failed); }
    return v;
}

EXPORT double hlg_hull_volume(i64 n, const double *pts, int *failed) { return hull_volume(n, pts, failed); }
