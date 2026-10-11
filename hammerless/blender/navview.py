"""Show the game's real nav mesh (maps/<map>.nav) in the viewport, with what the
analysis found: areas survivors can reach, the break in the path to the end room,
islands, drops and jump-ups."""
import math
import os

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..core.navanalysis import CHECKPOINT, EMPTY, NO_MOBS, OBSCURED, PLAYER_START, analyse
from ..core.navfile import load_nav

_state = {"path": None, "mtime": None, "mesh": None, "report": None, "batches": None, "key": None,
          "source": None,         # "game" (read from maps/<map>.nav) or "predicted" (our generator)
          "vis": None,            # expanded visibility lists {area id: {area id: attributes}} (analyzed navs)
          "owner": None,          # (scene name, map name) the nav on screen is for
          "busy": None,           # "build" / "analyze" while a nav thread runs
          "cancel": False}        # Cancel pressed: the running one stops on its next tick
_handlers = []

COLORS = {
    "start": (0.25, 0.55, 1.0, 0.45), "end": (0.75, 0.35, 1.0, 0.45),
    "reach": (0.2, 0.85, 0.35, 0.3), "unreach": (0.95, 0.1, 0.05, 0.55),
    "obscured": (1.0, 0.6, 0.1, 0.4), "plain": (0.55, 0.75, 0.95, 0.25),
    "can_spawn": (0.2, 0.85, 0.3, 0.45), "too_close": (1.0, 0.45, 0.05, 0.5), "in_view": (1.0, 0.85, 0.1, 0.45),
    "no_spawn": (0.95, 0.15, 0.15, 0.5), "no_wander": (0.95, 0.45, 0.6, 0.45), "no_mobs": (0.65, 0.3, 0.95, 0.45),
    "drop": (1.0, 0.55, 0.1, 1.0), "jump": (0.1, 0.9, 1.0, 1.0), "outline": (0.05, 0.05, 0.05, 0.6),
    "break": (1.0, 0.1, 0.1, 1.0), "ladder": (1.0, 0.9, 0.1, 1.0),
    "seen_complete": (0.2, 0.9, 0.3, 0.5), "seen_partly": (1.0, 0.85, 0.1, 0.45), "seen_both": (0.55, 0.95, 0.25, 0.5),
    "unseen": (0.25, 0.25, 0.28, 0.25), "viewer": (1.0, 1.0, 1.0, 0.7),
    "spot_cover": (0.2, 0.55, 1.0, 1.0), "spot_exposed": (1.0, 0.5, 0.1, 1.0),
    "zombie_now": (1.0, 0.08, 0.05, 0.95), "zombie_later": (0.55, 0.55, 0.55, 0.55),
    "zombie_lost": (0.7, 0.3, 1.0, 0.9), "survivors": (1.0, 1.0, 1.0, 0.9),
}
LIFT = 2.0      # draw this many Hammer units above the floor so areas don't flicker


def nav_path(context) -> str | None:
    from .ops import game_root
    root = game_root(context)
    if not root:
        return None
    return os.path.join(root, "left4dead2", "maps", f"{context.scene.hammerless.map_name}.nav")


def load(context) -> str:
    """(Re)read the nav file. Returns a short status message."""
    path = nav_path(context)
    if not path or not os.path.exists(path):
        _state.update(path=path, mesh=None, report=None, batches=None)
        return "No nav mesh yet: Build & Play makes one"
    mesh = load_nav(path)
    _state.update(path=path, mtime=os.path.getmtime(path), mesh=mesh, report=analyse(mesh), batches=None,
                  source="game", vis=None, owner=_owner(context))
    _redraw()
    return f"Loaded {len(mesh.areas)} nav areas"


def _owner(context=None):
    scene = (context or bpy.context).scene
    return (scene.name, scene.hammerless.map_name)


def set_predicted(mesh, owner=None) -> None:
    _state.update(path=None, mtime=None, mesh=mesh, report=analyse(mesh), batches=None, source="predicted", vis=None,
                  owner=owner or _owner())
    _redraw()


def source():
    return _state["source"]


def report():
    return _state["report"]


def mesh():
    return _state["mesh"]


def visibility_lists():
    """{area id: {visible area id: attributes}} for the shown nav, if it has been analyzed."""
    m = _state["mesh"]
    if m is None:
        return None
    if _state["vis"] is None:
        from ..core.navvis import expand_game_lists
        _state["vis"] = expand_game_lists(m) if any(a.visible or a.inherit_visibility for a in m.areas) else {}
    return _state["vis"]


