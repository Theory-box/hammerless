// leafambient.c's surface finder (vrad's CLightSurface) on the GPU: the map's tree walked along the ray, node faces
// taken where their lightmap holds the crossing point, leaf faces facing the ray, displacements in each leaf reached
// (each tested against the whole ray). Kept to the C, quirks and all, so the same surfaces are found.
// Bindings 1-14; the including shader's own start at 15.
struct WFace {
    vec4 lmS, lmT;          // lightmap vectors (luxels per world unit, offset)
    ivec4 a;                // lightofs, styles (4 bytes), smax, tmax (luxels across)
    ivec4 b;                // lightmap mins s, t; pages per style (4: bumped); texinfo flags
    ivec4 c;                // plane, flags (1 on a node, 2 a displacement), first winding point, winding points
    vec4 refl;              // reflectivity
};
struct WDisp {
    ivec4 a;                // face, triangles, first tree node, solid
    ivec4 b;                // first vertex, first triangle, first in order, 0
    vec4 bmins, bmaxs;      // (the box a ray must touch, a unit bigger still)
};
struct WNode { vec4 lo, hi; ivec4 i; };       // box; left (-1: a leaf), right, first, count

layout(set = 0, binding = 1, std430) readonly buffer Planes { vec4 planes[]; };
layout(set = 0, binding = 2, std430) readonly buffer Nodes { ivec4 nodes[]; };        // plane, children, first face | count << 16
layout(set = 0, binding = 3, std430) readonly buffer Leaves { ivec4 leaves[]; };      // first leaf face, count, first disp, count
layout(set = 0, binding = 4, std430) readonly buffer LeafFaces { int leaffaces[]; };
layout(set = 0, binding = 5, std430) readonly buffer Faces { WFace faces[]; };
layout(set = 0, binding = 6, std430) readonly buffer Windings { vec4 windings[]; };
layout(set = 0, binding = 7, std430) readonly buffer Disps { WDisp disps[]; };
layout(set = 0, binding = 8, std430) readonly buffer DispVerts { vec4 dverts[]; };    // xyz, w unused
layout(set = 0, binding = 9, std430) readonly buffer DispTris { ivec4 dtris[]; };
layout(set = 0, binding = 10, std430) readonly buffer DispNodes { WNode dnodes[]; };
layout(set = 0, binding = 11, std430) readonly buffer DispOrder { int dorder[]; };
layout(set = 0, binding = 12, std430) readonly buffer DispLux { vec2 dlux[]; };
layout(set = 0, binding = 13, std430) readonly buffer LeafDisps { int leafdisps[]; };
layout(set = 0, binding = 14, std430) readonly buffer LightData { uint lightdata[]; };

#define SURF_SKY 0x4
#define SURF_NOLIGHT 0x400
#define SURF_BUMPLIGHT 0x800
#define TEST_EPSILON 0.03125
#define MASK_OPAQUE_ 1

bool wFix;              // -fixquirks: faces only where they are, displacements only in reach (leafambient.c)

// the walk's state (lightsurf_t)
vec3 lsStart, lsDelta;
int lsSurface;
float lsHitfrac;
vec2 lsLuxel;
bool lsHasLuxel;

bool PointInFaceWinding(vec3 pt, int f) {
    int first = faces[f].c.z, n = faces[f].c.w;
    vec3 p0 = windings[first].xyz;
    vec3 test = normalize(cross(windings[first + 1].xyz - p0, pt - p0));
    for (int i = 1; i < n; i++) {
        vec3 pi = windings[first + i].xyz, pn = windings[first + (i + 1) % n].xyz;
        vec3 c = normalize(cross(pn - pi, pt - pi));
        if (dot(c, test) < 0.0) return false;
    }
    return true;
}

bool PointOnSurface(vec3 pt, int f) {
    if ((faces[f].b.w & SURF_NOLIGHT) != 0) return false;
    if (wFix && !PointInFaceWinding(pt, f)) return false;
    float s = dot(pt, faces[f].lmS.xyz) + faces[f].lmS.w, t = dot(pt, faces[f].lmT.xyz) + faces[f].lmT.w;
    if (s < float(faces[f].b.x) || t < float(faces[f].b.y)) return false;
    float ds = s - float(faces[f].b.x), dt = t - float(faces[f].b.y);
    if (ds > float(faces[f].a.z - 1) || dt > float(faces[f].a.w - 1)) return false;
    lsLuxel = vec2(ds, dt);
    return true;
}

