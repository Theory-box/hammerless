"""Automatic acoustics in Blender: ray traces the scene (its walls, floors and terrain) for
core/acoustics.py, adds the soundscapes to the map on export, and draws the result in View > Sound.
"""
import time

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..core import acoustics

_state = {"spots": None, "zones": None, "summary": "", "batches": None, "scale": None}
_handlers = []
COLOURS = {"OUTDOOR": (0.35, 0.65, 1.0, 0.55), "SHELTERED": (0.3, 0.9, 0.6, 0.55), "INDOOR": (1.0, 0.6, 0.2, 0.55)}


def _solid(obj) -> bool:
    from ..core.collision import SOLID_BRUSH_ENTITIES
    from .extract import effective_role
    role = effective_role(obj)
    if role in ("BRUSH", "TERRAIN"):
        return True
    return role == "BRUSH_ENTITY" and (obj.hammerless.classname or "func_detail") in SOLID_BRUSH_ENTITIES


def scene_bvh(context):
    """The scene's solid geometry (walls, floors, terrain, solid brush entities) in Hammer units."""
    scale = context.scene.hammerless.units_per_meter
    depsgraph = context.evaluated_depsgraph_get()
    verts, polys = [], []
    for obj in context.scene.objects:
        if obj.type != "MESH" or not obj.visible_get() or not _solid(obj):
            continue
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            m = obj.matrix_world
            base = len(verts)
            verts += [tuple((m @ v.co) * scale) for v in mesh.vertices]
            polys += [tuple(base + i for i in p.vertices) for p in mesh.polygons]
        finally:
            ev.to_mesh_clear()
    if not polys:
        return None, None
    v = np.array(verts)
    return BVHTree.FromPolygons(verts, polys), (tuple(v.min(axis=0)), tuple(v.max(axis=0)))


START_CLASSES = ("info_survivor_position", "info_player_start")


def scene_starts(context):
    """Where survivors start (Hammer units): the scene's survivor spawn objects."""
    from .extract import effective_role
    scale = context.scene.hammerless.units_per_meter
    return [tuple(o.matrix_world.translation * scale) for o in context.scene.objects
            if effective_role(o) == "ENTITY" and o.hammerless.classname in START_CLASSES]


def trace(context, starts=None):
    """Run the analysis. Returns (spots, zones, spacing, summary) or raises ValueError."""
    t0 = time.time()
    bvh, bounds = scene_bvh(context)
    if bvh is None:
        raise ValueError("Nothing to trace: the scene has no walls or floors")

    def cast(origin, direction, distance):
        hit = bvh.ray_cast(Vector(origin), Vector(direction), distance)
        return None if hit[0] is None else (hit[3], hit[1].z)
    spots, spacing = acoustics.sample_spots(bounds, cast)
    if not spots:
        raise ValueError("No floors found to listen from")
    spots = acoustics.reachable(spots, scene_starts(context) if starts is None else starts)
    acoustics.analyse(spots, cast)
    zl = acoustics.zones(spots, spacing)
    counts = {k: sum(s.kind == k for s in spots) for k in ("OUTDOOR", "SHELTERED", "INDOOR")}
    n = len(spots)
    summary = (f"{n} listening spots: {counts['OUTDOOR'] * 100 // n}% outdoors, {counts['SHELTERED'] * 100 // n}% "
               f"sheltered, {counts['INDOOR'] * 100 // n}% indoors; {len(zl)} zones, "
               f"{sum(len(z.positions) for z in zl)} sound portals ({time.time() - t0:.1f} s)")
    _state.update(spots=spots, zones=zl, summary=summary, batches=None)
    _redraw()
    return spots, zl, spacing, summary


def add_acoustics(context, ir, rep) -> None:
    """On export: the soundscapes (file + entities) for the map, per the World > Sound setting."""
    s = context.scene.hammerless
    if s.sound_mode == "OFF":
        return
    try:
        starts = [e.origin for e in ir.entities if e.classname in START_CLASSES and e.origin is not None]
        _spots, zl, spacing, summary = trace(context, starts)
    except ValueError as ex:
        rep.warnings.append(f"Sound: {ex}")
        return
    theme = {"URBAN": "URBAN"}.get(s.sound_mode)
    ir.entities += acoustics.entities(zl, s.map_name, spacing)
    ir.extra_scripts[f"scripts/soundscapes_{s.map_name}.txt"] = acoustics.soundscape_file(s.map_name, theme, zl)
    rep.info.append("Sound: " + summary)


