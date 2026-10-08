/* hlvrad: Hammerless's lighting compiler, a drop-in for L4D2's vrad.exe.
 *
 * Built in stages and checked against vrad's own output (vrad is not deterministic: two of its runs
 * differ in a few luxels, so light values are compared within that noise; everything else exactly).
 * Behaviour was read from the Source SDK 2013 vrad (for what it does, not copied) and from L4D2's
 * vrad_dll.dll where L4D2 differs.
 */
#ifndef HLVRAD_H
#define HLVRAD_H

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef float vec_t;
typedef vec_t vec3_t[3];

#define DotProduct(a, b) ((a)[0] * (b)[0] + (a)[1] * (b)[1] + (a)[2] * (b)[2])
#define VectorAdd(a, b, c) ((c)[0] = (a)[0] + (b)[0], (c)[1] = (a)[1] + (b)[1], (c)[2] = (a)[2] + (b)[2])
#define VectorSubtract(a, b, c) ((c)[0] = (a)[0] - (b)[0], (c)[1] = (a)[1] - (b)[1], (c)[2] = (a)[2] - (b)[2])
#define VectorCopy(a, b) ((b)[0] = (a)[0], (b)[1] = (a)[1], (b)[2] = (a)[2])
#define VectorClear(a) ((a)[0] = (a)[1] = (a)[2] = 0)
#define VectorScale(a, s, b) ((b)[0] = (a)[0] * (s), (b)[1] = (a)[1] * (s), (b)[2] = (a)[2] * (s))

/* ------------------------------------------------------------------ lumps */
enum {
    LUMP_ENTITIES = 0, LUMP_PLANES = 1, LUMP_TEXDATA = 2, LUMP_VERTEXES = 3, LUMP_VISIBILITY = 4, LUMP_NODES = 5,
    LUMP_TEXINFO = 6, LUMP_FACES = 7, LUMP_LIGHTING = 8, LUMP_LEAFS = 10, LUMP_EDGES = 12, LUMP_SURFEDGES = 13,
    LUMP_MODELS = 14, LUMP_WORLDLIGHTS = 15, LUMP_LEAFFACES = 16, LUMP_DISPINFO = 26, LUMP_ORIGINALFACES = 27,
    LUMP_VERTNORMALS = 30, LUMP_VERTNORMALINDICES = 31, LUMP_DISP_VERTS = 33, LUMP_GAME_LUMP = 35,
    LUMP_PAKFILE = 40, LUMP_TEXDATA_STRING_DATA = 43, LUMP_TEXDATA_STRING_TABLE = 44,
    LUMP_LEAF_AMBIENT_INDEX_HDR = 51, LUMP_LEAF_AMBIENT_INDEX = 52, LUMP_LIGHTING_HDR = 53,
    LUMP_WORLDLIGHTS_HDR = 54, LUMP_LEAF_AMBIENT_LIGHTING_HDR = 55, LUMP_LEAF_AMBIENT_LIGHTING = 56,
    LUMP_FACES_HDR = 58, LUMP_MAP_FLAGS = 59
};

typedef struct {
    unsigned char *data;
    int len, version, fourcc;
} lump_t;
extern lump_t lumps[64];
extern int map_revision;

void LoadBSPFile(const char *path);
void WriteBSPFile(const char *path);
void SetLump(int i, void *data, int len, int version);    /* takes ownership of data */

/* ------------------------------------------------------------------ the map's arrays (views into lumps) */
typedef struct { vec3_t normal; float dist; int type; } dplane_t;
typedef struct { vec3_t point; } dvertex_t;
typedef struct { unsigned short v[2]; } dedge_t;
typedef struct {
    unsigned short planenum;
    unsigned char side, onnode;
    int firstedge;
    short numedges, texinfo, dispinfo, surfaceFogVolumeID;
    unsigned char styles[4];
    int lightofs;
    float area;
    int m_LightmapTextureMinsInLuxels[2], m_LightmapTextureSizeInLuxels[2];
    int origFace;
    unsigned short numPrims, firstPrimID;
    unsigned int smoothingGroups;
} dface_t;

typedef struct {
    float textureVecsTexelsPerWorldUnits[2][4];
    float lightmapVecsLuxelsPerWorldUnits[2][4];
    int flags, texdata;
} texinfo_t;
#define SURF_LIGHT 0x0001
#define SURF_SKY2D 0x0002
#define SURF_SKY 0x0004
#define SURF_NODRAW 0x0080
#define SURF_NOLIGHT 0x0400
#define SURF_BUMPLIGHT 0x0800
#define TEX_SPECIAL (SURF_SKY | SURF_NOLIGHT)

typedef struct {
    int contents;
    short cluster, area_flags;        /* area: low 9 bits, flags: high 7 */
    short mins[3], maxs[3];
    unsigned short firstleafface, numleaffaces, firstleafbrush, numleafbrushes;
    short leafWaterDataID, pad;
} dleaf_t;

typedef struct {
    int numpairs;
    char **keys, **values;
} entity_t;

extern dplane_t *dplanes; extern int numplanes;
extern dleaf_t *dleafs; extern int numleafs;
extern unsigned short *dleaffaces; extern int numleaffaces;
extern int numclusters;
extern entity_t *entities; extern int num_entities;
extern texinfo_t *texinfo; extern int numtexinfo;
extern unsigned char *dlightdata; extern int lightdatasize;
extern dvertex_t *dvertexes; extern int numvertexes;
extern dedge_t *dedges; extern int numedges;
extern int *dsurfedges; extern int numsurfedges;
extern dface_t *dfaces; extern int numfaces;
extern dface_t *g_pFaces;          /* the faces being lit: the HDR copy in HDR mode */

void MapArrays(void);              /* point the arrays above at the loaded lumps */
int EdgeVertex(const dface_t *f, int edge);

/* vis.c */
void MapVis(void);
int VisRowBytes(void);
int HaveVis(void);
void GetClusterPVS(int cluster, unsigned char *pvs);
int PVSCheck(const unsigned char *pvs, int cluster);
int LeafFlags(int leaf);
void SetLeafFlags(int leaf, int flags);

/* entities.c */
void ParseEntities(void);
const char *ValueForKey(const entity_t *e, const char *key);
float FloatForKey(const entity_t *e, const char *key);
void GetVectorForKey(const entity_t *e, const char *key, vec3_t v);

/* ------------------------------------------------------------------ options */
extern int g_bHDR;
extern float smoothing_threshold;

/* ------------------------------------------------------------------ steps */
void PairEdges(void);
void SaveVertexNormals(void);
int FaceHasPatches(int facenum);
void AssignLightStyles(void);
void PrecompLightmapOffsets(void);
void CreateDirectLights(void);

/* ------------------------------------------------------------------ utilities */
void Error(const char *fmt, ...);
void Msg(const char *fmt, ...);
void *xalloc(size_t n);
vec_t VectorNormalize(vec3_t v);

#endif
