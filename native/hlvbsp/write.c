/* Emitting the tree into the BSP's arrays, the portal file for vis, and the .bsp file itself in the
 * layout L4D2's vbsp writes (version 21, lumps in vbsp's order, 4-byte aligned). */
#include "hlvbsp.h"
#include "disp.h"

dvertex_t *dvertexes; int numvertexes;
dedge_t *dedges; int numedges;
int *dsurfedges; int numsurfedges;
dnode_t *dnodes; int numnodes;
dleaf_t *dleafs; int numleafs;
dface_t *dfaces; int numfaces;
dface_t *dorigfaces; int numorigfaces;
unsigned short *dfaceids;
unsigned short *dleaffaces; int numleaffaces;
unsigned short *dleafbrushes; int numleafbrushes;
dmodel_t dmodels[1024]; int nummodels;
dbrush_t *dbrushes; int numbrushes;
dbrushside_t *dbrushsides; int numbrushsides;
darea_t dareas[256]; int numareas;
dareaportal_t dareaportals[1024]; int numareaportals;
vec3_t clipportalverts[65536]; int numclipportalverts;
static vec3_t *vertnormals; static int numvertnormals;
static unsigned short *vertnormalindices; static int numvertnormalindices;
static unsigned short *leafmindist;
static unsigned short *facemacro;

typedef struct { unsigned char type; unsigned short firstindex, numindices, firstvert, numverts; } dprimitive_t;
extern dprimitive_t g_primitives[];
extern int g_numprimitives;
extern unsigned short g_primindices[];
extern int g_numprimindices;

#define MAX_MAP_FACES 65536
static side_t **origface_sides;   /* per plane: chain of sides already made into original faces */
static int firstmodleaf;

void BeginBSPFile(void) {
    dvertexes = xalloc(sizeof(dvertex_t) * 65536);
    dedges = xalloc(sizeof(dedge_t) * 256000);
    dsurfedges = xalloc(sizeof(int) * 512000);
    dnodes = xalloc(sizeof(dnode_t) * 65536);
    dleafs = xalloc(sizeof(dleaf_t) * 65536);
    dfaces = xalloc(sizeof(dface_t) * MAX_MAP_FACES);
    dorigfaces = xalloc(sizeof(dface_t) * MAX_MAP_FACES);
    dfaceids = xalloc(sizeof(unsigned short) * MAX_MAP_FACES);
    dleaffaces = xalloc(sizeof(unsigned short) * 65536);
    dleafbrushes = xalloc(sizeof(unsigned short) * 65536);
    edgefaces[0] = xalloc(sizeof(face_t *) * 256000);
    edgefaces[1] = xalloc(sizeof(face_t *) * 256000);
    origface_sides = xalloc(sizeof(side_t *) * MAX_MAP_PLANES);
    nummodels = numfaces = numnodes = numbrushsides = numleaffaces = numleafbrushes = numsurfedges = 0;
    numedges = 1;
    numvertexes = 1;
    numleafs = 1;
    dleafs[0].contents = CONTENTS_SOLID;
    dleafs[0].leafwaterdata = -1;
}

void BeginModel(void) {
    dmodel_t *mod = &dmodels[nummodels];
    mod->firstface = numfaces;
    firstmodleaf = numleafs;
    firstmodeledge = numedges;
    entity_t *e = &entities[entity_num];
    vec3_t mins, maxs;
    ClearBounds(mins, maxs);
    for (int j = e->firstbrush; j < e->firstbrush + e->numbrushes; j++) {
        mapbrush_t *b = &mapbrushes[j];
        if (!b->numsides) continue;
        AddPointToBounds(b->mins, mins, maxs);
        AddPointToBounds(b->maxs, mins, maxs);
    }
    VectorCopy(mins, mod->mins);
    VectorCopy(maxs, mod->maxs);
}

void EndModel(void) {
    dmodel_t *mod = &dmodels[nummodels];
    mod->numfaces = numfaces - mod->firstface;
    nummodels++;
}

/* ------------------------------------------------------------------ faces */
static int CreateOrigFace(face_t *f) {
    if (!f->w) return -1;
    side_t *side = f->originalface;
    if (!side->winding) return -1;
    if (numorigfaces >= MAX_MAP_FACES) Error("Too many faces in map, max = %d", MAX_MAP_FACES);
    dface_t *of = &dorigfaces[numorigfaces++];
    memset(of, 0, sizeof(*of));
    of->origface = -1;
    side->next = origface_sides[f->planenum];
    origface_sides[f->planenum] = side;
    side->origindex = numorigfaces - 1;
    winding_t *w = side->winding;
    of->planenum = (unsigned short)side->planenum;
    of->onnode = (side->contents & CONTENTS_DETAIL) ? 0 : 1;
    of->side = side->planenum & 1;
    of->firstedge = numsurfedges;
    of->numedges = (short)w->numpoints;
    of->texinfo = (short)side->texinfo;
    of->dispinfo = (short)f->dispinfo;
    int vidx[128];
    for (int i = 0; i < w->numpoints; i++) vidx[i] = GetVertexnum(w->p[i]);
    for (int i = 0; i < w->numpoints; i++) {
        int e0 = vidx[i], e1 = vidx[(i + 1) % w->numpoints], j;
        for (j = firstmodeledge; j < numedges; j++) {
            if (e0 == dedges[j].v[1] && e1 == dedges[j].v[0] && edgefaces[0][j]->contents == f->contents) {
                if (edgefaces[1][j]) continue;
                edgefaces[1][j] = f;
                dsurfedges[numsurfedges++] = -j;
                break;
            }
        }
        if (j == numedges) {
            AddEdge(e0, e1, f);
            dsurfedges[numsurfedges++] = numedges - 1;
        }
    }
    return numorigfaces - 1;
}

