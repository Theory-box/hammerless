"""Blender scene -> MapIR (the only place that reads scene geometry)."""
from __future__ import annotations

import math
import os
import re

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from ..core import textures
from ..core.displacement import verts_per_side
from ..core.entities import CATALOG
from ..core.surfaces import write_patch_material
from ..core.ir import Brush, Entity, MapIR, MapSettings, Output, Polygon, Terrain

LIGHT_CLASS = {"POINT": "light", "SPOT": "light_spot", "SUN": "light_environment", "AREA": "light"}


# ---------------------------------------------------------------- roles

def collection_role(obj) -> str:
    """Nearest collection role up the hierarchy, or NONE."""
    parents = {}
    for coll in bpy.data.collections:
        for child in coll.children:
            parents[child] = coll
    for coll in obj.users_collection:
        c = coll
        while c is not None:
            if hasattr(c, "hammerless") and c.hammerless.role != "NONE":
                return c.hammerless.role
            c = parents.get(c)
    return "NONE"


def detail_choice(obj) -> str:
    """AUTO, DETAIL or WORLD: the object's own Detail choice, else its nearest collection's."""
    if obj.hammerless.brush_detail != "AUTO":
        return obj.hammerless.brush_detail
    parents = {}
    for coll in bpy.data.collections:
        for child in coll.children:
            parents[child] = coll
    for coll in obj.users_collection:
        c = coll
        while c is not None:
            if hasattr(c, "hammerless") and c.hammerless.brush_detail != "NONE":
                return c.hammerless.brush_detail
            c = parents.get(c)
    return "AUTO"


def _world_brushes(obj, *args) -> list:
    brushes = mesh_to_brushes(obj, *args)
    choice = detail_choice(obj)
    for b in brushes:
        b.detail = choice
    return brushes


def effective_role(obj) -> str:
    role = obj.hammerless.role
    if role != "AUTO":
        return role
    crole = collection_role(obj)
    if crole == "IGNORE":
        return "IGNORE"
    if obj.type == "LIGHT":
        return "LIGHT"
    if obj.type == "EMPTY":
        return "ENTITY" if obj.hammerless.classname else "IGNORE"
    if obj.type == "MESH":
        if obj.hammerless.classname and obj.hammerless.classname in CATALOG:
            return "BRUSH_ENTITY" if CATALOG[obj.hammerless.classname].brush else "ENTITY"
        return crole if crole in ("BRUSH", "TERRAIN") else "BRUSH"
    return "IGNORE"


# ---------------------------------------------------------------- materials

