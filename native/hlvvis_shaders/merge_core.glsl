// merge_core.glsl: hlvvis -jacobi's merge step on the GPU, a library to #include (no main()): the C's hull2(),
// outside_hull() and the rules of j_merge() / j_open() (native/hlvvis.c), operation for operation in the C's order
// and types (float windings, double arithmetic, every expression `precise`: no fused multiply-adds; the C is built
// with -ffp-contract=off, SSE2), so the windows that come out are the CPU's bit for bit (HL_MERGE_TEST checks it).
// Laid out for little memory per invocation: the windows live in a few private arrays shared by every step, and the
// hull has one call site (each inlined copy of a function with array parameters costs its own arrays: a layout with
// array parameters needed 37 KB per invocation, more scratch than the driver can give the card, and it lost the device).
//
// The including shader must, before this file:
//   #extension GL_EXT_shader_explicit_arithmetic_types_float64 : require
//   #extension GL_EXT_spirv_intrinsics : require
//   spirv_execution_mode(4462, 32); spirv_execution_mode(4462, 64);   // RoundingModeRTE (double -> float here)
//   spirv_execution_mode(4461, 32); spirv_execution_mode(4461, 64);   // SignedZeroInfNanPreserve
// and may define MERGE_HULL_CAP (the C's g_hull_cap, an int expression, at most 16) and MERGE_CAPDIR_BINDING (binding
// of the cap's direction table, default 8: double cap_dir[2 * cap], cos at [2k], sin at [2k + 1], the CPU's g_cap_dir
// as is, so no GLSL trig is used).
//
// State (side 0: S on the base's plane, side 1: W on the portal's plane):
//   MS[side][MS_n[side]]   the stored windows (at most the cap)
//   MC[side][MC_n[side]]   the incoming windows (side 0: source, side 1: pass; up to 64 points)
//   M_n[side], M_ccw[side] the side's plane normal and winding_ccw of the portal's own winding
// Steps: merge_side(side, op) with op MERGE_SET (MS = MC, capped: a first arrival, or opening fully with MC = the
// whole windings) or MERGE_GROW (if some point of MC lies outside MS by more than the noise: MS = hull(MS + MC));
// returns true if MS grew (MERGE_SET: always). Call it from one place only, in a loop that isn't unrolled.
#extension GL_EXT_control_flow_attributes : require

#ifndef MERGE_HULL_CAP
#define MERGE_HULL_CAP 16
#endif
#ifndef MERGE_CAPDIR_BINDING
#define MERGE_CAPDIR_BINDING 8
#endif
#define MERGE_JACOBI_NOISE 1e-4lf
#define MERGE_SET 1
#define MERGE_GROW 2
#define MC_MAX 64
#define MS_MAX 16
#define HP_MAX 128              // hull input: at most 2 * MC_MAX (a window capped on its own comes in twice, as the C's hull2(S, S))

layout(std430, binding = MERGE_CAPDIR_BINDING) readonly buffer MergeCapDir { double merge_cap_dir_tab[]; };

vec3 MS[2][MS_MAX]; int MS_n[2];
vec3 MC[2][MC_MAX]; int MC_n[2];
vec3 M_n[2]; int M_ccw[2];

// the hull's input points: MERGE_SET: MC then MC again (the C's hull2(S, S)); MERGE_GROW: MS then MC
int h_side, h_op, h_na;
vec3 hpt(int i) {
    if (h_op == MERGE_SET) return MC[h_side][i < h_na ? i : i - h_na];
    return i < h_na ? MS[h_side][i] : MC[h_side][i - h_na];
}

precise double merge_dot_dn(double sx, double sy, double sz, vec3 n) {
    precise double d = sx * double(n.x) + sy * double(n.y);
    d = d + sz * double(n.z);
    return d;
}

// some point of MC[side] lies outside convex polygon MS[side] (the C's outside_hull, eps = the noise)
bool merge_outside(int side) {
    vec3 n = M_n[side];
    int nh = MS_n[side], na = MC_n[side];
    precise double sx = 0.0lf, sy = 0.0lf, sz = 0.0lf;
    for (int i = 0; i < nh; i++) {
        vec3 p = MS[side][i], q = MS[side][(i + 1) % nh];
        sx = sx + (double(p.y) * double(q.z) - double(p.z) * double(q.y));
        sy = sy + (double(p.z) * double(q.x) - double(p.x) * double(q.z));
        sz = sz + (double(p.x) * double(q.y) - double(p.y) * double(q.x));
    }
    precise double o = merge_dot_dn(sx, sy, sz, n);
    if (o == 0.0lf) return true;
    for (int i = 0; i < nh; i++) {
        vec3 p = MS[side][i], q = MS[side][(i + 1) % nh];
        precise double ex = double(q.x) - double(p.x), ey = double(q.y) - double(p.y), ez = double(q.z) - double(p.z);
        for (int k = 0; k < na; k++) {
            vec3 a = MC[side][k];
            precise double ax = double(a.x) - double(p.x), ay = double(a.y) - double(p.y), az = double(a.z) - double(p.z);
            precise double c1 = ey * az - ez * ay;
            precise double c2 = ez * ax - ex * az;
            precise double c3 = ex * ay - ey * ax;
            precise double c = merge_dot_dn(c1, c2, c3, n);
            precise double el2 = ex * ex + ey * ey;
            el2 = el2 + ez * ez;
            precise double el = sqrt(el2);
            precise double lim = MERGE_JACOBI_NOISE * el;
            if ((o > 0.0lf && c < -lim) || (o < 0.0lf && c > lim)) return true;
        }
    }
    return false;
}

