/* Keeping the last bake (Hammerless's own, -keeplight <old map>): with No Bake Volumes, what isn't baked takes its
 * lighting from the previous build instead of the flat ambient colour, where that build has the same thing:
 *   - a face with the same corners (in order), lightmap size, place and axes, styles, bump mapping and (for a
 *     displacement) the same displaced vertexes: its luxels and averages;
 *   - a leaf with the same bounds, when the old map has the same leaves: its ambient samples;
 *   - a static prop with the same record (place, angles, flags...), model name and model checksum: its .vhv colours.
 * Anything else falls back to the ambient colour; so does anything inside a volume that is never baked (only what's
 * outside a "bake only inside" area keeps the last bake). The old map is untrusted input: every read is bounds checked. */
#include <limits.h>
#include "hlvrad.h"

int g_bKeep;

static unsigned char *o_faces, *o_light, *o_verts, *o_edges, *o_surf, *o_tex, *o_leafs, *o_ambidx, *o_amb, *o_pak,
    *o_game, *o_disp, *o_dverts, *o_sprp;
static int n_faces, n_light, n_verts, n_edges, n_surf, n_tex, n_leafs, n_ambidx, n_amb, n_pak, n_game, n_disp, n_dverts,
    n_sprp;

static unsigned long long *keys;       /* open addressing: face key -> old face index + 1 */
static int *slots, nslots;

#define DISPINFO_SIZE 176
#define DISPVERT_SIZE 20

static int Lump(const char *path, int i, unsigned char **d, int *n) {
    int version;
    if (!ReadLumpFrom(path, i, d, n, &version)) {
        *d = NULL, *n = 0;
        return 0;
    }
    return 1;
}

static int I32(const unsigned char *p) {
    int v;
    memcpy(&v, p, 4);
    return v;
}

/* one map's arrays, to read either the new map or the old one the same way */
typedef struct {
    const unsigned char *faces, *surf, *edges, *verts, *tex, *disp, *dverts;
    int nfaces, nsurf, nedges, nverts, ntex, ndisp, ndverts;
} mapview_t;

static mapview_t OldMap(void) {
    mapview_t m = {o_faces, o_surf, o_edges, o_verts, o_tex, o_disp, o_dverts,
                   n_faces / (int)sizeof(dface_t), n_surf / 4, n_edges / (int)sizeof(dedge_t),
                   n_verts / (int)sizeof(dvertex_t), n_tex / (int)sizeof(texinfo_t), n_disp / DISPINFO_SIZE,
                   n_dverts / DISPVERT_SIZE};
    return m;
}

static mapview_t NewMap(void) {
    mapview_t m = {(const unsigned char *)g_pFaces, (const unsigned char *)dsurfedges, (const unsigned char *)dedges,
                   (const unsigned char *)dvertexes, (const unsigned char *)texinfo, lumps[LUMP_DISPINFO].data,
                   lumps[LUMP_DISP_VERTS].data,
                   numfaces, numsurfedges, numedges, numvertexes, numtexinfo,
                   lumps[LUMP_DISPINFO].len / DISPINFO_SIZE, lumps[LUMP_DISP_VERTS].len / DISPVERT_SIZE};
    return m;
}

/* what a face's lightmap depends on, besides its corners */
typedef struct {
    int size[2], mins[2], styles, bump, disp;
    float lmvecs[8];
    unsigned long long disphash;
} facesig_t;

