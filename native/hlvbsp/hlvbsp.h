/* hlvbsp: Hammerless's map compiler, a drop-in for L4D2's vbsp.exe.
 *
 * The method is the one id Software published with Quake 2's map tools (qbsp3, GPL): brushes are carved
 * against each other (CSG), a BSP tree is built by choosing splitting planes from brush sides, portals
 * are made between the leaves, the outside is flooded from the entities and filled, and the faces that
 * remain are the portals between solid and empty space. Valve's file layout (BSP v21 as L4D2 writes it)
 * and the details that make the output match vbsp's were measured against vbsp's own output.
 *
 * Arithmetic: vbsp works in 32-bit floats; so do we (vec_t is float, no fused multiply-add).
 */
#ifndef HLVBSP_H
#define HLVBSP_H

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef float vec_t;
typedef vec_t vec3_t[3];

/* ------------------------------------------------------------------ limits & constants */
#define MAX_COORD_INTEGER 16384
#define MIN_COORD_INTEGER (-MAX_COORD_INTEGER)
#define COORD_EXTENT (2 * MAX_COORD_INTEGER)
#define MAX_POINTS_ON_WINDING 64
#define ON_EPSILON 0.1
#define MAX_MAP_PLANES 65536
#define MAX_MAP_BRUSHSIDES 655360
#define MAX_BRUSH_SIDES 128
#define MAXEDGES 32
#define PLANENUM_LEAF (-1)
#define TEXINFO_NODE (-1)
#define PLANE_HASHES 1024
#define RENDER_NORMAL_EPSILON 0.00001
#define RENDER_DIST_EPSILON 0.01f
#define BRUSH_CLIP_EPSILON 0.01f
#define BLOCKS_SIZE 1024
#define BLOCKS_SPACE (COORD_EXTENT / BLOCKS_SIZE)
#define BLOCKS_MIN (-(BLOCKS_SPACE / 2))
#define BLOCKS_MAX ((BLOCKS_SPACE / 2) - 1)
#define BLOCK_OFFSET ((BLOCKS_SPACE / 2) + 1)

#define PLANE_X 0
#define PLANE_Y 1
#define PLANE_Z 2
#define PLANE_ANYX 3
#define PLANE_ANYY 4
#define PLANE_ANYZ 5

#define SIDE_FRONT 0
#define SIDE_BACK 1
#define SIDE_ON 2
#define SIDE_CROSS (-2)

#define PSIDE_FRONT 1
#define PSIDE_BACK 2
#define PSIDE_BOTH (PSIDE_FRONT | PSIDE_BACK)
#define PSIDE_FACING 4

/* contents and surface flags (the game's bspflags.h) */
#define CONTENTS_SOLID 0x1
#define CONTENTS_WINDOW 0x2
#define CONTENTS_GRATE 0x8
#define CONTENTS_SLIME 0x10
#define CONTENTS_WATER 0x20
#define CONTENTS_BLOCKLOS 0x40
#define CONTENTS_OPAQUE 0x80
#define LAST_VISIBLE_CONTENTS 0x80
#define ALL_VISIBLE_CONTENTS (LAST_VISIBLE_CONTENTS | (LAST_VISIBLE_CONTENTS - 1))
#define CONTENTS_TESTFOGVOLUME 0x100
#define CONTENTS_MOVEABLE 0x4000
#define CONTENTS_AREAPORTAL 0x8000
#define CONTENTS_PLAYERCLIP 0x10000
#define CONTENTS_MONSTERCLIP 0x20000
#define CONTENTS_ORIGIN 0x1000000
#define CONTENTS_DETAIL 0x8000000
#define CONTENTS_TRANSLUCENT 0x10000000
#define CONTENTS_LADDER 0x20000000
#define MASK_WATER (CONTENTS_WATER | CONTENTS_MOVEABLE | CONTENTS_SLIME)
#define MASK_SPLITAREAPORTAL (CONTENTS_WATER | CONTENTS_SLIME)
#define TRANSPARENT_CONTENTS (CONTENTS_GRATE | CONTENTS_WINDOW)

