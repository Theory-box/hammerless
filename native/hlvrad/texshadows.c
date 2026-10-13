/* -textureshadows: alpha-tested textures cast partial shadows (vrad's CShadowTextureList, as L4D2's vrad_dll.dll
 * does it, read from its disassembly).
 *
 * What casts them in L4D2's vrad:
 *  - static props drawn as their triangles (-StaticPropPolys only) whose model has the "cast texture shadows" flag
 *    or is named by a lights.rad "forcetextureshadow" line (models/ and the extension left out, case as written);
 *  - world brushes with CONTENTS_GRATE (only with -textureshadows: without it they cast no shadow at all): each
 *    side whose material has a texture with alpha; their other sides are solid.
 * A material counts when its .vmt has "$translucent" or "$alphatest" anywhere in it (searched through every block,
 * so "patch" materials count too); its $basetexture's largest mip gives the alpha. A model's materials are looked
 * for in its first $cdmaterials folder only (vrad's KeyValues loader "succeeds" on a missing file, which ends the
 * search). A triangle whose texture area (the box around its texture coordinates, clamped to 0..1) is all opaque
 * stays solid; the others become see-through: a ray through one takes the texture's alpha at the hit point
 * (bilinear, L4D2's filter) as coverage, summed along the ray (at most 1); such triangles never stop a ray.
 * Only the light visibility tests (TestLine, TestLine_DoesHitSky) see through them; the bounce's tests treat them
 * as solid.
 *
 * Materials and textures are read from -modeldir if there, else from the game's VPKs (read here), else loose from the
 * game folder. */
#include <ctype.h>
#include <windows.h>
#include "hlvrad.h"

/* ------------------------------------------------------------------ lights.rad "forcetextureshadow" */
static char **g_forced;
static int g_numforced;

static void CleanModelName(const char *in, char *out, size_t outlen) {
    if (!_strnicmp(in, "models/", 7)) in += 7;
    snprintf(out, outlen, "%s", in);
    char *dot = strchr(out, '.');
    if (dot) *dot = 0;
}

void TS_ForceModel(const char *name) {
    char buf[1024];
    CleanModelName(name, buf, sizeof(buf));
    for (int i = 0; i < g_numforced; i++)
        if (!strcmp(g_forced[i], buf)) return;
    g_forced = realloc(g_forced, sizeof(char *) * (g_numforced + 1));
    g_forced[g_numforced++] = _strdup(buf);
}

int TS_ModelForced(const char *name) {
    char buf[1024];
    CleanModelName(name, buf, sizeof(buf));
    for (int i = 0; i < g_numforced; i++)            /* (vrad's symbol table: case sensitive) */
        if (!strcmp(g_forced[i], buf)) return 1;
    return 0;
}

/* ------------------------------------------------------------------ files */
/* The game's VPKs (every *_dir.vpk one folder under the install, later folders first, as hammerless/core/vpk.py
 * reads them), only their materials (.vmt, .vtf) */
typedef struct { char *path; int vpk, archive; unsigned offset, size, preload; const unsigned char *pre; } vpkfile_t;
typedef struct { char dir[1100]; unsigned char *tree; unsigned datastart; } vpk_t;
static vpk_t *g_vpks;
static int g_numvpks;
static vpkfile_t *g_vfiles;
static int g_numvfiles, g_vpkloaded;

