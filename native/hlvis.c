/* Hammerless native nav visibility: CNavArea::ComputeVisibilityToMesh / ComputeVisToArea /
 * ComputeVisibility / IsPartiallyVisible / SetupPVS from the SDK 2013 nav code, with the L4D2
 * differences measured against the game's own analysis (see core/navvis.py). Included from
 * hlnav.c. Positions, areas and the PVS walk use the game's 32-bit floats.
 *
 * Collision: several worlds live in slots. Slot 0 is the map's sight-blocking brushes; the other
 * used slots are solid entities, each traced on its own and merged by the engine's rule (the
 * closest hit wins). A 'physics' slot (SOLID_VPHYSICS entities: movers, doors...) is stuck when a
 * trace starts inside it.
 */

/* ---------------------------------------------------------------- world slots */
static int g_stats;
typedef struct { World w; int physics, used, rays; double lo[3], hi[3]; } WSlot;   /* rays: 0 = boxes only (custom ray test) */
#define MAX_SLOTS 1024
static WSlot SL[MAX_SLOTS];
static int g_nslots;                  /* slots in use are below this */

static void slot_free(WSlot *s) {
    free(s->w.sides); free(s->w.brushes); free(s->w.cs); free(s->w.cc); free(s->w.ci);
    memset(s, 0, sizeof *s);
}
/* move the loaded world (hl_world) into a slot */
EXPORT int hl_world_stash(int slot, int physics, int rays) {
    if (slot < 0 || slot >= MAX_SLOTS) return 0;
    slot_free(&SL[slot]);
    WSlot *s = &SL[slot];
    World w = {g_sides, g_brushes, g_nbrushes, g_cell, g_cx0, g_cy0, g_w, g_h, g_cell_start, g_cell_count, g_cell_ids};
    s->w = w;
    s->physics = physics; s->rays = rays; s->used = 1;
    for (int i = 0; i < 3; i++) { s->lo[i] = 1e300; s->hi[i] = -1e300; }
    for (int k = 0; k < s->w.nb; k++)
        for (int i = 0; i < 3; i++) {
            if (s->w.brushes[k].b[i] < s->lo[i]) s->lo[i] = s->w.brushes[k].b[i];
            if (s->w.brushes[k].b[3 + i] > s->hi[i]) s->hi[i] = s->w.brushes[k].b[3 + i];
        }
    if (slot + 1 > g_nslots) g_nslots = slot + 1;
    g_sides = NULL; g_brushes = NULL; g_nbrushes = 0; g_cell_start = g_cell_count = g_cell_ids = NULL; g_w = g_h = 0;
    g_world_gen++;
    return 1;
}
/* put a stashed world back as the loaded one */
EXPORT void hl_world_restore(int slot) {
    free(g_sides); free(g_brushes); free(g_cell_start); free(g_cell_count); free(g_cell_ids);
    WSlot *s = &SL[slot];
    g_sides = s->w.sides; g_brushes = s->w.brushes; g_nbrushes = s->w.nb; g_cell = s->w.cell;
    g_cx0 = s->w.cx0; g_cy0 = s->w.cy0; g_w = s->w.w; g_h = s->w.h; g_cell_start = s->w.cs; g_cell_count = s->w.cc; g_cell_ids = s->w.ci;
    memset(s, 0, sizeof *s);
    g_world_gen++;
}
EXPORT void hl_world_drop(int slot) {
    if (slot >= 0 && slot < MAX_SLOTS) slot_free(&SL[slot]);
    while (g_nslots > 0 && !SL[g_nslots - 1].used) g_nslots--;
}

