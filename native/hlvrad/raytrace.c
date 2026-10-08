/* The ray tracer vrad lights with (Valve's RayTracingEnvironment, rebuilt).
 *
 * Triangles: the sides of every opaque world brush (made from the brush's planes, so hidden sides count
 * too), the world's sky faces (marked sky), displacements and static props (to come). A ray stops at the
 * nearest triangle. The triangle test is Valve's to the float operation (plane distance, then two edge
 * equations scaled to 1 at the opposite corner, with L4D2's build multiplying by reciprocals), so hits
 * and misses at shadow edges come out the same; the acceleration structure is our own (a bounding
 * volume hierarchy), which finds the same nearest triangle. */
#include <xmmintrin.h>
#include "hlvrad.h"

typedef struct {
    float nx, ny, nz, d;
    float e[6];
    unsigned char c0, c1;
    int id;
} rttri_t;

typedef struct {
    float mins[3], maxs[3];
    int left, count;           /* leaf: count > 0, triangles start at left; else children left, left + 1 */
} bvhnode_t;

static rttri_t *tris;
static float (*tri_verts)[9];
static int numtris, maxtris;
static bvhnode_t *nodes;
static int numnodes_rt;
static int *tri_index;

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

/* ------------------------------------------------------------------ the hierarchy (binned SAH) */
static float *centroid;

static void NodeBounds(bvhnode_t *n, int first, int count) {
    for (int k = 0; k < 3; k++) n->mins[k] = 1e30f, n->maxs[k] = -1e30f;
    for (int i = first; i < first + count; i++) {
        const float *v = tri_verts[tri_index[i]];
        for (int j = 0; j < 9; j++) {
            int k = j % 3;
            if (v[j] < n->mins[k]) n->mins[k] = v[j];
            if (v[j] > n->maxs[k]) n->maxs[k] = v[j];
        }
    }
}

static float Area(const float *mins, const float *maxs) {
    float dx = maxs[0] - mins[0], dy = maxs[1] - mins[1], dz = maxs[2] - mins[2];
    return dx * dy + dy * dz + dz * dx;
}

#define BINS 16
static void Build(int ni, int first, int count) {
    bvhnode_t *n = &nodes[ni];
    NodeBounds(n, first, count);
    if (count <= 4) {
        n->left = first;
        n->count = count;
        return;
    }
    float cmin[3] = {1e30f, 1e30f, 1e30f}, cmax[3] = {-1e30f, -1e30f, -1e30f};
    for (int i = first; i < first + count; i++)
        for (int k = 0; k < 3; k++) {
            float c = centroid[3 * tri_index[i] + k];
            if (c < cmin[k]) cmin[k] = c;
            if (c > cmax[k]) cmax[k] = c;
        }
    int best_axis = -1, best_split = 0;
    float best_cost = Area(n->mins, n->maxs) * count;
    for (int axis = 0; axis < 3; axis++) {
        float ext = cmax[axis] - cmin[axis];
        if (ext <= 0) continue;
        int bcount[BINS] = {0};
        float bmin[BINS][3], bmax[BINS][3];
        for (int b = 0; b < BINS; b++)
            for (int k = 0; k < 3; k++) bmin[b][k] = 1e30f, bmax[b][k] = -1e30f;
        for (int i = first; i < first + count; i++) {
            int t = tri_index[i];
            int b = (int)((centroid[3 * t + axis] - cmin[axis]) / ext * BINS);
            if (b >= BINS) b = BINS - 1;
            bcount[b]++;
            for (int j = 0; j < 9; j++) {
                int k = j % 3;
                if (tri_verts[t][j] < bmin[b][k]) bmin[b][k] = tri_verts[t][j];
                if (tri_verts[t][j] > bmax[b][k]) bmax[b][k] = tri_verts[t][j];
            }
        }
        for (int s = 1; s < BINS; s++) {
            float lmin[3] = {1e30f, 1e30f, 1e30f}, lmax[3] = {-1e30f, -1e30f, -1e30f};
            float rmin[3] = {1e30f, 1e30f, 1e30f}, rmax[3] = {-1e30f, -1e30f, -1e30f};
            int lc = 0, rc = 0;
            for (int b = 0; b < BINS; b++) {
                if (!bcount[b]) continue;
                float *mn = b < s ? lmin : rmin, *mx = b < s ? lmax : rmax;
                for (int k = 0; k < 3; k++) {
                    if (bmin[b][k] < mn[k]) mn[k] = bmin[b][k];
                    if (bmax[b][k] > mx[k]) mx[k] = bmax[b][k];
                }
                if (b < s) lc += bcount[b];
                else rc += bcount[b];
            }
            if (!lc || !rc) continue;
            float cost = Area(lmin, lmax) * lc + Area(rmin, rmax) * rc;
            if (cost < best_cost) {
                best_cost = cost;
                best_axis = axis;
                best_split = s;
            }
        }
    }
    if (best_axis < 0) {
        n->left = first;
        n->count = count;
        return;
    }
    float ext = cmax[best_axis] - cmin[best_axis];
    int i = first, j = first + count - 1;
    while (i <= j) {
        int t = tri_index[i];
        int b = (int)((centroid[3 * t + best_axis] - cmin[best_axis]) / ext * BINS);
        if (b >= BINS) b = BINS - 1;
        if (b < best_split) i++;
        else {
            tri_index[i] = tri_index[j];
            tri_index[j--] = t;
        }
    }
    int lc = i - first;
    int l = numnodes_rt;
    numnodes_rt += 2;
    n = &nodes[ni];
    n->left = l;
    n->count = 0;
    Build(l, first, lc);
    Build(l + 1, i, count - lc);
}

