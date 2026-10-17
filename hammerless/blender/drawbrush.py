"""Draw Brush: drag a rectangle on a surface, move the mouse for its height, click: a new brush (Add), or that box
carved out of every brush it touches (Carve). On the grid (game units), aligned to the surface (an axis-aligned
surface gives an axis-aligned brush; a slope, a brush along the slope).

Keys: left mouse drag = the rectangle, then move = the height, click = done; Ctrl = no snapping; mouse wheel or
[ ] = grid size; Esc / right mouse = cancel."""
from __future__ import annotations

import math

import bpy
import gpu
from bpy.props import EnumProperty, IntProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import intersect_line_plane

GRIDS = (1, 2, 4, 8, 16, 32, 64, 128)
_draw = {"handler": None, "op": None}


def frame_for(normal: Vector):
    """The drawing frame of a surface: (n, u, v), u and v world axes when the surface is axis-aligned."""
    a = [abs(c) for c in normal]
    i = a.index(max(a))
    if a[i] > 0.999:
        n = Vector((0.0, 0.0, 0.0))
        n[i] = math.copysign(1.0, normal[i])
        others = [k for k in range(3) if k != i]
        u = Vector((0.0, 0.0, 0.0))
        v = Vector((0.0, 0.0, 0.0))
        u[others[0]] = 1.0
        v[others[1]] = 1.0
        return n, u, v
    n = normal.normalized()
    up = Vector((0, 0, 1)) if abs(n.z) < 0.9 else Vector((1, 0, 0))
    u = up.cross(n).normalized()
    v = n.cross(u)
    return n, u, v


def _snap(x: float, grid: float, on: bool) -> float:
    return round(x / grid) * grid if on else x


def box_corners(n, u, v, d0, a0, a1, b0, b1, h, upm):
    """The 8 corners (Blender units) of a box on a surface: a, b along u, v; from the surface (d0 along n) up h
    (all in game units). Bottom four, then top four, each round the same way."""
    lo_a, hi_a = sorted((a0, a1))
    lo_b, hi_b = sorted((b0, b1))
    c0, c1 = sorted((0.0, h))
    out = []
    for c in (c0, c1):
        for a, b in ((lo_a, lo_b), (hi_a, lo_b), (hi_a, hi_b), (lo_a, hi_b)):
            out.append((u * a + v * b + n * (d0 + c)) / upm)
    return out


def box_sides(n, u, v, d0, a0, a1, b0, b1, h, tag):
    """The same box as planes in game units (+-u, +-v, +-n), each tagged."""
    lo_a, hi_a = sorted((a0, a1))
    lo_b, hi_b = sorted((b0, b1))
    c0, c1 = sorted((0.0, h))
    return [(tuple(u), hi_a, tag), (tuple(-u), -lo_a, tag), (tuple(v), hi_b, tag), (tuple(-v), -lo_b, tag),
            (tuple(n), d0 + c1, tag), (tuple(-n), -(d0 + c0), tag)]


