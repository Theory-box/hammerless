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
          "vis": None}            # expanded visibility lists {area id: {area id: attributes}} (analyzed navs)
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
                  source="game", vis=None)
    _redraw()
    return f"Loaded {len(mesh.areas)} nav areas"


def set_predicted(mesh) -> None:
    _state.update(path=None, mtime=None, mesh=mesh, report=analyse(mesh), batches=None, source="predicted", vis=None)
    _redraw()


def source():
    return _state["source"]


def report():
    return _state["report"]


def mesh():
    return _state["mesh"]


def area_location(area_id: int, scale: float) -> Vector | None:
    m = _state["mesh"]
    if m is None:
        return None
    a = m.by_id().get(area_id)
    return Vector(a.centre) / scale + Vector((0, 0, 0.3)) if a else None


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
    out = []
    for a in mesh.areas:
        if a.id in start:
            continue
        linked = any(c in start for d in a.connections for c in d) or any(
            a.id in by[c].connections[d] for c in start for d in range(4))
        if linked:
            out.append(a)
    return out


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
    far = max(rep.distance.values()) or 1.0
    t = rep.distance[a.id] / far
    return (0.2 + 0.8 * t, 0.85 - 0.6 * t, 1.0 - 0.9 * t, 0.35)


def _build(scale: float, mode: str, links: bool, spots: bool = False, viewer=None):
    m, rep = _state["mesh"], _state["report"]
    reasons = spawn_reasons(m, viewer) if mode == "WHY" else None
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
    for kind, pts in list(link_lines.items()) + list(spot_lines.items()):
        if pts:
            batches[kind] = (uni, batch_for_shader(uni, "LINES", {"pos": pts}))
    return batches


def _draw():
    s = bpy.context.scene.hammerless
    if not s.show_nav or _state["mesh"] is None:
        return
    use_cursor = s.nav_color_mode == "VIS" or (s.nav_color_mode == "WHY" and s.spawn_from == "CURSOR")
    viewer = cursor_area(s.units_per_meter) if use_cursor else None
    key = (s.units_per_meter, s.nav_color_mode, s.show_nav_links, s.show_hiding_spots, viewer.id if viewer else None,
           s.spawn_from)
    if _state["batches"] is None or _state["key"] != key:
        _state["batches"] = _build(s.units_per_meter, s.nav_color_mode, s.show_nav_links, s.show_hiding_spots, viewer)
        _state["key"] = key
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL" if not s.nav_xray else "NONE")
    gpu.state.depth_mask_set(False)
    for name, (shader, batch) in _state["batches"].items():
        if name != "fill":
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
        msg = load(context)
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

    def execute(self, context):
        import threading
        from ..core.nav import collect_climbs, collect_regions
        from ..core.navpredict import predict
        from .ops import build_map_text, game_root
        # the map exactly as Build exports it (gates are movers, volumes triggers...), so Build & Play
        # can reuse this navmesh
        ir, text, rep2 = build_map_text(context, game_root(context))
        if text is None:
            self.report({"ERROR"}, rep2.errors[0] if rep2.errors else "The map doesn't build")
            return {"CANCELLED"}
        regions, _ = collect_regions(ir)
        climbs, _ = collect_climbs(ir)
        box = {"stage": "Starting", "nodes": 0, "mesh": None, "error": None}
        self._box = box

        def work():
            try:
                box["mesh"] = predict(text, regions, lambda stage, n: box.update(stage=stage, nodes=n), climbs,
                                      context.scene.hammerless.wall_climbs)
            except Exception as ex:          # shown to the user
                box["error"] = str(ex)
        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()
        self._timer = context.window_manager.event_timer_add(0.3, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        box = self._box
        if self._thread.is_alive():
            context.workspace.status_text_set(f"Hammerless: building the navmesh: {box['stage']}... "
                                              f"({box['nodes']} nodes)")
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        if box["error"] or box["mesh"] is None:
            self.report({"ERROR"}, f"Navmesh build failed: {box['error']}")
            return {"CANCELLED"}
        finish_prediction(context, box["mesh"])
        for problem in box["mesh"].problems:
            self.report({"WARNING"}, problem)
        if box["mesh"].problems:
            from .ops import _quoted_object
            from .problems import add_rows
            add_rows(context, [("WARNING", p, _quoted_object(p), None) for p in box["mesh"].problems])
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
                      "hiding spots), against the last compiled map. Then colour the nav by what the area under "
                      "the 3D cursor can see")

    _timer = None
    _thread = None
    _box: dict = {}

    def execute(self, context):
        import threading
        from ..core.buildplan import plan
        from ..core.nav import collect_climbs, collect_regions
        from ..core.navpredict import predict
        from .ops import build_map_text, game_root, work_dir
        root = game_root(context)
        if not root:
            self.report({"ERROR"}, "Left 4 Dead 2 not found. Set the L4D2 Folder in the Hammerless panel")
            return {"CANCELLED"}
        ir, text, rep2 = build_map_text(context, root)       # exactly what Build exports
        if text is None:
            self.report({"ERROR"}, rep2.errors[0] if rep2.errors else "The map doesn't build")
            return {"CANCELLED"}
        base = os.path.join(work_dir(context), context.scene.hammerless.map_name)
        try:
            with open(base + ".built.vmf", encoding="utf-8") as f:
                built = f.read()
        except OSError:
            built = None
        if built is None or not os.path.exists(base + ".bsp"):
            self.report({"ERROR"}, "The map hasn't been compiled yet: click Build (or Build & Play), then Analyze. "
                                   "(Build Navmesh doesn't compile the map)")
            return {"CANCELLED"}
        if plan(built, text)[0] == "full":
            import time
            when = time.strftime("%b %d %H:%M", time.localtime(os.path.getmtime(base + ".built.vmf")))
            self.report({"ERROR"}, f"Walls or floors changed since the map was last compiled ({when}): click Build "
                                   "(or Build & Play) to compile it, then Analyze. (Build Navmesh doesn't compile "
                                   "the map)")
            return {"CANCELLED"}
        regions, _ = collect_regions(ir)
        climbs, _ = collect_climbs(ir)
        box = {"stage": "Building the navmesh", "mesh": None, "error": None}
        self._box = box
        wall_climbs = context.scene.hammerless.wall_climbs

        def work():
            try:
                from ..core.navanalyze import analyze
                from ..core.vpk import GameContent
                mesh = predict(text, regions, None, climbs, wall_climbs)
                analyze(mesh, text, base + ".bsp", GameContent(root), os.path.join(root, "left4dead2"),
                        progress=lambda stage: box.update(stage=stage))
                box["mesh"] = mesh
            except Exception as ex:          # shown to the user
                box["error"] = str(ex)
        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        box = self._box
        if self._thread.is_alive():
            context.workspace.status_text_set(f"Hammerless: analyzing the navmesh: {box['stage']}...")
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        if box["error"] or box["mesh"] is None:
            self.report({"ERROR"}, f"Nav analysis failed: {box['error']}")
            return {"CANCELLED"}
        finish_prediction(context, box["mesh"])
        context.scene.hammerless.nav_color_mode = "VIS"
        spots = sum(len(a.hiding_spots) for a in box["mesh"].areas)
        self.report({"INFO"}, f"Analyzed {len(box['mesh'].areas)} areas: {spots} hiding spots. Put the 3D cursor "
                              "on an area (Shift + right-click) to see what it can see")
        return {"FINISHED"}