/* UTIL_TraceHull / UTIL_TraceLine with MASK_NAV_VISION through every slot */
static volatile LONG64 g_st_rays, g_st_hulls, g_st_cands, g_st_slots, g_st_pvsms;
static Tr vis_trace(Scratch *S, const float *a, const float *b, const float *mn, const float *mx) {
    double A[3] = {a[0], a[1], a[2]}, B[3] = {b[0], b[1], b[2]}, MN[3] = {mn[0], mn[1], mn[2]}, MX[3] = {mx[0], mx[1], mx[2]};
    int is_ray = mn[0] == 0 && mn[1] == 0 && mn[2] == 0 && mx[0] == 0 && mx[1] == 0 && mx[2] == 0;
    if (g_stats) { if (is_ray) InterlockedIncrement64(&g_st_rays); else InterlockedIncrement64(&g_st_hulls); }
    Tr best = trace_w(&SL[0].w, S, NULL, A, B, MN, MX);
    if (g_stats) InterlockedAdd64(&g_st_cands, S->last_n);
    double lo[3], hi[3];                 /* the swept box, with trace_w's 1-unit candidate margin */
    for (int i = 0; i < 3; i++) {
        lo[i] = (A[i] < B[i] ? A[i] : B[i]) + MN[i] - 1; hi[i] = (A[i] > B[i] ? A[i] : B[i]) + MX[i] + 1;
    }
    for (int k = 1; k < g_nslots; k++) {
        WSlot *s = &SL[k];
        if (!s->used || (is_ray && !s->rays)) continue;
        if (s->lo[0] > hi[0] || s->hi[0] < lo[0] || s->lo[1] > hi[1] || s->hi[1] < lo[1] || s->lo[2] > hi[2] || s->hi[2] < lo[2])
            continue;                    /* trace_w would find no candidate brush: no hit */
        if (g_stats) InterlockedIncrement64(&g_st_slots);
        Tr t = trace_w(&s->w, S, NULL, A, B, MN, MX);
        if (s->physics && t.startsolid) { t.fraction = 0.0; t.ex = A[0]; t.ey = A[1]; t.ez = A[2]; }
        if (t.fraction < best.fraction) best = t;
    }
    return best;
}

/* ---------------------------------------------------------------- the compiled map's PVS */
static float *PL; static int *PLT, *PLS, NPL;          /* planes: nx ny nz dist; type; signbits */
static int *ND, NND, *LC, NLF, HEAD;                   /* nodes: plane front back; leaf clusters */
static unsigned char *ROWS; static int ROWB, NCL;

EXPORT void hl_pvs_load(int nplanes, const float *planes4, const int *types, int nnodes, const int *nodes3,
                        int nleafs, const int *clusters, int head, int nclusters, int rowbytes, const unsigned char *rows) {
    free(PL); free(PLT); free(PLS); free(ND); free(LC); free(ROWS);
    NPL = nplanes; PL = malloc(sizeof(float) * 4 * (nplanes + 1)); PLT = malloc(sizeof(int) * (nplanes + 1)); PLS = malloc(sizeof(int) * (nplanes + 1));
    memcpy(PL, planes4, sizeof(float) * 4 * nplanes); memcpy(PLT, types, sizeof(int) * nplanes);
    for (int i = 0; i < nplanes; i++)
        PLS[i] = (PL[4 * i] < 0 ? 1 : 0) | (PL[4 * i + 1] < 0 ? 2 : 0) | (PL[4 * i + 2] < 0 ? 4 : 0);
    NND = nnodes; ND = malloc(sizeof(int) * 3 * (nnodes + 1)); memcpy(ND, nodes3, sizeof(int) * 3 * nnodes);
    NLF = nleafs; LC = malloc(sizeof(int) * (nleafs + 1)); memcpy(LC, clusters, sizeof(int) * nleafs);
    HEAD = head; NCL = nclusters; ROWB = rowbytes;
    ROWS = malloc((size_t)rowbytes * (nclusters + 1)); memcpy(ROWS, rows, (size_t)rowbytes * nclusters);
}
static int pvs_point_leaf(const float *p) {              /* CM_PointLeafnum */
    int num = HEAD;
    while (num >= 0) {
        int pl = ND[3 * num]; const float *n = PL + 4 * pl; float d;
        if (PLT[pl] < 3) d = p[PLT[pl]] - n[3];
        else d = n[0] * p[0] + n[1] * p[1] + n[2] * p[2] - n[3];
        num = d < 0 ? ND[3 * num + 2] : ND[3 * num + 1];
    }
    return -1 - num;
}
static int pvs_box_side(const float *lo, const float *hi, int pl) {   /* BOX_ON_PLANE_SIDE */
    const float *n = PL + 4 * pl; int t = PLT[pl];
    if (t < 3) {
        if (n[3] <= lo[t]) return 1;
        if (n[3] >= hi[t]) return 2;
        return 3;
    }
    int sb = PLS[pl]; float farp[3], nearp[3];
    for (int i = 0; i < 3; i++) { farp[i] = (sb & (1 << i)) ? lo[i] : hi[i]; nearp[i] = (sb & (1 << i)) ? hi[i] : lo[i]; }
    float d1 = n[0] * farp[0] + n[1] * farp[1] + n[2] * farp[2];
    float d2 = n[0] * nearp[0] + n[1] * nearp[1] + n[2] * nearp[2];
    int sides = 0;
    if (d1 >= n[3]) sides = 1;
    if (d2 < n[3]) sides |= 2;
    return sides;
}
static int pvs_box_in(const float *lo, const float *hi, const unsigned char *pvs) {   /* CheckBoxInPVS */
    int stack[1024], sp = 0; stack[sp++] = HEAD;
    while (sp) {
        int num = stack[--sp];
        for (;;) {
            if (num < 0) {
                int c = LC[-1 - num];
                if (c >= 0 && (pvs[c >> 3] & (1 << (c & 7)))) return 1;
                break;
            }
            int s = pvs_box_side(lo, hi, ND[3 * num]);
            if (s == 1) num = ND[3 * num + 1];
            else if (s == 2) num = ND[3 * num + 2];
            else { if (sp < 1024) stack[sp++] = ND[3 * num + 2]; num = ND[3 * num + 1]; }
        }
    }
    return 0;
}
static void pvs_add_origin(unsigned char *pvs, const float *p) {   /* AddOriginToPVS */
    int c = LC[pvs_point_leaf(p)];
    if (c < 0 || c >= NCL) return;
    const unsigned char *row = ROWS + (size_t)ROWB * c;
    for (int i = 0; i < ROWB; i++) pvs[i] |= row[i];
}

