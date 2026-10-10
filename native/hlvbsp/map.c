/* Reading the .vmf: entities, brushes (planes from three points, side windings, bevels), texture
 * mapping (texinfo / texdata) and the material table the Python side wrote for us. */
#include <ctype.h>
#include "hlvbsp.h"
#include "disp.h"

plane_t *mapplanes;
int nummapplanes;
static plane_t *planehash[PLANE_HASHES];

mapbrush_t *mapbrushes;
int nummapbrushes, max_mapbrushes;
side_t *brushsides;
static int *noshadow_ids, num_noshadow_ids;     /* info_no_dynamic_shadow sides */
brush_texture_t *side_textures;
int nummapbrushsides;
entity_t *entities;
int num_entities, max_entities;
vec3_t map_mins, map_maxs;
material_t *materials;
int nummaterials;
texinfo_t *texinfos;
int numtexinfo, max_texinfo;
texdata_t *texdatas;
int numtexdata, max_texdata;
char *texdata_strings;
int texdata_strings_len;
int *texdata_string_table;
int numtexdata_strings;
int map_revision;
int g_cliptexinfo = -1;
int c_areaportals;

/* ------------------------------------------------------------------ planes */
int PlaneTypeForNormal(const vec3_t normal) {
    if (normal[0] == 1.0 || normal[0] == -1.0) return PLANE_X;
    if (normal[1] == 1.0 || normal[1] == -1.0) return PLANE_Y;
    if (normal[2] == 1.0 || normal[2] == -1.0) return PLANE_Z;
    vec_t ax = fabsf(normal[0]), ay = fabsf(normal[1]), az = fabsf(normal[2]);
    if (ax >= ay && ax >= az) return PLANE_ANYX;
    if (ay >= ax && ay >= az) return PLANE_ANYY;
    return PLANE_ANYZ;
}

int PlaneEqual(const plane_t *p, const vec3_t normal, vec_t dist, float nep, float dep) {
    return fabsf(p->normal[0] - normal[0]) < nep && fabsf(p->normal[1] - normal[1]) < nep &&
           fabsf(p->normal[2] - normal[2]) < nep && fabsf(p->dist - dist) < dep;
}

static void AddPlaneToHash(plane_t *p) {
    int hash = ((int)fabsf(p->dist) / 8) & (PLANE_HASHES - 1);
    p->hash_chain = planehash[hash];
    planehash[hash] = p;
}

/* Planes come in pairs (n, d) and (-n, -d); an axial pair keeps the positive-facing one first. */
static int CreateNewFloatPlane(const vec3_t normal, vec_t dist) {
    if (getenv("HLVBSP_DEBUG_PLANES")) printf("PLANE %d: %.17g %.17g %.17g %.17g\n", nummapplanes, normal[0], normal[1], normal[2], dist);
    if (VectorLength(normal) < 0.5) Error("FloatPlane: bad normal");
    if (nummapplanes + 2 > MAX_MAP_PLANES) Error("MAX_MAP_PLANES");
    plane_t *p = &mapplanes[nummapplanes];
    VectorCopy(normal, p->normal);
    p->dist = dist;
    p->type = (p + 1)->type = PlaneTypeForNormal(p->normal);
    VectorFromOrigin(normal, (p + 1)->normal);
    (p + 1)->dist = -dist;
    nummapplanes += 2;
    if (p->type < 3 && (p->normal[0] < 0 || p->normal[1] < 0 || p->normal[2] < 0)) {
        plane_t temp = *p;
        *p = *(p + 1);
        *(p + 1) = temp;
        AddPlaneToHash(p);
        AddPlaneToHash(p + 1);
        return nummapplanes - 1;
    }
    AddPlaneToHash(p);
    AddPlaneToHash(p + 1);
    return nummapplanes - 2;
}

static int SnapVector(vec3_t normal) {
    for (int i = 0; i < 3; i++) {
        if (fabsf(normal[i] - 1) < RENDER_NORMAL_EPSILON) {
            VectorClear(normal);
            normal[i] = 1;
            return 1;
        }
        if (fabsf(normal[i] - -1) < RENDER_NORMAL_EPSILON) {
            VectorClear(normal);
            normal[i] = -1;
            return 1;
        }
    }
    return 0;
}

static void SnapPlane(vec3_t normal, vec_t *dist) {
    SnapVector(normal);
    if (fabsf(*dist - RoundInt(*dist)) < RENDER_DIST_EPSILON) *dist = RoundInt(*dist);
}

int FindFloatPlane(vec3_t normal, vec_t dist) {
    SnapPlane(normal, &dist);
    int hash = ((int)fabsf(dist) / 8) & (PLANE_HASHES - 1);
    for (int i = -1; i <= 1; i++) {        /* the neighbouring bins too */
        int h = (hash + i) & (PLANE_HASHES - 1);
        for (plane_t *p = planehash[h]; p; p = p->hash_chain)
            if (PlaneEqual(p, normal, dist, RENDER_NORMAL_EPSILON, RENDER_DIST_EPSILON)) return (int)(p - mapplanes);
    }
    return CreateNewFloatPlane(normal, dist);
}

static int PlaneFromPoints(const vec3_t p0, const vec3_t p1, const vec3_t p2) {
    vec3_t t1, t2, normal;
    VectorSubtract(p0, p1, t1);
    VectorSubtract(p2, p1, t2);
    CrossProduct(t1, t2, normal);
    VectorNormalizeX87(normal);
    vec_t dist = DotProduct(p0, normal);
    if (SnapVector(normal)) {
        /* re-derive the distance through the centre of the three points */
        vec3_t p3;     /* (the game's vector divide multiplies by the reciprocal) */
        float third = 1.0f / 3.0f;
        for (int k = 0; k < 3; k++) p3[k] = (p0[k] + p1[k] + p2[k]) * third;
        dist = DotProduct(normal, p3);
        if (getenv("HLVBSP_DEBUG_PLANES")) printf("  snap p3 %.17g %.17g %.17g -> %.17g (p0 %.17g %.17g %.17g)\n", p3[0], p3[1], p3[2], dist, p0[0], p0[1], p0[2]);
    }
    if (fabsf(dist - RoundInt(dist)) < RENDER_DIST_EPSILON) dist = RoundInt(dist);
    return FindFloatPlane(normal, dist);
}

/* ------------------------------------------------------------------ entities */
const char *ValueForKey(const entity_t *e, const char *key) {
    for (epair_t *ep = e->epairs; ep; ep = ep->next)
        if (!_stricmp(ep->key, key)) return ep->value;
    return "";
}

/* New keys go to the front of the list (the BSP lists them newest first). */
void SetKeyValue(entity_t *e, const char *key, const char *value) {
    for (epair_t *ep = e->epairs; ep; ep = ep->next)
        if (!_stricmp(ep->key, key)) {
            free(ep->value);
            ep->value = copystring(value);
            return;
        }
    epair_t *ep = xalloc(sizeof(*ep));
    ep->next = e->epairs;
    e->epairs = ep;
    ep->key = copystring(key);
    ep->value = copystring(value);
}

