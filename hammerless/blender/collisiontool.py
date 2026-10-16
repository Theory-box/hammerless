"""Make Collision Brushes: invisible brushes that follow a mesh's flat areas (core/slabcollision.py), for a big
Custom Model such as a house: the model draws, the brushes collide (the engine's fastest collision, no pieces
limit per prop). The brushes are an ordinary object to look at, edit or delete; the button makes them again."""
from __future__ import annotations

import numpy as np
import bpy

from ..core.slabcollision import slab_brushes

OF = "hl_collision_of"          # on the made objects: the name of the mesh they were made from
SKIPPED_SUFFIX = " (skipped faces)"
MAX_BRUSHES_WARN = 3000          # (the map holds 8192 brushes in all)
MAX_PROP_PIECES = 20             # (Valve-sized props: as fast as brushes with a horde, measured; bigger ones aren't)


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


def _outputs(src_name: str, skipped: bool):
    return [o for o in bpy.data.objects
            if o.get(OF) == src_name and o.name.endswith(SKIPPED_SUFFIX) == skipped]


def _remove(objs):
    for o in objs:
        me = o.data
        bpy.data.objects.remove(o)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)


def _chunks(items, most):
    """Halve along the longest axis of the pieces' centres until each part has at most `most` (compact props)."""
    if len(items) <= most:
        return [items]
    c = np.array([it[0] for it in items])
    ax = int(np.argmax(c.max(0) - c.min(0)))
    items = sorted(items, key=lambda it: it[0][ax])
    h = len(items) // 2
    return _chunks(items[:h], most) + _chunks(items[h:], most)


def _mesh(name, brushes, rcol, material):
    verts, faces, cols = [], [], []
    for vs, bf, ri in brushes:
        k = len(verts)
        faces += [tuple(k + i for i in f) for f in bf]
        verts += [tuple(v) for v in vs]
        cols += [rcol[ri]] * len(bf)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    attr = mesh.color_attributes.new("region", "FLOAT_COLOR", "CORNER")
    corner = np.repeat(np.array(cols, np.float32), [len(f) for f in faces], axis=0)
    attr.data.foreach_set("color", corner.ravel())
    mesh.materials.append(material)
    return mesh


class HL_OT_make_collision(bpy.types.Operator):
    bl_idname = "hammerless.make_collision"
    bl_label = "Make Collision"
    bl_description = ("Invisible brushes along this mesh's flat areas (walls, floors, roof, stairs): one thin slab "
                      "behind each, so players stand exactly on the floors and doorways keep their width. For a big "
                      "Custom Model like a house: set its Collision to None so it doesn't collide twice. Collision As "
                      "picks brushes or invisible props for the pieces. Areas "
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
        upm = context.scene.hammerless.units_per_meter or 52.49
        r = slab_brushes(co, tri, inch, src.hammerless.collision_min_area / unit ** 2,
                         min_width=max(inch, 1.05 / upm / unit))      # (1 game unit: the smallest brush)
        if not r.brushes:
            self.report({"WARNING"}, f"'{src.name}' has no flat area bigger than Smallest Face: no brushes made")
            return {"CANCELLED"}
        rng = np.random.default_rng(7)
        rcol = {}
        for _vs, _bf, ri in r.brushes:
            rcol.setdefault(ri, (*rng.uniform(0.2, 1.0, 3), 1.0))
        # the previous result goes (its material and place are kept: e.g. tools/toolsinvisible set by hand)
        old = _outputs(src.name, False)
        material = None
        for o in old:
            if o.data is not None and len(o.data.materials) and o.data.materials[0] is not None:
                material = o.data.materials[0]
                break
        material = material or game_material("tools/toolsnodraw")
        colls = list(old[0].users_collection) if old else []
        _remove(old)
        props = src.hammerless.collision_mode == "PROPS"
        if props:
            items = [(np.mean(vs, axis=0), (vs, bf, ri)) for vs, bf, ri in r.brushes]
            groups = [[b for _c, b in ch] for ch in _chunks(items, MAX_PROP_PIECES)]
        else:
            groups = [r.brushes]
        made = []
        for k, group in enumerate(groups):
            name = src.name + (f" Collision Prop {k + 1}" if props else " Collision")
            obj = bpy.data.objects.new(name, _mesh(name, group, rcol, material))
            obj[OF] = src.name
            for c in colls or src.users_collection:
                c.objects.link(obj)
            hs = obj.hammerless
            if props:
                hs.role = "MODEL"
                hs.model_kind = "STATIC"
                hs.model_collision = "HULLS"
            else:
                hs.role = "BRUSH"
                hs.brush_detail = "DETAIL"
            obj.display_type = "SOLID"
            made.append(obj)
        # what got no brush, to look at (not exported)
        if r.skipped:
            sm = bpy.data.meshes.new(src.name + " Collision" + SKIPPED_SUFFIX)
            st = tri[r.skipped]
            used, inv = np.unique(st, return_inverse=True)
            sm.from_pydata([tuple(co[i]) for i in used], [], [tuple(t) for t in inv.reshape(-1, 3)])
            _remove(_outputs(src.name, True))
            sobj = bpy.data.objects.new(src.name + " Collision" + SKIPPED_SUFFIX, sm)
            sobj[OF] = src.name
            for c in colls or src.users_collection:
                c.objects.link(sobj)
            sobj.hammerless.role = "IGNORE"
            sobj.display_type = "WIRE"
            sobj.color = (1.0, 0.0, 1.0, 1.0)
        else:
            _remove(_outputs(src.name, True))
        if not colls:
            try:
                from .mapcollection import place
                for o in made:
                    place(context.scene, o, True)
            except Exception:              # (it stays beside the mesh: still built)
                pass
        what = (f"{len(made)} invisible props of {len(r.brushes)} collision pieces" if props
                else f"{len(r.brushes)} collision brushes")
        msg = (f"{what} from {r.kept_regions} flat areas "
               f"({100 * r.kept_share:.1f}% of the surface; {r.paired_walls} two-sided walls as one brush each); "
               f"{r.regions - r.kept_regions} small areas and {r.thin} thin strips left out")
        if not props and len(r.brushes) > MAX_BRUSHES_WARN:
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
