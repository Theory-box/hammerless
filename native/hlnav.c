/* Hammerless native nav sampling: brush collision traces and the nav generator's
 * walkable-space flood fill, the hot loops of core/collision.py and core/navgen.py.
 * Must give bit-identical results to the Python versions: same double arithmetic in
 * the same order (build with -ffp-contract=off), same candidate order, same quirks.
 * Build: python native/build.py   (uses zig cc)
 */
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdint.h>
#include <windows.h>
#include <setjmp.h>

/* Allocation failures. On the thread running an exported call, a failed allocation jumps back to
 * that call's wrapper (OOM_WRAP), which frees the DLL's state and returns an error; Python then
 * raises (hl_oom). Code the visibility worker threads run can't jump to another thread's call: it
 * gets NULL, keeps what it has and stops; the flag still makes Python raise. */
static volatile LONG g_oom;
static _Thread_local jmp_buf *t_oom_jmp;
static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n ? n : 1);
    if (!q) {
        InterlockedExchange(&g_oom, 1);
        if (t_oom_jmp) longjmp(*t_oom_jmp, 1);
    }
    return q;
}
static void *xmalloc(size_t n) { return xrealloc(NULL, n); }
static void *xcalloc(size_t count, size_t size) {
    void *q = xrealloc(NULL, count * size);
    if (q) memset(q, 0, count * size ? count * size : 1);
    return q;
}
static void oom_cleanup(void);
#define OOM_WRAP(type, fail, call) do { jmp_buf oom_env; jmp_buf *oom_prev = t_oom_jmp; \
        if (setjmp(oom_env)) { t_oom_jmp = oom_prev; oom_cleanup(); return fail; } \
        t_oom_jmp = &oom_env; type oom_r = call; t_oom_jmp = oom_prev; return oom_r; } while (0)
#define OOM_WRAP_VOID(call) do { jmp_buf oom_env; jmp_buf *oom_prev = t_oom_jmp; \
        if (setjmp(oom_env)) { t_oom_jmp = oom_prev; oom_cleanup(); return; } \
        t_oom_jmp = &oom_env; call; t_oom_jmp = oom_prev; } while (0)

#define EXPORT __declspec(dllexport)
#define DIST_EPSILON 0.03125
#define NEVER_UPDATED -9999.0

/* ---------------------------------------------------------------- world */
typedef struct { double nx, ny, nz, dist, ax, ay, az; int bevel, flags; } Side;   /* flags: 1 sky, 2 displacement */
typedef struct { double b[6]; int first, count; } Brush;

static Side *g_sides; static Brush *g_brushes; static int g_nbrushes;
static double g_cell; static int g_cx0, g_cy0, g_w, g_h;
static int *g_cell_start, *g_cell_count, *g_cell_ids;   /* dense grid of cell lists (ascending brush ids) */
static int *g_scratch; static int g_scratch_cap; static int *g_mark; static int g_mark_gen, g_mark_cap, g_world_gen;

static void world_impl(int nbrushes, const double *bounds, const int *side_first, const int *side_count,
                     int nsides, const double *sides7, const int *side_bevel, const int *side_flags,
                     double cell, int cx0, int cy0, int w, int h, const int *cell_start, const int *cell_count,
                     int nids, const int *cell_ids) {
    free(g_sides); free(g_brushes); free(g_cell_start); free(g_cell_count); free(g_cell_ids);
    g_sides = xmalloc(sizeof(Side) * (nsides ? nsides : 1));
    for (int i = 0; i < nsides; i++) {
        const double *s = sides7 + 7 * i;
        g_sides[i].nx = s[0]; g_sides[i].ny = s[1]; g_sides[i].nz = s[2]; g_sides[i].dist = s[3];
        g_sides[i].ax = s[4]; g_sides[i].ay = s[5]; g_sides[i].az = s[6];
        g_sides[i].bevel = side_bevel[i]; g_sides[i].flags = side_flags[i];
    }
    g_nbrushes = nbrushes;
    g_brushes = xmalloc(sizeof(Brush) * (nbrushes ? nbrushes : 1));
    for (int i = 0; i < nbrushes; i++) {
        memcpy(g_brushes[i].b, bounds + 6 * i, sizeof(double) * 6);
        g_brushes[i].first = side_first[i]; g_brushes[i].count = side_count[i];
    }
    g_cell = cell; g_cx0 = cx0; g_cy0 = cy0; g_w = w; g_h = h;
    g_cell_start = xmalloc(sizeof(int) * (w * h ? w * h : 1)); memcpy(g_cell_start, cell_start, sizeof(int) * w * h);
    g_cell_count = xmalloc(sizeof(int) * (w * h ? w * h : 1)); memcpy(g_cell_count, cell_count, sizeof(int) * w * h);
    g_cell_ids = xmalloc(sizeof(int) * (nids ? nids : 1)); memcpy(g_cell_ids, cell_ids, sizeof(int) * nids);
    g_world_gen++;
}

typedef struct { double fraction, ex, ey, ez, nx, ny, nz, left; int startsolid, allsolid, flags; } Tr;

static int cmp_int(const void *a, const void *b) { return (*(const int *)a > *(const int *)b) - (*(const int *)a < *(const int *)b); }

static void clip(const Side *sides, const Brush *br, double p1x, double p1y, double p1z, double p2x, double p2y, double p2z,
                 double ex, double ey, double ez, int is_point, Tr *tr) {
    double enter = NEVER_UPDATED, leave = 1.0;
    int getout = 0, startout = 0; const Side *clipside = NULL;
    for (int k = 0; k < br->count; k++) {
        const Side *s = &sides[br->first + k];
        double dist = s->dist;
        if (is_point) { if (s->bevel) continue; }
        else { dist = dist + s->ax * ex; dist = dist + s->ay * ey; dist = dist + s->az * ez; }
        double d1 = p1x * s->nx; d1 = d1 + p1y * s->ny; d1 = d1 + p1z * s->nz; d1 = d1 - dist;
        double d2 = p2x * s->nx; d2 = d2 + p2y * s->ny; d2 = d2 + p2z * s->nz; d2 = d2 - dist;
        if (d2 > 0) getout = 1;
        if (d1 > 0) { startout = 1; if (d2 >= DIST_EPSILON || d2 >= d1) return; }
        else if (d2 <= 0) continue;
        if (d1 > d2) { double f = (d1 - DIST_EPSILON) / (d1 - d2); if (f > enter) { enter = f; clipside = s; } }
        else { double f = (d1 + DIST_EPSILON) / (d1 - d2); if (f < leave) leave = f; }
    }
    if (!startout) {
        tr->startsolid = 1;
        if (!getout) { tr->allsolid = 1; tr->fraction = 0.0; tr->left = 1.0; }
        else if (leave != 1.0 && leave > tr->left) { tr->left = leave; if (tr->fraction <= leave) tr->fraction = 1.0; }
        return;
    }
    if (enter < leave && enter > NEVER_UPDATED && enter < tr->fraction) {
        tr->fraction = enter > 0.0 ? enter : 0.0;
        tr->nx = clipside->nx; tr->ny = clipside->ny; tr->nz = clipside->nz; tr->flags = clipside->flags;
    }
}