void GetVectorForKey(const entity_t *e, const char *key, vec3_t v) {
    double a = 0, b = 0, c = 0;
    sscanf(ValueForKey(e, key), "%lf %lf %lf", &a, &b, &c);
    v[0] = a;
    v[1] = b;
    v[2] = c;
}

static entity_t *EntityByName(const char *name) {
    for (int i = 0; i < num_entities; i++)
        if (!_stricmp(ValueForKey(&entities[i], "targetname"), name)) return &entities[i];
    return NULL;
}

/* ------------------------------------------------------------------ materials */
static int material_hash[4096];
static unsigned name_hash(const char *s) {
    unsigned h = 5381;
    for (; *s; s++) h = h * 33 + (unsigned)tolower((unsigned char)*s);
    return h & 4095;
}
static int *material_next;

/* Lines of: name \t contents \t flags \t width \t height \t r \t g \t b \t surfaceprop \t found \t surfaceprop2
 * (a surface property name of "-" means none) */
void LoadMaterials(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) Error("can't open the material table %s", path);
    int cap = 256;
    materials = xalloc(sizeof(material_t) * cap);
    material_next = xalloc(sizeof(int) * cap);
    for (int i = 0; i < 4096; i++) material_hash[i] = -1;
    char line[1024];
    while (fgets(line, sizeof(line), f)) {
        char *tab = strchr(line, '\t');
        if (!tab) continue;
        *tab = 0;
        if (nummaterials == cap) {
            cap *= 2;
            materials = realloc(materials, sizeof(material_t) * cap);
            material_next = realloc(material_next, sizeof(int) * cap);
        }
        material_t *m = &materials[nummaterials];
        memset(m, 0, sizeof(*m));
        strncpy(m->name, line, sizeof(m->name) - 1);
        if (sscanf(tab + 1, "%i %i %i %i %f %f %f %63s %i %63s %127s %255s", &m->contents, &m->flags, &m->width, &m->height,
                   &m->reflectivity[0], &m->reflectivity[1], &m->reflectivity[2], m->surfaceprop, &m->found,
                   m->surfaceprop2, m->detailtype, m->bottommaterial) < 9)
            m->found = 1;
        if (!strcmp(m->detailtype, "-")) m->detailtype[0] = 0;
        if (!strcmp(m->bottommaterial, "-")) m->bottommaterial[0] = 0;
        if (!strcmp(m->surfaceprop, "-")) m->surfaceprop[0] = 0;
        if (!strcmp(m->surfaceprop2, "-")) m->surfaceprop2[0] = 0;
        unsigned h = name_hash(m->name);
        material_next[nummaterials] = material_hash[h];
        material_hash[h] = nummaterials;
        nummaterials++;
    }
    fclose(f);
}

static int LookupMaterial(const char *name) {
    for (int i = material_hash[name_hash(name)]; i >= 0; i = material_next[i])
        if (!_stricmp(materials[i].name, name)) return i;
    return -1;
}

/* Like vbsp's texture references: one per exact name; the game data comes from the table. */
typedef struct { char *name; int material; } textureref_t;
static textureref_t *texrefs;
static int numtexrefs, max_texrefs;

int FindMaterial(const char *name_) {
    char name[256];
    strncpy(name, name_, sizeof(name) - 1);
    name[sizeof(name) - 1] = 0;
    for (int i = 0; i < numtexrefs; i++)
        if (!strcmp(texrefs[i].name, name)) return i;
    if (numtexrefs == max_texrefs) {
        max_texrefs = max_texrefs ? max_texrefs * 2 : 256;
        texrefs = realloc(texrefs, sizeof(textureref_t) * max_texrefs);
    }
    int m = LookupMaterial(name);
    if (m < 0) {
        Warning("Material not found!: %s\n", name);
        if (nummaterials % 256 == 0) {
            materials = realloc(materials, sizeof(material_t) * (nummaterials + 256));
            material_next = realloc(material_next, sizeof(int) * (nummaterials + 256));
        }
        m = nummaterials++;
        memset(&materials[m], 0, sizeof(material_t));
        strncpy(materials[m].name, name, sizeof(materials[m].name) - 1);
        material_next[m] = -1;
    }
    texrefs[numtexrefs].name = copystring(name);
    texrefs[numtexrefs].material = m;
    if (materials[m].contents & (CONTENTS_WATER | CONTENTS_SLIME)) g_has_water = 1;
    return numtexrefs++;
}

static const char *TexrefName(int texref) { return texrefs[texref].name; }

/* ------------------------------------------------------------------ texdata / texinfo */
int TexDataString(const char *s) {
    for (int i = 0; i < numtexdata_strings; i++)
        if (!_stricmp(s, texdata_strings + texdata_string_table[i])) return i;
    int len = (int)strlen(s);
    texdata_strings = realloc(texdata_strings, texdata_strings_len + len + 1);
    memcpy(texdata_strings + texdata_strings_len, s, len + 1);
    texdata_string_table = realloc(texdata_string_table, sizeof(int) * (numtexdata_strings + 1));
    texdata_string_table[numtexdata_strings] = texdata_strings_len;
    texdata_strings_len += len + 1;
    return numtexdata_strings++;
}

int FindOrCreateTexData(int texref) {
    const char *name = TexrefName(texref);
    for (int i = 0; i < numtexdata; i++)
        if (!_stricmp(texdata_strings + texdata_string_table[texdatas[i].name_id], name)) return i;
    if (numtexdata == max_texdata) {
        max_texdata = max_texdata ? max_texdata * 2 : 256;
        texdatas = realloc(texdatas, sizeof(texdata_t) * max_texdata);
    }
    texdata_t *td = &texdatas[numtexdata];
    memset(td, 0, sizeof(*td));
    td->name_id = TexDataString(name);
    material_t *m = &materials[texrefs[texref].material];
    if (m->found) {
        td->width = td->view_width = m->width;
        td->height = td->view_height = m->height;
        memcpy(td->reflectivity, m->reflectivity, sizeof(td->reflectivity));
    }
    return numtexdata++;
}

int MaterialSurfaceProp(int texdata);

/* texdatas copied from another (vbsp's AddCloneTexData / FindAliasedTexData): source + 1, 0 = none */
static int *texdata_source, texdata_source_cap;

static void SetTexDataSource(int texdata, int source) {
    if (texdata >= texdata_source_cap) {
        int n = texdata + 256;
        texdata_source = realloc(texdata_source, sizeof(int) * n);
        memset(texdata_source + texdata_source_cap, 0, sizeof(int) * (n - texdata_source_cap));
        texdata_source_cap = n;
    }
    texdata_source[texdata] = source + 1;
}

/* The texdata of the material a copied texdata stands for (vbsp's GetOriginalMaterialNameForPatchedMaterial). */
int OriginalTexData(int texdata) {
    for (int guard = 0; guard < 16 && texdata < texdata_source_cap && texdata_source[texdata]; guard++)
        texdata = texdata_source[texdata] - 1;
    return texdata;
}

