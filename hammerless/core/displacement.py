"""Terrain heightfield -> displacement patches.

Each patch is a nodraw box brush whose top face carries a dispinfo. Neighbouring
patches sample the same grid points along shared edges, so seams line up
without any sewing step.

Vertex layout (matches vbsp's CCoreDispInfo): the top face winding starts at
`startposition` = the patch's (min x, min y) corner; row i advances along +Y and
column j along +X.
"""
from __future__ import annotations

import math

from . import geometry as g
from .ir import Brush, Terrain
from .vmf import Block, fmt, fmt_vec

TERRAIN_BASE_THICKNESS = 16.0


def verts_per_side(power: int) -> int:
    if power not in (2, 3, 4):
        raise ValueError("displacement power must be 2, 3 or 4")
    return (1 << power) + 1


def terrain_base_z(t: Terrain) -> float:
    hs = [h for row in t.heights for h in row if h is not None]
    if not hs:
        raise ValueError(f"terrain '{t.source}' has no samples")
    return math.floor(min(hs)) - 1.0


def build_patches(t: Terrain) -> list[tuple[Brush, Block]]:
    """Return (base brush, dispinfo for its top face = face 0) per patch."""
    n = verts_per_side(t.power)
    step = n - 1
    rows = len(t.heights)
    cols = len(t.heights[0]) if rows else 0
    if (rows - 1) % step or (cols - 1) % step:
        raise ValueError(
            f"terrain '{t.source}' grid is {rows}x{cols}; each side must be a multiple of {step} plus 1"
        )
    zb = terrain_base_z(t)
    patches = []
    for pr in range(0, rows - 1, step):
        for pc in range(0, cols - 1, step):
            window = [t.heights[pr + i][pc + j] for i in range(n) for j in range(n)]
            if all(h is None for h in window):
                continue
            x0 = t.origin[0] + pc * t.spacing
            y0 = t.origin[1] + pr * t.spacing
            x1 = x0 + step * t.spacing
            y1 = y0 + step * t.spacing
            brush = g.box_brush((x0, y0, zb - TERRAIN_BASE_THICKNESS), (x1, y1, zb),
                                "tools/toolsnodraw", t.source)
            brush.faces[0].material = t.material  # top face
            patches.append((brush, _dispinfo(t, pr, pc, n, (x0, y0, zb), zb)))
    return patches


def _dispinfo(t: Terrain, pr: int, pc: int, n: int, start, zb: float) -> Block:
    d = Block("dispinfo")
    d.kv("power", t.power)
    d.kv("startposition", f"[{fmt_vec(start)}]")
    d.kv("flags", 0).kv("elevation", 0).kv("subdiv", 0)
    normals, dists, offsets, offnormals, alphas = (Block(x) for x in
        ("normals", "distances", "offsets", "offset_normals", "alphas"))
    for i in range(n):
        nrow, drow, arow = [], [], []
        for j in range(n):
            h = t.heights[pr + i][pc + j]
            dist = 0.0 if h is None else h - zb
            nrow.append("0 0 1")
            drow.append(fmt(dist))
            a = t.alphas[pr + i][pc + j] if t.alphas else 0.0
            arow.append(fmt(max(0.0, min(255.0, a))))
        key = f"row{i}"
        normals.kv(key, " ".join(nrow))
        dists.kv(key, " ".join(drow))
        offsets.kv(key, " ".join(["0 0 0"] * n))
        offnormals.kv(key, " ".join(["0 0 1"] * n))
        alphas.kv(key, " ".join(arow))
    for b in (normals, dists, offsets, offnormals, alphas):
        d.add(b)
    allowed = Block("allowed_verts")
    allowed.kv("10", " ".join(["-1"] * 10))
    d.add(allowed)
    return d


def patch_vertex_positions(brush: Brush, disp: Block) -> list[list[tuple[float, float, float]]]:
    """Reconstruct final vertex positions from a patch (used by tests and previews)."""
    power = int(disp.get("power"))
    n = verts_per_side(power)
    sx, sy, sz = (float(c) for c in disp.get("startposition").strip("[]").split())
    top = brush.faces[0].verts
    x1 = max(v[0] for v in top)
    y1 = max(v[1] for v in top)
    dists = disp.blocks("distances")[0]
    out = []
    for i in range(n):
        drow = [float(x) for x in dists.get(f"row{i}").split()]
        row = []
        for j in range(n):
            x = sx + (x1 - sx) * j / (n - 1)
            y = sy + (y1 - sy) * i / (n - 1)
            row.append((x, y, sz + drow[j]))
        out.append(row)
    return out