static int FindOrCreateOrigFace(face_t *f) {
    if (!f->originalface) return -1;
    for (side_t *s = origface_sides[f->planenum]; s; s = s->next)
        if (s == f->originalface) return s->origindex;
    return CreateOrigFace(f);
}

static void EmitFace(face_t *f, int onnode) {
    f->outputnumber = -1;
    if (f->numpoints < 3) return;
    if (f->merged || f->split[0] || f->split[1]) return;
    if (texinfos[f->texinfo].flags & SURF_NODRAW) {
        if (f->dispinfo == -1) return;
        Warning("NODRAW on terrain surface!\n");
    }
    f->outputnumber = numfaces;
    if (numfaces >= MAX_MAP_FACES) Error("Too many faces in map, max = %d", MAX_MAP_FACES);
    dface_t *df = &dfaces[numfaces];
    memset(df, 0, sizeof(*df));
    dfaceids[numfaces] = (unsigned short)f->originalface->id;
    numfaces++;
    df->planenum = (unsigned short)f->planenum;
    df->onnode = (unsigned char)onnode;
    df->side = f->planenum & 1;
    df->texinfo = (short)f->texinfo;
    df->dispinfo = (short)f->dispinfo;
    df->smoothing = f->smoothing;
    df->origface = FindOrCreateOrigFace(f);
    df->fogvolume = -1;
    df->firstedge = numsurfedges;
    df->numedges = (short)f->numpoints;
    df->area = f->w ? WindingArea(f->w) : 0;
    df->firstprim = (unsigned short)f->firstprim;
    df->numprims = (unsigned short)(f->numprims & 0x7FFF);   /* top bit set = no dynamic shadows */
    for (int i = 0; i < f->numpoints; i++) {
        int e = GetEdge2(f->vertexnums[i], f->vertexnums[(i + 1) % f->numpoints], f);
        dsurfedges[numsurfedges++] = e;
    }
    if (f->originalface && f->originalface->noverlays) Overlay_AddFaceToLists(numfaces - 1, f->originalface);
}

static void EmitMarkFace(dleaf_t *leaf, face_t *f) {
    while (f->merged) f = f->merged;
    if (f->split[0]) {
        EmitMarkFace(leaf, f->split[0]);
        EmitMarkFace(leaf, f->split[1]);
        return;
    }
    int facenum = f->outputnumber;
    if (facenum == -1) return;
    if (facenum < 0 || facenum >= numfaces) Error("Bad leafface");
    int i;
    for (i = leaf->firstleafface; i < numleaffaces; i++)
        if (dleaffaces[i] == facenum) break;
    if (i == numleaffaces) dleaffaces[numleaffaces++] = (unsigned short)facenum;
}

static void VecToShorts(const vec3_t v, short *s) {
    for (int i = 0; i < 3; i++) s[i] = (short)v[i];
}

static void EmitLeaf(node_t *node) {
    if (numleafs >= 65536) Error("Too many BSP leaves, max = %d", 65536);
    node->diskid = numleafs;
    dleaf_t *leaf = &dleafs[numleafs++];
    memset(leaf, 0, sizeof(*leaf));
    leaf->cluster = nummodels == 0 ? (short)node->cluster : -1;
    leaf->contents = node->contents;
    leaf->area_flags = (short)((node->area & 0x1FF) | (1 << 9));    /* LEAF_FLAGS_SKY, as vbsp sets it */
    leaf->leafwaterdata = -1;
    VecToShorts(node->mins, leaf->mins);
    VecToShorts(node->maxs, leaf->maxs);
    leaf->firstleafbrush = (unsigned short)numleafbrushes;
    for (bspbrush_t *b = node->brushlist; b; b = b->next) {
        int brushnum = (int)(b->original - mapbrushes), i;
        for (i = leaf->firstleafbrush; i < numleafbrushes; i++)
            if (dleafbrushes[i] == brushnum) break;
        if (i == numleafbrushes) dleafbrushes[numleafbrushes++] = (unsigned short)brushnum;
    }
    leaf->numleafbrushes = (unsigned short)(numleafbrushes - leaf->firstleafbrush);
    if (leaf->contents & CONTENTS_SOLID) return;
    leaf->firstleafface = (unsigned short)numleaffaces;
    int s;
    for (portal_t *p = node->portals; p; p = p->next[s]) {
        s = (p->nodes[1] == node);
        face_t *f = p->face[s];
        if (!f) continue;
        EmitMarkFace(leaf, f);
    }
    for (leafface_t *l = node->leaffaces; l; l = l->next) EmitMarkFace(leaf, l->face);
    leaf->numleaffaces = (unsigned short)(numleaffaces - leaf->firstleafface);
}

static int EmitDrawNode_r(node_t *node) {
    if (node->planenum == PLANENUM_LEAF) {
        EmitLeaf(node);
        return -numleafs;
    }
    node->diskid = numnodes;
    dnode_t *n = &dnodes[numnodes++];
    memset(n, 0, sizeof(*n));
    VecToShorts(node->mins, n->mins);
    VecToShorts(node->maxs, n->maxs);
    if (node->planenum & 1) Error("WriteDrawNodes_r: odd planenum");
    n->planenum = node->planenum;
    n->firstface = (unsigned short)numfaces;
    n->area = (short)node->area;
    for (face_t *f = node->faces; f; f = f->next) EmitFace(f, 1);
    n->numfaces = (unsigned short)(numfaces - n->firstface);
    for (int i = 0; i < 2; i++) {
        if (node->children[i]->planenum == PLANENUM_LEAF) {
            n->children[i] = -(numleafs + 1);
            EmitLeaf(node->children[i]);
        } else {
            n->children[i] = numnodes;
            EmitDrawNode_r(node->children[i]);
        }
    }
    return (int)(n - dnodes);
}

