"""Brush tools (Brush Tools panel): Carve, Intersect and Clip convex brushes (core/csg.py, native/hlcsg.c).

A brush object's loose parts are its convex brushes. Each tool reads them as plane sets in game units (the map's
own numbers, so the compiler's snapping rules apply), cuts them in C and writes the pieces back as loose parts of
the same object: each face keeps its material and texture (its UVs re-projected through the source face's
mapping, so the alignment carries on across the cut), faces made by a cutter take the cutter's."""
from __future__ import annotations

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty
from mathutils import Matrix, Vector

CUTTER_TAG = 1_000_000          # tags from here on are the cutter's faces
CONVEX_EPS = 0.05               # (game units: a vertex this far outside a face's plane makes the part not convex)


class _Side:
    """Where a face came from: its material, its UV mapping (world point -> uv), whether its texture was painted."""
    __slots__ = ("material", "uv", "painted")

    def __init__(self, material, uv, painted):
        self.material, self.uv, self.painted = material, uv, painted


def _uv_map(pts, uvs):
    """The affine map p -> uv of a face from three of its corners (None when they're in a line)."""
    p0, uv0 = pts[0], uvs[0]
    for i in range(1, len(pts) - 1):
        e1, e2 = pts[i] - p0, pts[i + 1] - p0
        n = e1.cross(e2)
        if n.length < 1e-9:
            continue
        m = Matrix((e1, e2, n)).transposed()
        try:
            inv = m.inverted()
        except ValueError:
            continue
        du1, du2 = uvs[i] - uv0, uvs[i + 1] - uv0
        a = Matrix(((du1.x, du2.x, 0.0), (du1.y, du2.y, 0.0))) @ inv     # 2x3

        def f(p, a=a, p0=p0, uv0=uv0):
            d = p - p0
            return (uv0.x + a[0][0] * d.x + a[0][1] * d.y + a[0][2] * d.z,
                    uv0.y + a[1][0] * d.x + a[1][1] * d.y + a[1][2] * d.z)
        return f
    return None


def read_brushes(obj, scale: float, tag0: int = 0, records=None):
    """The object's loose parts as plane sets in game units: [(sides, convex, raw)], sides [(n, d, tag)], raw a
    Piece of the part's own faces (kept as it is when the part isn't convex); records[tag] = _Side. Uses the mesh
    itself (not modifiers)."""
    from ..core.csg import Piece
    from ..core.csg import plane_from_points
    from .extract import PAINTED, _loose_parts
    records = records if records is not None else {}
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    mw = obj.matrix_world
    uv = bm.loops.layers.uv.active
    painted = bm.faces.layers.int.get(PAINTED)
    slots = [s.material for s in obj.material_slots]
    out = []
    try:
        for part in _loose_parts(bm):
            sides = []
            raw = Piece()
            verts = {v for f in part for v in f.verts}
            world = {v: (mw @ v.co) * scale for v in verts}
            for f in part:
                pts = [world[v] for v in f.verts]
                pl = None
                for i in range(1, len(pts) - 1):
                    pl = plane_from_points(tuple(pts[0]), tuple(pts[i]), tuple(pts[i + 1]))
                    if pl is not None:
                        break
                if pl is None:
                    continue
                if mw.determinant() < 0:
                    pl = (tuple(-c for c in pl[0]), -pl[1], 0)
                tag = tag0 + len(records)
                uvs = [Vector(lp[uv].uv) for lp in f.loops] if uv is not None else None
                records[tag] = _Side(slots[f.material_index] if f.material_index < len(slots) else None,
                                     _uv_map(pts, uvs) if uvs else None,
                                     bool(f[painted]) if painted is not None else False)
                sides.append((pl[0], pl[1], tag))
                raw.sides.append((pl[0], pl[1], tag))
                raw.faces.append([tuple(p) for p in (reversed(pts) if mw.determinant() < 0 else pts)])
            convex = all(n[0] * p.x + n[1] * p.y + n[2] * p.z - d <= CONVEX_EPS
                         for (n, d, _t) in sides for p in world.values())
            out.append((sides, convex, raw))
    finally:
        bm.free()
    return out, records


