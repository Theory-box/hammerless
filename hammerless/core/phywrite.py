"""Our own writer of a model's collision file (.phy): Valve's VPHY wrapper around an IVP compact surface,
the format core/phy.py reads (see its notes). One solid made of convex pieces ('ledges').

Matched against studiomdl's output (measured 2026-10-08): IVP space is metres with axes (x, -z, y) of
model space; each piece is a 16-byte ledge header, 16 bytes per triangle (index, the triangle a ray through
it meets on the far side, then 3 edges: start point, offset in 4-byte units to its twin edge in the
neighbouring triangle), then 16 bytes per point; tree nodes hold the bounding sphere of their pieces and
box half-sizes as 250ths of its radius (rounded up); the surface keeps the volume's mass centre and the
largest distance from it. The surface's rotation inertia follows IVP's own approximation, which isn't the
solid formula (measured ratios 0.94 / 0.82 / 0.82 for two shapes, 0.71 for a cube): we store the true
inertia of the solid per unit mass, and a fixed surface deviation; the game uses these only for how
physics props turn.
"""
from __future__ import annotations

import math
import struct

INCH = 0.0254
# a ledge numbers its triangles in 12 bits and points from an edge to its twin in 15 (signed, 4-byte units):
# past this many triangles in one piece the numbers wrap and the collision comes out broken
MAX_PIECE_TRIANGLES = 4096


def _ivp(p):
    """Model space (inches) -> IVP (metres, axes x, -z, y)."""
    return (p[0] * INCH, -p[2] * INCH, p[1] * INCH)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _solid(points, tris):
    """Volume, centroid and inertia (about the centroid, per unit mass) of a closed convex piece."""
    vol = 0.0
    c = [0.0, 0.0, 0.0]
    cov = [[0.0] * 3 for _ in range(3)]
    o = points[0]
    for a, b, d in tris:
        A, B, D = _sub(points[a], o), _sub(points[b], o), _sub(points[d], o)
        v = _dot(A, _cross(B, D)) / 6.0
        vol += v
        for k in range(3):
            c[k] += v * (A[k] + B[k] + D[k]) / 4.0
        X = (A, B, D)
        s = [A[k] + B[k] + D[k] for k in range(3)]
        for i in range(3):
            for j in range(3):
                cov[i][j] += v / 20.0 * (sum(x[i] * x[j] for x in X) + s[i] * s[j])
    if abs(vol) < 1e-12:
        return 0.0, points[0], (0.0, 0.0, 0.0)
    c = [x / vol for x in c]
    C = [[cov[i][j] / vol - c[i] * c[j] for j in range(3)] for i in range(3)]
    inertia = (C[1][1] + C[2][2], C[0][0] + C[2][2], C[0][0] + C[1][1])
    return vol, tuple(c[k] + o[k] for k in range(3)), inertia


