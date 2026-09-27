"""MapIR -> VMF text, with validation and automatic fixes (sealing, director, sun)."""
from __future__ import annotations

import dataclasses

from dataclasses import dataclass, field

from . import geometry as g
from .displacement import build_patches
from .entities import default_keyvalues
from .entities import PSEUDO_ENTITIES, ZOMBIES_ONLY
from .gamefiles import collect_crescendos, director_input_script, script_path
from .ir import Brush, Entity, MapIR, Output, Vec3
from .nav import collect_climbs, collect_regions
from .vmf import Block, VMFWriter

SURVIVOR_SPAWNS = {"info_player_start", "info_survivor_position"}

# The Director only computes the flow (the start-to-end progress path) when the end
# room's info_changelevel names a different map; empty or the map itself breaks it,
# and with no flow there are no wandering commons, Tanks or Witches.
FALLBACK_NEXT_MAP = "c1m2_streets"


def end_landmark_renames(ir: MapIR) -> dict[str, tuple[Entity, Entity, str]]:
    """Duplicate landmark names that can be fixed automatically: exactly two landmarks
    share a name and one of them sits inside an info_changelevel using that name (the
    end room). Returns {name: (end landmark, changelevel, new name)}."""
    landmarks = [e for e in ir.entities if e.classname == "info_landmark"]
    names = [e.keyvalues.get("targetname") for e in landmarks]
    taken = set(names)
    fixes = {}
    for n in sorted({n for n in names if n and names.count(n) == 2}):
        for cl in ir.entities:
            if cl.classname != "info_changelevel" or cl.keyvalues.get("landmark") != n or not cl.brushes:
                continue
            lo, hi = g.bounds([v for b in cl.brushes for f in b.faces for v in f.verts])
            inside = [e for e in landmarks if e.keyvalues.get("targetname") == n and e.origin is not None
                      and all(lo[i] - 1 <= e.origin[i] <= hi[i] + 1 for i in range(3))]
            if len(inside) == 1:
                k = 2
                while f"{n}_end{k if k > 2 else ''}" in taken:
                    k += 1
                new = f"{n}_end{k if k > 2 else ''}"
                taken.add(new)
                fixes[n] = (inside[0], cl, new)
                break
    return fixes


# Auto detail: world brushes cut the map into visibility regions (vvis time grows fast
# with them); func_detail brushes don't, but still block players and cast shadows.
DETAIL_MIN_PLANES = 9       # round things: cylinders, arches (a box has 6, a wedge 5)
DETAIL_MAX_SIZE = 128.0     # small things: crates, steps, trim
SEAL_MATERIALS = {"tools/toolsskybox", "tools/toolsnodraw", "tools/toolsblack"}


def is_auto_detail(brush: Brush, mode: str) -> bool:
    if mode == "OFF":
        return False
    if all(f.material.lower() in SEAL_MATERIALS for f in brush.faces):
        return False        # sealing / hidden brushes stay world
    if mode == "ALL":
        return True
    lo, hi = g.bounds([v for f in brush.faces for v in f.verts])
    return (len(g.merge_coplanar(brush.faces)) >= DETAIL_MIN_PLANES
            or max(b - a for a, b in zip(lo, hi)) <= DETAIL_MAX_SIZE)


