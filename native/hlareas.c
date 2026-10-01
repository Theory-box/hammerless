/* Hammerless native nav areas: core/navareas.py's Generator from building areas through
 * FixConnections, on the nodes the flood fill left in hlnav.c. Included from hlnav.c.
 * Must give exactly the areas, connections and list orders the Python code gives (checked
 * stage by stage against it); the Python stays as the reference and the fallback.
 * Every Python list (areas, connections, incoming connections) is kept as an int list in the
 * same order with the same append / remove-first / contains operations.
 */

#define GRID 300.0
#define NAV_MESH_JUMP 0x0002
#define NAV_MESH_NO_MERGE 0x2000
#define NAV_MESH_STAIRS 0x1000
#define COPLANAR_SLOPE_LIMIT 0.99
#define COPLANAR_SLOPE_LIMIT_DISP 0.7
#define SLOPE_TOLERANCE 0.1
#define MERGE_MAX_TOTAL_CELLS 32
#define WALK_GUARD 1000000            /* node walks that would raise in Python stop here instead */

/* ---------------------------------------------------------------- int lists (Python lists) */
typedef struct { int *v; int n, cap; } IL;
static void il_push(IL *l, int x) { if (l->n == l->cap) { l->cap = l->cap ? l->cap * 2 : 4; l->v = realloc(l->v, sizeof(int) * l->cap); } l->v[l->n++] = x; }
static int il_has(const IL *l, int x) { for (int i = 0; i < l->n; i++) if (l->v[i] == x) return 1; return 0; }
static int il_remove(IL *l, int x) {                 /* list.remove: the first one */
    for (int i = 0; i < l->n; i++) if (l->v[i] == x) { memmove(l->v + i, l->v + i + 1, sizeof(int) * (l->n - i - 1)); l->n--; return 1; }
    return 0;
}
static void il_free(IL *l) { free(l->v); l->v = NULL; l->n = l->cap = 0; }
static IL il_copy(const IL *l) { IL c = {0}; if (l->n) { c.cap = l->n; c.v = malloc(sizeof(int) * c.cap); memcpy(c.v, l->v, sizeof(int) * l->n); c.n = l->n; } return c; }

/* ---------------------------------------------------------------- areas */
typedef struct {
    int seq; double nw[3], se[3], ne_z, sw_z; int nodes[4]; int attributes;
    IL connect[4], incoming[4];
} Area;
static Area *A; static int NA_, ACAP;     /* every area made this run (an index is the area's handle) */
static IL AL;                             /* self.areas, in order */
static int *NODE_AREA;                    /* Node.area */
static int g_seq;

static int new_area_rec(const double *nw, const double *se, int attributes) {   /* _new_area */
    if (NA_ == ACAP) { ACAP = ACAP ? ACAP * 2 : 1024; A = realloc(A, sizeof(Area) * ACAP); }
    Area *a = &A[NA_]; memset(a, 0, sizeof *a);
    a->seq = ++g_seq; memcpy(a->nw, nw, 24); memcpy(a->se, se, 24);
    for (int k = 0; k < 4; k++) a->nodes[k] = -1;
    a->attributes = attributes;
    return NA_++;
}
static void areas_reset(void) {
    for (int i = 0; i < NA_; i++) for (int d = 0; d < 4; d++) { il_free(&A[i].connect[d]); il_free(&A[i].incoming[d]); }
    NA_ = 0; il_free(&AL); g_seq = 0;
    free(NODE_AREA); NODE_AREA = malloc(sizeof(int) * (NN + 1));
    for (int i = 0; i < NN; i++) NODE_AREA[i] = -1;
}

static double size_x(int a) { return A[a].se[0] - A[a].nw[0]; }
static double size_y(int a) { return A[a].se[1] - A[a].nw[1]; }
static int all_nodes(int a) { const int *n = A[a].nodes; return n[0] >= 0 && n[1] >= 0 && n[2] >= 0 && n[3] >= 0; }
static double fmin2(double a, double b) { return b < a ? b : a; }    /* Python min(a, b): a unless b is smaller */
static double fmax2(double a, double b) { return b > a ? b : a; }

