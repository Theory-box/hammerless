/* Static prop vertex lighting (vrad -StaticPropLighting: CVradStaticPropMgr::ComputeLighting), as L4D2 does it.
 *
 * Every vertex of every static prop model (placed by the prop's origin and angles) gets the light of the
 * direct lights that see it (each at the vertex pushed 4 units toward the light, gathered the fast way,
 * the prop's own shadow left out if it's flagged so) plus the bounced light: rays over the vertex's
 * hemisphere picking up the lightmap colour (times reflectivity) of the faces they hit. A vertex inside a
 * wall is lit from a point crawled toward the prop's lighting origin or its nearest good vertex. The colours
 * are laid out like the model's .vtx (every LOD's strip groups) and stored as sp_hdr_<prop>.vhv files in
 * the map's pak lump, one 4-byte colour per vertex. Props flagged "no vertex lighting" get none. */
#include <float.h>
#include "hlvrad.h"

#define STATIC_PROP_USE_LIGHTING_ORIGIN 0x2
#define STATIC_PROP_IGNORE_NORMALS 0x8
#define STATIC_PROP_NO_PER_VERTEX_LIGHTING 0x40
#define STATIC_PROP_NO_SELF_SHADOWING 0x80
#define GATHERLFLAGS_FORCE_FAST 1
#define GATHERLFLAGS_IGNORE_NORMALS 2
#define PROP_RECORD 72
#define MAX_TRACE_LENGTH_F 56755.84f

extern __thread int g_gatherFlags, g_gatherSkipProp;
float GatherSampleLightAtPoint(const directlight_t *dl, const vec3_t pos, const vec3_t normal);
void LightSurfaceBegin(void);
int FindLightSurfaceKept(const vec3_t start, const vec3_t delta, int *hasluxel, float luxel[2]);
void AmbientSetup(void);
void VectorToColorRGBExp32(const vec3_t v, unsigned char *c);
void SkyDirectionAt(int i, vec3_t out);

int g_bStaticPropLighting;

typedef struct { vec3_t reflectivity; int name, width, height, view_width, view_height; } dtexdata_t;

/* ------------------------------------------------------------------ maths as mathlib has it */
/* AngleMatrix: float radians, the x87's sines and cosines rounded to float */
static void AngleMatrix(const float angles[3], const float *origin, float m[3][4]) {
    const float d2r = (float)(3.14159265358979323846 / 180.0);
    float y = angles[1] * d2r, p = angles[0] * d2r, r = angles[2] * d2r;
    float sy = (float)sin(y), cy = (float)cos(y), sp = (float)sin(p), cp = (float)cos(p), sr = (float)sin(r),
          cr = (float)cos(r);
    m[0][0] = cp * cy, m[1][0] = cp * sy, m[2][0] = -sp;
    float srsp = sr * sp, crsp = cr * sp;
    m[0][1] = srsp * cy - cr * sy;
    m[1][1] = srsp * sy + cr * cy;
    m[2][1] = sr * cp;
    m[0][2] = crsp * cy + sr * sy;
    m[1][2] = crsp * sy - sr * cy;
    m[2][2] = cr * cp;
    for (int k = 0; k < 3; k++) m[k][3] = origin ? origin[k] : 0.0f;
}

static void VectorTransform(const float *in, float m[3][4], vec3_t out) {
    for (int k = 0; k < 3; k++) out[k] = ((m[k][1] * in[1] + m[k][0] * in[0]) + m[k][2] * in[2]) + m[k][3];
}

static int PositionInSolid(const vec3_t p) { return dleafs[PointLeafnum(p)].contents & CONTENTS_SOLID; }

