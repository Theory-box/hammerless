"""L4D2 func_elevator landing geometry and runtime travel links.

Only the temporary collision world gets copies of the platform at its stops. The
exported map is unchanged. Travel edges are preview metadata, never ordinary NAV
connections: L4D2 associates landing areas with func_elevator at map load.
"""
from dataclasses import dataclass
import math

from .collision import CollisionBrush, Side, brush_from_vmf_sides
from .vmf import parse_shared

HAS_ELEVATOR = 0x40000000


def _vec(text):
    try:
        v = tuple(float(x) for x in text.split())
        return v if len(v) == 3 and all(math.isfinite(x) for x in v) else None
    except (ValueError, AttributeError):
        return None


@dataclass
class Elevator:
    name: str
    brushes: list
    mins: tuple
    maxs: tuple
    origin: tuple
    stops: list

    def landing_z(self, stop):
        return self.maxs[2] + stop[2] - self.origin[2]


def collect(text):
    entities = [e for e in parse_shared(text) if e.name == "entity"]
    floors = [_vec(e.get("origin")) for e in entities if e.get("classname") == "info_elevator_floor"]
    elevators, problems = [], []
    for e in entities:
        if e.get("classname") != "func_elevator":
            continue
        name = e.get("targetname") or e.get("id") or "unnamed"
        brushes = [brush_from_vmf_sides([(s.get("plane"), s.get("material", ""))
                                        for s in b.blocks("side")], "elevator:" + name)
                   for b in e.blocks("solid")]
        brushes = [b for b in brushes if b is not None]
        origin, top, bottom = (_vec(e.get(k)) for k in ("origin", "top", "bottom"))
        if not brushes or origin is None or top is None or bottom is None:
            problems.append(f"Elevator '{name}': needs brush geometry, origin, top and bottom for nav generation")
            continue
        mins = tuple(min(b.mins[i] for b in brushes) for i in range(3))
        maxs = tuple(max(b.maxs[i] for b in brushes) for i in range(3))
        # Floor markers in this shaft are additional destinations. They may lie beyond the top/bottom
        # keys (the stock hotel's stop1 is below its bottom key). Unrelated shafts stay separate.
        named = [p for p in floors if p is not None
                 and mins[0] <= p[0] <= maxs[0] and mins[1] <= p[1] <= maxs[1]]
        # The hotel has a bottom key 32 units above its actual named landing. The game maps both
        # destinations to the same boarding area. Baking two solid floors there creates a false ceiling.
        # Prefer the authored floor marker for such nearby aliases; preserve separated stops.
        stops = named + [p for p in (origin, top, bottom)
                         if not any(abs(p[2] - q[2]) <= 64 for q in named)]
        unique = []
        for p in stops:
            if not any(abs(p[2] - q[2]) <= 18 for q in unique):
                unique.append(p)
        elevators.append(Elevator(name, brushes, mins, maxs, origin, sorted(unique, key=lambda p: p[2])))
    return elevators, problems


def platforms(elevators):
    """Bake-only collision and seeds at every stop, including floors unreachable from the initial platform."""
    brushes, seeds = [], []
    for e in elevators:
        for stop in e.stops:
            dz = stop[2] - e.origin[2]
            for b in e.brushes:
                brushes.append(CollisionBrush([Side(s.normal, s.dist + s.normal[2] * dz, s.material, s.bevel)
                                                for s in b.sides],
                                               (b.mins[0], b.mins[1], b.mins[2] + dz),
                                               (b.maxs[0], b.maxs[1], b.maxs[2] + dz), b.source))
                seeds.append(((b.mins[0] + b.maxs[0]) / 2, (b.mins[1] + b.maxs[1]) / 2, b.maxs[2] + dz + 16))
    return brushes, seeds


def associate(mesh, elevators):
    """Preview potential elevator travel separately from serialized walk/drop edges."""
    mesh.elevator_links = []
    problems, edges = [], set()
    for e in elevators:
        landings = []
        for stop in e.stops:
            z = e.landing_z(stop)
            areas = [a for a in mesh.areas if e.mins[0] <= a.centre[0] <= e.maxs[0]
                     and e.mins[1] <= a.centre[1] <= e.maxs[1] and abs(a.centre[2] - z) <= 18]
            if not areas:
                problems.append(f"Elevator '{e.name}': no boarding nav near height {z:g}")
                continue
            target = min(areas, key=lambda a: (a.centre[0] - stop[0]) ** 2 + (a.centre[1] - stop[1]) ** 2)
            landings.append((areas, target))
            for a in areas:
                a.flags |= HAS_ELEVATOR
        for areas, _target in landings:
            for a in areas:
                for others, target in landings:
                    if a not in others and abs(a.centre[2] - target.centre[2]) > 18:
                        edges.add((a.id, target.id))
    mesh.elevator_links = sorted(edges)
    return problems


def from_bsp(data):
    """Recover runtime elevator extents when displaying a saved NAV alongside its BSP."""
    from .bspdecompile import Bsp
    from .geometry import box_brush
    from .vmf import Block, VMFWriter
    bsp, writer = Bsp(data), VMFWriter()
    models = bsp.models()
    blocks = []
    for pairs in bsp.entities():
        values = dict(pairs)
        if values.get("classname") not in ("func_elevator", "info_elevator_floor"):
            continue
        block = Block("entity", list(pairs))
        if values.get("classname") == "func_elevator":
            model = values.get("model", "")
            origin = _vec(values.get("origin"))
            if not model.startswith("*") or origin is None:
                continue
            index = int(model[1:])
            if not 0 <= index < len(models):
                continue
            lo, hi = models[index][:2]
            block.add(writer.solid(box_brush(tuple(lo[i] + origin[i] for i in range(3)),
                                            tuple(hi[i] + origin[i] for i in range(3)), "tools/toolsnodraw")))
        blocks.append(block)
    lines = []
    for b in blocks:
        b.write(lines)
    return collect("\n".join(lines))
