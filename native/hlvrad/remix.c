/* -remix <map>.hllayers: Live Light Mixing's result kept without baking again. The map was baked with -layers (each
 * light's own light kept apart, direct.c's WriteLayers, and the bake's own lightmaps in <map>.hllayers.base); since
 * then only lights' colour and brightness changed (the build checks: buildplan.py). Light adds up, bounces too, so
 * the lighting now is the bake's plus, for each layer, (its weight now - 1) times the layer, its weight being its
 * lights' intensity now over baked, per colour. That goes into every lightmap page of the layer's style (bump pages
 * too), always from the bake's own lightmaps (a remix of a remix doesn't round twice); the rest of the lighting (the
 * lights for models, leaf ambient, props' and detail props' light) is made from the result as a bake makes it.
 * The layers file then says the weights the map has ("applied"), once the map is written (RemixCommit). */
#include <float.h>
#include "hlvrad.h"

#define NUM_BUMP_VECTS 3               /* (as direct.c) */
#define MAXLIGHTMAPS 4

void VectorToColorRGBExp32(const vec3_t v, unsigned char *c);

typedef struct {
    char *info;                 /* its "key=value" lines */
    size_t data, end;           /* where its faces are in the file */
    int style;
    vec3_t baked, applied;
} rlayer_t;

static char g_commitTmp[1100], g_commitPath[1100];

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

static unsigned char *ReadAll(const char *path, size_t *size) {
    FILE *fp = fopen(path, "rb");
    if (!fp) return NULL;
    fseek(fp, 0, SEEK_END);
    long n = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    unsigned char *data = n >= 0 ? xalloc((size_t)n + 1) : NULL;
    if (!data || fread(data, 1, (size_t)n, fp) != (size_t)n) {
        free(data), fclose(fp);
        return NULL;
    }
    fclose(fp);
    *size = (size_t)n;
    return data;
}

/* (a corrupt or cut-short file: an error, never a read past its end) */
#define NEED(n) do { if ((size_t)(n) > size || at > size - (size_t)(n)) Error("-remix: %s is cut short or damaged", path); } while (0)

