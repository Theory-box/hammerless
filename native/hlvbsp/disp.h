/* Displacements: the .vmf data and the BSP records. */
#ifndef DISP_H
#define DISP_H
#include "hlvbsp.h"

#define MAX_DISPVERTS 289       /* (2^4 + 1)^2 */
#define MAX_DISPTRIS 512

typedef struct mapdisp_s {
    int power, flags, mintess;
    float smooth;
    vec3_t startpos;
    float normals[MAX_DISPVERTS][3], dists[MAX_DISPVERTS], offsets[MAX_DISPVERTS][3], alphas[MAX_DISPVERTS];
    unsigned short tritags[MAX_DISPTRIS];
    face_t face;
    int entitynum, contents, brushsideid;
} mapdisp_t;

#pragma pack(push, 1)
typedef struct { vec3_t vec; float dist, alpha; } dispvert_t;
#pragma pack(pop)

typedef struct { unsigned short neighbor; unsigned char orientation, span, nbspan; } dsubneighbor_t;
typedef struct { dsubneighbor_t sub[2]; } dedgeneighbor_t;
typedef struct { unsigned short n[4]; unsigned char count; } dcornerneighbors_t;
typedef struct {
    vec3_t startpos;
    int vert_start, tri_start, power, mintess;
    float smoothing;
    int contents;
    unsigned short mapface;
    int lmalpha_start, lmsample_start;
    dedgeneighbor_t edge[4];
    dcornerneighbors_t corner[4];
    uint32_t allowed[10];
} ddispinfo_t;

extern int nummapdisps;
extern mapdisp_t *mapdisps;
extern ddispinfo_t *g_dispinfo;
extern dispvert_t *g_dispverts;
extern int g_numdispverts;
extern unsigned short *g_disptris;
extern int g_numdisptris;
extern unsigned char *g_lmsamples;
extern int g_numlmsamples;

mapdisp_t *NewMapDisp(void);
void ParseDispRow(const char *key, const char *value, float *out, int cols, int per);
void ParseDispTriTags(const char *key, const char *value, mapdisp_t *md);
void EmitInitialDispInfos(void);
void EmitDispLMAlphaAndNeighbors(void);
void DispBounds(int i, vec3_t mins, vec3_t maxs);
int AppendTexinfo(const texinfo_t *t);
void EmitDispFaceVertexes(face_t *f);

#endif
