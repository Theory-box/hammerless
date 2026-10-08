/* Bounced light (vrad's radiosity): patches, which patches see which, and light passed between them.
 *
 * Every lit face starts as one patch (its polygon); patches are cut in half along their widest axis until
 * they are at most a few luxels across (finer near the face's edges). Each face's direct light, measured at
 * its samples, is averaged onto its patches. Each leaf patch then gets a list of the patches it can see
 * (same PVS, in front, nothing in the way), each weighted by a form factor, the weights normalised. A bounce
 * passes every patch's emitted light (times its texture's reflectivity) along those lists; the light
 * received is emitted in the next bounce, until little is left. The luxels then add the bounced light
 * of nearby patches (see FinalLightFace). */
#include <float.h>
#include <xmmintrin.h>
#include "hlvrad.h"

#define ON_EPSILON 0.1f
#define PLANE_TEST_EPSILON 0.01
#define TRANSFER_EPSILON 0.0000001
#define MAX_PATCHES (4 * 65536)
#define SURF_NOCHOP 0x4000
#define PI 3.14159265358979323846

patch_t *patches;
int numpatches;
static int maxpatches;
int *face_patches;               /* per face: its first patch (the list runs children before parents) */
static int *face_parents;        /* per face: its first unsubdivided patch */
static int *cluster_children;    /* per cluster: its first leaf patch */
int g_numbounce = 100;
float maxchop = 4, minchop = 4;

typedef struct { vec3_t reflectivity; int name, width, height, view_width, view_height; } dtexdata_t;

static int NewPatch(void) {
    if (numpatches == maxpatches) {
        maxpatches = maxpatches ? maxpatches * 2 : 4096;
        patches = realloc(patches, sizeof(patch_t) * maxpatches);
    }
    return numpatches++;
}

/* ------------------------------------------------------------------ making patches */
static void MakePatchForFace(int fn, winding_t *w) {
    dface_t *f = &g_pFaces[fn];
    texinfo_t *tx = &texinfo[f->texinfo];
    float area = WindingArea(w);
    if (area <= 0) return;
    int i = NewPatch();
    patch_t *p = &patches[i];
    memset(p, 0, sizeof(*p));
    p->parent = p->child1 = p->child2 = p->next_parent = p->next_cluster_child = -1;
    p->needs_bump = tx->flags & SURF_BUMPLIGHT ? 1 : 0;
    p->next = face_patches[fn];
    face_patches[fn] = i;
    /* chop by the lightmap scale: finer lightmaps get smaller patches */
    float chopscale[2];
    for (int k = 0; k < 2; k++) {
        float s = 0, c = 0;
        for (int j = 0; j < 3; j++) {
            s += tx->textureVecsTexelsPerWorldUnits[k][j] * tx->textureVecsTexelsPerWorldUnits[k][j];
            c += tx->lightmapVecsLuxelsPerWorldUnits[k][j] * tx->lightmapVecsLuxelsPerWorldUnits[k][j];
        }
        p->scale[k] = (float)sqrt(s);
        chopscale[k] = (float)sqrt(c);
    }
    p->area = area;
    p->sky = tx->flags & SURF_SKY ? 1 : 0;
    p->luxscale = (chopscale[0] + chopscale[1]) / 2;
    p->chop = maxchop;
    p->winding = w;
    const dplane_t *pl = &dplanes[f->planenum];
    VectorCopy(pl->normal, p->plane_normal);
    p->plane_dist = pl->dist + DotProduct(face_offset[fn], pl->normal);     /* (moved with its brush entity) */
    if (!face_offset[fn][0] && !face_offset[fn][1] && !face_offset[fn][2]) p->plane_dist = pl->dist;
    p->face = fn;
    WindingCenter(w, p->origin);
    VectorCopy(pl->normal, p->normal);
    WindingBounds(w, p->face_mins, p->face_maxs);
    VectorCopy(p->face_mins, p->mins);
    VectorCopy(p->face_maxs, p->maxs);
    /* light emitted by the texture, its reflectivity (kept under 1 so bouncing settles) */
    const dtexdata_t *td = (const dtexdata_t *)lumps[LUMP_TEXDATA].data + tx->texdata;
    LightForTexture(TexDataName(tx->texdata), p->baselight);
    p->basearea = (float)(td->height * td->width);
    for (int k = 0; k < 3; k++) {
        p->reflectivity[k] = td->reflectivity[k] * 1.0f;
        if (p->reflectivity[k] > 0.99) p->reflectivity[k] = 0.99f;
    }
    if (p->baselight[0] || p->baselight[1] || p->baselight[2]) tx->flags |= SURF_LIGHT;
}

/* -- displacements (CVRADDispColl): a patch over the 4 corners, then triangles halved along the grid
 * (each child keeps grid vertices as corners) until no grid level is left, then at edge midpoints, until
 * their edges are under dispchop luxels */
float dispchop = 8.0f;

static float Length(const vec3_t v) { return sqrtf((v[0] * v[0] + v[2] * v[2]) + v[1] * v[1]); }   /* (L4D2's order) */
static void Cross(const vec3_t a, const vec3_t b, vec3_t c) {
    c[0] = a[1] * b[2] - a[2] * b[1];
    c[1] = a[2] * b[0] - a[0] * b[2];
    c[2] = a[0] * b[1] - a[1] * b[0];
}

