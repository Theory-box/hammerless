"""Vector math, brush validation and Hammer texture-axis rules."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .ir import Brush, Polygon, Vec3

EPS = 0.01  # Hammer units
MAX_FACE_CORNERS = 64   # vbsp crashes on a brush face with more corners (MAX_POINTS_ON_WINDING; measured:
                        # 64 compiles, 65 crashes; a brush itself compiled with 4000 sides)
EXACT_OPEN_PLANES = 512  # up to this many planes the open-side test tries every direction (exact, fast)


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
    # Same result as comparing each face with every group in order (the first group that matches
    # wins), but groups are found through a grid of plane values, so a dense mesh with thousands of
    # faces doesn't take minutes. dot > 0.9999 means the normals differ by < 0.0142 per component
    # and the distances by < EPS * 10: one grid cell either way covers every possible match.
    return [g[1] for g in coplanar_groups(faces)]


def coplanar_groups(faces: list[Polygon]) -> list[tuple[Plane, Polygon, float, list[Polygon]]]:
    """merge_coplanar's groups: (plane, representative face, its area, every face on that plane)."""
    groups: list[tuple[Plane, Polygon, float, list[Polygon]]] = []
    grid: dict[tuple, list[int]] = {}
    nstep, dstep = 0.02, EPS * 10
    for f in faces:
        if len(f.verts) < 3:
            continue
        pl = Plane.from_polygon(f.verts)
        if length(pl.normal) == 0:
            continue
        area = polygon_area(f.verts)
        key = (math.floor(pl.normal[0] / nstep), math.floor(pl.normal[1] / nstep), math.floor(pl.normal[2] / nstep),
               math.floor(pl.dist / dstep))
        best = None
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                for c in (-1, 0, 1):
                    for d in (-1, 0, 1):
                        for i in grid.get((key[0] + a, key[1] + b, key[2] + c, key[3] + d), ()):
                            if best is not None and i >= best:
                                continue
                            gp = groups[i][0]
                            if dot(gp.normal, pl.normal) > 0.9999 and abs(gp.dist - pl.dist) < EPS * 10:
                                best = i
        if best is None:
            grid.setdefault(key, []).append(len(groups))
            groups.append((pl, f, area, [f]))
        else:
            groups[best][3].append(f)
            if area > groups[best][2]:
                groups[best] = (groups[best][0], f, area, groups[best][3])
    return groups


def polygon_area(verts: list[Vec3]) -> float:
    total = (0.0, 0.0, 0.0)
    for i in range(1, len(verts) - 1):
        total = add(total, cross(sub(verts[i], verts[0]), sub(verts[i + 1], verts[0])))
    return length(total) / 2.0


def open_direction(normals: list[Vec3], eps: float = 1e-6) -> Vec3 | None:
    """A direction no face blocks (the solid would go on forever that way), or None if the
    faces close it in. Such directions include one of the cross products of two face normals,
    or a reversed normal."""
    import numpy as np
    if not normals:
        return None
    n = np.asarray(normals, dtype=np.float64)
    i, j = np.triu_indices(len(n), k=1)
    c = np.cross(n[i], n[j])
    lens = np.linalg.norm(c, axis=1)
    c = c[lens > 1e-9] / lens[lens > 1e-9, None]
    pairs = np.empty((2 * len(c), 3))
    pairs[0::2], pairs[1::2] = c, -c                 # the same order as trying c, then -c, per pair
    cands = np.concatenate([-n, pairs])
    ok = np.nonzero((cands @ n.T <= eps).all(axis=1))[0]
    return tuple(float(v) for v in cands[ok[0]]) if len(ok) else None


def _direction_name(d: Vec3) -> str:
    axis = max(range(3), key=lambda i: abs(d[i]))
    return ("+" if d[axis] > 0 else "-") + "XYZ"[axis]


def _corners(points: "np.ndarray", normal) -> int:
    """Corners of the convex outline of points lying on one plane (points along an edge don't count:
    vbsp's face outline only has a corner where two neighbouring faces meet)."""
    import numpy as np
    n = np.asarray(normal, dtype=np.float64)
    u = np.cross(n, (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0))
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    pts = sorted({(round(float(p @ u), 3), round(float(p @ v), 3)) for p in points})
    if len(pts) < 3:
        return len(pts)

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    hull = []
    for seq in (pts, pts[::-1]):               # monotone chain; collinear points dropped (turn <= eps)
        part = []
        for q in seq:
            while len(part) >= 2 and turn(part[-2], part[-1], q) <= 1e-6:
                part.pop()
            part.append(q)
        hull += part[:-1]
    return len(hull)


