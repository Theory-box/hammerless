/* Lights (vrad's CreateDirectLights, the Parse* functions and ExportDirectLightsToWorldLights).
 *
 * Light entities: light (point), light_spot, light_environment (the sun and its sky ambient; only the
 * first counts), light_directional (L4D2: another sun, no ambient). light_dynamic is a game entity.
 * Each light is put at the front of the active list; the world lights lump lists them in that order.
 * L4D2 adds per light _castentityshadow (default on: flag 2; suns never) and _shadoworiginoffset.
 *
 * With a sun, vrad works out again which leaves see the sky (vbsp's guess also marks solid leaves): a
 * leaf holding a sky face sees it; another non-solid leaf sees it when its cluster can see one of those
 * (3D sky wins over 2D). The sun reaches the clusters of the sky leaves. */
#include "hlvrad.h"

#define LEAF_FLAGS_SKY 0x01
#define LEAF_FLAGS_RADIAL 0x02
#define LEAF_FLAGS_SKY2D 0x04
#define EQUAL_EPSILON 0.001
#define TEST_EPSILON 0.1
#define DWL_FLAGS_CASTENTITYSHADOWS 0x2
#define PI 3.14159265358979323846

directlight_t *activelights, *gSkyLight, *gAmbient;
int numdlights;
float lightscale = 1.0f;

/* ------------------------------------------------------------------ where a point is */
static int PointInLeaf(int node, const vec3_t p) {
    if (node < 0) return -1 - node;
    const dnode_t *n = &dnodes[node];
    const dplane_t *pl = &dplanes[n->planenum];
    float dist = DotProduct(p, pl->normal) - pl->dist;
    if (dist > TEST_EPSILON) return PointInLeaf(n->children[0], p);
    if (dist < -TEST_EPSILON) return PointInLeaf(n->children[1], p);
    int test = PointInLeaf(n->children[0], p);
    if (dleafs[test].cluster != -1) return test;
    return PointInLeaf(n->children[1], p);
}

/* vrad's PointLeafnum: no tolerance, on the plane is in front */
int PointLeafnum(const vec3_t p) {
    int node = 0;
    while (node >= 0) {
        const dplane_t *pl = &dplanes[dnodes[node].planenum];
        float d = pl->type < 3 ? p[pl->type] : (pl->normal[1] * p[1] + pl->normal[0] * p[0]) + pl->normal[2] * p[2];
        d = d - pl->dist;
        node = 0.0f > d ? dnodes[node].children[1] : dnodes[node].children[0];
    }
    return -1 - node;
}
int ClusterFromPoint(const vec3_t p) { return dleafs[PointInLeaf(0, p)].cluster; }

/* ------------------------------------------------------------------ lights */
static void MergeDLightVis(directlight_t *dl, int cluster) {
    int n = VisRowBytes();
    if (!dl->pvs) {
        dl->pvs = xalloc(n + 1);
        GetClusterPVS(cluster, dl->pvs);
        return;
    }
    unsigned char *pvs = xalloc(n + 1);
    GetClusterPVS(cluster, pvs);
    for (int i = 0; i < n; i++) dl->pvs[i] |= pvs[i];
    free(pvs);
}

static directlight_t *AllocDLight(const vec3_t origin, int add) {
    directlight_t *dl = xalloc(sizeof(directlight_t));
    dl->index = numdlights++;
    VectorCopy(origin, dl->light.origin);
    dl->light.cluster = ClusterFromPoint(dl->light.origin);
    dl->pvs = xalloc(VisRowBytes() + 1);
    GetClusterPVS(dl->light.cluster, dl->pvs);
    dl->facenum = -1;
    if (add) {
        dl->next = activelights;
        activelights = dl;
    }
    return dl;
}

static void AddToActiveList(directlight_t *dl) {
    dl->next = activelights;
    activelights = dl;
}

static float FloatForKeyWithDefault(const entity_t *e, const char *key, float def) {
    for (int i = e->numpairs - 1; i >= 0; i--)
        if (!strcmp(e->keys[i], key)) return (float)atof(e->values[i]);
    return def;
}

