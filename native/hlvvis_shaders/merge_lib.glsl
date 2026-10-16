// merge_lib.glsl: hlvvis -jacobi's merge step on the GPU, a library to #include (no main()).
// Ports hull2(), outside_hull(), winding_ccw(), the rule of j_merge() and j_open() from native/hlvvis.c, operation
// for operation in the C's order and with the C's types (float windings, double arithmetic), so the windows that
// come out are the CPU's bit for bit. Every float / double expression is `precise` (no fused multiply-adds); the
// C is built with -ffp-contract=off, SSE2 (IEEE single and double, round to nearest even).
//
// The including shader must, before this file:
//   #extension GL_EXT_shader_explicit_arithmetic_types_float64 : require
//   #extension GL_EXT_spirv_intrinsics : require
//   spirv_execution_mode(4462, 32); spirv_execution_mode(4462, 64);   // RoundingModeRTE (double -> float here)
//   spirv_execution_mode(4461, 32); spirv_execution_mode(4461, 64);   // SignedZeroInfNanPreserve
//   #extension GL_GOOGLE_include_directive : enable   (or paste the file)
// and may define, before the #include:
//   MERGE_MAXW            points per winding array (default 64 = the C's MAXPTS / MAXIN). Every winding parameter below is
//                         a vec3[MERGE_MAXW] with a separate count; hull2 takes two of them (up to 2 * MERGE_MAXW points,
//                         like the C's pts[2 * MAXPTS]).
//   MERGE_HULL_CAP        the C's g_hull_cap (default 16, -jacobi's); an int expression, e.g. int(pc.hull_cap). <= MERGE_MAXW.
//   MERGE_CAPDIR_BINDING  binding of the cap-direction table (default 8): double cap_dir[2 * cap], cos at [2k], sin at
//                         [2k + 1], the CPU's g_cap_dir written as is (set_hull_cap: a = 2*pi*k/cap in double, cos(a),
//                         sin(a) from the C library), so no GLSL trig is used and both sides read one table.
//   MERGE_NO_CAPDIR_BUFFER  define it to supply the table yourself as `double merge_cap_dir(int k, int c)` (c: 0 cos, 1 sin).
//
// Functions (all windings coplanar on the plane with float normal n; counts are ints):
//   int  winding_ccw(in vec3 w[MERGE_MAXW], int nw, vec3 n)
//        1 if w winds counter-clockwise around n (the C's winding_ccw: double area sum, then > 0).
//   int  outside_hull(in vec3 a[MERGE_MAXW], int na, in vec3 h[MERGE_MAXW], int nh, vec3 n, double eps)
//        1 if some point of a lies outside convex polygon h by more than eps (or h has no area).
//   void hull2(in vec3 a[MERGE_MAXW], int na, in vec3 b[MERGE_MAXW], int nb, vec3 n, int ccw,
//              out vec3 o[MERGE_MAXW], out int no)
//        the convex hull of a's and b's points, wound counter-clockwise around n if ccw != 0 else clockwise; more than
//        MERGE_HULL_CAP corners: the cap-gon of supporting lines in the table's directions (a superset).
//        a, b and o may be the same array at the call (GLSL copies in / out): hull2(S, nS, S, nS, ..., S, nS) caps S.
//   bool merge_candidate(inout bool inited, inout bool opened, inout int grows,
//                        inout vec3 S[MERGE_MAXW], inout int nS, inout vec3 W[MERGE_MAXW], inout int nW,
//                        in vec3 src[MERGE_MAXW], int nsrc, in vec3 pas[MERGE_MAXW], int npas,
//                        vec3 bn, int bccw, vec3 pn, int pccw, double noise)
//        j_merge's rule for one candidate (src on the base's plane bn, pas on the portal's plane pn; bccw / pccw =
//        winding_ccw of the base portal's / this portal's own winding). Returns true when the portal must be
//        expanded (the C's "dirty" request: the state was new, or S or W grew); the caller keeps the dirty flag and
//        the next-round list. noise = MERGE_JACOBI_NOISE. (The C also marks the portal in the base's vis bits.)
//   void open_fully(in vec3 bw[MERGE_MAXW], int nbw, vec3 bn, int bccw, in vec3 qw[MERGE_MAXW], int nqw, vec3 qn, int qccw,
//                   out vec3 S[MERGE_MAXW], out int nS, out vec3 W[MERGE_MAXW], out int nW, out bool opened)
//        j_open's rule: S = the base's whole winding, W = the portal's whole winding, each capped; opened = true.
//        Used by the C when (a) more than MERGE_JACOBI_MAX_ARRIVALS candidates reach one portal in a round (the first
//        is merged as usual, then it opens and a dirty request is made if none is pending), and (b) at expansion
//        when ++grows > MERGE_JACOBI_SATURATE and it is not yet opened.
//
// Exactness notes: see the report / comments at each step. The C's hull2 with np == 0 yields n = -2 (no points, no
// writes); mirrored, not guarded.

