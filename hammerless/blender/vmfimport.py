"""File > Import > Hammer Map (.vmf): a Hammer map as Blender objects, written back exactly by Build.

Each brush is a mesh object (its faces keep their sides' ids: "hl_side"); each entity an object with its
class, keyvalues and outputs (brush entities: an Empty with their brushes as children; props: their model).
The file itself is kept in the .blend (a text block): Build writes it back with only what changed in Blender
replaced (core/vmfimport.py), and adds anything new from the scene the usual way.
"""
from __future__ import annotations

import math
import os

import bpy
from bpy_extras.io_utils import ImportHelper
from mathutils import Euler, Matrix, Vector

from ..core import vmfimport as vi
from ..core.vmf import Block

SOURCE_KEY = "hl_vmf_source"        # scene: the text block holding the imported file
KIND, ID, INDEX, NAME = "hl_vmf_kind", "hl_vmf_id", "hl_vmf_index", "hl_vmf_name"
PROP_CLASSES = ("prop_static", "prop_dynamic", "prop_dynamic_override", "prop_physics", "prop_physics_override",
                "prop_physics_multiplayer", "prop_door_rotating", "prop_detail", "prop_ragdoll")


def imported(scene) -> bool:
    return bool(scene.get(SOURCE_KEY)) and scene[SOURCE_KEY] in bpy.data.texts


def is_imported_object(obj) -> bool:
    return obj.get(KIND) is not None


def _num3(text: str | None) -> tuple[float, float, float]:
    import re
    nums = [float(x) for x in re.findall(r"[-+0-9.eE]+", text or "")][:3]
    return tuple(nums + [0.0] * (3 - len(nums)))


def _matrix(origin, angles, upm: float) -> Matrix:
    pitch, yaw, roll = (math.radians(a) for a in angles)
    rot = Euler((roll, pitch, yaw), "XYZ").to_matrix().to_4x4()
    return Matrix.Translation(Vector(origin) / upm) @ rot


# ---------------------------------------------------------------- materials

class _Materials:
    def __init__(self, content, game_dir):
        self.content, self.game_dir = content, game_dir
        self.cache: dict[str, tuple] = {}

    def get(self, path: str):
        """(Blender material, texture width, height) for a game material path (made once)."""
        key = path.lower().replace("\\", "/")
        if key in self.cache:
            return self.cache[key]
        mat = bpy.data.materials.get(key)
        w = h = 512
        if mat is None:
            mat = bpy.data.materials.new(key)
            mat.hammerless.source_material = key
            self._texture(mat, key)
        img = next((n.image for n in (mat.node_tree.nodes if mat.use_nodes and mat.node_tree else [])
                    if n.type == "TEX_IMAGE" and n.image is not None), None)
        if img is not None:
            w, h = img.get("hl_full_size", img.size)
        self.cache[key] = (mat, max(int(w), 1), max(int(h), 1))
        return self.cache[key]

    def _texture(self, mat, path: str) -> None:
        if self.content is None:
            return
        from ..core.gamematerials import base_texture
        from .preview import _image_for_texture
        try:
            texture = base_texture(self.content, path, self.game_dir)
            img, _w, _h = _image_for_texture(texture, self.content, self.game_dir) if texture else (None, 0, 0)
        except Exception:
            img = None
        if img is None:
            return
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            return
        tex = nt.nodes.new("ShaderNodeTexImage")       # (through the faces' UVs: Hammer's alignment)
        tex.image = img
        tex.location = (-400, 300)
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.9
        avg = img.get("hl_average")
        if avg:
            mat.diffuse_color = (*avg, 1.0)


# ---------------------------------------------------------------- brushes

