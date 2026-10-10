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
    LUMP_MODELS = 14, LUMP_LEAFBRUSHES = 17, LUMP_BRUSHES = 18, LUMP_BRUSHSIDES = 19, LUMP_WORLDLIGHTS = 15, LUMP_LEAFFACES = 16, LUMP_DISPINFO = 26, LUMP_ORIGINALFACES = 27,
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
int ReadLumpFrom(const char *path, int i, unsigned char **data, int *len, int *version);
void SetLump(int i, void *data, int len, int version);    /* takes ownership of data */
const unsigned char *GameLump(int id, int *len);
int GameLumpVersion(int id);
void SetGameLump(int id, int version, const void *data, int len);

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
    int planenum, children[2];
    short mins[3], maxs[3];
    unsigned short firstface, numfaces;
    short area, pad;
} dnode_t;

typedef struct { int firstside, numsides, contents; } dbrush_t;
typedef struct { unsigned short planenum; short texinfo, dispinfo, bevel; } dbrushside_t;
#define CONTENTS_SOLID 0x1
#define CONTENTS_OPAQUE 0x80
#define CONTENTS_MOVEABLE 0x4000
#define MASK_OPAQUE (CONTENTS_SOLID | CONTENTS_MOVEABLE | CONTENTS_OPAQUE)
#define TRACE_ID_SKY 0x01000000
#define TRACE_ID_OPAQUE 0x02000000
#define TRACE_ID_STATICPROP 0x04000000
#define NUMVERTEXNORMALS 162
#define MAX_TRACE_LENGTH (1.732050807569 * 2 * 16384)
extern const float g_anorms[NUMVERTEXNORMALS][3];
extern dbrush_t *dbrushes; extern int numbrushes;
extern dbrushside_t *dbrushsides; extern int numbrushsides;
extern unsigned short *dleafbrushes; extern int numleafbrushes;

typedef struct {
    vec3_t mins, maxs, origin;
    int headnode, firstface, numfaces;
} dmodel_t;

typedef struct {
    int numpoints;
    vec3_t *p;
} winding_t;

/* ------------------------------------------------------------------ lights */
enum { emit_surface, emit_point, emit_spotlight, emit_skylight, emit_quakelight, emit_skyambient };

typedef struct {
    vec3_t origin, intensity, normal, shadow_cast_offset;
    int cluster, type, style;
    float stopdot, stopdot2, exponent, radius, constant_attn, linear_attn, quadratic_attn;
    int flags;
} dworldlight_t;

typedef struct directlight_s {
    int index;
    dworldlight_t light;
    unsigned char *pvs;               /* clusters it can reach */
    int facenum;
    float m_flStartFadeDistance, m_flEndFadeDistance, m_flCapDist;
    float sun_extent;                 /* sin of its own spread angle (a light_directional's SunSpreadAngle) */
    int has_sun_extent;               /* (else the light_environment's: g_SunAngularExtent) */
    int directional;                  /* a light_directional */
    struct directlight_s *next;
} directlight_t;

extern directlight_t *activelights, *gSkyLight, *gAmbient;
extern int numdlights;

typedef struct {
    int numpairs;
    char **keys, **values;
} entity_t;

extern dplane_t *dplanes; extern int numplanes;
extern dleaf_t *dleafs; extern int numleafs;
extern dnode_t *dnodes; extern int numnodes;
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

/* disp.c */
typedef struct {
    int face, power, contents, numverts;
    vec3_t points[4];                  /* the base quad, from the corner nearest the start position */
    vec3_t *verts, *flat;
    float *alpha;
    vec3_t *normals;                   /* per vertex, smoothed across neighbours */
    float sample_radius2;              /* how far a luxel gathers samples */
    float sample_width;                /* a luxel's size in world units */
    float patch_radius2;               /* how far a luxel gathers bounced light from patches */
} dispsurf_t;
extern dispsurf_t *dispsurfs;
extern int numdispsurfs;
void LoadDisplacements(void);
int DispTriangles(const dispsurf_t *d, unsigned short (*tris)[3]);
void AddDispsForRayTrace(void);
void DispUVToSurfPoint(const dispsurf_t *d, float u, float v, float push, vec3_t out);
void DispUVToSurfNormal(const dispsurf_t *d, float u, float v, vec3_t out);

/* staticprops.c */
extern const char *g_modeldir, *g_gamedir;
void AddStaticPropsForRayTrace(void);

/* embree.c (-embree) */
extern int g_bEmbree;
int EM_Init(void);
void *EM_NewScene(const float *verts, int ntris);
int EM_Nearest(void *scene, const float o[3], const float d[3], float tnear, float tfar, int (*skip)(int tri, void *data),
               void *data, float *t, float *u, float *v);
void EM_Nearest4(void *scene, const float o[3][4], const float d[3][4], const float tnear[4], const float tfar[4],
                 int (*skip)(int tri, void *data), void *data, int hit[4], float t[4]);