// 0: found (stop)
int EnumerateNode(int node, float f) {
    vec3 pt = lsStart + f * lsDelta;
    int sky = -1;
    int first = nodes[node].w & 0xffff, count = int(uint(nodes[node].w) >> 16);
    for (int i = 0; i < count; i++) {
        int fi = first + i;
        if ((faces[fi].c.y & 3) != 1) continue;     // (not on the node, or a displacement)
        if ((faces[fi].b.w & SURF_SKY) != 0) {
            if (PointInFaceWinding(pt, fi)) sky = fi;
            continue;
        }
        if (PointOnSurface(pt, fi)) {
            lsHitfrac = f, lsSurface = fi, lsHasLuxel = true;
            return 0;
        }
    }
    lsSurface = sky;
    return sky < 0 ? 1 : 0;
}

bool RayMayHitBox(vec3 s, vec3 inv, bvec3 zero, vec3 mins, vec3 maxs) {
    float t0 = -0.01, t1 = 1.01;
    for (int k = 0; k < 3; k++) {
        if (zero[k]) {
            if (s[k] < mins[k] || s[k] > maxs[k]) return false;
            continue;
        }
        float a = (mins[k] - s[k]) * inv[k], b = (maxs[k] - s[k]) * inv[k];
        if (a > b) { float x = a; a = b; b = x; }
        if (a > t0) t0 = a;
        if (b < t1) t1 = b;
        if (t0 > t1) return false;
    }
    return true;
}

// the nearest displacement hit in a leaf (as a fraction; 1: none), its face and luxel
float ClipRayToDispInLeaf(int leaf, out int face, out vec2 luxel) {
    vec3 s = lsStart, d = lsDelta;
    bvec3 zero = equal(d, vec3(0.0));
    vec3 inv = vec3(zero.x ? 0.0 : 1.0 / d.x, zero.y ? 0.0 : 1.0 / d.y, zero.z ? 0.0 : 1.0 / d.z);
    float best = 1.0;
    face = -1;
    luxel = vec2(0.0);
    int first = leaves[leaf].z, count = leaves[leaf].w;
    for (int k = 0; k < count; k++) {
        int di = leafdisps[first + k];
        if (disps[di].a.w == 0) continue;
        if (!RayMayHitBox(s, inv, zero, disps[di].bmins.xyz, disps[di].bmaxs.xyz)) continue;
        int fv = disps[di].b.x, ft = disps[di].b.y, fo = disps[di].b.z, fn = disps[di].a.z;
        float dist = 3.4e38, bu = 0.0, bv = 0.0;
        int bt = -1;
        int stack[64], sp = 0;
        if (disps[di].a.y > 0) stack[sp++] = 0;
        while (sp > 0) {
            int ni = fn + stack[--sp];
            if (!RayMayHitBox(s, inv, zero, dnodes[ni].lo.xyz, dnodes[ni].hi.xyz)) continue;
            if (dnodes[ni].i.x >= 0) {
                stack[sp++] = dnodes[ni].i.y, stack[sp++] = dnodes[ni].i.x;
                continue;
            }
            for (int i = dnodes[ni].i.z; i < dnodes[ni].i.z + dnodes[ni].i.w; i++) {
                int t = dorder[fo + i];
                ivec4 tr = dtris[ft + t];
                vec3 v1 = dverts[fv + tr.x].xyz, v2 = dverts[fv + tr.z].xyz, v3 = dverts[fv + tr.y].xyz;
                vec3 e1 = v2 - v1, e2 = v3 - v1;
                vec3 dxe2 = cross(d, e2);
                float denom = dot(dxe2, e1);
                if (abs(denom) < 1e-6) continue;
                denom = 1.0 / denom;
                vec3 org = s - v1;
                float u = dot(dxe2, org) * denom;
                vec3 oxe1 = cross(org, e1);
                float v = dot(oxe1, d) * denom;
                float tt = dot(oxe1, e2) * denom;
                if (tt < -1e-3 || tt > 1.0 + 1e-3) continue;
                if (u >= 0.0 && v >= 0.0 && u + v <= 1.0 && tt > 0.0 && (tt < dist || (tt == dist && t < bt)))
                    dist = tt, bu = u, bv = v, bt = t;
            }
        }
        if (bt < 0 || !(dist < best)) continue;
        best = dist;
        face = disps[di].a.x;
        ivec4 tr = dtris[ft + bt];
        vec2 l0 = dlux[fv + tr.x], l1 = dlux[fv + tr.z], l2 = dlux[fv + tr.y];
        luxel = (l0 + bu * (l1 - l0)) + bv * (l2 - l0);
    }
    return best;
}

