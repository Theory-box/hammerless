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


def _light_state(obj):
    lamp = obj.data
    return [lamp.energy, *lamp.color, lamp.spot_size if lamp.type == "SPOT" else 0,
            lamp.spot_blend if lamp.type == "SPOT" else 0]


def _light_values(obj, values, upm):
    """Keep exact imported keys until the corresponding Blender light controls change."""
    from .extract import _linear_to_srgb, source_angles
    lamp = obj.data
    before = list(obj.get("hl_vmf_light_state", _light_state(obj)))
    now = _light_state(obj)
    if now[:4] != before[:4]:
        col = " ".join(str(round(_linear_to_srgb(c) * 255)) for c in lamp.color)
        bright = lamp.energy * (100 if lamp.type == "SUN" else upm * upm / (400 * math.pi))
        values["_light"] = f"{col} {round(bright)}"
    if now[4:] != before[4:] and lamp.type == "SPOT":
        cone = math.degrees(lamp.spot_size) / 2
        values["_cone"] = f"{cone:g}"
        values["_inner_cone"] = f"{cone * (1 - lamp.spot_blend):g}"
    rotation = [v for row in obj.matrix_world.to_3x3().normalized() for v in row]
    old = obj.get("hl_vmf_light_rotation", rotation)
    if any(abs(a - b) > 1e-4 for a, b in zip(rotation, old)):
        angles = source_angles(obj.matrix_world @ Matrix.Rotation(math.radians(90), 4, "Y"))
        values["angles"] = " ".join(f"{c:g}" for c in angles)
        if "pitch" in values or lamp.type == "SUN":
            values["pitch"] = f"{-angles[0]:g}"


def _entity_object(e: Block, k: int, coll, upm: float):
    from .ops import preview_mesh, style_entity_object
    cls = e.get("classname", "") or ""
    origin = _num3(e.get("origin")) if e.get("origin") is not None else None
    angles = _num3(e.get("angles")) if e.get("angles") is not None else (0.0, 0.0, 0.0)
    solids = e.blocks("solid")
    name = e.get("targetname") or cls or f"entity {k}"
    from .lamps import LAMP_OF, _apply_light, DEFAULT_LIGHT
    lamp_kind = LAMP_OF.get(cls) if not solids else None
    if lamp_kind:
        lamp = bpy.data.lights.new(name, lamp_kind)
        _apply_light(lamp, e.get("_light", DEFAULT_LIGHT[lamp_kind]), upm)
        if lamp_kind == "SPOT":
            try:
                cone = float(e.get("_cone", "45"))
                inner = float(e.get("_inner_cone", str(cone * 0.7)))
                lamp.spot_size = math.radians(max(1, min(179, cone * 2)))
                lamp.spot_blend = max(0, min(1, 1 - inner / cone)) if cone else 0
            except ValueError:
                pass
        obj = bpy.data.objects.new(name, lamp)
    elif solids:
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = 16 / upm
    else:
        from ..core.entities import preview_model
        model = preview_model(cls, dict(it for it in e.items if isinstance(it, tuple)))
        obj = bpy.data.objects.new(name, preview_mesh(cls, upm, model))
    coll.objects.link(obj)
    if origin is not None:
        obj.matrix_world = _matrix(origin, angles, upm)
    if lamp_kind:
        light_angles = angles
        try:
            pitch = float(e.get("pitch", "0"))
            if pitch:
                light_angles = (-pitch, angles[1], angles[2])
        except ValueError:
            pass
        obj.matrix_world = _matrix(origin or (0, 0, 0), light_angles, upm) @ Matrix.Rotation(math.radians(-90), 4, "Y")
        obj["hl_vmf_light_state"] = _light_state(obj)
        obj["hl_vmf_light_rotation"] = [v for row in obj.matrix_world.to_3x3() for v in row]
    obj.hammerless.role = "ENTITY"
    obj.hide_render = not bool(lamp_kind)
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