const char *MaterialDetailType(int texdata) {
    texdata = OriginalTexData(texdata);
    const char *name = texdata_strings + texdata_string_table[texdatas[texdata].name_id];
    int m = LookupMaterial(name);
    return m >= 0 ? materials[m].detailtype : "";
}

const char *MaterialSurfacePropName(int texdata) {
    texdata = OriginalTexData(texdata);
    const char *name = texdata_strings + texdata_string_table[texdatas[texdata].name_id];
    int m = LookupMaterial(name);
    return m >= 0 ? materials[m].surfaceprop : "";
}

/* vbsp's g_SurfaceProperties[texdata]: vphysics' index when it's loaded, else a stand-in per name. */
int SurfacePropIndex(int texdata);
int texdata_surfaceprop(int texdata) {
    extern void *physprops_loaded(void);
    if (physprops_loaded()) return SurfacePropIndex(texdata);
    const char *p = MaterialSurfacePropName(texdata);
    if (!p[0]) return -1;
    unsigned h = 5381;
    for (; *p; p++) h = h * 33 + (unsigned char)*p;
    return (int)(h & 0x7FFFFFFF);
}

int AppendTexinfo(const texinfo_t *t) {
    if (numtexinfo == max_texinfo) {
        max_texinfo = max_texinfo ? max_texinfo * 2 : 256;
        texinfos = realloc(texinfos, sizeof(texinfo_t) * max_texinfo);
    }
    texinfos[numtexinfo] = *t;
    return numtexinfo++;
}

static int FindOrCreateTexInfo(const texinfo_t *t) {
    for (int i = 0; i < numtexinfo; i++) {
        if (texinfos[i].texdata != t->texdata) continue;
        if (!memcmp(&texinfos[i], t, sizeof(texinfo_t))) return i;
    }
    if (numtexinfo == max_texinfo) {
        max_texinfo = max_texinfo ? max_texinfo * 2 : 256;
        texinfos = realloc(texinfos, sizeof(texinfo_t) * max_texinfo);
    }
    texinfos[numtexinfo] = *t;
    return numtexinfo++;
}

static int strstr_i(const char *h, const char *n) {
    size_t ln = strlen(n);
    for (; *h; h++)
        if (!_strnicmp(h, n, ln)) return 1;
    return 0;
}

int FindOrCreateTexInfoPublic(const texinfo_t *t) { return FindOrCreateTexInfo(t); }
const char *TexDataName(int texdata) { return texdata_strings + texdata_string_table[texdatas[texdata].name_id]; }

/* A texdata under another name with the settings of `source` (vbsp's FindAliasedTexData, for the
   one-off water depth materials). */
int AliasedTexData(const char *name, int source) {
    for (int i = 0; i < numtexdata; i++)
        if (!strcmp(TexDataName(i), name)) return i;
    if (numtexdata == max_texdata) {
        max_texdata = max_texdata ? max_texdata * 2 : 256;
        texdatas = realloc(texdatas, sizeof(texdata_t) * max_texdata);
    }
    texdata_t *td = &texdatas[numtexdata];
    *td = texdatas[source];
    td->name_id = TexDataString(name);
    SetTexDataSource(numtexdata, source);
    return numtexdata++;
}

/* vbsp's AddCloneTexData: a copy of `source` under `name` (no search). */
int CloneTexData(int source, const char *name) {
    if (numtexdata == max_texdata) {
        max_texdata = max_texdata ? max_texdata * 2 : 256;
        texdatas = realloc(texdatas, sizeof(texdata_t) * max_texdata);
    }
    texdatas[numtexdata] = texdatas[source];
    texdatas[numtexdata].name_id = TexDataString(name);
    SetTexDataSource(numtexdata, source);
    return numtexdata++;
}

/* vbsp's FindTexData: by name, any case; -1 = none. */
int FindTexDataByName(const char *name) {
    for (int i = 0; i < numtexdata; i++)
        if (!_stricmp(TexDataName(i), name)) return i;
    return -1;
}

int FindTexInfoExact(const texinfo_t *t) {
    for (int i = 0; i < numtexinfo; i++)
        if (texinfos[i].texdata == t->texdata && !memcmp(&texinfos[i], t, sizeof(texinfo_t))) return i;
    return -1;
}

/* The $bottommaterial of a texinfo's material ("" = none). */
const char *BottomMaterial(int texinfo) {
    extern const char *Cubemap_PatchedValue(int texdata, const char *key);
    const char *patched = Cubemap_PatchedValue(texinfos[texinfo].texdata, "$bottommaterial");
    if (patched) return patched;
    int m = LookupMaterial(TexDataName(OriginalTexData(texinfos[texinfo].texdata)));
    return m >= 0 ? materials[m].bottommaterial : "";
}

/* A water face seen from below: the same mapping with the bottom material (vbsp's
   AssignBottomWaterMaterialToFace). 0 = the material has none (the face is dropped). */
int BottomWaterTexinfo(int texinfo) {
    const char *bottom = BottomMaterial(texinfo);
    if (!bottom[0]) {
        const char *name = TexDataName(texinfos[texinfo].texdata);
        if (!strstr_i(name, "nodraw") && !strstr_i(name, "toolsskip"))
            Warning("error: material %s doesn't have a $bottommaterial\n", name);
        return -1;
    }
    texinfo_t t = texinfos[texinfo];
    t.texdata = FindOrCreateTexData(FindMaterial(bottom));
    return FindOrCreateTexInfo(&t);
}

/* An overlay's texinfo: no axes, offsets of -99999 (vbsp's marker), its material's texdata. */
int OverlayTexinfo(const char *material) {
    texinfo_t t;
    memset(&t, 0, sizeof(t));
    for (int i = 0; i < 2; i++) t.vecs[i][3] = t.lmvecs[i][3] = -99999.0f;
    t.texdata = FindOrCreateTexData(FindMaterial(material));
    return FindOrCreateTexInfo(&t);
}

/* Texture axes from the side's u/v axes and scales; lightmap axes from its lightmap scale. */
int TexinfoForBrushTexture(plane_t *plane, brush_texture_t *bt, const vec3_t origin) {
    (void)plane;
    if (bt->material < 0) return 0;
    texinfo_t tx;
    memset(&tx, 0, sizeof(tx));
    if (!bt->scale[0]) bt->scale[0] = 1;
    if (!bt->scale[1]) bt->scale[1] = 1;
    for (int k = 0; k < 3; k++) {
        /* (times the reciprocal, as the game's vector divide does - measured) */
        tx.vecs[0][k] = bt->uaxis[k] * (1.0f / bt->scale[0]);
        tx.vecs[1][k] = bt->vaxis[k] * (1.0f / bt->scale[1]);
        tx.lmvecs[0][k] = bt->uaxis[k] / bt->lightmap_scale;
        tx.lmvecs[1][k] = bt->vaxis[k] / bt->lightmap_scale;
    }
    float shift_u = bt->scale[0] / bt->lightmap_scale;
    float shift_v = bt->scale[1] / bt->lightmap_scale;
    tx.vecs[0][3] = bt->shift[0] + DotProduct(origin, tx.vecs[0]);
    tx.vecs[1][3] = bt->shift[1] + DotProduct(origin, tx.vecs[1]);
    tx.lmvecs[0][3] = shift_u * bt->shift[0] + DotProduct(origin, tx.lmvecs[0]);
    tx.lmvecs[1][3] = shift_v * bt->shift[1] + DotProduct(origin, tx.lmvecs[1]);
    tx.flags = bt->flags;
    tx.texdata = FindOrCreateTexData(bt->material);
    return FindOrCreateTexInfo(&tx);
}

