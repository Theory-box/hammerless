"""The map's skybox drawn behind the scene in the 3D viewport (Lighting > Environment > Show in Viewport).

Drawn by the add-on, like the lighting view: nothing is added to the scene or saved. The sky's six faces are made
into a panorama once per sky (the same faces and orientation the sky light uses, skylight.from_skybox) and drawn
where the viewport shows nothing (the far depth), in any shading mode.
"""

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

PANO_W, PANO_H = 1024, 512

_SHADER = None
_state = {"sky": None, "texture": None, "batch": None, "error": None}
_handlers = []


def _shader():
    global _SHADER
    if _SHADER is not None:
        return _SHADER
    iface = gpu.types.GPUStageInterfaceInfo("hl_sky_iface")
    iface.smooth("VEC2", "ndc")
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant("MAT4", "invViewProj")
    info.push_constant("FLOAT", "exposure")
    info.sampler(0, "FLOAT_2D", "image")
    info.vertex_in(0, "VEC2", "pos")
    info.vertex_out(iface)
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(
        "void main() {"
        "  ndc = pos;"
        "  gl_Position = vec4(pos, 0.999999, 1.0);"        # (the far depth: only where nothing else is)
        "}")
    info.fragment_source(
        "float to_srgb(float c) {"
        "  c = clamp(c, 0.0, 1.0);"
        "  return c <= 0.0031308 ? c * 12.92 : 1.055 * pow(c, 1.0 / 2.4) - 0.055;"
        "}"
        "void main() {"
        "  vec4 a = invViewProj * vec4(ndc, -1.0, 1.0);"
        "  vec4 b = invViewProj * vec4(ndc, 1.0, 1.0);"
        "  vec3 d = normalize(b.xyz / b.w - a.xyz / a.w);"
        "  float u = atan(d.y, d.x) / 6.28318530718 + 0.5;"     # (skylight's panorama: column u looks along
        "  float v = acos(clamp(d.z, -1.0, 1.0)) / 3.14159265359;"  # atan2(y, x) = 2 pi u - pi, row 0 straight up)
        "  vec3 c = texture(image, vec2(u, v)).rgb * exposure;"
        "  fragColor = vec4(to_srgb(c.r), to_srgb(c.g), to_srgb(c.b), 1.0);"
        "}")
    _SHADER = gpu.shader.create_from_info(info)
    return _SHADER


def _load(context, sky: str) -> None:
    """The sky's panorama on the GPU (once per sky)."""
    from ..core.skylight import from_skybox
    from .ops import game_content, game_root
    _state.update(sky=sky, texture=None, error=None)
    content = game_content(game_root(context))
    if content is None:
        _state["error"] = "Left 4 Dead 2 not found"
        return
    try:
        pano = from_skybox(content, sky, PANO_W, PANO_H)
    except Exception as ex:
        pano, _state["error"] = None, f"couldn't read the sky ({ex})"
    if pano is None:
        _state["error"] = _state["error"] or f"no skybox named '{sky}' in the game"
        return
    px = np.concatenate([pano.astype(np.float32), np.ones(pano.shape[:2] + (1,), np.float32)], axis=2)
    _state["texture"] = gpu.types.GPUTexture((PANO_W, PANO_H), format="RGBA16F",
                                             data=gpu.types.Buffer("FLOAT", PANO_W * PANO_H * 4, px.ravel()))


def error() -> str | None:
    return _state["error"]


def _draw():
    context = bpy.context
    s = getattr(context.scene, "hammerless", None)
    if s is None or not s.show_sky:
        return
    sky = s.skyname.strip().lower()
    if not sky:
        return
    if _state["sky"] != sky:
        _load(context, sky)
    if _state["texture"] is None:
        return
    if _state["batch"] is None:
        _state["batch"] = batch_for_shader(_shader(), "TRIS", {"pos": [(-1, -1), (3, -1), (-1, 3)]})
    sh = _shader()
    sh.bind()
    vp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    sh.uniform_float("invViewProj", vp.inverted())
    sh.uniform_float("exposure", 2.0 ** s.sky_view_exposure)
    sh.uniform_sampler("image", _state["texture"])
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    gpu.state.blend_set("NONE")
    _state["batch"].draw(sh)
    gpu.state.depth_test_set("NONE")
    gpu.state.depth_mask_set(True)          # (as it was: the views drawn after this write depth)


def redraw(*_args):
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def register():
    _handlers.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW"))


def unregister():
    for h in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
    _handlers.clear()
    _state.update(sky=None, texture=None, batch=None, error=None)