#define SURF_LIGHT 0x0001
#define SURF_SKY2D 0x0002
#define SURF_SKY 0x0004
#define SURF_WARP 0x0008
#define SURF_TRANS 0x0010
#define SURF_NOPORTAL 0x0020
#define SURF_TRIGGER 0x0040
#define SURF_NODRAW 0x0080
#define SURF_HINT 0x0100
#define SURF_SKIP 0x0200
#define SURF_NOLIGHT 0x0400
#define SURF_BUMPLIGHT 0x0800
#define SURF_NOSHADOWS 0x1000
#define SURF_NODECALS 0x2000
#define SURF_NOCHOP 0x4000

/* ------------------------------------------------------------------ maths */
#define DotProduct(a, b) ((a)[0] * (b)[0] + (a)[1] * (b)[1] + (a)[2] * (b)[2])
#define VectorSubtract(a, b, c) ((c)[0] = (a)[0] - (b)[0], (c)[1] = (a)[1] - (b)[1], (c)[2] = (a)[2] - (b)[2])
#define VectorAdd(a, b, c) ((c)[0] = (a)[0] + (b)[0], (c)[1] = (a)[1] + (b)[1], (c)[2] = (a)[2] + (b)[2])
#define VectorCopy(a, b) ((b)[0] = (a)[0], (b)[1] = (a)[1], (b)[2] = (a)[2])
#define VectorClear(a) ((a)[0] = (a)[1] = (a)[2] = 0)
#define VectorScale(v, s, o) ((o)[0] = (v)[0] * (s), (o)[1] = (v)[1] * (s), (o)[2] = (v)[2] * (s))
#define VectorMA(v, s, b, o) ((o)[0] = (v)[0] + (b)[0] * (s), (o)[1] = (v)[1] + (b)[1] * (s), (o)[2] = (v)[2] + (b)[2] * (s))
#define VectorNegate(a, b) ((b)[0] = -(a)[0], (b)[1] = -(a)[1], (b)[2] = -(a)[2])
/* 0 - v, as vbsp's VectorSubtract(vec3_origin, v): zero components come out +0, not -0 */
#define VectorFromOrigin(a, b) ((b)[0] = 0 - (a)[0], (b)[1] = 0 - (a)[1], (b)[2] = 0 - (a)[2])

void CrossProduct(const vec3_t a, const vec3_t b, vec3_t c);
vec_t VectorNormalize(vec3_t v);
vec_t VectorLength(const vec3_t v);
void ClearBounds(vec3_t mins, vec3_t maxs);
void AddPointToBounds(const vec3_t p, vec3_t mins, vec3_t maxs);
vec_t RoundInt(vec_t v);

/* ------------------------------------------------------------------ windings */
typedef struct winding_s {
    int numpoints, maxpoints;
    vec3_t *p;
    struct winding_s *next;
} winding_t;

winding_t *AllocWinding(int points);
void FreeWinding(winding_t *w);
winding_t *CopyWinding(const winding_t *w);
winding_t *ReverseWinding(const winding_t *w);
winding_t *BaseWindingForPlane(const vec3_t normal, vec_t dist);
void ClipWindingEpsilon(const winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon, winding_t **front, winding_t **back);
void ClipWindingEpsilonOffset(winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon, winding_t **front, winding_t **back, const vec3_t offset);
void ClassifyWindingEpsilon(const winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon, winding_t **front, winding_t **back, winding_t **on);
void ClassifyWindingEpsilonOffset(winding_t *in, const vec3_t normal, vec_t dist, vec_t epsilon, winding_t **front, winding_t **back, winding_t **on, const vec3_t offset);
void ChopWindingInPlace(winding_t **w, const vec3_t normal, vec_t dist, vec_t epsilon);
void TranslateWinding(winding_t *w, const vec3_t offset);
vec_t WindingArea(const winding_t *w);
void WindingCenter(const winding_t *w, vec3_t center);
void WindingPlane(const winding_t *w, vec3_t normal, vec_t *dist);
void WindingBounds(const winding_t *w, vec3_t mins, vec3_t maxs);
int WindingIsTiny(const winding_t *w);
int WindingIsHuge(const winding_t *w);

