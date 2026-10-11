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
from .nav import NAV_VOLUME_BITS
from .navgen import NAV_MESH_NO_MERGE
from .vmf import parse_shared

# L4D2's nav_generate grows from landmarks and item/weapon spawns, not player spawns
# (verified in-game: a map with only survivor spawns fails with "No valid walkable seed
# positions"; build.py adds a landmark when a map has none).
SEED_CLASSES = {"info_landmark"}


def seed_positions(vmf_text: str) -> list[tuple[float, float, float]]:
    seeds = []
    for e in (b for b in parse_shared(vmf_text) if b.name == "entity"):
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
    for e in (b for b in parse_shared(vmf_text) if b.name == "entity"):
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
            if not r.bits & NAV_VOLUME_BITS and r.contains(c):
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

# entities the nav generator and analysis never read: logic (adding logic nodes doesn't make a new nav mesh)
_NOT_NAV = ("logic_", "math_", "filter_", "point_clientcommand", "point_servercommand", "point_broadcastclientcommand",
            "ambient_generic", "game_text", "env_fade", "env_shake", "env_hudhint", "env_instructor_hint",
            "info_director", "trigger_", "commentary_zombie_spawner", "func_nav_blocker")
# (triggers aren't solid to the nav; nav blockers act in the game only)


