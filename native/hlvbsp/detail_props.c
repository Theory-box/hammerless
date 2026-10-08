/* Detail props (game lump "dprp" version 4): grass and the like scattered over faces whose material
 * has a %detailtype, using the kinds described in the game's detail.vbsp, plus prop_detail /
 * prop_detail_sprite entities. Placement is random but repeatable: each face seeds the C library's
 * rand() and the game's random stream with its Hammer side id, as vbsp does, so the same map gets the
 * same grass. The objects are sorted by leaf with the MSVC library's quicksort (its order for equal
 * leaves is part of the result). */
#include <ctype.h>
#include <float.h>
#include "hlvbsp.h"
#include "disp.h"

/* atan2 in degrees as vbsp gets it (x87): double throughout, times RAD2DEG's float constant, rounded once */
static float atan2_deg(double y, double x) { return (float)(atan2(y, x) * (double)(float)(180.0 / M_PI)); }

const char *g_detail_file;               /* detail.vbsp text (Python reads it from the game) */

/* ------------------------------------------------------------------ the random numbers */
static unsigned long holdrand = 1;
static void crt_srand(unsigned seed) { holdrand = seed; }
static int crt_rand(void) { return (int)(((holdrand = holdrand * 214013L + 2531011L) >> 16) & 0x7fff); }
/* rand() / VALVE_RAND_MAX as vbsp compiles it: times the reciprocal */
static float rand01(void) { return (float)crt_rand() * (1.0f / 0x7fff); }
#define VALVE_RAND_MAX 0x7fff

/* vstdlib's uniform stream (Numerical Recipes' ran1) and its Gaussian stream */
#define IA 16807
#define IM 2147483647
#define IQ 127773
#define IR 2836
#define NTAB 32
#define NDIV (1 + (IM - 1) / NTAB)
#define AM (1.0 / IM)
#define RNMX (1.0 - 1.2e-7)
static int r_idum, r_iy, r_iv[NTAB];
static int g_have;
static float g_value;

static void RandomSeed(int seed) {
    r_idum = seed < 0 ? seed : -seed;
    r_iy = 0;
}

static int GenerateRandomNumber(void) {
    int j, k;
    if (r_idum <= 0 || !r_iy) {
        if (-(r_idum) < 1) r_idum = 1;
        else r_idum = -(r_idum);
        for (j = NTAB + 7; j >= 0; j--) {
            k = r_idum / IQ;
            r_idum = IA * (r_idum - k * IQ) - IR * k;
            if (r_idum < 0) r_idum += IM;
            if (j < NTAB) r_iv[j] = r_idum;
        }
        r_iy = r_iv[0];
    }
    k = r_idum / IQ;
    r_idum = IA * (r_idum - k * IQ) - IR * k;
    if (r_idum < 0) r_idum += IM;
    j = r_iy / NDIV;
    if (j >= NTAB || j < 0) j &= NTAB - 1;
    r_iy = r_iv[j];
    r_iv[j] = r_idum;
    return r_iy;
}

/* vstdlib's Gaussian stream, as its x87 code computes it (read from the binary): the uniform values
   come back unrounded, v1 is stored as a float but v2 stays unrounded, rsq is a float sum (v2 first),
   the result is rounded only by the caller; the cached second value is plain float maths. */
static double RandomDouble01(void) {
    double fl = AM * GenerateRandomNumber();
    if (fl > RNMX) fl = (float)RNMX;
    return fl;
}

static double RandomGaussian(float mean, float stddev) {
    if (!g_have) {
        float v1, rsq;
        double v2;
        do {
            v1 = (float)(2.0 * RandomDouble01() - 1.0);
            v2 = 2.0 * RandomDouble01() - 1.0;
            float v2f = (float)v2;
            rsq = v2f * v2f + v1 * v1;
        } while ((rsq > 1.0f) || (rsq == 0.0f));
        double fac = sqrt(log((double)rsq) * -2.0 / rsq);
        g_value = (float)(v1 * fac);
        g_have = 1;
        return v2 * fac * stddev + mean;
    }
    g_have = 0;
    return g_value * stddev + mean;
}

