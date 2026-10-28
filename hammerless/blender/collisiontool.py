"""Convert to Brushes: invisible brushes (or props) that collide like a mesh. Extend-or-Fill (core/brushfit.py): few
clean convex pieces on the mesh's own faces. Slabs (core/slabcollision.py): brushes that follow its flat areas, for a big
Custom Model such as a house: the model draws, the brushes collide (the engine's fastest collision, no pieces
limit per prop). The brushes are an ordinary object to look at, edit or delete; the button makes them again."""
from __future__ import annotations

import time

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
        mi = np.empty(len(me.loop_triangles), np.int64)
        me.loop_triangles.foreach_get("material_index", mi)
        # Evaluated material IDs belong to the dependency graph. Persistent meshes
        # must reference original datablocks, otherwise a graph rebuild frees them.
        mats = [s.material.original if s.material is not None else None for s in ev.material_slots]
        uv = None
        if me.uv_layers.active:
            uv = np.array([[tuple(me.uv_layers.active.data[i].uv) for i in t.loops]
                           for t in me.loop_triangles], dtype=np.float64)
    finally:
        ev.to_mesh_clear()
    M = np.array(obj.matrix_world)
    co = co @ M[:3, :3].T + M[:3, 3]
    if np.linalg.det(M[:3, :3]) < 0:           # (mirrored: keep the faces looking the same way)
        tri = tri[:, ::-1]
        if uv is not None:
            uv = uv[:, ::-1]
    return co, tri, mi, mats, uv


def _outputs(src, skipped: bool):
    """Find independent generated outputs by source name, plus legacy parented outputs."""
    return [o for o in bpy.data.objects
            if o.get(OF) is not None and o.name.endswith(SKIPPED_SUFFIX) == skipped
            and (o.parent == src or (o.parent is None and o.get(OF) == src.name))]


def _remove(objs):
    for o in objs:
        me = o.data
        bpy.data.objects.remove(o)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)


def _new_output(name, mesh, src):
    """Independent output in the source collections; mesh vertices are already world-space."""
    obj = bpy.data.objects.new(name, mesh)
    obj[OF] = src.name
    for c in src.users_collection:
        c.objects.link(obj)
    return obj


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


def _transfer_appearance(mesh, co, tri, mi, mats, uv):
    """Copy a source triangle's affine mapping to each matching exterior brush plane.

    The output is in world coordinates, as is co. Interior cut planes keep nodraw.
    A brush face has one material and one affine mapping; a merged seam cannot be
    represented without splitting the brush geometry.
    """
    from mathutils.bvhtree import BVHTree
    from .extract import PAINTED
    if not len(tri) or not mats:
        return
    tree = BVHTree.FromPolygons(co.tolist(), tri.tolist(), all_triangles=True)
    points = co[tri]
    normals = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-30)
    tolerance = max(float(np.linalg.norm(np.ptp(co, axis=0))) * 1e-6, 1e-6)
    slots = {}
    for index, mat in enumerate(mats):
        if mat is not None:
            slots[index] = len(mesh.materials)
            mesh.materials.append(mat)
    layer = mesh.uv_layers.new(name="UVMap") if uv is not None else None
    painted = mesh.attributes.new(PAINTED, 'INT', 'FACE') if layer else None
    mesh.update()
    for face in mesh.polygons:
        center = face.center
        # Range query avoids choosing the wrong triangle at a shared edge.
        hits = tree.find_nearest_range(center, tolerance)
        hits = [h for h in hits if lengths[h[2]] > 1e-20
                and float(np.dot(normals[h[2]], face.normal)) > 0.9999]
        if not hits:
            continue
        index = min(hits, key=lambda h: h[3])[2]
        if int(mi[index]) not in slots:
            continue
        face.material_index = slots[int(mi[index])]
        if layer is not None:
            a, b, c = points[index]
            # Project onto the source triangle's plane, then extend its affine UVs.
            basis = np.stack((b - a, c - a), axis=1)
            inverse = np.linalg.pinv(basis)
            for loop in face.loop_indices:
                point = np.array(mesh.vertices[mesh.loops[loop].vertex_index].co)
                u, v = inverse @ (point - a)
                layer.data[loop].uv = uv[index, 0] + u * (uv[index, 1] - uv[index, 0]) + v * (uv[index, 2] - uv[index, 0])
            painted.data[face.index].value = 1