def write_brushes(obj, pieces, records, scale: float) -> None:
    """The object's mesh made of these pieces (each a loose part), materials and UVs from the records."""
    from .extract import PAINTED
    me = obj.data
    inv = obj.matrix_world.inverted()
    slot_of = {}
    for i, s in enumerate(obj.material_slots):
        if s.material is not None and s.material not in slot_of:
            slot_of[s.material] = i
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap") if records and any(r.uv for r in records.values()) else None
    painted = bm.faces.layers.int.new(PAINTED) if any(r.painted for r in records.values()) else None
    flip = obj.matrix_world.determinant() < 0
    for piece in pieces:
        cache = {}

        def vert(p):
            key = (round(p[0], 4), round(p[1], 4), round(p[2], 4))
            v = cache.get(key)
            if v is None:
                v = cache[key] = bm.verts.new(inv @ (Vector(p) / scale))
            return v
        for (n, d, tag), poly in zip(piece.sides, piece.faces):
            vs = [vert(p) for p in poly]
            if flip:
                vs.reverse()
            if len({id(v) for v in vs}) < 3:
                continue
            try:
                f = bm.faces.new(vs)
            except ValueError:            # (a face already there: a coplanar duplicate)
                continue
            rec = records.get(tag)
            mat = rec.material if rec is not None else None
            if mat is not None and mat not in slot_of:
                me.materials.append(mat)
                slot_of[mat] = len(me.materials) - 1
            f.material_index = slot_of.get(mat, 0)
            if uv is not None and rec is not None and rec.uv is not None:
                for lp in f.loops:
                    lp[uv].uv = rec.uv((obj.matrix_world @ lp.vert.co) * scale)
            if painted is not None and rec is not None:
                f[painted] = 1 if rec.painted else 0
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    me.update()


def _targets(context, need_cutter: bool):
    objs = [o for o in context.selected_objects if o.type == "MESH" and o.hammerless.classname not in (
        "hammerless_control",)]
    cutter = context.object if need_cutter else None
    if need_cutter and (cutter is None or cutter.type != "MESH"):
        return None, []
    return cutter, [o for o in objs if o is not cutter]


class _BrushOp:
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"


class HL_OT_brush_carve(_BrushOp, bpy.types.Operator):
    bl_idname = "hammerless.brush_carve"
    bl_label = "Carve"
    bl_description = ("Cut the active object (the cutter) out of the other selected brushes: each becomes convex "
                      "pieces outside it, faces made by the cut take the cutter's material. The cutter stays")
    intersect: BoolProperty(name="Intersect", default=False,
                            description="Keep only the overlap with the cutter instead")
    delete_cutter: BoolProperty(name="Delete Cutter", default=False)

    def execute(self, context):
        from ..core import csg
        scale = context.scene.hammerless.units_per_meter
        context.view_layer.update()           # (world matrices current: a scale just set reads right)
        cutter, targets = _targets(context, True)
        if cutter is None or not targets:
            self.report({"WARNING"}, "Select the brushes to cut, then the cutter last (the active object)")
            return {"CANCELLED"}
        records = {}
        cut_parts, records = read_brushes(cutter, scale, CUTTER_TAG, records)
        cut_parts = [s for s, convex, _raw in cut_parts if convex]
        if not cut_parts:
            self.report({"WARNING"}, f"'{cutter.name}' has no convex part to cut with")
            return {"CANCELLED"}
        skipped = changed = 0
        for t in targets:
            recs = dict(records)
            parts, recs = read_brushes(t, scale, 0, recs)
            out = []
            touched = False
            for sides, convex, raw in parts:
                if not convex:
                    skipped += 1
                    out.append(raw)                 # (not convex: left exactly as it was)
                    continue
                if self.intersect:
                    for c in cut_parts:
                        p = csg.intersect(sides, c)
                        if p is not None:
                            out.append(p)
                    touched = True
                    continue
                pieces = [sides]
                for c in cut_parts:
                    nxt = []
                    for s in pieces:
                        got = csg.subtract(s, c)
                        nxt += [p.sides for p in got]
                    pieces = nxt
                got = [csg.intersect(s, s) for s in pieces]
                out += [p for p in got if p is not None]
                touched = True
            out = [p for p in out if p is not None]
            if touched:
                write_brushes(t, out, recs, scale)
                changed += 1
        if self.delete_cutter:
            bpy.data.objects.remove(cutter)
        msg = f"{'Intersected' if self.intersect else 'Carved'} {changed} object{'s' if changed != 1 else ''}"
        if skipped:
            msg += f"; {skipped} part{'s' if skipped != 1 else ''} not convex, left as they were"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class HL_OT_brush_clip(_BrushOp, bpy.types.Operator):
    bl_idname = "hammerless.brush_clip"
    bl_label = "Clip"
    bl_description = "Cut the selected brushes by a plane through the 3D cursor, keeping one side or both"
    axis: EnumProperty(name="Plane", default="Z", items=[
        ("X", "X", "Facing X"), ("Y", "Y", "Facing Y"), ("Z", "Z", "Facing up"),
        ("CURSOR", "Cursor", "Facing the 3D cursor's Z (rotate the cursor to tilt it)")])
    keep: EnumProperty(name="Keep", default="BOTH", items=[
        ("FRONT", "Front", "What's in front of the plane"), ("BACK", "Back", "What's behind it"),
        ("BOTH", "Both", "Both halves, as separate pieces")])

    def execute(self, context):
        from ..core import csg
        scale = context.scene.hammerless.units_per_meter
        context.view_layer.update()           # (world matrices current: a scale just set reads right)
        targets = [o for o in context.selected_objects if o.type == "MESH"]
        if not targets:
            self.report({"WARNING"}, "Select the brushes to clip")
            return {"CANCELLED"}
        cur = context.scene.cursor
        n = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1)),
             "CURSOR": cur.matrix.to_3x3() @ Vector((0, 0, 1))}[self.axis].normalized()
        origin = cur.location * scale
        plane = (tuple(n), n.dot(origin), CUTTER_TAG)
        for t in targets:
            recs = {CUTTER_TAG: _Side(t.active_material, None, False)}
            parts, recs = read_brushes(t, scale, 0, recs)
            out = []
            for sides, convex, raw in parts:
                if not convex:
                    out.append(raw)
                    continue
                front, back = csg.clip(sides, plane)
                if self.keep in ("FRONT", "BOTH") and front is not None:
                    out.append(front)
                if self.keep in ("BACK", "BOTH") and back is not None:
                    out.append(back)
            write_brushes(t, [p for p in out if p is not None], recs, scale)
        return {"FINISHED"}