static double z_at(int ai, double x, double y) {
    const Area *a = &A[ai];
    double dx = a->se[0] - a->nw[0], dy = a->se[1] - a->nw[1];
    if (dx <= 0 || dy <= 0) return a->ne_z;
    double u = fmin2(1.0, fmax2(0.0, (x - a->nw[0]) / dx));
    double v = fmin2(1.0, fmax2(0.0, (y - a->nw[1]) / dy));
    double north = a->nw[2] + u * (a->ne_z - a->nw[2]);
    double south = a->sw_z + u * (a->se[2] - a->sw_z);
    return north + v * (south - north);
}
static int overlaps_pt(int ai, double x, double y, double tol) {
    const Area *a = &A[ai];
    return x + tol >= a->nw[0] && x - tol <= a->se[0] && y + tol >= a->nw[1] && y - tol <= a->se[1];
}
static int overlaps_x(int s, int o) { return A[o].nw[0] < A[s].se[0] && A[o].se[0] > A[s].nw[0]; }
static int overlaps_y(int s, int o) { return A[o].nw[1] < A[s].se[1] && A[o].se[1] > A[s].nw[1]; }
static int overlaps_area(int s, int o) { return overlaps_x(s, o) && overlaps_y(s, o); }
static void extent_z(int ai, double *lo, double *hi) {
    double zs[4] = {A[ai].nw[2], A[ai].se[2], A[ai].ne_z, A[ai].sw_z};
    *lo = *hi = zs[0];
    for (int k = 1; k < 4; k++) { if (zs[k] < *lo) *lo = zs[k]; if (zs[k] > *hi) *hi = zs[k]; }
}
static int roughly_square(int a) {
    if (size_y(a) == 0) return 0;
    double aspect = size_x(a) / size_y(a);
    return 1.0 / 3.01 <= aspect && aspect <= 3.01;
}
static void corners(int ai, double c[4][3]) {
    const Area *a = &A[ai];
    c[0][0] = a->nw[0]; c[0][1] = a->nw[1]; c[0][2] = a->nw[2];
    c[1][0] = a->se[0]; c[1][1] = a->nw[1]; c[1][2] = a->ne_z;
    c[2][0] = a->se[0]; c[2][1] = a->se[1]; c[2][2] = a->se[2];
    c[3][0] = a->nw[0]; c[3][1] = a->se[1]; c[3][2] = a->sw_z;
}
static void area_normal(int ai, int alternate, double *out) {
    const Area *a = &A[ai]; double u[3], v[3];
    if (!alternate) {
        u[0] = a->se[0] - a->nw[0]; u[1] = 0.0; u[2] = a->ne_z - a->nw[2];
        v[0] = 0.0; v[1] = a->se[1] - a->nw[1]; v[2] = a->sw_z - a->nw[2];
    } else {
        u[0] = a->nw[0] - a->se[0]; u[1] = 0.0; u[2] = a->sw_z - a->se[2];
        v[0] = 0.0; v[1] = a->nw[1] - a->se[1]; v[2] = a->ne_z - a->se[2];
    }
    double n[3] = {u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]};
    double length = sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
    if (length == 0.0) length = 1.0;
    out[0] = n[0] / length; out[1] = n[1] / length; out[2] = n[2] / length;
}
static int on_disp(int ai) { for (int k = 0; k < 4; k++) { int n = A[ai].nodes[k]; if (n >= 0 && N[n].on_disp) return 1; } return 0; }
static int is_flat(int ai) {
    double a[3], b[3]; area_normal(ai, 0, a); area_normal(ai, 1, b);
    double tol = on_disp(ai) ? COPLANAR_SLOPE_LIMIT_DISP : COPLANAR_SLOPE_LIMIT;
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] > tol;
}
static int is_coplanar(int s, int o) {
    if (!on_disp(s) && !is_flat(s)) return 0;
    if (!on_disp(o) && !is_flat(o)) return 0;
    double a[3], b[3]; area_normal(s, 0, a); area_normal(o, 0, b);
    double tol = on_disp(s) ? COPLANAR_SLOPE_LIMIT_DISP : COPLANAR_SLOPE_LIMIT;
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] > tol;
}
static void refresh_corners(int ai) {
    Area *a = &A[ai];
    memcpy(a->nw, N[a->nodes[0]].pos, 24); memcpy(a->se, N[a->nodes[2]].pos, 24);
    a->ne_z = N[a->nodes[1]].pos[2]; a->sw_z = N[a->nodes[3]].pos[2];
}
static void closest_point(int ai, const double *p, double *out) {
    out[0] = fmin2(fmax2(p[0], A[ai].nw[0]), A[ai].se[0]);
    out[1] = fmin2(fmax2(p[1], A[ai].nw[1]), A[ai].se[1]);
    out[2] = z_at(ai, out[0], out[1]);
}
static double portal(int s, int o, int d, double *centre) {     /* returns the half width */
    const Area *a = &A[s], *b = &A[o];
    if (d == GEN_NORTH || d == GEN_SOUTH) {
        double cy = d == GEN_NORTH ? a->nw[1] : a->se[1];
        double left = fmin2(fmax2(fmax2(a->nw[0], b->nw[0]), a->nw[0]), a->se[0]);
        double right = fmin2(fmax2(fmin2(a->se[0], b->se[0]), a->nw[0]), a->se[0]);
        centre[0] = (left + right) / 2; centre[1] = cy; centre[2] = 0.0;
        return (right - left) / 2;
    }
    double cx = d == GEN_WEST ? a->nw[0] : a->se[0];
    double top = fmin2(fmax2(fmax2(a->nw[1], b->nw[1]), a->nw[1]), a->se[1]);
    double bottom = fmin2(fmax2(fmin2(a->se[1], b->se[1]), a->nw[1]), a->se[1]);
    centre[0] = cx; centre[1] = (top + bottom) / 2; centre[2] = 0.0;
    return (bottom - top) / 2;
}

static void connect_to(int s, int o, int d) {             /* CNavArea::ConnectTo */
    if (o == s || il_has(&A[s].connect[d], o)) return;
    il_push(&A[s].connect[d], o);
    il_remove(&A[s].incoming[d], o);
    int opp = OPP[d];
    if (!il_has(&A[o].connect[opp], s) && !il_has(&A[o].incoming[opp], s)) il_push(&A[o].incoming[opp], s);
}
static int is_connected(int s, int o, int d) { return o == s || il_has(&A[s].connect[d], o); }
static void disconnect(int s, int o) {
    for (int d = 0; d < 4; d++) {
        if (!il_has(&A[s].connect[d], o)) continue;
        il_remove(&A[s].connect[d], o);
        int opp = OPP[d];
        if (is_connected(o, s, opp)) { if (!il_has(&A[s].incoming[d], o)) il_push(&A[s].incoming[d], o); }
        else il_remove(&A[o].incoming[opp], s);
    }
}
static void forget(int s, int dead) { for (int d = 0; d < 4; d++) { il_remove(&A[s].connect[d], dead); il_remove(&A[s].incoming[d], dead); } }
static void forget_everywhere(int dead) { for (int i = 0; i < AL.n; i++) forget(AL.v[i], dead); }

/* ---------------------------------------------------------------- the area grid (_index_areas) */
static double py_floordiv(double vx, double wx) {          /* Python's float // */
    double mod = fmod(vx, wx), div = (vx - mod) / wx, fd;
    if (mod) { if ((wx < 0) != (mod < 0)) div -= 1.0; }
    if (div) { fd = floor(div); if (div - fd > 0.5) fd += 1.0; }
    else fd = copysign(0.0, vx / wx);
    return fd;
}
static IL *GL; static int G_X0, G_Y0, G_W, G_H, G_N;
static void index_areas(void) {
    for (int i = 0; i < G_N; i++) il_free(&GL[i]);
    free(GL); GL = NULL; G_N = 0;
    if (!AL.n) return;
    int x0 = 0, y0 = 0, x1 = 0, y1 = 0;
    for (int k = 0; k < AL.n; k++) {
        const Area *a = &A[AL.v[k]];
        int ax0 = (int)py_floordiv(a->nw[0], GRID), ax1 = (int)py_floordiv(a->se[0], GRID);
        int ay0 = (int)py_floordiv(a->nw[1], GRID), ay1 = (int)py_floordiv(a->se[1], GRID);
        if (!k || ax0 < x0) x0 = ax0; if (!k || ay0 < y0) y0 = ay0;
        if (!k || ax1 > x1) x1 = ax1; if (!k || ay1 > y1) y1 = ay1;
    }
    G_X0 = x0; G_Y0 = y0; G_W = x1 - x0 + 1; G_H = y1 - y0 + 1; G_N = G_W * G_H;
    GL = calloc(G_N, sizeof(IL));
    for (int k = 0; k < AL.n; k++) {
        const Area *a = &A[AL.v[k]];
        int ax0 = (int)py_floordiv(a->nw[0], GRID), ax1 = (int)py_floordiv(a->se[0], GRID);
        int ay0 = (int)py_floordiv(a->nw[1], GRID), ay1 = (int)py_floordiv(a->se[1], GRID);
        for (int gx = ax0; gx <= ax1; gx++) for (int gy = ay0; gy <= ay1; gy++)
            il_push(&GL[(gx - G_X0) * G_H + (gy - G_Y0)], AL.v[k]);
    }
}
static const IL *grid_cell(double x, double y) {
    int gx = (int)py_floordiv(x, GRID) - G_X0, gy = (int)py_floordiv(y, GRID) - G_Y0;
    if (!GL || gx < 0 || gy < 0 || gx >= G_W || gy >= G_H) return NULL;
    return &GL[gx * G_H + gy];
}
static int get_nav_area(const double *pos, double beneath_limit) {
    double tx = pos[0], ty = pos[1], tz = pos[2] + 5.0;
    int use = -1; double use_z = -99999999.9;
    const IL *cell = grid_cell(pos[0], pos[1]);
    if (!cell) return -1;
    for (int k = 0; k < cell->n; k++) {
        int a = cell->v[k];
        if (!overlaps_pt(a, tx, ty, 0.0)) continue;
        double z = z_at(a, tx, ty);
        if (z > tz || z < pos[2] - beneath_limit) continue;
        if (z > use_z) { use = a; use_z = z; }
    }
    return use;
}