class HL_OT_draw_brush(bpy.types.Operator):
    bl_idname = "hammerless.draw_brush"
    bl_label = "Draw Brush"
    bl_description = ("Drag a rectangle on a surface, move the mouse for its height, click: a brush (Add), or that "
                      "box carved out of the brushes it touches (Carve). Ctrl: no snapping; wheel / [ ]: grid size; "
                      "Esc: cancel")
    bl_options = {"REGISTER", "UNDO"}
    mode: EnumProperty(name="Mode", default="ADD", items=[("ADD", "Add", "A new brush"),
                                                          ("CARVE", "Carve", "Carve it out of the brushes it touches")])
    grid: IntProperty(name="Grid (units)", default=8, min=1, max=128)

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and context.mode == "OBJECT"

    # ------------------------------------------------------------ the surface under the mouse
    def _ray(self, context, event):
        region, rv3d = context.region, context.region_data
        co = (event.mouse_region_x, event.mouse_region_y)
        return (view3d_utils.region_2d_to_origin_3d(region, rv3d, co),
                view3d_utils.region_2d_to_vector_3d(region, rv3d, co))

    def _hover(self, context, event):
        origin, direction = self._ray(context, event)
        dg = context.evaluated_depsgraph_get()
        hit, loc, normal, index, obj, _m = context.scene.ray_cast(dg, origin, direction)
        if not hit:                                   # (nothing under the mouse: the ground at the cursor's height)
            z = context.scene.cursor.location.z
            loc = intersect_line_plane(origin, origin + direction, Vector((0, 0, z)), Vector((0, 0, 1)))
            normal, obj, index = Vector((0, 0, 1)), None, -1
            if loc is None:
                return None
        return loc, normal, obj, index

    def _to_plane(self, context, event):
        origin, direction = self._ray(context, event)
        return intersect_line_plane(origin, origin + direction, self.p0, self.n)

    def _coords(self, p: Vector, snap: bool):
        """A point on the surface as snapped (a, b) along u, v, in game units."""
        g = p * self.upm
        return _snap(g.dot(self.u), self.grid, snap), _snap(g.dot(self.v), self.grid, snap)

    # ------------------------------------------------------------ the box
    def _box(self):
        """The 8 corners (Blender units) of the box drawn so far, or None."""
        if self.a0 is None:
            return None
        return box_corners(self.n, self.u, self.v, self.d0, self.a0, self.a1, self.b0, self.b1, self.h, self.upm)

    def _sides(self, tag):
        return box_sides(self.n, self.u, self.v, self.d0, self.a0, self.a1, self.b0, self.b1, self.h, tag)

    # ------------------------------------------------------------ modal
    def invoke(self, context, event):
        self.upm = context.scene.hammerless.units_per_meter
        self.stage = "HOVER"
        self.a0 = self.b0 = self.a1 = self.b1 = None
        self.h = 0.0
        self.hit_obj, self.hit_index = None, -1
        self.marker = None
        self.n, self.u, self.v = Vector((0, 0, 1)), Vector((1, 0, 0)), Vector((0, 1, 0))
        self.p0, self.d0 = Vector((0, 0, 0)), 0.0
        _draw["op"] = self
        if _draw["handler"] is None:
            _draw["handler"] = bpy.types.SpaceView3D.draw_handler_add(_draw_overlay, (), "WINDOW", "POST_VIEW")
        context.window_manager.modal_handler_add(self)
        self._status(context)
        return {"RUNNING_MODAL"}

    def _status(self, context):
        what = "Carve" if self.mode == "CARVE" else "Add"
        if self.stage == "HOVER":
            text = f"Draw Brush ({what}): drag a rectangle on a surface"
        elif self.stage == "DRAG":
            text = f"{abs(self.a1 - self.a0):g} x {abs(self.b1 - self.b0):g}: release, then set the height"
        else:
            text = f"{abs(self.a1 - self.a0):g} x {abs(self.b1 - self.b0):g} x {abs(self.h):g}: click to finish"
        context.area.header_text_set(f"{text}   grid {self.grid}  (wheel / [ ] changes it, Ctrl: no snap, Esc: "
                                     "cancel)")
        context.area.tag_redraw()

    def _end(self, context):
        _draw["op"] = None
        if _draw["handler"] is not None:
            bpy.types.SpaceView3D.draw_handler_remove(_draw["handler"], "WINDOW")
            _draw["handler"] = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        snap = not event.ctrl
        if event.type in ("ESC", "RIGHTMOUSE") and event.value == "PRESS":
            self._end(context)
            return {"CANCELLED"}
        if event.type in ("WHEELUPMOUSE", "RIGHT_BRACKET") and event.value == "PRESS":
            self.grid = GRIDS[min(len(GRIDS) - 1, GRIDS.index(self.grid) + 1)] if self.grid in GRIDS else 8
        elif event.type in ("WHEELDOWNMOUSE", "LEFT_BRACKET") and event.value == "PRESS":
            self.grid = GRIDS[max(0, GRIDS.index(self.grid) - 1)] if self.grid in GRIDS else 8
        if self.stage == "HOVER":
            got = self._hover(context, event)
            if got is not None:
                loc, normal, obj, index = got
                self.n, self.u, self.v = frame_for(normal)
                self.p0 = loc
                self.d0 = (loc * self.upm).dot(self.n)
                a, b = self._coords(loc, snap)
                self.marker = (self.u * a + self.v * b + self.n * self.d0) / self.upm
                if event.type == "LEFTMOUSE" and event.value == "PRESS":
                    self.hit_obj, self.hit_index = obj, index
                    self.a0, self.b0 = a, b
                    self.a1, self.b1 = a, b
                    self.stage = "DRAG"
        elif self.stage == "DRAG":
            p = self._to_plane(context, event)
            if p is not None:
                self.a1, self.b1 = self._coords(p, snap)
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                if self.a1 == self.a0 or self.b1 == self.b0:
                    self.stage, self.a0 = "HOVER", None          # (no area: start again)
                else:
                    self.stage = "HEIGHT"
                    self.h = float(self.grid) if self.mode == "ADD" else -float(self.grid)
        elif self.stage == "HEIGHT":
            origin, direction = self._ray(context, event)
            centre = self.u * ((self.a0 + self.a1) / 2) + self.v * ((self.b0 + self.b1) / 2) + self.n * self.d0
            centre /= self.upm
            # (the point on the line up through the rectangle's centre nearest the mouse ray)
            w = origin - centre
            b = direction.dot(self.n)
            dd = direction.dot(w)
            e = self.n.dot(w)
            den = 1.0 - b * b
            if abs(den) > 1e-6:
                t = (e - b * dd) / den
                self.h = _snap(t * self.upm, self.grid, snap)
            if event.type == "LEFTMOUSE" and event.value == "PRESS" and self.h != 0.0:
                self._end(context)
                return self._finish(context)
        self._status(context)
        return {"RUNNING_MODAL"}

    # ------------------------------------------------------------ the result
    def _finish(self, context):
        mat = None
        if self.hit_obj is not None and self.hit_obj.type == "MESH" and self.hit_index >= 0:
            me = self.hit_obj.data
            if self.hit_index < len(me.polygons):
                mi = me.polygons[self.hit_index].material_index
                slots = self.hit_obj.material_slots
                mat = slots[mi].material if mi < len(slots) else None
        if self.mode == "ADD":
            corners = self._box()
            me = bpy.data.meshes.new("Brush")
            faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
            me.from_pydata([tuple(c) for c in corners], [], faces)
            me.validate()
            if mat is not None:
                me.materials.append(mat)
            me.update()
            obj = bpy.data.objects.new("Brush", me)
            context.collection.objects.link(obj)
            _outward(me)
            for o in context.selected_objects:
                o.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            self.report({"INFO"}, f"Brush {abs(self.a1 - self.a0):g} x {abs(self.b1 - self.b0):g} x {abs(self.h):g}")
            return {"FINISHED"}
        return self._carve(context, mat)

    def _carve(self, context, mat):
        from ..core import csg
        from .brushtools import CUTTER_TAG, _Side, read_brushes, write_brushes
        from .extract import effective_role
        from .mapcollection import map_objects
        context.view_layer.update()
        cutter = self._sides(CUTTER_TAG)
        corners = self._box()
        lo = Vector([min(c[i] for c in corners) for i in range(3)])
        hi = Vector([max(c[i] for c in corners) for i in range(3)])
        inside = map_objects(context.scene)
        n = 0
        for obj in context.scene.objects:
            if obj.type != "MESH" or obj.name not in inside or not obj.visible_get():
                continue
            if not (effective_role(obj) == "BRUSH" or obj.hammerless.classname == "func_detail"):
                continue
            bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
            if any(min(v[i] for v in bb) > hi[i] or max(v[i] for v in bb) < lo[i] for i in range(3)):
                continue
            recs = {CUTTER_TAG: _Side(mat, None, False)}
            parts, recs = read_brushes(obj, self.upm, 0, recs)
            out, touched = [], False
            for sides, convex, raw in parts:
                if not convex:
                    out.append(raw)
                    continue
                if csg.intersect(sides, cutter) is None:
                    out.append(csg.intersect(sides, sides))
                    continue
                out += csg.subtract(sides, cutter)
                touched = True
            if touched:
                write_brushes(obj, [p for p in out if p is not None], recs, self.upm)
                n += 1
        self.report({"INFO"} if n else {"WARNING"},
                    f"Carved {n} brush object{'s' if n != 1 else ''}" if n else "The box touches no brush")
        return {"FINISHED"}