def finish_prediction(context, mesh) -> None:
    set_predicted(mesh)
    context.scene.hammerless.show_nav = True
    from .problems import store_nav
    store_nav(context, mesh, _state["report"], predicted=True)


def built_shown() -> bool:
    """A navmesh built from the scene (Build Navmesh / Analyze) is on screen."""
    return _state["mesh"] is not None and _state["source"] == "predicted"


def analysis_shown() -> bool:
    m = _state["mesh"]
    return built_shown() and (m.analyzed or any(a.visible or a.inherit_visibility or a.hiding_spots for a in m.areas))


class HL_OT_nav_clear(bpy.types.Operator):
    bl_idname = "hammerless.nav_clear"
    bl_label = "Clear Navmesh"
    bl_description = ("Remove the navmesh built from the scene (and its analysis) from the viewport, and forget it, "
                      "so the next Build Navmesh or Build & Play makes a fresh one")

    def execute(self, context):
        from ..core import navpredict
        navpredict._last.update(key=None, mesh=None)
        _state.update(path=None, mtime=None, mesh=None, report=None, batches=None, key=None, source=None, vis=None)
        if context.scene.hammerless.nav_color_mode == "VIS":
            context.scene.hammerless.nav_color_mode = "REACH"
        _redraw()
        self.report({"INFO"}, "Navmesh cleared")
        return {"FINISHED"}


class HL_OT_nav_clear_analysis(bpy.types.Operator):
    bl_idname = "hammerless.nav_clear_analysis"
    bl_label = "Clear Analysis"
    bl_description = "Remove the visibility data and hiding spots from the shown navmesh (the navmesh itself stays)"

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
        self.report({"INFO"}, "Analysis cleared")
        return {"FINISHED"}


