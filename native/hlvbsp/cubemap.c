/* env_cubemap. Each cubemap is a sample (lump 42: origin, size). Surfaces whose material has
 * `$envmap env_cubemap` get a one-off material per cubemap, maps/<map>/<material>_<x>_<y>_<z>:
 * the sides an env_cubemap lists get that cubemap, every other such side the nearest cubemap in
 * front of it (else the nearest). The material is a patch .vmt in the pakfile (Python wrote its
 * text, see core/cubemappatch.py); the texture it points at, maps/<map>/c<x>_<y>_<z>, starts as a
 * copy of the default cubemap until the game's buildcubemaps replaces it. As vbsp's cubemap.cpp. */
#include "hlvbsp.h"
#include "disp.h"
#include <ctype.h>
#include <float.h>

typedef struct { int origin[3], size; } cubemapsample_t;
typedef struct { int *ids, n; } sidelist_t;

typedef struct {
    char *name;
    int specular, patchable;
    char *depvar, *dep;
    char **lines;
    int nlines;
} cuberecord_t;

static cubemapsample_t *samples;
static sidelist_t *sidelists;
static int numsamples;
static cuberecord_t *records;
static int numrecords;
typedef struct { char *name; char **lines; int nlines; } wvtrecord_t;
static wvtrecord_t *wvts;          /* blend materials' LightmappedGeneric copies (Python wrote the text) */
static int numwvts;
static char **defaultnames;               /* materials/maps/<map>/c<x>_<y>_<z>.vtf, in order */
static int numdefaultnames;
static unsigned char *is_cubemap_texdata;
typedef struct { int texdata, origin[3]; char *material; } clone_t;
static clone_t *clones;              /* the patched texdatas: for which material and cubemap */
static int numclones;
static int is_cubemap_cap;

const char *g_cubemap_file;

int CloneTexData(int source, const char *name);
int FindTexDataByName(const char *name);
int FindTexInfoExact(const texinfo_t *t);
const char *TexDataName(int texdata);
int OriginalTexData(int texdata);
void AddFileToPak(const char *name, unsigned char *data, int len);

/* ------------------------------------------------------------------ the entities */
void Cubemap_FromEntity(entity_t *e) {
    if (numsamples % 16 == 0) {
        samples = realloc(samples, sizeof(cubemapsample_t) * (numsamples + 16));
        sidelists = realloc(sidelists, sizeof(sidelist_t) * (numsamples + 16));
    }
    cubemapsample_t *s = &samples[numsamples];
    for (int i = 0; i < 3; i++) s->origin[i] = (int)e->origin[i];
    s->size = atoi(ValueForKey(e, "cubemapsize"));
    sidelist_t *l = &sidelists[numsamples++];
    l->ids = NULL;
    l->n = 0;
    char *list = copystring(ValueForKey(e, "sides")), *tok = strtok(list, " ");
    while (tok) {
        int id;
        if (sscanf(tok, "%d", &id) == 1) {
            l->ids = realloc(l->ids, sizeof(int) * (l->n + 1));
            l->ids[l->n++] = id;
        }
        tok = strtok(NULL, " ");
    }
    free(list);
}