static long long g_traces;
static int g_acc; static double g_acc_box[6];      /* while g_acc: union of the boxes traces looked at */
static void acc_begin(void) { g_acc = 1; for (int i = 0; i < 3; i++) { g_acc_box[i] = 1e300; g_acc_box[3 + i] = -1e300; } }
static void acc_end(double *box) { g_acc = 0; memcpy(box, g_acc_box, sizeof g_acc_box); }

/* a collision world and a per-thread scratch buffer, so traces can run on several threads */
typedef struct { Side *sides; Brush *brushes; int nb; double cell; int cx0, cy0, w, h; int *cs, *cc, *ci; } World;
typedef struct { int *ids; int cap; int *mark; int markcap; int markgen; float *samples; int samplecap; int last_n; } Scratch;

/* The brushes a trace can touch, in ascending id order: those in the grid cells the swept box can
 * reach whose bounds overlap 'box' (the swept box plus 1 unit). Short spans take the whole cell
 * rectangle; long ones only the rectangles of short pieces along the path (a brush outside them
 * can't be hit, so the brushes tested, and their order after the sort, are the same). */
static int gather(const World *W, Scratch *S, double p1x, double p1y, double p2x, double p2y, double ex, double ey,
                  const double *box) {
    /* a box with an infinite / NaN / absurd coordinate (or an empty world) touches no brush; turned
     * into cell numbers it would overflow int and the loops below would run for ever */
    for (int k = 0; k < 6; k++) if (!(fabs(box[k]) < 1e7)) return 0;
    if (W->nb <= 0 || !(W->cell > 0.0)) return 0;
    int cx0 = (int)floor(box[0] / W->cell), cx1 = (int)floor(box[3] / W->cell);
    int cy0 = (int)floor(box[1] / W->cell), cy1 = (int)floor(box[4] / W->cell);
    int n = 0;
#define GATHER_OVERLAPS(bb) ((bb)->b[0] <= box[3] && (bb)->b[3] >= box[0] && (bb)->b[1] <= box[4] && (bb)->b[4] >= box[1] && \
                             (bb)->b[2] <= box[5] && (bb)->b[5] >= box[2])
    if (cx0 == cx1 && cy0 == cy1) {
        int gx = cx0 - W->cx0, gy = cy0 - W->cy0;
        if (gx >= 0 && gy >= 0 && gx < W->w && gy < W->h) {
            int c = gx * W->h + gy;
            if (W->cc[c] > S->cap) {
                int *q = xrealloc(S->ids, sizeof(int) * (W->cc[c] * 2 + 64));
                if (!q) return 0;
                S->ids = q; S->cap = W->cc[c] * 2 + 64;
            }
            for (int k = 0; k < W->cc[c]; k++) {
                int id = W->ci[W->cs[c] + k];
                if (GATHER_OVERLAPS(&W->brushes[id])) S->ids[n++] = id;
            }
        }
        return n;
    }
    if (S->markcap < W->nb) {
        int *m = xcalloc(W->nb ? W->nb : 1, sizeof(int));
        if (!m) return 0;
        free(S->mark); S->mark = m; S->markcap = W->nb; S->markgen = 0;
    }
    S->markgen++;
    int pieces = 1;
    if ((cx1 - cx0) + (cy1 - cy0) > 4) {
        double lx = fabs(p2x - p1x), ly = fabs(p2y - p1y);
        pieces = (int)((lx > ly ? lx : ly) / W->cell) + 1;
    }
    for (int pc = 0; pc < pieces; pc++) {
        int qx0 = cx0, qx1 = cx1, qy0 = cy0, qy1 = cy1;
        if (pieces > 1) {
            double ta = (double)pc / pieces, tb = (double)(pc + 1) / pieces;
            double ax = p1x + ta * (p2x - p1x), bx = p1x + tb * (p2x - p1x);
            double ay = p1y + ta * (p2y - p1y), by = p1y + tb * (p2y - p1y);
            qx0 = (int)floor(((ax < bx ? ax : bx) - ex - 2) / W->cell); qx1 = (int)floor(((ax > bx ? ax : bx) + ex + 2) / W->cell);
            qy0 = (int)floor(((ay < by ? ay : by) - ey - 2) / W->cell); qy1 = (int)floor(((ay > by ? ay : by) + ey + 2) / W->cell);
            if (qx0 < cx0) qx0 = cx0; if (qx1 > cx1) qx1 = cx1; if (qy0 < cy0) qy0 = cy0; if (qy1 > cy1) qy1 = cy1;
        }
        if (qx0 < W->cx0) qx0 = W->cx0; if (qx1 > W->cx0 + W->w - 1) qx1 = W->cx0 + W->w - 1;
        if (qy0 < W->cy0) qy0 = W->cy0; if (qy1 > W->cy0 + W->h - 1) qy1 = W->cy0 + W->h - 1;
        for (int cx = qx0; cx <= qx1; cx++) for (int cy = qy0; cy <= qy1; cy++) {
            int gx = cx - W->cx0, gy = cy - W->cy0;
            if (gx < 0 || gy < 0 || gx >= W->w || gy >= W->h) continue;
            int c = gx * W->h + gy;
            for (int k = 0; k < W->cc[c]; k++) {
                int id = W->ci[W->cs[c] + k];
                if (S->mark[id] == S->markgen) continue;
                S->mark[id] = S->markgen;
                if (!GATHER_OVERLAPS(&W->brushes[id])) continue;
                if (n >= S->cap) {
                    int *q = xrealloc(S->ids, sizeof(int) * (n * 2 + 64));
                    if (!q) { qsort(S->ids, n, sizeof(int), cmp_int); return n; }
                    S->ids = q; S->cap = n * 2 + 64;
                }
                S->ids[n++] = id;
            }
        }
    }
#undef GATHER_OVERLAPS
    qsort(S->ids, n, sizeof(int), cmp_int);
    return n;
}

