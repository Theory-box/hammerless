/* The ray tracer vrad lights with (Valve's RayTracingEnvironment, rebuilt).
 *
 * Triangles: the sides of every opaque world brush (made from the brush's planes, so hidden sides count
 * too), the world's sky faces (marked sky), displacements and static props (to come). A ray stops at the
 * nearest triangle. The triangle test is Valve's to the float operation (plane distance, then two edge
 * equations scaled to 1 at the opposite corner, with L4D2's build multiplying by reciprocals), and so are
 * the kd-tree and its 4-ray traversal (a ray can register a hit in any leaf its packet visits), so hits and
 * misses exactly at shadow edges come out the same. */
#include <float.h>
#include <emmintrin.h>
#include "hlvrad.h"

typedef struct {
    float nx, ny, nz, d;
    float e[6];
    unsigned char c0, c1;
    int id;
} rttri_t;

static rttri_t *tris;
static float (*tri_verts)[9];
static int numtris, maxtris;

void RT_AddTriangle(int id, const vec3_t v0, const vec3_t v1, const vec3_t v2) {
    if (numtris == maxtris) {
        maxtris = maxtris ? maxtris * 2 : 4096;
        tris = realloc(tris, sizeof(rttri_t) * maxtris);
        tri_verts = realloc(tri_verts, sizeof(float[9]) * maxtris);
    }
    memcpy(tri_verts[numtris], v0, 12);
    memcpy(tri_verts[numtris] + 3, v1, 12);
    memcpy(tri_verts[numtris] + 6, v2, 12);
    tris[numtris].id = id;
    numtris++;
}

/* an edge's line in the two kept coordinates, scaled to 1 at the opposite corner (times the reciprocal) */
static void EdgeEquation(const float *p1, const float *p2, int c1, int c2, const float *inside, float *out) {
    float nx = p1[c2] - p2[c2], ny = p2[c1] - p1[c1];
    float d = -(nx * p1[c1] + ny * p1[c2]);
    float trial = inside[c1] * nx + inside[c2] * ny + d;
    if (trial < 0) {
        nx = -nx;
        ny = -ny;
        d = -d;
        trial = -trial;
    }
    float r = 1.0f / trial;
    out[0] = r * nx;
    out[1] = r * ny;
    out[2] = r * d;
}

static void IntersectionFormat(int i) {
    const float *p1 = tri_verts[i], *p2 = tri_verts[i] + 3, *p3 = tri_verts[i] + 6;
    vec3_t e1, e2, n;
    VectorSubtract(p2, p1, e1);
    VectorSubtract(p3, p1, e2);
    n[0] = e1[1] * e2[2] - e1[2] * e2[1];
    n[1] = e1[2] * e2[0] - e1[0] * e2[2];
    n[2] = e1[0] * e2[1] - e1[1] * e2[0];
    VectorNormalize(n);
    int drop = 0;
    if (fabsf(n[1]) > fabsf(n[0])) drop = 1;
    if (fabsf(n[2]) > fabsf(n[drop])) drop = 2;
    rttri_t *t = &tris[i];
    t->d = DotProduct(n, p1);
    t->nx = n[0];
    t->ny = n[1];
    t->nz = n[2];
    t->c0 = (unsigned char)((drop + 1) % 3);
    t->c1 = (unsigned char)((drop + 2) % 3);
    EdgeEquation(p1, p2, t->c0, t->c1, p3, t->e);
    EdgeEquation(p2, p3, t->c0, t->c1, p1, t->e + 3);
}

/* ------------------------------------------------------------------ the kd-tree (Valve's build) */
/* Surface area heuristic: try the middle and (every 1 + n/10th triangle's) vertices on each axis as split
 * planes; split when the estimated cost beats intersecting everything (costs 75 per step, 167 per triangle);
 * an empty side "grows" to the triangles. Kept to Valve's operations, since which leaves a ray visits decides
 * the rare hits exactly on an edge. */
#define KDNODE_STATE_LEAF 3
#define COST_OF_TRAVERSAL 75
#define COST_OF_INTERSECTION 167
#define MAX_TREE_DEPTH 21
#define PLANECHECK_POSITIVE 1
#define PLANECHECK_NEGATIVE -1
#define PLANECHECK_STRADDLING 0
#define MAILBOX_HASH_SIZE 256
#define MAX_NODE_STACK_LEN (40 * MAX_TREE_DEPTH)

typedef struct {
    int children;              /* leaf: 3 + (first index << 2); else split axis + (left child << 2) */
    union {
        float split;
        int count;
    };
} kdnode_t;