/* ------------------------------------------------------------------ map data */
typedef struct plane_s {
    vec3_t normal;
    vec_t dist;
    int type;
    struct plane_s *hash_chain;
} plane_t;

typedef struct {
    vec3_t uaxis, vaxis;
    vec_t shift[2];
    vec_t scale[2];         /* texture world units per texel */
    vec_t lightmap_scale;   /* world units per luxel */
    int material;           /* index into materials[] */
    int flags;
} brush_texture_t;

struct mapdisp_s;
typedef struct side_s {
    int planenum, texinfo;
    winding_t *winding;
    struct side_s *original;
    int contents, surf;
    int visible, tested, bevel;
    struct side_s *next;     /* chain of original faces per plane */
    int origindex;
    int id;
    unsigned smoothing;
    int material;            /* the side's material (for messages and dispinfo) */
    int disp;                /* index into mapdisps + 1; 0 = not a displacement */
} side_t;

typedef struct {
    int entitynum, brushnum, id;
    int contents;
    vec3_t mins, maxs;
    int numsides;
    side_t *original_sides;
} mapbrush_t;

typedef struct epair_s {
    struct epair_s *next;
    char *key, *value;
} epair_t;

typedef struct {
    epair_t *epairs;
    vec3_t origin;
    int firstbrush, numbrushes;
    int areaportalnum;
    int portalareas[2];
    struct portal_s *portals_into_areas[2];
} entity_t;

/* a material as the Python side resolved it (the game's .vmt files live in VPKs) */
typedef struct {
    char name[256];
    int contents, flags;
    int width, height;
    float reflectivity[3];
    char surfaceprop[64], surfaceprop2[64];   /* $surfaceprop / $surfaceprop2 names ("" = none) */
    int found;
} material_t;

typedef struct {
    float vecs[2][4];         /* texels per world unit */
    float lmvecs[2][4];       /* luxels per world unit */
    int flags, texdata;
} texinfo_t;

typedef struct {
    float reflectivity[3];
    int name_id;
    int width, height, view_width, view_height;
} texdata_t;

/* ------------------------------------------------------------------ compile data */
typedef struct bspbrush_s {
    int id;
    struct bspbrush_s *next;
    vec3_t mins, maxs;
    int side, testside;
    mapbrush_t *original;
    int numsides;
    side_t sides[6];           /* variably sized */
} bspbrush_t;

struct node_s;
struct portal_s;

typedef struct face_s {
    int id;
    struct face_s *next, *merged, *split[2];
    struct portal_s *portal;
    int texinfo, dispinfo;
    struct node_s *fogleaf;
    int planenum, contents, outputnumber;
    winding_t *w;
    int numpoints, badstartvert;
    int vertexnums[MAXEDGES];
    side_t *originalface;
    int firstprim, numprims;
    unsigned smoothing;
} face_t;

typedef struct leafface_s {
    face_t *face;
    struct leafface_s *next;
} leafface_t;

typedef struct node_s {
    int id, planenum;
    struct node_s *parent;
    vec3_t mins, maxs;
    bspbrush_t *volume;
    side_t *side;
    struct node_s *children[2];
    face_t *faces;
    bspbrush_t *brushlist;
    leafface_t *leaffaces;
    int contents, occupied;
    entity_t *occupant;
    int cluster, area;
    struct portal_s *portals;
    int diskid;
} node_t;

