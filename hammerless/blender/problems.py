"""Map check results in Blender: a clickable list in the panel and numbered
markers in the viewport, like Hammer's Check for Problems + Go to Error, but
before compiling."""
import math
import re

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .props import set_problem_index

_PART = re.compile(r" \(part \d+\)$")
_QUOTED = re.compile(r"'([^']+)'")
_handlers = []


def store(context, rep, new_build: bool = False) -> None:
    """Put a build/check Report's findings into the scene's problem list. The game's own
    report from the last Build & Play is kept until the next build."""
    s = context.scene.hammerless
    scale = s.units_per_meter
    if new_build:
        stale_flow(context, "New build started")
    kept = [] if new_build else [(p.name, p.severity, tuple(p.location), p.has_location, p.kind, p.stale)
                                 for p in s.problems if p.ingame]
    s.problems.clear()
    if new_build:
        s["ingame_flow"] = ""
    for name, severity, loc, has_loc, kind, stale in kept:
        item = s.problems.add()
        item.name, item.severity, item.location, item.has_location, item.ingame = name, severity, loc, has_loc, True
        item.kind, item.stale = kind, stale
    located = {p.message for p in rep.problems}
    rows = [("ERROR", m, "", None) for m in rep.errors if m not in located]
    rows += [(p.severity, p.message, p.source, p.location) for p in rep.problems]
    rows += [("WARNING", m, "", None) for m in rep.warnings if m not in located]
    for severity, message, source, loc in rows:
        item = s.problems.add()
        item.name, item.severity = message, severity
        if not source:   # messages name objects in quotes: select the first one that exists
            source = next((q for q in _QUOTED.findall(message)
                           if bpy.data.objects.get(_PART.sub("", q))), "")
        item.source = _PART.sub("", source)
        if loc is not None:
            item.location = Vector(loc) / scale
            item.has_location = True
    s.problems_checked = True
    set_problem_index(s, -1)      # no jump on refill
    _redraw(context)


def add_rows(context, rows, kind: str = "") -> None:
    """Append (severity, message, object name, location in Hammer units or None) to the list. With a
    kind, earlier rows of that kind are replaced (e.g. the last Build Navmesh's warnings)."""
    s = context.scene.hammerless
    if kind:
        for n in reversed(range(len(s.problems))):
            if s.problems[n].kind == kind:
                s.problems.remove(n)
    for severity, message, source, loc in rows:
        item = s.problems.add()
        item.name, item.severity, item.source, item.kind = message, severity, _PART.sub("", source or ""), kind
        if loc is not None:
            item.location = Vector(loc) / s.units_per_meter
            item.has_location = True
    s.problems_checked = True
    _redraw(context)


INGAME_BROKEN = ("The saved nav has no start-to-end route with recognized elevator and ladder links. "
                 "Inspect the main route and boarding areas, rebuild nav, then Build & Play. "
                 "This check does not identify a particular blocking object.")


def stale_flow(context, reason="Scene changed; Build & Play to test it again"):
    """Retire old game claims and markers instead of presenting them as current failures."""
    s = context.scene.hammerless
    s["game_test_revision"] = s.get("game_test_revision", 0) + 1
    s["game_test_pending"] = False
    had = bool(s.get("ingame_flow", ""))
    for p in s.problems:
        if p.ingame and not p.stale:
            p.name = "Previous game test (stale): " + p.name
            p.stale, p.has_location = True, False
            p.source, p.severity = "", "INFO"
            had = True
    if had:
        s["ingame_flow"] = "Previous game test is stale. " + reason + "."


def store_flow(report: dict) -> None:
    from ..core.flowdiagnostics import message
    scene = bpy.context.scene
    s = scene.hammerless
    s["game_test_pending"] = False
    for n in reversed(range(len(s.problems))):
        if s.problems[n].kind == "flow":
            s.problems.remove(n)
    severity, text = message(report)
    s["ingame_flow"] = (f"In game: Director flow works ({report['length']:.0f} units)"
                         if report["state"] == "ok" else "In game: " + text.split(". ")[0])
    if report["state"] != "ok":
        item = s.problems.add()
        item.name, item.severity, item.ingame, item.kind = text, severity, True, "flow"
        # A nearest reached area is not the obstruction. Never send users to an arbitrary dead end.
        item.has_location = False
        s.problems.move(len(s.problems) - 1, 0)
    set_problem_index(s, -1)
    _redraw(bpy.context)


PREDICTED_BROKEN = ("The generated nav has no route from the start to the end room. Recognized elevator "
                    "travel links are included. Check the main route for "
                    "missing floor nav, an unconnected ledge, or a blocked path. Elevator controls and doors "
                    "still need to allow travel in-game")


