/* Direct light on faces (vrad's BuildFacelights and FinalLightFace, without bounced light yet).
 *
 * Each face's lightmap is cut into one-luxel cells (luxel s covers [s, s + 1) along each axis, clipped to
 * the face); every cell is a sample, lit at its balance point (moved 1 unit off the face) by each light
 * that can reach its cluster and isn't blocked (the ray tracer). A luxel's value is then the weighted mean
 * of the nearby samples (its own face's and its smoothing neighbours'), weights by overlap / distance
 * (vrad's "radial" filter), and it is stored as 4 bytes (3 mantissas, a shared exponent). */
#include <float.h>
#include <xmmintrin.h>
#include "hlvrad.h"

#define NUM_BUMP_VECTS 3
#define MAXLIGHTMAPS 4
#define ON_EPSILON 0.1f
#define WEIGHT_EPS 0.00001
#define EQUAL_EPSILON 0.001
#define DIST_EPSILON (0.03125f)
#define CONSTANT_DOT (0.7 / 2)

typedef struct {
    int facenum;
    dface_t *face;
    vec3_t facenormal;
    float facedist;
    vec3_t modelorg;
    float worldToLuxelSpace[2][3], luxelToWorldSpace[2][3];
    vec3_t luxelOrigin;
    int isflat;
} lightinfo_t;

typedef struct {
    vec3_t pos, normal;
    float s, t;
    float coord[2], mins[2], maxs[2];
    float area;
} sample_t;

typedef struct {
    int numsamples;
    sample_t *sample;
    int numluxels;
    vec3_t *luxel;
    vec3_t *luxelNormals;                                /* (displacements) */
    float worldAreaPerLuxel;
    vec3_t *light[MAXLIGHTMAPS][NUM_BUMP_VECTS + 1];     /* per style, per normal, per sample */
} facelight_t;

static facelight_t *facelight;

/* ------------------------------------------------------------------ SSE helpers (Valve's estimates) */
static float ReciprocalSSE(float a) {
    __m128 x = _mm_set_ss(a), r = _mm_rcp_ss(x);
    r = _mm_sub_ss(_mm_add_ss(r, r), _mm_mul_ss(x, _mm_mul_ss(r, r)));
    return _mm_cvtss_f32(r);
}

static float ReciprocalSqrtSSE(float a) {
    __m128 x = _mm_set_ss(a), g = _mm_rsqrt_ss(x);
    /* one Newton step: 0.5 * g * (3 - a g^2) */
    g = _mm_mul_ss(g, _mm_sub_ss(_mm_set_ss(3.0f), _mm_mul_ss(x, _mm_mul_ss(g, g))));
    g = _mm_mul_ss(_mm_set_ss(0.5f), g);
    return _mm_cvtss_f32(g);
}

/* x^(e/4): quarter powers by square roots, the whole part by repeated squaring (Valve's PowSIMD) */
static float PowFixedPoint(float x, int exponent) {
    float rslt = 1.0f;
    int xp = abs(exponent);
    if (xp & 3) {
        float sq = sqrtf(x);
        if (xp & 1) rslt = sqrtf(sq);
        if (xp & 2) rslt = rslt * sq;
    }
    xp >>= 2;
    float cur = x;
    for (;;) {
        if (xp & 1) rslt = rslt * cur;
        xp >>= 1;
        if (xp) cur = cur * cur;
        else break;
    }
    if (exponent < 0) {
        __m128 r = _mm_set_ss(rslt == 0.0f ? FLT_EPSILON : rslt);
        return _mm_cvtss_f32(_mm_rcp_ss(r));
    }
    return rslt;
}

/* ------------------------------------------------------------------ normals at a point */
/* the smoothed normal at spot (relative to the face's model): within each fan triangle (centroid, corner j,
 * corner j+1) the face normal and the two corner normals mixed by spot's barycentric weights (vrad's SSE
 * version: the last triangle containing the point wins), then normalized */
void GetPhongNormal(int facenum, const vec3_t spot, vec3_t phong) {
    const dface_t *f = &g_pFaces[facenum];
    const float *facenormal = dplanes[f->planenum].normal;
    VectorCopy(facenormal, phong);
    if (smoothing_threshold == 1) return;
    const float *c = face_centroids[facenum];
    vec3_t vspot;
    VectorSubtract(spot, c, vspot);
    for (int j = 0; j < f->numedges; j++) {
        const float *n1 = FaceCornerNormal(facenum, j), *n2 = FaceCornerNormal(facenum, (j + 1) % f->numedges);
        vec3_t v1, v2;
        VectorSubtract(dvertexes[EdgeVertex(f, j)].point, c, v1);
        VectorSubtract(dvertexes[EdgeVertex(f, j + 1)].point, c, v2);
        float aa = DotProduct(v1, v1), bb = DotProduct(v2, v2), ab = DotProduct(v1, v2);
        float a1 = ReciprocalSSE(aa * bb - ab * ab);
        a1 = a1 * (bb * DotProduct(vspot, v1) - ab * DotProduct(vspot, v2));
        float a2 = ReciprocalSSE(bb);
        a2 = a2 * (DotProduct(vspot, v2) - a1 * ab);
        if (!(a1 >= 0.0f && a2 >= 0.0f)) continue;
        float scale = (1.0f - a1) - a2;
        for (int k = 0; k < 3; k++) {
            phong[k] = facenormal[k] * scale;
            phong[k] = phong[k] + n1[k] * a1;
            phong[k] = phong[k] + n2[k] * a2;
        }
    }
    float r = ReciprocalSqrtSSE(DotProduct(phong, phong));
    VectorScale(phong, r, phong);
}

static const float g_localBumpBasis[3][3] = {
    {0.81649661064147949f, 0.0f, 0.57735025882720947f},
    {-0.40824821591377258f, 0.70710676908493042f, 0.57735025882720947f},
    {-0.40824821591377258f, -0.70710676908493042f, 0.57735025882720947f},
};

