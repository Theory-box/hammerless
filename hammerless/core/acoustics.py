"""Automatic acoustics: soundscapes from ray tracing the map.

L4D2's sound engine already does reverb at runtime: soundscape dsp "1" (its automatic preset)
traces around the listener and blends room / hall / tunnel / open-space reverb to fit. Valve's own
maps use it nearly everywhere, but a map without soundscapes doesn't get it. What the engine can't
work out is the ambience: whether a spot is outdoors, sheltered or indoors, and, indoors, where the
outside sound comes in. That's precomputed here, by ray tracing the map ahead of time:

- listening spots: a grid dropped onto every floor (several storeys), at ear height
- each spot fires rays in all directions: how much sky it sees decides outdoors / sheltered / indoors
- neighbouring spots of a kind form zones; each zone gets a trigger_soundscape and a soundscape
- indoor zones get "sound portals": points at the openings to outdoor zones, where the outdoor
  ambience comes from (the soundscape system's positions, as Valve's sound designers place by hand)

The ray casting itself is passed in (Blender's BVH over the scene), so this module stays testable.
"""
import math
from dataclasses import dataclass, field

import numpy as np

from . import geometry as g
from .ir import Entity

EAR = 64.0            # listening height above the floor (units)
SPACING = 128.0       # grid spacing of the listening spots
MAX_SPOTS = 6000      # bigger maps use a coarser grid
RAYS = 96
FAR = 8192.0          # a ray that travels this far without a hit reached the sky
OUTDOOR_SKY = 0.45    # share of the upper-hemisphere rays that reach the sky
SHELTERED_SKY = 0.08
MAX_POSITIONS = 8     # soundscape positions per env_soundscape


@dataclass
class Spot:
    pos: tuple            # (x, y, z) Hammer units, at ear height
    cell: tuple           # (ix, iy, storey)
    floor_z: float
    sky: float = 0.0      # share of upward rays that escape
    mean: float = 0.0     # mean distance of the rays that hit (units)
    kind: str = ""        # OUTDOOR / SHELTERED / INDOOR


@dataclass
class Zone:
    kind: str
    spots: list
    positions: list = field(default_factory=list)   # sound portals (indoor zones)
    volumes: list = field(default_factory=list)     # each portal's loudness: wider openings let in more


def sphere_dirs(n: int) -> np.ndarray:
    """n evenly spread unit directions (Fibonacci sphere)."""
    i = np.arange(n) + 0.5
    z = 1 - 2 * i / n
    r = np.sqrt(1 - z * z)
    phi = i * math.pi * (3 - math.sqrt(5))
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=1)


def sample_spots(bounds, cast) -> tuple[list[Spot], float]:
    """Listening spots on every floor: a grid over the map's bounds, each column searched top down.
    cast(origin, direction, max_distance) -> (distance, normal_z) or None."""
    (x0, y0, z0), (x1, y1, z1) = bounds
    spacing = SPACING
    while ((x1 - x0) / spacing + 1) * ((y1 - y0) / spacing + 1) > MAX_SPOTS:
        spacing *= 1.5
    spots = []
    top = z1 + 16
    for ix, x in enumerate(np.arange(x0 + spacing / 2, x1, spacing)):
        for iy, y in enumerate(np.arange(y0 + spacing / 2, y1, spacing)):
            z = top
            storey = 0
            while z > z0 and storey < 8:
                hit = cast((x, y, z), (0.0, 0.0, -1.0), z - z0 + 16)
                if hit is None:
                    break
                d, nz = hit
                fz = z - d
                if nz > 0.7:                                   # a floor (walkable slope)
                    head = cast((x, y, fz + 1), (0.0, 0.0, 1.0), EAR + 8)
                    if head is None:                           # room to stand
                        spots.append(Spot((float(x), float(y), float(fz + EAR)), (ix, iy, storey), float(fz)))
                        storey += 1
                z = fz - 8                                     # continue below this surface
    return spots, spacing


STEP_UP = 72.0        # how far up a neighbouring spot can be and still count as reachable (stairs, ramps)