static kdnode_t *kd;
static int numkd, maxkd;
static int *trilist;
static int numtrilist, maxtrilist;
static float minbound[3], maxbound[3];

typedef struct {
    kdnode_t *kd;
    int numkd, maxkd;
    int *trilist;
    int numtrilist, maxtrilist;
    signed char *tmp0, *tmp1;
    int defer;                     /* (the main build: leave subtrees of fewer triangles than this for later) */
} kdbuild_t;

static int AddNode(kdbuild_t *b) {
    if (b->numkd == b->maxkd) {
        b->maxkd = b->maxkd ? b->maxkd * 2 : 1024;
        b->kd = realloc(b->kd, sizeof(kdnode_t) * b->maxkd);
    }
    memset(&b->kd[b->numkd], 0, sizeof(kdnode_t));
    return b->numkd++;
}

static void AddTriIndex(kdbuild_t *b, int t) {
    if (b->numtrilist == b->maxtrilist) {
        b->maxtrilist = b->maxtrilist ? b->maxtrilist * 2 : 4096;
        b->trilist = realloc(b->trilist, sizeof(int) * b->maxtrilist);
    }
    b->trilist[b->numtrilist++] = t;
}

/* subtrees left for the threads */
typedef struct { int node, n, depth; int *list; float mn[3], mx[3]; kdbuild_t b; } kdtask_t;
static kdtask_t *kdtasks;
static int numkdtasks;

static int Classify(int t, int axis, float split) {
    float minc = tri_verts[t][axis], maxc = minc;
    for (int v = 1; v < 3; v++) {
        float c = tri_verts[t][3 * v + axis];
        if (c < minc) minc = c;
        if (c > maxc) maxc = c;
    }
    if (minc >= split) return PLANECHECK_POSITIVE;
    if (maxc <= split) return PLANECHECK_NEGATIVE;
    if (minc == maxc) return PLANECHECK_POSITIVE;
    return PLANECHECK_STRADDLING;
}

/* The surface area heuristic: 75 + 167 * (straddling + left * area(left)/area + right * area(right)/area).
 * L4D2's is x87: the box sides and areas in double (two of the full box's sides rounded to float first,
 * the left box's x side too), 1/area rounded to float, each area's terms in its own order. */
static double CostOfSplit(int axis, const int *list, int n, const float *mn, const float *mx, float *split, int *nl,
                          int *nr, int *nb, signed char *classes) {
    *nl = *nr = *nb = 0;
    float min_coord = 1.0e23f, max_coord = -1.0e23f;
    for (int i = 0; i < n; i++) {
        int t = list[i];
        for (int v = 0; v < 3; v++) {
            float c = tri_verts[t][3 * v + axis];
            if (c < min_coord) min_coord = c;
            if (c > max_coord) max_coord = c;
        }
        int cl = Classify(t, axis, *split);
        if (classes) classes[t] = (signed char)cl;
        if (cl == PLANECHECK_NEGATIVE) (*nl)++;
        else if (cl == PLANECHECK_POSITIVE) (*nr)++;
        else (*nb)++;
    }
    if (*nl && !*nb && !*nr) *split = max_coord;
    if (*nr && !*nb && !*nl) *split = min_coord;
    float lmax[3], rmin[3];
    memcpy(lmax, mx, 12);
    memcpy(rmin, mn, 12);
    lmax[axis] = *split;
    rmin[axis] = *split;
    double d0 = (double)mx[0] - mn[0];
    float t1 = mx[1] - mn[1], t2 = mx[2] - mn[2];
    double S = ((double)t2 * d0 + d0 * t1) + (double)t2 * t1;
    float oo = (float)(1.0 / (S * 2.0));
    float ld0 = lmax[0] - mn[0];
    double ld1 = (double)lmax[1] - mn[1], ld2 = (double)lmax[2] - mn[2];
    double SL = (ld2 * ld0 + ld0 * ld1) + ld1 * ld2;
    double rd0 = (double)mx[0] - rmin[0], rd1 = (double)mx[1] - rmin[1], rd2 = (double)mx[2] - rmin[2];
    double SR = (rd0 * rd1 + rd2 * rd0) + rd1 * rd2;
    double x = (double)*nb + (SL * 2.0 * oo) * *nl;
    return (x + (SR * 2.0 * oo) * *nr) * (double)COST_OF_INTERSECTION + (double)COST_OF_TRAVERSAL;
}

static void MakeLeaf(kdbuild_t *b, int node, const int *list, int n) {
    b->kd[node].children = KDNODE_STATE_LEAF + (b->numtrilist << 2);
    b->kd[node].count = n;
    for (int i = 0; i < n; i++) AddTriIndex(b, list[i]);
}