def bad_next_map(cl: Entity, map_name: str) -> bool:
    nxt = cl.keyvalues.get("map", "").strip().lower()
    return not nxt or nxt == map_name.strip().lower()


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    problems: list = field(default_factory=list)   # mapcheck.Problem: with object and location
    solid_sources: dict = field(default_factory=dict)  # VMF solid id -> Blender object name
    nav_regions: list = field(default_factory=list)  # nav.NavRegion: marks for the nav mesh
    nav_climbs: list = field(default_factory=list)   # nav.NavClimb: Zombie Climb links

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(ir: MapIR, content=None, physical: bool = True) -> Report:
    """content: optional vpk.GameContent to check materials/models exist."""
    r = Report()
    from .mapcheck import Problem
    brush_problems = []          # with the brush's centre, so the problem list can go to it
    for b in ir.brushes + [b for e in ir.entities for b in e.brushes]:
        for p in g.check_brush(b):
            msg = f"Brush '{p.source}' {p.message}"
            r.errors.append(msg)
            pts = [v for f in b.faces for v in f.verts]
            centre = tuple(sum(v[i] for v in pts) / len(pts) for i in range(3)) if pts else None
            brush_problems.append(Problem("ERROR", msg, p.source, centre))
    misses = g.near_misses(solid_geometry(ir))
    for a, b, axis, gap, where in misses[:MAX_NEAR_MISSES]:
        who = f"'{a}'" if a == b else f"'{a}' and '{b}'"
        msg = (f"{who} almost line up ({gap:.3f} units apart in {axis}). Hammerless lines them up when it exports "
               "(near misses make the compiler cut sliver faces that break the lighting), but you can snap them")
        brush_problems.append(Problem("WARNING", msg, a, where))
        r.warnings.append(msg)
    if len(misses) > MAX_NEAR_MISSES:
        r.info.append(f"{len(misses) - MAX_NEAR_MISSES} more near misses not listed.")
    r.problems = list(brush_problems)

    classes = [e.classname for e in ir.entities]
    if not SURVIVOR_SPAWNS & set(classes):
        r.warnings.append("No survivor spawn (info_player_start / info_survivor_position): "
                          "players will spawn at the map origin.")
    if "info_player_start" not in classes and "info_survivor_position" in classes:
        r.info.append("No info_player_start. Adding one next to the first survivor spawn.")
    if classes.count("info_director") > 1:
        r.errors.append("More than one info_director.")
    if "prop_door_rotating_checkpoint" not in classes:
        r.info.append("No safe room doors. Fine for testing, but a campaign map needs them.")
    names = [e.keyvalues.get("targetname") for e in ir.entities if e.classname == "info_landmark"]
    fixable = end_landmark_renames(ir)
    for n in sorted({n for n in names if names.count(n) > 1}):
        if n in fixable:
            r.warnings.append(f"Both safe rooms' landmarks are named '{n}'. Renamed the end room's to "
                              f"'{fixable[n][2]}'. Change it on '{fixable[n][0].source}' if the next map "
                              "expects another name.")
            continue
        r.errors.append(f"Two info_landmark entities are both named '{n}'. The end safe room's landmark "
                        "must differ from the start safe room's (it pairs with the NEXT map's start room).")
    changelevels = [e for e in ir.entities if e.classname == "info_changelevel"]
    landmarks = {e.keyvalues.get("targetname") for e in ir.entities if e.classname == "info_landmark"}
    for cl in changelevels:
        if not cl.brushes:
            r.errors.append(f"info_changelevel '{cl.source}' has no brush volume.")
        if cl.keyvalues.get("landmark") and cl.keyvalues["landmark"] not in landmarks:
            r.warnings.append(f"info_changelevel refers to landmark '{cl.keyvalues['landmark']}' which doesn't exist.")
        if bad_next_map(cl, ir.settings.name):
            r.warnings.append(f"End safe room's Next Map is {'empty' if not cl.keyvalues.get('map') else 'this map itself'}. "
                              "The game then can't work out the path from start to end, so no wandering "
                              f"zombies spawn. Using '{FALLBACK_NEXT_MAP}'; set Next Map on "
                              f"'{cl.source or 'info_changelevel'}' to your next map.")

    names = {e.keyvalues.get("targetname") for e in ir.entities} | {"director"}  # director is auto-added
    for e in ir.entities:
        for o in e.outputs:
            if not o.target.startswith("!") and o.target not in names and o.target not in classes:
                r.warnings.append(f"'{e.source or e.classname}' output {o.output} targets '{o.target}', "
                                  "but no entity has that name.")

    regions, nav_problems = collect_regions(ir)
    r.errors += nav_problems
    r.warnings += collect_climbs(ir)[1]
    ir.crescendos.clear()
    r.errors += collect_crescendos(ir)
    for e in ir.entities:
        for o in e.outputs:
            if o.input.lower() == "scriptedpanicevent" and resolve_crescendo(ir, o.parameter) is None:
                r.warnings.append(f"'{e.source or e.classname}' starts crescendo '{o.parameter}', "
                                  "but no Crescendo Definition has that name.")
    if "info_changelevel" in classes and not any(x.bits & 128 for x in regions)             and "info_survivor_position" in classes:
        r.info.append("No PLAYER_START nav region. Survivors may not spawn in the start safe room. "
                      "The Start Safe Room preset includes one.")

    for t in ir.terrains:
        if t.power not in (2, 3, 4):
            r.errors.append(f"Terrain '{t.source}' power must be 2, 3 or 4.")

    if content is not None:
        mats = {f.material for b in ir.brushes for f in b.faces} | {t.material for t in ir.terrains}
        for m in sorted(mats):
            if m.startswith("hammerless/"):
                continue  # written by Hammerless into the game folder at export
            if not content.has_material(m):
                r.warnings.append(f"Material '{m}' not found in game files (will show as purple/black checkers).")
        for e in ir.entities:
            mdl = e.keyvalues.get("model", "")
            if mdl.endswith(".mdl") and not content.has_model(mdl):
                r.warnings.append(f"Model '{mdl}' on {e.classname} not found in game files.")

    if physical:
        from .mapcheck import cached_check
        try:
            found = cached_check(ir)
        except Exception as ex:   # a checker bug must never block building
            found = []
            r.info.append(f"Map check skipped ({ex})")
        for p in found:
            r.warnings.append(p.message)
        r.problems = brush_problems + found

    all_pts = _all_points(ir)
    if all_pts:
        mins, maxs = g.bounds(all_pts)
        if min(mins) < -16000 or max(maxs) > 16000:
            r.errors.append("Map extends beyond ±16000 units. Check your scale setting.")
    return r