class MaterialResolver:
    """Maps Blender materials to Source material paths, exporting custom textures."""

    def __init__(self, settings, game_dir: str | None, report):
        self.settings = settings
        self.game_dir = game_dir
        self.report = report
        self.cache: dict[str, str] = {}
        self.exported: list[str] = []

    def resolve(self, mat) -> tuple[str, float, int]:
        """Blender material -> (Source material path, texture scale, lightmap scale)."""
        lm_default = self.settings.lightmap_scale
        if mat is None:
            return self.settings.default_material, 0.25, lm_default
        hs = mat.hammerless
        lm = hs.lightmap_scale or lm_default
        if mat.name not in self.cache:
            self.cache[mat.name] = self._resolve_path(mat)
        return self.cache[mat.name], hs.texture_scale, lm

    def _resolve_path(self, mat) -> str:
        hs = mat.hammerless
        surface = hs.surface if hs.surface != "DEFAULT" else ""
        path = hs.source_material.strip().lower().replace("\\", "/")
        if not path and "/" in mat.name and not mat.name.startswith("hammerless/"):
            path = mat.name.lower()   # material named like a game path
        if path:
            if surface and self.game_dir:
                return self._patch(path, surface)
            return path
        return self._export_custom(mat, surface or "concrete") or self.settings.default_material

    def _patch(self, base: str, surface: str) -> str:
        """Game material with a different surface: a patch VMT that includes the original."""
        safe = re.sub(r"[^a-z0-9_]", "_", base)
        path = f"hammerless/{self.settings.map_name}/patch_{safe}_{surface}"
        write_patch_material(self.game_dir, path, base, surface)
        self.exported.append(path)
        return path

    def _export_custom(self, mat, surfaceprop: str = "concrete") -> str | None:
        img = _base_color_image(mat)
        if img is None:
            if self.game_dir is not None:
                self.report.warnings.append(f"Material '{mat.name}' has no Game Material and no image texture: "
                                            f"it uses the default material")
            return None
        if self.game_dir is None:
            self.report.warnings.append(f"Material '{mat.name}': L4D2 wasn't found, so its texture can't be "
                                        f"converted; it uses the default material")
            return None
        path = f"hammerless/{self.settings.map_name}/{texture_file_name(mat.name)}"
        mode = _alpha_mode(mat)
        sig = _image_signature(img, surfaceprop, mode)
        out = os.path.join(self.game_dir, "materials", *path.split("/"))
        try:
            if sig is None or _TEXTURE_CACHE.get(out) != sig or not (os.path.exists(out + ".vtf")
                                                                     and os.path.exists(out + ".vmt")):
                rgba = image_to_rgba8(img)
                if mode != "OPAQUE" and int(rgba[..., 3].min()) == 255:
                    mode = "OPAQUE"                  # an alpha channel with nothing see-through
                extra = {"$alphatest": "1"} if mode == "CUTOUT" else None
                textures.write_material(self.game_dir, path, rgba, surfaceprop=surfaceprop,
                                        translucent=mode == "BLENDED", extra=extra)
                _TEXTURE_CACHE[out] = sig
            self.exported.append(path)
        except Exception as ex:
            self.report.warnings.append(f"Couldn't convert texture for material '{mat.name}': {ex}")
            return None
        return path


_TEXTURE_CACHE: dict[str, tuple] = {}     # output file -> what it was made from (skips re-converting)


def texture_file_name(name: str) -> str:
    """A material name as a file name: a-z, 0-9 and _, plus a short hash when that changed the name
    (so 'Wall.001' and 'Wall_001' don't overwrite each other's texture)."""
    import hashlib
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower())
    if safe != name.lower():
        safe += "_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    return safe


def _image_signature(img, *extra):
    """What a converted texture depends on, or None when that can't be told (always convert)."""
    if img.is_dirty or img.packed_file is not None or img.source != "FILE":
        return None
    path = bpy.path.abspath(img.filepath)
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path, st.st_mtime, st.st_size, tuple(img.size), img.colorspace_settings.name) + extra


def _alpha_mode(mat) -> str:
    """How the base colour's alpha is used: OPAQUE (not wired to Alpha), CUTOUT ($alphatest) or
    BLENDED ($translucent, for materials set to Blended / Alpha Blend)."""
    if not mat.use_nodes or not mat.node_tree:
        return "OPAQUE"
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            alpha = node.inputs.get("Alpha")
            if alpha is None or not alpha.is_linked:
                return "OPAQUE"
            blended = getattr(mat, "surface_render_method", "") == "BLENDED" or mat.blend_method == "BLEND"
            return "BLENDED" if blended else "CUTOUT"
    return "OPAQUE"


def _base_color_image(mat):
    """The image feeding Base Color (through any nodes in between, e.g. Hue/Saturation or Mix);
    without a Principled BSDF or a wired Base Color, an image node that isn't wired to anything
    else (not a normal or roughness map). None when there's no such image."""
    if not mat.use_nodes or not mat.node_tree:
        return None

    def upstream(node, seen):
        if node in seen:
            return None
        seen.add(node)
        if node.type == "TEX_IMAGE":
            return node.image
        for inp in node.inputs:
            for link in inp.links:
                found = upstream(link.from_node, seen)
                if found is not None:
                    return found
        return None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            inp = node.inputs.get("Base Color")
            if inp and inp.is_linked:
                return upstream(inp.links[0].from_node, set())
    for node in mat.node_tree.nodes:
        if (node.type == "TEX_IMAGE" and node.image and not any(o.is_linked for o in node.outputs)
                and node.image.colorspace_settings.name != "Non-Color"):
            return node.image
    return None


def _pow2_floor(n: int, cap: int = 2048) -> int:
    return min(cap, 1 << max(0, int(math.log2(max(1, n)))))