/* ------------------------------------------------------------------ brushes */
static int BrushContents(mapbrush_t *b) {
    side_t *s = &b->original_sides[0];
    int contents = s->contents, all = contents;
    for (int i = 1; i < b->numsides; i++) all |= b->original_sides[i].contents;
    /* see-through contents anywhere make the whole brush see-through (and not solid) */
    int trans = all & (CONTENTS_WINDOW | CONTENTS_GRATE | CONTENTS_WATER | CONTENTS_SLIME);
    if (trans) {
        contents |= trans | CONTENTS_TRANSLUCENT;
        contents &= ~CONTENTS_SOLID;
    }
    if (all & CONTENTS_LADDER) contents |= CONTENTS_LADDER;     /* (L4D2: a ladder side makes a ladder brush) */
    return contents;
}

/* Each side's polygon: its plane's huge square cut by every other (non-bevel) side. */
static void MakeBrushWindings(mapbrush_t *ob) {
    ClearBounds(ob->mins, ob->maxs);
    for (int i = 0; i < ob->numsides; i++) {
        plane_t *plane = &mapplanes[ob->original_sides[i].planenum];
        winding_t *w = BaseWindingForPlane(plane->normal, plane->dist);
        for (int j = 0; j < ob->numsides && w; j++) {
            if (i == j || ob->original_sides[j].bevel) continue;
            plane = &mapplanes[ob->original_sides[j].planenum ^ 1];
            ChopWindingInPlace(&w, plane->normal, plane->dist, BRUSH_CLIP_EPSILON);
        }
        side_t *side = &ob->original_sides[i];
        if (side->winding) FreeWinding(side->winding);
        side->winding = w;
        if (w) {
            side->visible = 1;
            for (int j = 0; j < w->numpoints; j++) AddPointToBounds(w->p[j], ob->mins, ob->maxs);
        }
    }
    for (int i = 0; i < 3; i++) {
        if (ob->mins[i] < MIN_COORD_INTEGER || ob->maxs[i] > MAX_COORD_INTEGER)
            Msg("Brush %i: bounds out of range\n", ob->id);
        if (ob->mins[i] > MAX_COORD_INTEGER || ob->maxs[i] < MIN_COORD_INTEGER)
            Msg("Brush %i: no visible sides on brush\n", ob->id);
    }
}

static void ensure_sides(int extra) {
    /* brushsides is one big array the brushes point into: it must never move */
    if (nummapbrushsides + extra > MAX_MAP_BRUSHSIDES) Error("MAX_MAP_BRUSHSIDES");
}

/* Axial planes in a fixed order (-x +x -y +y -z +z) first, then bevels on non-axial edges, so
 * boxes swept against the brush (player collision) stop at its real outline. */
static void AddBrushBevels(mapbrush_t *b) {
    int order = 0;
    vec3_t normal;
    vec_t dist;
    for (int axis = 0; axis < 3; axis++) {
        for (int dir = -1; dir <= 1; dir += 2, order++) {
            int i;
            side_t *s;
            for (i = 0, s = b->original_sides; i < b->numsides; i++, s++)
                if (mapplanes[s->planenum].normal[axis] == dir) break;
            if (i == b->numsides) {
                ensure_sides(1);
                nummapbrushsides++;
                b->numsides++;
                VectorClear(normal);
                normal[axis] = dir;
                dist = dir == 1 ? b->maxs[axis] : -b->mins[axis];
                memset(s, 0, sizeof(*s));
                s->planenum = FindFloatPlane(normal, dist);
                s->texinfo = b->original_sides[0].texinfo;
                s->contents = b->original_sides[0].contents;
                s->bevel = 1;
                side_textures[s - brushsides].material = -1;    /* (no texture: texinfo 0 if re-made) */
            }
            if (i != order) {
                side_t t = b->original_sides[order];
                b->original_sides[order] = b->original_sides[i];
                b->original_sides[i] = t;
                int j = (int)(b->original_sides - brushsides);
                brush_texture_t tt = side_textures[j + order];
                side_textures[j + order] = side_textures[j + i];
                side_textures[j + i] = tt;
            }
        }
    }
    if (b->numsides == 6) return;     /* a box */
    for (int i = 6; i < b->numsides; i++) {
        side_t *s = b->original_sides + i;
        winding_t *w = s->winding;
        if (!w) continue;
        for (int j = 0; j < w->numpoints; j++) {
            int k = (j + 1) % w->numpoints;
            vec3_t vec, vec2;
            VectorSubtract(w->p[j], w->p[k], vec);
            if (VectorNormalizeX87(vec) < 0.5) continue;
            SnapVector(vec);
            for (k = 0; k < 3; k++)
                if (vec[k] == -1 || vec[k] == 1) break;
            if (k != 3) continue;         /* only non-axial edges */
            for (int axis = 0; axis < 3; axis++) {
                for (int dir = -1; dir <= 1; dir += 2) {
                    VectorClear(vec2);
                    vec2[axis] = dir;
                    CrossProduct(vec, vec2, normal);
                    if (VectorNormalizeX87(normal) < 0.5) continue;
                    dist = DotProduct(w->p[j], normal);
                    /* a bevel only if every point of the brush is behind it */
                    for (k = 0; k < b->numsides; k++) {
                        if (PlaneEqual(&mapplanes[b->original_sides[k].planenum], normal, dist, 0.01f, 0.01f)) break;
                        winding_t *w2 = b->original_sides[k].winding;
                        if (!w2) continue;
                        int l;
                        for (l = 0; l < w2->numpoints; l++) {
                            vec_t d = DotProduct(w2->p[l], normal) - dist;
                            if (d > 0.1) break;
                        }
                        if (l != w2->numpoints) break;
                    }
                    if (k != b->numsides) continue;
                    ensure_sides(1);
                    nummapbrushsides++;
                    side_t *s2 = &b->original_sides[b->numsides];
                    memset(s2, 0, sizeof(*s2));
                    s2->planenum = FindFloatPlane(normal, dist);
                    s2->texinfo = b->original_sides[0].texinfo;
                    s2->contents = b->original_sides[0].contents;
                    s2->bevel = 1;
                    side_textures[s2 - brushsides].material = -1;
                    b->numsides++;
                }
            }
        }
    }
}