def draw_panel(layout, context):
    s = context.scene.hammerless
    rep = _state["report"]
    big = layout.row()
    big.scale_y = 1.3
    if built_shown():
        big.operator("hammerless.nav_clear", text="Clear Navmesh", icon="X")
    else:
        big.operator("hammerless.nav_predict", text="Build Navmesh", icon="VIEWZOOM")
    row = layout.row(align=True)
    row.operator("hammerless.nav_load", text="Show the Game's Nav Mesh", icon="MOD_MESHDEFORM")
    if analysis_shown():
        layout.operator("hammerless.nav_clear_analysis", text="Clear Analysis", icon="X")
    else:
        layout.operator("hammerless.nav_analyze", text="Analyze Navmesh (visibility, hiding spots)", icon="HIDE_OFF")
    if rep is None:
        col = layout.column(align=True)
        col.scale_y = 0.8
        col.label(text="Build Navmesh: from this scene, no compile", icon="INFO")
        col.label(text="Game's: what the last Build & Play made", icon="BLANK1")
        return
    row.prop(s, "show_nav", text="", icon="HIDE_OFF" if s.show_nav else "HIDE_ON")
    layout.label(text="Showing: built from the scene" if _state["source"] == "predicted"
                 else "Showing: the game's nav mesh", icon="RESTRICT_VIEW_OFF")
    box = layout.box()
    col = box.column(align=True)
    if not rep.end:
        col.label(text="No end safe room marked in the nav", icon="INFO")
    elif rep.end_reached:
        far = max((rep.distance[e] for e in rep.end if e in rep.distance), default=0)
        col.label(text=f"Path start to end: OK ({far:.0f} units)", icon="CHECKMARK")
    else:
        r = col.row()
        r.alert = True
        r.label(text="Path start to end: BROKEN", icon="ERROR")
    col.label(text=f"{rep.total} areas, {len(rep.reachable)} reachable from the start")
    if rep.unreachable:
        col.label(text=f"{rep.unreachable} unreachable in {len(rep.islands)} island(s)", icon="GHOST_DISABLED")
    drops = sum(l.kind == "drop" for l in rep.links)
    jumps = sum(l.kind == "jump" for l in rep.links)
    col.label(text=f"{drops} drop-downs, {jumps} jump-ups / climbs, {len(_state['mesh'].ladders)} ladders")
    col = layout.column(align=True)
    col.prop(s, "nav_color_mode", text="")
    row = col.row(align=True)
    row.prop(s, "show_nav_links", toggle=True)
    row.prop(s, "show_hiding_spots", toggle=True)
    row.prop(s, "nav_xray", toggle=True)
    legend = layout.column(align=True)
    legend.scale_y = 0.8
    if s.nav_color_mode == "REACH":
        legend.label(text="Green: survivors can reach it. Red: they can't", icon="INFO")
    elif s.nav_color_mode == "FLOW":
        legend.label(text="Blue near the start, red far along the path", icon="INFO")
    elif s.nav_color_mode == "WHY":
        legend.prop(s, "spawn_from")
        legend.label(text="Green: wandering zombies can spawn", icon="INFO")
        legend.label(text="Red: no-spawn mark (EMPTY)", icon="BLANK1")
        legend.label(text=f"Orange: too close, under {SPAWN_SAFETY_RANGE:.0f} units walking", icon="BLANK1")
        if visibility_lists():
            legend.label(text="Yellow: a survivor can see it", icon="BLANK1")
        else:
            legend.label(text="(Analyze Navmesh to also show what's in view)", icon="BLANK1")
        legend.label(text="Nothing spawns until someone leaves the start room", icon="BLANK1")
    elif s.nav_color_mode == "VIS":
        if not visibility_lists():
            legend.label(text="No visibility in this nav: Analyze Navmesh, or show the game's nav", icon="INFO")
        else:
            legend.label(text="From the area under the 3D cursor (white):", icon="INFO")
            legend.label(text="green completely visible, yellow partly, grey not", icon="BLANK1")
    else:
        legend.label(text="Red: no zombies spawn (EMPTY + NO_MOBS)", icon="INFO")
        legend.label(text="Pink: no wanderers (EMPTY). Violet: no hordes (NO_MOBS)", icon="BLANK1")
        legend.label(text="Orange: Zombie Spawn Area (OBSCURED). Light blue: normal", icon="BLANK1")
        legend.label(text="An area takes a box's marks when its centre is inside it", icon="BLANK1")
    legend.label(text="Blue: start room. Purple: end room", icon="BLANK1")
    if s.show_nav_links:
        legend.label(text="Arrows: orange drop-down, cyan jump-up", icon="BLANK1")


CLASSES = (HL_OT_nav_load, HL_OT_nav_predict, HL_OT_nav_analyze, HL_OT_nav_clear, HL_OT_nav_clear_analysis)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    _handlers.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW"))


def unregister():
    for h in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
    _handlers.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
