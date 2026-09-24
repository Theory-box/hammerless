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
    name = f"HL_tex_{texture}"
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
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
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