/* ---------------------------------------------------------------- areas */
typedef struct { float nw[3], se[3], nez, swz, c[3], invdx, invdy; } VArea;
static VArea *VA; static int NVA;
#define VIS_EYE (0.75f * 71.0f)
#define VIS_STEP 25.0f
#define VIS_DOT_TOLERANCE 0.98f
#define VIS_NOT 0
#define VIS_POTENTIAL 1
#define VIS_COMPLETE 2

EXPORT void hl_vis_areas(int n, const float *d8) {
    free(VA); NVA = n; VA = calloc(n + 1, sizeof(VArea));
    for (int i = 0; i < n; i++) {
        VArea *a = &VA[i]; const float *d = d8 + 8 * i;
        memcpy(a->nw, d, 12); memcpy(a->se, d + 3, 12); a->nez = d[6]; a->swz = d[7];
        a->c[0] = (a->nw[0] + a->se[0]) / 2.0f; a->c[1] = (a->nw[1] + a->se[1]) / 2.0f; a->c[2] = (a->nw[2] + a->se[2]) / 2.0f;
        if ((a->se[0] - a->nw[0]) > 0.0f && (a->se[1] - a->nw[1]) > 0.0f) {
            a->invdx = 1.0f / (a->se[0] - a->nw[0]); a->invdy = 1.0f / (a->se[1] - a->nw[1]);
        } else a->invdx = a->invdy = 0.0f;
    }
}
static float area_z(const VArea *a, float x, float y) {              /* CNavArea::GetZ */
    if (a->invdx == 0.0f || a->invdy == 0.0f) return a->nez;
    float u = (x - a->nw[0]) * a->invdx, v = (y - a->nw[1]) * a->invdy;
    if (u < 0.0f) u = 0.0f; if (u - 1.0f >= 0.0f) u = 1.0f;
    if (v < 0.0f) v = 0.0f; if (v - 1.0f >= 0.0f) v = 1.0f;
    float north = a->nw[2] + u * (a->nez - a->nw[2]);
    float south = a->swz + u * (a->se[2] - a->swz);
    return north + v * (south - north);
}
static void area_corner(const VArea *a, int c, float *out) {
    switch (c) {
    case 0: out[0] = a->nw[0]; out[1] = a->nw[1]; out[2] = a->nw[2]; break;
    case 1: out[0] = a->se[0]; out[1] = a->nw[1]; out[2] = a->nez; break;
    case 2: out[0] = a->se[0]; out[1] = a->se[1]; out[2] = a->se[2]; break;
    default: out[0] = a->nw[0]; out[1] = a->se[1]; out[2] = a->swz; break;
    }
}
static void vnormalize(float *v) {                                     /* VectorNormalize */
    float r = sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    float ir = 1.0f / (r + 1.192092896e-07f);
    v[0] *= ir; v[1] *= ir; v[2] *= ir;
}
static const float ZERO3[3] = {0, 0, 0};
/* vis_trace(...).fraction >= 1 for a line, answered sooner: the world first (blocked there means
 * blocked), then each entity the line can reach; anything unusual falls back to the full trace. */