/* a face's corners (in loop order) and signature; -1 when its data doesn't make sense */
static int Face(const mapview_t *m, int f, float out[][3], int cap, facesig_t *sig) {
    if (f < 0 || f >= m->nfaces) return -1;
    const dface_t *df = (const dface_t *)m->faces + f;
    if (df->numedges < 3 || df->numedges > cap) return -1;
    for (int i = 0; i < df->numedges; i++) {
        long long k = (long long)df->firstedge + i;
        if (k < 0 || k >= m->nsurf) return -1;
        int e = ((const int *)m->surf)[k];
        if (e == INT_MIN) return -1;
        int ei = e >= 0 ? e : -e;
        if (ei >= m->nedges) return -1;
        int v = ((const dedge_t *)m->edges)[ei].v[e >= 0 ? 0 : 1];
        if (v >= m->nverts) return -1;
        memcpy(out[i], ((const dvertex_t *)m->verts)[v].point, 12);
    }
    memset(sig, 0, sizeof(*sig));
    sig->size[0] = df->m_LightmapTextureSizeInLuxels[0], sig->size[1] = df->m_LightmapTextureSizeInLuxels[1];
    sig->mins[0] = df->m_LightmapTextureMinsInLuxels[0], sig->mins[1] = df->m_LightmapTextureMinsInLuxels[1];
    sig->styles = df->styles[0] | df->styles[1] << 8 | df->styles[2] << 16 | (int)((unsigned)df->styles[3] << 24);
    if (df->texinfo < 0 || df->texinfo >= m->ntex) return -1;
    const texinfo_t *ti = (const texinfo_t *)m->tex + df->texinfo;
    sig->bump = (ti->flags & SURF_BUMPLIGHT) != 0;
    memcpy(sig->lmvecs, ti->lightmapVecsLuxelsPerWorldUnits, sizeof(sig->lmvecs));
    sig->disp = df->dispinfo >= 0;
    if (sig->disp) {                      /* (the displaced shape: its vertexes' offsets, distances, alphas) */
        if (df->dispinfo >= m->ndisp) return -1;
        const unsigned char *d = m->disp + (size_t)DISPINFO_SIZE * df->dispinfo;
        int start = I32(d + 12), power = I32(d + 20);
        if (power < 2 || power > 4) return -1;
        int n = ((1 << power) + 1) * ((1 << power) + 1);
        if (start < 0 || start > m->ndverts - n) return -1;
        unsigned long long h = 1469598103934665603ULL;
        const unsigned char *p = m->dverts + (size_t)DISPVERT_SIZE * start;
        for (int i = 0; i < DISPVERT_SIZE * n; i++) h = (h ^ p[i]) * 1099511628211ULL;
        sig->disphash = h ^ (unsigned long long)power;
    }
    return df->numedges;
}

static unsigned long long FaceKey(float c[][3], int n, const facesig_t *sig) {
    unsigned long long h = 1469598103934665603ULL;
    const unsigned char *b = (const unsigned char *)c;
    for (int i = 0; i < 12 * n; i++) h = (h ^ b[i]) * 1099511628211ULL;
    b = (const unsigned char *)sig;
    for (int i = 0; i < (int)sizeof(*sig); i++) h = (h ^ b[i]) * 1099511628211ULL;
    return h ? h : 1;
}

static void LoadOldSprp(const char *path);

void LoadKeepLight(const char *path) {
    if (!Lump(path, LUMP_FACES_HDR, &o_faces, &n_faces) || !n_faces) Lump(path, LUMP_FACES, &o_faces, &n_faces);
    Lump(path, LUMP_LIGHTING_HDR, &o_light, &n_light);
    Lump(path, LUMP_VERTEXES, &o_verts, &n_verts);
    Lump(path, LUMP_EDGES, &o_edges, &n_edges);
    Lump(path, LUMP_SURFEDGES, &o_surf, &n_surf);
    Lump(path, LUMP_TEXINFO, &o_tex, &n_tex);
    Lump(path, LUMP_DISPINFO, &o_disp, &n_disp);
    Lump(path, LUMP_DISP_VERTS, &o_dverts, &n_dverts);
    Lump(path, LUMP_LEAFS, &o_leafs, &n_leafs);
    Lump(path, LUMP_LEAF_AMBIENT_INDEX_HDR, &o_ambidx, &n_ambidx);
    Lump(path, LUMP_LEAF_AMBIENT_LIGHTING_HDR, &o_amb, &n_amb);
    Lump(path, LUMP_PAKFILE, &o_pak, &n_pak);
    Lump(path, LUMP_GAME_LUMP, &o_game, &n_game);
    LoadOldSprp(path);                   /* (now, before the props are lit in parallel) */
    mapview_t m = OldMap();
    int kept = 0;
    nslots = 1;
    while (nslots < m.nfaces * 2 + 2) nslots <<= 1;
    keys = xalloc(sizeof(unsigned long long) * nslots);
    slots = xalloc(sizeof(int) * nslots);
    for (int f = 0; f < m.nfaces && n_light; f++) {
        const dface_t *df = (const dface_t *)o_faces + f;
        if (df->lightofs < 0) continue;
        float c[64][3];
        facesig_t sig;
        int n = Face(&m, f, c, 64, &sig);
        if (n < 3) continue;
        unsigned long long k = FaceKey(c, n, &sig);
        int s = (int)(k & (nslots - 1));
        while (keys[s]) s = (s + 1) & (nslots - 1);
        keys[s] = k, slots[s] = f + 1;
        kept++;
    }
    g_bKeep = kept > 0 || n_amb > 0 || n_sprp > 0;
    Msg("keeping the last bake from %s: %d lit faces\n", path, kept);
}

