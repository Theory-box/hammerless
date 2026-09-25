"""Physical map checks, before compiling: can survivors walk from the start to the
end, and is anything stuck in a wall, sunk into the floor or floating?

Hammer only finds these after a compile (or in-game). We approximate the player
the way the nav mesh does: a 32-unit wide hull that steps up 18 units and needs
36 units of headroom (crouching). The walk is sampled on a 16-unit grid.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from . import geometry as g
from .entities import CATALOG
from .ir import Brush, MapIR, Vec3

CELL = 16.0          # grid spacing
HULL = 12.0          # hull samples at +-HULL around a cell centre (player is 32 wide)
STEP = 18.0          # tallest step a survivor walks up
HEAD = 36.0          # headroom needed (crouched)
MAX_DROP = 400.0     # survivors can drop this far (fall damage, but the nav allows it)
WALKABLE_NZ = 0.7    # floors steeper than ~45 degrees are slopes you slide off
SUNK = 24.0          # an entity this far below a floor's top counts as sunk, deeper as stuck

NONSOLID_MATERIALS = {"tools/toolstrigger", "tools/toolshint", "tools/toolsskip", "tools/toolsareaportal",
                      "tools/toolsfog", "tools/toolsoccluder", "tools/toolsinvisibleladder"}
SOLID_ENTITIES = {"func_detail", "func_button", "env_player_blocker", "func_brush"}
PLACED = {"Items", "Weapons", "Players", "Infected"}   # catalog categories that stand on floors
SPAWNS = {"info_player_start", "info_survivor_position", "info_survivor_rescue"}
_OFFSETS = ((0.0, 0.0), (-HULL, -HULL), (HULL, -HULL), (-HULL, HULL), (HULL, HULL))


@dataclass
class Problem:
    severity: str            # "ERROR" | "WARNING" | "INFO"
    message: str
    source: str = ""         # Blender object name to select
    location: Vec3 | None = None


class _Solid:
    def __init__(self, brush: Brush):
        faces = g.merge_coplanar(brush.faces)
        self.planes = [g.Plane.from_polygon(f.verts) for f in faces]
        pts = [v for f in brush.faces for v in f.verts]
        self.mins, self.maxs = g.bounds(pts)
        self.source = brush.source

    def spans(self, x: np.ndarray, y: np.ndarray):
        """Vertical extent (lo, hi) of the brush over points, and the top face's normal z.
        NaN where the vertical line misses the brush."""
        lo = np.full(x.shape, -np.inf)
        hi = np.full(x.shape, np.inf)
        top_nz = np.zeros(x.shape)
        miss = np.zeros(x.shape, bool)
        for p in self.planes:
            nx, ny, nz = p.normal
            rest = p.dist - nx * x - ny * y            # inside: nz * z <= rest
            if abs(nz) < 1e-6:
                miss |= rest < -g.EPS
                continue
            b = rest / nz
            if nz > 0:
                lower = b < hi
                hi = np.where(lower, b, hi)
                top_nz = np.where(lower, nz, top_nz)
            else:
                lo = np.maximum(lo, b)
        miss |= (lo > hi) | ~np.isfinite(hi) | ~np.isfinite(lo)
        return np.where(miss, np.nan, lo), np.where(miss, np.nan, hi), top_nz

    def contains(self, p: Vec3, margin: float = 0.5) -> bool:
        return all(pl.distance(p) < -margin for pl in self.planes)


def _is_solid(brush: Brush) -> bool:
    return any(f.material.lower() not in NONSOLID_MATERIALS for f in brush.faces)


def solids_of(ir: MapIR) -> list[_Solid]:
    out = [_Solid(b) for b in ir.brushes if _is_solid(b)]
    for e in ir.entities:
        if e.classname in SOLID_ENTITIES:
            out += [_Solid(b) for b in e.brushes]
    return out


def _terrain_height(t, x: float, y: float) -> float | None:
    rows, cols = len(t.heights), len(t.heights[0]) if t.heights else 0
    c = (x - t.origin[0]) / t.spacing
    r = (y - t.origin[1]) / t.spacing
    if rows < 2 or cols < 2 or not (0 <= r <= rows - 1 and 0 <= c <= cols - 1):
        return None
    r0, c0 = min(int(r), rows - 2), min(int(c), cols - 2)
    fr, fc = r - r0, c - c0
    k = [t.heights[r0][c0], t.heights[r0][c0 + 1], t.heights[r0 + 1][c0], t.heights[r0 + 1][c0 + 1]]
    if None in k:
        return None
    return (k[0] * (1 - fc) + k[1] * fc) * (1 - fr) + (k[2] * (1 - fc) + k[3] * fc) * fr


class WalkGrid:
    """Where a survivor can stand, per 16-unit cell (several levels per cell)."""

    def __init__(self, ir: MapIR):
        self.solids = solids_of(ir)
        self.terrains = ir.terrains
        pts = [s.mins for s in self.solids] + [s.maxs for s in self.solids]
        for t in ir.terrains:
            rows, cols = len(t.heights), len(t.heights[0]) if t.heights else 0
            pts += [(t.origin[0], t.origin[1], 0.0),
                    (t.origin[0] + (cols - 1) * t.spacing, t.origin[1] + (rows - 1) * t.spacing, 0.0)]
        if not pts:
            self.nx = self.ny = 0
            self.floors = {}
            return
        mins, maxs = g.bounds(pts)
        self.x0, self.y0 = mins[0], mins[1]
        self.nx = max(1, int(math.ceil((maxs[0] - mins[0]) / CELL)))
        self.ny = max(1, int(math.ceil((maxs[1] - mins[1]) / CELL)))
        self.floors: dict[tuple[int, int], list[float]] = {}
        self._build()

    def centre(self, i: int, j: int) -> tuple[float, float]:
        return self.x0 + (i + 0.5) * CELL, self.y0 + (j + 0.5) * CELL

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        return int((x - self.x0) // CELL), int((y - self.y0) // CELL)

    def _build(self):
        ii, jj = np.meshgrid(np.arange(self.nx), np.arange(self.ny), indexing="ij")
        cx = self.x0 + (ii + 0.5) * CELL
        cy = self.y0 + (jj + 0.5) * CELL
        # per cell: list of (lo, hi, walkable_top) spans at each hull sample
        spans: dict[tuple[int, int], list[list[tuple[float, float, bool]]]] = {}
        for s in self.solids:
            i0, j0 = self.cell_of(s.mins[0] - HULL, s.mins[1] - HULL)
            i1, j1 = self.cell_of(s.maxs[0] + HULL, s.maxs[1] + HULL)
            i0, j0, i1, j1 = max(i0, 0), max(j0, 0), min(i1, self.nx - 1), min(j1, self.ny - 1)
            if i0 > i1 or j0 > j1:
                continue
            sx, sy = cx[i0:i1 + 1, j0:j1 + 1], cy[i0:i1 + 1, j0:j1 + 1]
            results = [s.spans(sx + ox, sy + oy) for ox, oy in _OFFSETS]
            hit_any = np.zeros(sx.shape, bool)
            for lo, _hi, _nz in results:
                hit_any |= ~np.isnan(lo)
            for a, b in zip(*np.nonzero(hit_any)):
                cell = spans.setdefault((i0 + a, j0 + b), [[] for _ in _OFFSETS])
                for k, (lo, hi, nz) in enumerate(results):
                    if not np.isnan(lo[a, b]):
                        cell[k].append((float(lo[a, b]), float(hi[a, b]), float(nz[a, b]) >= WALKABLE_NZ))
        terrain_cells = set()
        if self.terrains:
            for i in range(self.nx):
                for j in range(self.ny):
                    x, y = self.centre(i, j)
                    if any(_terrain_height(t, x, y) is not None for t in self.terrains):
                        terrain_cells.add((i, j))
        for key in set(spans) | terrain_cells:
            samples = spans.get(key, [[] for _ in _OFFSETS])
            x, y = self.centre(*key)
            tops = [hi for k in range(len(_OFFSETS)) for lo, hi, ok in samples[k] if ok]
            for t in self.terrains:
                h = _terrain_height(t, x, y)
                if h is not None:
                    tops.append(h)
            levels = []
            for z in sorted(set(round(v, 1) for v in tops), reverse=True):
                if levels and levels[-1] - z < 8:
                    continue
                # headroom: nothing solid between knee height and head height at any hull sample
                if any(lo < z + HEAD and hi > z + STEP for k in range(len(_OFFSETS)) for lo, hi, _ in samples[k]):
                    continue
                levels.append(z)
            if levels:
                self.floors[key] = levels

    def floor_near(self, p: Vec3, below: float = 64.0, above: float = STEP) -> tuple[tuple[int, int], float] | None:
        """The standable level under/at point p (e.g. a spawn), if any."""
        key = self.cell_of(p[0], p[1])
        best = None
        for dk in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
            k = (key[0] + dk[0], key[1] + dk[1])
            for z in self.floors.get(k, ()):
                if p[2] - below <= z <= p[2] + above and (best is None or abs(z - p[2]) < abs(best[1] - p[2])):
                    best = (k, z)
            if best:
                return best
        return best

    def reachable(self, starts: list[tuple[tuple[int, int], float]]) -> dict[tuple[tuple[int, int], float], float]:
        """Breadth-first walk. Returns every reached (cell, level) and its path length."""
        seen = {s: 0.0 for s in starts}
        queue = deque(starts)
        while queue:
            cell, z = queue.popleft()
            d = seen[(cell, z)]
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (cell[0] + di, cell[1] + dj)
                for z2 in self.floors.get(n, ()):
                    if z - MAX_DROP <= z2 <= z + STEP and (n, z2) not in seen:
                        seen[(n, z2)] = d + CELL
                        queue.append((n, z2))
        return seen


def check_path(ir: MapIR, grid: WalkGrid | None = None) -> list[Problem]:
    starts_e = [e for e in ir.entities if e.classname == "info_survivor_position" and e.origin is not None] \
        or [e for e in ir.entities if e.classname == "info_player_start" and e.origin is not None]
    ends = [e for e in ir.entities if e.classname == "info_changelevel" and e.brushes]
    if not starts_e or not ends:
        return []
    grid = grid or WalkGrid(ir)
    problems, starts = [], []
    for e in starts_e:
        f = grid.floor_near(e.origin)
        if f is None:
            problems.append(Problem("ERROR", f"'{e.source or e.classname}' has no floor to stand on "
                                    "(stuck in a wall, too close to one, or floating)", e.source, e.origin))
        else:
            starts.append(f)
    if not starts:
        return problems
    seen = grid.reachable(starts)
    end = ends[0]
    lo, hi = g.bounds([v for b in end.brushes for f in b.faces for v in f.verts])
    targets = [(c, z) for c, zs in grid.floors.items() for z in zs
               if lo[0] <= grid.centre(*c)[0] <= hi[0] and lo[1] <= grid.centre(*c)[1] <= hi[1]
               and lo[2] - 16 <= z <= hi[2]]
    if not targets:
        problems.append(Problem("ERROR", "The end safe room has no floor survivors can stand on "
                                "(check its floor and that nothing fills the room)", end.source,
                                tuple((a + b) / 2 for a, b in zip(lo, hi))))
        return problems
    hit = [seen[t] for t in targets if t in seen]
    if hit:
        return problems
    goal = tuple((a + b) / 2 for a, b in zip(lo, hi))
    best = min(seen, key=lambda n: math.dist((*grid.centre(*n[0]), n[1]), goal))
    x, y = grid.centre(*best[0])
    problems.append(Problem(
        "ERROR", "Survivors can't walk from the start to the end safe room. The path stops at the "
        "marker: look there for a gap, a step taller than 18 units (0.3 m at default scale), "
        "a blocked doorway or a slope that's too steep. Zombies won't wander without this path",
        end.source, (x, y, best[1] + 8)))
    return problems


def check_placement(ir: MapIR, grid: WalkGrid | None = None) -> list[Problem]:
    solids = grid.solids if grid else solids_of(ir)
    problems = []
    for e in ir.entities:
        d = CATALOG.get(e.classname)
        if e.origin is None or d is None or d.category not in PLACED or d.brush:
            continue
        name = e.source or d.label
        x, y, z = e.origin
        inside = [s for s in solids if all(s.mins[i] - 1 <= e.origin[i] <= s.maxs[i] + 1 for i in range(3))
                  and s.contains((x, y, z + 1.0))]
        if inside:
            s = inside[0]
            _lo, hi, _nz = s.spans(np.array([x]), np.array([y]))
            top = float(hi[0])
            depth = top - z
            if not math.isnan(top) and depth <= SUNK:
                if e.classname in SPAWNS:
                    continue   # snapped onto the floor automatically at build time
                problems.append(Problem("WARNING", f"'{name}' is sunk {depth:.0f} units into '{s.source}'. "
                                        "Raise it so it sits on the surface", e.source, e.origin))
            else:
                problems.append(Problem("ERROR", f"'{name}' is inside '{s.source}' (stuck in solid). "
                                        "Move it out", e.source, e.origin))
            continue
        tops = []
        for s in solids:
            if s.mins[0] <= x <= s.maxs[0] and s.mins[1] <= y <= s.maxs[1] and s.mins[2] <= z:
                _lo, hi, _nz = s.spans(np.array([x]), np.array([y]))
                if not math.isnan(hi[0]) and hi[0] <= z + 1:
                    tops.append(float(hi[0]))
        tops += [h for t in ir.terrains if (h := _terrain_height(t, x, y)) is not None and h <= z + 1]
        if not tops:
            problems.append(Problem("WARNING", f"'{name}' has no floor under it (outside the map?)",
                                    e.source, e.origin))
            continue
        gap = z - max(tops)
        if gap > 24 and not (e.classname in SPAWNS and gap <= 48):
            problems.append(Problem("WARNING", f"'{name}' floats {gap:.0f} units above the floor",
                                    e.source, e.origin))
    return problems


def check_map(ir: MapIR) -> list[Problem]:
    grid = WalkGrid(ir)
    return check_placement(ir, grid) + check_path(ir, grid)


_cache: dict[str, list[Problem]] = {}


def _fingerprint(ir: MapIR) -> str:
    import hashlib
    h = hashlib.sha1()
    for b in ir.brushes:
        h.update(repr((b.source, [(f.material, f.verts) for f in b.faces])).encode())
    for e in ir.entities:
        h.update(repr((e.classname, e.origin, e.source, e.keyvalues.get("targetname"),
                       [[f.verts for f in b.faces] for b in e.brushes])).encode())
    for t in ir.terrains:
        h.update(repr((t.origin, t.spacing, t.heights)).encode())
    return h.hexdigest()


def cached_check(ir: MapIR) -> list[Problem]:
    """check_map, remembered for an unchanged map (the check takes a second or two)."""
    key = _fingerprint(ir)
    if key not in _cache:
        _cache.clear()
        _cache[key] = check_map(ir)
    return list(_cache[key])
