"""Map check results in Blender: a clickable list in the panel and numbered
markers in the viewport, like Hammer's Check for Problems + Go to Error, but
before compiling."""
import math
import re

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

_PART = re.compile(r" \(part \d+\)$")
_QUOTED = re.compile(r"'([^']+)'")
_handlers = []


def store(context, rep, new_build: bool = False) -> None:
    """Put a build/check Report's findings into the scene's problem list. The game's own
    report from the last Build & Play is kept until the next build."""
    s = context.scene.hammerless
    scale = s.units_per_meter
    kept = [] if new_build else [(p.name, p.severity, tuple(p.location), p.has_location, p.kind)
                                 for p in s.problems if p.ingame]
    s.problems.clear()
    if new_build:
        s["ingame_flow"] = ""
    for name, severity, loc, has_loc, kind in kept:
        item = s.problems.add()
        item.name, item.severity, item.location, item.has_location, item.ingame = name, severity, loc, has_loc, True
        item.kind = kind
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
    s["problem_index"] = -1      # no jump on refill
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


INGAME_BROKEN = ("In the game, survivors can't get from the start to the end safe room: its nav mesh "
                 "stops at the marker. Look there for a drop the nav doesn't connect, a gap, a step "
                 "that's too tall or a blocked path. Without this path no zombies wander")
INGAME_CONNECTED = ("In the game, the nav mesh reaches the end safe room but the Director still has no "
                    "start-to-end path. Check the end room's Next Map and both landmarks")


def store_flow(report: dict) -> None:
    """Show the game's flow report (see gamefiles.READY_SCRIPT) in the problem list."""
    scene = bpy.context.scene
    s = scene.hammerless
    for n in reversed(range(len(s.problems))):
        if s.problems[n].kind == "flow":
            s.problems.remove(n)
    if report["state"] == "broken":
        item = s.problems.add()
        item.name = INGAME_CONNECTED if report.get("connected") else INGAME_BROKEN
        item.severity, item.ingame, item.kind = "ERROR", True, "flow"
        if not report.get("connected"):
            item.location = Vector(report["location"]) / s.units_per_meter
            item.has_location = True
        s.problems.move(len(s.problems) - 1, 0)
    s["problem_index"] = -1
    _redraw(bpy.context)


PREDICTED_BROKEN = ("Survivors won't be able to get from the start to the end safe room: the nav mesh the "
                    "game will generate (built from the scene) stops at the marker. Look there for a gap, "
                    "a ledge between 19 and 64 units (L4D2 links neither way) or a blocked path. Without this "
                    "path no zombies wander")


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
        # mark the spot of the last reachable area nearest the end room (where the path stops),
        # not its middle: areas can be hundreds of units across
        a = by[rep.break_area]
        goal = [sum(by[e].centre[i] for e in rep.end) / len(rep.end) for i in range(3)]
        x = min(max(goal[0], a.nw[0]), a.se[0])
        y = min(max(goal[1], a.nw[1]), a.se[1])
        rows.append(("ERROR", PREDICTED_BROKEN if predicted else INGAME_BROKEN, (x, y, a.z_at(x, y))))
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
    for spot, dz in rep.dead_ledges[:12]:
        rows.append(("WARNING", f"A {dz:.0f}-unit ledge that bots and zombies can't use either way: L4D2 only links "
                     "a step of 18 or less, or a drop-down over 64 (one way down, can't climb back). Make it a "
                     "drop of 65+ for a drop-down, or 18 or less to walk it", spot))
    for severity, msg, loc in reversed(rows):
        item = s.problems.add()
        item.name, item.severity, item.ingame, item.kind = msg, severity, True, "nav"
        if loc is not None:
            item.location = Vector(loc) / scale + Vector((0, 0, 0.3))
            item.has_location = True
        s.problems.move(len(s.problems) - 1, 0)
    s.problems_checked = True
    s["problem_index"] = -1
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
    box = layout.box()
    head = box.row(align=True)
    errors = sum(p.severity == "ERROR" for p in s.problems)
    flow = s.get("ingame_flow", "")
    if not s.problems_checked:
        head.label(text="Map not checked yet", icon="QUESTION")
    elif not s.problems:
        head.label(text="No problems found", icon="CHECKMARK")
    else:
        count = head.row()
        count.alert = errors > 0
        count.label(text=f"{len(s.problems)} problem{'s' if len(s.problems) != 1 else ''}", icon="ERROR")
    head.prop(s, "show_problem_markers", text="", icon="HIDE_OFF" if s.show_problem_markers else "HIDE_ON")
    head.operator("hammerless.validate", text="Check", icon="VIEWZOOM")
    if flow:
        box.label(text=flow, icon="CHECKMARK" if flow.startswith("In game: path") else "ERROR")
    if s.problems:
        box.template_list("HL_UL_problems", "", s, "problems", s, "problem_index",
                          rows=min(max(len(s.problems), 2), 6))
        if 0 <= s.problem_index < len(s.problems):
            width = max(24, int(context.region.width / (7.2 * context.preferences.view.ui_scale)))
            _text(box, s.problems[s.problem_index].name, width)
        else:
            box.label(text="Click a problem to go to it", icon="RESTRICT_SELECT_OFF")


CLASSES = (HL_UL_problems,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    _handlers.append((bpy.types.SpaceView3D.draw_handler_add(_draw_3d, (), "WINDOW", "POST_VIEW"), "WINDOW"))
    _handlers.append((bpy.types.SpaceView3D.draw_handler_add(_draw_2d, (), "WINDOW", "POST_PIXEL"), "WINDOW"))


def unregister():
    for h, region in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(h, region)
    _handlers.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