typedef struct portal_s {
    int id;
    plane_t plane;
    node_t *onnode;
    node_t *nodes[2];
    struct portal_s *next[2];
    winding_t *winding;
    int sidefound;
    side_t *side;
    face_t *face[2];
} portal_t;

typedef struct {
    node_t *headnode;
    node_t outside_node;
    vec3_t mins, maxs;
} tree_t;

/* ------------------------------------------------------------------ globals */
extern plane_t mapplanes[MAX_MAP_PLANES];
extern int nummapplanes;
extern mapbrush_t *mapbrushes;
extern int nummapbrushes;
extern side_t *brushsides;
extern brush_texture_t *side_textures;
extern int nummapbrushsides;
extern entity_t *entities;
extern int num_entities;
extern vec3_t map_mins, map_maxs;
extern material_t *materials;
extern int nummaterials;
extern texinfo_t *texinfos;
extern int numtexinfo;
extern texdata_t *texdatas;
extern int numtexdata;
extern int entity_num;
extern int map_revision;
extern int verbose;
extern int g_cliptexinfo;
extern int c_areaportals;

/* ------------------------------------------------------------------ functions */
void Error(const char *fmt, ...);
void Warning(const char *fmt, ...);
void Msg(const char *fmt, ...);
void *xalloc(size_t n);
char *copystring(const char *s);

/* map.c */
int FindFloatPlane(vec3_t normal, vec_t dist);
int PlaneTypeForNormal(const vec3_t normal);
int PlaneEqual(const plane_t *p, const vec3_t normal, vec_t dist, float nep, float dep);
void LoadMapFile(const char *path);
void LoadMaterials(const char *path);
const char *ValueForKey(const entity_t *e, const char *key);
void SetKeyValue(entity_t *e, const char *key, const char *value);
void GetVectorForKey(const entity_t *e, const char *key, vec3_t v);
int FindMaterial(const char *name);
int TexinfoForBrushTexture(plane_t *plane, brush_texture_t *bt, const vec3_t origin);
int FindOrCreateTexData(int material);
int TexDataString(const char *s);
extern char *texdata_strings;
extern int texdata_strings_len;
extern int *texdata_string_table;
extern int numtexdata_strings;

/* brush.c */
bspbrush_t *AllocBrush(int numsides);
void FreeBrush(bspbrush_t *b);
void FreeBrushList(bspbrush_t *b);
bspbrush_t *CopyBrush(const bspbrush_t *b);
int CountBrushList(bspbrush_t *b);
void BoundBrush(bspbrush_t *b);
void CreateBrushWindings(bspbrush_t *b);
bspbrush_t *BrushFromBounds(const vec3_t mins, const vec3_t maxs);
vec_t BrushVolume(bspbrush_t *b);
void SplitBrush(bspbrush_t *brush, int planenum, bspbrush_t **front, bspbrush_t **back);
int BoxOnPlaneSide(const vec3_t mins, const vec3_t maxs, const plane_t *plane);
node_t *AllocNode(void);
tree_t *AllocTree(void);
tree_t *BrushBSP(bspbrush_t *list, const vec3_t mins, const vec3_t maxs);
void FreeTree(tree_t *tree);
void FreeTreePortals_r(node_t *node);
void PruneNodes(node_t *node);
void RemoveAreaPortalBrushes_R(node_t *node);

/* csg.c */
bspbrush_t *MakeBspBrushList(int start, int end, const vec3_t mins, const vec3_t maxs, int detail_screen);
bspbrush_t *ChopBrushes(bspbrush_t *head);
bspbrush_t *IntersectBrush(bspbrush_t *a, bspbrush_t *b);
void FixupAreaportalWaterBrushes(bspbrush_t *list);
#define FULL_DETAIL 0
#define ONLY_DETAIL 1
#define NO_DETAIL 2

