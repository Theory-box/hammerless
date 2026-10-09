/* Sky light from a picture of the sky (-skymap file, Hammerless's own: vrad has one sky colour).
 *
 * The file: "HLSK", width, height (int32), then width x height float RGB, row 0 straight up. Columns go
 * round the horizon: column u looks along angle 2 pi u - pi from +x towards +y (atan2(y, x)); row v looks
 * up at pi/2 - pi v. The Python side blurs it to the spacing of the sky rays and scales it so a floor
 * under open sky gets as much light as the flat sky colour would: here it's multiplied by the brightness
 * (luminance) of that colour. Without it the sky is lit exactly as vrad lights it. */
#include "hlvrad.h"

static float *skymap;
static int skymap_w, skymap_h;

void LoadSkyMap(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) Error("can't open the sky map %s", path);
    char magic[4];
    int wh[2];
    if (fread(magic, 1, 4, f) != 4 || memcmp(magic, "HLSK", 4) || fread(wh, 4, 2, f) != 2 || wh[0] < 2 || wh[1] < 2 ||
        wh[0] > 8192 || wh[1] > 8192)
        Error("%s isn't a sky map", path);
    skymap = xalloc(sizeof(float) * 3 * wh[0] * wh[1]);
    if (fread(skymap, sizeof(float) * 3, (size_t)wh[0] * wh[1], f) != (size_t)wh[0] * wh[1]) Error("%s is cut short", path);
    fclose(f);
    skymap_w = wh[0], skymap_h = wh[1];
    Msg("sky light from %s (%d x %d)\n", path, skymap_w, skymap_h);
}

int HaveSkyMap(void) { return skymap != NULL; }

/* the light from the sky in direction dir (any length), times scale; bilinear, wrapping round the horizon */
void SkyMapColor(const vec3_t dir, float scale, vec3_t out) {
    double len = sqrt((double)dir[0] * dir[0] + (double)dir[1] * dir[1] + (double)dir[2] * dir[2]);
    if (!(len > 0)) {
        VectorClear(out);
        return;
    }
    double z = dir[2] / len;
    z = z > 1 ? 1 : z < -1 ? -1 : z;
    double u = (atan2(dir[1], dir[0]) + 3.14159265358979323846) / (2 * 3.14159265358979323846) * skymap_w - 0.5;
    double v = (acos(z) / 3.14159265358979323846) * skymap_h - 0.5;
    if (v < 0) v = 0;
    if (v > skymap_h - 1) v = skymap_h - 1;
    int u0 = (int)floor(u), v0 = (int)floor(v);
    double fu = u - u0, fv = v - v0;
    int v1 = v0 + 1 < skymap_h ? v0 + 1 : v0;
    int ua = ((u0 % skymap_w) + skymap_w) % skymap_w, ub = (ua + 1) % skymap_w;
    for (int k = 0; k < 3; k++) {
        double a = skymap[3 * (v0 * skymap_w + ua) + k] * (1 - fu) + skymap[3 * (v0 * skymap_w + ub) + k] * fu;
        double b = skymap[3 * (v1 * skymap_w + ua) + k] * (1 - fu) + skymap[3 * (v1 * skymap_w + ub) + k] * fu;
        out[k] = (float)((a * (1 - fv) + b * fv) * scale);
    }
}

/* the brightness of a light colour (linear Rec. 709) */
float Luminance(const vec3_t c) { return 0.2126f * c[0] + 0.7152f * c[1] + 0.0722f * c[2]; }