void WriteBSP(node_t *headnode, face_t *leaffaces) {
    ResetEdgeLists();
    for (face_t *f = leaffaces; f; f = f->next) EmitFace(f, 0);
    dmodels[nummodels].headnode = EmitDrawNode_r(headnode);
    if (nummodels == 0) EmitAreaPortals(headnode);
    for (int i = 0; i < nummapdisps; i++) {
        if (mapdisps[i].entitynum != entity_num) continue;
        EmitDispFaceVertexes(&mapdisps[i].face);
        EmitFace(&mapdisps[i].face, 0);
    }
    EmitWaterVolumesForBSP(headnode);
}

/* ------------------------------------------------------------------ entities */
void SetModelNumbers(void) {
    int models = 1;
    char value[16];
    for (int i = 1; i < num_entities; i++) {
        if (!entities[i].numbrushes) continue;
        if (strcmp(ValueForKey(&entities[i], "classname"), "func_occluder")) {
            sprintf(value, "*%i", models);
            models++;
        } else value[0] = 0;
        SetKeyValue(&entities[i], "model", value);
    }
}

/* Named lights get switchable light styles 32 and up. */
void SetLightStyles(void) {
    char targets[32][64], value[16];
    int stylenum = 0;
    for (int i = 1; i < num_entities; i++) {
        entity_t *e = &entities[i];
        const char *t = ValueForKey(e, "classname");
        if (_strnicmp(t, "light", 5) || !_stricmp(t, "light_dynamic")) continue;
        t = ValueForKey(e, "targetname");
        if (!t[0]) continue;
        int j;
        for (j = 0; j < stylenum; j++)
            if (!strcmp(targets[j], t)) break;
        if (j == stylenum) {
            if (stylenum == 32) Error("Too many switched lights (error at light %s), max = %d", t, 32);
            strncpy(targets[j], t, 63);
            targets[j][63] = 0;
            stylenum++;
        }
        sprintf(value, "%i", 32 + j);
        const char *cur = ValueForKey(e, "style");
        if (atoi(cur) != 0) SetKeyValue(e, "defaultstyle", cur);
        SetKeyValue(e, "style", value);
    }
}

static void AddNodeToBounds(int node, vec3_t mins, vec3_t maxs) {
    if (node >= 0) {
        AddNodeToBounds(dnodes[node].children[0], mins, maxs);
        AddNodeToBounds(dnodes[node].children[1], mins, maxs);
        return;
    }
    int leaf = -1 - node;
    if (dleafs[leaf].contents & CONTENTS_SOLID) return;
    for (int i = 0; i < dleafs[leaf].numleaffaces; ++i) {
        int face = dleaffaces[dleafs[leaf].firstleafface + i];
        if (texinfos[dfaces[face].texinfo].flags & SURF_NODRAW) continue;    /* (L4D2's vbsp counts sky faces) */
        for (int j = 0; j < dfaces[face].numedges; ++j) {
            int edge = abs(dsurfedges[dfaces[face].firstedge + j]);
            AddPointToBounds(dvertexes[dedges[edge].v[0]].point, mins, maxs);
            AddPointToBounds(dvertexes[dedges[edge].v[1]].point, mins, maxs);
        }
    }
}

static int IsBoxInsideWorld(int node, const vec3_t mins, const vec3_t maxs) {
    for (;;) {
        if (node < 0) return !(dleafs[-1 - node].contents & CONTENTS_SOLID);
        dnode_t *n = &dnodes[node];
        int side = BoxOnPlaneSide(mins, maxs, &mapplanes[n->planenum]);
        if (side == 1) node = n->children[0];
        else if (side == 2) node = n->children[1];
        else {
            if (IsBoxInsideWorld(n->children[0], mins, maxs)) return 1;
            node = n->children[1];
        }
    }
}

/* world_mins / world_maxs: the drawn world (3D sky areas left out: not yet). */
void ComputeBoundsNoSkybox(void) {
    vec3_t mins, maxs;
    ClearBounds(mins, maxs);
    AddNodeToBounds(dmodels[0].headnode, mins, maxs);
    for (int i = 0; i < nummapdisps; ++i) {
        vec3_t dmin, dmax;
        DispBounds(i, dmin, dmax);
        if (getenv("HLVBSP_DEBUG_BOUNDS")) printf("disp %d box %g %g %g  %g %g %g inside %d\n", i, dmin[0], dmin[1], dmin[2], dmax[0], dmax[1], dmax[2], IsBoxInsideWorld(dmodels[0].headnode, dmin, dmax));
        if (IsBoxInsideWorld(dmodels[0].headnode, dmin, dmax)) {
            AddPointToBounds(dmin, mins, maxs);
            AddPointToBounds(dmax, mins, maxs);
        }
    }
    for (int i = 0; i < num_entities; ++i) {
        if (!strcmp(ValueForKey(&entities[i], "classname"), "worldspawn")) {
            char s[64];
            sprintf(s, "%i %i %i", (int)mins[0], (int)mins[1], (int)mins[2]);
            SetKeyValue(&entities[i], "world_mins", s);
            sprintf(s, "%i %i %i", (int)maxs[0], (int)maxs[1], (int)maxs[2]);
            SetKeyValue(&entities[i], "world_maxs", s);
            break;
        }
    }
}

static char *entdata;
static int entdatasize;

static void StripTrailing(char *e) {
    char *s = e + strlen(e) - 1;
    while (s >= e && *s <= 32) *s-- = 0;
}