def resolve_crescendo(ir: MapIR, parameter: str) -> str | None:
    """Crescendo name an output refers to. Accepts 'crescendo_1' and the v0.1 form
    'hammerless/crescendo_crescendo_1'."""
    name = parameter.split("/")[-1].removeprefix(f"hammerless_{ir.settings.name}_")  # already resolved
    for candidate in (name, name.removeprefix("crescendo_")):
        if candidate in ir.crescendos:
            return candidate
    return None


def _all_points(ir: MapIR) -> list[Vec3]:
    pts = [v for b in ir.brushes for f in b.faces for v in f.verts]
    pts += [v for e in ir.entities for b in e.brushes for f in b.faces for v in f.verts]
    pts += [e.origin for e in ir.entities if e.origin is not None]
    for t in ir.terrains:
        hs = [h for row in t.heights for h in row if h is not None]
        if hs:
            rows, cols = len(t.heights), len(t.heights[0])
            x1 = t.origin[0] + (cols - 1) * t.spacing
            y1 = t.origin[1] + (rows - 1) * t.spacing
            pts += [(t.origin[0], t.origin[1], min(hs) - 17), (x1, y1, max(hs))]
    return pts


SNAP_TO_FLOOR = {"info_player_start", "info_survivor_position", "info_survivor_rescue"}
SNAP_RANGE = 48.0     # look for a floor this far above/below the spawn point
SNAP_LIFT = 2.0       # stand this far above it (a spawn touching the floor counts as stuck)