def store_nav(context, mesh, rep, predicted: bool = False) -> None:
    """Problems found in a nav mesh (the game's, or our prediction): replaces earlier nav rows."""
    import math
    s = context.scene.hammerless
    scale = s.units_per_meter
    for n in reversed(range(len(s.problems))):
        if s.problems[n].kind == "nav" or (s.problems[n].ingame and not s.problems[n].kind):
            s.problems.remove(n)
    by = mesh.by_id()
    rows = []
    if not rep.start:
        rows.append(("ERROR", "The nav has no start safe room (no area is marked as the start): add a Start "
                     "Safe Room, or check its box covers the room's floor", None))
    if rep.end and not rep.end_reached and rep.break_area is not None:
        rows.append(("ERROR", PREDICTED_BROKEN if predicted else INGAME_BROKEN, None))
    goal = None
    if rep.end:
        goal = tuple(sum(by[e].centre[i] for e in rep.end) / len(rep.end) for i in range(3))
    for group in rep.islands[:3]:
        if len(group) < 4:
            continue
        # islands past a broken path are just "the rest of the map": say so once, above
        if goal and any(e in group for e in rep.end):
            continue
        centre = tuple(sum(by[i].centre[k] for i in group) / len(group) for k in range(3))
        rows.append(("WARNING", f"{len(group)} nav areas here can't be reached from the start, and zombies there "
                     "can't get down to the rest of the map (tops of walls, closed-off spots). Zombies can spawn "
                     "there and never reach survivors. Make it unwalkable "
                     "(player clip, a sloped or skybox top) or connect it", centre))
    # why there may be few zombies (core/zombiereport.py: the Director's measured rules)
    from ..core.zombiereport import analyse as zombie_report
    custom = s.director_enabled
    zr = zombie_report(mesh, s.dir_mob_interval_min if custom else 90, s.dir_mob_interval_max if custom else 180,
                       hordes_off=custom and s.dir_no_mobs, wanderers_off=custom and s.dir_no_wanderers)
    for severity, msg, loc in zr.rows:
        rows.append(("WARNING" if severity == "WARNING" else "INFO", "Zombies: " + msg, loc))
    for spot, dz in rep.dead_ledges[:12]:
        rows.append(("WARNING", f"A {dz:.0f}-unit ledge that bots and zombies can't use either way: L4D2 only links "
                     "a step of 18 or less, or a drop-down over 64 (one way down, can't climb back). Make it a "
                     "drop of 65+ for a drop-down, or 18 or less to walk it", spot))
    for severity, msg, loc in reversed(rows):
        item = s.problems.add()
        item.name, item.severity, item.ingame, item.kind = msg, severity, not predicted, "nav"
        if loc is not None:
            item.location = Vector(loc) / scale + Vector((0, 0, 0.3))
            item.has_location = True
        s.problems.move(len(s.problems) - 1, 0)
    s.problems_checked = True
    set_problem_index(s, -1)
    _redraw(context)


def go_to_problem(context, index: int) -> None:
    s = context.scene.hammerless
    if not 0 <= index < len(s.problems):
        return
    p = s.problems[index]
    obj = bpy.data.objects.get(p.source)
    if obj is not None and obj.visible_get():
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
    if p.has_location:
        context.scene.cursor.location = p.location
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            with context.temp_override(window=window, area=area, region=region):
                if p.has_location:
                    bpy.ops.view3d.view_center_cursor()
                elif obj is not None and obj.visible_get():
                    bpy.ops.view3d.view_selected()


def _redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _markers():
    s = bpy.context.scene.hammerless
    if not s.show_problem_markers:
        return []
    return [(n + 1, Vector(p.location), p.severity) for n, p in enumerate(s.problems) if p.has_location]


def _draw_3d():
    marks = _markers()
    if not marks:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.line_width_set(3.0)
    gpu.state.depth_test_set("NONE")
    size = 0.4 * max(0.25, bpy.context.scene.hammerless.units_per_meter / 64.0)
    for _n, loc, severity in marks:
        color = (1.0, 0.2, 0.15, 1.0) if severity == "ERROR" else (1.0, 0.75, 0.1, 1.0)
        pts = []
        for a, b in (((-1, 0, 0), (1, 0, 0)), ((0, -1, 0), (0, 1, 0)), ((0, 0, 0), (0, 0, 6))):
            pts += [loc + Vector(a) * size, loc + Vector(b) * size]
        ring = [loc + Vector((size * 1.6 * math.cos(t * math.pi / 4), size * 1.6 * math.sin(t * math.pi / 4), 0))
                for t in range(9)]
        for i in range(8):
            pts += [ring[i], ring[i + 1]]
        batch = batch_for_shader(shader, "LINES", {"pos": [tuple(p) for p in pts]})
        shader.uniform_float("color", color)
        batch.draw(shader)
    gpu.state.line_width_set(1.0)


