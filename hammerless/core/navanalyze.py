"""The game's nav analysis, done by us: visibility lists (and, as they are ported, hiding spots
and the rest), so the game can load an already analyzed nav. Follows the SDK 2013 nav code with
the L4D2 differences measured against the game's own nav_analyze output; the heavy part runs in
the DLL (native/hlvis.c).

What blocks sight is what the engine's traces hit with MASK_NAV_VISION:
  - the compiled map's own brushes with solid / moveable / blocklos contents (read from the BSP,
    so the planes and contents are exactly vbsp's), and its displacements;
  - solid brush entities, each traced on its own (closest hit wins); SOLID_VPHYSICS ones (movers,
    doors, func_brush...) stick a trace that starts inside them;
  - doors, with their collision model at the position they spawn in (the end safe room door
    starts open): they block both sight lines and boxes in the analysis (measured against the
    game's lists; a VScript TraceLine with the same mask passes through them, its filter differs).
"""
from __future__ import annotations

import ctypes

from . import fastnav
from .bsppvs import BspPVS, bsp_brushes
from .collision import CollisionWorld, displacement_brushes
from .vision import (MASK_BLOCKLOS, PHYSICS_BRUSH_CLASSES, SOLID_BRUSH_CLASSES, MaterialContents,
                     blocks_sight)

NOT_VISIBLE, POTENTIALLY_VISIBLE, COMPLETELY_VISIBLE = 0, 1, 2
DOOR_CLASSES = {"prop_door_rotating", "prop_door_rotating_checkpoint"}


DOOR_MODE = "all"          # doors block sight lines and boxes (measured: "none" and "boxes" leave every
                           # doorway area different from the game; "all" none)


def _slots(vmf_text: str, bsp_path: str, materials: MaterialContents, content=None):
    """(world brushes, [(brushes, physics, rays)]) for the vision traces."""
    from .vmf import parse
    top = parse(vmf_text)
    world = bsp_brushes(bsp_path, model=0, mask=MASK_BLOCKLOS)
    for b in top:
        if b.name != "world":
            continue
        for solid in b.blocks("solid"):
            for side in solid.blocks("side"):
                for disp in side.blocks("dispinfo"):
                    world.extend(displacement_brushes(side.get("plane"), disp))
    ents = []
    from .collision import brush_from_vmf_sides
    for e in (b for b in top if b.name == "entity"):
        cls = (e.get("classname") or "").lower()
        if cls in SOLID_BRUSH_CLASSES:
            if cls == "func_brush" and (e.get("Solidity") or "0").strip() == "1":
                continue
            brushes = []
            for solid in e.blocks("solid"):
                if blocks_sight(solid, materials):
                    br = brush_from_vmf_sides([(s.get("plane"), s.get("material", "")) for s in solid.blocks("side")], cls)
                    if br:
                        brushes.append(br)
            if brushes:
                ents.append((brushes, cls in PHYSICS_BRUSH_CLASSES, True))
        elif cls in DOOR_CLASSES and content is not None and DOOR_MODE != "none":
            pieces = _prop_pieces(e, content)
            if pieces:
                ents.append((pieces, True, DOOR_MODE == "all"))   # custom ray test: lines pass
    return world, ents


def _prop_pieces(ent, content):
    """A prop's collision pieces in world space (convex brushes), from its model's .phy."""
    from .phy import convex_brush, place, read_phy
    model = (ent.get("model") or "").replace("\\", "/")
    if not model.endswith(".mdl"):
        return []
    data = content.read(model[:-4] + ".phy")
    if not data:
        return []
    origin = tuple(float(v) for v in (ent.get("origin") or "0 0 0").split())
    angles = tuple(float(v) for v in (ent.get("angles") or "0 0 0").split())
    if (ent.get("classname") or "").lower() in DOOR_CLASSES:
        # a door that spawns open stands rotated by its distance (measured: the end safe room door,
        # spawnpos 1 at yaw -90, is at yaw -180 in the game); 2 opens the other way
        spawnpos = int(float(ent.get("spawnpos") or 0))
        distance = float(ent.get("distance") or 90)
        if spawnpos == 1:
            angles = (angles[0], angles[1] - distance, angles[2])
        elif spawnpos == 2:
            angles = (angles[0], angles[1] + distance, angles[2])
    out = []
    for pts in read_phy(data):
        br = convex_brush(place(pts, origin, angles), ent.get("classname"))
        if br:
            out.append(br)
    return out


def _load_slot(slot: int, brushes, physics: bool, rays: bool) -> None:
    fastnav.load_world(CollisionWorld(brushes), memo=False)
    fastnav._lib.hl_world_stash(slot, int(physics), int(rays))


