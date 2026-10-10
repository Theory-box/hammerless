"""Viewport previews of game materials: the real L4D2 texture, world-projected like Hammer.

Shown in Material Preview / Rendered view. Solid view uses the texture's average colour.
"""
from __future__ import annotations

import bpy
import numpy as np

from ..core.gamematerials import base_texture, read_texture_bytes
from ..core.vtf_read import read_vtf

PREVIEW_SIZE = 512          # largest mip decoded for previews
NODE_TAG = "hammerless_preview"


def _image_for_texture(texture: str, content, game_dir):
    """Blender image for a game texture (cached by name). Returns (image, full_width, full_height)."""
    from .ops import cache_name
    name = cache_name("HL_tex_", texture)
    img = bpy.data.images.get(name)
    if img is not None and "hl_full_size" in img:
        w, h = img["hl_full_size"]
        return img, w, h
    data = read_texture_bytes(content, texture, game_dir)
    if not data:
        return None, 0, 0
    w, h, rgba = read_vtf(data, PREVIEW_SIZE)
    ih, iw = rgba.shape[:2]
    if img is None:
        img = bpy.data.images.new(name, iw, ih, alpha=True)
    elif (img.size[0], img.size[1]) != (iw, ih):
        img.scale(iw, ih)
    px = (rgba[::-1].astype(np.float32) / 255.0).ravel()   # Blender rows are bottom-up
    img.pixels.foreach_set(px)
    img.pack()
    img["hl_full_size"] = (w, h)
    img["hl_average"] = [float(c) for c in rgba[..., :3].reshape(-1, 3).mean(axis=0) / 255.0]
    return img, w, h