class HL_OT_brush_hollow(_BrushOp, bpy.types.Operator):
    bl_idname = "hammerless.brush_hollow"
    bl_label = "Hollow"
    bl_description = ("Make the selected brushes hollow: walls of Thickness all round (the inside carved out), each "
                      "wall its own convex piece. A room from a block")
    thickness: bpy.props.FloatProperty(name="Thickness (units)", default=8.0, min=1.0, soft_max=64.0,
                                       description="Wall thickness in game units (8 = a typical wall)")

    def execute(self, context):
        from ..core import csg
        scale = context.scene.hammerless.units_per_meter
        context.view_layer.update()           # (world matrices current: a scale just set reads right)
        targets = [o for o in context.selected_objects if o.type == "MESH"]
        if not targets:
            self.report({"WARNING"}, "Select the brushes to hollow")
            return {"CANCELLED"}
        small = 0
        for t in targets:
            parts, recs = read_brushes(t, scale)
            out = []
            for sides, convex, raw in parts:
                if not convex:
                    out.append(raw)
                    continue
                inner = [(n, d - self.thickness, tag) for (n, d, tag) in sides]   # (each face moved in)
                if csg.intersect(inner, inner) is None:
                    small += 1                       # (thinner than two walls: left solid)
                    out.append(csg.intersect(sides, sides))
                    continue
                out += csg.subtract(sides, inner)
            write_brushes(t, [p for p in out if p is not None], recs, scale)
        if small:
            self.report({"WARNING"}, f"{small} brush{'es' if small != 1 else ''} thinner than two walls: left solid")
        return {"FINISHED"}


class HL_PT_brush_tools(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Brush Tools"
    bl_order = 3
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("hammerless.draw_brush", text="Draw Brush", icon="MESH_CUBE").mode = "ADD"
        row.operator("hammerless.draw_brush", text="Draw Carve", icon="MOD_BOOLEAN").mode = "CARVE"
        sub = layout.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="Drag on a surface, move for height, click", icon="INFO")
        layout.separator()
        col = layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.2
        row.operator("hammerless.brush_carve", text="Carve", icon="MOD_BOOLEAN").intersect = False
        row.operator("hammerless.brush_carve", text="Intersect", icon="SELECT_INTERSECT").intersect = True
        sub = col.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="Select the brushes, then the cutter last", icon="INFO")
        layout.separator()
        row = layout.row(align=True)
        row.scale_y = 1.2
        for axis in ("X", "Y", "Z", "CURSOR"):
            op = row.operator("hammerless.brush_clip", text=f"Clip {axis.title()}" if axis != "CURSOR" else "Clip",
                              icon="MOD_LINEART" if axis == "CURSOR" else "NONE")
            op.axis = axis
        sub = layout.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="Cuts the selected brushes through the 3D cursor", icon="INFO")
        layout.separator()
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("hammerless.brush_hollow", text="Hollow", icon="MOD_SOLIDIFY")


CLASSES = (HL_OT_brush_carve, HL_OT_brush_clip, HL_OT_brush_hollow, HL_PT_brush_tools)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