/* ------------------------------------------------------------------ the light at a vertex */
static FILE *splog;       /* (debugging, SPLOG: each call as vradhook logs vrad's) */
static void SpLog(int kind, const vec3_t pos, const vec3_t n, const vec3_t out, int a5, int a6) {
    if (!splog) return;
    float rec[13];
    memcpy(rec, &kind, 4), memcpy(rec + 1, pos, 12), memcpy(rec + 4, n, 12), memcpy(rec + 7, out, 12);
    memcpy(rec + 10, &a5, 4), memcpy(rec + 11, &a6, 4), rec[12] = 0;
    fwrite(rec, 4, 13, splog);
}

static void DirectLightingAtPoint(const vec3_t pos, const vec3_t normal, vec3_t out, int skipProp, int flags) {
    VectorClear(out);
    int cluster = ClusterFromPoint(pos);
    for (directlight_t *dl = activelights; dl; dl = dl->next) {
        if (dl->light.style) continue;
        if (!PVSCheck(dl->pvs, cluster)) continue;
        vec3_t adj;
        if (dl->light.type == emit_skyambient) {
            for (int k = 0; k < 3; k++) adj[k] = normal[k] * 4.0f + pos[k];       /* (out along the normal) */
        } else {
            vec3_t fudge;                                                         /* (toward the light) */
            if (dl->light.type == emit_skylight)
                for (int k = 0; k < 3; k++) fudge[k] = -dl->light.normal[k];
            else {
                VectorSubtract(dl->light.origin, pos, fudge);
                VectorNormalize(fudge);
            }
            for (int k = 0; k < 3; k++) adj[k] = pos[k] + fudge[k] * 4.0f;
        }
        g_gatherFlags = flags | GATHERLFLAGS_FORCE_FAST, g_gatherSkipProp = skipProp;
        float f = GatherSampleLightAtPoint(dl, adj, normal);
        g_gatherFlags = 0, g_gatherSkipProp = -1;
        for (int k = 0; k < 3; k++) out[k] = dl->light.intensity[k] * f + out[k];
    }
    SpLog(0, pos, normal, out, skipProp, flags);
}

/* a lightmap colour (ColorRGBExp32ToVector) */
static void DecodeRGBE(const unsigned char *c, vec3_t v) {
    for (int k = 0; k < 3; k++) v[k] = (float)c[k] * (float)(ldexp(1.0, (signed char)c[3]) / 255.0) * 255.0f;
}

/* L4D2's: no falloff with distance, and the full number of rays unless forced fast */
static int spiCount, spiTarget = -1;      /* (debugging, SPRAYK: one call's rays, as vradhook logs vrad's) */
static FILE *rayLog;
static void IndirectLightingAtPoint(const vec3_t pos, const vec3_t normal, vec3_t out, int forceFast, int ignoreNormals) {
    int logRays = rayLog && spiCount++ == spiTarget;
    const dtexdata_t *dtexdata = (const dtexdata_t *)lumps[LUMP_TEXDATA].data;
    VectorClear(out);
    int nsamples = g_bFast || forceFast ? NUMVERTEXNORMALS / 4 : (int)(1.0f * 162.0f);
    float totalDot = 0;
    LightSurfaceBegin();
    for (int j = 0; j < nsamples; j++) {
        vec3_t dir;
        SkyDirectionAt(j, dir);
        float dot = ignoreNormals ? 0.35355f : (normal[0] * dir[0] + normal[1] * dir[1]) + normal[2] * dir[2];
        if ((double)dot <= 0.001) continue;
        totalDot += dot;
        vec3_t delta;
        for (int k = 0; k < 3; k++) delta[k] = (pos[k] + dir[k] * MAX_TRACE_LENGTH_F) - pos[k];
        int hasluxel;
        float luxel[2];
        int s = FindLightSurfaceKept(pos, delta, &hasluxel, luxel);
        if (logRays) {
            float rec[12];
            memcpy(rec, pos, 12), memcpy(rec + 3, delta, 12), memcpy(rec + 6, &s, 4);
            extern __thread float g_lastHitFrac;
            rec[7] = g_lastHitFrac, rec[8] = luxel[0], rec[9] = luxel[1], rec[10] = (float)hasluxel, rec[11] = s < 0;
            fwrite(rec, 4, 12, rayLog);
        }
        if (s < 0) continue;
        const dface_t *f = &g_pFaces[s];
        const texinfo_t *tx = &texinfo[f->texinfo];
        if (tx->flags & SURF_SKY) continue;
        if (f->styles[0] == 255 || f->lightofs < 0) continue;
        vec3_t c;
        if (!hasluxel) {
            DecodeRGBE(dlightdata + f->lightofs - 4, c);
        } else {
            int smax = f->m_LightmapTextureSizeInLuxels[0] + 1, tmax = f->m_LightmapTextureSizeInLuxels[1] + 1;
            int ds = (int)luxel[0], dt = (int)luxel[1];
            ds = ds > smax - 1 ? smax - 1 : ds < 0 ? 0 : ds;
            dt = dt > tmax - 1 ? tmax - 1 : dt < 0 ? 0 : dt;
            DecodeRGBE(dlightdata + f->lightofs + 4 * (dt * smax + ds), c);
        }
        for (int k = 0; k < 3; k++) out[k] = out[k] + dtexdata[tx->texdata].reflectivity[k] * c[k];
    }
    if (totalDot != 0) {
        float s = 1.0f / totalDot;
        VectorScale(out, s, out);
    }
    SpLog(1, pos, normal, out, forceFast, ignoreNormals);
}