static void DispPatchCommon(patch_t *p, const vec3_t *pts, int n, float area, const vec3_t normal) {
    p->scale[0] = p->scale[1] = 1.0f;
    p->chop = dispchop;
    p->sky = 0;
    p->winding = AllocWinding(n);
    p->winding->numpoints = n;
    vec3_t center = {0, 0, 0};
    for (int i = 0; i < n; i++) {
        VectorCopy(pts[i], p->winding->p[i]);
        VectorAdd(pts[i], center, center);
    }
    VectorScale(center, n == 4 ? 1.0f / 4.0f : 1.0f / 3.0f, p->origin);
    VectorCopy(normal, p->normal);
    VectorCopy(normal, p->plane_normal);
    p->plane_dist = DotProduct(normal, pts[0]);
    p->area = area;
    /* (the box starts at FLT_MIN, not -FLT_MAX: as vrad) */
    for (int k = 0; k < 3; k++) p->mins[k] = FLT_MAX, p->maxs[k] = FLT_MIN;
    for (int i = 0; i < n; i++)
        for (int k = 0; k < 3; k++) {
            p->mins[k] = p->mins[k] < pts[i][k] ? p->mins[k] : pts[i][k];
            p->maxs[k] = p->maxs[k] > pts[i][k] ? p->maxs[k] : pts[i][k];
        }
    p->needs_bump = texinfo[g_pFaces[p->face].texinfo].flags & SURF_BUMPLIGHT ? 1 : 0;
}

static void DispBaseLight(patch_t *p) {
    const texinfo_t *tx = &texinfo[g_pFaces[p->face].texinfo];
    const dtexdata_t *td = (const dtexdata_t *)lumps[LUMP_TEXDATA].data + tx->texdata;
    LightForTexture(TexDataName(tx->texdata), p->baselight);
    p->basearea = (float)(td->height * td->width);
    for (int k = 0; k < 3; k++) {
        p->reflectivity[k] = td->reflectivity[k] * 1.0f;
        if (p->reflectivity[k] > 0.99) p->reflectivity[k] = 0.99f;
    }
}

static void CreateDispParentPatch(const dispsurf_t *d) {
    int n = (1 << d->power) + 1;
    vec3_t pts[4];
    VectorCopy(d->verts[0], pts[0]);
    VectorCopy(d->verts[n * (n - 1)], pts[1]);
    VectorCopy(d->verts[n * n - 1], pts[2]);
    VectorCopy(d->verts[n - 1], pts[3]);
    int i = NewPatch();
    patch_t *p = &patches[i];
    memset(p, 0, sizeof(*p));
    p->next = face_patches[d->face];
    face_patches[d->face] = i;
    p->face = d->face;
    p->child1 = p->child2 = p->parent = p->next_cluster_child = p->next_parent = -1;
    vec3_t e0, e1, normal;
    VectorSubtract(pts[1], pts[0], e0);
    VectorSubtract(pts[3], pts[0], e1);
    Cross(e1, e0, normal);
    float area = VectorNormalize(normal);
    DispPatchCommon(p, pts, 4, area, normal);
    VectorCopy(p->mins, p->face_mins);
    VectorCopy(p->maxs, p->face_maxs);
    DispBaseLight(p);
}

static int InitDispPatch(int parent, int child, const vec3_t *pts, const int *indices) {
    int i = NewPatch();
    patch_t *p = &patches[i], *par = &patches[parent];
    memset(p, 0, sizeof(*p));
    p->next = -1;
    p->face = par->face;
    if (child == 0) par->child1 = i;
    else par->child2 = i;
    p->child1 = p->child2 = p->next_cluster_child = p->next_parent = -1;
    p->parent = parent;
    vec3_t e0, e1, normal;
    VectorSubtract(pts[1], pts[0], e0);
    VectorSubtract(pts[2], pts[0], e1);
    Cross(e1, e0, normal);
    float area = VectorNormalize(normal);
    area *= 0.5f;
    DispPatchCommon(p, pts, 3, area, normal);
    for (int k = 0; k < 3; k++) p->indices[k] = (short)indices[k];
    VectorCopy(par->face_mins, p->face_mins);
    VectorCopy(par->face_maxs, p->face_maxs);
    VectorCopy(par->baselight, p->baselight);
    p->basearea = par->basearea;
    VectorCopy(par->reflectivity, p->reflectivity);
    return i;
}

/* small enough (longest edge under dispchop luxels, or a sliver)? */
static int DispPatchSmall(const dispsurf_t *d, const vec3_t *e, int nedges, const vec3_t a, const vec3_t b, int tri) {
    float maxlen = d->sample_width, minedge = maxlen * dispchop;
    float longest = 0.0f;
    for (int k = 0; k < nedges; k++)
        if (longest < Length(e[k])) longest = Length(e[k]);
    if (longest < minedge) return 1;
    float minarea = (dispchop * maxlen) * (dispchop * maxlen);
    if (tri) minarea *= 0.5f;
    vec3_t n;
    Cross(a, b, n);
    double area = VectorNormalizeD(n);      /* (x87: compared unrounded) */
    if (tri) area *= 0.5f;
    return minarea > area;
}

static int LongestEdge(const vec3_t *e, int nedges) {
    float longest = 0.0f;
    int best = -1;
    for (int k = 0; k < nedges; k++)
        if (longest < Length(e[k])) longest = Length(e[k]), best = k;
    return best;
}