static int ray_clear(Scratch *S, const float *a, const float *b) {
    double A[3] = {a[0], a[1], a[2]}, B[3] = {b[0], b[1], b[2]};
    int r = line_clear_w(&SL[0].w, S, A, B, NULL);
    if (r < 0) return vis_trace(S, a, b, ZERO3, ZERO3).fraction >= 1.0;
    if (r == 0) return 0;
    double lo[3], hi[3];
    for (int i = 0; i < 3; i++) { lo[i] = (A[i] < B[i] ? A[i] : B[i]) - 1; hi[i] = (A[i] > B[i] ? A[i] : B[i]) + 1; }
    for (int k = 1; k < g_nslots; k++) {
        WSlot *s = &SL[k];
        if (!s->used || !s->rays) continue;
        if (s->lo[0] > hi[0] || s->hi[0] < lo[0] || s->lo[1] > hi[1] || s->hi[1] < lo[1] || s->lo[2] > hi[2] || s->hi[2] < lo[2]) continue;
        int inside = 0;
        int e = line_clear_w(&s->w, S, A, B, &inside);
        if (e == 0) return 0;
        if (e < 0) {
            if (s->physics) return 0;                    /* starts inside a physics entity: stuck */
            return vis_trace(S, a, b, ZERO3, ZERO3).fraction >= 1.0;
        }
    }
    return 1;
}

static int partially_visible(Scratch *S, const VArea *a, const float *eye) {      /* IsPartiallyVisible */
    float off = VIS_EYE;
    float ctr[3] = {a->c[0], a->c[1], a->c[2] + off};
    if (ray_clear(S, eye, ctr)) return 1;
    float e2c[3] = {ctr[0] - eye[0], ctr[1] - eye[1], ctr[2] - eye[2]}; vnormalize(e2c);
    for (int c = 0; c < 4; c++) {
        float corner[3]; area_corner(a, c, corner); corner[2] += off;
        float e2k[3] = {corner[0] - eye[0], corner[1] - eye[1], corner[2] - eye[2]}; vnormalize(e2k);
        if (e2k[0] * e2c[0] + e2k[1] * e2c[1] + e2k[2] * e2c[2] >= VIS_DOT_TOLERANCE) continue;
        float tgt[3] = {corner[0], corner[1], corner[2] + off};       /* (sic) the eye height again */
        if (ray_clear(S, eye, tgt)) return 1;
    }
    return 0;
}

