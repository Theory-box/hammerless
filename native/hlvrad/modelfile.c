/* A model's file (<model minus .mdl><ext>) from -modeldir, where the Python side copies them out of the game. */
#include "hlvrad.h"

unsigned char *ReadModelFile(const char *model, const char *ext, int *len) {
    if (!g_modeldir) return NULL;
    char path[1400];
    snprintf(path, sizeof(path), "%s/%s", g_modeldir, model);
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
