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
    winding_t *w;                              /* (-extra: a partial luxel's piece of the face, in world space) */
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

/* the scalar version (patches): the first fan triangle that holds the point wins; the face normal is kept
 * as it is when none does */
void GetPhongNormalScalar(int facenum, const vec3_t spot, vec3_t phong) {
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
        float a1 = (DotProduct(vspot, v1) * bb - DotProduct(vspot, v2) * ab) / (aa * bb - ab * ab);
        float a2 = (DotProduct(vspot, v2) - a1 * ab) / bb;
        if (!(a1 >= 0.0f && a2 >= 0.0f)) continue;
        float scale = (1.0f - a1) - a2;
        for (int k = 0; k < 3; k++) {
            phong[k] = facenormal[k] * scale;
            phong[k] = n1[k] * a1 + phong[k];
            phong[k] = n2[k] * a2 + phong[k];
        }
        VectorNormalize(phong);
        return;
    }
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
                if (g_bExtra && sp->area < fl->worldAreaPerLuxel - EQUAL_EPSILON) {
                    for (int k = 0; k < s2->numpoints; k++) {
                        vec3_t wp;
                        LuxelSpaceToWorld(l, s2->p[k][0], s2->p[k][1], wp);
                        VectorCopy(wp, s2->p[k]);
                    }
                    sp->w = s2;
                } else {
                    FreeWinding(s2);
                }
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
    /* each cell's area: its corners on the surface (not pushed out) */
    vec3_t *corners = xalloc(sizeof(vec3_t) * (width + 1) * (height + 1));
    for (int v = 0; v < height + 1; v++)
        for (int u = 0; u < width + 1; u++) DispUVToSurfPoint(d, (float)u * stepU, (float)v * stepV, 0.0f, corners[v * (width + 1) + u]);
    winding_t *w = AllocWinding(4);
    w->numpoints = 4;
    for (int v = 0; v < height; v++)
        for (int u = 0; u < width; u++) {
            VectorCopy(corners[v * (width + 1) + u], w->p[0]);
            VectorCopy(corners[(v + 1) * (width + 1) + u], w->p[1]);
            VectorCopy(corners[(v + 1) * (width + 1) + u + 1], w->p[2]);
            VectorCopy(corners[v * (width + 1) + u + 1], w->p[3]);
            fl->sample[v * width + u].area = WindingArea(w);
        }
    FreeWinding(w);
    free(corners);
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
    int tinted;                                    /* (sky map: the light's colour per normal, instead of its own) */
    float tint[NUM_BUMP_VECTS + 1][3][LANES];
} lightout4_t;

typedef struct {
    float pos[3][LANES];                       /* the points (1 unit off the face) */
    float normals[NUM_BUMP_VECTS + 1][3][LANES];
    int normalCount;
} points4_t;

/* -gpu: a face's light is worked out in rounds. A round runs the face as usual, but each group of points' lights is a
 * question for the GPU: answered ones (from earlier rounds) are replayed in order, new ones recorded (their answer 0
 * for now). A face with new questions stops there (before supersampling decides anything from the zeros); the GPU
 * answers every face's questions together, and the face runs again. Supersampling's passes each need a round. */
typedef struct {
    gpugroup_t *pend;               /* the new questions */
    int npend, cappend;
    float *res;                     /* the answers so far: per question, 64 floats per style slot */
    int nres;
    int cursor, incomplete;         /* (this run: the next question; whether any was new) */
} gpuface_t;
static gpuface_t *gpufaces;
static __thread gpuface_t *gpucur;
static const float gpuzero[64 * 256];

/* the GPU's answer for a group of points (64 floats per slot: per point 4 normals' colours and "lit"): an answer
 * from an earlier round, or (recorded) zeros */
static const float *GpuQuery(const points4_t *p, const int cluster[LANES], int lanes, int flags, int slot) {
    gpuface_t *g = gpucur;
    int R = 64 * GPU_NumSlots();
    if (g->cursor < g->nres) return g->res + (size_t)R * g->cursor++;
    g->cursor++;
    g->incomplete = 1;
    if (g->npend == g->cappend) g->cappend = g->cappend ? 2 * g->cappend : 16, g->pend = realloc(g->pend, sizeof(gpugroup_t) * g->cappend);
    gpugroup_t *q = &g->pend[g->npend++];
    memset(q, 0, sizeof(*q));
    memcpy(q->pos, p->pos, sizeof(q->pos));
    for (int n = 0; n < p->normalCount; n++) memcpy(q->nrm[n], p->normals[n], sizeof(q->nrm[n]));
    memcpy(q->cluster, cluster, sizeof(q->cluster));
    q->lanes = lanes, q->normalCount = p->normalCount, q->flags = flags, q->skip = -1, q->slot = slot;
    return gpuzero;
}

static float Dot4(const float v[3][LANES], int i, const float *w) {
    return (v[0][i] * w[0] + v[1][i] * w[1]) + v[2][i] * w[2];
}

/* vrad's GatherSampleLightSSE options, as static prop lighting sets them (faces use none): fewer sky rays,
 * a constant dot instead of the normals', and one static prop that casts no shadow */
#define GATHERLFLAGS_FORCE_FAST 1
#define GATHERLFLAGS_IGNORE_NORMALS 2
__thread int g_gatherFlags;
__thread int g_gatherSkipProp = -1;
static __thread int g_gatherNoRays;            /* (-gpu: the falloff only; every point taken as seeing the light) */

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
        if (g_gatherFlags & GATHERLFLAGS_IGNORE_NORMALS) d = CONSTANT_DOT;
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
    float vis[LANES] = {1, 1, 1, 1};
    if (!g_gatherNoRays) TestLine4(p->pos, (const float(*)[LANES])src, g_gatherSkipProp, vis);
    for (int i = 0; i < LANES; i++) out->dot[0][i] = vis[i] * dot[i];
    for (int n = 1; n < p->normalCount; n++)
        for (int i = 0; i < LANES; i++) {
            float d = (p->normals[n][0][i] * delta[0][i] + p->normals[n][1][i] * delta[1][i]) + p->normals[n][2][i] * delta[2][i];
            if (g_gatherFlags & GATHERLFLAGS_IGNORE_NORMALS) d = CONSTANT_DOT;
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
        if (g_gatherFlags & GATHERLFLAGS_IGNORE_NORMALS) d = CONSTANT_DOT;
        dot[i] = d > 0 ? d : 0;
        if (dot[i] != 0) allzero = 0;
    }
    if (allzero) return;
    /* a sun with a spread angle: 30 rays (7 with -fast), all but the first jittered over a disc of that size */
    float extent = dl->has_sun_extent ? dl->sun_extent : g_SunAngularExtent;
    int nsamples = extent > 0.0f ? (g_bFast || (g_gatherFlags & GATHERLFLAGS_FORCE_FAST) ? 7 : 30) : 1;
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
        TestLine_DoesHitSky4(p->pos, (const float(*)[LANES])stop, g_gatherSkipProp, frac);
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
            if (g_gatherFlags & GATHERLFLAGS_IGNORE_NORMALS) {
                out->dot[n][i] = CONSTANT_DOT;
                continue;
            }
            out->dot[n][i] = d * see[i];
        }
}