def floor_below(ir: MapIR, x: float, y: float, z: float) -> float | None:
    """Highest brush or terrain top within SNAP_RANGE of z at (x, y)."""
    best = None
    solids = ir.brushes + [b for e in ir.entities if e.classname == "func_detail" for b in e.brushes]
    for b in solids:
        span = g.vertical_span(b, x, y)
        if span and abs(span[1] - z) <= SNAP_RANGE and (best is None or span[1] > best):
            best = span[1]
    for t in ir.terrains:
        rows, cols = len(t.heights), len(t.heights[0]) if t.heights else 0
        c = (x - t.origin[0]) / t.spacing
        r = (y - t.origin[1]) / t.spacing
        if 0 <= r <= rows - 1 and 0 <= c <= cols - 1:
            r0, c0 = min(int(r), rows - 2), min(int(c), cols - 2)
            fr, fc = r - r0, c - c0
            corners = [t.heights[r0][c0], t.heights[r0][c0 + 1], t.heights[r0 + 1][c0], t.heights[r0 + 1][c0 + 1]]
            if None not in corners:
                h = (corners[0] * (1 - fc) + corners[1] * fc) * (1 - fr) + (corners[2] * (1 - fc) + corners[3] * fc) * fr
                if abs(h - z) <= SNAP_RANGE and (best is None or h > best):
                    best = h
    return best


def snap_spawns_to_floor(ir: MapIR, report: "Report") -> None:
    """Put player/survivor spawns just above the floor under them. A spawn whose origin
    touches or sits in the floor makes the player spawn as a spectator, and nav
    generation fails ('No valid walkable seed positions')."""
    moved = 0
    for e in ir.entities:
        if e.classname not in SNAP_TO_FLOOR or e.origin is None:
            continue
        x, y, z = e.origin
        floor = floor_below(ir, x, y, z)
        if floor is not None and abs(z - (floor + SNAP_LIFT)) > 0.5:
            e.origin = (x, y, floor + SNAP_LIFT)
            moved += 1
    if moved:
        report.info.append(f"Placed {moved} spawn point(s) just above the floor.")


SPAWN_CLEARANCE = 64.0   # keep the auto-added player start this far from survivor spawns


def clear_spawn_spot(ir: MapIR, entities, near: Vec3) -> Vec3:
    """A floor spot near `near`, at least SPAWN_CLEARANCE from every survivor/player spawn,
    so spawn points don't overlap (normal mapping practice)."""
    import math
    spawns = [e.origin for e in entities if e.classname in SNAP_TO_FLOOR and e.origin is not None]
    x0, y0, z0 = near
    for radius in (SPAWN_CLEARANCE, SPAWN_CLEARANCE * 2, SPAWN_CLEARANCE * 3):
        for step in range(8):
            a = math.radians(180 + 45 * step)        # try behind first, then around
            x, y = x0 + radius * math.cos(a), y0 + radius * math.sin(a)
            if any(math.hypot(x - sx, y - sy) < SPAWN_CLEARANCE and abs(z0 - sz) < 72 for sx, sy, sz in spawns):
                continue
            floor = floor_below(ir, x, y, z0)
            if floor is not None:
                return (x, y, floor + SNAP_LIFT)
    return (x0 - SPAWN_CLEARANCE, y0, z0)


MAX_NEAR_MISSES = 40


def solid_geometry(ir: MapIR) -> list:
    """Brushes that become the map's solid surfaces (world and func_detail): the ones whose
    near misses make vbsp cut sliver faces. Triggers, ladders and clips don't."""
    return ir.brushes + [b for e in ir.entities if e.classname == "func_detail" for b in e.brushes]


def zombie_ladder_entity(e: Entity, report: Report) -> Entity:
    """func_ladder with team 'zombies' -> func_simpleladder team 2. vbsp turns every func_ladder
    into a team 0 func_simpleladder and drops the team, so zombie-only ladders are written
    directly, with the climbable face's outward normal (what vbsp would work out)."""
    kv = {k: v for k, v in e.keyvalues.items() if k != "team"}
    if e.keyvalues.get("team", "").strip().lower() not in ZOMBIES_ONLY:
        return dataclasses.replace(e, keyvalues=kv)
    faces = [f for b in e.brushes for f in b.faces if f.material.lower() == "tools/toolsinvisibleladder"]
    if not faces:
        report.warnings.append(f"Zombie ladder '{e.source}' has no tools/toolsinvisibleladder face; "
                               "it stays an ordinary ladder.")
        return dataclasses.replace(e, keyvalues=kv)
    n = g.polygon_normal(faces[0].verts)
    kv.update({"team": "2", "normal.x": f"{n[0]:.6f}", "normal.y": f"{n[1]:.6f}", "normal.z": f"{n[2]:.6f}"})
    return dataclasses.replace(e, classname="func_simpleladder", keyvalues=kv)