def image_to_rgba8(img) -> np.ndarray:
    w, h = img.size
    if w == 0 or h == 0:
        raise ValueError("image has no pixels (missing file?)")
    tw, th = _pow2_floor(w), _pow2_floor(h)
    src = img
    if (tw, th) != (w, h):
        src = img.copy()
        src.scale(tw, th)
    px = np.empty(tw * th * 4, dtype=np.float32)
    src.pixels.foreach_get(px)
    if src is not img:
        bpy.data.images.remove(src)
    arr = px.reshape(th, tw, 4)[::-1]           # Blender rows are bottom-up
    if img.colorspace_settings.name != "Non-Color" and img.is_float:
        arr[..., :3] = np.where(arr[..., :3] <= 0.0031308, arr[..., :3] * 12.92,
                                1.055 * np.power(np.clip(arr[..., :3], 0, None), 1 / 2.4) - 0.055)
    return np.clip(arr * 255 + 0.5, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- geometry

def _evaluated_mesh(obj, depsgraph, scale: float, matrix=None):
    """World-space bmesh (modifiers applied) scaled to Hammer units. matrix: where this copy of
    the object is (an instance), else the object's own place."""
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    eval_obj.to_mesh_clear()
    bm.transform(Matrix.Scale(scale, 4) @ (matrix if matrix is not None else obj.matrix_world))
    return bm


def _loose_parts(bm) -> list[list]:
    """Group faces into connected components."""
    bm.faces.ensure_lookup_table()
    seen = set()
    parts = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, comp = [f], []
        seen.add(f.index)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for e in cur.edges:
                for nf in e.link_faces:
                    if nf.index not in seen:
                        seen.add(nf.index)
                        stack.append(nf)
        parts.append(comp)
    return parts


def mesh_to_brushes(obj, depsgraph, scale: float, materials: MaterialResolver, matrix=None,
                    label: str | None = None) -> list[Brush]:
    bm = _evaluated_mesh(obj, depsgraph, scale, matrix)
    slots = [s.material for s in obj.material_slots]
    mirrored = (matrix if matrix is not None else obj.matrix_world).determinant() < 0  # flips face winding
    label = label or obj.name
    brushes = []
    try:
        for i, part in enumerate(_loose_parts(bm)):
            name = label if i == 0 else f"{label} (part {i + 1})"
            if obj.hammerless.use_convex_hull:
                faces = _hull_faces(part)
                mi = part[0].material_index
                mat = slots[mi] if mi < len(slots) else None
                polys = [Polygon(verts, *materials.resolve(mat)) for verts in faces]
            else:
                polys = []
                for f in part:
                    mat = slots[f.material_index] if f.material_index < len(slots) else None
                    path, tscale, lmscale = materials.resolve(mat)
                    verts = [tuple(v.co) for v in f.verts]
                    if mirrored:
                        verts.reverse()
                    polys.append(Polygon(verts, path, tscale, lmscale))
            brushes.append(Brush(polys, name))
    finally:
        bm.free()
    return brushes


def _hull_faces(part_faces) -> list[list[tuple]]:
    bm = bmesh.new()
    for v in {v for f in part_faces for v in f.verts}:
        bm.verts.new(v.co)
    res = bmesh.ops.convex_hull(bm, input=bm.verts[:])
    for g in res.get("geom_interior", []) + res.get("geom_unused", []):
        if isinstance(g, bmesh.types.BMVert) and g.is_valid:
            bm.verts.remove(g)
    bmesh.ops.dissolve_limit(bm, angle_limit=0.001, verts=bm.verts[:], edges=bm.edges[:])
    bm.normal_update()
    out = [[tuple(v.co) for v in f.verts] for f in bm.faces]
    bm.free()
    return out


def mesh_to_terrain(obj, depsgraph, scale: float, materials: MaterialResolver) -> Terrain:
    hs = obj.hammerless
    power = int(hs.terrain_power)
    n = verts_per_side(power)
    step = n - 1
    patch = hs.terrain_patch_size
    spacing = patch / step

    bm = _evaluated_mesh(obj, depsgraph, scale)
    bm.faces.ensure_lookup_table()
    verts = [v.co.copy() for v in bm.verts]
    polys = [[v.index for v in f.verts] for f in bm.faces]
    vcol = {}           # red channel = blend; from a face-corner or a vertex colour attribute
    color_layer = bm.loops.layers.color.active or bm.loops.layers.float_color.active
    vert_layer = bm.verts.layers.float_color.active or bm.verts.layers.color.active
    if color_layer is None and vert_layer is None:
        color_layer = next(iter(bm.loops.layers.color.values()), None) or next(
            iter(bm.loops.layers.float_color.values()), None)
        vert_layer = next(iter(bm.verts.layers.float_color.values()), None) or next(
            iter(bm.verts.layers.color.values()), None)
    if color_layer is not None:
        for f in bm.faces:
            for loop in f.loops:
                vcol[loop.vert.index] = loop[color_layer][0]
    elif vert_layer is not None:
        for v in bm.verts:
            vcol[v.index] = v[vert_layer][0]
    slots = [s.material for s in obj.material_slots]
    material, _, _ = materials.resolve(slots[0] if slots else None)
    if material == materials.settings.default_material:
        material = "nature/blend_grass_grass_01"
    bm.free()

    if not verts:
        raise ValueError(f"terrain '{obj.name}' has no geometry")
    xs = [v.x for v in verts]; ys = [v.y for v in verts]; zs = [v.z for v in verts]
    # the grid spans the terrain exactly: as many patches as the Patch Size needs, each a little
    # smaller to fit, so the outer samples lie on the terrain's edges. (A grid of fixed cells left a
    # part cell past the far edges whose outer corners missed the terrain and sank: a sloping, unwalkable
    # strip up to a cell wide along those edges.)
    x0, y0 = min(xs), min(ys)
    ext_x, ext_y = max(max(xs) - x0, 1.0), max(max(ys) - y0, 1.0)
    px = max(1, math.ceil(ext_x / patch - 1e-6))
    py = max(1, math.ceil(ext_y / patch - 1e-6))
    spacing, spacing_y = ext_x / (px * step), ext_y / (py * step)
    cols, rows = px * step + 1, py * step + 1

    bvh = BVHTree.FromPolygons(verts, polys)
    kd = None
    if vcol:
        kd = KDTree(len(verts))
        for i, v in enumerate(verts):
            kd.insert(v, i)
        kd.balance()
    top = max(zs) + 64
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
    snap = 1.0 + 1e-6               # a sample this close outside the mesh's bounds is on its edge (float error)
    heights, alphas = [], []
    for r in range(rows):
        hrow, arow = [], []
        for c in range(cols):
            x, y = x0 + c * spacing, y0 + r * spacing_y
            hit, _normal, _idx, _dist = bvh.ray_cast(Vector((x, y, top)), Vector((0, 0, -1)))
            if hit is None:
                # a sample outside the terrain only by float error at its edge is on the edge, not
                # in a hole: take the height just inside the mesh
                cx, cy = min(max(x, lo_x + 1e-3), hi_x - 1e-3), min(max(y, lo_y + 1e-3), hi_y - 1e-3)
                if (cx, cy) != (x, y) and abs(cx - x) <= snap and abs(cy - y) <= snap:
                    hit, _normal, _idx, _dist = bvh.ray_cast(Vector((cx, cy, top)), Vector((0, 0, -1)))
            hrow.append(None if hit is None else hit.z)
            if kd is not None:
                _co, vi, _d = kd.find(Vector((x, y, hit.z if hit else 0)))
                arow.append(vcol.get(vi, 0.0) * 255.0)
            else:
                arow.append(0.0)
        heights.append(hrow)
        alphas.append(arow)
    return Terrain((x0, y0), spacing, heights, power, material, alphas if kd else None, obj.name, spacing_y)


# ---------------------------------------------------------------- entities

def source_angles(matrix_world) -> tuple[float, float, float]:
    """Blender world rotation -> Source (pitch, yaw, roll). Entity forward = +X."""
    e = matrix_world.to_euler("XYZ")
    return (math.degrees(e.y), math.degrees(e.z), math.degrees(e.x))


def object_keyvalues(obj) -> dict[str, str]:
    return {kv.key: kv.value for kv in obj.hammerless.keyvalues if kv.key}


def object_outputs(obj) -> list[Output]:
    return [Output(o.output, o.target, o.input, o.parameter, o.delay, 1 if o.only_once else -1)
            for o in obj.hammerless.outputs if o.output and o.target and o.input]


def light_entity(obj, scale: float) -> Entity:
    lamp = obj.data
    cls = LIGHT_CLASS.get(lamp.type, "light")
    col = " ".join(str(int(round(_linear_to_srgb(c) * 255))) for c in lamp.color)
    origin = tuple(c * scale for c in obj.matrix_world.translation)
    # Blender lights shine along local -Z; Source lights along +X.
    pitch, yaw, roll = source_angles(obj.matrix_world @ Matrix.Rotation(math.radians(90), 4, "Y"))
    kv = object_keyvalues(obj)
    if cls == "light_environment":
        kv.setdefault("_light", f"{col} {int(lamp.energy * 100)}")
        kv.setdefault("_ambient", "140 160 190 80")
        kv.setdefault("pitch", str(round(-pitch)))  # light_environment: negative = pointing down
    else:
        kv.setdefault("_light", f"{col} {int(lamp.energy / 3)}")
        if cls == "light_spot":
            cone = math.degrees(lamp.spot_size) / 2
            kv.setdefault("_cone", str(round(cone)))
            kv.setdefault("_inner_cone", str(round(cone * (1 - lamp.spot_blend))))
    return Entity(cls, origin, (pitch, yaw, roll), kv, source=obj.name)


# ---------------------------------------------------------------- main

def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _rgb(color) -> tuple[int, int, int]:
    return tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in color)