/* the three bump directions around the (phong) normal, in the texture's frame */
void GetBumpNormals(const float *sVect, const float *tVect, const vec3_t flatNormal, const vec3_t phongNormal,
                    vec3_t bumpNormals[3]) {
    vec3_t tmp, basis[3];
    tmp[0] = sVect[1] * tVect[2] - sVect[2] * tVect[1];
    tmp[1] = sVect[2] * tVect[0] - sVect[0] * tVect[2];
    tmp[2] = sVect[0] * tVect[1] - sVect[1] * tVect[0];
    int leftHanded = DotProduct(flatNormal, tmp) < 0.0f;
    basis[1][0] = phongNormal[1] * sVect[2] - phongNormal[2] * sVect[1];
    basis[1][1] = phongNormal[2] * sVect[0] - phongNormal[0] * sVect[2];
    basis[1][2] = phongNormal[0] * sVect[1] - phongNormal[1] * sVect[0];
    VectorNormalize(basis[1]);
    basis[0][0] = basis[1][1] * phongNormal[2] - basis[1][2] * phongNormal[1];
    basis[0][1] = basis[1][2] * phongNormal[0] - basis[1][0] * phongNormal[2];
    basis[0][2] = basis[1][0] * phongNormal[1] - basis[1][1] * phongNormal[0];
    VectorNormalize(basis[0]);
    VectorCopy(phongNormal, basis[2]);
    if (leftHanded) VectorScale(basis[1], -1.0f, basis[1]);
    for (int i = 0; i < 3; i++) {
        /* VectorIRotate: the basis rows are the face's axes (matrix3x4 rows) */
        for (int k = 0; k < 3; k++)
            bumpNormals[i][k] = (g_localBumpBasis[i][0] * basis[0][k] + g_localBumpBasis[i][1] * basis[1][k]) +
                                g_localBumpBasis[i][2] * basis[2][k];
    }
}

/* ------------------------------------------------------------------ luxel space */
static void CalcFaceVectors(lightinfo_t *l) {
    const texinfo_t *tex = &texinfo[l->face->texinfo];
    for (int i = 0; i < 2; i++)
        for (int j = 0; j < 3; j++) l->worldToLuxelSpace[i][j] = tex->lightmapVecsLuxelsPerWorldUnits[i][j];
    const float(*lv)[4] = tex->lightmapVecsLuxelsPerWorldUnits;
    vec3_t cross;
    cross[0] = lv[1][1] * lv[0][2] - lv[1][2] * lv[0][1];
    cross[1] = lv[1][2] * lv[0][0] - lv[1][0] * lv[0][2];
    cross[2] = lv[1][0] * lv[0][1] - lv[1][1] * lv[0][0];
    float det = -DotProduct(l->facenormal, cross);
    if (fabs(det) < 1.0e-20) {
        Msg(" warning - face vectors parallel to face normal. bad lighting will be produced\n");
        VectorClear(l->luxelOrigin);
    } else {
        const float *n = l->facenormal;
        float(*w)[3] = l->worldToLuxelSpace;
        l->luxelToWorldSpace[0][0] = (n[2] * w[1][1] - n[1] * w[1][2]) / det;
        l->luxelToWorldSpace[1][0] = (n[1] * w[0][2] - n[2] * w[0][1]) / det;
        l->luxelOrigin[0] = -(l->facedist * cross[0]) / det;
        l->luxelToWorldSpace[0][1] = (n[0] * w[1][2] - n[2] * w[1][0]) / det;
        l->luxelToWorldSpace[1][1] = (n[2] * w[0][0] - n[0] * w[0][2]) / det;
        l->luxelOrigin[1] = -(l->facedist * cross[1]) / det;
        l->luxelToWorldSpace[0][2] = (n[1] * w[1][0] - n[0] * w[1][1]) / det;
        l->luxelToWorldSpace[1][2] = (n[0] * w[0][1] - n[1] * w[0][0]) / det;
        l->luxelOrigin[2] = -(l->facedist * cross[2]) / det;
        for (int k = 0; k < 3; k++) l->luxelOrigin[k] += -lv[0][3] * l->luxelToWorldSpace[0][k];
        for (int k = 0; k < 3; k++) l->luxelOrigin[k] += -lv[1][3] * l->luxelToWorldSpace[1][k];
    }
    VectorAdd(l->luxelOrigin, l->modelorg, l->luxelOrigin);
}

static void InitLightinfo(lightinfo_t *l, int facenum) {
    memset(l, 0, sizeof(*l));
    l->facenum = facenum;
    l->face = &g_pFaces[facenum];
    VectorCopy(dplanes[l->face->planenum].normal, l->facenormal);
    l->facedist = dplanes[l->face->planenum].dist;
    VectorCopy(face_offset[facenum], l->modelorg);
    CalcFaceVectors(l);
    l->isflat = 1;
    if (smoothing_threshold != 1) {
        for (int j = 0; j < l->face->numedges; j++) {
            float dot = DotProduct(l->facenormal, FaceCornerNormal(facenum, j));
            if (dot < 1.0 - EQUAL_EPSILON) {
                l->isflat = 0;
                break;
            }
        }
    }
}

static void WorldToLuxelSpace(const lightinfo_t *l, const vec3_t world, float *coord) {
    vec3_t pos;
    VectorSubtract(world, l->luxelOrigin, pos);
    coord[0] = DotProduct(pos, l->worldToLuxelSpace[0]) - l->face->m_LightmapTextureMinsInLuxels[0];
    coord[1] = DotProduct(pos, l->worldToLuxelSpace[1]) - l->face->m_LightmapTextureMinsInLuxels[1];
}

static void LuxelSpaceToWorld(const lightinfo_t *l, float s, float t, vec3_t world) {
    vec3_t pos;
    s += l->face->m_LightmapTextureMinsInLuxels[0];
    t += l->face->m_LightmapTextureMinsInLuxels[1];
    for (int k = 0; k < 3; k++) pos[k] = l->luxelOrigin[k] + s * l->luxelToWorldSpace[0][k];
    for (int k = 0; k < 3; k++) world[k] = pos[k] + t * l->luxelToWorldSpace[1][k];
}