/* the sky ambient: 162 pseudo-random directions over the sphere (Halton bases 2 and 3; Valve's sequence
 * starts at its second element) */
static float Halton(int seed, int base) {
    float ret = 0.0f, fbase = (float)base, inv = (float)(1.0 / fbase);
    float step = 1.0f / fbase;                 /* (each digit's weight: times 1/base, as Valve's) */
    while (seed) {
        int dig = seed % base;
        ret += (float)dig * inv;
        inv = inv * step;
        seed /= base;
    }
    return ret;
}

static vec3_t *skytable;          /* (the directions, worked out once before the threads start) */
static int skytable_n;

static void SkyDirectionCompute(int i, vec3_t out) {
    float z = Halton(i + 2, 2);
    z = (float)(2 * z - 1.0);
    float phi = (float)acos(z);
    float theta = (float)(2.0 * 3.14159265358979323846 * Halton(i + 2, 3));
    float sp = (float)sin(phi);
    out[0] = (float)cos(theta) * sp;
    out[1] = (float)sin(theta) * sp;
    out[2] = z;
}

/* (for the static props' bounced light, which walks the same directions) */
static void SkyDirection(int i, vec3_t out) {
    if (i < skytable_n) VectorCopy(skytable[i], out);
    else SkyDirectionCompute(i, out);
}

void BuildSkyDirections(int n) {
    skytable = xalloc(sizeof(vec3_t) * (n + 1));
    for (int i = 0; i < n; i++) SkyDirectionCompute(i, skytable[i]);
    skytable_n = n;
}

void SkyDirectionAt(int i, vec3_t out) { SkyDirection(i, out); }

/* (for the GPU: the table) */
const vec3_t *SkyDirections(int *n) {
    *n = skytable_n;
    return (const vec3_t *)skytable;
}

