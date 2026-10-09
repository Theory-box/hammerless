/* hlphys.exe: the collision triangles of prop models, for a 64-bit hlvrad (the game's vphysics.dll, which makes
 * them, only comes 32-bit). hlphys -game <dir> -modeldir <dir> -list <models.txt> -out <file>: for each model
 * named in the list (one per line), int32 n (-1: no collision model) and n triangles of 9 floats. */
#include "hlvrad.h"

#include <stdarg.h>

int PropCollisionTris(const char *name, float **tris);
const char *g_modeldir, *g_gamedir;

void Error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "hlphys: ");
    vfprintf(stderr, fmt, ap);
    fputc(10, stderr);
    va_end(ap);
    exit(1);
}

void Msg(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}

void *xalloc(size_t n) {
    void *p = calloc(1, n ? n : 1);
    if (!p) Error("out of memory");
    return p;
}

int main(int argc, char **argv) {
    const char *list = NULL, *out = NULL;
    for (int i = 1; i + 1 < argc; i++) {
        if (!strcmp(argv[i], "-game")) g_gamedir = argv[++i];
        else if (!strcmp(argv[i], "-modeldir")) g_modeldir = argv[++i];
        else if (!strcmp(argv[i], "-list")) list = argv[++i];
        else if (!strcmp(argv[i], "-out")) out = argv[++i];
    }
    if (!list || !out) Error("usage: hlphys -game <dir> -modeldir <dir> -list <models.txt> -out <file>");
    FILE *lf = fopen(list, "r"), *of = fopen(out, "wb");
    if (!lf || !of) Error("can't open %s or %s", list, out);
    char line[1024];
    while (fgets(line, sizeof(line), lf)) {
        line[strcspn(line, "\r\n")] = 0;
        float *tris = NULL;
        int n = PropCollisionTris(line, &tris);
        fwrite(&n, 4, 1, of);
        if (n > 0) fwrite(tris, sizeof(float) * 9, n, of);
        free(tris);
    }
    fclose(of), fclose(lf);
    return 0;
}
