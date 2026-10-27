"""The Particles panel: a searchable grid of the game's particle effects, each with a picture made from its own
sprites (core/particles.py), and the crossed planes a Particle Effect entity shows in the viewport.

The pictures come from the user's own game files (Valve's textures: never shipped), made a page at a time as the
grid shows them and kept in Blender's config folder, under a fingerprint of the game's effect list (a game update
makes them again).
"""
from __future__ import annotations

import hashlib
import os

import bpy

PER_PAGE = 24
_state: dict = {"key": None, "effects": [], "by_name": {}, "content": None, "dir": None, "sprites": {},
                "previews": None, "groups": [("ALL", "All", "")]}


def _load(context) -> bool:
    """The game's effects (read once per game install), the picture folder and the preview icons. False without
    the game."""
    from .ops import game_content, game_root
    root = game_root(context)
    content = game_content(root) if root else None
    if content is None:
        return False
    if _state["key"] == root:
        return True
    from ..core.particles import game_effects
    effects = sorted(game_effects(content), key=lambda e: e.name.lower())
    stamp = hashlib.sha1("|".join(f"{e.name}:{e.material}:{e.color}:{e.radius}" for e in effects).encode())
    folder = os.path.join(bpy.utils.user_resource("CONFIG"), "hammerless_cache", "particles",
                          stamp.hexdigest()[:12])
    os.makedirs(folder, exist_ok=True)
    groups = sorted({e.file for e in effects if not e.is_child})
    _state["groups"] = [("ALL", "All", "Every effect")] + [
        (g, _group_label(g), g) for g in groups]
    if _state["previews"] is None:
        from bpy.utils import previews
        _state["previews"] = previews.new()
    _state.update(key=root, effects=effects, by_name={e.name.lower(): e for e in effects}, content=content,
                  dir=folder, sprites={})
    return True


def _group_label(file: str) -> str:
    stem = os.path.splitext(os.path.basename(file))[0]
    return stem.replace("_fx", "").replace("_", " ").strip().title()


def picture(name: str) -> str | None:
    """The effect's picture as a PNG in the cache folder (made now if missing), or None when it has no sprite."""
    eff = _state["by_name"].get(name.lower())
    if eff is None or _state["dir"] is None:
        return None
    path = os.path.join(_state["dir"], hashlib.sha1(eff.name.lower().encode()).hexdigest()[:16] + ".png")
    if os.path.exists(path):
        return path
    from ..core.particles import thumbnail, write_png
    try:
        img = thumbnail(_state["content"], eff, _state["by_name"], 128, _state["sprites"])
    except Exception:                    # (a texture this reader can't take: no picture, the name still shows)
        img = None
    if img is None:
        return None
    write_png(path, img)
    return path


def icon(name: str) -> int:
    """The effect's picture as a preview icon id (0: none)."""
    pcoll = _state["previews"]
    if pcoll is None:
        return 0
    if name in pcoll:
        return pcoll[name].icon_id
    path = picture(name)
    if path is None:
        return 0
    return pcoll.load(name, path, "IMAGE").icon_id


def shown(context) -> list:
    """The effects the grid lists now: the search and group applied (an effect's parts only when asked)."""
    wm = context.window_manager
    words = wm.hl_pfx_search.lower().split()
    group = wm.hl_pfx_group
    out = []
    for e in _state["effects"]:
        if e.is_child and not wm.hl_pfx_parts:
            continue
        if group != "ALL" and e.file != group:
            continue
        if words and not all(w in e.name.lower() for w in words):
            continue
        out.append(e)
    return out


# ---------------------------------------------------------------- the viewport: crossed planes