/* ------------------------------------------------------------------ the VMF reader */
typedef struct {
    char *text, *at;
    char token[4096];
    int quoted;
} parser_t;

static int next_token(parser_t *p) {
    char *s = p->at;
    for (;;) {
        while (*s && isspace((unsigned char)*s)) s++;
        if (s[0] == '/' && s[1] == '/') {
            while (*s && *s != '\n') s++;
            continue;
        }
        break;
    }
    if (!*s) {
        p->at = s;
        return 0;
    }
    int n = 0;
    p->quoted = 0;
    if (*s == '"') {
        p->quoted = 1;
        s++;
        while (*s && *s != '"') {
            if (n < (int)sizeof(p->token) - 1) p->token[n++] = *s;
            s++;
        }
        if (*s == '"') s++;
    } else if (*s == '{' || *s == '}') {
        p->token[n++] = *s++;
    } else {
        while (*s && !isspace((unsigned char)*s) && *s != '{' && *s != '}' && *s != '"') {
            if (n < (int)sizeof(p->token) - 1) p->token[n++] = *s;
            s++;
        }
    }
    p->token[n] = 0;
    p->at = s;
    return 1;
}

static void skip_block(parser_t *p) {
    int depth = 1;
    while (depth && next_token(p)) {
        if (!p->quoted && !strcmp(p->token, "{")) depth++;
        else if (!p->quoted && !strcmp(p->token, "}")) depth--;
    }
}

typedef struct {
    entity_t *ent;
    int base_contents, base_flags;
} loadent_t;

/* A side's displacement: power, start, and per-vertex rows (normals, distances, offsets, alphas, tags). */
static void load_dispinfo(parser_t *p, side_t *side) {
    mapdisp_t *md = NewMapDisp();
    side->disp = nummapdisps;
    char key[4096], block[64];
    while (next_token(p)) {
        if (!p->quoted && !strcmp(p->token, "}")) break;
        strcpy(key, p->token);
        if (!next_token(p)) break;
        if (!p->quoted && !strcmp(p->token, "{")) {
            strncpy(block, key, sizeof(block) - 1);
            block[sizeof(block) - 1] = 0;
            int cols = (1 << md->power) + 1;
            char k2[4096];
            while (next_token(p)) {
                if (!p->quoted && !strcmp(p->token, "}")) break;
                strcpy(k2, p->token);
                if (!next_token(p)) break;
                if (!p->quoted && !strcmp(p->token, "{")) { skip_block(p); continue; }
                if (_strnicmp(k2, "row", 3)) continue;
                if (!_stricmp(block, "normals")) ParseDispRow(k2, p->token, &md->normals[0][0], cols, 3);
                else if (!_stricmp(block, "distances")) ParseDispRow(k2, p->token, md->dists, cols, 1);
                else if (!_stricmp(block, "offsets")) ParseDispRow(k2, p->token, &md->offsets[0][0], cols, 3);
                else if (!_stricmp(block, "alphas")) ParseDispRow(k2, p->token, md->alphas, cols, 1);
                else if (!_stricmp(block, "triangle_tags")) ParseDispTriTags(k2, p->token, md);
            }
            continue;
        }
        const char *v = p->token;
        if (!_stricmp(key, "power")) md->power = atoi(v);
        else if (!_stricmp(key, "startposition")) {
            double a = 0, b = 0, c = 0;
            sscanf(v, "[%lf %lf %lf]", &a, &b, &c);
            md->startpos[0] = (float)a; md->startpos[1] = (float)b; md->startpos[2] = (float)c;
        } else if (!_stricmp(key, "flags")) md->flags = atoi(v);
        else if (!_stricmp(key, "mintess")) md->mintess = atoi(v);
        else if (!_stricmp(key, "smooth")) md->smooth = (float)atof(v);
    }
}

/* A brush side: plane points, material, axes, lightmap scale. */
static void load_side(parser_t *p, mapbrush_t *b, loadent_t *le, int *side_index) {
    ensure_sides(1);
    side_t *side = &brushsides[nummapbrushsides];
    memset(side, 0, sizeof(*side));
    brush_texture_t td;
    memset(&td, 0, sizeof(td));
    td.material = -1;
    vec3_t pts[3] = {{0}};
    int have_plane = 0;
    (*side_index)++;
    char key[4096];
    while (next_token(p)) {
        if (!p->quoted && !strcmp(p->token, "}")) break;
        strcpy(key, p->token);
        if (!next_token(p)) break;
        if (!p->quoted && !strcmp(p->token, "{")) {
            if (!_stricmp(key, "dispinfo")) load_dispinfo(p, side);
            else skip_block(p);
            continue;
        }
        const char *v = p->token;
        if (!_stricmp(key, "plane")) {
            if (sscanf(v, "(%f %f %f) (%f %f %f) (%f %f %f)", &pts[0][0], &pts[0][1], &pts[0][2], &pts[1][0],
                       &pts[1][1], &pts[1][2], &pts[2][0], &pts[2][1], &pts[2][2]) != 9)
                Error("parsing plane definition (brush %i)", b->id);
            have_plane = 1;
        } else if (!_stricmp(key, "material")) {
            int t = FindMaterial(v);
            material_t *m = &materials[texrefs[t].material];
            td.material = t;
            td.flags = m->flags;
            side->contents = m->contents;
            side->surf = td.flags;
            side->material = t;
        } else if (!_stricmp(key, "uaxis")) {
            if (sscanf(v, "[%f %f %f %f] %f", &td.uaxis[0], &td.uaxis[1], &td.uaxis[2], &td.shift[0], &td.scale[0]) != 5)
                Error("parsing U axis definition");
        } else if (!_stricmp(key, "vaxis")) {
            if (sscanf(v, "[%f %f %f %f] %f", &td.vaxis[0], &td.vaxis[1], &td.vaxis[2], &td.shift[1], &td.scale[1]) != 5)
                Error("parsing V axis definition");
        } else if (!_stricmp(key, "lightmapscale")) {
            td.lightmap_scale = (float)atoi(v);
            if (td.lightmap_scale == 0.0f) {
                Warning("luxel size of 0\n");
                td.lightmap_scale = 16;
            }
            if (td.lightmap_scale < 1.0f) td.lightmap_scale = 1.0f;
        } else if (!_stricmp(key, "contents")) {
            side->contents |= atoi(v);
        } else if (!_stricmp(key, "flags")) {
            td.flags |= atoi(v);
            side->surf = td.flags;
        } else if (!_stricmp(key, "id")) {
            side->id = atoi(v);
        } else if (!_stricmp(key, "smoothing_groups")) {
            side->smoothing = (unsigned)atoi(v);
        }
    }
    side->contents |= le->base_contents;
    side->surf |= le->base_flags;
    td.flags |= le->base_flags;
    if (side->contents & (CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP)) side->contents |= CONTENTS_DETAIL;
    if (!(side->contents & (ALL_VISIBLE_CONTENTS | CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP)))
        side->contents |= CONTENTS_SOLID;
    if (side->surf & (SURF_HINT | SURF_SKIP)) side->contents = 0;    /* hints and skips have no contents */
    if (!have_plane) return;
    int planenum = PlaneFromPoints(pts[0], pts[1], pts[2]);
    int k;
    for (k = 0; k < b->numsides; k++) {
        side_t *s2 = b->original_sides + k;
        if (s2->planenum == planenum) {
            Warning("Brush %i: duplicate plane\n", b->id);
            break;
        }
        if (s2->planenum == (planenum ^ 1)) {
            Warning("Brush %i: mirrored plane\n", b->id);
            break;
        }
    }
    if (k != b->numsides) return;
    side_t *dst = b->original_sides + b->numsides;
    if (dst != side) *dst = *side;
    dst->planenum = planenum;
    dst->texinfo = TexinfoForBrushTexture(&mapplanes[planenum], &td, (vec3_t){0, 0, 0});
    side_textures[nummapbrushsides] = td;
    nummapbrushsides++;
    b->numsides++;
}

