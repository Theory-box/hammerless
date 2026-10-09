// The sky map's light in direction dir, times scale (skymap.c's SkyMapColor). Needs: buffer SkyMap { float skymap[]; }
// and ints skymapW, skymapH.
vec3 SkyMapColor(vec3 dir, float scale, int w, int h) {
    float len = length(dir);
    if (!(len > 0.0)) return vec3(0.0);
    float z = clamp(dir.z / len, -1.0, 1.0);
    float u = (atan(dir.y, dir.x) + 3.14159265358979) / (2.0 * 3.14159265358979) * float(w) - 0.5;
    float v = (acos(z) / 3.14159265358979) * float(h) - 0.5;
    v = clamp(v, 0.0, float(h - 1));
    int u0 = int(floor(u)), v0 = int(floor(v));
    float fu = u - float(u0), fv = v - float(v0);
    int v1 = v0 + 1 < h ? v0 + 1 : v0;
    int ua = ((u0 % w) + w) % w, ub = (ua + 1) % w;
    vec3 out_;
    for (int k = 0; k < 3; k++) {
        float a = skymap[3 * (v0 * w + ua) + k] * (1.0 - fu) + skymap[3 * (v0 * w + ub) + k] * fu;
        float b = skymap[3 * (v1 * w + ua) + k] * (1.0 - fu) + skymap[3 * (v1 * w + ub) + k] * fu;
        out_[k] = (a * (1.0 - fv) + b * fv) * scale;
    }
    return out_;
}

float Luminance(vec3 c) { return 0.2126 * c.x + 0.7152 * c.y + 0.0722 * c.z; }
