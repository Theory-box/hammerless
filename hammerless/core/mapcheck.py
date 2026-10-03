"""Quick placement checks, before compiling: is anything stuck in a wall, sunk into
the floor, floating or outside the map? Hammer only finds these after a compile (or
in-game). Whether survivors can walk from start to end is answered by navpredict.py,
which runs a copy of the game's own nav generator.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import geometry as g
from .entities import CATALOG
from .ir import Brush, MapIR, Vec3

SUNK = 24.0          # an entity this far below a floor's top counts as sunk, deeper as stuck

NONSOLID_MATERIALS = {"tools/toolstrigger", "tools/toolshint", "tools/toolsskip", "tools/toolsareaportal",
                      "tools/toolsfog", "tools/toolsoccluder", "tools/toolsinvisibleladder"}
SOLID_ENTITIES = {"func_detail", "func_button", "env_player_blocker", "func_brush"}
PLACED = {"Items", "Weapons", "Players", "Infected"}   # catalog categories that stand on floors
SPAWNS = {"info_player_start", "info_survivor_position", "info_survivor_rescue"}


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


def check_placement(ir: MapIR) -> list[Problem]:
    solids = solids_of(ir)
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
        if gap > 24 and _on_a_prop(ir, x, y, z):
            continue                   # standing on a table, crate... (props aren't in the floor check)
        if gap > 24 and not (e.classname in SPAWNS and gap <= 48):
            problems.append(Problem("WARNING", f"'{name}' floats {gap:.0f} units above the floor",
                                    e.source, e.origin))
    return problems


def _on_a_prop(ir: MapIR, x: float, y: float, z: float, reach: float = 64.0) -> bool:
    """Whether a prop (or door) stands close below this point: the item probably sits on it."""
    for p in ir.entities:
        if p.origin is None or not p.classname.startswith("prop_"):
            continue
        px, py, pz = p.origin
        if abs(px - x) <= reach and abs(py - y) <= reach and pz <= z + 1 and z - pz <= 96:
            return True
    return False


def check_map(ir: MapIR) -> list[Problem]:
    """Quick checks for Check / Build. The start-to-end path isn't checked here: the grid walk above
    is only an approximation (it missed ledges L4D2 doesn't link). Nav > Build Navmesh runs a copy
    of the game's own generator instead (navpredict.py)."""
    return check_placement(ir)


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