/* (speed only) a big node's split candidates, each costed on its own thread */
typedef struct { int axis; float trial, split; double cost; int nl, nr, nb; } candidate_t;
static candidate_t *cands;
static const int *cand_list;
static int cand_n;
static const float *cand_mn, *cand_mx;
static void CandidateWork(int i, int thread) {
    (void)thread;
    candidate_t *c = &cands[i];
    c->split = c->trial;
    c->cost = CostOfSplit(c->axis, cand_list, cand_n, cand_mn, cand_mx, &c->split, &c->nl, &c->nr, &c->nb, NULL);
}
#define PARALLEL_SPLIT_TRIS 2048

static void RefineNode(kdbuild_t *b, int node, const int *list, int n, const float *mn, const float *mx, int depth) {
    if (n < 3) {
        MakeLeaf(b, node, list, n);
        return;
    }
    if (n < b->defer) {
        kdtasks = realloc(kdtasks, sizeof(kdtask_t) * (numkdtasks + 1));
        kdtask_t *t = &kdtasks[numkdtasks++];
        memset(t, 0, sizeof(*t));
        t->node = node, t->n = n, t->depth = depth;
        t->list = xalloc(sizeof(int) * (n + 1));
        memcpy(t->list, list, sizeof(int) * n);
        memcpy(t->mn, mn, 12), memcpy(t->mx, mx, 12);
        return;
    }
    signed char *tmp0 = b->tmp0, *tmp1 = b->tmp1;
    float best_cost = 1.0e23f, best_split = 0;
    int best_nl = 0, best_nr = 0, best_nb = 0, split_axis = 0;
    int skip = 1 + n / 10;
    if (b->defer && n >= PARALLEL_SPLIT_TRIS) {        /* (the main build only: candidates use globals) */
        int nc = 0;
        cands = xalloc(sizeof(candidate_t) * (3 * (3 * (n / skip + 2) + 1) + 1));
        for (int axis = 0; axis < 3; axis++)
            for (int ts = -1; ts < n; ts += skip)
                for (int tv = 0; tv < 3; tv++) {
                    float trial;
                    if (ts == -1) trial = (float)(0.5 * (mn[axis] + mx[axis]));
                    else {
                        trial = tri_verts[list[ts]][3 * tv + axis];
                        if (trial > mx[axis] || trial < mn[axis]) continue;
                    }
                    cands[nc].axis = axis, cands[nc].trial = trial, nc++;
                    if (ts == -1) break;
                }
        cand_list = list, cand_n = n, cand_mn = mn, cand_mx = mx;
        RunThreadsOn(nc, CandidateWork);
        float best_trial = 0;
        for (int i = 0; i < nc; i++)
            if (best_cost > cands[i].cost) {          /* (the same comparisons as the loop below) */
                split_axis = cands[i].axis;
                best_cost = (float)cands[i].cost;
                best_nl = cands[i].nl, best_nr = cands[i].nr, best_nb = cands[i].nb;
                best_split = cands[i].split, best_trial = cands[i].trial;
            }
        free(cands);
        for (int i = 0; i < n; i++) tmp1[list[i]] = (signed char)Classify(list[i], split_axis, best_trial);
        goto chosen;
    }
    for (int axis = 0; axis < 3; axis++) {
        for (int ts = -1; ts < n; ts += skip) {
            for (int tv = 0; tv < 3; tv++) {
                float trial;
                if (ts == -1) trial = (float)(0.5 * (mn[axis] + mx[axis]));
                else {
                    trial = tri_verts[list[ts]][3 * tv + axis];
                    if (trial > mx[axis] || trial < mn[axis]) continue;
                }
                int nl, nr, nb;
                double cost = CostOfSplit(axis, list, n, mn, mx, &trial, &nl, &nr, &nb, tmp0);
                if (best_cost > cost) {            /* (unrounded cost against the kept float) */
                    split_axis = axis;
                    best_cost = (float)cost;
                    best_nl = nl;
                    best_nr = nr;
                    best_nb = nb;
                    best_split = trial;
                    for (int i = 0; i < n; i++) tmp1[list[i]] = tmp0[list[i]];
                }
                if (ts == -1) break;
            }
        }
    }
chosen:;
    float no_split = (float)(COST_OF_INTERSECTION * n);
    if (no_split <= best_cost || depth > MAX_TREE_DEPTH) {
        MakeLeaf(b, node, list, n);
        return;
    }
    int *nlist = xalloc(sizeof(int) * (n + 1));
    float lmax[3], rmin[3];
    memcpy(lmax, mx, 12);
    memcpy(rmin, mn, 12);
    lmax[split_axis] = best_split;
    rmin[split_axis] = best_split;
    int nlo = 0, nbo = 0, nro = 0;
    for (int i = 0; i < n; i++) {
        int t = list[i];
        if (tmp1[t] == PLANECHECK_NEGATIVE) nlist[nlo++] = t;
        else if (tmp1[t] == PLANECHECK_POSITIVE) nlist[n - ++nro] = t;
        else nlist[best_nl + nbo++] = t;
    }
    int left = AddNode(b);
    AddNode(b);
    b->kd[node].children = split_axis + (left << 2);
    b->kd[node].split = best_split;
    if (n < 20 && (best_nl == 0 || best_nr == 0)) depth += 100;
    RefineNode(b, left, nlist, best_nl + best_nb, mn, lmax, depth + 1);
    RefineNode(b, left + 1, nlist + best_nl, best_nr + best_nb, rmin, mx, depth + 1);
    free(nlist);
}