static Tr trace_w(const World *W, Scratch *S, double *acc, const double *start, const double *end,
                  const double *mins, const double *maxs) {
    double ex = (maxs[0] - mins[0]) * 0.5, ey = (maxs[1] - mins[1]) * 0.5, ez = (maxs[2] - mins[2]) * 0.5;
    double ox = (maxs[0] + mins[0]) * 0.5, oy = (maxs[1] + mins[1]) * 0.5, oz = (maxs[2] + mins[2]) * 0.5;
    double p1x = start[0] + ox, p1y = start[1] + oy, p1z = start[2] + oz;
    double p2x = end[0] + ox, p2y = end[1] + oy, p2z = end[2] + oz;
    int is_point = (ex == 0.0 && ey == 0.0 && ez == 0.0);
    Tr tr; memset(&tr, 0, sizeof tr); tr.fraction = 1.0;
    double x0 = (p1x < p2x ? p1x : p2x) - ex - 1, y0 = (p1y < p2y ? p1y : p2y) - ey - 1, z0 = (p1z < p2z ? p1z : p2z) - ez - 1;
    double x1 = (p1x > p2x ? p1x : p2x) + ex + 1, y1 = (p1y > p2y ? p1y : p2y) + ey + 1, z1 = (p1z > p2z ? p1z : p2z) + ez + 1;
    if (acc) {
        if (x0 < acc[0]) acc[0] = x0; if (y0 < acc[1]) acc[1] = y0; if (z0 < acc[2]) acc[2] = z0;
        if (x1 > acc[3]) acc[3] = x1; if (y1 > acc[4]) acc[4] = y1; if (z1 > acc[5]) acc[5] = z1;
    }
    double box[6] = {x0, y0, z0, x1, y1, z1};
    int n = gather(W, S, p1x, p1y, p2x, p2y, ex, ey, box);
    S->last_n = n;
    for (int k = 0; k < n; k++) {
        clip(W->sides, &W->brushes[S->ids[k]], p1x, p1y, p1z, p2x, p2y, p2z, ex, ey, ez, is_point, &tr);
        if (tr.allsolid) break;
    }
    if (tr.fraction == 1.0) { tr.ex = end[0]; tr.ey = end[1]; tr.ez = end[2]; }
    else {
        double f = tr.fraction;
        tr.ex = start[0] + f * (end[0] - start[0]); tr.ey = start[1] + f * (end[1] - start[1]); tr.ez = start[2] + f * (end[2] - start[2]);
    }
    return tr;
}

/* Is a line clear? (trace_w(...).fraction >= 1 for a zero-size trace, answered sooner.) Within one
 * world a hit can only be undone by a brush the line starts inside (the leave-solid rule resets the
 * fraction to 1), so when no candidate brush holds the start point the first hit is the answer.
 * Returns 1 clear, 0 blocked, -1 the start is inside a brush (*inside_out: let the caller decide). */
static int line_clear_w(const World *W, Scratch *S, const double *start, const double *end, int *inside_out) {
    double p1x = start[0], p1y = start[1], p1z = start[2], p2x = end[0], p2y = end[1], p2z = end[2];
    double x0 = (p1x < p2x ? p1x : p2x) - 1, y0 = (p1y < p2y ? p1y : p2y) - 1, z0 = (p1z < p2z ? p1z : p2z) - 1;
    double x1 = (p1x > p2x ? p1x : p2x) + 1, y1 = (p1y > p2y ? p1y : p2y) + 1, z1 = (p1z > p2z ? p1z : p2z) + 1;
    double box[6] = {x0, y0, z0, x1, y1, z1};
    int n = gather(W, S, p1x, p1y, p2x, p2y, 0.0, 0.0, box);
    /* does any candidate hold the start point? (clip's startout: some non-bevel plane has d1 > 0) */
    for (int k = 0; k < n; k++) {
        const Brush *b = &W->brushes[S->ids[k]];
        if (p1x < b->b[0] || p1x > b->b[3] || p1y < b->b[1] || p1y > b->b[4] || p1z < b->b[2] || p1z > b->b[5]) continue;
        int out = 0;
        for (int j = 0; j < b->count && !out; j++) {
            const Side *sd = &W->sides[b->first + j];
            if (sd->bevel) continue;
            double d1 = p1x * sd->nx; d1 = d1 + p1y * sd->ny; d1 = d1 + p1z * sd->nz; d1 = d1 - sd->dist;
            if (d1 > 0) out = 1;
        }
        if (!out) { if (inside_out) *inside_out = 1; return -1; }
    }
    if (inside_out) *inside_out = 0;
    for (int k = 0; k < n; k++) {
        Tr tr; memset(&tr, 0, sizeof tr); tr.fraction = 1.0;
        clip(W->sides, &W->brushes[S->ids[k]], p1x, p1y, p1z, p2x, p2y, p2z, 0, 0, 0, 1, &tr);
        if (tr.fraction < 1.0) return 0;
    }
    return 1;
}

static Scratch g_scr;                 /* the nav generator's (single-threaded) scratch */
static Tr trace(const double *start, const double *end, const double *mins, const double *maxs) {
    g_traces++;
    World W = {g_sides, g_brushes, g_nbrushes, g_cell, g_cx0, g_cy0, g_w, g_h, g_cell_start, g_cell_count, g_cell_ids};
    return trace_w(&W, &g_scr, g_acc ? g_acc_box : NULL, start, end, mins, maxs);
}

static void trace_impl(const double *start, const double *end, const double *mins, const double *maxs, double *out10) {
    Tr t = trace(start, end, mins, maxs);
    out10[0] = t.fraction; out10[1] = t.ex; out10[2] = t.ey; out10[3] = t.ez; out10[4] = t.nx; out10[5] = t.ny; out10[6] = t.nz;
    out10[7] = t.startsolid; out10[8] = t.allsolid; out10[9] = t.flags;
}

/* ---------------------------------------------------------------- sampler (navgen.Sampler) */
#define GENERATION_STEP 25.0
#define STEP_HEIGHT 18.0
#define DEATH_DROP 400.0
#define CLIMB_UP_HEIGHT 200.0
#define HALF_HUMAN_HEIGHT 35.5
#define HUMAN_HEIGHT 71.0
#define DUCK_HULL_TOP 55.0
#define SLOPE_LIMIT 0.7
#define NODE_Z_TOLERANCE (0.45 * GENERATION_STEP)
#define COMMUTATIVE_Z 50.0
#define DISPLACEMENT_TEST 10000.0
#define JUMP_CROUCH_HEIGHT 64.0
#define HALF_HUMAN_WIDTH 16.0
#define HUMAN_CROUCH_HEIGHT 55.0
#define CLIFF_HEIGHT 300.0
#define NAV_MESH_CROUCH 1
#define NAV_MESH_CLIFF 0x8000

