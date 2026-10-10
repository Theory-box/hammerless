/* Instances (func_instance): another .vmf merged into the map where the entity stands, as vbsp does it
 * (CMapFile::CheckForInstances / MergeInstance). The instance file is read into map data of its own (its own
 * planes, brushes, sides, entities), then: its planes are added to the map's as they are; its brushes and
 * sides are appended, the world's (and ladders') moved and turned with the instance, their texture axes
 * turned and shifted; its entities appended with names prefixed or suffixed (per the game's .fgd: which keys
 * hold names, positions, angles), "replace" parameters substituted, outputs' targets fixed up; its world
 * brushes become the map's. Nested instances are appended as entities and merged by the same loop. */
#include <ctype.h>
#include "hlvbsp.h"
#include "disp.h"

const char *g_fgd_file;              /* -fgd <file>: the .fgd's classes and their keys' types (written by Python) */

/* ------------------------------------------------------------------ the .fgd's keys (vbsp's GameData) */
enum { KV_OTHER, KV_TARGET_DEST, KV_TARGET_SRC, KV_ORIGIN, KV_ANGLE, KV_AXIS, KV_ANGLE_NEG_PITCH };
typedef struct { char *name; int type; } gdvar_t;
typedef struct { char *name; gdvar_t *vars; int nvars; } gdclass_t;
static gdclass_t *gdclasses;
static int numgdclasses, gd_loaded;

static int VarType(const char *t) {
    if (!_stricmp(t, "target_destination")) return KV_TARGET_DEST;
    if (!_stricmp(t, "target_source")) return KV_TARGET_SRC;
    if (!_stricmp(t, "origin")) return KV_ORIGIN;
    if (!_stricmp(t, "angle")) return KV_ANGLE;
    if (!_stricmp(t, "axis")) return KV_AXIS;
    if (!_stricmp(t, "angle_negative_pitch")) return KV_ANGLE_NEG_PITCH;
    return KV_OTHER;
}

/* lines "class <name>" then "<key> <type>" per key, in the order vbsp's GameData holds them (bases first) */
static int LoadGameData(void) {
    if (gd_loaded) return gd_loaded > 0;
    gd_loaded = -1;
    if (!g_fgd_file) return 0;
    FILE *f = fopen(g_fgd_file, "r");
    if (!f) return 0;
    char line[1024], a[512], b[512];
    while (fgets(line, sizeof(line), f)) {
        int n = sscanf(line, "%511s %511s", a, b);
        if (n < 1) continue;
        if (!strcmp(a, "class") && n == 2) {
            gdclasses = realloc(gdclasses, sizeof(gdclass_t) * (numgdclasses + 1));
            gdclass_t *c = &gdclasses[numgdclasses++];
            memset(c, 0, sizeof(*c));
            c->name = copystring(b);
        } else if (numgdclasses && n == 2) {
            gdclass_t *c = &gdclasses[numgdclasses - 1];
            c->vars = realloc(c->vars, sizeof(gdvar_t) * (c->nvars + 1));
            c->vars[c->nvars].name = copystring(a);
            c->vars[c->nvars].type = VarType(b);
            c->nvars++;
        }
    }
    fclose(f);
    gd_loaded = 1;
    return 1;
}

static gdclass_t *ClassForName(const char *name) {
    for (int i = 0; i < numgdclasses; i++)
        if (!_stricmp(gdclasses[i].name, name)) return &gdclasses[i];
    return NULL;
}

/* the class an instance's entity is remapped with: its own keys, then Origin's and Angles' when it lacks them
 * (GameData::BeginInstanceRemap: a new class with these bases; a base's key already there isn't added again) */
static gdvar_t remap_vars[1024];
static int num_remap_vars;
static float remap_origin[3], remap_angles[3], remap_mat[3][4];
static char remap_prefix[256];