static void MoveBrushesToWorld(entity_t *mapent) {
    int newbrushes = mapent->numbrushes, worldbrushes = entities[0].numbrushes;
    mapbrush_t *temp = xalloc(sizeof(mapbrush_t) * (newbrushes ? newbrushes : 1));
    memcpy(temp, mapbrushes + mapent->firstbrush, newbrushes * sizeof(mapbrush_t));
    memmove(mapbrushes + worldbrushes + newbrushes, mapbrushes + worldbrushes,
            sizeof(mapbrush_t) * (nummapbrushes - worldbrushes - newbrushes));
    memcpy(mapbrushes + worldbrushes, temp, sizeof(mapbrush_t) * newbrushes);
    entities[0].numbrushes += newbrushes;
    for (int i = 1; i < num_entities; i++) entities[i].firstbrush += newbrushes;
    mapent->numbrushes = 0;
    free(temp);
}

static void load_solid(parser_t *p, loadent_t *le) {
    if (nummapbrushes == max_mapbrushes) {
        max_mapbrushes = max_mapbrushes ? max_mapbrushes * 2 : 1024;
        mapbrushes = realloc(mapbrushes, sizeof(mapbrush_t) * max_mapbrushes);
    }
    mapbrush_t *b = &mapbrushes[nummapbrushes];
    memset(b, 0, sizeof(*b));
    b->original_sides = &brushsides[nummapbrushsides];
    b->entitynum = num_entities - 1;
    b->brushnum = nummapbrushes - le->ent->firstbrush;
    int side_index = 0;
    char key[4096];
    while (next_token(p)) {
        if (!p->quoted && !strcmp(p->token, "}")) break;
        strcpy(key, p->token);
        if (!next_token(p)) break;
        if (!p->quoted && !strcmp(p->token, "{")) {
            if (!_stricmp(key, "side")) load_side(p, b, le, &side_index);
            else skip_block(p);
            continue;
        }
        if (!_stricmp(key, "id")) b->id = atoi(p->token);
    }
    if (!b->numsides) return;
    b->contents = BrushContents(b);
    MakeBrushWindings(b);
    /* world clip brushes never split the tree */
    if (b->entitynum == 0 && (b->contents & (CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP))) {
        if (g_cliptexinfo < 0) g_cliptexinfo = b->original_sides[0].texinfo;
        for (int i = 0; i < b->numsides; i++) b->original_sides[i].texinfo = TEXINFO_NODE;
    }
    if (b->contents & CONTENTS_ORIGIN) {
        /* an origin brush sets its entity's origin and is dropped */
        if (num_entities == 1) Error("Brush %i: origin brushes not allowed in world", b->id);
        vec3_t origin;
        char string[64];
        VectorAdd(b->mins, b->maxs, origin);
        VectorScale(origin, 0.5, origin);
        sprintf(string, "%i %i %i", (int)origin[0], (int)origin[1], (int)origin[2]);
        SetKeyValue(&entities[b->entitynum], "origin", string);
        VectorCopy(origin, entities[b->entitynum].origin);
        b->numsides = 0;
        return;
    }
    int hasdisp = 0;
    for (int i = 0; i < b->numsides; i++)
        if (b->original_sides[i].disp) hasdisp = 1;
    if (hasdisp) {
        /* the displacement keeps the side's face; the brush itself is dropped */
        if (b->entitynum != 0)
            Error("Error: displacement found on a(n) %s entity - not supported (entity %d, brush %d)",
                  ValueForKey(&entities[b->entitynum], "classname"), b->entitynum, b->brushnum);
        for (int i = 0; i < b->numsides; i++) {
            side_t *s = &b->original_sides[i];
            if (!s->disp) continue;
            if (s->winding->numpoints != 4)
                Error("Trying to create a non-quad displacement! (entity %d, brush %d)", b->entitynum, b->brushnum);
            mapdisp_t *md = &mapdisps[s->disp - 1];
            memset(&md->face, 0, sizeof(md->face));
            md->face.originalface = s;
            md->face.texinfo = s->texinfo;
            md->face.dispinfo = -1;
            md->face.planenum = s->planenum;
            md->face.numpoints = s->winding->numpoints;
            md->face.w = CopyWinding(s->winding);
            md->face.contents = b->contents;
            md->entitynum = b->entitynum;
            md->brushsideid = s->id;
        }
        b->numsides = 0;
        return;
    }
    AddBrushBevels(b);
    nummapbrushes++;
    le->ent->numbrushes++;
}

static int IsAreaPortal(const char *cls) { return !strncmp(cls, "func_areaportal", 15); }

/* L4D2 keeps a ladder as a brush model (func_simpleladder) facing its ladder sides' normal, with their team */
void AddLadderKeys(entity_t *mapent) {
    SetKeyValue(mapent, "team", "0");
    SetKeyValue(mapent, "normal.x", "0");
    SetKeyValue(mapent, "normal.y", "0");
    SetKeyValue(mapent, "normal.z", "1");
    for (int i = 0; i < mapent->numbrushes; i++) {
        mapbrush_t *b = &mapbrushes[mapent->firstbrush + i];
        for (int j = 0; j < b->numsides; j++) {
            const side_t *sd = &b->original_sides[j];
            if (!(sd->contents & CONTENTS_LADDER)) continue;
            if (sd->contents & 0x800) SetKeyValue(mapent, "team", "1");             /* CONTENTS_TEAM1 */
            else if (sd->contents & 0x1000) SetKeyValue(mapent, "team", "2");       /* CONTENTS_TEAM2 */
            const float *n = mapplanes[sd->planenum].normal;
            SetKeyValue(mapent, "normal.x", FmtF(n[0]));
            SetKeyValue(mapent, "normal.y", FmtF(n[1]));
            SetKeyValue(mapent, "normal.z", FmtF(n[2]));
        }
    }
}