/* ---------------------------------------------------------------- building areas */
static void add_dir(const double *p, int d, double amount, double *out) {     /* navareas._add_dir */
    double k = amount / GENERATION_STEP;
    out[0] = p[0] + STEPX[d] * k; out[1] = p[1] + STEPY[d] * k; out[2] = p[2];
}
static void assign(int src, int owner) {                  /* _assign / AssignNodes */
    const int *n = A[src].nodes;
    int nw = n[0], ne = n[1], sw = n[3], last = ne, v = nw, guard = 0;
    while (v >= 0 && v != sw && guard++ < WALK_GUARD) {
        int h = v;
        while (h >= 0 && h != last && guard++ < WALK_GUARD) { NODE_AREA[h] = owner; h = N[h].to[GEN_EAST]; }
        if (last < 0) break;
        last = N[last].to[GEN_SOUTH];
        v = N[v].to[GEN_SOUTH];
    }
}
static void build_area(int node, int width, int height) {
    int vert = node, ne = -1, horiz = node;
    for (int y = 0; y < height; y++) {
        horiz = vert;
        for (int x = 0; x < width; x++) horiz = N[horiz].to[GEN_EAST];
        if (y == 0) ne = horiz;
        vert = N[vert].to[GEN_SOUTH];
    }
    int sw = vert; horiz = vert;
    for (int x = 0; x < width; x++) horiz = N[horiz].to[GEN_EAST];
    int se = horiz;
    int a = new_area_rec(N[node].pos, N[se].pos, 0);
    A[a].ne_z = N[ne].pos[2]; A[a].sw_z = N[sw].pos[2];
    A[a].nodes[0] = node; A[a].nodes[1] = ne; A[a].nodes[2] = se; A[a].nodes[3] = sw;
    assign(a, a);
    int attr = N[node].attributes;
    double m = MAX_TRAVERSABLE_HEIGHT;
    if (N[node].obstacle[GEN_SOUTH] > m || N[node].obstacle[GEN_EAST] > m || N[ne].obstacle[GEN_WEST] > m || N[ne].obstacle[GEN_SOUTH] > m
        || N[se].obstacle[GEN_NORTH] > m || N[se].obstacle[GEN_WEST] > m || N[sw].obstacle[GEN_EAST] > m || N[sw].obstacle[GEN_NORTH] > m)
        attr |= NAV_MESH_NO_MERGE;
    if ((attr & NAV_MESH_CROUCH) && !N[node].crouch[C_SE]) attr &= ~NAV_MESH_CROUCH;
    A[a].attributes = attr;
    il_push(&AL, a);
}
static void stage_create(void) {
    int *out = malloc(sizeof(int) * 3 * (NN + 1));
    int n = hl_create_areas(out, NN + 1);
    for (int i = 0; i < n && i <= NN; i++) build_area(out[3 * i], out[3 * i + 1], out[3 * i + 2]);
    free(out);
    index_areas();
}

/* ---------------------------------------------------------------- ConnectGeneratedAreas */
static int find_first_area_in_direction(const double *start, int d, double rng, double beneath, double *found) {
    double pos[3] = {start[0], start[1], start[2]};
    int count = (int)(rng / GENERATION_STEP + 0.5);
    for (int i = 0; i < count; i++) {
        double nxt[3]; add_dir(pos, d, GENERATION_STEP, nxt); memcpy(pos, nxt, 24);
        if (hull(start, pos).fraction < 1.0) break;
        int area = get_nav_area(pos, beneath);
        if (area >= 0) { if (found) { found[0] = pos[0]; found[1] = pos[1]; found[2] = z_at(area, pos[0], pos[1]); } return area; }
    }
    return -1;
}
static int test_jump_down(const double *frm, const double *to) {
    double dz = frm[2] - to[2];
    if (dz <= JUMP_CROUCH_HEIGHT || dz >= DEATH_DROP) return 0;
    double up = 1.0, b[3] = {0, 0, 0};
    while (up <= CLIMB_UP_HEIGHT) {
        double e[3] = {frm[0], frm[1], frm[2] + up};
        Tr tr = hull(frm, e);
        if (!(tr.fraction <= 0.0 || tr.startsolid)) {
            double a[3] = {frm[0], frm[1], tr.ez - 0.5};
            b[0] = to[0]; b[1] = to[1]; b[2] = a[2];
            tr = hull(a, b);
            if (tr.fraction == 1.0 && !tr.startsolid) break;
        }
        up += 1.0;
    }
    if (up > CLIMB_UP_HEIGHT) return 0;
    double end[3] = {b[0], b[1], to[2] + 2.0};
    Tr tr = hull(b, end);
    if (tr.fraction <= 0.0 || tr.startsolid) return 0;
    return tr.ez <= end[2] + STEP_HEIGHT;
}
static int find_jump_down_area(const double *frm, int d) {
    double s0[3] = {frm[0], frm[1], frm[2] + HALF_HUMAN_HEIGHT}, start[3], to[3];
    add_dir(s0, d, GENERATION_STEP / 2.0, start);
    int area = find_first_area_in_direction(start, d, 4.0 * GENERATION_STEP, DEATH_DROP, to);
    if (area >= 0 && test_jump_down(frm, to)) return area;
    return -1;
}
static void link_(int area, int node, int d, int back) {
    int adj = N[node].to[d];
    if (adj >= 0 && NODE_AREA[adj] >= 0 && N[adj].to[back] == node) connect_to(area, NODE_AREA[adj], d);
    else {
        int down = find_jump_down_area(N[node].pos, d);
        if (down >= 0 && down != area) connect_to(area, down, d);
    }
}
static void edge_drop(int area, int node, int d) {
    if (NODE_AREA[node] >= 0) return;
    int adj = N[node].to[d];
    if (blocked_any(node) || (adj >= 0 && blocked_any(adj))) return;
    if (adj < 0 || NODE_AREA[adj] < 0) {
        int down = find_jump_down_area(N[node].pos, d);
        if (down >= 0 && down != area) connect_to(area, down, d);
    }
}
static void stage_connect(void) {
    for (int k = 0; k < AL.n; k++) {
        int area = AL.v[k];
        int nw = A[area].nodes[0], ne = A[area].nodes[1], se = A[area].nodes[2], sw = A[area].nodes[3], n, end, g;
        for (n = nw, g = 0; n >= 0 && n != ne && g < WALK_GUARD; g++) { link_(area, n, GEN_NORTH, GEN_SOUTH); n = N[n].to[GEN_EAST]; }
        for (n = nw, g = 0; n >= 0 && n != sw && g < WALK_GUARD; g++) { link_(area, n, GEN_WEST, GEN_EAST); n = N[n].to[GEN_SOUTH]; }
        n = N[sw].to[GEN_NORTH];
        if (n >= 0) { end = N[se].to[GEN_NORTH]; for (g = 0; n >= 0 && n != end && g < WALK_GUARD; g++) { link_(area, n, GEN_SOUTH, GEN_NORTH); n = N[n].to[GEN_EAST]; } }
        for (n = sw, g = 0; n >= 0 && n != se && g < WALK_GUARD; g++) { edge_drop(area, n, GEN_SOUTH); n = N[n].to[GEN_EAST]; }
        n = N[ne].to[GEN_WEST];
        if (n >= 0) { end = N[se].to[GEN_WEST]; for (g = 0; n >= 0 && n != end && g < WALK_GUARD; g++) { link_(area, n, GEN_EAST, GEN_WEST); n = N[n].to[GEN_SOUTH]; } }
        for (n = ne, g = 0; n >= 0 && n != se && g < WALK_GUARD; g++) { edge_drop(area, n, GEN_EAST); n = N[n].to[GEN_SOUTH]; }
    }
}