/* portals.c */
void MakeHeadnodePortals(tree_t *tree);
void MakeNodePortal(node_t *node);
void SplitNodePortals(node_t *node);
void MakeTreePortals(tree_t *tree);
int FloodEntities(tree_t *tree);
void FillOutside(node_t *headnode);
void FloodAreas(tree_t *tree);
void MarkVisibleSides(tree_t *tree, int start, int end, int detail_screen);
int VisibleContents(int contents);
int Portal_VisFlood(portal_t *p);
void EmitAreaPortals(node_t *headnode);
void FreePortal(portal_t *p);
extern int c_areas;

/* faces.c */
void MakeFaces(node_t *node);
face_t *FixTjuncs(node_t *headnode, face_t *leaffaces);
face_t *AllocFace(void);
face_t *NewFaceFromFace(face_t *f);
void FreeFace(face_t *f);
void FreeFaceList(face_t *f);
void MergeFaceList(face_t **list);
void SubdivideFaceList(face_t **list);
int GetVertexnum(const vec3_t v);
int GetEdge2(int v1, int v2, face_t *f);
int AddEdge(int v1, int v2, face_t *f);
void ResetEdgeLists(void);
extern int firstmodeledge;
extern face_t **edgefaces[2];

/* detail.c */
face_t *MergeDetailTree(tree_t *tree, int start, int end);

/* write.c */
void BeginBSPFile(void);
void BeginModel(void);
void EndModel(void);
void WriteBSP(node_t *headnode, face_t *leaffaces);
void EndBSPFile(const char *path);
void SetModelNumbers(void);
void SetLightStyles(void);
void WritePortalFile(tree_t *tree, const char *path);
void ComputeBoundsNoSkybox(void);

/* the BSP's arrays */
typedef struct { vec3_t point; } dvertex_t;
typedef struct { unsigned short v[2]; } dedge_t;
typedef struct {
    int planenum, children[2];
    short mins[3], maxs[3];
    unsigned short firstface, numfaces;
    short area, pad;
} dnode_t;
typedef struct {
    int contents;
    short cluster, area_flags;
    short mins[3], maxs[3];
    unsigned short firstleafface, numleaffaces, firstleafbrush, numleafbrushes;
    short leafwaterdata, pad;
} dleaf_t;
typedef struct {
    unsigned short planenum;
    unsigned char side, onnode;
    int firstedge;
    short numedges, texinfo, dispinfo, fogvolume;
    unsigned char styles[4];
    int lightofs;
    float area;
    int lm_mins[2], lm_size[2];
    int origface;
    unsigned short numprims, firstprim;
    unsigned smoothing;
} dface_t;
typedef struct { vec3_t mins, maxs, origin; int headnode, firstface, numfaces; } dmodel_t;
typedef struct { int firstside, numsides, contents; } dbrush_t;
typedef struct { unsigned short planenum; short texinfo, dispinfo; unsigned char bevel, thin; } dbrushside_t;
typedef struct { int numareaportals, firstareaportal; } darea_t;
typedef struct { unsigned short key, otherarea, firstclip, numclip; int planenum; } dareaportal_t;

extern dvertex_t *dvertexes; extern int numvertexes;
extern dedge_t *dedges; extern int numedges;
extern int *dsurfedges; extern int numsurfedges;
extern dnode_t *dnodes; extern int numnodes;
extern dleaf_t *dleafs; extern int numleafs;
extern dface_t *dfaces; extern int numfaces;
extern dface_t *dorigfaces; extern int numorigfaces;
extern unsigned short *dfaceids;
extern unsigned short *dleaffaces; extern int numleaffaces;
extern unsigned short *dleafbrushes; extern int numleafbrushes;
extern dmodel_t dmodels[1024]; extern int nummodels;
extern dbrush_t *dbrushes; extern int numbrushes;
extern dbrushside_t *dbrushsides; extern int numbrushsides;
extern darea_t dareas[256]; extern int numareas;
extern dareaportal_t dareaportals[1024]; extern int numareaportals;
extern vec3_t clipportalverts[65536]; extern int numclipportalverts;

#endif