static void CreateDispChildPatchesSub(const dispsurf_t *d, int parent) {
    const winding_t *w = patches[parent].winding;
    if (w->numpoints != 3) return;
    vec3_t e[3];
    /* (L4D2: the same edges as above, though the cases below split p0-p1, p1-p2, p0-p2) */
    VectorSubtract(w->p[1], w->p[0], e[0]);
    VectorSubtract(w->p[2], w->p[0], e[1]);
    VectorSubtract(w->p[2], w->p[1], e[2]);
    if (DispPatchSmall(d, e, 3, e[1], e[0], 1)) return;
    int longest = LongestEdge(e, 3);
    vec3_t pts[2][3], mid;
    const float *p0 = w->p[0], *p1 = w->p[1], *p2 = w->p[2];
    if (longest == 0) {
        for (int k = 0; k < 3; k++) mid[k] = (p0[k] + p1[k]) * 0.5f;
        VectorCopy(p0, pts[0][0]), VectorCopy(mid, pts[0][1]), VectorCopy(p2, pts[0][2]);
        VectorCopy(mid, pts[1][0]), VectorCopy(p1, pts[1][1]), VectorCopy(p2, pts[1][2]);
    } else if (longest == 1) {
        for (int k = 0; k < 3; k++) mid[k] = (p1[k] + p2[k]) * 0.5f;
        VectorCopy(p0, pts[0][0]), VectorCopy(p1, pts[0][1]), VectorCopy(mid, pts[0][2]);
        VectorCopy(mid, pts[1][0]), VectorCopy(p2, pts[1][1]), VectorCopy(p0, pts[1][2]);
    } else {
        for (int k = 0; k < 3; k++) mid[k] = (p0[k] + p2[k]) * 0.5f;
        VectorCopy(p0, pts[0][0]), VectorCopy(p1, pts[0][1]), VectorCopy(mid, pts[0][2]);
        VectorCopy(mid, pts[1][0]), VectorCopy(p1, pts[1][1]), VectorCopy(p2, pts[1][2]);
    }
    static const int none[3] = {-1, -1, -1};
    int c0 = InitDispPatch(parent, 0, (const vec3_t *)pts[0], none);
    int c1 = InitDispPatch(parent, 1, (const vec3_t *)pts[1], none);
    CreateDispChildPatchesSub(d, c0);
    CreateDispChildPatchesSub(d, c1);
}

static void CreateDispChildPatches(const dispsurf_t *d, int parent, int level) {
    const winding_t *w = patches[parent].winding;
    int n = (1 << d->power) + 1;
    if (w->numpoints == 4) {
        vec3_t e[4];
        VectorSubtract(w->p[1], w->p[0], e[0]);
        VectorSubtract(w->p[2], w->p[1], e[1]);
        VectorSubtract(w->p[3], w->p[2], e[2]);
        VectorSubtract(w->p[3], w->p[0], e[3]);
        if (DispPatchSmall(d, e, 4, e[3], e[0], 0)) return;
        int idx[2][3] = {{n * n - 1, 0, n * (n - 1)}, {0, n * n - 1, n - 1}};
        int c[2];
        for (int t = 0; t < 2; t++) {
            vec3_t pts[3];
            for (int k = 0; k < 3; k++) VectorCopy(d->verts[idx[t][k]], pts[k]);
            c[t] = InitDispPatch(parent, t, (const vec3_t *)pts, idx[t]);
        }
        CreateDispChildPatches(d, c[0], 0);
        CreateDispChildPatches(d, c[1], 0);
        return;
    }
    if (w->numpoints != 3) return;
    vec3_t e[3];
    VectorSubtract(w->p[1], w->p[0], e[0]);
    VectorSubtract(w->p[2], w->p[0], e[1]);
    VectorSubtract(w->p[2], w->p[1], e[2]);
    if (DispPatchSmall(d, e, 3, e[1], e[0], 1)) return;
    if (level >= d->power * 2) {
        CreateDispChildPatchesSub(d, parent);
        return;
    }
    const int *pi = patches[parent].indices;
    int mid = (pi[1] + pi[0]) / 2;
    int idx[2][3] = {{pi[2], pi[0], mid}, {pi[1], pi[2], mid}};
    int c[2];
    for (int t = 0; t < 2; t++) {
        vec3_t pts[3];
        for (int k = 0; k < 3; k++) VectorCopy(d->verts[idx[t][k]], pts[k]);
        c[t] = InitDispPatch(parent, t, (const vec3_t *)pts, idx[t]);
    }
    CreateDispChildPatches(d, c[0], level + 1);
    CreateDispChildPatches(d, c[1], level + 1);
}

void MakePatches(void) {
    const dmodel_t *models = (const dmodel_t *)lumps[LUMP_MODELS].data;
    int nummodels = lumps[LUMP_MODELS].len / sizeof(dmodel_t);
    face_patches = xalloc(sizeof(int) * (numfaces + 1));
    face_parents = xalloc(sizeof(int) * (numfaces + 1));
    for (int i = 0; i < numfaces; i++) face_patches[i] = face_parents[i] = -1;
    for (int m = 0; m < nummodels; m++) {
        for (int j = 0; j < models[m].numfaces; j++) {
            int fn = models[m].firstface + j;
            if (g_pFaces[fn].dispinfo != -1) continue;
            winding_t *w = WindingFromFace(&g_pFaces[fn], face_offset[fn]);
            MakePatchForFace(fn, w);
        }
    }
    for (int i = 0; i < numdispsurfs; i++)
        if (dispsurfs[i].face >= 0) CreateDispParentPatch(&dispsurfs[i]);
}

/* ------------------------------------------------------------------ subdividing */
static int PreventSubdivision(const patch_t *p) {
    const texinfo_t *tx = &texinfo[g_pFaces[p->face].texinfo];
    if (tx->flags & SURF_NOCHOP) return 1;
    if (tx->flags & SURF_NOLIGHT && !(tx->flags & SURF_LIGHT)) return 1;
    return 0;
}

