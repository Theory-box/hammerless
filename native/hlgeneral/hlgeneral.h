/* Hammerless native cut scorer for General (core/organicfit.py): the C twin of brushfit.CutScorer, built to
 * core/_hlgeneral.dll (python native/build.py hlgeneral). Loaded with ctypes over plain arrays: no Python C API,
 * so it doesn't depend on Blender's Python version. Must answer as the Python scorer does: the same parts, the
 * same point sets in the same (hull) order, volumes equal to rounding (build with -ffp-contract=off).
 * Files: setup.c (per piece: edges, the spanning forest and its Euler tour, grid cells, cycle boxes, vertex
 * orders, the sheet test), score.c (per cut), hull.c (an exact convex hull volume: quickhull). */
#ifndef HLGENERAL_H
#define HLGENERAL_H
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

typedef int64_t i64;

/* allocation: NULL on failure, callers check (hlg_new frees everything and returns NULL; hlg_score returns -3) */
static inline void *xmalloc(size_t n) { return malloc(n ? n : 1); }
static inline void *xcalloc(size_t n, size_t s) { return calloc(n ? n : 1, s); }

typedef struct { i64 key; i64 idx; } KI;          /* sort by key, ties by idx (stable) */
typedef struct { double key; i64 idx; } KD;
int cmp_ki(const void *a, const void *b);
int cmp_kd(const void *a, const void *b);
int cmp_i64(const void *a, const void *b);

/* a grid index: items grouped by cell (sorted), cells grouped 4x4x4 into parents, boxes of both */
typedef struct {
    i64 n, ncell, npar;
    i64 *items;        /* n, grouped by (parent, cell) */
    i64 *start;        /* ncell + 1 */
    i64 *keys;         /* ncell */
    double *lo, *hi;   /* ncell * 3 */
    i64 *pstart;       /* npar + 1: a parent's first cell */
    double *plo, *phi; /* npar * 3 */
} CellIdx;

typedef struct {
    i64 nv, nt, ne;
    double *V; i64 *T;
    int composed, ok, sheet;
    i64 *ea, *eb, *et, *eid, *vt;
    double *nrm, *w, *tlo, *thi;
    double vnoise, scale;
    int LB;
    i64 *cell;              /* nt: finest cell key */
    i64 *nontree; i64 nnt;
    i64 *parent, *tree_edge, *tin, *tout, *shell, *roots; i64 nshell;
    double *pre;            /* nt + 1 */
    double *plo, *phi;      /* nnt * 3: cycle boxes */
    CellIdx tc, ec;
    double *vkey; i64 *vsorted; double *vkey_s; i64 *vorder; i64 *vtin_s; i64 *tour_to_sorted;
    double *Vs;             /* vertices in hull order */
    /* per-cut scratch (nv / ne sized, kept) */
    double *s; unsigned char *zero, *pos, *posS, *vs; i64 *cid; i64 *vlab;
    double *s_s; unsigned char *zero_s, *pos_s, *posS_s, *vs_s;      /* (the same in hull order) */
    void *results;          /* score.c: the last answer (and a second one: score compares "plus" against "solid") */
} HG;
void hlg_free_results(HG *h);

#define SORT0 (1.0 / 1.2360679775)
#define SORT1 (0.6180339887 / 1.2360679775)
#define SORT2 (0.3819660113 / 1.2360679775)

int cellidx_build(CellIdx *c, int LB, i64 n, const i64 *cell, const double *lo, const double *hi);
void cellidx_free(CellIdx *c);
/* the items (sorted if asked) of the cells the plane could pass through; -1 on allocation failure */
i64 cellidx_near(const CellIdx *c, const double *n, double d, double tol, i64 **out, int sorted);
int straddle(const double *lo, const double *hi, const double *n, double d, double tol);

/* hull.c: the exact volume of the convex hull of n points; 0 if degenerate; -1 if the build failed (the caller
 * then hulls them some other way) */
double hull_volume(i64 n, const double *pts, int *failed);

#endif