def _draw_2d():
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    marks = _markers()
    if not marks:
        return
    region, rv3d = bpy.context.region, bpy.context.region_data
    size = bpy.context.scene.hammerless.units_per_meter / 64.0
    for n, loc, severity in marks:
        xy = location_3d_to_region_2d(region, rv3d, loc + Vector((0, 0, 6 * 0.4 * max(0.25, size))))
        if xy is None:
            continue
        blf.size(0, 16)
        blf.color(0, 1.0, 0.35 if severity == "ERROR" else 0.8, 0.2, 1.0)
        blf.position(0, xy.x + 6, xy.y + 4, 0)
        blf.draw(0, f"#{n}")


class HL_UL_problems(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        icon = {"ERROR": "ERROR", "WARNING": "ERROR", "INFO": "INFO"}.get(item.severity, "DOT")
        row = layout.row(align=True)
        if item.severity == "ERROR":
            row.alert = True
        num = f"#{index + 1} " if item.has_location else ""
        row.label(text=num + item.name, icon=icon)


def draw_panel(layout, context):
    """Problem list section for the main panel."""
    from .spawn import _text
    s = context.scene.hammerless
    box = layout
    head = box.row(align=True)
    flow = s.get("ingame_flow", "")
    head.operator("hammerless.validate", text="Check the Map", icon="VIEWZOOM")
    head.prop(s, "show_problem_markers", text="", icon="HIDE_OFF" if s.show_problem_markers else "HIDE_ON")
    if not s.problems_checked:
        box.label(text="Not checked yet: Check, or Build, lists them here")
    elif not s.problems:
        box.label(text="No problems found", icon="CHECKMARK")
    if flow:
        box.label(text=flow, icon="CHECKMARK" if flow.startswith("In game: Director flow works") else "INFO" if flow.startswith("Previous") else "ERROR")
    if s.problems:
        box.template_list("HL_UL_problems", "", s, "problems", s, "problem_index",
                          rows=min(max(len(s.problems), 2), 6))
        if 0 <= s.problem_index < len(s.problems):
            width = max(24, int(context.region.width / (7.2 * context.preferences.view.ui_scale)))
            _text(box, s.problems[s.problem_index].name, width)
        else:
            box.label(text="Click a problem to go to it", icon="RESTRICT_SELECT_OFF")


CLASSES = (HL_UL_problems,)


@bpy.app.handlers.persistent
def _scene_changed(scene, depsgraph):
    if not hasattr(scene, "hammerless"):
        return
    s = scene.hammerless
    if not (s.get("game_test_pending") or str(s.get("ingame_flow", "")).startswith("In game:")):
        return
    kinds = (bpy.types.Object, bpy.types.Mesh, bpy.types.Light, bpy.types.Material, bpy.types.Collection, bpy.types.NodeTree)
    if any(isinstance(u.id, kinds) for u in depsgraph.updates):
        # Use the handler's scene even if a different one is currently active.
        from types import SimpleNamespace
        stale_flow(SimpleNamespace(scene=scene))


@bpy.app.handlers.persistent
def _file_loaded(_):
    for scene in bpy.data.scenes:
        if hasattr(scene, "hammerless"):
            from types import SimpleNamespace
            stale_flow(SimpleNamespace(scene=scene), "Reopened file; Build & Play for a fresh game test")


def _registration_ready():
    # Addon registration runs under RestrictBlend: scene data is only safe afterward.
    _file_loaded(None)
    return None


def register():
    bpy.app.handlers.depsgraph_update_post.append(_scene_changed)
    bpy.app.handlers.load_post.append(_file_loaded)
    bpy.app.timers.register(_registration_ready, first_interval=0.0)
    for c in CLASSES:
        bpy.utils.register_class(c)
    _handlers.append((bpy.types.SpaceView3D.draw_handler_add(_draw_3d, (), "WINDOW", "POST_VIEW"), "WINDOW"))
    _handlers.append((bpy.types.SpaceView3D.draw_handler_add(_draw_2d, (), "WINDOW", "POST_PIXEL"), "WINDOW"))


def unregister():
    if bpy.app.timers.is_registered(_registration_ready):
        bpy.app.timers.unregister(_registration_ready)
    for handlers, fn in ((bpy.app.handlers.depsgraph_update_post, _scene_changed), (bpy.app.handlers.load_post, _file_loaded)):
        if fn in handlers:
            handlers.remove(fn)
    for h, region in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(h, region)
    _handlers.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