/* ---------------------------------------------------------------- MarkJumpAreas */
static void stage_mark_jump(void) {
    static const double zero[3] = {0.0, 0.0, 0.0};
    for (int k = 0; k < AL.n; k++) {
        int a = AL.v[k];
        double n1[3], n2[3]; area_normal(a, 0, n1); area_normal(a, 1, n2);
        double low = fmin2(n1[2], n2[2]);
        if (low < SLOPE_LIMIT) A[a].attributes |= NAV_MESH_JUMP | NAV_MESH_NO_MERGE;
        else if (low < SLOPE_LIMIT + SLOPE_TOLERANCE) {
            double c[3] = {(A[a].nw[0] + A[a].se[0]) / 2, (A[a].nw[1] + A[a].se[1]) / 2, (A[a].nw[2] + A[a].se[2]) / 2};
            double p[3] = {c[0], c[1], c[2] + HALF_HUMAN_HEIGHT}, e[3] = {p[0], p[1], p[2] - 9999.9};
            Tr tr = trace(p, e, zero, zero);
            if (!tr.startsolid && fabs(tr.nz - low) > SLOPE_TOLERANCE) A[a].attributes |= NAV_MESH_JUMP | NAV_MESH_NO_MERGE;
        }
    }
}

/* ---------------------------------------------------------------- MergeGeneratedAreas */
static int can_merge(int a, int b) {
    double cells = nearbyint(size_x(a) / GENERATION_STEP) * nearbyint(size_y(a) / GENERATION_STEP)
                 + nearbyint(size_x(b) / GENERATION_STEP) * nearbyint(size_y(b) / GENERATION_STEP);
    return !(A[a].attributes & NAV_MESH_NO_MERGE) && !(A[b].attributes & NAV_MESH_NO_MERGE) && cells <= MERGE_MAX_TOTAL_CELLS;
}
static int cmp_seq(const void *x, const void *y) { int a = A[*(const int *)x].seq, b = A[*(const int *)y].seq; return (a > b) - (a < b); }
static void finish_merge(int area, int adj) {
    refresh_corners(area);
    assign(adj, area);
    for (int d = 0; d < 4; d++) {
        IL snap = il_copy(&A[adj].connect[d]);
        for (int i = 0; i < snap.n; i++) if (snap.v[i] != adj && snap.v[i] != area) connect_to(area, snap.v[i], d);
        il_free(&snap);
    }
    disconnect(area, adj);
    IL near = {0};
    for (int d = 0; d < 4; d++) {
        for (int i = 0; i < A[adj].connect[d].n; i++) if (!il_has(&near, A[adj].connect[d].v[i])) il_push(&near, A[adj].connect[d].v[i]);
        for (int i = 0; i < A[adj].incoming[d].n; i++) if (!il_has(&near, A[adj].incoming[d].v[i])) il_push(&near, A[adj].incoming[d].v[i]);
    }
    qsort(near.v, near.n, sizeof(int), cmp_seq);
    for (int i = 0; i < near.n; i++) {
        int other = near.v[i];
        if (other == area || other == adj) continue;
        for (int d = 0; d < 4; d++)
            if (il_has(&A[other].connect[d], adj)) { disconnect(other, adj); disconnect(other, area); connect_to(other, area, d); }
    }
    il_free(&near);
    il_remove(&AL, adj);
    forget_everywhere(adj);
}
static int merge_test(int d, int a, int b) {
    const int *x = A[a].nodes, *y = A[b].nodes;
    switch (d) {
    case GEN_NORTH: return x[0] == y[3] && x[1] == y[2];
    case GEN_SOUTH: return y[0] == x[3] && y[1] == x[2];
    case GEN_WEST: return x[0] == y[1] && x[3] == y[2];
    default: return y[0] == x[1] && y[3] == x[2];
    }
}
static const int MT_DIR[4] = {GEN_NORTH, GEN_SOUTH, GEN_WEST, GEN_EAST}, MT_AXIS[4] = {1, 1, 0, 0};
static const int MT_MINE[4][2] = {{0, 1}, {3, 2}, {0, 3}, {1, 2}};
static void stage_merge(void) {
    double limit = GENERATION_STEP * AREA_MAX_SIZE;
    unsigned char *clean = calloc(NA_ + 1, 1);       /* no areas are made while merging */
    for (;;) {
        int merged = 0;
        for (int k = 0; k < AL.n && !merged; k++) {
            int area = AL.v[k];
            if (clean[area]) continue;
            if (A[area].attributes & NAV_MESH_NO_MERGE) { clean[area] = 1; continue; }
            for (int t = 0; t < 4 && !merged; t++) {
                int d = MT_DIR[t];
                IL snap = il_copy(&A[area].connect[d]);
                for (int i = 0; i < snap.n; i++) {
                    int adj = snap.v[i];
                    if (!can_merge(area, adj)) continue;
                    double size = MT_AXIS[t] == 1 ? size_y(area) + size_y(adj) : size_x(area) + size_x(adj);
                    if (size > limit) continue;
                    if (merge_test(d, area, adj) && A[area].attributes == A[adj].attributes && is_coplanar(area, adj)) {
                        IL touched = {0};
                        for (int dd = 0; dd < 4; dd++) {
                            const IL *ls[4] = {&A[area].connect[dd], &A[area].incoming[dd], &A[adj].connect[dd], &A[adj].incoming[dd]};
                            for (int q = 0; q < 4; q++) for (int j = 0; j < ls[q]->n; j++) il_push(&touched, ls[q]->v[j]);
                        }
                        A[area].nodes[MT_MINE[t][0]] = A[adj].nodes[MT_MINE[t][0]];
                        A[area].nodes[MT_MINE[t][1]] = A[adj].nodes[MT_MINE[t][1]];
                        finish_merge(area, adj);
                        for (int j = 0; j < touched.n; j++) clean[touched.v[j]] = 0;
                        clean[area] = 0;
                        for (int dd = 0; dd < 4; dd++) {
                            for (int j = 0; j < A[area].connect[dd].n; j++) clean[A[area].connect[dd].v[j]] = 0;
                            for (int j = 0; j < A[area].incoming[dd].n; j++) clean[A[area].incoming[dd].v[j]] = 0;
                        }
                        il_free(&touched);
                        merged = 1;
                        break;
                    }
                }
                il_free(&snap);
            }
            if (!merged) clean[area] = 1;
        }
        if (!merged) break;
    }
    free(clean);
    index_areas();
}