static const double TMIN[3] = {-0.45, -0.45, 0.0}, TMAX[3] = {0.45, 0.45, 55.0};
static const double STEPX[4] = {0.0, GENERATION_STEP, 0.0, -GENERATION_STEP}, STEPY[4] = {-GENERATION_STEP, 0.0, GENERATION_STEP, 0.0};
static const int OPP[4] = {2, 3, 0, 1};
static const int CVX[4] = {-1, 1, 1, -1}, CVY[4] = {-1, -1, 1, 1};

typedef struct {
    double pos[3], normal[3], obstacle[4], ground[4];
    int to[4], parent, visited, attributes, crouch[4], blocked[4], crouch_checked, cliff_checked, on_disp, next_xy;
} Node;

static Node *N; static int NN, NCAP;
/* hash from exact (x, y) to the newest node there (then next_xy) */
static int *H; static uint64_t *HK; static int HCAP;

static uint64_t key_xy(double x, double y) {   /* grid coordinates have all-zero low bits: mix everything down */
    uint64_t a, b; memcpy(&a, &x, 8); memcpy(&b, &y, 8);
    uint64_t h = a ^ (b * 0x9E3779B97F4A7C15ULL);
    h ^= h >> 33; h *= 0xFF51AFD7ED558CCDULL; h ^= h >> 33; h *= 0xC4CEB9FE1A85EC53ULL; h ^= h >> 33;
    return h;
}
static int hslot(double x, double y) {
    uint64_t k = key_xy(x, y); int i = (int)(k & (uint64_t)(HCAP - 1));
    while (H[i] >= 0) { Node *n = &N[H[i]]; if (n->pos[0] == x && n->pos[1] == y) return i; i = (i + 1) & (HCAP - 1); }
    return i;
}
static void hrehash(void) {
    int old = HCAP; int *oh = H; HCAP = HCAP ? HCAP * 2 : 1 << 16;
    H = xmalloc(sizeof(int) * HCAP); for (int i = 0; i < HCAP; i++) H[i] = -1;
    if (oh) { for (int i = 0; i < old; i++) if (oh[i] >= 0) { Node *n = &N[oh[i]]; H[hslot(n->pos[0], n->pos[1])] = oh[i]; } free(oh); }
}
static int hcount;

static int get_node(const double *p) {
    int s = hslot(p[0], p[1]);
    for (int i = H[s]; i >= 0; i = N[i].next_xy) if (fabs(N[i].pos[2] - p[2]) < NODE_Z_TOLERANCE) return i;
    return -1;
}
static int new_node(const double *p, const double *nrm, int parent, int on_disp) {
    if (NN == NCAP) { NCAP = NCAP ? NCAP * 2 : 4096; N = xrealloc(N, sizeof(Node) * NCAP); }
    if ((hcount + 1) * 2 > HCAP) hrehash();
    Node *n = &N[NN]; memset(n, 0, sizeof *n);
    memcpy(n->pos, p, 24); memcpy(n->normal, nrm, 24);
    for (int d = 0; d < 4; d++) n->to[d] = -1;
    n->parent = parent; n->on_disp = on_disp;
    int s = hslot(p[0], p[1]);
    if (H[s] < 0) hcount++;
    n->next_xy = H[s]; H[s] = NN;          /* newest first */
    return NN++;
}

static double round_to_units(double val, double unit) {
    val = val + (val < 0.0 ? -unit * 0.5 : unit * 0.5);
    int iu = (int)unit;
    return (double)(iu * ((int)val / iu));
}

static Tr hull(const double *a, const double *b) { return trace(a, b, TMIN, TMAX); }

static int stay_on_floor(Tr *tr, double z_limit) {
    double start[3] = {tr->ex, tr->ey, tr->ez}, end[3] = {tr->ex, tr->ey, tr->ez - z_limit};
    *tr = hull(start, end);
    if (tr->startsolid || tr->fraction >= 1.0) return 0;
    if (tr->nz < SLOPE_LIMIT) return 0;
    return 1;
}

static int trace_adjacent(int depth, const double *start, const double *end, Tr *out, double z_limit) {
    Tr tr = hull(start, end);
    if (tr.startsolid) { *out = tr; return 0; }
    if (end[0] == tr.ex && end[1] == tr.ey) { int ok = stay_on_floor(&tr, z_limit); *out = tr; return ok; }
    double dx = tr.ex - start[0], dy = tr.ey - start[1];
    if (depth && dx * dx + dy * dy < 1.0) { *out = tr; return 0; }
    if (!stay_on_floor(&tr, z_limit)) { *out = tr; return 0; }
    double top[3] = {tr.ex, tr.ey, tr.ez}, up[3] = {tr.ex, tr.ey, tr.ez + STEP_HEIGHT};
    tr = hull(top, up);
    double fwd[3] = {tr.ex, tr.ey, tr.ez}, fend[3] = {end[0], end[1], tr.ez};
    return trace_adjacent(depth + 1, fwd, fend, out, DEATH_DROP);
}

static int node_overlapped(const double *pos, double ox, double oy) {
    static const double mn[3] = {-0.5, -0.5, -0.5}, mx[3] = {0.5, 0.5, 0.5};
    double start[3] = {pos[0], pos[1], pos[2] + HALF_HUMAN_HEIGHT};
    double end[3] = {start[0] + ox * GENERATION_STEP, start[1] + oy * GENERATION_STEP, start[2]};
    Tr tr = trace(start, end, mn, mx);
    if (tr.startsolid || tr.allsolid || tr.fraction < 0.1) return 1;
    double s2[3] = {tr.ex, tr.ey, tr.ez}, e2[3] = {end[0], end[1], end[2] - HALF_HUMAN_HEIGHT * 2};
    tr = trace(s2, e2, mn, mx);
    return tr.startsolid || tr.allsolid || tr.fraction == 1.0 || tr.nz < 0.7;
}

typedef struct { double ground[4]; int crouch[4], blocked[4], attrs; } Props;

static int test_crouch_area(const double *pos, Props *pr, int corner, const double *mins, double mx, double my) {
    double p[3] = {pos[0], pos[1], pos[2]};
    double up[3] = {p[0], p[1], p[2] + JUMP_CROUCH_HEIGHT};
    Tr tr = hull(p, up);
    double max_height = tr.ez - p[2];
    double h = 0.0;
    while (h <= max_height) {
        double s[3] = {p[0], p[1], p[2] + h};
        double m1[3] = {mx, my, HUMAN_CROUCH_HEIGHT};
        Tr t = trace(s, s, mins, m1);
        if (!t.startsolid) {
            pr->ground[corner] = s[2] - p[2];
            double m2[3] = {mx, my, HUMAN_HEIGHT};
            t = trace(s, s, mins, m2);
            return !t.startsolid;
        }
        h += 1.0;
    }
    pr->ground[corner] = JUMP_CROUCH_HEIGHT;
    pr->blocked[corner] = 1;
    return 0;
}