static gdvar_t *RemapVar(const char *name) {
    for (int i = 0; i < num_remap_vars; i++)
        if (!_stricmp(remap_vars[i].name, name)) return &remap_vars[i];
    return NULL;
}

static void AddRemapBase(const gdclass_t *c) {
    for (int i = 0; i < c->nvars && num_remap_vars < 1024; i++)
        if (!RemapVar(c->vars[i].name)) remap_vars[num_remap_vars++] = c->vars[i];
}

/* ------------------------------------------------------------------ vbsp's mathlib (floats) */
static void SinCos(float radians, float *s, float *c) {
    /* (x87 fsincos of the float, stored as floats) */
    long double r = radians;
    *s = (float)sinl(r);
    *c = (float)cosl(r);
}

#define DEG2RAD(x) ((float)(x) * (float)(3.14159265358979323846f / 180.f))
#define RAD2DEG(x) ((float)(x) * (float)(180.f / 3.14159265358979323846f))

static void AngleMatrix(const float angles[3], const float position[3], float m[3][4]) {
    float sr, sp, sy, cr, cp, cy;
    SinCos(DEG2RAD(angles[1]), &sy, &cy);
    SinCos(DEG2RAD(angles[0]), &sp, &cp);
    SinCos(DEG2RAD(angles[2]), &sr, &cr);
    /* (vbsp's compiled code, measured by disassembly: floats, products grouped (sr*sp)*cy, not sp*(sr*cy)) */
    float srsp = sr * sp, crsp = cr * sp;
    m[0][0] = cp * cy;
    m[1][0] = cp * sy;
    m[2][0] = -sp;
    m[0][1] = srsp * cy - cr * sy;
    m[1][1] = srsp * sy + cr * cy;
    m[2][1] = sr * cp;
    m[0][2] = crsp * cy + sr * sy;
    m[1][2] = crsp * sy - sr * cy;
    m[2][2] = cr * cp;
    for (int k = 0; k < 3; k++) m[k][3] = position ? position[k] : 0.0f;
}

static void VectorTransform(const float in[3], const float m[3][4], float out[3]) {
    for (int k = 0; k < 3; k++) out[k] = DotProduct(in, m[k]) + m[k][3];
}

static void VectorRotate(const float in[3], const float m[3][4], float out[3]) {
    for (int k = 0; k < 3; k++) out[k] = DotProduct(in, m[k]);
}

/* (vbsp's compiled code, measured by disassembly: the centre in floats; the extents summed on the x87 (double)
 * and used unrounded, except that maxs.x adds the extent rounded to a float) */
static void TransformAABB(const float m[3][4], const vec3_t mins, const vec3_t maxs, vec3_t omins, vec3_t omaxs) {
    vec3_t center, extents, wc;
    double we[3];
    VectorAdd(mins, maxs, center);
    for (int k = 0; k < 3; k++) center[k] *= 0.5f;
    VectorSubtract(maxs, center, extents);
    VectorTransform(center, m, wc);
    for (int k = 0; k < 3; k++)
        we[k] = (fabs((double)extents[0] * m[k][0]) + fabs((double)extents[1] * m[k][1])) + fabs((double)extents[2] * m[k][2]);
    for (int k = 0; k < 3; k++) omins[k] = (float)((double)wc[k] - we[k]);
    omaxs[0] = wc[0] + (float)we[0];
    omaxs[1] = (float)((double)wc[1] + we[1]);
    omaxs[2] = (float)((double)wc[2] + we[2]);
}

static void ConcatTransforms(const float a[3][4], const float b[3][4], float out[3][4]) {
    for (int r = 0; r < 3; r++) {
        for (int c = 0; c < 3; c++) out[r][c] = a[r][0] * b[0][c] + a[r][1] * b[1][c] + a[r][2] * b[2][c];
        out[r][3] = a[r][0] * b[0][3] + a[r][1] * b[1][3] + a[r][2] * b[2][3] + a[r][3];
    }
}

