/* inflate(): every point within d of the polygon must be inside the result. Random convex polygons: thin needles,
   sharp corners, 64-point ones (the 64-gon path), far from the origin, both windings.
       python -m ziglang cc -O2 -ffp-contract=off -target x86_64-windows-gnu -w -o vis_inflate_test.exe tests/compile/vis_inflate_test.c
   Prints the worst distance outside (float noise, ~0.002 at 16k units, is fine: hlvvis widens by 0.02 for a 0.01 need). */
#define main hlvvis_main
#include "../../native/hlvvis.c"
#undef main

static double rnd(void) { return rand() / (double)RAND_MAX; }

int main(void) {
    srand(12345);
    const double D = 0.02;
    double worst = -1e9; long long tests = 0, paths[3] = {0};
    for (int trial = 0; trial < 200000; trial++) {
        double nx = rnd() * 2 - 1, ny = rnd() * 2 - 1, nz = rnd() * 2 - 1, nl = sqrt(nx * nx + ny * ny + nz * nz);
        if (trial % 3 == 0) { nx = 0; ny = 0; nz = 1; nl = 1; }
        V n = {(float)(nx / nl), (float)(ny / nl), (float)(nz / nl)};
        V u0 = fabs(n.x) < 0.9f ? (V){0, -n.z, n.y} : (V){n.z, 0, -n.x};
        double ul = sqrt(u0.x * u0.x + u0.y * u0.y + u0.z * u0.z);
        double Ux = u0.x / ul, Uy = u0.y / ul, Uz = u0.z / ul;
        double Vx = n.y * Uz - n.z * Uy, Vy = n.z * Ux - n.x * Uz, Vz = n.x * Uy - n.y * Ux;
        double farv = trial % 2 ? 16000 : 500;
        double cx = (rnd() * 2 - 1) * farv, cy = (rnd() * 2 - 1) * farv, cz = (rnd() * 2 - 1) * farv;
        double A = 1 + rnd() * 300, B = A * (trial % 4 == 0 ? 1e-4 + rnd() * 1e-3 : rnd());
        int N = 3 + rand() % 62;
        if (trial % 5 == 0) N = 64;
        double ts[64];
        for (int i = 0; i < N; i++) ts[i] = rnd() * 2 * 3.14159265358979;
        for (int i = 1; i < N; i++) { double k = ts[i]; int j = i - 1; while (j >= 0 && ts[j] > k) { ts[j + 1] = ts[j]; j--; } ts[j + 1] = k; }
        int cw = trial % 2;
        WinBuf w; w.n = N;
        double rot = rnd() * 6.283;
        for (int i = 0; i < N; i++) {
            double t = ts[cw ? N - 1 - i : i];
            double a = A * cos(t), b = B * sin(t), x = a * cos(rot) - b * sin(rot), y = a * sin(rot) + b * cos(rot);
            w.p[i] = (V){(float)(cx + x * Ux + y * Vx), (float)(cy + x * Uy + y * Vy), (float)(cz + x * Uz + y * Vz)};
        }
        WinBuf o; inflate((Win *)&w, n, D, &o);
        paths[o.n == 64 && N < 64 ? 2 : o.n > N ? 1 : 0]++;
        /* o's winding about n */
        double sx = 0, sy = 0, sz = 0;
        for (int i = 0; i < o.n; i++) { V p = o.p[i], q = o.p[(i + 1) % o.n];
            sx += (double)p.y * q.z - (double)p.z * q.y; sy += (double)p.z * q.x - (double)p.x * q.z; sz += (double)p.x * q.y - (double)p.y * q.x; }
        double sg = sx * n.x + sy * n.y + sz * n.z >= 0 ? 1 : -1;
        for (int s = 0; s < 40; s++) {
            int i = rand() % N; V p = w.p[i], q = w.p[(i + 1) % N];
            double t = s % 2 ? 0 : rnd(), th = rnd() * 6.283, r = D * (0.999 * sqrt(rnd()));
            if (s % 4 == 1) r = D * 0.999;
            double X = p.x + t * (q.x - p.x) + r * (cos(th) * Ux + sin(th) * Vx);
            double Y = p.y + t * (q.y - p.y) + r * (cos(th) * Uy + sin(th) * Vy);
            double Z = p.z + t * (q.z - p.z) + r * (cos(th) * Uz + sin(th) * Vz);
            /* inside o? crossing number in (U, V); outside: distance to o's boundary */
            double qx = X * Ux + Y * Uy + Z * Uz, qy = X * Vx + Y * Vy + Z * Vz, out = 1e300; int in = 0;
            for (int k = 0; k < o.n; k++) {
                V a = o.p[k], b = o.p[(k + 1) % o.n];
                double ax = a.x * Ux + a.y * Uy + a.z * Uz, ay = a.x * Vx + a.y * Vy + a.z * Vz;
                double bx = b.x * Ux + b.y * Uy + b.z * Uz, by = b.x * Vx + b.y * Vy + b.z * Vz;
                if ((ay > qy) != (by > qy) && qx < ax + (qy - ay) * (bx - ax) / (by - ay)) in = !in;
                double ex = bx - ax, ey = by - ay, l2 = ex * ex + ey * ey, tt = l2 > 0 ? ((qx - ax) * ex + (qy - ay) * ey) / l2 : 0;
                tt = tt < 0 ? 0 : tt > 1 ? 1 : tt;
                double dx = ax + tt * ex - qx, dy = ay + tt * ey - qy, dd = sqrt(dx * dx + dy * dy);
                if (dd < out) out = dd;
            }
            if (in) out = -out;            tests++;
            if (out > worst) { worst = out; if (out > 1e-3) printf("trial %d: N=%d out=%d B/A=%.1e farv=%.0f: point %.5f outside\n", trial, N, o.n, B / A, farv, out); }
        }
    }
    printf("%lld points; paths miter %lld / bevel %lld / 64-gon %lld; worst: %.6f %s\n", tests, paths[0], paths[1], paths[2],
           worst, worst <= 2e-3 ? "(inside, to float noise)" : "OUTSIDE");
    return 0;
}