static int compute_vis(Scratch *S, int ti, int ai) {                              /* this->ComputeVisibility(area) */
    const VArea *t = &VA[ti], *a = &VA[ai];
    float dx = a->c[0] - t->c[0], dy = a->c[1] - t->c[1], dz = a->c[2] - t->c[2];
    float dist_sq = dx * dx + dy * dy + dz * dz;
    float k[4][3]; for (int c = 0; c < 4; c++) { area_corner(t, c, k[c]); k[c][2] += VIS_EYE; }
    float tc[3] = {t->c[0], t->c[1], t->c[2] + VIS_EYE};
    float mn[3] = {k[0][0], k[0][1], 0}, mx[3] = {k[2][0], k[2][1], 0};
    mn[2] = fminf(fminf(fminf(k[0][2], k[1][2]), k[2][2]), k[3][2]);
    mx[2] = fmaxf(fmaxf(fmaxf(k[0][2], k[1][2]), k[2][2]), k[3][2]) + 0.1f;
    for (int i = 0; i < 3; i++) { mn[i] -= tc[i]; mx[i] -= tc[i]; }
    float omin[3] = {a->nw[0], a->nw[1], a->nw[2]}, omax[3] = {a->se[0], a->se[1], a->se[2]};
    float tgt[3];
    for (int i = 0; i < 3; i++) tgt[i] = tc[i] < omin[i] ? omin[i] : (tc[i] > omax[i] ? omax[i] : tc[i]);
    tgt[2] = area_z(a, tgt[0], tgt[1]) + VIS_EYE;
    Tr tr = vis_trace(S, tc, tgt, mn, mx);
    float ex = (float)tr.ex, ey = (float)tr.ey;
    if (tr.fraction == 1.0 || (ex > omin[0] && ex < omax[0] && ey > omin[1] && ey < omax[1])) return VIS_COMPLETE;

    int vis = VIS_COMPLETE;
    if (partially_visible(S, a, tc)) vis |= VIS_POTENTIAL; else vis &= ~VIS_COMPLETE;
    float e2c[3] = {t->c[0] - a->c[0], t->c[1] - a->c[1], t->c[2] - a->c[2]}; vnormalize(e2c);
    /* The game walks the sample grid row by row and stops once vis is POTENTIALLY_VISIBLE (one sample
     * seen, one not). The answer only depends on whether any / all of the tested samples are seen,
     * so the same samples are tested here in a spread-out (bit-reversed) order, which finds a seen
     * and an unseen one much sooner. Same samples, same skips, same result. */
    float margin = VIS_STEP / 2.0f, sx = t->se[0] - t->nw[0], sy = t->se[1] - t->nw[1];
    int ns = 0;
    for (float yy = margin; yy <= sy - margin; yy += VIS_STEP)
        for (float xx = margin; xx <= sx - margin; xx += VIS_STEP) {
            float tp[3] = {t->nw[0] + xx, t->nw[1] + yy, 0};
            tp[2] = area_z(t, tp[0], tp[1]) + VIS_EYE;
            if (dist_sq > 1000.0f * 1000.0f) {
                float e2k[3] = {tp[0] - tc[0], tp[1] - tc[1], tp[2] - tc[2]}; vnormalize(e2k);
                if (e2k[0] * e2c[0] + e2k[1] * e2c[1] + e2k[2] * e2c[2] >= VIS_DOT_TOLERANCE) continue;
            }
            if (ns == S->samplecap) { S->samplecap = S->samplecap ? S->samplecap * 2 : 1024; S->samples = realloc(S->samples, sizeof(float) * 3 * S->samplecap); }
            memcpy(S->samples + 3 * ns, tp, sizeof tp); ns++;
        }
    int bits = 0; while ((1 << bits) < ns) bits++;
    for (int k = 0; k < (1 << bits); k++) {
        int idx = 0; for (int b = 0; b < bits; b++) if (k & (1 << b)) idx |= 1 << (bits - 1 - b);
        if (idx >= ns) continue;
        if (vis == VIS_POTENTIAL) return VIS_POTENTIAL;
        if (partially_visible(S, a, S->samples + 3 * idx)) vis |= VIS_POTENTIAL; else vis &= ~VIS_COMPLETE;
    }
    return vis;
}

static void area_pvs(int i, unsigned char *pvs) {                    /* SetupPVS */
    const VArea *a = &VA[i];
    memset(pvs, 0, ROWB);
    float margin = VIS_STEP / 2.0f, sx = a->se[0] - a->nw[0], sy = a->se[1] - a->nw[1];
    for (float yy = margin; yy <= sy - margin; yy += VIS_STEP)
        for (float xx = margin; xx <= sx - margin; xx += VIS_STEP) {
            float p[3] = {a->nw[0] + xx, a->nw[1] + yy, 0};
            p[2] = area_z(a, p[0], p[1]) + VIS_EYE;
            pvs_add_origin(pvs, p);
        }
}
static void area_eye_box(int i, float *lo, float *hi) {
    const VArea *a = &VA[i]; float p[5][3];
    for (int c = 0; c < 4; c++) { area_corner(a, c, p[c]); p[c][2] += VIS_EYE; }
    p[4][0] = a->c[0]; p[4][1] = a->c[1]; p[4][2] = a->c[2] + VIS_EYE;
    for (int k = 0; k < 3; k++) { lo[k] = hi[k] = p[4][k]; for (int c = 0; c < 4; c++) { if (p[c][k] < lo[k]) lo[k] = p[c][k]; if (p[c][k] > hi[k]) hi[k] = p[c][k]; } }
}

static IL *VL;                                                        /* per area: (other << 2) | attributes */
static IL *VR;                                                        /* per area i: j, (o2t << 2) | t2o for j > i */
static volatile LONG g_vis_next;
static float g_vis_r2;
static int g_vis_threads = 0;                                         /* 0: one per core */

