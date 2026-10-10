"""No Bake Volumes: where the light compiler doesn't bake (hlvrad -nobake).

The volumes go to hlvrad as text, one convex brush a line: "<invert> <planes> nx ny nz d ..." (inside where
n.p <= d for every plane). A point isn't baked when it's inside a volume that isn't inverted, or when there are
inverted volumes ("bake only inside") and it's inside none of them.
"""
from __future__ import annotations

from . import geometry as g
from .entities import NO_BAKE
from .ir import MapIR


def no_bake_text(ir: MapIR) -> str:
    """The No Bake Volumes for hlvrad, or "" if the map has none."""
    lines = []
    for e in ir.entities:
        if e.classname != NO_BAKE:
            continue
        invert = 1 if str(e.keyvalues.get("invert", "0")).strip() in ("1", "true", "True") else 0
        for b in e.brushes:
            planes = []
            for f in g.merge_coplanar(b.faces):
                pl = g.Plane.from_polygon(f.verts)
                planes.append("%.6f %.6f %.6f %.3f" % (pl.normal[0], pl.normal[1], pl.normal[2], pl.dist))
            if len(planes) >= 4:
                lines.append(f"{invert} {len(planes)} " + " ".join(planes))
    return "\n".join(lines) + "\n" if lines else ""


def in_volume(text: str, p) -> bool:
    """Whether a point is skipped (the same rule as hlvrad's): for tests."""
    inside, any_inv, in_inv = False, False, False
    for line in text.splitlines():
        nums = line.split()
        if not nums:
            continue
        inv, n = int(nums[0]), int(nums[1])
        vals = [float(x) for x in nums[2:2 + 4 * n]]
        hit = all(vals[4 * k] * p[0] + vals[4 * k + 1] * p[1] + vals[4 * k + 2] * p[2] <= vals[4 * k + 3]
                  for k in range(n))
        if inv:
            any_inv = True
            in_inv = in_inv or hit
        else:
            inside = inside or hit
    return inside or (any_inv and not in_inv)
