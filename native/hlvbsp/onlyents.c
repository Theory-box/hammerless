/* -onlyents: an edited .vmf's entities put into the map compiled from it before, as vbsp -onlyents does (the
 * add-on's smart builds, when only entities changed). Geometry, visibility, lighting and detail props stay as they
 * are. What depends on the entities is made again: brush entities' model numbers, named lights' styles, the static
 * props (game lump "sprp"), the env_cubemap list, worldspawn's world_mins / world_maxs, the water_lod_control
 * vbsp adds, func_occluders' numbers. Every other lump is copied as it was, written in vbsp's order.
 *
 * vbsp also puts a "stale.txt" in the pakfile (its mark that the lighting may not fit the new entities; the
 * add-on takes it out again), and when the map has no leaf ambient lighting (it was never lit) it writes an
 * empty one (one dark sample per leaf), as its reader makes up for old maps. */
#include "hlvbsp.h"
#include "disp.h"

typedef char check_dnode[sizeof(dnode_t) == 32 ? 1 : -1];
typedef char check_dleaf[sizeof(dleaf_t) == 32 ? 1 : -1];
typedef char check_dmodel[sizeof(dmodel_t) == 48 ? 1 : -1];

static unsigned char *bsp;
static long bsplen;
static int lver[64], lofs[64], llen[64];

void SetLump(int i, const void *data, int len, int version);
int LumpFileOffset(int lump);
void WriteBSPFile(const char *path);
void UnparseEntities(void);
extern char *entdata;
extern int entdatasize;
void EmitStaticProps(void);
extern unsigned char *g_sprp;
extern int g_sprp_len;
void LoadPakLump(const unsigned char *zip, int len);
void AddFileToPak(const char *name, unsigned char *data, int len);
unsigned char *BuildPakLump(int *outlen);
unsigned char *Cubemap_Lump(int *len);

static void ReadBSP(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) Error("Can't open %s (-onlyents needs the map compiled before)", path);
    fseek(f, 0, SEEK_END);
    bsplen = ftell(f);
    fseek(f, 0, SEEK_SET);
    bsp = xalloc(bsplen + 1);
    if (bsplen < 1036 || fread(bsp, 1, bsplen, f) != (size_t)bsplen) Error("Can't read %s", path);
    fclose(f);
    int version;
    memcpy(&version, bsp + 4, 4);
    if (memcmp(bsp, "VBSP", 4) || version != 21) Error("%s is not an L4D2 map (version 21)", path);
    for (int i = 0; i < 64; i++) {
        int fields[4];
        memcpy(fields, bsp + 8 + 16 * i, 16);
        lver[i] = fields[0], lofs[i] = fields[1], llen[i] = fields[2];
        if (fields[3]) Error("%s has a compressed lump (%d): -onlyents can't rewrite it", path, i);
        if (llen[i] < 0 || (llen[i] && (lofs[i] < 0 || (long)lofs[i] + llen[i] > bsplen))) Error("%s is damaged (lump %d)", path, i);
    }
}

/* A copy of a lump's records (so they can be used as the program's own arrays). */
static void *LumpCopy(int i, int recsize, int *count) {
    if (llen[i] % recsize) Error("Lump %d of the map has an odd size", i);
    *count = llen[i] / recsize;
    void *p = xalloc(llen[i] + recsize);
    memcpy(p, bsp + lofs[i], llen[i]);
    return p;
}

/* The compiled tree in place of the .vmf's: the static props' leaves and the world's bounds are found in it, as
 * vbsp finds them in the map it loaded (the planes too: the .vmf's own are numbered differently). */