static void SubdividePatch(int i);

static int CreateChildPatch(int parent, winding_t *w, float area, const vec3_t center) {
    int c = NewPatch();
    patch_t *child = &patches[c], *par = &patches[parent];
    *child = *par;
    child->next = child->next_parent = child->next_cluster_child = child->child1 = child->child2 = -1;
    child->parent = parent;
    child->winding = w;
    child->area = area;
    VectorCopy(center, child->origin);
    GetPhongNormalScalar(child->face, child->origin, child->normal);
    WindingBounds(w, child->mins, child->maxs);
    if (child->baselight[0] || child->baselight[1] || child->baselight[2]) return c;
    /* near the face's edges, chop finer */
    vec3_t total;
    VectorSubtract(child->maxs, child->mins, total);
    VectorScale(total, child->luxscale, total);
    if (child->chop > minchop && total[0] < child->chop && total[1] < child->chop && total[2] < child->chop) {
        for (int k = 0; k < 3; k++) {
            if ((child->face_maxs[k] == child->maxs[k] || child->face_mins[k] == child->mins[k]) && total[k] > minchop) {
                child->chop = child->chop / 2 > minchop ? child->chop / 2 : minchop;
                break;
            }
        }
    }
    return c;
}

static void SubdividePatch(int i) {
    patch_t *p = &patches[i];
    if (p->sky) return;
    vec3_t total;
    VectorSubtract(p->maxs, p->mins, total);
    VectorScale(total, p->luxscale, total);
    float widest = -1;
    int axis = -1, split_it = 0;
    for (int k = 0; k < 3; k++) {
        if (total[k] > widest) axis = k, widest = total[k];
        if (total[k] >= p->chop && total[k] >= minchop) split_it = 1;
    }
    if (!split_it && axis != -1) {
        /* make it squarer */
        if (total[axis] > total[(axis + 1) % 3] * 2 && total[axis] > total[(axis + 2) % 3] * 2 && p->chop > minchop) {
            split_it = 1;
            p->chop = p->chop / 2 > minchop ? p->chop / 2 : minchop;
        }
    }
    if (!split_it) return;
    vec3_t split = {0, 0, 0};
    split[axis] = 1;
    float dist = (p->mins[axis] + p->maxs[axis]) * 0.5f;
    winding_t *o1, *o2;
    ClipWindingEpsilon(p->winding, split, dist, ON_EPSILON, &o1, &o2);
    vec3_t c1, c2;
    float a1 = o1 ? WindingAreaAndBalancePoint(o1, c1) : 0, a2 = o2 ? WindingAreaAndBalancePoint(o2, c2) : 0;
    if (a1 == 0 || a2 == 0) {
        Msg("zero area child patch\n");
        return;
    }
    int k1 = CreateChildPatch(i, o1, a1, c1);
    int k2 = CreateChildPatch(i, o2, a2, c2);
    patches[i].child1 = k1;
    patches[i].child2 = k2;
    SubdividePatch(k1);
    SubdividePatch(k2);
}

void SubdividePatches(void) {
    if (g_numbounce == 0) return;
    int before = numpatches;
    Msg("%i patches before subdivision\n", before);
    for (int i = 0; i < before; i++) {
        patches[i].next_parent = face_parents[patches[i].face];
        face_parents[patches[i].face] = i;
    }
    for (int i = 0; i < before; i++) {
        patches[i].parent = -1;
        if (PreventSubdivision(&patches[i])) continue;
        int di = g_pFaces[patches[i].face].dispinfo;
        if (di == -1) SubdividePatch(i);
        else CreateDispChildPatches(&dispsurfs[di], i, 0);
    }
    for (int i = 0; i < numfaces; i++) face_patches[i] = -1;
    for (int i = 0; i < numpatches; i++) {
        patches[i].next = face_patches[patches[i].face];
        face_patches[patches[i].face] = i;
    }
    /* each patch's cluster (from a corner when its centre is in solid) */
    for (int i = 0; i < numpatches; i++) {
        patch_t *p = &patches[i];
        p->cluster = ClusterFromPoint(p->origin);
        for (int j = 0; p->cluster == -1 && j < p->winding->numpoints; j++) p->cluster = ClusterFromPoint(p->winding->p[j]);
    }
    cluster_children = xalloc(sizeof(int) * (numclusters + 1));
    for (int i = 0; i < numclusters; i++) cluster_children[i] = -1;
    for (int n = 0; n < numpatches; n++) {
        int i = numpatches - n - 1;
        patch_t *p = &patches[i];
        if (p->child1 == -1 && p->cluster != -1) {
            p->next_cluster_child = cluster_children[p->cluster];
            cluster_children[p->cluster] = i;
        }
    }
    Msg("%i patches after subdivision\n", numpatches);
}

/* per cluster: the displacement faces with a patch in it (they aren't in the leaves' face lists) */
static int **cluster_disps, *ncluster_disps;

void AddDispsToClusterTable(void) {
    cluster_disps = xalloc(sizeof(int *) * (numclusters + 1));
    ncluster_disps = xalloc(sizeof(int) * (numclusters + 1));
    for (int f = 0; f < numfaces; f++) {
        if (g_pFaces[f].dispinfo == -1) continue;
        for (int i = face_patches[f]; i != -1; i = patches[i].next) {
            int c = patches[i].cluster;
            if (c == -1) continue;
            int k;
            for (k = 0; k < ncluster_disps[c]; k++)
                if (cluster_disps[c][k] == f) break;
            if (k < ncluster_disps[c]) continue;
            cluster_disps[c] = realloc(cluster_disps[c], sizeof(int) * (ncluster_disps[c] + 1));
            cluster_disps[c][ncluster_disps[c]++] = f;
        }
    }
}

