"""Make Collision Brushes: invisible brushes that follow a mesh's flat areas (core/slabcollision.py), for a big
Custom Model such as a house: the model draws, the brushes collide (the engine's fastest collision, no pieces
limit per prop). The brushes are an ordinary object to look at, edit or delete; the button makes them again."""
from __future__ import annotations

import numpy as np
import bpy

from ..core.slabcollision import brush_faces, slab_brushes

OF = "hl_collision_of"          # on the made objects: the name of the mesh they were made from
SKIPPED_SUFFIX = " (skipped faces)"
MAX_BRUSHES_WARN = 3000          # (the map holds 8192 brushes in all)


def _world_triangles(obj, depsgraph):
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        me.calc_loop_triangles()
        co = np.empty(len(me.vertices) * 3, np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        tri = np.empty(len(me.loop_triangles) * 3, np.int64)
        me.loop_triangles.foreach_get("vertices", tri)
        tri = tri.reshape(-1, 3)
    finally:
        ev.to_mesh_clear()
    M = np.array(obj.matrix_world)
    co = co @ M[:3, :3].T + M[:3, 3]
    if np.linalg.det(M[:3, :3]) < 0:           # (mirrored: keep the faces looking the same way)
        tri = tri[:, ::-1]
    return co, tri


def _old(src_name: str, skipped: bool):
    for o in bpy.data.objects:
        if o.get(OF) == src_name and o.name.endswith(SKIPPED_SUFFIX) == skipped:
            return o
    return None


def _replace(name: str, mesh, src, skipped: bool):
    old = _old(src.name, skipped)
    if old is not None:
        old_mesh = old.data
        old.data = mesh
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
        return old, False
    obj = bpy.data.objects.new(name, mesh)
    obj[OF] = src.name
    for c in src.users_collection:
        c.objects.link(obj)
    return obj, True


class HL_OT_make_collision(bpy.types.Operator):
    bl_idname = "hammerless.make_collision"
    bl_label = "Make Collision Brushes"
    bl_description = ("Invisible brushes along this mesh's flat areas (walls, floors, roof, stairs): one thin slab "
                      "behind each, so players stand exactly on the floors and doorways keep their width. For a big "
                      "Custom Model like a house: set its Collision to None so it doesn't collide twice. Areas "
                      "smaller than Smallest Face are left out and shown. Press again after editing the mesh")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and obj.get(OF) is None

    def execute(self, context):
        from .ops import game_material
        src = context.active_object
        unit = context.scene.unit_settings.scale_length or 1.0
        inch = 0.0254 / unit
        co, tri = _world_triangles(src, context.evaluated_depsgraph_get())
        r = slab_brushes(co, tri, inch, src.hammerless.collision_min_area / unit ** 2)
        if not r.brushes:
            self.report({"WARNING"}, f"'{src.name}' has no flat area bigger than Smallest Face: no brushes made")
            return {"CANCELLED"}
        # the brushes: one loose part each
        verts, faces, cols = [], [], []
        rng = np.random.default_rng(7)
        rcol = {}
        for vs, m, ri in r.brushes:
            faces += brush_faces(m, len(verts))
            verts += [tuple(v) for v in vs]
            c = rcol.setdefault(ri, (*rng.uniform(0.2, 1.0, 3), 1.0))
            cols += [c] * (m + 2)
        mesh = bpy.data.meshes.new(src.name + " Collision")
        mesh.from_pydata(verts, [], faces)
        attr = mesh.color_attributes.new("region", "FLOAT_COLOR", "CORNER")
        corner = np.repeat(np.array(cols, np.float32), [len(f) for f in faces], axis=0)
        attr.data.foreach_set("color", corner.ravel())
        mesh.materials.append(game_material("tools/toolsnodraw"))
        obj, new = _replace(src.name + " Collision", mesh, src, False)
        hs = obj.hammerless
        hs.role = "BRUSH"
        hs.brush_detail = "DETAIL"
        obj.display_type = "SOLID"
        obj.matrix_world = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
        # what got no brush, to look at (not exported)
        if r.skipped:
            sm = bpy.data.meshes.new(src.name + " Collision" + SKIPPED_SUFFIX)
            st = tri[r.skipped]
            used, inv = np.unique(st, return_inverse=True)
            sm.from_pydata([tuple(co[i]) for i in used], [], [tuple(t) for t in inv.reshape(-1, 3)])
            sobj, _ = _replace(src.name + " Collision" + SKIPPED_SUFFIX, sm, src, True)
            sobj.hammerless.role = "IGNORE"
            sobj.display_type = "WIRE"
            sobj.color = (1.0, 0.0, 1.0, 1.0)
            sobj.matrix_world = obj.matrix_world
        else:
            old = _old(src.name, True)
            if old is not None:
                bpy.data.objects.remove(old)
        if new:
            try:
                from .mapcollection import place
                place(context.scene, obj, True)
            except Exception:              # (it stays beside the mesh: still built)
                pass
        msg = (f"{len(r.brushes)} collision brushes from {r.kept_regions} flat areas "
               f"({100 * r.kept_share:.1f}% of the surface; {r.paired_walls} two-sided walls as one brush each); "
               f"{r.regions - r.kept_regions} small areas and {r.thin} thin strips left out")
        if len(r.brushes) > MAX_BRUSHES_WARN:
            self.report({"WARNING"}, msg + f": that's a lot of the map's 8,192 brushes, raise Smallest Face")
        else:
            self.report({"INFO"}, msg)
        if src.hammerless.role == "MODEL" and src.hammerless.model_collision != "NONE":
            self.report({"WARNING"}, f"'{src.name}' still has its own collision: set its Collision to None")
        return {"FINISHED"}


CLASSES = (HL_OT_make_collision,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