def _load_pvs(bsp_path: str) -> None:
    pv = BspPVS(bsp_path)
    planes = [v for (nx, ny, nz, d, _t, _s) in pv.planes for v in (nx, ny, nz, d)]
    types = [t for (_a, _b, _c, _d, t, _s) in pv.planes]
    nodes = [v for n in pv.nodes for v in n]
    rows = b"".join(pv.cluster_row(c) for c in range(pv.numclusters))
    F, I = ctypes.c_float, ctypes.c_int
    fastnav._lib.hl_pvs_load(I(len(pv.planes)), (F * max(1, len(planes)))(*planes), (I * max(1, len(types)))(*types),
                             I(len(pv.nodes)), (I * max(1, len(nodes)))(*nodes), I(len(pv.leaf_cluster)),
                             (I * max(1, len(pv.leaf_cluster)))(*pv.leaf_cluster), I(pv.headnode),
                             I(pv.numclusters), I(pv.rowbytes), ctypes.create_string_buffer(rows, max(1, len(rows))))


def visibility(areas, vmf_text: str, bsp_path: str, materials: MaterialContents, content=None,
               radius: float = 0.0) -> list[dict[int, int]]:
    """ComputeVisibilityToMesh for every area (in list order). areas: objects with nw, se,
    ne_z, sw_z. Returns, per area, {index of a visible area: attributes} (1 potentially,
    2 completely, 3 both)."""
    if not fastnav.available():
        raise RuntimeError("the nav analysis needs the native DLL")
    lib = fastnav._lib
    prev = fastnav.stash_current()                       # keep the nav generator's world
    ents = []
    try:
        world, ents = _slots(vmf_text, bsp_path, materials, content)
        _load_slot(0, world, False, True)
        for k, (brushes, physics, rays) in enumerate(ents, 1):
            _load_slot(k, brushes, physics, rays)
        _load_pvs(bsp_path)
        data = [v for a in areas for v in (a.nw[0], a.nw[1], a.nw[2], a.se[0], a.se[1], a.se[2], a.ne_z, a.sw_z)]
        lib.hl_vis_areas(ctypes.c_int(len(areas)), (ctypes.c_float * max(1, len(data)))(*data))
        lib.hl_vis_run.restype = ctypes.c_int
        total = lib.hl_vis_run(ctypes.c_float(radius))
        counts = (ctypes.c_int * max(1, len(areas)))()
        entries = (ctypes.c_int * max(1, total))()
        lib.hl_vis_get(counts, entries, ctypes.c_int(total))
        out, k = [], 0
        E = list(entries)
        for c in list(counts):
            out.append({e >> 2: e & 3 for e in E[k:k + c]})
            k += c
        return out
    finally:
        for k in range(0, len(ents) + 1):
            lib.hl_world_drop(k)
        fastnav.restore_current(prev)


# ---------------------------------------------------------------- hiding spots (ComputeHidingSpots)
MASK_NPCSOLID_BRUSHONLY = 0x1 | 0x2 | 0x8 | 0x4000 | 0x20000
NAV_MESH_JUMP, NAV_MESH_DONT_HIDE = 0x2, 0x200
HIDING_IN_COVER, HIDING_EXPOSED = 0x1, 0x8


def _f32(v: float) -> float:
    import struct
    return struct.unpack("<f", struct.pack("<f", v))[0]


def hiding_spots(mesh, vmf_text: str, bsp_path: str, materials: MaterialContents) -> list[list[tuple]]:
    """Per area, its hiding spots as (position, flags), like CNavArea::ComputeHidingSpots: a
    corner with walls on both sides (no two-way, non-jump neighbour within 20 units of it) gets a
    spot 12.5 units in, IN_COVER when something is overhead or at least half of 16 lines around
    it hit (MASK_NPCSOLID_BRUSHONLY), else EXPOSED. Matches the game's spots exactly (positions,
    order and flags, measured on a 2330-area map)."""
    import math
    from .vision import vision_world
    _w, phys, bents = vision_world(vmf_text, materials, split=True)
    world = CollisionWorld(bsp_brushes(bsp_path, model=0, mask=MASK_NPCSOLID_BRUSHONLY) + phys.brushes + bents.brushes)
    prev = fastnav.stash_current()
    try:
        fastnav.load_world(world, memo=False)
        tracer = fastnav._Tracer()

        def clear(a, b):
            return tracer(a, b, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)).fraction == 1.0

        def in_cover(spot):
            fr = (spot[0], spot[1], _f32(spot[2] + 35.5))
            if not clear(fr, (fr[0], fr[1], _f32(fr[2] + 20.0))):
                return True
            count, inc, ang = 0, _f32(math.pi / 8.0), 0.0
            while ang < _f32(2.0 * math.pi):
                to = (_f32(fr[0] + _f32(100.0 * _f32(math.cos(ang)))), _f32(fr[1] + _f32(100.0 * _f32(math.sin(ang)))),
                      _f32(fr[2] + 35.5))
                if not clear(fr, to):
                    count += 1
                ang = _f32(ang + inc)
            return count >= 8

        by = mesh.by_id()
        out = []
        for a in mesh.areas:
            spots = []
            out.append(spots)
            if a.flags & (NAV_MESH_JUMP | NAV_MESH_DONT_HIDE):
                continue
            cnt = [0, 0, 0, 0]
            for d in range(4):
                lo, hi = 999999.9, -999999.9
                for cid in a.connections[d]:
                    o = by.get(cid)
                    if o is None or a.id not in o.connections[(d + 2) % 4] or o.flags & NAV_MESH_JUMP:
                        continue
                    if d in (0, 2):
                        lo, hi = min(lo, o.nw[0]), max(hi, o.se[0])
                    else:
                        lo, hi = min(lo, o.nw[1]), max(hi, o.se[1])
                if d == 0:
                    cnt[0] += lo - a.nw[0] >= 20
                    cnt[1] += a.se[0] - hi >= 20
                elif d == 2:
                    cnt[3] += lo - a.nw[0] >= 20
                    cnt[2] += a.se[0] - hi >= 20
                elif d == 1:
                    cnt[1] += lo - a.nw[1] >= 20
                    cnt[2] += a.se[1] - hi >= 20
                else:
                    cnt[0] += lo - a.nw[1] >= 20
                    cnt[3] += a.se[1] - hi >= 20
            for c in range(4):
                if cnt[c] != 2:
                    continue
                p = _position_in_area(a, c)
                if not c or not any(math.dist(s[0], p) < 30.0 for s in spots):
                    spots.append((p, HIDING_IN_COVER if in_cover(p) else HIDING_EXPOSED))
        return out
    finally:
        fastnav.restore_current(prev)