def _brush_object(solid: Block, name: str, coll, mats: _Materials, upm: float):
    faces = vi.solid_faces(solid)
    if not faces:
        return None
    disp = [(side, w) for side, w in faces if side.blocks("dispinfo")]
    verts: list[tuple] = []
    index: dict[tuple, int] = {}
    polys: list[list[int]] = []
    poly_sides: list[int] = []
    poly_uvs: list[list[tuple]] = []
    poly_mats: list = []

    def vid(p) -> int:
        key = (round(p[0], 3), round(p[1], 3), round(p[2], 3))
        if key not in index:
            index[key] = len(verts)
            verts.append(p)
        return index[key]

    for side, w in (disp or faces):
        mat, tw, th = mats.get(side.get("material", "tools/toolsnodraw"))
        surf = vi.disp_surface(side, w) if disp else None
        if surf is not None:
            grid, _alphas = surf
            n = len(grid)
            ids = [[vid(p) for p in row] for row in grid]
            for r in range(n - 1):
                for c in range(n - 1):
                    quad = [ids[r][c], ids[r + 1][c], ids[r + 1][c + 1], ids[r][c + 1]]
                    pts = [grid[r][c], grid[r + 1][c], grid[r + 1][c + 1], grid[r][c + 1]]
                    polys.append(quad)
                    poly_sides.append(vi._id(side))
                    poly_uvs.append([vi.side_uv(side, p, tw, th) for p in pts])
                    poly_mats.append(mat)
            continue
        polys.append([vid(p) for p in w])
        poly_sides.append(vi._id(side))
        poly_uvs.append([vi.side_uv(side, p, tw, th) for p in w])
        poly_mats.append(mat)
    # orient displacement quads like the face (outward)
    if disp:
        fn = vi._norm(vi._newell(disp[0][1]))
        for i, q in enumerate(polys):
            if vi._dot(vi._newell([verts[k] for k in q]), fn) < 0:
                polys[i] = list(reversed(q))
                poly_uvs[i] = list(reversed(poly_uvs[i]))
    lo = [min(v[k] for v in verts) for k in range(3)]
    hi = [max(v[k] for v in verts) for k in range(3)]
    center = [(lo[k] + hi[k]) / 2 for k in range(3)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple((v[k] - center[k]) / upm for k in range(3)) for v in verts], [], polys)
    slots: dict = {}
    for m in poly_mats:
        if m.name not in slots:
            slots[m.name] = len(mesh.materials)
            mesh.materials.append(m)
    mesh.polygons.foreach_set("material_index", [slots[m.name] for m in poly_mats])
    uv = mesh.uv_layers.new(name="UVMap")
    k = 0
    for poly, uvs in zip(mesh.polygons, poly_uvs):
        for li, (u, v) in zip(poly.loop_indices, uvs):
            uv.data[li].uv = (u, v)
        k += 1
    attr = mesh.attributes.new("hl_side", "INT", "FACE")
    attr.data.foreach_set("value", poly_sides)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = Vector(center) / upm
    coll.objects.link(obj)
    obj[KIND] = "disp" if disp else "solid"
    obj[ID] = vi._id(solid)
    obj[NAME] = obj.name
    if disp:
        obj["hl_vmf_shape"] = _shape_key(obj, upm)
    return obj


def _shape_key(obj, upm: float) -> str:
    """A displacement's shape, to notice edits (they aren't written back yet)."""
    import hashlib
    m = obj.matrix_basis          # (set at once by location etc.: matrix_world waits for an update)
    pts = sorted((round((m @ v.co).x * upm, 1), round((m @ v.co).y * upm, 1), round((m @ v.co).z * upm, 1))
                 for v in obj.data.vertices)
    return hashlib.sha1(repr(pts).encode()).hexdigest()


# ---------------------------------------------------------------- entities