static void GatherSampleAmbientSky4(lightout4_t *out, const directlight_t *dl, const points4_t *p) {
    float sumdot[LANES] = {0}, ambient[NUM_BUMP_VECTS + 1][LANES] = {{0}}, possible[NUM_BUMP_VECTS + 1][LANES] = {{0}};
    float dots[NUM_BUMP_VECTS + 1][LANES];
    float skyscale = HaveSkyMap() ? Luminance(dl->light.intensity) : 0.0f;
    float colsum[NUM_BUMP_VECTS + 1][3][LANES] = {{{0}}};
    int nsky = g_bFast || (g_gatherFlags & GATHERLFLAGS_FORCE_FAST) ? NUMVERTEXNORMALS / 4
                                                                     : (int)(g_flSkySampleScale * 162.0f);
    int ignore = g_gatherFlags & GATHERLFLAGS_IGNORE_NORMALS;
    for (int j = 0; j < nsky; j++) {
        vec3_t anorm;
        SkyDirection(j, anorm);
        int valid[LANES], any = 0;
        for (int i = 0; i < LANES; i++) {
            dots[0][i] = -((p->normals[0][0][i] * anorm[0] + p->normals[0][1][i] * anorm[1]) + p->normals[0][2][i] * anorm[2]);
            if (ignore) dots[0][i] = CONSTANT_DOT;
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
                if (ignore) dots[n][i] = CONSTANT_DOT;
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
        TestLine_DoesHitSky4(p->pos, (const float(*)[LANES])stop, g_gatherSkipProp, frac);      /* (flEpsilon is 0 here) */
        for (int n = 0; n < p->normalCount; n++)
            for (int i = 0; i < LANES; i++) ambient[n][i] = ambient[n][i] + frac[i] * dots[n][i];
        if (skyscale > 0) {                        /* (the sky seen this way: the ray goes along -anorm) */
            vec3_t look = {-anorm[0], -anorm[1], -anorm[2]}, c;
            SkyMapColor(look, skyscale, c);
            for (int n = 0; n < p->normalCount; n++)
                for (int i = 0; i < LANES; i++)
                    for (int k = 0; k < 3; k++) colsum[n][k][i] += frac[i] * dots[n][i] * c[k];
        }
    }
    for (int i = 0; i < LANES; i++) out->falloff[i] = 1.0f;
    for (int n = 0; n < p->normalCount; n++)
        for (int i = 0; i < LANES; i++) {
            float factor = ReciprocalSSE(possible[0][i]) * possible[n][i];
            float d = factor * sumdot[i];
            d = ReciprocalSSE(d);
            out->dot[n][i] = ambient[n][i] * d;
        }
    if (skyscale > 0) {         /* (colour = the sky's average over the rays that got out, weighted as the light is) */
        out->tinted = 1;
        for (int n = 0; n < p->normalCount; n++)
            for (int i = 0; i < LANES; i++)
                for (int k = 0; k < 3; k++) out->tint[n][k][i] = ambient[n][i] > 0 ? colsum[n][k][i] / ambient[n][i] : 0.0f;
    }
}

static void GatherSampleLight4(lightout4_t *out, const directlight_t *dl, const points4_t *p) {
    out->tinted = 0;
    memset(out->dot, 0, sizeof(out->dot)), memset(out->falloff, 0, sizeof(out->falloff));
    memset(out->sunAmount, 0, sizeof(out->sunAmount));
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

/* one light at one point (in all 4 lanes, as vrad duplicates it): the falloff times the normal's dot */
float GatherSampleLightAtPoint(const directlight_t *dl, const vec3_t pos, const vec3_t normal, vec3_t color) {
    points4_t p;
    p.normalCount = 1;
    for (int c = 0; c < 3; c++)
        for (int i = 0; i < LANES; i++) p.pos[c][i] = pos[c], p.normals[0][c][i] = normal[c];
    lightout4_t out;
    GatherSampleLight4(&out, dl, &p);
    for (int k = 0; k < 3; k++) color[k] = out.tinted ? out.tint[0][k][0] : dl->light.intensity[k];
    return out.dot[0][0] * out.falloff[0];
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

/* 4 points' illumination positions (1 unit off the face), normals (bumped too) and clusters, as vrad's
 * ComputeIlluminationPointAndNormalsSSE: a displacement's own normal, else the flat or phong normal */
static void SetupPoints4(const lightinfo_t *l, int facenum, int normalCount, int isdisp, const vec3_t *flatBump,
                         const float pos[LANES][3], const float nin[LANES][3], points4_t *p, int cluster[LANES],
                         float nout[LANES][3]) {
    const texinfo_t *tex = &texinfo[g_pFaces[facenum].texinfo];
    p->normalCount = normalCount;
    for (int i = 0; i < LANES; i++) {
        vec3_t normal, bumps[NUM_BUMP_VECTS];
        if (isdisp) {
            /* (the sample's own normal; the point still moves along the base face's normal) */
            VectorCopy(nin[i], normal);
            if (normalCount > 1)
                GetBumpNormals(tex->textureVecsTexelsPerWorldUnits[0], tex->textureVecsTexelsPerWorldUnits[1], l->facenormal,
                               normal, bumps);
        } else if (l->isflat) {
            VectorCopy(l->facenormal, normal);
            for (int b = 0; b < NUM_BUMP_VECTS && normalCount > 1; b++) VectorCopy(flatBump[b], bumps[b]);
        } else {
            vec3_t spot;
            VectorSubtract(pos[i], l->modelorg, spot);
            GetPhongNormal(facenum, spot, normal);
            if (normalCount > 1)
                GetBumpNormals(tex->textureVecsTexelsPerWorldUnits[0], tex->textureVecsTexelsPerWorldUnits[1], l->facenormal,
                               normal, bumps);
        }
        VectorCopy(normal, nout[i]);
        for (int c = 0; c < 3; c++) {
            /* (the point moves 1 unit off the face so the face itself doesn't shadow it) */
            p->pos[c][i] = pos[i][c] + l->facenormal[c];
            p->normals[0][c][i] = normal[c];
            for (int b = 1; b < normalCount; b++) p->normals[b][c][i] = bumps[b - 1][c];
        }
        cluster[i] = ClusterFromPoint(pos[i]);
    }
}

/* ------------------------------------------------------------------ supersampling (-extra) */
/* Where neighbouring samples differ a lot (in perceived brightness), a sample is lit again at 16 points
 * across its luxel (direct light) and 4 (sky ambient), points outside a partial luxel's piece of the face
 * dropped; then again for samples next to changed ones, up to 4 passes. */
#define AMBIENT_ONLY 1
#define NON_AMBIENT_ONLY 2

/* L4D2's PointsInWinding ORs its "outside" mask into a stack slot it never initialises, so a row of points
 * can be rejected because of what was left there: its own previous mask (an all-outside row rejects the
 * rest of the sample), the last light's value in ResampleLightAt4Points, and also whatever the ray
 * tracer's deeper frames left (its mailbox, the sky test's temporaries), which isn't reproduced here. */
static __thread __m128 g_staleSlot;
int g_ssPoints = 4, g_ssPasses = 4, g_bFixQuirks;
float g_ssThreshold = 0.0625f;

static void ResampleLightAt4Points(const points4_t *p, const int cluster[LANES], const dface_t *f, int style, int flags,
                                   vec3_t result[LANES][NUM_BUMP_VECTS + 1]) {
    memset(result, 0, sizeof(vec3_t) * LANES * (NUM_BUMP_VECTS + 1));
    if (g_bGPU) {
        int slot = GPU_StyleSlot(f->styles[style]);
        if (slot < 0) return;
        int gf = (flags & AMBIENT_ONLY ? GPU_AMBIENT_ONLY : 0) | (flags & NON_AMBIENT_ONLY ? GPU_NON_AMBIENT_ONLY : 0);
        const float *r = GpuQuery(p, cluster, LANES, gf, slot) + 64 * slot;
        for (int i = 0; i < LANES; i++)
            for (int b = 0; b < p->normalCount; b++)
                for (int k = 0; k < 3; k++) result[i][b][k] = r[16 * i + 3 * b + k];
        /* what vrad leaves in the stale slot: the last light's first-normal values, whose signs (all PointsInWinding
         * reads) are its falloff's: worked out without rays (sky lights' falloffs are never negative) */
        directlight_t *last = NULL;
        float lastmask[LANES] = {0};
        for (directlight_t *dl = activelights; dl; dl = dl->next) {
            if ((flags & AMBIENT_ONLY) && dl->light.type != emit_skyambient) continue;
            if ((flags & NON_AMBIENT_ONLY) && dl->light.type == emit_skyambient) continue;
            if (dl->light.style != f->styles[style]) continue;
            float mask[LANES] = {0};
            int any = 0;
            for (int i = 0; i < LANES; i++)
                if (PVSCheck(dl->pvs, cluster[i])) mask[i] = 1.0f, any = 1;
            if (any) last = dl, memcpy(lastmask, mask, sizeof(mask));
        }
        if (last) {
            lightout4_t out;
            memset(&out, 0, sizeof(out));
            if (last->light.type == emit_skylight || last->light.type == emit_skyambient) out.falloff[0] = out.falloff[1] = out.falloff[2] = out.falloff[3] = 1.0f;
            else {
                g_gatherNoRays = 1;
                GatherSampleLight4(&out, last, p);
                g_gatherNoRays = 0;
            }
            for (int i = 0; i < LANES; i++) {
                float fx = (out.dot[0][i] > 0 ? 1.0f : 0.0f) * out.falloff[i];
                ((float *)&g_staleSlot)[i] = fx * lastmask[i];
            }
        }
        return;
    }
    for (directlight_t *dl = activelights; dl; dl = dl->next) {
        if ((flags & AMBIENT_ONLY) && dl->light.type != emit_skyambient) continue;
        if ((flags & NON_AMBIENT_ONLY) && dl->light.type == emit_skyambient) continue;
        if (dl->light.style != f->styles[style]) continue;
        float mask[LANES] = {0};
        int any = 0;
        for (int i = 0; i < LANES; i++)
            if (PVSCheck(dl->pvs, cluster[i])) mask[i] = 1.0f, any = 1;
        if (!any) continue;
        lightout4_t out;
        GatherSampleLight4(&out, dl, p);
        for (int b = 0; b < p->normalCount; b++)
            for (int i = 0; i < LANES; i++) {
                float fx = out.dot[b][i] * out.falloff[i];
                fx = fx * mask[i];
                if (b == 0) ((float *)&g_staleSlot)[i] = fx;
                for (int k = 0; k < 3; k++) result[i][b][k] += fx * (out.tinted ? out.tint[b][k][i] : dl->light.intensity[k]);
            }
    }
}

/* which of 4 points lie in the winding (every edge turns the same way as the first); bit i: point i is out */
static int PointsInWinding(const float pt[3][LANES], const winding_t *w, int *invalid) {
    __m128 px = _mm_loadu_ps(pt[0]), py = _mm_loadu_ps(pt[1]), pz = _mm_loadu_ps(pt[2]);
    __m128 mask = g_bFixQuirks ? _mm_setzero_ps() : g_staleSlot, tx = _mm_setzero_ps(), ty = tx, tz = tx;     /* (-fixquirks: no leftover) */
    *invalid = 0;
    for (int k = 0; k < w->numpoints; k++) {
        const float *a = w->p[k], *b = w->p[(k + 1) % w->numpoints];
        __m128 ex = _mm_sub_ps(_mm_set1_ps(b[0]), _mm_set1_ps(a[0])), ey = _mm_sub_ps(_mm_set1_ps(b[1]), _mm_set1_ps(a[1])),
               ez = _mm_sub_ps(_mm_set1_ps(b[2]), _mm_set1_ps(a[2]));
        __m128 vx = _mm_sub_ps(px, _mm_set1_ps(a[0])), vy = _mm_sub_ps(py, _mm_set1_ps(a[1])),
               vz = _mm_sub_ps(pz, _mm_set1_ps(a[2]));
        __m128 cx = _mm_sub_ps(_mm_mul_ps(ey, vz), _mm_mul_ps(ez, vy));
        __m128 cy = _mm_sub_ps(_mm_mul_ps(ez, vx), _mm_mul_ps(ex, vz));
        __m128 cz = _mm_sub_ps(_mm_mul_ps(ex, vy), _mm_mul_ps(ey, vx));
        __m128 r = _mm_rsqrt_ps(_mm_add_ps(_mm_add_ps(_mm_mul_ps(cx, cx), _mm_mul_ps(cy, cy)), _mm_mul_ps(cz, cz)));
        cx = _mm_mul_ps(cx, r), cy = _mm_mul_ps(cy, r), cz = _mm_mul_ps(cz, r);
        if (k == 0) {
            tx = cx, ty = cy, tz = cz;
            continue;
        }
        __m128 dot = _mm_add_ps(_mm_add_ps(_mm_mul_ps(cx, tx), _mm_mul_ps(cy, ty)), _mm_mul_ps(cz, tz));
        mask = _mm_or_ps(mask, _mm_cmplt_ps(dot, _mm_setzero_ps()));
        g_staleSlot = mask;
        *invalid = _mm_movemask_ps(mask);
        if (*invalid == 0xF) return 0;
    }
    return 1;
}

/* -sspoints n other than vrad's 4: an n x n grid of points across the luxel for direct light, (n / 2) x (n / 2) for the sky
 * ambient (at least 1), lit 4 at a time; points outside a partial luxel's piece of the face left out */
static int SupersampleGrid(const lightinfo_t *l, int facenum, facelight_t *fl, int si, int style, int normalCount,
                           const vec3_t *flatBump, vec3_t *light, int flags) {
    const sample_t *sample = &fl->sample[si];
    const dface_t *f = &g_pFaces[facenum];
    float origin[2];
    WorldToLuxelSpace(l, sample->pos, origin);
    int n = flags & NON_AMBIENT_ONLY ? g_ssPoints : (g_ssPoints / 2 > 1 ? g_ssPoints / 2 : 1), total = n * n, count = 0;
    float cscale = 1.0f / (float)n, csshift = -((float)(n - 1) * cscale) / 2.0f;
    for (int b = 0; b < normalCount; b++) VectorClear(light[b]);
    float nin[LANES][3], nout[LANES][3], pos[LANES][3], pt[3][LANES];
    for (int i = 0; i < LANES; i++) VectorCopy(sample->normal, nin[i]);
    for (int at = 0; at < total; at += LANES) {
        int lanes = total - at < LANES ? total - at : LANES;
        for (int i = 0; i < LANES; i++) {
            int k = at + (i < lanes ? i : lanes - 1);
            LuxelSpaceToWorld(l, origin[0] + csshift + (float)(k / n) * cscale, origin[1] + csshift + (float)(k % n) * cscale, pos[i]);
            for (int c = 0; c < 3; c++) pt[c][i] = pos[i][c];
        }
        int invalid = 0;
        if (sample->w && !PointsInWinding((const float(*)[LANES])pt, sample->w, &invalid)) continue;
        points4_t p;
        int cluster[LANES];
        SetupPoints4(l, facenum, normalCount, 0, flatBump, (const float(*)[3])pos, (const float(*)[3])nin, &p, cluster, nout);
        vec3_t result[LANES][NUM_BUMP_VECTS + 1];
        ResampleLightAt4Points(&p, cluster, f, style, flags, result);
        for (int i = 0; i < lanes; i++) {
            if ((invalid >> i) & 1) continue;
            for (int b = 0; b < normalCount; b++) VectorAdd(light[b], result[i][b], light[b]);
            count++;
        }
    }
    return count;
}

static int SupersampleLightAtPoint(const lightinfo_t *l, int facenum, facelight_t *fl, int si, int style, int normalCount,
                                   const vec3_t *flatBump, vec3_t *light, int flags) {
    if (g_ssPoints != 4) return SupersampleGrid(l, facenum, fl, si, style, normalCount, flatBump, light, flags);
    const sample_t *sample = &fl->sample[si];
    const dface_t *f = &g_pFaces[facenum];
    float origin[2];
    WorldToLuxelSpace(l, sample->pos, origin);
    float width = flags & NON_AMBIENT_ONLY ? 4 : 2;
    float cscale = 1.0f / width, csshift = (float)(-((width - 1) * cscale) / 2.0);
    for (int n = 0; n < normalCount; n++) VectorClear(light[n]);
    int count = 0;
    float nin[LANES][3], nout[LANES][3], pos[LANES][3], pt[3][LANES];
    for (int i = 0; i < LANES; i++) VectorCopy(sample->normal, nin[i]);
    int rows = flags & NON_AMBIENT_ONLY ? 4 : 1;
    float row[4];
    for (int c = 0; c < 4; c++) row[c] = csshift + (float)c * cscale;
    for (int srow = 0; srow < rows; srow++) {
        for (int i = 0; i < LANES; i++) {
            float cs, ct;
            if (flags & NON_AMBIENT_ONLY) cs = origin[0] + row[srow], ct = origin[1] + row[i];
            else cs = origin[0] + (i < 2 ? csshift : csshift + cscale), ct = origin[1] + ((i & 1) ? csshift + cscale : csshift);
            LuxelSpaceToWorld(l, cs, ct, pos[i]);
            for (int c = 0; c < 3; c++) pt[c][i] = pos[i][c];
        }
        int invalid = 0;
        if (sample->w && !PointsInWinding((const float(*)[LANES])pt, sample->w, &invalid)) {
            if (flags & NON_AMBIENT_ONLY) continue;
            return 0;
        }
        points4_t p;
        int cluster[LANES];
        SetupPoints4(l, facenum, normalCount, 0, flatBump, (const float(*)[3])pos, (const float(*)[3])nin, &p, cluster, nout);
        vec3_t result[LANES][NUM_BUMP_VECTS + 1];
        ResampleLightAt4Points(&p, cluster, f, style, flags, result);
        for (int i = 0; i < LANES; i++) {
            if ((invalid >> i) & 1) continue;
            for (int n = 0; n < normalCount; n++) VectorAdd(light[n], result[i][n], light[n]);
            count++;
        }
    }
    return count;
}

static void SampleIntensity(vec3_t **ls, int i, int normalCount, int dest, int size, float *intensity) {
    for (int n = 0; n < normalCount; n++) {
        float in = (ls[n][i][0] + ls[n][i][1]) + ls[n][i][2];
        intensity[n * size + dest] = (float)pow(in / 256.0, 1.0 / 2.2);
    }
}

static void BuildSupersampleFaceLights(const lightinfo_t *l, int facenum, facelight_t *fl, int style, int normalCount,
                                       const vec3_t *flatBump) {
    const dface_t *f = &g_pFaces[facenum];
    int w = f->m_LightmapTextureSizeInLuxels[0] + 1, h = f->m_LightmapTextureSizeInLuxels[1] + 1, size = w * h;
    unsigned char *done = xalloc(size + 1);
    float *gradient = xalloc(sizeof(float) * (fl->numsamples + 1)), *intensity = xalloc(sizeof(float) * (normalCount * size + 1));
    vec3_t **ls = fl->light[style];
    for (int i = 0; i < fl->numsamples; i++)
        SampleIntensity(ls, i, normalCount, (int)fl->sample[i].s + (int)fl->sample[i].t * w, size, intensity);
    for (int pass = 1, another = 1; another && pass <= g_ssPasses; pass++) {
        for (int i = 0; i < fl->numsamples; i++) {
            if (done[i]) continue;
            gradient[i] = 0.0f;
            int ss = (int)fl->sample[i].s, st = (int)fl->sample[i].t;
            for (int n = 0; n < normalCount; n++) {
                int j = n * size + ss + st * w;
#define G(o)                                                       \
    {                                                              \
        float g_ = fabsf(intensity[j] - intensity[(o)]);           \
        gradient[i] = gradient[i] > g_ ? gradient[i] : g_;         \
    }
                if (st > 0) {
                    if (ss > 0) G(j - 1 - w);
                    G(j - w);
                    if (ss < w - 1) G(j + 1 - w);
                }
                if (st < h - 1) {
                    if (ss > 0) G(j - 1 + w);
                    G(j + w);
                    if (ss < w - 1) G(j + 1 + w);
                }
                if (ss > 0) G(j - 1);
                if (ss < w - 1) G(j + 1);
#undef G
            }
        }
        another = 0;
        for (int i = 0; i < fl->numsamples; i++) {
            if (done[i] || gradient[i] < g_ssThreshold) continue;
            done[i] = 1;
            another = 1;
            vec3_t amb[NUM_BUMP_VECTS + 1], dir[NUM_BUMP_VECTS + 1];
            int na = SupersampleLightAtPoint(l, facenum, fl, i, style, normalCount, flatBump, amb, AMBIENT_ONLY);
            int nd = SupersampleLightAtPoint(l, facenum, fl, i, style, normalCount, flatBump, dir, NON_AMBIENT_ONLY);
            if (gpucur && gpucur->incomplete) continue;      /* (-gpu: answers to come) */
            if (na > 0 && nd > 0) {
                float sd = 1.0f / nd, sa = 1.0f / na;
                for (int n = 0; n < normalCount; n++)
                    for (int k = 0; k < 3; k++) {
                        ls[n][i][k] = 0.0f + dir[n][k] * sd;
                        ls[n][i][k] = ls[n][i][k] + amb[n][k] * sa;
                    }
                SampleIntensity(ls, i, normalCount, (int)fl->sample[i].s + (int)fl->sample[i].t * w, size, intensity);
            }
        }
        if (gpucur && gpucur->incomplete) break;
    }
    free(done), free(gradient), free(intensity);
}

static void FreeFacelight(facelight_t *fl) {
    for (int i = 0; i < fl->numsamples; i++)
        if (fl->sample[i].w) FreeWinding(fl->sample[i].w);
    free(fl->sample), free(fl->luxel), free(fl->luxelNormals);
    for (int k = 0; k < MAXLIGHTMAPS; k++)
        for (int n = 0; n < NUM_BUMP_VECTS + 1; n++) free(fl->light[k][n]);
    memset(fl, 0, sizeof(*fl));
}

void BuildFacelights(int facenum) {
    g_staleSlot = _mm_setzero_ps();     /* (what's left there before a face: unknown; nothing matches vrad best) */
    dface_t *f = &g_pFaces[facenum];
    facelight_t *fl = &facelight[facenum];
    if (g_bGPU) {                       /* (this face's questions and answers; a rerun starts afresh) */
        gpucur = &gpufaces[facenum];
        gpucur->cursor = 0, gpucur->incomplete = 0;
        FreeFacelight(fl);
    }
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
        int cluster[LANES];
        float pos[LANES][3], nin[LANES][3], nout[LANES][3];
        for (int i = 0; i < LANES; i++) {
            sample_t *sp = &fl->sample[group + (i < count ? i : count - 1)];
            VectorCopy(sp->pos, pos[i]);
            VectorCopy(sp->normal, nin[i]);
        }
        SetupPoints4(&l, facenum, normalCount, isdisp, (const vec3_t *)flatBump, (const float(*)[3])pos,
                     (const float(*)[3])nin, &p, cluster, nout);
        if (!isdisp && !l.isflat)
            for (int i = 0; i < count; i++) VectorCopy(nout[i], fl->sample[group + i].normal);
        if (g_bGPU) {               /* (all the lights at once, per style slot) */
            const float *r = GpuQuery(&p, cluster, count, 0, -1);
            for (int slot = 0; slot < GPU_NumSlots(); slot++) {
                const float *rs = r + 64 * slot;
                int nonzero = 0;
                for (int i = 0; i < count; i++) nonzero |= rs[16 * i + 12] != 0;
                if (!nonzero) continue;
                int style = FindOrAllocateLightstyleSamples(f, fl, GPU_SlotStyle(slot), normalCount);
                if (style < 0) continue;
                for (int b = 0; b < normalCount; b++)
                    for (int i = 0; i < count; i++)
                        for (int k = 0; k < 3; k++) fl->light[style][b][group + i][k] += rs[16 * i + 3 * b + k];
            }
            continue;
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
                    for (int k = 0; k < 3; k++)
                        fl->light[style][b][group + i][k] += fxdot[b][i] * (out.tinted ? out.tint[b][k][i] : dl->light.intensity[k]);
        }
    }
    if (gpucur && gpucur->incomplete) return;            /* (-gpu: answers to come) */
    if (g_bExtra && !isdisp)
        for (int k = 0; k < MAXLIGHTMAPS && f->styles[k] != 255; k++) {
            BuildSupersampleFaceLights(&l, facenum, fl, k, normalCount, (const vec3_t *)flatBump);
            if (gpucur && gpucur->incomplete) return;
        }
    /* the samples' direct light (style 0) onto the face's patches, for bouncing */
    int k0;
    for (k0 = 0; k0 < MAXLIGHTMAPS; k0++)
        if (f->styles[k0] == 0) break;
    if (k0 >= MAXLIGHTMAPS) return;
    for (int i = 0; i < fl->numsamples; i++) AddSampleToPatch(facenum, fl->sample[i].pos, fl->sample[i].area, fl->light[k0][0][i]);
    FinishPatchLights(facenum);
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

static void InsertPatchSampleDataIntoHashTable(void);
static int samples_hashed, patches_hashed;

/* the displacements' sample and patch hashes, made before FinalLightFace runs in threads */
void PrepareFinalLight(void) {
    int disps = 0;
    for (int i = 0; i < numfaces && !disps; i++) disps = g_pFaces[i].dispinfo != -1;
    if (!disps) return;
    if (!samples_hashed) InsertSamplesDataIntoHashTable(), samples_hashed = 1;
    if (!patches_hashed) InsertPatchSampleDataIntoHashTable(), patches_hashed = 1;
}

static radial_t *BuildDispLuxelRadial(int facenum, int style) {
    if (!samples_hashed) InsertSamplesDataIntoHashTable(), samples_hashed = 1;
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

/* -- bounced light: each leaf patch of the face and its neighbours spreads its light over the luxels
 * within 1.42 patch sizes, weighted 2 - distance^2 (in patch sizes) */
static void AddBouncedToRadial(radial_t *rad, const vec3_t pnt, const float *cmins, const float *cmaxs, const vec3_t *light,
                               int hasBump, int neighbourBump) {
    float coord[2];
    WorldToLuxelSpace(&rad->l, pnt, coord);
    float dists = cmaxs[0] - cmins[0], distt = cmaxs[1] - cmins[1];
    dists = (float)(1.0 > dists ? 1.0 : (double)dists);
    distt = (float)(1.0 > distt ? 1.0 : (double)distt);
    int s_min = (int)(coord[0] - dists * 1.42), t_min = (int)(coord[1] - distt * 1.42);
    int s_max = (int)(coord[0] + dists * 1.42 + 1.0), t_max = (int)(coord[1] + distt * 1.42 + 1.0);
    if (s_min < 0) s_min = 0;
    if (t_min < 0) t_min = 0;
    if (s_max > rad->w) s_max = rad->w;
    if (t_max > rad->h) t_max = rad->h;
    float ootdist = 1.0f / distt;
    for (int s = s_min; s < s_max; s++) {
        float ds = (coord[0] - (float)s) / dists;
        float ds2 = ds * ds;
        for (int t = t_min; t < t_max; t++) {
            float dt = (coord[1] - (float)t) * ootdist;
            float r = 2.0f - (dt * dt + ds2);
            if (!(r > 0.0f)) continue;
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

static void PatchLightmapCoordRange(radial_t *rad, const patch_t *p, float *mins, float *maxs) {
    mins[0] = mins[1] = 1E30f;
    maxs[0] = maxs[1] = -1E30f;
    for (int i = 0; i < p->winding->numpoints; i++) {
        float c[2];
        WorldToLuxelSpace(&rad->l, p->winding->p[i], c);
        for (int k = 0; k < 2; k++) {
            mins[k] = mins[k] < c[k] ? mins[k] : c[k];
            maxs[k] = maxs[k] > c[k] ? maxs[k] : c[k];
        }
    }
}

static void AddFacePatchesToRadial(radial_t *rad, int face, int bump) {
    for (int i = face_patches[face]; i != -1; i = patches[i].next) {
        const patch_t *p = &patches[i];
        if (p->child1 != -1) continue;
        float mins[2], maxs[2];
        PatchLightmapCoordRange(rad, p, mins, maxs);
        if (g_pFaces[face].dispinfo != -1) {
            vec3_t origin;                     /* (a displacement patch's centre on its base face) */
            WindingCenter(p->winding, origin);
            AddBouncedToRadial(rad, origin, mins, maxs, (const vec3_t *)p->totallight, bump, bump);
        } else {
            AddBouncedToRadial(rad, p->origin, mins, maxs, (const vec3_t *)p->totallight, bump, bump);
        }
    }
}

static radial_t *BuildPatchRadial(int facenum) {
    int bump = texinfo[g_pFaces[facenum].texinfo].flags & SURF_BUMPLIGHT ? 1 : 0;
    radial_t *rad = AllocateRadial(facenum);
    AddFacePatchesToRadial(rad, facenum, bump);
    int nn;
    const int *n = FaceNeighbours(facenum, &nn);
    for (int j = 0; j < nn; j++) AddFacePatchesToRadial(rad, n[j], bump);   /* (with this face's bump flag, as vrad) */
    return rad;
}

/* -- displacements' bounced light: each luxel takes the leaf patches (its own face's and neighbours')
 * within the displacement's patch radius, by distance and facing (vrad's patch hash, queried once per face) */
static samplecell_t *pcells;
static int pcellcap, numpcells, *pcellhash, pcellmask;
/* (per thread: the patches already listed this time, by stamp; vrad's 16-bit key never wraps in a map's faces) */
static __thread int iteration_key, *patch_key;

static int PatchCellFind(int x, int y, int z, int add) {
    unsigned h = ((unsigned)x * 73856093u ^ (unsigned)y * 19349663u ^ (unsigned)z * 83492791u) & (unsigned)pcellmask;
    for (; pcellhash[h] != -1; h = (h + 1) & (unsigned)pcellmask) {
        samplecell_t *c = &pcells[pcellhash[h]];
        if (c->x == x && c->y == y && c->z == z) return pcellhash[h];
    }
    if (!add) return -1;
    if (numpcells == pcellcap) {
        pcellcap = pcellcap ? pcellcap * 2 : 1024;
        pcells = realloc(pcells, sizeof(samplecell_t) * pcellcap);
    }
    samplecell_t *c = &pcells[numpcells];
    memset(c, 0, sizeof(*c));
    c->x = x, c->y = y, c->z = z;
    pcellhash[h] = numpcells;
    return numpcells++;
}

static void InsertPatchSampleDataIntoHashTable(void) {
    int size = 1024;
    while (size < 2 * numpatches) size *= 2;
    pcellmask = size - 1;
    pcellhash = xalloc(sizeof(int) * size);
    for (int i = 0; i < size; i++) pcellhash[i] = -1;
    for (int f = 0; f < numfaces; f++) {
        if (texinfo[g_pFaces[f].texinfo].flags & TEX_SPECIAL) continue;
        for (int i = face_patches[f]; i != -1; i = patches[i].next) {
            if (patches[i].child1 != -1) continue;
            const float *o = patches[i].origin;
            int ci = PatchCellFind((int)(o[0] / 64.0f), (int)(o[1] / 64.0f), (int)(o[2] / 64.0f), 1);
            samplecell_t *c = &pcells[ci];
            if (c->count == c->cap) {
                c->cap = c->cap ? c->cap * 2 : 16;
                c->handles = realloc(c->handles, sizeof(int) * c->cap);
            }
            c->handles[c->count++] = i;
        }
    }
}

static int GetInterestingPatches(int facenum, float radius, int **out) {
    facelight_t *fl = &facelight[facenum];
    vec3_t lmin = {FLT_MAX, FLT_MAX, FLT_MAX}, lmax = {-FLT_MAX, -FLT_MAX, -FLT_MAX};
    for (int i = 0; i < fl->numluxels; i++)
        for (int k = 0; k < 3; k++) {
            lmin[k] = fl->luxel[i][k] < lmin[k] ? fl->luxel[i][k] : lmin[k];
            lmax[k] = fl->luxel[i][k] > lmax[k] ? fl->luxel[i][k] : lmax[k];
        }
    int amin[3], asize[3];
    for (int k = 0; k < 3; k++) {
        amin[k] = (int)((lmin[k] - radius) / 64.0f);
        asize[k] = (int)((lmax[k] + radius) / 64.0f) + 1 - amin[k];
    }
    int nbits = asize[0] * asize[1] * asize[2];
    unsigned char *bits = xalloc((nbits + 7) / 8 + 1);
    for (int i = 0; i < fl->numluxels; i++) {
        int vmin[3], vmax[3];
        for (int k = 0; k < 3; k++) {
            vmin[k] = (int)((fl->luxel[i][k] - radius) / 64.0f);
            vmax[k] = (int)((fl->luxel[i][k] + radius) / 64.0f) + 1;
        }
        for (int x = vmin[0]; x < vmax[0]; x++)
            for (int y = vmin[1]; y < vmax[1]; y++)
                for (int z = vmin[2]; z < vmax[2]; z++) {
                    int b = (z - amin[2]) * (asize[0] * asize[1]) + (y - amin[1]) * asize[0] + (x - amin[0]);
                    bits[b >> 3] |= 1 << (b & 7);
                }
    }
    if (!patch_key) patch_key = xalloc(sizeof(int) * (numpatches + 1));
    int key = ++iteration_key, n = 0, cap = 0;
    int *list = NULL;
    for (int x = 0; x < asize[0]; x++)
        for (int y = 0; y < asize[1]; y++)
            for (int z = 0; z < asize[2]; z++) {
                int b = z * (asize[0] * asize[1]) + y * asize[0] + x;
                if (!(bits[b >> 3] & (1 << (b & 7)))) continue;
                int ci = PatchCellFind(x + amin[0], y + amin[1], z + amin[2], 0);
                if (ci < 0) continue;
                for (int h = 0; h < pcells[ci].count; h++) {
                    patch_t *p = &patches[pcells[ci].handles[h]];
                    if (patch_key[pcells[ci].handles[h]] == key) continue;
                    patch_key[pcells[ci].handles[h]] = key;
                    if (!IsNeighbor(facenum, p->face)) continue;
                    if (n == cap) cap = cap ? cap * 2 : 64, list = realloc(list, sizeof(int) * cap);
                    list[n++] = pcells[ci].handles[h];
                }
            }
    free(bits);
    *out = list;
    return n;
}

static radial_t *BuildDispPatchRadial(int facenum, int bump) {
    if (!patches_hashed) InsertPatchSampleDataIntoHashTable(), patches_hashed = 1;
    facelight_t *fl = &facelight[facenum];
    radial_t *rad = AllocateRadial(facenum);
    const dispsurf_t *d = &dispsurfs[g_pFaces[facenum].dispinfo];
    float radius = (float)sqrt(d->patch_radius2), r2 = radius * radius;
    int *list, n = GetInterestingPatches(facenum, radius, &list);
    const texinfo_t *tx = &texinfo[g_pFaces[facenum].texinfo];
    for (int j = 0; j < rad->w * rad->h; j++) {
        const float *lp = fl->luxel[j], *ln = fl->luxelNormals[j];
        for (int q = 0; q < n; q++) {
            const patch_t *p = &patches[list[q]];
            int nbump = texinfo[g_pFaces[p->face].texinfo].flags & SURF_BUMPLIGHT ? 1 : 0;
            vec3_t seg;
            VectorSubtract(p->origin, lp, seg);
            float dist = sqrtf(DotProduct(seg, seg));
            float influence = 1.0f - (dist * dist) / r2;
            if (influence <= 0.0f) continue;
            if (bump) {
                vec3_t normals[NUM_BUMP_VECTS + 1], u, v;
                VectorCopy(ln, normals[0]);
                PreGetBumpNormalsForDisp(tx, u, v, normals[0]);
                GetBumpNormals(u, v, normals[0], normals[0], &normals[1]);
                float sc = DotProduct(p->normal, normals[0]);
                sc = 0.0f > sc ? 0.0f : sc;
                float bi = nbump ? influence * sc : influence * sc * 0.05f;
                for (int b = 0; b < NUM_BUMP_VECTS + 1; b++)
                    for (int k = 0; k < 3; k++) rad->light[b][j][k] += p->totallight[nbump ? b : 0][k] * bi;
                rad->weight[j] += bi;
            } else {
                float sc = DotProduct(p->normal, ln);
                sc = 0.0f > sc ? 0.0f : sc;
                influence *= sc;
                for (int k = 0; k < 3; k++) rad->light[0][j][k] += p->totallight[0][k] * influence;
                rad->weight[j] += influence;
            }
        }
    }
    free(list);
    return rad;
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
        radial_t *prad = g_numbounce > 0 && k == 0 ? (isdisp ? BuildDispPatchRadial(facenum, bump) : BuildPatchRadial(facenum)) : NULL;
        unsigned char *pdata[NUM_BUMP_VECTS + 1];
        for (int b = 0; b < bumpCount; b++) pdata[b] = dlightdata + f->lightofs + (k * bumpCount + b) * fl->numluxels * 4;
        int avgCount = 0;
        for (int j = 0; j < fl->numluxels; j++) {
            vec3_t lb[NUM_BUMP_VECTS + 1];
            int ok = isdisp ? DispSampleRadial(rad, j, lb, bumpCount) : SampleRadial(rad, fl->luxel[j], lb, bumpCount);
            if (prad) {
                vec3_t v[NUM_BUMP_VECTS + 1];
                if (isdisp) DispSampleRadial(prad, j, v, bumpCount);
                else SampleRadial(prad, fl->luxel[j], v, bumpCount);
                for (int b = 0; b < bumpCount; b++) VectorAdd(lb[b], v[b], lb[b]);
            }
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
        if (prad) FreeRadial(prad);
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

/* -gpu: every face's direct light in rounds (see GpuQuery) */
static int *roundfaces;
static void RoundWork(int i, int thread) { (void)thread; BuildFacelights(roundfaces[i]); }

void BuildFacelightsGPU(void) {
    gpufaces = xalloc(sizeof(gpuface_t) * (numfaces + 1));
    roundfaces = xalloc(sizeof(int) * (numfaces + 1));
    int nround = numfaces, R = 64 * GPU_NumSlots();
    for (int i = 0; i < numfaces; i++) roundfaces[i] = i;
    for (int round = 1; nround; round++) {
        RunThreadsOn(nround, RoundWork);
        /* every face's new questions, together */
        int nq = 0, nf = 0;
        for (int i = 0; i < nround; i++) nq += gpufaces[roundfaces[i]].npend;
        if (!nq) break;
        gpugroup_t *q = xalloc(sizeof(gpugroup_t) * nq);
        float *ans = xalloc(sizeof(float) * (size_t)R * nq), *slotout = xalloc(sizeof(float) * 64 * (size_t)nq);
        for (int i = 0, at = 0; i < nround; i++) {
            gpuface_t *g = &gpufaces[roundfaces[i]];
            memcpy(q + at, g->pend, sizeof(gpugroup_t) * g->npend);
            at += g->npend;
        }
        for (int slot = 0; slot < GPU_NumSlots(); slot++) {
            GPU_Gather(q, nq, slot, slotout);
            for (int k = 0; k < nq; k++) memcpy(ans + (size_t)R * k + 64 * slot, slotout + 64 * (size_t)k, 64 * sizeof(float));
        }
        /* the answers to their faces; those faces run again */
        for (int i = 0, at = 0; i < nround; i++) {
            gpuface_t *g = &gpufaces[roundfaces[i]];
            if (!g->npend) continue;
            g->res = realloc(g->res, sizeof(float) * (size_t)R * (g->nres + g->npend));
            memcpy(g->res + (size_t)R * g->nres, ans + (size_t)R * at, sizeof(float) * (size_t)R * g->npend);
            g->nres += g->npend, at += g->npend;
            g->npend = 0;
            roundfaces[nf++] = roundfaces[i];
        }
        free(q), free(ans), free(slotout);
        if (getenv("HLGPUDBG")) Msg("round %d: %d questions, %d faces again\n", round, nq, nf);
        nround = nf;
    }
    for (int i = 0; i < numfaces; i++) free(gpufaces[i].pend), free(gpufaces[i].res);
    free(gpufaces), free(roundfaces);
    gpufaces = NULL;
}