static void SubtreeWork(int i, int thread) {
    (void)thread;
    static __thread signed char *t0, *t1;
    if (!t0) t0 = xalloc(numtris + 1), t1 = xalloc(numtris + 1);
    kdtask_t *t = &kdtasks[i];
    t->b.tmp0 = t0, t->b.tmp1 = t1;
    AddNode(&t->b);
    RefineNode(&t->b, 0, t->list, t->n, t->mn, t->mx, t->depth);
}

void RT_SetupAccelerationStructure(void) {
    kdbuild_t main = {0};
    main.defer = 8192;            /* (speed only: smaller subtrees built on all cores; a tree node's number doesn't
                                   * change which nodes a ray visits, so the tracing is the same) */
    AddNode(&main);
    int *root = xalloc(sizeof(int) * (numtris + 1));
    for (int t = 0; t < numtris; t++) root[t] = t;
    for (int c = 0; c < 3; c++) minbound[c] = 1.0e23f, maxbound[c] = -1.0e23f;
    for (int t = 0; t < numtris; t++)
        for (int v = 0; v < 3; v++)
            for (int c = 0; c < 3; c++) {
                float x = tri_verts[t][3 * v + c];
                if (x < minbound[c]) minbound[c] = x;
                if (x > maxbound[c]) maxbound[c] = x;
            }
    main.tmp0 = xalloc(numtris + 1);
    main.tmp1 = xalloc(numtris + 1);
    RefineNode(&main, 0, root, numtris, minbound, maxbound, 0);
    free(root);
    RunThreadsOn(numkdtasks, SubtreeWork);
    /* each subtree into the main arrays: its root into the node left for it, the rest after, indices moved */
    for (int i = 0; i < numkdtasks; i++) {
        kdtask_t *t = &kdtasks[i];
        int base = main.numkd - 1, lbase = main.numtrilist;     /* (subtree node k > 0 goes to base + k) */
        for (int k = 1; k < t->b.numkd; k++) AddNode(&main);
        for (int k = 0; k < t->b.numkd; k++) {
            kdnode_t n = t->b.kd[k];
            if ((n.children & 3) == KDNODE_STATE_LEAF) n.children = KDNODE_STATE_LEAF + (((n.children >> 2) + lbase) << 2);
            else n.children = (n.children & 3) + (((n.children >> 2) + base) << 2);
            main.kd[k == 0 ? t->node : base + k] = n;
        }
        for (int k = 0; k < t->b.numtrilist; k++) AddTriIndex(&main, t->b.trilist[k]);
        free(t->b.kd), free(t->b.trilist), free(t->list);
    }
    free(kdtasks), kdtasks = NULL, numkdtasks = 0;
    kd = main.kd, numkd = main.numkd, maxkd = main.maxkd;
    trilist = main.trilist, numtrilist = main.numtrilist, maxtrilist = main.maxtrilist;
    free(main.tmp0), free(main.tmp1);
    for (int i = 0; i < numtris; i++) IntersectionFormat(i);
    if (getenv("RTDUMP")) {     /* (debugging: triangles, nodes, leaf lists) */
        FILE *f = fopen(getenv("RTDUMP"), "wb");
        fwrite(&numtris, 4, 1, f), fwrite(tri_verts, 36, numtris, f);
        fwrite(&numkd, 4, 1, f), fwrite(kd, sizeof(kdnode_t), numkd, f);
        fwrite(&numtrilist, 4, 1, f), fwrite(trilist, 4, numtrilist, f);
        fwrite(tris, sizeof(rttri_t), numtris, f);
        fclose(f);
    }
}

