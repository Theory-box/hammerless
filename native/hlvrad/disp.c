/* Displacements (terrain) as vrad builds them from the BSP (CCoreDispInfo / CVRADDispColl).
 *
 * The base quad is the face's 4 corners, turned to start at the corner nearest the stored start position;
 * a vertex sits on the flat quad (interpolated row by row) moved by its stored direction times distance.
 * The full-detail triangles (each quad cut corner to corner, the diagonal alternating with the vertex
 * index) block light in the ray tracer. */
#include "hlvrad.h"

#pragma pack(push, 1)
typedef struct { vec3_t vec; float dist, alpha; } dispvert_t;
#pragma pack(pop)

typedef struct {
    vec3_t startpos;
    int vert_start, tri_start, power, mintess;
    float smoothing;
    int contents;
    unsigned short mapface;
    int lmalpha_start, lmsample_start;
    unsigned char neighbours[88];      /* (edge and corner neighbours: unused here) */
    uint32_t allowed[10];
} ddispinfo_t;

dispsurf_t *dispsurfs;
int numdispsurfs;

static float LengthSqr(const vec3_t v) { return v[0] * v[0] + v[1] * v[1] + v[2] * v[2]; }

static void GenerateDispSurf(dispsurf_t *d, const dispvert_t *dv) {
    int ps = (1 << d->power) + 1;
    float ooint = 1.0f / (float)(ps - 1);
    vec3_t e0, e1;
    VectorSubtract(d->points[1], d->points[0], e0);
    VectorScale(e0, ooint, e0);
    VectorSubtract(d->points[2], d->points[3], e1);
    VectorScale(e1, ooint, e1);
    for (int i = 0; i < ps; i++) {
        vec3_t end0, end1, seg, segint;
        VectorScale(e0, (float)i, end0);
        VectorAdd(end0, d->points[0], end0);
        VectorScale(e1, (float)i, end1);
        VectorAdd(end1, d->points[3], end1);
        VectorSubtract(end1, end0, seg);
        VectorScale(seg, ooint, segint);
        for (int j = 0; j < ps; j++) {
            int ndx = i * ps + j;
            vec3_t t;
            VectorScale(segint, (float)j, t);
            VectorAdd(end0, t, d->flat[ndx]);
            VectorCopy(d->flat[ndx], d->verts[ndx]);
            d->verts[ndx][0] += 0.0f;          /* (+ the subdivision position: zero) */
            d->verts[ndx][1] += 0.0f;
            d->verts[ndx][2] += 0.0f;
            VectorScale(dv[ndx].vec, dv[ndx].dist, t);
            VectorAdd(d->verts[ndx], t, d->verts[ndx]);
            d->alpha[ndx] = dv[ndx].alpha;
        }
    }
}

void LoadDisplacements(void) {
    const ddispinfo_t *di = (const ddispinfo_t *)lumps[LUMP_DISPINFO].data;
    numdispsurfs = lumps[LUMP_DISPINFO].len / (int)sizeof(ddispinfo_t);
    if (lumps[LUMP_DISPINFO].len % (int)sizeof(ddispinfo_t)) Error("dispinfo lump has an odd size");
    const dispvert_t *verts = (const dispvert_t *)lumps[LUMP_DISP_VERTS].data;
    dispsurfs = xalloc(sizeof(dispsurf_t) * (numdispsurfs + 1));
    for (int i = 0; i < numdispsurfs; i++) dispsurfs[i].face = -1;
    for (int f = 0; f < numfaces; f++) {
        const dface_t *face = &g_pFaces[f];
        if (face->dispinfo == -1) continue;
        dispsurf_t *d = &dispsurfs[face->dispinfo];
        const ddispinfo_t *info = &di[face->dispinfo];
        d->face = f;
        d->power = info->power;
        d->contents = info->contents;
        vec3_t pts[4];
        for (int k = 0; k < 4; k++) VectorCopy(dvertexes[EdgeVertex(face, k)].point, pts[k]);
        int start = -1;
        float best = 999999999.0f;
        for (int k = 0; k < 4; k++) {
            vec3_t seg;
            VectorSubtract(info->startpos, pts[k], seg);
            float d2 = LengthSqr(seg);
            if (d2 < best) {
                best = d2;
                start = k;
            }
        }
        for (int k = 0; k < 4; k++) VectorCopy(pts[(k + start) % 4], d->points[k]);
        int n = ((1 << d->power) + 1) * ((1 << d->power) + 1);
        d->numverts = n;
        d->verts = xalloc(sizeof(vec3_t) * n);
        d->flat = xalloc(sizeof(vec3_t) * n);
        d->alpha = xalloc(sizeof(float) * n);
        GenerateDispSurf(d, verts + info->vert_start);
    }
}

/* the full-detail triangles, as CCoreDispInfo's collision surface lists them */
int DispTriangles(const dispsurf_t *d, unsigned short (*tris)[3]) {
    int w = (1 << d->power) + 1, n = 0;
    for (int v = 0; v < w - 1; v++) {
        for (int u = 0; u < w - 1; u++) {
            int ndx = v * w + u;
            if (ndx % 2 == 1) {        /* top left to bottom right */
                tris[n][0] = ndx, tris[n][1] = ndx + w, tris[n][2] = ndx + 1, n++;
                tris[n][0] = ndx + 1, tris[n][1] = ndx + w, tris[n][2] = ndx + w + 1, n++;
            } else {                   /* bottom left to top right */
                tris[n][0] = ndx, tris[n][1] = ndx + w, tris[n][2] = ndx + w + 1, n++;
                tris[n][0] = ndx, tris[n][1] = ndx + w + 1, tris[n][2] = ndx + 1, n++;
            }
        }
    }
    return n;
}

void AddDispsForRayTrace(void) {
    unsigned short tris[512][3];
    for (int i = 0; i < numdispsurfs; i++) {
        const dispsurf_t *d = &dispsurfs[i];
        if (d->face < 0 || !(d->contents & MASK_OPAQUE)) continue;
        int n = DispTriangles(d, tris);
        for (int t = 0; t < n; t++)
            RT_AddTriangle(TRACE_ID_OPAQUE, d->verts[tris[t][0]], d->verts[tris[t][1]], d->verts[tris[t][2]]);
    }
}