def seal_brushes(ir: MapIR):
    """Six skybox brushes forming a hollow box around everything."""
    pts = _all_points(ir)
    if not pts:
        return []
    (x0, y0, z0), (x1, y1, z1) = g.bounds(pts)
    p, t = ir.settings.seal_padding, ir.settings.seal_thickness
    x0, y0, z0, x1, y1, z1 = x0 - p, y0 - p, z0 - p, x1 + p, y1 + p, z1 + p
    sky = "tools/toolsskybox"
    boxes = [
        ((x0 - t, y0 - t, z0 - t), (x1 + t, y1 + t, z0)),   # bottom
        ((x0 - t, y0 - t, z1), (x1 + t, y1 + t, z1 + t)),   # top
        ((x0 - t, y0, z0), (x0, y1, z1)),                   # -x
        ((x1, y0, z0), (x1 + t, y1, z1)),                   # +x
        ((x0 - t, y0 - t, z0), (x1 + t, y0, z1)),           # -y
        ((x0 - t, y1, z0), (x1 + t, y1 + t, z1)),           # +y
    ]
    return [g.box_brush(a, b, sky, "auto_seal") for a, b in boxes]


def build_vmf(ir: MapIR, content=None) -> tuple[str | None, Report]:
    report = validate(ir, content)
    if not report.ok:
        return None, report
    snap_spawns_to_floor(ir, report)
    welded = g.weld_near_misses(solid_geometry(ir))
    if welded:
        report.info.append(f"Lined up {welded} brush corner position(s) that missed each other by under "
                           f"{g.WELD_TOLERANCE:g} units (they make the compiler cut sliver faces that break lighting).")
    for lm, cl, new in end_landmark_renames(ir).values():
        lm.keyvalues = {**lm.keyvalues, "targetname": new}
        cl.keyvalues = {**cl.keyvalues, "landmark": new}

    w = VMFWriter()
    s = ir.settings
    world = Block("world")
    world.kv("id", w.new_id()).kv("mapversion", 1).kv("classname", "worldspawn")
    world.kv("skyname", s.skyname).kv("detailmaterial", s.detail_material)
    world.kv("detailvbsp", s.detail_vbsp).kv("maxpropscreenwidth", -1)

    # detail only when the map is sealed by the automatic shell (func_detail doesn't seal)
    mode = s.auto_detail if s.auto_seal else "OFF"
    detail = [b for b in ir.brushes if is_auto_detail(b, mode)]
    detail_ids = {id(b) for b in detail}
    for b in ir.brushes:
        if id(b) not in detail_ids:
            world.add(w.solid(b))
    if detail:
        report.info.append(f"Made {len(detail)} round/small brush(es) func_detail so they don't slow down vvis.")
    n_patches = 0
    for t in ir.terrains:
        for brush, disp in build_patches(t):
            world.add(w.solid(brush, {0: disp}))
            n_patches += 1
    if s.auto_seal:
        for b in seal_brushes(ir):
            world.add(w.solid(b))
        report.info.append("Sealed the map in an automatic skybox shell.")

    entities = [e for e in ir.entities if e.classname not in PSEUDO_ENTITIES]
    # point crescendo outputs at the map-specific script file
    for e in entities:
        for o in e.outputs:
            if o.input.lower() == "scriptedpanicevent":
                name = resolve_crescendo(ir, o.parameter)
                if name:
                    o.parameter = director_input_script(s.name, name)
    for e in entities:
        if e.classname == "info_changelevel" and bad_next_map(e, s.name):
            e.keyvalues = {**e.keyvalues, "map": FALLBACK_NEXT_MAP}
    entities = [zombie_ladder_entity(e, report) if e.classname == "func_ladder" else e for e in entities]
    classes = {e.classname for e in entities}
    if s.auto_director and "info_director" not in classes:
        entities.append(Entity("info_director", (0, 0, 0), (0, 0, 0), default_keyvalues("info_director")))
        report.info.append("Added info_director.")
    if s.auto_light_environment and "light_environment" not in classes:
        kv = {"_light": "{} {} {} {}".format(*s.sun_color, s.sun_brightness),
              "_ambient": "{} {} {} {}".format(*s.ambient_color, s.ambient_brightness),
              "pitch": f"{s.sun_pitch:g}"}
        entities.append(Entity("light_environment", (0, 0, 0), (s.sun_pitch, s.sun_yaw, 0), kv))
        report.info.append("No sun in the scene. Added one from the Lighting settings.")
    if s.fog_enabled and "env_fog_controller" not in classes:
        entities.append(Entity("env_fog_controller", (0, 0, 64), (0, 0, 0), {
            "targetname": "hammerless_fog", "fogenable": "1", "spawnflags": "1",
            "fogcolor": "{} {} {}".format(*s.fog_color), "fogcolor2": "{} {} {}".format(*s.fog_color),
            "fogstart": f"{s.fog_start:g}", "fogend": f"{s.fog_end:g}",
            "fogmaxdensity": f"{s.fog_max_density:g}", "farz": "-1"}))
    entities.append(Entity("logic_script", (0, 0, 24), (0, 0, 0), {
        "targetname": "hammerless_ready", "vscripts": script_path("ready"), "thinkfunction": "HLR_Think"}))
    if s.debug_log:
        entities.append(Entity("logic_script", (0, 0, 32), (0, 0, 0), {
            "targetname": "hammerless_debug", "vscripts": script_path(f"debug_{s.name}"),
            "thinkfunction": "HL_Think"}))
        report.info.append("Debug log on: Director events and stats go to the console (HAMMERLESS_DEBUG).")
    if s.autotest:
        from .autotest import plan_route
        route, _ = plan_route(ir)   # also names unnamed buttons so the script can press them
        entities.append(Entity("logic_script", (0, 0, 40), (0, 0, 0), {
            "targetname": "hammerless_autotest", "vscripts": script_path(f"autotest_{s.name}"),
            "thinkfunction": "HLT_Think"}))
        report.info.append(f"Bot Walkthrough Test on: {len(route)} waypoint(s). Watch for HAMMERLESS_AUTOTEST "
                           "in the console.")
    if s.director_enabled:
        entities.append(Entity("logic_auto", (0, 0, 48), (0, 0, 0), {"spawnflags": "1"}, outputs=[
            Output("OnMapSpawn", "director", "BeginScript", director_input_script(s.name, "director"), 1.0, 1)]))
    if "info_player_start" not in classes:
        first = next((e for e in entities if e.classname == "info_survivor_position"), None)
        origin = clear_spawn_spot(ir, entities, first.origin) if first else (0.0, 0.0, 0.0)
        entities.append(Entity("info_player_start", origin, first.angles if first else (0, 0, 0), {}))

    if not any(e.classname == "info_landmark" for e in entities):
        # nav_generate grows the nav mesh from item spawns and landmarks, not from player
        # spawns (verified in-game: a map with only survivor/player spawns fails with "No
        # valid walkable seed positions"; adding one info_landmark fixes it). Valve's maps
        # always have landmarks, so add an unused one above the first spawn as a seed.
        spawn = next((e for e in entities if e.classname in SNAP_TO_FLOOR and e.origin is not None), None)
        if spawn is not None:
            x, y, z = spawn.origin
            entities.append(Entity("info_landmark", (x, y, z + 32), (0, 0, 0), {"targetname": "hammerless_nav_seed"}))

    ent_blocks = [w.entity(e, [w.solid(b) for b in e.brushes]) for e in entities]
    if detail:
        ent_blocks.append(w.entity(Entity("func_detail"), [w.solid(b) for b in detail]))
    report.info.append(
        f"{len(ir.brushes)} brushes, {n_patches} terrain patches, {len(entities)} entities.")
    report.solid_sources = dict(w.solid_sources)
    return w.document(world, ent_blocks), report