/* ------------------------------------------------------------------ samples and luxels */
static int BuildFacesamples(lightinfo_t *l, facelight_t *fl) {
    int width = l->face->m_LightmapTextureSizeInLuxels[0] + 1, height = l->face->m_LightmapTextureSizeInLuxels[1] + 1;
    const texinfo_t *tex = &texinfo[l->face->texinfo];
    const float *a = tex->lightmapVecsLuxelsPerWorldUnits[0], *b = tex->lightmapVecsLuxelsPerWorldUnits[1];
    fl->worldAreaPerLuxel = (float)(1.0 / (sqrt(DotProduct(a, a)) * sqrt(DotProduct(b, b))));
    sample_t *samples = xalloc(sizeof(sample_t) * (width * height + 1));
    int n = 0;
    /* the face in luxel coordinates */
    winding_t *lw = WindingFromFace(l->face, l->modelorg);
    for (int i = 0; i < lw->numpoints; i++) {
        float coord[2];
        WorldToLuxelSpace(l, lw->p[i], coord);
        lw->p[i][0] = coord[0];
        lw->p[i][1] = coord[1];
        lw->p[i][2] = 0;
    }
    const vec3_t sNorm = {1.0f, 0.0f, 0.0f}, tNorm = {0.0f, 1.0f, 0.0f};
    const float sampleOffset = 1.0f;
    winding_t *t1, *t2, *s1, *s2;
    for (int t = 0; t < height && lw; t++) {
        ClipWindingEpsilon(lw, tNorm, t + sampleOffset, ON_EPSILON / 16.0f, &t1, &t2);
        for (int s = 0; s < width && t2; s++) {
            ClipWindingEpsilon(t2, sNorm, s + sampleOffset, ON_EPSILON / 16.0f, &s1, &s2);
            if (s2) {
                sample_t *sp = &samples[n++];
                sp->s = s;
                sp->t = t;
                vec3_t center, mins, maxs;
                sp->area = WindingAreaAndBalancePoint(s2, center) * fl->worldAreaPerLuxel;
                sp->coord[0] = center[0];
                sp->coord[1] = center[1];
                WindingBounds(s2, mins, maxs);
                sp->mins[0] = mins[0];
                sp->mins[1] = mins[1];
                sp->maxs[0] = maxs[0];
                sp->maxs[1] = maxs[1];
                LuxelSpaceToWorld(l, sp->coord[0], sp->coord[1], sp->pos);
                FreeWinding(s2);
            }
            if (t2) FreeWinding(t2);
            t2 = s1;
        }
        FreeWinding(lw);
        if (t2) FreeWinding(t2);
        lw = t1;
    }
    if (lw) FreeWinding(lw);
    fl->numsamples = n;
    fl->sample = samples;
    for (int i = 0; i < n; i++) VectorCopy(l->facenormal, fl->sample[i].normal);
    if (!n) Msg("no samples %d\n", l->facenum);
    return 1;
}

static void BuildFaceLuxels(lightinfo_t *l, facelight_t *fl) {
    int width = l->face->m_LightmapTextureSizeInLuxels[0] + 1, height = l->face->m_LightmapTextureSizeInLuxels[1] + 1;
    fl->numluxels = width * height;
    fl->luxel = xalloc(sizeof(vec3_t) * (fl->numluxels + 1));
    for (int t = 0; t < height; t++)
        for (int s = 0; s < width; s++) LuxelSpaceToWorld(l, s, t, fl->luxel[s + t * width]);
}

/* A displacement's samples: a grid of width x height cells in its (u, v), each at its centre on the
 * surface (pushed 1 unit out), with the blended vertex normal there. Its luxels sit on the surface at
 * the grid corners (u, v = 0 .. 1). */
static void BuildDispSamples(lightinfo_t *l, facelight_t *fl) {
    const dispsurf_t *d = &dispsurfs[l->face->dispinfo];
    int width = l->face->m_LightmapTextureSizeInLuxels[0] + 1, height = l->face->m_LightmapTextureSizeInLuxels[1] + 1;
    float stepU = 1.0f / (float)width, stepV = 1.0f / (float)height;
    float halfU = stepU * 0.5f, halfV = stepV * 0.5f;
    fl->numsamples = width * height;
    fl->sample = xalloc(sizeof(sample_t) * (fl->numsamples + 1));
    for (int v = 0; v < height; v++)
        for (int u = 0; u < width; u++) {
            sample_t *sp = &fl->sample[v * width + u];
            sp->s = (float)u;
            sp->t = (float)v;
            sp->coord[0] = (float)u * stepU + halfU;
            sp->coord[1] = (float)v * stepV + halfV;
            DispUVToSurfPoint(d, sp->coord[0], sp->coord[1], 1.0f, sp->pos);
            DispUVToSurfNormal(d, sp->coord[0], sp->coord[1], sp->normal);
        }
}

static void BuildDispLuxels(lightinfo_t *l, facelight_t *fl) {
    const dispsurf_t *d = &dispsurfs[l->face->dispinfo];
    int width = l->face->m_LightmapTextureSizeInLuxels[0] + 1, height = l->face->m_LightmapTextureSizeInLuxels[1] + 1;
    fl->numluxels = width * height;
    fl->luxel = xalloc(sizeof(vec3_t) * (fl->numluxels + 1));
    fl->luxelNormals = xalloc(sizeof(vec3_t) * (fl->numluxels + 1));
    float stepU = 1.0f / (float)(width - 1), stepV = 1.0f / (float)(height - 1);
    for (int v = 0; v < height; v++)
        for (int u = 0; u < width; u++) {
            float uv[2] = {(float)u * stepU, (float)v * stepV};
            DispUVToSurfPoint(d, uv[0], uv[1], 1.0f, fl->luxel[v * width + u]);
            DispUVToSurfNormal(d, uv[0], uv[1], fl->luxelNormals[v * width + u]);
        }
}

/* ------------------------------------------------------------------ light at 4 points (vrad's SSE gather) */
/* Samples go through in groups of 4 (the last group padded with copies of its last sample), each light's
 * shadow rays traced as one packet, as vrad does: the packet decides which tree leaves are visited. */
#define LANES 4
typedef struct {
    float dot[NUM_BUMP_VECTS + 1][LANES];
    float falloff[LANES];
    float sunAmount[LANES];
} lightout4_t;

typedef struct {
    float pos[3][LANES];                       /* the points (1 unit off the face) */
    float normals[NUM_BUMP_VECTS + 1][3][LANES];
    int normalCount;
} points4_t;

static float Dot4(const float v[3][LANES], int i, const float *w) {
    return (v[0][i] * w[0] + v[1][i] * w[1]) + v[2][i] * w[2];
}