def reachable(spots: list[Spot], starts) -> list[Spot]:
    """The spots survivors can walk to from the start positions (Hammer units): grid neighbours, up
    at most STEP_UP, down any drop. Roofs and sealed-off spaces nobody can reach are left out."""
    if not starts or not spots:
        return spots
    by_cell = {}
    for i, s in enumerate(spots):
        by_cell.setdefault(s.cell[:2], []).append(i)
    keep = [False] * len(spots)
    stack = []
    for x, y, z in starts:                  # the spot under each start (nearest, its floor not above the start)
        best, bd = None, np.inf
        for i, s in enumerate(spots):
            if s.floor_z <= z + 16:
                d = (s.pos[0] - x) ** 2 + (s.pos[1] - y) ** 2 + ((s.floor_z - z) * 4) ** 2
                if d < bd:
                    best, bd = i, d
        if best is not None and not keep[best]:
            keep[best] = True
            stack.append(best)
    while stack:
        k = stack.pop()
        for cell in _neighbours(spots[k].cell):
            for j in by_cell.get(cell, ()):
                if not keep[j] and spots[j].floor_z - spots[k].floor_z <= STEP_UP:
                    keep[j] = True
                    stack.append(j)
    return [s for s, k in zip(spots, keep) if k]


def analyse(spots: list[Spot], cast, rays: int = RAYS) -> None:
    dirs = sphere_dirs(rays)
    up = dirs[:, 2] > 0.15
    for s in spots:
        dist = np.full(rays, np.inf)
        for k, d in enumerate(dirs):
            hit = cast(s.pos, tuple(d), FAR)
            if hit is not None:
                dist[k] = hit[0]
        hits = np.isfinite(dist)
        s.sky = float((~hits[up]).mean()) if up.any() else 0.0
        s.mean = float(dist[hits].mean()) if hits.any() else FAR
        s.kind = "OUTDOOR" if s.sky >= OUTDOOR_SKY else "SHELTERED" if s.sky >= SHELTERED_SKY else "INDOOR"


MIN_ZONE = 4          # smaller patches take their neighbours' kind (no soundscape flicker at a doorframe)


def zones(spots: list[Spot], spacing: float) -> list[Zone]:
    """Neighbouring spots (4-connected on the grid, floors within a step) of the same kind; tiny
    patches merge into the kind around them first."""
    out = _connected(spots)
    by_cell = {}
    for s in spots:
        by_cell.setdefault(s.cell[:2], []).append(s)
    changed = False
    for z in out:
        if len(z.spots) >= MIN_ZONE:
            continue
        around = [o.kind for s in z.spots for nx, ny in _neighbours(s.cell) for o in by_cell.get((nx, ny), ())
                  if o.kind != z.kind and abs(o.floor_z - s.floor_z) <= 72]
        if around:
            kind = max(set(around), key=around.count)
            for s in z.spots:
                s.kind = kind
            changed = True
    if changed:
        out = _connected(spots)
    _sound_portals(out, spots)
    return out


def _neighbours(cell):
    ix, iy = cell[:2]
    return ((ix + 1, iy), (ix - 1, iy), (ix, iy + 1), (ix, iy - 1))


def _connected(spots: list[Spot]) -> list[Zone]:
    by_cell = {}
    for i, s in enumerate(spots):
        by_cell.setdefault(s.cell[:2], []).append(i)
    seen = [False] * len(spots)
    out = []
    for i in range(len(spots)):
        if seen[i]:
            continue
        stack, members = [i], []
        seen[i] = True
        while stack:
            k = stack.pop()
            members.append(spots[k])
            ix, iy = spots[k].cell[:2]
            for nx, ny in ((ix + 1, iy), (ix - 1, iy), (ix, iy + 1), (ix, iy - 1)):
                for j in by_cell.get((nx, ny), ()):
                    if not seen[j] and spots[j].kind == spots[k].kind and abs(spots[j].floor_z - spots[k].floor_z) <= 72:
                        seen[j] = True
                        stack.append(j)
        out.append(Zone(spots[i].kind, members))
    return out


def _sound_portals(zone_list: list[Zone], spots: list[Spot]) -> None:
    """Indoor zones: points where they meet outdoor or sheltered spots (doorways, windows), spread
    out (farthest-point picks), at most MAX_POSITIONS."""
    kind_at = {}
    for s in spots:
        kind_at.setdefault(s.cell[:2], []).append(s)
    for z in zone_list:
        if z.kind != "INDOOR":
            continue
        openings = []
        for s in z.spots:
            ix, iy = s.cell[:2]
            for nx, ny in ((ix + 1, iy), (ix - 1, iy), (ix, iy + 1), (ix, iy - 1)):
                for o in kind_at.get((nx, ny), ()):
                    if o.kind != "INDOOR" and abs(o.floor_z - s.floor_z) <= 72:
                        openings.append(tuple((np.array(s.pos) + np.array(o.pos)) / 2))
        if not openings:
            continue
        pts = np.array(openings)
        picked = [0]
        dmin = np.linalg.norm(pts - pts[0], axis=1)
        while len(picked) < min(MAX_POSITIONS, len(pts)):
            k = int(np.argmax(dmin))
            if dmin[k] < 64:
                break
            picked.append(k)
            dmin = np.minimum(dmin, np.linalg.norm(pts - pts[k], axis=1))
        z.positions = [tuple(float(v) for v in pts[k]) for k in picked]
        # an opening's width: how many opening points (one per grid step along it) lie close by
        width = [int((np.linalg.norm(pts - pts[k], axis=1) < 2.5 * SPACING).sum()) for k in picked]
        z.volumes = [round(min(0.9, 0.3 + 0.12 * w), 2) for w in width]