def _entity_object(e: Block, k: int, coll, upm: float):
    from .ops import preview_mesh, style_entity_object
    cls = e.get("classname", "") or ""
    origin = _num3(e.get("origin")) if e.get("origin") is not None else None
    angles = _num3(e.get("angles")) if e.get("angles") is not None else (0.0, 0.0, 0.0)
    solids = e.blocks("solid")
    name = e.get("targetname") or cls or f"entity {k}"
    if solids:
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = 16 / upm
    else:
        model = e.get("model", "") if cls.lower() in PROP_CLASSES else ""
        obj = bpy.data.objects.new(name, preview_mesh(cls, upm, model))
    coll.objects.link(obj)
    if origin is not None:
        obj.matrix_world = _matrix(origin, angles, upm)
    obj.hammerless.classname = cls          # (sets the class's default keys: replaced below)
    obj.hammerless.keyvalues.clear()
    for key, value in [it for it in e.items if isinstance(it, tuple)]:
        if key.lower() in ("id", "classname", "origin", "angles"):
            continue
        kv = obj.hammerless.keyvalues.add()
        kv.key, kv.value = key, value
    obj.hammerless.outputs.clear()
    raw = [it for c in e.blocks("connections") for it in c.items if isinstance(it, tuple)]
    for out, value in raw:
        f = (value.split("\x1b") if "\x1b" in value else value.split(",")) + ["", "", "", "", ""]
        o = obj.hammerless.outputs.add()
        o.output, o.target, o.input, o.parameter = out, f[0], f[1], f[2]
        try:
            o.delay = float(f[3] or 0)
        except ValueError:
            o.delay = 0.0
        o.only_once = (f[4].strip() == "1")
    if not solids:
        try:
            style_entity_object(obj)
        except Exception:
            pass
    obj[KIND] = "entity"
    obj[ID] = vi._id(e)
    obj[INDEX] = k
    obj[NAME] = obj.name
    return obj


# ---------------------------------------------------------------- the operator

class HL_OT_import_vmf(bpy.types.Operator, ImportHelper):
    bl_idname = "hammerless.import_vmf"
    bl_label = "Import Hammer Map"
    bl_description = ("Bring in a Hammer map (.vmf): its brushes, displacements, entities and props as objects. "
                      "Build writes it back exactly, with your changes")
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".vmf"
    filter_glob: bpy.props.StringProperty(default="*.vmf", options={"HIDDEN"})

    def execute(self, context):
        import time
        t0 = time.time()
        try:
            with open(self.filepath, encoding="latin-1") as f:
                text = f.read()
        except OSError as ex:
            self.report({"ERROR"}, f"Couldn't read the map: {ex}")
            return {"CANCELLED"}
        try:
            n_brushes, n_ents = import_text(context, text, self.filepath)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"Couldn't import the map: {ex}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {n_brushes} brushes and {n_ents} entities in {time.time() - t0:.1f} s. "
                              "Build writes the map back with your changes")
        return {"FINISHED"}


def import_text(context, text: str, path: str) -> tuple[int, int]:
    from .ops import game_content, game_root
    s = context.scene.hammerless
    upm = s.units_per_meter
    root = game_root(context)
    content = game_content(root) if root else None
    mats = _Materials(content, os.path.join(root, "left4dead2") if root else None)
    doc = vi.Document(text)
    base = os.path.splitext(os.path.basename(path))[0]
    src = bpy.data.texts.new(f"vmf {base}")
    src.from_string(text)
    context.scene[SOURCE_KEY] = src.name
    context.scene["hl_vmf_path"] = path
    top = bpy.data.collections.new(f"VMF {base}")
    context.scene.collection.children.link(top)
    world = bpy.data.collections.new(f"{base} world")
    ents = bpy.data.collections.new(f"{base} entities")
    top.children.link(world)
    top.children.link(ents)
    n_brushes = 0
    for solid in doc.world.blocks("solid"):
        if _brush_object(solid, f"brush {vi._id(solid)}", world, mats, upm) is not None:
            n_brushes += 1
    wobj = bpy.data.objects.new("worldspawn", None)
    wobj.empty_display_size = 0.0
    ents.objects.link(wobj)
    wobj.hammerless.keyvalues.clear()
    for key, value in [it for it in doc.world.items if isinstance(it, tuple)]:
        if key.lower() in ("id", "classname"):
            continue
        kv = wobj.hammerless.keyvalues.add()
        kv.key, kv.value = key, value
    wobj[KIND] = "world"
    wobj[ID] = vi._id(doc.world)
    wobj[NAME] = wobj.name
    for k, e in enumerate(doc.entities):
        obj = _entity_object(e, k, ents, upm)
        for solid in e.blocks("solid"):
            b = _brush_object(solid, f"{obj.name} brush {vi._id(solid)}", ents, mats, upm)
            if b is not None:
                b.parent = obj
                b.matrix_parent_inverse = obj.matrix_world.inverted()
                n_brushes += 1
    # (a Valve map brings its own sky, sun, Director and spawns: Build adds none of its own; Auto Seal stays off
    # while it has its own seal: turn it on to delete the map's outer shell)
    s.map_name = "".join(c if c.isalnum() or c == "_" else "_" for c in base.lower())[:60] or s.map_name
    for attr, value in (("auto_seal", False), ("auto_director", False), ("auto_light_environment", False),
                        ("sound_mode", "OFF")):       # (and its own soundscapes)
        if hasattr(s, attr):
            setattr(s, attr, value)
    return n_brushes, len(doc.entities)


