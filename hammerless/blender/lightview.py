"""Baked lighting view: the last compile's lightmaps drawn over the viewport.

Like the nav view, it's drawn by the add-on (no objects): nothing is added to the scene, saved or
exported, and turning it off frees it. It shows the compiled map's own faces, so after moving a
wall the light stays where the compiled wall was until the next compile.
"""
import os
import time

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..core.lightmap import read_lightmaps

_state = {"path": None, "mtime": None, "data": None, "batch": None, "texture": None, "scale": None,
          "error": None, "loaded_at": None, "checked": 0.0}
_handlers = []
_SHADER = None


def _shader():
    global _SHADER
    if _SHADER is not None:
        return _SHADER
    iface = gpu.types.GPUStageInterfaceInfo("hl_lightmap_iface")
    iface.smooth("VEC2", "uvInterp")
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant("MAT4", "ModelViewProjectionMatrix")
    info.push_constant("FLOAT", "exposure")
    info.push_constant("FLOAT", "bias")
    info.sampler(0, "FLOAT_2D", "image")
    info.vertex_in(0, "VEC3", "pos")
    info.vertex_in(1, "VEC2", "uv")
    info.vertex_out(iface)
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(
        "void main() {"
        "  uvInterp = uv;"
        "  gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);"
        "  gl_Position.z -= bias * gl_Position.w;"     # a hair towards the camera: wins over the scene's own faces
        "}")
    info.fragment_source(
        "float to_srgb(float c) {"
        "  c = clamp(c, 0.0, 1.0);"
        "  return c <= 0.0031308 ? c * 12.92 : 1.055 * pow(c, 1.0 / 2.4) - 0.055;"
        "}"
        "void main() {"
        "  vec3 light = texture(image, uvInterp).rgb * exposure;"
        "  fragColor = vec4(to_srgb(light.r), to_srgb(light.g), to_srgb(light.b), 1.0);"
        "}")
    _SHADER = gpu.shader.create_from_info(info)
    return _SHADER


def bsp_path(context) -> str:
    from .ops import work_dir
    return os.path.join(work_dir(context), f"{context.scene.hammerless.map_name}.bsp")


def shown() -> bool:
    return _state["data"] is not None


def load(context) -> str | None:
    """Read the last compile's lighting. Returns an error message, or None."""
    path = bsp_path(context)
    if not os.path.exists(path):
        return "No compiled map yet: press Compile Only (or Build & Play) first"
    try:
        with open(path, "rb") as f:
            data = read_lightmaps(f.read())
    except (OSError, ValueError) as ex:
        return f"Couldn't read the compiled map: {ex}"
    if data.faces == 0:
        return "The compiled map has no baked lighting (Quality: Quick skips lighting)"
    _state.update(path=path, mtime=os.path.getmtime(path), data=data, batch=None, texture=None, scale=None,
                  error=None, loaded_at=time.time())
    _redraw()
    return None


def clear() -> None:
    _state.update(path=None, mtime=None, data=None, batch=None, texture=None, scale=None, error=None,
                  loaded_at=None)
    _redraw()


def _build(scale: float):
    data = _state["data"]
    h, w = data.atlas.shape[:2]
    buf = gpu.types.Buffer("FLOAT", w * h * 4, data.atlas.ravel())
    _state["texture"] = gpu.types.GPUTexture((w, h), format="RGBA16F", data=buf)
    pos = data.positions / scale
    _state["batch"] = batch_for_shader(_shader(), "TRIS", {"pos": pos.astype(np.float32), "uv": data.uvs})
    _state["scale"] = scale


def _follow_compiles() -> None:
    """A newer compile of the shown map replaces the view (checked at most once a second)."""
    now = time.time()
    if now - _state["checked"] < 1.0 or not _state["path"]:
        return
    _state["checked"] = now
    try:
        mtime = os.path.getmtime(_state["path"])
    except OSError:
        return
    if mtime != _state["mtime"] and now - mtime > 1.0:       # (finished writing)
        err = load(bpy.context)
        if err:
            _state["error"] = err


