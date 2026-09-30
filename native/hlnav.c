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

#define EXPORT __declspec(dllexport)
#define DIST_EPSILON 0.03125
#define NEVER_UPDATED -9999.0

/* ---------------------------------------------------------------- world */
typedef struct { double nx, ny, nz, dist, ax, ay, az; int bevel, flags; } Side;   /* flags: 1 sky, 2 displacement */
typedef struct { double b[6]; int first, count; } Brush;

static Side *g_sides; static Brush *g_brushes; static int g_nbrushes;
static double g_cell; static int g_cx0, g_cy0, g_w, g_h;
static int *g_cell_start, *g_cell_count, *g_cell_ids;   /* dense grid of cell lists (ascending brush ids) */
static int *g_scratch; static int g_scratch_cap; static int *g_mark; static int g_mark_gen;

EXPORT void hl_world(int nbrushes, const double *bounds, const int *side_first, const int *side_count,
                     int nsides, const double *sides7, const int *side_bevel, const int *side_flags,
                     double cell, int cx0, int cy0, int w, int h, const int *cell_start, const int *cell_count,
                     int nids, const int *cell_ids) {
    free(g_sides); free(g_brushes); free(g_cell_start); free(g_cell_count); free(g_cell_ids); free(g_mark);
    g_sides = malloc(sizeof(Side) * (nsides ? nsides : 1));
    for (int i = 0; i < nsides; i++) {
        const double *s = sides7 + 7 * i;
        g_sides[i].nx = s[0]; g_sides[i].ny = s[1]; g_sides[i].nz = s[2]; g_sides[i].dist = s[3];
        g_sides[i].ax = s[4]; g_sides[i].ay = s[5]; g_sides[i].az = s[6];
        g_sides[i].bevel = side_bevel[i]; g_sides[i].flags = side_flags[i];
    }
    g_nbrushes = nbrushes;
    g_brushes = malloc(sizeof(Brush) * (nbrushes ? nbrushes : 1));
    for (int i = 0; i < nbrushes; i++) {
        memcpy(g_brushes[i].b, bounds + 6 * i, sizeof(double) * 6);
        g_brushes[i].first = side_first[i]; g_brushes[i].count = side_count[i];
    }
    g_cell = cell; g_cx0 = cx0; g_cy0 = cy0; g_w = w; g_h = h;
    g_cell_start = malloc(sizeof(int) * (w * h ? w * h : 1)); memcpy(g_cell_start, cell_start, sizeof(int) * w * h);
    g_cell_count = malloc(sizeof(int) * (w * h ? w * h : 1)); memcpy(g_cell_count, cell_count, sizeof(int) * w * h);
    g_cell_ids = malloc(sizeof(int) * (nids ? nids : 1)); memcpy(g_cell_ids, cell_ids, sizeof(int) * nids);
    g_mark = calloc(nbrushes ? nbrushes : 1, sizeof(int)); g_mark_gen = 0;
}

typedef struct { double fraction, ex, ey, ez, nx, ny, nz, left; int startsolid, allsolid, flags; } Tr;

static int cmp_int(const void *a, const void *b) { return (*(const int *)a > *(const int *)b) - (*(const int *)a < *(const int *)b); }