def _ordered(a, b):
    """A min / max pair, the right way round (a panel lets them cross)."""
    return (a, b) if a <= b else (b, a)


def scene_settings_to_ir(s) -> MapSettings:
    mob_min, mob_max = _ordered(s.dir_mob_min, s.dir_mob_max)
    every_min, every_max = _ordered(s.dir_mob_interval_min, s.dir_mob_interval_max)
    fog_start, fog_end = _ordered(s.fog_start, s.fog_end)
    return MapSettings(
        name=s.map_name, skyname=s.skyname, auto_seal=s.auto_seal, auto_detail=s.auto_detail,
        auto_light_environment=s.auto_sun,
        sun_color=_rgb(s.sun_color), sun_brightness=s.sun_brightness,
        sun_pitch=s.sun_pitch, sun_yaw=s.sun_yaw,
        ambient_color=_rgb(s.ambient_color), ambient_brightness=s.ambient_brightness,
        lightmap_scale=s.lightmap_scale, wall_climbs=s.wall_climbs,
        fog_enabled=s.fog_enabled, fog_color=_rgb(s.fog_color), fog_start=fog_start,
        fog_end=fog_end, fog_max_density=s.fog_max_density,
        director_enabled=s.director_enabled, dir_common_limit=s.dir_common_limit,
        dir_mob_min=mob_min, dir_mob_max=mob_max,
        dir_mob_interval_min=every_min, dir_mob_interval_max=every_max,
        dir_max_specials=s.dir_max_specials, dir_special_interval=s.dir_special_interval,
        dir_tank_limit=s.dir_tank_limit, dir_witch_limit=s.dir_witch_limit,
        dir_no_mobs=s.dir_no_mobs, dir_no_wanderers=s.dir_no_wanderers,
        dir_spawns={t: getattr(s, f"dir_spawn_{t}") for t in (
            "tank", "witch", "smoker", "boomer", "hunter", "charger", "jockey", "spitter")},
        debug_log=s.debug_log, debug_interval=s.debug_interval, autotest=s.autotest,
    )


