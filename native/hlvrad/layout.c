/* Which faces get lightmaps, their light styles and where their data goes in the lighting lump
 * (vrad's MakePatches face test, the start of BuildFacelights, PrecompLightmapOffsets).
 *
 * A face gets lightmaps unless its texture is sky or unlit, or it is a flat face with no area. Style 0 is
 * always there; switchable lights add theirs. In the lighting lump each face has first one average
 * colour per style (in reverse style order), then per style the lightmap, and for bumped materials three
 * more (one per bump direction); a lightmap is (size+1) x (size+1) samples of 4 bytes. */
#include "hlvrad.h"

texinfo_t *texinfo; int numtexinfo;
unsigned char *dlightdata; int lightdatasize;

vec3_t *face_offset;              /* per face: its brush entity's origin (faces are lit where they stand) */
vec3_t *face_centroids;           /* per face: the centre of its polygon (corners averaged), without the offset */
entity_t **face_entity;
static unsigned char *face_has_patches;
static unsigned char *face_nonrendering;

static int NonRenderingEntity(const entity_t *e) {
    if (!e) return 0;
    const char *cls = ValueForKey(e, "classname");
    if (!_strnicmp(cls, "trigger_", 8)) return 1;
    const char *names[] = {"func_nav_attribute_region","func_nav_avoid","func_nav_prefer","func_simpleladder","func_precipitation","func_precipitation_blocker","func_dustcloud","func_dustmotes","func_smokevolume","func_clip_vphysics","fog_volume","info_changelevel","func_nav_blocker","func_viscluster","func_areaportal","func_areaportalwindow","func_occluder","func_ladder", NULL};
    for (int i = 0; names[i]; i++) if (!_stricmp(cls, names[i])) return 1;
    return 0;
}

static int NonRenderingMaterial(const texinfo_t *tx) {
    if (tx->flags & (SURF_NODRAW | 0x40 | 0x100 | 0x200)) return 1; /* hint, skip */
    if (tx->texdata < 0) return 0;
    const char *name = TexDataName(tx->texdata);
    const char *names[] = {"tools/toolsnodraw","tools/toolstrigger","tools/fogvolume","tools/toolshint","tools/toolsskip","tools/toolsclip","tools/toolsplayerclip","tools/toolsnpcclip","tools/toolsinvisible","tools/toolsinvisibleladder","tools/toolsblocklight","tools/toolsblock_los","tools/toolsareaportal","tools/toolsoccluder","tools/toolsorigin", NULL};
    for (int i = 0; names[i]; i++) if (!_stricmp(name, names[i])) return 1;
    return 0;
}

int FaceIsNonRendering(int facenum) { return face_nonrendering[facenum]; }


static const entity_t *EntityForModel(int model) {
    if (model == 0) return num_entities ? &entities[0] : NULL;
    char name[16];
    snprintf(name, sizeof(name), "*%d", model);
    for (int i = 0; i < num_entities; i++)
        if (!strcmp(ValueForKey(&entities[i], "model"), name)) return &entities[i];
    return NULL;
}

/* vrad's MakePatches face test: a face of a model gets patches (and so lightmaps) unless it is a flat
 * face whose polygon, nearly straight corners dropped, has no area. Displacements always do. */
void FindFacePatches(void) {
    const dmodel_t *models = (const dmodel_t *)lumps[LUMP_MODELS].data;
    int nummodels = lumps[LUMP_MODELS].len / sizeof(dmodel_t);
    face_offset = xalloc(sizeof(vec3_t) * (numfaces + 1));
    face_entity = xalloc(sizeof(entity_t *) * (numfaces + 1));
    face_has_patches = xalloc(numfaces + 1);
    face_nonrendering = xalloc(numfaces + 1);
    int skipped = 0;
    face_centroids = xalloc(sizeof(vec3_t) * (numfaces + 1));
    for (int m = 0; m < nummodels; m++) {
        const entity_t *e = EntityForModel(m);
        vec3_t origin = {0, 0, 0};
        if (e) GetVectorForKey(e, "origin", origin);
        for (int j = 0; j < models[m].numfaces; j++) {
            int fn = models[m].firstface + j;
            if (fn < 0 || fn >= numfaces) continue;
            face_entity[fn] = (entity_t *)e;
            VectorCopy(origin, face_offset[fn]);
            if (NonRenderingEntity(e) || NonRenderingMaterial(&texinfo[g_pFaces[fn].texinfo])) {
                face_nonrendering[fn] = 1;
                g_pFaces[fn].lightofs = -1;
                memset(g_pFaces[fn].styles, 255, 4);
                skipped++;
                continue; /* No samples, lightmap allocation or bounce patches; shadow brushes are unchanged. */
            }
            if (g_pFaces[fn].dispinfo != -1) {
                face_has_patches[fn] = 1;
                continue;
            }
            winding_t *w = WindingFromFace(&g_pFaces[fn], origin);
            face_has_patches[fn] = WindingArea(w) > 0;
            if (face_has_patches[fn]) {
                vec3_t c;
                WindingCenter(w, c);
                VectorSubtract(c, origin, face_centroids[fn]);
            }
            FreeWinding(w);
        }
    }
    Msg("Skipped light reception on %d non-rendering faces (shadow geometry retained).\n", skipped);
}

int FaceHasPatches(int facenum) { return face_has_patches[facenum]; }

void AssignLightStyles(void) {
    for (int i = 0; i < numfaces; i++) {
        dface_t *f = &g_pFaces[i];
        f->lightofs = -1;
        memset(f->styles, 255, 4);
        if (texinfo[f->texinfo].flags & TEX_SPECIAL) continue;
        if (!FaceHasPatches(i)) continue;
        f->styles[0] = 0;
    }
}

void PrecompLightmapOffsets(void) {
    lightdatasize = 0;
    for (int i = 0; i < numfaces; i++) {
        dface_t *f = &g_pFaces[i];
        if (texinfo[f->texinfo].flags & TEX_SPECIAL) continue;
        int nstyles;
        for (nstyles = 0; nstyles < 4; nstyles++)
            if (f->styles[nstyles] == 255) break;
        if (!nstyles) continue;
        lightdatasize += nstyles * 4;            /* the average colours */
        f->lightofs = lightdatasize;
        int luxels = (f->m_LightmapTextureSizeInLuxels[0] + 1) * (f->m_LightmapTextureSizeInLuxels[1] + 1);
        int maps = texinfo[f->texinfo].flags & SURF_BUMPLIGHT ? 4 : 1;
        lightdatasize += luxels * 4 * nstyles * maps;
    }
    dlightdata = xalloc(lightdatasize + 1);
}

float face_entity_minlight(int facenum) {
    return face_entity[facenum] ? FloatForKey(face_entity[facenum], "_minlight") : 0.0f;
}