static const char *ValueForKeyOrNull(const entity_t *e, const char *key) {
    for (int i = e->numpairs - 1; i >= 0; i--)
        if (!strcmp(e->keys[i], key)) return e->values[i];
    return NULL;
}

/* "r g b [brightness]" (or two of those, LDR then HDR) -> linear intensity */
int LightForString(const char *s, vec3_t intensity) {
    double r = 0, g = 0, b = 0, scaler = 0, rh, gh, bh, sh;
    VectorClear(intensity);
    int n = sscanf(s, "%lf %lf %lf %lf %lf %lf %lf %lf", &r, &g, &b, &scaler, &rh, &gh, &bh, &sh);
    if (n == 8) {
        if (g_bHDR) r = rh, g = gh, b = bh, scaler = sh;
        n = 4;
    }
    if (r < 0.0f || g < 0.0f || b < 0.0f || scaler < 0.0f) {
        VectorClear(intensity);
        return 0;
    }
    intensity[0] = (float)(pow(r / 255.0, 2.2) * 255);
    switch (n) {
    case 1:
        intensity[1] = intensity[2] = intensity[0];
        break;
    case 3:
    case 4:
        intensity[1] = (float)(pow(g / 255.0, 2.2) * 255);
        intensity[2] = (float)(pow(b / 255.0, 2.2) * 255);
        if (n == 4) {
            float sc = (float)(scaler / 255.0);
            VectorScale(intensity, sc, intensity);
        }
        break;
    default:
        Msg("unknown light specifier type - %s\n", s);
        return 0;
    }
    VectorScale(intensity, lightscale, intensity);
    return 1;
}

static int LightForKey(const entity_t *e, const char *key, vec3_t intensity) {
    return LightForString(ValueForKey(e, key), intensity);
}

static const entity_t *FindTargetEntity(const char *target) {
    for (int i = 0; i < num_entities; i++)
        if (!strcmp(ValueForKey(&entities[i], "targetname"), target)) return &entities[i];
    return NULL;
}

/* the direction from "angles" (or the older "angle" / "pitch"; -1 up, -2 down) */
/* (L4D2's x87 code: degrees * (1/180 as a float) * pi in double, then fsin / fcos - measured) */
static double DegToRad(float deg) { return (double)deg * (double)(1.0f / 180.0f) * PI; }

static void SetupLightNormalFromProps(const vec3_t angles, float angle, float pitch, vec3_t out) {
    if (angle == -1) {
        out[0] = out[1] = 0;
        out[2] = 1;
    } else if (angle == -2) {
        out[0] = out[1] = 0;
        out[2] = -1;
    } else {
        if (!angle) angle = angles[1];
        out[2] = 0;
        double a = DegToRad(angle);
        out[0] = (float)cos(a);
        out[1] = (float)sin(a);
    }
    if (!pitch) pitch = angles[0];
    double p = DegToRad(pitch), cp = cos(p);
    out[2] = (float)sin(p);
    out[0] = (float)(cp * out[0]);
    out[1] = (float)(cp * out[1]);
}

static void ParseLightGeneric(const entity_t *e, directlight_t *dl) {
    dl->light.style = (int)FloatForKey(e, "style");
    if (FloatForKeyWithDefault(e, "_castentityshadow", 1.0f) != 0) dl->light.flags |= DWL_FLAGS_CASTENTITYSHADOWS;
    else dl->light.flags &= ~DWL_FLAGS_CASTENTITYSHADOWS;
    VectorClear(dl->light.shadow_cast_offset);
    if (ValueForKeyOrNull(e, "_shadoworiginoffset")) GetVectorForKey(e, "_shadoworiginoffset", dl->light.shadow_cast_offset);
    if (!(g_bHDR && LightForKey(e, "_lightHDR", dl->light.intensity))) LightForKey(e, "_light", dl->light.intensity);
    const char *target = ValueForKey(e, "target");
    if (target[0]) {
        const entity_t *e2 = FindTargetEntity(target);
        if (!e2) {
            Msg("WARNING: light at (%i %i %i) has missing target\n", (int)dl->light.origin[0], (int)dl->light.origin[1],
                (int)dl->light.origin[2]);
        } else {
            vec3_t dest;
            GetVectorForKey(e2, "origin", dest);
            VectorSubtract(dest, dl->light.origin, dl->light.normal);
            VectorNormalize(dl->light.normal);
        }
    } else {
        vec3_t angles;
        GetVectorForKey(e, "angles", angles);
        SetupLightNormalFromProps(angles, FloatForKey(e, "angle"), FloatForKey(e, "pitch"), dl->light.normal);
    }
    if (g_bHDR) {
        float s = FloatForKeyWithDefault(e, "_lightscaleHDR", 1.0f);
        VectorScale(dl->light.intensity, s, dl->light.intensity);
    }
}