#ifndef MERGE_MAXW
#define MERGE_MAXW 64
#endif
#ifndef MERGE_HULL_CAP
#define MERGE_HULL_CAP 16
#endif
#ifndef MERGE_CAPDIR_BINDING
#define MERGE_CAPDIR_BINDING 8
#endif
#define MERGE_JACOBI_NOISE 1e-4lf        // the C's JACOBI_NOISE (double 1e-4)
#define MERGE_JACOBI_SATURATE 16         // expansions before a portal opens fully
#define MERGE_JACOBI_MAX_ARRIVALS 64     // candidates per portal per round before it opens fully

#ifndef MERGE_NO_CAPDIR_BUFFER
layout(std430, binding = MERGE_CAPDIR_BINDING) readonly buffer MergeCapDir { double merge_cap_dir_tab[]; };
double merge_cap_dir(int k, int c) { return merge_cap_dir_tab[2 * k + c]; }
#endif

// the C's `sx * n.x + sy * n.y + sz * n.z` on a float n: ((sx * nx + sy * ny) + sz * nz) in double
precise double merge_dot_dn(double sx, double sy, double sz, vec3 n) {
    precise double d = sx * double(n.x) + sy * double(n.y);
    d = d + sz * double(n.z);
    return d;
}

// the C's area sum over the edges of w (used by winding_ccw and outside_hull): sx += (double)p.y*q.z - (double)p.z*q.y ...
precise double merge_area_dot(in vec3 w[MERGE_MAXW], int nw, vec3 n) {
    precise double sx = 0.0lf, sy = 0.0lf, sz = 0.0lf;
    for (int i = 0; i < nw; i++) {
        vec3 p = w[i], q = w[(i + 1) % nw];
        sx = sx + (double(p.y) * double(q.z) - double(p.z) * double(q.y));
        sy = sy + (double(p.z) * double(q.x) - double(p.x) * double(q.z));
        sz = sz + (double(p.x) * double(q.y) - double(p.y) * double(q.x));
    }
    return merge_dot_dn(sx, sy, sz, n);
}

int winding_ccw(in vec3 w[MERGE_MAXW], int nw, vec3 n) {
    precise double o = merge_area_dot(w, nw, n);
    return o > 0.0lf ? 1 : 0;
}

// some point of a lies outside convex polygon h (both on the plane with normal n), by any margin
int outside_hull(in vec3 a[MERGE_MAXW], int na, in vec3 h[MERGE_MAXW], int nh, vec3 n, double eps) {
    precise double o = merge_area_dot(h, nh, n);
    if (o == 0.0lf) return 1;
    for (int i = 0; i < nh; i++) {
        vec3 p = h[i], q = h[(i + 1) % nh];
        precise double ex = double(q.x) - double(p.x), ey = double(q.y) - double(p.y), ez = double(q.z) - double(p.z);
        for (int k = 0; k < na; k++) {
            precise double ax = double(a[k].x) - double(p.x), ay = double(a[k].y) - double(p.y), az = double(a[k].z) - double(p.z);
            // c = (ey*az - ez*ay)*n.x + (ez*ax - ex*az)*n.y + (ex*ay - ey*ax)*n.z, left to right
            precise double c1 = ey * az - ez * ay;
            precise double c2 = ez * ax - ex * az;
            precise double c3 = ex * ay - ey * ax;
            precise double c = merge_dot_dn(c1, c2, c3, n);
            precise double el2 = ex * ex + ey * ey;
            el2 = el2 + ez * ez;
            precise double el = sqrt(el2);
            precise double lim = eps * el;                 // the C's -eps * el is (-eps) * el == -(eps * el) exactly
            if ((o > 0.0lf && c < -lim) || (o < 0.0lf && c > lim)) return 1;
        }
    }
    return 0;
}