/* ------------------------------------------------------------------ tracing 4 rays together (Valve's Trace4Rays) */
static __m128 ReciprocalSaturate4(__m128 a) {
    __m128 zero = _mm_cmpeq_ps(a, _mm_setzero_ps());
    a = _mm_or_ps(a, _mm_and_ps(_mm_set1_ps(FLT_EPSILON), zero));
    __m128 r = _mm_rcp_ps(a);
    return _mm_sub_ps(_mm_add_ps(r, r), _mm_mul_ps(a, _mm_mul_ps(r, r)));
}

static int SignBit(float f) {
    unsigned u;
    memcpy(&u, &f, 4);
    return (u >> 31) & 1;
}

/* bit c set when all 4 directions are negative along c; -1 when the signs are mixed */
static int DirectionSignMask(const float d[3][4]) {
    int ret = 0;
    for (int c = 0; c < 3; c++) {
        int neg = 0;
        for (int i = 0; i < 4; i++) neg += SignBit(d[c][i]);
        if (neg == 4) ret |= 1 << c;
        else if (neg) return -1;
    }
    return ret;
}

typedef struct {
    int node;
    __m128 tmin, tmax;
} kdvisit_t;

static void Trace4Masked(const float o[3][4], const float d[3][4], __m128 TMin, __m128 TMax, int mask, int hit[4],
                         float hitdist[4], int skip_id) {
    for (int i = 0; i < 4; i++) hit[i] = -1, hitdist[i] = 1.0e23f;
    if (!numtris) return;
    __m128 org[3], dir[3], inv[3];
    for (int c = 0; c < 3; c++) {
        org[c] = _mm_loadu_ps(o[c]);
        dir[c] = _mm_loadu_ps(d[c]);
        inv[c] = ReciprocalSaturate4(dir[c]);
    }
    __m128 hd = _mm_set1_ps(1.0e23f);
    __m128i hid = _mm_set1_epi32(-1);
    for (int c = 0; c < 3; c++) {
        __m128 a = _mm_mul_ps(_mm_sub_ps(_mm_set1_ps(minbound[c]), org[c]), inv[c]);
        __m128 b = _mm_mul_ps(_mm_sub_ps(_mm_set1_ps(maxbound[c]), org[c]), inv[c]);
        TMin = _mm_max_ps(TMin, _mm_min_ps(a, b));
        TMax = _mm_min_ps(TMax, _mm_max_ps(a, b));
    }
    __m128 active = _mm_cmple_ps(TMin, TMax);
    if (!_mm_movemask_ps(active)) goto done;
    /* (the triangles already tested: a slot counts only when stamped with this call's number) */
    static __thread int mailbox[MAILBOX_HASH_SIZE], mailstamp[MAILBOX_HASH_SIZE], call;
    call++;
    int front[3], back[3];
    for (int c = 0; c < 3; c++) {
        if (mask & (1 << c)) back[c] = 0, front[c] = 1;
        else back[c] = 1, front[c] = 0;
    }
    kdvisit_t stack[MAX_NODE_STACK_LEN];
    int sp = MAX_NODE_STACK_LEN, cur = 0;
    const __m128 zeros = _mm_setzero_ps(), ones = _mm_set1_ps(1.0f);
    const __m128 eps = _mm_set1_ps(1.0e-10f), neps = _mm_set1_ps(-1.0e-10f);
    for (;;) {
        while ((kd[cur].children & 3) != KDNODE_STATE_LEAF) {
            int axis = kd[cur].children & 3, fchild = kd[cur].children >> 2;
            __m128 dist = _mm_mul_ps(_mm_sub_ps(_mm_set1_ps(kd[cur].split), org[axis]), inv[axis]);
            active = _mm_cmple_ps(TMin, TMax);
            __m128 hits_front = _mm_and_ps(active, _mm_cmpge_ps(dist, TMin));
            if (!_mm_movemask_ps(hits_front)) {
                cur = fchild + back[axis];
                TMin = _mm_max_ps(TMin, dist);
            } else {
                __m128 hits_back = _mm_and_ps(active, _mm_cmple_ps(dist, TMax));
                if (!_mm_movemask_ps(hits_back)) {
                    cur = fchild + front[axis];
                    TMax = _mm_min_ps(TMax, dist);
                } else {
                    if (sp <= 0) Error("ray tracer: node stack overflow");
                    --sp;
                    stack[sp].node = fchild + back[axis];
                    stack[sp].tmin = _mm_max_ps(TMin, dist);
                    stack[sp].tmax = TMax;
                    cur = fchild + front[axis];
                    TMax = _mm_min_ps(TMax, dist);
                }
            }
        }
        int ntris = kd[cur].count;
        if (ntris) {
            const int *tl = trilist + (kd[cur].children >> 2);
            for (int k = 0; k < ntris; k++) {
                int tnum = tl[k];
                int slot = tnum & (MAILBOX_HASH_SIZE - 1);
                const rttri_t *t = &tris[tnum];
                if ((mailstamp[slot] == call && mailbox[slot] == tnum) || t->id == skip_id) continue;
                mailbox[slot] = tnum, mailstamp[slot] = call;
                __m128 nx = _mm_set1_ps(t->nx), ny = _mm_set1_ps(t->ny), nz = _mm_set1_ps(t->nz);
                __m128 ddotn = _mm_add_ps(_mm_add_ps(_mm_mul_ps(dir[0], nx), _mm_mul_ps(dir[1], ny)), _mm_mul_ps(dir[2], nz));
                __m128 did = _mm_or_ps(_mm_cmpgt_ps(ddotn, eps), _mm_cmplt_ps(ddotn, neps));
                __m128 odotn = _mm_add_ps(_mm_add_ps(_mm_mul_ps(org[0], nx), _mm_mul_ps(org[1], ny)), _mm_mul_ps(org[2], nz));
                __m128 isect = _mm_div_ps(_mm_sub_ps(_mm_set1_ps(t->d), odotn), ddotn);
                did = _mm_and_ps(did, _mm_cmpgt_ps(isect, eps));         /* (L4D2: 1e-10, not 0) */
                did = _mm_and_ps(did, _mm_cmplt_ps(isect, hd));
                if (!_mm_movemask_ps(did)) continue;
                __m128 h1 = _mm_add_ps(org[t->c0], _mm_mul_ps(isect, dir[t->c0]));
                __m128 h2 = _mm_add_ps(org[t->c1], _mm_mul_ps(isect, dir[t->c1]));
                __m128 b0 = _mm_add_ps(_mm_add_ps(_mm_mul_ps(_mm_set1_ps(t->e[0]), h1), _mm_mul_ps(_mm_set1_ps(t->e[1]), h2)),
                                       _mm_set1_ps(t->e[2]));
                did = _mm_and_ps(did, _mm_cmpge_ps(b0, eps));            /* (on an edge: a miss) */
                __m128 b1 = _mm_add_ps(_mm_add_ps(_mm_mul_ps(_mm_set1_ps(t->e[3]), h1), _mm_mul_ps(_mm_set1_ps(t->e[4]), h2)),
                                       _mm_set1_ps(t->e[5]));
                did = _mm_and_ps(did, _mm_cmpge_ps(b1, eps));
                did = _mm_and_ps(did, _mm_cmple_ps(_mm_add_ps(b1, b0), ones));
                if (!_mm_movemask_ps(did)) continue;
                __m128i didi = _mm_castps_si128(did);
                hid = _mm_or_si128(_mm_and_si128(_mm_set1_epi32(tnum), didi), _mm_andnot_si128(didi, hid));
                hd = _mm_or_ps(_mm_and_ps(isect, did), _mm_andnot_ps(did, hd));
            }
            if (!_mm_movemask_ps(_mm_cmple_ps(TMax, hd))) goto done;      /* every ray has hit something nearer */
        }
        if (sp == MAX_NODE_STACK_LEN) goto done;
        cur = stack[sp].node;
        TMin = stack[sp].tmin;
        TMax = stack[sp].tmax;
        sp++;
    }
done:
    _mm_storeu_si128((__m128i *)hit, hid);
    _mm_storeu_ps(hitdist, hd);
}

