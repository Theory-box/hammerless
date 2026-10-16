"""Automatic acoustics in Blender: ray traces the scene (its walls, floors and terrain) for
core/acoustics.py, adds the soundscapes to the map on export, and draws the result in the Sound panel.
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


def scene_geometry(context):
    """The scene's solid geometry (walls, floors, terrain, solid brush entities) in Hammer units, as arrays:
    (vertices (n, 3), each polygon's corners one after another, each polygon's corner count)."""
    scale = context.scene.hammerless.units_per_meter
    depsgraph = context.evaluated_depsgraph_get()
    verts, corners, sizes, base = [], [], [], 0
    from .mapcollection import map_objects
    inside = map_objects(context.scene)
    for obj in context.scene.objects:
        if obj.name not in inside or obj.type != "MESH" or not obj.visible_get() or not _solid(obj):
            continue
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            co = np.empty(len(mesh.vertices) * 3, np.float32)
            mesh.vertices.foreach_get("co", co)
            # (as mathutils works out matrix_world @ co * scale: single precision products summed in double;
            # the listening spots' grid starts at the bounds, so a last-bit difference moves every spot)
            m = np.array(obj.matrix_world, dtype=np.float32)
            prod = co.reshape(-1, 1, 3) * m[None, :3, :3]
            w = ((prod[..., 0].astype(np.float64) + prod[..., 1]) + prod[..., 2]) + m[:3, 3].astype(np.float64)
            verts.append(w.astype(np.float32) * np.float32(scale))
            idx = np.empty(len(mesh.loops), np.int32)
            mesh.loops.foreach_get("vertex_index", idx)
            start = np.empty(len(mesh.polygons), np.int32)
            total = np.empty(len(mesh.polygons), np.int32)
            mesh.polygons.foreach_get("loop_start", start)
            mesh.polygons.foreach_get("loop_total", total)
            if len(total):           # (each polygon's corners in polygon order)
                corners.append(idx[np.repeat(start, total) + np.arange(total.sum()) - np.repeat(np.cumsum(total) - total, total)] + base)
                sizes.append(total)
            base += len(mesh.vertices)
        finally:
            ev.to_mesh_clear()
    if not sizes:
        return None
    return np.concatenate(verts), np.concatenate(corners), np.concatenate(sizes)


def scene_bvh(context, geometry=None):
    """The scene's solid geometry (scene_geometry) as a BVH tree, and its bounds; (None, None) when there is none."""
    geometry = scene_geometry(context) if geometry is None else geometry
    if geometry is None:
        return None, None
    v, corners, sizes = geometry
    polys = [c.tolist() for c in np.split(corners, np.cumsum(sizes)[:-1])]
    return BVHTree.FromPolygons(v.tolist(), polys), (tuple(map(float, v.min(axis=0))),
                                                     tuple(map(float, v.max(axis=0))))


# The last analysis, kept: it depends only on the solid geometry, the starts and the Sound volumes, so a build
# where they didn't change (e.g. only logic) reuses it. {"key": ..., "result": (spots, zones, spacing, summary)}
_TRACE_CACHE: dict = {"key": None, "result": None}


START_CLASSES = ("info_survivor_position", "info_player_start")


def scene_starts(context):
    """Where survivors start (Hammer units): the scene's survivor spawn objects."""
    from .extract import effective_role
    scale = context.scene.hammerless.units_per_meter
    from .mapcollection import map_objects
    inside = map_objects(context.scene)
    return [tuple(o.matrix_world.translation * scale) for o in context.scene.objects
            if o.name in inside and effective_role(o) == "ENTITY" and o.hammerless.classname in START_CLASSES]


def scene_volumes(context, aspect: str) -> list:
    """The map's Control Volumes (and older volumes) ticked for an aspect, from the scene (Hammer units)."""
    from ..core.control import of, volumes
    from ..core.entities import CONTROL, NAV_CUT, NO_BAKE
    from ..core.ir import Entity, MapIR
    from .extract import mesh_to_brushes, object_keyvalues
    from .mapcollection import map_objects
    inside = map_objects(context.scene)
    objs = [o for o in context.scene.objects if o.name in inside and o.type == "MESH" and o.visible_get()
            and o.hammerless.classname in (CONTROL, NAV_CUT, NO_BAKE)]
    if not objs:
        return []
    depsgraph = context.evaluated_depsgraph_get()
    upm = context.scene.hammerless.units_per_meter

    class _NoMaterials:
        def resolve(self, mat):
            return "tools/toolsskip", 0.25, 16
    ents = [Entity(o.hammerless.classname, None, (0, 0, 0), object_keyvalues(o),
                   mesh_to_brushes(o, depsgraph, upm, _NoMaterials()), o.name) for o in objs]
    return of(volumes(MapIR(entities=ents)), aspect)