def apply_preview(mat, content, game_dir, units_per_meter: float) -> bool:
    """Give a game material a node tree showing its real texture. Returns True on success."""
    path = (mat.hammerless.source_material or "").strip().lower().replace("\\", "/")
    if not path or content is None:
        return False
    texture = base_texture(content, path, game_dir)
    if not texture:
        return False
    try:
        img, w, h = _image_for_texture(texture, content, game_dir)
    except Exception:
        return False
    if img is None:
        return False

    mat.use_nodes = True
    nt = mat.node_tree
    for n in [n for n in nt.nodes if n.get(NODE_TAG)]:
        nt.nodes.remove(n)
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) or nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs[0], out.inputs[0])

    geo = nt.nodes.new("ShaderNodeNewGeometry")
    mapping = nt.nodes.new("ShaderNodeMapping")
    tex = nt.nodes.new("ShaderNodeTexImage")
    for n, x in ((geo, -900), (mapping, -700), (tex, -450)):
        n[NODE_TAG] = True
        n.location = (x, 300)
    tex.image = img
    tex.projection = "BOX"
    tex.projection_blend = 0.15
    tex.interpolation = "Linear"
    update_mapping(mat, units_per_meter)
    nt.links.new(geo.outputs["Position"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    # faces painted with the texturing tools show through their UVs (the alignment they're exported with)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    uvtex = nt.nodes.new("ShaderNodeTexImage")
    painted = nt.nodes.new("ShaderNodeAttribute")
    mix = nt.nodes.new("ShaderNodeMix")
    for n, x, y in ((coord, -900, 0), (uvtex, -450, 0), (painted, -450, 520), (mix, -200, 300)):
        n[NODE_TAG] = True
        n.location = (x, y)
    uvtex.image = img
    uvtex.interpolation = "Linear"
    painted.attribute_type = "GEOMETRY"
    painted.attribute_name = "hl_tex_uv"
    mix.data_type = "RGBA"
    nt.links.new(coord.outputs["UV"], uvtex.inputs["Vector"])
    nt.links.new(painted.outputs["Fac"], mix.inputs["Factor"])
    nt.links.new(tex.outputs["Color"], mix.inputs[6])
    nt.links.new(uvtex.outputs["Color"], mix.inputs[7])
    nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    # fences, glass and foliage ($alphatest / $translucent): see-through where the texture is
    from ..core.gamematerials import read_vmt
    vmt = read_vmt(content, path, game_dir)
    cut = str(vmt.get("$alphatest", "0")).strip() not in ("0", "")
    clear = str(vmt.get("$translucent", "0")).strip() not in ("0", "")
    for attr, value in (("surface_render_method", "DITHERED"), ("blend_method", "OPAQUE")):
        try:                              # (as new: an earlier see-through game material's setting goes)
            setattr(mat, attr, value)
        except (AttributeError, TypeError):
            pass
    if cut or clear:
        amix = nt.nodes.new("ShaderNodeMix")
        amix[NODE_TAG] = True
        amix.location = (-200, 0)
        amix.data_type = "FLOAT"
        nt.links.new(painted.outputs["Fac"], amix.inputs["Factor"])
        nt.links.new(tex.outputs["Alpha"], amix.inputs[2])
        nt.links.new(uvtex.outputs["Alpha"], amix.inputs[3])
        nt.links.new(amix.outputs[0], bsdf.inputs["Alpha"])
        for attr, value in (("surface_render_method", "BLENDED" if clear and not cut else "DITHERED"),
                            ("blend_method", "BLEND" if clear and not cut else "CLIP")):
            try:
                setattr(mat, attr, value)
            except (AttributeError, TypeError):
                pass
    mat["hl_uvmix"] = 1
    bsdf.inputs["Roughness"].default_value = 0.9
    avg = img.get("hl_average")
    if avg:
        mat.diffuse_color = (*avg, 1.0)
    return True


def update_mapping(mat, units_per_meter: float) -> None:
    """Match Hammer's texture scale: one texture repeat = width * scale Hammer units."""
    if not mat.use_nodes or not mat.node_tree:
        return
    tex = next((n for n in mat.node_tree.nodes if n.get(NODE_TAG) and n.type == "TEX_IMAGE"), None)
    mapping = next((n for n in mat.node_tree.nodes if n.get(NODE_TAG) and n.type == "MAPPING"), None)
    if tex is None or mapping is None or tex.image is None:
        return
    w, h = tex.image.get("hl_full_size", tex.image.size)
    s = max(mat.hammerless.texture_scale, 0.001)
    mapping.inputs["Scale"].default_value = (units_per_meter / (w * s), units_per_meter / (h * s),
                                             units_per_meter / (w * s))


# ---------------------------------------------------------------- 3D model previews

def _model_material(material_path: str, content, game_dir):
    """Blender material showing a model's texture through its UVs (cached)."""
    from .ops import cache_name
    name = cache_name("HL_mdl_", material_path)
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    texture = base_texture(content, material_path, game_dir)
    if texture:
        try:
            img, _w, _h = _image_for_texture(texture, content, game_dir)
        except Exception:
            img = None
        if img is not None:
            nt = mat.node_tree
            bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            tex.location = (-400, 300)
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            bsdf.inputs["Roughness"].default_value = 0.8
            avg = img.get("hl_average")
            if avg:
                mat.diffuse_color = (*avg, 1.0)
    return mat


def model_mesh(model_path: str, content, game_dir, units_per_meter: float):
    """Blender mesh of a game model (LOD 0, textured), cached per model. None if unreadable."""
    from ..core.mdl import find_material, load_model
    from .ops import cache_name
    name = cache_name("HL_mdl_", model_path, units_per_meter)
    mesh = bpy.data.meshes.get(name)
    if mesh is not None:
        return mesh
    try:
        mm = load_model(content, model_path)
    except Exception:
        return None
    if mm is None or not mm.triangles:
        return None
    # keep only vertices that triangles use (body groups can leave unused ones)
    tris = np.array(mm.triangles, dtype=np.int64)
    used, inverse = np.unique(tris, return_inverse=True)
    tris = inverse.reshape(-1, 3)
    verts = mm.positions[used] / units_per_meter
    uvs = mm.uvs[used]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts.tolist(), [], tris.tolist())
    uv_layer = mesh.uv_layers.new(name="UVMap")
    loop_uv = uvs[tris.ravel()]
    uv_layer.data.foreach_set("uv", loop_uv.astype(np.float32).ravel())

    slot_of: dict[int, int] = {}
    for tex_index in sorted(set(mm.triangle_materials)):
        tex_name = mm.materials[tex_index] if tex_index < len(mm.materials) else ""
        path = find_material(content, mm, tex_name) if tex_name else None
        slot_of[tex_index] = len(mesh.materials)
        mesh.materials.append(_model_material(path, content, game_dir) if path else None)
    mesh.polygons.foreach_set("material_index", [slot_of[t] for t in mm.triangle_materials])
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    mesh.update()
    mesh["hl_model"] = model_path
    return mesh