def particle_mesh(name: str, scale: float):
    """Two upright planes crossed like an X (as games drew grass), showing the effect's picture, sized roughly like
    the effect; shared by every entity with that effect. None when the effect can't be shown."""
    from .ops import cache_name
    mesh_name = cache_name("HL_pfx_", name.lower(), scale)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is not None:
        return mesh
    if not _load(bpy.context):
        return None
    eff = _state["by_name"].get(name.lower())
    if eff is None:
        return None
    path = picture(eff.name)
    size = max(24.0, min(320.0, eff.radius * 4.0)) / scale       # (Hammer units: about the cloud its particles make)
    h = size
    w = size / 2
    verts = [(-w, 0, -h * 0.25), (w, 0, -h * 0.25), (w, 0, h * 0.75), (-w, 0, h * 0.75),
             (0, -w, -h * 0.25), (0, w, -h * 0.25), (0, w, h * 0.75), (0, -w, h * 0.75)]
    mesh = bpy.data.meshes.new(mesh_name)
    mesh.from_pydata(verts, [], [(0, 1, 2, 3), (4, 5, 6, 7)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop, co in zip(uv.data, [(0, 0), (1, 0), (1, 1), (0, 1)] * 2):
        loop.uv = co
    mesh["hl_model"] = True              # (drawn solid and textured, like a real model preview)
    mesh["hl_particle"] = eff.name
    if path is not None:
        mesh.materials.append(_material(eff.name, path))
    mesh.update()
    return mesh


def _material(name: str, path: str):
    from .ops import cache_name
    mat_name = cache_name("HL_pfx_mat_", name.lower())
    mat = bpy.data.materials.get(mat_name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    tex = nodes.new("ShaderNodeTexImage")
    img = bpy.data.images.load(path, check_existing=True)
    img.alpha_mode = "STRAIGHT"
    tex.image = img
    emit = nodes.new("ShaderNodeEmission")
    clear = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    links.new(tex.outputs["Color"], emit.inputs["Color"])
    links.new(tex.outputs["Alpha"], mix.inputs[0])
    links.new(clear.outputs[0], mix.inputs[1])
    links.new(emit.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])
    nodes.active = tex                   # (Solid view's Texture colour shows the active image)
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    mat.blend_method = "BLEND"
    mat.use_backface_culling = False
    mat.hammerless.source_material = "tools/toolsnodraw"   # (never exported: entities are points)
    return mat


# ---------------------------------------------------------------- the grid

class HL_OT_place_particle(bpy.types.Operator):
    bl_idname = "hammerless.place_particle"
    bl_label = "Place Particle Effect"
    bl_description = ("Put this effect in the map at the 3D cursor (with Change Selected on: give the selected "
                      "Particle Effect this effect instead)")
    bl_options = {"REGISTER", "UNDO"}

    effect: bpy.props.StringProperty()

    def execute(self, context):
        from .ops import make_entity_object, preview_mesh, style_entity_object
        from ..core.entities import preview_model
        obj = context.active_object
        s = context.scene.hammerless
        if (context.window_manager.hl_pfx_replace and obj is not None and obj.select_get()
                and obj.hammerless.classname == "info_particle_system"):
            kv = next((x for x in obj.hammerless.keyvalues if x.key == "effect_name"), None) \
                or obj.hammerless.keyvalues.add()
            kv.key, kv.value = "effect_name", self.effect
            obj.data = preview_mesh("info_particle_system", s.units_per_meter,
                                    preview_model("info_particle_system", {"effect_name": self.effect}))
            style_entity_object(obj)
            obj.name = self.effect
            if context.window_manager.hl_pfx_autoplay:
                context.view_layer.update()
                bpy.ops.hammerless.particle_preview()
            return {"FINISHED"}
        for o in context.selected_objects:
            o.select_set(False)
        obj = make_entity_object(context, "info_particle_system", context.scene.cursor.location.copy(),
                                 keyvalues={"effect_name": self.effect}, name=self.effect)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if context.window_manager.hl_pfx_autoplay:
            context.view_layer.update()
            bpy.ops.hammerless.particle_preview()
        return {"FINISHED"}


class HL_OT_particle_page(bpy.types.Operator):
    bl_idname = "hammerless.particle_page"
    bl_label = "Page"
    bl_description = "The next or previous page of effects"
    bl_options = {"INTERNAL"}

    step: bpy.props.IntProperty(default=1)

    def execute(self, context):
        wm = context.window_manager
        pages = max(1, (len(shown(context)) + PER_PAGE - 1) // PER_PAGE)
        wm.hl_pfx_page = max(0, min(pages - 1, wm.hl_pfx_page + self.step))
        return {"FINISHED"}


class HL_PT_particles(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Particles"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        if not _load(context):
            layout.label(text="Set the L4D2 folder (Settings) to browse the game's effects", icon="INFO")
            return
        wm = context.window_manager
        row = layout.row(align=True)
        row.prop(wm, "hl_pfx_search", text="", icon="VIEWZOOM")
        row.prop(wm, "hl_pfx_parts", text="", icon="OUTLINER_OB_POINTCLOUD")
        layout.prop(wm, "hl_pfx_group", text="")
        layout.prop(wm, "hl_pfx_autoplay", text="Preview when placing")
        items = shown(context)
        pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE)
        page = min(wm.hl_pfx_page, pages - 1)
        nav = layout.row(align=True)
        nav.operator("hammerless.particle_page", text="", icon="TRIA_LEFT").step = -1
        nav.label(text=f"{len(items)} effects, page {page + 1} of {pages}")
        nav.operator("hammerless.particle_page", text="", icon="TRIA_RIGHT").step = 1
        obj = context.active_object
        swapping = obj is not None and obj.select_get() and obj.hammerless.classname == "info_particle_system"
        if swapping:
            row = layout.row(align=True)
            from . import particlepreview
            playing = particlepreview._state.get('object') == obj
            row.operator('hammerless.particle_preview', text='Restart Preview' if playing else 'Preview Animation', icon='PLAY')
            row.operator('hammerless.particle_preview_stop', text='', icon='PAUSE')
            active = next((k for k in obj.hammerless.keyvalues if k.key == 'start_active'), None)
            if active is not None: layout.prop(active, 'value', text='Starts on in game (1=yes, 0=no)')
            layout.label(text='Build & Play uses this placed effect', icon='INFO')
            layout.label(text='Approximate preview; some effects need control points')
            layout.prop(wm, "hl_pfx_replace", text=f"Change '{obj.name[:24]}'", icon="FILE_REFRESH")
        grid = layout.grid_flow(row_major=True, columns=3, even_columns=True, even_rows=True, align=False)
        for eff in items[page * PER_PAGE:(page + 1) * PER_PAGE]:
            cell = grid.column(align=True)
            ic = icon(eff.name)
            if ic:
                cell.template_icon(icon_value=ic, scale=4.0)
            else:
                cell.label(text="(no picture)")
            cell.operator("hammerless.place_particle", text=eff.name[:22]).effect = eff.name


def _groups(self, context):
    return _state["groups"]


def _reset_page(self, context):
    self.hl_pfx_page = 0


CLASSES = (HL_OT_place_particle, HL_OT_particle_page, HL_PT_particles)


def register():
    from . import particlepreview
    particlepreview.register()
    for c in CLASSES:
        bpy.utils.register_class(c)
    wm = bpy.types.WindowManager
    wm.hl_pfx_search = bpy.props.StringProperty(name="Search", description="Effect name contains these words",
                                                update=_reset_page)
    wm.hl_pfx_group = bpy.props.EnumProperty(name="Group", items=_groups, update=_reset_page,
                                             description="The game's particle file the effects come from")
    wm.hl_pfx_parts = bpy.props.BoolProperty(name="Parts too", update=_reset_page,
                                             description="Also list effects that are only parts of others")
    wm.hl_pfx_autoplay = bpy.props.BoolProperty(name="Preview when placing", default=True, description="Animate the selected effect after placing or changing it; does not affect game export")
    wm.hl_pfx_page = bpy.props.IntProperty(name="Page", min=0)
    wm.hl_pfx_replace = bpy.props.BoolProperty(
        name="Change Selected", description="Clicking an effect changes the selected Particle Effect's effect "
                                            "instead of adding a new one")


def unregister():
    from . import particlepreview
    particlepreview.unregister()
    wm = bpy.types.WindowManager
    for p in ("hl_pfx_search", "hl_pfx_group", "hl_pfx_parts", "hl_pfx_page", "hl_pfx_replace", "hl_pfx_autoplay"):
        if hasattr(wm, p):
            delattr(wm, p)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    if _state["previews"] is not None:
        from bpy.utils import previews
        previews.remove(_state["previews"])
        _state["previews"] = None
    _state["key"] = None