# ---------------------------------------------------------------- writing back (Build)

def _faces(obj, upm: float, materials) -> list[tuple[int | None, list, str]]:
    m = obj.matrix_world
    mesh = obj.data
    attr = mesh.attributes.get("hl_side")
    ids = [0] * len(mesh.polygons)
    if attr is not None and attr.domain == "FACE":
        attr.data.foreach_get("value", ids)
    out = []
    for poly, sid in zip(mesh.polygons, ids):
        pts = [tuple(c * upm for c in (m @ mesh.vertices[v].co)) for v in poly.vertices]
        mat = obj.material_slots[poly.material_index].material if poly.material_index < len(obj.material_slots) else None
        path = materials.resolve(mat)[0] if materials is not None else (mat.name if mat else "")
        out.append((sid or None, pts, path))
    return out


def _originals(objs, kind: str) -> dict:
    """id -> the object that is the original (a copy keeps the id but not the name: it's new)."""
    out = {}
    for o in objs:
        if o.get(KIND) == kind and o.get(ID) is not None:
            if o.get(NAME) == o.name or o[ID] not in out:
                out[o[ID]] = o
    return out


def rebuild(context, writer, materials, report) -> tuple[vi.Document, Block, list]:
    """The imported file with the scene's changes: (document, world block, entity blocks (None: deleted))."""
    from .extract import mesh_to_brushes, object_keyvalues
    s = context.scene.hammerless
    upm = s.units_per_meter
    doc = vi.Document(bpy.data.texts[context.scene[SOURCE_KEY]].as_string())
    writer._next_id = max(writer._next_id, doc.max_id() + 1)
    objs = [o for o in context.scene.objects]
    solids = {**_originals(objs, "solid"), **_originals(objs, "disp")}
    entities = {o[INDEX]: o for o in objs if o.get(KIND) == "entity" and o.get(NAME) == o.name}
    depsgraph = context.evaluated_depsgraph_get()

    def default_side(verts, material):
        from ..core.ir import Polygon
        return writer.side(Polygon(list(verts), material or "tools/toolsnodraw"))

    def scale_of(material: str):
        """The lightmap scale for an imported side (Imported Brushes Too): its material's own, else the scene's."""
        m = bpy.data.materials.get(material.lower().replace("\\", "/"))
        own = m.hammerless.lightmap_scale if m is not None else 0
        return own or s.lightmap_scale

    def solid(block: Block):
        out = _solid(block)
        if out is not None and s.lightmap_scale_imported:
            vi.set_lightmap_scale(out, scale_of)
        return out

    def _solid(block: Block):
        obj = solids.get(vi._id(block))
        if obj is None or not obj.visible_get():
            return None
        if obj.get(KIND) == "disp":
            if obj.get("hl_vmf_shape") != _shape_key(obj, upm):
                report.warnings.append(f"'{obj.name}': editing displacements isn't written back yet: it keeps "
                                       "its imported shape")
            return block
        return vi.solid_block(block, _faces(obj, upm, materials), writer.new_id, default_side)

    def new_solids(parent):
        out = []
        for o in objs:
            if o.parent is parent and o.type == "MESH" and o.get(KIND) is None and o.visible_get():
                for b in mesh_to_brushes(o, depsgraph, upm, materials):
                    out.append(writer.solid(b))
        return out

    wobj = next((o for o in objs if o.get(KIND) == "world"), None)
    world = doc.world
    if wobj is not None:
        world = vi.set_entity_values(world, {"classname": "worldspawn", **object_keyvalues(wobj)}, None)
    world = vi.with_solids(world, [b for b in (solid(x) for x in doc.world.blocks("solid")) if b is not None])
    out_ents = []
    for k, e in enumerate(doc.entities):
        obj = entities.get(k)
        if obj is None or not obj.visible_get():
            out_ents.append(None)
            continue
        values = {"classname": obj.hammerless.classname, **object_keyvalues(obj)}
        orig_origin = e.get("origin")
        if orig_origin is not None:
            origin = tuple(c * upm for c in obj.matrix_world.translation)
            if max(abs(a - b) for a, b in zip(origin, _num3(orig_origin))) > 0.01:
                values["origin"] = " ".join(f"{c:g}" for c in (round(x, 3) for x in origin))
            else:
                values["origin"] = orig_origin
            if e.get("angles") is not None and e.blocks("solid"):
                values["angles"] = e.get("angles")      # (a brush entity's brushes carry its shape)
            elif e.get("angles") is not None:
                from .extract import source_angles
                # (the same turn can be written several ways: 0 180 -180 is 180 0 0; compare the rotations)
                now = obj.matrix_world.to_3x3().normalized()
                was = _matrix((0, 0, 0), _num3(e.get("angles")), upm).to_3x3()
                if max(abs(now[i][j] - was[i][j]) for i in range(3) for j in range(3)) > 1e-4:
                    values["angles"] = " ".join(f"{round(c, 3):g}" for c in source_angles(obj.matrix_world))
                else:
                    values["angles"] = e.get("angles")
        elif e.get("angles") is not None:
            values["angles"] = e.get("angles")
        outputs = _outputs(obj, e)
        block = vi.set_entity_values(e, values, outputs)
        kept = [b for b in (solid(x) for x in e.blocks("solid")) if b is not None] + new_solids(obj)
        out_ents.append(vi.with_solids(block, kept) if e.blocks("solid") or kept else block)
    return doc, world, out_ents


