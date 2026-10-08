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
    /* light emitted by the texture (TODO: texlights), its reflectivity (kept under 1 so bouncing settles) */
    const dtexdata_t *td = (const dtexdata_t *)lumps[LUMP_TEXDATA].data + tx->texdata;
    p->basearea = (float)(td->height * td->width);
    for (int k = 0; k < 3; k++) {
        p->reflectivity[k] = td->reflectivity[k] * 1.0f;
        if (p->reflectivity[k] > 0.99) p->reflectivity[k] = 0.99f;
    }
    if (p->baselight[0] || p->baselight[1] || p->baselight[2]) tx->flags |= SURF_LIGHT;
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
    /* TODO: displacement patches */
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
    GetPhongNormal(child->face, child->origin, child->normal);
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
        if (g_pFaces[patches[i].face].dispinfo == -1) SubdividePatch(i);
        /* TODO: displacement patches */
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
    ff *= 0.5f / poly->area;
    return ff;
}

static float FormFactorDiffToDiff(const patch_t *d1, const patch_t *d2) {
    vec3_t delta;
    VectorSubtract(d1->origin, d2->origin, delta);
    float len = VectorNormalize(delta);
    return -DotProduct(delta, d1->normal) * DotProduct(delta, d2->normal) / (len * len);
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
    double threshold = (PI * 0.04) * DotProduct(d, d);
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

static int ntests, *test_shooter, *test_receiver, *test_hit;
static float *test_dist, *test_len;

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

static raystream_t stream;

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

void MakeAllScales(void) {
    int **cluster_leaves = xalloc(sizeof(int *) * (numclusters + 1)), *nleaves = xalloc(sizeof(int) * (numclusters + 1));
    for (int l = 0; l < numleafs; l++) {
        int c = dleafs[l].cluster;
        if (c < 0 || c >= numclusters) continue;
        cluster_leaves[c] = realloc(cluster_leaves[c], sizeof(int) * (nleaves[c] + 1));
        cluster_leaves[c][nleaves[c]++] = l;
    }
    transfer_t *all = xalloc(sizeof(transfer_t) * MAX_PATCHES);
    test_shooter = xalloc(sizeof(int) * MAX_PATCHES);
    test_receiver = xalloc(sizeof(int) * MAX_PATCHES);
    test_hit = xalloc(sizeof(int) * MAX_PATCHES);
    test_dist = xalloc(sizeof(float) * MAX_PATCHES);
    test_len = xalloc(sizeof(float) * MAX_PATCHES);
    unsigned char *pvs = xalloc(VisRowBytes() + 1), *face_tested = xalloc(numfaces + 1);
    for (int c = 0; c < numclusters; c++) {
        GetClusterPVS(c, pvs);
        for (int i = cluster_children[c]; i != -1; i = patches[i].next_cluster_child) {
            patch_t *p = &patches[i];
            memset(face_tested, 0, numfaces);
            ntests = 0;
            for (int j = 0; j < numclusters; j++) {
                if (!(pvs[j >> 3] & (1 << (j & 7)))) continue;
                for (int li = 0; li < nleaves[j]; li++) {
                    const dleaf_t *leaf = &dleafs[cluster_leaves[j][li]];
                    for (int k = 0; k < leaf->numleaffaces; k++) {
                        int l = dleaffaces[leaf->firstleafface + k];
                        if (face_tested[l]) continue;
                        face_tested[l] = 1;
                        if (p->face == l) continue;
                        TestPatchToFace(i, l);
                    }
                }
                /* TODO: displacements in this cluster */
            }
            FinishStream(&stream);
            p->numtransfers = 0;
            for (int t = 0; t < ntests; t++)
                if (test_hit[t] == -1 || test_dist[t] >= test_len[t]) MakeTransfer(test_shooter[t], test_receiver[t], all);
            MakeScales(i, all);
            total_transfer += p->numtransfers;
            if (p->numtransfers > max_transfer) max_transfer = p->numtransfers;
        }
    }
    Msg("transfers %d, max %d\n", total_transfer, max_transfer);
    free(all), free(test_shooter), free(test_receiver), free(test_hit), free(test_dist), free(test_len), free(pvs), free(face_tested);
}

/* ------------------------------------------------------------------ bouncing */
static vec3_t *emitlight;
static vec3_t (*addlight)[4];

static void GatherLight(int j) {
    patch_t *p = &patches[j];
    const transfer_t *t = p->transfers;
    if (p->needs_bump) {
        vec3_t normals[4], sum[4] = {{0}};
        GetPhongNormal(p->face, p->origin, normals[0]);
        const texinfo_t *tx = &texinfo[g_pFaces[p->face].texinfo];
        GetBumpNormals(tx->textureVecsTexelsPerWorldUnits[0], tx->textureVecsTexelsPerWorldUnits[1], p->normal, normals[0],
                       &normals[1]);
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