def cursor_area(scale: float):
    """The nav area under the 3D cursor (the highest one below it), or None."""
    m = _state["mesh"]
    if m is None:
        return None
    p = bpy.context.scene.cursor.location * scale
    best = None
    for a in m.areas:
        if a.nw[0] <= p.x <= a.se[0] and a.nw[1] <= p.y <= a.se[1]:
            z = a.z_at(p.x, p.y)
            if z <= p.z + 40 and (best is None or z > best[0]):
                best = (z, a)
    return best[1] if best else None


SPAWN_SAFETY_RANGE = 550.0     # z_spawn_safety_range. Measured: the first zombies after leaving the start
                               # room were never closer than ~680 units walking (326 in a straight line)


def survivor_areas(mesh, viewer=None) -> list:
    """Where the survivors are: the area under the 3D cursor, or (None) the areas just outside the
    start room, where they stand when they leave it (the Director places its first zombies then)."""
    if viewer is not None:
        return [viewer]
    by = mesh.by_id()
    start = {a.id for a in mesh.areas if a.spawn_attributes & PLAYER_START}
    from_start = {c for sid in start for d in by[sid].connections for c in d}
    return [a for a in mesh.areas if a.id not in start
            and (a.id in from_start or any(c in start for d in a.connections for c in d))]


def spawn_reasons(mesh, viewer=None) -> dict:
    """{area id: "can_spawn" | "no_spawn" | "too_close" | "in_view"} for wandering zombies, with
    the survivors at survivor_areas(): the first rule that stops a spawn wins (no-spawn mark,
    walking distance under z_spawn_safety_range, seen by a survivor if the nav is analyzed)."""
    import heapq
    import math
    by = mesh.by_id()
    sources = survivor_areas(mesh, viewer)
    dist = {a.id: 0.0 for a in sources}
    heap = [(0.0, a.id) for a in sources]
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i] or d > SPAWN_SAFETY_RANGE:
            continue
        a = by[i]
        for side in a.connections:
            for j in side:
                b = by.get(j)
                if b is not None:
                    nd = d + math.dist(a.centre, b.centre)
                    if nd < dist.get(j, 1e18):
                        dist[j] = nd
                        heapq.heappush(heap, (nd, j))
    lists = visibility_lists() or {}
    seen = set()
    for s in sources:
        seen.update(k for k, v in lists.get(s.id, {}).items() if v)
    out = {}
    for a in mesh.areas:
        if a.spawn_attributes & EMPTY:
            out[a.id] = "no_spawn"
        elif dist.get(a.id, 1e18) < SPAWN_SAFETY_RANGE:
            out[a.id] = "too_close"
        elif a.id in seen:
            out[a.id] = "in_view"
        else:
            out[a.id] = "can_spawn"
    return out


def _area_color(a, rep, mode, viewer=None, reasons=None):
    if mode == "VIS":
        lists = visibility_lists() or {}
        if viewer is None:
            return COLORS["unseen"]
        if a.id == viewer.id:
            return COLORS["viewer"]
        v = lists.get(viewer.id, {}).get(a.id, 0)
        return {1: COLORS["seen_partly"], 2: COLORS["seen_complete"], 3: COLORS["seen_both"]}.get(v, COLORS["unseen"])
    if mode == "WHY" and viewer is not None and a.id == viewer.id:
        return COLORS["viewer"]
    if a.spawn_attributes & PLAYER_START:
        return COLORS["start"]
    if a.spawn_attributes & CHECKPOINT:
        return COLORS["end"]
    if mode == "WHY":
        return COLORS[reasons.get(a.id, "can_spawn")] if reasons else COLORS["plain"]
    if mode == "REACH":
        return COLORS["reach"] if a.id in rep.reachable else COLORS["unreach"]
    if mode == "AHEAD":
        n = (_state.get("ahead") or {}).get(a.id)
        if n is None:
            return COLORS["unseen"]                  # (off the route, or the nav isn't analyzed)
        return COLORS["no_spawn"] if n < 10 else COLORS["in_view"] if n < 30 else COLORS["can_spawn"]
    if mode == "SPAWN":
        attrs = a.spawn_attributes
        if attrs & EMPTY and attrs & NO_MOBS:
            return COLORS["no_spawn"]
        if attrs & EMPTY:
            return COLORS["no_wander"]
        if attrs & NO_MOBS:
            return COLORS["no_mobs"]
        return COLORS["obscured"] if attrs & OBSCURED else COLORS["plain"]
    # FLOW: heat by walking distance from the start room
    if a.id not in rep.distance:
        return COLORS["unreach"]
    far = rep.__dict__.get("_far")
    if far is None:
        far = rep.__dict__["_far"] = max(rep.distance.values()) or 1.0
    t = rep.distance[a.id] / far
    return (0.2 + 0.8 * t, 0.85 - 0.6 * t, 1.0 - 0.9 * t, 0.35)


