"""Model collision (.phy): Valve's VPHY wrapper around an IVP compact surface, read into convex
pieces (each the points of one IVP 'ledge'), in Hammer units and model space.

Layout: a 16-byte file header (header size, id, solid count, checksum), then per solid an int
size and the solid: 'VPHY', version, model type (0 = IVP compact surface), surface size, drag
axis areas (3 floats), axis map size; then the compact surface (48 bytes: mass centre, inertia,
radius, deviation/size, offset of the ledge tree root, 'IVPS'). The ledge tree has 28-byte nodes
(offset of the right child, offset of the node's ledge, centre, radius, box sizes); a node with
no right child is a leaf and owns one convex ledge: 16-byte header (offset of its points,
client data, flags/size, triangle count), 16-byte triangles of 3 edges whose low 16 bits index
the ledge's 16-byte points. IVP is in metres with y and z swapped: hl = (x, z, -y) / 0.0254.
"""
from __future__ import annotations

import struct

METERS_PER_INCH = 0.0254


def _ivp_to_hl(x: float, y: float, z: float) -> tuple[float, float, float]:
    f = 1.0 / METERS_PER_INCH
    return (x * f, z * f, -y * f)


def read_phy_pieces(data: bytes):
    """[(points, triangles as index triples into points)] for every convex piece. A damaged file
    raises ValueError (instead of reading past the end or looping)."""
    try:
        return _read_phy_pieces(data)
    except (struct.error, IndexError, RecursionError) as ex:
        raise ValueError(f"damaged collision model ({ex})") from None


def _read_phy_pieces(data: bytes):
    if not data or len(data) < 16:
        return []
    header, _ident, solids, _checksum = struct.unpack_from("<iiii", data, 0)
    pos = header
    pieces = []
    for _ in range(solids):
        (size,) = struct.unpack_from("<i", data, pos)
        body = data[pos + 4:pos + 4 + size]
        pos += 4 + size
        if body[:4] != b"VPHY":
            continue                       # (old format without the VPHY header: not used by L4D2)
        _vid, _version, model_type, _surface = struct.unpack_from("<4shhi", body, 0)
        if model_type != 0:
            continue
        cs = 28
        (root,) = struct.unpack_from("<i", body, cs + 32)
        stack = [cs + root]
        visited = set()
        while stack:
            node = stack.pop()
            if node in visited or not 0 <= node <= len(body) - 28:
                raise ValueError("bad ledge tree")
            visited.add(node)
            right, ledge_off = struct.unpack_from("<ii", body, node)
            if right == 0:                 # leaf: one convex piece
                ledge = node + ledge_off
                point_off, _client, _flags, ntri = struct.unpack_from("<iiih", body, ledge)
                points_at = ledge + point_off
                tris = []
                for t in range(ntri):
                    tri = ledge + 16 + 16 * t
                    tris.append(tuple(struct.unpack_from("<I", body, tri + 4 + 4 * e)[0] & 0xFFFF for e in range(3)))
                used = sorted({i for tri in tris for i in tri})
                where = {i: k for k, i in enumerate(used)}
                pts = []
                for i in used:
                    x, y, z, _w = struct.unpack_from("<ffff", body, points_at + 16 * i)
                    pts.append(_ivp_to_hl(x, y, z))
                if len(pts) >= 4:
                    pieces.append((pts, [tuple(where[i] for i in tri) for tri in tris]))
            else:
                stack.append(node + 28)         # left child follows the node (28-byte nodes)
                stack.append(node + right)
    return pieces


def hull_planes(points, triangles, eps: float = 0.01):
    """The outward planes of a convex piece from its own triangles (as the .phy stores them): the
    same planes convex_planes finds, without trying every three points (that took minutes on
    detailed models). Triangles that aren't hull faces are skipped the same way."""
    planes = []
    n = len(points)
    for a, b, c in triangles:
        if max(a, b, c) >= n:
            continue
        p, q, r = points[a], points[b], points[c]
        u = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        v = (r[0] - p[0], r[1] - p[1], r[2] - p[2])
        nrm = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        length = (nrm[0] ** 2 + nrm[1] ** 2 + nrm[2] ** 2) ** 0.5
        if length < 1e-6:
            continue
        nrm = (nrm[0] / length, nrm[1] / length, nrm[2] / length)
        d = nrm[0] * p[0] + nrm[1] * p[1] + nrm[2] * p[2]
        side = [nrm[0] * s[0] + nrm[1] * s[1] + nrm[2] * s[2] - d for s in points]
        if all(x <= eps for x in side):
            pass
        elif all(x >= -eps for x in side):
            nrm, d = (-nrm[0], -nrm[1], -nrm[2]), -d
        else:
            continue
        if not any(abs(nrm[0] - m[0]) < 1e-4 and abs(nrm[1] - m[1]) < 1e-4 and abs(nrm[2] - m[2]) < 1e-4
                   and abs(d - e) < 1e-3 for m, e in planes):
            planes.append((nrm, d))
    return planes


def convex_planes(points, eps: float = 0.01):
    """The outward planes (normal, dist) of a convex point set's hull: every plane through
    three of its points with all points on or behind it (a small brute force: pieces have
    only a few dozen points)."""
    import itertools
    import math
    planes = []
    n = len(points)
    for a, b, c in itertools.combinations(range(n), 3):
        p, q, r = points[a], points[b], points[c]
        u = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        v = (r[0] - p[0], r[1] - p[1], r[2] - p[2])
        nrm = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        length = math.sqrt(nrm[0] ** 2 + nrm[1] ** 2 + nrm[2] ** 2)
        if length < 1e-6:
            continue
        nrm = (nrm[0] / length, nrm[1] / length, nrm[2] / length)
        d = nrm[0] * p[0] + nrm[1] * p[1] + nrm[2] * p[2]
        side = [nrm[0] * s[0] + nrm[1] * s[1] + nrm[2] * s[2] - d for s in points]
        if all(x <= eps for x in side):
            pass
        elif all(x >= -eps for x in side):
            nrm, d = (-nrm[0], -nrm[1], -nrm[2]), -d
        else:
            continue
        if not any(abs(nrm[0] - m[0]) < 1e-4 and abs(nrm[1] - m[1]) < 1e-4 and abs(nrm[2] - m[2]) < 1e-4
                   and abs(d - e) < 1e-3 for m, e in planes):
            planes.append((nrm, d))
    return planes


def angle_matrix(pitch: float, yaw: float, roll: float):
    """mathlib AngleMatrix: rows of the 3x3 rotation (columns = forward, left, up)."""
    import math
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    return ((cp * cy, sr * sp * cy + cr * -sy, cr * sp * cy + -sr * -sy),
            (cp * sy, sr * sp * sy + cr * cy, cr * sp * sy + -sr * cy),
            (-sp, sr * cp, cr * cp))


def place(points, origin, angles):
    """Model-space points to world space for an entity at origin with angles (pitch yaw roll)."""
    m = angle_matrix(*angles)
    return [(origin[0] + m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2],
             origin[1] + m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2],
             origin[2] + m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2]) for p in points]


