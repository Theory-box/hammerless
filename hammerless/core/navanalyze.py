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
    game's lists; a VScript TraceLine with the same mask passes through them, its filter differs);
  - solid props: static, dynamic and physics props block sight with their collision model
    (measured: a container, a trailer and a van each hid what's behind them in the game's lists).
"""
from __future__ import annotations

import ctypes

from . import fastnav
from .bsppvs import f32 as _f32
from .bsppvs import BspPVS, bsp_brushes
from .collision import CollisionWorld, displacement_brushes, solid_displacements
from .vision import (MASK_BLOCKLOS, PHYSICS_BRUSH_CLASSES, SOLID_BRUSH_CLASSES, MaterialContents,
                     blocks_sight)

NOT_VISIBLE, POTENTIALLY_VISIBLE, COMPLETELY_VISIBLE = 0, 1, 2
DOOR_CLASSES = {"prop_door_rotating", "prop_door_rotating_checkpoint"}
# props that are always VPhysics-solid (their 'solid' key isn't used) / whose 'solid' key picks it
PHYSICS_PROP_CLASSES = {"prop_physics", "prop_physics_override", "prop_physics_multiplayer", "prop_car_alarm"}
SOLID_KEY_PROP_CLASSES = {"prop_static", "prop_dynamic", "prop_dynamic_override"}
SOLID_BBOX, SOLID_VPHYSICS = 2, 6


DOOR_MODE = "all"          # doors block sight lines and boxes (measured: "none" and "boxes" leave every
                           # doorway area different from the game; "all" none)


def _parsed(vmf):
    """The VMF's top-level blocks (text or already parsed)."""
    if isinstance(vmf, str):
        from .vmf import parse
        return parse(vmf)
    return vmf


def _entity_solids(top, materials: MaterialContents, mask: int):
    """[(brushes, physics)] per solid brush entity: its brushes whose vbsp contents meet 'mask'."""
    from .collision import brush_from_vmf_sides
    out = []
    for e in (b for b in top if b.name == "entity"):
        cls = (e.get("classname") or "").lower()
        if cls not in SOLID_BRUSH_CLASSES:
            continue
        if cls == "func_brush" and (e.get("Solidity") or "0").strip() == "1":
            continue                        # Solidity: Never Solid
        brushes = []
        for solid in e.blocks("solid"):
            if blocks_sight(solid, materials, mask):
                br = brush_from_vmf_sides([(sd.get("plane"), sd.get("material", "")) for sd in solid.blocks("side")], cls)
                if br:
                    brushes.append(br)
        if brushes:
            out.append((brushes, cls in PHYSICS_BRUSH_CLASSES))
    return out


def _slots(vmf, bsp_path: str, materials: MaterialContents, content=None):
    """(world brushes, [(brushes, physics, rays)]) for the vision traces."""
    top = _parsed(vmf)
    world = bsp_brushes(bsp_path, model=0, mask=MASK_BLOCKLOS)
    for b in top:
        if b.name != "world":
            continue
        for solid in b.blocks("solid"):
            for side, disp, verts in solid_displacements(solid):
                world.extend(displacement_brushes(side.get("plane"), disp, verts=verts))
    ents = [(brushes, physics, True) for brushes, physics in _entity_solids(top, materials, MASK_BLOCKLOS)]
    if content is not None:
        hulls = _HULL_CACHE                  # model -> its collision pieces' planes (kept between runs)
        props = []
        for e in (b for b in top if b.name == "entity"):
            cls = (e.get("classname") or "").lower()
            if cls in DOOR_CLASSES and DOOR_MODE != "none":
                try:
                    pieces = _prop_pieces(e, content, hulls)
                except (ValueError, TypeError) as ex:
                    SKIPPED.append(f"'{e.get('targetname') or cls}': {ex}")
                    pieces = []
                if pieces:
                    ents.append((pieces, True, DOOR_MODE == "all"))
            elif cls in PHYSICS_PROP_CLASSES or cls in SOLID_KEY_PROP_CLASSES:
                try:
                    props += _prop_pieces(e, content, hulls)
                except (ValueError, TypeError) as ex:     # odd keyvalues or a damaged model: skip this prop
                    SKIPPED.append(f"'{e.get('targetname') or e.get('model') or cls}': {ex}")
        if props:
            # one slot for all of them: each prop is a VPhysics object (a trace starting inside one
            # is stuck at 0, else the closest hit wins), and that's what one physics slot gives
            ents.append((props, True, True))
    if len(ents) >= fastnav.NAV_STASH_SLOT:
        raise RuntimeError(f"{len(ents)} solid entities and doors: more than the nav analysis handles "
                           f"({fastnav.NAV_STASH_SLOT - 1})")
    return world, ents


