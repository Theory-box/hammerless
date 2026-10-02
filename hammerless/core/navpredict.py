"""Predict the nav mesh the game will generate for a map, before compiling it.

Runs our reimplementation of the game's generator (navgen / navareas) on the map's
VMF text and marks the result like Build & Play does in-game (start room, end room,
Zombie Spawn Areas: see nav.py), so navanalysis can report the path from start to
end, islands and drops exactly as it does for the game's own .nav file.
"""
from __future__ import annotations

from .collision import CollisionWorld
from .navareas import NAV_MESH_JUMP, Generator
from .navfile import NavArea, NavLadder, NavMesh
from .navgen import NAV_MESH_NO_MERGE
from .vmf import parse

# L4D2's nav_generate grows from landmarks and item/weapon spawns, not player spawns
# (verified in-game: a map with only survivor spawns fails with "No valid walkable seed
# positions"; build.py adds a landmark when a map has none).
SEED_CLASSES = {"info_landmark"}


def seed_positions(vmf_text: str) -> list[tuple[float, float, float]]:
    seeds = []
    for e in (b for b in parse(vmf_text) if b.name == "entity"):
        cls = e.get("classname", "")
        if cls in SEED_CLASSES or (cls.startswith("weapon_") and cls.endswith("_spawn")):
            origin = e.get("origin")
            if origin:
                seeds.append(tuple(float(v) for v in origin.split()))
    return seeds


LADDER_CLASSES = {"func_ladder", "func_simpleladder"}   # vbsp turns func_ladder into func_simpleladder


def ladder_bounds(vmf_text: str) -> list[tuple[tuple, tuple]]:
    """World bounds of each ladder entity, in map order, padded by a unit like the engine's
    brush-model bounds (what nav generation measures ladders by)."""
    from .collision import brush_from_vmf_sides
    out = []
    for e in (b for b in parse(vmf_text) if b.name == "entity"):
        if e.get("classname", "") not in LADDER_CLASSES:
            continue
        mins, maxs = [1e9] * 3, [-1e9] * 3
        for solid in e.blocks("solid"):
            b = brush_from_vmf_sides([(sd.get("plane"), sd.get("material", "")) for sd in solid.blocks("side")])
            if b:
                mins = [min(mins[i], b.mins[i]) for i in range(3)]
                maxs = [max(maxs[i], b.maxs[i]) for i in range(3)]
        if mins[0] <= maxs[0]:
            out.append((tuple(v - 1.0 for v in mins), tuple(v + 1.0 for v in maxs)))
    return out


def to_navmesh(areas, regions, ladders=()) -> NavMesh:
    """Our generator's areas as a NavMesh, with the spawn attributes Build & Play's marking
    script would set (every area whose centre lies in a region gets its bits)."""
    ids = {id(a): i + 1 for i, a in enumerate(areas)}
    mesh = NavMesh(analyzed=False)
    for a in areas:
        n = NavArea(ids[id(a)], a.attributes & ~(NAV_MESH_JUMP | NAV_MESH_NO_MERGE), a.nw, a.se, a.ne_z, a.sw_z)
        n.connections = [[ids[id(b)] for b in a.connect[d] if id(b) in ids] for d in range(4)]
        c = n.centre
        for r in regions:
            if all(r.mins[i] <= c[i] <= r.maxs[i] for i in range(3)):
                n.spawn_attributes |= r.bits
        mesh.areas.append(n)
    lids = {id(lad): i + 1 for i, lad in enumerate(ladders)}
    aid = lambda a: ids.get(id(a), 0) if a is not None else 0
    for a, n in zip(areas, mesh.areas):
        n.ladders = [[lids[id(l)] for l in a.ladders[k] if id(l) in lids] for k in (0, 1)]
    for lad in ladders:
        mesh.ladders.append(NavLadder(lids[id(lad)], lad.width, lad.top, lad.bottom, lad.length, lad.dir,
                                      aid(lad.top_forward), aid(lad.top_left), aid(lad.top_right),
                                      aid(lad.top_behind), aid(lad.bottom_area)))
    return mesh


_last: dict = {"key": None, "mesh": None}


def _key(vmf_text: str, regions, climbs=(), wall_climbs=False) -> str:
    import hashlib
    return hashlib.sha1((vmf_text + repr([(r.mins, r.maxs, r.bits) for r in regions])
                         + repr([(c.bottom, c.top) for c in climbs]) + repr(wall_climbs)).encode()).hexdigest()


def cached(vmf_text: str, regions, climbs=(), wall_climbs=False) -> NavMesh | None:
    """The last prediction, if it was made for exactly this map (e.g. by the Predict button)."""
    if _last["key"] == _key(vmf_text, regions, climbs, wall_climbs) and _last["mesh"] is not None:
        import copy
        return copy.deepcopy(_last["mesh"])
    return None


def predict(vmf_text: str, regions, progress=None, climbs=(), wall_climbs=False) -> NavMesh:
    """progress(stage: str, count: int) is called between stages (from any thread).
    climbs: nav.NavClimb links to add (Zombie Climb markers)."""
    import copy
    mesh = _predict(vmf_text, regions, progress, climbs, wall_climbs)
    _last.update(key=_key(vmf_text, regions, climbs, wall_climbs), mesh=copy.deepcopy(mesh))
    return mesh


def _generator(vmf_text: str) -> Generator:
    gen = Generator(CollisionWorld.from_vmf(vmf_text))
    for p in seed_positions(vmf_text):
        gen.add_seed(p)
    for mins, maxs in ladder_bounds(vmf_text):
        gen.add_ladder(mins, maxs)
    return gen