/* ------------------------------------------------------------------ the material records */
static void LoadCubemapTable(void) {
    static int loaded;
    if (loaded || !g_cubemap_file) return;
    loaded = 1;
    FILE *f = fopen(g_cubemap_file, "rb");
    if (!f) return;
    char line[4096];
    cuberecord_t *r = NULL;
    wvtrecord_t *w = NULL;
    int want = 0;
    while (fgets(line, sizeof(line), f)) {
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
        if (want > 0 && w) {
            w->lines[w->nlines++] = copystring(line);
            want--;
            continue;
        }
        if (want > 0 && r) {
            r->lines[r->nlines++] = copystring(line);
            want--;
            continue;
        }
        if (!strncmp(line, "wvt\t", 4)) {
            char *tab = strchr(line + 4, '\t');
            if (!tab) continue;
            *tab = 0;
            wvts = realloc(wvts, sizeof(wvtrecord_t) * (numwvts + 1));
            w = &wvts[numwvts++];
            r = NULL;
            w->name = copystring(line + 4);
            want = atoi(tab + 1);
            w->lines = xalloc(sizeof(char *) * (want + 1));
            w->nlines = 0;
            continue;
        }
        if (strncmp(line, "mat\t", 4)) continue;
        w = NULL;
        char *fields[7], *p = line;
        int k = 0;
        for (; k < 7 && p; k++) {
            fields[k] = p;
            p = strchr(p, '\t');
            if (p) *p++ = 0;
        }
        if (k < 7) continue;
        records = realloc(records, sizeof(cuberecord_t) * (numrecords + 1));
        r = &records[numrecords++];
        r->name = copystring(fields[1]);
        r->specular = atoi(fields[2]);
        r->patchable = atoi(fields[3]);
        r->depvar = copystring(fields[4]);
        r->dep = copystring(fields[5]);
        want = atoi(fields[6]);
        r->lines = xalloc(sizeof(char *) * (want + 1));
        r->nlines = 0;
    }
    fclose(f);
}

static int same_name(const char *a, const char *b) {
    for (;; a++, b++) {
        int ca = tolower((unsigned char)(*a == 92 ? '/' : *a)), cb = tolower((unsigned char)(*b == 92 ? '/' : *b));
        if (ca != cb) return 0;
        if (!ca) return 1;
    }
}

static cuberecord_t *Record(const char *name) {
    LoadCubemapTable();
    for (int i = 0; i < numrecords; i++)
        if (!strcmp(records[i].name, name)) return &records[i];
    for (int i = 0; i < numrecords; i++)
        if (same_name(records[i].name, name)) return &records[i];
    return NULL;
}

/* ------------------------------------------------------------------ patching */
/* maps/<map>/<material>_<x>_<y>_<z> (or maps/<map>/c<x>_<y>_<z> for the texture), lower case */
static void PatchedName(const char *material, const int origin[3], int is_material, char *out, int max) {
    int len = snprintf(out, max, "maps/%s/%s%s%d_%d_%d", g_mapbase, material, is_material ? "_" : "", origin[0], origin[1],
                       origin[2]);
    if (is_material && len >= 128 - 1)
        Error("Generated env_cubemap patch name : %s too long! (max = %d)\n", out, 128);
    for (char *c = out; *c; c++) *c = *c == 92 ? '/' : (char)tolower((unsigned char)*c);
}

static void replace_all(char *s, int max, const char *token, const char *value) {
    char tmp[4096];
    char *at;
    while ((at = strstr(s, token)) != NULL) {
        snprintf(tmp, sizeof(tmp), "%.*s%s%s", (int)(at - s), s, value, at + strlen(token));
        strncpy(s, tmp, max - 1);
        s[max - 1] = 0;
    }
}

/* The patch .vmt for a material and (first) its dependent; 0 when neither needs one. */
static int PatchEnvmapForMaterialAndDependents(const char *material, const char *recname, const int origin[3],
                                               const char *cubetex, int depth) {
    cuberecord_t *r = Record(recname);
    if (!r || depth > 8) return 0;
    int dep_patched = 0;
    char deppatched[600] = "";
    if (strcmp(r->dep, "-")) {
        dep_patched = PatchEnvmapForMaterialAndDependents(r->dep, r->dep, origin, cubetex, depth + 1);
        if (dep_patched) PatchedName(r->dep, origin, 1, deppatched, sizeof(deppatched));
    }
    if (!r->patchable) return 0;
    char patched[600], path[700];
    PatchedName(material, origin, 1, patched, sizeof(patched));
    snprintf(path, sizeof(path), "materials/%s.vmt", patched);
    int cap = 4096, len = 0;
    char *text = xalloc(cap);
    for (int i = 0; i < r->nlines; i++) {
        char line[4096];
        strncpy(line, r->lines[i], sizeof(line) - 1);
        line[sizeof(line) - 1] = 0;
        replace_all(line, sizeof(line), "@NAME@", material);
        replace_all(line, sizeof(line), "@CUBE@", cubetex);
        replace_all(line, sizeof(line), "@DEP@", deppatched);
        int n = (int)strlen(line);
        while (len + n + 3 > cap) text = realloc(text, cap *= 2);
        memcpy(text + len, line, n);
        len += n;
        text[len++] = '\r';
        text[len++] = '\n';
    }
    AddFileToPak(path, (unsigned char *)text, len);
    return 1;
}