static void MatrixAngles(const float m[3][4], float angles[3]) {
    float forward[3] = {m[0][0], m[1][0], m[2][0]}, left[3] = {m[0][1], m[1][1], m[2][1]}, upz = m[2][2];
    float xy = sqrtf(forward[0] * forward[0] + forward[1] * forward[1]);
    if (xy > 0.001f) {
        angles[1] = RAD2DEG(atan2f(forward[1], forward[0]));
        angles[0] = RAD2DEG(atan2f(-forward[2], xy));
        angles[2] = RAD2DEG(atan2f(left[2], upz));
    } else {
        angles[1] = RAD2DEG(atan2f(-left[0], left[1]));
        angles[0] = RAD2DEG(atan2f(-forward[2], xy));
        angles[2] = 0;
    }
}

/* ------------------------------------------------------------------ remapping keys (GameData) */
static int RemapNameField(const char *in, char *out, int fixup) {
    strcpy(out, in);
    if (in[0] && in[0] != '@') {
        if (fixup == 0) sprintf(out, "%s-%s", remap_prefix, in);
        else if (fixup == 1) sprintf(out, "%s-%s", in, remap_prefix);
    }
    return _stricmp(in, out) != 0;
}

static int RemapKeyValue(const char *key, const char *in, char *out, int fixup) {
    gdvar_t *v = RemapVar(key);
    if (!v || v->type == KV_OTHER) return 0;
    strcpy(out, in);
    int turned = remap_angles[0] != 0.0f || remap_angles[1] != 0.0f || remap_angles[2] != 0.0f;
    switch (v->type) {
    case KV_TARGET_DEST:
    case KV_TARGET_SRC:
        RemapNameField(in, out, fixup);
        break;
    case KV_ORIGIN: {
        float p[3] = {0, 0, 0}, o[3];
        sscanf(in, "%f %f %f", &p[0], &p[1], &p[2]);
        VectorTransform(p, remap_mat, o);
        sprintf(out, "%s %s %s", FmtG(o[0]), FmtG(o[1]), FmtG(o[2]));
        break;
    }
    case KV_ANGLE:
    case KV_AXIS:
        if (turned) {
            float a[3] = {0, 0, 0}, o[3], m[3][4], l[3][4];
            sscanf(in, "%f %f %f", &a[0], &a[1], &a[2]);
            AngleMatrix(a, NULL, m);
            ConcatTransforms(remap_mat, m, l);
            MatrixAngles(l, o);
            sprintf(out, "%s %s %s", FmtG(o[0]), FmtG(o[1]), FmtG(o[2]));
        }
        break;
    case KV_ANGLE_NEG_PITCH:
        if (turned) {
            float a[3] = {0, 0, 0}, o[3], m[3][4], l[3][4];
            sscanf(in, "%f", &a[0]);
            a[0] = -a[0];
            AngleMatrix(a, NULL, m);
            ConcatTransforms(remap_mat, m, l);
            MatrixAngles(l, o);
            sprintf(out, "%s", FmtG(-o[0]));
        }
        break;
    }
    return _stricmp(in, out) != 0;
}

/* "replace" keys of the func_instance: "$variable value", substituted (case-insensitive) in a value */
static int StrSubst(const char *in, const char *match, const char *repl, char *out, int outlen) {
    int ml = (int)strlen(match), rl = (int)strlen(repl), n = 0;
    if (!ml) {
        if ((int)strlen(in) + 1 > outlen) return 0;
        strcpy(out, in);
        return 1;
    }
    for (const char *s = in; *s;) {
        if (!_strnicmp(s, match, ml)) {
            if (n + rl + 1 > outlen) return 0;
            memcpy(out + n, repl, rl);
            n += rl, s += ml;
        } else {
            if (n + 2 > outlen) return 0;
            out[n++] = *s++;
        }
    }
    out[n] = 0;
    return 1;
}

