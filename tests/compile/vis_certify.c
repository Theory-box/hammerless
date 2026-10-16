/* vis_certify: proves cluster pairs HIDDEN (no straight sight line through the portals), the complement of
 * vis_rays (which only proves pairs visible). Used by vis_check.py.
 *   vis_certify <map.prt> a1 b1 [a2 b2 ...]      [-budget STEPS] [-seconds S]
 * One line per pair: "pair A-B: PROVEN HIDDEN ..." or "pair A-B: UNDECIDED chain p0 p1 ... pk" (one-way portal
 * numbers as hlvvis counts them: 2i leaves the portal line's first cluster, 2i+1 the second) or
 * "pair A-B: UNDECIDED budget".
 *
 * The method is vvis's path search (Quake vis: source and pass windows narrowed by separating planes along
 * every portal chain), with every decision made so the windows can only be too big, never too small:
 * - geometry in doubles straight from the .prt; a window cut by a plane keeps everything with d >= -EPS and the
 *   cut is made at d = -EPS exactly, so each cut window contains the exact one;
 * - a plane is used as a separator only when the source is on one side and the pass on the other beyond
 *   doubt (every point within EPS_ON of the plane counts as on it, and the plane is dropped unless the sides
 *   are clear); dropping a plane only widens the result;
 * - the rough pass ("flood") is the same: a portal counts as in front when any point is in front by more than
 *   -EPS, and a chain is only followed while a portal entering the target cluster is still in reach;
 * - a sight line crosses a convex cluster once, so a chain never re-enters a cluster already on it;
 * - no result of another portal is used as a filter (vvis's "done portal" shortcut), only this search.
 * Why empty means hidden: for any sight line from a point s of the base portal through portals p1..pk to a
 * point x of pk, the crossing of pi is in the pass window of step i (induction: s is in the source window,
 * which is the base cut behind every portal crossed, the crossing of p(i-1) is in the previous pass window, and
 * every separating plane has the source on its back side and the previous pass on its front side, so x, beyond
 * the previous pass along the line, is on the front side too, as it is of the base plane). A window that comes
 * out empty therefore holds no crossing of any sight line, and when every chain from every portal leaving A to
 * a portal entering B dies, no sight line from A to B exists. The one unavoidable slack is EPS_ON: a point
 * that is within 1e-9 of a separating plane counts as on it, so a sight line that hugs a separator within
 * ~1e-9 units may be missed; the proof holds for every opening wider than ~1e-7 units, far below anything
 * that renders.
 * Cost: without vvis's might-see pruning the search can be exponential in open areas, so a budget (steps and
 * seconds) ends a pair as UNDECIDED. UNDECIDED with a chain is not a proof of visibility either: it is the chain
 * to try with a sight-line search.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <windows.h>

#define MAXW 256                 /* points of a cut window; a cut needing more keeps its input (a superset) */
#define MAXDEPTH 4096
static const double EPS = 1e-5;  /* cuts keep this much of the wrong side */
static const double EPS_ON = 1e-9;

typedef struct { double x, y, z; } V;
typedef struct { int n; V p[MAXW]; } Win;
typedef struct { V n; double d; } Plane;
typedef struct { Win *w; Plane pl; int leaf; V origin; double radius; uint64_t *flood; } Portal;

static int NCL, NP, NPD, PW;
static Portal *P;
static int *lf_first, *lf_count, *lf_list;