def trace(context, starts=None):
    """Run the analysis. Returns (spots, zones, spacing, summary) or raises ValueError."""
    res = trace_work(trace_read(context, starts))
    _redraw()
    return res


def trace_read(context, starts=None):
    """The part of trace that reads the scene (Blender's thread): the geometry as arrays, the volumes, the starts."""
    import hashlib
    t0 = time.time()
    geometry = scene_geometry(context)
    if geometry is None:
        raise ValueError("Nothing to trace: the scene has no walls or floors")
    vols = scene_volumes(context, "sound")
    starts = scene_starts(context) if starts is None else starts
    h = hashlib.sha1(repr((sorted(tuple(round(c, 3) for c in p) for p in starts), repr(vols))).encode())
    for a in geometry:
        h.update(a.astype(np.float32 if a.dtype.kind == "f" else np.int32).tobytes())
    return geometry, vols, starts, h.hexdigest(), t0


def trace_work(args):
    """The rest of trace: the rays and the analysis (no Blender data: mathutils only, any thread)."""
    geometry, vols, starts, key, t0 = args
    if _TRACE_CACHE["key"] == key:
        spots, zl, spacing, summary = _TRACE_CACHE["result"]
        _state.update(spots=spots, zones=zl, summary=summary, batches=None)
        return spots, zl, spacing, summary
    bvh, bounds = scene_bvh(None, geometry)

    def cast(origin, direction, distance):
        hit = bvh.ray_cast(Vector(origin), Vector(direction), distance)
        return None if hit[0] is None else (hit[3], hit[1].z)
    spots, spacing = acoustics.sample_spots(bounds, cast)
    if vols:                        # (Control Volumes ticked Sound: not listened from there)
        from ..core.control import skipped
        spots = [sp for sp in spots if not skipped(vols, "sound", sp.pos)]
    if not spots:
        raise ValueError("No floors found to listen from")
    spots = acoustics.reachable(spots, starts)
    if not spots:
        raise ValueError("No floor can be reached from the survivors' start: check the start is on a floor")
    acoustics.analyse(spots, cast)
    zl = acoustics.zones(spots, spacing)
    counts = {k: sum(s.kind == k for s in spots) for k in ("OUTDOOR", "SHELTERED", "INDOOR")}
    n = len(spots)
    summary = (f"{n} listening spots: {counts['OUTDOOR'] * 100 // n}% outdoors, {counts['SHELTERED'] * 100 // n}% "
               f"sheltered, {counts['INDOOR'] * 100 // n}% indoors; {len(zl)} zones, "
               f"{sum(len(z.positions) for z in zl)} sound portals ({time.time() - t0:.1f} s)")
    _state.update(spots=spots, zones=zl, summary=summary, batches=None)
    _TRACE_CACHE.update(key=key, result=(spots, zl, spacing, summary))
    return spots, zl, spacing, summary


def add_acoustics(context, ir, rep, defer: bool = False) -> None:
    """On export: the soundscapes (file + entities) for the map, per the Sound panel's setting. defer: only the
    scene is read here; finish_acoustics (on the export's other thread) does the rest."""
    s = context.scene.hammerless
    if s.sound_mode == "OFF":
        return
    try:
        starts = [e.origin for e in ir.entities if e.classname in START_CLASSES and e.origin is not None]
        args = trace_read(context, starts)
    except ValueError as ex:
        rep.warnings.append(f"Sound: {ex}")
        return
    ir.__dict__["_acoustics"] = (args, s.map_name, {"URBAN": "URBAN"}.get(s.sound_mode))
    if not defer:
        finish_acoustics(ir, rep)
        _redraw()


def finish_acoustics(ir, rep) -> None:
    """add_acoustics' rays and analysis, and the soundscapes they make (no Blender data: any thread)."""
    job = ir.__dict__.pop("_acoustics", None)
    if job is None:
        return
    args, map_name, theme = job
    try:
        _spots, zl, spacing, summary = trace_work(args)
    except ValueError as ex:
        rep.warnings.append(f"Sound: {ex}")
        return
    ir.entities += acoustics.entities(zl, map_name, spacing)
    ir.extra_scripts[f"scripts/soundscapes_{map_name}.txt"] = acoustics.soundscape_file(map_name, theme, zl)
    rep.info.append("Sound: " + summary)


# ---------------------------------------------------------------- Sound panel: the view

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
