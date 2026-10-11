/* Brush building for the nav collision: core/collision.py _windings + _make_brush (vbsp's AddBrushBevels) in C.
 * The same double arithmetic in the same order as the Python (tests compare every brush of real maps bit for
 * bit), so either one gives the same nav mesh. Included from hlnav.c. */

typedef struct { double x, y, z; } V3;

static double b_dot(V3 a, V3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static V3 b_cross(V3 a, V3 b) { V3 r = {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; return r; }
static V3 b_sub(V3 a, V3 b) { V3 r = {a.x - b.x, a.y - b.y, a.z - b.z}; return r; }
static double b_comp(V3 v, int a) { return a == 0 ? v.x : a == 1 ? v.y : v.z; }
static V3 b_norm(V3 v, double *len) {
    double length = sqrt(b_dot(v, v));
    *len = length;
    if (length == 0.0) { V3 z = {0.0, 0.0, 0.0}; return z; }
    V3 r = {v.x / length, v.y / length, v.z / length};
    return r;
}
/* float(round(x)) in Python: half to even (the default mode), and through an int, so never -0.0 */
static double py_round(double x) { return nearbyint(x) + 0.0; }

typedef struct { V3 n; double d; int src; int bevel; } BSide;   /* src: the input side its material comes from */

/* hl_make_brush: n input planes (nx ny nz d, 4 doubles each) and their bevel flags. Writes up to cap sides
 * (nx ny nz d), each one's material source (input index) and bevel flag, and the box (mins, maxs). Returns the
 * side count, 0 when the planes make no brush (Python None), -1 when cap is too small, -2 when out of memory. */
EXPORT int hl_make_brush(int n, const double *planes, const int *bevel_in, double *out_planes, int *out_src,
                         int *out_bevel, int cap, double *box) {
    int result = -2;
    V3 *nrm = malloc(sizeof(V3) * (n + 1));
    double *dist = malloc(sizeof(double) * (n + 1));
    int *cnt = calloc(n + 1, sizeof(int)), *capw = calloc(n + 1, sizeof(int));
    V3 **pts = calloc(n + 1, sizeof(V3 *));
    BSide *sides = NULL;
    V3 **wind = NULL; int *wn = NULL;
    double *keys = NULL;
    if (!nrm || !dist || !cnt || !capw || !pts) goto done;
    for (int i = 0; i < n; i++) {
        nrm[i].x = planes[4 * i]; nrm[i].y = planes[4 * i + 1]; nrm[i].z = planes[4 * i + 2]; dist[i] = planes[4 * i + 3];
    }
    /* _windings */
    for (int i = 0; i < n; i++)
        for (int j = i + 1; j < n; j++)
            for (int k = j + 1; k < n; k++) {
                V3 n1 = nrm[i], n2 = nrm[j], n3 = nrm[k];
                double d1 = dist[i], d2 = dist[j], d3 = dist[k];
                double det = b_dot(n1, b_cross(n2, n3));
                if (fabs(det) < 1e-9) continue;
                V3 c23 = b_cross(n2, n3), c31 = b_cross(n3, n1), c12 = b_cross(n1, n2);
                V3 p = {(d1 * c23.x + d2 * c31.x + d3 * c12.x) / det, (d1 * c23.y + d2 * c31.y + d3 * c12.y) / det,
                        (d1 * c23.z + d2 * c31.z + d3 * c12.z) / det};
                int inside = 1;
                for (int m = 0; m < n; m++) if (!(b_dot(nrm[m], p) - dist[m] <= 0.01)) { inside = 0; break; }
                if (!inside) continue;
                for (int m = 0; m < n; m++) {
                    if (!(fabs(b_dot(nrm[m], p) - dist[m]) <= 0.01)) continue;
                    int dup = 0;
                    for (int q = 0; q < cnt[m]; q++) {
                        V3 o = pts[m][q];
                        if (fabs(o.x - p.x) < 1e-3 && fabs(o.y - p.y) < 1e-3 && fabs(o.z - p.z) < 1e-3) { dup = 1; break; }
                    }
                    if (dup) continue;
                    if (cnt[m] == capw[m]) {
                        int nc = capw[m] ? 2 * capw[m] : 8;
                        V3 *np = realloc(pts[m], sizeof(V3) * nc);
                        if (!np) goto done;
                        pts[m] = np; capw[m] = nc;
                    }
                    pts[m][cnt[m]++] = p;
                }
            }
    int maxpts = 0;
    for (int m = 0; m < n; m++) if (cnt[m] > maxpts) maxpts = cnt[m];
    keys = malloc(sizeof(double) * (maxpts + 1));
    if (!keys) goto done;
    int nverts = 0;
    for (int m = 0; m < n; m++) {
        if (cnt[m] < 3) { cnt[m] = 0; continue; }       /* (Python: an empty winding) */
        V3 c = {0.0, 0.0, 0.0};
        double sx = 0.0, sy = 0.0, sz = 0.0;
        for (int q = 0; q < cnt[m]; q++) { sx += pts[m][q].x; sy += pts[m][q].y; sz += pts[m][q].z; }
        c.x = sx / cnt[m]; c.y = sy / cnt[m]; c.z = sz / cnt[m];
        double ul;
        V3 u = b_norm(b_sub(pts[m][0], c), &ul);
        V3 v = b_cross(nrm[m], u);
        for (int q = 0; q < cnt[m]; q++) {
            V3 r = b_sub(pts[m][q], c);
            keys[q] = atan2(b_dot(r, v), b_dot(r, u));
        }
        for (int q = 1; q < cnt[m]; q++) {               /* stable insertion sort (Python's sort is stable) */
            double kk = keys[q]; V3 pp = pts[m][q];
            int r = q - 1;
            while (r >= 0 && keys[r] > kk) { keys[r + 1] = keys[r]; pts[m][r + 1] = pts[m][r]; r--; }
            keys[r + 1] = kk; pts[m][r + 1] = pp;
        }
        nverts += cnt[m];
    }
    if (nverts < 4) { result = 0; goto done; }
    double mins[3], maxs[3];
    int first = 1;
    for (int m = 0; m < n; m++)
        for (int q = 0; q < cnt[m]; q++)
            for (int a = 0; a < 3; a++) {
                double x = b_comp(pts[m][q], a);
                if (first) { mins[a] = maxs[a] = x; }
                else { if (x < mins[a]) mins[a] = x; if (x > maxs[a]) maxs[a] = x; }
                if (a == 2) first = 0;
            }
    /* sides and their windings; room for the axial and edge bevels */
    int scap = n + 6 + 64, ns = n;
    sides = malloc(sizeof(BSide) * scap);
    wind = malloc(sizeof(V3 *) * scap);
    wn = malloc(sizeof(int) * scap);
    if (!sides || !wind || !wn) goto done;
    for (int i = 0; i < n; i++) {
        sides[i].n = nrm[i]; sides[i].d = dist[i]; sides[i].src = i; sides[i].bevel = bevel_in[i];
        wind[i] = pts[i]; wn[i] = cnt[i];
    }
#define GROW() do { if (ns == scap) { int nc = 2 * scap; BSide *s2 = realloc(sides, sizeof(BSide) * nc); \
        V3 **w2 = realloc(wind, sizeof(V3 *) * nc); int *n2 = realloc(wn, sizeof(int) * nc); \
        if (s2) sides = s2; if (w2) wind = w2; if (n2) wn = n2; if (!s2 || !w2 || !n2) goto done; scap = nc; } } while (0)
    /* axial planes, in canonical order (-x, +x, -y, +y, -z, +z) */
    int order = 0;
    for (int axis = 0; axis < 3; axis++)
        for (int sgn = -1; sgn <= 1; sgn += 2) {
            int idx = -1;
            for (int i = 0; i < ns; i++) if (b_comp(sides[i].n, axis) == (double)sgn) { idx = i; break; }
            if (idx < 0) {
                GROW();
                V3 an = {0.0, 0.0, 0.0};
                if (axis == 0) an.x = sgn; else if (axis == 1) an.y = sgn; else an.z = sgn;
                sides[ns].n = an; sides[ns].d = sgn == 1 ? maxs[axis] : -mins[axis];
                sides[ns].src = sides[0].src; sides[ns].bevel = 1;
                wind[ns] = NULL; wn[ns] = 0;
                idx = ns++;
            }
            if (idx != order) {
                BSide ts = sides[order]; sides[order] = sides[idx]; sides[idx] = ts;
                V3 *tw = wind[order]; wind[order] = wind[idx]; wind[idx] = tw;
                int tn = wn[order]; wn[order] = wn[idx]; wn[idx] = tn;
            }
            order++;
        }
    /* edge bevels for slanted edges */
    if (ns > 6) {
        int base = ns;
        for (int si = 6; si < base; si++) {
            V3 *w = wind[si]; int cw = wn[si];
            for (int j = 0; j < cw; j++) {
                double length;
                V3 vec = b_norm(b_sub(w[j], w[(j + 1) % cw]), &length);
                if (length < 0.5) continue;
                double vc[3] = {vec.x, vec.y, vec.z};
                for (int a = 0; a < 3; a++) if (fabs(vc[a] - py_round(vc[a])) < 0.00001) vc[a] = py_round(vc[a]);
                vec.x = vc[0]; vec.y = vc[1]; vec.z = vc[2];
                if (vc[0] == -1.0 || vc[0] == 1.0 || vc[1] == -1.0 || vc[1] == 1.0 || vc[2] == -1.0 || vc[2] == 1.0)
                    continue;           /* axial edge */
                for (int axis = 0; axis < 3; axis++)
                    for (int sgn = -1; sgn <= 1; sgn += 2) {
                        V3 v2 = {0.0, 0.0, 0.0};
                        if (axis == 0) v2.x = sgn; else if (axis == 1) v2.y = sgn; else v2.z = sgn;
                        double nl;
                        V3 normal = b_norm(b_cross(vec, v2), &nl);
                        if (nl < 0.5) continue;
                        double d = b_dot(w[j], normal);
                        int ok = 1;
                        for (int k = 0; k < ns && ok; k++) {
                            V3 sn = sides[k].n;
                            if (fabs(sn.x - normal.x) < 0.01 && fabs(sn.y - normal.y) < 0.01 && fabs(sn.z - normal.z) < 0.01
                                    && fabs(sides[k].d - d) < 0.01) { ok = 0; break; }
                            for (int q = 0; q < wn[k]; q++)
                                if (b_dot(wind[k][q], normal) - d > 0.1) { ok = 0; break; }
                        }
                        if (!ok) continue;
                        /* snap_plane */
                        double sn[3] = {normal.x, normal.y, normal.z};
                        for (int a = 0; a < 3; a++) {
                            if (fabs(sn[a] - 1) < 0.00001) { sn[0] = sn[1] = sn[2] = 0.0; sn[a] = 1.0; break; }
                            if (fabs(sn[a] + 1) < 0.00001) { sn[0] = sn[1] = sn[2] = 0.0; sn[a] = -1.0; break; }
                        }
                        if (fabs(d - py_round(d)) < 0.01) d = py_round(d);
                        GROW();
                        sides[ns].n.x = sn[0]; sides[ns].n.y = sn[1]; sides[ns].n.z = sn[2]; sides[ns].d = d;
                        sides[ns].src = sides[0].src; sides[ns].bevel = 1;
                        wind[ns] = NULL; wn[ns] = 0;
                        ns++;
                    }
            }
        }
    }
#undef GROW
    if (ns > cap) { result = -1; goto done; }
    for (int i = 0; i < ns; i++) {
        out_planes[4 * i] = sides[i].n.x; out_planes[4 * i + 1] = sides[i].n.y; out_planes[4 * i + 2] = sides[i].n.z;
        out_planes[4 * i + 3] = sides[i].d; out_src[i] = sides[i].src; out_bevel[i] = sides[i].bevel;
    }
    for (int a = 0; a < 3; a++) { box[a] = mins[a]; box[3 + a] = maxs[a]; }
    result = ns;
done:
    if (pts) for (int m = 0; m < n; m++) free(pts[m]);
    free(pts); free(nrm); free(dist); free(cnt); free(capw); free(keys);
    free(sides); free(wind); free(wn);
    return result;
}

/* hl_make_brushes: many brushes in one call (props, displacements: thousands at a time). in_counts[b] planes each,
 * packed in planes/bevel_in. Writes each one's side count to out_counts (0: no brush, -1: over per_cap) and its sides
 * packed (per_cap slots each). Returns 0, or -2 when out of memory. */
EXPORT int hl_make_brushes(int nb, const int *in_counts, const double *planes, const int *bevel_in, int per_cap,
                           double *out_planes, int *out_src, int *out_bevel, int *out_counts, double *boxes) {
    int off = 0;
    for (int b = 0; b < nb; b++) {
        int r = hl_make_brush(in_counts[b], planes + 4 * off, bevel_in + off, out_planes + 4 * (size_t)b * per_cap,
                              out_src + (size_t)b * per_cap, out_bevel + (size_t)b * per_cap, per_cap, boxes + 6 * b);
        if (r == -2) return -2;
        out_counts[b] = r;
        off += in_counts[b];
    }
    return 0;
}