static void crouch_props(const double *pos, Props *pr) {
    memset(pr, 0, sizeof *pr);
    for (int c = 0; c < 4; c++) {
        double vx = CVX[c], vy = CVY[c];
        double mins[3] = {fmin(0.0, vx * HALF_HUMAN_WIDTH), fmin(0.0, vy * HALF_HUMAN_WIDTH), 0.0};
        double mxx = fmax(0.0, vx * HALF_HUMAN_WIDTH), mxy = fmax(0.0, vy * HALF_HUMAN_WIDTH);
        if (!test_crouch_area(pos, pr, c, mins, mxx, mxy)) { pr->attrs |= NAV_MESH_CROUCH; pr->crouch[c] = 1; }
    }
}

static int check_cliff(const double *pos, int d, int exhaustive) {
    double to[3] = {pos[0] + STEPX[d], pos[1] + STEPY[d], pos[2]};
    Tr tr; int ok = trace_adjacent(0, pos, to, &tr, DEATH_DROP * 10);
    if (ok && !tr.allsolid && !tr.startsolid) {
        double dz = pos[2] - tr.ez;
        if (dz > CLIFF_HEIGHT) return 1;
        if ((d == 2 || d == 1) && fabs(dz) < STEP_HEIGHT && exhaustive) { double np[3] = {tr.ex, tr.ey, tr.ez}; return check_cliff(np, d, 0); }
    }
    return 0;
}

static int cliff_at(const double *pos) {
    for (int k = 0; k < 4; k++) { double p[3] = {pos[0], pos[1], pos[2]}; if (check_cliff(p, k, 1)) return 1; }
    return 0;
}

/* ---- memory of sweeps between runs. Each step / crouch / cliff result at a position is a pure
 * function of the position and of the brushes touching the space its traces looked at, so it's
 * kept with that box. When brushes change, results whose box touches one are dropped; the flood
 * redoes those and replays the rest, in its usual order, so the nodes match a fresh run exactly. */
typedef struct { int ok, disp; double to[3], n[3], obst; } StepRes;
typedef struct {
    double pos[3]; int next_xy;
    unsigned char have_step[4], have_crouch, have_cliff;
    StepRes s[4]; double sbox[4][6];
    Props crouch; double cbox[6];
    int cliff; double clbox[6];
} Memo;
static Memo *M; static int NM, MCAP;
static int *MH; static int MHCAP, mhcount;
static long long g_memo_hits, g_memo_misses;

static int mslot(double x, double y) {
    uint64_t k = key_xy(x, y); int i = (int)(k & (uint64_t)(MHCAP - 1));
    while (MH[i] >= 0) { Memo *e = &M[MH[i]]; if (e->pos[0] == x && e->pos[1] == y) return i; i = (i + 1) & (MHCAP - 1); }
    return i;
}
static void mrehash(void) {
    int old = MHCAP; int *oh = MH; MHCAP = MHCAP ? MHCAP * 2 : 1 << 16;
    MH = xmalloc(sizeof(int) * MHCAP); for (int i = 0; i < MHCAP; i++) MH[i] = -1;
    if (oh) { for (int i = 0; i < old; i++) if (oh[i] >= 0) { Memo *e = &M[oh[i]]; MH[mslot(e->pos[0], e->pos[1])] = oh[i]; } free(oh); }
}
static Memo *memo_at(const double *p) {        /* the memo for this exact position, made if missing */
    if (!MH) mrehash();
    for (int i = MH[mslot(p[0], p[1])]; i >= 0; i = M[i].next_xy) if (M[i].pos[2] == p[2]) return &M[i];
    if (NM == MCAP) { MCAP = MCAP ? MCAP * 2 : 4096; M = xrealloc(M, sizeof(Memo) * MCAP); }
    if ((mhcount + 1) * 2 > MHCAP) mrehash();
    int sl = mslot(p[0], p[1]);
    if (MH[sl] < 0) mhcount++;
    Memo *e = &M[NM]; memset(e, 0, sizeof *e);
    memcpy(e->pos, p, 24); e->next_xy = MH[sl]; MH[sl] = NM;
    return &M[NM++];
}
EXPORT void hl_memo_clear(void) { free(M); M = NULL; NM = MCAP = 0; free(MH); MH = NULL; MHCAP = 0; mhcount = 0; }
static int box_hits(const double *b, int nbox, const double *boxes) {
    for (int k = 0; k < nbox; k++) {
        const double *c = boxes + 6 * k;    /* brush bounds, tested like clip()'s candidate test */
        if (c[0] <= b[3] && c[3] >= b[0] && c[1] <= b[4] && c[4] >= b[1] && c[2] <= b[5] && c[5] >= b[2]) return 1;
    }
    return 0;
}
/* drop what changed brushes (given by bounds, old and new) could affect; returns how many results went */
EXPORT int hl_memo_invalidate(int nbox, const double *boxes) {
    int dropped = 0;
    for (int i = 0; i < NM; i++) {
        Memo *e = &M[i];
        for (int d = 0; d < 4; d++) if (e->have_step[d] && box_hits(e->sbox[d], nbox, boxes)) { e->have_step[d] = 0; dropped++; }
        if (e->have_crouch && box_hits(e->cbox, nbox, boxes)) { e->have_crouch = 0; dropped++; }
        if (e->have_cliff && box_hits(e->clbox, nbox, boxes)) { e->have_cliff = 0; dropped++; }
    }
    return dropped;
}

static void check_crouch(int ni) {
    if (N[ni].crouch_checked) return;
    N[ni].crouch_checked = 1;
    double pos[3] = {N[ni].pos[0], N[ni].pos[1], N[ni].pos[2]};
    Memo *e = memo_at(pos);
    if (!e->have_crouch) {
        Props pr; acc_begin(); crouch_props(pos, &pr);
        e = memo_at(pos); acc_end(e->cbox); e->crouch = pr; e->have_crouch = 1; g_memo_misses++;
    } else g_memo_hits++;
    for (int c = 0; c < 4; c++) {
        N[ni].ground[c] = e->crouch.ground[c];
        if (e->crouch.crouch[c]) N[ni].crouch[c] = 1;
        if (e->crouch.blocked[c]) N[ni].blocked[c] = 1;
    }
    N[ni].attributes |= e->crouch.attrs;
}
static void check_cliff_node(int ni) {
    if (N[ni].cliff_checked) return;
    N[ni].cliff_checked = 1;
    double pos[3] = {N[ni].pos[0], N[ni].pos[1], N[ni].pos[2]};
    Memo *e = memo_at(pos);
    if (!e->have_cliff) {
        acc_begin(); int c = cliff_at(pos);
        e = memo_at(pos); acc_end(e->clbox); e->cliff = c; e->have_cliff = 1; g_memo_misses++;
    } else g_memo_hits++;
    if (e->cliff) N[ni].attributes |= NAV_MESH_CLIFF;
}

