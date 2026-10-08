/* Faces' neighbours and smoothed vertex normals (vrad's PairEdges / SaveVertexNormals).
 *
 * Each face corner's normal is the face normal plus the normals of the faces sharing that vertex that it
 * smooths with: all of them for a displacement; otherwise faces in a common smoothing group (none of
 * them a hard edge), or, when neither face has smoothing groups, faces within 45 degrees. The normals
 * go into lump 30 once each (in order of first use) and lump 31 indexes them per face corner. */
#include "hlvrad.h"

typedef struct {
    int numneighbors, *neighbor;
    vec3_t facenormal;
    vec3_t *normal;              /* per corner */
    int bHasDisp;
} faceneighbor_t;

faceneighbor_t *faceneighbor;
float smoothing_threshold = 0.7071067f;        /* cos(45 degrees) */
#define SMOOTHING_GROUP_HARD_EDGE 0x80000000u

int EdgeVertex(const dface_t *f, int edge) {
    if (edge < 0) edge += f->numedges;
    else if (edge >= f->numedges) edge = edge % f->numedges;
    int k = dsurfedges[f->firstedge + edge];
    return k < 0 ? dedges[-k].v[1] : dedges[k].v[0];
}

void PairEdges(void) {
    int *vertexref = xalloc(sizeof(int) * (numvertexes + 1));
    int **vertexface = xalloc(sizeof(int *) * (numvertexes + 1));
    faceneighbor = xalloc(sizeof(faceneighbor_t) * (numfaces + 1));
    for (int i = 0; i < numfaces; i++)
        for (int j = 0; j < g_pFaces[i].numedges; j++) vertexref[EdgeVertex(&g_pFaces[i], j)]++;
    for (int i = 0; i < numvertexes; i++) {
        vertexface[i] = xalloc(sizeof(int) * (vertexref[i] + 1));
        vertexref[i] = 0;
    }
    /* every face using each vertex (once) */
    for (int i = 0; i < numfaces; i++) {
        for (int j = 0; j < g_pFaces[i].numedges; j++) {
            int n = EdgeVertex(&g_pFaces[i], j), k;
            for (k = 0; k < vertexref[n]; k++)
                if (vertexface[n][k] == i) break;
            if (k >= vertexref[n]) vertexface[n][vertexref[n]++] = i;
        }
    }
    for (int i = 0; i < numfaces; i++) {
        VectorCopy(dplanes[g_pFaces[i].planenum].normal, faceneighbor[i].facenormal);
        faceneighbor[i].bHasDisp = g_pFaces[i].dispinfo != -1;
    }
    int tmpneighbor[65];
    for (int i = 0; i < numfaces; i++) {
        const dface_t *f = &g_pFaces[i];
        faceneighbor_t *fn = &faceneighbor[i];
        int numneighbors = 0;
        fn->normal = xalloc(sizeof(vec3_t) * (f->numedges + 1));
        for (int j = 0; j < f->numedges; j++) {
            int n = EdgeVertex(f, j);
            for (int k = 0; k < vertexref[n]; k++) {
                int other = vertexface[n][k];
                if (other == i) continue;
                /* a face without a displacement doesn't smooth with displacements */
                if (!fn->bHasDisp && faceneighbor[other].bHasDisp) continue;
                const float *nn = faceneighbor[other].facenormal;
                double cos_angle = DotProduct(nn, fn->facenormal);
                if (fn->bHasDisp) {
                    VectorAdd(fn->normal[j], nn, fn->normal[j]);
                } else if (f->smoothingGroups == 0 && g_pFaces[other].smoothingGroups == 0) {
                    if (cos_angle >= smoothing_threshold) VectorAdd(fn->normal[j], nn, fn->normal[j]);
                    else continue;
                } else {
                    unsigned group = f->smoothingGroups & g_pFaces[other].smoothingGroups;
                    if (group & SMOOTHING_GROUP_HARD_EDGE) continue;
                    if (group) VectorAdd(fn->normal[j], nn, fn->normal[j]);
                    else continue;
                }
                int m;
                for (m = 0; m < numneighbors; m++)
                    if (tmpneighbor[m] == other) break;
                if (m >= numneighbors) {
                    if (numneighbors >= 64) Error("Stack overflow in neighbors");
                    tmpneighbor[numneighbors++] = other;
                }
            }
        }
        if (numneighbors) {
            fn->numneighbors = numneighbors;
            fn->neighbor = xalloc(sizeof(int) * numneighbors);
            memcpy(fn->neighbor, tmpneighbor, sizeof(int) * numneighbors);
        }
        for (int j = 0; j < f->numedges; j++) {
            VectorAdd(fn->normal[j], fn->facenormal, fn->normal[j]);
            VectorNormalize(fn->normal[j]);
        }
    }
    for (int i = 0; i < numvertexes; i++) free(vertexface[i]);
    free(vertexface);
    free(vertexref);
}

/* ------------------------------------------------------------------ the unique normals */
#define NHASH 65536
static int *nhash_head, *nhash_next;

static unsigned HashNormal(const float *v) {
    unsigned h = 2166136261u;
    for (int i = 0; i < 3; i++) {
        float x = v[i] == 0.0f ? 0.0f : v[i];          /* -0 equals 0 */
        unsigned bits;
        memcpy(&bits, &x, 4);
        h = (h ^ bits) * 16777619u;
    }
    return h & (NHASH - 1);
}

void SaveVertexNormals(void) {
    int total = 0;
    for (int i = 0; i < numfaces; i++) total += g_pFaces[i].numedges;
    unsigned short *indices = xalloc(sizeof(unsigned short) * (total + 1));
    float *normals = xalloc(sizeof(vec3_t) * (total + 1));
    int numnormals = 0, numindices = 0;
    nhash_head = xalloc(sizeof(int) * NHASH);
    nhash_next = xalloc(sizeof(int) * (total + 1));
    for (int i = 0; i < NHASH; i++) nhash_head[i] = -1;
    for (int i = 0; i < numfaces; i++) {
        for (int j = 0; j < g_pFaces[i].numedges; j++) {
            const float *v = faceneighbor[i].normal[j];
            unsigned h = HashNormal(v);
            int found = -1;
            for (int k = nhash_head[h]; k >= 0; k = nhash_next[k]) {
                const float *p = normals + 3 * k;
                if (p[0] == v[0] && p[1] == v[1] && p[2] == v[2]) {
                    found = k;
                    break;
                }
            }
            if (found < 0) {
                found = numnormals++;
                memcpy(normals + 3 * found, v, sizeof(vec3_t));
                nhash_next[found] = nhash_head[h];
                nhash_head[h] = found;
            }
            if (found > 65535) Error("too many vertex normals");
            indices[numindices++] = (unsigned short)found;
        }
    }
    free(nhash_head);
    free(nhash_next);
    SetLump(LUMP_VERTNORMALS, normals, numnormals * 12, 0);
    SetLump(LUMP_VERTNORMALINDICES, indices, numindices * 2, 0);
}