static void UnparseEntities(void) {
    size_t cap = 1 << 16, n = 0;
    entdata = xalloc(cap);
    char key[1024], value[1024], line[2100];
    for (int i = 0; i < num_entities; i++) {
        epair_t *ep = entities[i].epairs;
        if (!ep) continue;
        for (int pass = 0; pass < 1; pass++) {
            const char *open = "{\n";
            size_t l = strlen(open);
            if (n + l + 1 > cap) { cap *= 2; entdata = realloc(entdata, cap); }
            memcpy(entdata + n, open, l); n += l;
        }
        for (; ep; ep = ep->next) {
            strncpy(key, ep->key, sizeof(key) - 1); key[sizeof(key) - 1] = 0;
            StripTrailing(key);
            strncpy(value, ep->value, sizeof(value) - 1); value[sizeof(value) - 1] = 0;
            StripTrailing(value);
            int l = snprintf(line, sizeof(line), "\"%s\" \"%s\"\n", key, value);
            while (n + l + 1 > cap) { cap *= 2; entdata = realloc(entdata, cap); }
            memcpy(entdata + n, line, l); n += l;
        }
        if (n + 3 > cap) { cap *= 2; entdata = realloc(entdata, cap); }
        memcpy(entdata + n, "}\n", 2); n += 2;
    }
    entdata[n] = 0;
    entdatasize = (int)n + 1;
}

/* ------------------------------------------------------------------ brushes, planes, texinfo */
static void EmitBrushes(void) {
    numbrushsides = 0;
    numbrushes = nummapbrushes;
    dbrushes = xalloc(sizeof(dbrush_t) * (nummapbrushes + 1));
    dbrushsides = xalloc(sizeof(dbrushside_t) * (nummapbrushsides + nummapbrushes * 6 + 1));
    for (int bnum = 0; bnum < nummapbrushes; bnum++) {
        mapbrush_t *b = &mapbrushes[bnum];
        dbrush_t *db = &dbrushes[bnum];
        db->contents = b->contents;
        db->firstside = numbrushsides;
        db->numsides = b->numsides;
        for (int j = 0; j < b->numsides; j++) {
            dbrushside_t *cp = &dbrushsides[numbrushsides++];
            memset(cp, 0, sizeof(*cp));
            cp->planenum = (unsigned short)b->original_sides[j].planenum;
            cp->texinfo = (short)b->original_sides[j].texinfo;
            if (cp->texinfo == -1) cp->texinfo = (short)g_cliptexinfo;
            cp->bevel = (unsigned char)b->original_sides[j].bevel;
        }
        /* boxes swept against the brush need its axial planes */
        for (int x = 0; x < 3; x++)
            for (int s = -1; s <= 1; s += 2) {
                vec3_t normal = {0, 0, 0};
                normal[x] = (vec_t)s;
                vec_t dist = s == -1 ? -b->mins[x] : b->maxs[x];
                int planenum = FindFloatPlane(normal, dist), i;
                for (i = 0; i < b->numsides; i++)
                    if (b->original_sides[i].planenum == planenum) break;
                if (i == b->numsides) {
                    dbrushsides[numbrushsides].planenum = (unsigned short)planenum;
                    dbrushsides[numbrushsides].texinfo = dbrushsides[numbrushsides - 1].texinfo;
                    numbrushsides++;
                    db->numsides++;
                }
            }
    }
}

static void SaveVertexNormals(void) {
    vertnormals = xalloc(sizeof(vec3_t) * (numfaces + 1));
    vertnormalindices = xalloc(sizeof(unsigned short) * (numsurfedges + 1));
    numvertnormals = numvertnormalindices = 0;
    for (int i = 0; i < numfaces; i++) {
        dface_t *f = &dfaces[i];
        for (int j = 0; j < f->numedges; j++) vertnormalindices[numvertnormalindices++] = (unsigned short)numvertnormals;
        VectorCopy(mapplanes[f->planenum].normal, vertnormals[numvertnormals]);
        numvertnormals++;
    }
}

static void CalcFaceExtents(dface_t *s) {
    vec_t mins[2] = {1e24f, 1e24f}, maxs[2] = {-1e24f, -1e24f};
    texinfo_t *tex = &texinfos[s->texinfo];
    for (int i = 0; i < s->numedges; i++) {
        int e = dsurfedges[s->firstedge + i];
        dvertex_t *v = e >= 0 ? dvertexes + dedges[e].v[0] : dvertexes + dedges[-e].v[1];
        for (int j = 0; j < 2; j++) {
            vec_t val = v->point[0] * tex->lmvecs[j][0] + v->point[1] * tex->lmvecs[j][1] +
                        v->point[2] * tex->lmvecs[j][2] + tex->lmvecs[j][3];
            if (val < mins[j]) mins[j] = val;
            if (val > maxs[j]) maxs[j] = val;
        }
    }
    for (int i = 0; i < 2; i++) {
        mins[i] = (float)floor(mins[i]);
        maxs[i] = (float)ceil(maxs[i]);
        s->lm_mins[i] = (int)mins[i];
        s->lm_size[i] = (int)(maxs[i] - mins[i]);
        if (s->lm_size[i] > (s->dispinfo == -1 ? 32 : 125) + 1) {
            Error("Bad surface extents - surface is too big to have a lightmap\n\tmaterial %s",
                  texdata_strings + texdata_string_table[texdatas[tex->texdata].name_id]);
        }
    }
}

static void UpdateAllFaceLightmapExtents(void) {
    for (int i = 0; i < numfaces; i++) {
        dface_t *f = &dfaces[i];
        if (texinfos[f->texinfo].flags & (SURF_SKY | SURF_NOLIGHT)) continue;
        CalcFaceExtents(f);
    }
}