/* ------------------------------------------------------------------ colours as the .vhv stores them */
static float lineartovertex[4096];

static void BuildVertexLightTable(void) {
    /* mathlib's BuildGammaTable(2.2, 2.2, 0, overbright 2) */
    double g = 1.0 / (double)2.2f;
    for (int i = 0; i < 4096; i++) {
        float f = (float)pow((double)i * 0.0009765625, g);
        f = f * 0.5f;
        lineartovertex[i] = f > 1.0f ? 1.0f : f;
    }
}

static int RoundX87(float f) { return (int)lrintf(f); }

/* ConvertRGBExp32ToRGBA8888 */
static void RGBExpToRGBA8888(const unsigned char *rgbe, unsigned char *out) {
    float scale = (float)(ldexp(1.0, (signed char)rgbe[3]) / 255.0);
    float v[3];
    for (int k = 0; k < 3; k++) {
        int i = RoundX87((float)rgbe[k] * scale * 1024.0f);
        if ((unsigned)i > 0xfff) i = i < 0 ? 0 : 0xfff;
        v[k] = lineartovertex[i];
    }
    float gb = v[1] > v[2] ? v[1] : v[2];
    float mx = v[0] > gb ? v[0] : gb;
    if (mx > 1.0f) {
        float s = 1.0f / mx;
        for (int k = 0; k < 3; k++) v[k] = v[k] * s;
    }
    for (int k = 0; k < 3; k++) {
        if (0.0f > v[k]) v[k] = 0.0f;
        out[k] = (unsigned char)RoundX87(v[k] * 255.0f);
    }
    out[3] = 255;
}

/* ------------------------------------------------------------------ the pak lump (a stored zip) */
static unsigned int Crc32(const unsigned char *d, int n) {
    unsigned int c = 0xffffffffu;
    for (int i = 0; i < n; i++) {
        c ^= d[i];
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1)));
    }
    return ~c;
}

typedef struct { char *name; unsigned char *data; int len; unsigned int crc; } pakfile_t;
static pakfile_t *newfiles;
static int nnewfiles;

static void AddToPak(const char *name, const unsigned char *data, int len) {
    newfiles = realloc(newfiles, sizeof(pakfile_t) * (nnewfiles + 1));
    pakfile_t *f = &newfiles[nnewfiles++];
    f->name = _strdup(name);
    f->data = xalloc(len + 1);
    memcpy(f->data, data, len);
    f->len = len;
    f->crc = Crc32(data, len);
}

static void Put16(unsigned char **o, int v) { (*o)[0] = (unsigned char)v, (*o)[1] = (unsigned char)(v >> 8), *o += 2; }
static void Put32(unsigned char **o, unsigned int v) { memcpy(*o, &v, 4), *o += 4; }

