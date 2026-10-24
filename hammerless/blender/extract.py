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


PAINTED = "hl_tex_uv"      # face attribute: 1 = painted with the texturing tools (its UVs hold its alignment)


def texture_size(mat, remember: bool = False) -> tuple[int, int]:
    """A material's texture size in texels, for UVs <-> texture axes: the game texture's full size, or a custom
    texture's size as exported (power of two, at most 2048). The first time a face is painted it's remembered on the
    material (remember=True), so painting and export always agree even if the preview changes later."""
    if mat is None:
        return 512, 512
    stored = mat.get("hl_tex_size")
    if stored is not None and len(stored) == 2 and stored[0] > 0 and stored[1] > 0:
        return int(stored[0]), int(stored[1])
    size = (512, 512)
    img = _base_color_image(mat)
    if img is not None:
        w, h = img.get("hl_full_size", img.size)
        if w and h:
            size = (int(w), int(h))
            if not (mat.hammerless.source_material or "").strip():
                size = (_pow2_floor(size[0]), _pow2_floor(size[1]))      # (exported power-of-two, see _export_custom)
    if remember:
        mat["hl_tex_size"] = list(size)
    return size


def _world_brushes(obj, *args, **kw) -> list:
    brushes = mesh_to_brushes(obj, *args, **kw)
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
        cls = obj.hammerless.classname.strip()
        if cls in CATALOG:
            return "BRUSH_ENTITY" if CATALOG[cls].brush else "ENTITY"
        if cls:                       # (a class of the game's not in the Add menu, e.g. func_door: a mesh with
            return "BRUSH_ENTITY"     # a classname is a brush entity; point ones are in the catalog)
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
        self.texture_writes: list = []    # (read here, written by write_textures)

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
        path = game_path(mat)
        if path:
            if surface and self.game_dir:
                return self._patch(path, surface)
            return path
        return self._export_custom(mat, surface or "concrete") or self.settings.default_material

    def _patch(self, base: str, surface: str) -> str:
        """Game material with a different surface: a patch VMT that includes the original."""
        import hashlib
        safe = re.sub(r"[^a-z0-9_]", "_", base)
        if safe != base.replace("/", "_") or "_" in base:     # (a/b_c and a_b/c would share a name)
            safe += "_" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:6]
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
            if not _texture_current(out, sig):
                if sig is not None and img.packed_file is None:
                    try:                              # (its file may have changed: Blender keeps the old pixels until
                        img.reload()                  # Image > Reload, so read the file again first)
                    except RuntimeError:
                        pass
                rgba = image_to_rgba8(img)            # (Blender's image: read here, on Blender's thread)
                if mode != "OPAQUE" and int(rgba[..., 3].min()) == 255:
                    mode = "OPAQUE"                  # an alpha channel with nothing see-through
                extra = {"$alphatest": "1"} if mode == "CUTOUT" else None
                # (converted and written by write_textures, on the export's other thread)
                self.texture_writes.append((self.game_dir, path, rgba, surfaceprop, mode == "BLENDED", extra, out, sig,
                                            mat.name))
            self.exported.append(path)
        except Exception as ex:
            self.report.warnings.append(f"Couldn't convert texture for material '{mat.name}': {ex}")
            return None
        return path


_TEXTURE_CACHE: dict[str, tuple] = {}     # output file -> what it was made from (skips re-converting)
_PACKED_DIGESTS: dict[tuple, str] = {}   # (image, packed data block, size) -> its bytes' sha1


@bpy.app.handlers.persistent
def forget_packed_digests(*_args):
    """(load_post, undo_post, redo_post: packed images' memory can be reused, their digests are worked out again)"""
    _PACKED_DIGESTS.clear()


def _texture_current(out: str, sig) -> bool:
    """The texture at `out` (.vtf + .vmt) is already made from `sig`: in this session's cache, or in the .hlsig file
    written beside it (so a Blender restart doesn't convert every texture again)."""
    if sig is None or not (os.path.exists(out + ".vtf") and os.path.exists(out + ".vmt")):
        return False
    if _TEXTURE_CACHE.get(out) == sig:
        return True
    try:
        with open(out + ".hlsig", encoding="utf-8") as f:
            if f.read() == repr(sig):
                _TEXTURE_CACHE[out] = sig
                return True
    except OSError:
        pass
    return False


def write_textures(writes: list, report) -> None:
    """The textures MaterialResolver read (texture_writes): converted and written. Uses no Blender data (the
    export's second half runs it on another thread)."""
    for game_dir, path, rgba, surfaceprop, translucent, extra, out, sig, mat_name in writes:
        try:
            _TEXTURE_CACHE.pop(out, None)
            if os.path.exists(out + ".hlsig"):
                os.remove(out + ".hlsig")
            textures.write_material(game_dir, path, rgba, surfaceprop=surfaceprop, translucent=translucent,
                                    extra=extra)
            _TEXTURE_CACHE[out] = sig
            if sig is not None:
                with open(out + ".hlsig", "w", encoding="utf-8") as f:
                    f.write(repr(sig))
        except Exception as ex:
            report.warnings.append(f"Couldn't convert texture for material '{mat_name}': {ex}")
    writes.clear()


def game_path(mat) -> str:
    """The game material a Blender material uses ("" for a custom one): its Game Material, else its name when that
    looks like a game path (not its .001), as the game names it (no materials/, no .vmt)."""
    if mat is None:
        return ""
    path = mat.hammerless.source_material.strip().lower().replace("\\", "/")
    if not path and "/" in mat.name and not mat.name.startswith("hammerless/"):
        path = re.sub(r"\.\d{3}$", "", mat.name.lower())
    return re.sub(r"\.vmt$", "", re.sub(r"^/*(materials/)?", "", path))


def texture_file_name(name: str) -> str:
    """A material name as a file name: a-z, 0-9 and _, plus a short hash when that changed the name
    (so 'Wall.001' and 'Wall_001' don't overwrite each other's texture)."""
    import hashlib
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower())
    if safe != name:                    # (also 'Wall' and 'wall': the game's files don't care about case)
        safe += "_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    return safe


def _image_signature(img, *extra):
    """What a converted texture depends on, or None when that can't be told (always convert)."""
    if img.is_dirty or img.source != "FILE":
        return None
    if img.packed_file is not None:          # (packed in the .blend: its packed bytes)
        import hashlib
        pf = img.packed_file
        # (hashed once per session: packing again makes new packed data, so the same data block and size are the
        # same bytes; edits to a packed image's pixels show as is_dirty, handled above)
        mem = (img.session_uid, img.filepath_raw, pf.as_pointer(), pf.size)
        digest = _PACKED_DIGESTS.get(mem)
        if digest is None:
            try:
                data = pf.data
            except (AttributeError, RuntimeError):
                return None
            digest = _PACKED_DIGESTS[mem] = hashlib.sha1(data).hexdigest()
        return (("packed", img.name, digest), tuple(img.size), img.colorspace_settings.name, img.alpha_mode) + extra
    path = bpy.path.abspath(img.filepath, library=img.library)
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path, st.st_mtime, st.st_size, tuple(img.size), img.colorspace_settings.name, img.alpha_mode) + extra


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