void EM_Blocked4(void *scene, const float o[3][4], const float d[3][4], const float tnear[4], const float tfar[4],
                 int (*skip)(int tri, void *data), void *data, int blocked[4]);

/* gpu.c (-gpu: lighting on the GPU through Vulkan ray queries; faster, not vrad's to the bit) */
extern int g_bGPU, g_gpuCheck;
typedef struct {                       /* (gather.comp's Group) 4 points lit together */
    float pos[3][4];
    float nrm[4][3][4];                /* [normal][axis][point] */
    int cluster[4];
    int lanes, normalCount, flags, skip, slot, pad[3];
} gpugroup_t;
#define GPU_FORCE_FAST 1
#define GPU_IGNORE_NORMALS 2
#define GPU_AMBIENT_ONLY 4
#define GPU_NON_AMBIENT_ONLY 8
#define GPU_PROP_VERTEX 16
typedef struct { float lmS[4], lmT[4]; int a[4], b[4], c[4]; float refl[4]; } gpuwface_t;   /* (walk.glsl's) */
typedef struct { int a[4], b[4]; float bmins[4], bmaxs[4]; } gpuwdisp_t;
typedef struct { float lo[4], hi[4]; int i[4]; } gpuwnode_t;
typedef struct {                       /* the map as leafambient.c's surface finder walks it */
    const float *planes; int nplanes;              /* 4 floats each */
    const int *nodes; int nnodes;                  /* 4 ints each */
    const int *leaves; int nleaves;                /* 4 ints each */
    const int *leaffaces; int nleaffaces;
    const gpuwface_t *faces; int nfaces;
    const float *windings; int nwindings;          /* 4 floats each */
    const gpuwdisp_t *disps; int ndisps;
    const float *dverts; int ndverts;              /* 4 floats each */
    const int *dtris; int ndtris;                  /* 4 ints each */
    const gpuwnode_t *dnodes; int ndnodes;
    const int *dorder; int ndorder;
    const float *dlux; int ndlux;                  /* 2 floats each */
    const int *leafdisps; int nleafdisps;
} gpuwalk_t;
void GPU_StartInit(void);
int GPU_Init(void);
void GPU_ShadowScene(void);
void GPU_Lights(void);
int GPU_NumSlots(void);
int GPU_SlotStyle(int slot);
int GPU_StyleSlot(int style);
void GPU_Gather(const gpugroup_t *groups, int n, int slot, float *out);       /* out: 16 floats per point */
void GPU_WalkScene(const gpuwalk_t *w);
void GPU_Lightmaps(void);
void GPU_Ambient(const float *points, int n, const float skylight[4], float *out);   /* out: 18 floats per point */
void GPU_PropIndirect(const float *points, int n, float *out);
void GPU_PatchEnds(const float *ends, int n);
typedef struct { int face, kind; float lux[6]; } gpustri_t;     /* (gi.comp's STri: 0 face, 1 disp, 2 sky) */
void GPU_GIScene(const float *verts, const gpustri_t *tris, int ntris);
void GPU_GIGather(const gpugroup_t *groups, int n, int rays, int seed, float *out);   /* out: 16 floats a point */
extern int g_giPasses, g_giRays;                                  /* (-gi n, -girays n) */
void BuildIndirectGPU(void);
void GpuGISurfaces(void);
void GPU_ClearPairs(const int *pairs, int n, uint32_t *bits);                  /* (shooter, receiver) in */                       /* in 7, out 3 per vertex */
const float *RT_Triangles(int *n);
const float *SkyMapData(int *w, int *h);
const vec3_t *SkyDirections(int *n);

/* raytrace.c */
void RT_AddTriangle(int id, const vec3_t v0, const vec3_t v1, const vec3_t v2);
void RT_SetupAccelerationStructure(void);
void RT_Trace4(const float o[3][4], const float d[3][4], const float tmin[4], const float tmax[4], int skip_id, int hit[4],
               float hitdist[4]);
void TestLine4(const float start[3][4], const float stop[3][4], int static_prop_to_skip, float vis[4]);
void TestLine_DoesHitSky4(const float start[3][4], const float stop[3][4], int static_prop_to_skip, float frac[4]);
int RT_TriangleID(int tri);
float TestLine(const vec3_t start, const vec3_t stop, int static_prop_to_skip);
float TestLine_DoesHitSky(const vec3_t start, const vec3_t stop, int static_prop_to_skip);
void AddBrushesForRayTrace(void);

void RT_Trace4Mask(const float o[3][4], const float d[3][4], const float tmax[4], int mask, int hit[4], float hitdist[4]);