/* the old entries as they were, the new ones after them; a central directory for all, the old comment */
static void WritePak(void) {
    const lump_t *l = &lumps[LUMP_PAKFILE];
    const unsigned char *z = l->data;
    int zlen = l->len, cdofs = zlen, count = 0;
    const unsigned char *comment = NULL;
    int clen = 0;
    for (int p = zlen - 22; p >= 0; p--) {
        unsigned int sig;
        memcpy(&sig, z + p, 4);
        if (sig != 0x06054b50) continue;
        unsigned short n, cl;
        memcpy(&n, z + p + 10, 2), memcpy(&cdofs, z + p + 16, 4), memcpy(&cl, z + p + 20, 2);
        count = n, comment = z + p + 22, clen = cl;
        break;
    }
    /* the old central entries, without any we replace */
    int total = cdofs, cdsize = 0, kept = 0;
    unsigned char **keep = xalloc(sizeof(char *) * (count + 1));
    int *keeplen = xalloc(sizeof(int) * (count + 1));
    for (int i = 0, p = cdofs; i < count; i++) {
        unsigned short nl, el, cl;
        memcpy(&nl, z + p + 28, 2), memcpy(&el, z + p + 30, 2), memcpy(&cl, z + p + 32, 2);
        int dup = 0;
        for (int k = 0; k < nnewfiles; k++)
            if ((int)strlen(newfiles[k].name) == nl && !_strnicmp(newfiles[k].name, (const char *)z + p + 46, nl)) dup = 1;
        int sz = 46 + nl + el + cl;
        if (!dup) keep[kept] = (unsigned char *)z + p, keeplen[kept++] = sz, cdsize += sz;
        p += sz;
    }
    for (int k = 0; k < nnewfiles; k++) total += 30 + (int)strlen(newfiles[k].name) + newfiles[k].len;
    for (int k = 0; k < nnewfiles; k++) cdsize += 46 + (int)strlen(newfiles[k].name);
    static const char xzp[32] = "XZP1 0";
    if (!comment) comment = (const unsigned char *)xzp, clen = 32;
    unsigned char *out = xalloc(total + cdsize + 22 + clen + 1), *o = out;
    memcpy(o, z, cdofs), o += cdofs;
    int *ofs = xalloc(sizeof(int) * (nnewfiles + 1));
    for (int k = 0; k < nnewfiles; k++) {
        pakfile_t *f = &newfiles[k];
        int nl = (int)strlen(f->name);
        ofs[k] = (int)(o - out);
        Put32(&o, 0x04034b50), Put16(&o, 10), Put16(&o, 0), Put16(&o, 0), Put16(&o, 0), Put16(&o, 0);
        Put32(&o, f->crc), Put32(&o, f->len), Put32(&o, f->len), Put16(&o, nl), Put16(&o, 0);
        memcpy(o, f->name, nl), o += nl;
        memcpy(o, f->data, f->len), o += f->len;
    }
    int cdstart = (int)(o - out);
    for (int i = 0; i < kept; i++) memcpy(o, keep[i], keeplen[i]), o += keeplen[i];
    for (int k = 0; k < nnewfiles; k++) {
        pakfile_t *f = &newfiles[k];
        int nl = (int)strlen(f->name);
        Put32(&o, 0x02014b50), Put16(&o, 20), Put16(&o, 10), Put16(&o, 0), Put16(&o, 0), Put16(&o, 0), Put16(&o, 0);
        Put32(&o, f->crc), Put32(&o, f->len), Put32(&o, f->len), Put16(&o, nl), Put16(&o, 0), Put16(&o, 0);
        Put16(&o, 0), Put16(&o, 0), Put32(&o, 0), Put32(&o, ofs[k]);
        memcpy(o, f->name, nl), o += nl;
    }
    int n = kept + nnewfiles;
    Put32(&o, 0x06054b50), Put16(&o, 0), Put16(&o, 0), Put16(&o, n), Put16(&o, n);
    Put32(&o, (unsigned int)(o - out - cdstart - 12)), Put32(&o, cdstart), Put16(&o, clen);
    memcpy(o, comment, clen), o += clen;
    SetLump(LUMP_PAKFILE, out, (int)(o - out), l->version);
    free(keep), free(keeplen), free(ofs);
}