/* ------------------------------------------------------------------ direct light onto patches */
/* a sample's direct light (style 0) onto the leaf patches whose box it touches */
void AddSampleToPatch(int facenum, const vec3_t pos, float area, const vec3_t light) {
    if (g_numbounce == 0) return;
    if ((light[0] + light[1] + light[2]) / 3 < 1) return;
    float radius = (float)(sqrt(area) / 2.0);
    for (int i = face_patches[facenum]; i != -1; i = patches[i].next) {
        patch_t *p = &patches[i];
        if (p->sky || p->child1 != -1) continue;
        vec3_t mins, maxs;
        WindingBounds(p->winding, mins, maxs);
        int k;
        for (k = 0; k < 3; k++)
            if (mins[k] > pos[k] + radius || maxs[k] < pos[k] - radius) break;
        if (k < 3) continue;
        p->samplearea += area;
        for (k = 0; k < 3; k++) p->samplelight[k] = p->samplelight[k] + area * light[k];
    }
}

/* after a face's samples: sums up to parents, averages, parents take their children's mean */
void FinishPatchLights(int facenum) {
    if (face_patches[facenum] == -1) return;
    for (int i = face_patches[facenum]; i != -1; i = patches[i].next) {
        patch_t *p = &patches[i];
        if (p->parent == -1) continue;
        patch_t *par = &patches[p->parent];
        par->samplearea += p->samplearea;
        VectorAdd(par->samplelight, p->samplelight, par->samplelight);
    }
    if (g_numbounce > 0) {
        for (int i = face_patches[facenum]; i != -1; i = patches[i].next) {
            patch_t *p = &patches[i];
            if (!p->samplearea) continue;
            float scale = (float)(1.0 / p->samplearea);
            vec3_t v;
            VectorScale(p->samplelight, scale, v);
            VectorAdd(p->totallight[0], v, p->totallight[0]);
            VectorAdd(p->directlight, v, p->directlight);
        }
    }
    for (int i = face_patches[facenum]; i != -1; i = patches[i].next) {
        patch_t *p = &patches[i];
        if (p->child1 == -1) continue;
        patch_t *c1 = &patches[p->child1], *c2 = &patches[p->child2];
        float s1 = c1->area / (c1->area + c2->area), s2 = c2->area / (c1->area + c2->area);
        for (int k = 0; k < 3; k++) p->totallight[0][k] = c1->totallight[0][k] * s1 + s2 * c2->totallight[0][k];
        VectorCopy(p->totallight[0], p->directlight);
    }
}

/* ------------------------------------------------------------------ transfers */
static float FormFactorPolyToDiff(const patch_t *poly, const patch_t *diff) {
    const winding_t *w = poly->winding;
    float ff = 0.0f;
    for (int i = 0; i < w->numpoints; i++) {
        int n = i < w->numpoints - 1 ? i + 1 : 0;
        vec3_t v1, v2, g;
        VectorSubtract(w->p[i], diff->origin, v1);
        VectorSubtract(w->p[n], diff->origin, v2);
        VectorNormalize(v1);
        VectorNormalize(v2);
        g[0] = v1[1] * v2[2] - v1[2] * v2[1];
        g[1] = v1[2] * v2[0] - v1[0] * v2[2];
        g[2] = v1[0] * v2[1] - v1[1] * v2[0];
        float sina = VectorNormalize(g);
        if (sina < -1.0f || sina > 1.0f) return 0.0f;
        float a = (float)asin(sina);
        VectorScale(g, a, g);
        ff += DotProduct(g, diff->normal);
    }
    return (float)((0.5 / (double)poly->area) * ff);
}

/* (L4D2's is x87: the dots and the unrounded length in double, rounded to float at the end) */
static float FormFactorDiffToDiff(const patch_t *d1, const patch_t *d2) {
    vec3_t delta;
    VectorSubtract(d1->origin, d2->origin, delta);
    double len = VectorNormalizeD(delta);
    double dot1 = ((double)delta[0] * d1->normal[0] + (double)delta[1] * d1->normal[1]) + (double)delta[2] * d1->normal[2];
    double dot2 = ((double)delta[0] * d2->normal[0] + (double)delta[1] * d2->normal[1]) + (double)delta[2] * d2->normal[2];
    return (float)-((dot2 * dot1) / (len * len));
}

typedef struct { int patch; float transfer; } transfer_t;

static void MakeTransfer(int i1, int i2, transfer_t *all) {
    patch_t *p1 = &patches[i1], *p2 = &patches[i2];
    if (texinfo[g_pFaces[p2->face].texinfo].flags & SURF_SKY) return;
    if (p1->numtransfers >= MAX_PATCHES) return;
    if (p2->area <= 0) return;
    float scale = FormFactorDiffToDiff(p2, p1);
    if (scale <= 0) return;
    vec3_t d;
    VectorSubtract(p1->origin, p2->origin, d);
    float threshold = (float)((PI * 0.04) * DotProduct(d, d));
    if (threshold < p2->area) {
        scale = FormFactorPolyToDiff(p2, p1);
        if (scale <= 0.0) return;
    }
    float trans = p2->area * scale;
    if (trans <= TRANSFER_EPSILON) return;
    all[p1->numtransfers].patch = i2;
    all[p1->numtransfers].transfer = trans;
    p1->numtransfers++;
}

