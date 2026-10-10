"""Texture alignment on brush faces: Hammer's texture axes and Blender UVs, both ways.

A Hammer side's texture: texel s = (p . u) / uscale + ushift, t = (p . v) / vscale + vshift, with u, v unit vectors
("uaxis" "[ux uy uz ushift] uscale"). Blender UVs hold the same thing as (s / width, -t / height) (the importer's
convention: v up). Faces painted with the texturing tools keep their alignment in their UVs; the exporter turns the
UVs back into the axes exactly (axes_from_uv), so what the viewport shows is what the game shows.

Alignment like Hammer's Face Edit: World (the dominant axis' plane, as Hammerless always aligned) or Face (in the
face's own plane), then scale, shift and rotation; justify fits or lines the texture up with the face's edges.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import polygon_normal, world_texture_axes

Vec3 = tuple[float, float, float]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def _scaled(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


@dataclass
class Alignment:
    """How a face's texture lies: the settings the texturing panel shows."""
    scale_u: float = 0.25
    scale_v: float = 0.25
    shift_u: float = 0.0
    shift_v: float = 0.0
    rotation: float = 0.0           # degrees, about the face normal
    mode: str = "WORLD"             # WORLD or FACE


def base_axes(normal: Vec3, mode: str) -> tuple[Vec3, Vec3]:
    """The unrotated texture axes for a face: World (Hammerless's world alignment) or Face (in the face's plane:
    v down the face's slope, or world-aligned when the face is level)."""
    if mode == "FACE":
        n = _norm(normal)
        if abs(n[2]) < 0.999:
            # v: the in-plane direction closest to straight down; u across it (to the right seen from outside)
            down = (0.0, 0.0, -1.0)
            d = _dot(down, n)
            v = _norm((down[0] - d * n[0], down[1] - d * n[1], down[2] - d * n[2]))
            u = _norm(_cross(n, v))
            return u, v
    return world_texture_axes(normal)


def rotate_axes(u: Vec3, v: Vec3, normal: Vec3, degrees: float) -> tuple[Vec3, Vec3]:
    """The axes turned about the face normal (positive: counter-clockwise seen from outside, as Hammer)."""
    if not degrees:
        return u, v
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    n = _norm(normal)

    def rot(x):
        # Rodrigues: x cos + (n x x) sin + n (n . x)(1 - cos)
        k = _cross(n, x)
        d = _dot(n, x) * (1 - c)
        return (x[0] * c + k[0] * s + n[0] * d, x[1] * c + k[1] * s + n[1] * d, x[2] * c + k[2] * s + n[2] * d)
    return rot(u), rot(v)


def axes(points: list[Vec3], al: Alignment) -> tuple[tuple[Vec3, float, float], tuple[Vec3, float, float]]:
    """The face's texture axes: ((u, ushift, uscale), (v, vshift, vscale)). points: counter-clockwise from outside."""
    n = polygon_normal(points)
    u, v = base_axes(n, al.mode)
    u, v = rotate_axes(u, v, n, al.rotation)
    su = al.scale_u if abs(al.scale_u) >= 1e-4 else math.copysign(1e-4, al.scale_u or 1.0)
    sv = al.scale_v if abs(al.scale_v) >= 1e-4 else math.copysign(1e-4, al.scale_v or 1.0)
    return (u, al.shift_u, su), (v, al.shift_v, sv)


def texel(p: Vec3, axis) -> float:
    vec, shift, scale = axis
    return _dot(p, vec) / (scale if abs(scale) > 1e-9 else 1e-9) + shift


def uvs(points: list[Vec3], uaxis, vaxis, width: int, height: int) -> list[tuple[float, float]]:
    """Blender UVs for the points (v up, as the importer writes them). Whole texture repeats are taken off so a face
    far from the origin keeps its UVs small (Blender stores them as 32-bit floats: large ones lose the alignment)."""
    out = [(texel(p, uaxis) / max(width, 1), -texel(p, vaxis) / max(height, 1)) for p in points]
    if not out:
        return out
    du, dv = math.floor(out[0][0]), math.floor(out[0][1])
    return [(u - du, v - dv) for u, v in out]