def _zombie_boxes(plan, scale: float):
    """An upright box per simulated wanderer (zombie-sized: 24 x 24 x 64 units), and a white post where the
    survivors stand."""
    tris, cols = [], []

    def box(p, half, height, col):
        x, y, z = p
        lo = [Vector((x + dx, y + dy, z + LIFT)) / scale for dx, dy in ((-half, -half), (half, -half), (half, half),
                                                                       (-half, half))]
        hi = [v + Vector((0, 0, height / scale)) for v in lo]
        quads = [(lo[0], lo[1], hi[1], hi[0]), (lo[1], lo[2], hi[2], hi[1]), (lo[2], lo[3], hi[3], hi[2]),
                 (lo[3], lo[0], hi[0], hi[3]), (hi[0], hi[1], hi[2], hi[3])]
        for a, b, c, d in quads:
            tris.extend((a, b, c, a, c, d))
            cols.extend([col] * 6)
    for z in plan.zombies:
        box(z.pos, 12, 64, COLORS[f"zombie_{z.kind}"])
    if plan.survivors is not None:
        box(plan.survivors, 8, 140, COLORS["survivors"])
    return tris, cols


def zombie_plan():
    """The last simulated wanderers on screen (core/spawnsim.py), or None."""
    return _state.get("zombies")


def _build(scale: float, mode: str, links: bool, spots: bool = False, viewer=None, zombies=None):
    m, rep = _state["mesh"], _state["report"]
    reasons = spawn_reasons(m, viewer) if mode == "WHY" else None
    akey = (id(m), bool(m.analyzed), sum(len(a.visible or []) for a in m.areas[:50]))   # (Analyze changes it in place)
    if mode == "AHEAD" and _state.get("ahead_key") != akey:
        from ..core.zombiereport import analyse as zombie_report
        _state["ahead"], _state["ahead_key"] = zombie_report(m).ahead, akey
    tris, tri_cols, outline = [], [], []
    spot_lines = {"spot_cover": [], "spot_exposed": []}
    if spots:
        r = 8.0 / scale
        for a in m.areas:
            for h in a.hiding_spots:
                p = Vector((h.pos[0], h.pos[1], h.pos[2] + LIFT + 2)) / scale
                kind = "spot_cover" if h.flags & 1 else "spot_exposed"
                spot_lines[kind] += [p - Vector((r, 0, 0)), p + Vector((r, 0, 0)), p - Vector((0, r, 0)), p + Vector((0, r, 0)),
                                     p, p + Vector((0, 0, 2 * r))]
    for a in m.areas:
        c = [Vector((x, y, z + LIFT)) / scale for x, y, z in a.corners]
        col = _area_color(a, rep, mode, viewer, reasons)
        tris += [c[0], c[1], c[2], c[0], c[2], c[3]]
        tri_cols += [col] * 6
        for i in range(4):
            outline += [c[i], c[(i + 1) % 4]]
    link_lines = {"drop": [], "jump": [], "ladder": []}
    if links:
        for lad in m.ladders:        # a rail from bottom to top, with rungs
            b, t = Vector(lad.bottom) / scale, Vector(lad.top) / scale
            side = Vector((0.0, 0.0, 0.0))
            side[1 if lad.direction in (1, 3) else 0] = lad.width * 0.5 / scale
            link_lines["ladder"] += [b - side, t - side, b + side, t + side]
            steps = max(1, int((lad.top[2] - lad.bottom[2]) / 32))
            for i in range(steps + 1):
                p = b + (t - b) * (i / steps)
                link_lines["ladder"] += [p - side, p + side]
        by = m.by_id()
        for l in rep.links:
            if l.kind not in link_lines:
                continue
            a, b = Vector(by[l.a].centre) / scale, Vector(by[l.b].centre) / scale
            lift = Vector((0, 0, (LIFT + 6) / scale))
            a, b = a + lift, b + lift
            d = (b - a)
            if d.length < 1e-6:
                continue
            side = Vector((-d.y, d.x, 0)).normalized() * min(0.25, d.length * 0.2)
            tip = b - d.normalized() * min(0.4, d.length * 0.3)
            link_lines[l.kind] += [a, b, b, tip + side, b, tip - side]   # arrow towards b
    flat = gpu.shader.from_builtin("SMOOTH_COLOR")
    uni = gpu.shader.from_builtin("UNIFORM_COLOR")
    batches = {
        "fill": (flat, batch_for_shader(flat, "TRIS", {"pos": tris, "color": tri_cols})),
        "outline": (uni, batch_for_shader(uni, "LINES", {"pos": outline})),
    }
    if zombies is not None:
        ztris, zcols = _zombie_boxes(zombies, scale)
        if ztris:
            batches["zombies"] = (flat, batch_for_shader(flat, "TRIS", {"pos": ztris, "color": zcols}))
    for kind, pts in list(link_lines.items()) + list(spot_lines.items()):
        if pts:
            batches[kind] = (uni, batch_for_shader(uni, "LINES", {"pos": pts}))
    return batches


