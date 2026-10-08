/* Light-emitting textures (vrad's texlights): lights.rad from the game folder, then a -lights file, then
 * <map>.rad next to the map; each line "texture r g b brightness" (optionally "hdr:" or "ldr:" first; a
 * later file overrides an earlier one). "noshadow" and "forcetextureshadow" lines are read elsewhere. */
#include <ctype.h>
#include "hlvrad.h"

typedef struct { char name[256]; vec3_t value; } texlight_t;
static texlight_t *texlights;
static int num_texlights;
static char level_name[256];

static void ReadLightFile(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) {
        Msg("Warning: Couldn't open texlight file %s.\n", path);
        return;
    }
    Msg("[Reading texlights from '%s']\n", path);
    char buf[1024];
    while (fgets(buf, sizeof(buf), f)) {
        char *scan = buf;
        if (!_strnicmp("hdr:", scan, 4)) {
            scan += 4;
            if (!g_bHDR) continue;
        }
        if (!_strnicmp("ldr:", scan, 4)) {
            scan += 4;
            if (g_bHDR) continue;
        }
        scan += strspn(scan, " \t");
        char word[1024];
        if (sscanf(scan, "noshadow %1023s", word) == 1 || sscanf(scan, "forcetextureshadow %1023s", word) == 1) continue;
        char name[256];
        if (sscanf(scan, "%255s ", name) != 1) continue;
        vec3_t value;
        LightForString(scan + strlen(name) + 1, value);
        int j;
        for (j = 0; j < num_texlights; j++)
            if (!strcmp(texlights[j].name, name)) break;
        if (j == num_texlights) {
            texlights = realloc(texlights, sizeof(texlight_t) * (num_texlights + 1));
            num_texlights++;
        }
        snprintf(texlights[j].name, sizeof(texlights[j].name), "%s", name);
        VectorCopy(value, texlights[j].value);
    }
    fclose(f);
}

void LoadTexLights(const char *gamedir, const char *bsppath, const char *designer) {
    char path[1200];
    snprintf(path, sizeof(path), "%s/lights.rad", gamedir ? gamedir : ".");
    ReadLightFile(path);
    if (designer) ReadLightFile(designer);
    /* the map's own: <map>.rad */
    snprintf(path, sizeof(path), "%s", bsppath);
    char *dot = strrchr(path, '.'), *slash = strrchr(path, '/'), *bslash = strrchr(path, '\\');
    if (bslash > slash) slash = bslash;
    if (dot && (!slash || dot > slash)) *dot = 0;
    snprintf(level_name, sizeof(level_name), "%s", slash ? slash + 1 : path);
    strncat(path, ".rad", sizeof(path) - strlen(path) - 1);
    FILE *f = fopen(path, "r");
    if (f) {
        fclose(f);
        ReadLightFile(path);
    }
}

/* the light a texture gives off (zero for most); cubemap-patched copies ("maps/<map>/name_x_y_z") count
 * as the original */
void LightForTexture(const char *name, vec3_t result) {
    VectorClear(result);
    char base[512];
    if (!strncmp("maps/", name, 5) && !strncmp(level_name, name + 5, strlen(level_name)) && name[5 + strlen(level_name)] == '/') {
        snprintf(base, sizeof(base), "%s", name + 6 + strlen(level_name));
        int ok = 1;
        for (int i = 0; i < 3; i++) {
            char *u = strrchr(base, '_');
            if (u) *u = 0;
            else ok = 0;
        }
        if (ok) name = base;
    }
    for (int i = 0; i < num_texlights; i++)
        if (!_stricmp(name, texlights[i].name)) {
            VectorCopy(texlights[i].value, result);
            return;
        }
}

const char *TexDataName(int texdata) {
    const int *td = (const int *)lumps[LUMP_TEXDATA].data + 8 * texdata;
    const int *table = (const int *)lumps[LUMP_TEXDATA_STRING_TABLE].data;
    return (const char *)lumps[LUMP_TEXDATA_STRING_DATA].data + table[td[3]];
}