/* ------------------------------------------------------------------ KeyValues (detail.vbsp) */
typedef struct kv_s {
    char *name, *value;          /* value NULL: a block */
    struct kv_s *child, *next;
} kv_t;

static const char *kv_at;
static int kv_token(char *out, int max, int *quoted) {
    for (;;) {
        while (*kv_at && isspace((unsigned char)*kv_at)) kv_at++;
        if (kv_at[0] == '/' && kv_at[1] == '/') {
            while (*kv_at && *kv_at != '\n') kv_at++;
            continue;
        }
        break;
    }
    if (!*kv_at) return 0;
    int n = 0;
    *quoted = 0;
    if (*kv_at == '"') {
        *quoted = 1;
        kv_at++;
        while (*kv_at && *kv_at != '"') {
            if (n < max - 1) out[n++] = *kv_at;
            kv_at++;
        }
        if (*kv_at) kv_at++;
    } else if (*kv_at == '{' || *kv_at == '}') {
        out[n++] = *kv_at++;
    } else {
        while (*kv_at && !isspace((unsigned char)*kv_at) && *kv_at != '{' && *kv_at != '}' && *kv_at != '"') {
            if (n < max - 1) out[n++] = *kv_at;
            kv_at++;
        }
    }
    out[n] = 0;
    return 1;
}

static kv_t *kv_parse_block(void) {
    kv_t *first = NULL, **tail = &first;
    char name[1024], tok[4096];
    int q;
    while (kv_token(name, sizeof(name), &q)) {
        if (!q && !strcmp(name, "}")) break;
        if (!kv_token(tok, sizeof(tok), &q)) break;
        if (!q && tok[0] == '[') {                   /* a condition like [$X360]: skip it */
            if (!kv_token(tok, sizeof(tok), &q)) break;
        }
        kv_t *k = xalloc(sizeof(*k));
        k->name = copystring(name);
        if (!q && !strcmp(tok, "{")) k->child = kv_parse_block();
        else {
            k->value = copystring(tok);
            /* a value can be followed by a condition */
            const char *save = kv_at;
            char cond[64];
            int cq;
            if (kv_token(cond, sizeof(cond), &cq) && !cq && cond[0] == '[') {
            } else kv_at = save;
        }
        *tail = k;
        tail = &k->next;
    }
    return first;
}

static kv_t *kv_find(kv_t *k, const char *name) {
    for (; k; k = k->next)
        if (!_stricmp(k->name, name)) return k;
    return NULL;
}
static float kv_float(kv_t *block, const char *name, float def) {
    kv_t *k = kv_find(block, name);
    return k && k->value ? (float)atof(k->value) : def;
}
static int kv_int(kv_t *block, const char *name, int def) {
    kv_t *k = kv_find(block, name);
    return k && k->value ? atoi(k->value) : def;
}
static const char *kv_string(kv_t *block, const char *name) {
    kv_t *k = kv_find(block, name);
    return k && k->value ? k->value : NULL;
}

/* ------------------------------------------------------------------ the detail kinds */
enum { TYPE_MODEL, TYPE_SPRITE, TYPE_SHAPE_CROSS, TYPE_SHAPE_TRI };
typedef struct {
    char model[260];
    float amount, mincos, maxcos;
    int flags, orientation, type;
    float pos[2][2], tex[2][2];
    float scalestddev;
    unsigned char shapesize, shapeangle, sway;
} detailmodel_t;
typedef struct { float alpha; detailmodel_t *models; int nmodels; } detailgroup_t;
typedef struct { char name[128]; float density; detailgroup_t *groups; int ngroups; } detailobject_t;

static detailobject_t *dobjects;
static int numdobjects;