void Remix(const char *path) {
    size_t size = 0;
    unsigned char *file = ReadAll(path, &size);
    if (!file) Error("-remix: can't read %s", path);
    size_t at = 0;
    NEED(16);
    int head[4];
    memcpy(head, file, 16);
    at = 16;
    if (head[0] != 0x594c4c48 || head[1] != 3) Error("-remix: %s isn't a light layers file (version 3)", path);
    if (head[2] != numfaces) Error("-remix: %s is another bake's (%d faces, the map has %d)", path, head[2], numfaces);
    int nlayers = head[3];
    if (nlayers < 0 || nlayers > 256) Error("-remix: %s is damaged", path);
    /* the bake's own lightmaps: every remix starts from them */
    char base[1100];
    snprintf(base, sizeof(base), "%s.base", path);
    size_t bsize = 0;
    unsigned char *baked = ReadAll(base, &bsize);
    if (!baked || bsize != (size_t)lightdatasize) Error("-remix: %s is missing or another bake's", base);
    memcpy(dlightdata, baked, lightdatasize);
    free(baked);
    rlayer_t *L = xalloc(sizeof(rlayer_t) * (nlayers + 1));
    for (int l = 0; l < nlayers; l++) {         /* (what each was baked with, where its faces are) */
        int len, n;
        NEED(4);
        memcpy(&len, file + at, 4);
        at += 4;
        if (len < 0) Error("-remix: %s is damaged", path);
        NEED(len);
        L[l].info = xalloc((size_t)len + 1);
        memcpy(L[l].info, file + at, len);
        at += len;
        L[l].data = at;
        NEED(4);
        memcpy(&n, file + at, 4);
        at += 4;
        if (n < 0 || n > numfaces) Error("-remix: %s is damaged", path);
        for (int i = 0; i < n; i++) {
            int h[3];
            NEED(12);
            memcpy(h, file + at, 12);
            at += 12;
            if (h[0] < 0 || h[0] >= numfaces || h[1] < 0 || (h[2] != 1 && h[2] != NUM_BUMP_VECTS + 1))
                Error("-remix: %s is damaged", path);
            NEED(6 * (size_t)h[1] * h[2]);
            at += 6 * (size_t)h[1] * h[2];
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
            if (L[l].baked[i] <= 0 && now[l][i] > 0)
                Error("-remix: layer %d was baked without this colour (dark): it needs a bake", l + 1);
            w[i] = L[l].baked[i] > 0 ? now[l][i] / L[l].baked[i] : 1.0f;
            delta[i] = w[i] - 1.0f;                     /* (from the bake's own lightmaps) */
            any |= fabsf(delta[i]) > 1e-6f;
        }
        const char *who = InfoValue(L[l].info, "_hlobject");
        Msg("layer %d (%.*s): weight %.3f %.3f %.3f\n", l + 1, who ? (int)strcspn(who, "\n") : 1, who ? who : "?", w[0], w[1], w[2]);
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
            dface_t *f = &g_pFaces[h[0]];
            int luxels = (f->m_LightmapTextureSizeInLuxels[0] + 1) * (f->m_LightmapTextureSizeInLuxels[1] + 1);
            int bumpCount = texinfo[f->texinfo].flags & SURF_BUMPLIGHT ? NUM_BUMP_VECTS + 1 : 1;
            if (luxels != h[1] || bumpCount != h[2]) Error("-remix: %s is another bake's (face %d differs)", path, h[0]);
            int k;
            for (k = 0; k < MAXLIGHTMAPS && f->styles[k] != L[l].style; k++) {}
            if (k == MAXLIGHTMAPS || f->lightofs < 0) continue;       /* (the bake had no room for its style here) */
            if ((size_t)f->lightofs + ((size_t)k + 1) * bumpCount * luxels * 4 > (size_t)lightdatasize)
                Error("-remix: face %d's lightmap is outside the map's lighting", h[0]);
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
    /* the layers file with the weights the map will have: written now, put in place once the map is (RemixCommit) */
    snprintf(g_commitTmp, sizeof(g_commitTmp), "%s.tmp", path);
    snprintf(g_commitPath, sizeof(g_commitPath), "%s", path);
    FILE *out = fopen(g_commitTmp, "wb");
    if (!out) Error("Can't write %s", g_commitTmp);
    fwrite(file, 1, 16, out);
    for (int l = 0; l < nlayers; l++) {
        size_t cap = strlen(L[l].info) + 128, n = 0;
        char *info = xalloc(cap);
        for (const char *p = L[l].info; *p;) {             /* (its lines, "applied" as now) */
            const char *e = strchr(p, '\n');
            size_t len = e ? (size_t)(e - p) : strlen(p);
            if (!strncmp(p, "applied=", 8))
                n += (size_t)snprintf(info + n, cap - n, "applied=%.9g %.9g %.9g\n", L[l].applied[0], L[l].applied[1], L[l].applied[2]);
            else
                n += (size_t)snprintf(info + n, cap - n, "%.*s\n", (int)len, p);
            p += len + (e ? 1 : 0);
        }
        int ilen = (int)n;
        fwrite(&ilen, 4, 1, out), fwrite(info, 1, n, out);
        fwrite(file + L[l].data, 1, L[l].end - L[l].data, out);
        free(info), free(L[l].info);
    }
    int bad = ferror(out);
    if (fclose(out) || bad) {
        remove(g_commitTmp);
        Error("Can't write %s", g_commitTmp);
    }
    free(file), free(L), free(now), free(touched);
}

/* the map's written: its layers file says the weights it has now */
void RemixCommit(void) {
    if (!g_commitTmp[0]) return;
    remove(g_commitPath);
    if (rename(g_commitTmp, g_commitPath)) Error("Can't write %s", g_commitPath);
}