/* ------------------------------------------------------------------ models */
static unsigned char *ReadFileAll(const char *model, const char *ext, int *len) {
    if (!g_modeldir) return NULL;
    char path[1400];
    snprintf(path, sizeof(path), "%s/%s", g_modeldir, model);
    for (char *q = path; *q; q++)
        if (*q == '\\') *q = '/';
    char *dot = strrchr(path, '.'), *slash = strrchr(path, '/');
    if (dot && (!slash || dot > slash)) *dot = 0;
    strncat(path, ext, sizeof(path) - strlen(path) - 1);
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    *len = (int)ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *b = xalloc(*len + 1);
    if (fread(b, 1, *len, f) != (size_t)*len) *len = 0;
    fclose(f);
    return b;
}

static int I32(const unsigned char *p) { int v; memcpy(&v, p, 4); return v; }
static int U16(const unsigned char *p) { unsigned short v; memcpy(&v, p, 2); return v; }

typedef struct { vec3_t color; vec3_t pos; int valid; } colorvert_t;
typedef struct { vec3_t pos, normal; int index; } badvert_t;

/* one prop: its vertexes' colours, then the .vhv */
static void LightProp(int propIndex, const unsigned char *rec, const unsigned char *mdl, int mlen, const unsigned char *vvd,
                      int vlen, const unsigned char *vtx, int xlen) {
    vec3_t origin, angles, lightingOrigin;
    memcpy(origin, rec, 12), memcpy(angles, rec + 12, 12), memcpy(lightingOrigin, rec + 44, 12);
    int flags = rec[31];
    int originValid = (flags & STATIC_PROP_USE_LIGHTING_ORIGIN) != 0;
    int skip = (flags & STATIC_PROP_NO_SELF_SHADOWING) ? propIndex : -1;
    int gflags = (flags & STATIC_PROP_IGNORE_NORMALS) ? GATHERLFLAGS_IGNORE_NORMALS : 0;
    float matPos[3][4], matNormal[3][4];
    AngleMatrix(angles, origin, matPos);
    AngleMatrix(angles, NULL, matNormal);
    int vstart = I32(vvd + 56);
    int numbp = I32(mdl + 232), bpindex = I32(mdl + 236);
    /* the meshes the .vhv lists (every strip group of every LOD) and their colours */
    int nmeshes = 0, totalverts = 0, cap = 16;
    int *meshlod = xalloc(sizeof(int) * cap), *meshn = xalloc(sizeof(int) * cap);
    unsigned char **meshcol = xalloc(sizeof(char *) * cap);
    int vtxlods = I32(vtx + 20), vtxbp = I32(vtx + 32);
    for (int b = 0; b < numbp; b++) {
        int bp = bpindex + 16 * b, nmodels = I32(mdl + bp + 4), modelindex = I32(mdl + bp + 12);
        int xbp = vtxbp + 8 * b;
        for (int m = 0; m < nmodels; m++) {
            int sub = bp + modelindex + 148 * m;
            int nm = I32(mdl + sub + 72), meshindex = I32(mdl + sub + 76), numverts = I32(mdl + sub + 80);
            int first = I32(mdl + sub + 84) / 48;
            colorvert_t *cv = xalloc(sizeof(colorvert_t) * (numverts + 1));
            badvert_t *bad = NULL;
            int nbad = 0, n = 0;
            for (int mm = 0; mm < nm; mm++) {
                int mesh = sub + meshindex + 116 * mm, nv = I32(mdl + mesh + 8), vofs = I32(mdl + mesh + 12);
                for (int v = 0; v < nv; v++, n++) {
                    const unsigned char *vert = vvd + vstart + 48 * (first + vofs + v);
                    vec3_t pos, normal;
                    VectorTransform((const float *)(vert + 16), matPos, pos);
                    VectorTransform((const float *)(vert + 28), matNormal, normal);
                    if (n >= numverts) continue;
                    if (PositionInSolid(pos)) {
                        bad = realloc(bad, sizeof(badvert_t) * (nbad + 1));
                        VectorCopy(pos, bad[nbad].pos), VectorCopy(normal, bad[nbad].normal), bad[nbad].index = n;
                        nbad++;
                        continue;
                    }
                    vec3_t direct, indirect = {0, 0, 0};
                    DirectLightingAtPoint(pos, normal, direct, skip, gflags);
                    if (g_numbounce >= 1) IndirectLightingAtPoint(pos, normal, indirect, 0, (flags & STATIC_PROP_IGNORE_NORMALS) != 0);
                    cv[n].valid = 1;
                    VectorCopy(pos, cv[n].pos);
                    VectorAdd(indirect, direct, cv[n].color);
                }
            }
            /* vertexes in solid: lit from a point crawled toward the lighting origin or the nearest good vertex */
            if (nbad && (originValid || nbad != n)) {
                for (int k = 0; k < nbad; k++) {
                    vec3_t best;
                    if (originValid) {
                        VectorCopy(lightingOrigin, best);
                    } else {
                        int bi = 0;
                        float closest = FLT_MAX;
                        for (int c = 0; c < n; c++) {
                            if (!cv[c].valid) continue;
                            vec3_t d;
                            VectorSubtract(cv[c].pos, bad[k].pos, d);
                            float dist = sqrtf((d[1] * d[1] + d[2] * d[2]) + d[0] * d[0]);
                            if (dist < closest) closest = dist, bi = c;
                        }
                        VectorCopy(cv[bi].pos, best);
                    }
                    for (int it = 19; it > 0; it--) {
                        vec3_t mid;
                        for (int c = 0; c < 3; c++) mid[c] = (bad[k].pos[c] + best[c]) * 0.5f;
                        if (PositionInSolid(mid)) break;
                        VectorCopy(mid, best);
                    }
                    vec3_t direct, indirect;
                    DirectLightingAtPoint(best, bad[k].normal, direct, -1, 0);
                    IndirectLightingAtPoint(best, bad[k].normal, indirect, 1, 0);
                    VectorCopy(best, cv[bad[k].index].pos);
                    VectorAdd(indirect, direct, cv[bad[k].index].color);
                }
            }
            free(bad);
            /* the .vtx layout: per LOD, per mesh, per strip group, its vertexes' colours */
            int xmodel = xbp + I32(vtx + xbp + 4) + 8 * m;
            for (int lod = 0; lod < vtxlods; lod++) {
                int xlod = xmodel + I32(vtx + xmodel + 4) + 12 * lod;
                for (int mm = 0; mm < nm; mm++) {
                    int mesh = sub + meshindex + 116 * mm, vofs = I32(mdl + mesh + 12);
                    int xmesh = xlod + I32(vtx + xlod + 4) + 9 * mm;
                    int ngroups = I32(vtx + xmesh);
                    for (int g = 0; g < ngroups; g++) {
                        int xsg = xmesh + I32(vtx + xmesh + 4) + 25 * g;
                        int nv = I32(vtx + xsg), xv = xsg + I32(vtx + xsg + 4);
                        if (nmeshes == cap) {
                            cap *= 2;
                            meshlod = realloc(meshlod, sizeof(int) * cap), meshn = realloc(meshn, sizeof(int) * cap);
                            meshcol = realloc(meshcol, sizeof(char *) * cap);
                        }
                        unsigned char *col = xalloc(4 * nv + 1);
                        for (int v = 0; v < nv; v++) {
                            int idx = vofs + U16(vtx + xv + 9 * v + 4);
                            unsigned char rgbe[4], rgba[4];
                            static const vec3_t zero = {0, 0, 0};
                            VectorToColorRGBExp32(idx < numverts ? cv[idx].color : zero, rgbe);
                            RGBExpToRGBA8888(rgbe, rgba);
                            col[4 * v] = rgba[2], col[4 * v + 1] = rgba[1], col[4 * v + 2] = rgba[0], col[4 * v + 3] = rgba[3];
                        }
                        meshlod[nmeshes] = lod, meshn[nmeshes] = nv, meshcol[nmeshes] = col;
                        nmeshes++, totalverts += nv;
                    }
                }
            }
            free(cv);
        }
    }
    (void)mlen, (void)vlen, (void)xlen;
    /* the .vhv: a header, the meshes, the colours from 512 on, the whole padded to 512 */
    int start = (40 + 28 * nmeshes + 511) & ~511;
    int size = (start + 4 * totalverts + 511) & ~511;
    unsigned char *f = xalloc(size + 1);
    memset(f, 0, size);
    int hdr[6] = {2, I32(mdl + 8), 4, 4, totalverts, nmeshes};
    memcpy(f, hdr, sizeof(hdr));
    int at = start;
    for (int i = 0; i < nmeshes; i++) {
        int mh[3] = {meshlod[i], meshn[i], at};
        memcpy(f + 40 + 28 * i, mh, sizeof(mh));
        memcpy(f + at, meshcol[i], 4 * meshn[i]);
        at += 4 * meshn[i];
        free(meshcol[i]);
    }
    char name[64];
    snprintf(name, sizeof(name), g_bHDR ? "sp_hdr_%d.vhv" : "sp_%d.vhv", propIndex);
    AddToPak(name, f, size);
    free(f), free(meshlod), free(meshn), free(meshcol);
}

