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
from dataclasses import dataclass

DIST_EPSILON = 0.03125          # coordsize.h
NORMAL_EPSILON = 0.00001        # vbsp plane snapping
PLANE_DIST_EPSILON = 0.01
NEVER_UPDATED = -9999.0

# brush contents that nav generation traces hit (MASK_NPCSOLID_BRUSHONLY): not player clip, not triggers
NONSOLID = {"tools/toolstrigger", "tools/toolshint", "tools/toolsskip", "tools/toolsareaportal",
            "tools/toolsfog", "tools/toolsoccluder", "tools/toolsplayerclip", "tools/toolsinvisibleladder",
            "tools/toolsclip"}
SKY = {"tools/toolsskybox", "tools/toolsskybox2d"}
# ladder brushes too: nav generation stands nodes on them (measured: the game's nav has a small
# area on top of each ladder brush, and the ladder's bottom stays inside it)
SOLID_BRUSH_ENTITIES = {"func_detail", "func_wall", "func_illusionary_never", "func_ladder", "func_simpleladder"}


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


_BRUSH_CACHE: dict = {}
_BRUSH_CACHE_MAX = 200000


def make_brush(sides: list[Side], source: str = "") -> CollisionBrush | None:
    """Add vbsp's bevel planes (AddBrushBevels) to a brush's sides. Remembered, since the same
    sides always give the same brush and most brushes don't change between nav runs."""
    key = tuple((s.normal, s.dist, s.material, s.bevel) for s in sides)
    hit = _BRUSH_CACHE.get(key, False)
    if hit is False:
        if len(_BRUSH_CACHE) >= _BRUSH_CACHE_MAX:
            _BRUSH_CACHE.clear()
        hit = _BRUSH_CACHE[key] = _make_brush(sides)
    if hit is None:
        return None
    return CollisionBrush(hit.sides, hit.mins, hit.maxs, source)


def _make_brush(sides: list[Side], source: str = "") -> CollisionBrush | None:
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


DISPLACEMENT = "__displacement__"      # material marker for displacement collision triangles


def displacement_vertices(plane: str, disp) -> list[list[tuple[float, float, float]]]:
    """Vertex grid of a displacement on an axis-aligned face. Row i runs along +Y from
    startposition and column j along +X (the layout core/displacement.py writes);
    each vertex moves along its normal by its distance, plus offset and elevation."""
    power = int(disp.get("power"))
    n = 2 ** power + 1
    pts = [tuple(float(c) for c in g.split()) for g in _POINTS.search(plane).groups()]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    sx, sy, sz = (float(c) for c in disp.get("startposition").strip("[]").split())
    x1 = max(xs) if abs(sx - min(xs)) < abs(sx - max(xs)) else min(xs)
    y1 = max(ys) if abs(sy - min(ys)) < abs(sy - max(ys)) else min(ys)
    elevation = float(disp.get("elevation", "0"))

    def rows(name, width):
        blocks = disp.blocks(name)
        if not blocks:
            return [[0.0] * (n * width) for _ in range(n)]
        return [[float(v) for v in blocks[0].get(f"row{i}", "").split()] or [0.0] * (n * width) for i in range(n)]

    normals, dists, offsets = rows("normals", 3), rows("distances", 1), rows("offsets", 3)
    grid = []
    for i in range(n):
        row = []
        for j in range(n):
            base = (sx + (x1 - sx) * j / (n - 1), sy + (y1 - sy) * i / (n - 1), sz)
            nx, ny, nz = normals[i][3 * j:3 * j + 3]
            d = dists[i][j]
            ox, oy, oz = offsets[i][3 * j:3 * j + 3]
            row.append((base[0] + nx * d + ox, base[1] + ny * d + oy, base[2] + nz * d + oz + elevation))
        grid.append(row)
    return grid


def displacement_brushes(plane: str, disp, thickness: float = 1.0) -> list[CollisionBrush]:
    """Each displacement triangle as a thin convex solid (face, back face, edge planes) with
    bevels, which is how swept boxes collide with displacement triangles."""
    grid = displacement_vertices(plane, disp)
    n = len(grid)
    flat = [p for row in grid for p in row]
    tris = []
    for i in range(n - 1):
        for j in range(n - 1):
            k = i * n + j                     # the engine's alternating diagonal
            if k % 2:
                tris += [(k, k + n, k + 1), (k + 1, k + n, k + n + 1)]
            else:
                tris += [(k, k + n, k + n + 1), (k, k + n + 1, k + 1)]
    out = []
    for a, b, c in tris:
        pa, pb, pc = flat[a], flat[b], flat[c]
        normal, length = _norm(_cross(_sub(pb, pa), _sub(pc, pa)))
        if length < 1e-6:
            continue
        if normal[2] < 0:
            normal = (-normal[0], -normal[1], -normal[2])
        dist = _dot(normal, pa)
        sides = [Side(normal, dist, DISPLACEMENT), Side((-normal[0], -normal[1], -normal[2]), -(dist - thickness),
                                                          DISPLACEMENT)]
        for p, q, r in ((pa, pb, pc), (pb, pc, pa), (pc, pa, pb)):
            en, el = _norm(_cross(_sub(q, p), normal))
            if el < 1e-9:
                continue
            if _dot(en, r) - _dot(en, p) > 0:
                en = (-en[0], -en[1], -en[2])
            sides.append(Side(en, _dot(en, p), DISPLACEMENT))
        brush = make_brush(sides, "displacement")
        if brush:
            out.append(brush)
    return out


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

    @property
    def displacement(self) -> bool:
        return self.material == DISPLACEMENT