def mesh_signature(mesh: NavMesh):
    return ([(a.id, a.flags, a.spawn_attributes, a.nw, a.se, a.ne_z, a.sw_z, a.connections, a.ladders)
             for a in mesh.areas],
            [(l.id, l.width, l.top, l.bottom, l.length, l.direction, l.top_forward, l.top_left, l.top_right,
              l.top_behind, l.bottom_area) for l in mesh.ladders])


def check_native(vmf_text: str, regions=(), climbs=(), wall_climbs=False) -> str | None:
    """Builds the mesh with the DLL's area pipeline and with the Python steps; None when they
    match exactly, else where they first differ. (Set HAMMERLESS_NAV_CHECK=1 to run this on
    every Build Navmesh.)"""
    fast = _predict(vmf_text, regions, None, climbs, wall_climbs, native_areas=True)
    slow = _predict(vmf_text, regions, None, climbs, wall_climbs, native_areas=False)
    a, b = mesh_signature(fast), mesh_signature(slow)
    if a == b:
        return None
    if len(a[0]) != len(b[0]):
        return f"native {len(a[0])} areas, Python {len(b[0])}"
    for x, y in zip(a[0], b[0]):
        if x != y:
            return f"area {x[0]} differs: native {x}, Python {y}"
    return "ladders differ"


def _predict(vmf_text: str, regions, progress=None, climbs=(), wall_climbs=False, native_areas=None) -> NavMesh:
    from . import fastnav
    with fastnav.LOCK:                 # the DLL's world is shared with the nav analysis (other threads)
        return _predict_locked(vmf_text, regions, progress, climbs, wall_climbs, native_areas)


def _predict_locked(vmf_text: str, regions, progress, climbs, wall_climbs, native_areas) -> NavMesh:
    import os
    if native_areas is None and os.environ.get("HAMMERLESS_NAV_CHECK"):
        problem = check_native(vmf_text, regions, climbs, wall_climbs)
        if problem:
            raise RuntimeError(f"native nav pipeline doesn't match the Python one: {problem}")
    gen = _generator(vmf_text)
    from . import fastnav
    if native_areas is not False and fastnav.available():   # the area pipeline in the DLL: same mesh, faster
        steps = (("Sampling walkable space", lambda: gen.sample(collect=False)),
                 ("Building areas", gen.native_areas), ("Connecting ladders", gen.connect_ladders))
    else:
        steps = python_steps(gen)
    for label, step in steps:
        if progress:
            progress(label, gen.node_count)
        step()
    return _finish(gen, regions, progress, climbs, wall_climbs)


def python_steps(gen):
    return (("Sampling walkable space", gen.sample), ("Building areas", gen.create_areas),
             ("Connecting areas", gen.connect_areas), ("Marking jump areas", gen.mark_jump_areas),
             ("Merging areas", gen.merge_areas), ("Splitting areas under overhangs", gen.split_areas_under_overhangs),
             ("Squaring up areas", gen.square_up_areas), ("Marking stairs", gen.mark_stair_areas),
             ("Removing jump areas", gen.stitch_and_remove_jump_areas),
             ("Fixing corners", gen.fix_corner_on_corner_areas), ("Fixing connections", gen.fix_connections),
             ("Connecting ladders", gen.connect_ladders))


def _finish(gen, regions, progress, climbs, wall_climbs) -> NavMesh:
    problems = []
    if wall_climbs:
        from .entities import ZOMBIE_CLIMB_MAX
        if progress:
            progress("Adding wall climbs", gen.node_count)
        gen.add_wall_climbs(ZOMBIE_CLIMB_MAX)
    for c in climbs:
        err = gen.add_climb(c.bottom, c.top)
        if err:
            problems.append(f"Zombie Climb '{c.source}': {err}")
    mesh = to_navmesh(gen.areas, regions, gen.ladders)
    mesh.problems = problems + spawn_block_problems(mesh, regions)
    return mesh


EMPTY, NO_MOBS = 2, 8192
SPAWN_BLOCK_SHARE = 0.4      # warn when one box stops spawns on more than this share of the nav


def spawn_block_problems(mesh: NavMesh, regions) -> list[str]:
    """A no-spawn box (EMPTY / NO_MOBS) covering much of the map leaves the Director almost
    nowhere to put commons: no zombies, and nothing else looks wrong."""
    out = []
    total = len(mesh.areas) or 1
    for r in regions:
        if not r.bits & (EMPTY | NO_MOBS):
            continue
        n = sum(1 for a in mesh.areas if all(r.mins[i] <= a.centre[i] <= r.maxs[i] for i in range(3)))
        if n / total > SPAWN_BLOCK_SHARE:
            what = " and ".join(w for w, bit in (("no wandering commons", EMPTY), ("no hordes", NO_MOBS)) if r.bits & bit)
            out.append(f"'{r.source}' marks {100 * n // total}% of the nav mesh ({what}), so few or no commons "
                       "can spawn. Is the box bigger than you meant? Shrink it to the spots zombies shouldn't use")
    blocked = sum(1 for a in mesh.areas if a.spawn_attributes & (EMPTY | NO_MOBS))
    if not out and blocked / total > SPAWN_BLOCK_SHARE:
        out.append(f"No-spawn boxes together mark {100 * blocked // total}% of the nav mesh, so commons have few "
                   "places to spawn. Check the Nav Attribute Regions set to EMPTY / NO_MOBS")
    return out