int EnumerateLeaf(int leaf, float start, float end) {
    bool hit = false;
    int first = leaves[leaf].x, count = leaves[leaf].y;
    for (int i = 0; i < count; i++) {
        int fi = leaffaces[first + i];
        if ((faces[fi].c.y & 3) != 0) continue;     // (on a node, or a displacement)
        vec4 p = planes[faces[fi].c.x];
        if (dot(p.xyz, lsDelta) > 0.0) continue;
        float sdn = dot(lsStart, p.xyz), ddn = dot(lsDelta, p.xyz);
        float front = sdn + start * ddn - p.w, back = sdn + end * ddn - p.w;
        bool side = front < 0.0;
        if ((back < 0.0) == side) continue;
        float f = front / (front - back);
        float mid = start * (1.0 - f) + end * f;
        if (mid >= lsHitfrac) continue;
        vec3 pt = lsStart + mid * lsDelta;
        if (PointOnSurface(pt, fi)) lsHitfrac = mid, lsSurface = fi, hit = true, lsHasLuxel = true;
    }
    int dface;
    vec2 lux;
    float dist = ClipRayToDispInLeaf(leaf, dface, lux);
    if (wFix && dist > end + 1e-4) dist = 1.0;
    if (dist < lsHitfrac) {
        lsHitfrac = dist, lsSurface = dface, lsLuxel = lux;
        hit = true, lsHasLuxel = true;
    }
    return hit ? 0 : 1;
}

// EnumerateNodesAlongRay_r without recursion: 0 when a surface was found (lsSurface; -1 for none at all)
struct WTask { int node, kind; float s, e; };     // kind 0 a subtree over s..e, 1 a node's faces at s
int Walk() {
    WTask stack[256];
    int sp = 0;
    stack[sp++] = WTask(0, 0, 0.0, 1.0);
    while (sp > 0) {
        WTask w = stack[--sp];
        if (w.kind == 1) {
            if (EnumerateNode(w.node, w.s) == 0) return 0;
            continue;
        }
        int node = w.node;
        float start = w.s, end = w.e;
        while (node >= 0) {
            ivec4 n = nodes[node];
            vec4 p = planes[n.x];
            float sdn = dot(lsStart, p.xyz), ddn = dot(lsDelta, p.xyz);
            float front = sdn + start * ddn - p.w, back = sdn + end * ddn - p.w;
            if (front <= -TEST_EPSILON && back <= -TEST_EPSILON) node = n.z;
            else if (front >= TEST_EPSILON && back >= TEST_EPSILON) node = n.y;
            else {
                bool side = front < 0.0;
                float split;
                if (ddn == 0.0) split = 1.0;
                else split = clamp((p.w - sdn) / ddn, 0.0, 1.0);
                if (sp + 2 > 256) return 1;            // (deeper than any real map's tree)
                stack[sp++] = WTask(side ? n.y : n.z, 0, split, end);       // (then the far side,)
                stack[sp++] = WTask(node, 1, split, split);                 // (the node's faces,)
                node = side ? n.z : n.y;                                    // (the near side first)
                end = split;
            }
        }
        if (EnumerateLeaf(-1 - node, start, end) == 0) return 0;
    }
    return 1;
}

int LightByte(int i) { return int((lightdata[i >> 2] >> (8 * (i & 3))) & 0xFFu); }
int FaceStyle(int face, int m) { return (faces[face].a.y >> (8 * m)) & 0xFF; }

// a lightmap colour (r, g, b, signed exponent) as light: c * 2^e / 255
vec3 Rgbe(int ofs) {
    int e = LightByte(ofs + 3);
    if (e > 127) e -= 256;
    return vec3(float(LightByte(ofs)), float(LightByte(ofs + 1)), float(LightByte(ofs + 2))) * (exp2(float(e)) / 255.0);
}

// the luxel's colour offset in the first style's page (clamped into the lightmap)
int LuxelOffset(int face, vec2 lux) {
    int smax = faces[face].a.z, tmax = faces[face].a.w;
    int ds = clamp(int(lux.x), 0, smax - 1), dt = clamp(int(lux.y), 0, tmax - 1);
    return faces[face].a.x + 4 * (dt * smax + ds);
}