static double dot(V a, V b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static V sub(V a, V b) { return (V){a.x - b.x, a.y - b.y, a.z - b.z}; }
static V cross(V a, V b) { return (V){a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
static void fail(const char *m) { printf("error: %s\n", m); exit(2); }

/* ---- windows ------------------------------------------------------------------------------------------- */
/* out = the part of `in` with n.x - d >= -EPS (cut at exactly -EPS); 0 if nothing is left */
static int cut(const Win *in, const Plane *pl, Win *out) {
    double dist[MAXW];
    int front = 0, back = 0;
    for (int i = 0; i < in->n; i++) {
        dist[i] = dot(in->p[i], pl->n) - pl->d + EPS;
        if (dist[i] >= 0) front++; else back++;
    }
    if (!back) { if (out != in) *out = *in; return 1; }
    if (!front) return 0;
    Win tmp; tmp.n = 0;
    for (int i = 0; i < in->n; i++) {
        int j = (i + 1) % in->n;
        if (dist[i] >= 0) { if (tmp.n == MAXW) { if (out != in) *out = *in; return 1; } tmp.p[tmp.n++] = in->p[i]; }
        if ((dist[i] >= 0) != (dist[j] >= 0)) {
            if (tmp.n == MAXW) { if (out != in) *out = *in; return 1; }
            double t = dist[i] / (dist[i] - dist[j]);
            V a = in->p[i], b = in->p[j];
            tmp.p[tmp.n++] = (V){a.x + t * (b.x - a.x), a.y + t * (b.y - a.y), a.z + t * (b.z - a.z)};
        }
    }
    if (tmp.n < 3) return 0;
    *out = tmp;
    return 1;
}

/* target cut by every plane that separates `src` (back) from `pas` (front): planes through an edge of one and a
 * point of the other. Returns 0 if the target is emptied. */
static int clip_separators(const Win *src, const Win *pas, Win *target) {
    for (int which = 0; which < 2; which++) {
        const Win *E = which ? pas : src, *O = which ? src : pas;
        for (int i = 0; i < E->n; i++) {
            V a = E->p[i], b = E->p[(i + 1) % E->n];
            V e = sub(b, a);
            for (int j = 0; j < O->n; j++) {
                V n = cross(e, sub(O->p[j], a));
                double l = sqrt(dot(n, n));
                if (l < 1e-9) continue;
                n.x /= l; n.y /= l; n.z /= l;
                double d = dot(n, a);
                double smin = 1e300, smax = -1e300, pmin = 1e300, pmax = -1e300;
                for (int k = 0; k < src->n; k++) { double v = dot(src->p[k], n) - d; if (v < smin) smin = v; if (v > smax) smax = v; }
                for (int k = 0; k < pas->n; k++) { double v = dot(pas->p[k], n) - d; if (v < pmin) pmin = v; if (v > pmax) pmax = v; }
                Plane pl;
                if (smax <= EPS_ON && pmin >= -EPS_ON && pmax > EPS_ON) pl = (Plane){n, d};
                else if (smin >= -EPS_ON && pmax <= EPS_ON && pmin < -EPS_ON) pl = (Plane){{-n.x, -n.y, -n.z}, -d};
                else continue;
                if (!cut(target, &pl, target)) return 0;
            }
        }
    }
    return 1;
}

/* ---- the rough pass: which portals a portal can reach at all (vvis's BasePortalVis + flood), conservative */
static CRITICAL_SECTION g_flood_lock;
static void ensure_flood_locked(int q);
static void ensure_flood(int q) {
    if (*(uint64_t *volatile *)&P[q].flood) return;
    EnterCriticalSection(&g_flood_lock);
    ensure_flood_locked(q);
    LeaveCriticalSection(&g_flood_lock);
}
static void ensure_flood_locked(int q) {
    Portal *p = &P[q];
    if (p->flood) return;
    uint64_t *front = calloc(PW, 8);
    for (int t = 0; t < NPD; t++) {
        if (t == q || t == (q ^ 1)) continue;
        Portal *tp = &P[t];
        int k;
        for (k = 0; k < tp->w->n; k++) if (dot(tp->w->p[k], p->pl.n) - p->pl.d > -EPS) break;
        if (k == tp->w->n) continue;                      /* nothing of t in front of q */
        for (k = 0; k < p->w->n; k++) if (dot(p->w->p[k], tp->pl.n) - tp->pl.d < EPS) break;
        if (k == p->w->n) continue;                       /* nothing of q behind t */
        front[t >> 6] |= 1ull << (t & 63);
    }
    uint64_t *fl = calloc(PW, 8);
    int *stack = malloc((NCL + 1) * sizeof(int)), sp = 0;
    char *seen = calloc(NCL, 1);
    stack[sp++] = p->leaf; seen[p->leaf] = 1;
    while (sp) {
        int leaf = stack[--sp];
        for (int i = 0; i < lf_count[leaf]; i++) {
            int t = lf_list[lf_first[leaf] + i];
            if (!((front[t >> 6] >> (t & 63)) & 1)) continue;
            if ((fl[t >> 6] >> (t & 63)) & 1) continue;
            fl[t >> 6] |= 1ull << (t & 63);
            int nl = P[t].leaf;
            if (!seen[nl]) { seen[nl] = 1; stack[sp++] = nl; }
        }
    }
    free(front); free(stack); free(seen);
    MemoryBarrier();
    p->flood = fl;
}

/* ---- the search ---------------------------------------------------------------------------------------- */
/* (per thread: the pairs are shared out over the CPU cores) */
static _Thread_local Win *g_src, *g_pass;              /* per depth */
static _Thread_local uint64_t *g_might;                /* per depth, PW words */
static _Thread_local int *g_chain;                     /* portal at each depth */
static _Thread_local char *g_onpath;                   /* clusters on the current chain */
static _Thread_local uint64_t *g_targets, *g_mask;
static _Thread_local long long g_steps, g_budget;
static _Thread_local double g_deadline;
static _Thread_local Plane g_base;                     /* the base portal's plane */
static _Thread_local V g_base_origin; static _Thread_local double g_base_radius;
static _Thread_local int g_found_depth;
/* -all N: every surviving chain (distinct portal sequences, at most N) is recorded and the search goes on */
static int g_all;
static _Thread_local int g_nchains, *g_chains, *g_chainlen;
static int record_chain(int depth) {
    for (int i = 0; i < g_nchains; i++)
        if (g_chainlen[i] == depth + 1 && !memcmp(g_chains + (size_t)i * MAXDEPTH, g_chain, (depth + 1) * sizeof(int))) return 0;
    if (g_nchains >= g_all) return 1;
    memcpy(g_chains + (size_t)g_nchains * MAXDEPTH, g_chain, (depth + 1) * sizeof(int)); g_chainlen[g_nchains++] = depth + 1;
    return 0;
}

static double now(void) { return (double)clock() / CLOCKS_PER_SEC; }

enum { NOTHING = 0, FOUND = 1, BUDGET = 2 };

static int search(int depth, int leaf) {
    /* chain[0] is the base portal; depth 1 is the first opening (no pass window yet) */
    const Win *source = &g_src[depth], *pass = depth > 1 ? &g_pass[depth] : NULL;
    const uint64_t *might = g_might + (size_t)depth * PW;
    uint64_t *nmight = g_might + (size_t)(depth + 1) * PW;
    if (depth + 1 >= MAXDEPTH) return NOTHING;
    for (int i = 0; i < lf_count[leaf]; i++) {
        int q = lf_list[lf_first[leaf] + i];
        if (!((might[q >> 6] >> (q & 63)) & 1)) continue;
        Portal *p = &P[q];
        int target = (g_targets[q >> 6] >> (q & 63)) & 1;
        if (!target && g_onpath[p->leaf]) continue;      /* a sight line crosses a convex cluster once */
        uint64_t more = 0;
        if (!target) {
            ensure_flood(q);
            for (int k = 0; k < PW; k++) { nmight[k] = might[k] & p->flood[k] & g_mask[k]; more |= nmight[k] & g_targets[k]; }
            if (!more) continue;                          /* no portal into B can follow */
        }
        if (++g_steps > g_budget || (g_steps & 1023) == 0 && now() > g_deadline) return BUDGET;
        /* the portal must be in front of the base plane, the base behind the portal's plane (bounding spheres) */
        double d = dot(p->origin, g_base.n) - g_base.d;
        if (d < -p->radius - EPS) continue;
        Win *npass = &g_pass[depth + 1], *nsrc = &g_src[depth + 1];
        if (d > p->radius + EPS) *npass = *p->w; else if (!cut(p->w, &g_base, npass)) continue;
        d = dot(g_base_origin, p->pl.n) - p->pl.d;
        if (d > g_base_radius + EPS) continue;
        Plane back = {{-p->pl.n.x, -p->pl.n.y, -p->pl.n.z}, -p->pl.d};
        if (d < -g_base_radius - EPS) *nsrc = *source; else if (!cut(source, &back, nsrc)) continue;
        if (pass && !clip_separators(nsrc, pass, npass)) continue;
        g_chain[depth] = q;
        if (target) {
            g_found_depth = depth;
            if (!g_all) return FOUND;
            if (record_chain(depth)) return FOUND;        /* (the cap: stop) */
            continue;
        }
        g_onpath[p->leaf] = 1;
        int r = search(depth + 1, p->leaf);
        g_onpath[p->leaf] = 0;
        if (r) return r;
    }
    return NOTHING;
}

/* ---- portals ------------------------------------------------------------------------------------------- */
static void load(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) fail("can't open the portal file");
    char line[256];
    if (!fgets(line, sizeof line, f) || strncmp(line, "PRT1", 4)) fail("not a PRT1 file");
    if (fscanf(f, "%d %d", &NCL, &NP) != 2 || NCL <= 0 || NP < 0) fail("bad header");
    NPD = 2 * NP; PW = (NPD + 63) / 64; if (!PW) PW = 1;
    P = calloc(NPD, sizeof(Portal));
    lf_count = calloc(NCL, sizeof(int));
    int *c0 = malloc((NP + 1) * sizeof(int)), *c1 = malloc((NP + 1) * sizeof(int));
    for (int i = 0; i < NP; i++) {
        int n;
        if (fscanf(f, "%d %d %d", &n, &c0[i], &c1[i]) != 3 || n < 3 || n > MAXW || c0[i] < 0 || c1[i] < 0
            || c0[i] >= NCL || c1[i] >= NCL) fail("bad portal line");
        Win *w = malloc(sizeof(Win)), *wb = malloc(sizeof(Win));
        w->n = wb->n = n;
        for (int j = 0; j < n; j++)
            if (fscanf(f, " (%lf %lf %lf )", &w->p[j].x, &w->p[j].y, &w->p[j].z) != 3) fail("bad portal point");
        for (int j = 0; j < n; j++) wb->p[j] = w->p[n - 1 - j];
        V nn = cross(sub(w->p[0], w->p[1]), sub(w->p[2], w->p[1]));    /* as hlvvis: (p0 - p1) x (p2 - p1) */
        double l = sqrt(dot(nn, nn));
        if (l > 0) { nn.x /= l; nn.y /= l; nn.z /= l; }
        Plane pl = {nn, dot(w->p[0], nn)};
        Portal *fw = &P[2 * i], *rv = &P[2 * i + 1];
        fw->w = w; fw->leaf = c1[i]; fw->pl = (Plane){{-pl.n.x, -pl.n.y, -pl.n.z}, -pl.d};   /* c0 -> c1 */
        rv->w = wb; rv->leaf = c0[i]; rv->pl = pl;                                            /* c1 -> c0 */
        lf_count[c0[i]]++; lf_count[c1[i]]++;
    }
    fclose(f);
    lf_first = calloc(NCL, sizeof(int)); lf_list = malloc((NPD + 1) * sizeof(int));
    for (int c = 1; c < NCL; c++) lf_first[c] = lf_first[c - 1] + lf_count[c - 1];
    int *fill = calloc(NCL, sizeof(int));
    for (int i = 0; i < NP; i++) {
        lf_list[lf_first[c0[i]] + fill[c0[i]]++] = 2 * i;
        lf_list[lf_first[c1[i]] + fill[c1[i]]++] = 2 * i + 1;
    }
    free(fill); free(c0); free(c1);
    for (int i = 0; i < NPD; i++) {
        Portal *p = &P[i];
        V c = {0, 0, 0};
        for (int j = 0; j < p->w->n; j++) { c.x += p->w->p[j].x; c.y += p->w->p[j].y; c.z += p->w->p[j].z; }
        c.x /= p->w->n; c.y /= p->w->n; c.z /= p->w->n;
        double r = 0;
        for (int j = 0; j < p->w->n; j++) { V d = sub(p->w->p[j], c); double l = sqrt(dot(d, d)); if (l > r) r = l; }
        p->origin = c; p->radius = r;
    }
}

/* the search from A to B: NOTHING (proof: no chain survives), FOUND (chain in g_chain[0..g_found_depth]) or BUDGET */
static int certify_dir(int A, int B) {
    memset(g_targets, 0, PW * 8); memset(g_mask, 0, PW * 8);
    /* the portals into B; and the portals whose reverse the reverse of some target can reach: a sight line
       read backwards goes from B's portal through the chain's portals reversed */
    uint64_t *rev = calloc(PW, 8);
    for (int i = 0; i < lf_count[B]; i++) {
        int t = lf_list[lf_first[B] + i] ^ 1;             /* enters B */
        g_targets[t >> 6] |= 1ull << (t & 63);
        ensure_flood(t ^ 1);
        for (int w = 0; w < PW; w++) rev[w] |= P[t ^ 1].flood[w];
    }
    for (int q = 0; q < NPD; q++) if ((rev[(q ^ 1) >> 6] >> ((q ^ 1) & 63)) & 1) g_mask[q >> 6] |= 1ull << (q & 63);
    for (int w = 0; w < PW; w++) g_mask[w] |= g_targets[w];       /* (a flood never holds its own portal) */
    free(rev);
    if (getenv("VIS_CERTIFY_DEBUG")) {
        int nt = 0, nm = 0; for (int q = 0; q < NPD; q++) { nt += (g_targets[q >> 6] >> (q & 63)) & 1; nm += (g_mask[q >> 6] >> (q & 63)) & 1; }
        printf("  debug: %d targets, %d portals in the reverse mask, %d portals leave %d\n", nt, nm, lf_count[A], A);
        for (int i = 0; i < lf_count[A]; i++) {
            int p0 = lf_list[lf_first[A] + i]; ensure_flood(p0);
            int nf = 0, ft = 0; for (int q = 0; q < NPD; q++) if ((P[p0].flood[q >> 6] >> (q & 63)) & 1) { nf++; ft += (g_targets[q >> 6] >> (q & 63)) & 1; }
            printf("  debug: base %d (into %d): in mask %d, flood %d portals, %d of them targets\n", p0, P[p0].leaf, (int)((g_mask[p0 >> 6] >> (p0 & 63)) & 1), nf, ft);
        }
    }
    int result = NOTHING;
    memset(g_onpath, 0, NCL);
    g_onpath[A] = 1;
    for (int i = 0; i < lf_count[A] && !result; i++) {
        int p0 = lf_list[lf_first[A] + i];
        if (!((g_mask[p0 >> 6] >> (p0 & 63)) & 1)) continue;
        Portal *b = &P[p0];
        if ((g_targets[p0 >> 6] >> (p0 & 63)) & 1) { g_chain[0] = p0; g_found_depth = 0; if (!g_all || record_chain(0)) { result = FOUND; break; } continue; }
        ensure_flood(p0);
        uint64_t more = 0;
        for (int w = 0; w < PW; w++) { g_might[w] = b->flood[w] & g_mask[w]; more |= g_might[w] & g_targets[w]; }
        if (!more) continue;
        g_base = b->pl; g_base_origin = b->origin; g_base_radius = b->radius;
        g_chain[0] = p0;
        g_src[1] = *b->w;                                 /* the source starts as the whole base */
        memcpy(g_might + (size_t)1 * PW, g_might, PW * 8);
        g_onpath[b->leaf] = 1;
        result = search(1, b->leaf);
        g_onpath[b->leaf] = 0;
    }
    if (g_all && result == NOTHING && g_nchains) result = FOUND;
    return result;
}

static int g_np, (*g_pairs)[2], *g_proven;
static double g_seconds; static long long g_budget0;
static char **g_res;
static volatile LONG g_nextpair;
#define PR(...) (len += snprintf(buf + len, cap - len > 0 ? cap - len : 0, __VA_ARGS__))
static DWORD WINAPI pair_worker(LPVOID arg) {
    (void)arg;
    g_src = malloc((size_t)MAXDEPTH * sizeof(Win)); g_pass = malloc((size_t)MAXDEPTH * sizeof(Win));
    g_might = calloc((size_t)MAXDEPTH * PW, 8); g_chain = malloc(MAXDEPTH * sizeof(int));
    g_onpath = calloc(NCL, 1); g_targets = calloc(PW, 8); g_mask = calloc(PW, 8);
    int cap = 1 << 16; char *buf = malloc(cap);
    for (;;) {
        int k = (int)InterlockedIncrement(&g_nextpair) - 1;
        if (k >= g_np) return 0;
        int len = 0;
        buf[0] = 0;
        int A = g_pairs[k][0], B = g_pairs[k][1];
        if (A < 0 || B < 0 || A >= NCL || B >= NCL || A == B) { PR("pair %d-%d: bad clusters\n", A, B); goto next; }
        double t0 = now();
        /* a sight line reversed is a sight line: a proof in either direction is a proof of the pair. Both
           directions are tried (their windows differ); the chain reported is the forward one. */
        g_deadline = t0 + g_seconds; g_steps = 0; g_budget = g_budget0;
        if (g_all) {
            g_nchains = 0;
            if (!g_chains) { g_chains = malloc((size_t)g_all * MAXDEPTH * sizeof(int)); g_chainlen = malloc(g_all * sizeof(int)); }
            int r = certify_dir(A, B);
            PR("pair %d-%d: %s, %d chains%s   (%lld steps, %.2f s)\n", A, B,
                   r == NOTHING ? "PROVEN HIDDEN" : "UNDECIDED", g_nchains, g_nchains >= g_all ? " (cap)" : "", g_steps, now() - t0);
            for (int c = 0; c < g_nchains; c++) {
                PR("  chain:");
                for (int i = 0; i < g_chainlen[c]; i++) PR(" %d", g_chains[(size_t)c * MAXDEPTH + i]);
                PR("\n");
            }
            
            goto next;
        }
        int fwd = certify_dir(A, B), found_depth = g_found_depth;
        int chain[MAXDEPTH]; long long steps = g_steps;
        if (fwd == FOUND) memcpy(chain, g_chain, (found_depth + 1) * sizeof(int));
        int result = fwd;
        if (fwd != NOTHING) {
            g_deadline = now() + g_seconds; g_steps = 0;
            int back = certify_dir(B, A);
            steps += g_steps;
            if (back == NOTHING) result = NOTHING;
            else if (fwd == BUDGET && back == FOUND) { result = FOUND; found_depth = g_found_depth; memcpy(chain, g_chain, (found_depth + 1) * sizeof(int)); }
        }
        double dt = now() - t0;
        if (result == FOUND) {
            PR("pair %d-%d: UNDECIDED chain", A, B);
            for (int i = 0; i <= found_depth; i++) PR(" %d", chain[i]);
            PR("   (%lld steps, %.2f s)\n", steps, dt);
        } else if (result == BUDGET) {
            PR("pair %d-%d: UNDECIDED budget   (%lld steps, %.2f s)\n", A, B, steps, dt);
        } else {
            PR("pair %d-%d: PROVEN HIDDEN%s   (%lld steps, %.2f s)\n", A, B, fwd == NOTHING ? "" : " (from the far side)", steps, dt);
            g_proven[k] = 1;
        }
        
    
      next:
        g_res[k] = _strdup(buf);
    }
}

int main(int argc, char **argv) {
    if (argc < 4) { printf("vis_certify <map.prt> a b [a b ...] [-budget STEPS] [-seconds S]\n"); return 2; }
    long long budget = 20000000; double seconds = 120;
    int pairs[1024][2], np = 0;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "-budget") && i + 1 < argc) budget = atoll(argv[++i]);
        else if (!strcmp(argv[i], "-seconds") && i + 1 < argc) seconds = atof(argv[++i]);
        else if (!strcmp(argv[i], "-all") && i + 1 < argc) g_all = atoi(argv[++i]);
        else if (i + 1 < argc && np < 1024) { pairs[np][0] = atoi(argv[i]); pairs[np][1] = atoi(argv[++i]); np++; }
    }
    load(argv[1]);
    InitializeCriticalSection(&g_flood_lock);
    g_np = np; g_pairs = pairs; g_seconds = seconds; g_budget0 = budget;
    g_res = calloc(np > 0 ? np : 1, sizeof(char *)); g_proven = calloc(np > 0 ? np : 1, sizeof(int));
    SYSTEM_INFO si; GetSystemInfo(&si);
    int nt = g_all ? 1 : (int)si.dwNumberOfProcessors;
    if (getenv("VIS_CERTIFY_THREADS")) nt = atoi(getenv("VIS_CERTIFY_THREADS"));
    if (nt < 1) nt = 1;
    if (nt > 64) nt = 64;
    HANDLE th[64];
    for (int t = 0; t < nt; t++) th[t] = CreateThread(NULL, 0, pair_worker, NULL, 0, NULL);
    WaitForMultipleObjects(nt, th, TRUE, INFINITE);
    int proven = 0;
    for (int k = 0; k < np; k++) { if (g_res[k]) fputs(g_res[k], stdout); proven += g_proven[k]; }
    printf("%d of %d pairs proven hidden\n", proven, np);
    return 0;
}