static void vis_area(Scratch *S, unsigned char *pvs, int i) {
    area_pvs(i, pvs);
    for (int j = i + 1; j < NVA; j++) {
        float dx = VA[j].c[0] - VA[i].c[0], dy = VA[j].c[1] - VA[i].c[1], dz = VA[j].c[2] - VA[i].c[2];
        if (!(dx * dx + dy * dy + dz * dz <= g_vis_r2)) continue;
        float lo[3], hi[3]; area_eye_box(j, lo, hi);
        if (!pvs_box_in(lo, hi, pvs)) continue;                      /* outside the PVS: neither way */
        int o2t = compute_vis(S, i, j);
        int t2o = compute_vis(S, j, i);                              /* L4D2: always when inside the PVS (measured) */
        if (!o2t && t2o) o2t = VIS_POTENTIAL;
        if (!t2o && o2t) t2o = VIS_POTENTIAL;
        if (o2t || t2o) { il_push(&VR[i], j); il_push(&VR[i], (o2t << 2) | t2o); }
    }
}
static DWORD WINAPI vis_worker(LPVOID arg) {
    (void)arg;
    Scratch S; memset(&S, 0, sizeof S);
    unsigned char *pvs = malloc(ROWB + 1);
    for (;;) {
        int i = (int)InterlockedIncrement(&g_vis_next) - 1;
        if (i >= NVA) break;
        vis_area(&S, pvs, i);
    }
    free(pvs); free(S.ids); free(S.mark); free(S.samples);
    return 0;
}
EXPORT void hl_vis_threads(int n) { g_vis_threads = n; }
EXPORT void hl_vis_stats(long long *out) { out[0] = g_st_rays; out[1] = g_st_hulls; out[2] = g_st_cands; out[3] = g_st_slots; g_st_rays = g_st_hulls = g_st_cands = g_st_slots = 0; }
EXPORT void hl_vis_stats_on(int on) { g_stats = on; }

/* ComputeVisibilityToMesh for every area, in list order. radius: nav_max_view_distance (0: 1500).
 * Each area's pairs (with the areas after it) are independent, so they run on every core; the
 * lists are then assembled in exactly the order the one-thread loop builds them. */
EXPORT int hl_vis_run(float radius) {
    if (VL) { for (int i = 0; i < NVA; i++) il_free(&VL[i]); free(VL); }
    VL = calloc(NVA + 1, sizeof(IL)); VR = calloc(NVA + 1, sizeof(IL));
    if (radius <= 0.0f) radius = 1500.0f;
    g_vis_r2 = radius * radius;
    int threads = g_vis_threads;
    if (threads <= 0) { SYSTEM_INFO si; GetSystemInfo(&si); threads = (int)si.dwNumberOfProcessors; }
    if (threads > 64) threads = 64;
    g_vis_next = 0;
    if (threads <= 1) vis_worker(NULL);
    else {
        HANDLE h[64];
        for (int t = 0; t < threads; t++) h[t] = CreateThread(NULL, 0, vis_worker, NULL, 0, NULL);
        WaitForMultipleObjects(threads, h, TRUE, INFINITE);
        for (int t = 0; t < threads; t++) CloseHandle(h[t]);
    }
    for (int i = 0; i < NVA; i++) {
        il_push(&VL[i], (i << 2) | VIS_COMPLETE);
        for (int k = 0; k < VR[i].n; k += 2) {
            int j = VR[i].v[k], o2t = VR[i].v[k + 1] >> 2, t2o = VR[i].v[k + 1] & 3;
            if (t2o) il_push(&VL[i], (j << 2) | t2o);
            if (o2t) il_push(&VL[j], (i << 2) | o2t);
        }
        il_free(&VR[i]);
    }
    free(VR); VR = NULL;
    int total = 0;
    for (int i = 0; i < NVA; i++) total += VL[i].n;
    return total;
}
EXPORT int hl_vis_get(int *counts, int *entries, int cap) {
    int k = 0;
    for (int i = 0; i < NVA; i++) {
        counts[i] = VL[i].n;
        for (int j = 0; j < VL[i].n; j++) { if (k < cap) entries[k] = VL[i].v[j]; k++; }
    }
    return k;
}
EXPORT int hl_vis_compute(int ti, int ai) { return compute_vis(&g_scr, ti, ai); }   /* one direction (tests) */
