/* The pakfile lump: a zip of files the map carries. vbsp packs the default cubemap for env_cubemap
 * materials: in this vbsp the faces are cleared, not copied from the sky, so it is a black 32x32 cube
 * (VTF 7.4, which still stores 7 faces: the 6 sides and a sphere map) in DXT5 for LDR and all zeros
 * in RGBA16161616F for HDR. The zip is the plain kind vbsp writes: stored files, no dates, and a
 * 32-byte "XZP1 0" comment. */
#include "hlvbsp.h"

typedef struct { char name[300]; unsigned char *data; int len; unsigned crc; } pakfile_t;
static pakfile_t pakfiles[64];
static int numpakfiles;

static unsigned crc32(const unsigned char *p, int n) {
    static unsigned table[256];
    if (!table[1]) {
        for (unsigned i = 0; i < 256; i++) {
            unsigned c = i;
            for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
    }
    unsigned c = 0xFFFFFFFFu;
    for (int i = 0; i < n; i++) c = table[(c ^ p[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

void AddFileToPak(const char *name, unsigned char *data, int len) {
    if (numpakfiles == 64) Error("too many files in the pakfile");
    pakfile_t *f = &pakfiles[numpakfiles++];
    strncpy(f->name, name, sizeof(f->name) - 1);
    f->data = data;
    f->len = len;
    f->crc = crc32(data, len);
}

static void put16(unsigned char *p, int v) { p[0] = (unsigned char)v; p[1] = (unsigned char)(v >> 8); }
static void put32(unsigned char *p, unsigned v) { put16(p, (int)(v & 0xFFFF)); put16(p + 2, (int)(v >> 16)); }

unsigned char *BuildPakLump(int *outlen) {
    if (!numpakfiles) {
        *outlen = 0;
        return NULL;
    }
    int size = 22 + 32;
    for (int i = 0; i < numpakfiles; i++) size += 30 + 46 + 2 * (int)strlen(pakfiles[i].name) + pakfiles[i].len;
    unsigned char *out = xalloc(size), *p = out;
    int *offsets = xalloc(sizeof(int) * numpakfiles);
    for (int i = 0; i < numpakfiles; i++) {
        pakfile_t *f = &pakfiles[i];
        int nl = (int)strlen(f->name);
        offsets[i] = (int)(p - out);
        put32(p, 0x04034b50); put16(p + 4, 10); put16(p + 6, 0); put16(p + 8, 0);
        put16(p + 10, 0); put16(p + 12, 0);
        put32(p + 14, f->crc); put32(p + 18, (unsigned)f->len); put32(p + 22, (unsigned)f->len);
        put16(p + 26, nl); put16(p + 28, 0);
        memcpy(p + 30, f->name, nl);
        memcpy(p + 30 + nl, f->data, f->len);
        p += 30 + nl + f->len;
    }
    int cd = (int)(p - out);
    for (int i = 0; i < numpakfiles; i++) {
        pakfile_t *f = &pakfiles[i];
        int nl = (int)strlen(f->name);
        memset(p, 0, 46);
        put32(p, 0x02014b50); put16(p + 4, 20); put16(p + 6, 10);
        put32(p + 16, f->crc); put32(p + 20, (unsigned)f->len); put32(p + 24, (unsigned)f->len);
        put16(p + 28, nl);
        put32(p + 42, (unsigned)offsets[i]);
        memcpy(p + 46, f->name, nl);
        p += 46 + nl;
    }
    int cdsize = (int)(p - out) - cd;
    memset(p, 0, 22 + 32);
    put32(p, 0x06054b50);
    put16(p + 8, numpakfiles); put16(p + 10, numpakfiles);
    put32(p + 12, (unsigned)cdsize); put32(p + 16, (unsigned)cd);
    put16(p + 20, 32);
    memcpy(p + 22, "XZP1 0", 6);
    p += 22 + 32;
    free(offsets);
    *outlen = (int)(p - out);
    return out;
}

/* A black 32x32 cube map: VTF 7.4 header (88 bytes, one resource: the image at 88), then the mips
 * smallest first, 7 faces each. */
static unsigned char *CubemapVTF(int hdr, int *outlen) {
    const int size = 32, mips = 6, faces = 7;
    int body = 0;
    for (int m = 0; m < mips; m++) {
        int s = size >> m;
        body += faces * (hdr ? s * s * 8 : ((s + 3) / 4) * ((s + 3) / 4) * 16);
    }
    unsigned char *v = xalloc(88 + body);
    memcpy(v, "VTF", 4);
    put32(v + 4, 7); put32(v + 8, 4); put32(v + 12, 88);
    put16(v + 16, size); put16(v + 18, size);
    put32(v + 20, hdr ? 0x6000 : 0x4000);        /* ENVMAP (+ 0x2000 for the HDR one) */
    put16(v + 24, 1);                            /* frames */
    float one = 1.0f;
    memcpy(v + 32, &one, 4); memcpy(v + 36, &one, 4); memcpy(v + 40, &one, 4);   /* reflectivity */
    memcpy(v + 48, &one, 4);                     /* bump scale */
    put32(v + 52, hdr ? 24 : 15);                /* RGBA16161616F / DXT5 */
    v[56] = mips;
    put32(v + 57, 0xFFFFFFFFu);                  /* no low-res image */
    v[61] = 0; v[62] = 0;
    put16(v + 63, 1);                            /* depth */
    put32(v + 68, 1);                            /* one resource */
    put32(v + 80, 0x30);                         /* the high-res image ... */
    put32(v + 84, 88);                           /* ... at 88 */
    unsigned char *p = v + 88;
    if (!hdr) {
        for (int m = mips - 1; m >= 0; m--) {
            int s = size >> m, blocks = ((s + 3) / 4) * ((s + 3) / 4);
            for (int f = 0; f < faces; f++)
                for (int b = 0; b < blocks; b++, p += 16) {
                    /* alpha 0 and 1, all alpha indices 0; colour 0 and 1 (black); a texel outside the
                     * mip (1x1 and 2x2) gets index 3 */
                    p[1] = 1;
                    p[10] = 1;
                    unsigned idx = 0;
                    for (int y = 0; y < 4; y++)
                        for (int x = 0; x < 4; x++)
                            if (x >= s || y >= s) idx |= 3u << (2 * (4 * y + x));
                    put32(p + 12, idx);
                }
        }
    }
    *outlen = 88 + body;
    return v;
}

void AddDefaultCubemaps(const char *mapname) {
    char name[300];
    int len;
    unsigned char *ldr = CubemapVTF(0, &len);
    snprintf(name, sizeof(name), "materials/maps/%s/cubemapdefault.vtf", mapname);
    AddFileToPak(name, ldr, len);
    unsigned char *hdr = CubemapVTF(1, &len);
    snprintf(name, sizeof(name), "materials/maps/%s/cubemapdefault.hdr.vtf", mapname);
    AddFileToPak(name, hdr, len);
}