/* returns the new node index, or -1 when an existing node was linked */
static int add_node(const double *dest, const double *nrm, int d, int src, double obstacle_height, int on_disp) {
    int node = get_node(dest);
    int isnew = node < 0;
    if (isnew) node = new_node(dest, nrm, src, on_disp);
    N[src].to[d] = node; N[src].obstacle[d] = obstacle_height;
    double dz = N[src].pos[2] - dest[2];
    if (fabs(dz) < COMMUTATIVE_Z) {
        if (obstacle_height > 0) { obstacle_height = obstacle_height + dz; if (obstacle_height < 0.0) obstacle_height = 0.0; }
        N[node].to[OPP[d]] = src; N[node].obstacle[OPP[d]] = obstacle_height;
        N[node].visited |= 1 << OPP[d];
    }
    check_crouch(node);
    check_cliff_node(node);
    return isnew ? node : -1;
}

static void step_compute(const double *from, int d, StepRes *r) {
    r->ok = 0;
    double cx = round_to_units(from[0], GENERATION_STEP) + STEPX[d];
    double cy = round_to_units(from[1], GENERATION_STEP) + STEPY[d];
    double frm[3] = {from[0], from[1], from[2]};
    double pos[3] = {cx, cy, frm[2]};
    double obstacle_height = 0.0, to[3], to_n[3];
    Tr result; int ok = trace_adjacent(0, frm, pos, &result, DEATH_DROP);
    if (ok) { to[0] = result.ex; to[1] = result.ey; to[2] = result.ez; to_n[0] = result.nx; to_n[1] = result.ny; to_n[2] = result.nz; }
    else {
        int success = 0; double h = STEP_HEIGHT;
        while (h <= CLIMB_UP_HEIGHT) {
            double a[3] = {frm[0], frm[1], frm[2] + h}, b[3] = {pos[0], pos[1], pos[2] + h};
            Tr tr = hull(a, b);
            if (!tr.startsolid && tr.fraction == 1.0) {
                if (!stay_on_floor(&tr, DEATH_DROP)) break;
                to[0] = tr.ex; to[1] = tr.ey; to[2] = tr.ez; to_n[0] = tr.nx; to_n[1] = tr.ny; to_n[2] = tr.nz;
                double c[3] = {frm[0], frm[1], frm[2] + h};
                Tr tr2 = hull(frm, c);
                if (tr2.fraction < 1.0) break;
                obstacle_height = h; success = 1; break;
            }
            h += 1.0;
        }
        if (!success) return;
    }
    if (result.flags & 1) return;          /* sky */
    if (node_overlapped(to, 1, 1) && node_overlapped(to, -1, 1) && node_overlapped(to, 1, -1) && node_overlapped(to, -1, -1)) return;
    double up[3] = {to[0], to[1], to[2] + DISPLACEMENT_TEST};
    Tr u = hull(to, up);
    if (u.fraction > 0) {
        double ue[3] = {u.ex, u.ey, u.ez};
        Tr dn = hull(ue, to);
        if (dn.fraction < 1 && dn.ez > to[2] + STEP_HEIGHT) return;
    }
    double dz = to[2] - from[2];
    if (obstacle_height < STEP_HEIGHT || dz > obstacle_height - 2.0) obstacle_height = 0.0;
    r->ok = 1; memcpy(r->to, to, 24); memcpy(r->n, to_n, 24); r->obst = obstacle_height; r->disp = (result.flags & 2) != 0;
}

static int NCUT;                        /* (No Nav Volumes: see hl_cuts) */
static int in_cut(const double *p);

static int step(int cur, int d) {
    double pos[3] = {N[cur].pos[0], N[cur].pos[1], N[cur].pos[2]};
    Memo *e = memo_at(pos);
    if (!e->have_step[d]) {
        StepRes r; acc_begin(); step_compute(pos, d, &r);
        e = memo_at(pos); acc_end(e->sbox[d]); e->s[d] = r; e->have_step[d] = 1; g_memo_misses++;
    } else g_memo_hits++;
    StepRes r = e->s[d];
    if (!r.ok) return -1;
    if (NCUT && in_cut(r.to)) return -1;           /* (a No Nav Volume: like a wall) */
    return add_node(r.to, r.n, d, cur, r.obst, r.disp);
}

static double *SEEDS; static int NSEEDS;

/* No Nav Volumes: no node is made inside one (a convex hull each: its box, then n.p <= d + eps for its planes;
 * no planes: the box alone) */
static int NCUT; static double *CUTB, *CUTP, CUTE; static int *CUTN;
EXPORT void hl_cuts(int n, const double *bounds, const int *counts, const double *planes, double eps) {
    free(CUTB), free(CUTP), free(CUTN);
    CUTB = NULL, CUTP = NULL, CUTN = NULL, NCUT = 0, CUTE = eps;
    if (n <= 0) return;
    int total = 0;
    for (int i = 0; i < n; i++) total += counts[i];
    CUTB = xrealloc(NULL, sizeof(double) * 6 * n);
    CUTN = xrealloc(NULL, sizeof(int) * n);
    CUTP = xrealloc(NULL, sizeof(double) * 4 * (total + 1));
    memcpy(CUTB, bounds, sizeof(double) * 6 * n);
    memcpy(CUTN, counts, sizeof(int) * n);
    memcpy(CUTP, planes, sizeof(double) * 4 * total);
    NCUT = n;
}
static int in_cut(const double *p) {
    const double *pl = CUTP;
    for (int i = 0; i < NCUT; pl += 4 * CUTN[i], i++) {
        const double *b = CUTB + 6 * i;
        int in = 1;
        for (int k = 0; k < 3 && in; k++) in = b[k] - CUTE <= p[k] && p[k] <= b[3 + k] + CUTE;
        for (int j = 0; j < CUTN[i] && in; j++)
            in = pl[4 * j] * p[0] + pl[4 * j + 1] * p[1] + pl[4 * j + 2] * p[2] <= pl[4 * j + 3] + CUTE;
        if (in) return 1;
    }
    return 0;
}

EXPORT void hl_reset(void) {
    free(N); N = NULL; NN = NCAP = 0; free(H); H = NULL; HCAP = 0; hcount = 0; free(SEEDS); SEEDS = NULL; NSEEDS = 0; g_traces = 0; g_memo_hits = g_memo_misses = 0;
}