/* 4 rays: same-signed directions are traced together; mixed ones in groups, as Valve's tracer does */
void RT_Trace4(const float o[3][4], const float d[3][4], const float tmin[4], const float tmax[4], int skip_id, int hit[4],
               float hitdist[4]) {
    int mask = DirectionSignMask(d);
    __m128 TMin = _mm_loadu_ps(tmin), TMax = _mm_loadu_ps(tmax);
    if (mask != -1) {
        Trace4Masked(o, d, TMin, TMax, mask, hit, hitdist, skip_id);
        return;
    }
    unsigned char need[4] = {1, 1, 1, 1};
    float td[3][4];
    for (int i = 0; i < 4; i++) {
        if (!need[i]) continue;
        need[i] = 2;
        for (int c = 0; c < 3; c++)
            for (int j = 0; j < 4; j++) td[c][j] = d[c][i];
        for (int j = i + 1; j < 4; j++) {
            if (need[j] && SignBit(d[0][j]) == SignBit(d[0][i]) && SignBit(d[1][j]) == SignBit(d[1][i]) &&
                SignBit(d[2][j]) == SignBit(d[2][i])) {
                need[j] = 2;
                for (int c = 0; c < 3; c++) td[c][j] = d[c][j];
            }
        }
        int th[4];
        float tdist[4];
        Trace4Masked(o, (const float(*)[4])td, TMin, TMax, DirectionSignMask((const float(*)[4])td), th, tdist, skip_id);
        for (int j = 0; j < 4; j++)
            if (need[j] == 2) {
                need[j] = 0;
                hit[j] = th[j];
                hitdist[j] = tdist[j];
            }
    }
}