static void GatherSampleStandardLight4(lightout4_t *out, const directlight_t *dl, const points4_t *p) {
    float src[3][LANES], delta[3][LANES], dist[LANES], dist2[LANES], dot[LANES];
    int hard = dl->m_flEndFadeDistance > dl->m_flStartFadeDistance;
    for (int i = 0; i < LANES; i++) {
        for (int c = 0; c < 3; c++) {
            src[c][i] = dl->light.origin[c];
            delta[c][i] = src[c][i] - p->pos[c][i];
        }
        dist2[i] = (delta[0][i] * delta[0][i] + delta[1][i] * delta[1][i]) + delta[2][i] * delta[2][i];
        float r = ReciprocalSqrtSSE(dist2[i]);
        for (int c = 0; c < 3; c++) delta[c][i] = delta[c][i] * r;
        dist[i] = sqrtf(dist2[i]);
        float d = (delta[0][i] * p->normals[0][0][i] + delta[1][i] * p->normals[0][1][i]) + delta[2][i] * p->normals[0][2][i];
        dot[i] = d > 0 ? d : 0;
    }
    if (hard) {
        int any = 0;
        for (int i = 0; i < LANES; i++) {
            if (!(dist[i] <= dl->m_flEndFadeDistance)) dot[i] = 0;
            else any = 1;
        }
        if (!any) return;
    }
    float evald[LANES];
    for (int i = 0; i < LANES; i++) {
        dist[i] = dist[i] > 1.0f ? dist[i] : 1.0f;
        evald[i] = dist[i] < dl->m_flCapDist ? dist[i] : dl->m_flCapDist;
    }
    switch (dl->light.type) {
    case emit_point:
        for (int i = 0; i < LANES; i++) {
            float f = evald[i] * evald[i] * dl->light.quadratic_attn;
            f = f + dl->light.linear_attn * evald[i];
            f = f + dl->light.constant_attn;
            out->falloff[i] = ReciprocalSSE(f);
        }
        break;
    case emit_surface: {
        int allzero = 1;
        for (int i = 0; i < LANES; i++) {
            float d2 = -Dot4((const float(*)[LANES])delta, i, dl->light.normal);
            d2 = d2 > 0 ? d2 : 0;
            if (dot[i] != 0) allzero = 0;
            out->falloff[i] = ReciprocalSSE(dist2[i]) * d2;
        }
        if (allzero) return;
        for (int c = 0; c < 3; c++)
            for (int i = 0; i < LANES; i++) src[c][i] += dl->light.normal[c] * DIST_EPSILON;
        break;
    }
    case emit_spotlight: {
        int any = 0;
        float d2[LANES];
        for (int i = 0; i < LANES; i++) {
            d2[i] = -Dot4((const float(*)[LANES])delta, i, dl->light.normal);
            if (d2[i] > dl->light.stopdot2) any = 1;
            else dot[i] = 0;
        }
        if (!any) return;
        for (int i = 0; i < LANES; i++) {
            float f = evald[i] * evald[i] * dl->light.quadratic_attn;
            f = f + dl->light.linear_attn * evald[i];
            f = f + dl->light.constant_attn;
            f = ReciprocalSSE(f);
            f = f * d2[i];
            float mult = ReciprocalSSE(dl->light.stopdot - dl->light.stopdot2) * (d2[i] - dl->light.stopdot2);
            mult = mult < 1.0f ? mult : 1.0f;
            mult = mult > 0.0f ? mult : 0.0f;
            if (dl->light.exponent != 0.0f && dl->light.exponent != 1.0f)
                mult = PowFixedPoint(mult, (int)(4.0 * dl->light.exponent));
            if (!(d2[i] <= dl->light.stopdot)) mult = 1.0f;
            out->falloff[i] = mult * f;
        }
        break;
    }
    }
    if (hard) {
        for (int i = 0; i < LANES; i++) {
            float t = ReciprocalSSE(dl->m_flEndFadeDistance - dl->m_flStartFadeDistance) * (dist[i] - dl->m_flStartFadeDistance);
            t = t < 1.0f ? t : 1.0f;
            t = t > 0.0f ? t : 0.0f;
            t = 1.0f - t;
            float mult = (6.0f * t - 15.0f) * t + 10.0f;
            mult = (t * t) * mult;
            mult = t * mult;
            out->falloff[i] = mult * out->falloff[i];
        }
    }
    float vis[LANES];
    TestLine4(p->pos, (const float(*)[LANES])src, -1, vis);
    for (int i = 0; i < LANES; i++) out->dot[0][i] = vis[i] * dot[i];
    for (int n = 1; n < p->normalCount; n++)
        for (int i = 0; i < LANES; i++) {
            float d = (p->normals[n][0][i] * delta[0][i] + p->normals[n][1][i] * delta[1][i]) + p->normals[n][2][i] * delta[2][i];
            out->dot[n][i] = d > 0 ? d : 0;
        }
}

static void SkyDirection(int i, vec3_t out);

static void GatherSampleSkyLight4(lightout4_t *out, const directlight_t *dl, const points4_t *p) {
    float dot[LANES];
    int allzero = 1;
    for (int i = 0; i < LANES; i++) {
        float d = -((p->normals[0][0][i] * dl->light.normal[0] + p->normals[0][1][i] * dl->light.normal[1]) +
                    p->normals[0][2][i] * dl->light.normal[2]);
        dot[i] = d > 0 ? d : 0;
        if (dot[i] != 0) allzero = 0;
    }
    if (allzero) return;
    /* a sun with a spread angle: 30 rays (7 with -fast), all but the first jittered over a disc of that size */
    float extent = dl->has_sun_extent ? dl->sun_extent : g_SunAngularExtent;
    int nsamples = extent > 0.0f ? (g_bFast ? 7 : 30) : 1;
    float see[LANES] = {0}, frac[LANES], stop[3][LANES];
    float L = (float)MAX_TRACE_LENGTH;
    float jitter = (float)((double)extent * MAX_TRACE_LENGTH);
    for (int d = 0; d < nsamples; d++) {
        vec3_t delta;
        for (int c = 0; c < 3; c++) delta[c] = dl->light.normal[c] * -L;
        if (d) {
            vec3_t ofs;
            SkyDirection(d - 1, ofs);
            for (int c = 0; c < 3; c++) delta[c] = ofs[c] * jitter + delta[c];
        }
        for (int c = 0; c < 3; c++)
            for (int i = 0; i < LANES; i++) stop[c][i] = p->pos[c][i] + delta[c];
        TestLine_DoesHitSky4(p->pos, (const float(*)[LANES])stop, -1, frac);
        for (int i = 0; i < LANES; i++) see[i] += frac[i];
    }
    float scale = 1.0f / (float)nsamples;
    for (int i = 0; i < LANES; i++) {
        see[i] = see[i] * scale;
        out->dot[0][i] = dot[i] * see[i];
        out->falloff[i] = 1.0f;
        out->sunAmount[i] = see[i] * 10000.0f;
    }
    for (int n = 1; n < p->normalCount; n++)
        for (int i = 0; i < LANES; i++) {
            float d = -((p->normals[n][0][i] * dl->light.normal[0] + p->normals[n][1][i] * dl->light.normal[1]) +
                        p->normals[n][2][i] * dl->light.normal[2]);
            out->dot[n][i] = d * see[i];
        }
}

/* the sky ambient: 162 pseudo-random directions over the sphere (Halton bases 2 and 3; Valve's sequence
 * starts at its second element) */
static float Halton(int seed, int base) {
    float ret = 0.0f, fbase = (float)base, inv = (float)(1.0 / fbase);
    while (seed) {
        int dig = seed % base;
        ret += (float)dig * inv;
        inv /= fbase;
        seed /= base;
    }
    return ret;
}

