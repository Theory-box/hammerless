/* Vector maths and convex polygons ("windings") in 32-bit floats. */
#include <stdarg.h>
#include <float.h>
#include <xmmintrin.h>
#include "hlvbsp.h"

void Error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    printf("Error: ");
    vprintf(fmt, ap);
    printf("\n");
    va_end(ap);
    fflush(stdout);
    exit(1);
}

void Warning(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}

void Msg(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}

void *xalloc(size_t n) {
    void *p = calloc(1, n ? n : 1);
    if (!p) Error("out of memory (%u bytes)", (unsigned)n);
    return p;
}

char *copystring(const char *s) {
    char *c = xalloc(strlen(s) + 1);
    strcpy(c, s);
    return c;
}

/* printf's "%f" the way vbsp's (old MSVC) runtime writes it: an exact tie at the 6th decimal rounds
   away from zero, where ours rounds to even (10442.3828125 -> "10442.382813", not "...812").
   Returns one of a few rotating buffers, so several can sit in one printf. */
const char *FmtF(double v) {
    static char bufs[8][384];
    static int next;
    char *out = bufs[next++ & 7], exact[384];
    snprintf(exact, sizeof(exact), "%.40f", v);      /* exact for any float (and for these doubles) */
    char *dot = strchr(exact, '.');
    if (!dot || dot[7] != '5' || strspn(dot + 8, "0") != strlen(dot + 8)) {
        snprintf(out, 384, "%f", v);
        return out;
    }
    /* a tie: cut after 6 decimals and add one unit in the last place */
    dot[7] = 0;
    int i = (int)strlen(exact) - 1;
    for (; i >= 0; i--) {
        if (exact[i] == '.' || exact[i] == '-') continue;
        if (exact[i] < '9') {
            exact[i]++;
            break;
        }
        exact[i] = '0';
    }
    if (i < 0) {      /* carried past the first digit: 9.9999995 -> 10.000000 */
        int neg = exact[0] == '-';
        snprintf(out, 384, "%s1%s", neg ? "-" : "", exact + neg);
    } else strcpy(out, exact);
    return out;
}

/* printf's "%g" as vbsp's old MSVC runtime writes it: the exponent with at least 3 digits ("3.05176e-005") and an
   exact tie at the 6th significant digit rounded away from zero. Rotating buffers, as FmtF. */
const char *FmtG(double v) {
    static char bufs[8][64];
    static int next;
    char *out = bufs[next++ & 7], exact[128];
    snprintf(exact, sizeof(exact), "%.40e", v);      /* the 7th significant digit and what follows */
    if (exact[0] == '-') memmove(exact, exact + 1, strlen(exact));
    if (exact[7] == '5' && strspn(exact + 8, "0") == strcspn(exact + 8, "e")) v = nextafter(v, v < 0 ? -HUGE_VAL : HUGE_VAL);
    snprintf(out, 64, "%g", v);
    char *e = strchr(out, 'e');
    if (e) {
        int x = atoi(e + 1);
        sprintf(e, "e%c%03d", x < 0 ? '-' : '+', x < 0 ? -x : x);
    }
    return out;
}

void CrossProduct(const vec3_t a, const vec3_t b, vec3_t c) {
    c[0] = a[1] * b[2] - a[2] * b[1];
    c[1] = a[2] * b[0] - a[0] * b[2];
    c[2] = a[0] * b[1] - a[1] * b[0];
}

/* The game libraries' normalize (vbsp is built without the SSE reciprocal square root): the length,
 * then each component times 1 / (length + FLT_EPSILON). */
vec_t VectorNormalize(vec3_t v) {
    float radius = sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    float iradius = 1.f / (radius + FLT_EPSILON);
    v[0] *= iradius;
    v[1] *= iradius;
    v[2] *= iradius;
    return radius;
}

vec_t VectorLength(const vec3_t v) {
    return sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
}

void ClearBounds(vec3_t mins, vec3_t maxs) {
    mins[0] = mins[1] = mins[2] = 99999;
    maxs[0] = maxs[1] = maxs[2] = -99999;
}

void AddPointToBounds(const vec3_t p, vec3_t mins, vec3_t maxs) {
    for (int i = 0; i < 3; i++) {
        if (p[i] < mins[i]) mins[i] = p[i];
        if (p[i] > maxs[i]) maxs[i] = p[i];
    }
}

vec_t RoundInt(vec_t v) {
    return floorf(v + 0.5f);
}

/* ------------------------------------------------------------------ windings */
winding_t *AllocWinding(int points) {
    winding_t *w = xalloc(sizeof(*w));
    w->p = xalloc(sizeof(vec3_t) * (points > 0 ? points : 1));
    w->maxpoints = points;
    return w;
}

void FreeWinding(winding_t *w) {
    if (!w) return;
    free(w->p);
    free(w);
}

winding_t *CopyWinding(const winding_t *w) {
    winding_t *c = AllocWinding(w->numpoints);
    c->numpoints = w->numpoints;
    memcpy(c->p, w->p, sizeof(vec3_t) * w->numpoints);
    return c;
}