/* a row's rays, binned by direction signs and traced 4 at a time (vrad's ray stream) */
typedef struct {
    int n[8];
    float o[8][3][4], d[8][3][4];
    int out[8][4];
} raystream_t;

/* (per thread: the patch being shot from's rays and their results) */
static __thread int ntests, *test_shooter, *test_receiver, *test_hit;
static __thread float *test_dist, *test_len;

static void FlushStream(raystream_t *s, int m) {
    float d[3][4], len[4];
    __m128 x = _mm_loadu_ps(s->d[m][0]), y = _mm_loadu_ps(s->d[m][1]), z = _mm_loadu_ps(s->d[m][2]);
    __m128 l = _mm_sqrt_ps(_mm_add_ps(_mm_add_ps(_mm_mul_ps(x, x), _mm_mul_ps(y, y)), _mm_mul_ps(z, z)));
    __m128 zero = _mm_cmpeq_ps(l, _mm_setzero_ps());
    __m128 a = _mm_or_ps(l, _mm_and_ps(_mm_set1_ps(FLT_EPSILON), zero));
    __m128 r = _mm_rcp_ps(a);
    r = _mm_sub_ps(_mm_add_ps(r, r), _mm_mul_ps(a, _mm_mul_ps(r, r)));
    _mm_storeu_ps(d[0], _mm_mul_ps(x, r));
    _mm_storeu_ps(d[1], _mm_mul_ps(y, r));
    _mm_storeu_ps(d[2], _mm_mul_ps(z, r));
    _mm_storeu_ps(len, l);
    int hit[4];
    float dist[4];
    RT_Trace4Mask((const float(*)[4])s->o[m], (const float(*)[4])d, len, m, hit, dist);
    for (int k = 0; k < 4; k++) {
        int t = s->out[m][k];
        test_len[t] = len[k];
        test_hit[t] = hit[k];
        test_dist[t] = dist[k];
    }
    s->n[m] = 0;
}

static void AddToStream(raystream_t *s, const vec3_t start, const vec3_t end, int test) {
    vec3_t d;
    VectorSubtract(end, start, d);
    int m = (d[0] < 0 ? 1 : 0) | (d[1] < 0 ? 2 : 0) | (d[2] < 0 ? 4 : 0);
    int pos = s->n[m];
    for (int c = 0; c < 3; c++) s->o[m][c][pos] = start[c], s->d[m][c][pos] = d[c];
    s->out[m][pos] = test;
    if (pos == 3) FlushStream(s, m);
    else s->n[m]++;
}

static void FinishStream(raystream_t *s) {
    for (int m = 0; m < 8; m++) {
        int cnt = s->n[m];
        if (!cnt) continue;
        for (int c = cnt; c < 4; c++) {
            for (int k = 0; k < 3; k++) s->o[m][k][c] = s->o[m][k][0], s->d[m][k][c] = s->d[m][k][0];
            s->out[m][c] = s->out[m][0];
        }
        FlushStream(s, m);
    }
}

static __thread raystream_t stream;

static void TestPatchToPatch(int i1, int i2) {
    patch_t *p = &patches[i1], *p2 = &patches[i2];
    if (p2->child1 != -1) {
        vec3_t t;
        VectorSubtract(p->origin, p2->origin, t);
        if (DotProduct(t, t) * 0.0625 < p2->area) {
            TestPatchToPatch(i1, p2->child1);
            TestPatchToPatch(i1, p2->child2);
            return;
        }
    }
    if (DotProduct(p2->origin, p->normal) > p->plane_dist + PLANE_TEST_EPSILON) {
        vec3_t a, b;
        VectorAdd(p->origin, p->normal, a);
        VectorAdd(p2->origin, p2->normal, b);
        test_shooter[ntests] = i1;
        test_receiver[ntests] = i2;
        AddToStream(&stream, a, b, ntests);
        ntests++;
    }
}

static void TestPatchToFace(int i, int facenum) {
    if (face_parents[facenum] == -1) return;
    patch_t *p = &patches[i], *p2 = &patches[face_parents[facenum]];
    if (!(DotProduct(p->origin, p2->normal) > p2->plane_dist + PLANE_TEST_EPSILON)) return;
    for (int j = face_parents[facenum]; j != -1; j = patches[j].next_parent) TestPatchToPatch(i, j);
}

static void MakeScales(int i, const transfer_t *all) {
    patch_t *p = &patches[i];
    if (!p->numtransfers) return;
    p->transfers = xalloc(sizeof(transfer_t) * p->numtransfers);
    float total = 0;
    for (int j = 0; j < p->numtransfers; j++) total += all[j].transfer;
    /* (the total should be pi; overlapping surfaces can make it more) */
    if (total > PI) total = 1.0f / total;
    else total = (float)(1.0f / PI);
    transfer_t *t = p->transfers;
    for (int j = 0; j < p->numtransfers; j++) t[j].transfer = all[j].transfer * total, t[j].patch = all[j].patch;
}

static int total_transfer, max_transfer;

/* each patch's transfers: the patches it sees (in clusters its cluster's PVS has), unblocked; the patches
 * in parallel, each writing only its own list */
static int **ms_cluster_leaves, *ms_nleaves, *ms_order, *ms_cluster;
static FILE *ms_dump;