/* vbsp's MoveBrushesToWorldGeneral (an instance's world brushes): only entities before this one move along,
 * and its displacements become the world's */
void MoveBrushesToWorldGeneral(entity_t *mapent) {
    int ent = (int)(mapent - entities);
    for (int i = 0; i < nummapdisps; i++)
        if (mapdisps[i].entitynum == ent) mapdisps[i].entitynum = 0;
    int newbrushes = mapent->numbrushes, worldbrushes = entities[0].numbrushes;
    mapbrush_t *temp = xalloc(sizeof(mapbrush_t) * (newbrushes ? newbrushes : 1));
    memcpy(temp, mapbrushes + mapent->firstbrush, newbrushes * sizeof(mapbrush_t));
    memmove(mapbrushes + worldbrushes + newbrushes, mapbrushes + worldbrushes,
            sizeof(mapbrush_t) * (mapent->firstbrush - worldbrushes));
    memcpy(mapbrushes + worldbrushes, temp, sizeof(mapbrush_t) * newbrushes);
    entities[0].numbrushes += newbrushes;
    for (int i = 1; i < num_entities; i++)
        if (entities[i].firstbrush < mapent->firstbrush) entities[i].firstbrush += newbrushes;
    free(temp);
    mapent->numbrushes = 0;
}

/* A new entity at the end of the list (the array grows). */
entity_t *AllocEntity(void) {
    if (num_entities == max_entities) {
        max_entities = max_entities ? max_entities * 2 : 1024;
        entities = realloc(entities, sizeof(entity_t) * max_entities);
    }
    entity_t *e = &entities[num_entities++];
    memset(e, 0, sizeof(*e));
    return e;
}

static void load_entity(parser_t *p) {
    entity_t *mapent = AllocEntity();
    memset(mapent, 0, sizeof(*mapent));
    mapent->firstbrush = nummapbrushes;
    loadent_t le = {mapent, 0, 0};
    char key[4096];
    while (next_token(p)) {
        if (!p->quoted && !strcmp(p->token, "}")) break;
        strcpy(key, p->token);
        if (!next_token(p)) break;
        if (!p->quoted && !strcmp(p->token, "{")) {
            if (!_stricmp(key, "solid")) load_solid(p, &le);
            else if (!_stricmp(key, "overlaytransition")) {
                /* water overlays: an "overlaydata" block each (any entity may carry them; Hammer puts them on
                 * info_overlay_transition) */
                char k2[4096];
                while (next_token(p)) {
                    if (!p->quoted && !strcmp(p->token, "}")) break;
                    strcpy(k2, p->token);
                    if (!next_token(p)) break;
                    if (p->quoted || strcmp(p->token, "{")) continue;
                    if (_stricmp(k2, "overlaydata")) { skip_block(p); continue; }
                    int w = WaterOverlay_New();
                    char k3[4096];
                    while (next_token(p)) {
                        if (!p->quoted && !strcmp(p->token, "}")) break;
                        strcpy(k3, p->token);
                        if (!next_token(p)) break;
                        if (!p->quoted && !strcmp(p->token, "{")) { skip_block(p); continue; }
                        WaterOverlay_Key(w, k3, p->token);
                    }
                }
            }
            else if (!_stricmp(key, "connections")) {
                /* outputs go after the keys read so far (at the list's tail) */
                char k2[4096];
                while (next_token(p)) {
                    if (!p->quoted && !strcmp(p->token, "}")) break;
                    strcpy(k2, p->token);
                    if (!next_token(p)) break;
                    epair_t *ep = xalloc(sizeof(*ep));
                    ep->key = copystring(k2);
                    ep->value = copystring(p->token);
                    ep->connection = 1;
                    if (!mapent->epairs) mapent->epairs = ep;
                    else {
                        epair_t *t = mapent->epairs;
                        while (t->next) t = t->next;
                        t->next = ep;
                    }
                }
            } else skip_block(p);
            continue;
        }
        const char *v = p->token;
        if (!_stricmp(key, "classname")) {
            if (!_stricmp(v, "func_detail")) le.base_contents = CONTENTS_DETAIL;
            /* (L4D2: func_ladder adds no contents - the ladder material does) */
            else if (!_stricmp(v, "func_water")) le.base_contents = CONTENTS_WATER;
        } else if (!_stricmp(key, "id")) {
            SetKeyValue(mapent, "hammerid", v);
            continue;
        } else if (!_stricmp(key, "mapversion")) {
            map_revision = atoi(v);
        }
        SetKeyValue(mapent, key, v);
    }
    GetVectorForKey(mapent, "origin", mapent->origin);
    /* brushes of an entity with an origin are stored relative to it */
    if (mapent->origin[0] || mapent->origin[1] || mapent->origin[2]) {
        for (int i = 0; i < mapent->numbrushes; i++) {
            mapbrush_t *b = &mapbrushes[mapent->firstbrush + i];
            for (int j = 0; j < b->numsides; j++) {
                side_t *s = &b->original_sides[j];
                const float *pn = mapplanes[s->planenum].normal;     /* (in x87 double, rounded once - measured) */
                vec_t newdist = (float)((double)mapplanes[s->planenum].dist -
                                        ((double)pn[0] * mapent->origin[0] + (double)pn[1] * mapent->origin[1] +
                                         (double)pn[2] * mapent->origin[2]));
                vec3_t n;
                VectorCopy(mapplanes[s->planenum].normal, n);
                s->planenum = FindFloatPlane(n, newdist);
                s->texinfo = TexinfoForBrushTexture(&mapplanes[s->planenum], &side_textures[s - brushsides], mapent->origin);
            }
            MakeBrushWindings(b);
        }
    }
    const char *cls = ValueForKey(mapent, "classname");
    if (!strcmp(cls, "func_detail")) {
        MoveBrushesToWorld(mapent);
        mapent->numbrushes = 0;
        mapent->epairs = NULL;
        return;
    }
    if (!strcmp(cls, "func_ladder")) {
        AddLadderKeys(mapent);
        SetKeyValue(mapent, "classname", "func_simpleladder");
        return;
    }
    if (!strcmp(cls, "func_viscluster")) {
        extern void AddVisCluster(entity_t *e);
        AddVisCluster(mapent);
        return;
    }
    if (!_stricmp(cls, "info_no_dynamic_shadow")) {
        /* its sides cast no dynamic shadows (marked once the map is read) */
        char *list = copystring(ValueForKey(mapent, "sides")), *tok = strtok(list, " ");
        while (tok) {
            int id, k;
            if (sscanf(tok, "%d", &id) == 1) {
                for (k = 0; k < num_noshadow_ids; k++)
                    if (noshadow_ids[k] == id) break;
                if (k == num_noshadow_ids) {
                    noshadow_ids = realloc(noshadow_ids, sizeof(int) * (num_noshadow_ids + 1));
                    noshadow_ids[num_noshadow_ids++] = id;
                }
            }
            tok = strtok(NULL, " ");
        }
        free(list);
        mapent->epairs = NULL;
        return;
    }
    if (!strcmp(cls, "env_cubemap")) {
        extern void Cubemap_FromEntity(entity_t *e);
        Cubemap_FromEntity(mapent);
        mapent->epairs = NULL;
        return;
    }
    if (!strcmp(cls, "info_overlay")) {
        int accessor = Overlay_FromEntity(mapent);
        if (accessor < 0) mapent->epairs = NULL;
        else {
            char buf[16];
            SetKeyValue(mapent, "classname", "info_overlay_accessor");
            sprintf(buf, "%i", accessor);
            SetKeyValue(mapent, "OverlayID", buf);
        }
        return;
    }
    if (!strcmp(cls, "info_overlay_transition") || !_stricmp(cls, "func_instance_parms")) {
        mapent->epairs = NULL;
        return;
    }
    if (IsAreaPortal(cls)) {
        char str[32];
        if (mapent->numbrushes != 1) Error("Entity %i: func_areaportal can only be a single brush", num_entities - 1);
        mapbrush_t *b = &mapbrushes[nummapbrushes - 1];
        b->contents = CONTENTS_AREAPORTAL;
        c_areaportals++;
        mapent->areaportalnum = c_areaportals;
        sprintf(str, "%i", c_areaportals);
        SetKeyValue(mapent, "portalnumber", str);
        MoveBrushesToWorld(mapent);
        return;
    }
    if (mapent != &entities[0]) {
        /* only world brushes can be detail */
        for (int i = 0; i < mapent->numbrushes; i++) {
            mapbrush_t *b = &mapbrushes[mapent->firstbrush + i];
            for (int j = 0; j < b->numsides; j++) b->original_sides[j].contents &= ~CONTENTS_DETAIL;
        }
    }
}

