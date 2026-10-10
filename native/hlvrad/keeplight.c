/* Keeping the last bake (Hammerless's own, -keeplight <old map>): with No Bake Volumes, what isn't baked takes its
 * lighting from the previous build instead of the flat ambient colour, where that build has the same thing:
 *   - a face with the same corners (in order), lightmap size, styles and bump mapping: its luxels and averages;
 *   - a leaf with the same bounds, when the old map has the same leaves: its ambient samples;
 *   - a static prop with the same record (model, place, flags) and dictionary name: its .vhv vertex colours.
 * Anything else falls back to the ambient colour. So "bake only the selection" leaves the rest as it was baked. */
#include "hlvrad.h"

int g_bKeep;
static void SetKeepPath(const char *path);

static unsigned char *o_faces, *o_light, *o_verts, *o_edges, *o_surf, *o_tex, *o_leafs, *o_ambidx, *o_amb, *o_pak,
    *o_game;
static int n_faces, n_light, n_verts, n_edges, n_surf, n_tex, n_leafs, n_ambidx, n_amb, n_pak, n_game;

static unsigned long long *keys;       /* open addressing: face key -> old face index + 1 */
static int *slots, nslots;

static int Lump(const char *path, int i, unsigned char **d, int *n) {
    int version;
    if (!ReadLumpFrom(path, i, d, n, &version)) {
        *d = NULL, *n = 0;
        return 0;
    }
    return 1;
}

/* a face's corners, in loop order, from one map's arrays */
static int Corners(const unsigned char *faces, int f, const unsigned char *surf, int nsurf, const unsigned char *edges,
                   int nedges, const unsigned char *verts, int nverts, float out[][3], int cap) {
    const dface_t *df = (const dface_t *)faces + f;
    int n = df->numedges < cap ? df->numedges : cap;
    for (int i = 0; i < n; i++) {
        int k = df->firstedge + i;
        if (k < 0 || k >= nsurf) return -1;
        int e = ((const int *)surf)[k];
        int ei = e >= 0 ? e : -e;
        if (ei >= nedges) return -1;
        int v = ((const dedge_t *)edges)[ei].v[e >= 0 ? 0 : 1];
        if (v >= nverts) return -1;
        memcpy(out[i], ((const dvertex_t *)verts)[v].point, 12);
    }
    return n;
}

static unsigned long long FaceKey(const dface_t *df, float c[][3], int n, int bump) {
    unsigned long long h = 1469598103934665603ULL;
#define MIX(x) (h = (h ^ (unsigned long long)(unsigned)(x)) * 1099511628211ULL)
    for (int i = 0; i < n; i++)
        for (int k = 0; k < 3; k++) MIX((int)floorf(c[i][k] * 8.0f + 0.5f));
    MIX(df->m_LightmapTextureSizeInLuxels[0]), MIX(df->m_LightmapTextureSizeInLuxels[1]);
    MIX(df->styles[0] | df->styles[1] << 8 | df->styles[2] << 16 | (unsigned)df->styles[3] << 24);
    MIX(bump), MIX(df->dispinfo >= 0);
#undef MIX
    return h ? h : 1;
}

static int OldBump(int f) {
    const dface_t *df = (const dface_t *)o_faces + f;
    if (df->texinfo < 0 || (df->texinfo + 1) * (int)sizeof(texinfo_t) > n_tex) return 0;
    return (((const texinfo_t *)o_tex)[df->texinfo].flags & SURF_BUMPLIGHT) != 0;
}