static void ParseDetailGroup(detailobject_t *d, kv_t *block) {
    float alpha = kv_float(block->child, "alpha", 1.0f);
    int i = d->ngroups;
    while (--i >= 0)
        if (alpha > d->groups[i].alpha) break;
    i++;                                        /* insert after i */
    d->groups = realloc(d->groups, sizeof(detailgroup_t) * (d->ngroups + 1));
    memmove(d->groups + i + 1, d->groups + i, sizeof(detailgroup_t) * (d->ngroups - i));
    d->ngroups++;
    detailgroup_t *g = &d->groups[i];
    memset(g, 0, sizeof(*g));
    g->alpha = alpha;
    float total = 0.0f;
    for (kv_t *it = block->child; it; it = it->next) {
        if (!it->child) continue;
        g->models = realloc(g->models, sizeof(detailmodel_t) * (g->nmodels + 1));
        detailmodel_t *m = &g->models[g->nmodels++];
        memset(m, 0, sizeof(*m));
        const char *model = kv_string(it->child, "model");
        if (model) {
            strncpy(m->model, model, sizeof(m->model) - 1);
            m->type = TYPE_MODEL;
        } else {
            const char *sprite = kv_string(it->child, "sprite");
            if (sprite) {
                const char *shape = kv_string(it->child, "sprite_shape");
                m->type = TYPE_SPRITE;
                if (shape && !_stricmp(shape, "cross")) m->type = TYPE_SHAPE_CROSS;
                else if (shape && !_stricmp(shape, "tri")) m->type = TYPE_SHAPE_TRI;
                float x = 0, y = 0, w = 64, h = 64, size = 512;
                int n = sscanf(sprite, "%f %f %f %f %f", &x, &y, &w, &h, &size);
                if (n != 5 || size == 0) Error("Invalid arguments to \"sprite\" in detail.vbsp");
                m->tex[0][0] = (x + 0.5f) / size;
                m->tex[0][1] = (y + 0.5f) / size;
                m->tex[1][0] = (x + w - 0.5f) / size;
                m->tex[1][1] = (y + h - 0.5f) / size;
                m->pos[0][0] = -10; m->pos[0][1] = 20;
                m->pos[1][0] = 10; m->pos[1][1] = 0;
                const char *ss = kv_string(it->child, "spritesize");
                if (ss) {
                    sscanf(ss, "%f %f %f %f", &x, &y, &w, &h);
                    float ox = w * x, oy = h * y;
                    m->pos[0][0] = -ox;
                    m->pos[0][1] = h - oy;
                    m->pos[1][0] = w - ox;
                    m->pos[1][1] = -oy;
                }
                m->scalestddev = kv_float(it->child, "spriterandomscale", 0.0f);
                float sway = kv_float(it->child, "sway", 0.0f);
                sway = sway < 0 ? 0 : sway > 1 ? 1 : sway;
                m->sway = (unsigned char)(255.0 * sway);
                m->shapeangle = (unsigned char)kv_int(it->child, "shape_angle", 0);
                float ssize = kv_float(it->child, "shape_size", 0.0f);
                ssize = ssize < 0 ? 0 : ssize > 1 ? 1 : ssize;
                m->shapesize = (unsigned char)(255.0 * ssize);
            }
        }
        m->amount = kv_float(it->child, "amount", 1.0) + total;
        total = m->amount;
        if (kv_int(it->child, "upright", 0)) m->flags |= 1;
        float minangle = kv_float(it->child, "minAngle", 180), maxangle = kv_float(it->child, "maxAngle", 180);
        m->mincos = (float)cos(minangle * M_PI / 180.f);
        m->maxcos = (float)cos(maxangle * M_PI / 180.f);
        m->orientation = kv_int(it->child, "detailOrientation", 0);
        if (m->mincos < m->maxcos) m->mincos = m->maxcos;
    }
    if (total > 1.0f)
        for (int k = 0; k < g->nmodels; ++k) g->models[k].amount /= total;
}

