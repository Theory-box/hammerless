/* Detail prop lighting (vrad's ComputeDetailPropLighting): one colour per grass sprite or detail model.
 *
 * Each prop's colour is the strongest it could get from the direct lights that see its cluster (each
 * light's full amount at the prop's centre, facing the prop's up) plus the ambient light from 162 rays
 * (the lightmap colours they reach, as for leaf ambient, averaged). It is written into the prop's record
 * in game lump 'dprp'; light styles other than 0 go to 'dplh' (HDR) with half the amount, and the LDR
 * list 'dplt' is kept as it was. */
#include "hlvrad.h"

#define DETAIL_PROP_TYPE_MODEL 0
#define DETAIL_PROP_TYPE_SPRITE 1
#define DETAIL_RECORD 52            /* DetailObjectLump_t (version 4) */
#define MAX_LIGHTSTYLES 64
#define VERTEXNORMAL_CONE_INNER_ANGLE (7.275 * 3.14159265358979323846 / 180.0)
#define COORD_EXTENT (2 * 16384)

float GatherSampleLightAtPoint(const directlight_t *dl, const vec3_t pos, const vec3_t normal);
void CalcRayAmbientLighting(const vec3_t start, const vec3_t end, float tanTheta, vec3_t *colors, int nstyles);
void AmbientSetup(void);
void VectorToColorRGBExp32(const vec3_t v, unsigned char *c);

static vec3_t *modelCenter, *spriteCenter;

/* mathlib's AngleVectors: float radians, the x87's sine and cosine rounded to float, its grouping */
static void AngleVectors(const float angles[3], vec3_t forward, vec3_t right, vec3_t up) {
    const float d2r = (float)(3.14159265358979323846 / 180.0);
    float y = angles[1] * d2r, p = angles[0] * d2r, r = angles[2] * d2r;
    float sy = (float)sin(y), cy = (float)cos(y), sp = (float)sin(p), cp = (float)cos(p), sr = (float)sin(r),
          cr = (float)cos(r);
    forward[0] = cp * cy, forward[1] = cp * sy, forward[2] = -sp;
    float srsp = sr * sp, crsp = cr * sp;
    right[0] = cr * sy - srsp * cy;
    right[1] = cr * -1.0f * cy - srsp * sy;
    right[2] = sr * -1.0f * cp;
    up[0] = crsp * cy + sr * sy;
    up[1] = crsp * sy - sr * cy;
    up[2] = cr * cp;
}

/* the model's hull centre, from its .mdl in -modeldir (studiohdr_t's hull_min/hull_max); 0 without it */
static void ModelCenter(const char *name, vec3_t c) {
    VectorClear(c);
    if (!g_modeldir) return;
    char path[1024];
    snprintf(path, sizeof(path), "%s/%s", g_modeldir, name);
    for (char *q = path; *q; q++)
        if (*q == '\\') *q = '/';
    FILE *f = fopen(path, "rb");
    if (!f) return;
    unsigned char h[128];
    if (fread(h, 1, sizeof(h), f) == sizeof(h) && !memcmp(h, "IDST", 4)) {
        float mins[3], maxs[3];
        memcpy(mins, h + 104, 12), memcpy(maxs, h + 116, 12);
        for (int k = 0; k < 3; k++) c[k] = (mins[k] + maxs[k]) * 0.5f;
    }
    fclose(f);
}

static int WorldCenter(const unsigned char *prop, vec3_t center, vec3_t normal) {
    float origin[3], angles[3], scale;
    unsigned short model;
    memcpy(origin, prop, 12), memcpy(angles, prop + 12, 12), memcpy(&model, prop + 24, 2), memcpy(&scale, prop + 48, 4);
    vec3_t forward, right;
    AngleVectors(angles, forward, right, normal);
    VectorCopy(origin, center);
    vec3_t off;
    switch (prop[44]) {
    case DETAIL_PROP_TYPE_MODEL: VectorCopy(modelCenter[model], off); break;
    case DETAIL_PROP_TYPE_SPRITE:
        for (int k = 0; k < 3; k++) off[k] = spriteCenter[model][k] * scale;
        break;
    default: return 1;
    }
    for (int k = 0; k < 3; k++) center[k] = forward[k] * off[0] + center[k];
    float ny = -off[1];
    for (int k = 0; k < 3; k++) center[k] = right[k] * ny + center[k];
    for (int k = 0; k < 3; k++) center[k] = normal[k] * off[2] + center[k];
    return 1;
}

static int IsValid(const vec3_t v) {
    for (int k = 0; k < 3; k++)
        if (!isfinite(v[k])) return 0;
    return 1;
}

static void MaxDirectLighting(const vec3_t origin, const vec3_t normal, vec3_t *maxcolor) {
    int cluster = ClusterFromPoint(origin);
    for (int s = 0; s < MAX_LIGHTSTYLES; s++) VectorClear(maxcolor[s]);
    for (directlight_t *dl = activelights; dl; dl = dl->next) {
        if (dl->light.type == emit_skyambient) continue;
        if (!PVSCheck(dl->pvs, cluster)) continue;
        float f = GatherSampleLightAtPoint(dl, origin, normal);
        for (int k = 0; k < 3; k++) maxcolor[dl->light.style][k] = dl->light.intensity[k] * f + maxcolor[dl->light.style][k];
    }
}