void RT_SetupAccelerationStructure(void) {
    for (int i = 0; i < numtris; i++) IntersectionFormat(i);
    tri_index = xalloc(sizeof(int) * (numtris + 1));
    centroid = xalloc(sizeof(float) * 3 * (numtris + 1));
    for (int i = 0; i < numtris; i++) {
        tri_index[i] = i;
        for (int k = 0; k < 3; k++)
            centroid[3 * i + k] = (tri_verts[i][k] + tri_verts[i][3 + k] + tri_verts[i][6 + k]) * (1.0f / 3.0f);
    }
    nodes = xalloc(sizeof(bvhnode_t) * (2 * numtris + 2));
    numnodes_rt = 1;
    if (numtris) Build(0, 0, numtris);
    free(centroid);
}

/* ------------------------------------------------------------------ tracing */
static int RayBox(const bvhnode_t *n, const float *o, const float *inv, float tmax) {
    float t0 = 0, t1 = tmax;
    for (int k = 0; k < 3; k++) {
        float a = (n->mins[k] - o[k]) * inv[k], b = (n->maxs[k] - o[k]) * inv[k];
        if (a > b) {
            float t = a;
            a = b;
            b = t;
        }
        if (a > t0) t0 = a;
        if (b < t1) t1 = b;
        if (t0 > t1 * 1.0000001f + 1e-4f) return 0;    /* (generous: the box test must not miss) */
    }
    return 1;
}

/* the nearest triangle along the ray (dir unit length), other than those with id skip_id; -1 if none.
 * As Valve's: a hit needs |dir.N| > 1e-10, 0 < t < the best so far, and both edge values >= 0 with their
 * sum <= 1. */
