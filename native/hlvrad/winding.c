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