/* the old face the same as this one (everything compared, not just the key), or -1 */
int KeepFace(int facenum) {
    if (!g_bKeep || !keys) return -1;
    mapview_t nm = NewMap(), om = OldMap();
    float c[64][3], oc[64][3];
    facesig_t sig, osig;
    int n = Face(&nm, facenum, c, 64, &sig);
    if (n < 3) return -1;
    unsigned long long k = FaceKey(c, n, &sig);
    for (int s = (int)(k & (nslots - 1)); keys[s]; s = (s + 1) & (nslots - 1)) {
        if (keys[s] != k) continue;
        int f = slots[s] - 1;
        if (Face(&om, f, oc, 64, &osig) != n || memcmp(c, oc, sizeof(float) * 3 * n) ||
            memcmp(&sig, &osig, sizeof(sig)))
            continue;
        const dface_t *of = (const dface_t *)o_faces + f;
        long long luxels = (long long)(of->m_LightmapTextureSizeInLuxels[0] + 1) *
                           (of->m_LightmapTextureSizeInLuxels[1] + 1);
        int styles = 0;
        while (styles < 4 && of->styles[styles] != 255) styles++;
        if (luxels <= 0 || of->lightofs - 4 * styles < 0 ||
            (long long)of->lightofs + luxels * 4 * styles * (sig.bump ? 4 : 1) > n_light)
            continue;
        return f;
    }
    return -1;
}

/* an old face's luxel (style k, bump vector b of bumpCount), RGBE bytes; and its average for style k */
const unsigned char *KeepLuxel(int oldface, int k, int b, int bumpCount, int j) {
    const dface_t *of = (const dface_t *)o_faces + oldface;
    int luxels = (of->m_LightmapTextureSizeInLuxels[0] + 1) * (of->m_LightmapTextureSizeInLuxels[1] + 1);
    return o_light + of->lightofs + ((size_t)(k * bumpCount + b) * luxels + j) * 4;
}

const unsigned char *KeepAverage(int oldface, int k) {
    return o_light + ((const dface_t *)o_faces)[oldface].lightofs - 4 * (k + 1);
}

/* a leaf's old ambient samples (28 bytes each), when the old map has the same leaves; returns the count (at most
 * maxcount), or -1 */
int KeepLeaf(int leaf, const unsigned char **samples, int maxcount) {
    if (!g_bKeep || n_leafs != numleafs * (int)sizeof(dleaf_t) || n_ambidx < (leaf + 1) * 4) return -1;
    const dleaf_t *ol = (const dleaf_t *)o_leafs + leaf, *nl = &dleafs[leaf];
    if (memcmp(ol->mins, nl->mins, sizeof(ol->mins)) || memcmp(ol->maxs, nl->maxs, sizeof(ol->maxs))) return -1;
    unsigned short count, first;
    memcpy(&count, o_ambidx + 4 * leaf, 2), memcpy(&first, o_ambidx + 4 * leaf + 2, 2);
    if (!count || count > maxcount || ((long long)first + count) * 28 > n_amb) return -1;
    *samples = o_amb + 28 * (size_t)first;
    return count;
}

