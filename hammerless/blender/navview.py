"""Show the game's real nav mesh (maps/<map>.nav) in the viewport, with what the
analysis found: areas survivors can reach, the break in the path to the end room,
islands, drops and jump-ups."""
import math
import os

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..core.navanalysis import CHECKPOINT, OBSCURED, PLAYER_START, analyse
from ..core.navfile import load_nav

_state = {"path": None, "mtime": None, "mesh": None, "report": None, "batches": None, "key": None,
          "source": None}         # "game" (read from maps/<map>.nav) or "predicted" (our generator)
_handlers = []

COLORS = {
    "start": (0.25, 0.55, 1.0, 0.45), "end": (0.75, 0.35, 1.0, 0.45),
    "reach": (0.2, 0.85, 0.35, 0.3), "unreach": (0.95, 0.1, 0.05, 0.55),
    "obscured": (1.0, 0.6, 0.1, 0.4), "plain": (0.55, 0.75, 0.95, 0.25),
    "drop": (1.0, 0.55, 0.1, 1.0), "jump": (0.1, 0.9, 1.0, 1.0), "outline": (0.05, 0.05, 0.05, 0.6),
    "break": (1.0, 0.1, 0.1, 1.0), "ladder": (1.0, 0.9, 0.1, 1.0),
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
                  source="game")
    _redraw()
    return f"Loaded {len(mesh.areas)} nav areas"


def set_predicted(mesh) -> None:
    _state.update(path=None, mtime=None, mesh=mesh, report=analyse(mesh), batches=None, source="predicted")
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


def _area_color(a, rep, mode):
    if a.spawn_attributes & PLAYER_START:
        return COLORS["start"]
    if a.spawn_attributes & CHECKPOINT:
        return COLORS["end"]
    if mode == "REACH":
        return COLORS["reach"] if a.id in rep.reachable else COLORS["unreach"]
    if mode == "SPAWN":
        return COLORS["obscured"] if a.spawn_attributes & OBSCURED else COLORS["plain"]
    # FLOW: heat by walking distance from the start room
    if a.id not in rep.distance:
        return COLORS["unreach"]
    far = max(rep.distance.values()) or 1.0
    t = rep.distance[a.id] / far
    return (0.2 + 0.8 * t, 0.85 - 0.6 * t, 1.0 - 0.9 * t, 0.35)


def _build(scale: float, mode: str, links: bool):
    m, rep = _state["mesh"], _state["report"]
    tris, tri_cols, outline = [], [], []
    for a in m.areas:
        c = [Vector((x, y, z + LIFT)) / scale for x, y, z in a.corners]
        col = _area_color(a, rep, mode)
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
    for kind, pts in link_lines.items():
        if pts:
            batches[kind] = (uni, batch_for_shader(uni, "LINES", {"pos": pts}))
    return batches


def _draw():
    s = bpy.context.scene.hammerless
    if not s.show_nav or _state["mesh"] is None:
        return
    key = (s.units_per_meter, s.nav_color_mode, s.show_nav_links)
    if _state["batches"] is None or _state["key"] != key:
        _state["batches"] = _build(s.units_per_meter, s.nav_color_mode, s.show_nav_links)
        _state["key"] = key
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL" if not s.nav_xray else "NONE")
    gpu.state.depth_mask_set(False)
    for name, (shader, batch) in _state["batches"].items():
        if name != "fill":
            shader.bind()
            shader.uniform_float("color", COLORS[name])
            gpu.state.line_width_set(2.5 if name in ("drop", "jump", "ladder") else 1.0)
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
    bl_label = "Predict Nav Mesh"
    bl_description = ("Work out the nav mesh the game will generate, from this scene, without compiling "
                      "(our copy of the game's generator). Shows where survivors can go and where the path "
                      "from start to end breaks")

    _timer = None
    _thread = None
    _box: dict = {}

    def execute(self, context):
        import threading
        from ..core.build import Report, build_vmf
        from ..core.nav import collect_climbs, collect_regions
        from ..core.navpredict import predict
        from .extract import extract_scene
        rep = Report()
        ir, _ = extract_scene(context, rep, None)
        if rep.errors:
            self.report({"ERROR"}, rep.errors[0])
            return {"CANCELLED"}
        text, rep2 = build_vmf(ir, None)
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
            context.workspace.status_text_set(f"Hammerless: predicting the nav mesh: {box['stage']}... "
                                              f"({box['nodes']} nodes)")
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        if box["error"] or box["mesh"] is None:
            self.report({"ERROR"}, f"Nav prediction failed: {box['error']}")
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
            self.report({"INFO"}, f"Predicted {rep.total} nav areas (no end safe room to check the path)")
        elif rep.end_reached:
            self.report({"INFO"}, f"Predicted {rep.total} nav areas: the path from start to end works")
        else:
            self.report({"WARNING"}, "Predicted nav: survivors can't reach the end safe room (see the marker)")
        return {"FINISHED"}


def finish_prediction(context, mesh) -> None:
    set_predicted(mesh)
    context.scene.hammerless.show_nav = True
    from .problems import store_nav
    store_nav(context, mesh, _state["report"], predicted=True)


def draw_panel(layout, context):
    s = context.scene.hammerless
    rep = _state["report"]
    big = layout.row()
    big.scale_y = 1.3
    big.operator("hammerless.nav_predict", text="Predict Nav Mesh", icon="VIEWZOOM")
    row = layout.row(align=True)
    row.operator("hammerless.nav_load", text="Show the Game's Nav Mesh", icon="MOD_MESHDEFORM")
    if rep is None:
        col = layout.column(align=True)
        col.scale_y = 0.8
        col.label(text="Predict: before building, from this scene", icon="INFO")
        col.label(text="Game's: what the last Build & Play made", icon="BLANK1")
        return
    row.prop(s, "show_nav", text="", icon="HIDE_OFF" if s.show_nav else "HIDE_ON")
    layout.label(text="Showing: predicted from the scene" if _state["source"] == "predicted"
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
    row.prop(s, "nav_xray", toggle=True)
    legend = layout.column(align=True)
    legend.scale_y = 0.8
    if s.nav_color_mode == "REACH":
        legend.label(text="Green: survivors can reach it. Red: they can't", icon="INFO")
    elif s.nav_color_mode == "FLOW":
        legend.label(text="Blue near the start, red far along the path", icon="INFO")
    else:
        legend.label(text="Orange: Zombie Spawn Area (OBSCURED)", icon="INFO")
    legend.label(text="Blue: start room. Purple: end room", icon="BLANK1")
    if s.show_nav_links:
        legend.label(text="Arrows: orange drop-down, cyan jump-up", icon="BLANK1")


CLASSES = (HL_OT_nav_load, HL_OT_nav_predict)


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
