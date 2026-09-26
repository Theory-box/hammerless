"""Brush collision the way the Source engine does it, for the nav generator.

Engine hull traces sweep an axis-aligned box against every brush's planes (the
classic Quake algorithm, which Source kept): each plane is pushed out by the box's
extents and the entry/exit fractions are clipped with DIST_EPSILON. vbsp adds
"bevel" planes to every brush (its axial bounding planes, plus edge bevels on
slanted edges) so boxes don't snag on corners; we add the same ones.

Planes are built from the VMF's three plane points, snapped like vbsp's
FindFloatPlane, so the numbers match what the compiled map collides with.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

DIST_EPSILON = 0.03125          # coordsize.h
NORMAL_EPSILON = 0.00001        # vbsp plane snapping
PLANE_DIST_EPSILON = 0.01
NEVER_UPDATED = -9999.0

# brush contents that nav generation traces hit (MASK_NPCSOLID_BRUSHONLY): not player clip, not triggers
NONSOLID = {"tools/toolstrigger", "tools/toolshint", "tools/toolsskip", "tools/toolsareaportal",
            "tools/toolsfog", "tools/toolsoccluder", "tools/toolsplayerclip", "tools/toolsinvisibleladder",
            "tools/toolsclip"}
SKY = {"tools/toolsskybox", "tools/toolsskybox2d"}
SOLID_BRUSH_ENTITIES = {"func_detail", "func_wall", "func_illusionary_never"}


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(v):
    length = math.sqrt(_dot(v, v))
    return ((v[0] / length, v[1] / length, v[2] / length), length) if length else ((0.0, 0.0, 0.0), 0.0)


def snap_plane(normal, dist):
    """vbsp SnapPlane: nearly-axial normals become axial, near-integer distances integer."""
    n = list(normal)
    for i in range(3):
        if abs(n[i] - 1) < NORMAL_EPSILON:
            n = [0.0, 0.0, 0.0]
            n[i] = 1.0
            break
        if abs(n[i] + 1) < NORMAL_EPSILON:
            n = [0.0, 0.0, 0.0]
            n[i] = -1.0
            break
    if abs(dist - round(dist)) < PLANE_DIST_EPSILON:
        dist = float(round(dist))
    return tuple(n), dist


@dataclass
class Side:
    normal: tuple[float, float, float]
    dist: float
    material: str = ""
    bevel: bool = False


@dataclass
class CollisionBrush:
    sides: list[Side]
    mins: tuple[float, float, float]
    maxs: tuple[float, float, float]
    source: str = ""


def _windings(planes):
    """Vertices of a convex brush per plane (intersections of plane triples inside the brush)."""
    pts_on = [[] for _ in planes]
    for i in range(len(planes)):
        for j in range(i + 1, len(planes)):
            for k in range(j + 1, len(planes)):
                (n1, d1), (n2, d2), (n3, d3) = planes[i], planes[j], planes[k]
                det = _dot(n1, _cross(n2, n3))
                if abs(det) < 1e-9:
                    continue
                c23, c31, c12 = _cross(n2, n3), _cross(n3, n1), _cross(n1, n2)
                p = tuple((d1 * c23[a] + d2 * c31[a] + d3 * c12[a]) / det for a in range(3))
                if all(_dot(n, p) - d <= 0.01 for n, d in planes):
                    for m, (n, d) in enumerate(planes):
                        if abs(_dot(n, p) - d) <= 0.01 and not any(
                                abs(q[0] - p[0]) < 1e-3 and abs(q[1] - p[1]) < 1e-3 and abs(q[2] - p[2]) < 1e-3
                                for q in pts_on[m]):
                            pts_on[m].append(p)
    out = []
    for (n, _d), pts in zip(planes, pts_on):
        if len(pts) < 3:
            out.append([])
            continue
        c = tuple(sum(p[a] for p in pts) / len(pts) for a in range(3))
        u, _ = _norm(_sub(pts[0], c))
        v = _cross(n, u)
        pts.sort(key=lambda p: math.atan2(_dot(_sub(p, c), v), _dot(_sub(p, c), u)))
        out.append(pts)
    return out


def make_brush(sides: list[Side], source: str = "") -> CollisionBrush | None:
    """Add vbsp's bevel planes (AddBrushBevels) to a brush's sides."""
    planes = [(s.normal, s.dist) for s in sides]
    wind = _windings(planes)
    verts = [p for w in wind for p in w]
    if len(verts) < 4:
        return None
    mins = tuple(min(p[a] for p in verts) for a in range(3))
    maxs = tuple(max(p[a] for p in verts) for a in range(3))
    sides = list(sides)
    wind = list(wind)
    # axial planes, in canonical order (-x, +x, -y, +y, -z, +z)
    order = 0
    for axis in range(3):
        for sign in (-1, 1):
            i = next((i for i, s in enumerate(sides) if s.normal[axis] == sign), None)
            if i is None:
                n = [0.0, 0.0, 0.0]
                n[axis] = float(sign)
                d = maxs[axis] if sign == 1 else -mins[axis]
                sides.append(Side(tuple(n), d, sides[0].material, True))
                wind.append([])
                i = len(sides) - 1
            if i != order:
                sides[order], sides[i] = sides[i], sides[order]
                wind[order], wind[i] = wind[i], wind[order]
            order += 1
    # edge bevels for slanted edges
    if len(sides) > 6:
        base = len(sides)
        for si in range(6, base):
            w = wind[si]
            for j in range(len(w)):
                vec, length = _norm(_sub(w[j], w[(j + 1) % len(w)]))
                if length < 0.5:
                    continue
                vec = tuple(float(round(c)) if abs(c - round(c)) < NORMAL_EPSILON else c for c in vec)
                if any(c in (-1.0, 1.0) for c in vec):
                    continue        # axial edge
                for axis in range(3):
                    for sign in (-1, 1):
                        v2 = [0.0, 0.0, 0.0]
                        v2[axis] = float(sign)
                        normal, nl = _norm(_cross(vec, tuple(v2)))
                        if nl < 0.5:
                            continue
                        dist = _dot(w[j], normal)
                        ok = True
                        for k, s in enumerate(sides):
                            if (abs(s.normal[0] - normal[0]) < 0.01 and abs(s.normal[1] - normal[1]) < 0.01
                                    and abs(s.normal[2] - normal[2]) < 0.01 and abs(s.dist - dist) < 0.01):
                                ok = False
                                break
                            if any(_dot(p, normal) - dist > 0.1 for p in wind[k]):
                                ok = False
                                break
                        if ok:
                            n2, d2 = snap_plane(normal, dist)
                            sides.append(Side(n2, d2, sides[0].material, True))
                            wind.append([])
    return CollisionBrush(sides, mins, maxs, source)


_POINTS = re.compile(r"\(([^)]*)\)\s*\(([^)]*)\)\s*\(([^)]*)\)")


def brush_from_vmf_sides(vmf_sides, source="") -> CollisionBrush | None:
    """vmf_sides: [(plane string, material)]. Plane from 3 points like vbsp's PlaneFromPoints."""
    sides = []
    for plane, material in vmf_sides:
        m = _POINTS.search(plane)
        if not m:
            continue
        p0, p1, p2 = (tuple(float(c) for c in g.split()) for g in m.groups())
        normal, length = _norm(_cross(_sub(p0, p1), _sub(p2, p1)))
        if length == 0:
            continue
        n, d = snap_plane(normal, _dot(p0, normal))
        sides.append(Side(n, d, material.lower()))
    return make_brush(sides, source) if len(sides) >= 4 else None


