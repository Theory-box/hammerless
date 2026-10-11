/* vis_rays: proves cluster pairs visible with random sight lines (never proves hidden); used by vis_check.py.
 *   vis_rays <map.bsp> <map.prt> <rays per pair> a1 b1 [a2 b2 ...]
 * Each ray joins a random point on a portal of cluster a to a random point on a portal of cluster b; it is a
 * real sight line when every leaf it passes through has a cluster (solid leaves have cluster -1). */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { double x, y, z; } V;
static int *nplane, *nchild, *lclus; static float *pn, *pd;
static int NN;

static int open_seg(int node, V a, V b) {
    while (node >= 0) {
        const float *n = pn + 3 * nplane[node]; double d = pd[nplane[node]];
        double da = n[0] * a.x + n[1] * a.y + n[2] * a.z - d, db = n[0] * b.x + n[1] * b.y + n[2] * b.z - d;
        if (da >= 0 && db >= 0) { node = nchild[2 * node]; continue; }
        if (da < 0 && db < 0) { node = nchild[2 * node + 1]; continue; }
        double t = da / (da - db);
        V m = {a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t};
        int near = da >= 0 ? 0 : 1;
        if (!open_seg(nchild[2 * node + near], a, m)) return 0;
        a = m; node = nchild[2 * node + 1 - near];
    }
    return lclus[-node - 1] >= 0;
}

typedef struct { int n; V p[64]; } W;
static W *pw; static int *pc0, *pc1, NP, NCL;
static uint64_t rng = 88172645463325252ull;
static double rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (rng >> 11) * (1.0 / 9007199254740992.0); }

static V rand_on(const W *w) {
    /* a random point of the polygon, pulled 0.1 % toward its centre (off the edges) */
    double tot = 0, ar[64];
    for (int t = 1; t + 1 < w->n; t++) {
        V u = {w->p[t].x - w->p[0].x, w->p[t].y - w->p[0].y, w->p[t].z - w->p[0].z};
        V v = {w->p[t + 1].x - w->p[0].x, w->p[t + 1].y - w->p[0].y, w->p[t + 1].z - w->p[0].z};
        V c = {u.y * v.z - u.z * v.y, u.z * v.x - u.x * v.z, u.x * v.y - u.y * v.x};
        ar[t] = sqrt(c.x * c.x + c.y * c.y + c.z * c.z); tot += ar[t];
    }
    double r = rnd() * tot; int t = 1;
    while (t + 2 < w->n && r > ar[t]) { r -= ar[t]; t++; }
    double s1 = rnd(), s2 = rnd();
    if (s1 + s2 > 1) { s1 = 1 - s1; s2 = 1 - s2; }
    V p = {w->p[0].x + (w->p[t].x - w->p[0].x) * s1 + (w->p[t + 1].x - w->p[0].x) * s2,
           w->p[0].y + (w->p[t].y - w->p[0].y) * s1 + (w->p[t + 1].y - w->p[0].y) * s2,
           w->p[0].z + (w->p[t].z - w->p[0].z) * s1 + (w->p[t + 1].z - w->p[0].z) * s2};
    V c = {0, 0, 0};
    for (int i = 0; i < w->n; i++) { c.x += w->p[i].x; c.y += w->p[i].y; c.z += w->p[i].z; }
    c.x /= w->n; c.y /= w->n; c.z /= w->n;
    p.x += (c.x - p.x) * 0.001; p.y += (c.y - p.y) * 0.001; p.z += (c.z - p.z) * 0.001;
    return p;
}

int main(int argc, char **argv) {
    if (argc < 6) { printf("vis_rays <map.bsp> <map.prt> <rays> a b [a b ...]\n"); return 2; }
    FILE *f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long len = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *d = malloc(len); fread(d, 1, len, f); fclose(f);
    #define LUMP(i, o, l) int o = *(int *)(d + 8 + 16 * (i) + 4), l = *(int *)(d + 8 + 16 * (i) + 8)
    LUMP(1, po, pl_); LUMP(5, no, nl); LUMP(10, lo, ll);
    int npl = pl_ / 20; pn = malloc(npl * 12); pd = malloc(npl * 4);
    for (int i = 0; i < npl; i++) { memcpy(pn + 3 * i, d + po + 20 * i, 12); memcpy(pd + i, d + po + 20 * i + 12, 4); }
    NN = nl / 32; nplane = malloc(NN * 4); nchild = malloc(NN * 8);
    for (int i = 0; i < NN; i++) { memcpy(nplane + i, d + no + 32 * i, 4); memcpy(nchild + 2 * i, d + no + 32 * i + 4, 8); }
    int lver = *(int *)(d + 8 + 16 * 10); int lsize = lver == 1 ? 32 : 56; int nlf = ll / lsize;
    lclus = malloc(nlf * 4);
    for (int i = 0; i < nlf; i++) { short c; memcpy(&c, d + lo + lsize * i + 4, 2); lclus[i] = c; }
    FILE *pf = fopen(argv[2], "r"); char line[256]; fgets(line, sizeof line, pf);
    fscanf(pf, "%d %d", &NCL, &NP);
    pw = malloc(NP * sizeof(W)); pc0 = malloc(NP * 4); pc1 = malloc(NP * 4);
    for (int i = 0; i < NP; i++) {
        fscanf(pf, "%d %d %d", &pw[i].n, &pc0[i], &pc1[i]);
        for (int j = 0; j < pw[i].n; j++) fscanf(pf, " (%lf %lf %lf )", &pw[i].p[j].x, &pw[i].p[j].y, &pw[i].p[j].z);
    }
    long long rays = atoll(argv[3]);
    for (int a = 4; a + 1 < argc; a += 2) {
        int A = atoi(argv[a]), B = atoi(argv[a + 1]);
        int pa[512], na = 0, pb[512], nb = 0;
        for (int i = 0; i < NP; i++) {
            if ((pc0[i] == A || pc1[i] == A) && na < 512) pa[na++] = i;
            if ((pc0[i] == B || pc1[i] == B) && nb < 512) pb[nb++] = i;
        }
        long long hits = 0;
        for (long long r = 0; r < rays && !hits; r++) {
            V p = rand_on(&pw[pa[(int)(rnd() * na)]]), q = rand_on(&pw[pb[(int)(rnd() * nb)]]);
            if (open_seg(0, p, q)) { hits++; printf("  %d-%d SEEN: (%.2f %.2f %.2f) -> (%.2f %.2f %.2f)\n", A, B, p.x, p.y, p.z, q.x, q.y, q.z); }
        }
        printf("pair %d-%d: %s (%d and %d portals, up to %lld rays)\n", A, B, hits ? "VISIBLE (a ray got through)" : "no ray got through", na, nb, rays);
        fflush(stdout);
    }
    return 0;
}