static int VFileCmp(const void *a, const void *b) {
    int c = strcmp(((const vpkfile_t *)a)->path, ((const vpkfile_t *)b)->path);
    return c ? c : ((const vpkfile_t *)a)->vpk - ((const vpkfile_t *)b)->vpk;      /* (the earlier VPK first) */
}
static int NameCmp(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

static void LoadVPK(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return;
    unsigned head[3];
    if (fread(head, 4, 3, f) != 3 || head[0] != 0x55aa1234 || (head[1] != 1 && head[1] != 2)) {
        fclose(f);
        return;
    }
    unsigned hsize = head[1] == 2 ? 28 : 12;
    fseek(f, hsize, SEEK_SET);
    unsigned char *tree = xalloc(head[2] + 1);
    if (fread(tree, 1, head[2], f) != head[2]) {
        free(tree), fclose(f);
        return;
    }
    fclose(f);
    g_vpks = realloc(g_vpks, sizeof(vpk_t) * (g_numvpks + 1));
    vpk_t *v = &g_vpks[g_numvpks];
    snprintf(v->dir, sizeof(v->dir), "%s", path);
    v->tree = tree, v->datastart = hsize + head[2];
    const unsigned char *p = tree, *end = tree + head[2];
    while (p < end && *p) {
        const char *ext = (const char *)p;
        p += strlen(ext) + 1;
        int want = !_stricmp(ext, "vmt") || !_stricmp(ext, "vtf");
        while (p < end && *p) {
            const char *dir = (const char *)p;
            p += strlen(dir) + 1;
            while (p < end && *p) {
                const char *name = (const char *)p;
                p += strlen(name) + 1;
                if (p + 18 > end) goto done;
                unsigned short preload, archive;
                unsigned offset, size;
                memcpy(&preload, p + 4, 2), memcpy(&archive, p + 6, 2), memcpy(&offset, p + 8, 4), memcpy(&size, p + 12, 4);
                const unsigned char *pre = p + 18;
                p += 18 + preload;
                if (!want || _strnicmp(dir, "materials", 9)) continue;
                char full[1200];
                snprintf(full, sizeof(full), "%s/%s.%s", dir, name, ext);
                for (char *q = full; *q; q++) *q = (char)tolower((unsigned char)*q);
                g_vfiles = realloc(g_vfiles, sizeof(vpkfile_t) * (g_numvfiles + 1));
                vpkfile_t *e = &g_vfiles[g_numvfiles++];
                e->path = _strdup(full), e->vpk = g_numvpks, e->archive = archive, e->offset = offset, e->size = size;
                e->preload = preload, e->pre = pre;
            }
            p++;
        }
        p++;
    }
done:
    g_numvpks++;
}

static void LoadVPKs(void) {
    g_vpkloaded = 1;
    if (!g_gamedir) return;
    char root[1100];
    snprintf(root, sizeof(root), "%s", g_gamedir);
    size_t n = strlen(root);
    while (n && (root[n - 1] == '/' || root[n - 1] == '\\')) root[--n] = 0;
    char *slash = strrchr(root, '/'), *bslash = strrchr(root, '\\');
    if (bslash > slash) slash = bslash;
    if (!slash) return;
    *slash = 0;                              /* (the install: the game folder's parent) */
    char pattern[1200];
    snprintf(pattern, sizeof(pattern), "%s\\*", root);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    char **dirs = NULL;
    int ndirs = 0;
    do {
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && fd.cFileName[0] != '.') {
            dirs = realloc(dirs, sizeof(char *) * (ndirs + 1));
            dirs[ndirs++] = _strdup(fd.cFileName);
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    qsort(dirs, ndirs, sizeof(char *), NameCmp);
    for (int i = ndirs - 1; i >= 0; i--) {     /* (later folders first: update, then the DLCs, then the game) */
        snprintf(pattern, sizeof(pattern), "%s\\%s\\*_dir.vpk", root, dirs[i]);
        char **names = NULL;
        int nn = 0;
        h = FindFirstFileA(pattern, &fd);
        if (h != INVALID_HANDLE_VALUE) {
            do {
                names = realloc(names, sizeof(char *) * (nn + 1));
                names[nn++] = _strdup(fd.cFileName);
            } while (FindNextFileA(h, &fd));
            FindClose(h);
        }
        if (nn) qsort(names, nn, sizeof(char *), NameCmp);
        for (int k = 0; k < nn; k++) {
            char vp[1300];
            snprintf(vp, sizeof(vp), "%s\\%s\\%s", root, dirs[i], names[k]);
            LoadVPK(vp);
            free(names[k]);
        }
        free(names);
        free(dirs[i]);
    }
    free(dirs);
    if (g_numvfiles) qsort(g_vfiles, g_numvfiles, sizeof(vpkfile_t), VFileCmp);
}

static unsigned char *ReadVPKFile(const char *rel, int *len) {
    if (!g_vpkloaded) LoadVPKs();
    char key[1100];
    snprintf(key, sizeof(key), "%s", rel);
    for (char *q = key; *q; q++) *q = (char)tolower((unsigned char)*q);
    int lo = 0, hi = g_numvfiles - 1, at = -1;
    while (lo <= hi) {                       /* (the first entry with that path: the earliest VPK's) */
        int mid = (lo + hi) / 2, c = strcmp(g_vfiles[mid].path, key);
        if (c < 0) lo = mid + 1;
        else {
            if (!c) at = mid;
            hi = mid - 1;
        }
    }
    for (; at >= 0 && at < g_numvfiles && !strcmp(g_vfiles[at].path, key); at++) {
        const vpkfile_t *e = &g_vfiles[at];
        const vpk_t *v = &g_vpks[e->vpk];
        unsigned char *d = xalloc((size_t)e->preload + e->size + 1);
        memcpy(d, e->pre, e->preload);
        if (e->size) {
            char arch[1200];
            unsigned ofs = e->offset;
            snprintf(arch, sizeof(arch), "%s", v->dir);
            if (e->archive == 0x7fff) ofs += v->datastart;
            else {
                char *tail = strstr(arch, "_dir.vpk");
                if (tail) snprintf(tail, sizeof(arch) - (size_t)(tail - arch), "_%03d.vpk", e->archive);
            }
            FILE *f = fopen(arch, "rb");
            if (!f) {                        /* (that archive is missing: the next VPK that has the file) */
                free(d);
                continue;
            }
            fseek(f, (long)ofs, SEEK_SET);
            size_t got = fread(d + e->preload, 1, e->size, f);
            fclose(f);
            if (got != e->size) {
                free(d);
                continue;
            }
        }
        d[e->preload + e->size] = 0;
        *len = (int)(e->preload + e->size);
        return d;
    }
    return NULL;
}

static unsigned char *ReadFileAt(const char *path, int *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *d = xalloc(n + 1);
    if (fread(d, 1, n, f) != (size_t)n) n = 0;
    d[n] = 0;
    fclose(f);
    *len = (int)n;
    return d;
}

/* a game file: from -modeldir (when the build put it there), the game's VPKs, else loose in the game folder */
static unsigned char *ReadGameFile(const char *relpath, int *len) {
    char rel[1100], path[2300];
    snprintf(rel, sizeof(rel), "%s", relpath);
    for (char *q = rel; *q; q++)
        if (*q == '\\') *q = '/';
    unsigned char *d;
    if (g_modeldir) {
        snprintf(path, sizeof(path), "%s/%s", g_modeldir, rel);
        if ((d = ReadFileAt(path, len))) return d;
    }
    if ((d = ReadVPKFile(rel, len))) return d;
    if (g_gamedir) {
        snprintf(path, sizeof(path), "%s/%s", g_gamedir, rel);
        if ((d = ReadFileAt(path, len))) return d;
    }
    return NULL;
}

/* ------------------------------------------------------------------ .vmt (KeyValues text) */
static int KVToken(const char **pp, char *out, int outlen, int *quoted) {
    const char *p = *pp;
    for (;;) {
        while (*p && isspace((unsigned char)*p)) p++;
        if (p[0] == '/' && p[1] == '/') {
            while (*p && *p != '\n') p++;
            continue;
        }
        break;
    }
    *quoted = 0;
    if (!*p) return 0;
    int n = 0;
    if (*p == '"') {
        *quoted = 1;
        p++;
        while (*p && *p != '"') {
            if (n < outlen - 1) out[n++] = *p;
            p++;
        }
        if (*p == '"') p++;
    } else if (*p == '{' || *p == '}') {
        out[n++] = *p++;
    } else {
        while (*p && !isspace((unsigned char)*p) && *p != '"' && *p != '{' && *p != '}') {
            if (n < outlen - 1) out[n++] = *p;
            p++;
        }
    }
    out[n] = 0;
    *pp = p;
    return 1;
}

/* a "[$X360]"-style condition after a key: true on Windows PCs */
static int KVCondition(const char *s) {
    int not = 0;
    s++;
    if (*s == '!') not = 1, s++;
    int r = !_strnicmp(s, "$WIN32", 6) || !_strnicmp(s, "$WINDOWS", 8);
    return not ? !r : r;
}

/* the first key named <name> in the file, in the order written (vrad searches the root, then each key and its
 * block in turn); its value in out ("" for a block). Returns whether there is one. */
static int VMTFindKey(const char *text, const char *name, char *out, int outlen) {
    const char *p = text;
    char tok[1024], val[1024], next[1024];
    int q, depth = 0, found = 0;
    out[0] = 0;
    while (KVToken(&p, tok, sizeof(tok), &q)) {
        if (!q && tok[0] == '}') {
            if (--depth <= 0 && depth < 0) depth = 0;
            continue;
        }
        if (!q && tok[0] == '{') {
            depth++;
            continue;
        }
        /* a key: then its value or a block */
        const char *save = p;
        int vq;
        if (!KVToken(&p, val, sizeof(val), &vq)) val[0] = 0, vq = 0;
        int block = !vq && val[0] == '{';
        if (block) {
            p = save;                   /* (the '{' is read by the loop) */
            val[0] = 0;
        }
        /* a condition after the value drops the key when false */
        int keep = 1;
        const char *save2 = p;
        int cq;
        if (!block && KVToken(&p, next, sizeof(next), &cq) && !cq && next[0] == '[') keep = KVCondition(next);
        else p = save2;
        if (keep && !found && !_stricmp(tok, name)) {
            snprintf(out, outlen, "%s", val);
            found = 1;
        }
    }
    return found;
}

/* ------------------------------------------------------------------ .vtf: the largest mip's alpha */
static int BlockBytes(int fmt) { return fmt == 13 || fmt == 20 ? 8 : 16; }
static int IsDXT(int fmt) { return fmt == 13 || fmt == 14 || fmt == 15 || fmt == 20; }
static int PixelBytes(int fmt) {
    switch (fmt) {
    case 0: case 1: case 11: case 12: case 16: return 4;
    case 2: case 3: case 9: case 10: return 3;
    case 4: case 6: case 17: case 18: case 19: case 21: case 22: return 2;
    case 5: case 7: case 8: return 1;
    case 24: case 25: return 8;
    default: return 0;
    }
}
static int ImageBytes(int fmt, int w, int h) {
    if (IsDXT(fmt)) return ((w + 3) / 4) * ((h + 3) / 4) * BlockBytes(fmt);
    return w * h * PixelBytes(fmt);
}

static int g_dxt5Round = -1;       /* (debugging: the DXT5 alpha interpolation's rounding) */

static void DXTAlphaBlock(const unsigned char *b, int fmt, unsigned char a[16]) {
    if (fmt == 15) {                                   /* DXT5: interpolated alpha */
        int a0 = b[0], a1 = b[1];
        int pal[8];
        pal[0] = a0, pal[1] = a1;
        int r = g_dxt5Round;
        if (a0 > a1) {
            for (int i = 1; i < 7; i++) pal[i + 1] = ((7 - i) * a0 + i * a1 + r) / 7;
        } else {
            for (int i = 1; i < 5; i++) pal[i + 1] = ((5 - i) * a0 + i * a1 + (r ? 2 : 0)) / 5;
            pal[6] = 0, pal[7] = 255;
        }
        unsigned long long bits = 0;
        for (int i = 0; i < 6; i++) bits |= (unsigned long long)b[2 + i] << (8 * i);
        for (int i = 0; i < 16; i++) a[i] = (unsigned char)pal[(bits >> (3 * i)) & 7];
    } else if (fmt == 14) {                            /* DXT3: 4-bit alpha */
        for (int i = 0; i < 16; i++) {
            int v = (b[i / 2] >> (4 * (i & 1))) & 15;
            a[i] = (unsigned char)(v * 17);
        }
    } else {                                           /* DXT1: transparent where 3-colour mode uses index 3 */
        int c0 = b[0] | b[1] << 8, c1 = b[2] | b[3] << 8;
        unsigned int idx = b[4] | b[5] << 8 | b[6] << 16 | (unsigned int)b[7] << 24;
        for (int i = 0; i < 16; i++) a[i] = (c0 <= c1 && ((idx >> (2 * i)) & 3) == 3) ? 0 : 255;
    }
}

static unsigned char *LoadVTFAlpha(const char *name, int *pw, int *ph) {
    char path[1100];
    snprintf(path, sizeof(path), "materials/%s.vtf", name);
    int len;
    unsigned char *d = ReadGameFile(path, &len);
    if (!d) return NULL;
    unsigned char *alpha = NULL;
    if (len < 64 || memcmp(d, "VTF", 4)) goto done;
    unsigned int major, minor, headerSize, flags;
    memcpy(&major, d + 4, 4), memcpy(&minor, d + 8, 4), memcpy(&headerSize, d + 12, 4);
    int w = d[16] | d[17] << 8, h = d[18] | d[19] << 8;
    memcpy(&flags, d + 20, 4);
    int frames = d[24] | d[25] << 8, firstFrame = d[26] | d[27] << 8;
    int fmt, lowFmt;
    memcpy(&fmt, d + 52, 4);
    int mips = d[56];
    memcpy(&lowFmt, d + 57, 4);
    int lw = d[61], lh = d[62];
    int depth = (major > 7 || minor >= 2) ? (d[63] | d[64] << 8) : 1;
    if (depth < 1) depth = 1;
    if (frames < 1) frames = 1;
    int faces = 1;
    if (flags & 0x4000) faces = (minor < 5 && firstFrame != 0xffff) ? 7 : 6;
    if (!PixelBytes(fmt) && !IsDXT(fmt)) {
        Msg("Warning: -textureshadows: %s: texture format %d not read\n", path, fmt);
        goto done;
    }
    long start = -1;
    if (minor >= 3) {
        int nres;
        memcpy(&nres, d + 68, 4);
        for (int i = 0; i < nres && 80 + 8 * i + 8 <= len; i++) {
            const unsigned char *r = d + 80 + 8 * i;
            if (r[0] == 0x30 && r[1] == 0 && r[2] == 0) {
                int ofs;
                memcpy(&ofs, r + 4, 4);
                start = ofs;
            }
        }
    } else {
        start = (long)headerSize + (lowFmt >= 0 && lw && lh ? ImageBytes(lowFmt, lw, lh) : 0);
    }
    if (start < 0) goto done;
    long ofs = start;
    for (int m = mips - 1; m >= 1; m--) {
        int mw = w >> m > 0 ? w >> m : 1, mh = h >> m > 0 ? h >> m : 1;
        ofs += (long)ImageBytes(fmt, mw, mh) * frames * faces * depth;
    }
    if (ofs + ImageBytes(fmt, w, h) > len) goto done;
    const unsigned char *src = d + ofs;
    alpha = xalloc((size_t)w * h + 1);
    if (IsDXT(fmt)) {
        int bw = (w + 3) / 4, bh = (h + 3) / 4;
        for (int by = 0; by < bh; by++)
            for (int bx = 0; bx < bw; bx++) {
                unsigned char a[16];
                const unsigned char *blk = src + (by * bw + bx) * BlockBytes(fmt);
                if (fmt == 13) {
                    DXTAlphaBlock(blk, fmt, a);
                    for (int i = 0; i < 16; i++) a[i] = 255;   /* (plain DXT1: no alpha) */
                } else DXTAlphaBlock(blk, fmt, a);
                for (int i = 0; i < 16; i++) {
                    int x = bx * 4 + (i & 3), y = by * 4 + (i >> 2);
                    if (x < w && y < h) alpha[y * w + x] = a[i];
                }
            }
    } else {
        int pb = PixelBytes(fmt);
        for (int i = 0; i < w * h; i++) {
            const unsigned char *p = src + (size_t)i * pb;
            int a = 255;
            switch (fmt) {
            case 0: case 12: a = p[3]; break;                     /* RGBA8888, BGRA8888 */
            case 1: case 11: a = p[0]; break;                     /* ABGR8888, ARGB8888 */
            case 6: a = p[1]; break;                              /* IA88 */
            case 8: a = p[0]; break;                              /* A8 */
            case 19: a = (p[1] >> 4) * 17; break;                 /* BGRA4444 */
            case 21: a = (p[1] & 0x80) ? 255 : 0; break;          /* BGRA5551 */
            default: a = 255;
            }
            alpha[i] = (unsigned char)a;
        }
    }
    *pw = w, *ph = h;
    Msg("Loaded alpha texture %s\n", path);
done:
    free(d);
    return alpha;
}

/* ------------------------------------------------------------------ the textures and the triangles' entries */
typedef struct {
    char material[264];
    short width, height;
    unsigned char *alpha;
} alphatex_t;
typedef struct {
    int tex;
    float uv[3][2];
} matentry_t;

static alphatex_t *g_tex;
static int g_numtex;
static matentry_t *g_mat;
static int g_nummat, g_maxmat;

/* vrad's FindOrLoadIfValid: the material's alpha texture (-1: none) */
static int MaterialTexture(const char *matpath) {
    char path[1100];
    snprintf(path, sizeof(path), "%s", matpath);
    for (char *q = path; *q; q++)
        if (*q == '\\') *q = '/';
    for (int i = 0; i < g_numtex; i++)
        if (!_stricmp(g_tex[i].material, path)) return i;
    if (g_dxt5Round < 0) g_dxt5Round = getenv("HLDXT5ROUND") ? atoi(getenv("HLDXT5ROUND")) : 0;
    int len;
    unsigned char *vmt = ReadGameFile(path, &len);
    if (!vmt) return -1;
    char val[1024];
    int index = -1;
    if (VMTFindKey((const char *)vmt, "$translucent", val, sizeof(val)) ||
        VMTFindKey((const char *)vmt, "$alphatest", val, sizeof(val))) {
        if (VMTFindKey((const char *)vmt, "$basetexture", val, sizeof(val))) {
            int w, h;
            unsigned char *alpha = LoadVTFAlpha(val, &w, &h);
            if (alpha) {
                g_tex = realloc(g_tex, sizeof(alphatex_t) * (g_numtex + 1));
                alphatex_t *t = &g_tex[g_numtex];
                snprintf(t->material, sizeof(t->material), "%s", path);
                t->width = (short)w, t->height = (short)h, t->alpha = alpha;
                index = g_numtex++;
            }
        }
    }
    free(vmt);
    return index;
}

/* a model's textures' alpha textures (studiohdr_t: textures 0xcc/0xd0, $cdmaterials 0xd4/0xd8), -1 for none */
int *TS_ModelTextures(const unsigned char *mdl, int mlen, int *count) {
    int numtex, texindex, numcd, cdindex;
    memcpy(&numtex, mdl + 0xcc, 4), memcpy(&texindex, mdl + 0xd0, 4);
    memcpy(&numcd, mdl + 0xd4, 4), memcpy(&cdindex, mdl + 0xd8, 4);
    if (numtex < 0 || texindex < 0 || texindex + 64LL * numtex > mlen) numtex = 0;
    int *out = xalloc(sizeof(int) * (numtex + 1));
    for (int i = 0; i < numtex; i++) {
        out[i] = -1;
        if (numcd < 1 || cdindex < 0 || cdindex + 4 > mlen) continue;
        int cdofs, nameofs;
        memcpy(&cdofs, mdl + cdindex, 4);              /* (only the first folder: see the top) */
        memcpy(&nameofs, mdl + texindex + 64 * i, 4);
        if (cdofs < 0 || cdofs >= mlen || texindex + 64 * i + nameofs >= mlen) continue;
        char path[1100];
        snprintf(path, 261, "materials/%s%s.vmt", (const char *)mdl + cdofs, (const char *)mdl + texindex + 64 * i + nameofs);
        out[i] = MaterialTexture(path);
    }
    *count = numtex;
    return out;
}

/* a brush side's material's alpha texture */
int TS_MaterialTexture(const char *name) {
    char path[1100];
    snprintf(path, 261, "materials/%s.vmt", name);
    return MaterialTexture(path);
}

void TS_TextureSize(int tex, int *w, int *h) {
    *w = g_tex[tex].width;
    *h = g_tex[tex].height;
}

/* the texture's mean alpha over the box around the triangle's texture coordinates; *see_through: below 1 */
float TS_TriangleCoverage(int tex, const float *t0, const float *t1, const float *t2, int *see_through) {
    float umin = t1[0] > t0[0] ? t0[0] : t1[0];
    if (!(t2[0] > umin)) umin = t2[0];
    float umax = t0[0] > t1[0] ? t0[0] : t1[0];
    if (!(umax > t2[0])) umax = t2[0];
    float vmin = t1[1] > t0[1] ? t0[1] : t1[1];
    if (!(t2[1] > vmin)) vmin = t2[1];
    float vmax = t0[1] > t1[1] ? t0[1] : t1[1];
    if (!(vmax > t2[1])) vmax = t2[1];
#define CLAMP01(x) ((x) > 1.0f ? 1.0f : (0.0f > (x) ? 0.0f : (x)))
    umin = CLAMP01(umin), umax = CLAMP01(umax), vmin = CLAMP01(vmin), vmax = CLAMP01(vmax);
#undef CLAMP01
    const alphatex_t *t = &g_tex[tex];
    int u0 = (int)((float)(t->width - 1) * umin), u1 = (int)((float)(t->width - 1) * umax);
    int v0 = (int)((float)(t->height - 1) * vmin), v1 = (int)((float)(t->height - 1) * vmax);
    long long total = 0, count = 0;
    for (int v = v0; v <= v1; v++)
        for (int u = u0; u <= u1; u++) total += t->alpha[v * t->width + u], count++;
    if (!count) {
        *see_through = 0;
        return 1.0f;
    }
    *see_through = total < count * 255;
    return (float)((double)total / ((double)count * 255.0));
}

int TS_AddMaterial(int tex, const float *t0, const float *t1, const float *t2) {
    if (g_nummat == g_maxmat) {
        g_maxmat = g_maxmat ? 2 * g_maxmat : 256;
        g_mat = realloc(g_mat, sizeof(matentry_t) * g_maxmat);
    }
    matentry_t *m = &g_mat[g_nummat];
    m->tex = tex;
    memcpy(m->uv[0], t0, 8), memcpy(m->uv[1], t1, 8), memcpy(m->uv[2], t2, 8);
    return g_nummat++;
}

/* the coverage where a ray meets a see-through triangle (barycentrics as vrad passes them) */
float TS_SampleCoverage(int mat, float b0, float b1, float b2) {
    const matentry_t *m = &g_mat[mat];
    const alphatex_t *t = &g_tex[m->tex];
    float u = (m->uv[0][0] * b0 + m->uv[1][0] * b1) + m->uv[2][0] * b2;
    float v = (m->uv[0][1] * b0 + m->uv[1][1] * b1) + m->uv[2][1] * b2;
    int w = t->width, h = t->height;
    float U = (float)w * u, V = (float)h * v;
    int iu = (int)floorf(U), iv = (int)floorf(V);
    int u0 = iu & (w - 1), u1 = (iu + 1) & (w - 1), v0 = iv & (h - 1), v1 = (iv + 1) & (h - 1);
    float fu = U - (float)u0, fv = V - (float)v0;
    const unsigned char *r0 = t->alpha + v0 * w, *r1 = t->alpha + v1 * w;
    int top = (int)((float)r0[u1] * fu + (float)r0[u0] * (1.0f - fu));
    int bot = (int)((float)r1[u1] * fu + (float)r1[u0] * (1.0f - fu));
    int a = (int)((float)top * (1.0f - fv) + (float)bot * fv);
    return (float)a * (1.0f / 255.0f);
}

int TS_HaveTextures(void) { return g_nummat > 0; }

/* (for the GPU: the entries, 8 floats each (texture's number as int bits, the 3 corners' u v, 0); the textures, 4 ints
 * each (first byte in the texels, width, height, 0); every texture's texels one after another) */
void TS_GPUData(float **mats, int *nmat, int **texs, int *ntex, unsigned char **texels, size_t *ntexels) {
    *nmat = g_nummat, *ntex = g_numtex;
    *mats = xalloc(32 * (size_t)(g_nummat + 1));
    for (int i = 0; i < g_nummat; i++) {
        float *m = *mats + 8 * i;
        memcpy(m, &g_mat[i].tex, 4);
        m[1] = g_mat[i].uv[0][0], m[2] = g_mat[i].uv[0][1], m[3] = g_mat[i].uv[1][0];
        m[4] = g_mat[i].uv[1][1], m[5] = g_mat[i].uv[2][0], m[6] = g_mat[i].uv[2][1], m[7] = 0;
    }
    size_t total = 0;
    for (int i = 0; i < g_numtex; i++) total += (size_t)g_tex[i].width * g_tex[i].height;
    *texs = xalloc(16 * (size_t)(g_numtex + 1));
    *texels = xalloc(total + 4);
    size_t at = 0;
    for (int i = 0; i < g_numtex; i++) {
        size_t n = (size_t)g_tex[i].width * g_tex[i].height;
        (*texs)[4 * i] = (int)at, (*texs)[4 * i + 1] = g_tex[i].width, (*texs)[4 * i + 2] = g_tex[i].height, (*texs)[4 * i + 3] = 0;
        memcpy(*texels + at, g_tex[i].alpha, n);
        at += n;
    }
    *ntexels = at;
}