def _draw():
    if _state["data"] is None:
        return
    s = bpy.context.scene.hammerless
    if not s.show_lightmap:
        return
    _follow_compiles()
    if _state["batch"] is None or _state["scale"] != s.units_per_meter:
        _build(s.units_per_meter)
    shader = _shader()
    shader.bind()
    shader.uniform_float("ModelViewProjectionMatrix", gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
    shader.uniform_float("exposure", 0.5 * 2.0 ** s.lightmap_exposure)   # Source's overbright 2: light 2.0 shows white
    shader.uniform_float("bias", 0.0 if s.lightmap_xray else 2e-5)
    shader.uniform_sampler("image", _state["texture"])
    gpu.state.blend_set("MULTIPLY" if s.lightmap_mode == "LIT" else "NONE")
    gpu.state.depth_test_set("LESS_EQUAL" if not s.lightmap_xray else "NONE")
    gpu.state.face_culling_set("NONE")
    _state["batch"].draw(shader)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def _redraw():
    for window in bpy.context.window_manager.windows if bpy.context.window_manager else ():
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def on_display_change(self, context):
    _redraw()


# ---------------------------------------------------------------- operators and panel

class HL_OT_lightmap_show(bpy.types.Operator):
    bl_idname = "hammerless.lightmap_show"
    bl_label = "Show Baked Lighting"
    bl_description = ("Show the lighting from the last compile in the viewport (Compile Only bakes it without "
                      "launching the game). Nothing is added to the scene")

    def execute(self, context):
        err = load(context)
        if err:
            self.report({"WARNING"}, err)
            return {"CANCELLED"}
        context.scene.hammerless.show_lightmap = True
        d = _state["data"]
        self.report({"INFO"}, f"Baked lighting: {d.faces} faces, {d.luxels:,} light samples"
                              + ("" if d.hdr else " (LDR)"))
        return {"FINISHED"}


class HL_OT_lightmap_clear(bpy.types.Operator):
    bl_idname = "hammerless.lightmap_clear"
    bl_label = "Hide Baked Lighting"
    bl_description = "Stop showing the baked lighting and free it"

    def execute(self, context):
        clear()
        return {"FINISHED"}


def draw_panel(layout, context):
    s = context.scene.hammerless
    big = layout.row()
    big.scale_y = 1.3
    if shown():
        big.operator("hammerless.lightmap_clear", text="Hide Baked Lighting", icon="X")
    else:
        big.operator("hammerless.lightmap_show", text="Show Baked Lighting", icon="LIGHT_SUN")
        col = layout.column(align=True)
        col.scale_y = 0.8
        col.label(text="Shows the last compile's lighting.", icon="INFO")
        col.label(text="Compile Only bakes it without playing.", icon="BLANK1")
        return
    row = layout.row(align=True)
    row.prop(s, "lightmap_mode", expand=True)
    layout.prop(s, "lightmap_exposure", slider=True)
    layout.prop(s, "lightmap_xray")
    if s.lightmap_mode == "LIT":
        layout.label(text="Tip: Solid view, Flat lighting, Texture colour", icon="INFO")
    d = _state["data"]
    mins = int((time.time() - os.path.getmtime(_state["path"])) // 60) if os.path.exists(_state["path"] or "") else 0
    col = layout.column(align=True)
    col.scale_y = 0.8
    col.label(text=f"Compiled {mins} min ago" if mins else "Compiled just now", icon="TIME")
    col.label(text=f"{d.faces:,} faces, {d.luxels:,} light samples", icon="BLANK1")
    if _state["error"]:
        col.label(text=_state["error"], icon="ERROR")


CLASSES = (HL_OT_lightmap_show, HL_OT_lightmap_clear)


def _forget_on_load(*_args):
    clear()


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(_forget_on_load)
    _handlers.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW"))


def unregister():
    if _forget_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_forget_on_load)
    for h in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
    _handlers.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