static void clip(const Brush *br, double p1x, double p1y, double p1z, double p2x, double p2y, double p2z,
                 double ex, double ey, double ez, int is_point, Tr *tr) {
    double enter = NEVER_UPDATED, leave = 1.0;
    int getout = 0, startout = 0; const Side *clipside = NULL;
    for (int k = 0; k < br->count; k++) {
        const Side *s = &g_sides[br->first + k];
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

static Tr trace(const double *start, const double *end, const double *mins, const double *maxs) {
    g_traces++;
    double ex = (maxs[0] - mins[0]) * 0.5, ey = (maxs[1] - mins[1]) * 0.5, ez = (maxs[2] - mins[2]) * 0.5;
    double ox = (maxs[0] + mins[0]) * 0.5, oy = (maxs[1] + mins[1]) * 0.5, oz = (maxs[2] + mins[2]) * 0.5;
    double p1x = start[0] + ox, p1y = start[1] + oy, p1z = start[2] + oz;
    double p2x = end[0] + ox, p2y = end[1] + oy, p2z = end[2] + oz;
    int is_point = (ex == 0.0 && ey == 0.0 && ez == 0.0);
    Tr tr; memset(&tr, 0, sizeof tr); tr.fraction = 1.0;
    double x0 = (p1x < p2x ? p1x : p2x) - ex - 1, y0 = (p1y < p2y ? p1y : p2y) - ey - 1, z0 = (p1z < p2z ? p1z : p2z) - ez - 1;
    double x1 = (p1x > p2x ? p1x : p2x) + ex + 1, y1 = (p1y > p2y ? p1y : p2y) + ey + 1, z1 = (p1z > p2z ? p1z : p2z) + ez + 1;
    int cx0 = (int)floor(x0 / g_cell), cx1 = (int)floor(x1 / g_cell), cy0 = (int)floor(y0 / g_cell), cy1 = (int)floor(y1 / g_cell);
    int n = 0;
    if (cx0 == cx1 && cy0 == cy1) {
        int gx = cx0 - g_cx0, gy = cy0 - g_cy0;
        if (gx >= 0 && gy >= 0 && gx < g_w && gy < g_h) {
            int c = gx * g_h + gy; n = g_cell_count[c];
            if (n > g_scratch_cap) { g_scratch_cap = n * 2 + 64; g_scratch = realloc(g_scratch, sizeof(int) * g_scratch_cap); }
            memcpy(g_scratch, g_cell_ids + g_cell_start[c], sizeof(int) * n);
        }
    } else {
        g_mark_gen++;
        for (int cx = cx0; cx <= cx1; cx++) for (int cy = cy0; cy <= cy1; cy++) {
            int gx = cx - g_cx0, gy = cy - g_cy0;
            if (gx < 0 || gy < 0 || gx >= g_w || gy >= g_h) continue;
            int c = gx * g_h + gy;
            for (int k = 0; k < g_cell_count[c]; k++) {
                int id = g_cell_ids[g_cell_start[c] + k];
                if (g_mark[id] == g_mark_gen) continue;
                g_mark[id] = g_mark_gen;
                if (n >= g_scratch_cap) { g_scratch_cap = n * 2 + 64; g_scratch = realloc(g_scratch, sizeof(int) * g_scratch_cap); }
                g_scratch[n++] = id;
            }
        }
        qsort(g_scratch, n, sizeof(int), cmp_int);
    }
    for (int k = 0; k < n; k++) {
        const Brush *b = &g_brushes[g_scratch[k]];
        if (b->b[0] <= x1 && b->b[3] >= x0 && b->b[1] <= y1 && b->b[4] >= y0 && b->b[2] <= z1 && b->b[5] >= z0) {
            clip(b, p1x, p1y, p1z, p2x, p2y, p2z, ex, ey, ez, is_point, &tr);
            if (tr.allsolid) break;
        }
    }
    if (tr.fraction == 1.0) { tr.ex = end[0]; tr.ey = end[1]; tr.ez = end[2]; }
    else {
        double f = tr.fraction;
        tr.ex = start[0] + f * (end[0] - start[0]); tr.ey = start[1] + f * (end[1] - start[1]); tr.ez = start[2] + f * (end[2] - start[2]);
    }
    return tr;
}

EXPORT void hl_trace(const double *start, const double *end, const double *mins, const double *maxs, double *out10) {
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

static uint64_t key_xy(double x, double y) { uint64_t a, b; memcpy(&a, &x, 8); memcpy(&b, &y, 8); return a * 0x9E3779B97F4A7C15ULL ^ (b + 0x632BE59BD9B4E019ULL + (a << 6) + (a >> 2)); }
static int hslot(double x, double y) {
    uint64_t k = key_xy(x, y); int i = (int)(k & (uint64_t)(HCAP - 1));
    while (H[i] >= 0) { Node *n = &N[H[i]]; if (n->pos[0] == x && n->pos[1] == y) return i; i = (i + 1) & (HCAP - 1); }
    return i;
}
static void hrehash(void) {
    int old = HCAP; int *oh = H; HCAP = HCAP ? HCAP * 2 : 1 << 16;
    H = malloc(sizeof(int) * HCAP); for (int i = 0; i < HCAP; i++) H[i] = -1;
    if (oh) { for (int i = 0; i < old; i++) if (oh[i] >= 0) { Node *n = &N[oh[i]]; H[hslot(n->pos[0], n->pos[1])] = oh[i]; } free(oh); }
}
static int hcount;

static int get_node(const double *p) {
    int s = hslot(p[0], p[1]);
    for (int i = H[s]; i >= 0; i = N[i].next_xy) if (fabs(N[i].pos[2] - p[2]) < NODE_Z_TOLERANCE) return i;
    return -1;
}
static int new_node(const double *p, const double *nrm, int parent, int on_disp) {
    if (NN == NCAP) { NCAP = NCAP ? NCAP * 2 : 4096; N = realloc(N, sizeof(Node) * NCAP); }
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

static int test_crouch_area(int ni, int corner, const double *mins, double mx, double my) {
    double p[3] = {N[ni].pos[0], N[ni].pos[1], N[ni].pos[2]};
    double up[3] = {p[0], p[1], p[2] + JUMP_CROUCH_HEIGHT};
    Tr tr = hull(p, up);
    double max_height = tr.ez - p[2];
    double h = 0.0;
    while (h <= max_height) {
        double s[3] = {p[0], p[1], p[2] + h};
        double m1[3] = {mx, my, HUMAN_CROUCH_HEIGHT};
        Tr t = trace(s, s, mins, m1);
        if (!t.startsolid) {
            N[ni].ground[corner] = s[2] - p[2];
            double m2[3] = {mx, my, HUMAN_HEIGHT};
            t = trace(s, s, mins, m2);
            return !t.startsolid;
        }
        h += 1.0;
    }
    N[ni].ground[corner] = JUMP_CROUCH_HEIGHT;
    N[ni].blocked[corner] = 1;
    return 0;
}

static void check_crouch(int ni) {
    if (N[ni].crouch_checked) return;
    N[ni].crouch_checked = 1;
    for (int c = 0; c < 4; c++) {
        double vx = CVX[c], vy = CVY[c];
        double mins[3] = {fmin(0.0, vx * HALF_HUMAN_WIDTH), fmin(0.0, vy * HALF_HUMAN_WIDTH), 0.0};
        double mxx = fmax(0.0, vx * HALF_HUMAN_WIDTH), mxy = fmax(0.0, vy * HALF_HUMAN_WIDTH);
        if (!test_crouch_area(ni, c, mins, mxx, mxy)) { N[ni].attributes |= NAV_MESH_CROUCH; N[ni].crouch[c] = 1; }
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
    if (!N[node].cliff_checked) {
        N[node].cliff_checked = 1;
        for (int k = 0; k < 4; k++) { double p[3] = {N[node].pos[0], N[node].pos[1], N[node].pos[2]}; if (check_cliff(p, k, 1)) { N[node].attributes |= NAV_MESH_CLIFF; break; } }
    }
    return isnew ? node : -1;
}

static int step(int cur, int d) {
    double cx = round_to_units(N[cur].pos[0], GENERATION_STEP) + STEPX[d];
    double cy = round_to_units(N[cur].pos[1], GENERATION_STEP) + STEPY[d];
    double frm[3] = {N[cur].pos[0], N[cur].pos[1], N[cur].pos[2]};
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
        if (!success) return -1;
    }
    if (result.flags & 1) return -1;       /* sky */
    if (node_overlapped(to, 1, 1) && node_overlapped(to, -1, 1) && node_overlapped(to, 1, -1) && node_overlapped(to, -1, -1)) return -1;
    double up[3] = {to[0], to[1], to[2] + DISPLACEMENT_TEST};
    Tr u = hull(to, up);
    if (u.fraction > 0) {
        double ue[3] = {u.ex, u.ey, u.ez};
        Tr dn = hull(ue, to);
        if (dn.fraction < 1 && dn.ez > to[2] + STEP_HEIGHT) return -1;
    }
    double dz = to[2] - N[cur].pos[2];
    if (obstacle_height < STEP_HEIGHT || dz > obstacle_height - 2.0) obstacle_height = 0.0;
    return add_node(to, to_n, d, cur, obstacle_height, (result.flags & 2) != 0);
}

static double *SEEDS; static int NSEEDS;

EXPORT void hl_reset(void) {
    free(N); N = NULL; NN = NCAP = 0; free(H); H = NULL; HCAP = 0; hcount = 0; free(SEEDS); SEEDS = NULL; NSEEDS = 0; g_traces = 0;
}

EXPORT int hl_add_seed(double x, double y, double z) {
    double p[3] = {round_to_units(x, GENERATION_STEP), round_to_units(y, GENERATION_STEP), z};
    double s[3] = {p[0], p[1], p[2] + DUCK_HULL_TOP - 0.1}, e[3] = {p[0], p[1], p[2] - DEATH_DROP};
    Tr tr = hull(s, e);
    if (tr.allsolid) return 0;
    SEEDS = realloc(SEEDS, sizeof(double) * 6 * (NSEEDS + 1));
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

EXPORT int hl_sample(int max_nodes) {
    if (!H) hrehash();
    for (int seed_i = 0; seed_i < NSEEDS && NN < max_nodes; seed_i++) {
        double *sd = SEEDS + 6 * seed_i;
        if (get_node(sd) >= 0) continue;         /* L4D2 skips covered seeds */
        run(new_node(sd, sd + 3, -1, 0), max_nodes);
    }
    return NN;
}

/* carry on sampling from a new node (a ladder end, found by the caller) */
EXPORT int hl_sample_from(double x, double y, double z, double nx, double ny, double nz, int max_nodes) {
    if (!H) hrehash();
    double p[3] = {x, y, z}, n[3] = {nx, ny, nz};
    run(new_node(p, n, -1, 0), max_nodes);
    return NN;
}

EXPORT int hl_has_node(double x, double y, double z) {
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
static int test_area(int node, int width, int height) {
    const double *normal = N[node].normal, *pos = N[node].pos;
    double d = -(normal[0] * pos[0] + normal[1] * pos[1] + normal[2] * pos[2]);
#define OFF_PLANE(k) (fabs(N[k].pos[0] * normal[0] + N[k].pos[1] * normal[1] + N[k].pos[2] * normal[2] + d) > OFF_PLANE_TOLERANCE)
    int node_crouch = N[node].crouch[C_SE];
    if (N[node].blocked[C_SE]) return 0;
    int node_attr = N[node].attributes & ~NAV_MESH_CROUCH;
    int multi = width > 1 || height > 1;
    int vert = node, horiz;
    for (int y = 0; y < height; y++) {
        horiz = vert;
        for (int x = 0; x < width; x++) {
            Node *h = &N[horiz]; int hc;
            if (y == 0 && x == 0) { hc = h->crouch[C_SE]; if (h->blocked[C_SE]) return 0; }
            else if (y == 0) { hc = h->crouch[C_SE] || h->crouch[C_SW]; if (h->blocked[C_SE] || h->blocked[C_SW]) return 0; }
            else if (x == 0) { hc = h->crouch[C_SE] || h->crouch[C_NE]; if (h->blocked[C_SE] || h->blocked[C_NE]) return 0; }
            else { hc = (h->attributes & NAV_MESH_CROUCH) != 0; if (blocked_any(horiz)) return 0; }
            if ((node_crouch != 0) != (hc != 0)) return 0;
            if ((h->attributes & ~NAV_MESH_CROUCH) != node_attr) return 0;
            if (COVERED[horiz] || !CLOSED[horiz]) return 0;
            if (!check_obstacles(horiz, width, height, x, y)) return 0;
            horiz = h->to[GEN_EAST];
            if (horiz < 0) return 0;
            if (multi && OFF_PLANE(horiz)) return 0;
        }
        if (!check_obstacles(horiz, width, height, width, y)) return 0;
        vert = N[vert].to[GEN_SOUTH];
        if (vert < 0) return 0;
        if (multi && OFF_PLANE(vert)) return 0;
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
                if (!valid_crouch_area(horiz)) return 0;
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
    int *chain = malloc(sizeof(int) * (NN + 1));
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
EXPORT int hl_create_areas(int *out, int cap) {
    free(COVERED); free(CLOSED);
    COVERED = calloc(NN + 1, 1); CLOSED = calloc(NN + 1, 1);
    for (int i = 0; i < NN; i++) CLOSED[i] = (unsigned char)closed_cell(i);
    int *east = malloc(sizeof(int) * (NN + 1)), *south = malloc(sizeof(int) * (NN + 1));
    runs(GEN_EAST, east); runs(GEN_SOUTH, south);
    int width = AREA_MAX_SIZE, height = AREA_MAX_SIZE, uncovered = NN, count = 0;
    while (uncovered > 0) {
        for (int node = NN - 1; node >= 0; node--) {          /* CNavNode::m_list: newest first */
            if (COVERED[node] || east[node] < width || south[node] < height) continue;
            if (test_area(node, width, height)) {
                uncovered -= covered_count(node, width, height);
                if (count < cap) { out[3 * count] = node; out[3 * count + 1] = width; out[3 * count + 2] = height; }
                count++;
            }
        }
        if (width >= height) width--; else height--;
        if (width <= 0 || height <= 0) break;
    }
    free(east); free(south);
    return count;
}
