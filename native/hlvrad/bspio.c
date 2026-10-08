/* Reading and writing the .bsp: every lump is read as it is and written back in the order L4D2's
 * tools write them (measured: vbsp's order with the HDR faces right after the faces). */
#include <stdarg.h>
#include "hlvrad.h"

lump_t lumps[64];
int map_revision;
static int game_lump_offset;      /* where the game lump was in the file read (its entries point into the file) */

dplane_t *dplanes; int numplanes;
dvertex_t *dvertexes; int numvertexes;
dedge_t *dedges; int numedges;
int *dsurfedges; int numsurfedges;
dface_t *dfaces; int numfaces;
dface_t *g_pFaces;

void Error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "Error: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
    exit(1);
}

void Msg(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    fflush(stdout);
}

void *xalloc(size_t n) {
    void *p = calloc(1, n ? n : 1);
    if (!p) Error("out of memory (%u bytes)", (unsigned)n);
    return p;
}

void LoadBSPFile(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) Error("Can't open %s", path);
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *file = xalloc(size);
    if (fread(file, 1, size, f) != (size_t)size) Error("Can't read %s", path);
    fclose(f);
    if (size < 1036 || memcmp(file, "VBSP", 4)) Error("%s is not a BSP file", path);
    int version;
    memcpy(&version, file + 4, 4);
    if (version != 21) Error("%s is BSP version %d (L4D2 maps are 21)", path, version);
    for (int i = 0; i < 64; i++) {
        int h[4];
        memcpy(h, file + 8 + 16 * i, 16);
        lump_t *l = &lumps[i];
        l->version = h[0];
        l->len = h[2];
        l->fourcc = h[3];
        if (h[1] < 0 || h[2] < 0 || (long)h[1] + h[2] > size) Error("lump %d runs past the end of the file", i);
        l->data = xalloc(l->len + 1);
        memcpy(l->data, file + h[1], l->len);
        if (i == LUMP_GAME_LUMP) game_lump_offset = h[1];
    }
    memcpy(&map_revision, file + 8 + 16 * 64, 4);
    free(file);
}

void SetLump(int i, void *data, int len, int version) {
    if (lumps[i].data != data) free(lumps[i].data);
    lumps[i].data = data;
    lumps[i].len = len;
    lumps[i].version = version;
}

void MapArrays(void) {
    dplanes = (dplane_t *)lumps[LUMP_PLANES].data; numplanes = lumps[LUMP_PLANES].len / sizeof(dplane_t);
    dvertexes = (dvertex_t *)lumps[LUMP_VERTEXES].data; numvertexes = lumps[LUMP_VERTEXES].len / sizeof(dvertex_t);
    dedges = (dedge_t *)lumps[LUMP_EDGES].data; numedges = lumps[LUMP_EDGES].len / sizeof(dedge_t);
    dsurfedges = (int *)lumps[LUMP_SURFEDGES].data; numsurfedges = lumps[LUMP_SURFEDGES].len / 4;
    texinfo = (texinfo_t *)lumps[LUMP_TEXINFO].data; numtexinfo = lumps[LUMP_TEXINFO].len / sizeof(texinfo_t);
    dfaces = (dface_t *)lumps[LUMP_FACES].data; numfaces = lumps[LUMP_FACES].len / sizeof(dface_t);
}

/* vbsp's order (as hlvbsp writes it) with 58 (HDR faces) after the faces; an empty lump takes the
 * current offset. */
static const int lump_order[] = {59, 6, 2, 43, 44, 10, 17, 1, 18, 19, 14, 5, 20, 21, 4, 0, 29, 62, 26, 3, 12, 13, 7, 58,
                                 33, 48, 28, 9, 8, 53, 37, 38, 39, 30, 31, 51, 52, 55, 56, 16, 36, 45, 50, 60, 61, 46,
                                 15, 41, 42, 54, 34, 47, 11, 27, 22, 23, 24, 25, 49, 35, 40};

void WriteBSPFile(const char *path) {
    int n = sizeof(lump_order) / sizeof(lump_order[0]), listed[64] = {0};
    for (int k = 0; k < n; k++) listed[lump_order[k]] = 1;
    for (int i = 0; i < 64; i++)
        if (!listed[i] && lumps[i].len) Error("lump %d has data but no place in the file order", i);
    FILE *f = fopen(path, "wb");
    if (!f) Error("Can't write %s", path);
    unsigned char header[1036];
    memset(header, 0, sizeof(header));
    memcpy(header, "VBSP", 4);
    int version = 21;
    memcpy(header + 4, &version, 4);
    memcpy(header + 8 + 64 * 16, &map_revision, 4);
    fwrite(header, 1, sizeof(header), f);
    int offset = (int)sizeof(header);
    for (int k = 0; k < n; k++) {
        int i = lump_order[k];
        lump_t *l = &lumps[i];
        int fields[4] = {l->version, offset, l->len, l->fourcc};
        memcpy(header + 8 + 16 * i, fields, 16);
        if (!l->len) continue;
        if (i == LUMP_GAME_LUMP && l->len >= 4) {
            /* the game lumps' directory holds file offsets: move them with the lump */
            int count;
            memcpy(&count, l->data, 4);
            for (int g = 0; g < count && 4 + 16 * g + 16 <= l->len; g++) {
                int ofs;
                memcpy(&ofs, l->data + 4 + 16 * g + 8, 4);
                ofs += offset - game_lump_offset;
                memcpy(l->data + 4 + 16 * g + 8, &ofs, 4);
            }
            game_lump_offset = offset;
        }
        fwrite(l->data, 1, l->len, f);
        offset += l->len;
        static const unsigned char zero[4] = {0};
        int pad = (4 - (offset & 3)) & 3;
        fwrite(zero, 1, pad, f);
        offset += pad;
    }
    fseek(f, 0, SEEK_SET);
    fwrite(header, 1, sizeof(header), f);
    fclose(f);
}

/* one game lump's data by its id (e.g. 'sprp' for static props), as read from the file */
const unsigned char *GameLump(int id, int *len) {
    const lump_t *l = &lumps[LUMP_GAME_LUMP];
    if (l->len < 4) return NULL;
    int count;
    memcpy(&count, l->data, 4);
    for (int g = 0; g < count && 4 + 16 * g + 16 <= l->len; g++) {
        int gid, ofs, glen;
        memcpy(&gid, l->data + 4 + 16 * g, 4);
        memcpy(&ofs, l->data + 4 + 16 * g + 8, 4);
        memcpy(&glen, l->data + 4 + 16 * g + 12, 4);
        if (gid != id) continue;
        int rel = ofs - game_lump_offset;
        if (rel < 0 || rel + glen > l->len) return NULL;
        *len = glen;
        return l->data + rel;
    }
    return NULL;
}