def import_bsp(op, context, data: bytes, name: str) -> set:
    """A compiled map, decompiled (core.bspdecompile) and imported like a Hammer map. The .vmf is kept in the
    build folder (Build writes the map back from it)."""
    import time
    from ..core.bspdecompile import decompile
    from .ops import work_dir
    t0 = time.time()
    try:
        text, notes = decompile(data)
    except Exception as ex:
        import traceback
        traceback.print_exc()
        op.report({"ERROR"}, f"Couldn't read the map: {ex}")
        return {"CANCELLED"}
    path = os.path.join(work_dir(context), f"{name}_decompiled.vmf")
    with open(path, "w", encoding="latin-1", errors="replace") as f:
        f.write(text)
    try:
        n_brushes, n_ents = import_text(context, text, path)
    except Exception as ex:
        import traceback
        traceback.print_exc()
        op.report({"ERROR"}, f"Couldn't import the map: {ex}")
        return {"CANCELLED"}
    # (never the game's own map name: Build would replace Valve's map)
    context.scene.hammerless.map_name = f"{name}_remix"
    for n in notes:
        op.report({"WARNING"}, n)
    op.report({"INFO"}, f"Imported {name}: {n_brushes} brushes and {n_ents} entities in {time.time() - t0:.1f} s. "
                        "Instances, visgroups and brush grouping aren't kept in a compiled map")
    return {"FINISHED"}


def game_map_names(context) -> list[str]:
    from .ops import game_content, game_root
    root = game_root(context)
    content = game_content(root) if root else None
    if content is None:
        return []
    names = {f[5:-4] for f in content.files if f.startswith("maps/") and f.endswith(".bsp") and "/" not in f[5:]}
    for sub in ("left4dead2", "update", "left4dead2_dlc1", "left4dead2_dlc2", "left4dead2_dlc3"):
        folder = os.path.join(root, sub, "maps")       # (the campaigns' maps are loose files here)
        try:
            names |= {f[:-4] for f in os.listdir(folder) if f.lower().endswith(".bsp")}
        except OSError:
            pass
    return sorted(names, key=str.lower)


def _search_maps(self, context, edit_text):
    t = edit_text.lower()
    return [n for n in game_map_names(context) if t in n.lower()]


class HL_OT_import_game_map(bpy.types.Operator):
    bl_idname = "hammerless.import_game_map"
    bl_label = "Import L4D2 Map"
    bl_description = ("Bring in one of the game's own maps (c1m1_hotel...) to look at or remix: decompiled from the "
                      "game's files into brushes, terrain, entities and props")
    bl_options = {"REGISTER", "UNDO"}
    map: bpy.props.StringProperty(name="Map", default="c1m1_hotel", search=_search_maps,
                                  description="The game's map to import (type to search: c1m1_hotel, c2m1_highway...)")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from .ops import game_content, game_root
        root = game_root(context)
        content = game_content(root) if root else None
        data = content.read(f"maps/{self.map}.bsp") if content and self.map else None
        if not data:
            self.report({"ERROR"}, "Left 4 Dead 2 or the map wasn't found (Settings > Game & Folders)")
            return {"CANCELLED"}
        return import_bsp(self, context, data, self.map)


class HL_OT_import_bsp(bpy.types.Operator, ImportHelper):
    bl_idname = "hammerless.import_bsp"
    bl_label = "Import Compiled Map"
    bl_description = "Bring in a compiled Left 4 Dead 2 map (.bsp), decompiled into brushes, terrain, entities and props"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".bsp"
    filter_glob: bpy.props.StringProperty(default="*.bsp", options={"HIDDEN"})

    def execute(self, context):
        try:
            with open(self.filepath, "rb") as f:
                data = f.read()
        except OSError as ex:
            self.report({"ERROR"}, f"Couldn't read the map: {ex}")
            return {"CANCELLED"}
        return import_bsp(self, context, data, os.path.splitext(os.path.basename(self.filepath))[0])


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
    sky = (doc.world.get("skyname") or "").strip()
    if sky:                                     # (the Sky setting shows the map's, and writes it back)
        s.skyname = sky
        s.sky_custom = False
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

def _faces(obj, upm: float, materials, depsgraph=None) -> list[tuple]:
    """(side id, corners, material, texture axes or None) per face; axes for faces painted with the texturing
    tools (from their UVs). The shape after its modifiers (what the viewport shows), as new brushes are."""
    ev = obj.evaluated_get(depsgraph) if depsgraph is not None and obj.modifiers else None
    if ev is not None:
        try:
            return _mesh_faces(obj, ev.to_mesh(), upm, materials)
        finally:
            ev.to_mesh_clear()
    return _mesh_faces(obj, obj.data, upm, materials)