/* ---------------------------------------------------------------- SplitEdit */
static void finish_split(int old, int nu, int ignore) {
    A[nu].attributes = A[old].attributes;
    A[nu].ne_z = z_at(old, A[nu].se[0], A[nu].nw[1]);
    A[nu].sw_z = z_at(old, A[nu].nw[0], A[nu].se[1]);
    for (int d = 0; d < 4; d++) {
        if (d == ignore) continue;
        IL snap = il_copy(&A[old].connect[d]);
        for (int i = 0; i < snap.n; i++) {
            int adj = snap.v[i];
            int ov = (d == GEN_NORTH || d == GEN_SOUTH) ? overlaps_x(nu, adj) : overlaps_y(nu, adj);
            if (ov) { connect_to(nu, adj, d); if (is_connected(adj, old, OPP[d])) connect_to(adj, nu, OPP[d]); }
            /* (sic) the game re-links incoming connections inside this loop */
            IL inc = il_copy(&A[old].incoming[d]);
            for (int j = 0; j < inc.n; j++) {
                int c = inc.v[j];
                if ((d == GEN_NORTH || d == GEN_SOUTH) ? overlaps_x(nu, c) : overlaps_y(nu, c)) connect_to(c, nu, OPP[d]);
            }
            il_free(&inc);
        }
        il_free(&snap);
    }
    il_push(&AL, nu);
    if (all_nodes(old)) {
        memcpy(A[nu].nodes, A[old].nodes, sizeof(int) * 4);
        int d, c0, c1;
        switch (ignore) {
        case GEN_NORTH: d = GEN_SOUTH; c0 = 0; c1 = 1; break;
        case GEN_SOUTH: d = GEN_NORTH; c0 = 3; c1 = 2; break;
        case GEN_EAST: d = GEN_WEST; c0 = 1; c1 = 2; break;
        default: d = GEN_EAST; c0 = 0; c1 = 3; break;
        }
        int guard = 0;
        while (!overlaps_pt(nu, N[A[nu].nodes[c0]].pos[0], N[A[nu].nodes[c0]].pos[1], GENERATION_STEP / 2)) {
            A[nu].nodes[c0] = N[A[nu].nodes[c0]].to[d];
            A[nu].nodes[c1] = N[A[nu].nodes[c1]].to[d];
            guard++;
            if (guard > 10000 || A[nu].nodes[c0] < 0 || A[nu].nodes[c1] < 0) { for (int k = 0; k < 4; k++) A[nu].nodes[k] = -1; break; }
        }
        if (all_nodes(nu)) {
            assign(nu, nu);
            A[nu].ne_z = N[A[nu].nodes[1]].pos[2]; A[nu].sw_z = N[A[nu].nodes[3]].pos[2];
            A[nu].nw[2] = N[A[nu].nodes[0]].pos[2];
            A[nu].se[2] = N[A[nu].nodes[2]].pos[2];
        }
    }
}
/* returns 1 and the two new areas, or 0 */
static int split_edit(int area, int along_x, double edge, int *alpha_out, int *beta_out) {
    int alpha, beta;
    if (along_x) {
        if (edge <= A[area].nw[1] + 1.0 || edge >= A[area].se[1] - 1.0) return 0;
        double ase[3] = {A[area].se[0], edge, z_at(area, A[area].se[0], edge)};
        double nw[3]; memcpy(nw, A[area].nw, 24);
        alpha = new_area_rec(nw, ase, 0);
        double bnw[3] = {A[area].nw[0], edge, z_at(area, A[area].nw[0], edge)};
        double se[3]; memcpy(se, A[area].se, 24);
        beta = new_area_rec(bnw, se, 0);
        connect_to(alpha, beta, GEN_SOUTH);
        connect_to(beta, alpha, GEN_NORTH);
        finish_split(area, alpha, GEN_SOUTH);
        finish_split(area, beta, GEN_NORTH);
    } else {
        if (edge <= A[area].nw[0] + 1.0 || edge >= A[area].se[0] - 1.0) return 0;
        double ase[3] = {edge, A[area].se[1], z_at(area, edge, A[area].se[1])};
        double nw[3]; memcpy(nw, A[area].nw, 24);
        alpha = new_area_rec(nw, ase, 0);
        double bnw[3] = {edge, A[area].nw[1], z_at(area, edge, A[area].nw[1])};
        double se[3]; memcpy(se, A[area].se, 24);
        beta = new_area_rec(bnw, se, 0);
        connect_to(alpha, beta, GEN_EAST);
        connect_to(beta, alpha, GEN_WEST);
        finish_split(area, alpha, GEN_EAST);
        finish_split(area, beta, GEN_WEST);
    }
    il_remove(&AL, area);
    forget_everywhere(area);
    *alpha_out = alpha; *beta_out = beta;
    return 1;
}