void LoadKeepLight(const char *path) {
    SetKeepPath(path);
    if (!Lump(path, LUMP_FACES_HDR, &o_faces, &n_faces) || !n_faces) Lump(path, LUMP_FACES, &o_faces, &n_faces);
    Lump(path, LUMP_LIGHTING_HDR, &o_light, &n_light);
    Lump(path, LUMP_VERTEXES, &o_verts, &n_verts);
    Lump(path, LUMP_EDGES, &o_edges, &n_edges);
    Lump(path, LUMP_SURFEDGES, &o_surf, &n_surf);
    Lump(path, LUMP_TEXINFO, &o_tex, &n_tex);
    Lump(path, LUMP_LEAFS, &o_leafs, &n_leafs);
    Lump(path, LUMP_LEAF_AMBIENT_INDEX_HDR, &o_ambidx, &n_ambidx);
    Lump(path, LUMP_LEAF_AMBIENT_LIGHTING_HDR, &o_amb, &n_amb);
    Lump(path, LUMP_PAKFILE, &o_pak, &n_pak);
    Lump(path, LUMP_GAME_LUMP, &o_game, &n_game);
    int nf = n_faces / (int)sizeof(dface_t), kept = 0;
    nslots = 1;
    while (nslots < nf * 2 + 2) nslots <<= 1;
    keys = xalloc(sizeof(unsigned long long) * nslots);
    slots = xalloc(sizeof(int) * nslots);
    for (int f = 0; f < nf && n_light; f++) {
        const dface_t *df = (const dface_t *)o_faces + f;
        if (df->lightofs < 0) continue;
        float c[64][3];
        int n = Corners(o_faces, f, o_surf, n_surf / 4, o_edges, n_edges / (int)sizeof(dedge_t), o_verts,
                        n_verts / (int)sizeof(dvertex_t), c, 64);
        if (n < 3) continue;
        unsigned long long k = FaceKey(df, c, n, OldBump(f));
        int s = (int)(k & (nslots - 1));
        while (keys[s]) s = (s + 1) & (nslots - 1);
        keys[s] = k, slots[s] = f + 1;
        kept++;
    }
    g_bKeep = kept > 0 || n_amb > 0;
    Msg("keeping the last bake from %s: %d lit faces\n", path, kept);
}

/* the old face the same as this one (corners checked, not just the key), or -1 */
int KeepFace(int facenum) {
    if (!g_bKeep || !keys) return -1;
    const dface_t *df = &g_pFaces[facenum];
    float c[64][3], oc[64][3];
    int n = Corners((const unsigned char *)g_pFaces, facenum, (const unsigned char *)dsurfedges, numsurfedges,
                    (const unsigned char *)dedges, numedges, (const unsigned char *)dvertexes, numvertexes, c, 64);
    if (n < 3) return -1;
    int bump = (texinfo[df->texinfo].flags & SURF_BUMPLIGHT) != 0;
    unsigned long long k = FaceKey(df, c, n, bump);
    for (int s = (int)(k & (nslots - 1)); keys[s]; s = (s + 1) & (nslots - 1)) {
        if (keys[s] != k) continue;
        int f = slots[s] - 1;
        const dface_t *of = (const dface_t *)o_faces + f;
        if (of->numedges != df->numedges || OldBump(f) != bump) continue;
        if (Corners(o_faces, f, o_surf, n_surf / 4, o_edges, n_edges / (int)sizeof(dedge_t), o_verts,
                    n_verts / (int)sizeof(dvertex_t), oc, 64) != n || memcmp(c, oc, sizeof(float) * 3 * n))
            continue;
        int luxels = (of->m_LightmapTextureSizeInLuxels[0] + 1) * (of->m_LightmapTextureSizeInLuxels[1] + 1);
        int styles = 0;
        while (styles < 4 && of->styles[styles] != 255) styles++;
        if (of->lightofs - 4 * styles < 0 || of->lightofs + luxels * 4 * styles * (bump ? 4 : 1) > n_light) continue;
        return f;
    }
    return -1;
}

/* an old face's luxel (style k, bump vector b of bumpCount), RGBE bytes; and its average for style k */
const unsigned char *KeepLuxel(int oldface, int k, int b, int bumpCount, int j) {
    const dface_t *of = (const dface_t *)o_faces + oldface;
    int luxels = (of->m_LightmapTextureSizeInLuxels[0] + 1) * (of->m_LightmapTextureSizeInLuxels[1] + 1);
    return o_light + of->lightofs + ((k * bumpCount + b) * luxels + j) * 4;
}

const unsigned char *KeepAverage(int oldface, int k) {
    return o_light + ((const dface_t *)o_faces)[oldface].lightofs - 4 * (k + 1);
}

/* a leaf's old ambient samples (28 bytes each), when the old map has the same leaves; returns the count, or -1 */
int KeepLeaf(int leaf, const unsigned char **samples) {
    if (!g_bKeep || n_leafs != numleafs * (int)sizeof(dleaf_t) || n_ambidx < (leaf + 1) * 4) return -1;
    const dleaf_t *ol = (const dleaf_t *)o_leafs + leaf, *nl = &dleafs[leaf];
    if (memcmp(ol->mins, nl->mins, sizeof(ol->mins)) || memcmp(ol->maxs, nl->maxs, sizeof(ol->maxs))) return -1;
    unsigned short count, first;
    memcpy(&count, o_ambidx + 4 * leaf, 2), memcpy(&first, o_ambidx + 4 * leaf + 2, 2);
    if (!count || (first + count) * 28 > n_amb) return -1;
    *samples = o_amb + 28 * first;
    return count;
}

