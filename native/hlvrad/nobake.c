/* No Bake Volumes (Hammerless's own, -nobake <file>): places the lighting isn't worked out. A point there gets the
 * map's ambient colour (the light_environment's ambient) instead of baked light; surfaces there still cast shadows.
 *
 * The file: one convex brush a line, "<invert> <planes> nx ny nz d ..." (inside where n.p <= d for every plane).
 * A point isn't baked when it's inside a brush that isn't inverted, or when there are inverted brushes ("bake only
 * inside") and it's inside none of them. */
#include "hlvrad.h"

typedef struct {
    int invert, n;
    float (*pl)[4];
} nbhull_t;

static nbhull_t *hulls;
static int numhulls, anyInvert;
int g_bNoBake;

void LoadNoBake(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) Error("Can't open %s", path);
    char line[65536];
    while (fgets(line, sizeof(line), f)) {
        char *s = line, *e;
        long inv = strtol(s, &e, 10);
        if (e == s) continue;
        s = e;
        long n = strtol(s, &e, 10);
        if (e == s || n < 1 || n > 4096) Error("%s: bad volume", path);
        s = e;
        nbhull_t h = {inv != 0, (int)n, xalloc(sizeof(float[4]) * (size_t)n)};
        for (int k = 0; k < 4 * n; k++) {
            h.pl[k / 4][k % 4] = strtof(s, &e);
            if (e == s) Error("%s: bad volume", path);
            s = e;
        }
        hulls = realloc(hulls, sizeof(nbhull_t) * (numhulls + 1));
        hulls[numhulls++] = h;
        if (h.invert) anyInvert = 1;
    }
    fclose(f);
    g_bNoBake = numhulls > 0;
    Msg("%d no bake volume brush(es)\n", numhulls);
}

static int InHull(const nbhull_t *h, const float *p) {
    for (int k = 0; k < h->n; k++)
        if (h->pl[k][0] * p[0] + h->pl[k][1] * p[1] + h->pl[k][2] * p[2] > h->pl[k][3]) return 0;
    return 1;
}

int NoBakePoint(const float *p) {
    if (!g_bNoBake) return 0;
    int inInvert = 0;
    for (int i = 0; i < numhulls; i++) {
        if (!InHull(&hulls[i], p)) continue;
        if (!hulls[i].invert) return 1;
        inInvert = 1;
    }
    return anyInvert && !inInvert;
}

void NoBakeColor(vec3_t out) {
    if (gAmbient) VectorCopy(gAmbient->light.intensity, out);
    else out[0] = out[1] = out[2] = 0;
}