/* 1 / (c + b x + a x^2) through three points, as L4D2's build computes it (measured from the binary:
 * times 1/det, the c terms summed in their own order, the blend counted in double precision). */
static int SolveInverseQuadraticMonotonic(float x1, float y1, float x2, float y2, float x3, float y3, float *a, float *b,
                                          float *c) {
    float t;
#define SWAP(p, q) (t = p, p = q, q = t)
    if (x1 > x2) SWAP(x1, x2), SWAP(y1, y2);
    if (x2 > x3) SWAP(x2, x3), SWAP(y2, y3);
    if (x1 > x2) SWAP(x1, x2), SWAP(y1, y2);
#undef SWAP
    float dy31 = y3 - y1, dx31 = x3 - x1;
    float lerp = (x2 - x1) * dy31 / dx31 + y1;
    float det = (x1 - x2) * (x1 - x3) * (x2 - x3);
    for (double blend_d = 0.0; blend_d <= 1.0; blend_d += 0.05) {
        float blend = (float)blend_d;
        float tempy2 = (1.0f - blend) * y2 + lerp * blend;
        if (det == 0.0f) return 0;
        float rdet = 1.0f / det;
        *a = ((tempy2 - y1) * x3 + (y1 - y3) * x2 + (y3 - tempy2) * x1) * rdet;
        *b = ((y1 - tempy2) * (x3 * x3) + (tempy2 - y3) * (x1 * x1) + (x2 * x2) * dy31) * rdet;
        *c = ((x3 * y1 - x1 * y3) * (x2 * x2) + x1 * x3 * dx31 * tempy2 + (x1 * x1 * y3 - x3 * x3 * y1) * x2) * rdet;
        float derivative = *a * 2.0f + *b;
        if (y1 < y2 && y2 < y3) {
            if (derivative >= 0.0) return 1;
        } else if (y1 > y2 && y2 > y3) {
            if (derivative <= 0.0) return 1;
        } else return 1;
    }
    return 1;
}

static void SetLightFalloffParams(const entity_t *e, directlight_t *dl) {
    float d50 = FloatForKey(e, "_fifty_percent_distance");
    dl->m_flStartFadeDistance = 0;
    dl->m_flEndFadeDistance = -1;
    dl->m_flCapDist = 1.0e22f;
    if (d50) {
        float d0 = FloatForKey(e, "_zero_percent_distance");
        if (d0 < d50) {
            Msg("light has _fifty_percent_distance of %f but _zero_percent_distance of %f\n", d50, d0);
            d0 = 2.0 * d50;
        }
        float a = 0, b = 1, c = 0;
        if (!SolveInverseQuadraticMonotonic(0, 1.0, d50, 2.0, d0, 256.0, &a, &b, &c))
            Msg("can't solve quadratic for light %f %f\n", d50, d0);
        float v50 = c + d50 * (b + d50 * a);
        float scale = 2.0 / v50;
        a *= scale;
        b *= scale;
        c *= scale;
        dl->light.quadratic_attn = a;
        dl->light.linear_attn = b;
        dl->light.constant_attn = c;
        if (atoi(ValueForKey(e, "_hardfalloff"))) {
            dl->m_flEndFadeDistance = d0;
            dl->m_flStartFadeDistance = 0.75 * d0 + 0.25 * d50;
        } else if (fabs(a) > 0.) {
            float flMax = b / (-2.0 * a);
            if (flMax > 0.0) {
                dl->m_flCapDist = flMax;
                dl->m_flStartFadeDistance = flMax;
                dl->m_flEndFadeDistance = 10.0 * flMax;
            }
        }
    } else {
        dl->light.constant_attn = FloatForKey(e, "_constant_attn");
        dl->light.linear_attn = FloatForKey(e, "_linear_attn");
        dl->light.quadratic_attn = FloatForKey(e, "_quadratic_attn");
        dl->light.radius = FloatForKey(e, "_distance");
        if (dl->light.constant_attn < EQUAL_EPSILON) dl->light.constant_attn = 0;
        if (dl->light.linear_attn < EQUAL_EPSILON) dl->light.linear_attn = 0;
        if (dl->light.quadratic_attn < EQUAL_EPSILON) dl->light.quadratic_attn = 0;
        if (dl->light.constant_attn < EQUAL_EPSILON && dl->light.linear_attn < EQUAL_EPSILON &&
            dl->light.quadratic_attn < EQUAL_EPSILON)
            dl->light.constant_attn = 1;
        /* (brightness is given at 100 units) */
        float ratio = dl->light.constant_attn + 100 * dl->light.linear_attn + 100 * 100 * dl->light.quadratic_attn;
        if (ratio > 0) VectorScale(dl->light.intensity, ratio, dl->light.intensity);
    }
}

