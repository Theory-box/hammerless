/* Overlays (info_overlay): decals projected onto chosen brush sides. vbsp keeps the entity's
 * numbers, ties each overlay to the faces its sides become (faces with different overlays don't
 * merge), gives it a texinfo of its own and writes lumps 45 (overlays), 60 (fade distances) and
 * 61 (system levels, all zero). A named overlay stays as an info_overlay_accessor entity. */
#include "hlvbsp.h"

#define MAX_MAP_OVERLAYS 512
#define OVERLAY_BSP_FACE_COUNT 64
#define OVERLAY_NUM_RENDER_ORDERS 4
#define OVERLAY_RENDER_ORDER_SHIFT 14

typedef struct {
    int id;
    float u[2], v[2];
    float fademin_sq, fademax_sq;
    vec3_t origin, uvpoints[4], basis[3];
    int renderorder;
    char material[256];
    int *sides, nsides;
    int faces[OVERLAY_BSP_FACE_COUNT + 1], nfaces;
    int texinfo;
} mapoverlay_t;

static mapoverlay_t *overlays;
static int numoverlays;

static float FloatKey(const entity_t *e, const char *key) { return (float)atof(ValueForKey(e, key)); }

/* The overlay's data; returns its id when it has a name (it's kept for the game to find), else -1. */
int Overlay_FromEntity(entity_t *e) {
    if (numoverlays % 64 == 0) overlays = realloc(overlays, sizeof(mapoverlay_t) * (numoverlays + 64));
    mapoverlay_t *o = &overlays[numoverlays];
    memset(o, 0, sizeof(*o));
    o->id = numoverlays++;
    int accessor = ValueForKey(e, "targetname")[0] ? o->id : -1;
    o->u[0] = FloatKey(e, "StartU");
    o->u[1] = FloatKey(e, "EndU");
    o->v[0] = FloatKey(e, "StartV");
    o->v[1] = FloatKey(e, "EndV");
    o->fademin_sq = FloatKey(e, "fademindist");
    if (o->fademin_sq > 0) o->fademin_sq *= o->fademin_sq;
    o->fademax_sq = FloatKey(e, "fademaxdist");
    if (o->fademax_sq > 0) o->fademax_sq *= o->fademax_sq;
    GetVectorForKey(e, "BasisOrigin", o->origin);
    o->renderorder = atoi(ValueForKey(e, "RenderOrder"));
    if (o->renderorder < 0 || o->renderorder >= OVERLAY_NUM_RENDER_ORDERS)
        Error("Overlay (%s) at %f %f %f has invalid render order (%d).\n", ValueForKey(e, "material"), o->origin[0],
              o->origin[1], o->origin[2], o->renderorder);
    char key[8];
    for (int i = 0; i < 4; i++) {
        sprintf(key, "uv%d", i);
        GetVectorForKey(e, key, o->uvpoints[i]);
    }
    GetVectorForKey(e, "BasisU", o->basis[0]);
    GetVectorForKey(e, "BasisV", o->basis[1]);
    GetVectorForKey(e, "BasisNormal", o->basis[2]);
    const char *mat = ValueForKey(e, "material");
    if (strlen(mat) >= sizeof(o->material))
        Error("Overlay Material Name (%s) too long! > OVERLAY_MAP_STRLEN (%d)", mat, (int)sizeof(o->material));
    strcpy(o->material, mat);
    /* the side ids */
    const char *s = ValueForKey(e, "sides");
    char *list = copystring(s), *tok = strtok(list, " ");
    while (tok) {
        int id;
        if (sscanf(tok, "%d", &id) == 1) {
            o->sides = realloc(o->sides, sizeof(int) * (o->nsides + 1));
            o->sides[o->nsides++] = id;
        }
        tok = strtok(NULL, " ");
    }
    free(list);
    return accessor;
}

static int has(const int *list, int n, int v) {
    for (int i = 0; i < n; i++)
        if (list[i] == v) return 1;
    return 0;
}

/* After the map is read: each side learns which overlays sit on it (the first side with the id). */
void Overlay_UpdateSideLists(void) {
    for (int i = 0; i < numoverlays; i++) {
        mapoverlay_t *o = &overlays[i];
        for (int k = 0; k < o->nsides; k++) {
            side_t *side = NULL;
            for (int j = 0; j < nummapbrushsides && !side; j++)
                if (brushsides[j].id == o->sides[k]) side = &brushsides[j];
            if (!side || has(side->overlays, side->noverlays, o->id)) continue;
            side->overlays = realloc(side->overlays, sizeof(int) * (side->noverlays + 1));
            side->overlays[side->noverlays++] = o->id;
        }
    }
}