def axes_from_uv(points: list[Vec3], uv: list[tuple[float, float]], width: int, height: int):
    """Hammer's axes from a face's corners and their UVs (the affine map they make, in the face's plane):
    ((u, ushift, uscale), (v, vshift, vscale)), or None when the UVs or the face are degenerate."""
    if len(points) < 3:
        return None
    n = polygon_normal(points)
    if _dot(n, n) < 1e-12:
        return None
    # three corners spanning the face (the first, and the two making the largest triangle with it)
    best, pick = 0.0, None
    p0 = points[0]
    for i in range(1, len(points)):
        for j in range(i + 1, len(points)):
            c = _cross(tuple(points[i][k] - p0[k] for k in range(3)), tuple(points[j][k] - p0[k] for k in range(3)))
            a = _dot(c, c)
            if a > best:
                best, pick = a, (i, j)
    if pick is None or best < 1e-10:
        return None
    e1 = _norm(tuple(points[pick[0]][k] - p0[k] for k in range(3)))
    e2 = _norm(_cross(n, e1))
    idx = (0, pick[0], pick[1])
    xy = [(_dot(points[i], e1), _dot(points[i], e2)) for i in idx]
    det = (xy[1][0] - xy[0][0]) * (xy[2][1] - xy[0][1]) - (xy[2][0] - xy[0][0]) * (xy[1][1] - xy[0][1])
    if abs(det) < 1e-12:
        return None
    out = []
    for k, size, sign in ((0, width, 1.0), (1, height, -1.0)):
        vals = [sign * uv[i][k] * size for i in idx]          # texels (t is minus v * height)
        # vals = a x + b y + c over the three corners
        a = ((vals[1] - vals[0]) * (xy[2][1] - xy[0][1]) - (vals[2] - vals[0]) * (xy[1][1] - xy[0][1])) / det
        b = ((xy[1][0] - xy[0][0]) * (vals[2] - vals[0]) - (xy[2][0] - xy[0][0]) * (vals[1] - vals[0])) / det
        c = vals[0] - a * xy[0][0] - b * xy[0][1]
        g = (e1[0] * a + e2[0] * b, e1[1] * a + e2[1] * b, e1[2] * a + e2[2] * b)
        glen = math.sqrt(_dot(g, g))
        if glen < 1e-12:
            return None
        out.append((_scaled(g, 1.0 / glen), c % max(size, 1), 1.0 / glen))     # (shift: one repeat is all)
    return out[0], out[1]


def alignment_from_axes(points: list[Vec3], uaxis, vaxis, mode: str = "WORLD") -> Alignment:
    """The settings that give these axes (for reading a face back into the panel): the rotation is the angle from
    the mode's base u axis to this u axis about the normal; a flipped axis reads as a negative scale."""
    n = _norm(polygon_normal(points))
    bu, bv = base_axes(n, mode)

    def flat(a):                      # (in the face's plane: what a texture on the face shows of an axis)
        d = _dot(a, n)
        return (a[0] - d * n[0], a[1] - d * n[1], a[2] - d * n[2])
    u = uaxis[0]
    pbu = _norm(flat(bu))
    ang = math.degrees(math.atan2(_dot(_cross(pbu, u), n), _dot(pbu, u)))
    ang = round(ang, 4) % 360.0
    ru, rv = rotate_axes(bu, bv, n, ang)
    fu, fv = flat(ru), flat(rv)
    # the face shows axis/scale = flat(r)/s: s = scale read * |flat(r)|, its sign where flat(r) points
    su = uaxis[2] * math.sqrt(_dot(fu, fu)) * (1 if _dot(fu, u) >= 0 else -1)
    sv = vaxis[2] * math.sqrt(_dot(fv, fv)) * (1 if _dot(fv, vaxis[0]) >= 0 else -1)
    # on the face p.r = p.flat(r) + (r.n) d: the shift takes the difference (none when r is in the plane)
    d = _dot(n, points[0])
    shu = uaxis[1] - _dot(ru, n) * d / su if abs(su) > 1e-9 else uaxis[1]
    shv = vaxis[1] - _dot(rv, n) * d / sv if abs(sv) > 1e-9 else vaxis[1]
    return Alignment(su, sv, shu, shv, ang, mode)


def _nonzero(s: float) -> float:
    return s if abs(s) >= 1e-4 else math.copysign(1e-4, s or 1.0)


def justify(points: list[Vec3], al: Alignment, how: str, width: int, height: int) -> Alignment:
    """The alignment moved (and for FIT, scaled) so the texture lines up with the face: LEFT, RIGHT, TOP, BOTTOM,
    CENTER, or FIT (one copy of the texture exactly covering the face)."""
    (u, _su, scu), (v, _sv, scv) = axes(points, al)
    pu = [_dot(p, u) for p in points]
    pv = [_dot(p, v) for p in points]
    out = Alignment(_nonzero(al.scale_u), _nonzero(al.scale_v), al.shift_u, al.shift_v, al.rotation, al.mode)
    if how == "FIT":
        out.scale_u = math.copysign(max((max(pu) - min(pu)) / max(width, 1), 1e-4), al.scale_u or 1)
        out.scale_v = math.copysign(max((max(pv) - min(pv)) / max(height, 1), 1e-4), al.scale_v or 1)
        how = "TOPLEFT"
    su, sv = out.scale_u, out.scale_v
    lo_u = min(p / su for p in pu)
    hi_u = max(p / su for p in pu)
    lo_v = min(p / sv for p in pv)
    hi_v = max(p / sv for p in pv)
    if how in ("LEFT", "TOPLEFT"):
        out.shift_u = -lo_u
    elif how == "RIGHT":
        out.shift_u = width - hi_u
    elif how == "CENTER":
        out.shift_u = (width - (lo_u + hi_u)) / 2
    if how in ("TOP", "TOPLEFT"):
        out.shift_v = -lo_v
    elif how == "BOTTOM":
        out.shift_v = height - hi_v
    elif how == "CENTER":
        out.shift_v = (height - (lo_v + hi_v)) / 2
    out.shift_u %= max(width, 1)
    out.shift_v %= max(height, 1)
    return out