static void AmbientLighting(const vec3_t origin, vec3_t *color) {
    for (int s = 0; s < MAX_LIGHTSTYLES; s++) VectorClear(color[s]);
    float tanTheta = (float)tan(VERTEXNORMAL_CONE_INNER_ANGLE);
    for (int i = 0; i < NUMVERTEXNORMALS; i++) {
        vec3_t end;
        for (int k = 0; k < 3; k++) end[k] = g_anorms[i][k] * (float)(COORD_EXTENT * 1.74) + origin[k];
        CalcRayAmbientLighting(origin, end, tanTheta, color, MAX_LIGHTSTYLES);
    }
    const float scale = 255.0f / (float)NUMVERTEXNORMALS;
    for (int s = 0; s < MAX_LIGHTSTYLES; s++) VectorScale(color[s], scale, color[s]);
}

void ComputeDetailPropLighting(void) {
    int len;
    const unsigned char *g = GameLump(0x64707270 /* 'dprp' */, &len);
    if (!g || len < 4 || GameLumpVersion(0x64707270) != 4) return;
    /* (a copy: the lighting is written into it, then it replaces the lump) */
    unsigned char *d = xalloc(len + 1);
    memcpy(d, g, len);
    int o = 0, n;
    memcpy(&n, d, 4), o += 4;
    modelCenter = xalloc(sizeof(vec3_t) * (n + 1));
    for (int i = 0; i < n; i++, o += 128) {
        char name[129];
        memcpy(name, d + o, 128), name[128] = 0;
        ModelCenter(name, modelCenter[i]);
    }
    memcpy(&n, d + o, 4), o += 4;
    spriteCenter = xalloc(sizeof(vec3_t) * (n + 1));
    for (int i = 0; i < n; i++, o += 32) {
        float ul[2], lr[2];
        memcpy(ul, d + o, 8), memcpy(lr, d + o + 8, 8);
        /* (x out of the front, y right, z up) */
        spriteCenter[i][0] = 0.0f;
        spriteCenter[i][1] = (lr[0] + ul[0]) * 0.5f;
        spriteCenter[i][2] = (lr[1] + ul[1]) * 0.5f;
    }
    int count;
    memcpy(&count, d + o, 4), o += 4;
    if (count <= 0) {
        free(d);
        return;
    }
    AmbientSetup();
    /* the LDR styles stay as they were; the HDR ones are made again */
    int ldrlen = 0;
    const unsigned char *ldr = GameLumpVersion(0x64706c74) == 0 ? GameLump(0x64706c74 /* 'dplt' */, &ldrlen) : NULL;
    int ldrcount = 0;
    if (ldr && ldrlen >= 4) memcpy(&ldrcount, ldr, 4);
    unsigned char *ldrcopy = xalloc(4 + 5 * ldrcount + 1);
    memcpy(ldrcopy, &ldrcount, 4);
    if (ldrcount) memcpy(ldrcopy + 4, ldr + 4, 5 * ldrcount);
    int nstyles = 0, cap = 64;
    unsigned char *styles = xalloc(4 + 5 * cap);
    Msg("Computing detail prop lighting : %d props\n", count);
    vec3_t direct[MAX_LIGHTSTYLES], amb[MAX_LIGHTSTYLES];
    for (int i = 0; i < count; i++) {
        unsigned char *prop = d + o + DETAIL_RECORD * i;
        vec3_t origin, normal;
        WorldCenter(prop, origin, normal);
        if (!IsValid(origin) || !IsValid(normal)) {
            Msg("WARNING: Bogus detail props encountered!\n");
            for (int s = 0; s < MAX_LIGHTSTYLES; s++)        /* (a debug colour) */
                for (int k = 0; k < 3; k++) direct[s][k] = amb[s][k] = k == 0 ? 1.0f : 0.0f;
        } else {
            MaxDirectLighting(origin, normal, direct);
            AmbientLighting(origin, amb);
        }
        vec3_t total;
        VectorAdd(amb[0], direct[0], total);
        VectorToColorRGBExp32(total, prop + 28);
        int has = 0;
        prop[36] = 0;
        for (int s = 1; s < MAX_LIGHTSTYLES; s++) {
            for (int k = 0; k < 3; k++) total[k] = (direct[s][k] + amb[s][k]) * 0.5f;
            if (total[0] == 0.0f && total[1] == 0.0f && total[2] == 0.0f) continue;
            if (!has) {
                memcpy(prop + 32, &nstyles, 4);
                has = 1;
            }
            if (nstyles == cap) styles = realloc(styles, 4 + 5 * (cap *= 2));
            VectorToColorRGBExp32(total, styles + 4 + 5 * nstyles);
            styles[4 + 5 * nstyles + 4] = (unsigned char)s;
            nstyles++;
            prop[36]++;
        }
    }
    memcpy(styles, &nstyles, 4);
    SetGameLump(0x64707270, 4, d, len);
    SetGameLump(0x64706c74, 0, ldrcopy, 4 + 5 * ldrcount);
    SetGameLump(0x64706c68, 0, styles, 4 + 5 * nstyles);
    free(d), free(ldrcopy), free(styles);
}