static void SkyDirection(int i, vec3_t out) {
    float z = Halton(i + 2, 2);
    z = (float)(2 * z - 1.0);
    float phi = (float)acos(z);
    float theta = (float)(2.0 * 3.14159265358979323846 * Halton(i + 2, 3));
    float sp = (float)sin(phi);
    out[0] = (float)cos(theta) * sp;
    out[1] = (float)sin(theta) * sp;
    out[2] = z;
}

static void GatherSampleAmbientSky4(lightout4_t *out, const directlight_t *dl, const points4_t *p) {
    float sumdot[LANES] = {0}, ambient[NUM_BUMP_VECTS + 1][LANES] = {{0}}, possible[NUM_BUMP_VECTS + 1][LANES] = {{0}};
    float dots[NUM_BUMP_VECTS + 1][LANES];
    (void)dl;
    for (int j = 0; j < NUMVERTEXNORMALS; j++) {
        vec3_t anorm;
        SkyDirection(j, anorm);
        int valid[LANES], any = 0;
        for (int i = 0; i < LANES; i++) {
            dots[0][i] = -((p->normals[0][0][i] * anorm[0] + p->normals[0][1][i] * anorm[1]) + p->normals[0][2][i] * anorm[2]);
            valid[i] = dots[0][i] > EQUAL_EPSILON;
            any |= valid[i];
        }
        if (!any) continue;
        for (int i = 0; i < LANES; i++) {
            if (!valid[i]) dots[0][i] = 0;
            sumdot[i] = dots[0][i] + sumdot[i];
            possible[0][i] = (valid[i] ? 1.0f : 0.0f) + possible[0][i];
        }
        for (int n = 1; n < p->normalCount; n++)
            for (int i = 0; i < LANES; i++) {
                dots[n][i] = -((p->normals[n][0][i] * anorm[0] + p->normals[n][1][i] * anorm[1]) + p->normals[n][2][i] * anorm[2]);
                int v2 = dots[n][i] > EQUAL_EPSILON;
                if (!v2) dots[n][i] = 0;
                possible[n][i] = (valid[i] && v2 ? 1.0f : 0.0f) + possible[n][i];
            }
        float stop[3][LANES], frac[LANES];
        float L = (float)MAX_TRACE_LENGTH;
        for (int c = 0; c < 3; c++) {
            float dc = anorm[c] * -L;
            for (int i = 0; i < LANES; i++) stop[c][i] = dc + p->pos[c][i];
        }
        TestLine_DoesHitSky4(p->pos, (const float(*)[LANES])stop, -1, frac);      /* (flEpsilon is 0 for faces) */
        for (int n = 0; n < p->normalCount; n++)
            for (int i = 0; i < LANES; i++) ambient[n][i] = ambient[n][i] + frac[i] * dots[n][i];
    }
    for (int i = 0; i < LANES; i++) out->falloff[i] = 1.0f;
    for (int n = 0; n < p->normalCount; n++)
        for (int i = 0; i < LANES; i++) {
            float factor = ReciprocalSSE(possible[0][i]) * possible[n][i];
            float d = factor * sumdot[i];
            d = ReciprocalSSE(d);
            out->dot[n][i] = ambient[n][i] * d;
        }
}

static void GatherSampleLight4(lightout4_t *out, const directlight_t *dl, const points4_t *p) {
    memset(out, 0, sizeof(*out));
    switch (dl->light.type) {
    case emit_skylight: GatherSampleSkyLight4(out, dl, p); break;
    case emit_skyambient: GatherSampleAmbientSky4(out, dl, p); break;
    default: GatherSampleStandardLight4(out, dl, p); break;
    }
    for (int i = 0; i < LANES; i++) {
        out->dot[0][i] = out->dot[0][i] > 0 ? out->dot[0][i] : 0;
        int notZero = out->dot[0][i] > 0;
        for (int n = 1; n < p->normalCount; n++) {
            out->dot[n][i] = out->dot[n][i] > 0 ? out->dot[n][i] : 0;
            if (!notZero) out->dot[n][i] = 0;
        }
    }
}

/* ------------------------------------------------------------------ a face's direct light */
static int FindOrAllocateLightstyleSamples(dface_t *f, facelight_t *fl, int style, int normalCount) {
    int k;
    for (k = 0; k < MAXLIGHTMAPS; k++) {
        if (f->styles[k] == style) return k;
        if (f->styles[k] == 255) break;
    }
    if (k >= MAXLIGHTMAPS) return -1;
    f->styles[k] = (unsigned char)style;
    for (int n = 0; n < normalCount; n++) fl->light[k][n] = xalloc(sizeof(vec3_t) * (fl->numsamples + 1));
    return k;
}

