/* Polygons (vrad's polylib, as L4D2's build computes it). */
#include "hlvrad.h"

winding_t *AllocWinding(int points) {
    winding_t *w = xalloc(sizeof(winding_t) + sizeof(vec3_t) * (points > 0 ? points : 1));
    w->p = (vec3_t *)(w + 1);
    return w;
}

void FreeWinding(winding_t *w) { free(w); }

/* the face's corners moved by origin (a brush entity's), then nearly straight corners dropped */
winding_t *WindingFromFace(const dface_t *f, const vec3_t origin) {
    winding_t *w = AllocWinding(f->numedges);
    w->numpoints = f->numedges;
    for (int i = 0; i < f->numedges; i++) {
        const float *v = dvertexes[EdgeVertex(f, i)].point;
        VectorAdd(v, origin, w->p[i]);
    }
    RemoveColinearPoints(w);
    return w;
}

/* a corner stays when its two edges turn by more than acos(0.999) (about 2.6 degrees) */
void RemoveColinearPoints(winding_t *w) {
    vec3_t keep[64], v1, v2;
    int nump = 0;
    for (int i = 0; i < w->numpoints; i++) {
        int j = (i + 1) % w->numpoints, k = (i + w->numpoints - 1) % w->numpoints;
        VectorSubtract(w->p[j], w->p[i], v1);
        VectorSubtract(w->p[i], w->p[k], v2);
        VectorNormalize(v1);
        VectorNormalize(v2);
        float dot = DotProduct(v1, v2);
        if ((double)dot < 0.999 && nump < 64) {
            VectorCopy(w->p[i], keep[nump]);
            nump++;
        }
    }
    if (nump == w->numpoints) return;
    w->numpoints = nump;
    memcpy(w->p, keep, sizeof(vec3_t) * nump);
}

/* a fan of triangles from the first corner; each cross product's length summed y, z, then x */
float WindingArea(const winding_t *w) {
    float total = 0;
    for (int i = 2; i < w->numpoints; i++) {
        float d1x = w->p[i - 1][0] - w->p[0][0], d1y = w->p[i - 1][1] - w->p[0][1], d1z = w->p[i - 1][2] - w->p[0][2];
        float d2x = w->p[i][0] - w->p[0][0], d2y = w->p[i][1] - w->p[0][1], d2z = w->p[i][2] - w->p[0][2];
        float cx = d2z * d1y - d2y * d1z, cy = d1z * d2x - d2z * d1x, cz = d2y * d1x - d2x * d1y;
        total += sqrtf((cy * cy + cz * cz) + cx * cx);
    }
    return total * 0.5f;
}

/* ------------------------------------------------------------------ clipping (as vbsp / vrad's polylib) */
#define MAX_COORD_INTEGER 16384
#define SIDE_FRONT 0
#define SIDE_BACK 1
#define SIDE_ON 2

/* a huge square on the plane, 4 x the world's half-size along two in-plane axes */
winding_t *BaseWindingForPlane(const vec3_t normal, vec_t dist) {
    int x = -1;
    vec_t max = -1, v;
    for (int i = 0; i < 3; i++) {
        v = fabsf(normal[i]);
        if (v > max) {
            x = i;
            max = v;
        }
    }
    if (x == -1) Error("BaseWindingForPlane: no axis found");
    vec3_t vup = {0, 0, 0}, org, vright;
    if (x == 2) vup[0] = 1;
    else vup[2] = 1;
    v = DotProduct(vup, normal);
    vup[0] += -v * normal[0];
    vup[1] += -v * normal[1];
    vup[2] += -v * normal[2];
    VectorNormalize(vup);
    VectorScale(normal, dist, org);
    vright[0] = vup[1] * normal[2] - vup[2] * normal[1];
    vright[1] = vup[2] * normal[0] - vup[0] * normal[2];
    vright[2] = vup[0] * normal[1] - vup[1] * normal[0];
    VectorScale(vup, (MAX_COORD_INTEGER * 4), vup);
    VectorScale(vright, (MAX_COORD_INTEGER * 4), vright);
    winding_t *w = AllocWinding(4);
    VectorSubtract(org, vright, w->p[0]);
    VectorAdd(w->p[0], vup, w->p[0]);
    VectorAdd(org, vright, w->p[1]);
    VectorAdd(w->p[1], vup, w->p[1]);
    VectorAdd(org, vright, w->p[2]);
    VectorSubtract(w->p[2], vup, w->p[2]);
    VectorSubtract(org, vright, w->p[3]);
    VectorSubtract(w->p[3], vup, w->p[3]);
    w->numpoints = 4;
    return w;
}

/* keep the part in front of the plane; frees the original when it changes (NULL when nothing is left) */
void ChopWindingInPlace(winding_t **inout, const vec3_t normal, vec_t dist, vec_t epsilon) {
    winding_t *in = *inout;
    int n = in->numpoints, counts[3] = {0, 0, 0};
    float *dists = xalloc(sizeof(float) * (n + 1));
    int *sides = xalloc(sizeof(int) * (n + 1));
    for (int i = 0; i < n; i++) {
        float dot = DotProduct(in->p[i], normal) - dist;
        dists[i] = dot;
        sides[i] = dot > epsilon ? SIDE_FRONT : dot < -epsilon ? SIDE_BACK : SIDE_ON;
        counts[sides[i]]++;
    }
    sides[n] = sides[0];
    dists[n] = dists[0];
    if (!counts[SIDE_FRONT]) {
        FreeWinding(in);
        *inout = NULL;
    } else if (counts[SIDE_BACK]) {
        winding_t *f = AllocWinding(n + 4);
        for (int i = 0; i < n; i++) {
            const float *p1 = in->p[i];
            if (sides[i] == SIDE_ON) {
                VectorCopy(p1, f->p[f->numpoints]);
                f->numpoints++;
                continue;
            }
            if (sides[i] == SIDE_FRONT) {
                VectorCopy(p1, f->p[f->numpoints]);
                f->numpoints++;
            }
            if (sides[i + 1] == SIDE_ON || sides[i + 1] == sides[i]) continue;
            const float *p2 = in->p[(i + 1) % n];
            float dot = dists[i] / (dists[i] - dists[i + 1]);
            vec3_t mid;
            for (int j = 0; j < 3; j++) {
                if (normal[j] == 1) mid[j] = dist;
                else if (normal[j] == -1) mid[j] = -dist;
                else mid[j] = p1[j] + dot * (p2[j] - p1[j]);
            }
            VectorCopy(mid, f->p[f->numpoints]);
            f->numpoints++;
        }
        FreeWinding(in);
        *inout = f;
    }
    free(dists);
    free(sides);
}