def _mesh_hole(faces: list[Polygon]) -> bool:
    """True if the faces don't close up: some edge isn't shared by exactly one other face running
    the other way (a hole, or a face pointing the wrong way). Exact, and fast at any size."""
    from collections import Counter
    edges = Counter()
    for f in faces:
        vs = [tuple(round(c, 4) for c in v) for v in f.verts]
        for a, b in zip(vs, vs[1:] + vs[:1]):
            if a != b:
                edges[(a, b)] += 1
    return any(c != 1 or edges.get((b, a), 0) != 1 for (a, b), c in edges.items())


def check_brush(brush: Brush, tolerance: float = 0.1) -> list[BrushProblem]:
    """Return problems that would make this an invalid Source brush."""
    import numpy as np
    problems: list[BrushProblem] = []
    verts = [v for f in brush.faces for v in f.verts]
    groups = coplanar_groups(brush.faces)
    faces = [g[1] for g in groups]
    if len(faces) < 4:
        return [BrushProblem(brush.source, "has fewer than 4 distinct faces (not a closed solid)")]

    for f in brush.faces:
        pl = Plane.from_polygon(f.verts)
        worst = max(abs(pl.distance(v)) for v in f.verts)
        if worst > tolerance:
            problems.append(BrushProblem(brush.source, f"has a non-planar face (off by {worst:.2f} units)"))
            break

    planes = [Plane.from_polygon(f.verts) for f in faces]
    if len(planes) <= EXACT_OPEN_PLANES:
        gap = open_direction([pl.normal for pl in planes])
        side = f"its {_direction_name(gap)} side has no face" if gap is not None else ""
    else:                                       # a dense mesh: the same question, answered from its edges
        side = "it has a gap" if _mesh_hole(brush.faces) else ""
    if side:
        problems.append(BrushProblem(
            brush.source,
            f"is open: {side} (a hole in the mesh), or that face points "
            "inward. In Edit Mode: fill a hole by selecting its edges and pressing F; fix a flipped face with "
            "select all, Mesh > Normals > Recalculate Outside",
        ))
        return problems

    V = np.unique(np.asarray(verts, dtype=np.float64), axis=0)
    N = np.asarray([pl.normal for pl in planes], dtype=np.float64)
    D = np.asarray([pl.dist for pl in planes], dtype=np.float64)
    lowest, outside = np.inf, None
    for c in range(0, len(N), 256):             # vertices against planes, a block of planes at a time
        d = V @ N[c:c + 256].T - D[c:c + 256]
        lowest = min(lowest, float(d.min()))       # (every plane: "inward" needs all of them)
        if outside is None and (d > tolerance).any():
            outside = c
    if lowest >= -tolerance:
        problems.append(BrushProblem(
            brush.source,
            "has faces pointing inward. In Edit Mode: select all, Mesh > Normals > Recalculate Outside",
        ))
        return problems
    if outside is not None:
        problems.append(BrushProblem(
            brush.source,
            "is not convex. Split it into convex pieces, or enable 'Use Convex Hull' on it",
        ))
    else:
        # a side's outline is the faces merged into it (vbsp's winding: one corner per neighbouring side)
        most = max((_corners(np.asarray([v for f in g[3] for v in f.verts], dtype=np.float64), pl.normal)
                    for g, pl in zip(groups, planes) if sum(len(f.verts) for f in g[3]) > MAX_FACE_CORNERS),
                   default=0)
        if most > MAX_FACE_CORNERS:
            problems.append(BrushProblem(
                brush.source,
                f"has a face with {most} corners: the map compiler crashes above {MAX_FACE_CORNERS} (for example "
                "the cap of a cylinder with many segments). Use fewer segments, or cut that face into pieces",
            ))

    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    if min(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) < 1.0:
        problems.append(BrushProblem(brush.source, "is thinner than 1 unit"))
    if max(map(abs, xs + ys + zs)) > 16384:
        problems.append(BrushProblem(brush.source, "is outside the map limit (±16384 units)"))
    return problems


WELD_TOLERANCE = 0.5     # Hammer units (under 1 cm at the default scale)