int texdata_surfaceprop(int texdata);

/* Drop unused texinfos / texdata (sky texinfos collapse to the first), renumbering everything. */
static void CompactTexinfos(void) {
    Msg("Compacting texture/material tables...\n");
    int *ref = xalloc(sizeof(int) * (numtexinfo + 1)), *out = xalloc(sizeof(int) * (numtexinfo + 1));
    int *tdref = xalloc(sizeof(int) * (numtexdata + 1)), *tdout = xalloc(sizeof(int) * (numtexdata + 1));
    for (int i = 0; i < numfaces; i++) ref[dfaces[i].texinfo]++;
    for (int i = 0; i < numbrushsides; i++) {
        if (!ref[dbrushsides[i].texinfo]) {
            /* a brush side whose texinfo no face uses: reuse a used one with the same flags & surface */
            int t = dbrushsides[i].texinfo, flags = texinfos[t].flags, sp = texdata_surfaceprop(texinfos[t].texdata);
            int found = t;
            for (int j = 0; j < numtexinfo; j++)
                if (ref[j] > 0 && texinfos[j].flags == flags && texdata_surfaceprop(texinfos[j].texdata) == sp) {
                    found = j;
                    break;
                }
            dbrushsides[i].texinfo = (short)found;
            if (!ref[found]) ref[found]++;
        }
    }
    Overlay_CountTexinfos(ref);
    Water_CountTexinfos(ref);
    for (int i = 0; i < numtexinfo; i++)
        if (ref[i] > 0) tdref[texinfos[i].texdata]++;
    int oldcount = numtexinfo, oldtd = numtexdata, oldstr = texdata_strings_len;
    texinfo_t *old = xalloc(sizeof(texinfo_t) * (numtexinfo + 1));
    memcpy(old, texinfos, sizeof(texinfo_t) * numtexinfo);
    int n = 0, firstsky = -1, first2d = -1;
    for (int i = 0; i < oldcount; i++) {
        if (!ref[i]) { out[i] = -1; continue; }
        if (old[i].flags & SURF_SKY2D) {
            if (first2d < 0) { first2d = n; texinfos[n++] = old[i]; }
            out[i] = first2d;
            continue;
        }
        if (old[i].flags & SURF_SKY) {
            if (firstsky < 0) { firstsky = n; texinfos[n++] = old[i]; }
            out[i] = firstsky;
            continue;
        }
        out[i] = n;
        texinfos[n++] = old[i];
    }
    numtexinfo = n;
    Overlay_RemapTexinfos(out);
    Water_RemapTexinfos(out);
    /* texdata, with a fresh string table */
    char *oldstrings = texdata_strings;
    int *oldtable = texdata_string_table;
    texdata_t *oldtexdata = xalloc(sizeof(texdata_t) * (numtexdata + 1));
    memcpy(oldtexdata, texdatas, sizeof(texdata_t) * numtexdata);
    texdata_strings = NULL;
    texdata_strings_len = 0;
    texdata_string_table = NULL;
    numtexdata_strings = 0;
    int ntd = 0;
    for (int i = 0; i < oldtd; i++) {
        if (!tdref[i]) { tdout[i] = -1; continue; }
        tdout[i] = ntd;
        texdatas[ntd] = oldtexdata[i];
        texdatas[ntd].name_id = TexDataString(oldstrings + oldtable[oldtexdata[i].name_id]);
        ntd++;
    }
    numtexdata = ntd;
    for (int i = 0; i < numtexinfo; i++) texinfos[i].texdata = tdout[texinfos[i].texdata];
    for (int i = 0; i < numfaces; i++) dfaces[i].texinfo = (short)out[dfaces[i].texinfo];
    for (int i = 0; i < numbrushsides; i++) dbrushsides[i].texinfo = (short)out[dbrushsides[i].texinfo];
    Msg("Reduced %d texinfos to %d\n", oldcount, numtexinfo);
    Msg("Reduced %d texdatas to %d (%d bytes to %d)\n", oldtd, numtexdata, oldstr, texdata_strings_len);
    free(ref); free(out); free(tdref); free(tdout); free(old); free(oldtexdata); free(oldstrings); free(oldtable);
}

/* ------------------------------------------------------------------ the file */
typedef struct { const void *data; int len, version; } lump_t;
static lump_t lumps[64];

static void SetLump(int i, const void *data, int len, int version) {
    lumps[i].data = data;
    lumps[i].len = len;
    lumps[i].version = version;
}

/* The order L4D2's vbsp writes lumps in (measured; an empty lump takes the current offset). */
static const int lump_order[] = {59, 6, 2, 43, 44, 10, 17, 1, 18, 19, 14, 5, 20, 21, 4, 0, 29, 62, 26, 3, 12, 13, 7, 33,
                                 48, 28, 9, 8, 53, 37, 38, 39, 30, 31, 51, 52, 55, 56, 16, 36, 45, 50, 60, 61, 46, 15,
                                 41, 42, 54, 34, 47, 11, 27, 22, 23, 24, 25, 49, 35, 40};

extern unsigned char *phys_collide; extern int phys_collide_len;
unsigned char *phys_collide; int phys_collide_len;

static void WriteBSPFile(const char *path) {
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
    for (unsigned k = 0; k < sizeof(lump_order) / sizeof(lump_order[0]); k++) {
        int i = lump_order[k];
        lump_t *l = &lumps[i];
        int fields[4] = {l->version, offset, l->len, 0};
        memcpy(header + 8 + 16 * i, fields, 16);
        if (l->len) {
            fwrite(l->data, 1, l->len, f);
            offset += l->len;
            static const unsigned char zero[4] = {0};
            int pad = (4 - (offset & 3)) & 3;
            fwrite(zero, 1, pad, f);
            offset += pad;
        }
    }
    fseek(f, 0, SEEK_SET);
    fwrite(header, 1, sizeof(header), f);
    fclose(f);
}