/* vbsp's MarkNoDynamicShadowSides */
void MarkNoDynamicShadowSides(void) {
    for (int i = 0; i < nummapbrushsides; i++) brushsides[i].no_dynamic_shadows = 0;
    for (int k = 0; k < num_noshadow_ids; k++)
        for (int i = 0; i < nummapbrushsides; i++)
            if (brushsides[i].id == noshadow_ids[k]) brushsides[i].no_dynamic_shadows = 1;
}

/* ------------------------------------------------------------------ a map's own data */
void MapState_Save(mapstate_t *s) {
    s->planes = mapplanes, s->nplanes = nummapplanes;
    memcpy(s->planehash, planehash, sizeof(planehash));
    s->brushes = mapbrushes, s->nbrushes = nummapbrushes, s->maxbrushes = max_mapbrushes;
    s->sides = brushsides, s->side_textures = side_textures, s->nsides = nummapbrushsides;
    s->ents = entities, s->nents = num_entities, s->maxents = max_entities;
    VectorCopy(map_mins, s->mins), VectorCopy(map_maxs, s->maxs);
    s->areaportals = c_areaportals;
}

void MapState_Use(const mapstate_t *s) {
    mapplanes = s->planes, nummapplanes = s->nplanes;
    memcpy(planehash, s->planehash, sizeof(planehash));
    mapbrushes = s->brushes, nummapbrushes = s->nbrushes, max_mapbrushes = s->maxbrushes;
    brushsides = s->sides, side_textures = s->side_textures, nummapbrushsides = s->nsides;
    entities = s->ents, num_entities = s->nents, max_entities = s->maxents;
    VectorCopy(s->mins, map_mins), VectorCopy(s->maxs, map_maxs);
    c_areaportals = s->areaportals;
}

void MapState_Free(mapstate_t *s) {
    free(s->planes), free(s->brushes), free(s->sides), free(s->side_textures), free(s->ents);
    memset(s, 0, sizeof(*s));
}

/* areaportal windows make the brush entities they point at see-through */
static void ForceFuncAreaPortalWindowContents(void) {
    const char *targets[] = {"target", "BackgroundBModel"};
    for (int i = 0; i < num_entities; i++) {
        entity_t *e = &entities[i];
        const char *cls = ValueForKey(e, "classname");
        if (!IsAreaPortal(cls) || !_stricmp(cls, "func_areaportal")) continue;
        for (int t = 0; t < 2; t++) {
            const char *name = ValueForKey(e, targets[t]);
            if (!name[0]) continue;
            entity_t *be = EntityByName(name);
            if (!be) continue;
            for (int k = 0; k < be->numbrushes; k++) {
                mapbrushes[be->firstbrush + k].contents &= ~CONTENTS_SOLID;
                mapbrushes[be->firstbrush + k].contents |= CONTENTS_TRANSLUCENT | CONTENTS_WINDOW;
            }
        }
    }
}

/* vbsp's LoadMapFile: reads a .vmf into the current (fresh) map data. The main map then takes in its
 * instances; an instance's own func_instances are merged in by the main map's loop (they're appended) */
void ReadMapFile(const char *path, int main_map) {
    FILE *f = fopen(path, "rb");
    if (!f) Error("Error opening %s", path);
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    parser_t p;
    p.text = xalloc(n + 1);
    fread(p.text, 1, n, f);
    fclose(f);
    p.at = p.text;
    mapplanes = xalloc(sizeof(plane_t) * MAX_MAP_PLANES), nummapplanes = 0;
    memset(planehash, 0, sizeof(planehash));
    brushsides = xalloc(sizeof(side_t) * MAX_MAP_BRUSHSIDES), nummapbrushsides = 0;
    side_textures = xalloc(sizeof(brush_texture_t) * MAX_MAP_BRUSHSIDES);
    mapbrushes = NULL, nummapbrushes = max_mapbrushes = 0;
    entities = NULL, num_entities = max_entities = 0;
    c_areaportals = 0;
    int first_overlay = Overlay_Count(), first_wateroverlay = WaterOverlay_Count();
    if (main_map || verbose) Msg("Loading %s\n", path);
    char key[4096];
    while (next_token(&p)) {
        strcpy(key, p.token);
        if (!next_token(&p)) break;
        if (!p.quoted && !strcmp(p.token, "{")) {
            if (!_stricmp(key, "world") || !_stricmp(key, "entity")) load_entity(&p);
            else skip_block(&p);
        }
    }
    free(p.text);
    Overlay_UpdateSideLists(first_overlay);
    WaterOverlay_UpdateSideLists(first_wateroverlay);
    if (main_map) CheckForInstances(path);
    ClearBounds(map_mins, map_maxs);
    for (int i = 0; i < entities[0].numbrushes; i++) {
        if (mapbrushes[i].mins[0] > MAX_COORD_INTEGER) continue;
        AddPointToBounds(mapbrushes[i].mins, map_mins, map_maxs);
        AddPointToBounds(mapbrushes[i].maxs, map_mins, map_maxs);
    }
    ForceFuncAreaPortalWindowContents();
}

void LoadMapFile(const char *path) { ReadMapFile(path, 1); }