// convex hull of a's and b's points (coplanar, plane normal n); ccw != 0: counter-clockwise around n, else clockwise
void hull2(in vec3 a[MERGE_MAXW], int na, in vec3 b[MERGE_MAXW], int nb, vec3 n, int ccw,
           out vec3 o[MERGE_MAXW], out int no) {
    vec3 pts[2 * MERGE_MAXW]; int np = 0;
    for (int i = 0; i < na; i++) pts[np++] = a[i];
    for (int i = 0; i < nb; i++) pts[np++] = b[i];
    // u: float, from float n (the C's (V){0, -n.z, n.y} / (V){n.z, 0, -n.x}; fabs(n.x) < 0.9f is a float compare)
    vec3 u = abs(n.x) < 0.9 ? vec3(0.0, -n.z, n.y) : vec3(n.z, 0.0, -n.x);
    // ul = sqrt((double)u.x*u.x + (double)u.y*u.y + (double)u.z*u.z): products and sums in double, left to right
    precise double ul2 = double(u.x) * double(u.x) + double(u.y) * double(u.y);
    ul2 = ul2 + double(u.z) * double(u.z);
    precise double ul = sqrt(ul2);
    precise double ux = double(u.x) / ul, uy = double(u.y) / ul, uz = double(u.z) / ul;
    // v = n x u with float n promoted to double per product
    precise double vx = double(n.y) * uz - double(n.z) * uy;
    precise double vy = double(n.z) * ux - double(n.x) * uz;
    precise double vz = double(n.x) * uy - double(n.y) * ux;
    precise double px[2 * MERGE_MAXW], py[2 * MERGE_MAXW]; int idx[2 * MERGE_MAXW];
    for (int i = 0; i < np; i++) {
        // px = pts.x*ux + pts.y*uy + pts.z*uz: ((x*ux + y*uy) + z*uz), each float promoted to double
        precise double t = double(pts[i].x) * ux + double(pts[i].y) * uy;
        px[i] = t + double(pts[i].z) * uz;
        precise double s = double(pts[i].x) * vx + double(pts[i].y) * vy;
        py[i] = s + double(pts[i].z) * vz;
        idx[i] = i;
    }
    // insertion sort by (px, py), stable (ties keep input order)
    for (int i = 1; i < np; i++) {
        int k = idx[i], j = i - 1;
        while (j >= 0 && (px[idx[j]] > px[k] || (px[idx[j]] == px[k] && py[idx[j]] > py[k]))) { idx[j + 1] = idx[j]; j--; }
        idx[j + 1] = k;
    }
    // monotone chain: lower hull then upper hull, counter-clockwise in (u, v); cr <= 0 pops (collinear points dropped)
    int h[4 * MERGE_MAXW], m = 0;
    for (int pass = 0; pass < 2; pass++) {
        int start = m;
        for (int ii = 0; ii < np; ii++) {
            int i = pass != 0 ? idx[np - 1 - ii] : idx[ii];
            while (m >= start + 2) {
                int a0 = h[m - 2], b0 = h[m - 1];
                precise double cr = (px[b0] - px[a0]) * (py[i] - py[a0]) - (py[b0] - py[a0]) * (px[i] - px[a0]);
                if (cr <= 0.0lf) m--; else break;
            }
            h[m++] = i;
        }
        m--;
    }
    if (m > MERGE_HULL_CAP) {
        // too many corners: the K-gon of supporting lines in K directions around the hull (contains it)
        const int K = MERGE_HULL_CAP;
        precise double lx[MERGE_MAXW], ly[MERGE_MAXW], ld[MERGE_MAXW];
        for (int k = 0; k < K; k++) {
            lx[k] = merge_cap_dir(k, 0); ly[k] = merge_cap_dir(k, 1); ld[k] = -1e300lf;
            for (int i = 0; i < m; i++) {
                precise double d = px[h[i]] * lx[k] + py[h[i]] * ly[k];
                if (d > ld[k]) ld[k] = d;
            }
            ld[k] = ld[k] + 1e-2lf;                          // (outside by more than float corners can drift)
        }
        precise double ox = 0.0lf, oy = 0.0lf, oz = 0.0lf;   // the plane's offset along n: the mean of ALL input points
        for (int i = 0; i < np; i++) { ox = ox + double(pts[i].x); oy = oy + double(pts[i].y); oz = oz + double(pts[i].z); }
        ox = ox / double(np); oy = oy / double(np); oz = oz / double(np);
        precise double on = merge_dot_dn(ox, oy, oz, n);
        no = K;
        for (int k = 0; k < K; k++) {                        // corner k: lines k and k+1 meet (counter-clockwise)
            int j = (k + 1) % K;
            precise double det = lx[k] * ly[j] - ly[k] * lx[j];
            precise double cun = ld[k] * ly[j] - ly[k] * ld[j];
            precise double cvn = lx[k] * ld[j] - ld[k] * lx[j];
            precise double cu = cun / det, cv = cvn / det;
            // (float)(cu*ux + cv*vx + on*n.x): ((cu*ux + cv*vx) + on*nx) in double, then one rounding to float (RTE)
            precise double qx = cu * ux + cv * vx; qx = qx + on * double(n.x);
            precise double qy = cu * uy + cv * vy; qy = qy + on * double(n.y);
            precise double qz = cu * uz + cv * vz; qz = qz + on * double(n.z);
            vec3 q = vec3(float(qx), float(qy), float(qz));
            o[ccw != 0 ? k : K - 1 - k] = q;
        }
        return;
    }
    no = m;
    // hull is counter-clockwise in (u, v): around u x v = n; flip if the original winding goes the other way
    for (int i = 0; i < m; i++) o[i] = pts[h[ccw != 0 ? i : m - 1 - i]];
}