void BuildFacelights(int facenum) {
    dface_t *f = &g_pFaces[facenum];
    facelight_t *fl = &facelight[facenum];
    f->lightofs = -1;
    memset(f->styles, 255, 4);
    if (texinfo[f->texinfo].flags & TEX_SPECIAL) return;
    if (!FaceHasPatches(facenum)) return;
    lightinfo_t l;
    InitLightinfo(&l, facenum);
    int isdisp = f->dispinfo != -1;
    if (isdisp) {
        BuildDispSamples(&l, fl);
        BuildDispLuxels(&l, fl);
    } else {
        BuildFacesamples(&l, fl);
        BuildFaceLuxels(&l, fl);
    }
    const texinfo_t *tex = &texinfo[f->texinfo];
    int normalCount = tex->flags & SURF_BUMPLIGHT ? NUM_BUMP_VECTS + 1 : 1;
    f->styles[0] = 0;
    for (int n = 0; n < normalCount; n++) fl->light[0][n] = xalloc(sizeof(vec3_t) * (fl->numsamples + 1));
    vec3_t flatBump[NUM_BUMP_VECTS];
    if (l.isflat && normalCount > 1)
        GetBumpNormals(tex->textureVecsTexelsPerWorldUnits[0], tex->textureVecsTexelsPerWorldUnits[1], l.facenormal,
                       l.facenormal, flatBump);
    for (int group = 0; group < fl->numsamples; group += LANES) {
        int count = fl->numsamples - group < LANES ? fl->numsamples - group : LANES;
        points4_t p;
        p.normalCount = normalCount;
        int cluster[LANES];
        for (int i = 0; i < LANES; i++) {
            sample_t *sp = &fl->sample[group + (i < count ? i : count - 1)];
            vec3_t normal, bumps[NUM_BUMP_VECTS];
            if (isdisp) {
                /* (the sample's own normal; the point still moves along the base face's normal) */
                VectorCopy(sp->normal, normal);
                if (normalCount > 1)
                    GetBumpNormals(tex->textureVecsTexelsPerWorldUnits[0], tex->textureVecsTexelsPerWorldUnits[1],
                                   l.facenormal, normal, bumps);
            } else if (l.isflat) {
                VectorCopy(l.facenormal, normal);
                for (int b = 0; b < NUM_BUMP_VECTS && normalCount > 1; b++) VectorCopy(flatBump[b], bumps[b]);
            } else {
                vec3_t spot;
                VectorSubtract(sp->pos, l.modelorg, spot);
                GetPhongNormal(facenum, spot, normal);
                if (normalCount > 1)
                    GetBumpNormals(tex->textureVecsTexelsPerWorldUnits[0], tex->textureVecsTexelsPerWorldUnits[1],
                                   l.facenormal, normal, bumps);
                if (i < count) VectorCopy(normal, sp->normal);
            }
            for (int c = 0; c < 3; c++) {
                /* (the point moves 1 unit off the face so the face itself doesn't shadow it) */
                p.pos[c][i] = sp->pos[c] + l.facenormal[c];
                p.normals[0][c][i] = normal[c];
                for (int b = 1; b < normalCount; b++) p.normals[b][c][i] = bumps[b - 1][c];
            }
            cluster[i] = ClusterFromPoint(sp->pos);
        }
        for (directlight_t *dl = activelights; dl; dl = dl->next) {
            float mask[LANES] = {0};
            int any = 0;
            for (int i = 0; i < count; i++)
                if (PVSCheck(dl->pvs, cluster[i])) mask[i] = 1.0f, any = 1;
            if (!any) continue;
            lightout4_t out;
            GatherSampleLight4(&out, dl, &p);
            float fxdot[NUM_BUMP_VECTS + 1][LANES];
            int nonzero = 0;
            for (int b = 0; b < normalCount; b++)
                for (int i = 0; i < LANES; i++) {
                    fxdot[b][i] = out.dot[b][i] * mask[i];
                    fxdot[b][i] = fxdot[b][i] * out.falloff[i];
                    if (fxdot[b][i] != 0) nonzero = 1;
                }
            if (!nonzero) continue;
            int style = FindOrAllocateLightstyleSamples(f, fl, dl->light.style, normalCount);
            if (style < 0) continue;
            for (int b = 0; b < normalCount; b++)
                for (int i = 0; i < count; i++)
                    for (int k = 0; k < 3; k++) fl->light[style][b][group + i][k] += fxdot[b][i] * dl->light.intensity[k];
        }
    }
}

/* ------------------------------------------------------------------ luxels from samples (radial) */
typedef struct {
    lightinfo_t l;
    int w, h;
    vec3_t *light[NUM_BUMP_VECTS + 1];
    float *weight;
} radial_t;

static radial_t *AllocateRadial(int facenum) {
    radial_t *rad = xalloc(sizeof(radial_t));
    InitLightinfo(&rad->l, facenum);
    rad->w = rad->l.face->m_LightmapTextureSizeInLuxels[0] + 1;
    rad->h = rad->l.face->m_LightmapTextureSizeInLuxels[1] + 1;
    for (int b = 0; b < NUM_BUMP_VECTS + 1; b++) rad->light[b] = xalloc(sizeof(vec3_t) * (rad->w * rad->h + 1));
    rad->weight = xalloc(sizeof(float) * (rad->w * rad->h + 1));
    return rad;
}

static void FreeRadial(radial_t *rad) {
    for (int b = 0; b < NUM_BUMP_VECTS + 1; b++) free(rad->light[b]);
    free(rad->weight);
    free(rad);
}

#define OO_SQRT_3 0.57735025882720947f
static void AddDirectToRadial(radial_t *rad, const vec3_t pnt, const float *cmins, const float *cmaxs, vec3_t *light,
                              int hasBump, int neighbourBump) {
    float coord[2];
    WorldToLuxelSpace(&rad->l, pnt, coord);
    int s_min = (int)cmins[0], t_min = (int)cmins[1];
    int s_max = (int)(cmaxs[0] + 0.9999f) + 1, t_max = (int)(cmaxs[1] + 0.9999f) + 1;
    if (s_min < 0) s_min = 0;
    if (t_min < 0) t_min = 0;
    if (s_max > rad->w) s_max = rad->w;
    if (t_max > rad->h) t_max = rad->h;
    for (int s = s_min; s < s_max; s++) {
        for (int t = t_min; t < t_max; t++) {
            float s0 = (float)(cmins[0] - s > -1.0 ? cmins[0] - s : -1.0);
            float t0 = (float)(cmins[1] - t > -1.0 ? cmins[1] - t : -1.0);
            float s1 = (float)(cmaxs[0] - s < 1.0 ? cmaxs[0] - s : 1.0);
            float t1 = (float)(cmaxs[1] - t < 1.0 ? cmaxs[1] - t : 1.0);
            float area = (s1 - s0) * (t1 - t0);
            if (!(area > EQUAL_EPSILON)) continue;
            float ds = fabsf(coord[0] - s), dt = fabsf(coord[1] - t);
            float r = ds > dt ? ds : dt;
            r = r < 0.1 ? (float)(area / 0.1) : area / r;
            int i = s + t * rad->w;
            if (hasBump) {
                if (neighbourBump) {
                    for (int b = 0; b < NUM_BUMP_VECTS + 1; b++)
                        for (int k = 0; k < 3; k++) rad->light[b][i][k] += light[b][k] * r;
                } else {
                    for (int k = 0; k < 3; k++) rad->light[0][i][k] += light[0][k] * r;
                    for (int b = 1; b < NUM_BUMP_VECTS + 1; b++)
                        for (int k = 0; k < 3; k++) rad->light[b][i][k] += light[0][k] * (r * OO_SQRT_3);
                }
            } else {
                for (int k = 0; k < 3; k++) rad->light[0][i][k] += light[0][k] * r;
            }
            rad->weight[i] += r;
        }
    }
}