/* ---------------------------------------------------------------- SplitAreasUnderOverhangs */
static void stage_overhangs(void) {
    int restart = 1;
    while (restart) {
        restart = 0;
        IL snap = il_copy(&AL);
        for (int k = 0; k < snap.n && !restart; k++) {
            int area = snap.v[k];
            double lo_a, hi_a; extent_z(area, &lo_a, &hi_a);
            for (int d = 0; d < 4 && !restart; d++) {
                IL others = il_copy(&A[area].connect[d]);
                for (int i = 0; i < others.n; i++) {
                    int other = others.v[i];
                    if (!overlaps_area(area, other)) continue;
                    double lo_o, hi_o; extent_z(other, &lo_o, &hi_o);
                    if (!(lo_a > hi_o + HUMAN_CROUCH_HEIGHT) && !(lo_o > hi_a + HUMAN_CROUCH_HEIGHT)) continue;
                    int below = area, above = other, a2b = OPP[d];
                    if (lo_o < lo_a) { below = other; above = area; a2b = OPP[a2b]; }
                    int b2a = OPP[a2b], along_x;
                    double edge_size, coord, length;
                    if (a2b == GEN_EAST || a2b == GEN_WEST) {
                        along_x = 0;
                        edge_size = A[below].se[0] - A[below].nw[0];
                        if (A[above].se[0] < A[below].se[0]) { coord = A[above].se[0]; length = A[above].se[0] - A[below].nw[0]; }
                        else { coord = A[above].nw[0]; length = A[below].se[0] - A[above].nw[0]; }
                    } else {
                        along_x = 1;
                        edge_size = A[below].se[1] - A[below].nw[1];
                        if (A[above].se[1] < A[below].se[1]) { coord = A[above].se[1]; length = A[above].se[1] - A[below].nw[1]; }
                        else { coord = A[above].nw[1]; length = A[below].se[1] - A[above].nw[1]; }
                    }
                    if (length < GENERATION_STEP) {
                        if (length < GENERATION_STEP * 0.3 || edge_size <= GENERATION_STEP * 2) continue;
                        double off = GENERATION_STEP - length;
                        coord += (a2b == GEN_NORTH || a2b == GEN_WEST) ? -off : off;
                    }
                    int from_below = is_connected(below, above, b2a) && above != below;
                    if (from_below) disconnect(below, above);
                    int from_above = is_connected(above, below, a2b);
                    if (from_above) disconnect(above, below);
                    int ra, rb;
                    if (split_edit(below, along_x, coord, &ra, &rb)) {
                        int keep = (a2b == GEN_NORTH || a2b == GEN_WEST) ? ra : rb;
                        if (from_above) connect_to(above, keep, a2b);
                        restart = 1;
                        break;
                    }
                }
                il_free(&others);
            }
        }
        il_free(&snap);
    }
    index_areas();
}

/* ---------------------------------------------------------------- SquareUpAreas */
static void split_dir(int area, int x_axis) {
    if (roughly_square(area)) return;
    double split = x_axis ? round_to_units(size_x(area) / 2.0 + A[area].nw[0], GENERATION_STEP)
                          : round_to_units(size_y(area) / 2.0 + A[area].nw[1], GENERATION_STEP);
    double lo = x_axis ? A[area].nw[0] : A[area].nw[1], hi = x_axis ? A[area].se[0] : A[area].se[1];
    if (fabs(split - lo) < 0.1 || fabs(split - hi) < 0.1) return;
    int ra, rb;
    if (split_edit(area, !x_axis, split, &ra, &rb)) { split_dir(ra, x_axis); split_dir(rb, x_axis); }
}
static void stage_square_up(void) {
    int i = 0;
    while (i < AL.n) {
        int area = AL.v[i];
        if (all_nodes(area) && !roughly_square(area)) {
            split_dir(area, size_x(area) > size_y(area));
            if (i < AL.n && AL.v[i] != area) continue;
        }
        i++;
    }
    index_areas();
}

/* ---------------------------------------------------------------- MarkStairAreas */
#define STAIRS_NO 0
#define STAIRS_MAYBE 1
#define STAIRS_YES 2
static double py_hypot(double x, double y) { return (double)sqrtl((long double)x * x + (long double)y * y); }
static int is_stairs(const double *start, const double *end, int ret) {
    if (ret == STAIRS_NO) return ret;
    const double inc = 5.0, min_step = 0x1.46771ddfbc2b8p+2;     /* 5 * tan(acos(SLOPE_LIMIT)) */
    double length = py_hypot(end[0] - start[0], end[1] - start[1]);
    double mins[3], maxs[3];
    if (fabs(start[0] - end[0]) > fabs(start[1] - end[1])) { mins[0] = -8.0; mins[1] = -inc / 2; maxs[0] = 8.0; maxs[1] = inc / 2; }
    else { mins[0] = -inc / 2; mins[1] = -8.0; maxs[0] = inc / 2; maxs[1] = 8.0; }
    mins[2] = 0.0; maxs[2] = 1.0;
    double off = DUCK_HULL_TOP;
    if (fabs(start[2] - end[2]) > STEP_HEIGHT) {
        double s[3] = {start[0], start[1], start[2] + off}, e[3] = {start[0], start[1], start[2] - off};
        Tr tr = trace(s, e, mins, maxs);
        if (tr.startsolid || (tr.flags & 2)) return STAIRS_NO;
        double prior = tr.ez;
        double step = length ? inc / length : 1.0;
        double t = 0.0;
        while (t <= 1.0) {
            double p[3];
            for (int k = 0; k < 3; k++) p[k] = start[k] + t * (end[k] - start[k]);
            double ps[3] = {p[0], p[1], p[2] + off}, pe[3] = {p[0], p[1], p[2] - off};
            tr = trace(ps, pe, mins, maxs);
            if (tr.startsolid || (tr.flags & 2)) return STAIRS_NO;
            double h = tr.ez;
            if (t == 0.0 && fabs(h - start[2]) > STEP_HEIGHT) return STAIRS_NO;
            if (t == 1.0 && fabs(h - end[2]) > STEP_HEIGHT) return STAIRS_NO;
            if (tr.nz < 0.97) return STAIRS_NO;
            double dz = fabs(h - prior);
            if (min_step <= dz && dz <= STEP_HEIGHT) ret = STAIRS_YES;
            else if (dz > STEP_HEIGHT) return STAIRS_NO;
            prior = h;
            t += step;
        }
    }
    return ret;
}
static void stage_stairs(void) {
    for (int k = 0; k < AL.n; k++) {
        int a = AL.v[k];
        A[a].attributes &= ~NAV_MESH_STAIRS;
        if (size_x(a) <= GENERATION_STEP && size_y(a) <= GENERATION_STEP) continue;
        double n1[3], n2[3]; area_normal(a, 0, n1); area_normal(a, 1, n2);
        if (n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2] < 0.95) continue;
        double c[4][3]; corners(a, c);
        double *nw = c[0], *ne = c[1], *se = c[2], *sw = c[3], ins = 5.0;
        double seg[6][2][3] = {
            {{nw[0] + ins, nw[1] + ins, nw[2]}, {ne[0] - ins, ne[1] + ins, ne[2]}},
            {{sw[0] + ins, sw[1] - ins, sw[2]}, {se[0] - ins, se[1] - ins, se[2]}},
            {{nw[0] + ins, nw[1] + ins, nw[2]}, {sw[0] + ins, sw[1] - ins, sw[2]}},
            {{ne[0] - ins, ne[1] + ins, ne[2]}, {se[0] - ins, se[1] - ins, se[2]}},
            {{(nw[0] + ne[0]) / 2, (nw[1] + ne[1]) / 2, (nw[2] + ne[2]) / 2}, {(sw[0] + se[0]) / 2, (sw[1] + se[1]) / 2, (sw[2] + se[2]) / 2}},
            {{(ne[0] + se[0]) / 2, (ne[1] + se[1]) / 2, (ne[2] + se[2]) / 2}, {(nw[0] + sw[0]) / 2, (nw[1] + sw[1]) / 2, (nw[2] + sw[2]) / 2}},
        };
        int ret = STAIRS_MAYBE;
        for (int s = 0; s < 6; s++) ret = is_stairs(seg[s][0], seg[s][1], ret);
        if (ret == STAIRS_YES) A[a].attributes = NAV_MESH_STAIRS;      /* (sic) replaces all flags */
    }
}