def _draw():
    s = bpy.context.scene.hammerless
    if not s.show_nav or _state["mesh"] is None or _state["owner"] != _owner():
        return                  # (a nav of another scene, map or file isn't drawn)
    use_cursor = s.nav_color_mode == "VIS" or (s.nav_color_mode == "WHY" and s.spawn_from == "CURSOR")
    viewer = cursor_area(s.units_per_meter) if use_cursor else None
    key = (s.units_per_meter, s.nav_color_mode, s.show_nav_links, s.show_hiding_spots, viewer.id if viewer else None,
           s.spawn_from, s.show_zombie_spawns, s.zombie_seed, round(s.zombie_progress, 3),
           s.dir_common_limit if s.director_enabled else 30)
    if _state["batches"] is None or _state["key"] != key:
        plan = None
        if s.show_zombie_spawns:
            from ..core.spawnsim import simulate
            plan = simulate(_state["mesh"], s.zombie_seed, s.zombie_progress,
                            common_limit=s.dir_common_limit if s.director_enabled else 30)
        _state["zombies"] = plan
        _state["batches"] = _build(s.units_per_meter, s.nav_color_mode, s.show_nav_links, s.show_hiding_spots, viewer,
                                   plan)
        _state["key"] = key
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL" if not s.nav_xray else "NONE")
    gpu.state.depth_mask_set(False)
    for name, (shader, batch) in _state["batches"].items():
        if name not in ("fill", "zombies"):
            shader.bind()
            shader.uniform_float("color", COLORS[name])
            gpu.state.line_width_set(2.5 if name in ("drop", "jump", "ladder", "spot_cover", "spot_exposed") else 1.0)
        batch.draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.depth_mask_set(True)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def _redraw():
    wm = bpy.context.window_manager
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _on_display_change(self, context):
    _state["batches"] = None
    _redraw()


class HL_OT_nav_load(bpy.types.Operator):
    bl_idname = "hammerless.nav_load"
    bl_label = "Show Game Nav Mesh"
    bl_description = ("Read the nav mesh the game generated for this map and draw it in the viewport: "
                      "where survivors can go, where the path to the end room breaks, drops and jump-ups")

    def execute(self, context):
        import struct
        try:
            msg = load(context)
        except (ValueError, struct.error, OSError) as ex:     # (an unknown version, or one being written)
            self.report({"ERROR"}, f"Couldn't read the game's nav mesh: {ex}")
            return {"CANCELLED"}
        context.scene.hammerless.show_nav = _state["mesh"] is not None
        self.report({"INFO"}, msg)
        if _state["report"] is not None:
            from .problems import store_nav
            store_nav(context, _state["mesh"], _state["report"])
        return {"FINISHED"}