/* bounce.c */
typedef struct {
    winding_t *winding;
    vec3_t mins, maxs, face_mins, face_maxs, origin, normal, plane_normal;
    float plane_dist, area, scale[2], luxscale, chop, basearea;
    int sky, needs_bump, face, cluster;
    int parent, child1, child2, next, next_parent, next_cluster_child;
    vec3_t baselight, reflectivity, samplelight, directlight;
    float samplearea;
    vec3_t totallight[4];              /* per bump normal */
    int indices[3];                    /* (displacement patches: the vertices of their corners, -1 below the grid) */
    unsigned short iteration_key;
    int numtransfers;
    void *transfers;
} patch_t;
extern patch_t *patches;
extern int numpatches, *face_patches, g_numbounce;
void MakePatches(void);
void SubdividePatches(void);
void AddSampleToPatch(int facenum, const vec3_t pos, float area, const vec3_t light);
void FinishPatchLights(int facenum);
void MakeAllScales(void);
void AddDispsToClusterTable(void);
void PreGetBumpNormalsForDisp(const texinfo_t *tx, vec3_t u, vec3_t v, vec3_t normal);
extern float dispchop;
void BounceLight(void);

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
void FindFacePatches(void);
int FaceHasPatches(int facenum);
extern vec3_t *face_offset;

/* winding.c */
winding_t *AllocWinding(int points);
void FreeWinding(winding_t *w);
winding_t *WindingFromFace(const dface_t *f, const vec3_t origin);
void RemoveColinearPoints(winding_t *w);
float WindingArea(const winding_t *w);
winding_t *BaseWindingForPlane(const vec3_t normal, vec_t dist);
void ChopWindingInPlace(winding_t **inout, const vec3_t normal, vec_t dist, vec_t epsilon);
winding_t *CopyWinding(const winding_t *w);
void ClipWindingEpsilon(const winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon, winding_t **front,
                        winding_t **back);
void WindingBounds(const winding_t *w, vec3_t mins, vec3_t maxs);
void WindingCenter(const winding_t *w, vec3_t center);
float WindingAreaAndBalancePoint(const winding_t *w, vec3_t center);

/* normals.c */
const float *FaceCornerNormal(int facenum, int corner);
const int *FaceNeighbours(int facenum, int *count);

/* direct.c */
void AllocFacelights(void);
void BuildFacelights(int facenum);
void BuildFacelightsGPU(void);
void FinalLightFace(int facenum);
void GetPhongNormal(int facenum, const vec3_t spot, vec3_t phongnormal);
void GetPhongNormalScalar(int facenum, const vec3_t spot, vec3_t phongnormal);
void GetBumpNormals(const float *sVect, const float *tVect, const vec3_t flatNormal, const vec3_t phongNormal,
                    vec3_t bumpNormals[3]);
extern vec3_t *face_centroids;
float face_entity_minlight(int facenum);
void AssignLightStyles(void);
void PrecompLightmapOffsets(void);
void CreateDirectLights(void);
int LightForString(const char *s, vec3_t intensity);
void LoadTexLights(const char *gamedir, const char *bsppath, const char *designer);
void LightForTexture(const char *name, vec3_t result);
const char *TexDataName(int texdata);
extern float g_SunAngularExtent;
extern int g_bFast, g_bExtra;
extern int g_ssPoints, g_ssPasses, g_bFixQuirks;   /* (supersampling: points across a luxel, passes; beyond vrad) */
extern float g_ssThreshold;
/* No Bake Volumes (nobake.c, -nobake): points whose light isn't baked, and the colour they get instead */
extern int g_bNoBake;
void LoadNoBake(const char *path);
int NoBakePoint(const float *p);
void NoBakeColor(vec3_t out);
extern float maxchop, minchop;                     /* (bounce patches' sizes, in luxels) */
void ExportDirectLightsToWorldLights(void);
void ComputePerLeafAmbientLighting(void);
void ComputeDetailPropLighting(void);
void ComputeStaticPropLighting(void);
void PrepareFinalLight(void);
void BuildSkyDirections(int n);
void LoadSkyMap(const char *path);
int HaveSkyMap(void);
void SkyMapColor(const vec3_t dir, float scale, vec3_t out);
float Luminance(const vec3_t c);
#define MAX_THREADS 64
extern int g_numthreads;
int NumThreads(void);
void RunThreadsOn(int count, void (*fn)(int item, int thread));
void RunThreadsOnRange(int first, int count, void (*fn)(int item, int thread));
double Seconds(void);
extern int g_bStaticPropLighting, g_bStaticPropPolys;
extern float g_flSkySampleScale;            /* (-final: 16, -extrasky n: n) */
extern char **g_noshadow;                    /* (lights.rad "noshadow" materials) */
extern int g_numnoshadow;
int ClusterFromPoint(const vec3_t p);
int PointLeafnum(const vec3_t p);

/* ------------------------------------------------------------------ utilities */
void Error(const char *fmt, ...);
void Msg(const char *fmt, ...);
void *xalloc(size_t n);
vec_t VectorNormalize(vec3_t v);
double VectorNormalizeD(vec3_t v);

#endif