static int add_seed_impl(double x, double y, double z) {
    double p[3] = {round_to_units(x, GENERATION_STEP), round_to_units(y, GENERATION_STEP), z};
    double s[3] = {p[0], p[1], p[2] + DUCK_HULL_TOP - 0.1}, e[3] = {p[0], p[1], p[2] - DEATH_DROP};
    Tr tr = hull(s, e);
    if (tr.allsolid) return 0;
    if (NCUT) {
        double g[3] = {round_to_units(tr.ex, GENERATION_STEP), round_to_units(tr.ey, GENERATION_STEP), tr.ez};
        if (in_cut(g)) return 0;
    }
    SEEDS = xrealloc(SEEDS, sizeof(double) * 6 * (NSEEDS + 1));
    double *o = SEEDS + 6 * NSEEDS++;
    o[0] = round_to_units(tr.ex, GENERATION_STEP); o[1] = round_to_units(tr.ey, GENERATION_STEP); o[2] = tr.ez;
    o[3] = tr.nx; o[4] = tr.ny; o[5] = tr.nz;
    return 1;
}

/* flood from 'cur' until the search backs out past it */
static void run(int cur, int max_nodes) {
    while (cur >= 0 && NN < max_nodes) {
        int d = -1;
        for (int k = 0; k < 4; k++) if (!(N[cur].visited & (1 << k))) { d = k; break; }
        if (d < 0) { cur = N[cur].parent; continue; }
        N[cur].visited |= 1 << d;
        int nx = step(cur, d);
        if (nx >= 0) cur = nx;
    }
}

static int sample_impl(int max_nodes) {
    if (!H) hrehash();
    for (int seed_i = 0; seed_i < NSEEDS && NN < max_nodes; seed_i++) {
        double *sd = SEEDS + 6 * seed_i;
        if (get_node(sd) >= 0) continue;         /* L4D2 skips covered seeds */
        run(new_node(sd, sd + 3, -1, 0), max_nodes);
    }
    return NN;
}

/* carry on sampling from a new node (a ladder end, found by the caller) */
static int sample_from_impl(double x, double y, double z, double nx, double ny, double nz, int max_nodes) {
    if (!H) hrehash();
    double p[3] = {x, y, z}, n[3] = {nx, ny, nz};
    run(new_node(p, n, -1, 0), max_nodes);
    return NN;
}

static int has_node_impl(double x, double y, double z) {
    if (!H) hrehash();
    double p[3] = {x, y, z};
    return get_node(p) >= 0;
}

EXPORT int hl_node_count(void) { return NN; }

/* per node: pos3 normal3 obstacle4 ground4 (14 doubles); to4 parent attributes crouch4 blocked4 on_disp (15 ints) */
EXPORT void hl_nodes(double *d14, int *i15) {
    for (int i = 0; i < NN; i++) {
        Node *n = &N[i]; double *d = d14 + 14 * i; int *k = i15 + 15 * i;
        memcpy(d, n->pos, 24); memcpy(d + 3, n->normal, 24); memcpy(d + 6, n->obstacle, 32); memcpy(d + 10, n->ground, 32);
        for (int j = 0; j < 4; j++) { k[j] = n->to[j]; k[6 + j] = n->crouch[j]; k[10 + j] = n->blocked[j]; }
        k[4] = n->parent; k[5] = n->attributes; k[14] = n->on_disp;
    }
}

EXPORT long long hl_trace_count(void) { return g_traces; }
EXPORT long long hl_memo_hits(void) { return g_memo_hits; }
EXPORT long long hl_memo_misses(void) { return g_memo_misses; }
EXPORT int hl_memo_size(void) { return NM; }

/* ---------------------------------------------------------------- areas (navareas.Generator.create_areas) */
#define AREA_MAX_SIZE 50
#define OFF_PLANE_TOLERANCE 5.0
#define MAX_TRAVERSABLE_HEIGHT STEP_HEIGHT
#define GEN_NORTH 0
#define GEN_EAST 1
#define GEN_SOUTH 2
#define GEN_WEST 3
#define C_NW 0
#define C_NE 1
#define C_SE 2
#define C_SW 3

static int bi_linked(int i, int d) {
    int n = N[i].to[d];
    return n >= 0 && N[n].to[OPP[d]] == i && fabs(N[n].pos[2] - N[i].pos[2]) <= STEP_HEIGHT;
}
static int closed_cell(int i) {
    if (!(bi_linked(i, GEN_SOUTH) && bi_linked(i, GEN_EAST))) return 0;
    int e = N[i].to[GEN_EAST], s = N[i].to[GEN_SOUTH];
    return bi_linked(e, GEN_SOUTH) && bi_linked(s, GEN_EAST) && N[e].to[GEN_SOUTH] == N[s].to[GEN_EAST];
}
static int blocked_any(int i) { const int *b = N[i].blocked; return b[0] || b[1] || b[2] || b[3]; }
static int check_obstacles(int i, int width, int height, int x, int y) {
    if (width > 1 || height > 1) {
        const double *o = N[i].obstacle;
        if (x > 0 && o[GEN_WEST] > MAX_TRAVERSABLE_HEIGHT) return 0;
        if (y > 0 && o[GEN_NORTH] > MAX_TRAVERSABLE_HEIGHT) return 0;
        if (x < width - 1 && o[GEN_EAST] > MAX_TRAVERSABLE_HEIGHT) return 0;
        if (y < height - 1 && o[GEN_SOUTH] > MAX_TRAVERSABLE_HEIGHT) return 0;
    }
    return 1;
}
static int valid_crouch_area(int i) {
    static const double mins[3] = {0.0, 0.0, 0.0}, maxs[3] = {GENERATION_STEP, GENERATION_STEP, HUMAN_CROUCH_HEIGHT};
    const double *p = N[i].pos; double e[3] = {p[0], p[1], p[2] + JUMP_CROUCH_HEIGHT};
    return !trace(p, e, mins, maxs).allsolid;
}

static unsigned char *COVERED, *CLOSED;