/* Faces may merge only when their sides carry the same overlays. */
int OverlaysAreEqual(const side_t *a, const side_t *b) {
    if (a->noverlays != b->noverlays) return 0;
    for (int i = 0; i < a->noverlays; i++)
        if (!has(b->overlays, b->noverlays, a->overlays[i])) return 0;
    return 1;
}

/* A face was written: it belongs to the overlays of its side. */
void Overlay_AddFaceToLists(int face, const side_t *side) {
    for (int i = 0; i < side->noverlays; i++) {
        mapoverlay_t *o = &overlays[side->overlays[i]];
        if (has(o->faces, o->nfaces < OVERLAY_BSP_FACE_COUNT + 1 ? o->nfaces : OVERLAY_BSP_FACE_COUNT + 1, face)) continue;
        if (o->nfaces < OVERLAY_BSP_FACE_COUNT + 1) o->faces[o->nfaces] = face;
        o->nfaces++;
    }
}

int OverlayTexinfo(const char *material);

/* Each overlay's texinfo (vbsp makes them here, after the faces). */
void Overlay_EmitOverlayFaces(void) {
    if (numoverlays > MAX_MAP_OVERLAYS) Error("Too Many Overlays!\nMAX_MAP_OVERLAYS = %d", MAX_MAP_OVERLAYS);
    for (int i = 0; i < numoverlays; i++) {
        mapoverlay_t *o = &overlays[i];
        o->texinfo = OverlayTexinfo(o->material);
        if (o->nfaces >= OVERLAY_BSP_FACE_COUNT)
            Error("Overlay touching too many faces (touching %d, max %d)\nOverlay %s at %.1f %.1f %.1f", o->nfaces,
                  OVERLAY_BSP_FACE_COUNT, o->material, o->origin[0], o->origin[1], o->origin[2]);
    }
}

/* CompactTexinfos: the overlays' texinfos count as used, then are renumbered. */
void Overlay_CountTexinfos(int *refcount) {
    for (int i = 0; i < numoverlays; i++) refcount[overlays[i].texinfo]++;
}

void Overlay_RemapTexinfos(const int *newindex) {
    for (int i = 0; i < numoverlays; i++) overlays[i].texinfo = newindex[overlays[i].texinfo];
}

/* Lump 45: doverlay_t (352 bytes); 60: fade distances; 61: system levels (zero). */
unsigned char *Overlay_Lumps(int *len45, unsigned char **fades, int *len60, unsigned char **levels, int *len61) {
    unsigned char *b = xalloc(352 * numoverlays + 1);
    *fades = xalloc(8 * numoverlays + 1);
    *levels = xalloc(4 * numoverlays + 1);
    for (int i = 0; i < numoverlays; i++) {
        mapoverlay_t *o = &overlays[i];
        unsigned char *p = b + 352 * i;
        short ti = (short)o->texinfo;
        unsigned short countorder = (unsigned short)(o->nfaces | (o->renderorder << OVERLAY_RENDER_ORDER_SHIFT));
        memcpy(p, &o->id, 4);
        memcpy(p + 4, &ti, 2);
        memcpy(p + 6, &countorder, 2);
        memcpy(p + 8, o->faces, 4 * o->nfaces);
        memcpy(p + 264, o->u, 8);
        memcpy(p + 272, o->v, 8);
        vec3_t uv[4];
        memcpy(uv, o->uvpoints, sizeof(uv));
        /* BasisU rides in the unused z of the first three points; the fourth's z flags a flipped V */
        uv[0][2] = o->basis[0][0];
        uv[1][2] = o->basis[0][1];
        uv[2][2] = o->basis[0][2];
        vec3_t cross;
        CrossProduct(o->basis[2], o->basis[0], cross);
        if (DotProduct(cross, o->basis[1]) < 0.0f) uv[3][2] = 1.0f;
        memcpy(p + 280, uv, 48);
        memcpy(p + 328, o->origin, 12);
        memcpy(p + 340, o->basis[2], 12);
        memcpy(*fades + 8 * i, &o->fademin_sq, 4);
        memcpy(*fades + 8 * i + 4, &o->fademax_sq, 4);
    }
    *len45 = 352 * numoverlays;
    *len60 = 8 * numoverlays;
    *len61 = 4 * numoverlays;
    return b;
}