static void ParseLightPoint(const entity_t *e) {
    vec3_t dest;
    GetVectorForKey(e, "origin", dest);
    directlight_t *dl = AllocDLight(dest, 1);
    ParseLightGeneric(e, dl);
    dl->light.type = emit_point;
    SetLightFalloffParams(e, dl);
}

static void ParseLightSpot(const entity_t *e) {
    vec3_t dest;
    GetVectorForKey(e, "origin", dest);
    directlight_t *dl = AllocDLight(dest, 1);
    ParseLightGeneric(e, dl);
    dl->light.type = emit_spotlight;
    dl->light.stopdot = FloatForKey(e, "_inner_cone");
    if (!dl->light.stopdot) dl->light.stopdot = 10;
    dl->light.stopdot2 = FloatForKey(e, "_cone");
    if (!dl->light.stopdot2) dl->light.stopdot2 = dl->light.stopdot;
    if (dl->light.stopdot2 < dl->light.stopdot) dl->light.stopdot2 = dl->light.stopdot;
    if (dl->light.stopdot == 180 && dl->light.stopdot2 == 180) {
        /* a spot open all round is a point light */
        dl->light.stopdot = dl->light.stopdot2 = 0;
        dl->light.type = emit_point;
        dl->light.exponent = 0;
    } else {
        if (dl->light.stopdot > 90) {
            Msg("WARNING: light_spot at (%i %i %i) has inner angle larger than 90 degrees! Clamping to 90...\n",
                (int)dl->light.origin[0], (int)dl->light.origin[1], (int)dl->light.origin[2]);
            dl->light.stopdot = 90;
        }
        if (dl->light.stopdot2 > 90) {
            Msg("WARNING: light_spot at (%i %i %i) has outer angle larger than 90 degrees! Clamping to 90...\n",
                (int)dl->light.origin[0], (int)dl->light.origin[1], (int)dl->light.origin[2]);
            dl->light.stopdot2 = 90;
        }
        dl->light.stopdot2 = (float)cos(dl->light.stopdot2 / 180 * PI);
        dl->light.stopdot = (float)cos(dl->light.stopdot / 180 * PI);
        dl->light.exponent = FloatForKey(e, "_exponent");
    }
    SetLightFalloffParams(e, dl);
}

/* ------------------------------------------------------------------ the sky's leaves */
static int MapHasSky(void) {
    for (int i = 0; i < numfaces; i++)
        if (texinfo[g_pFaces[i].texinfo].flags & SURF_SKY) return 1;
    return 0;
}