/* ---------------------------------------------------------------- StitchAndRemoveJumpAreas */
static void try_connect(int src, const IL *dest_live, int out_dir) {
    IL dest = il_copy(dest_live);
    for (int i = 0; i < dest.n; i++) {
        int dst = dest.v[i];
        if (A[dst].attributes & NAV_MESH_JUMP) continue;
        double centre[3]; double half = portal(src, dst, out_dir, centre);
        if (half <= 0.0) continue;
        double sp[3], dp[3]; closest_point(src, centre, sp); closest_point(dst, centre, dp);
        if ((A[src].attributes & NAV_MESH_STAIRS) && sp[2] + STEP_HEIGHT < dp[2]) continue;
        if (py_hypot(sp[0] - dp[0], sp[1] - dp[1]) < GENERATION_STEP * 3) connect_to(src, dst, out_dir);
    }
    il_free(&dest);
}
static void try_connect_many(int jump, const IL *sources_live, const IL *dest, int out_dir, int depth) {
    if (depth > 900) return;                 /* Python would stop at its recursion limit */
    IL sources = il_copy(sources_live);
    for (int i = 0; i < sources.n; i++) {
        int src = sources.v[i];
        if (!is_connected(src, jump, out_dir)) continue;
        if (A[src].attributes & NAV_MESH_JUMP) {
            int inc = OPP[out_dir];
            try_connect_many(jump, &A[src].incoming[inc], dest, out_dir, depth + 1);
            try_connect_many(jump, &A[src].connect[inc], dest, out_dir, depth + 1);
            continue;
        }
        try_connect(src, dest, out_dir);
    }
    il_free(&sources);
}
static void stage_stitch(void) {
    IL snap = il_copy(&AL);
    for (int k = 0; k < snap.n; k++) {
        int jump = snap.v[k];
        if (!(A[jump].attributes & NAV_MESH_JUMP)) continue;
        for (int inc = 0; inc < 4; inc++) {
            int out = OPP[inc];
            try_connect_many(jump, &A[jump].incoming[inc], &A[jump].connect[out], out, 0);
            try_connect_many(jump, &A[jump].connect[inc], &A[jump].connect[out], out, 0);
        }
    }
    il_free(&snap);
    IL dead = {0};
    for (int k = 0; k < AL.n; k++) if (A[AL.v[k]].attributes & NAV_MESH_JUMP) il_push(&dead, AL.v[k]);
    for (int i = 0; i < dead.n; i++) { il_remove(&AL, dead.v[i]); forget_everywhere(dead.v[i]); }
    il_free(&dead);
    index_areas();
}

/* ---------------------------------------------------------------- FixCornerOnCornerAreas */
static void stage_fix_corners(void) {
    double max_drop = STEP_HEIGHT, half = GENERATION_STEP * 0.5;
    static const double VEC[4][2] = {{0.0, -1.0}, {1.0, 0.0}, {0.0, 1.0}, {-1.0, 0.0}};
    int i = 0;
    while (i < AL.n) {                       /* (areas added here get their turn too) */
        int area = AL.v[i];
        i++;
        for (int corner = 0; corner < 4; corner++) {
            int right = corner, left = (corner + 3) % 4;
            if (A[area].connect[left].n || A[area].connect[right].n || A[area].incoming[left].n || A[area].incoming[right].n) continue;
            double cs[4][3]; corners(area, cs);
            double cp[3] = {cs[corner][0], cs[corner][1], cs[corner][2]};
            int pairs[2][2] = {{left, (left + 3) % 4}, {right, (right + 1) % 4}};
            for (int pi = 0; pi < 2; pi++) {
                int along_other = pairs[pi][0], along_ours = pairs[pi][1];
                double vo[2] = {VEC[along_other][0] * half, VEC[along_other][1] * half};
                double other_pos[3] = {cp[0] + vo[0], cp[1] + vo[1], cp[2]};
                int other = get_nav_area(other_pos, 120.0);
                if (other < 0) continue;
                Tr tr;
                if (!trace_adjacent(0, cp, other_pos, &tr, max_drop)) continue;
                double oc[4][3]; corners(other, oc);
                int k2 = (corner + 2) % 4;
                if (!(oc[k2][0] == cp[0] && oc[k2][1] == cp[1] && oc[k2][2] == cp[2])) continue;
                double vu[2] = {VEC[along_ours][0] * half, VEC[along_ours][1] * half};
                double c[4][3] = {{cp[0] + vo[0] + vu[0], cp[1] + vo[1] + vu[1], cp[2]},
                                  {other_pos[0], other_pos[1], other_pos[2]}, {cp[0], cp[1], cp[2]},
                                  {cp[0] + vu[0], cp[1] + vu[1], cp[2]}};
                Tr t1, t2;
                int ok1 = trace_adjacent(0, c[1], c[0], &t1, max_drop);
                int ok2 = ok1 ? trace_adjacent(0, c[3], c[0], &t2, max_drop) : 0;
                if (!(ok1 && ok2)) continue;
                if (get_nav_area(c[0], 120.0) >= 0) continue;
                c[0][0] = t2.ex; c[0][1] = t2.ey; c[0][2] = t2.ez;
                double *nw = c[0], *ne = c[0], *se = c[0], *sw = c[0];      /* ClassifyCorners */
                for (int q = 0; q < 4; q++) {
                    double *p = c[q];
                    if (p[0] <= nw[0] && p[1] <= nw[1]) nw = p;
                    if (p[0] >= ne[0] && p[1] <= ne[1]) ne = p;
                    if (p[0] >= se[0] && p[1] >= se[1]) se = p;
                    if (p[0] <= sw[0] && p[1] >= sw[1]) sw = p;
                }
                int nu = new_area_rec(nw, se, A[area].attributes);
                A[nu].ne_z = ne[2]; A[nu].sw_z = sw[2];
                il_push(&AL, nu);
                index_areas();
                connect_to(area, nu, along_other);
                connect_to(nu, area, OPP[along_other]);
                connect_to(other, nu, along_ours);
                connect_to(nu, other, OPP[along_ours]);
            }
        }
    }
}