#define MAX_KEYVALUE_LEN 32768
static void ReplaceInstancePair(epair_t *pair, entity_t *inst) {
    static char value[MAX_KEYVALUE_LEN], newvalue[MAX_KEYVALUE_LEN], var[MAX_KEYVALUE_LEN];
    int overwritten = 0;
    strcpy(newvalue, pair->value);
    for (epair_t *ep = inst->epairs; ep; ep = ep->next) {
        if (_strnicmp(ep->key, "replace", 7)) continue;
        strcpy(var, ep->value);
        char *sp = strchr(var, ' ');
        if (!sp) continue;
        *sp = 0;
        strcpy(value, newvalue);
        if (!StrSubst(value, var, sp + 1, newvalue, sizeof(newvalue))) {
            overwritten = 1;
            break;
        }
    }
    if (!overwritten && strcmp(pair->value, newvalue)) {
        free(pair->value);
        pair->value = copystring(newvalue);
    }
}

/* ------------------------------------------------------------------ finding the file */
static int FileExists(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    fclose(f);
    return 1;
}

/* V_StripFilename: the folder (no slash at the end) */
static void StripFilename(char *path) {
    char *e = path + strlen(path);
    while (e > path && e[-1] != '/' && e[-1] != 92) e--;
    if (e > path) e--;
    *e = 0;
}

static int DeterminePath(const char *base, const char *file, char *out) {
    char fixed[1024];
    snprintf(fixed, sizeof(fixed) - 5, "%s", file);
    /* V_SetExtension: any extension replaced by .vmf; V_FixSlashes */
    char *dot = strrchr(fixed, '.'), *slash = strrchr(fixed, '/'), *bslash = strrchr(fixed, 92);
    if (bslash > slash) slash = bslash;
    if (dot && (!slash || dot > slash)) *dot = 0;
    strcat(fixed, ".vmf");
    for (char *c = fixed; *c; c++)
        if (*c == '/') *c = 92;
    /* first next to the map (for nested instances too: vbsp passes the main map's name) */
    snprintf(out, 1024, "%s", base);
    StripFilename(out);
    strcat(out, "\\");
    strcat(out, fixed);
    if (FileExists(out)) return 1;
    /* then from the "maps" folder the map is in */
    char dir[1024];
    snprintf(dir, sizeof(dir) - 2, "%s", base);
    StripFilename(dir);
    for (char *c = dir; *c; c++) *c = (char)tolower((unsigned char)(*c == '/' ? 92 : *c));
    strcat(dir, "\\");
    char *pos = strstr(dir, "\\maps\\");
    if (pos) {
        pos[6] = 0;
        snprintf(out, 1024, "%s%s", dir, fixed);
        if (FileExists(out)) return 1;
    }
    out[0] = 0;
    return 0;
}

/* ------------------------------------------------------------------ merging */
static int instance_count;

static void MergePlanes(const mapstate_t *in) {
    for (int i = 0; i < in->nplanes; i += 2) {
        vec3_t n;
        VectorCopy(in->planes[i].normal, n);
        FindFloatPlane(n, in->planes[i].dist);
    }
}

static void MergeBrushes(const mapstate_t *in, const float m[3][4]) {
    int max_id = 0;
    for (int i = 0; i < nummapbrushes; i++)
        if (mapbrushes[i].id > max_id) max_id = mapbrushes[i].id;
    if (nummapbrushes + in->nbrushes > 0) {
        extern int max_mapbrushes;
        if (nummapbrushes + in->nbrushes > max_mapbrushes) {
            max_mapbrushes = nummapbrushes + in->nbrushes + 1024;
            mapbrushes = realloc(mapbrushes, sizeof(mapbrush_t) * max_mapbrushes);
        }
    }
    for (int i = 0; i < in->nbrushes; i++) {
        mapbrush_t *b = &mapbrushes[nummapbrushes + i];
        *b = in->brushes[i];
        b->entitynum += num_entities;
        b->brushnum += nummapbrushes;
        if (i < in->ents[0].numbrushes || (b->contents & CONTENTS_LADDER)) {
            vec3_t mins, maxs;
            VectorCopy(b->mins, mins), VectorCopy(b->maxs, maxs);
            TransformAABB(m, mins, maxs, b->mins, b->maxs);
        }
        b->id += max_id;
        b->original_sides = &brushsides[nummapbrushsides + (int)(in->brushes[i].original_sides - in->sides)];
    }
    nummapbrushes += in->nbrushes;
}