static void UseCompiledTree(void) {
    int n;
    if (llen[1] / 20 > MAX_MAP_PLANES) Error("The map has too many planes");
    unsigned char *p = LumpCopy(1, 20, &n);
    for (int i = 0; i < n; i++) {
        memcpy(mapplanes[i].normal, p + 20 * i, 12);
        memcpy(&mapplanes[i].dist, p + 20 * i + 12, 4);
        memcpy(&mapplanes[i].type, p + 20 * i + 16, 4);
        mapplanes[i].hash_chain = NULL;
    }
    nummapplanes = n;
    free(p);
    dnodes = LumpCopy(5, 32, &numnodes);
    dleafs = LumpCopy(10, 32, &numleafs);
    dvertexes = LumpCopy(3, 12, &numvertexes);
    dedges = LumpCopy(12, 4, &numedges);
    dsurfedges = LumpCopy(13, 4, &numsurfedges);
    dleaffaces = LumpCopy(16, 2, &numleaffaces);
    dmodel_t *models = LumpCopy(14, 48, &n);
    if (n < 1 || n > 1024) Error("The map has no world model");
    memcpy(dmodels, models, 48 * n);
    nummodels = n;
    free(models);
    unsigned char *f = LumpCopy(7, 56, &numfaces);
    dfaces = xalloc(sizeof(dface_t) * (numfaces + 1));
    for (int i = 0; i < numfaces; i++) {
        memcpy(&dfaces[i].firstedge, f + 56 * i + 4, 4);
        memcpy(&dfaces[i].numedges, f + 56 * i + 8, 2);
        memcpy(&dfaces[i].texinfo, f + 56 * i + 10, 2);
    }
    free(f);
    unsigned char *t = LumpCopy(6, 72, &numtexinfo);
    texinfos = xalloc(sizeof(texinfo_t) * (numtexinfo + 1));
    for (int i = 0; i < numtexinfo; i++) {
        memcpy(texinfos[i].vecs, t + 72 * i, 32);
        memcpy(texinfos[i].lmvecs, t + 72 * i + 32, 32);
        memcpy(&texinfos[i].flags, t + 72 * i + 64, 4);
        memcpy(&texinfos[i].texdata, t + 72 * i + 68, 4);
    }
    free(t);
    /* vbsp bounds as many displacements as the map has, taking each from the .vmf's list */
    int ndisp = llen[26] / (int)sizeof(ddispinfo_t);
    if (ndisp < nummapdisps) nummapdisps = ndisp;
}

/* func_occluders' numbers, as the full compile gave them (vbsp's FixupOnlyEntsOccluderEntities). */
static void NumberOccluders(void) {
    int n = 0;
    char str[16];
    for (int i = 1; i < num_entities; i++) {
        if (strcmp(ValueForKey(&entities[i], "classname"), "func_occluder")) continue;
        sprintf(str, "%i", n++);
        SetKeyValue(&entities[i], "occludernumber", str);
    }
}

/* The game lumps as they were, the static props taken out and the new ones added at the end (vbsp drops the old
 * "sprp" and makes a new one, which goes last). The directory holds file offsets: made once the place is known. */
static unsigned char *game;
static int gamelen;

typedef struct { char id[4]; unsigned short flags, version; const unsigned char *data; int len; } gamelump_t;