winding_t *ReverseWinding(const winding_t *w) {
    winding_t *c = AllocWinding(w->numpoints);
    for (int i = 0; i < w->numpoints; i++) VectorCopy(w->p[w->numpoints - 1 - i], c->p[i]);
    c->numpoints = w->numpoints;
    return c;
}

/* A huge square on the plane, 4 x the world's half-size along two in-plane axes. */
/* mathlib's VectorNormalize (x87): the squares summed in double, the length rounded to float, then
   times 1/(length + FLT_EPSILON). Planes, windings, bevels and t-junction edges use it. */
vec_t VectorNormalizeX87(vec3_t v) {
    return (float)VectorNormalizeX87d(v);
}

/* The same, returning the length as it stays on the x87 stack (unrounded): callers that keep
   computing in x87 (SubdivideFace) see this one. */
double VectorNormalizeX87d(vec3_t v) {
    double dlen = sqrt((double)v[0] * v[0] + (double)v[1] * v[1] + (double)v[2] * v[2]);
    float len = (float)dlen;
    float oo = 1.0f / (len + FLT_EPSILON);
    v[0] *= oo;
    v[1] *= oo;
    v[2] *= oo;
    return dlen;
}

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
    VectorMA(vup, -v, normal, vup);
    VectorNormalizeX87(vup);     /* (mathlib's VectorNormalize here: the length summed in x87 - measured) */
    VectorScale(normal, dist, org);
    CrossProduct(vup, normal, vright);
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

/* Distances of every point to the plane and which side each is on; counts per side. */
static void classify(const winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon,
                     vec_t *dists, int *sides, int *counts) {
    int i;
    counts[0] = counts[1] = counts[2] = 0;
    for (i = 0; i < in->numpoints; i++) {
        vec_t dot = DotProduct(in->p[i], normal);
        dot -= dist;
        dists[i] = dot;
        if (dot > epsilon) sides[i] = SIDE_FRONT;
        else if (dot < -epsilon) sides[i] = SIDE_BACK;
        else sides[i] = SIDE_ON;
        counts[sides[i]]++;
    }
    sides[i] = sides[0];
    dists[i] = dists[0];
}

/* Where edge p1-p2 crosses the plane; exact on axial planes. */
static void split_point(const vec_t *p1, const vec_t *p2, vec_t d1, vec_t d2, const vec3_t normal, vec_t dist, vec3_t mid) {
    vec_t dot = d1 / (d1 - d2);
    for (int j = 0; j < 3; j++) {
        if (normal[j] == 1) mid[j] = dist;
        else if (normal[j] == -1) mid[j] = -dist;
        else mid[j] = p1[j] + dot * (p2[j] - p1[j]);
    }
}

static void split(const winding_t *in, const vec3_t normal, vec_t dist, const vec_t *dists, const int *sides,
                  winding_t **front, winding_t **back) {
    int maxpts = in->numpoints + 4;
    winding_t *f = AllocWinding(maxpts), *b = back ? AllocWinding(maxpts) : NULL;
    vec3_t mid;
    for (int i = 0; i < in->numpoints; i++) {
        const vec_t *p1 = in->p[i];
        if (sides[i] == SIDE_ON) {
            VectorCopy(p1, f->p[f->numpoints]); f->numpoints++;
            if (b) { VectorCopy(p1, b->p[b->numpoints]); b->numpoints++; }
            continue;
        }
        if (sides[i] == SIDE_FRONT) { VectorCopy(p1, f->p[f->numpoints]); f->numpoints++; }
        if (sides[i] == SIDE_BACK && b) { VectorCopy(p1, b->p[b->numpoints]); b->numpoints++; }
        if (sides[i + 1] == SIDE_ON || sides[i + 1] == sides[i]) continue;
        split_point(p1, in->p[(i + 1) % in->numpoints], dists[i], dists[i + 1], normal, dist, mid);
        VectorCopy(mid, f->p[f->numpoints]); f->numpoints++;
        if (b) { VectorCopy(mid, b->p[b->numpoints]); b->numpoints++; }
    }
    if (f->numpoints > maxpts || (b && b->numpoints > maxpts)) Error("ClipWinding: points exceeded estimate");
    if (f->numpoints > MAX_POINTS_ON_WINDING || (b && b->numpoints > MAX_POINTS_ON_WINDING))
        Error("ClipWinding: MAX_POINTS_ON_WINDING");
    *front = f;
    if (back) *back = b;
}

void ClipWindingEpsilon(const winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon,
                        winding_t **front, winding_t **back) {
    vec_t dists[MAX_POINTS_ON_WINDING + 4];
    int sides[MAX_POINTS_ON_WINDING + 4], counts[3];
    classify(in, normal, dist, epsilon, dists, sides, counts);
    *front = *back = NULL;
    if (!counts[0]) { *back = CopyWinding(in); return; }
    if (!counts[1]) { *front = CopyWinding(in); return; }
    split(in, normal, dist, dists, sides, front, back);
}