def extract_scene(context, report, game_dir: str | None = None) -> tuple[MapIR, MaterialResolver]:
    s = context.scene.hammerless
    scale = s.units_per_meter
    depsgraph = context.evaluated_depsgraph_get()
    ir = MapIR(settings=scene_settings_to_ir(s))
    materials = MaterialResolver(s, game_dir, report)

    hidden = []
    for obj in context.scene.objects:
        if not obj.visible_get():
            # hidden (H, the eye or monitor icon) or in an excluded / hidden collection: left out of
            # the map, like everything you can't see. Ones hidden by hand are listed
            in_layer = obj.name in context.view_layer.objects
            if (in_layer and (obj.hide_get() or obj.hide_viewport) and obj.hammerless.preset_part == ""
                    and effective_role(obj) in ("BRUSH", "TERRAIN", "BRUSH_ENTITY", "ENTITY")):
                hidden.append(obj.name)
            continue
        role = effective_role(obj)
        if role in ("BRUSH", "BRUSH_ENTITY", "TERRAIN") and obj.type != "MESH":
            report.warnings.append(f"'{obj.name}' is a {obj.type.lower()}, not a mesh, so it can't be a brush or "
                                   f"terrain: convert it (Object > Convert > Mesh) to export it")
            continue
        try:
            if role == "BRUSH" and obj.type == "MESH":
                ir.brushes.extend(_world_brushes(obj, depsgraph, scale, materials))
            elif role == "BRUSH_ENTITY" and obj.type == "MESH":
                cls = obj.hammerless.classname or "func_detail"
                ir.entities.append(Entity(cls, None, (0, 0, 0), object_keyvalues(obj),
                                          mesh_to_brushes(obj, depsgraph, scale, materials), obj.name,
                                          object_outputs(obj)))
            elif role == "TERRAIN" and obj.type == "MESH":
                ir.terrains.append(mesh_to_terrain(obj, depsgraph, scale, materials))
            elif role == "ENTITY":
                if not obj.hammerless.classname:
                    report.warnings.append(f"'{obj.name}' is a Point Entity but has no class. Skipped.")
                    continue
                origin = tuple(c * scale for c in obj.matrix_world.translation)
                ir.entities.append(Entity(obj.hammerless.classname, origin,
                                          source_angles(obj.matrix_world), object_keyvalues(obj), [], obj.name,
                                          object_outputs(obj)))
            elif role == "LIGHT":
                ir.entities.append(light_entity(obj, scale))
        except Exception as ex:
            report.errors.append(f"'{obj.name}': {ex}")
    _extract_instances(context, depsgraph, scale, ir, materials, report)
    if hidden:
        report.warnings.append(f"{len(hidden)} hidden object(s) are left out of the map (Alt+H shows them; a "
                               f"missing wall can make the map leak): {', '.join(sorted(hidden)[:5])}"
                               + (", ..." if len(hidden) > 5 else ""))
    return ir, materials