def _model_info(model: str, content, cache: dict) -> tuple[int, str | None]:
    """(studiohdr flags, its prop_data: None, "static" when it has allowstatic, else "dynamic")."""
    key = ("info", model)
    if key not in cache:
        import re
        import struct
        data = content.read(model)
        flags, prop_data = 0, None
        if data and len(data) >= 320:
            flags = struct.unpack_from("<i", data, 152)[0]
            at, size = struct.unpack_from("<ii", data, 312)        # keyvalueindex, keyvaluesize
            text = data[at:at + size].decode("latin-1", "replace") if size > 0 else ""
            m = re.search(r'"?prop_data"?\s*\{([^}]*)\}', text, re.I)
            if m:
                static = re.search(r'"allowstatic"\s*"([^"]*)"', m.group(1), re.I)
                prop_data = "static" if static and static.group(1).strip() not in ("", "0") else "dynamic"
        cache[key] = (flags, prop_data)
    return cache[key]


def _prop_exists(cls: str, model: str, content, cache: dict) -> bool:
    """Whether the prop is in the running map. vbsp drops a prop_static whose model isn't a
    $staticprop or has prop_data that doesn't allow static; the game deletes a prop_physics whose
    model has no prop_data and a prop_dynamic whose model has prop_data without allowstatic
    (CBaseProp::Spawn; the _override classes skip the check). Measured: prop_physics fire
    barrels (no prop_data) are gone in the game and don't block sight."""
    flags, prop_data = _model_info(model, content, cache)
    if cls == "prop_static":
        return bool(flags & 0x10) and prop_data != "dynamic"
    if cls == "prop_physics":
        return prop_data is not None
    if cls == "prop_dynamic":
        return prop_data != "dynamic"
    return True


def _prop_solid(ent) -> int:
    """How a prop collides at spawn: SOLID_VPHYSICS (its .phy), SOLID_BBOX (its model's hull box)
    or 0 (not solid)."""
    cls = (ent.get("classname") or "").lower()
    if cls in PHYSICS_PROP_CLASSES or cls in DOOR_CLASSES:
        return SOLID_VPHYSICS
    try:
        solid = int(float(ent.get("solid") or SOLID_VPHYSICS))
    except ValueError:
        solid = SOLID_VPHYSICS
    return solid if solid in (SOLID_BBOX, SOLID_VPHYSICS) else 0


_HULL_CACHE: dict = {}
SKIPPED: list[str] = []          # props left out of the last analysis (odd keyvalues, damaged model)


def _model_hulls(model: str, solid: int, content, cache: dict):
    """A model's collision pieces as plane lists (normal, dist) in model space: its .phy's convex
    pieces, or (SOLID_BBOX) the box of its hull (studiohdr hull_min / hull_max)."""
    key = (model, solid)
    if key not in cache:
        from .phy import convex_planes, hull_planes, read_phy_pieces
        pieces = []
        if solid == SOLID_VPHYSICS:
            data = content.read(model[:-4] + ".phy")
            pieces = [hull_planes(pts, tris) if tris else convex_planes(pts)
                      for pts, tris in read_phy_pieces(data)] if data else []
        elif solid == SOLID_BBOX:
            from .vpk import model_bounds
            data = content.read(model)
            if data and len(data) >= 128:
                lo, hi = model_bounds(data)
                if all(h > l for l, h in zip(lo, hi)):
                    pieces = [[((1.0, 0.0, 0.0), hi[0]), ((-1.0, 0.0, 0.0), -lo[0]), ((0.0, 1.0, 0.0), hi[1]),
                               ((0.0, -1.0, 0.0), -lo[1]), ((0.0, 0.0, 1.0), hi[2]), ((0.0, 0.0, -1.0), -lo[2])]]
        cache[key] = [p for p in pieces if len(p) >= 4]
    return cache[key]