static void MarkCubemapTexData(int td) {
    if (td >= is_cubemap_cap) {
        int n = td + 256;
        is_cubemap_texdata = realloc(is_cubemap_texdata, n);
        memset(is_cubemap_texdata + is_cubemap_cap, 0, n - is_cubemap_cap);
        is_cubemap_cap = n;
    }
    is_cubemap_texdata[td] = 1;
}

static int IsCubemapTexData(int td) { return td < is_cubemap_cap && is_cubemap_texdata[td]; }

static int stristr_(const char *h, const char *n) {
    size_t ln = strlen(n);
    for (; *h; h++)
        if (!_strnicmp(h, n, ln)) return 1;
    return 0;
}

/* The texinfo of a side's material patched for the cubemap at `origin` (vbsp's Cubemap_CreateTexInfo). */
static int Cubemap_CreateTexInfo(int texinfo, const int origin[3]) {
    if (texinfo == TEXINFO_NODE) return texinfo;
    int td = texinfos[texinfo].texdata;
    const char *name = TexDataName(td);
    if (IsCubemapTexData(td)) {
        Warning("Multiple references for cubemap on texture %s!!!\n", name);
        return texinfo;
    }
    char search[64];
    snprintf(search, sizeof(search), "_%d_%d_%d", origin[0], origin[1], origin[2]);
    if (stristr_(name, search)) return texinfo;
    char generated[600];
    PatchedName(name, origin, 1, generated, sizeof(generated));
    int newtd = FindTexDataByName(generated);
    int had = newtd != -1;
    if (!had) {
        char tex[600];
        PatchedName("c", origin, 0, tex, sizeof(tex));
        char nametmp[600];
        strncpy(nametmp, name, sizeof(nametmp) - 1);
        nametmp[sizeof(nametmp) - 1] = 0;
        if (!PatchEnvmapForMaterialAndDependents(nametmp, TexDataName(OriginalTexData(td)), origin, tex, 0)) return texinfo;
        char file[700];
        snprintf(file, sizeof(file), "materials/%s.vtf", tex);
        defaultnames = realloc(defaultnames, sizeof(char *) * (numdefaultnames + 1));
        defaultnames[numdefaultnames++] = copystring(file);
        newtd = CloneTexData(td, generated);
        MarkCubemapTexData(newtd);
        clones = realloc(clones, sizeof(clone_t) * (numclones + 1));
        clones[numclones].texdata = newtd;
        memcpy(clones[numclones].origin, origin, sizeof(clones[numclones].origin));
        clones[numclones++].material = copystring(nametmp);
    }
    texinfo_t t = texinfos[texinfo];
    t.texdata = newtd;
    if (had) {
        int found = FindTexInfoExact(&t);
        if (found >= 0) return found;
    }
    return AppendTexinfo(&t);
}

static void SetSideTexinfo(side_t *side, int texinfo) {
    side->texinfo = texinfo;
    if (side->disp) mapdisps[side->disp - 1].face.texinfo = texinfo;
}

static int SideIDToIndex(int id) {
    for (int i = 0; i < nummapbrushsides; i++)
        if (brushsides[i].id == id) return i;
    return -1;
}

/* The sides each env_cubemap lists. */
void Cubemap_FixupBrushSidesMaterials(void) {
    Msg("fixing up env_cubemap materials on brush sides...\n");
    for (int c = 0; c < numsamples; c++) {
        for (int i = 0; i < sidelists[c].n; i++) {
            int index = SideIDToIndex(sidelists[c].ids[i]);
            if (index < 0) {
                Warning("env_cubemap pointing at deleted brushside near (%d, %d, %d)\n", samples[c].origin[0],
                        samples[c].origin[1], samples[c].origin[2]);
                continue;
            }
            side_t *side = &brushsides[index];
            SetSideTexinfo(side, Cubemap_CreateTexInfo(side->texinfo, samples[c].origin));
        }
    }
}

