// expand_lib.glsl: hlvvis -jacobi's expansion step (the chops and the separating planes), a library to #include.
// Operation for operation the CPU's (see expand.comp). The including shader declares, before this file:
//   push constants pc with float eps_up, float len_lo, uint robust, uint maxchop
//   #define MAXW 64 (winding buffers) and the float controls / float64 extensions (as expand.comp)
#define MAXCHOP int(pc.maxchop)   // a chop that would need more points keeps its input (the CPU's g_maxpts)

// windings in private arrays (a: the working pass, s: the working source, t: scratch)
vec3 A[MAXW]; int nA;
vec3 Sr[MAXW]; int nS;
vec3 Wp[MAXW]; int nW;
vec3 T[MAXW];

precise float dot3(vec3 p, vec3 n) {           // (p.x * n.x + p.y * n.y) + p.z * n.z, as the CPU's SSE and dotv
    precise float d = p.x * n.x + p.y * n.y;
    d = d + p.z * n.z;
    return d;
}

// chop the working pass A by plane (n, d): keep the front (points on the plane kept). Returns 0 if nothing is left,
// 1 if A changed or stayed (a chop needing more than MAXCHOP points keeps A as it was, like the CPU)
int chopA(vec3 n, float d) {
    float dists[MAXW + 1]; int sides[MAXW + 1];
    bool anyf = false, anyb = false;
    for (int i = 0; i < nA; i++) {
        precise float dd = dot3(A[i], n) - d;
        dists[i] = dd;
        if (dd >= pc.eps_up) { sides[i] = 0; anyf = true; }
        else if (dd <= -pc.eps_up) { sides[i] = 1; anyb = true; }
        else sides[i] = 2;
    }
    if (!anyb) return 1;
    if (!anyf) return 0;
    sides[nA] = sides[0]; dists[nA] = dists[0];
    int m = 0;
    for (int i = 0; i < nA; i++) {
        vec3 p1 = A[i];
        if (m == MAXCHOP) return 1;
        if (sides[i] == 2) { T[m++] = p1; continue; }
        if (sides[i] == 0) T[m++] = p1;
        if (sides[i + 1] == 2 || sides[i + 1] == sides[i]) continue;
        if (m == MAXCHOP) return 1;
        vec3 p2 = A[(i + 1) % nA]; precise vec3 mid;
        precise float den = dists[i] - dists[i + 1];
        precise float t = float(double(dists[i]) / double(den));   // (the CPU's correctly rounded float division: Vulkan's float divide may be 2.5 ulp off)
        mid.x = n.x == 1.0 ? d : n.x == -1.0 ? -d : p1.x + t * (p2.x - p1.x);
        mid.y = n.y == 1.0 ? d : n.y == -1.0 ? -d : p1.y + t * (p2.y - p1.y);
        mid.z = n.z == 1.0 ? d : n.z == -1.0 ? -d : p1.z + t * (p2.z - p1.z);
        T[m++] = mid;
    }
    for (int i = 0; i < m; i++) A[i] = T[i];
    nA = m;
    return 1;
}

// the same chop on the working source Sr
int chopS(vec3 n, float d) {
    float dists[MAXW + 1]; int sides[MAXW + 1];
    bool anyf = false, anyb = false;
    for (int i = 0; i < nS; i++) {
        precise float dd = dot3(Sr[i], n) - d;
        dists[i] = dd;
        if (dd >= pc.eps_up) { sides[i] = 0; anyf = true; }
        else if (dd <= -pc.eps_up) { sides[i] = 1; anyb = true; }
        else sides[i] = 2;
    }
    if (!anyb) return 1;
    if (!anyf) return 0;
    sides[nS] = sides[0]; dists[nS] = dists[0];
    int m = 0;
    for (int i = 0; i < nS; i++) {
        vec3 p1 = Sr[i];
        if (m == MAXCHOP) return 1;
        if (sides[i] == 2) { T[m++] = p1; continue; }
        if (sides[i] == 0) T[m++] = p1;
        if (sides[i + 1] == 2 || sides[i + 1] == sides[i]) continue;
        if (m == MAXCHOP) return 1;
        vec3 p2 = Sr[(i + 1) % nS]; precise vec3 mid;
        precise float den = dists[i] - dists[i + 1];
        precise float t = float(double(dists[i]) / double(den));   // (the CPU's correctly rounded float division: Vulkan's float divide may be 2.5 ulp off)
        mid.x = n.x == 1.0 ? d : n.x == -1.0 ? -d : p1.x + t * (p2.x - p1.x);
        mid.y = n.y == 1.0 ? d : n.y == -1.0 ? -d : p1.y + t * (p2.y - p1.y);
        mid.z = n.z == 1.0 ? d : n.z == -1.0 ? -d : p1.z + t * (p2.z - p1.z);
        T[m++] = mid;
    }
    for (int i = 0; i < m; i++) Sr[i] = T[i];
    nS = m;
    return 1;
}