def _prop_pieces(ent, content, cache: dict | None = None):
    """A prop's collision pieces in world space (convex brushes), from its model's .phy (or its
    hull box for 'solid' 2: turned with the prop when static, an upright box on an entity)."""
    from .collision import Side, make_brush
    from .phy import angle_matrix
    model = (ent.get("model") or "").replace("\\", "/").lower()
    solid = _prop_solid(ent)
    cls = (ent.get("classname") or "").lower()
    cache = {} if cache is None else cache
    if not model.endswith(".mdl") or not solid or not _prop_exists(cls, model, content, cache):
        return []
    hulls = _model_hulls(model, solid, content, cache)
    if not hulls:
        return []
    origin = tuple(float(v) for v in (ent.get("origin") or "0 0 0").split())
    angles = tuple(float(v) for v in (ent.get("angles") or "0 0 0").split())
    if cls in DOOR_CLASSES:
        # a door that spawns open stands rotated by its distance (measured: the end safe room door,
        # spawnpos 1 at yaw -90, is at yaw -180 in the game); 2 opens the other way
        spawnpos = int(float(ent.get("spawnpos") or 0))
        distance = float(ent.get("distance") or 90)
        if spawnpos == 1:
            angles = (angles[0], angles[1] - distance, angles[2])
        elif spawnpos == 2:
            angles = (angles[0], angles[1] + distance, angles[2])
    m = angle_matrix(*angles)
    if solid == SOLID_BBOX:
        # an upright box: an entity's is its hull box (it doesn't turn with the entity), a static
        # prop's the box around its turned hull (measured: a container at yaw 45 / 30)
        (_n, hx), (_n, lx), (_n, hy), (_n, ly), (_n, hz), (_n, lz) = hulls[0]
        corners = [(x, y, z) for x in (-lx, hx) for y in (-ly, hy) for z in (-lz, hz)]
        if cls == "prop_static":
            corners = [tuple(m[r][0] * c[0] + m[r][1] * c[1] + m[r][2] * c[2] for r in range(3)) for c in corners]
        lo = [min(c[i] for c in corners) for i in range(3)]
        hi = [max(c[i] for c in corners) for i in range(3)]
        hulls = [[((1.0, 0.0, 0.0), hi[0]), ((-1.0, 0.0, 0.0), -lo[0]), ((0.0, 1.0, 0.0), hi[1]),
                  ((0.0, -1.0, 0.0), -lo[1]), ((0.0, 0.0, 1.0), hi[2]), ((0.0, 0.0, -1.0), -lo[2])]]
        m = angle_matrix(0.0, 0.0, 0.0)
    out = []
    for planes in hulls:
        sides = []
        for (nx, ny, nz), d in planes:      # world = origin + M p, so n' = M n, d' = d + n'.origin
            n = (m[0][0] * nx + m[0][1] * ny + m[0][2] * nz, m[1][0] * nx + m[1][1] * ny + m[1][2] * nz,
                 m[2][0] * nx + m[2][1] * ny + m[2][2] * nz)
            sides.append(Side(n, d + n[0] * origin[0] + n[1] * origin[1] + n[2] * origin[2]))
        br = make_brush(sides, ent.get("classname"))
        if br:
            out.append(br)
    return out


def _load_slot(slot: int, brushes, physics: bool, rays: bool) -> None:
    fastnav.load_world(CollisionWorld(brushes), memo=False)
    if not fastnav._lib.hl_world_stash(slot, int(physics), int(rays)):
        raise RuntimeError(f"nav analysis: no room for collision slot {slot}")