/* The nearest cubemap in front of the side (else the nearest at all). */
static int FindClosestCubemap(const vec3_t entorigin, const side_t *side) {
    if (!side->winding) return 0;
    vec3_t center = {0, 0, 0};
    for (int i = 0; i < side->winding->numpoints; i++) VectorAdd(center, side->winding->p[i], center);
    float scale = 1.0f / side->winding->numpoints;
    for (int k = 0; k < 3; k++) center[k] *= scale;
    VectorAdd(center, entorigin, center);
    const plane_t *plane = &mapplanes[side->planenum];
    int best = -1;
    float bestdist = FLT_MAX;
    for (int c = 0; c < numsamples; c++) {
        vec3_t d;
        for (int k = 0; k < 3; k++) d[k] = (float)samples[c].origin[k] - center[k];
        /* mathlib's VectorNormalize: the length in x87, rounded to float */
        float len = (float)sqrt((double)d[0] * d[0] + (double)d[1] * d[1] + (double)d[2] * d[2]);
        float oo = 1.0f / (len + FLT_EPSILON);
        for (int k = 0; k < 3; k++) d[k] *= oo;
        float dot = DotProduct(d, plane->normal);
        if (dot >= 0.0f && len < bestdist) {
            bestdist = len;
            best = c;
        }
    }
    if (best == -1) {
        for (int c = 0; c < numsamples; c++) {
            vec3_t d;
            for (int k = 0; k < 3; k++) d[k] = (float)samples[c].origin[k] - center[k];
            float len = (float)sqrt((double)d[0] * d[0] + (double)d[1] * d[1] + (double)d[2] * d[2]);
            if (len < bestdist) {
                bestdist = len;
                best = c;
            }
        }
    }
    return best;
}

/* Every other side with a specular material gets its nearest cubemap. */
void Cubemap_AttachDefaultCubemapToSpecularSides(void) {
    unsigned char *specular = xalloc(nummapbrushsides + 1), *manual = xalloc(nummapbrushsides + 1);
    int *entity = xalloc(sizeof(int) * (nummapbrushsides + 1));
    for (int i = 0; i < nummapbrushsides; i++) {
        entity[i] = -1;
        side_t *side = &brushsides[i];
        if (side->texinfo == TEXINFO_NODE) continue;
        cuberecord_t *r = Record(TexDataName(OriginalTexData(texinfos[side->texinfo].texdata)));
        specular[i] = r && r->specular;
    }
    for (int c = 0; c < numsamples; c++)
        for (int i = 0; i < sidelists[c].n; i++) {
            int index = SideIDToIndex(sidelists[c].ids[i]);
            if (index >= 0) manual[index] = 1;
        }
    for (int b = 0; b < nummapbrushes; b++)
        for (int j = 0; j < mapbrushes[b].numsides; j++)
            entity[mapbrushes[b].original_sides + j - brushsides] = mapbrushes[b].entitynum;
    for (int i = 0; i < nummapbrushsides; i++) {
        if (!specular[i] || manual[i]) continue;
        side_t *side = &brushsides[i];
        vec3_t origin = {0, 0, 0};
        if (entity[i] >= 0) VectorCopy(entities[entity[i]].origin, origin);
        int c = FindClosestCubemap(origin, side);
        if (c == -1) continue;
        SetSideTexinfo(side, Cubemap_CreateTexInfo(side->texinfo, samples[c].origin));
    }
    free(specular);
    free(manual);
    free(entity);
}

/* ------------------------------------------------------------------ blend materials on brush faces */
static wvtrecord_t *WvtRecord(const char *name) {
    LoadCubemapTable();
    for (int i = 0; i < numwvts; i++)
        if (!strcmp(wvts[i].name, name)) return &wvts[i];
    for (int i = 0; i < numwvts; i++)
        if (same_name(wvts[i].name, name)) return &wvts[i];
    return NULL;
}