static radial_t *BuildLuxelRadial(int facenum, int style) {
    facelight_t *fl = &facelight[facenum];
    radial_t *rad = AllocateRadial(facenum);
    int bump = texinfo[g_pFaces[facenum].texinfo].flags & SURF_BUMPLIGHT ? 1 : 0;
    vec3_t light[NUM_BUMP_VECTS + 1];
    for (int k = 0; k < fl->numsamples; k++) {
        for (int b = 0; b < (bump ? NUM_BUMP_VECTS + 1 : 1); b++) VectorCopy(fl->light[style][b][k], light[b]);
        AddDirectToRadial(rad, fl->sample[k].pos, fl->sample[k].mins, fl->sample[k].maxs, light, bump, bump);
    }
    int nn;
    const int *neighbours = FaceNeighbours(facenum, &nn);
    for (int j = 0; j < nn; j++) {
        int nf = neighbours[j];
        facelight_t *nfl = &facelight[nf];
        int nbump = texinfo[g_pFaces[nf].texinfo].flags & SURF_BUMPLIGHT ? 1 : 0;
        int nstyle = 0;
        if (g_pFaces[nf].styles[nstyle] != g_pFaces[facenum].styles[style]) {
            for (nstyle = 1; nstyle < MAXLIGHTMAPS; nstyle++)
                if (g_pFaces[nf].styles[nstyle] == g_pFaces[facenum].styles[style]) break;
            if (nstyle >= MAXLIGHTMAPS) continue;
        }
        if (!nfl->light[nstyle][0]) continue;
        lightinfo_t l;
        InitLightinfo(&l, nf);
        for (int k = 0; k < nfl->numsamples; k++) {
            for (int b = 0; b < (nbump ? NUM_BUMP_VECTS + 1 : 1); b++) VectorCopy(nfl->light[nstyle][b][k], light[b]);
            vec3_t tmp;
            float mins[2], maxs[2];
            LuxelSpaceToWorld(&l, nfl->sample[k].mins[0], nfl->sample[k].mins[1], tmp);
            WorldToLuxelSpace(&rad->l, tmp, mins);
            LuxelSpaceToWorld(&l, nfl->sample[k].maxs[0], nfl->sample[k].maxs[1], tmp);
            WorldToLuxelSpace(&rad->l, tmp, maxs);
            AddDirectToRadial(rad, nfl->sample[k].pos, mins, maxs, light, bump, nbump);
        }
    }
    return rad;
}

/* -- displacements: each luxel gathers the samples (of its own and its neighbouring faces) within the
 * displacement's sample radius, found through a hash of 64-unit cells (vrad's sample hash) */
typedef struct { int x, y, z, count, cap; int *handles; } samplecell_t;
static samplecell_t *cells;
static int cellcap, numcells, *cellhash, cellmask;

static int CellFind(int x, int y, int z, int add) {
    unsigned h = ((unsigned)x * 73856093u ^ (unsigned)y * 19349663u ^ (unsigned)z * 83492791u) & (unsigned)cellmask;
    for (; cellhash[h] != -1; h = (h + 1) & (unsigned)cellmask) {
        samplecell_t *c = &cells[cellhash[h]];
        if (c->x == x && c->y == y && c->z == z) return cellhash[h];
    }
    if (!add) return -1;
    if (numcells == cellcap) {
        cellcap = cellcap ? cellcap * 2 : 1024;
        cells = realloc(cells, sizeof(samplecell_t) * cellcap);
    }
    samplecell_t *c = &cells[numcells];
    memset(c, 0, sizeof(*c));
    c->x = x, c->y = y, c->z = z;
    cellhash[h] = numcells;
    return numcells++;
}

static void InsertSamplesDataIntoHashTable(void) {
    int total = 0;
    for (int f = 0; f < numfaces; f++) total += facelight[f].numsamples;
    int size = 1024;
    while (size < 2 * total) size *= 2;        /* (at most one cell per sample: never more than half full) */
    cellmask = size - 1;
    cellhash = xalloc(sizeof(int) * size);
    for (int i = 0; i < size; i++) cellhash[i] = -1;
    for (int f = 0; f < numfaces; f++) {
        if (texinfo[g_pFaces[f].texinfo].flags & TEX_SPECIAL) continue;
        facelight_t *fl = &facelight[f];
        for (int k = 0; k < fl->numsamples; k++) {
            const float *p = fl->sample[k].pos;
            int ci = CellFind((int)(p[0] / 64.0f), (int)(p[1] / 64.0f), (int)(p[2] / 64.0f), 1);
            samplecell_t *c = &cells[ci];
            if (c->count == c->cap) {
                c->cap = c->cap ? c->cap * 2 : 16;
                c->handles = realloc(c->handles, sizeof(int) * c->cap);
            }
            c->handles[c->count++] = (k & 0xFFFF) | (f << 16);
        }
    }
}

static int IsNeighbor(int face, int other) {
    if (face == other) return 1;
    int nn;
    const int *n = FaceNeighbours(face, &nn);
    for (int i = 0; i < nn; i++)
        if (n[i] == other) return 1;
    return 0;
}

static radial_t *BuildDispLuxelRadial(int facenum, int style) {
    static int hashed;
    if (!hashed) InsertSamplesDataIntoHashTable(), hashed = 1;
    facelight_t *fl = &facelight[facenum];
    radial_t *rad = AllocateRadial(facenum);
    const dispsurf_t *d = &dispsurfs[g_pFaces[facenum].dispinfo];
    int bump = texinfo[g_pFaces[facenum].texinfo].flags & SURF_BUMPLIGHT ? 1 : 0;
    float radius = (float)sqrt(d->sample_radius2), r2 = radius * radius;
    int lightstyle = g_pFaces[facenum].styles[style];
    for (int j = 0; j < rad->w * rad->h; j++) {
        const float *lp = fl->luxel[j], *ln = fl->luxelNormals[j];
        int vmin[3], vmax[3];
        for (int a = 0; a < 3; a++) {
            vmin[a] = (int)((lp[a] - radius) * (1.0f / 64.0f));
            vmax[a] = (int)((lp[a] + radius) * (1.0f / 64.0f)) + 1;
        }
        for (int z = vmin[2]; z < vmax[2] + 1; z++)
            for (int y = vmin[1]; y < vmax[1] + 1; y++)
                for (int x = vmin[0]; x < vmax[0] + 1; x++) {
                    int ci = CellFind(x, y, z, 0);
                    if (ci < 0) continue;
                    const samplecell_t *c = &cells[ci];
                    for (int h = 0; h < c->count; h++) {
                        int ns = c->handles[h] & 0xFFFF, nf = (c->handles[h] >> 16) & 0xFFFF;
                        if (!IsNeighbor(facenum, nf)) continue;
                        int nstyle = -1;
                        for (int k = 0; k < MAXLIGHTMAPS; k++)
                            if (g_pFaces[nf].styles[k] == lightstyle) {
                                nstyle = k;
                                break;
                            }
                        if (nstyle == -1) continue;
                        facelight_t *nfl = &facelight[nf];
                        int nbump = texinfo[g_pFaces[nf].texinfo].flags & SURF_BUMPLIGHT ? 1 : 0;
                        const sample_t *sp = &nfl->sample[ns];
                        float angle = DotProduct(sp->normal, ln);
                        if (angle < 0.15f) continue;
                        vec3_t seg;
                        VectorSubtract(sp->pos, lp, seg);
                        float dist = sqrtf(DotProduct(seg, seg));
                        float influence = 1.0f - (dist * dist) / r2;
                        if (influence <= 0.0f) continue;
                        influence *= angle;
                        if (bump && nbump) {
                            for (int b = 0; b < NUM_BUMP_VECTS + 1; b++)
                                for (int k = 0; k < 3; k++) rad->light[b][j][k] += nfl->light[nstyle][b][ns][k] * influence;
                        } else if (bump) {
                            influence *= 0.05f;
                            for (int b = 0; b < NUM_BUMP_VECTS + 1; b++)
                                for (int k = 0; k < 3; k++) rad->light[b][j][k] += nfl->light[nstyle][0][ns][k] * influence;
                        } else {
                            for (int k = 0; k < 3; k++) rad->light[0][j][k] += nfl->light[nstyle][0][ns][k] * influence;
                        }
                        rad->weight[j] += influence;
                    }
                }
    }
    return rad;
}