/* ---------------------------------------------------------------- FixConnections */
static int nodes_along(int area, int d, IL *out) {
    int start, end, step;
    switch (d) {
    case GEN_NORTH: start = 0; end = 1; step = GEN_EAST; break;
    case GEN_SOUTH: start = 3; end = 2; step = GEN_EAST; break;
    case GEN_EAST: start = 1; end = 2; step = GEN_SOUTH; break;
    default: start = 0; end = 3; step = GEN_SOUTH; break;
    }
    int n = A[area].nodes[start], e = A[area].nodes[end], guard = 0;
    while (n >= 0 && n != e && guard < 100000) { il_push(out, n); n = N[n].to[step]; guard++; }
    if (n >= 0 && n == e) il_push(out, n);
    return out->n;
}
static int closest_node(int area, const double *pos, int d) {
    if (!all_nodes(area)) return -1;
    IL along = {0}; nodes_along(area, d, &along);
    int best = -1; double best_d = INFINITY;
    for (int i = 0; i < along.n; i++) {
        const double *q = N[along.v[i]].pos;
        double a = pos[0] - q[0], b = pos[1] - q[1], c = pos[2] - q[2];
        double dd = a * a + b * b + c * c;
        if (dd < best_d) { best = along.v[i]; best_d = dd; }
    }
    il_free(&along);
    return best;
}
static void stage_fix_connections(void) {
    static const int EDGE[4][2] = {{0, 1}, {1, 2}, {3, 2}, {0, 3}};    /* N, E, S, W */
    for (int k = 0; k < AL.n; k++) {
        int area = AL.v[k];
        if (!(A[area].attributes & NAV_MESH_STAIRS) || !all_nodes(area)) continue;
        double cs[4][3]; corners(area, cs);
        for (int d = 0; d < 4; d++) {
            int c0 = EDGE[d][0], c1 = EDGE[d][1];
            if (fabs(cs[c0][2] - cs[c1][2]) < STEP_HEIGHT) continue;
            IL drop = {0}, snap = il_copy(&A[area].connect[d]);
            for (int i = 0; i < snap.n; i++) {
                int adj = snap.v[i];
                if (!all_nodes(adj)) continue;
                double centre[3], adj_pos[3];
                portal(area, adj, d, centre);
                closest_point(adj, centre, adj_pos);
                int node = closest_node(area, centre, d), adj_node = closest_node(adj, adj_pos, OPP[d]);
                if (node < 0 || adj_node < 0) continue;
                int a0 = EDGE[OPP[d]][0], a1 = EDGE[OPP[d]][1];
                const double *pos = N[node].pos, *apos = N[adj_node].pos;
                if (N[node].ground[c0] > STEP_HEIGHT || N[node].ground[c1] > STEP_HEIGHT
                    || apos[2] + N[adj_node].ground[a0] > pos[2] + STEP_HEIGHT
                    || apos[2] + N[adj_node].ground[a1] > pos[2] + STEP_HEIGHT)
                    il_push(&drop, adj);
            }
            for (int i = 0; i < drop.n; i++) disconnect(area, drop.v[i]);
            il_free(&drop); il_free(&snap);
        }
    }
    for (int k = 0; k < AL.n; k++) {
        int area = AL.v[k];
        IL drop = {0};
        for (int d = 0; d < 4; d++) {
            IL adjs = il_copy(&A[area].connect[d]);
            for (int i = 0; i < adjs.n; i++) {
                IL fars = il_copy(&A[adjs.v[i]].connect[d]);
                for (int j = 0; j < fars.n; j++) if (is_connected(area, fars.v[j], d)) il_push(&drop, fars.v[j]);
                il_free(&fars);
            }
            il_free(&adjs);
        }
        for (int i = 0; i < drop.n; i++) disconnect(area, drop.v[i]);
        il_free(&drop);
    }
}

/* ---------------------------------------------------------------- running and reading back */
/* Runs the stages after sampling, up to and including 'upto' (1 build, 2 connect, 3 mark jump,
 * 4 merge, 5 overhangs, 6 square up, 7 stairs, 8 remove jump areas, 9 corners, 10 connections).
 * Returns the number of areas. */
EXPORT int hl_areas_run(int upto) {
    areas_reset();
    void (*stages[10])(void) = {stage_create, stage_connect, stage_mark_jump, stage_merge, stage_overhangs,
                                stage_square_up, stage_stairs, stage_stitch, stage_fix_corners, stage_fix_connections};
    for (int s = 0; s < upto && s < 10; s++) stages[s]();
    return AL.n;
}
EXPORT int hl_areas_seq(void) { return g_seq; }
/* per area, in list order: d8 = nw3 se3 ne_z sw_z; i6 = seq attributes nodes4; cn8 = how many
 * connect[0..3] then incoming[0..3]; then all those links as list positions (-1: an area no
 * longer listed) in that order into links. Returns the number of links written (or needed). */
EXPORT int hl_areas_get(double *d8, int *i6, int *cn8, int *links, int cap) {
    int *pos = malloc(sizeof(int) * (NA_ + 1));
    for (int i = 0; i < NA_; i++) pos[i] = -1;
    for (int k = 0; k < AL.n; k++) pos[AL.v[k]] = k;
    int nl = 0;
    for (int k = 0; k < AL.n; k++) {
        const Area *a = &A[AL.v[k]];
        double *d = d8 + 8 * k; int *ii = i6 + 6 * k, *c = cn8 + 8 * k;
        memcpy(d, a->nw, 24); memcpy(d + 3, a->se, 24); d[6] = a->ne_z; d[7] = a->sw_z;
        ii[0] = a->seq; ii[1] = a->attributes; for (int q = 0; q < 4; q++) ii[2 + q] = a->nodes[q];
        for (int d2 = 0; d2 < 8; d2++) {
            const IL *l = d2 < 4 ? &a->connect[d2] : &a->incoming[d2 - 4];
            c[d2] = l->n;
            for (int j = 0; j < l->n; j++) { if (nl < cap) links[nl] = pos[l->v[j]]; nl++; }
        }
    }
    free(pos);
    return nl;
}