static unsigned char *o_sprp;
static int n_sprp, sprp_loaded;
static char keep_path[1024];

static void SetKeepPath(const char *path) { snprintf(keep_path, sizeof(keep_path), "%s", path); }

static void LoadOldSprp(void) {
    sprp_loaded = 1;
    if (n_game < 4) return;
    int count;
    memcpy(&count, o_game, 4);
    for (int g = 0; g < count; g++) {
        int id, ofs, len;
        memcpy(&id, o_game + 4 + 16 * g, 4), memcpy(&ofs, o_game + 4 + 16 * g + 8, 4), memcpy(&len, o_game + 4 + 16 * g + 12, 4);
        if (id != 0x73707270 || len <= 0) continue;
        FILE *f = fopen(keep_path, "rb");
        if (!f) return;
        o_sprp = xalloc(len + 1);
        if (fseek(f, ofs, SEEK_SET) == 0 && fread(o_sprp, 1, len, f) == (size_t)len) n_sprp = len;
        fclose(f);
        return;
    }
}

/* an old stored file from the old map's pak, by name */
static const unsigned char *OldPakFile(const char *name, int *len) {
    if (n_pak < 22) return NULL;
    int cd = -1, entries = 0;
    for (int p = n_pak - 22; p >= 0; p--) {
        unsigned int sig;
        memcpy(&sig, o_pak + p, 4);
        if (sig != 0x06054b50) continue;
        unsigned short n;
        memcpy(&n, o_pak + p + 10, 2), memcpy(&cd, o_pak + p + 16, 4);
        entries = n;
        break;
    }
    int nl = (int)strlen(name);
    for (int e = 0, p = cd; e < entries && p >= 0 && p + 46 <= n_pak; e++) {
        unsigned short method, fnlen, extra, comment;
        unsigned int csize, local;
        memcpy(&method, o_pak + p + 10, 2), memcpy(&csize, o_pak + p + 20, 4), memcpy(&fnlen, o_pak + p + 28, 2);
        memcpy(&extra, o_pak + p + 30, 2), memcpy(&comment, o_pak + p + 32, 2), memcpy(&local, o_pak + p + 42, 4);
        if (fnlen == nl && !_strnicmp((const char *)o_pak + p + 46, name, nl) && method == 0 && local + 30 <= (unsigned)n_pak) {
            unsigned short lfn, lex;
            memcpy(&lfn, o_pak + local + 26, 2), memcpy(&lex, o_pak + local + 28, 2);
            unsigned int at = local + 30 + lfn + lex;
            if (at + csize > (unsigned)n_pak) return NULL;
            *len = (int)csize;
            return o_pak + at;
        }
        p += 46 + fnlen + extra + comment;
    }
    return NULL;
}

/* prop i's old .vhv, when the old map has the same prop (record and model name); NULL otherwise */
const unsigned char *KeepProp(int i, const unsigned char *rec, int reclen, const char *model, const char *vhvname,
                              int *len) {
    if (!g_bKeep) return NULL;
    if (!sprp_loaded) LoadOldSprp();
    if (n_sprp < 4) return NULL;
    int nd;
    memcpy(&nd, o_sprp, 4);
    const unsigned char *p = o_sprp + 4 + 128 * nd;
    if (p + 4 > o_sprp + n_sprp) return NULL;
    int nleaves;
    memcpy(&nleaves, p, 4);
    p += 4 + 2 * nleaves;
    if (p + 4 > o_sprp + n_sprp) return NULL;
    int np;
    memcpy(&np, p, 4);
    p += 4;
    if (i >= np || p + reclen * (i + 1) > o_sprp + n_sprp) return NULL;
    const unsigned char *orec = p + reclen * i;
    /* (the same place, angles, flags, fades, lighting origin...: all but the model index and the leaf range, which a
     * recompile can renumber; the model is compared by name below) */
    if (memcmp(orec, rec, 24) || memcmp(orec + 30, rec + 30, reclen - 30)) return NULL;
    unsigned short om;
    memcpy(&om, orec + 24, 2);
    if (om >= nd || _strnicmp((const char *)o_sprp + 4 + 128 * om, model, 128)) return NULL;
    return OldPakFile(vhvname, len);
}
