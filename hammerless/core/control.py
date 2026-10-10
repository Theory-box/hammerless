"""Control Volumes: one volume that says where things are (or aren't) worked out.

Mode *Not inside* (EXCLUDE) or *Only inside* (ONLY), and a tick for each thing it controls:
  nav     the nav mesh isn't made (Not inside) / is made only there (Only inside)
  light   light isn't baked (the ambient colour, or the last bake) / Only inside: a choice in Lighting's Bake dropdown
  vis     brushes (Auto detail) don't cut up visibility: they become func_detail (Only inside: those outside do)
  sound   the sound trace doesn't listen there / listens only there
  spawns  no zombies spawn on the nav there (EMPTY + NO_MOBS marks; Not inside only)
The older No Nav Volume (nav, Not inside) and No Bake Volume (light; its invert key = Only inside) read as the same.

A point is skipped for a thing when it's inside a Not-inside volume ticked for it, or when there are Only-inside
volumes ticked for it and it's inside none of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import geometry as g
from .entities import CONTROL, NAV_CUT, NO_BAKE
from .ir import Entity, MapIR

ASPECTS = ("nav", "light", "vis", "sound", "spawns")
EPS = 1.0


def _on(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Volume:
    name: str
    only: bool
    aspects: frozenset
    mins: tuple
    maxs: tuple
    hulls: tuple = ()                 # per brush: planes (nx, ny, nz, d): inside where n.p <= d
    entity: Entity | None = field(default=None, repr=False)

    def contains(self, p, eps: float = EPS) -> bool:
        if not all(self.mins[i] - eps <= p[i] <= self.maxs[i] + eps for i in range(3)):
            return False
        return any(all(nx * p[0] + ny * p[1] + nz * p[2] <= d + eps for nx, ny, nz, d in h) for h in self.hulls)


def _hulls(e: Entity) -> tuple:
    hulls = []
    for b in e.brushes:
        planes = []
        for f in g.merge_coplanar(b.faces):
            pl = g.Plane.from_polygon(f.verts)
            planes.append((pl.normal[0], pl.normal[1], pl.normal[2], pl.dist))
        if len(planes) >= 4:
            hulls.append(tuple(planes))
    return tuple(hulls)


def settings_of(e: Entity) -> tuple[bool, frozenset] | None:
    """(only inside, what it controls) for a Control Volume or one of the older volumes; None for anything else."""
    kv = e.keyvalues
    if e.classname == CONTROL:
        only = str(kv.get("mode", "EXCLUDE")).upper() == "ONLY"
        aspects = frozenset(a for a in ASPECTS if _on(kv.get(a, "0")))
        if only:
            aspects -= {"spawns"}             # (spawns only inside: not a thing the nav marks can say)
        return only, aspects
    if e.classname == NAV_CUT:
        return False, frozenset({"nav"})
    if e.classname == NO_BAKE:
        return _on(kv.get("invert", "0")), frozenset({"light"})
    return None


def volumes(ir: MapIR) -> list[Volume]:
    out = []
    for e in ir.entities:
        st = settings_of(e)
        if st is None or not st[1]:
            continue
        pts = [v for b in e.brushes for f in b.faces for v in f.verts]
        hulls = _hulls(e)
        if not pts or not hulls:
            continue
        mins, maxs = g.bounds(pts)
        out.append(Volume(e.source or e.classname, st[0], st[1], mins, maxs, hulls, e))
    return out


def skipped(vols: list[Volume], aspect: str, p, eps: float = EPS) -> bool:
    """The point is left out for this aspect (see the module's rule)."""
    only = False
    for v in vols:
        if aspect not in v.aspects:
            continue
        if v.only:
            only = True
            continue
        if v.contains(p, eps):
            return True
    if not only:
        return False
    return not any(v.only and aspect in v.aspects and v.contains(p, eps) for v in vols)


def of(vols: list[Volume], aspect: str, only: bool | None = None) -> list[Volume]:
    return [v for v in vols if aspect in v.aspects and (only is None or v.only == only)]