def _extract_instances(context, depsgraph, scale, ir, materials, report) -> None:
    """Copies made by collection instances (Add > Collection Instance, linked asset kits) and by
    geometry nodes: each copy exports like the object it copies, at the copy's place."""
    skipped = set()
    for inst in depsgraph.object_instances:
        if not inst.is_instance or inst.parent is None:
            continue
        src = inst.object.original
        holder = inst.parent.original
        role = effective_role(src)
        label = f"{holder.name} > {src.name}"
        matrix = inst.matrix_world.copy()
        try:
            if role == "BRUSH" and src.type == "MESH":
                ir.brushes.extend(_world_brushes(src, depsgraph, scale, materials, matrix, label))
            elif role == "BRUSH_ENTITY" and src.type == "MESH":
                cls = src.hammerless.classname or "func_detail"
                ir.entities.append(Entity(cls, None, (0, 0, 0), object_keyvalues(src),
                                          mesh_to_brushes(src, depsgraph, scale, materials, matrix, label), label,
                                          object_outputs(src)))
            elif role == "ENTITY" and src.hammerless.classname:
                origin = tuple(c * scale for c in matrix.translation)
                ir.entities.append(Entity(src.hammerless.classname, origin, source_angles(matrix),
                                          object_keyvalues(src), [], label, object_outputs(src)))
            elif role in ("TERRAIN", "LIGHT"):
                skipped.add(f"{src.name} ({role.lower()})")
        except Exception as ex:
            report.errors.append(f"'{label}': {ex}")
    if skipped:
        report.warnings.append("Instanced terrain and lights aren't exported yet (make them real objects): "
                               + ", ".join(sorted(skipped)[:5]))