def _outward(me) -> None:
    """Faces facing out (the box's winding depends on which way the height went)."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    me.update()


def _draw_overlay():
    op = _draw["op"]
    if op is None:
        return
    try:
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        colour = (1.0, 0.35, 0.25, 1.0) if op.mode == "CARVE" else (0.3, 1.0, 0.45, 1.0)
        gpu.state.line_width_set(2.0)
        gpu.state.depth_test_set("NONE")
        box = op._box() if op.stage != "HOVER" else None
        if box is not None:
            edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
            pts = [tuple(box[i]) for e in edges for i in e]
            shader.bind()
            shader.uniform_float("color", colour)
            batch_for_shader(shader, "LINES", {"pos": pts}).draw(shader)
        elif op.marker is not None:
            m, s = op.marker, 4.0 / op.upm
            pts = [tuple(m + op.u * s), tuple(m - op.u * s), tuple(m + op.v * s), tuple(m - op.v * s)]
            shader.bind()
            shader.uniform_float("color", colour)
            batch_for_shader(shader, "LINES", {"pos": pts}).draw(shader)
        gpu.state.line_width_set(1.0)
    except Exception:            # (never an error out of the viewport's drawing)
        pass


CLASSES = (HL_OT_draw_brush,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    if _draw["handler"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw["handler"], "WINDOW")
        _draw["handler"] = None
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
