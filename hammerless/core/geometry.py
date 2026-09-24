"""Vector math, brush validation and Hammer texture-axis rules."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .ir import Brush, Polygon, Vec3

EPS = 0.01  # Hammer units


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def normalize(a: Vec3) -> Vec3:
    l = length(a)
    return (0.0, 0.0, 0.0) if l == 0 else (a[0] / l, a[1] / l, a[2] / l)


def polygon_normal(verts: list[Vec3]) -> Vec3:
    """Newell's method: robust normal for any (near-)planar polygon, CCW = outward."""
    nx = ny = nz = 0.0
    for i, a in enumerate(verts):
        b = verts[(i + 1) % len(verts)]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return normalize((nx, ny, nz))


@dataclass
class Plane:
    normal: Vec3
    dist: float

    @staticmethod
    def from_polygon(verts: list[Vec3]) -> "Plane":
        n = polygon_normal(verts)
        centroid = scale(
            (sum(v[0] for v in verts), sum(v[1] for v in verts), sum(v[2] for v in verts)),
            1.0 / len(verts),
        )
        return Plane(n, dot(n, centroid))

    def distance(self, p: Vec3) -> float:
        return dot(self.normal, p) - self.dist


# ---------------------------------------------------------------- validation

@dataclass
class BrushProblem:
    source: str
    message: str


def merge_coplanar(faces: list[Polygon]) -> list[Polygon]:
    """Collapse faces lying on the same plane into one entry.

    A brush is defined by its planes, so for VMF output we only need one polygon
    per plane (e.g. a triangulated quad becomes one face). We keep the polygon
    with the most area as representative, and its material.
    """
    groups: list[tuple[Plane, Polygon, float]] = []
    for f in faces:
        if len(f.verts) < 3:
            continue
        pl = Plane.from_polygon(f.verts)
        if length(pl.normal) == 0:
            continue
        area = polygon_area(f.verts)
        for i, (gp, gf, ga) in enumerate(groups):
            if dot(gp.normal, pl.normal) > 0.9999 and abs(gp.dist - pl.dist) < EPS * 10:
                if area > ga:
                    groups[i] = (gp, f, area)
                break
        else:
            groups.append((pl, f, area))
    return [g[1] for g in groups]


def polygon_area(verts: list[Vec3]) -> float:
    total = (0.0, 0.0, 0.0)
    for i in range(1, len(verts) - 1):
        total = add(total, cross(sub(verts[i], verts[0]), sub(verts[i + 1], verts[0])))
    return length(total) / 2.0


def check_brush(brush: Brush, tolerance: float = 0.1) -> list[BrushProblem]:
    """Return problems that would make this an invalid Source brush."""
    problems: list[BrushProblem] = []
    verts = [v for f in brush.faces for v in f.verts]
    faces = merge_coplanar(brush.faces)
    if len(faces) < 4:
        return [BrushProblem(brush.source, "has fewer than 4 distinct faces (not a closed solid)")]

    for f in brush.faces:
        pl = Plane.from_polygon(f.verts)
        worst = max(abs(pl.distance(v)) for v in f.verts)
        if worst > tolerance:
            problems.append(BrushProblem(brush.source, f"has a non-planar face (off by {worst:.2f} units)"))
            break

    planes = [Plane.from_polygon(f.verts) for f in faces]
    if all(pl.distance(v) >= -tolerance for pl in planes for v in verts):
        problems.append(BrushProblem(
            brush.source,
            "has faces pointing inward. In Edit Mode: select all, Mesh > Normals > Recalculate Outside",
        ))
        return problems

    for pl in planes:
        if any(pl.distance(v) > tolerance for v in verts):
            problems.append(BrushProblem(
                brush.source,
                "is not convex. Split it into convex pieces, or enable 'Use Convex Hull' on it",
            ))
            break

    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    if min(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) < 1.0:
        problems.append(BrushProblem(brush.source, "is thinner than 1 unit"))
    if max(map(abs, xs + ys + zs)) > 16384:
        problems.append(BrushProblem(brush.source, "is outside the map limit (±16384 units)"))
    return problems


# ---------------------------------------------------------------- texturing

def world_texture_axes(normal: Vec3) -> tuple[Vec3, Vec3]:
    """World-aligned U/V axes for a face normal, projected along the dominant axis.

    Like Hammer's world alignment, except textures are never mirrored: Hammer uses
    the same U axis for opposite faces, so walls facing -X/-Y and ceilings show
    text backwards. Here U always points to the viewer's right when looking at
    the face from outside, and V points down.
    """
    ax, ay, az = abs(normal[0]), abs(normal[1]), abs(normal[2])
    if az >= ax and az >= ay:
        if normal[2] >= 0:                        # floor, seen from above
            return (1.0, 0.0, 0.0), (0.0, -1.0, 0.0)
        return (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)  # ceiling, seen from below
    if ax >= ay:                                  # wall facing +X / -X
        return (0.0, 1.0 if normal[0] > 0 else -1.0, 0.0), (0.0, 0.0, -1.0)
    return (-1.0 if normal[1] > 0 else 1.0, 0.0, 0.0), (0.0, 0.0, -1.0)  # wall facing +Y / -Y


def bounds(points: list[Vec3]) -> tuple[Vec3, Vec3]:
    return (
        (min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points)),
        (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points)),
    )


def box_brush(mins: Vec3, maxs: Vec3, material: str, source: str = "") -> Brush:
    """Axis-aligned box brush with CCW-outward faces."""
    x0, y0, z0 = mins
    x1, y1, z1 = maxs
    p = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    quads = [
        (4, 5, 6, 7),  # top (+z)
        (0, 3, 2, 1),  # bottom (-z)
        (0, 1, 5, 4),  # -y
        (2, 3, 7, 6),  # +y
        (1, 2, 6, 5),  # +x
        (3, 0, 4, 7),  # -x
    ]
    return Brush([Polygon([p[i] for i in q], material) for q in quads], source)
