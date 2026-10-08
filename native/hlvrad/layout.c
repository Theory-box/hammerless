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

/* the face's area as vrad's WindingArea measures it (a fan of triangles from the first corner) */
static float FaceArea(const dface_t *f) {
    float total = 0;
    vec3_t p0, d1, d2, cross;
    VectorCopy(dvertexes[EdgeVertex(f, 0)].point, p0);
    for (int i = 2; i < f->numedges; i++) {
        VectorSubtract(dvertexes[EdgeVertex(f, i - 1)].point, p0, d1);
        VectorSubtract(dvertexes[EdgeVertex(f, i)].point, p0, d2);
        cross[0] = d1[1] * d2[2] - d1[2] * d2[1];
        cross[1] = d1[2] * d2[0] - d1[0] * d2[2];
        cross[2] = d1[0] * d2[1] - d1[1] * d2[0];
        total += 0.5f * sqrtf(DotProduct(cross, cross));
    }
    return total;
}

int FaceHasPatches(int facenum) {
    const dface_t *f = &g_pFaces[facenum];
    if (f->dispinfo != -1) return 1;
    return FaceArea(f) > 0;
}

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