static void MergeBrushSides(const mapstate_t *in, const float origin[3], const float m[3][4]) {
    int max_id = 0;
    for (int i = 0; i < nummapbrushsides; i++)
        if (brushsides[i].id > max_id) max_id = brushsides[i].id;
    if (nummapbrushsides + in->nsides > MAX_MAP_BRUSHSIDES) Error("MAX_MAP_BRUSHSIDES");
    for (int i = 0; i < in->nsides; i++) {
        side_t *side = &brushsides[nummapbrushsides + i];
        *side = in->sides[i];
        {
            vec3_t n;
            VectorCopy(in->planes[side->planenum].normal, n);
            side->planenum = FindFloatPlane(n, in->planes[side->planenum].dist);
        }
        side->id += max_id;
        mapdisp_t *md = side->disp ? &mapdisps[side->disp - 1] : NULL;
        int translate = md && md->entitynum == 0;
        for (int j = 0; !translate && j < in->ents[0].numbrushes; j++) {
            int loc = (int)(in->brushes[j].original_sides - in->sides);
            if (i >= loc && i < loc + in->brushes[j].numsides) translate = 1;
        }
        for (int j = in->ents[0].numbrushes; !translate && j < in->nbrushes; j++) {
            int loc = (int)(in->brushes[j].original_sides - in->sides);
            if (i >= loc && i < loc + in->brushes[j].numsides && (in->brushes[j].contents & CONTENTS_LADDER)) translate = 1;
        }
        if (translate) {
            if (side->winding)
                for (int p = 0; p < side->winding->numpoints; p++) {
                    vec3_t t;
                    VectorCopy(side->winding->p[p], t);
                    VectorTransform(t, m, side->winding->p[p]);
                }
            /* MatrixTransformPlane */
            const plane_t *pl = &mapplanes[side->planenum];
            vec3_t n;
            VectorRotate(pl->normal, m, n);
            vec_t dist = pl->dist * DotProduct(n, n);
            dist += n[0] * m[0][3] + n[1] * m[1][3] + n[2] * m[2][3];
            side->planenum = FindFloatPlane(n, dist);
            brush_texture_t bt = in->side_textures[i];
            VectorRotate(in->side_textures[i].uaxis, m, bt.uaxis);
            VectorRotate(in->side_textures[i].vaxis, m, bt.vaxis);
            bt.shift[0] -= DotProduct(origin, bt.uaxis) / bt.scale[0];
            bt.shift[1] -= DotProduct(origin, bt.vaxis) / bt.scale[1];
            side->texinfo = TexinfoForBrushTexture(&mapplanes[side->planenum], &bt, (vec3_t){0, 0, 0});
        }
        if (md) {
            md->brushsideid = side->id;
            vec3_t t;
            VectorCopy(md->startpos, t);
            VectorTransform(t, m, md->startpos);
            md->face.originalface = side;
            md->face.texinfo = side->texinfo;
            md->face.planenum = side->planenum;
            md->entitynum += num_entities;
            for (int p = 0; p < md->face.w->numpoints; p++) {
                VectorCopy(md->face.w->p[p], t);
                VectorTransform(t, m, md->face.w->p[p]);
            }
        }
    }
    nummapbrushsides += in->nsides;
}