static void BuildGameLump(void) {
    gamelump_t gl[64];
    int n = 0;
    if (llen[35] >= 4) {
        const unsigned char *g = bsp + lofs[35];
        int count;
        memcpy(&count, g, 4);
        if (count < 0 || count > 63 || 4 + 16 * count > llen[35]) Error("The map's game lump is damaged");
        for (int i = 0; i < count; i++) {
            const unsigned char *d = g + 4 + 16 * i;
            gamelump_t *l = &gl[n];
            memcpy(l->id, d, 4);
            memcpy(&l->flags, d + 4, 2);
            memcpy(&l->version, d + 6, 2);
            int ofs;
            memcpy(&ofs, d + 8, 4);
            memcpy(&l->len, d + 12, 4);
            if (l->len < 0 || ofs < 0 || (long)ofs + l->len > bsplen) Error("The map's game lump is damaged");
            l->data = bsp + ofs;
            if (memcmp(l->id, "prps", 4)) n++;      /* ("sprp", stored backwards) */
        }
    }
    static unsigned char empty12[12];
    gamelump_t *sp = &gl[n++];
    memcpy(sp->id, "prps", 4);
    sp->flags = 0;
    sp->version = 9;
    sp->data = g_sprp ? g_sprp : empty12;
    sp->len = g_sprp ? g_sprp_len : 12;
    gamelen = 4 + 16 * n;
    for (int i = 0; i < n; i++) gamelen += gl[i].len;
    game = xalloc(gamelen);
    memcpy(game, &n, 4);
    unsigned char *q = game + 4 + 16 * n;
    for (int i = 0; i < n; i++) {
        memcpy(q, gl[i].data, gl[i].len);
        q += gl[i].len;
    }
    SetLump(35, game, gamelen, lver[35]);
    int ofs = LumpFileOffset(35) + 4 + 16 * n;
    for (int i = 0; i < n; i++) {
        unsigned char *d = game + 4 + 16 * i;
        memcpy(d, gl[i].id, 4);
        memcpy(d + 4, &gl[i].flags, 2);
        memcpy(d + 6, &gl[i].version, 2);
        memcpy(d + 8, &ofs, 4);
        memcpy(d + 12, &gl[i].len, 4);
        ofs += gl[i].len;
    }
}

/* A map never lit has no leaf ambient lighting: vbsp's reader makes it (one sample per leaf: the old per-leaf
 * cubes when there are any, else dark) and writes it with the rest. */
static void MakeLeafAmbientIfNone(void) {
    if (llen[52] > 0) return;
    int n = numleafs;
    unsigned char *index = xalloc(4 * n + 1), *ldr = xalloc(28 * n + 1), *hdr = xalloc(28 * n + 1);
    for (int i = 0; i < n; i++) {
        unsigned short count = 1, first = (unsigned short)i;
        memcpy(index + 4 * i, &count, 2);
        memcpy(index + 4 * i + 2, &first, 2);
        if (llen[56] > 0 && 24 * (i + 1) <= llen[56]) memcpy(ldr + 28 * i, bsp + lofs[56] + 24 * i, 24);
        if (llen[55] > 0 && 24 * (i + 1) <= llen[55]) memcpy(hdr + 28 * i, bsp + lofs[55] + 24 * i, 24);
    }
    SetLump(52, index, 4 * n, lver[52]);
    SetLump(51, index, 4 * n, lver[51]);
    SetLump(56, ldr, 28 * n, 1);
    SetLump(55, hdr, 28 * n, 1);
}

void OnlyEnts(const char *source) {
    char path[1100];
    snprintf(path, sizeof(path), "%s.bsp", source);
    ReadBSP(path);
    memcpy(&map_revision, bsp + 8 + 64 * 16, 4);      /* (the .vmf's "mapversion" replaces it, when it has one) */
    char vmf[1100];
    snprintf(vmf, sizeof(vmf), "%s.vmf", source);
    LoadMapFile(vmf);
    UseCompiledTree();
    SetModelNumbers();
    SetLightStyles();
    EmitStaticProps();
    ComputeBoundsNoSkybox();
    EnsurePresenceOfWaterLODControlEntity();
    NumberOccluders();
    UnparseEntities();

    for (int i = 0; i < 64; i++) SetLump(i, llen[i] ? bsp + lofs[i] : NULL, llen[i], lver[i]);
    SetLump(0, entdata, entdatasize, lver[0]);
    int len;
    unsigned char *cubemaps = Cubemap_Lump(&len);
    SetLump(42, cubemaps, len, lver[42]);
    MakeLeafAmbientIfNone();
    if (llen[40] > 0) LoadPakLump(bsp + lofs[40], llen[40]);
    static unsigned char stale[] = "stale";
    AddFileToPak("stale.txt", stale, (int)sizeof(stale));
    unsigned char *pak = BuildPakLump(&len);
    SetLump(40, pak, len, lver[40]);
    BuildGameLump();             /* (last: its directory needs every other lump's size) */
    Msg("Writing %s\n", path);
    WriteBSPFile(path);
}