static void LoadDetailObjects(void) {
    if (!g_detail_file) return;
    FILE *f = fopen(g_detail_file, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *text = xalloc(n + 1);
    fread(text, 1, n, f);
    fclose(f);
    kv_at = text;
    char name[256], tok[16];
    int q;
    if (!kv_token(name, sizeof(name), &q) || !kv_token(tok, sizeof(tok), &q) || strcmp(tok, "{")) return;
    kv_t *root = kv_parse_block();
    for (kv_t *it = root; it; it = it->next) {
        if (!it->child) continue;
        dobjects = realloc(dobjects, sizeof(detailobject_t) * (numdobjects + 1));
        detailobject_t *d = &dobjects[numdobjects++];
        memset(d, 0, sizeof(*d));
        strncpy(d->name, it->name, sizeof(d->name) - 1);
        d->density = kv_float(it->child, "density", 0.0f);
        for (kv_t *g = it->child; g; g = g->next)
            if (g->child) ParseDetailGroup(d, g);
    }
}

/* ------------------------------------------------------------------ the lump */
#pragma pack(push, 1)
typedef struct {
    vec3_t origin, angles;
    unsigned short model, leaf;
    unsigned char lighting[4];
    unsigned lightstyles;
    unsigned char stylecount, sway, shapeangle, shapesize, orientation, pad2[3], type, pad3[3];
    float scale;
} dprop_t;
#pragma pack(pop)
typedef struct { float ul[2], lr[2], texul[2], texlr[2]; } dsprite_t;

static char (*ddict)[128];
static int numddict;
static dsprite_t *dsprites;
static int numdsprites;
static dprop_t *dprops;
static int numdprops, maxdprops;

static int AddDict(const char *name) {
    char d[128];
    memset(d, 0, sizeof(d));
    strncpy(d, name, sizeof(d));
    for (int i = numddict; --i >= 0;)
        if (!memcmp(ddict[i], d, sizeof(d))) return i;
    ddict = realloc(ddict, 128 * (numddict + 1));
    memcpy(ddict[numddict], d, 128);
    return numddict++;
}

static int AddSpriteDict(const float pos[2][2], const float tex[2][2]) {
    dsprite_t s;
    memcpy(s.ul, pos[0], 8); memcpy(s.lr, pos[1], 8);
    memcpy(s.texul, tex[0], 8); memcpy(s.texlr, tex[1], 8);
    for (int i = numdsprites; --i >= 0;)
        if (!memcmp(&dsprites[i], &s, sizeof(s))) return i;
    dsprites = realloc(dsprites, sizeof(dsprite_t) * (numdsprites + 1));
    dsprites[numdsprites] = s;
    return numdsprites++;
}

static int DetailLeaf(const vec3_t pt) {
    int node = 0;
    while (node >= 0) {
        plane_t *p = &mapplanes[dnodes[node].planenum];
        node = DotProduct(pt, p->normal) < p->dist ? dnodes[node].children[1] : dnodes[node].children[0];
    }
    return -node - 1;
}

static dprop_t *NewProp(void) {
    if (numdprops == maxdprops) {
        maxdprops = maxdprops ? maxdprops * 2 : 1024;
        dprops = realloc(dprops, sizeof(dprop_t) * maxdprops);
    }
    dprop_t *p = &dprops[numdprops++];
    memset(p, 0, sizeof(*p));
    p->lighting[0] = p->lighting[1] = p->lighting[2] = 255;
    return p;
}

extern int PropModelValid(const char *name, const char *what);
static int detail_overflow;

static void AddDetailModel(const char *model, const vec3_t pt, const vec3_t angles, int orientation) {
    if (!PropModelValid(model, "detail_prop")) return;
    if (numdprops == 65535) {
        ++detail_overflow;
        return;
    }
    dprop_t *p = NewProp();
    p->model = (unsigned short)AddDict(model);
    VectorCopy(angles, p->angles);
    VectorCopy(pt, p->origin);
    p->leaf = (unsigned short)DetailLeaf(pt);
    p->orientation = (unsigned char)orientation;
    p->type = TYPE_MODEL;
}

static void AddDetailSprite(const vec3_t pt, const vec3_t angles, int orientation, const float pos[2][2],
                            const float tex[2][2], float scale, int type, int shapeangle, int shapesize, int sway) {
    if (numdprops >= 65535) Error("Error! Too many detail props emitted on this map! (64K max!)");
    dprop_t *p = NewProp();
    p->model = (unsigned short)AddSpriteDict(pos, tex);
    VectorCopy(angles, p->angles);
    VectorCopy(pt, p->origin);
    p->leaf = (unsigned short)DetailLeaf(pt);
    p->orientation = (unsigned char)orientation;
    p->type = (unsigned char)type;
    p->scale = scale;
    p->shapeangle = (unsigned char)shapeangle;
    p->shapesize = (unsigned char)shapesize;
    p->sway = (unsigned char)sway;
}

/* angles of a basis: the matrix with columns x, y, z rotated about z by rot degrees */
static void BasisAngles(const vec3_t xaxis, const vec3_t yaxis, const vec3_t zaxis, float rot, vec3_t angles) {
    float m[4][4] = {{xaxis[0], yaxis[0], zaxis[0], 0}, {xaxis[1], yaxis[1], zaxis[1], 0},
                     {xaxis[2], yaxis[2], zaxis[2], 0}, {0, 0, 0, 1}};
    /* SetupMatrixAxisRot((0,0,1), rot) */
    double rad = rot * (M_PI / 180.0f);
    float s = (float)sin(rad), c = (float)cos(rad), t = 1.0f - c;
    const float ax[3] = {0, 0, 1};
    float tx = t * ax[0], ty = t * ax[1], tz = t * ax[2], sx = s * ax[0], sy = s * ax[1], sz = s * ax[2];
    float r[4][4] = {{tx * ax[0] + c, tx * ax[1] - sz, tx * ax[2] + sy, 0.0f},
                     {tx * ax[1] + sz, ty * ax[1] + c, ty * ax[2] - sx, 0.0f},
                     {tx * ax[2] - sy, ty * ax[2] + sx, tz * ax[2] + c, 0.0f},
                     {0.0f, 0.0f, 0.0f, 1.0f}};
    float o[4][4];
    for (int i = 0; i < 4; i++)
        for (int j = 0; j < 4; j++) o[i][j] = m[i][0] * r[0][j] + m[i][1] * r[1][j] + m[i][2] * r[2][j] + m[i][3] * r[3][j];
    /* MatrixToAngles */
    float fwd[3] = {o[0][0], o[1][0], o[2][0]}, left[3] = {o[0][1], o[1][1], o[2][1]}, up2 = o[2][2];
    double xy = sqrt((double)fwd[0] * fwd[0] + (double)fwd[1] * fwd[1]);
    if (xy > 0.001f) {
        angles[1] = atan2_deg(fwd[1], fwd[0]);
        angles[0] = atan2_deg(-fwd[2], xy);
        angles[2] = atan2_deg(left[2], up2);
    } else {
        angles[1] = atan2_deg(-left[0], left[1]);
        angles[0] = atan2_deg(-fwd[2], xy);
        angles[2] = 0;
    }
}

/* mathlib's VectorNormalize: the length in x87, rounded to float, then times 1/(length + FLT_EPSILON) */
static void NormalizeX87(vec3_t v) {
    float len = (float)sqrt((double)v[0] * v[0] + (double)v[1] * v[1] + (double)v[2] * v[2]);
    float oo = 1.0f / (len + FLT_EPSILON);
    v[0] *= oo; v[1] *= oo; v[2] *= oo;
}

/* func_detail_blocker entities (L4D2): no detail props inside their brushes' boxes */
static entity_t **blockers;
static int numblockers;

static int InsideBlocker(const vec3_t pt) {
    for (int i = 0; i < numblockers; i++) {
        for (int j = 0; j < blockers[i]->numbrushes; j++) {
            const mapbrush_t *b = &mapbrushes[blockers[i]->firstbrush + j];
            if (pt[0] > b->maxs[0] || b->mins[0] > pt[0] || pt[1] > b->maxs[1] || b->mins[1] > pt[1] ||
                pt[2] > b->maxs[2] || b->mins[2] > pt[2])
                continue;
            return 1;
        }
    }
    return 0;
}

/* The leaf a point is in, walking the written tree (vbsp's arithmetic: y, x, then z) */
static int PointLeafnum(const vec3_t p) {
    int n = 0;
    do {
        const dnode_t *node = &dnodes[n];
        const plane_t *pl = &mapplanes[node->planenum];
        float d = (pl->normal[1] * p[1] + pl->normal[0] * p[0]) + pl->normal[2] * p[2];
        n = pl->dist > d ? node->children[1] : node->children[0];
    } while (n >= 0);
    return -1 - n;
}

static void PlaceDetail(const detailmodel_t *m, const vec3_t pt, const vec3_t normal) {
    float cosangle = normal[2];
    if (cosangle < m->maxcos) return;
    if (cosangle < m->mincos) {
        float prob = (cosangle - m->maxcos) / (m->mincos - m->maxcos);
        float t = rand01();
        if (t > prob) return;
    }
    if (InsideBlocker(pt)) return;
    /* (L4D2: none under water, when the map has any) */
    if (g_has_water) {
        int leaf = PointLeafnum(pt);
        if (leaf >= 0 && (dleafs[leaf].contents & (CONTENTS_WATER | CONTENTS_SLIME))) return;
    }
    vec3_t angles;
    if (m->flags & 1) {
        angles[0] = 0;
        angles[1] = ((float)crt_rand() * 360.0f) * (1.0f / VALVE_RAND_MAX);
        angles[2] = 0.0f;
    } else {
        vec3_t z, x = {1, 0, 0}, y;
        VectorCopy(normal, z);
        NormalizeX87(z);
        if (fabs(DotProduct(x, z)) - 1.0 > -1e-3) {
            x[0] = 0; x[1] = 1; x[2] = 0;
        }
        CrossProduct(z, x, y);
        NormalizeX87(y);
        CrossProduct(y, z, x);
        NormalizeX87(x);
        float rot = (float)((double)crt_rand() * 360.0f * (1.0f / VALVE_RAND_MAX));     /* (x87) */
        BasisAngles(x, y, z, rot, angles);
    }
    if (m->type == TYPE_MODEL) AddDetailModel(m->model, pt, angles, m->orientation);
    else {
        float scale = 1.0f;
        if (m->scalestddev != 0.0f) scale = (float)fabs(RandomGaussian(1.0f, m->scalestddev));
        AddDetailSprite(pt, angles, m->orientation, m->pos, m->tex, scale, m->type, m->shapeangle, m->shapesize, m->sway);
    }
}

static int SelectGroup(const detailobject_t *d, float alpha) {
    int start, end;
    for (start = 0; start < d->ngroups - 1; ++start)
        if (alpha < d->groups[start + 1].alpha) break;
    end = start + 1;
    if (end >= d->ngroups) --end;
    if (start == end) return start;
    float dist = 0.0f, da = d->groups[end].alpha - d->groups[start].alpha;
    if (da != 0.0f) dist = (alpha - d->groups[start].alpha) / da;
    float r = rand01();
    return r > dist ? start : end;
}

static int SelectDetail(const detailgroup_t *g) {
    float r = rand01();
    for (int i = 0; i < g->nmodels; ++i)
        if (r <= g->models[i].amount) return i;
    return -1;
}

static void EmitOnFace(dface_t *face, const detailobject_t *d) {
    if (face->numedges < 3 || !d->ngroups) return;
    int *se = &dsurfedges[face->firstedge];
    int vi = se[0] < 0;
    float *first = dvertexes[dedges[abs(se[0])].v[vi]].point;
    for (int i = 1; i < face->numedges - 1; ++i) {
        int vidx = se[i] < 0;
        dedge_t *e = &dedges[abs(se[i])];
        vec3_t e1, e2, areavec;
        VectorSubtract(dvertexes[e->v[vidx]].point, first, e1);
        VectorSubtract(dvertexes[e->v[1 - vidx]].point, first, e2);
        CrossProduct(e1, e2, areavec);
        float len = sqrtf((areavec[1] * areavec[1] + areavec[2] * areavec[2]) + areavec[0] * areavec[0]);
        float area = 0.5f * len;
        int n = (int)(area * d->density * 0.000001);
        for (int k = 0; k < n; ++k) {
            float u = rand01();
            float v = rand01();
            if (v > 1.0f - u) {
                u = 1.0f - u;
                v = 1.0f - v;
            }
            int g = SelectGroup(d, 1.0f);
            int m = SelectDetail(&d->groups[g]);
            if (m < 0) continue;
            vec3_t pt, normal;
            VectorMA(first, u, e1, pt);
            VectorMA(pt, v, e2, pt);
            float oob = 1.0f / -len;          /* (the game's VectorDivide multiplies by the reciprocal) */
            for (int c = 0; c < 3; c++) normal[c] = areavec[c] * oob;
            PlaceDetail(&d->groups[g].models[m], pt, normal);
        }
    }
}

extern void DispPositionOnSurface(int i, float u, float v, vec3_t pt, vec3_t normal, float *alpha);

static void EmitOnDisplacement(dface_t *face, const detailobject_t *d) {
    if (!d->ngroups) return;
    float area = 0.0f;
    int *se = &dsurfedges[face->firstedge];
    int vi = se[0] < 0;
    float *first = dvertexes[dedges[abs(se[0])].v[vi]].point;
    for (int i = 1; i <= 2; ++i) {
        int vidx = se[i] < 0;
        dedge_t *e = &dedges[abs(se[i])];
        vec3_t e1, e2, areavec;
        VectorSubtract(dvertexes[e->v[vidx]].point, first, e1);
        VectorSubtract(dvertexes[e->v[1 - vidx]].point, first, e2);
        CrossProduct(e1, e2, areavec);
        area += 0.5f * VectorLength(areavec);
    }
    int n = (int)(area * d->density * 0.000001);
    for (int i = 0; i < n; ++i) {
        float u = rand01();
        float v = rand01();
        float alpha = 0;
        vec3_t pt = {0, 0, 0}, normal = {0, 0, 0};
        DispPositionOnSurface(face->dispinfo, u, v, pt, normal, &alpha);
        alpha *= 1.0f / 255.0f;
        int g = SelectGroup(d, alpha);
        int m = SelectDetail(&d->groups[g]);
        if (m < 0) continue;
        PlaceDetail(&d->groups[g].models[m], pt, normal);
    }
}

/* MSVC's qsort, step for step (its order for equal keys is part of vbsp's output) */
static void swap_bytes(char *a, char *b, size_t w) {
    if (a == b) return;
    while (w--) {
        char t = *a;
        *a++ = *b;
        *b++ = t;
    }
}
static void shortsort(char *lo, char *hi, size_t w, int (*comp)(const void *, const void *)) {
    while (hi > lo) {
        char *max = lo;
        for (char *p = lo + w; p <= hi; p += w)
            if (comp(p, max) > 0) max = p;
        swap_bytes(max, hi, w);
        hi -= w;
    }
}
static void msvc_qsort(void *base, size_t num, size_t w, int (*comp)(const void *, const void *)) {
    char *lo, *hi, *mid, *loguy, *higuy, *lostk[64], *histk[64];
    size_t size;
    int stkptr = 0;
    if (num < 2) return;
    lo = (char *)base;
    hi = (char *)base + w * (num - 1);
recurse:
    size = (hi - lo) / w + 1;
    if (size <= 8) shortsort(lo, hi, w, comp);
    else {
        mid = lo + (size / 2) * w;
        if (comp(lo, mid) > 0) swap_bytes(lo, mid, w);
        if (comp(lo, hi) > 0) swap_bytes(lo, hi, w);
        if (comp(mid, hi) > 0) swap_bytes(mid, hi, w);
        loguy = lo;
        higuy = hi;
        for (;;) {
            if (mid > loguy) {
                do { loguy += w; } while (loguy < mid && comp(loguy, mid) <= 0);
            }
            if (mid <= loguy) {
                do { loguy += w; } while (loguy <= hi && comp(loguy, mid) <= 0);
            }
            do { higuy -= w; } while (higuy > mid && comp(higuy, mid) > 0);
            if (higuy < loguy) break;
            swap_bytes(loguy, higuy, w);
            if (mid == higuy) mid = loguy;
        }
        higuy += w;
        if (mid < higuy) {
            do { higuy -= w; } while (higuy > mid && comp(higuy, mid) == 0);
        }
        if (mid >= higuy) {
            do { higuy -= w; } while (higuy > lo && comp(higuy, mid) == 0);
        }
        if (higuy - lo >= hi - loguy) {
            if (lo < higuy) { lostk[stkptr] = lo; histk[stkptr] = higuy; ++stkptr; }
            if (loguy < hi) { lo = loguy; goto recurse; }
        } else {
            if (loguy < hi) { lostk[stkptr] = loguy; histk[stkptr] = hi; ++stkptr; }
            if (lo < higuy) { hi = higuy; goto recurse; }
        }
    }
    --stkptr;
    if (stkptr >= 0) {
        lo = lostk[stkptr];
        hi = histk[stkptr];
        goto recurse;
    }
}

static int by_leaf(const void *a, const void *b) {
    int d = ((const dprop_t *)a)->leaf - ((const dprop_t *)b)->leaf;
    return d < 0 ? -1 : d > 0 ? 1 : 0;
}

extern const char *MaterialDetailType(int texdata);
extern unsigned short *dfaceids;
unsigned char *g_dprp;
int g_dprp_len;

void EmitDetailObjects(void) {
    LoadDetailObjects();
    numblockers = 0;
    for (int i = 0; i < num_entities; i++)
        if (!strcmp(ValueForKey(&entities[i], "classname"), "func_detail_blocker")) {
            blockers = realloc(blockers, sizeof(entity_t *) * (numblockers + 1));
            blockers[numblockers++] = &entities[i];
        }
    for (int j = 0; j < numfaces; ++j) {
        dface_t *face = &dfaces[j];
        const char *type = MaterialDetailType(texinfos[face->texinfo].texdata);
        if (!type || !type[0]) continue;
        detailobject_t *d = NULL;
        for (int k = 0; k < numdobjects && !d; k++)
            if (!_stricmp(dobjects[k].name, type)) d = &dobjects[k];
        if (!d) {
            Warning("Material %s uses unknown detail object type %s!\n",
                    texdata_strings + texdata_string_table[texdatas[texinfos[face->texinfo].texdata].name_id], type);
            continue;
        }
        int seed = dfaceids[j];
        crt_srand((unsigned)seed);
        RandomSeed(seed);
        if (face->dispinfo < 0) EmitOnFace(face, d);
        else EmitOnDisplacement(face, d);
    }
    for (int i = 0; i < num_entities; ++i) {
        entity_t *e = &entities[i];
        const char *cls = ValueForKey(e, "classname");
        vec3_t origin, angles;
        if (!strcmp(cls, "detail_prop") || !strcmp(cls, "prop_detail")) {
            GetVectorForKey(e, "origin", origin);
            GetVectorForKey(e, "angles", angles);
            AddDetailModel(ValueForKey(e, "model"), origin, angles, atoi(ValueForKey(e, "detailOrientation")));
            e->epairs = NULL;
            continue;
        }
        if (!strcmp(cls, "prop_detail_sprite")) {
            float pos[2][2] = {{0}}, tex[2][2] = {{0}};
            GetVectorForKey(e, "origin", origin);
            GetVectorForKey(e, "angles", angles);
            sscanf(ValueForKey(e, "position_ul"), "%f %f", &pos[0][0], &pos[0][1]);
            sscanf(ValueForKey(e, "position_lr"), "%f %f", &pos[1][0], &pos[1][1]);
            sscanf(ValueForKey(e, "tex_ul"), "%f %f", &tex[0][0], &tex[0][1]);
            sscanf(ValueForKey(e, "tex_size"), "%f %f", &tex[1][0], &tex[1][1]);
            float total = (float)atof(ValueForKey(e, "tex_total_size"));
            tex[1][0] += tex[0][0] - 0.5f;
            tex[1][1] += tex[0][1] - 0.5f;
            tex[0][0] += 0.5f;
            tex[0][1] += 0.5f;
            float oo = 1.0f / total;
            for (int a = 0; a < 2; a++)
                for (int b = 0; b < 2; b++) tex[a][b] *= oo;
            AddDetailSprite(origin, angles, atoi(ValueForKey(e, "detailOrientation")), pos, tex, 1.0f, TYPE_SPRITE, 0, 0, 0);
            e->epairs = NULL;
        }
    }
    msvc_qsort(dprops, numdprops, sizeof(dprop_t), by_leaf);
    int size = 12 + 128 * numddict + 32 * numdsprites + 52 * numdprops;
    g_dprp = xalloc(size);
    unsigned char *q = g_dprp;
    memcpy(q, &numddict, 4); q += 4;
    memcpy(q, ddict, 128 * numddict); q += 128 * numddict;
    memcpy(q, &numdsprites, 4); q += 4;
    memcpy(q, dsprites, 32 * numdsprites); q += 32 * numdsprites;
    memcpy(q, &numdprops, 4); q += 4;
    memcpy(q, dprops, 52 * numdprops);
    g_dprp_len = size;
    if (detail_overflow) Warning("Error! Too many detail props on this map. %d were not emitted!\n", detail_overflow);
}