void ComputeStaticPropLighting(void) {
    if (!g_bStaticPropLighting || g_bFast) return;
    int len;
    const unsigned char *g = GameLump(0x73707270 /* 'sprp' */, &len);
    if (!g || len < 12) return;
    AmbientSetup();
    BuildVertexLightTable();
    if (getenv("SPLOG")) splog = fopen("splog_ours.bin", "wb");
    if (getenv("SPRAYK")) spiTarget = atoi(getenv("SPRAYK")), rayLog = fopen("sprays_ours.bin", "wb");
    int numdict = I32(g);
    const unsigned char *names = g + 4, *p = g + 4 + 128 * numdict;
    int numleaves = I32(p);
    p += 4 + 2 * numleaves;
    int numprops = I32(p);
    p += 4;
    Msg("Computing static prop lighting : %d props\n", numprops);
    for (int i = 0; i < numprops; i++) {
        const unsigned char *rec = p + PROP_RECORD * i;
        if (rec[31] & STATIC_PROP_NO_PER_VERTEX_LIGHTING) continue;
        int model = U16(rec + 24);
        if (model >= numdict) continue;
        char name[129];
        memcpy(name, names + 128 * model, 128), name[128] = 0;
        int mlen = 0, vlen = 0, xlen = 0;
        unsigned char *mdl = ReadFileAll(name, ".mdl", &mlen), *vvd = ReadFileAll(name, ".vvd", &vlen),
                      *vtx = ReadFileAll(name, ".dx90.vtx", &xlen);
        if (mdl && vvd && vtx && mlen >= 240 && vlen >= 64 && xlen >= 36 && !memcmp(mdl, "IDST", 4) && !memcmp(vvd, "IDSV", 4))
            LightProp(i, rec, mdl, mlen, vvd, vlen, vtx, xlen);
        else
            Msg("Warning: static prop %d (%s): model files missing, not lit\n", i, name);
        free(mdl), free(vvd), free(vtx);
    }
    if (nnewfiles) WritePak();
    if (splog) fclose(splog);
    if (rayLog) fclose(rayLog);
}