// j_merge's rule for one candidate (src, pas) into the portal's state. Returns true if the portal must be expanded
// (again): the state was new this flow, or S or W grew. Opened portals take nothing.
bool merge_candidate(inout bool inited, inout bool opened, inout int grows,
                     inout vec3 S[MERGE_MAXW], inout int nS, inout vec3 W[MERGE_MAXW], inout int nW,
                     in vec3 src[MERGE_MAXW], int nsrc, in vec3 pas[MERGE_MAXW], int npas,
                     vec3 bn, int bccw, vec3 pn, int pccw, double noise) {
    vec3 T[MERGE_MAXW]; int nT;
    if (!inited) {
        // first arrival: the candidate's windows, each capped if it has more than cap points
        inited = true; grows = 0; opened = false;
        nS = nsrc; for (int i = 0; i < nsrc; i++) S[i] = src[i];
        nW = npas; for (int i = 0; i < npas; i++) W[i] = pas[i];
        if (nS > MERGE_HULL_CAP) { hull2(S, nS, S, nS, bn, bccw, T, nT); nS = nT; for (int i = 0; i < nT; i++) S[i] = T[i]; }
        if (nW > MERGE_HULL_CAP) { hull2(W, nW, W, nW, pn, pccw, T, nT); nW = nT; for (int i = 0; i < nT; i++) W[i] = T[i]; }
        return true;
    }
    if (opened) return false;
    bool grew = false;
    if (outside_hull(src, nsrc, S, nS, bn, noise) != 0) {
        hull2(S, nS, src, nsrc, bn, bccw, T, nT); nS = nT; for (int i = 0; i < nT; i++) S[i] = T[i];
        grew = true;
    }
    if (outside_hull(pas, npas, W, nW, pn, noise) != 0) {
        hull2(W, nW, pas, npas, pn, pccw, T, nT); nW = nT; for (int i = 0; i < nT; i++) W[i] = T[i];
        grew = true;
    }
    return grew;
}

// j_open's rule: open the portal fully (the whole base winding bw and the whole portal winding qw, capped): a superset
void open_fully(in vec3 bw[MERGE_MAXW], int nbw, vec3 bn, int bccw, in vec3 qw[MERGE_MAXW], int nqw, vec3 qn, int qccw,
                out vec3 S[MERGE_MAXW], out int nS, out vec3 W[MERGE_MAXW], out int nW, out bool opened) {
    vec3 T[MERGE_MAXW]; int nT;
    nS = nbw; for (int i = 0; i < nbw; i++) S[i] = bw[i];
    nW = nqw; for (int i = 0; i < nqw; i++) W[i] = qw[i];
    if (nS > MERGE_HULL_CAP) { hull2(S, nS, S, nS, bn, bccw, T, nT); nS = nT; for (int i = 0; i < nT; i++) S[i] = T[i]; }
    if (nW > MERGE_HULL_CAP) { hull2(W, nW, W, nW, qn, qccw, T, nT); nW = nT; for (int i = 0; i < nT; i++) W[i] = T[i]; }
    opened = true;
}