static void ScalesWork(int item, int thread) {
    (void)thread;
    static __thread transfer_t *all;
    static __thread unsigned char *pvs, *face_tested, *disp_tested;
    static __thread int pvs_cluster;
    if (!all) {
        all = xalloc(sizeof(transfer_t) * MAX_PATCHES);
        test_shooter = xalloc(sizeof(int) * MAX_PATCHES);
        test_receiver = xalloc(sizeof(int) * MAX_PATCHES);
        test_hit = xalloc(sizeof(int) * MAX_PATCHES);
        test_dist = xalloc(sizeof(float) * MAX_PATCHES);
        test_len = xalloc(sizeof(float) * MAX_PATCHES);
        pvs = xalloc(VisRowBytes() + 1), face_tested = xalloc(numfaces + 1), disp_tested = xalloc(numfaces + 1);
        pvs_cluster = -1;
    }
    int i = ms_order[item], c = ms_cluster[item];
    if (c != pvs_cluster) GetClusterPVS(c, pvs), pvs_cluster = c;
    patch_t *p = &patches[i];
    memset(face_tested, 0, numfaces);
    memset(disp_tested, 0, numfaces);
    ntests = 0;
    for (int j = 0; j < numclusters; j++) {
        if (!(pvs[j >> 3] & (1 << (j & 7)))) continue;
        for (int li = 0; li < ms_nleaves[j]; li++) {
            const dleaf_t *leaf = &dleafs[ms_cluster_leaves[j][li]];
            for (int k = 0; k < leaf->numleaffaces; k++) {
                int l = dleaffaces[leaf->firstleafface + k];
                if (face_tested[l]) continue;
                face_tested[l] = 1;
                if (p->face == l) continue;
                TestPatchToFace(i, l);
            }
        }
        for (int k = 0; k < ncluster_disps[j]; k++) {
            int l = cluster_disps[j][k];
            if (disp_tested[l]) continue;
            disp_tested[l] = 1;
            if (p->face == l) continue;
            TestPatchToFace(i, l);
        }
    }
    FinishStream(&stream);
    p->numtransfers = 0;
    for (int t = 0; t < ntests; t++)
        if (test_hit[t] == -1 || test_dist[t] >= test_len[t]) MakeTransfer(test_shooter[t], test_receiver[t], all);
    if (ms_dump) fwrite(&i, 4, 1, ms_dump), fwrite(&p->numtransfers, 4, 1, ms_dump), fwrite(all, 8, p->numtransfers, ms_dump);
    MakeScales(i, all);
}

void MakeAllScales(void) {
    ms_cluster_leaves = xalloc(sizeof(int *) * (numclusters + 1)), ms_nleaves = xalloc(sizeof(int) * (numclusters + 1));
    for (int l = 0; l < numleafs; l++) {
        int c = dleafs[l].cluster;
        if (c < 0 || c >= numclusters) continue;
        ms_cluster_leaves[c] = realloc(ms_cluster_leaves[c], sizeof(int) * (ms_nleaves[c] + 1));
        ms_cluster_leaves[c][ms_nleaves[c]++] = l;
    }
    if (getenv("HLPATCHES")) {      /* (debugging: origin, normal, plane dist, area, face, cluster, children) */
        FILE *pf = fopen(getenv("HLPATCHES"), "wb");
        for (int i = 0; i < numpatches; i++) {
            patch_t *p = &patches[i];
            fwrite(p->origin, 12, 1, pf), fwrite(p->normal, 12, 1, pf), fwrite(&p->plane_dist, 4, 1, pf), fwrite(&p->area, 4, 1, pf);
            fwrite(&p->face, 4, 1, pf), fwrite(&p->cluster, 4, 1, pf), fwrite(&p->child1, 4, 1, pf), fwrite(&p->child2, 4, 1, pf);
        }
        fclose(pf);
    }
    /* the patches in vrad's order: by cluster, each cluster's leaf patches */
    ms_order = xalloc(sizeof(int) * (numpatches + 1)), ms_cluster = xalloc(sizeof(int) * (numpatches + 1));
    int n = 0;
    for (int c = 0; c < numclusters; c++)
        for (int i = cluster_children[c]; i != -1; i = patches[i].next_cluster_child) ms_order[n] = i, ms_cluster[n++] = c;
    ms_dump = getenv("HLTRANSFERS") ? fopen(getenv("HLTRANSFERS"), "wb") : NULL;   /* (debugging: the lists as MakeScales gets them) */
    int threads = g_numthreads;
    if (ms_dump) g_numthreads = 1;                 /* (the dump in order) */
    RunThreadsOn(n, ScalesWork);
    g_numthreads = threads;
    if (ms_dump) fclose(ms_dump), ms_dump = NULL;
    for (int k = 0; k < n; k++) {
        int t = patches[ms_order[k]].numtransfers;
        total_transfer += t;
        if (t > max_transfer) max_transfer = t;
    }
    Msg("transfers %d, max %d\n", total_transfer, max_transfer);
    for (int c = 0; c < numclusters; c++) free(ms_cluster_leaves[c]);
    free(ms_cluster_leaves), free(ms_nleaves), free(ms_order), free(ms_cluster);
}