class _Pieces:
    """Extend-or-Fill's pieces in the shape the output code takes (Slabs' result has the same .brushes)."""

    def __init__(self, brushes, info):
        self.brushes, self.info = brushes, info


def _tune(hs):
    """General's Fine Tuning settings as organicfit's constants."""
    return dict(STOP_GAIN=hs.brush_stop_gain / 100.0, MAX_CANDIDATES=int(hs.brush_max_candidates),
                CERT_TOL=hs.brush_fit_tolerance / 100.0, AIM_RES=hs.brush_aim_precision / 100.0,
                MERGE=bool(hs.brush_merge_pass), JOIN=bool(hs.brush_sliver_pass))


class HL_OT_make_collision(bpy.types.Operator):
    bl_idname = "hammerless.make_collision"
    bl_label = "Convert to Brushes"
    bl_description = ("Brushes (or invisible props) that collide like this mesh. Hard Surface: few clean convex "
                      "pieces on the mesh's own faces, exact except shallow detail (Detail). General: few convex "
                      "pieces of any closed shape, within Fit of it. Slabs: a thin slab "
                      "behind each flat area, for big shells like a house. For a Custom Model, set its Collision "
                      "to None so it doesn't collide twice. Press again after editing the mesh")
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
        co, tri, mi, mats, uv = _world_triangles(src, context.evaluated_depsgraph_get())
        upm = context.scene.hammerless.units_per_meter or 52.49
        algo = src.hammerless.brush_algorithm
        eof = algo != "SLABS"                   # (Extend-or-Fill or Organic: convex pieces of a solid)
        if eof:
            r = self._solid_pieces(src, co, tri, unit * upm, algo)
            if r is None:
                return {"CANCELLED"}
        else:
            min_area = src.hammerless.collision_min_area / unit ** 2
            min_width = max(inch, 1.05 / upm / unit)      # (1 game unit: the smallest brush)
            r = slab_brushes(co, tri, inch, min_area, min_width=min_width)
            if not r.brushes:
                self.report({"WARNING"}, f"'{src.name}' has no flat area bigger than Smallest Face: no brushes made")
                return {"CANCELLED"}
        rng = np.random.default_rng(7)
        rcol = {}
        for _vs, _bf, ri in r.brushes:
            rcol.setdefault(ri, (*rng.uniform(0.2, 1.0, 3), 1.0))
        # the previous result goes (its material and place are kept: e.g. tools/toolsinvisible set by hand)
        old = _outputs(src, False)
        material = None
        for o in old:
            if o.hammerless.brush_detail == "VIS":      # (the vis-only brushes: always nodraw)
                continue
            me = o.data
            if me is not None and len(me.polygons) and me.polygons[0].material_index < len(me.materials):
                material = me.materials[me.polygons[0].material_index]      # (what its faces wear: a texture
                if material is not None:                                      # applied adds a slot)
                    break
        material = material or game_material("tools/toolsnodraw")
        _remove(old)
        props = src.hammerless.collision_mode == "PROPS"
        if props:
            items = [(np.mean(vs, axis=0), (vs, bf, ri)) for vs, bf, ri in r.brushes]
            groups = [[b for _c, b in ch] for ch in _chunks(items, MAX_PROP_PIECES)]
        else:
            groups = [r.brushes]
        keep = not props and src.hammerless.brush_keep_appearance
        made = []
        for k, group in enumerate(groups):
            name = src.name + (f" Collision Prop {k + 1}" if props else " Collision")
            mesh = _mesh(name, group, rcol, game_material("tools/toolsnodraw") if keep else material)
            if keep:
                _transfer_appearance(mesh, co, tri, mi, mats, uv)
            obj = _new_output(name, mesh, src)
            hs = obj.hammerless
            if props:
                hs.role = "MODEL"
                hs.model_kind = "STATIC"
                hs.model_collision = "HULLS"
            else:
                hs.role = "BRUSH"
                hs.brush_detail = "WORLD"
            obj.display_type = "TEXTURED" if keep else "SOLID"
            made.append(obj)
        _remove(_outputs(src, True))     # (the skipped-faces object older versions made)
        what = (f"{len(groups)} invisible props of {len(r.brushes)} collision pieces" if props
                else f"{len(r.brushes)} collision brushes")
        if eof and r.info.get("method") == "General":
            info = r.info
            air = info["air"] / max(info["volume"], 1e-9) * 100
            msg = (f"{what} (General, {info['seconds']:.1f} s{', ' + info['why'] if info.get('why') else ''}); "
                   f"collision beyond the mesh: about {air:.1f}% of its volume")
            if info.get("pinched"):
                msg += (f"; {info['pinched']} parts joined to others only at single points were converted on their "
                        "own")
            if info.get("raised"):
                msg += (f"; Fit raised to about {info['raised']:.2f}%: finer would need more memory than General allows "
                        "(the finest it can measure this mesh to)")
            if info.get("half_unit"):
                msg += (f"; Fit raised to {info['half_unit']:.2f}% - half a game unit, the finest General goes (finer "
                        "isn't felt in game)")
            if info["stopped"]:
                msg += ("; it ran out of time, so the last pieces are looser than Fit (their hulls): raise Time Limit for "
                        "a full fit")
            elif info["pieces"] >= src.hammerless.brush_max_pieces:
                msg += "; it used all of Max Pieces: raise it or Detail for a closer or simpler fit"
        elif eof:
            info = r.info
            filled = info["filled"] / max(info["volume"], 1e-9) * 100
            msg = (f"{what} (Hard Surface, {info['seconds']:.1f} s)" +
                   (f"; detail covered over: {filled:.2f}% of the volume" if info["filled"] > 0 else "; exact"))
            if info["stuck"]:
                msg += "; some of it couldn't be split into convex pieces: those pieces are their convex hull"
            if info.get("capped"):
                ms = src.hammerless.brush_max_sides
                msg += (f"; {info['capped']} curved pieces had more than {ms} sides (Max Sides): each is a slightly "
                        f"larger {ms}-sided brush")
        else:
            msg = (f"{what} from {r.kept_regions} flat areas "
                   f"({100 * r.kept_share:.1f}% of the surface; {r.paired_walls} two-sided walls as one brush each); "
                   f"{r.regions - r.kept_regions} small areas and {r.thin} thin strips left out")
        if not props and len(r.brushes) > MAX_BRUSHES_WARN:
            self.report({"WARNING"}, msg + f": that's a lot of the map's 8,192 brushes, raise Smallest Face")
        else:
            self.report({"INFO"}, msg)
        if not src.hammerless.brush_remove_original and src.hammerless.role == "MODEL" and src.hammerless.model_collision != "NONE":
            self.report({"WARNING"}, f"'{src.name}' still has its own collision: set its Collision to None")
        if src.hammerless.brush_remove_original:
            # Keep this inside the UNDO operator: one undo restores the source
            # and removes all generated objects. Do not delete shared mesh data.
            for child in list(src.children):
                world = child.matrix_world.copy()
                child.parent = None
                child.matrix_world = world
            for selected in context.selected_objects:
                selected.select_set(False)
            bpy.data.objects.remove(src, do_unlink=True)
            for obj in made:
                del obj[OF]  # no source left to regenerate; avoid matching a reused name
                obj.select_set(True)
            context.view_layer.objects.active = made[0]
        return {"FINISHED"}


    def _solid_pieces(self, src, co, tri, scale, algo):
        """The mesh (Blender units) through core/brushfit.py (Extend-or-Fill) or core/organicfit.py (Organic) in game
        units; its pieces back as brushes. Auto: Extend-or-Fill, and Organic when the mesh is too detailed for it."""
        try:
            from ..core import brushfit, organicfit
        except ImportError as e:
            self.report({"ERROR"}, f"Convert to Brushes needs its geometry kernel (manifold3d), which didn't load: {e}")
            return None
        hs = src.hammerless
        detail = hs.brush_detail_depth * scale
        why = ""
        meshes = info = None
        try:
            if algo in ("AUTO", "EOF"):
                if len(tri) > 20000 and algo == "EOF":
                    self.report({"WARNING"}, f"'{src.name}' has {len(tri)} triangles: Hard Surface is meant for "
                                             "props; this may take a while")
                t0 = time.time()
                try:
                    meshes, info = brushfit.convert(co * scale, tri, detail=detail,
                                                    time_limit=10.0 if algo == "AUTO" else None)
                    info["method"] = "Hard Surface"
                except brushfit.TooBig as e:
                    if algo == "EOF":
                        self.report({"ERROR"}, f"'{src.name}': {e} (or use General)")
                        return None
                    why = ("Hard Surface took too long" if "longer than" in str(e)
                           else "too detailed for Hard Surface")
            if meshes is None:
                brushfit.NATIVE[0] = bool(bpy.context.scene.hammerless.general_native)
                limit = float(hs.brush_time_limit)
                left = limit - (time.time() - t0 if algo == "AUTO" else 0.0)
                size = float(np.linalg.norm(co.max(axis=0) - co.min(axis=0))) * scale
                asked = size * hs.brush_fit / 100.0               # (Fit: a share of the object's diagonal)
                fit = max(asked, 0.5)                             # (and never under half a game unit)
                meshes, info = organicfit.convert(co * scale, tri, detail=fit,
                                                  max_pieces=hs.brush_max_pieces, time_limit=max(left, min(20.0, limit)),
                                                  max_sides=hs.brush_max_sides, tune=_tune(hs))
                info["method"], info["why"] = "General", why
                if info.get("fit", 0.0) > fit * 1.05 and size > 0:
                    info["raised"] = 100.0 * info["fit"] / size
                elif asked < 0.5 and size > 0:
                    info["half_unit"] = 100.0 * 0.5 / size
        except brushfit.NotSolid as e:
            self.report({"ERROR"}, f"'{src.name}': {e} (or use Slabs)")
            return None
        except Exception as e:                  # (a kernel failure on odd geometry: said plainly, not a traceback)
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"'{src.name}': Convert to Brushes failed ({type(e).__name__}: {e}); try Slabs")
            return None
        if not meshes:
            self.report({"WARNING"}, f"'{src.name}': no pieces made")
            return None
        brushes, capped = [], 0
        for k, (V, T) in enumerate(meshes):
            faces = brushfit.hull_faces(V, T)
            if len(faces) > hs.brush_max_sides:
                capped += 1
                V, T = brushfit.arrays(organicfit.fewer_sides(brushfit.Manifold.hull_points(V.tolist()),
                                                              hs.brush_max_sides))
                faces = brushfit.hull_faces(V, T)
            # (only the corners the faces use: one inside a face or on a straight edge would be a loose point)
            used = sorted({i for f in faces for i in f})
            remap = {i: j for j, i in enumerate(used)}
            faces = [[remap[i] for i in f] for f in faces]
            brushes.append(((V[used] / scale).tolist(), faces, k))
        info["capped"] = capped
        return _Pieces(brushes, info)


CLASSES = (HL_OT_make_collision,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