class HL_OT_nav_predict(bpy.types.Operator):
    bl_idname = "hammerless.nav_predict"
    bl_label = "Build Navmesh"
    bl_description = ("Work out the nav mesh the game will generate, from this scene, without compiling "
                      "(our copy of the game's generator). Shows where survivors can go and where the path "
                      "from start to end breaks")

    _timer = None
    _thread = None
    _box: dict = {}

    @classmethod
    def poll(cls, context):
        return not _state["busy"]

    def execute(self, context):
        import threading
        from ..core.nav import collect_climbs, collect_regions
        from ..core.navworker import predict
        from .ops import build_map_text, game_root
        # the map exactly as Build exports it (gates are movers, volumes triggers...), so Build & Play
        # can reuse this navmesh
        ir, text, rep2 = build_map_text(context, game_root(context))
        if text is None:
            self.report({"ERROR"}, rep2.errors[0] if rep2.errors else "The map doesn't build")
            return {"CANCELLED"}
        regions, _ = collect_regions(ir)
        climbs, _ = collect_climbs(ir)
        box = {"stage": "Starting", "nodes": 0, "mesh": None, "error": None, "stop": False}
        self._box = box
        self._owner = _owner(context)
        wall_climbs = context.scene.hammerless.wall_climbs        # (read here, not in the thread)

        def work():
            try:
                box["mesh"] = predict(text, regions, lambda stage, n: _progress(box, stage, nodes=n), climbs,
                                      wall_climbs, lambda: box["stop"])     # (its own process: Blender stays quick)
            except Exception as ex:          # shown to the user
                box["error"] = str(ex)
        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()
        _state["busy"] = "build"
        _state["cancel"] = False
        _redraw()
        self._timer = context.window_manager.event_timer_add(0.3, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        _end_job(context, self._timer)

    def modal(self, context, event):
        if (event.type == "ESC" and event.value == "PRESS") or _state["cancel"]:
            self._box["stop"] = True
            _end_job(context, self._timer)
            self.report({"WARNING"}, "Navmesh build cancelled")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        box = self._box
        if self._thread.is_alive():
            context.workspace.status_text_set(f"Hammerless: building the navmesh: {box['stage']}... "
                                              f"({box['nodes']} nodes)")
            return {"PASS_THROUGH"}
        _end_job(context, self._timer)
        if box["error"] or box["mesh"] is None:
            self.report({"ERROR"}, f"Navmesh build failed: {box['error']}")
            return {"CANCELLED"}
        if _owner(context) != self._owner:
            self.report({"WARNING"}, "The scene or Map Name changed while the navmesh was being built: not shown")
            return {"CANCELLED"}
        finish_prediction(context, box["mesh"])
        for problem in box["mesh"].problems:
            self.report({"WARNING"}, problem)
        if box["mesh"].problems:
            from .ops import _quoted_object
            from .problems import add_rows
            add_rows(context, [("WARNING", p, _quoted_object(p), None) for p in box["mesh"].problems],
                     kind="navgen")
        rep = _state["report"]
        if not rep.end:
            self.report({"INFO"}, f"Built {rep.total} nav areas (no end safe room to check the path)")
        elif rep.end_reached:
            self.report({"INFO"}, f"Built {rep.total} nav areas: the path from start to end works")
        else:
            self.report({"WARNING"}, "Navmesh: survivors can't reach the end safe room (see the marker)")
        return {"FINISHED"}


class HL_OT_nav_analyze(bpy.types.Operator):
    bl_idname = "hammerless.nav_analyze"
    bl_label = "Analyze Navmesh"
    bl_description = ("Build the navmesh and run the game's nav analysis on it here (visibility between areas, "
                      "hiding spots). When the walls changed since the last build, it first compiles what the "
                      "analysis looks through (walls and visibility, not the lighting). Then colour the nav by "
                      "what the area under the 3D cursor can see. Build puts the result in the game")

    _timer = None
    _thread = None
    _box: dict = {}
    _job = None

    @classmethod
    def poll(cls, context):
        return not _state["busy"]

    def execute(self, context):
        import threading
        from ..core import compile as cc
        from ..core.navpredict import cached
        from ..core.navworker import analyze, predict
        from .ops import compile_options, export_vmf, game_root, surface_report, work_dir
        root = game_root(context)
        if not root:
            self.report({"ERROR"}, "Left 4 Dead 2 not found: set Settings > Game & Folders > L4D2 Folder to the 'Left 4 Dead 2' folder (the one with left4dead2.exe)")
            return {"CANCELLED"}
        base = os.path.join(work_dir(context), context.scene.hammerless.map_name)
        tools = cc.Tools(root)
        if tools.missing():
            self.report({"ERROR"}, "L4D2 Authoring Tools not installed (Steam > Library > Tools > "
                                   "Left 4 Dead 2 Authoring Tools): the analysis needs the compiled map")
            return {"CANCELLED"}
        if cc.compile_running(base + ".vmf"):
            self.report({"ERROR"}, "This map is still compiling: wait for it to finish, then Analyze again")
            return {"CANCELLED"}
        path, _root, rep = export_vmf(self, context)         # exactly what Build exports
        surface_report(self, rep)
        if not path:
            return {"CANCELLED"}
        with open(path, encoding="utf-8") as f:
            text = f.read()
        # the analysis looks through the compiled walls and visibility (never the lighting): compile
        # what's missing of those, without baking lighting or touching the game's copy of the map
        import dataclasses
        opts = compile_options(context.scene.hammerless)
        opts = dataclasses.replace(cc.PRESETS[opts] if isinstance(opts, str) else opts, rad="SKIP",
                                   assets=cc.asset_fingerprint(tools.gamedir, context.scene.hammerless.map_name))
        job = cc.CompileJob(tools, path, opts, copy_to_game=False, skip_if_unchanged=True)   # (as Build: same options)
        self._job = None
        if not job.up_to_date():
            try:
                self._job = job.start()
            except RuntimeError as ex:          # already compiling
                self.report({"ERROR"}, str(ex))
                return {"CANCELLED"}
        regions, climbs = rep.nav_regions, rep.nav_climbs
        compiled = threading.Event()
        if self._job is None:
            compiled.set()
        box = {"stage": "Building the navmesh", "mesh": None, "error": None, "compiled": compiled, "stop": False}
        self._box = box
        wall_climbs = context.scene.hammerless.wall_climbs

        def work():
            try:
                mesh = cached(text, regions, climbs, wall_climbs)      # Build Navmesh made this one already
                if mesh is None:
                    mesh = predict(text, regions, lambda stage, n: _progress(box, None), climbs,
                                   wall_climbs, lambda: box["stop"])     # needs no compiled map
                box["stage"] = "Compiling the map"
                compiled.wait()
                if box["error"]:
                    return
                analyze(mesh, text, base + ".bsp", root, lambda stage: _progress(box, stage),
                        lambda: box["stop"])
                box["mesh"] = mesh
            except Exception as ex:          # shown to the user
                box["error"] = str(ex)
        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()
        _state["busy"] = "analyze"
        _state["cancel"] = False
        _redraw()
        self._owner = _owner(context)
        self._settings = (context.scene.hammerless.map_name, context.scene.hammerless.nav_source)
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        self._stop(context)

    def _stop(self, context):
        box = self._box
        box["stop"] = True
        if self._job is not None and not self._job.done:
            self._job.cancel()                    # (the compile it started stops too)
        if box.get("compiled") is not None and not box["compiled"].is_set():
            box["error"] = box.get("error") or "stopped"
            box["compiled"].set()                 # let the thread end instead of waiting for ever
        _end_job(context, self._timer)

    def modal(self, context, event):
        if (event.type == "ESC" and event.value == "PRESS") or _state["cancel"]:
            self._stop(context)
            self.report({"WARNING"}, "Nav analysis cancelled")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        box = self._box
        job = self._job
        if job is not None and not box["compiled"].is_set():
            new = job.poll()
            if new:
                from .ops import write_log
                write_log(new, append=True)
                context.workspace.status_text_set(f"Hammerless: compiling for the analysis: {new[-1][:100]}")
            if job.done:
                if job.failed:
                    summ = job.summary
                    box["error"] = ("the map leaks (use Load Leak to see where)" if summ and summ.leaked else
                                    summ.errors[0] if summ and summ.errors else "the compile failed, see the log")
                box["compiled"].set()
            return {"PASS_THROUGH"}
        if self._thread.is_alive():
            context.workspace.status_text_set(f"Hammerless: analyzing the navmesh: {box['stage']}...")
            return {"PASS_THROUGH"}
        _end_job(context, self._timer)
        if box["error"] or box["mesh"] is None:
            self.report({"ERROR"}, f"Nav analysis failed: {box['error']}")
            return {"CANCELLED"}
        mesh = box["mesh"]
        s = context.scene.hammerless
        map_name, _nav_source = self._settings          # the map this analysis was made for
        if _owner(context) != self._owner:
            self.report({"WARNING"}, f"Analyzed '{map_name}' (the scene or Map Name changed meanwhile, so it isn't "
                                     "shown here)")
            return {"FINISHED"}
        finish_prediction(context, mesh)
        s.nav_color_mode = "VIS"
        spots = sum(len(a.hiding_spots) for a in mesh.areas)
        done = ("Compiled the walls and visibility and analyzed" if job is not None and not job.skipped
                else "Analyzed")
        self.report({"INFO"}, f"{done} {len(mesh.areas)} areas: {spots} hiding spots. Put the 3D cursor "
                              "on an area (Shift + right-click) to see what it can see")
        return {"FINISHED"}


def _progress(box, stage, **more) -> None:
    """A nav thread's progress: also where it stops when cancelled (at its next stage)."""
    if box["stop"]:
        raise RuntimeError("cancelled")
    if stage is not None:
        box.update(stage=stage, **more)


class HL_OT_nav_cancel(bpy.types.Operator):
    bl_idname = "hammerless.nav_cancel"
    bl_label = "Cancel"
    bl_description = "Stop building or analyzing the navmesh (Esc does it too)"

    def execute(self, context):
        _state["cancel"] = True
        return {"FINISHED"}


def _end_job(context, timer) -> None:
    _state["busy"] = None
    _state["cancel"] = False
    _redraw()
    if timer is not None:
        try:
            context.window_manager.event_timer_remove(timer)
        except Exception:
            pass
    try:
        context.workspace.status_text_set(None)
    except Exception:
        pass


def finish_prediction(context, mesh) -> None:
    set_predicted(mesh, _owner(context))
    context.scene.hammerless.show_nav = True
    from .problems import store_nav
    store_nav(context, mesh, _state["report"], predicted=True)


_disk = {"key": None, "time": 0.0, "value": None}


def _nav_on_disk(context) -> str | None:
    """None / "nav" / "analyzed": this map's nav mesh in the game folder (checked at most once a second)."""
    import time
    from ..core import compile as cc
    from .ops import game_root
    key = context.scene.hammerless.map_name
    now = time.monotonic()
    if _disk["key"] == key and now - _disk["time"] < 1.0:
        return _disk["value"]
    root = game_root(context)
    value = None
    if root:
        analyzed = cc.nav_analyzed(cc.Tools(root), key)
        value = None if analyzed is None else ("analyzed" if analyzed else "nav")
    _disk.update(key=key, time=now, value=value)
    return value


def built_shown() -> bool:
    """A navmesh built from the scene (Build Navmesh / Analyze) is on screen."""
    return _state["mesh"] is not None and _state["source"] == "predicted"


def analysis_shown() -> bool:
    m = _state["mesh"]
    return built_shown() and (m.analyzed or any(a.visible or a.inherit_visibility or a.hiding_spots for a in m.areas))


class HL_OT_nav_clear(bpy.types.Operator):
    bl_idname = "hammerless.nav_clear"
    bl_label = "Clear Navmesh"
    bl_description = ("Delete this map's nav mesh (and its analysis) and remove it from the viewport: the next "
                      "Build or Build & Play makes a new one")

    @classmethod
    def poll(cls, context):
        return not _state["busy"]

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        from ..core import compile as cc
        from ..core import navpredict
        from .ops import game_root
        navpredict._last.update(key=None, mesh=None)
        _state.update(path=None, mtime=None, mesh=None, report=None, batches=None, key=None, source=None, vis=None)
        if context.scene.hammerless.nav_color_mode == "VIS":
            context.scene.hammerless.nav_color_mode = "REACH"
        _redraw()
        root = game_root(context)
        locked = cc.clear_nav(cc.Tools(root), context.scene.hammerless.map_name) if root else []
        if locked:
            self.report({"WARNING"}, f"Couldn't delete {', '.join(locked)}: the game has the map loaded. Load "
                                     "another map (or close the game) and clear again")
        else:
            self.report({"INFO"}, "Navmesh cleared: the next Build makes a new one")
        return {"FINISHED"}


class HL_OT_nav_clear_analysis(bpy.types.Operator):
    bl_idname = "hammerless.nav_clear_analysis"
    bl_label = "Clear Analysis"
    bl_description = ("Remove the visibility data and hiding spots from this map's nav mesh (the nav mesh itself "
                      "stays): the next Build or Build & Play analyzes it again")

    @classmethod
    def poll(cls, context):
        return not _state["busy"]

    def execute(self, context):
        m = _state["mesh"]
        if m is not None:
            for a in m.areas:
                a.visible, a.inherit_visibility, a.hiding_spots = [], 0, []
            m.analyzed = False
        _state.update(batches=None, vis=None)
        if context.scene.hammerless.nav_color_mode == "VIS":
            context.scene.hammerless.nav_color_mode = "REACH"
        _redraw()
        from ..core import compile as cc
        from .ops import game_root
        root = game_root(context)
        try:
            done = bool(root) and cc.clear_nav_analysis(cc.Tools(root), context.scene.hammerless.map_name)
        except OSError:
            self.report({"WARNING"}, "Couldn't change the nav mesh: the game has the map loaded. Load another map "
                                     "(or close the game) and clear again")
            return {"FINISHED"}
        self.report({"INFO"}, "Analysis cleared: the next Build analyzes the nav mesh again" if done
                    else "Analysis cleared from the view")
        return {"FINISHED"}


def header_status() -> str:
    """What the preview shows, for its header."""
    if _state["report"] is None:
        return ""
    return "from the scene" if _state["source"] == "predicted" else "the game's"


_LEGEND = {
    "REACH": ["Green: survivors can reach it. Red: they can't"],
    "FLOW": ["Blue near the start, red far along the path"],
    "WHY": ["Green: wanderers can spawn. Red: no-spawn mark",
            f"Orange: under {SPAWN_SAFETY_RANGE:.0f} units' walk. Yellow: in view"],
    "VIS": ["From the area under the 3D cursor (white):", "green fully visible, yellow partly, grey not"],
    "AHEAD": ["Hidden spawn spots ahead of survivors standing here:",
              "red under 10 (dead zone: add cover), yellow under 30, green plenty"],
}


def draw_buttons(layout, context):
    """Build / show / analyze (in the Nav Mesh panel, above the Preview): built from the scene, or the game's (each
    clears what it made: small buttons beside them)."""
    rep = _state["report"]
    if _state["busy"]:
        row = layout.row()
        row.scale_y = 1.2
        row.alert = True
        row.operator("hammerless.nav_cancel", text="Cancel", icon="CANCEL")
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator("hammerless.nav_predict", text="Build Nav Mesh", icon="VIEWZOOM")
    row.operator("hammerless.nav_load", text="Show Game's Nav", icon="MOD_MESHDEFORM")
    on_disk = _nav_on_disk(context)
    if rep is not None or on_disk:
        row.operator("hammerless.nav_clear", text="", icon="TRASH")
    row = layout.row(align=True)
    if analysis_shown():
        row.operator("hammerless.nav_analyze", text="Analyzed", icon="CHECKMARK")
    else:
        row.operator("hammerless.nav_analyze", text="Analyze (visibility, hiding spots)", icon="HIDE_OFF")
    if analysis_shown() or on_disk == "analyzed":
        row.operator("hammerless.nav_clear_analysis", text="", icon="X")


def draw_panel(layout, context):
    s = context.scene.hammerless
    rep = _state["report"]
    if rep is None:
        layout.label(text="Build Nav Mesh or Show Game's Nav (above) to see it here")
        return
    box = layout.box()
    col = box.column(align=True)
    if not rep.end:
        col.label(text="No end safe room in the nav", icon="INFO")
    elif rep.end_reached:
        far = max((rep.distance[e] for e in rep.end if e in rep.distance), default=0)
        col.label(text=f"Start to end: OK ({far:.0f} units)", icon="CHECKMARK")
    else:
        r = col.row()
        r.alert = True
        r.label(text="Start to end: BROKEN", icon="ERROR")
    col.label(text=f"{rep.total} areas, {len(rep.reachable)} reachable", icon="BLANK1")
    if rep.unreachable:
        r = col.row()
        r.alert = True
        r.label(text=f"{rep.unreachable} unreachable, in {len(rep.islands)} island(s)", icon="BLANK1")
    drops = sum(l.kind == "drop" for l in rep.links)
    jumps = sum(l.kind == "jump" for l in rep.links)
    col.label(text=f"{drops} drops, {jumps} jumps / climbs, {len(_state['mesh'].ladders)} ladders", icon="BLANK1")
    col = layout.column(align=True)
    col.prop(s, "nav_color_mode", text="")
    if s.nav_color_mode == "WHY":
        col.prop(s, "spawn_from")
    row = col.row(align=True)
    row.prop(s, "show_nav_links", toggle=True)
    row.prop(s, "show_hiding_spots", toggle=True)
    row.prop(s, "nav_xray", toggle=True)
    zbox = layout.box()
    zbox.prop(s, "show_zombie_spawns", icon="GHOST_ENABLED")
    if s.show_zombie_spawns:
        row = zbox.row(align=True)
        row.prop(s, "zombie_seed")
        row.operator("hammerless.zombie_reroll", text="Reroll", icon="FILE_REFRESH")
        zbox.prop(s, "zombie_progress", slider=True)
        plan = zombie_plan()
        col = zbox.column(align=True)
        col.scale_y = 0.8
        if plan is None or not plan.budget:
            col.label(text="Needs a start and an end safe room joined by the nav", icon="INFO")
        else:
            col.label(text=f"{plan.budget} wanderers (route {plan.route:.0f} units x 0.03; Max Common Infected "
                           f"only caps how many are alive)", icon="INFO")
            col.label(text=f"Red, appear now: {plan.count('now')}   Grey, waiting: {plan.count('later')}", icon="BLANK1")
            lost = plan.count("lost")
            if lost:
                r = col.row()
                r.alert = lost > plan.budget * 0.25
                r.label(text=f"Purple, lost: {lost} (" + ", ".join(f"{n} {why}" for why, n in
                                                                  sorted(plan.why_lost.items(), key=lambda x: -x[1]))
                        + ")", icon="BLANK1")
            col.label(text="A simulation of the Director's rule, not the game itself", icon="BLANK1")
    lines = list(_LEGEND.get(s.nav_color_mode, ["Red: no zombies. Pink: no wanderers. Violet: no hordes",
                                                "Orange: Zombie Spawn Area. Light blue: normal"]))
    if s.nav_color_mode in ("WHY", "VIS", "AHEAD") and not visibility_lists():
        lines.append("(Analyze to also show what's in view)")
    lines.append("Blue: start room. Purple: end room")
    legend = layout.column(align=True)
    legend.scale_y = 0.8
    for i, line in enumerate(lines):
        legend.label(text=line, icon="INFO" if i == 0 else "BLANK1")


class HL_OT_zombie_reroll(bpy.types.Operator):
    bl_idname = "hammerless.zombie_reroll"
    bl_label = "Reroll Zombies"
    bl_description = "Another roll of the Director's dice: where its wandering commons would go with a new seed"

    def execute(self, context):
        context.scene.hammerless.zombie_seed += 1
        return {"FINISHED"}


CLASSES = (HL_OT_nav_load, HL_OT_nav_predict, HL_OT_nav_analyze, HL_OT_nav_clear, HL_OT_nav_clear_analysis, HL_OT_nav_cancel,
           HL_OT_zombie_reroll)


@bpy.app.handlers.persistent
def _forget_on_load(*_args):
    """Another .blend was opened: its nav isn't the one on screen."""
    _state.update(path=None, mtime=None, mesh=None, report=None, batches=None, key=None, source=None, vis=None,
                  owner=None)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(_forget_on_load)
    _handlers.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW"))


def unregister():
    if _forget_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_forget_on_load)
    for h in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
    _handlers.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