static void MergeEntities(int inst_ent, const mapstate_t *in, const float origin[3], const float angles[3],
                          const float m[3][4]) {
    static char temp[MAX_KEYVALUE_LEN];
    entity_t *instent = &entities[inst_ent];
    const char *tn = ValueForKey(instent, "targetname"), *nm = ValueForKey(instent, "name");
    if (tn[0]) snprintf(remap_prefix, sizeof(remap_prefix), "%s", tn);
    else if (nm[0]) snprintf(remap_prefix, sizeof(remap_prefix), "%s", nm);
    else sprintf(remap_prefix, "InstanceAuto%d", instance_count);
    int max_id = 0;
    for (int i = 0; i < num_entities; i++) {
        const char *id = ValueForKey(&entities[i], "hammerid");
        if (id[0] && atoi(id) > max_id) max_id = atoi(id);
    }
    int fixup = atoi(ValueForKey(instent, "fixup_style"));
    extern int max_entities;
    if (num_entities + in->nents > max_entities) {
        max_entities = num_entities + in->nents + 1024;
        entities = realloc(entities, sizeof(entity_t) * max_entities);
        instent = &entities[inst_ent];
    }
    int world = -1;
    for (int i = 0; i < in->nents; i++) {
        entity_t *e = &entities[num_entities + i];
        *e = in->ents[i];
        e->firstbrush += nummapbrushes - in->nbrushes;
        const char *id = ValueForKey(e, "hammerid");
        if (id[0]) {
            sprintf(temp, "%d", atoi(id) + max_id);
            SetKeyValue(e, "hammerid", temp);
        }
        const char *cls = ValueForKey(e, "classname");
        if (!_stricmp(cls, "worldspawn")) {
            world = num_entities + i;
            continue;
        }
        vec3_t t;
        VectorCopy(e->origin, t);
        VectorTransform(t, m, e->origin);
        for (epair_t *ep = e->epairs; ep; ep = ep->next) ReplaceInstancePair(ep, instent);
        /* BeginInstanceRemap (info_overlay_accessor: as info_overlay) */
        const char *gcls = !_stricmp(cls, "info_overlay_accessor") ? "info_overlay" : cls;
        gdclass_t *c = ClassForName(gcls);
        if (c) {
            num_remap_vars = 0;
            AddRemapBase(c);
            gdclass_t *b;
            if (!RemapVar("Origin") && (b = ClassForName("Origin"))) AddRemapBase(b);
            if (!RemapVar("Angles") && (b = ClassForName("Angles"))) AddRemapBase(b);
            VectorCopy(origin, remap_origin);
            VectorCopy(angles, remap_angles);
            AngleMatrix(remap_angles, remap_origin, remap_mat);
            for (int j = 0; j < num_remap_vars; j++) {
                const char *v = ValueForKey(e, remap_vars[j].name);
                char *copy = copystring(v);
                if (RemapKeyValue(remap_vars[j].name, copy, temp, fixup)) SetKeyValue(e, remap_vars[j].name, temp);
                free(copy);
            }
        }
        if (!_stricmp(cls, "func_simpleladder")) AddLadderKeys(e);
    }
    /* outputs: "replace" parameters, then the target (the first field) renamed */
    for (int i = 0; i < in->nents; i++)
        for (epair_t *ep = in->ents[i].epairs; ep; ep = ep->next)
            if (ep->connection) ReplaceInstancePair(ep, instent);
    for (int i = 0; i < in->nents; i++)
        for (epair_t *ep = in->ents[i].epairs; ep; ep = ep->next) {
            if (!ep->connection) continue;
            char orig[4096];
            strncpy(orig, ep->value, sizeof(orig) - 1);
            orig[sizeof(orig) - 1] = 0;
            char delim = strchr(orig, 0x1b) ? 0x1b : ',';
            char *pos = strchr(orig, delim);
            if (pos) *pos = 0;
            if (RemapNameField(orig, temp, fixup)) {
                size_t n = strlen(temp) + (pos ? strlen(pos + 1) + 2 : 1);
                char *nv = xalloc(n);
                strcpy(nv, temp);
                if (pos) {
                    char d[2] = {0x1b, 0};
                    strcat(nv, d);
                    strcat(nv, pos + 1);
                }
                free(ep->value);
                ep->value = nv;
            }
        }
    num_entities += in->nents;
    if (world >= 0) {
        MoveBrushesToWorldGeneral(&entities[world]);
        entities[world].numbrushes = 0;
        entities[world].epairs = NULL;
    }
}