def _ledge(points, tris):
    """One convex piece as an IVP compact ledge (bytes, without its tree node)."""
    n = len(tris)
    if n > MAX_PIECE_TRIANGLES or len(points) > 0xFFFF:
        raise ValueError(f"collision piece has {n} triangles and {len(points)} points: at most "
                         f"{MAX_PIECE_TRIANGLES} triangles fit in a collision file")
    # each triangle: slot 0 header, slots 1-3 edges (4 bytes each); edge (i -> next) twins with (next -> i)
    where = {}
    for t, tri in enumerate(tris):
        for e in range(3):
            where[(tri[e], tri[(e + 1) % 3])] = 4 * t + 1 + e
    out = bytearray(16 + 16 * n)
    normals = []
    cents = []
    for tri in tris:
        a, b, c = (points[i] for i in tri)
        normals.append(_cross(_sub(b, a), _sub(c, a)))
        cents.append(tuple((a[k] + b[k] + c[k]) / 3 for k in range(3)))
    for t, tri in enumerate(tris):
        pierce = _pierce(t, cents, normals, points, tris)
        struct.pack_into("<I", out, 16 + 16 * t, (t & 0xFFF) | ((pierce & 0xFFF) << 12))
        for e in range(3):
            me = 4 * t + 1 + e
            twin = where.get((tri[(e + 1) % 3], tri[e]))
            if twin is None:
                raise ValueError("collision piece isn't closed")
            opp = (twin - me) & 0x7FFF
            struct.pack_into("<I", out, 16 + 16 * t + 4 + 4 * e, (tri[e] & 0xFFFF) | (opp << 16))
    point_off = len(out)
    for p in points:
        out += struct.pack("<3fi", *p, 0)
    size = len(out)
    struct.pack_into("<iiIhh", out, 0, point_off, 0, (1 << 2) | ((size // 16) << 8), n, 0)
    return bytes(out)


def _pierce(t, cents, normals, points, tris):
    """The triangle a ray from triangle t's middle, going inward along its normal, comes out through."""
    o, d = cents[t], normals[t]
    d = (-d[0], -d[1], -d[2])
    best, best_dist = t, float("inf")
    for u, tri in enumerate(tris):
        if u == t:
            continue
        n = normals[u]
        den = _dot(n, d)
        if den <= 1e-12:
            continue
        dist = _dot(n, _sub(points[tri[0]], o)) / den
        if 0 < dist < best_dist:
            # inside the triangle?
            p = tuple(o[k] + d[k] * dist for k in range(3))
            a, b, c = (points[i] for i in tri)
            if all(_dot(_cross(_sub(q, p_), _sub(p, p_)), n) >= -1e-9
                   for p_, q in ((a, b), (b, c), (c, a))):
                best, best_dist = u, dist
    return best


def _sphere(pts):
    lo = [min(p[k] for p in pts) for k in range(3)]
    hi = [max(p[k] for p in pts) for k in range(3)]
    c = tuple((lo[k] + hi[k]) / 2 for k in range(3))
    r = max(math.sqrt(_dot(_sub(p, c), _sub(p, c))) for p in pts)
    half = [(hi[k] - lo[k]) / 2 for k in range(3)]
    return c, r, half


def _box_sizes(half, r):
    return tuple(min(255, math.ceil(h / r * 250 - 1e-6)) if r > 0 else 0 for h in half)


def build_phy(pieces, checksum: int, name: str, surfaceprop: str, mass: float) -> bytes:
    """pieces: [(points in model space inches, triangles counter-clockwise from outside)]."""
    ivp = [([_ivp(p) for p in pts], tris) for pts, tris in pieces]
    vols = [_solid(p, t) for p, t in ivp]
    total = sum(v for v, _c, _i in vols) or 1e-12
    mc = tuple(sum(v * c[k] for v, c, _i in vols) / total for k in range(3))
    # inertia about the shared mass centre (parallel axis), per unit mass
    inertia = [0.0, 0.0, 0.0]
    for v, c, i in vols:
        dx = _sub(c, mc)
        shift = (dx[1] ** 2 + dx[2] ** 2, dx[0] ** 2 + dx[2] ** 2, dx[0] ** 2 + dx[1] ** 2)
        for k in range(3):
            inertia[k] += v / total * (i[k] + shift[k])
    all_pts = [p for pts, _t in ivp for p in pts]
    radius = max(math.sqrt(_dot(_sub(p, mc), _sub(p, mc))) for p in all_pts)

    # layout: compact surface header (48), ledges, then the tree (28 bytes per node, left child right after)
    body = bytearray(48)
    ledge_at = []
    for pts, tris in ivp:
        ledge_at.append(len(body))
        body += _ledge(pts, tris)
    root = len(body)
    leaves = [(i, _sphere(pts)) for i, (pts, _t) in enumerate(ivp)]
    _tree(body, leaves, ledge_at, ivp)
    size = len(body)
    struct.pack_into("<3f3ffIi3i", body, 0, *mc, *inertia, radius, 250 | (size << 8), root, 0, 0,
                     struct.unpack("<i", b"IVPS")[0])
    vphy = struct.pack("<4shhi3fi", b"VPHY", 0x100, 0, size, 1.0, 1.0, 1.0, 0) + bytes(body)
    vol_in3 = total / INCH ** 3
    text = (f'solid {{\n"index" "0"\n"name" "{name}"\n"mass" "{mass:f}"\n"surfaceprop" "{surfaceprop}"\n'
            f'"damping" "0.000000"\n"rotdamping" "0.000000"\n"inertia" "1.000000"\n"volume" "{vol_in3:f}"\n}}\n'
            f'editparams {{\n"rootname" ""\n"totalmass" "{mass:f}"\n}}\n')
    return (struct.pack("<iiii", 16, 0, 1, checksum) + struct.pack("<i", len(vphy)) + vphy
            + text.encode("latin-1") + b"\0")


def _tree(body, leaves, ledge_at, ivp):
    """Append the ledge tree for leaves [(piece index, sphere)]: a leaf points back at its ledge; an inner
    node holds the sphere of everything under it, its left child right after it, the right one further on."""
    node = len(body)
    body += bytearray(28)
    pts = [p for i, _s in leaves for p in ivp[i][0]]
    c, r, half = _sphere(pts)
    if len(leaves) == 1:
        i = leaves[0][0]
        struct.pack_into("<ii3ff4B", body, node, 0, ledge_at[i] - node, *c, r, *_box_sizes(half, r), 0)
        return
    axis = max(range(3), key=lambda k: half[k])
    order = sorted(leaves, key=lambda l: l[1][0][axis])
    mid = len(order) // 2
    _tree(body, order[:mid], ledge_at, ivp)
    right = len(body)
    _tree(body, order[mid:], ledge_at, ivp)
    struct.pack_into("<ii3ff4B", body, node, right - node, 0, *c, r, *_box_sizes(half, r), 0)
