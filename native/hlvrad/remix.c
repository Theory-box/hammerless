/* -remix <map>.hllayers: Live Light Mixing's result kept without baking again. The map was baked with -layers (each
 * light's own light kept apart, direct.c's WriteLayers); since then only lights' colour and brightness changed (the
 * build checks: buildplan.py). Light adds up, bounces too, so the lighting now is the lighting baked plus, for each
 * layer, (its weight now - the weight the map has it at) times the layer, its weight being its lights' intensity now
 * over baked, per colour. That goes into every lightmap page of the layer's style (bump pages too); the rest of
 * the lighting (the lights for models, leaf ambient, props' and detail props' light) is made from the result as a
 * bake makes it. The layers file then says the weights the map has. */
#include <float.h>
#include "hlvrad.h"

#define NUM_BUMP_VECTS 3               /* (as direct.c) */
#define MAXLIGHTMAPS 4

void VectorToColorRGBExp32(const vec3_t v, unsigned char *c);

typedef struct {
    char *info;                 /* its "key=value" lines */
    int infolen;
    size_t data, end;           /* where its faces are in the file */
    int style;
    vec3_t baked, applied;
} rlayer_t;

static const char *InfoValue(const char *info, const char *key) {
    size_t n = strlen(key);
    for (const char *p = info; p && *p;) {
        if (!strncmp(p, key, n) && p[n] == '=') return p + n + 1;
        p = strchr(p, '\n');
        if (p) p++;
    }
    return NULL;
}

static void InfoVector(const char *info, const char *key, const float *dflt, vec3_t out) {
    const char *v = InfoValue(info, key);
    VectorCopy(dflt, out);
    if (v) sscanf(v, "%f %f %f", &out[0], &out[1], &out[2]);
}

static float HalfToFloat(unsigned short h) {
    unsigned int sign = (unsigned int)(h & 0x8000u) << 16, e = (h >> 10) & 0x1f, m = h & 0x3ffu, x;
    if (e == 0) {
        float f = ldexpf((float)m, -24);
        return sign ? -f : f;
    }
    if (e == 31) x = sign | 0x7f800000u | (m << 13);
    else x = sign | ((e + 112) << 23) | (m << 13);
    float f;
    memcpy(&f, &x, 4);
    return f;
}

static int Cmp(const void *a, const void *b) {
    float x = *(const float *)a, y = *(const float *)b;
    return x < y ? -1 : x > y;
}

/* the median colour of a face's style's flat page, stored before lightofs in reverse style order (as FinalLightFace) */
static void FaceAverage(dface_t *f, int k, int luxels, int bumpCount) {
    float *c[3];
    for (int i = 0; i < 3; i++) c[i] = xalloc(sizeof(float) * (luxels + 1));
    const unsigned char *p = dlightdata + f->lightofs + (size_t)k * bumpCount * luxels * 4;
    for (int j = 0; j < luxels; j++)
        for (int i = 0; i < 3; i++) c[i][j] = ldexpf((float)p[4 * j + i], (signed char)p[4 * j + 3]);
    vec3_t median;
    for (int i = 0; i < 3; i++) qsort(c[i], luxels, sizeof(float), Cmp), median[i] = c[i][luxels >> 1], free(c[i]);
    VectorToColorRGBExp32(median, dlightdata + f->lightofs - 4 * (k + 1));
}

