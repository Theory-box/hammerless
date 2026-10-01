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
typedef struct {
    Side *sides; Brush *brushes; int nb; double cell; int cx0, cy0, w, h;
    int *cs, *cc, *ci; int physics, used, rays;   /* rays: 0 = boxes only (a custom ray test lets lines through) */
} WSlot;
#define MAX_SLOTS 1024
static WSlot SL[MAX_SLOTS];

static void slot_free(WSlot *s) {
    free(s->sides); free(s->brushes); free(s->cs); free(s->cc); free(s->ci);
    memset(s, 0, sizeof *s);
}
/* move the loaded world (hl_world) into a slot */
EXPORT int hl_world_stash(int slot, int physics, int rays) {
    if (slot < 0 || slot >= MAX_SLOTS) return 0;
    slot_free(&SL[slot]);
    WSlot *s = &SL[slot];
    s->sides = g_sides; s->brushes = g_brushes; s->nb = g_nbrushes; s->cell = g_cell;
    s->cx0 = g_cx0; s->cy0 = g_cy0; s->w = g_w; s->h = g_h; s->cs = g_cell_start; s->cc = g_cell_count; s->ci = g_cell_ids;
    s->physics = physics; s->rays = rays; s->used = 1;
    g_sides = NULL; g_brushes = NULL; g_nbrushes = 0; g_cell_start = g_cell_count = g_cell_ids = NULL; g_w = g_h = 0;
    g_world_gen++;
    return 1;
}
/* put a stashed world back as the loaded one */
EXPORT void hl_world_restore(int slot) {
    free(g_sides); free(g_brushes); free(g_cell_start); free(g_cell_count); free(g_cell_ids);
    WSlot *s = &SL[slot];
    g_sides = s->sides; g_brushes = s->brushes; g_nbrushes = s->nb; g_cell = s->cell;
    g_cx0 = s->cx0; g_cy0 = s->cy0; g_w = s->w; g_h = s->h; g_cell_start = s->cs; g_cell_count = s->cc; g_cell_ids = s->ci;
    memset(s, 0, sizeof *s);
    g_world_gen++;
}
EXPORT void hl_world_drop(int slot) { if (slot >= 0 && slot < MAX_SLOTS) slot_free(&SL[slot]); }

static Tr trace_slot(WSlot *s, const double *a, const double *b, const double *mn, const double *mx) {
    Side *o1 = g_sides; Brush *o2 = g_brushes; int o3 = g_nbrushes; double o4 = g_cell;
    int o5 = g_cx0, o6 = g_cy0, o7 = g_w, o8 = g_h; int *o9 = g_cell_start, *o10 = g_cell_count, *o11 = g_cell_ids;
    g_sides = s->sides; g_brushes = s->brushes; g_nbrushes = s->nb; g_cell = s->cell;
    g_cx0 = s->cx0; g_cy0 = s->cy0; g_w = s->w; g_h = s->h; g_cell_start = s->cs; g_cell_count = s->cc; g_cell_ids = s->ci;
    g_world_gen++;                       /* the brush marks belong to whichever world is loaded */
    Tr t = trace(a, b, mn, mx);
    g_sides = o1; g_brushes = o2; g_nbrushes = o3; g_cell = o4;
    g_cx0 = o5; g_cy0 = o6; g_w = o7; g_h = o8; g_cell_start = o9; g_cell_count = o10; g_cell_ids = o11;
    g_world_gen++;
    return t;
}