/* Pack the arrays into the file's record layouts. */
static unsigned char *pack_planes(int *len) {
    unsigned char *b = xalloc(20 * nummapplanes + 1);
    for (int i = 0; i < nummapplanes; i++) {
        memcpy(b + 20 * i, mapplanes[i].normal, 12);
        memcpy(b + 20 * i + 12, &mapplanes[i].dist, 4);
        memcpy(b + 20 * i + 16, &mapplanes[i].type, 4);
    }
    *len = 20 * nummapplanes;
    return b;
}

static unsigned char *pack_faces(dface_t *src, int n, int *len) {
    unsigned char *b = xalloc(56 * n + 1);
    for (int i = 0; i < n; i++) {
        unsigned char *p = b + 56 * i;
        dface_t *f = &src[i];
        memcpy(p + 0, &f->planenum, 2);
        p[2] = f->side;
        p[3] = f->onnode;
        memcpy(p + 4, &f->firstedge, 4);
        memcpy(p + 8, &f->numedges, 2);
        memcpy(p + 10, &f->texinfo, 2);
        memcpy(p + 12, &f->dispinfo, 2);
        memcpy(p + 14, &f->fogvolume, 2);
        memcpy(p + 16, f->styles, 4);
        memcpy(p + 20, &f->lightofs, 4);
        memcpy(p + 24, &f->area, 4);
        memcpy(p + 28, f->lm_mins, 8);
        memcpy(p + 36, f->lm_size, 8);
        memcpy(p + 44, &f->origface, 4);
        memcpy(p + 48, &f->numprims, 2);
        memcpy(p + 50, &f->firstprim, 2);
        memcpy(p + 52, &f->smoothing, 4);
    }
    *len = 56 * n;
    return b;
}

static unsigned char *pack_texinfo(int *len) {
    unsigned char *b = xalloc(72 * numtexinfo + 1);
    for (int i = 0; i < numtexinfo; i++) {
        memcpy(b + 72 * i, texinfos[i].vecs, 32);
        memcpy(b + 72 * i + 32, texinfos[i].lmvecs, 32);
        memcpy(b + 72 * i + 64, &texinfos[i].flags, 4);
        memcpy(b + 72 * i + 68, &texinfos[i].texdata, 4);
    }
    *len = 72 * numtexinfo;
    return b;
}

static unsigned char *pack_texdata(int *len) {
    unsigned char *b = xalloc(32 * numtexdata + 1);
    for (int i = 0; i < numtexdata; i++) {
        memcpy(b + 32 * i, texdatas[i].reflectivity, 12);
        memcpy(b + 32 * i + 12, &texdatas[i].name_id, 4);
        memcpy(b + 32 * i + 16, &texdatas[i].width, 4);
        memcpy(b + 32 * i + 20, &texdatas[i].height, 4);
        memcpy(b + 32 * i + 24, &texdatas[i].view_width, 4);
        memcpy(b + 32 * i + 28, &texdatas[i].view_height, 4);
    }
    *len = 32 * numtexdata;
    return b;
}

static unsigned char *pack_models(int *len) {
    unsigned char *b = xalloc(48 * nummodels + 1);
    for (int i = 0; i < nummodels; i++) memcpy(b + 48 * i, &dmodels[i], 48);
    *len = 48 * nummodels;
    return b;
}

static unsigned char *pack_brushsides(int *len) {
    unsigned char *b = xalloc(8 * numbrushsides + 1);
    for (int i = 0; i < numbrushsides; i++) {
        memcpy(b + 8 * i, &dbrushsides[i].planenum, 2);
        memcpy(b + 8 * i + 2, &dbrushsides[i].texinfo, 2);
        memcpy(b + 8 * i + 4, &dbrushsides[i].dispinfo, 2);
        b[8 * i + 6] = dbrushsides[i].bevel;
        b[8 * i + 7] = dbrushsides[i].thin;
    }
    *len = 8 * numbrushsides;
    return b;
}

static unsigned char *pack_areaportals(int *len) {
    unsigned char *b = xalloc(12 * numareaportals + 1);
    for (int i = 0; i < numareaportals; i++) {
        memcpy(b + 12 * i, &dareaportals[i].key, 8);
        memcpy(b + 12 * i + 8, &dareaportals[i].planenum, 4);
    }
    *len = 12 * numareaportals;
    return b;
}

static unsigned char *pack_primitives(int *len) {
    unsigned char *b = xalloc(10 * g_numprimitives + 1);
    for (int i = 0; i < g_numprimitives; i++) {
        unsigned char *p = b + 10 * i;
        p[0] = g_primitives[i].type;
        p[1] = 0;
        memcpy(p + 2, &g_primitives[i].firstindex, 2);
        memcpy(p + 4, &g_primitives[i].numindices, 2);
        memcpy(p + 6, &g_primitives[i].firstvert, 2);
        memcpy(p + 8, &g_primitives[i].numverts, 2);
    }
    *len = 10 * g_numprimitives;
    return b;
}

void EmitPhysCollision(void);
void EmitStaticProps(void);
void EmitDetailObjects(void);
extern unsigned char *g_dprp;
extern int g_dprp_len;
extern unsigned char *g_sprp;
extern int g_sprp_len;

void AddDefaultCubemaps(const char *mapname);
unsigned char *BuildPakLump(int *outlen);

