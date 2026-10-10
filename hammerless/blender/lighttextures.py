"""The baked lighting view's Textured mode: the game's textures times the baked light, as the game draws them.

Brush faces: their material's base texture times the lightmap. Props: their model's texture times the light baked
at their vertexes. Textures go straight to the GPU (decoded once per game texture and kept while Blender runs):
nothing is added to the scene or the .blend.
"""
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

TEXTURE_SIZE = 256            # largest mip decoded (a view, not the game)

_SHADER = None
_textures: dict = {}          # material path -> (GPUTexture or None, cut out where see-through)
_white = None


def shader():
    global _SHADER
    if _SHADER is not None:
        return _SHADER
    iface = gpu.types.GPUStageInterfaceInfo("hl_lighttex_iface")
    iface.smooth("VEC2", "luvI")
    iface.smooth("VEC2", "tuvI")
    iface.smooth("VEC3", "colI")
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant("MAT4", "ModelViewProjectionMatrix")
    info.push_constant("FLOAT", "exposure")
    info.push_constant("FLOAT", "bias")
    info.push_constant("FLOAT", "use_atlas")
    info.push_constant("FLOAT", "alphatest")
    info.sampler(0, "FLOAT_2D", "image")
    info.sampler(1, "FLOAT_2D", "base")
    info.vertex_in(0, "VEC3", "pos")
    info.vertex_in(1, "VEC2", "luv")
    info.vertex_in(2, "VEC2", "tuv")
    info.vertex_in(3, "VEC3", "col")
    info.vertex_out(iface)
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(
        "void main() {"
        "  luvI = luv; tuvI = tuv; colI = col;"
        "  gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);"
        "  gl_Position.z -= bias * gl_Position.w;"
        "}")
    info.fragment_source(
        "float to_srgb(float c) {"
        "  c = clamp(c, 0.0, 1.0);"
        "  return c <= 0.0031308 ? c * 12.92 : 1.055 * pow(c, 1.0 / 2.4) - 0.055;"
        "}"
        "void main() {"
        "  vec3 light = use_atlas > 0.5 ? texture(image, luvI).rgb : colI;"
        "  vec4 t = texture(base, fract(tuvI));"
        "  if (alphatest > 0.5 && t.a < 0.5) discard;"
        "  vec3 albedo = t.rgb;"
        "  vec3 c = albedo * light * exposure;"
        "  fragColor = vec4(to_srgb(c.r), to_srgb(c.g), to_srgb(c.b), 1.0);"
        "}")
    _SHADER = gpu.shader.create_from_info(info)
    return _SHADER


def _white_texture():
    global _white
    if _white is None:
        _white = gpu.types.GPUTexture((1, 1), format="RGBA16F", data=gpu.types.Buffer("FLOAT", 4, [1.0, 1.0, 1.0, 1.0]))
    return _white


def _texture(material: str, content, game_dir):
    """(The material's base texture on the GPU (linear colour, its alpha) or None, whether its see-through parts
    are cut out: $alphatest or $translucent, drawn here as cut-outs)."""
    if material in _textures:
        return _textures[material]
    tex, cut = None, False
    if material and content is not None:
        try:
            from ..core.gamematerials import base_texture, read_texture_bytes, read_vmt
            from ..core.vtf_read import read_vtf
            vmt = read_vmt(content, material, game_dir)
            cut = any(str(vmt.get(k, "0")).strip() not in ("0", "") for k in ("$alphatest", "$translucent"))
            name = base_texture(content, material, game_dir)
            data = read_texture_bytes(content, name, game_dir) if name else None
            if data:
                _w, _h, rgba = read_vtf(data, TEXTURE_SIZE)
                c = rgba[..., :3].astype(np.float32) / 255.0
                lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
                px = np.concatenate([lin, rgba[..., 3:4].astype(np.float32) / 255.0], axis=2)
                h, w = px.shape[:2]
                tex = gpu.types.GPUTexture((w, h), format="RGBA16F",
                                           data=gpu.types.Buffer("FLOAT", w * h * 4, px.ravel()))
        except Exception:
            tex = None
    _textures[material] = (tex, cut)
    return _textures[material]


def build(data, props, scale: float, content, game_dir) -> list:
    """[(batch, texture, use_atlas, cut out)] per material, brush faces then props."""
    out = []
    sh = shader()
    if data is not None and len(data.positions):
        pos = (data.positions / scale).astype(np.float32)
        corner_mat = np.repeat(data.tri_material, 3)
        for m in np.unique(data.tri_material):
            sel = corner_mat == m
            n = int(sel.sum())
            batch = batch_for_shader(sh, "TRIS", {"pos": pos[sel], "luv": data.uvs[sel], "tuv": data.tex_uvs[sel],
                                                  "col": np.zeros((n, 3), np.float32)})
            name = data.materials[m] if 0 <= m < len(data.materials) else ""
            tex, cut = _texture(name, content, game_dir)
            out.append((batch, tex or _white_texture(), 1.0, cut and tex is not None))
    if props is not None and len(props.positions):
        pos = (props.positions / scale).astype(np.float32)
        corner_mat = np.repeat(props.tri_material, 3)
        for m in np.unique(props.tri_material):
            sel = corner_mat == m
            n = int(sel.sum())
            batch = batch_for_shader(sh, "TRIS", {"pos": pos[sel], "luv": np.zeros((n, 2), np.float32),
                                                  "tuv": props.tex_uvs[sel], "col": props.colors[sel]})
            tex, cut = _texture(props.materials[m], content, game_dir)
            out.append((batch, tex or _white_texture(), 0.0, cut and tex is not None))
    return out


def draw(batches, atlas, exposure: float, bias: float, props: bool) -> None:
    sh = shader()
    sh.bind()
    sh.uniform_float("ModelViewProjectionMatrix", gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
    sh.uniform_float("exposure", exposure)
    sh.uniform_float("bias", bias)
    sh.uniform_sampler("image", atlas)
    for batch, tex, use_atlas, cut in batches:
        if not use_atlas and not props:
            continue
        sh.uniform_float("use_atlas", use_atlas)
        sh.uniform_float("alphatest", 1.0 if cut else 0.0)
        sh.uniform_sampler("base", tex)
        batch.draw(sh)