@dataclass
class Trace:
    fraction: float = 1.0
    endpos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: tuple[float, float, float] = (0.0, 0.0, 0.0)
    startsolid: bool = False
    allsolid: bool = False
    material: str = ""
    fraction_left_solid: float = 0.0

    @property
    def sky(self) -> bool:
        return self.material in SKY


class CollisionWorld:
    CELL = 256.0

    def __init__(self, brushes: list[CollisionBrush]):
        self.brushes = brushes
        self.grid: dict[tuple[int, int], list[int]] = {}
        for i, b in enumerate(brushes):
            for cx in range(int(math.floor(b.mins[0] / self.CELL)), int(math.floor(b.maxs[0] / self.CELL)) + 1):
                for cy in range(int(math.floor(b.mins[1] / self.CELL)), int(math.floor(b.maxs[1] / self.CELL)) + 1):
                    self.grid.setdefault((cx, cy), []).append(i)
        self.traces = 0

    @classmethod
    def from_vmf(cls, text: str) -> "CollisionWorld":
        """World and solid brush-entity brushes from a VMF (what nav generation collides with)."""
        from .vmf import parse
        top = parse(text)
        brushes = []

        def add_solids(block, owner):
            for solid in block.blocks("solid"):
                sides = [(s.get("plane"), s.get("material", "")) for s in solid.blocks("side")]
                if all(m.lower() in NONSOLID for _p, m in sides):
                    continue
                b = brush_from_vmf_sides(sides, owner)
                if b:
                    brushes.append(b)

        for world in (b for b in top if b.name == "world"):
            add_solids(world, "world")
        for ent in (b for b in top if b.name == "entity"):
            if ent.get("classname") in SOLID_BRUSH_ENTITIES:
                add_solids(ent, ent.get("classname"))
        return cls(brushes)

    def _candidates(self, lo, hi):
        seen = set()
        for cx in range(int(math.floor(lo[0] / self.CELL)), int(math.floor(hi[0] / self.CELL)) + 1):
            for cy in range(int(math.floor(lo[1] / self.CELL)), int(math.floor(hi[1] / self.CELL)) + 1):
                for i in self.grid.get((cx, cy), ()):
                    if i not in seen:
                        seen.add(i)
                        b = self.brushes[i]
                        if (b.mins[0] <= hi[0] and b.maxs[0] >= lo[0] and b.mins[1] <= hi[1] and b.maxs[1] >= lo[1]
                                and b.mins[2] <= hi[2] and b.maxs[2] >= lo[2]):
                            yield b

    def trace_hull(self, start, end, mins, maxs) -> Trace:
        """UTIL_TraceHull against the brushes: sweep box [mins, maxs] from start to end."""
        self.traces += 1
        ext = ((maxs[0] - mins[0]) * 0.5, (maxs[1] - mins[1]) * 0.5, (maxs[2] - mins[2]) * 0.5)
        off = ((maxs[0] + mins[0]) * 0.5, (maxs[1] + mins[1]) * 0.5, (maxs[2] + mins[2]) * 0.5)
        p1 = (start[0] + off[0], start[1] + off[1], start[2] + off[2])
        p2 = (end[0] + off[0], end[1] + off[1], end[2] + off[2])
        is_point = ext == (0.0, 0.0, 0.0)
        tr = Trace()
        lo = tuple(min(p1[a], p2[a]) - ext[a] - 1 for a in range(3))
        hi = tuple(max(p1[a], p2[a]) + ext[a] + 1 for a in range(3))
        for b in self._candidates(lo, hi):
            self._clip(b, p1, p2, ext, is_point, tr)
            if tr.allsolid:
                break
        if tr.fraction == 1.0:
            tr.endpos = tuple(end)
        else:
            tr.endpos = tuple(start[a] + tr.fraction * (end[a] - start[a]) for a in range(3))
        return tr

    @staticmethod
    def _clip(b: CollisionBrush, p1, p2, ext, is_point, tr: Trace):
        enter, leave = NEVER_UPDATED, 1.0
        getout = startout = False
        clip = None
        for s in b.sides:
            if s.bevel and is_point:
                continue
            n = s.normal
            dist = s.dist + (0.0 if is_point else abs(n[0]) * ext[0] + abs(n[1]) * ext[1] + abs(n[2]) * ext[2])
            d1 = p1[0] * n[0] + p1[1] * n[1] + p1[2] * n[2] - dist
            d2 = p2[0] * n[0] + p2[1] * n[1] + p2[2] * n[2] - dist
            if d2 > 0:
                getout = True
            if d1 > 0:
                startout = True
            if d1 > 0 and (d2 >= DIST_EPSILON or d2 >= d1):
                return                      # completely in front of this face: no hit
            if d1 <= 0 and d2 <= 0:
                continue
            if d1 > d2:                     # entering
                f = (d1 - DIST_EPSILON) / (d1 - d2)
                if f > enter:
                    enter = f
                    clip = s
            else:                           # leaving
                f = (d1 + DIST_EPSILON) / (d1 - d2)
                if f < leave:
                    leave = f
        if not startout:
            tr.startsolid = True
            if not getout:
                tr.allsolid = True
                tr.fraction = 0.0
                tr.fraction_left_solid = 1.0
            elif leave != 1.0 and leave > tr.fraction_left_solid:
                tr.fraction_left_solid = leave
                if tr.fraction <= leave:
                    tr.fraction = 1.0
            return
        if enter < leave and enter > NEVER_UPDATED and enter < tr.fraction:
            tr.fraction = max(0.0, enter)
            tr.normal = clip.normal
            tr.material = clip.material