/* maps/<map>/<material>_wvt_patch: a LightmappedGeneric copy of the blend material (vbsp's
   CreateBrushVersionOfWorldVertexTransitionMaterial). */
static int CreateBrushVersionOfWorldVertexTransitionMaterial(int texinfo, wvtrecord_t *w) {
    if (texinfo == TEXINFO_NODE) return texinfo;
    int td = texinfos[texinfo].texdata;
    const char *name = TexDataName(td);
    if (stristr_(name, "_wvt_patch")) return texinfo;
    char patched[700];
    int len = snprintf(patched, sizeof(patched), "maps/%s/%s_wvt_patch", g_mapbase, name);
    if (len >= 128 - 1) Error("Generated worldvertextransition patch name : %s too long! (max = %d)\n", patched, 128);
    for (char *c = patched; *c; c++) *c = *c == 92 ? '/' : (char)tolower((unsigned char)*c);
    int newtd = FindTexDataByName(patched);
    int had = newtd != -1;
    if (!had) {
        Warning("Patching WVT material: %s\n", patched);
        int cap = 4096, n = 0;
        char *text = xalloc(cap);
        for (int i = 0; i < w->nlines; i++) {
            int l = (int)strlen(w->lines[i]);
            while (n + l + 3 > cap) text = realloc(text, cap *= 2);
            memcpy(text + n, w->lines[i], l);
            n += l;
            text[n++] = '\r';
            text[n++] = '\n';
        }
        char path[800];
        snprintf(path, sizeof(path), "materials/%s.vmt", patched);
        AddFileToPak(path, (unsigned char *)text, n);
        newtd = CloneTexData(td, patched);
    }
    texinfo_t t = texinfos[texinfo];
    t.texdata = newtd;
    if (had) {
        int found = FindTexInfoExact(&t);
        if (found >= 0) return found;
    }
    return AppendTexinfo(&t);
}

void WorldVertexTransitionFixup(void) {
    for (int i = 0; i < nummapbrushsides; ++i) {
        side_t *side = &brushsides[i];
        if (side->disp || side->texinfo < 0) continue;
        wvtrecord_t *w = WvtRecord(TexDataName(texinfos[side->texinfo].texdata));
        if (!w) continue;
        side->texinfo = CreateBrushVersionOfWorldVertexTransitionMaterial(side->texinfo, w);
    }
}

/* Cubemaps no surface uses still get their texture. */
void Cubemap_AddUnreferencedCubemaps(void) {
    for (int c = 0; c < numsamples; c++) {
        char tex[600], file[700];
        PatchedName("c", samples[c].origin, 0, tex, sizeof(tex));
        snprintf(file, sizeof(file), "materials/%s.vtf", tex);
        int j;
        for (j = 0; j < numdefaultnames; j++)
            if (!_stricmp(defaultnames[j], tex)) break;     /* (vbsp compares against the bare name) */
        if (j == numdefaultnames) {
            defaultnames = realloc(defaultnames, sizeof(char *) * (numdefaultnames + 1));
            defaultnames[numdefaultnames++] = copystring(file);
        }
    }
}

int Cubemap_DefaultNames(const char ***names) {
    *names = (const char **)defaultnames;
    return numdefaultnames;
}

unsigned char *Cubemap_Lump(int *len) {
    *len = 16 * numsamples;
    return (unsigned char *)samples;
}

/* A patched material's value for a dependent material var (vbsp reads it through the patch): the
   dependent's patched name when the patch replaced it, else NULL. */
const char *Cubemap_PatchedValue(int texdata, const char *key) {
    static char out[600];
    for (int i = 0; i < numclones; i++) {
        if (clones[i].texdata != texdata) continue;
        cuberecord_t *r = Record(clones[i].material);
        if (!r || _stricmp(r->depvar, key)) return NULL;
        cuberecord_t *d = Record(r->dep);
        if (!d || !d->patchable) return NULL;
        PatchedName(r->dep, clones[i].origin, 1, out, sizeof(out));
        return out;
    }
    return NULL;
}