int RT_TraceRay(const vec3_t o, const vec3_t dir, float tmax, int skip_id, float *hitdist) {
    float best = 1.0e23f;
    int hit = -1;
    if (!numtris) {
        *hitdist = best;
        return -1;
    }
    float inv[3];
    for (int k = 0; k < 3; k++) inv[k] = dir[k] != 0 ? 1.0f / dir[k] : (dir[k] < 0 || signbit(dir[k]) ? -1e30f : 1e30f);
    int stack[128], sp = 0;
    stack[sp++] = 0;
    while (sp) {
        const bvhnode_t *n = &nodes[stack[--sp]];
        float limit = best < tmax ? best : tmax;
        if (!RayBox(n, o, inv, limit * 1.001f + 1.0f)) continue;
        if (n->count) {
            for (int i = n->left; i < n->left + n->count; i++) {
                int ti = tri_index[i];
                const rttri_t *t = &tris[ti];
                if (t->id == skip_id) continue;
                float ddotn = (dir[0] * t->nx + dir[1] * t->ny) + dir[2] * t->nz;
                if (!(ddotn > 1.0e-10f || ddotn < -1.0e-10f)) continue;
                float numer = t->d - ((o[0] * t->nx + o[1] * t->ny) + o[2] * t->nz);
                float isect = numer / ddotn;
                if (!(isect > 0.0f) || !(isect < best)) continue;
                float h1 = o[t->c0] + isect * dir[t->c0];
                float h2 = o[t->c1] + isect * dir[t->c1];
                float b0 = (t->e[0] * h1 + t->e[1] * h2) + t->e[2];
                if (!(b0 >= 0.0f)) continue;
                float b1 = (t->e[3] * h1 + t->e[4] * h2) + t->e[5];
                if (!(b1 >= 0.0f)) continue;
                if (!(b1 + b0 <= 1.0f)) continue;
                best = isect;
                hit = ti;
            }
        } else {
            if (sp + 2 > 128) Error("ray tracer: stack overflow");
            stack[sp++] = n->left + 1;
            stack[sp++] = n->left;
        }
    }
    *hitdist = best;
    return hit;
}

int RT_TriangleID(int tri) { return tris[tri].id; }

/* SSE's reciprocal estimate refined once (Valve's ReciprocalSIMD) */
static float ReciprocalSSE(float a) {
    __m128 x = _mm_set_ss(a), r = _mm_rcp_ss(x);
    r = _mm_sub_ss(_mm_add_ss(r, r), _mm_mul_ss(x, _mm_mul_ss(r, r)));
    return _mm_cvtss_f32(r);
}

/* start -> stop: the unit direction and the length, as vrad's TestLine makes them */
static float RayFromSegment(const vec3_t start, const vec3_t stop, vec3_t dir) {
    VectorSubtract(stop, start, dir);
    float len = sqrtf((dir[0] * dir[0] + dir[1] * dir[1]) + dir[2] * dir[2]);
    float r = ReciprocalSSE(len);
    VectorScale(dir, r, dir);
    return len;
}

/* 1 when nothing blocks start -> stop */
float TestLine(const vec3_t start, const vec3_t stop, int static_prop_to_skip) {
    vec3_t dir;
    float len = RayFromSegment(start, stop, dir), dist;
    int hit = RT_TraceRay(start, dir, len, TRACE_ID_STATICPROP | static_prop_to_skip, &dist);
    return hit != -1 && dist < len ? 0.0f : 1.0f;
}

/* how much of the line reaches the sky: 0 when the first thing it meets is not sky
 * TODO: 3D skybox recursion */
float TestLine_DoesHitSky(const vec3_t start, const vec3_t stop, int static_prop_to_skip) {
    vec3_t dir;
    float len = RayFromSegment(start, stop, dir), dist;
    int hit = RT_TraceRay(start, dir, len, TRACE_ID_STATICPROP | static_prop_to_skip, &dist);
    float occlusion = 0.0f;
    if (hit != -1 && dist < len && !(tris[hit].id & TRACE_ID_SKY)) occlusion = 1.0f;
    return 1.0f - occlusion;
}

/* ------------------------------------------------------------------ the world's triangles */
static void AddBrush(int b) {
    const dbrush_t *brush = &dbrushes[b];
    if (!(brush->contents & MASK_OPAQUE)) return;
    for (int i = 0; i < brush->numsides; i++) {
        const dbrushside_t *side = &dbrushsides[brush->firstside + i];
        const dplane_t *plane = &dplanes[side->planenum];
        int tflags = side->texinfo >= 0 && side->texinfo < numtexinfo ? texinfo[side->texinfo].flags : 0;
        if ((tflags & SURF_SKY) || side->dispinfo) continue;
        winding_t *w = BaseWindingForPlane(plane->normal, plane->dist);
        for (int j = 0; j < brush->numsides && w; j++) {
            if (i == j) continue;
            const dbrushside_t *other = &dbrushsides[brush->firstside + j];
            if (other->bevel) continue;
            const dplane_t *p2 = &dplanes[other->planenum ^ 1];
            ChopWindingInPlace(&w, p2->normal, p2->dist, 0);
        }
        if (!w) continue;
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