static void MergeInstance(int inst_ent, mapstate_t *in) {
    entity_t *e = &entities[inst_ent];
    float origin[3], angles[3] = {0, 0, 0}, m[3][4];
    VectorCopy(e->origin, origin);
    instance_count++;
    sscanf(ValueForKey(e, "angles"), "%f %f %f", &angles[0], &angles[1], &angles[2]);
    AngleMatrix(angles, origin, m);
    MergePlanes(in);
    MergeBrushes(in, m);
    MergeBrushSides(in, origin, m);
    MergeEntities(inst_ent, in, origin, angles, m);
    Overlay_Translate(in->first_overlay, origin, m);
    WaterOverlay_Translate(in->first_wateroverlay, m);
}

/* vbsp's CheckForInstances: for the main map, each func_instance (also those that come with instances) */
void CheckForInstances(const char *path) {
    if (!LoadGameData()) {
        Msg("Could not locate GameData file %s\n", g_fgd_file ? g_fgd_file : "(none: -fgd)");
        return;
    }
    for (int i = 0; i < num_entities; i++) {
        if (strcmp(ValueForKey(&entities[i], "classname"), "func_instance")) continue;
        const char *file = ValueForKey(&entities[i], "file");
        /* how deep in instances this one is (instances inside instances): a loop would never end */
        static int *depth, depth_cap;
        if (depth_cap < num_entities + 1) {
            int cap = (num_entities + 1) * 2;
            depth = realloc(depth, sizeof(int) * cap);
            if (!depth) Error("out of memory");
            memset(depth + depth_cap, 0, sizeof(int) * (cap - depth_cap));
            depth_cap = cap;
        }
        static int merged;            /* (a loop that branches grows wide before it grows deep) */
        if (file[0] && (depth[i] >= 64 || ++merged > 65536)) Error("Instances include each other in a loop (%s)", file);
        int before = num_entities;
        if (file[0]) {
            char found[1100];
            int loaded = 0;
            if (DeterminePath(path, file, found)) {
                mapstate_t main_map, inst;
                MapState_Save(&main_map);
                int first_overlay = Overlay_Count();      /* (the overlays the instance file adds come after) */
                int first_wateroverlay = WaterOverlay_Count();
                ReadMapFile(found, 0);
                MapState_Save(&inst);
                inst.first_overlay = first_overlay;
                inst.first_wateroverlay = first_wateroverlay;
                MapState_Use(&main_map);
                MergeInstance(i, &inst);
                free(inst.planes), free(inst.sides), free(inst.side_textures), free(inst.brushes), free(inst.ents);
                loaded = 1;
            }
            if (!loaded) Error("Could not open instance file %s", file);     /* (vbsp: an error, the compile stops) */
        }
        if (num_entities > before) {
            if (depth_cap < num_entities + 1) {
                int cap = (num_entities + 1) * 2;
                depth = realloc(depth, sizeof(int) * cap);
                if (!depth) Error("out of memory");
                memset(depth + depth_cap, 0, sizeof(int) * (cap - depth_cap));
                depth_cap = cap;
            }
            for (int k = before; k < num_entities; k++) depth[k] = depth[i] + 1;
        }
        entities[i].numbrushes = 0;
        entities[i].epairs = NULL;
    }
}