void ClassifyWindingEpsilon(const winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon,
                            winding_t **front, winding_t **back, winding_t **on) {
    vec_t dists[MAX_POINTS_ON_WINDING + 4];
    int sides[MAX_POINTS_ON_WINDING + 4], counts[3];
    classify(in, normal, dist, epsilon, dists, sides, counts);
    *front = *back = *on = NULL;
    if (!counts[0] && !counts[1]) { *on = CopyWinding(in); return; }
    if (!counts[0]) { *back = CopyWinding(in); return; }
    if (!counts[1]) { *front = CopyWinding(in); return; }
    split(in, normal, dist, dists, sides, front, back);
}

void TranslateWinding(winding_t *w, const vec3_t offset) {
    for (int i = 0; i < w->numpoints; i++) VectorAdd(w->p[i], offset, w->p[i]);
}

/* Clip with everything moved by offset first (keeps precision near the origin), then moved back. */
void ClipWindingEpsilonOffset(winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon,
                              winding_t **front, winding_t **back, const vec3_t offset) {
    vec3_t neg;
    VectorNegate(offset, neg);
    TranslateWinding(in, offset);
    ClipWindingEpsilon(in, normal, dist + DotProduct(offset, normal), epsilon, front, back);
    TranslateWinding(in, neg);
    if (*front) TranslateWinding(*front, neg);
    if (*back) TranslateWinding(*back, neg);
}

void ClassifyWindingEpsilonOffset(winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon,
                                  winding_t **front, winding_t **back, winding_t **on, const vec3_t offset) {
    vec3_t neg;
    VectorNegate(offset, neg);
    TranslateWinding(in, offset);
    ClassifyWindingEpsilon(in, normal, dist + DotProduct(offset, normal), epsilon, front, back, on);
    TranslateWinding(in, neg);
    if (*front) TranslateWinding(*front, neg);
    if (*back) TranslateWinding(*back, neg);
    if (*on) TranslateWinding(*on, neg);
}

/* Keep the part in front of the plane; frees the original when it changes. */
void ChopWindingInPlace(winding_t **inout, const vec3_t normal, vec_t dist, vec_t epsilon) {
    winding_t *in = *inout, *f;
    vec_t dists[MAX_POINTS_ON_WINDING + 4];
    int sides[MAX_POINTS_ON_WINDING + 4], counts[3];
    classify(in, normal, dist, epsilon, dists, sides, counts);
    if (!counts[0]) {
        FreeWinding(in);
        *inout = NULL;
        return;
    }
    if (!counts[1]) return;
    split(in, normal, dist, dists, sides, &f, NULL);
    FreeWinding(in);
    *inout = f;
}

vec_t WindingArea(const winding_t *w) {
    vec3_t d1, d2, cross;
    vec_t total = 0;
    for (int i = 2; i < w->numpoints; i++) {
        VectorSubtract(w->p[i - 1], w->p[0], d1);
        VectorSubtract(w->p[i], w->p[0], d2);
        CrossProduct(d1, d2, cross);
        /* (L4D2's vbsp sums y, z, then x - measured) */
        total += sqrtf((cross[1] * cross[1] + cross[2] * cross[2]) + cross[0] * cross[0]);
    }
    return total * 0.5f;
}

void WindingCenter(const winding_t *w, vec3_t center) {
    VectorClear(center);
    for (int i = 0; i < w->numpoints; i++) VectorAdd(w->p[i], center, center);
    float scale = 1.0 / w->numpoints;
    VectorScale(center, scale, center);
}

void WindingPlane(const winding_t *w, vec3_t normal, vec_t *dist) {
    vec3_t v1, v2;
    VectorSubtract(w->p[1], w->p[0], v1);
    if (w->numpoints > 3) VectorSubtract(w->p[3], w->p[0], v2);    /* avoids three points in a line */
    else VectorSubtract(w->p[2], w->p[0], v2);
    CrossProduct(v2, v1, normal);
    VectorNormalize(normal);
    *dist = DotProduct(w->p[0], normal);
}

void WindingBounds(const winding_t *w, vec3_t mins, vec3_t maxs) {
    mins[0] = mins[1] = mins[2] = 99999;
    maxs[0] = maxs[1] = maxs[2] = -99999;
    for (int i = 0; i < w->numpoints; i++)
        for (int j = 0; j < 3; j++) {
            vec_t v = w->p[i][j];
            if (v < mins[j]) mins[j] = v;
            if (v > maxs[j]) maxs[j] = v;
        }
}

/* True when fewer than three edges are longer than 0.2 units (vertex snapping would crush it). */
int WindingIsTiny(const winding_t *w) {
    int edges = 0;
    vec3_t delta;
    for (int i = 0; i < w->numpoints; i++) {
        int j = i == w->numpoints - 1 ? 0 : i + 1;
        VectorSubtract(w->p[j], w->p[i], delta);
        if (VectorLength(delta) > 0.2) {
            if (++edges == 3) return 0;
        }
    }
    return 1;
}

int WindingIsHuge(const winding_t *w) {
    for (int i = 0; i < w->numpoints; i++)
        for (int j = 0; j < 3; j++)
            if (w->p[i][j] < MIN_COORD_INTEGER || w->p[i][j] > MAX_COORD_INTEGER) return 1;
    return 0;
}