/* ------------------------------------------------------------------ bouncing */
/* a displacement's texture axes for its bump normals: turned into the lightmap's axes when they differ */
void PreGetBumpNormalsForDisp(const texinfo_t *tx, vec3_t u, vec3_t v, vec3_t normal) {
    vec3_t tu, tv, lu, lv;
    for (int k = 0; k < 3; k++) {
        tu[k] = tx->textureVecsTexelsPerWorldUnits[0][k], tv[k] = tx->textureVecsTexelsPerWorldUnits[1][k];
        lu[k] = tx->lightmapVecsLuxelsPerWorldUnits[0][k], lv[k] = tx->lightmapVecsLuxelsPerWorldUnits[1][k];
    }
    VectorNormalize(tu);
    VectorNormalize(tv);
    VectorNormalize(lu);
    VectorNormalize(lv);
    if (fabs(DotProduct(tu, lu)) < 0.999f || fabs(DotProduct(tv, lv)) < 0.999f) {
        /* columns: (light u, light v, normal) times (tex u, tex v, normal) */
        float a[3][3], b[3][3], m[3][3];
        for (int k = 0; k < 3; k++) {
            a[k][0] = lu[k], a[k][1] = lv[k], a[k][2] = normal[k];
            b[k][0] = tu[k], b[k][1] = tv[k], b[k][2] = normal[k];
        }
        for (int r = 0; r < 3; r++)
            for (int c = 0; c < 3; c++) m[r][c] = a[r][0] * b[0][c] + a[r][1] * b[1][c] + a[r][2] * b[2][c];
        for (int k = 0; k < 3; k++) u[k] = m[k][0], v[k] = m[k][1], normal[k] = m[k][2];
        return;
    }
    VectorCopy(tu, u);
    VectorCopy(tv, v);
}

static vec3_t *emitlight;
static vec3_t (*addlight)[4];

static void GatherLight(int j) {
    patch_t *p = &patches[j];
    const transfer_t *t = p->transfers;
    if (p->needs_bump) {
        vec3_t normals[4], sum[4] = {{0}};
        const texinfo_t *tx = &texinfo[g_pFaces[p->face].texinfo];
        if (g_pFaces[p->face].dispinfo != -1) {
            vec3_t u, v;
            VectorCopy(p->normal, normals[0]);
            PreGetBumpNormalsForDisp(tx, u, v, normals[0]);
            GetBumpNormals(u, v, normals[0], normals[0], &normals[1]);
        } else {
            GetPhongNormalScalar(p->face, p->origin, normals[0]);
            GetBumpNormals(tx->textureVecsTexelsPerWorldUnits[0], tx->textureVecsTexelsPerWorldUnits[1], p->normal, normals[0],
                           &normals[1]);
        }
        VectorCopy(p->normal, normals[0]);          /* (the base lightmap uses the flat normal) */
        for (int k = 0; k < p->numtransfers; k++, t++) {
            const patch_t *p2 = &patches[t->patch];
            vec3_t delta, v;
            VectorSubtract(p2->origin, p->origin, delta);
            VectorNormalize(delta);
            for (int i = 0; i < 3; i++) v[i] = emitlight[t->patch][i] * p2->reflectivity[i];
            float scale = 1.0f / DotProduct(delta, p->normal);
            VectorScale(v, t->transfer * scale, v);
            for (int i = 0; i < 4; i++) {
                float dot = DotProduct(delta, normals[i]);
                if (dot <= 0) continue;
                for (int c = 0; c < 3; c++) sum[i][c] += v[c] * dot;
            }
        }
        for (int i = 0; i < 4; i++) VectorCopy(sum[i], addlight[j][i]);
    } else {
        vec3_t sum = {0, 0, 0};
        for (int k = 0; k < p->numtransfers; k++, t++) {
            vec3_t v;
            for (int i = 0; i < 3; i++) v[i] = emitlight[t->patch][i] * patches[t->patch].reflectivity[i];
            VectorScale(v, t->transfer, v);
            VectorAdd(sum, v, sum);
        }
        VectorCopy(sum, addlight[j][0]);
    }
}

static void CollectLight(vec3_t total) {
    VectorClear(total);
    for (int i = numpatches - 1; i >= 0; i--) {
        patch_t *p = &patches[i];
        int nc = p->needs_bump ? 4 : 1;
        if (p->sky) {
            VectorClear(emitlight[i]);
        } else if (p->child1 == -1) {
            for (int j = 0; j < nc; j++) VectorAdd(p->totallight[j], addlight[i][j], p->totallight[j]);
            VectorCopy(addlight[i][0], emitlight[i]);
            VectorAdd(total, emitlight[i], total);
        } else {
            patch_t *c1 = &patches[p->child1], *c2 = &patches[p->child2];
            float s1 = c1->area / (c1->area + c2->area), s2 = c2->area / (c1->area + c2->area);
            for (int j = 0; j < nc; j++)
                for (int k = 0; k < 3; k++) p->totallight[j][k] = c1->totallight[j][k] * s1 + s2 * c2->totallight[j][k];
            for (int k = 0; k < 3; k++) emitlight[i][k] = emitlight[p->child1][k] * s1 + s2 * emitlight[p->child2][k];
        }
        memset(addlight[i], 0, sizeof(addlight[i]));
    }
}

void BounceLight(void) {
    emitlight = xalloc(sizeof(vec3_t) * (numpatches + 1));
    addlight = xalloc(sizeof(*addlight) * (numpatches + 1));
    for (int i = 0; i < numpatches; i++) {
        VectorCopy(patches[i].totallight[0], emitlight[i]);
        VectorClear(patches[i].totallight[0]);
    }
    for (int b = 0; g_numbounce > 0; b++) {
        for (int j = 0; j < numpatches; j++) GatherLight(j);
        vec3_t added;
        CollectLight(added);
        Msg("\tBounce #%i added RGB(%.0f, %.0f, %.0f)\n", b + 1, added[0], added[1], added[2]);
        if (b + 1 == g_numbounce || (added[0] < 1.0 && added[1] < 1.0 && added[2] < 1.0)) break;
    }
}