# ---------------------------------------------------------------- the game's side

THEMES = {
    # outdoor ambience, the same heard from inside (comes in through the sound portals), room tone
    "URBAN": ("urban.util_genericrooftop", "urban.util_genericexterior_from_interior_1",
              "(ambient/Ambience/crucial_MedRoomtone_Amb_loop.wav"),
}


def soundscape_name(map_name: str, zone_list: list, n: int) -> str:
    z = zone_list[n]
    return f"hammerless.{map_name}.indoor{n}" if z.kind == "INDOOR" else f"hammerless.{map_name}.{z.kind.lower()}"


def soundscape_file(map_name: str, theme: str | None, zone_list: list | None = None) -> str:
    """scripts/soundscapes_<map>.txt: outdoors and sheltered shared, one per indoor zone (its sound
    portals and their loudness). dsp 1 = the engine's own automatic reverb everywhere."""
    zone_list = zone_list or []
    out = [f"// Hammerless: automatic soundscapes for {map_name} (dsp 1 = the engine's automatic reverb)", ""]
    outdoor = exterior = roomtone = None
    if theme in THEMES:
        outdoor, exterior, roomtone = THEMES[theme]
    entries = [(f"hammerless.{map_name}.outdoor", "OUTDOOR", []), (f"hammerless.{map_name}.sheltered", "SHELTERED", [])]
    entries += [(soundscape_name(map_name, zone_list, n), "INDOOR", z.volumes) for n, z in enumerate(zone_list)
                if z.kind == "INDOOR"]
    for name, kind, volumes in entries:
        out += [f'"{name}"', "{", '\t"dsp"\t"1"']
        if outdoor and kind in ("OUTDOOR", "SHELTERED"):
            vol = "1" if kind == "OUTDOOR" else ".55"
            out += ['\t"playsoundscape"', "\t{", f'\t\t"name"\t"{outdoor}"', f'\t\t"volume"\t"{vol}"', "\t}"]
        if roomtone and kind == "INDOOR":
            out += ['\t"playlooping"', "\t{", f'\t\t"wave"\t"{roomtone}"', '\t\t"volume"\t".5"', '\t\t"position"\t"random"', "\t}"]
            for p, vol in enumerate(volumes):      # the outside, from each opening
                out += ['\t"playsoundscape"', "\t{", f'\t\t"name"\t"{exterior}"', f'\t\t"volume"\t"{vol}"',
                        f'\t\t"positionoverride"\t"{p}"', "\t}"]
        out += ["}", ""]
    return "\n".join(out)


def entities(zone_list: list[Zone], map_name: str, spacing: float) -> list[Entity]:
    """Per zone: an env_soundscape_triggerable (its soundscape, and info_targets at its sound
    portals) and a trigger_soundscape made of one box per listening spot."""
    ents = []
    half = spacing / 2
    floors = {}                                        # storeys per column: a trigger stops below the next
    for z in zone_list:
        for s in z.spots:
            floors.setdefault(s.cell[:2], []).append(s.floor_z)
    for n, z in enumerate(zone_list):
        name = f"hl_sound_{n}"
        centre = tuple(float(v) for v in np.mean([s.pos for s in z.spots], axis=0))
        kv = {"targetname": name, "soundscape": soundscape_name(map_name, zone_list, n), "radius": "0"}
        for p, pos in enumerate(z.positions):
            target = f"{name}_pos{p}"
            kv[f"position{p}"] = target
            ents.append(Entity("info_target", pos, (0, 0, 0), {"targetname": target}))
        ents.append(Entity("env_soundscape_triggerable", centre, (0, 0, 0), kv))
        boxes = []
        for s in z.spots:
            above = [f for f in floors[s.cell[:2]] if f > s.floor_z + 1]
            top = min([s.floor_z + 160] + [f - 16 for f in above])
            boxes.append(g.box_brush((s.pos[0] - half, s.pos[1] - half, s.floor_z - 16),
                                     (s.pos[0] + half, s.pos[1] + half, top), "tools/toolstrigger"))
        ents.append(Entity("trigger_soundscape", None, (0, 0, 0), {"soundscape": name, "spawnflags": "1"}, boxes))
    return ents
