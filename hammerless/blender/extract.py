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
        if img is None or self.game_dir is None:
            return None
        safe = re.sub(r"[^a-z0-9_]", "_", mat.name.lower())
        path = f"hammerless/{self.settings.map_name}/{safe}"
        try:
            rgba = image_to_rgba8(img)
            textures.write_material(self.game_dir, path, rgba, surfaceprop=surfaceprop,
                                    translucent=mat.blend_method in ("BLEND",))
            self.exported.append(path)
        except Exception as ex:
            self.report.warnings.append(f"Couldn't convert texture for material '{mat.name}': {ex}")
            return None
        return path


def _base_color_image(mat):
    if not mat.use_nodes or not mat.node_tree:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            inp = node.inputs.get("Base Color")
            if inp and inp.is_linked and inp.links[0].from_node.type == "TEX_IMAGE":
                return inp.links[0].from_node.image
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
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

def _evaluated_mesh(obj, depsgraph, scale: float):
    """World-space bmesh (modifiers applied) scaled to Hammer units."""
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    eval_obj.to_mesh_clear()
    bm.transform(Matrix.Scale(scale, 4) @ obj.matrix_world)
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


def mesh_to_brushes(obj, depsgraph, scale: float, materials: MaterialResolver) -> list[Brush]:
    bm = _evaluated_mesh(obj, depsgraph, scale)
    slots = [s.material for s in obj.material_slots]
    mirrored = obj.matrix_world.determinant() < 0  # negative scale flips face winding
    brushes = []
    try:
        for i, part in enumerate(_loose_parts(bm)):
            name = obj.name if i == 0 else f"{obj.name} (part {i + 1})"
            if obj.hammerless.use_convex_hull:
                faces = _hull_faces(part)
                mat = slots[part[0].material_index] if slots else None
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
    color_layer = bm.loops.layers.color.active or bm.loops.layers.float_color.active
    vcol = {}
    if color_layer is not None:
        for f in bm.faces:
            for loop in f.loops:
                vcol[loop.vert.index] = loop[color_layer][0]  # red channel = blend
    slots = [s.material for s in obj.material_slots]
    material, _, _ = materials.resolve(slots[0] if slots else None)
    if material == materials.settings.default_material:
        material = "nature/blend_grass_grass_01"
    bm.free()

    if not verts:
        raise ValueError(f"terrain '{obj.name}' has no geometry")
    xs = [v.x for v in verts]; ys = [v.y for v in verts]; zs = [v.z for v in verts]
    x0, y0 = math.floor(min(xs)), math.floor(min(ys))
    px = max(1, math.ceil((max(xs) - x0) / patch))
    py = max(1, math.ceil((max(ys) - y0) / patch))
    cols, rows = px * step + 1, py * step + 1

    bvh = BVHTree.FromPolygons(verts, polys)
    kd = None
    if vcol:
        kd = KDTree(len(verts))
        for i, v in enumerate(verts):
            kd.insert(v, i)
        kd.balance()
    top = max(zs) + 64
    heights, alphas = [], []
    for r in range(rows):
        hrow, arow = [], []
        for c in range(cols):
            x, y = x0 + c * spacing, y0 + r * spacing
            hit, _normal, _idx, _dist = bvh.ray_cast(Vector((x, y, top)), Vector((0, 0, -1)))
            hrow.append(None if hit is None else hit.z)
            if kd is not None:
                _co, vi, _d = kd.find(Vector((x, y, hit.z if hit else 0)))
                arow.append(vcol.get(vi, 0.0) * 255.0)
            else:
                arow.append(0.0)
        heights.append(hrow)
        alphas.append(arow)
    return Terrain((x0, y0), spacing, heights, power, material, alphas if kd else None, obj.name)


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
    col = " ".join(str(int(max(0, min(1, c)) * 255)) for c in lamp.color)
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

def _rgb(color) -> tuple[int, int, int]:
    return tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in color)


def scene_settings_to_ir(s) -> MapSettings:
    return MapSettings(
        name=s.map_name, skyname=s.skyname, auto_seal=s.auto_seal,
        auto_light_environment=s.auto_sun,
        sun_color=_rgb(s.sun_color), sun_brightness=s.sun_brightness,
        sun_pitch=s.sun_pitch, sun_yaw=s.sun_yaw,
        ambient_color=_rgb(s.ambient_color), ambient_brightness=s.ambient_brightness,
        lightmap_scale=s.lightmap_scale,
        fog_enabled=s.fog_enabled, fog_color=_rgb(s.fog_color), fog_start=s.fog_start,
        fog_end=s.fog_end, fog_max_density=s.fog_max_density,
        director_enabled=s.director_enabled, dir_common_limit=s.dir_common_limit,
        dir_mob_min=s.dir_mob_min, dir_mob_max=s.dir_mob_max,
        dir_mob_interval_min=s.dir_mob_interval_min, dir_mob_interval_max=s.dir_mob_interval_max,
        dir_max_specials=s.dir_max_specials, dir_special_interval=s.dir_special_interval,
        dir_tank_limit=s.dir_tank_limit, dir_witch_limit=s.dir_witch_limit,
        dir_no_mobs=s.dir_no_mobs, dir_no_wanderers=s.dir_no_wanderers,
        debug_log=s.debug_log, debug_interval=s.debug_interval,
    )


def extract_scene(context, report, game_dir: str | None = None) -> tuple[MapIR, MaterialResolver]:
    s = context.scene.hammerless
    scale = s.units_per_meter
    depsgraph = context.evaluated_depsgraph_get()
    ir = MapIR(settings=scene_settings_to_ir(s))
    materials = MaterialResolver(s, game_dir, report)

    for obj in context.scene.objects:
        if not obj.visible_get() and obj.hammerless.role == "AUTO":
            continue  # hidden objects are skipped unless explicitly tagged
        role = effective_role(obj)
        try:
            if role == "BRUSH" and obj.type == "MESH":
                ir.brushes.extend(mesh_to_brushes(obj, depsgraph, scale, materials))
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
    return ir, materials
