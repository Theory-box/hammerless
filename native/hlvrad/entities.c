/* The entity lump read into key/value lists (vrad's ParseEntities). A key given twice: the last one
 * counts (vrad keeps pairs newest first and takes the first it finds). */
#include <ctype.h>
#include "hlvrad.h"

entity_t *entities; int num_entities;

static const char *skip_space(const char *p) {
    while (*p && isspace((unsigned char)*p)) p++;
    return p;
}

static const char *read_quoted(const char *p, char **out) {
    if (*p != '"') Error("entity lump: expected a quoted string");
    const char *s = ++p;
    while (*p && *p != '"') p++;
    size_t n = (size_t)(p - s);
    *out = xalloc(n + 1);
    memcpy(*out, s, n);
    if (*p) p++;
    return p;
}

void ParseEntities(void) {
    const char *p = (const char *)lumps[LUMP_ENTITIES].data, *end = p + lumps[LUMP_ENTITIES].len;
    int cap = 0;
    num_entities = 0;
    for (;;) {
        p = skip_space(p);
        if (p >= end || !*p) break;
        if (*p != '{') Error("entity lump: expected {");
        p++;
        if (num_entities == cap) {
            cap = cap ? cap * 2 : 256;
            entities = realloc(entities, sizeof(entity_t) * cap);
        }
        entity_t *e = &entities[num_entities++];
        memset(e, 0, sizeof(*e));
        int kcap = 0;
        for (;;) {
            p = skip_space(p);
            if (*p == '}') {
                p++;
                break;
            }
            if (!*p) Error("entity lump: unexpected end");
            if (e->numpairs == kcap) {
                kcap = kcap ? kcap * 2 : 16;
                e->keys = realloc(e->keys, sizeof(char *) * kcap);
                e->values = realloc(e->values, sizeof(char *) * kcap);
            }
            p = read_quoted(p, &e->keys[e->numpairs]);
            p = skip_space(p);
            p = read_quoted(p, &e->values[e->numpairs]);
            e->numpairs++;
        }
    }
}

const char *ValueForKey(const entity_t *e, const char *key) {
    for (int i = e->numpairs - 1; i >= 0; i--)
        if (!strcmp(e->keys[i], key)) return e->values[i];
    return "";
}

float FloatForKey(const entity_t *e, const char *key) { return (float)atof(ValueForKey(e, key)); }

void GetVectorForKey(const entity_t *e, const char *key, vec3_t v) {
    double a = 0, b = 0, c = 0;
    sscanf(ValueForKey(e, key), "%lf %lf %lf", &a, &b, &c);
    v[0] = (float)a;
    v[1] = (float)b;
    v[2] = (float)c;
}