def _mesh_faces(obj, mesh, upm: float, materials) -> list[tuple]:
    from .extract import PAINTED, texture_size
    m = obj.matrix_world
    attr = mesh.attributes.get("hl_side")
    ids = [0] * len(mesh.polygons)
    if attr is not None and attr.domain == "FACE":
        attr.data.foreach_get("value", ids)
    pa = mesh.attributes.get(PAINTED)
    painted = [0] * len(mesh.polygons)
    if pa is not None and pa.domain == "FACE":
        pa.data.foreach_get("value", painted)
    uv = mesh.uv_layers.active
    mirrored = m.determinant() < 0
    out = []
    for poly, sid, pnt in zip(mesh.polygons, ids, painted):
        pts = [tuple(c * upm for c in (m @ mesh.vertices[v].co)) for v in poly.vertices]
        mat = obj.material_slots[poly.material_index].material if poly.material_index < len(obj.material_slots) else None
        path = materials.resolve(mat)[0] if materials is not None else (mat.name if mat else "")
        tex = None
        if pnt and uv is not None:
            from ..core.texalign import axes_from_uv
            w, h = texture_size(mat)
            tex = axes_from_uv(pts, [tuple(uv.data[li].uv) for li in poly.loop_indices], w, h)
        if mirrored:                  # (a mirrored object's faces wind the other way in the world: its planes
            pts = pts[::-1]           # would face inward)
        out.append((sid or None, pts, path, tex))
    return out


def _originals(objs, kind: str, key: str = ID) -> dict:
    """id -> the object that is the original: the one still named as imported, else (renamed) the only one. A copy
    keeps the tags but not the name: it's a new object, built like any other (written_back)."""
    out = {}
    for o in objs:
        if o.get(KIND) == kind and o.get(key) is not None:
            if o.get(NAME) == o.name or o[key] not in out:
                out[o[key]] = o
    return out


def written_back(objs) -> set:
    """The names of the imported map's objects that rebuild writes back (the rest, copies of them included, are
    built as new objects)."""
    out = {o.name for o in objs if o.get(KIND) == "world"}
    for kind in ("solid", "disp"):
        out |= {o.name for o in _originals(objs, kind).values()}
    out |= {o.name for o in _originals(objs, "entity", INDEX).values()}
    return out


_DOC: dict = {"key": None, "doc": None}    # the imported file, parsed: kept while its text is the same


def _document(text, lightmap_scale_imported: bool) -> vi.Document:
    """The imported file parsed (4.6 MB takes 1.5 s): again only when its text changed. Rebuilding never changes the
    parsed blocks, except Imported Brushes Too's lightmap scales, which every build then sets again (so it's part of
    the key: turned off, the file's own scales come back)."""
    import hashlib
    raw = text.as_string()
    key = (text.name, len(raw), hashlib.sha1(raw.encode("utf-8", "surrogatepass")).hexdigest(), lightmap_scale_imported)
    if _DOC["key"] != key:
        _DOC.update(key=key, doc=vi.Document(raw))
    return _DOC["doc"]