def inputs_key(vmf_text: str, regions, climbs=(), wall_climbs=False, extra: str = "") -> str:
    """What the nav mesh (and its analysis) is made from: the map without its logic entities and without any
    entity's outputs, the nav settings, and the nav code itself. The same key: the nav on disk still fits."""
    import hashlib
    import os
    from .buildplan import _split
    from .vmf import parse
    kept = []
    for b in parse(vmf_text):
        if b.name.lower() == "entity":
            cls = (b.get("classname") or "").lower()
            if cls.startswith(_NOT_NAV):
                continue
            # (outputs and names: logic nodes name the doors and props they use)
            b.items = [it for it in b.items if not (hasattr(it, "name") and it.name.lower() == "connections")
                       and not (isinstance(it, tuple) and it[0].lower() == "targetname")]
        kept.append(b)
    h = hashlib.sha1(repr(_split(kept)).encode())
    h.update(_key("", regions, climbs, wall_climbs).encode() + extra.encode())
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(here)):            # (a new Hammerless version makes a new nav)
        if "nav" in name.lower() and name.endswith((".py", ".dll")):
            with open(os.path.join(here, name), "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def _key(vmf_text: str, regions, climbs=(), wall_climbs=False) -> str:
    import hashlib
    return hashlib.sha1((vmf_text + repr([(r.mins, r.maxs, r.bits, r.hulls, r.source) for r in regions])
                         + repr([(c.bottom, c.top, c.source) for c in climbs]) + repr(wall_climbs)).encode()).hexdigest()


def cached(vmf_text: str, regions, climbs=(), wall_climbs=False) -> NavMesh | None:
    """The last prediction, if it was made for exactly this map (e.g. by the Predict button)."""
    if _last["key"] == _key(vmf_text, regions, climbs, wall_climbs) and _last["mesh"] is not None:
        import copy
        return copy.deepcopy(_last["mesh"])
    return None


def predict(vmf_text: str, regions, progress=None, climbs=(), wall_climbs=False) -> NavMesh:
    """progress(stage: str, count: int) is called between stages (from any thread).
    climbs: nav.NavClimb links to add (Zombie Climb markers)."""
    return remember(_predict(vmf_text, regions, progress, climbs, wall_climbs), vmf_text, regions, climbs, wall_climbs)


def remember(mesh: NavMesh, vmf_text: str, regions, climbs=(), wall_climbs=False) -> NavMesh:
    """Keep a copy for cached() (also for meshes made in another process: navworker)."""
    import copy
    _last.update(key=_key(vmf_text, regions, climbs, wall_climbs), mesh=copy.deepcopy(mesh))
    return mesh


PROP_COLLISION = True        # static props are solid to the nav (False: brushes only, as before 2026-10-11)


def static_prop_brushes(vmf_text: str) -> list:
    """The map's prop_static collision as convex brushes (their models' .phy, or the hull box for solid 2): the game's
    nav_generate walks around them (measured on the user's styleguide_urban: the game's nav lies under 9% of the
    big props, ours under 74% before this), which also cuts open streets into the small areas the Director needs."""
    from .compile import find_game_root
    from .navanalyze import _prop_pieces
    from .vmf import parse_shared
    from .vpk import GameContent
    root = find_game_root()
    if not root:
        return []
    content, cache, out = GameContent(root), {}, []
    for ent in (b for b in parse_shared(vmf_text) if b.name == "entity"):
        if (ent.get("classname") or "").lower() != "prop_static":
            continue
        try:
            out.extend(_prop_pieces(ent, content, cache))
        except Exception:            # (a damaged model: that prop just isn't solid to the nav)
            pass
    return out


TOP_SEED_MIN = 48.0          # a brush top this wide each way can hold a standing player (hull 32) with room


def top_seeds(world) -> list[tuple[float, float, float]]:
    """A seed above the middle of every flat top a player could stand on (world and detail brushes, not props): roofs,
    ledges, walkways the flood from the item spawns never reaches (it doesn't climb walls), so they get nav without a
    landmark placed on each. Only tops inside the map: a line straight up from it hits something (a ceiling or the
    skybox), which the top of the map's outer shell doesn't. The seed drops onto the floor below it (find_ground);
    one in solid or on something too narrow makes nothing."""
    from .collision import DISPLACEMENT, SKY
    out = []
    for b in world.brushes:
        if b.source.startswith("prop_") or b.source == "displacement":
            continue
        if b.maxs[0] - b.mins[0] < TOP_SEED_MIN or b.maxs[1] - b.mins[1] < TOP_SEED_MIN:
            continue
        if not any(not s.bevel and s.normal[2] >= 0.7 and s.material != DISPLACEMENT and s.material not in SKY
                   for s in b.sides):
            continue              # (a skybox brush's top is never a floor: Auto Seal's box, the map's own sky)
        x, y, z = (b.mins[0] + b.maxs[0]) / 2.0, (b.mins[1] + b.maxs[1]) / 2.0, b.maxs[2] + 16.0
        up = world.trace_hull((x, y, z), (x, y, z + 32768.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        if up.fraction < 1.0 and not up.startsolid:
            out.append((x, y, z))
    return out


def _generator(vmf_text: str, regions=()) -> Generator:
    from .nav import nav_cuts
    world = CollisionWorld.from_vmf(vmf_text)
    if PROP_COLLISION:
        props = static_prop_brushes(vmf_text)
        if props:
            world = CollisionWorld(world.brushes + props)
    gen = Generator(world)
    gen.cuts = nav_cuts(regions)          # (before the seeds: none start inside a No Nav Volume)
    for p in seed_positions(vmf_text):
        gen.add_seed(p)
    from . import fastnav
    if fastnav.available():
        fastnav.load_world(world)          # (the up traces below in the DLL; sampling loads it again, memo kept)
    for p in top_seeds(world):
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
        if fastnav.available():
            fastnav._lib.hl_oom()          # (a stale flag from an earlier failure)
        mesh = _predict_locked(vmf_text, regions, progress, climbs, wall_climbs, native_areas)
        if fastnav.available():
            fastnav.check_memory()
        return mesh


def _predict_locked(vmf_text: str, regions, progress, climbs, wall_climbs, native_areas) -> NavMesh:
    import os
    if native_areas is None and os.environ.get("HAMMERLESS_NAV_CHECK"):
        problem = check_native(vmf_text, regions, climbs, wall_climbs)
        if problem:
            raise RuntimeError(f"native nav pipeline doesn't match the Python one: {problem}")
    gen = _generator(vmf_text, regions)
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
    # where survivors can walk from the start room: worked out before the zombie-only climb links
    pre = to_navmesh(gen.areas, regions, gen.ladders)
    walk = survivor_areas(pre)
    survivors = None if walk is None else {id(a) for a, m in zip(gen.areas, pre.areas) if m.id in walk}
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
    if gen.node_count >= MAX_NODES:
        problems.insert(0, f"The map has more walkable space than the nav builder covers ({MAX_NODES:,} nav points): "
                           "part of it has no nav mesh (and building it is slow). Split the level into two maps, "
                           "or remove unreachable floors (roofs, areas outside the playable space)")
    if not gen.areas and any(len(c) > 3 and c[3] for c in gen.cuts):
        problems.insert(0, "No nav mesh: nothing walkable is inside the Control Volumes set to Only inside (nav). "
                           "Each needs floor in it that survivors can reach from a spawn (or a seed)")
    blocked = spawn_block_problems(mesh, regions)        # (the user's own no-spawn boxes)
    if survivors is not None:
        # commons off the roofs and ledges survivors can't walk to (the old nav put 26 of 30 starting commons there):
        # EMPTY is only read by the wandering commons' placement (server.dll); specials (smokers prefer spawning
        # above the survivors) and hordes still use these areas
        for a, m in zip(gen.areas, mesh.areas):
            if id(a) not in survivors:
                m.spawn_attributes |= EMPTY
    mesh.problems = problems + blocked
    return mesh


def _links(mesh: NavMesh) -> dict[int, list]:
    """Each area's way out: connections (walks, drops) and the ladders it's on."""
    by = mesh.by_id()
    lad = {l.id: l for l in mesh.ladders}
    out = {}
    for a in mesh.areas:
        nxt = [i for d in a.connections for i in d if i in by]
        for k in (0, 1):
            for lid in (a.ladders[k] if a.ladders else []):
                l = lad.get(lid)
                if l is not None:
                    nxt += [i for i in (l.bottom_area, l.top_forward, l.top_left, l.top_right, l.top_behind) if i in by]
        out[a.id] = nxt
    return out


def _spread(sources, edges) -> set:
    seen, todo = set(sources), list(sources)
    while todo:
        for i in edges.get(todo.pop(), ()):
            if i not in seen:
                seen.add(i)
                todo.append(i)
    return seen


def survivor_areas(mesh: NavMesh) -> set | None:
    """Ids of the areas survivors can walk (drop, climb a ladder) to from the start room; None without one."""
    start = [a.id for a in mesh.areas if a.spawn_attributes & PLAYER_START]
    return _spread(start, _links(mesh)) if start else None


MAX_NODES = 500000           # navgen.Generator.sample's default


EMPTY, NO_MOBS, PLAYER_START = 2, 8192, 128
SPAWN_BLOCK_SHARE = 0.4      # warn when one box stops spawns on more than this share of the nav


def spawn_block_problems(mesh: NavMesh, regions) -> list[str]:
    """A no-spawn box (EMPTY / NO_MOBS) covering much of the map leaves the Director almost
    nowhere to put commons: no zombies, and nothing else looks wrong."""
    out = []
    total = len(mesh.areas) or 1
    for r in regions:
        if not r.bits & (EMPTY | NO_MOBS) or r.bits & NAV_VOLUME_BITS:
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