void Remix(const char *path) {
    FILE *fp = fopen(path, "rb");
    if (!fp) Error("-remix: can't read %s", path);
    fseek(fp, 0, SEEK_END);
    size_t size = (size_t)ftell(fp);
    fseek(fp, 0, SEEK_SET);
    unsigned char *file = xalloc(size + 1);
    if (fread(file, 1, size, fp) != size) Error("-remix: can't read %s", path);
    fclose(fp);
    int head[4];
    memcpy(head, file, 16);
    if (head[0] != 0x594c4c48 || head[1] != 3) Error("-remix: %s isn't a light layers file (version 3)", path);
    if (head[2] != numfaces) Error("-remix: %s is another bake's (%d faces, the map has %d)", path, head[2], numfaces);
    int nlayers = head[3];
    rlayer_t *L = xalloc(sizeof(rlayer_t) * (nlayers + 1));
    size_t at = 16;
    for (int l = 0; l < nlayers; l++) {         /* (what each was baked with, where its faces are) */
        int len;
        memcpy(&len, file + at, 4);
        L[l].info = xalloc(len + 1), L[l].infolen = len;
        memcpy(L[l].info, file + at + 4, len);
        at += 4 + len;
        L[l].data = at;
        int n;
        memcpy(&n, file + at, 4);
        at += 4;
        for (int i = 0; i < n; i++) {
            int h[3];
            memcpy(h, file + at, 12);
            at += 12 + 6 * (size_t)h[1] * h[2];
        }
        L[l].end = at;
        const char *st = InfoValue(L[l].info, "style");
        L[l].style = st ? atoi(st) : 0;
        static const float zero[3] = {0, 0, 0}, one[3] = {1, 1, 1};
        InfoVector(L[l].info, "intensity", zero, L[l].baked);
        InfoVector(L[l].info, "applied", one, L[l].applied);
    }
    /* the weights now: each layer's lights' intensity now (from the map's entities, AssignLayers) over baked */
    vec3_t *now = xalloc(sizeof(vec3_t) * (nlayers + 1));
    for (directlight_t *dl = activelights; dl; dl = dl->next)
        if (dl->layer >= 1 && dl->layer <= nlayers) VectorAdd(now[dl->layer - 1], dl->light.intensity, now[dl->layer - 1]);
    unsigned char *touched = xalloc((size_t)numfaces * MAXLIGHTMAPS + 1);
    int changed = 0;
    for (int l = 0; l < nlayers; l++) {
        vec3_t w, delta;
        int any = 0;
        for (int i = 0; i < 3; i++) {
            w[i] = L[l].baked[i] > 0 ? now[l][i] / L[l].baked[i] : L[l].applied[i];
            delta[i] = w[i] - L[l].applied[i];
            any |= fabsf(delta[i]) > 1e-6f;
        }
        Msg("layer %d (%s): weight %.3f %.3f %.3f\n", l + 1, InfoValue(L[l].info, "_hlobject") ? InfoValue(L[l].info, "_hlobject") : "?",
            w[0], w[1], w[2]);
        VectorCopy(w, L[l].applied);
        if (!any) continue;
        changed++;
        size_t p = L[l].data;
        int n;
        memcpy(&n, file + p, 4);
        p += 4;
        for (int i = 0; i < n; i++) {
            int h[3];
            memcpy(h, file + p, 12);
            const unsigned short *v = (const unsigned short *)(file + p + 12);
            p += 12 + 6 * (size_t)h[1] * h[2];
            if (h[0] < 0 || h[0] >= numfaces) Error("-remix: bad face %d in %s", h[0], path);
            dface_t *f = &g_pFaces[h[0]];
            int luxels = (f->m_LightmapTextureSizeInLuxels[0] + 1) * (f->m_LightmapTextureSizeInLuxels[1] + 1);
            int bumpCount = texinfo[f->texinfo].flags & SURF_BUMPLIGHT ? NUM_BUMP_VECTS + 1 : 1;
            if (luxels != h[1] || bumpCount != h[2]) Error("-remix: %s is another bake's (face %d differs)", path, h[0]);
            int k;
            for (k = 0; k < MAXLIGHTMAPS && f->styles[k] != L[l].style; k++) {}
            if (k == MAXLIGHTMAPS || f->lightofs < 0) continue;       /* (the bake had no room for its style here) */
            for (int b = 0; b < bumpCount; b++) {
                unsigned char *lm = dlightdata + f->lightofs + ((size_t)k * bumpCount + b) * luxels * 4;
                for (int j = 0; j < luxels; j++) {
                    vec3_t c;
                    for (int ch = 0; ch < 3; ch++) {
                        float base = ldexpf((float)lm[4 * j + ch], (signed char)lm[4 * j + 3]);
                        float add = delta[ch] * HalfToFloat(v[3 * ((size_t)b * luxels + j) + ch]);
                        c[ch] = base + add > 0 ? base + add : 0;
                    }
                    VectorToColorRGBExp32(c, lm + 4 * j);
                }
            }
            touched[(size_t)h[0] * MAXLIGHTMAPS + k] = 1;
        }
    }
    for (int facenum = 0; facenum < numfaces; facenum++)
        for (int k = 0; k < MAXLIGHTMAPS; k++)
            if (touched[(size_t)facenum * MAXLIGHTMAPS + k]) {
                dface_t *f = &g_pFaces[facenum];
                int luxels = (f->m_LightmapTextureSizeInLuxels[0] + 1) * (f->m_LightmapTextureSizeInLuxels[1] + 1);
                FaceAverage(f, k, luxels, texinfo[f->texinfo].flags & SURF_BUMPLIGHT ? NUM_BUMP_VECTS + 1 : 1);
            }
    Msg("%d of %d light layers changed\n", changed, nlayers);
    /* the layers file with the weights the map has now (written whole, then renamed) */
    char tmp[1100];
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    FILE *out = fopen(tmp, "wb");
    if (!out) Error("Can't write %s", tmp);
    fwrite(file, 1, 16, out);
    for (int l = 0; l < nlayers; l++) {
        char info[4400];
        int n = 0;
        for (const char *p = L[l].info; *p;) {             /* (its lines, "applied" as now) */
            const char *e = strchr(p, '\n');
            size_t len = e ? (size_t)(e - p) : strlen(p);
            if (!strncmp(p, "applied=", 8))
                n += snprintf(info + n, sizeof(info) - n, "applied=%.9g %.9g %.9g\n", L[l].applied[0], L[l].applied[1], L[l].applied[2]);
            else
                n += snprintf(info + n, sizeof(info) - n, "%.*s\n", (int)len, p);
            if (n >= (int)sizeof(info)) Error("-remix: layer text too long");
            p += len + (e ? 1 : 0);
        }
        fwrite(&n, 4, 1, out), fwrite(info, 1, n, out);
        fwrite(file + L[l].data, 1, L[l].end - L[l].data, out);
        free(L[l].info);
    }
    fclose(out);
    remove(path);
    if (rename(tmp, path)) Error("Can't write %s", path);
    free(file), free(L), free(now), free(touched);
}