static int DispSampleRadial(radial_t *rad, int j, vec3_t *light, int bumpCount) {
    int ok = 1;
    for (int b = 0; b < bumpCount; b++) {
        VectorClear(light[b]);
        if (rad->weight[j] > 0.0f) VectorScale(rad->light[b][j], 1.0f / rad->weight[j], light[b]);
        else if (b == 0) ok = 0;
    }
    return ok;
}

static int SampleRadial(radial_t *rad, const vec3_t pnt, vec3_t *light, int bumpCount) {
    float coord[2];
    WorldToLuxelSpace(&rad->l, pnt, coord);
    int u = (int)(coord[0] + 0.5f), v = (int)(coord[1] + 0.5f), i = u + v * rad->w;
    if (u < 0 || u > rad->w || v < 0 || v > rad->h) {
        for (int b = 0; b < bumpCount; b++) light[b][0] = 2550, light[b][1] = light[b][2] = 0;
        return 0;
    }
    int ok = 1;
    for (int b = 0; b < bumpCount; b++) {
        VectorClear(light[b]);
        if (rad->weight[i] > WEIGHT_EPS) {
            float sc = (float)(1.0 / rad->weight[i]);
            VectorScale(rad->light[b][i], sc, light[b]);
        } else {                               /* no sample reaches it: black (L4D2 vrad) */
            if (b == 0) ok = 0;
        }
    }
    return ok;
}

/* 4 bytes: 3 mantissas and the exponent putting the largest in 128..255 (L4D2's version: the float's own
 * exponent, truncated mantissas) */
void VectorToColorRGBExp32(const vec3_t v, unsigned char *c) {
    const float *pmax;
    if (v[0] > v[1]) pmax = v[0] > v[2] ? &v[0] : &v[2];
    else pmax = v[1] > v[2] ? &v[1] : &v[2];
    int exponent = 0;
    if (*pmax != 0.0f) {
        unsigned bits;
        memcpy(&bits, pmax, 4);
        exponent = (int)((bits & 0x7F800000) >> 23) - (7 + 127);
    }
    unsigned sbits = (unsigned)(127 - exponent) << 23;
    float scalar;
    memcpy(&scalar, &sbits, 4);
    c[0] = (unsigned char)(int)(v[0] * scalar);
    c[1] = (unsigned char)(int)(v[1] * scalar);
    c[2] = (unsigned char)(int)(v[2] * scalar);
    c[3] = (unsigned char)(signed char)exponent;
}

static int FloatCompare(const void *a, const void *b) {
    float x = *(const float *)a, y = *(const float *)b;
    return x < y ? -1 : x > y ? 1 : 0;
}

void FinalLightFace(int facenum) {
    dface_t *f = &g_pFaces[facenum];
    if (texinfo[f->texinfo].flags & TEX_SPECIAL) return;
    facelight_t *fl = &facelight[facenum];
    int nstyles;
    for (nstyles = 0; nstyles < MAXLIGHTMAPS; nstyles++)
        if (f->styles[nstyles] == 255) break;
    if (!nstyles || f->lightofs < 0) return;
    float minlight = face_entity_minlight(facenum) * 128;
    int bump = texinfo[f->texinfo].flags & SURF_BUMPLIGHT ? 1 : 0, bumpCount = bump ? NUM_BUMP_VECTS + 1 : 1;
    float *reds = xalloc(sizeof(float) * (fl->numluxels + 1)), *greens = xalloc(sizeof(float) * (fl->numluxels + 1)),
          *blues = xalloc(sizeof(float) * (fl->numluxels + 1));
    for (int k = 0; k < nstyles; k++) {
        int isdisp = f->dispinfo != -1;
        radial_t *rad = isdisp ? BuildDispLuxelRadial(facenum, k) : BuildLuxelRadial(facenum, k);
        unsigned char *pdata[NUM_BUMP_VECTS + 1];
        for (int b = 0; b < bumpCount; b++) pdata[b] = dlightdata + f->lightofs + (k * bumpCount + b) * fl->numluxels * 4;
        int avgCount = 0;
        for (int j = 0; j < fl->numluxels; j++) {
            vec3_t lb[NUM_BUMP_VECTS + 1];
            int ok = isdisp ? DispSampleRadial(rad, j, lb, bumpCount) : SampleRadial(rad, fl->luxel[j], lb, bumpCount);
            if (fl->numsamples == 0) {
                for (int b = 0; b < bumpCount; b++) lb[b][0] = 255, lb[b][1] = lb[b][2] = 0;
                ok = 0;
            }
            for (int b = 0; b < bumpCount; b++) {
                for (int i = 0; i < 3; i++) lb[b][i] = lb[b][i] > minlight ? lb[b][i] : minlight;
                if (b == 0 && ok) {
                    reds[avgCount] = lb[0][0];
                    greens[avgCount] = lb[0][1];
                    blues[avgCount] = lb[0][2];
                    avgCount++;
                }
                VectorToColorRGBExp32(lb[b], pdata[b]);
                pdata[b] += 4;
            }
        }
        FreeRadial(rad);
        /* the median colour, stored before lightofs in reverse style order */
        unsigned char *avg = dlightdata + f->lightofs - 4 * (k + 1);
        vec3_t median = {0, 0, 0};
        if (avgCount) {
            qsort(reds, avgCount, sizeof(float), FloatCompare);
            qsort(greens, avgCount, sizeof(float), FloatCompare);
            qsort(blues, avgCount, sizeof(float), FloatCompare);
            median[0] = reds[avgCount >> 1];
            median[1] = greens[avgCount >> 1];
            median[2] = blues[avgCount >> 1];
        }
        VectorToColorRGBExp32(median, avg);
    }
    free(reds);
    free(greens);
    free(blues);
}

void AllocFacelights(void) { facelight = xalloc(sizeof(facelight_t) * (numfaces + 1)); }