/* UTIL_TraceHull / UTIL_TraceLine with MASK_NAV_VISION through every slot */
static Tr vis_trace(const float *a, const float *b, const float *mn, const float *mx) {
    double A[3] = {a[0], a[1], a[2]}, B[3] = {b[0], b[1], b[2]}, MN[3] = {mn[0], mn[1], mn[2]}, MX[3] = {mx[0], mx[1], mx[2]};
    int is_ray = mn[0] == 0 && mn[1] == 0 && mn[2] == 0 && mx[0] == 0 && mx[1] == 0 && mx[2] == 0;
    Tr best = trace_slot(&SL[0], A, B, MN, MX);
    for (int k = 1; k < MAX_SLOTS; k++) {
        WSlot *s = &SL[k];
        if (!s->used || (is_ray && !s->rays)) continue;
        Tr t = trace_slot(s, A, B, MN, MX);
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
    int sb = PLS[pl]; float far[3], near[3];
    for (int i = 0; i < 3; i++) { far[i] = (sb & (1 << i)) ? lo[i] : hi[i]; near[i] = (sb & (1 << i)) ? hi[i] : lo[i]; }
    float d1 = n[0] * far[0] + n[1] * far[1] + n[2] * far[2];
    float d2 = n[0] * near[0] + n[1] * near[1] + n[2] * near[2];
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
static int ray_clear(const float *a, const float *b) { return vis_trace(a, b, ZERO3, ZERO3).fraction >= 1.0; }

static int partially_visible(const VArea *a, const float *eye) {      /* IsPartiallyVisible */
    float off = VIS_EYE;
    float ctr[3] = {a->c[0], a->c[1], a->c[2] + off};
    if (ray_clear(eye, ctr)) return 1;
    float e2c[3] = {ctr[0] - eye[0], ctr[1] - eye[1], ctr[2] - eye[2]}; vnormalize(e2c);
    for (int c = 0; c < 4; c++) {
        float corner[3]; area_corner(a, c, corner); corner[2] += off;
        float e2k[3] = {corner[0] - eye[0], corner[1] - eye[1], corner[2] - eye[2]}; vnormalize(e2k);
        if (e2k[0] * e2c[0] + e2k[1] * e2c[1] + e2k[2] * e2c[2] >= VIS_DOT_TOLERANCE) continue;
        float tgt[3] = {corner[0], corner[1], corner[2] + off};       /* (sic) the eye height again */
        if (ray_clear(eye, tgt)) return 1;
    }
    return 0;
}

static int compute_vis(int ti, int ai) {                              /* this->ComputeVisibility(area) */
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
    Tr tr = vis_trace(tc, tgt, mn, mx);
    float ex = (float)tr.ex, ey = (float)tr.ey;
    if (tr.fraction == 1.0 || (ex > omin[0] && ex < omax[0] && ey > omin[1] && ey < omax[1])) return VIS_COMPLETE;

    int vis = VIS_COMPLETE;
    if (partially_visible(a, tc)) vis |= VIS_POTENTIAL; else vis &= ~VIS_COMPLETE;
    float e2c[3] = {t->c[0] - a->c[0], t->c[1] - a->c[1], t->c[2] - a->c[2]}; vnormalize(e2c);
    float margin = VIS_STEP / 2.0f, sx = t->se[0] - t->nw[0], sy = t->se[1] - t->nw[1];
    for (float yy = margin; yy <= sy - margin; yy += VIS_STEP) {
        for (float xx = margin; xx <= sx - margin; xx += VIS_STEP) {
            if (vis == VIS_POTENTIAL) return VIS_POTENTIAL;
            float tp[3] = {t->nw[0] + xx, t->nw[1] + yy, 0};
            tp[2] = area_z(t, tp[0], tp[1]) + VIS_EYE;
            if (dist_sq > 1000.0f * 1000.0f) {
                float e2k[3] = {tp[0] - tc[0], tp[1] - tc[1], tp[2] - tc[2]}; vnormalize(e2k);
                if (e2k[0] * e2c[0] + e2k[1] * e2c[1] + e2k[2] * e2c[2] >= VIS_DOT_TOLERANCE) continue;
            }
            if (partially_visible(a, tp)) vis |= VIS_POTENTIAL; else vis &= ~VIS_COMPLETE;
        }
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

/* ComputeVisibilityToMesh for every area, in list order. radius: nav_max_view_distance (0: 1500). */
EXPORT int hl_vis_run(float radius) {
    if (VL) { for (int i = 0; i < NVA; i++) il_free(&VL[i]); free(VL); }
    VL = calloc(NVA + 1, sizeof(IL));
    if (radius <= 0.0f) radius = 1500.0f;
    float r2 = radius * radius;
    unsigned char *pvs = malloc(ROWB + 1);
    for (int i = 0; i < NVA; i++) {
        area_pvs(i, pvs);
        il_push(&VL[i], (i << 2) | VIS_COMPLETE);
        for (int j = i + 1; j < NVA; j++) {
            float dx = VA[j].c[0] - VA[i].c[0], dy = VA[j].c[1] - VA[i].c[1], dz = VA[j].c[2] - VA[i].c[2];
            if (!(dx * dx + dy * dy + dz * dz <= r2)) continue;
            float lo[3], hi[3]; area_eye_box(j, lo, hi);
            int outside = !pvs_box_in(lo, hi, pvs);
            int o2t = outside ? VIS_NOT : compute_vis(i, j), t2o = VIS_NOT;
            if (!outside) t2o = compute_vis(j, i);          /* L4D2: always when inside the PVS (measured) */
            if (!o2t && t2o) o2t = VIS_POTENTIAL;
            if (!t2o && o2t) t2o = VIS_POTENTIAL;
            if (t2o) il_push(&VL[i], (j << 2) | t2o);
            if (o2t) il_push(&VL[j], (i << 2) | o2t);
        }
    }
    free(pvs);
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
EXPORT int hl_vis_compute(int ti, int ai) { return compute_vis(ti, ai); }   /* one direction (tests) */