class CollisionWorld:
    CELL = 64.0

    def __init__(self, brushes: list[CollisionBrush]):
        self.brushes = brushes
        # packed for speed: bounds, and per side (nx, ny, nz, dist, |nx|, |ny|, |nz|, bevel, normal, material)
        self._packed = [((b.mins[0], b.mins[1], b.mins[2], b.maxs[0], b.maxs[1], b.maxs[2]),
                         [(sd.normal[0], sd.normal[1], sd.normal[2], sd.dist, abs(sd.normal[0]), abs(sd.normal[1]),
                           abs(sd.normal[2]), sd.bevel, sd.normal, sd.material) for sd in b.sides])
                        for b in brushes]
        self.grid: dict[tuple[int, int], list[int]] = {}
        for i, b in enumerate(brushes):
            for cx in range(int(math.floor(b.mins[0] / self.CELL)), int(math.floor(b.maxs[0] / self.CELL)) + 1):
                for cy in range(int(math.floor(b.mins[1] / self.CELL)), int(math.floor(b.maxs[1] / self.CELL)) + 1):
                    self.grid.setdefault((cx, cy), []).append(i)
        self.traces = 0
        self.native_trace = None

    @classmethod
    def from_vmf(cls, text: str) -> "CollisionWorld":
        """World and solid brush-entity brushes from a VMF (what nav generation collides with).
        A brush with a displacement side isn't solid in the compiled map: its displacement
        surface is, as triangles."""
        from .vmf import parse
        top = parse(text)
        brushes = []

        def add_solids(block, owner):
            for solid in block.blocks("solid"):
                vmf_sides = solid.blocks("side")
                disps = [(s, s.blocks("dispinfo")[0]) for s in vmf_sides if s.blocks("dispinfo")]
                if disps:
                    for side, disp in disps:
                        brushes.extend(displacement_brushes(side.get("plane"), disp))
                    continue
                sides = [(s.get("plane"), s.get("material", "")) for s in vmf_sides]
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

    def _candidates(self, x0, y0, z0, x1, y1, z1):
        cell = self.CELL
        cx0, cx1 = math.floor(x0 / cell), math.floor(x1 / cell)
        cy0, cy1 = math.floor(y0 / cell), math.floor(y1 / cell)
        packed, grid = self._packed, self.grid
        if cx0 == cx1 and cy0 == cy1:
            ids = grid.get((cx0, cy0), ())
        else:
            seen = set()
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    seen.update(grid.get((cx, cy), ()))
            ids = sorted(seen)
        for i in ids:
            bx0, by0, bz0, bx1, by1, bz1 = packed[i][0]
            if bx0 <= x1 and bx1 >= x0 and by0 <= y1 and by1 >= y0 and bz0 <= z1 and bz1 >= z0:
                yield packed[i][1]

    def trace_hull(self, start, end, mins, maxs) -> Trace:
        """UTIL_TraceHull against the brushes: sweep box [mins, maxs] from start to end."""
        self.traces += 1
        if self.native_trace is not None:          # the DLL holds this world: same trace, faster
            return self.native_trace(start, end, mins, maxs)
        ex, ey, ez = (maxs[0] - mins[0]) * 0.5, (maxs[1] - mins[1]) * 0.5, (maxs[2] - mins[2]) * 0.5
        ox, oy, oz = (maxs[0] + mins[0]) * 0.5, (maxs[1] + mins[1]) * 0.5, (maxs[2] + mins[2]) * 0.5
        p1x, p1y, p1z = start[0] + ox, start[1] + oy, start[2] + oz
        p2x, p2y, p2z = end[0] + ox, end[1] + oy, end[2] + oz
        is_point = ex == 0.0 and ey == 0.0 and ez == 0.0
        tr = Trace()
        cands = self._candidates(min(p1x, p2x) - ex - 1, min(p1y, p2y) - ey - 1, min(p1z, p2z) - ez - 1,
                                 max(p1x, p2x) + ex + 1, max(p1y, p2y) + ey + 1, max(p1z, p2z) + ez + 1)
        for sides in cands:
            self._clip(sides, p1x, p1y, p1z, p2x, p2y, p2z, ex, ey, ez, is_point, tr)
            if tr.allsolid:
                break
        if tr.fraction == 1.0:
            tr.endpos = (end[0], end[1], end[2])
        else:
            f = tr.fraction
            tr.endpos = (start[0] + f * (end[0] - start[0]), start[1] + f * (end[1] - start[1]),
                         start[2] + f * (end[2] - start[2]))
        return tr

    @staticmethod
    def _clip(sides, p1x, p1y, p1z, p2x, p2y, p2z, ex, ey, ez, is_point, tr: Trace):
        enter, leave = NEVER_UPDATED, 1.0
        getout = startout = False
        clip = None
        eps = DIST_EPSILON
        for nx, ny, nz, dist, ax, ay, az, bevel, normal, material in sides:
            if is_point:
                if bevel:
                    continue
            else:
                dist = dist + ax * ex + ay * ey + az * ez
            d1 = p1x * nx + p1y * ny + p1z * nz - dist
            d2 = p2x * nx + p2y * ny + p2z * nz - dist
            if d2 > 0:
                getout = True
            if d1 > 0:
                startout = True
                if d2 >= eps or d2 >= d1:
                    return                  # completely in front of this face: no hit
            elif d2 <= 0:
                continue
            if d1 > d2:                     # entering
                f = (d1 - eps) / (d1 - d2)
                if f > enter:
                    enter = f
                    clip = (normal, material)
            else:                           # leaving
                f = (d1 + eps) / (d1 - d2)
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
            tr.normal, tr.material = clip