def _outputs(obj, e: Block):
    """The entity's outputs as (output, value): None when they're as imported (the original text stays)."""
    raw = [it for c in e.blocks("connections") for it in c.items if isinstance(it, tuple)]

    def norm(out, value):
        f = (value.split("\x1b") if "\x1b" in value else value.split(",")) + ["", "", "", "", ""]
        try:
            delay = float(f[3] or 0)
        except ValueError:
            delay = 0.0
        return (out, f[0], f[1], f[2], round(delay, 4), f[4].strip() == "1")
    now = [(o.output, o.target, o.input, o.parameter, round(o.delay, 4), o.only_once) for o in obj.hammerless.outputs]
    if now == [norm(*r) for r in raw]:
        return None
    out = []
    for i, o in enumerate(obj.hammerless.outputs):
        if i < len(raw) and norm(*raw[i]) == now[i]:
            out.append(raw[i])
            continue
        out.append((o.output, f"{o.target},{o.input},{o.parameter},{o.delay:g},{1 if o.only_once else -1}"))
    return out


def copy_instances(scene, folder: str, report) -> None:
    """An imported map's instance files, next to the built map as its func_instances name them (the compilers
    look for them there)."""
    import shutil
    from ..core.mapcompiler import vmf_instances
    source = scene.get("hl_vmf_path", "")
    if not source or not os.path.isfile(source):
        report.warnings.append("The imported map's own file is gone: its instances can't be found for the build")
        return
    for key, src in vmf_instances(source):
        rel = os.path.splitext(key.replace("\\", "/"))[0] + ".vmf"
        dst = os.path.join(folder, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        except OSError as ex:
            report.warnings.append(f"Couldn't copy instance '{key}' for the build: {ex}")


def menu_func(self, context):
    self.layout.operator(HL_OT_import_vmf.bl_idname, text="Hammer Map (.vmf) [Hammerless]")


CLASSES = (HL_OT_import_vmf,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_func)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