def _load_pvs(bsp_path: str) -> None:
    pv = BspPVS(bsp_path)
    planes = [v for (nx, ny, nz, d, _t, _s) in pv.planes for v in (nx, ny, nz, d)]
    types = [t for (_a, _b, _c, _d, t, _s) in pv.planes]
    nodes = [v for n in pv.nodes for v in n]
    rows = b"".join(pv.cluster_row(c) for c in range(pv.numclusters))
    F, I = ctypes.c_float, ctypes.c_int
    fastnav._lib.hl_pvs_load.restype = ctypes.c_int
    ok = fastnav._lib.hl_pvs_load(I(len(pv.planes)), (F * max(1, len(planes)))(*planes), (I * max(1, len(types)))(*types),
                             I(len(pv.nodes)), (I * max(1, len(nodes)))(*nodes), I(len(pv.leaf_cluster)),
                             (I * max(1, len(pv.leaf_cluster)))(*pv.leaf_cluster), I(pv.headnode),
                             I(pv.numclusters), I(pv.rowbytes), ctypes.create_string_buffer(rows, max(1, len(rows))))
    fastnav.check_memory()
    if not ok:
        raise ValueError("the compiled map's visibility data is damaged or incomplete (was it still being "
                         "written?): build the map again")


def visibility(areas, vmf, bsp_path: str, materials: MaterialContents, content=None,
               radius: float = 0.0) -> list[dict[int, int]]:
    """ComputeVisibilityToMesh for every area (in list order). areas: objects with nw, se,
    ne_z, sw_z. Returns, per area, {index of a visible area: attributes} (1 potentially,
    2 completely, 3 both)."""
    if not fastnav.available():
        raise RuntimeError("the nav analysis needs the native DLL")
    with fastnav.LOCK:                                    # the DLL's worlds are shared: one user at a time
        fastnav._lib.hl_oom()
        out = _visibility(areas, vmf, bsp_path, materials, content, radius)
        fastnav.check_memory()
        return out


def _visibility(areas, vmf, bsp_path, materials, content, radius):
    lib = fastnav._lib
    prev = fastnav.stash_current()                       # keep the nav generator's world
    ents = []
    try:
        world, ents = _slots(vmf, bsp_path, materials, content)
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


def hiding_spots(mesh, vmf, bsp_path: str, materials: MaterialContents) -> list[list[tuple]]:
    """Per area, its hiding spots as (position, flags), like CNavArea::ComputeHidingSpots: a
    corner with walls on both sides (no two-way, non-jump neighbour within 20 units of it) gets a
    spot 12.5 units in, IN_COVER when something is overhead or at least half of 16 lines around
    it hit (MASK_NPCSOLID_BRUSHONLY), else EXPOSED. Matches the game's spots exactly (positions,
    order and flags, measured on a 2330-area map)."""
    top = _parsed(vmf)
    ents = [b for brushes, _phys in _entity_solids(top, materials, MASK_NPCSOLID_BRUSHONLY) for b in brushes]
    disp = [b for w in top if w.name == "world" for so in w.blocks("solid")
            for sd, di, verts in solid_displacements(so) for b in displacement_brushes(sd.get("plane"), di, verts=verts)]
    world = CollisionWorld(bsp_brushes(bsp_path, model=0, mask=MASK_NPCSOLID_BRUSHONLY) + ents + disp)
    with fastnav.LOCK:
        if fastnav.available():
            fastnav._lib.hl_oom()
        out = _hiding_spots(mesh, world)
        if fastnav.available():
            fastnav.check_memory()
        return out


def _hiding_spots(mesh, world):
    import math
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
    from . import fastnav
    if not fastnav.available():
        raise RuntimeError("the nav analysis needs the native DLL")
    materials = MaterialContents(content, game_dir)
    top = _parsed(vmf_text)                    # parsed once for both steps
    SKIPPED.clear()
    if progress:
        progress("Finding hiding spots")
    spots = hiding_spots(mesh, top, bsp_path, materials)
    from .navfile import HidingSpot
    next_id = 0
    for a, lst in zip(mesh.areas, spots):
        a.hiding_spots = []
        for pos, flags in lst:
            a.hiding_spots.append(HidingSpot(next_id, pos, flags))
            next_id += 1
    if progress:
        progress("Computing visibility")
    lists = visibility(mesh.areas, top, bsp_path, materials, content)
    compress_visibility(mesh, lists)
    for a in mesh.areas:
        a.occupy = (120.0, 120.0)          # ComputeEarliestOccupyTimes: only Counter-Strike changes it
    mesh.analyzed = True
    if SKIPPED and hasattr(mesh, "problems"):
        mesh.problems.append("Nav analysis left out these props (they won't block sight): " + "; ".join(SKIPPED[:5]))
    return mesh