/* the old map's static prop lump (read from the file: game lump offsets are file offsets) */
static void LoadOldSprp(const char *path) {
    if (n_game < 4) return;
    int count = I32(o_game);
    if (count < 0 || count > (n_game - 4) / 16) return;
    for (int g = 0; g < count; g++) {
        const unsigned char *e = o_game + 4 + 16 * g;
        int id = I32(e), ofs = I32(e + 8), len = I32(e + 12);
        if (id != 0x73707270 || len <= 0 || ofs <= 0) continue;
        FILE *f = fopen(path, "rb");
        if (!f) return;
        unsigned char *buf = xalloc((size_t)len + 1);
        if (fseek(f, ofs, SEEK_SET) == 0 && fread(buf, 1, (size_t)len, f) == (size_t)len) o_sprp = buf, n_sprp = len;
        else free(buf);
        fclose(f);
        return;
    }
}

/* an old stored (uncompressed) file from the old map's pak, by name */
static const unsigned char *OldPakFile(const char *name, int *len) {
    if (n_pak < 22) return NULL;
    long long cd = -1, entries = 0;
    for (int p = n_pak - 22; p >= 0; p--) {
        if (I32(o_pak + p) != 0x06054b50) continue;
        unsigned short n;
        memcpy(&n, o_pak + p + 10, 2);
        cd = (unsigned)I32(o_pak + p + 16);
        entries = n;
        break;
    }
    long long nl = (long long)strlen(name);
    for (long long e = 0, p = cd; e < entries && p >= 0 && p + 46 <= n_pak; e++) {
        unsigned short method, fnlen, extra, comment;
        memcpy(&method, o_pak + p + 10, 2), memcpy(&fnlen, o_pak + p + 28, 2);
        memcpy(&extra, o_pak + p + 30, 2), memcpy(&comment, o_pak + p + 32, 2);
        long long csize = (unsigned)I32(o_pak + p + 20), local = (unsigned)I32(o_pak + p + 42);
        if (p + 46 + fnlen > n_pak) return NULL;
        if (fnlen == nl && !_strnicmp((const char *)o_pak + p + 46, name, (size_t)nl) && method == 0 &&
            local + 30 <= n_pak) {
            unsigned short lfn, lex;
            memcpy(&lfn, o_pak + local + 26, 2), memcpy(&lex, o_pak + local + 28, 2);
            long long at = local + 30 + lfn + lex;
            if (at + csize > n_pak) return NULL;
            *len = (int)csize;
            return o_pak + at;
        }
        p += 46 + fnlen + extra + comment;
    }
    return NULL;
}

/* prop i's old .vhv, when the old map has the same prop (record, model name) and the model is unchanged (the
 * .vhv's checksum is the model's); NULL otherwise */
const unsigned char *KeepProp(int i, const unsigned char *rec, int reclen, const char *model, int checksum,
                              const char *vhvname, int *len) {
    if (!g_bKeep || n_sprp < 4 || reclen < 30) return NULL;
    const unsigned char *end = o_sprp + n_sprp;
    int nd = I32(o_sprp);
    if (nd < 0 || nd > (n_sprp - 4) / 128) return NULL;
    const unsigned char *p = o_sprp + 4 + 128 * (size_t)nd;
    if (p + 4 > end) return NULL;
    int nleaves = I32(p);
    if (nleaves < 0 || nleaves > (end - p - 4) / 2) return NULL;
    p += 4 + 2 * (size_t)nleaves;
    if (p + 4 > end) return NULL;
    int np = I32(p);
    p += 4;
    if (np < 0 || i < 0 || i >= np || (long long)reclen * (i + 1) > end - p) return NULL;
    const unsigned char *orec = p + (size_t)reclen * i;
    /* (the same place, angles, flags, fades, lighting origin...: all but the model index and the leaf range, which a
     * recompile can renumber; the model is compared by name) */
    if (memcmp(orec, rec, 24) || memcmp(orec + 30, rec + 30, reclen - 30)) return NULL;
    unsigned short om;
    memcpy(&om, orec + 24, 2);
    if (om >= nd || _strnicmp((const char *)o_sprp + 4 + 128 * (size_t)om, model, 128)) return NULL;
    const unsigned char *vhv = OldPakFile(vhvname, len);
    if (vhv == NULL || *len < 8 || I32(vhv + 4) != checksum) return NULL;     /* (an edited model: lit again) */
    return vhv;
}