def _emissive(mat) -> bool:
    """Whether a material glows: a Principled BSDF with Emission Strength above 0 and a colour that isn't black
    (or wired), or an Emission shader. Model materials that glow are self-lit in the game (UnlitGeneric: full
    bright, whatever light falls on them)."""
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return False
    for node in mat.node_tree.nodes:
        if node.type == "EMISSION":
            return any(link.from_node is node for link in mat.node_tree.links)
        if node.type == "BSDF_PRINCIPLED":
            strength, colour = node.inputs.get("Emission Strength"), node.inputs.get("Emission Color")
            if strength is None or colour is None:
                continue
            if (strength.is_linked or strength.default_value > 0) and (
                    colour.is_linked or any(c > 0 for c in tuple(colour.default_value)[:3])):
                return True
    return False


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
    px = np.empty(tw * th * 4, dtype=np.float32)
    try:
        if (tw, th) != (w, h):
            src = img.copy()
            src.scale(tw, th)
        src.pixels.foreach_get(px)
    finally:
        if src is not img:
            bpy.data.images.remove(src)        # (never left behind in the .blend, even on an error)
    arr = px.reshape(th, tw, 4)[::-1]           # Blender rows are bottom-up
    if img.is_float and img.alpha_mode not in ("NONE", "CHANNEL_PACKED"):
        # (float buffers are premultiplied in Blender: the game's alpha is straight, else edges go dark)
        a = arr[..., 3:4]
        arr[..., :3] = np.where(a > 1e-6, arr[..., :3] / np.maximum(a, 1e-6), 0.0)
    if img.colorspace_settings.name != "Non-Color" and img.is_float:
        arr[..., :3] = np.where(arr[..., :3] <= 0.0031308, arr[..., :3] * 12.92,
                                1.055 * np.power(np.clip(arr[..., :3], 0, None), 1 / 2.4) - 0.055)
    return np.clip(arr * 255 + 0.5, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- geometry

def _evaluated_mesh(obj, depsgraph, scale: float, matrix=None, mesh=None, shading=False):
    """World-space bmesh (modifiers applied) scaled to Hammer units. matrix: where this copy of
    the object is (an instance), else the object's own place. mesh: already evaluated geometry to
    use instead of the object's (a Geometry Nodes instance)."""
    bm = bmesh.new()
    from .brushshading import groups
    if mesh is not None:
        bm.from_mesh(mesh)
        flags = groups(mesh) if shading else None
    else:
        eval_obj = obj.evaluated_get(depsgraph)
        evaluated_mesh = eval_obj.to_mesh()
        try:
            bm.from_mesh(evaluated_mesh)
            flags = groups(evaluated_mesh) if shading else None
        finally:
            eval_obj.to_mesh_clear()
    if flags is not None:
        layer = bm.faces.layers.int.new("hl_export_smoothing")
        for face, flag in zip(bm.faces, flags):
            face[layer] = flag if flag < 0x80000000 else flag - 0x100000000
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
                    label: str | None = None, mesh=None) -> list[Brush]:
    bm = _evaluated_mesh(obj, depsgraph, scale, matrix, mesh, shading=True)
    slots = list(mesh.materials) if mesh is not None else [s.material for s in obj.material_slots]
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
                painted = bm.faces.layers.int.get(PAINTED)
                uv = bm.loops.layers.uv.active
                for f in part:
                    mat = slots[f.material_index] if f.material_index < len(slots) else None
                    path, tscale, lmscale = materials.resolve(mat)
                    verts = [tuple(v.co) for v in f.verts]
                    tex = None
                    if painted is not None and uv is not None and f[painted]:
                        from ..core.texalign import axes_from_uv
                        w, h = texture_size(mat)
                        tex = axes_from_uv(verts, [tuple(lp[uv].uv) for lp in f.loops], w, h)
                    if mirrored:
                        verts.reverse()
                    flag = f[bm.faces.layers.int["hl_export_smoothing"]] & 0xffffffff
                    polys.append(Polygon(verts, path, tscale, lmscale, tex, flag))
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
    # red channel = blend; from the mesh's active colour attribute (the one painted), else any
    color_layer = vert_layer = None
    active = getattr(obj.data.color_attributes, "active_color", None)
    if active is not None:
        for layers in (bm.loops.layers.color, bm.loops.layers.float_color):
            color_layer = color_layer or layers.get(active.name)
        for layers in (bm.verts.layers.float_color, bm.verts.layers.color):
            vert_layer = vert_layer or (layers.get(active.name) if color_layer is None else None)
    if color_layer is None and vert_layer is None:
        color_layer = bm.loops.layers.color.active or bm.loops.layers.float_color.active
        vert_layer = bm.verts.layers.float_color.active or bm.verts.layers.color.active
    if color_layer is None and vert_layer is None:
        color_layer = next(iter(bm.loops.layers.color.values()), None) or next(
            iter(bm.loops.layers.float_color.values()), None)
        vert_layer = next(iter(bm.verts.layers.float_color.values()), None) or next(
            iter(bm.verts.layers.color.values()), None)
    # (byte colours come as sRGB, float ones linear: the same paint gives the same blend either way)
    is_byte = (color_layer.name in bm.loops.layers.color.keys() if color_layer is not None else
               vert_layer is not None and vert_layer.name in bm.verts.layers.color.keys())
    as_srgb = (lambda x: x) if is_byte else _linear_to_srgb
    corner_cols = {}    # face -> [(corner, blend)]: a sample takes the blend of the face it lands on
    if color_layer is not None:
        for f in bm.faces:
            corner_cols[f.index] = [(loop.vert.co.copy(), as_srgb(loop[color_layer][0])) for loop in f.loops]
    elif vert_layer is not None:
        for f in bm.faces:
            corner_cols[f.index] = [(v.co.copy(), as_srgb(v[vert_layer][0])) for v in f.verts]
    slots = [s.material for s in obj.material_slots]
    material, tex_scale, lm_scale = materials.resolve(slots[0] if slots else None)
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
    painted = bool(corner_cols)
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
            if painted and hit is not None and _idx in corner_cols:
                # (blended across the face hit, by nearness to its corners: smooth, and never paint from
                # the other side of a cliff)
                ws = [(1.0 / max((co - hit).length, 1e-4), val) for co, val in corner_cols[_idx]]
                arow.append(sum(w * v for w, v in ws) / sum(w for w, _v in ws) * 255.0)
            else:
                arow.append(0.0)
        heights.append(hrow)
        alphas.append(arow)
    return Terrain((x0, y0), spacing, heights, power, material, alphas if painted else None, obj.name, spacing_y,
                   tex_scale, lm_scale)