// The separating planes of (src, pas) (the CPU's sep_planes, one pass point at a time), each applied to the working
// pass A at once, in the CPU's order (source edges i, then pass points j). srcIsS: src is the working source Sr and
// pas is Wp; else src is Wp and pas is Sr (flipclip: the planes negated). Returns 0 if A is clipped away.
// float(1.0 / sqrt(double(len))), the CPU's (double then rounded to float), bit for bit, mostly without doubles
// (the card's FP64 is 1/64 rate: this was most of the expansion's time). r = inversesqrt(len) is within a few ulps;
// e = 1 - len * r * r is worked out almost exactly with fused multiply-adds (r * r as hi + lo, then len times it), so
// the true value is r + r * e / 2 to about 2^-20 of an ulp. When that lies clearly inside one float's rounding
// interval (more than 1/64 of an ulp from a halfway point; the double result is within 2^-28 ulps of the true value)
// the rounded double is that float. Otherwise (a hair from a halfway point, a power-of-two edge, or a poor estimate)
// the double way, as before.
float rsqrt_as_cpu(float len) {
    float r = inversesqrt(len);
    uint rb = floatBitsToUint(r);
    uint mant = rb & 0x7fffffu;
    if (mant > 8u && mant < 0x7ffff7u) {           // (not within 8 ulps of a power of two: one ulp size around r)
        precise float hi = r * r;
        precise float lo = fma(r, r, -hi);
        precise float phi = len * hi;
        precise float plo = fma(len, hi, -phi) + len * lo;
        precise float e = (1.0 - phi) - plo;
        precise float u = uintBitsToFloat(rb + 1u) - r;
        precise float dd = (0.5 * r * e) / u;      // the true value - r, in ulps
        if (abs(dd) < 4.0) {
            float k = floor(dd + 0.5);
            if (abs(dd - k) < 0.484375)            // (> 1/64 ulp from a halfway point)
                return uintBitsToFloat(uint(int(rb) + int(k)));
        }
    }
    precise double inv = 1.0lf / sqrt(double(len));
    return float(inv);
}

int clip_seps(bool srcIsS) {
    int ns = srcIsS ? nS : nW, np = srcIsS ? nW : nS;
    for (int i = 0; i < ns; i++) {
        int l = (i + 1) % ns;
        vec3 si = srcIsS ? Sr[i] : Wp[i];
        vec3 sl = srcIsS ? Sr[l] : Wp[l];
        precise vec3 V1 = vec3(sl.x - si.x, sl.y - si.y, sl.z - si.z);
        for (int j = 0; j < np; j++) {
            vec3 P = srcIsS ? Wp[j] : Sr[j];
            precise vec3 v2 = vec3(P.x - si.x, P.y - si.y, P.z - si.z);
            precise float nx = V1.y * v2.z - V1.z * v2.y;
            precise float ny = V1.z * v2.x - V1.x * v2.z;
            precise float nz = V1.x * v2.y - V1.y * v2.x;
            precise float len = nx * nx + ny * ny;
            len = len + nz * nz;
            if (len <= pc.len_lo) continue;
            float invf = rsqrt_as_cpu(len);
            precise float fx = nx * invf, fy = ny * invf, fz = nz * invf;
            vec3 nrm = vec3(fx, fy, fz);
            precise float dist = dot3(P, nrm);
            // which side the source is on: its first decisive point (not the edge's two)
            int decided = 0; bool flip = false;
            for (int k = 0; k < ns && decided == 0; k++) {
                if (k == i || k == l) continue;
                vec3 sk = srcIsS ? Sr[k] : Wp[k];
                precise float dk = dot3(sk, nrm) - dist;
                if (dk <= -pc.eps_up) decided = 1;
                else if (dk >= pc.eps_up) { decided = 1; flip = true; }
            }
            if (decided == 0) continue;                         // planar with the source: skip
            if (flip) { nrm = -nrm; dist = -dist; }
            bool bad = false, front = false;
            if (pc.robust != 0u) {
                // every source point behind or on the plane (merged hulls' hair-length edges)
                for (int k = 0; k < ns; k++) {
                    if (k == i || k == l) continue;
                    vec3 sk = srcIsS ? Sr[k] : Wp[k];
                    precise float dk = dot3(sk, nrm) - dist;
                    if (dk >= pc.eps_up) bad = true;
                }
            }
            // every other pass point on or in front, at least one in front
            for (int k = 0; k < np; k++) {
                if (k == j) continue;
                vec3 pk = srcIsS ? Wp[k] : Sr[k];
                precise float dk = dot3(pk, nrm) - dist;
                if (dk <= -pc.eps_up) bad = true;
                if (dk >= pc.eps_up) front = true;
            }
            if (bad || !front) continue;
            if (!srcIsS) { nrm = -nrm; dist = -dist; }          // flipclip
            if (chopA(nrm, dist) == 0) return 0;
        }
    }
    return 1;
}