static void BuildVisForLightEnvironment(directlight_t **suns, int nsuns) {
    for (int leaf = 0; leaf < numleafs; leaf++) {
        int flags = LeafFlags(leaf) & ~(LEAF_FLAGS_SKY | LEAF_FLAGS_SKY2D);
        for (int k = 0; k < dleafs[leaf].numleaffaces; k++) {
            int face = dleaffaces[dleafs[leaf].firstleafface + k];
            int tf = texinfo[g_pFaces[face].texinfo].flags;
            if (tf & SURF_SKY) {
                flags |= tf & SURF_SKY2D ? LEAF_FLAGS_SKY2D : LEAF_FLAGS_SKY;
                for (int s = 0; s < nsuns; s++) MergeDLightVis(suns[s], dleafs[leaf].cluster);
                break;
            }
        }
        SetLeafFlags(leaf, flags);
    }
    /* leaves that see a sky leaf (set afterwards, so the sky doesn't spread from leaf to leaf) */
    int bytes = (numleafs >> 3) + 1;
    unsigned char *sky3d = xalloc(bytes), *sky2d = xalloc(bytes), *pvs = xalloc(VisRowBytes() + 1);
    for (int leaf = 0; leaf < numleafs; leaf++) {
        if (LeafFlags(leaf) & LEAF_FLAGS_SKY) continue;
        if (dleafs[leaf].contents & CONTENTS_SOLID) continue;
        GetClusterPVS(dleafs[leaf].cluster, pvs);
        for (int other = 0; other < numleafs; other++) {
            if (other == leaf) continue;
            int of = LeafFlags(other);
            if (!(of & (LEAF_FLAGS_SKY | LEAF_FLAGS_SKY2D))) continue;
            if (!PVSCheck(pvs, dleafs[other].cluster)) continue;
            if (of & LEAF_FLAGS_SKY2D) sky2d[leaf >> 3] |= 1 << (leaf & 7);
            if (of & LEAF_FLAGS_SKY) {
                sky3d[leaf >> 3] |= 1 << (leaf & 7);
                break;
            }
        }
    }
    for (int leaf = 0; leaf < numleafs; leaf++) {
        int flags = LeafFlags(leaf);
        if (flags & LEAF_FLAGS_SKY) continue;
        if (dleafs[leaf].contents & CONTENTS_SOLID) continue;
        if (sky2d[leaf >> 3] & (1 << (leaf & 7))) flags |= LEAF_FLAGS_SKY2D;
        if (sky3d[leaf >> 3] & (1 << (leaf & 7))) flags = (flags | LEAF_FLAGS_SKY) & ~LEAF_FLAGS_SKY2D;
        else if ((flags & LEAF_FLAGS_RADIAL) && MapHasSky()) {
            /* vrad means to trace rays from the leaf's centre to find the sky that radial vis cut off,
             * but its rays start from memory it never sets (CanLeafTraceToSky, in the SDK too): they
             * see the sky from wherever that is. Measured: every such leaf gets the sky when the map
             * has sky faces. */
            flags |= LEAF_FLAGS_SKY;
        }
        SetLeafFlags(leaf, flags);
    }
    free(sky3d);
    free(sky2d);
    free(pvs);
}

float g_SunAngularExtent;

static void ParseLightEnvironment(const entity_t *e) {
    vec3_t dest;
    GetVectorForKey(e, "origin", dest);
    directlight_t *dl = AllocDLight(dest, 0);
    ParseLightGeneric(e, dl);
    const char *spread = ValueForKeyOrNull(e, "SunSpreadAngle");
    if (spread) {
        g_SunAngularExtent = (float)sin(atof(spread) * (PI / 180.0));
        Msg("sun extent from map=%f\n", g_SunAngularExtent);
    }
    if (gSkyLight) return;                       /* only the first light_environment */
    gSkyLight = dl;
    dl->light.type = emit_skylight;
    gAmbient = AllocDLight(dl->light.origin, 0);
    gAmbient->light.type = emit_skyambient;
    if (!(g_bHDR && LightForKey(e, "_ambientHDR", gAmbient->light.intensity)) &&
        !LightForKey(e, "_ambient", gAmbient->light.intensity))
        VectorScale(dl->light.intensity, 0.5f, gAmbient->light.intensity);
    if (g_bHDR) {
        float s = FloatForKeyWithDefault(e, "_AmbientScaleHDR", 1.0f);
        VectorScale(gAmbient->light.intensity, s, gAmbient->light.intensity);
    }
    gSkyLight->light.flags &= ~DWL_FLAGS_CASTENTITYSHADOWS;
    gAmbient->light.flags &= ~DWL_FLAGS_CASTENTITYSHADOWS;
    directlight_t *suns[2] = {gSkyLight, gAmbient};
    BuildVisForLightEnvironment(suns, 2);
    AddToActiveList(gSkyLight);
    AddToActiveList(gAmbient);
}