def _position_in_area(a, c):
    """FindPositionInArea: 12.5 units in from the corner, pulled towards the middle if that's off."""
    mx, my = {0: (1, 1), 1: (-1, 1), 3: (1, -1), 2: (-1, -1)}[c]
    cp = [a.nw, (a.se[0], a.nw[1], a.ne_z), a.se, (a.nw[0], a.se[1], a.sw_z)][c]
    sx, sy = a.se[0] - a.nw[0], a.se[1] - a.nw[1]
    for ox, oy in ((12.5 * mx, 12.5 * my), (12.5 * mx, sy * 0.5 * my), (sx * 0.5 * mx, 12.5 * my),
                   (sx * 0.5 * mx, sy * 0.5 * my), (1.0 * mx, 1.0 * my)):
        p = (_f32(cp[0] + ox), _f32(cp[1] + oy), cp[2])
        if a.nw[0] <= p[0] <= a.se[0] and a.nw[1] <= p[1] <= a.se[1]:
            return p
    return tuple(cp)


# ---------------------------------------------------------------- storing visibility (PostCustomAnalysis)
MAX_VIS_DELTA = 64          # nav_max_vis_delta_list_length (asked the game)


def compress_visibility(mesh, lists: list[dict[int, int]]) -> None:
    """Store each area's list as the game does: an area nobody inherits from takes the
    neighbour (connections, by direction) whose list differs least, when the difference is at
    most 64 entries; the file then holds only the difference (NOT_VISIBLE entries cancel the
    inherited ones). Entries are area positions (0-based), 'inherit from' an area id."""
    by_index = {a.id: i for i, a in enumerate(mesh.areas)}
    full = [dict(l) for l in lists]
    inherit = [None] * len(mesh.areas)
    inherited_from = [False] * len(mesh.areas)
    stored = [list(l.items()) for l in full]

    def delta(i, j):
        mine, other = full[i], full[j]
        out = [(k, v) for k, v in mine.items() if other.get(k) != v]
        out += [(k, NOT_VISIBLE) for k in other if k not in mine]
        return out
    for i, a in enumerate(mesh.areas):
        if inherited_from[i]:
            continue
        best, anchor = None, None
        for d in range(4):
            for cid in a.connections[d]:
                j = by_index.get(cid)
                if j is None:
                    continue
                if inherit[j] is not None:
                    j = inherit[j]
                    if j == i:
                        continue
                dl = delta(i, j)
                if anchor is None or len(dl) < len(best):
                    best, anchor = dl, j
        if anchor is not None and len(best) <= MAX_VIS_DELTA and anchor != i:
            inherit[i] = anchor
            stored[i] = best
            inherited_from[anchor] = True
    for i, a in enumerate(mesh.areas):
        a.visible = [(k, v) for k, v in stored[i]]
        a.inherit_visibility = mesh.areas[inherit[i]].id if inherit[i] is not None else 0


# ---------------------------------------------------------------- everything
def analyze(mesh, vmf_text: str, bsp_path: str, content, game_dir: str | None = None, progress=None):
    """nav_analyze on our nav mesh: hiding spots, visibility, occupy times. Marks it analyzed so
    the game loads it as is. (Light intensity: pending; left at the game's default 1.0.)"""
    materials = MaterialContents(content, game_dir)
    if progress:
        progress("Finding hiding spots")
    spots = hiding_spots(mesh, vmf_text, bsp_path, materials)
    from .navfile import HidingSpot
    next_id = 0
    for a, lst in zip(mesh.areas, spots):
        a.hiding_spots = []
        for pos, flags in lst:
            a.hiding_spots.append(HidingSpot(next_id, pos, flags))
            next_id += 1
    if progress:
        progress("Computing visibility")
    lists = visibility(mesh.areas, vmf_text, bsp_path, materials, content)
    compress_visibility(mesh, lists)
    for a in mesh.areas:
        a.occupy = (120.0, 120.0)          # ComputeEarliestOccupyTimes: only Counter-Strike changes it
    mesh.analyzed = True
    return mesh