# ---------------------------------------------------------------- View > Sound

def _build(scale):
    smooth = gpu.shader.from_builtin("SMOOTH_COLOR")
    uni = gpu.shader.from_builtin("UNIFORM_COLOR")
    pos, col = [], []
    h = acoustics.SPACING * 0.45
    for s in _state["spots"]:
        x, y, _z = s.pos
        z = s.floor_z + 2
        quad = [(x - h, y - h, z), (x + h, y - h, z), (x + h, y + h, z), (x - h, y + h, z)]
        pos += [quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]]
        col += [COLOURS[s.kind]] * 6
    lines = []
    for z in _state["zones"]:
        for p in z.positions:
            x, y, zz = p
            lines += [(x, y, zz - 64), (x, y, zz + 48), (x - 24, y, zz), (x + 24, y, zz), (x, y - 24, zz), (x, y + 24, zz)]
    p = (np.array(pos) / scale).astype(np.float32)
    _state["batches"] = (
        (smooth, batch_for_shader(smooth, "TRIS", {"pos": p, "color": np.array(col, np.float32)})),
        (uni, batch_for_shader(uni, "LINES", {"pos": (np.array(lines) / scale).astype(np.float32)})) if lines else None)
    _state["scale"] = scale


def _draw():
    s = bpy.context.scene.hammerless
    if not s.show_sound or not _state["spots"]:
        return
    if _state["batches"] is None or _state["scale"] != s.units_per_meter:
        _build(s.units_per_meter)
    mvp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    fill, lines = _state["batches"]
    fill[0].bind()
    fill[0].uniform_float("ModelViewProjectionMatrix", mvp)
    fill[1].draw(fill[0])
    if lines:
        gpu.state.depth_test_set("NONE")
        gpu.state.line_width_set(3.0)
        lines[0].bind()
        lines[0].uniform_float("ModelViewProjectionMatrix", mvp)
        lines[0].uniform_float("color", (1.0, 0.95, 0.2, 1.0))
        lines[1].draw(lines[0])
        gpu.state.line_width_set(1.0)
    gpu.state.depth_mask_set(True)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def _redraw():
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def on_display_change(self, context):
    _redraw()


class HL_OT_sound_trace(bpy.types.Operator):
    bl_idname = "hammerless.sound_trace"
    bl_label = "Trace Sound"
    bl_description = ("Ray trace the scene from every floor: which spots are outdoors, sheltered or indoors, and "
                      "where the outside sound comes in (what Build puts in the map's soundscapes)")

    def execute(self, context):
        try:
            _spots, _zones, _spacing, summary = trace(context)
        except ValueError as ex:
            self.report({"WARNING"}, str(ex))
            return {"CANCELLED"}
        context.scene.hammerless.show_sound = True
        self.report({"INFO"}, summary)
        return {"FINISHED"}


def _note(layout, lines, icon="INFO"):
    col = layout.column(align=True)
    col.scale_y = 0.8
    for i, line in enumerate(lines):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def draw_panel(layout, context):
    s = context.scene.hammerless
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator("hammerless.sound_trace", icon="OUTLINER_OB_SPEAKER")
    row.prop(s, "show_sound", text="", icon="HIDE_OFF" if s.show_sound else "HIDE_ON")
    if not _state["spots"]:
        _note(layout, ["Where the map is outdoors, sheltered or", "indoors, as Build's soundscapes see it"])
        return
    summary = _state["summary"]
    _note(layout, [summary[i:i + 46] for i in range(0, len(summary), 46)])
    _note(layout, ["Blue: outdoors. Green: sheltered.", "Orange: indoors. Yellow crosses: where",
                   "the outside sound comes in (sound portals)"], icon="BLANK1")


CLASSES = (HL_OT_sound_trace,)


@bpy.app.handlers.persistent
def _forget_on_load(*_args):
    _state.update(spots=None, zones=None, summary="", batches=None)


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