def rebuild(context, writer, materials, report) -> tuple[vi.Document, Block, list]:
    """The imported file with the scene's changes: (document, world block, entity blocks (None: deleted))."""
    from .extract import mesh_to_brushes, object_keyvalues
    s = context.scene.hammerless
    upm = s.units_per_meter
    doc = _document(bpy.data.texts[context.scene[SOURCE_KEY]], s.lightmap_scale_imported)
    writer._next_id = max(writer._next_id, doc.max_id() + 1)
    from .mapcollection import map_objects
    inside = map_objects(context.scene)
    objs = [o for o in context.scene.objects if o.name in inside]
    solids = {**_originals(objs, "solid"), **_originals(objs, "disp")}
    entities = _originals(objs, "entity", INDEX)
    back = written_back(objs)
    depsgraph = context.evaluated_depsgraph_get()

    def default_side(verts, material, tex=None):
        from ..core.ir import Polygon
        return writer.side(Polygon(list(verts), material or "tools/toolsnodraw", tex_axes=tex))

    def scale_of(material: str):
        """The lightmap scale for an imported side (Imported Brushes Too): its material's own, else the scene's."""
        m = bpy.data.materials.get(material.lower().replace("\\", "/"))
        own = m.hammerless.lightmap_scale if m is not None else 0
        return own or s.lightmap_scale

    raised = [0]

    def solid(block: Block):
        out = _solid(block)
        if out is not None and s.lightmap_scale_imported:
            raised[0] += vi.set_lightmap_scale(out, scale_of)
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
        return vi.solid_block(block, _faces(obj, upm, materials, depsgraph), writer.new_id, default_side)

    children: dict = {}                 # (each object's children in the map, listed once: not per entity)
    for o in objs:
        if o.parent is not None:
            children.setdefault(o.parent.name, []).append(o)

    def new_solids(parent):
        out = []
        for o in children.get(parent.name, ()):
            if (o.type == "MESH" and o.name not in back and o.get(KIND) != "disp"
                    and o.visible_get()):          # (new meshes, and copies of its imported brushes)
                for b in mesh_to_brushes(o, depsgraph, upm, materials):
                    out.append(writer.solid(b))
        return out

    wobj = next((o for o in objs if o.get(KIND) == "world"), None)
    world = doc.world
    sky = s.skyname.strip()                     # (the Sky setting is the map's sky)
    if wobj is not None:
        values = {"classname": "worldspawn", **object_keyvalues(wobj)}
        if sky:
            values["skyname"] = sky
        world = vi.set_entity_values(world, values, None)
    elif sky and sky != (world.get("skyname") or "").strip():
        keep = {it[0]: it[1] for it in world.items if isinstance(it, tuple) and it[0].lower() != "id"}
        world = vi.set_entity_values(world, {**keep, "skyname": sky}, None)
    world = vi.with_solids(world, [b for b in (solid(x) for x in doc.world.blocks("solid")) if b is not None])
    world = Block(world.name, list(world.items))    # (the build adds its new brushes to it: not to the kept file's)
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
                from ..core.vmf import fmt_vec
                values["origin"] = fmt_vec(origin)
            else:
                values["origin"] = orig_origin
            if e.get("angles") is not None and e.blocks("solid"):
                values["angles"] = e.get("angles")      # (a brush entity's brushes carry its shape)
            elif obj.type == "LIGHT":
                if e.get("angles") is not None:
                    values["angles"] = e.get("angles")
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
        if obj.type == "LIGHT":
            _light_values(obj, values, upm)
        outputs = _outputs(obj, e)
        block = vi.set_entity_values(e, values, outputs)
        kept = [b for b in (solid(x) for x in e.blocks("solid")) if b is not None] + new_solids(obj)
        out_ents.append(vi.with_solids(block, kept) if e.blocks("solid") or kept else block)
    if raised[0]:
        report.info.append(f"Lightmap Scale {s.lightmap_scale}: {raised[0]} displacement(s) are too big for it (a "
                           "displacement's lightmap is at most 125 luxels a side): they use the finest scale that fits")
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

    def times(value):
        f = (value.split("\x1b") if "\x1b" in value else value.split(",")) + ["", "", "", "", ""]
        return f[4].strip() or "-1"
    now = [(o.output, o.target, o.input, o.parameter, round(o.delay, 4), o.only_once) for o in obj.hammerless.outputs]
    if now == [norm(*r) for r in raw]:
        return None
    out = []
    for i, o in enumerate(obj.hammerless.outputs):
        if i < len(raw) and norm(*raw[i]) == now[i]:
            out.append(raw[i])
            continue
        # (an output that fired a set number of times keeps it: the imported one with the same output, target
        # and input, wherever it is now; a delay with its digits, not 6 of them)
        was = next((r for r in raw if norm(*r)[:3] == (o.output, o.target, o.input)), None)
        n = "1" if o.only_once else (times(was[1]) if was is not None and times(was[1]) != "1" else "-1")
        delay = f"{o.delay:.4f}".rstrip("0").rstrip(".") or "0"
        fields = (o.target, o.input, o.parameter, delay, n)
        sep = "\x1b" if any("," in x for x in fields) else ","   # (a parameter with commas: Hammer's own separator)
        out.append((o.output, sep.join(fields)))
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
    self.layout.operator(HL_OT_import_game_map.bl_idname, text="L4D2 Map (from the game) [Hammerless]")
    self.layout.operator(HL_OT_import_bsp.bl_idname, text="Compiled Map (.bsp) [Hammerless]")


CLASSES = (HL_OT_import_vmf, HL_OT_import_game_map, HL_OT_import_bsp)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_func)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