def weld_near_misses(brushes: list[Brush], tolerance: float = WELD_TOLERANCE) -> int:
    """Make brush corner coordinates that nearly match exactly equal, per axis.

    Brushes from Blender meshes often miss each other by a fraction of a unit (scale
    rounding, or a top a quarter unit lower than its neighbour). vbsp then cuts faces
    folded to zero area, and vrad's full-quality bounces run away and bake black
    lightmaps. Values closer than `tolerance` (a cluster spans at most twice that) move
    to the most used value of their cluster. A brush the move would bend out of shape
    (non-planar or non-convex) keeps its original corners. Returns how many values moved."""
    if tolerance <= 0 or not brushes:
        return 0
    from collections import Counter
    counts = [Counter() for _ in range(3)]
    for b in brushes:
        for f in b.faces:
            for v in f.verts:
                for i in range(3):
                    counts[i][v[i]] += 1
    maps = []
    moved = 0
    for i in range(3):
        values = sorted(counts[i])
        mapping, cluster = {}, [values[0]]

        def flush(cl):
            rep = max(cl, key=lambda v: (counts[i][v], -abs(v - round(v))))
            for v in cl:
                mapping[v] = rep
        for v in values[1:]:
            if v - cluster[-1] < tolerance and v - cluster[0] < 2 * tolerance:
                cluster.append(v)
            else:
                flush(cluster)
                cluster = [v]
        flush(cluster)
        moved += sum(1 for k, v in mapping.items() if k != v)
        maps.append(mapping)
    for b in brushes:
        before = [list(f.verts) for f in b.faces]
        for f in b.faces:
            f.verts = [(maps[0][v[0]], maps[1][v[1]], maps[2][v[2]]) for v in f.verts]
        if any(p.message for p in check_brush(b, tolerance=0.01)) and not check_brush_problems_before(before, b):
            for f, verts in zip(b.faces, before):
                f.verts = verts
    return moved


_PART_SUFFIX = __import__("re").compile(r" \(part \d+\)$")
NEAR_MISS_REPORT = 0.02     # smaller gaps are float rounding that disappears when the VMF is written


def near_misses(brushes: list[Brush], tolerance: float = WELD_TOLERANCE,
                smallest: float = NEAR_MISS_REPORT) -> list[tuple[str, str, str, float, Vec3]]:
    """Corners of neighbouring brushes that almost, but don't quite, line up on an axis:
    (object A, object B, axis, gap, where). One entry (the closest) per pair of objects and axis."""
    boxes = []
    for b in brushes:
        pts = [v for f in b.faces for v in f.verts]
        boxes.append((tuple(min(p[i] for p in pts) for i in range(3)), tuple(max(p[i] for p in pts) for i in range(3)))
                     if pts else None)

    def neighbours(a, b):
        (la, ha), (lb, hb) = boxes[a], boxes[b]
        return all(la[i] <= hb[i] + tolerance and lb[i] <= ha[i] + tolerance for i in range(3))
    found: dict = {}
    for axis in range(3):
        entries = sorted({(v[axis], bi, v) for bi, b in enumerate(brushes) if boxes[bi] for f in b.faces for v in f.verts})
        start = stop = 0
        for j, (val, bj, vj) in enumerate(entries):
            while entries[start][0] < val - tolerance:
                start += 1
            while stop < j and entries[stop][0] <= val - smallest:
                stop += 1           # entries[stop:j] are too close (equal) to count: never look at them
            for k in range(start, stop):
                vk_val, bk, vk = entries[k]
                gap = val - vk_val
                if gap < smallest or bk == bj or not neighbours(bk, bj):
                    continue
                a, b = sorted((_PART_SUFFIX.sub("", brushes[bk].source), _PART_SUFFIX.sub("", brushes[bj].source)))
                key = (a, b, axis)
                if key not in found or gap < found[key][3]:
                    where = tuple((vj[i] + vk[i]) / 2 for i in range(3))
                    found[key] = (a, b, "XYZ"[axis], gap, where)
    return sorted(found.values(), key=lambda r: r[3])


def check_brush_problems_before(before: list[list[Vec3]], brush: Brush) -> bool:
    """Did the brush already have problems before welding? (Then welding isn't to blame.)"""
    orig = Brush([Polygon(v, f.material) for v, f in zip(before, brush.faces)], brush.source)
    return bool(check_brush(orig, tolerance=0.01))


def vertical_span(brush: Brush, x: float, y: float) -> tuple[float, float] | None:
    """Where the vertical line through (x, y) is inside a convex brush: (bottom z, top z)."""
    lo, hi = -1e9, 1e9
    for f in merge_coplanar(brush.faces):
        pl = Plane.from_polygon(f.verts)
        nx, ny, nz = pl.normal
        rest = pl.dist - nx * x - ny * y        # inside: nz * z <= rest
        if abs(nz) < 1e-6:
            if rest < -EPS:
                return None                      # the line misses this side entirely
            continue
        bound = rest / nz
        if nz > 0:
            hi = min(hi, bound)
        else:
            lo = max(lo, bound)
        if lo > hi:
            return None
    return (lo, hi) if hi > -1e8 else None


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