void EndBSPFile(const char *path) {
    EmitBrushes();
    SaveVertexNormals();
    UpdateAllFaceLightmapExtents();
    EmitDispLMAlphaAndNeighbors();
    Overlay_EmitOverlayFaces();
    EmitPhysCollision();
    leafmindist = xalloc(sizeof(unsigned short) * (numleafs + 1));
    EmitStaticProps();
    EmitDetailObjects();
    ComputeBoundsNoSkybox();
    EnsurePresenceOfWaterLODControlEntity();
    UnparseEntities();
    CompactTexinfos();
    facemacro = xalloc(sizeof(unsigned short) * (numfaces + 1));
    for (int i = 0; i < numfaces; i++) facemacro[i] = 0xFFFF;

    int len;
    for (int i = 0; i < 64; i++) SetLump(i, NULL, 0, 0);
    unsigned char *planes = pack_planes(&len); SetLump(1, planes, len, 0);
    SetLump(10, dleafs, 32 * numleafs, 1);
    SetLump(55, NULL, 0, 1);
    SetLump(56, NULL, 0, 1);
    SetLump(3, dvertexes, 12 * numvertexes, 0);
    SetLump(5, dnodes, 32 * numnodes, 0);
    unsigned char *ti = pack_texinfo(&len); SetLump(6, ti, len, 0);
    unsigned char *td = pack_texdata(&len); SetLump(2, td, len, 0);
    SetLump(47, facemacro, 2 * numfaces, 0);
    unsigned char *prims = pack_primitives(&len); SetLump(37, prims, len, 0);
    SetLump(39, g_primindices, 2 * g_numprimindices, 0);
    unsigned char *faces = pack_faces(dfaces, numfaces, &len); SetLump(7, faces, len, 1);
    SetLump(11, dfaceids, 2 * numfaces, 0);
    unsigned char *ofaces = pack_faces(dorigfaces, numorigfaces, &len); SetLump(27, ofaces, len, 0);
    SetLump(18, dbrushes, 12 * numbrushes, 0);
    unsigned char *bs = pack_brushsides(&len); SetLump(19, bs, len, 0);
    SetLump(16, dleaffaces, 2 * numleaffaces, 0);
    SetLump(17, dleafbrushes, 2 * numleafbrushes, 0);
    SetLump(13, dsurfedges, 4 * numsurfedges, 0);
    SetLump(12, dedges, 4 * numedges, 0);
    unsigned char *models = pack_models(&len); SetLump(14, models, len, 0);
    SetLump(20, dareas, 8 * numareas, 0);
    unsigned char *aps = pack_areaportals(&len); SetLump(21, aps, len, 0);
    SetLump(8, NULL, 0, 1);
    SetLump(53, NULL, 0, 1);
    SetLump(0, entdata, entdatasize, 0);
    SetLump(15, NULL, 0, 1);
    SetLump(54, NULL, 0, 1);
    static int occlusion[3] = {0, 0, 0};
    SetLump(9, occlusion, 12, 2);
    static int mapflags = 0;
    SetLump(59, &mapflags, 4, 0);
    SetLump(41, clipportalverts, 12 * numclipportalverts, 0);
    SetLump(43, texdata_strings, texdata_strings_len, 0);
    SetLump(44, texdata_string_table, 4 * numtexdata_strings, 0);
    SetLump(29, phys_collide, phys_collide_len, 0);
    static unsigned short physdisp = 0;
    extern unsigned char *phys_disp;
    extern int phys_disp_len;
    if (phys_disp) SetLump(28, phys_disp, phys_disp_len, 0);
    else SetLump(28, &physdisp, 2, 0);
    SetLump(30, vertnormals, 12 * numvertnormals, 0);
    SetLump(31, vertnormalindices, 2 * numvertnormalindices, 0);
    SetLump(46, leafmindist, 2 * numleafs, 0);
    SetLump(26, g_dispinfo, (int)sizeof(ddispinfo_t) * nummapdisps, 0);
    SetLump(33, g_dispverts, 20 * g_numdispverts, 0);
    SetLump(48, g_disptris, 2 * g_numdisptris, 0);
    SetLump(34, g_lmsamples, g_numlmsamples, 0);
    SetLump(62, NULL, 0, 16);
    {
        unsigned char *fades, *levels;
        int len60, len61;
        unsigned char *ov = Overlay_Lumps(&len, &fades, &len60, &levels, &len61);
        SetLump(45, ov, len, 0);
        SetLump(60, fades, len60, 0);
        SetLump(61, levels, len61, 0);
        unsigned char *lw = Water_Lump(&len);
        SetLump(36, lw, len, 0);
    }
    /* the pakfile: the default cubemap (vbsp makes it whenever the world has a sky) */
    {
        char base[256];
        const char *slash = strrchr(path, '/'), *bslash = strrchr(path, 92);   /* backslash */
        const char *b = slash > bslash ? slash : bslash;
        strncpy(base, b ? b + 1 : path, sizeof(base) - 1);
        base[sizeof(base) - 1] = 0;
        char *dot = strrchr(base, '.');
        if (dot) *dot = 0;
        if (ValueForKey(&entities[0], "skyname")[0]) AddDefaultCubemaps(base);
        int paklen;
        unsigned char *pak = BuildPakLump(&paklen);
        SetLump(40, pak, paklen, 0);
    }
    /* game lumps: static props (sprp v9) and detail props (dprp v4: no detail props yet, three zero
     * counts); their directory holds file offsets, so it is filled in once the lump's place is known */
    static unsigned char empty12[12];
    unsigned char *sprp = g_sprp ? g_sprp : empty12;
    int sprp_len = g_sprp ? g_sprp_len : 12;
    unsigned char *dprp = g_dprp ? g_dprp : empty12;
    int dprp_len = g_dprp ? g_dprp_len : 12;
    int gamelen = 4 + 2 * 16 + sprp_len + dprp_len;
    unsigned char *game = xalloc(gamelen);
    int count = 2;
    memcpy(game, &count, 4);
    memcpy(game + 4, "prps", 4);
    unsigned short flags = 0, ver = 9;
    memcpy(game + 8, &flags, 2);
    memcpy(game + 10, &ver, 2);
    memcpy(game + 16, &sprp_len, 4);
    memcpy(game + 20, "prpd", 4);
    ver = 4;
    memcpy(game + 24, &flags, 2);
    memcpy(game + 26, &ver, 2);
    memcpy(game + 32, &dprp_len, 4);
    memcpy(game + 36, sprp, sprp_len);
    memcpy(game + 36 + sprp_len, dprp, dprp_len);
    SetLump(35, game, gamelen, 0);
    {
        int offset = 1036;
        for (unsigned k = 0; k < sizeof(lump_order) / sizeof(lump_order[0]); k++) {
            int i = lump_order[k];
            if (i == 35) break;
            offset += lumps[i].len;
            offset = (offset + 3) & ~3;
        }
        int o1 = offset + 4 + 2 * 16, o2 = o1 + sprp_len;
        memcpy(game + 12, &o1, 4);
        memcpy(game + 28, &o2, 4);
    }
    Msg("Writing %s\n", path);
    WriteBSPFile(path);
}