static void ParseLightDirectional(const entity_t *e) {
    vec3_t dest;
    GetVectorForKey(e, "origin", dest);
    directlight_t *dl = AllocDLight(dest, 1);
    ParseLightGeneric(e, dl);
    const char *spread = ValueForKeyOrNull(e, "SunSpreadAngle");
    dl->has_sun_extent = 1;                      /* (its own, 0 without the key) */
    if (spread) dl->sun_extent = (float)sin(atof(spread) * (PI / 180.0));
    dl->light.type = emit_skylight;
    dl->directional = 1;
    dl->light.flags &= ~DWL_FLAGS_CASTENTITYSHADOWS;
    BuildVisForLightEnvironment(&dl, 1);
}

void CreateDirectLights(void) {
    numdlights = 0;
    activelights = gSkyLight = gAmbient = NULL;
    /* surfaces: a light per leaf patch of a light-emitting texture, by its share of the texture's area */
    for (int i = 0; i < numpatches; i++) {
        const patch_t *p = &patches[i];
        if (p->child1 != -1) continue;
        if (p->basearea < 1e-6) continue;
        if ((p->baselight[0] + p->baselight[1] + p->baselight[2]) / 3 >= 0.1f) {
            directlight_t *dl = AllocDLight(p->origin, 1);
            dl->light.type = emit_surface;
            VectorCopy(p->normal, dl->light.normal);
            VectorScale(p->baselight, lightscale * p->area * p->scale[0] * p->scale[1] / p->basearea, dl->light.intensity);
            VectorScale(dl->light.intensity, 100.0f * 100.0f, dl->light.intensity);     /* (DIRECT_SCALE) */
        }
    }
    for (int i = 0; i < num_entities; i++) {
        const entity_t *e = &entities[i];
        const char *name = ValueForKey(e, "classname");
        if (strncmp(name, "light", 5)) continue;
        if (!strcmp(name, "light_dynamic")) continue;
        if (!strcmp(name, "light_spot")) ParseLightSpot(e);
        else if (!strcmp(name, "light_environment")) ParseLightEnvironment(e);
        else if (!strcmp(name, "light_directional")) ParseLightDirectional(e);
        else if (!strcmp(name, "light")) ParseLightPoint(e);
        else Msg("unsupported light entity: \"%s\"\n", name);
    }
    Msg("%i direct lights\n", numdlights);
}

/* lump 54 (HDR) / 15: the active lights, intensities in 0..1 */
void ExportDirectLightsToWorldLights(void) {
    int n = 0;
    for (directlight_t *dl = activelights; dl; dl = dl->next) n++;
    unsigned char *out = xalloc(100 * n + 1), *p = out;
    for (directlight_t *dl = activelights; dl; dl = dl->next, p += 100) {
        float f[3];
        memcpy(p, dl->light.origin, 12);
        VectorScale(dl->light.intensity, (float)(1.0 / 255.0), f);
        memcpy(p + 12, f, 12);
        memcpy(p + 24, dl->light.normal, 12);
        memcpy(p + 36, dl->light.shadow_cast_offset, 12);
        int ints[3] = {dl->light.cluster, dl->light.type, dl->light.style};
        memcpy(p + 48, ints, 12);
        float fl[7] = {dl->light.stopdot, dl->light.stopdot2, dl->light.exponent, dl->light.radius,
                       dl->light.constant_attn, dl->light.linear_attn, dl->light.quadratic_attn};
        memcpy(p + 60, fl, 28);
        int tail[3] = {dl->light.flags, 0, 0};       /* flags, texinfo, owner */
        memcpy(p + 88, tail, 12);
    }
    SetLump(g_bHDR ? LUMP_WORLDLIGHTS_HDR : LUMP_WORLDLIGHTS, out, 100 * n, 1);
}