/* 4 unit rays with the same direction signs (mask), from 0 to tmax (vrad's ray stream) */
void RT_Trace4Mask(const float o[3][4], const float d[3][4], const float tmax[4], int mask, int hit[4], float hitdist[4]) {
    Trace4Masked(o, d, _mm_setzero_ps(), _mm_loadu_ps(tmax), mask, hit, hitdist, -1);
}

int RT_TriangleID(int tri) { return tris[tri].id; }

/* SSE's reciprocal estimate refined once (Valve's ReciprocalSIMD) */
static float ReciprocalSSE(float a) {
    __m128 x = _mm_set_ss(a), r = _mm_rcp_ss(x);
    r = _mm_sub_ss(_mm_add_ss(r, r), _mm_mul_ss(x, _mm_mul_ss(r, r)));
    return _mm_cvtss_f32(r);
}

/* 4 segments start -> stop as vrad's TestLine makes rays of them: unit directions, lengths */
static void RaysFromSegments(const float start[3][4], const float stop[3][4], float d[3][4], float len[4]) {
    for (int i = 0; i < 4; i++) {
        float dx = stop[0][i] - start[0][i], dy = stop[1][i] - start[1][i], dz = stop[2][i] - start[2][i];
        len[i] = sqrtf((dx * dx + dy * dy) + dz * dz);
        float r = ReciprocalSSE(len[i]);
        d[0][i] = dx * r;
        d[1][i] = dy * r;
        d[2][i] = dz * r;
    }
}

/* visibility of 4 segments (1 = nothing in the way), skipping the triangles of one static prop */
void TestLine4(const float start[3][4], const float stop[3][4], int static_prop_to_skip, float vis[4]) {
    float d[3][4], len[4], tmin[4] = {0, 0, 0, 0}, dist[4];
    int hit[4];
    RaysFromSegments(start, stop, d, len);
    RT_Trace4(start, (const float(*)[4])d, tmin, len, TRACE_ID_STATICPROP | static_prop_to_skip, hit, dist);
    for (int i = 0; i < 4; i++) vis[i] = hit[i] != -1 && dist[i] < len[i] ? 0.0f : 1.0f;
}

/* how much of each segment reaches the sky: 0 when the first thing met is not sky
 * TODO: 3D skybox recursion */
void TestLine_DoesHitSky4(const float start[3][4], const float stop[3][4], int static_prop_to_skip, float frac[4]) {
    float d[3][4], len[4], tmin[4] = {0, 0, 0, 0}, dist[4];
    int hit[4];
    RaysFromSegments(start, stop, d, len);
    RT_Trace4(start, (const float(*)[4])d, tmin, len, TRACE_ID_STATICPROP | static_prop_to_skip, hit, dist);
    for (int i = 0; i < 4; i++) {
        float occl = hit[i] != -1 && dist[i] < len[i] && !(tris[hit[i]].id & TRACE_ID_SKY) ? 1.0f : 0.0f;
        occl = occl > 0 ? occl : 0;
        occl = occl < 1 ? occl : 1;
        frac[i] = 1.0f - occl;
    }
}

/* one segment (all 4 lanes the same) */
float TestLine(const vec3_t start, const vec3_t stop, int static_prop_to_skip) {
    float s[3][4], e[3][4], v[4];
    for (int c = 0; c < 3; c++)
        for (int i = 0; i < 4; i++) s[c][i] = start[c], e[c][i] = stop[c];
    TestLine4((const float(*)[4])s, (const float(*)[4])e, static_prop_to_skip, v);
    return v[0];
}

float TestLine_DoesHitSky(const vec3_t start, const vec3_t stop, int static_prop_to_skip) {
    float s[3][4], e[3][4], v[4];
    for (int c = 0; c < 3; c++)
        for (int i = 0; i < 4; i++) s[c][i] = start[c], e[c][i] = stop[c];
    TestLine_DoesHitSky4((const float(*)[4])s, (const float(*)[4])e, static_prop_to_skip, v);
    return v[0];
}