/* ------------------------------------------------------------------ the portal file */
static int num_visclusters, num_visportals;

static void WriteFloat(FILE *f, vec_t v) {
    if (fabs(v - RoundInt(v)) < 0.001) fprintf(f, "%i ", (int)RoundInt(v));
    else fprintf(f, "%f ", v);
}

static void BuildVisLeafList_r(node_t *node, node_t ***leaves, int *n, int *cap) {
    if (node->planenum != PLANENUM_LEAF) {
        node->cluster = -99;
        BuildVisLeafList_r(node->children[0], leaves, n, cap);
        BuildVisLeafList_r(node->children[1], leaves, n, cap);
        return;
    }
    if (node->contents & CONTENTS_SOLID) {
        node->cluster = -1;
        return;
    }
    if (*n == *cap) {
        *cap = *cap ? *cap * 2 : 1024;
        *leaves = realloc(*leaves, sizeof(node_t *) * *cap);
    }
    (*leaves)[(*n)++] = node;
}

static void CreateVisPortals_r(node_t *node) {
    if (node->planenum == PLANENUM_LEAF) return;
    MakeNodePortal(node);
    SplitNodePortals(node);
    CreateVisPortals_r(node->children[0]);
    CreateVisPortals_r(node->children[1]);
}

static int clusterleaf;
static void SaveClusters_r(node_t *node) {
    if (node->planenum == PLANENUM_LEAF) {
        dleafs[clusterleaf++].cluster = (short)node->cluster;
        return;
    }
    SaveClusters_r(node->children[0]);
    SaveClusters_r(node->children[1]);
}

void WritePortalFile(tree_t *tree, const char *path) {
    Msg("writing %s...", path);
    node_t *headnode = tree->headnode;
    FreeTreePortals_r(headnode);
    MakeHeadnodePortals(tree);
    CreateVisPortals_r(headnode);
    Msg("Building visibility clusters...\n");
    node_t **leaves = NULL;
    int n = 0, cap = 0;
    BuildVisLeafList_r(headnode, &leaves, &n, &cap);
    num_visclusters = 0;
    for (int i = 0; i < n; i++) leaves[i]->cluster = num_visclusters++;   /* (func_viscluster: not yet) */
    /* per cluster, the portals written from their first leaf */
    portal_t **list = xalloc(sizeof(portal_t *) * 1);
    int nlist = 0, listcap = 0;
    int *start = xalloc(sizeof(int) * (num_visclusters + 1));
    num_visportals = 0;
    for (int c = 0; c < n; c++) {
        node_t *node = leaves[c];
        start[node->cluster] = nlist;
        for (portal_t *p = node->portals; p;) {
            if (p->nodes[0] == node) {
                if (p->nodes[0]->cluster != p->nodes[1]->cluster && Portal_VisFlood(p)) {
                    if (nlist == listcap) {
                        listcap = listcap ? listcap * 2 : 1024;
                        list = realloc(list, sizeof(portal_t *) * listcap);
                    }
                    list[nlist++] = p;
                    num_visportals++;
                }
                p = p->next[0];
            } else p = p->next[1];
        }
    }
    FILE *pf = fopen(path, "w");
    if (!pf) Error("Error opening %s", path);
    fprintf(pf, "PRT1\n%i\n%i\n", num_visclusters, num_visportals);
    for (int k = 0; k < nlist; k++) {
        portal_t *p = list[k];
        winding_t *w = p->winding;
        vec3_t normal;
        vec_t dist;
        WindingPlane(w, normal, &dist);
        if (DotProduct(p->plane.normal, normal) < 0.99)
            fprintf(pf, "%i %i %i ", w->numpoints, p->nodes[1]->cluster, p->nodes[0]->cluster);
        else
            fprintf(pf, "%i %i %i ", w->numpoints, p->nodes[0]->cluster, p->nodes[1]->cluster);
        for (int i = 0; i < w->numpoints; i++) {
            fprintf(pf, "(");
            WriteFloat(pf, w->p[i][0]);
            WriteFloat(pf, w->p[i][1]);
            WriteFloat(pf, w->p[i][2]);
            fprintf(pf, ") ");
        }
        fprintf(pf, "\n");
    }
    fclose(pf);
    clusterleaf = 1;
    SaveClusters_r(headnode);
    free(leaves);
    free(list);
    free(start);
    Msg("done (0)\n");
}
