"""No Bake Volumes: where the light compiler doesn't bake (hlvrad -nobake).

The volumes go to hlvrad as text, one convex brush a line: "<invert> <planes> nx ny nz d ..." (inside where
n.p <= d for every plane). A point isn't baked when it's inside a volume that isn't inverted, or when there are
inverted volumes ("bake only inside") and it's inside none of them.
"""
from __future__ import annotations

from . import geometry as g
from .entities import NO_BAKE
from .ir import MapIR


def _inverted(e) -> bool:
    return str(e.keyvalues.get("invert", "0")).strip() in ("1", "true", "True")


def _lines(e, invert: int) -> list[str]:
    lines = []
    for b in e.brushes:
        planes = []
        for f in g.merge_coplanar(b.faces):
            pl = g.Plane.from_polygon(f.verts)
            planes.append("%.6f %.6f %.6f %.3f" % (pl.normal[0], pl.normal[1], pl.normal[2], pl.dist))
        if len(planes) >= 4:
            lines.append(f"{invert} {len(planes)} " + " ".join(planes))
    return lines


def no_bake_text(ir: MapIR) -> str:
    """The No Bake Volumes ("Don't bake inside") for hlvrad, or "" if the map has none. Every bake uses them."""
    lines = [ln for e in ir.entities if e.classname == NO_BAKE and not _inverted(e) for ln in _lines(e, 0)]
    return "\n".join(lines) + "\n" if lines else ""


def bake_only_volumes(ir: MapIR) -> dict[str, str]:
    """The "Bake only inside" volumes, by name: hlvrad text for each. Only a bake that picks one uses it (Lighting's
    Bake choice); builds bake the whole map."""
    out = {}
    for e in ir.entities:
        if e.classname == NO_BAKE and _inverted(e):
            lines = _lines(e, 1)
            if lines:
                out[e.source or "volume"] = "\n".join(lines) + "\n"
    return out


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


def view_volume(clip, eye, forward, distance: float, units_per_meter: float) -> str:
    """A "bake only inside" volume for what a viewport sees: its view pyramid (clip: the 4x4 projection x view
    matrix, Blender units) cut off `distance` Hammer units from the eye. One line for hlvrad -nobake."""
    rows = [list(map(float, clip[i])) for i in range(4)]
    planes = []
    for k, sign in ((0, 1), (0, -1), (1, 1), (1, -1), (2, 1)):        # left, right, bottom, top, near
        a, b, c, d = (rows[3][j] + sign * rows[k][j] for j in range(4))
        n = (a * a + b * b + c * c) ** 0.5
        if n < 1e-12:
            continue
        # inside: a.x + d >= 0  ->  -a.x <= d (Blender units)  ->  -(a/n).X <= (d/n) * upm (Hammer units)
        planes.append((-a / n, -b / n, -c / n, d / n * units_per_meter))
    f = [float(v) for v in forward]
    fl = sum(v * v for v in f) ** 0.5 or 1.0
    f = [v / fl for v in f]
    e = [float(v) * units_per_meter for v in eye]
    planes.append((f[0], f[1], f[2], sum(f[i] * e[i] for i in range(3)) + distance))      # far
    return f"1 {len(planes)} " + " ".join("%.6f %.6f %.6f %.3f" % p for p in planes) + "\n"
