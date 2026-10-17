"""Brush CSG (native/hlcsg.c): convex brushes as planes, cut into convex pieces. For the Blender brush tools
(Carve, Intersect, Clip). Each plane carries a tag (src): which side it came from, so a tool can give the new
faces their materials. A plane is ((nx, ny, nz), d), inside where n.p <= d.

    pieces = subtract(target, cutter)       # [Piece], the target's convex pieces outside the cutter
    Piece.sides: [(normal, d, src)]; Piece.faces: [vertex list per side, in the same order]
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field

SUBTRACT, INTERSECT, CLIP = 0, 1, 2


@dataclass
class Piece:
    sides: list = field(default_factory=list)    # [((nx, ny, nz), d, src)]
    faces: list = field(default_factory=list)    # [[(x, y, z), ...]] per side, wound outward

    def volume(self) -> float:
        """d * area / 3 summed over the faces (a closed convex solid)."""
        v = 0.0
        for (n, d, _s), f in zip(self.sides, self.faces):
            v += d * _area(f, n) / 3.0
        return v


def _area(poly, n) -> float:
    sx = sy = sz = 0.0
    x0, y0, z0 = poly[0]
    for i in range(1, len(poly) - 1):
        ax, ay, az = poly[i][0] - x0, poly[i][1] - y0, poly[i][2] - z0
        bx, by, bz = poly[i + 1][0] - x0, poly[i + 1][1] - y0, poly[i + 1][2] - z0
        sx += ay * bz - az * by
        sy += az * bx - ax * bz
        sz += ax * by - ay * bx
    return 0.5 * abs(sx * n[0] + sy * n[1] + sz * n[2])


def _lib():
    from . import fastnav
    if not fastnav.available():
        raise RuntimeError("Hammerless's native library (_hlnav.dll) isn't there: the brush tools need it")
    lib = fastnav._lib
    if not getattr(lib, "_hl_csg_typed", False):
        lib.hl_csg.restype = ctypes.c_int
        lib._hl_csg_typed = True
    return lib


def _planes(sides):
    n = len(sides)
    arr = (ctypes.c_double * (4 * max(1, n)))(*[v for (nrm, d, _s) in sides for v in (*nrm, d)])
    src = (ctypes.c_int * max(1, n))(*[s for (_n, _d, s) in sides])
    return n, arr, src


def _run(op: int, a, b) -> list[Piece | None]:
    lib = _lib()
    na, ap, asrc = _planes(a)
    nb, bp, bsrc = _planes(b)
    cap_pieces = max(4, 2 * (nb + 2))
    cap_sides = cap_pieces * (na + nb + 4)
    cap_verts = cap_sides * 24
    while True:
        out_planes = (ctypes.c_double * (4 * cap_sides))()
        out_src, out_sides = (ctypes.c_int * cap_sides)(), (ctypes.c_int * cap_pieces)()
        out_verts, out_fv = (ctypes.c_double * (3 * cap_verts))(), (ctypes.c_int * cap_sides)()
        count = lib.hl_csg(ctypes.c_int(op), ctypes.c_int(na), ap, asrc, ctypes.c_int(nb), bp, bsrc,
                           out_planes, out_src, out_sides, out_verts, out_fv,
                           ctypes.c_int(cap_sides), ctypes.c_int(cap_verts), ctypes.c_int(cap_pieces))
        if count != -1:
            break
        cap_sides, cap_verts, cap_pieces = cap_sides * 4, cap_verts * 4, cap_pieces * 4
    pieces: list[Piece | None] = []
    si = vi = 0
    for k in range(count):
        ns = out_sides[k]
        if ns == 0:
            pieces.append(None)                  # (clip: nothing on that side)
            continue
        p = Piece()
        for _ in range(ns):
            p.sides.append(((out_planes[4 * si], out_planes[4 * si + 1], out_planes[4 * si + 2]),
                            out_planes[4 * si + 3], out_src[si]))
            nv = out_fv[si]
            p.faces.append([(out_verts[3 * (vi + j)], out_verts[3 * (vi + j) + 1], out_verts[3 * (vi + j) + 2])
                            for j in range(nv)])
            vi += nv
            si += 1
        pieces.append(p)
    return pieces


def subtract(target, cutter) -> list[Piece]:
    """The target's convex pieces outside the cutter (the target itself, tidied, when they don't overlap; [] when
    the cutter swallows it). New faces carry the cutter's tags."""
    return [p for p in _run(SUBTRACT, target, cutter) if p is not None]


def intersect(a, b) -> Piece | None:
    """The overlap of two brushes, or None."""
    out = [p for p in _run(INTERSECT, a, b) if p is not None]
    return out[0] if out else None


def clip(target, plane) -> tuple[Piece | None, Piece | None]:
    """The target cut by a plane ((n, d, src): src tags the new face): (the piece in front, the piece behind)."""
    front, back = _run(CLIP, target, [plane])
    return front, back


def merge(a, b) -> Piece | None:
    """The two brushes as one when their union is convex (they share a face and nothing sticks out), else None."""
    lib = _lib()
    if not getattr(lib, "_hl_merge_typed", False):
        lib.hl_csg_merge.restype = ctypes.c_int
        lib._hl_merge_typed = True
    na, ap, asrc = _planes(a)
    nb, bp, bsrc = _planes(b)
    cap_sides = na + nb + 8
    cap_verts = cap_sides * 32
    while True:
        out_planes = (ctypes.c_double * (4 * cap_sides))()
        out_src, out_sides = (ctypes.c_int * cap_sides)(), (ctypes.c_int * 1)()
        out_verts, out_fv = (ctypes.c_double * (3 * cap_verts))(), (ctypes.c_int * cap_sides)()
        r = lib.hl_csg_merge(ctypes.c_int(na), ap, asrc, ctypes.c_int(nb), bp, bsrc, out_planes, out_src, out_sides,
                             out_verts, out_fv, ctypes.c_int(cap_sides), ctypes.c_int(cap_verts))
        if r != -1:
            break
        cap_sides, cap_verts = cap_sides * 4, cap_verts * 4
    if r != 1:
        return None
    p = Piece()
    vi = 0
    for si in range(out_sides[0]):
        p.sides.append(((out_planes[4 * si], out_planes[4 * si + 1], out_planes[4 * si + 2]), out_planes[4 * si + 3],
                        out_src[si]))
        nv = out_fv[si]
        p.faces.append([(out_verts[3 * (vi + j)], out_verts[3 * (vi + j) + 1], out_verts[3 * (vi + j) + 2])
                        for j in range(nv)])
        vi += nv
    return p


def box(lo, hi, src0: int = 0) -> list:
    """An axis-aligned box's planes (tags src0 .. src0 + 5: +x -x +y -y +z -z)."""
    return [((1.0, 0.0, 0.0), hi[0], src0), ((-1.0, 0.0, 0.0), -lo[0], src0 + 1),
            ((0.0, 1.0, 0.0), hi[1], src0 + 2), ((0.0, -1.0, 0.0), -lo[1], src0 + 3),
            ((0.0, 0.0, 1.0), hi[2], src0 + 4), ((0.0, 0.0, -1.0), -lo[2], src0 + 5)]


def plane_from_points(a, b, c, src: int = 0):
    """The plane through three points (wound counter-clockwise seen from outside), as ((n), d, src)."""
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = (nx * nx + ny * ny + nz * nz) ** 0.5
    if ln == 0.0:
        return None
    nx, ny, nz = nx / ln, ny / ln, nz / ln
    return ((nx, ny, nz), nx * a[0] + ny * a[1] + nz * a[2], src)