// the convex hull of the np points hpt(0 .. np-1) (the C's hull2), into MS[h_side]
precise double h_px[HP_MAX], h_py[HP_MAX];
int h_idx[HP_MAX], h_h[2 * HP_MAX];
vec3 h_out[MS_MAX];
void merge_hull(int np) {
    vec3 n = M_n[h_side];
    int ccw = M_ccw[h_side];
    vec3 u = abs(n.x) < 0.9 ? vec3(0.0, -n.z, n.y) : vec3(n.z, 0.0, -n.x);
    precise double ul2 = double(u.x) * double(u.x) + double(u.y) * double(u.y);
    ul2 = ul2 + double(u.z) * double(u.z);
    precise double ul = sqrt(ul2);
    precise double ux = double(u.x) / ul, uy = double(u.y) / ul, uz = double(u.z) / ul;
    precise double vx = double(n.y) * uz - double(n.z) * uy;
    precise double vy = double(n.z) * ux - double(n.x) * uz;
    precise double vz = double(n.x) * uy - double(n.y) * ux;
    for (int i = 0; i < np; i++) {
        vec3 pt = hpt(i);
        precise double t = double(pt.x) * ux + double(pt.y) * uy;
        h_px[i] = t + double(pt.z) * uz;
        precise double s = double(pt.x) * vx + double(pt.y) * vy;
        h_py[i] = s + double(pt.z) * vz;
        h_idx[i] = i;
    }
    for (int i = 1; i < np; i++) {
        int k = h_idx[i], j = i - 1;
        while (j >= 0 && (h_px[h_idx[j]] > h_px[k] || (h_px[h_idx[j]] == h_px[k] && h_py[h_idx[j]] > h_py[k]))) { h_idx[j + 1] = h_idx[j]; j--; }
        h_idx[j + 1] = k;
    }
    int m = 0;
    for (int pass = 0; pass < 2; pass++) {
        int start = m;
        for (int ii = 0; ii < np; ii++) {
            int i = pass != 0 ? h_idx[np - 1 - ii] : h_idx[ii];
            while (m >= start + 2) {
                int a0 = h_h[m - 2], b0 = h_h[m - 1];
                precise double cr = (h_px[b0] - h_px[a0]) * (h_py[i] - h_py[a0]) - (h_py[b0] - h_py[a0]) * (h_px[i] - h_px[a0]);
                if (cr <= 0.0lf) m--; else break;
            }
            h_h[m++] = i;
        }
        m--;
    }
    if (m > MERGE_HULL_CAP) {
        const int K = MERGE_HULL_CAP;
        precise double lx[MS_MAX], ly[MS_MAX], ld[MS_MAX];
        for (int k = 0; k < K; k++) {
            lx[k] = merge_cap_dir_tab[2 * k]; ly[k] = merge_cap_dir_tab[2 * k + 1]; ld[k] = -1e300lf;
            for (int i = 0; i < m; i++) {
                precise double d = h_px[h_h[i]] * lx[k] + h_py[h_h[i]] * ly[k];
                if (d > ld[k]) ld[k] = d;
            }
            ld[k] = ld[k] + 1e-2lf;
        }
        precise double ox = 0.0lf, oy = 0.0lf, oz = 0.0lf;
        for (int i = 0; i < np; i++) { vec3 pt = hpt(i); ox = ox + double(pt.x); oy = oy + double(pt.y); oz = oz + double(pt.z); }
        ox = ox / double(np); oy = oy / double(np); oz = oz / double(np);
        precise double on = merge_dot_dn(ox, oy, oz, n);
        for (int k = 0; k < K; k++) {
            int j = (k + 1) % K;
            precise double det = lx[k] * ly[j] - ly[k] * lx[j];
            precise double cun = ld[k] * ly[j] - ly[k] * ld[j];
            precise double cvn = lx[k] * ld[j] - ld[k] * lx[j];
            precise double cu = cun / det, cv = cvn / det;
            precise double qx = cu * ux + cv * vx; qx = qx + on * double(n.x);
            precise double qy = cu * uy + cv * vy; qy = qy + on * double(n.y);
            precise double qz = cu * uz + cv * vz; qz = qz + on * double(n.z);
            h_out[ccw != 0 ? k : K - 1 - k] = vec3(float(qx), float(qy), float(qz));
        }
        for (int k = 0; k < K; k++) MS[h_side][k] = h_out[k];
        MS_n[h_side] = K;
        return;
    }
    for (int i = 0; i < m; i++) h_out[i] = hpt(h_h[ccw != 0 ? i : m - 1 - i]);
    for (int i = 0; i < m; i++) MS[h_side][i] = h_out[i];
    MS_n[h_side] = m;
}

// one step on one side (see the top); true if MS grew
bool merge_side(int side, int op) {
    h_side = side, h_op = op;
    int np;
    if (op == MERGE_SET) {
        int n = MC_n[side];
        if (n <= MERGE_HULL_CAP) {
            for (int i = 0; i < n; i++) MS[side][i] = MC[side][i];
            MS_n[side] = n;
            return true;
        }
        h_na = n, np = 2 * n;
    } else {
        if (!merge_outside(side)) return false;
        h_na = MS_n[side], np = MS_n[side] + MC_n[side];
    }
    merge_hull(np);
    return true;
}