/* TestArea (navareas.Generator.test_area) */
/* where the last test failed, for skipping tests that must fail again (navareas._still_fails) */
#define F_NONE 0
#define F_DEAD 1
#define F_CELL 2
#define F_EAST 3
#define F_SOUTH 4
typedef struct { unsigned char kind, multi_only; short x, y; } Fail;
static Fail g_fail;
static int fail_at(int kind, int x, int y, int multi_only) { g_fail.kind = (unsigned char)kind; g_fail.x = (short)x; g_fail.y = (short)y; g_fail.multi_only = (unsigned char)multi_only; return 0; }
static int still_fails(const Fail *f, int width, int height) {
    if (f->kind == F_NONE) return 0;
    if (f->multi_only && width == 1 && height == 1) return 0;
    if (f->kind == F_DEAD) return 1;
    if (f->kind == F_CELL) return f->x < width && f->y < height;
    if (f->kind == F_EAST) return f->x <= width && f->y < height;
    return f->y <= height;
}
static int test_area(int node, int width, int height) {
    g_fail.kind = F_NONE;
    const double *normal = N[node].normal, *pos = N[node].pos;
    double d = -(normal[0] * pos[0] + normal[1] * pos[1] + normal[2] * pos[2]);
#define OFF_PLANE(k) (fabs(N[k].pos[0] * normal[0] + N[k].pos[1] * normal[1] + N[k].pos[2] * normal[2] + d) > OFF_PLANE_TOLERANCE)
    int node_crouch = N[node].crouch[C_SE];
    if (N[node].blocked[C_SE]) return fail_at(F_DEAD, 0, 0, 0);
    int node_attr = N[node].attributes & ~NAV_MESH_CROUCH;
    int multi = width > 1 || height > 1;
    int vert = node, horiz;
    for (int y = 0; y < height; y++) {
        horiz = vert;
        for (int x = 0; x < width; x++) {
            Node *h = &N[horiz]; int hc;
            if (y == 0 && x == 0) { hc = h->crouch[C_SE]; if (h->blocked[C_SE]) return fail_at(F_CELL, x, y, 0); }
            else if (y == 0) { hc = h->crouch[C_SE] || h->crouch[C_SW]; if (h->blocked[C_SE] || h->blocked[C_SW]) return fail_at(F_CELL, x, y, 0); }
            else if (x == 0) { hc = h->crouch[C_SE] || h->crouch[C_NE]; if (h->blocked[C_SE] || h->blocked[C_NE]) return fail_at(F_CELL, x, y, 0); }
            else { hc = (h->attributes & NAV_MESH_CROUCH) != 0; if (blocked_any(horiz)) return fail_at(F_CELL, x, y, 0); }
            if ((node_crouch != 0) != (hc != 0)) return fail_at(F_CELL, x, y, 0);
            if ((h->attributes & ~NAV_MESH_CROUCH) != node_attr) return fail_at(F_CELL, x, y, 0);
            if (COVERED[horiz] || !CLOSED[horiz]) return fail_at(F_CELL, x, y, 0);
            if (!check_obstacles(horiz, width, height, x, y)) return 0;
            horiz = h->to[GEN_EAST];
            if (horiz < 0) return fail_at(F_EAST, x + 1, y, 0);
            if (multi && OFF_PLANE(horiz)) return fail_at(F_EAST, x + 1, y, 1);
        }
        if (!check_obstacles(horiz, width, height, width, y)) return 0;
        vert = N[vert].to[GEN_SOUTH];
        if (vert < 0) return fail_at(F_SOUTH, 0, y + 1, 0);
        if (multi && OFF_PLANE(vert)) return fail_at(F_SOUTH, 0, y + 1, 1);
    }
    if (multi) {
        horiz = vert;
        for (int x = 0; x < width; x++) {
            if (!check_obstacles(horiz, width, height, x, height)) return 0;
            horiz = N[horiz].to[GEN_EAST];
            if (horiz < 0 || OFF_PLANE(horiz)) return 0;
        }
        if (!check_obstacles(horiz, width, height, width, height)) return 0;
    }
    if (node_crouch) {
        vert = node;
        for (int y = 0; y < height; y++) {
            horiz = vert;
            for (int x = 0; x < width; x++) {
                if (!valid_crouch_area(horiz)) return fail_at(F_CELL, x, y, 0);
                horiz = N[horiz].to[GEN_EAST];
            }
            vert = N[vert].to[GEN_SOUTH];
        }
    }
    return 1;
#undef OFF_PLANE
}

static int covered_count(int node, int width, int height) {
    int n = 0, vert = node;
    for (int y = 0; y < height; y++) {
        int horiz = vert;
        for (int x = 0; x < width; x++) { COVERED[horiz] = 1; n++; horiz = N[horiz].to[GEN_EAST]; }
        vert = N[vert].to[GEN_SOUTH];
    }
    return n;
}

/* longest closed-cell run from each node in direction d (a speed-up bound only) */
static void runs(int d, int *run) {
    for (int i = 0; i < NN; i++) run[i] = -1;
    int *chain = xmalloc(sizeof(int) * (NN + 1));
    for (int s = 0; s < NN; s++) {
        if (run[s] >= 0) continue;
        int len = 0, n = s;
        while (n >= 0 && run[n] == -1 && CLOSED[n]) { run[n] = -2; chain[len++] = n; n = N[n].to[d]; }
        int base = (n >= 0 && run[n] >= 0) ? run[n] : 0;
        for (int k = len - 1, v = base + 1; k >= 0; k--, v++) run[chain[k]] = v;
        if (run[s] < 0) run[s] = 0;
    }
    free(chain);
}

/* CreateNavAreasFromNodes: writes (node index, width, height) per area in build order; returns the count */
static int create_areas_impl(int *out, int cap) {
    free(COVERED); free(CLOSED);
    COVERED = xcalloc(NN + 1, 1); CLOSED = xcalloc(NN + 1, 1);
    for (int i = 0; i < NN; i++) CLOSED[i] = (unsigned char)closed_cell(i);
    int *east = xmalloc(sizeof(int) * (NN + 1)), *south = xmalloc(sizeof(int) * (NN + 1));
    runs(GEN_EAST, east); runs(GEN_SOUTH, south);
    Fail *memo = xcalloc(NN + 1, sizeof(Fail));
    int width = AREA_MAX_SIZE, height = AREA_MAX_SIZE, uncovered = NN, count = 0;
    while (uncovered > 0) {
        for (int node = NN - 1; node >= 0; node--) {          /* CNavNode::m_list: newest first */
            if (COVERED[node] || east[node] < width || south[node] < height) continue;
            if (still_fails(&memo[node], width, height)) continue;
            if (test_area(node, width, height)) {
                uncovered -= covered_count(node, width, height);
                if (count < cap) { out[3 * count] = node; out[3 * count + 1] = width; out[3 * count + 2] = height; }
                count++;
            } else if (g_fail.kind != F_NONE) memo[node] = g_fail;
        }
        if (width >= height) width--; else height--;
        if (width <= 0 || height <= 0) break;
    }
    free(east); free(south); free(memo);
    return count;
}

#include "hlareas.c"
#include "hlvis.c"