/* ------------------------------------------------------------------ the world's triangles */
/* A brush's sides as triangles: each side's plane as a huge square, cut by every other (non-bevel) side.
 * L4D2 does this around a point inside the brush (the origin pushed onto any side it is behind, up to 4
 * passes) for precision, then moves the result back. */
static void AddBrush(int b) {
    const dbrush_t *brush = &dbrushes[b];
    if (!(brush->contents & MASK_OPAQUE)) return;
    vec3_t c = {0, 0, 0};
    for (int pass = 0, done = 0; pass < 4 && !done; pass++) {
        done = 1;
        for (int i = 0; i < brush->numsides; i++) {
            const dplane_t *pl = &dplanes[dbrushsides[brush->firstside + i].planenum];
            float d = ((pl->normal[1] * c[1] + pl->normal[0] * c[0]) + pl->normal[2] * c[2]) - pl->dist;
            if (0 > d) {
                for (int k = 0; k < 3; k++) c[k] -= pl->normal[k] * d;
                done = 0;
            }
        }
    }
    vec3_t off = {-c[0], -c[1], -c[2]};
    for (int i = 0; i < brush->numsides; i++) {
        const dbrushside_t *side = &dbrushsides[brush->firstside + i];
        const dplane_t *plane = &dplanes[side->planenum];
        int tflags = side->texinfo >= 0 && side->texinfo < numtexinfo ? texinfo[side->texinfo].flags : 0;
        if ((tflags & SURF_SKY) || side->dispinfo) continue;
        float dist = plane->dist + ((plane->normal[1] * off[1] + plane->normal[0] * off[0]) + plane->normal[2] * off[2]);
        winding_t *w = BaseWindingForPlane(plane->normal, dist);
        for (int j = 0; j < brush->numsides && w; j++) {
            if (i == j) continue;
            const dbrushside_t *other = &dbrushsides[brush->firstside + j];
            if (other->bevel) continue;
            const dplane_t *p2 = &dplanes[other->planenum ^ 1];
            float d2 = ((p2->normal[1] * off[1] + p2->normal[0] * off[0]) + p2->normal[2] * off[2]) + p2->dist;
            ChopWindingInPlace(&w, p2->normal, d2, 0);
        }
        if (!w) continue;
        for (int j = 0; j < w->numpoints; j++)
            for (int k = 0; k < 3; k++) w->p[j][k] = (w->p[j][k] + c[k]) + 0.0f;    /* (+0.0f: vrad's transform turns -0 into 0) */
        for (int j = 2; j < w->numpoints; j++) RT_AddTriangle(TRACE_ID_OPAQUE, w->p[0], w->p[j - 1], w->p[j]);
        FreeWinding(w);
    }
}

static void GetBrushes_r(int node, int *list, int *count, unsigned char *seen) {
    if (node < 0) {
        const dleaf_t *leaf = &dleafs[-1 - node];
        for (int i = 0; i < leaf->numleafbrushes; i++) {
            int b = dleafbrushes[leaf->firstleafbrush + i];
            if (!seen[b]) {
                seen[b] = 1;
                list[(*count)++] = b;
            }
        }
        return;
    }
    GetBrushes_r(dnodes[node].children[0], list, count, seen);
    GetBrushes_r(dnodes[node].children[1], list, count, seen);
}

void AddBrushesForRayTrace(void) {
    const dmodel_t *models = (const dmodel_t *)lumps[LUMP_MODELS].data;
    if (!lumps[LUMP_MODELS].len) return;
    int *list = xalloc(sizeof(int) * (numbrushes + 1)), count = 0;
    unsigned char *seen = xalloc(numbrushes + 1);
    GetBrushes_r(models[0].headnode, list, &count, seen);
    for (int i = 0; i < count; i++) AddBrush(list[i]);
    free(list);
    free(seen);
    /* the sky faces block rays as sky */
    for (int i = 0; i < models[0].numfaces; i++) {
        const dface_t *f = &g_pFaces[models[0].firstface + i];
        if (!(texinfo[f->texinfo].flags & SURF_SKY)) continue;
        for (int j = 2; j < f->numedges; j++)
            RT_AddTriangle(TRACE_ID_SKY, dvertexes[EdgeVertex(f, 0)].point, dvertexes[EdgeVertex(f, j - 1)].point,
                           dvertexes[EdgeVertex(f, j)].point);
    }
}