def _linear_to_srgb(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * 12.92 if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


# ---------------------------------------------------------------- entities

def source_angles(matrix_world) -> tuple[float, float, float]:
    """Blender world rotation -> Source (pitch, yaw, roll). Entity forward = +X."""
    e = matrix_world.to_euler("XYZ")
    return (math.degrees(e.y), math.degrees(e.z), math.degrees(e.x))


# ---------------------------------------------------------------- custom models

PROP_CLASSES = {"STATIC": "prop_static", "DYNAMIC": "prop_dynamic", "PHYSICS": "prop_physics"}


def _model_texture(mat, path: str, content) -> str:
    """The texture a model material shows: a Hammerless-made material's own texture, or the
    $basetexture of the game material it uses."""
    if path.startswith("hammerless/"):
        base = game_path(mat)
        if not base:
            return path                       # a converted image texture: the VTF is at the same path
        path = base                           # a game material with a surface patch: show the original
    if content is not None:
        data = content.read(f"materials/{path.lower()}.vmt")
        shader = (data or b"").decode("latin-1", "replace").lstrip().split(None, 1)[0].strip('"').lower() if data else ""
        if shader and (path.lower().startswith("models/")
                       or shader not in ("lightmappedgeneric", "worldvertextransition", "patch", "unlitgeneric")):
            # a model material of the game's own (VertexLitGeneric, the commons' Infected shader with its colour
            # gradient...): the model uses it as it is, through a material that includes it
            return "include:" + path
        from ..core.gamematerials import base_texture
        tex = base_texture(content, path)     # (patch materials followed to the one they include)
        if tex:
            return tex
    return path


def model_prop(obj, depsgraph, scale: float, materials: MaterialResolver, ir, content, matrix=None,
               label: str | None = None) -> Entity:
    """A Custom Model object: its mesh as a model (shared by every object with the same mesh, scale and
    settings) and a prop entity placing it. Model space = the object's own axes, scaled to Hammer units."""
    from ..core.models import ModelSpec, safe_name, PHYSICS
    hs = obj.hammerless
    matrix = matrix if matrix is not None else obj.matrix_world
    loc, rot, sca = matrix.decompose()
    kind = hs.model_kind
    arm = obj.find_armature()
    if arm is not None:
        kind = "DYNAMIC"                 # (an animated model: the game plays animations on a prop_dynamic)
    collide = hs.model_collision if kind != "PHYSICS" else "HULLS"
    # the model is the mesh with modifiers applied: with any on, objects sharing the mesh can differ (an Armature
    # modifier only poses it: the model is taken at rest, its animations from the bones)
    modified = any(m.show_viewport and not (arm is not None and m.type == "ARMATURE") for m in obj.modifiers)
    shape = f"{obj.data.name}@{obj.name}" if modified else obj.data.name
    rig = ""
    if arm is not None:              # (copies of a rigged model share one model: the same rig, actions and place on
        rel = arm.matrix_world.inverted() @ obj.matrix_world      # the rig; each prop still plays its own copy)
        own = any(len(pb.constraints) for pb in arm.pose.bones) or (
            arm.animation_data is not None and len(arm.animation_data.drivers))
        scene = bpy.context.scene
        actions = _armature_actions(arm)
        rig_print = None if own else _rig_fingerprint(arm, actions, scene.render.fps / (scene.render.fps_base or 1.0))
        rig = (rig_print or arm.data.name, tuple(a.get("hl_name") or a.name for a in actions) if rig_print else
               tuple(a.name for a in actions), tuple(round(v, 4) for row in rel for v in row),
               arm.data.pose_position, arm.name if own else "",   # (constraints / drivers: this rig moves its own way)
               _rig_attachments(ir, arm))
        if rig_print and not modified:
            # (by content, not by name: duplicated props each have their own mesh and rig data, the same model)
            shape = _mesh_fingerprint(ir, obj)
    elif not modified:
        # (by content too: a copy-pasted or Shift+D copy has its own mesh data, the same model; e.g. Make
        # Collision's props of copied houses)
        shape = _mesh_fingerprint(ir, obj)
    key = (shape, tuple(round(c, 4) for c in sca), kind, collide, hs.physics_class_name or hs.physics_class, round(hs.model_mass, 3),
           tuple(slot.material.name if slot.material else "" for slot in obj.material_slots), rig)
    name = getattr(ir, "_model_keys", {}).get(key)
    if name is None:
        import hashlib
        stem = safe_name(obj.name if modified else obj.data.name)
        if key[1:] != ((1.0, 1.0, 1.0), "STATIC", "HULLS", key[4], key[5], key[6], ""):
            stem += "_" + hashlib.sha1(repr(key).encode()).hexdigest()[:6]
        folder = f"hammerless/{ir.settings.name}/"
        room = MDL_NAME_MAX - len(folder) - len(".mdl")
        if len(stem) > room:        # (the .mdl holds its name in 64 bytes: studiomdl refuses longer, the game cuts it)
            short = hashlib.sha1(stem.encode()).hexdigest()[:6]
            stem = (stem[:room - 7].rstrip("_") + "_" + short) if room >= 13 else short[:max(room, 1)]
            if room < 6:
                materials.report.errors.append(
                    f"Custom Model '{obj.name}': the Map Name '{ir.settings.name}' is too long for model names (the "
                    f"game keeps {MDL_NAME_MAX} characters of a model's path): give the map a shorter name")
        name, n = folder + stem, 1
        while name in ir.models:              # two meshes whose names clean up the same: _2, _3... (kept in the room)
            n += 1
            tail = f"_{n}"
            name = folder + stem[:max(1, room - len(tail))] + tail
        ir.__dict__.setdefault("_model_keys", {})[key] = name
        ir.models[name] = _model_spec(obj, depsgraph, scale, sca, materials, ir, content, name, kind, collide)
    spec = ir.models[name]
    kv = {"model": f"models/{name}.mdl", "solid": "6" if spec.collision else "0"}
    if kind == "STATIC":
        kv["disableshadows"] = "0"
    if spec.skeleton is not None and spec.skeleton.animations:
        names = [a.name for a in spec.skeleton.animations]
        kv["DefaultAnim"] = next((n for n in names if n.lower() == "idle"), names[0])   # (it loops on its own)
    elif spec.skeleton is not None and spec.skeleton.default_animation:
        kv["DefaultAnim"] = spec.skeleton.default_animation
    kv.update(object_keyvalues(obj))
    if kind == "STATIC" and obj.get("hl_collision_of") is not None and obj.material_slots and all(
            s.material is not None and materials.resolve(s.material)[0].lower().replace("\\", "/")
            == "tools/toolsnodraw" for s in obj.material_slots):
        # (Make Collision's prop, never drawn: our map compiler gives it one leaf, not every leaf it overlaps; the
        # leaves only tell the engine when to draw a prop, and all props share 65,535 of them)
        kv["hl_collision_only"] = "1"
    origin = tuple(c * scale for c in loc)
    turn = rot.to_matrix().to_4x4()
    if spec.skeleton is not None and spec.skeleton.valve_space:
        # (the game's animations turn a character from -Y, how it faces in Blender, to +X: so the prop is turned
        # back a quarter, after the object's own rotation)
        turn = turn @ Matrix.Rotation(-math.pi / 2, 4, "Z")
    return Entity(PROP_CLASSES[kind], origin, source_angles(turn), kv, [], label or obj.name,
                  object_outputs(obj))


def _hull_thickness(hull) -> float:
    """The smallest distance across a convex hull (Hammer units): from each face's plane to the farthest point."""
    import numpy as np
    normals = np.array([tuple(f.normal) for f in hull.faces], dtype=np.float64).reshape(-1, 3)
    first = np.array([tuple(f.verts[0].co) for f in hull.faces], dtype=np.float64).reshape(-1, 3)
    pts = np.array([tuple(v.co) for v in hull.verts], dtype=np.float64).reshape(-1, 3)
    ok = np.linalg.norm(normals, axis=1) >= 1e-9
    if not ok.any() or not len(pts):
        return 0.0
    normals, first = normals[ok], first[ok]
    d0 = (normals * first).sum(axis=1)
    return float(np.abs(pts @ normals.T - d0).max(axis=0).min())   # (each face: its farthest point; the least)


def _model_spec(obj, depsgraph, scale, sca, materials, ir, content, name, kind, collide):
    from ..core.models import ModelSpec
    from ..core.phywrite import MAX_PIECE_TRIANGLES
    arm = obj.find_armature()
    rest_was = None
    if arm is not None:                      # the mesh as modelled (rest pose): the animations come from the bones
        rest_was = arm.data.pose_position
        arm.data.pose_position = "REST"
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    skeleton = None
    game_rest = None
    try:
        flip = (sca.x * sca.y * sca.z) < 0       # a mirrored copy: keep the faces facing out
        size = Vector((sca.x * scale, sca.y * scale, sca.z * scale))
        game_rel = None
        if arm is not None and arm.get("hl_game_model"):
            # (a game skeleton is in the armature's space: the mesh is taken there, wherever its object is)
            game_rel = arm.matrix_world.inverted() @ obj.matrix_world
            game_nrm = game_rel.to_3x3().inverted().transposed()
            size = Vector((scale, scale, scale))
            flip = game_rel.to_3x3().determinant() < 0
        mesh.calc_loop_triangles()
        uv = mesh.uv_layers.active.data if mesh.uv_layers.active else None
        normals = mesh.corner_normals if hasattr(mesh, "corner_normals") else None
        slots = obj.material_slots
        mat_names: dict[int, str] = {}
        triangles = []
        tri_vis = []
        deferred = None
        if arm is None and game_rel is None:
            # (copied out here, made into triangles by _triangles_from on the export's other thread)
            import numpy as np
            nt, nl = len(mesh.loop_triangles), len(mesh.loops)
            t_loops = np.empty(nt * 3, np.int32); mesh.loop_triangles.foreach_get("loops", t_loops)
            t_verts = np.empty(nt * 3, np.int32); mesh.loop_triangles.foreach_get("vertices", t_verts)
            t_mat = np.empty(nt, np.int32); mesh.loop_triangles.foreach_get("material_index", t_mat)
            co = np.empty(len(mesh.vertices) * 3, np.float32); mesh.vertices.foreach_get("co", co)
            nrm = np.empty(nl * 3, np.float32)
            (normals if normals is not None else mesh.loops).foreach_get("vector" if normals is not None else "normal", nrm)
            uvs = None
            if uv is not None:
                uvs = np.empty(nl * 2, np.float32); uv.foreach_get("uv", uvs)
            for mi in t_mat.tolist():            # (the materials in the order the triangles meet them, as before)
                if mi in mat_names:
                    continue
                mat = slots[mi].material if mi < len(slots) else None
                path, _ts, _lm = materials.resolve(mat)
                mname = texture_file_name(mat.name) if mat is not None else "_empty_slot"
                mode = _alpha_mode(mat) if mat is not None else "OPAQUE"
                tool = path.lower().replace("\\", "/")
                nodraw = {"tools/toolsnodraw": "nodraw", "tools/toolsinvisible": "invisible"}.get(tool, False)
                ir.model_materials[mname] = (_model_texture(mat, path, content), mode == "BLENDED", mode == "CUTOUT",
                                             False, _emissive(mat), nodraw)
                mat_names[mi] = mname
            deferred = (t_loops, t_verts, t_mat, co, nrm, uvs, dict(mat_names), (sca.x, sca.y, sca.z),
                        (size.x, size.y, size.z), flip)
        for tri in ([] if deferred is not None else mesh.loop_triangles):
            mi = tri.material_index
            if mi not in mat_names:
                mat = slots[mi].material if mi < len(slots) else None
                path, _ts, _lm = materials.resolve(mat)
                mname = texture_file_name(mat.name) if mat is not None else "_empty_slot"
                mode = _alpha_mode(mat) if mat is not None else "OPAQUE"
                tool = path.lower().replace("\\", "/")
                nodraw = {"tools/toolsnodraw": "nodraw", "tools/toolsinvisible": "invisible"}.get(tool, False)
                ir.model_materials[mname] = (_model_texture(mat, path, content), mode == "BLENDED", mode == "CUTOUT",
                                             False, _emissive(mat), nodraw)
                mat_names[mi] = mname
            verts = []
            for li, vi in zip(tri.loops, tri.vertices):
                co = mesh.vertices[vi].co
                n = normals[li].vector if normals is not None else mesh.loops[li].normal
                if game_rel is not None:
                    co, n = game_rel @ co, (game_nrm @ n).normalized()
                else:
                    n = Vector((n.x / (sca.x or 1), n.y / (sca.y or 1), n.z / (sca.z or 1))).normalized()
                p = (co.x * size.x, co.y * size.y, co.z * size.z)
                t = uv[li].uv if uv is not None else (0.0, 0.0)
                verts.append((p, (n.x, n.y, n.z), (t[0], t[1])))
            if flip:
                verts.reverse()
            triangles.append((mat_names[mi], tuple(verts)))
            if arm is not None:
                tri_vis.append(tuple(reversed(tri.vertices)) if flip else tuple(tri.vertices))
        pieces = []
        if collide == "HULLS":
            bm = bmesh.new()
            bm.from_mesh(mesh)
            bm.transform(Matrix.Diagonal((size.x, size.y, size.z, 1.0)) @ (game_rel if game_rel is not None
                                                                              else Matrix.Identity(4)))
            for part in _loose_parts(bm):
                pts = [tuple(v.co) for f in part for v in f.verts]
                hull = bmesh.new()
                for p in {tuple(round(c, 4) for c in q) for q in pts}:
                    hull.verts.new(p)
                if len(hull.verts) >= 4:
                    res = bmesh.ops.convex_hull(hull, input=hull.verts[:])
                    for g in res.get("geom_interior", []) + res.get("geom_unused", []):
                        if isinstance(g, bmesh.types.BMVert) and g.is_valid:
                            hull.verts.remove(g)
                    bmesh.ops.triangulate(hull, faces=hull.faces[:])
                    hull.verts.index_update()
                    if len(hull.faces) > MAX_PIECE_TRIANGLES:
                        # more than the collision file can number: left out rather than written broken
                        materials.report.warnings.append(
                            f"Custom Model '{obj.name}': a collision piece has {len(hull.faces)} triangles (at most "
                            f"{MAX_PIECE_TRIANGLES}), so that piece has no collision: split it into smaller parts "
                            "or use a simpler shape")
                    elif hull.faces and _hull_thickness(hull) > 0.5:      # a flat part (a plane) has no inside
                        pieces.append(([tuple(v.co) for v in hull.verts],
                                       [tuple(v.index for v in f.verts) for f in hull.faces]))
                hull.free()
            bm.free()
        if arm is not None and flip:
            materials.report.warnings.append(
                f"Custom Model '{obj.name}' is mirrored (a negative scale): animated models can't be mirrored yet, so "
                "it's built still. Apply the scale, or mirror the mesh and armature themselves")
        elif arm is not None and arm.get("hl_game_model"):
            from .gameskeleton import game_skeleton
            skeleton, game_rest = game_skeleton(obj, arm, mesh, tri_vis, size, materials.report, content)
        elif arm is not None:
            skeleton = _skeleton(obj, arm, mesh, tri_vis, size, materials.report)
    finally:
        eval_obj.to_mesh_clear()
        if rest_was is not None:
            arm.data.pose_position = rest_was
            bpy.context.view_layer.update()
    if skeleton is not None and not CHECK_ONLY["on"]:     # (Check the Map: no need to play every animation)
        _sample_actions(obj, arm, skeleton, size, materials.report, game_rest)
        for m in set(t[0] for t in triangles):     # (its materials keep their colour on the infected: see models.py)
            if m in ir.model_materials:
                spec = ir.model_materials[m]
                ir.model_materials[m] = (*spec[:3], True, *spec[4:])
    if kind == "PHYSICS" and not pieces:
        materials.report.warnings.append(
            f"Custom Model '{obj.name}' is a Physics Prop but has no solid part (every part is flat or thinner than "
            "half a unit): the game removes a physics prop it can't simulate. Give it some thickness, or make it "
            "Static or Dynamic")
    surface = "default"
    for slot in obj.material_slots:
        if slot.material is not None and slot.material.hammerless.surface not in ("", "DEFAULT"):
            surface = slot.material.hammerless.surface
            break
    spec = ModelSpec(name, triangles, pieces, kind, surface, obj.hammerless.model_mass, obj.hammerless.physics_class_name or obj.hammerless.physics_class,
                     f"models/hammerless/{ir.settings.name}/", skeleton)
    if deferred is not None:
        spec._deferred = deferred            # (fill_triangles makes spec.triangles)
    return spec


def _triangles_from(t_loops, t_verts, t_mat, co, nrm, uvs, mat_names, sca, size, flip) -> list:
    """A still model's triangles from the arrays _model_spec copied out of its mesh: the same arithmetic as its
    loop over the mesh (mathutils Vectors of the same float32 values), so the same numbers bit for bit. Uses no
    Blender data: runs on the export's other thread."""
    sx, sy, sz = (Vector((sca[0], sca[1], sca[2])))         # (the object's scale as mathutils stored it: floats)
    sz_ = Vector((size[0], size[1], size[2]))
    out = []
    lo, ve, ma = t_loops.tolist(), t_verts.tolist(), t_mat.tolist()
    cl, nl_ = co.tolist(), nrm.tolist()
    ul = uvs.tolist() if uvs is not None else None
    import time
    for t in range(len(ma)):
        if not t & 2047:
            time.sleep(0)                      # (lets Blender's own thread have the interpreter: it draws the UI)
        verts = []
        for k in range(3):
            li, vi = lo[3 * t + k], ve[3 * t + k]
            c = Vector((cl[3 * vi], cl[3 * vi + 1], cl[3 * vi + 2]))
            n = Vector((nl_[3 * li], nl_[3 * li + 1], nl_[3 * li + 2]))
            n = Vector((n.x / (sx or 1), n.y / (sy or 1), n.z / (sz or 1))).normalized()
            p = (c.x * sz_.x, c.y * sz_.y, c.z * sz_.z)
            uvp = (ul[2 * li], ul[2 * li + 1]) if ul is not None else (0.0, 0.0)
            verts.append((p, (n.x, n.y, n.z), (uvp[0], uvp[1])))
        if flip:
            verts.reverse()
        out.append((mat_names[ma[t]], tuple(verts)))
    return out


def fill_triangles(ir) -> None:
    """The deferred models' triangles (_model_spec): made now, on whatever thread runs this."""
    for spec in ir.models.values():
        d = spec.__dict__.pop("_deferred", None)
        if d is not None:
            spec.triangles[:] = _triangles_from(*d)


# ---------------------------------------------------------------- animated models (armatures)

def _bone_space(obj, arm):
    """Armature space -> the mesh object's local space, where its vertices are before they're scaled by `size` (the
    object's scale times Hammer units per metre): bones get that same scaling in _local."""
    return obj.matrix_world.inverted() @ arm.matrix_world


def _local(m_parent, m, size) -> tuple:
    """A bone's position and rotation relative to its parent (None: the model), scale dropped, in Hammer units."""
    rel = m if m_parent is None else _unscaled(m_parent).inverted() @ m
    loc, rot, _s = rel.decompose()
    if m_parent is None:
        loc = Vector((loc.x * size.x, loc.y * size.y, loc.z * size.z))
    else:
        loc = loc * size.x        # (children: the model's scale is uniform, checked in _skeleton)
    e = rot.to_euler("XYZ")
    return (loc.x, loc.y, loc.z), (e.x, e.y, e.z)


def _unscaled(m):
    loc, rot, _s = m.decompose()
    return Matrix.LocRotScale(loc, rot, None)


def _deform_bones(arm):
    """The armature's deform bones, parents before children (a bone's parent: its nearest deform ancestor)."""
    out = []

    def walk(b):
        if b.use_deform:
            out.append(b)
        for c in b.children:
            walk(c)
    for b in arm.data.bones:
        if b.parent is None:
            walk(b)
    return out


def _deform_parent(b, index):
    p = b.parent
    while p is not None and p.name not in index:
        p = p.parent
    return index[p.name] if p is not None else -1


def _skeleton(obj, arm, mesh, tri_vis, size, report):
    from ..core import skeletal
    bones = _deform_bones(arm)
    if not bones:
        report.warnings.append(f"Custom Model '{obj.name}': its armature '{arm.name}' has no deform bones, so it's "
                               "built as a still model")
        return None
    if max(size) - min(size) > 1e-4 * max(size):
        report.warnings.append(f"Custom Model '{obj.name}' is scaled unevenly: an animated model's bones need the same "
                               "scale on X, Y and Z (apply the scale)")
    index = {b.name: i for i, b in enumerate(bones)}
    space = _bone_space(obj, arm)
    sk_bones = []
    for b in bones:
        pi = _deform_parent(b, index)
        m = space @ b.matrix_local
        mp = space @ bones[pi].matrix_local if pi >= 0 else None
        pos, rot = _local(mp, m, size)
        sk_bones.append(skeletal.Bone(b.name, pi, pos, rot))
    # vertex weights: vertex groups named after deform bones
    group_bone = {g.index: index[g.name] for g in obj.vertex_groups if g.name in index}
    per_vertex = []
    for v in mesh.vertices:
        per_vertex.append([(group_bone[g.group], g.weight) for g in v.groups if g.group in group_bone])
    weights = [tuple(per_vertex[i] for i in vis) for vis in tri_vis]
    if not any(per_vertex):
        report.warnings.append(f"Custom Model '{obj.name}': none of its vertices is weighted to a bone of '{arm.name}' "
                               "(vertex groups named after the bones), so it moves as one piece with the first bone")
    # attachments: empties parented to a bone
    atts = []
    for child in arm.children:
        if child.type == "EMPTY" and child.parent_type == "BONE" and child.parent_bone in index:
            bi = index[child.parent_bone]
            m = _unscaled(space @ bones[bi].matrix_local).inverted() @ (obj.matrix_world.inverted() @ child.matrix_world)
            loc, rot, _s = m.decompose()
            e = rot.to_euler("XYZ")
            atts.append(skeletal.Attachment(child.name, bi, tuple(loc * size.x), (e.x, e.y, e.z)))
    sk = skeletal.Skeleton(sk_bones, weights, [], atts)
    for p in skeletal.problems(sk):
        report.warnings.append(f"Custom Model '{obj.name}': {p}")
    return sk


def _action_curves(act):
    """An action's F-curves (Blender 4.4's slotted actions keep them in layers / strips / channel bags)."""
    try:
        curves = list(act.fcurves)
        if curves:
            return curves
    except (AttributeError, RuntimeError):
        pass
    out = []
    for layer in getattr(act, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out += list(bag.fcurves)
    return out


def _armature_actions(arm):
    """The armature's animations: its action and the actions on its NLA tracks (Blender's way of keeping several
    per object); with none of those, every action that moves bones of these names."""
    ad = arm.animation_data
    own = []
    if ad is not None:
        if ad.action is not None:
            own.append(ad.action)
        for track in ad.nla_tracks:
            for strip in track.strips:
                if strip.action is not None and strip.action not in own:
                    own.append(strip.action)
    own = [a for a in own if not a.get("hl_preview")]      # (a game animation loaded to look at: the game has it)
    if own:
        return own
    names = {b.name for b in arm.data.bones}
    out = []
    for act in bpy.data.actions:
        if act.get("hl_preview"):
            continue
        for fc in _action_curves(act):
            m = re.match(r'pose\.bones\["(.+?)"\]', fc.data_path)
            if m and m.group(1) in names:
                out.append(act)
                break
    return sorted(out, key=lambda a: a.name.lower())


# Sampled poses, kept between builds: a rig's animation played frame by frame (scene.frame_set) makes Blender update
# the whole scene each frame, about 0.6 s a model on a 3,000-object map. The pose of a rig without constraints or
# drivers depends only on its bones and its actions, so copies of one rig (duplicated props) share one sampling,
# and an unchanged rig isn't sampled again. Key: _rig_fingerprint; value: [(action, cyclic, frames)], each frame
# {bone name: its matrix in armature space}.
_POSE_CACHE: dict[str, list] = {}


def _rig_fingerprint(arm, actions, fps) -> str | None:
    """What a rig's sampled animations depend on: its bones' rest matrices and parents, each action's keys, frame
    range and cycling, and the frame rate. None: don't share or keep its samples (constraints or drivers can follow
    other objects; actions this Blender can't list the curves of)."""
    import hashlib
    if any(len(pb.constraints) for pb in arm.pose.bones):
        return None
    ad = arm.animation_data
    if ad is not None and len(ad.drivers):
        return None
    parts = [round(fps, 4), arm.data.pose_position]
    for b in arm.data.bones:
        pb = arm.pose.bones.get(b.name)
        parts.append((b.name, b.parent.name if b.parent else "",
                      tuple(round(v, 5) for row in b.matrix_local for v in row),
                      b.use_connect, b.use_inherit_rotation, b.inherit_scale, b.use_local_location,
                      pb.rotation_mode if pb is not None else ""))
    for act in actions:
        try:
            curves = _action_curves(act)
        except (AttributeError, RuntimeError):
            return None
        parts.append((act.name, act.get("hl_name"), tuple(round(f, 4) for f in act.frame_range),
                      bool(getattr(act, "use_cyclic", False))))
        for fc in curves:
            parts.append((fc.data_path, fc.array_index, fc.extrapolation, fc.mute,
                          tuple(_settings_of(m) for m in fc.modifiers),
                          tuple((round(k.co.x, 4), round(k.co.y, 6), k.interpolation, k.easing,
                                 round(k.handle_left.x, 4), round(k.handle_left.y, 6),
                                 round(k.handle_right.x, 4), round(k.handle_right.y, 6))
                                for k in fc.keyframe_points)))
    return hashlib.sha1(repr(parts).encode()).hexdigest()


def _mesh_fingerprint(ir, obj) -> str:
    """A mesh's content (its points, faces, UVs, materials and bone weights), worked out once per mesh per build:
    copies with their own mesh data still share one model."""
    import hashlib
    import numpy as np
    me = obj.data
    cache = ir.__dict__.setdefault("_mesh_prints", {})
    key = (me.name_full, tuple(obj.vertex_groups.keys()))
    if key in cache:
        return cache[key]
    if me.shape_keys is not None or me.has_custom_normals:   # (shape keys, custom normals: kept per mesh, as before)
        cache[key] = me.name
        return me.name
    h = hashlib.sha1()
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    h.update(np.round(co, 4).tobytes())
    for coll, attr, kind, n in ((me.loops, "vertex_index", np.int32, 1), (me.polygons, "loop_total", np.int32, 1),
                                (me.edges, "vertices", np.int32, 2), (me.edges, "use_edge_sharp", np.bool_, 1),
                                (me.polygons, "material_index", np.int32, 1), (me.polygons, "use_smooth", np.bool_, 1)):
        a = np.empty(len(coll) * n, kind)
        coll.foreach_get(attr, a)
        h.update(a.tobytes())
    h.update(repr((me.uv_layers.active_index, getattr(me, "use_auto_smooth", None),
                   round(getattr(me, "auto_smooth_angle", 0.0), 6))).encode())
    for layer in me.uv_layers:
        uv = np.empty(len(me.loops) * 2, np.float32)
        layer.data.foreach_get("uv", uv)
        h.update(layer.name.encode() + np.round(uv, 5).tobytes())
    names = [g.name for g in obj.vertex_groups]
    if names:                                # (no vertex groups: no weights to read, vertex by vertex)
        h.update(repr([[(names[g.group] if g.group < len(names) else g.group, round(g.weight, 4)) for g in v.groups]
                       for v in me.vertices]).encode())
    cache[key] = "mesh:" + h.hexdigest()
    return cache[key]


def _rig_attachments(ir, arm) -> tuple:
    """An armature's attachment Empties (name, bone), worked out once per armature per build (arm.children looks
    through every object in the file)."""
    cache = ir.__dict__.setdefault("_rig_attachments", {})
    if arm.name not in cache:
        cache[arm.name] = tuple(sorted((c.name, c.parent_bone) for c in arm.children if c.type == "EMPTY"))
    return cache[arm.name]


def _settings_of(item) -> tuple:
    """An F-modifier's type and settings (a Noise's strength, a Cycles' count...), for _rig_fingerprint."""
    out = [item.type]
    for p in item.bl_rna.properties:
        if p.identifier in ("rna_type", "is_active", "show_expanded", "active") or p.type not in (
                "BOOLEAN", "INT", "FLOAT", "ENUM"):
            continue
        v = getattr(item, p.identifier, None)
        try:
            v = tuple(v)
        except TypeError:
            pass
        out.append((p.identifier, repr(v)))
    return tuple(out)


def _sample_actions(obj, arm, sk, size, report, game_rest=None):
    """Every action of the armature, frame by frame, as an animation (its name; looping when Cyclic Animation is on
    in the action's settings)."""
    from ..core import skeletal
    actions = _armature_actions(arm)
    if not actions:
        return
    scene = bpy.context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    key = _rig_fingerprint(arm, actions, fps) if game_rest is None else None
    if key is not None and key in _POSE_CACHE:          # (sampled already: this build or an earlier one)
        _animations_from_poses(sk, _POSE_CACHE[key], _bone_space(obj, arm), size, fps)
        return
    raw: list = []                                      # (what this sampling adds to the cache)
    ad = arm.animation_data or arm.animation_data_create()
    old_action, old_slot, old_nla = ad.action, getattr(ad, "action_slot", None), ad.use_nla
    ad.use_nla = False                       # (only the action being sampled moves the bones)
    old_frame = scene.frame_current
    space = _bone_space(obj, arm)
    by_name = {b.name: arm.pose.bones.get(b.name) for b in sk.bones}
    parents = [sk.bones[b.parent].name if b.parent >= 0 else None for b in sk.bones]
    used = set()
    # (each action from the rest pose: a bone one action keys doesn't carry over into the next; the rig is put
    # back as it was, and sampled in Pose Position even if it was left in Rest Position)
    basis = {pb.name: pb.matrix_basis.copy() for pb in arm.pose.bones}
    old_position = arm.data.pose_position
    arm.data.pose_position = "POSE"
    try:
        for act in actions:
            for pb in arm.pose.bones:
                pb.matrix_basis = Matrix.Identity(4)
            ad.action = act
            slots = list(getattr(act, "slots", []))
            if slots and hasattr(ad, "action_slot"):
                ad.action_slot = next((sl for sl in slots if getattr(sl, "target_id_type", "") == "OBJECT"), slots[0])
            start, end = (int(round(f)) for f in act.frame_range)
            cyclic = bool(getattr(act, "use_cyclic", False))
            if cyclic and end > start:
                end -= 1             # (a loop's last frame is its first again: written once)
            frames, poses = [], []
            for f in range(start, max(end, start) + 1):
                scene.frame_set(f)
                if game_rest is not None:           # (a game skeleton: the pose in the game's bone frames)
                    from .gameskeleton import game_pose
                    frames.append(game_pose(arm, sk, game_rest, bpy.context.scene.hammerless.units_per_meter))
                    continue
                if key is not None:
                    poses.append({pb.name: pb.matrix.copy() for pb in arm.pose.bones})
                frame = []
                for b, parent in zip(sk.bones, parents):
                    m = space @ by_name[b.name].matrix
                    mp = space @ by_name[parent].matrix if parent is not None else None
                    frame.append(_local(mp, m, size))
                frames.append(frame)
            # (the animation's name: the action's, or its 'hl_name' property when the action's name must differ,
            # e.g. two models each with an 'idle')
            name = re.sub(r"[^A-Za-z0-9_]+", "_", str(act.get("hl_name") or act.name)).strip("_") or "anim"
            while name.lower() in used:
                name += "_2"
            used.add(name.lower())
            sk.animations.append(skeletal.Animation(name, frames, fps, cyclic))
            raw.append((name, cyclic, poses))
        if key is not None:
            _POSE_CACHE[key] = raw
    finally:
        arm.data.pose_position = old_position
        ad.use_nla = old_nla
        try:
            ad.action = old_action
        except (TypeError, RuntimeError):
            pass
        if old_slot is not None and hasattr(ad, "action_slot"):
            try:
                ad.action_slot = old_slot
            except (TypeError, RuntimeError):
                pass
        scene.frame_set(old_frame)
        for pb in arm.pose.bones:     # (after the frame: re-evaluating the action would undo pose tweaks not keyed)
            if pb.name in basis:
                pb.matrix_basis = basis[pb.name]


MDL_NAME_MAX = 63          # a model's path without "models/", as the .mdl stores it (64 bytes with its end)
CHECK_ONLY = {"on": False}  # (set by Check the Map: the scene is read for problems, models aren't made)


def _animations_from_poses(sk, raw, space, size, fps) -> None:
    """A model's animations from a rig's sampled poses (_POSE_CACHE): each bone relative to its parent, in this
    model's space and size, as _sample_actions works them out while sampling."""
    from ..core import skeletal
    parents = [sk.bones[b.parent].name if b.parent >= 0 else None for b in sk.bones]
    for name, cyclic, poses in raw:
        frames = []
        for pose in poses:
            frames.append([_local(space @ pose[parent] if parent is not None else None, space @ pose[b.name], size)
                           for b, parent in zip(sk.bones, parents)])
        sk.animations.append(skeletal.Animation(name, frames, fps, cyclic))


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
        # Cycles: a point or spot light of P watts gives P / (4 pi d^2) W/m^2 at d metres. vrad (with
        # Hammer's default inverse-square falloff) gives brightness * 100^2 / d_units^2, and 1 W/m^2 is
        # 100 of its units (the same scale as the sun: strength 1 -> brightness 100)
        kv.setdefault("_light", f"{col} {round(lamp.energy * scale * scale / (400 * math.pi))}")
        if cls == "light_spot":
            cone = math.degrees(lamp.spot_size) / 2
            kv.setdefault("_cone", str(round(cone)))
            kv.setdefault("_inner_cone", str(round(cone * (1 - lamp.spot_blend))))
        kv.update(light_style_keyvalues(obj, kv))
    return Entity(cls, origin, (pitch, yaw, roll), kv, source=obj.name)


def light_style_keyvalues(obj, kv: dict[str, str]) -> dict[str, str]:
    """A light's Pattern, Switchable and Starts Off as the game's keys. A switchable light needs a name (the map
    compiler gives each named light a light style of its own, which the game turns on and off): its targetname, or
    one made from the object's name. Keys set by hand win."""
    hs = obj.hammerless
    out = {}
    custom = hs.light_pattern == "CUSTOM"
    switchable = hs.light_switchable or light_in_logic(obj)
    from .lightpresets import preset_slot
    if hs.light_pattern not in ("0", "CUSTOM") and "style" not in kv and preset_slot(obj) is None:
        out["style"] = hs.light_pattern            # (a pattern without its Behaviour: the game's own style)
    if custom and "pattern" not in kv:
        out["pattern"] = "".join(c for c in hs.light_custom.lower() if "a" <= c <= "z") or "m"
    if (switchable or custom or hs.light_start_off) and not kv.get("targetname"):
        out["targetname"] = light_name(obj)
    if hs.light_start_off and "spawnflags" not in kv:
        out["spawnflags"] = "1"                   # (initially dark)
    return out


def light_in_logic(obj) -> bool:
    """A light an Entity Events node uses, or one with a Behaviour: switchable (logic turns it on and off by the name
    its panel shows)."""
    if any(slot.graph is not None for slot in getattr(obj.hammerless, "behaviours", ())):
        return True
    return in_world_logic(obj)


def in_world_logic(obj) -> bool:
    """An Entity Events node (of any graph) acts on this object itself."""
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "HL_LogicTree":
            continue
        for n in tree.nodes:
            if n.bl_idname == "HL_NodeObject" and getattr(n, "target", None) is obj:
                return True
    return False


def light_name(obj) -> str:
    """The name logic and the game know a switchable light by: its targetname, or one made from its name."""
    name = next((kv.value for kv in obj.hammerless.keyvalues if kv.key == "targetname" and kv.value), "")
    if not name:
        from .lightpresets import shared_name
        name = shared_name(obj) or ""         # (lights of one Light Pattern graph: one name, one light style)
    return name or "hl_light_" + (re.sub(r"[^a-z0-9_]+", "_", obj.name.lower()).strip("_") or "light")


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
    from .props import mode_settings_values
    mob_min, mob_max = _ordered(s.dir_mob_min, s.dir_mob_max)
    every_min, every_max = _ordered(s.dir_mob_interval_min, s.dir_mob_interval_max)
    fog_start, fog_end = _ordered(s.fog_start, s.fog_end)
    return MapSettings(
        name=s.map_name, game_mode=s.game_mode, skyname=s.skyname, auto_seal=s.auto_seal, auto_detail=s.auto_detail,
        auto_light_environment=s.auto_sun,
        light_mixing=s.light_mixing and s.light_tool == "HAMMERLESS" and not s.light_exact,
        sun_color=_rgb(s.sun_color), sun_brightness=s.sun_brightness,
        sun_pitch=s.sun_pitch, sun_yaw=s.sun_yaw,
        # Skybox/HDRI supplies the colour; its normalized panorama is scaled by
        # this intensity. A hidden flat-colour swatch must not extinguish it.
        ambient_color=_rgb(s.ambient_color) if s.sky_light == "FLAT" else (255, 255, 255),
        ambient_brightness=s.ambient_brightness,
        lightmap_scale=s.lightmap_scale, wall_climbs=s.wall_climbs,
        fog_enabled=s.fog_enabled, fog_color=_rgb(s.fog_color), fog_start=fog_start,
        fog_end=fog_end, fog_max_density=s.fog_max_density,
        mode_options=mode_settings_values(s),
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


def extract_scene(context, report, game_dir: str | None = None, content=None,
                  defer_textures: bool = False) -> tuple[MapIR, MaterialResolver]:
    s = context.scene.hammerless
    scale = s.units_per_meter
    depsgraph = context.evaluated_depsgraph_get()
    ir = MapIR(settings=scene_settings_to_ir(s))
    if ir.settings.mode_options:
        ir.scripted_mode = True
    materials = MaterialResolver(s, game_dir, report)

    hidden = []
    from .mapcollection import map_collection, map_objects, outside_but_built
    from .vmfimport import INDEX, _originals, written_back
    inside = map_objects(context.scene)
    back = written_back([o for o in context.scene.objects if o.name in inside])
    originals = _originals([o for o in context.scene.objects if o.name in inside], "entity", INDEX)

    def point_copy(obj) -> bool:
        """A copy of an imported point entity (a prop, a light, a spawn): it's built like a new entity. A brush
        entity's copy isn't (its shape is in the parts under the original)."""
        orig = originals.get(obj.get(INDEX))
        model = orig if orig is not None and orig is not obj else obj
        return obj.name not in back and not any(c.type == "MESH" for c in model.children)
    for obj in context.scene.objects:
        if obj.name not in inside:
            continue                  # (outside the map collection: not part of the map)
        if obj.get("hl_vmf_kind") == "disp" and obj.name not in back:
            report.warnings.append(f"'{obj.name}': a copy of an imported displacement isn't built (copying "
                                   "displacements isn't supported yet)")
            continue
        if obj.name in back or (obj.parent is not None and obj.parent.name in back and obj.type == "MESH"
                                and obj.parent.get("hl_vmf_kind") == "entity"):
            continue                  # (an imported map's: Build writes those back itself, blender/vmfimport.py;
            #                            new meshes under its brush entities become their brushes there)
        copy = obj.get("hl_vmf_kind") == "entity" and point_copy(obj)
        if not copy and (obj.get("hl_vmf_kind") == "entity" or (obj.parent is not None and obj.parent.name not in back
                                                              and obj.parent.get("hl_vmf_kind") == "entity")):
            # (a copy of an imported entity: its preview shape isn't its own, and a brush entity's parts are
            # under an Empty: built as it is, it would come out wrong)
            if obj.get("hl_vmf_kind") == "entity":
                report.warnings.append(f"'{obj.name}': a copy of an imported brush entity isn't built yet: add a "
                                       "new one (Add panel), or duplicate it in Hammer")
            continue
        if map_collection(context.scene) is None and not obj.visible_get():
            # hidden (H, the eye or monitor icon) or in an excluded / hidden collection: left out of
            # the map, like everything you can't see. Ones hidden by hand are listed
            in_layer = obj.name in context.view_layer.objects
            if (in_layer and (obj.hide_get() or obj.hide_viewport) and obj.hammerless.preset_part == ""
                    and effective_role(obj) in ("BRUSH", "TERRAIN", "BRUSH_ENTITY", "ENTITY")):
                hidden.append(obj.name)
            continue
        role = "ENTITY" if copy else effective_role(obj)
        if (role == "IGNORE" and obj.hammerless.role == "AUTO" and obj.type in ("CURVE", "SURFACE", "META", "FONT")
                and collection_role(obj) in ("BRUSH", "TERRAIN")):
            role = collection_role(obj)          # (in a Brush / Terrain collection: say why it isn't built)
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
            elif role == "MODEL":
                if obj.type != "MESH":
                    report.warnings.append(f"'{obj.name}' is a Custom Model but not a mesh: skipped")
                    continue
                ir.entities.append(model_prop(obj, depsgraph, scale, materials, ir, content))
        except Exception as ex:
            report.errors.append(f"'{obj.name}': {ex}")
    _extract_instances(context, depsgraph, scale, ir, materials, report, content, inside)
    _drop_comma_outputs(ir, report)
    if map_collection(context.scene) is None:
        report.info.append("Tip: Organize Scene (Map panel) puts the map in a collection named after it, sorted by "
                           "kind; then only what's in that collection is built")
    else:
        out = outside_but_built(context.scene)
        if out:
            report.warnings.append(f"{len(out)} object(s) are outside the map collection, so they aren't in the map "
                                   f"(move them in, or Organize Scene): {', '.join(out[:5])}"
                                   + (", ..." if len(out) > 5 else ""))
    if hidden:
        report.warnings.append(f"{len(hidden)} hidden object(s) are left out of the map (Alt+H shows them; a "
                               f"missing wall can make the map leak): {', '.join(sorted(hidden)[:5])}"
                               + (", ..." if len(hidden) > 5 else ""))
    if not defer_textures:           # (deferred: the caller runs write_textures, e.g. on the export's other thread)
        write_textures(materials.texture_writes, report)
        fill_triangles(ir)
    return ir, materials


def _drop_comma_outputs(ir, report) -> None:
    """An object Output with a comma in it would split into the wrong fields in the map (the game stores
    target, input, value, delay and times comma-separated): it is left out, with a warning."""
    for e in ir.entities:
        keep = []
        for o in e.outputs:
            bad = next((v for v in (o.target, o.input, o.parameter) if "," in (v or "")), None)
            if bad is None:
                keep.append(o)
            else:
                report.warnings.append(f"'{e.source}': the Output {o.output} has a comma in '{bad}', which the game "
                                       "can't pass in an output (it splits the output's fields): it is left out")
        e.outputs = keep


def _extract_instances(context, depsgraph, scale, ir, materials, report, content=None, inside=None) -> None:
    """Copies made by collection instances (Add > Collection Instance, linked asset kits) and by
    geometry nodes: each copy exports like the object it copies, at the copy's place."""
    skipped, skipped_geo = set(), set()
    later = []           # animated models: made after the walk (making one re-evaluates the scene)
    for inst in depsgraph.object_instances:
        if not inst.is_instance or inst.parent is None:
            continue
        src = inst.object.original
        holder = inst.parent.original
        if inside is not None and holder.name not in inside:
            continue                  # (a copy made outside the map collection)
        if holder.hammerless.role == "IGNORE" or collection_role(holder) == "IGNORE":
            continue                  # (the instancer itself is set to Ignore)
        role = effective_role(src)
        label = f"{holder.name} > {src.name}"
        matrix = inst.matrix_world.copy()
        # Geometry Nodes instancing geometry (not an object): the copy's "object" is the node tree's own
        # object, carrying the instanced mesh. Its settings are the holder's, its shape is that mesh only
        geo = None
        if src == holder:
            geo = inst.object.data if inst.object.type == "MESH" else None
            label = f"{holder.name} > instance"
            if geo is None or role not in ("BRUSH", "BRUSH_ENTITY", "ENTITY"):
                if role not in ("IGNORE", "NONE"):
                    skipped_geo.add(holder.name)
                continue
        try:
            if role == "BRUSH" and src.type == "MESH":
                ir.brushes.extend(_world_brushes(src, depsgraph, scale, materials, matrix, label, mesh=geo))
            elif role == "BRUSH_ENTITY" and src.type == "MESH":
                cls = src.hammerless.classname or "func_detail"
                ir.entities.append(Entity(cls, None, (0, 0, 0), object_keyvalues(src),
                                          mesh_to_brushes(src, depsgraph, scale, materials, matrix, label, mesh=geo),
                                          label, object_outputs(src)))
            elif role == "ENTITY" and src.hammerless.classname:
                origin = tuple(c * scale for c in matrix.translation)
                ir.entities.append(Entity(src.hammerless.classname, origin, source_angles(matrix),
                                          object_keyvalues(src), [], label, object_outputs(src)))
            elif role == "MODEL" and src.type == "MESH":
                if src.find_armature() is not None:
                    later.append((src, matrix, label))
                else:
                    ir.entities.append(model_prop(src, depsgraph, scale, materials, ir, content, matrix, label))
            elif role in ("TERRAIN", "LIGHT"):
                skipped.add(f"{src.name} ({role.lower()})")
        except Exception as ex:
            report.errors.append(f"'{label}': {ex}")
    for src, matrix, label in later:
        try:
            ir.entities.append(model_prop(src, bpy.context.evaluated_depsgraph_get(), scale, materials, ir, content,
                                          matrix, label))
        except Exception as ex:
            report.errors.append(f"'{label}': {ex}")
    if skipped:
        report.warnings.append("Instanced terrain and lights aren't exported yet (make them real objects): "
                               + ", ".join(sorted(skipped)[:5]))
    if skipped_geo:
        report.warnings.append("Geometry Nodes copies of geometry (not of an object) export only as brushes, not "
                               "as Custom Models, terrain or lights: add Realize Instances, or instance an object: " + ", ".join(sorted(skipped_geo)[:5]))
