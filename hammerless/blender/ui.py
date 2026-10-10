"""Sidebar panels (N panel > Hammerless), Add menu, collection panel."""
import os

import bpy

from ..core.entities import CATALOG, CATEGORIES, PRESETS
from .extract import effective_role


class HL_UL_keyvalues(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        d = CATALOG.get(data.classname)
        label = next((k.label for k in d.keys if k.key == item.key), None) if d else None
        row = layout.row(align=True)
        row.prop(item, "key", text="", emboss=False)
        row.prop(item, "value", text="")
        if label:
            row.label(text=label)


class HL_UL_outputs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        layout.label(text=f"{item.output} > {item.target} > {item.input}"
                          + (f" ({item.parameter})" if item.parameter else ""), icon="LINKED")


# ---------------------------------------------------------------- layout helpers

class _Panel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"


class _Sub(_Panel):
    bl_options = {"DEFAULT_CLOSED"}


def _settings(layout):
    """A column of settings with their labels lined up on the left (Blender's property split)."""
    col = layout.column()
    col.use_property_split = True
    col.use_property_decorate = False
    return col


def _hint(layout, *lines, icon="INFO"):
    col = layout.column(align=True)
    col.scale_y = 0.8
    for i, line in enumerate(lines):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def _label(s, prop: str) -> str:
    """The shown name of an enum setting's current choice."""
    value = getattr(s, prop)
    return next((i.name for i in s.bl_rna.properties[prop].enum_items if i.identifier == value), value)


def _status(layout, text, alert=False):
    """Short status on the right of a panel header (seen with the panel collapsed)."""
    row = layout.row()
    row.alert = alert
    row.label(text=text)


# ---------------------------------------------------------------- Build & Play

class HL_PT_build(_Panel, bpy.types.Panel):
    bl_label = "Build & Play"
    bl_order = 0

    def draw_header_preset(self, context):
        from .lightview import bake_status
        from .ops import BUILD_PROGRESS
        if bake_status(context)[0] == "BUSY":
            vis = BUILD_PROGRESS["vis"]
            _status(self.layout, "Vis " + vis[4:] if vis else "Building...")

    def draw(self, context):
        s = context.scene.hammerless
        layout = self.layout
        problem = setup_problem(context)
        if problem:
            box = layout.box()
            row = box.row()
            row.alert = True
            row.label(text=problem[0], icon="ERROR")
            _hint(box, *problem[1:])
            if problem[0].startswith("Left 4 Dead 2"):
                box.prop(s, "game_root", text="L4D2 Folder")
            layout.separator()
        col = _settings(layout)
        col.prop(s, "map_name")
        col.prop(s, "compile_preset", text="Quality")
        if s.compile_preset == "CUSTOM":
            _hint(layout, f"Lighting {_label(s, 'light_quality')}, Visibility {_label(s, 'vis_mode')}")
        layout.separator()
        big = layout.row()
        big.scale_y = 1.6
        big.operator("hammerless.build", text="Build & Play", icon="PLAY").play = True
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("hammerless.build", text="Build", icon="FILE_REFRESH").play = False
        row.operator("hammerless.launch", text="Play", icon="URL")
        if _leaked(context):
            layout.separator()
            row = layout.row()
            row.alert = True
            row.operator("hammerless.load_leak", text="Map leaks: show where", icon="ERROR")


class HL_PT_problems(_Panel, bpy.types.Panel):
    bl_label = "Problems"
    bl_parent_id = "HL_PT_build"

    def draw_header_preset(self, context):
        s = context.scene.hammerless
        if not s.problems_checked:
            _status(self.layout, "Not checked")
        else:
            errors = sum(p.severity == "ERROR" for p in s.problems)
            _status(self.layout, str(len(s.problems)) if s.problems else "None", alert=errors > 0)

    def draw(self, context):
        from .problems import draw_panel
        draw_panel(self.layout, context)


_setup_cache = {"key": None, "time": 0.0, "value": None}


def setup_problem(context):
    """(headline, hint lines...) when Hammerless can't build yet: L4D2 not found (Steam's libraries are
    searched automatically, so this is rare) or the Authoring Tools missing. None when all is set.
    Checked at most every 2 seconds: the panel redraws on every mouse move."""
    import time
    from .props import preferences
    s = context.scene.hammerless
    prefs = preferences()
    key = (s.game_root, prefs.game_root if prefs else "")
    now = time.monotonic()
    if _setup_cache["key"] == key and now - _setup_cache["time"] < 2.0:
        return _setup_cache["value"]
    from ..core import compile as cc
    from .ops import game_root
    root = game_root(context)
    if not root:
        value = ("Left 4 Dead 2 wasn't found",
                 "It's found in any Steam library on its own.",
                 "If yours isn't, set its folder below (the one",
                 "with left4dead2.exe), or once for every file",
                 "in the add-on's Preferences")
    elif cc.Tools(root).missing():
        value = ("The L4D2 Authoring Tools aren't installed",
                 "They're the map compilers Hammerless runs.",
                 "Steam > Library > Tools > Left 4 Dead 2",
                 "Authoring Tools > Install")
    else:
        value = None
    _setup_cache.update(key=key, time=now, value=value)
    return value


def _leaked(context) -> bool:
    """The last build leaked: its leak file is newer than the map file it compiled."""
    from .ops import work_dir
    base = os.path.join(work_dir(context), context.scene.hammerless.map_name)
    try:
        return os.path.getmtime(base + ".lin") >= os.path.getmtime(base + ".vmf") - 1.0
    except OSError:
        return False


_status_cache = {"key": None, "time": 0.0, "value": ("", "")}


def _nav_status(context) -> tuple[str, str]:
    """(short, long) nav mesh status for the map (bots and zombies need one). Worked out at most
    once a second: it reads files in the game folder and the panel redraws on every mouse move."""
    import time
    s = context.scene.hammerless
    key = (s.map_name, s.nav_source, s.generate_nav, s.game_root)
    now = time.monotonic()
    if _status_cache["key"] == key and now - _status_cache["time"] < 1.0:
        return _status_cache["value"]
    value = _nav_status_now(context)
    _status_cache.update(key=key, time=now, value=value)
    return value


def _nav_status_now(context) -> tuple[str, str]:
    import time
    from .ops import game_root
    s = context.scene.hammerless
    root = game_root(context)
    if not root:
        return "L4D2 not found", "L4D2 not found: set its folder in Settings"
    nav = os.path.join(root, "left4dead2", "maps", f"{s.map_name}.nav")
    if not os.path.exists(nav):
        return "None yet", "No nav mesh yet: the next Build makes it"
    from ..core import compile as cc
    from .ops import needs_nav
    age = time.strftime("%b %d %H:%M", time.localtime(os.path.getmtime(nav)))
    maker = {"blender": "Made in Blender", "game": "Made by the game"}.get(cc.nav_maker(cc.Tools(root), s.map_name),
                                                                           "Built")
    if needs_nav(context, root):
        return "Out of date", f"{maker} {age}: the next Build makes a new one"
    return "Up to date", f"{maker} {age}"


# ---------------------------------------------------------------- Selected Object

class HL_PT_object(_Panel, bpy.types.Panel):
    bl_label = "Selected Object"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        from .presets import draw_part, draw_root
        obj = context.object
        hs = obj.hammerless
        layout = self.layout
        if hs.preset:
            draw_root(layout, obj)
            return
        draw_part(layout, obj)
        col = _settings(layout)
        col.prop(hs, "role")
        eff = effective_role(obj)
        if hs.role == "AUTO":
            col.label(text=f"Exports as: {eff.replace('_', ' ').title()}")
        if eff in ("ENTITY", "BRUSH_ENTITY"):
            row = col.row(align=True)
            row.prop(hs, "classname", text="Class")
            row.operator("hammerless.set_entity_class" if eff == "ENTITY" else "hammerless.set_brush_entity",
                         text="", icon="VIEWZOOM")
            d = CATALOG.get(hs.classname)
            if d:
                layout.separator()
                box = layout.box().column(align=True)
                box.scale_y = 0.85
                for line in _wrap(d.description, 44):
                    box.label(text=line)
        if eff == "BRUSH":
            col.prop(hs, "brush_detail")
            if hs.brush_detail == "AUTO":
                col.label(text=_detail_note(context, obj))
        if eff in ("BRUSH", "BRUSH_ENTITY"):
            col.prop(hs, "use_convex_hull")
        if eff == "MODEL":
            col.prop(hs, "model_kind")
            if hs.model_kind == "PHYSICS":
                col.prop(hs, "physics_class")
                col.prop(hs, "model_mass")
            else:
                col.prop(hs, "model_collision")
            if hs.model_kind == "DYNAMIC":
                col.label(text="Name it (Keyvalues: targetname) to use it from logic", icon="INFO")


def _detail_note(context, obj) -> str:
    """What Auto means for this brush right now."""
    from .extract import detail_choice
    choice = detail_choice(obj)
    if choice != "AUTO":
        return f"From its collection: {choice.title()}"
    s = context.scene.hammerless
    if not s.auto_seal or s.auto_detail == "OFF":
        return "Map setting: World (Auto Detail is off)"
    return "Map setting: " + ("Detail" if s.auto_detail == "ALL" else "Detail if round or small")


def _obj_role(context) -> str:
    obj = context.object
    if obj is None or obj.hammerless.preset:
        return ""
    return effective_role(obj)


class HL_PT_obj_model(_Panel, bpy.types.Panel):
    bl_label = "Model"
    bl_parent_id = "HL_PT_object"

    @classmethod
    def poll(cls, context):
        return _obj_role(context) == "ENTITY" and any(kv.key == "model" for kv in context.object.hammerless.keyvalues)

    def draw(self, context):
        mkv = next(kv for kv in context.object.hammerless.keyvalues if kv.key == "model")
        row = self.layout.row(align=True)
        row.prop(mkv, "value", text="")
        row.operator("hammerless.pick_model", text="", icon="VIEWZOOM")


class HL_PT_obj_material(_Panel, bpy.types.Panel):
    bl_label = "Material"
    bl_parent_id = "HL_PT_object"

    @classmethod
    def poll(cls, context):
        return _obj_role(context) in ("BRUSH", "BRUSH_ENTITY", "TERRAIN") and context.object.active_material is not None

    def draw_header_preset(self, context):
        _status(self.layout, context.object.active_material.name)

    def draw(self, context):
        mat = context.object.active_material
        col = _settings(self.layout)
        row = col.row(align=True)
        row.prop(mat.hammerless, "source_material")
        row.operator("hammerless.pick_material", text="", icon="VIEWZOOM")
        if not mat.hammerless.source_material:
            col.label(text="Empty: your image texture is converted")
        col.separator()
        col.prop(mat.hammerless, "surface")
        col.prop(mat.hammerless, "texture_scale")
        col.prop(mat.hammerless, "lightmap_scale")
        self.layout.separator()
        self.layout.operator("hammerless.refresh_previews", icon="SHADING_TEXTURE")


class HL_PT_obj_terrain(_Panel, bpy.types.Panel):
    bl_label = "Terrain"
    bl_parent_id = "HL_PT_object"

    @classmethod
    def poll(cls, context):
        return _obj_role(context) == "TERRAIN"

    def draw(self, context):
        hs = context.object.hammerless
        col = _settings(self.layout)
        col.prop(hs, "terrain_power")
        col.prop(hs, "terrain_patch_size")
        _hint(self.layout, "Vertex colour red blends to the", "material's second texture")


class HL_PT_obj_keyvalues(_Panel, bpy.types.Panel):
    bl_label = "Settings"
    bl_parent_id = "HL_PT_object"

    @classmethod
    def poll(cls, context):
        return _obj_role(context) in ("ENTITY", "BRUSH_ENTITY")

    def draw(self, context):
        hs = context.object.hammerless
        row = self.layout.row()
        row.template_list("HL_UL_keyvalues", "", hs, "keyvalues", hs, "keyvalues_index", rows=4)
        side = row.column(align=True)
        side.operator("hammerless.kv_add", text="", icon="ADD")
        side.operator("hammerless.kv_remove", text="", icon="REMOVE")
        side.separator()
        side.operator("hammerless.reset_keyvalues", text="", icon="FILE_REFRESH")


class HL_PT_obj_outputs(_Panel, bpy.types.Panel):
    bl_label = "Outputs"
    bl_parent_id = "HL_PT_object"

    @classmethod
    def poll(cls, context):
        return _obj_role(context) in ("ENTITY", "BRUSH_ENTITY")

    def draw_header_preset(self, context):
        n = len(context.object.hammerless.outputs)
        if n:
            _status(self.layout, str(n))

    def draw(self, context):
        hs = context.object.hammerless
        layout = self.layout
        _hint(layout, "When something happens here, tell", "another object to do something")
        row = layout.row()
        row.template_list("HL_UL_outputs", "", hs, "outputs", hs, "outputs_index", rows=2)
        side = row.column(align=True)
        side.operator("hammerless.output_add", text="", icon="ADD")
        side.operator("hammerless.output_remove", text="", icon="REMOVE")
        if 0 <= hs.outputs_index < len(hs.outputs):
            o = hs.outputs[hs.outputs_index]
            layout.separator()
            col = _settings(layout.box())
            col.prop(o, "output")
            col.prop(o, "target")
            col.prop(o, "input")
            col.prop(o, "parameter")
            col.separator()
            col.prop(o, "delay")
            col.prop(o, "only_once")


# ---------------------------------------------------------------- World

# ---------------------------------------------------------------- Lighting

class HL_PT_lighting(_Panel, bpy.types.Panel):
    bl_label = "Lighting"
    bl_order = 3
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        s = context.scene.hammerless
        _status(self.layout, _label(s, "light_quality"))

    def draw(self, context):
        s = context.scene.hammerless
        layout = self.layout
        ours = s.light_tool == "HAMMERLESS"
        col = _settings(layout)
        col.prop(s, "light_tool")
        if ours:
            col.prop(s, "light_exact")
        if s.light_tool == "CYCLES":
            col.prop(s, "cycles_samples")
            col.prop(s, "cycles_stitch")
            col.prop(s, "cycles_denoise")
        col.separator()
        col.prop(s, "light_quality")
        on = col.column()
        on.enabled = s.light_quality != "OFF"
        on.prop(s, "light_fast")
        full = on.column()
        full.enabled = not s.light_fast
        full.prop(s, "light_sky_rays_pick")
        if s.light_sky_rays_pick == "OTHER":
            full.prop(s, "light_sky_rays", text="Times")
        full.prop(s, "light_supersample")
        ss = full.column(align=True)
        ss.enabled = s.light_supersample and ours
        ss.prop(s, "light_ss_points")
        ss.prop(s, "light_ss_passes")
        ss.prop(s, "light_ss_threshold")
        full.prop(s, "light_bounces")
        gi_ok = ours and not s.light_exact
        bm = full.column()
        bm.enabled = gi_ok
        bm.prop(s, "light_bounce_method")
        gi = gi_ok and s.light_bounce_method == "GI" and not s.light_fast
        if gi:
            full.prop(s, "light_gi_rays")
        else:
            full.prop(s, "light_patch_size")
        on.prop(s, "static_prop_lighting")
        on.prop(s, "light_prop_polys")
        q = full.column()
        q.enabled = ours
        q.prop(s, "light_fix_quirks")
        col.separator()
        col.prop(s, "lightmap_scale", text="Lightmap Scale")
        from .vmfimport import imported as _imported
        if _imported(context.scene):
            col.prop(s, "lightmap_scale_imported")
        if s.vis_mode == "SKIP" and s.light_quality != "OFF" and not gi:
            _hint(layout, "Visibility is Off: no bounced light", "(vrad lights only directly without vis)")
        if s.light_fast:
            _hint(layout, "Fast Lighting always uses Valve's vrad")
        elif ours and s.hdr_mode != "HDR" and s.light_quality != "OFF":
            _hint(layout, "HDR (Advanced) isn't HDR only: Valve's", "vrad lights the map (much slower, no GPU).",
                  "L4D2 only uses HDR", icon="ERROR")
        elif not ours and s.light_quality != "OFF":
            _hint(layout, "Points, Passes, Edge Threshold, Bounce", "Method and Fix vrad's Quirks need the",
                  "Hammerless light compiler")
        _hint(layout, "Smaller lightmap scale: sharper shadows,", "slower. Materials can override it")


class HL_PT_light_advanced(_Sub, bpy.types.Panel):
    bl_label = "Advanced"
    bl_parent_id = "HL_PT_lighting"

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "hdr_mode")
        col.prop(s, "extra_vrad", text="Extra vrad Options")


def _note_lines(layout, lines):
    col = layout.column(align=True)
    col.scale_y = 0.8
    for line in lines:
        col.label(text=line)


class HL_PT_sky(_Sub, bpy.types.Panel):
    bl_label = "Sky & Sun"
    bl_parent_id = "HL_PT_lighting"

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "sky_choice")
        if s.sky_choice == "CUSTOM":
            col.prop(s, "skyname", text="Name")
        row = col.row(align=True)
        row.prop(s, "show_sky")
        if s.show_sky:
            row.prop(s, "sky_view_exposure")
            from .skyview import error
            if error():
                col.label(text=f"Sky: {error()}", icon="ERROR")
        col.separator()
        own = [o for o in context.scene.objects
               if o.type != "LIGHT" and o.hammerless.classname == "light_environment" and o.visible_get()]
        if own:                       # (an imported map's own sun: the settings below don't touch it)
            box = col.box()
            box.label(text="This map has its own sun:", icon="LIGHT_SUN")
            for o in own[:4]:
                row = box.row()
                row.label(text=o.name)
                row.operator("hammerless.select_object", text="Select", icon="RESTRICT_SELECT_OFF").name = o.name
            _note_lines(box, ["It lights the map: change its _light / _ambient", "keys, or delete it. The settings",
                              "below only make a sun for maps without one"])
        col.prop(s, "auto_sun")
        sub = col.column()
        sub.enabled = s.auto_sun and not own
        sub.prop(s, "sun_color")
        sub.prop(s, "sun_brightness")
        sub.prop(s, "sun_pitch")
        sub.prop(s, "sun_yaw")
        if s.sky_light == "FLAT":
            sub.prop(s, "ambient_color")
        sub.prop(s, "ambient_brightness")
        col.separator()
        # (the sky's colours go on whatever sun the map has, the added one or its own: not tied to Add Sun)
        col.prop(s, "sky_light")
        if s.sky_light == "IMAGE":
            col.template_ID(s, "sky_image", open="image.open")
            col.prop(s, "sky_rotation")
        _hint(self.layout, "A Blender Sun lamp in the scene", "overrides these")
        if s.sky_light != "FLAT" and s.light_tool != "HAMMERLESS":
            _hint(self.layout, "Sky Light from the sky needs", "Light Compiler: Hammerless")


class HL_PT_view_light(_Sub, bpy.types.Panel):
    bl_label = "Baked Lighting"
    bl_parent_id = "HL_PT_lighting"

    def draw_header_preset(self, context):
        from .lightview import header_status
        text, alert = header_status(context)
        _status(self.layout, text, alert)

    def draw(self, context):
        from .lightview import draw_panel
        draw_panel(self.layout, context)


# ---------------------------------------------------------------- Visibility

class HL_PT_visibility(_Panel, bpy.types.Panel):
    bl_label = "Visibility"
    bl_order = 4
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        _status(self.layout, _label(context.scene.hammerless, "vis_mode"))

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "vis_mode")
        col.prop(s, "vis_tool")
        col.prop(s, "auto_detail")
        col.prop(s, "extra_vvis", text="Extra vvis Options")
        _hint(self.layout, "What the game skips drawing when it", "can't be seen: better frame rates")


class HL_PT_view_vis(_Sub, bpy.types.Panel):
    bl_label = "View"
    bl_parent_id = "HL_PT_visibility"

    def draw_header_preset(self, context):
        from .visview import header_status
        text = header_status(context)
        if text:
            _status(self.layout, text)

    def draw(self, context):
        from .visview import draw_panel
        draw_panel(self.layout, context)


# ---------------------------------------------------------------- Nav Mesh

class HL_PT_navmesh(_Panel, bpy.types.Panel):
    bl_label = "Nav Mesh"
    bl_order = 5
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        short, _long = _nav_status(context)
        _status(self.layout, short, alert=short in ("Out of date", "L4D2 not found"))

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "nav_source")
        sub = col.column()
        sub.enabled = s.nav_source == "BLENDER"
        sub.prop(s, "nav_analysis")
        sub.prop(s, "wall_climbs")
        col.prop(s, "generate_nav", text="Rebuild Nav Next Time")


class HL_PT_view_nav(_Sub, bpy.types.Panel):
    bl_label = "View"
    bl_parent_id = "HL_PT_navmesh"

    def draw(self, context):
        from .navview import draw_panel
        _hint(self.layout, _nav_status(context)[1])
        self.layout.separator()
        draw_panel(self.layout, context)


# ---------------------------------------------------------------- Sound

class HL_PT_sound(_Panel, bpy.types.Panel):
    bl_label = "Sound"
    bl_order = 6
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        _status(self.layout, _label(context.scene.hammerless, "sound_mode"))

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "sound_mode")
        if s.sound_mode == "OFF":
            _hint(self.layout, "No room reverb: the map sounds dry")
        else:
            _hint(self.layout, "Build ray traces the map from every floor", "and adds soundscapes (see View below)")


class HL_PT_view_sound(_Sub, bpy.types.Panel):
    bl_label = "View"
    bl_parent_id = "HL_PT_sound"

    def draw(self, context):
        from .sound import draw_panel
        draw_panel(self.layout, context)


# ---------------------------------------------------------------- World

class HL_PT_world(_Panel, bpy.types.Panel):
    bl_label = "World"
    bl_order = 7
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        _hint(self.layout, "Fog, the AI Director", "and the map's logic graphs")


class HL_PT_fog(_Sub, bpy.types.Panel):
    bl_label = "Fog"
    bl_parent_id = "HL_PT_world"

    def draw_header(self, context):
        self.layout.prop(context.scene.hammerless, "fog_enabled", text="")

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.enabled = s.fog_enabled
        col.prop(s, "fog_color")
        col.separator()
        col.prop(s, "fog_start")
        col.prop(s, "fog_end")
        col.prop(s, "fog_max_density")


class HL_PT_director(_Sub, bpy.types.Panel):
    bl_label = "AI Director"
    bl_parent_id = "HL_PT_world"

    def draw_header(self, context):
        self.layout.prop(context.scene.hammerless, "director_enabled", text="")

    def draw(self, context):
        s = context.scene.hammerless
        layout = self.layout
        if not s.director_enabled:
            _hint(layout, "Off: the game's normal Director.", "Tick the box above to customise it")
            layout.separator()
        col = _settings(layout)
        col.enabled = s.director_enabled
        col.prop(s, "dir_common_limit")
        col.separator()
        sub = col.column(align=True)
        sub.prop(s, "dir_mob_min")
        sub.prop(s, "dir_mob_max")
        sub = col.column(align=True)
        sub.prop(s, "dir_mob_interval_min")
        sub.prop(s, "dir_mob_interval_max")
        col.prop(s, "dir_no_mobs")
        col.prop(s, "dir_no_wanderers")
        col.separator()
        col.prop(s, "dir_max_specials")
        col.prop(s, "dir_special_interval")
        col.prop(s, "dir_tank_limit")
        col.prop(s, "dir_witch_limit")
        layout.separator()
        box = layout.box()
        box.enabled = s.director_enabled
        box.label(text="Director Spawns")
        grid = box.grid_flow(columns=2, align=True)
        for t in ("tank", "witch", "smoker", "boomer", "hunter", "charger", "jockey", "spitter"):
            grid.prop(s, f"dir_spawn_{t}", toggle=True)
        _hint(box, "Off: only your logic graph spawns it")


class HL_PT_logic(_Sub, bpy.types.Panel):
    bl_label = "Logic Graphs"
    bl_parent_id = "HL_PT_world"

    def draw_header_preset(self, context):
        from .logic import logic_trees
        n = len(logic_trees())
        if n:
            _status(self.layout, str(n))

    def draw(self, context):
        from .logic import logic_trees
        layout = self.layout
        trees = logic_trees()
        if trees:
            col = layout.column(align=True)
            for t in trees:          # which map each graph belongs to
                row = col.row(align=True)
                row.label(text=t.name)
                row.prop_search(t, "scene_name", bpy.data, "scenes", text="")
        else:
            layout.label(text="No logic graph yet")
        layout.separator()
        col = layout.column(align=True)
        col.operator("hammerless.logic_new", icon="ADD")
        col.operator("hammerless.logic_from_outputs", icon="NODE")
        layout.separator()
        _hint(layout, "Open any editor as 'L4D2 Logic' to edit.", "Shift+A adds nodes, like the shader editor")


# ---------------------------------------------------------------- Settings

class HL_PT_settings(_Panel, bpy.types.Panel):
    bl_label = "Settings"
    bl_order = 8
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        _hint(self.layout, "The map and model compilers, the game", "window, folders, scene scale, debugging")


class HL_PT_compile(_Sub, bpy.types.Panel):
    bl_label = "Compile"
    bl_parent_id = "HL_PT_settings"

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "map_tool")
        col.prop(s, "model_compiler")
        col.prop(s, "extra_vbsp", text="Extra vbsp Options")
        _hint(self.layout, "Lighting and Visibility have their own", "panels (compiler, quality, options)")


class HL_PT_game(_Sub, bpy.types.Panel):
    bl_label = "Game Window"
    bl_parent_id = "HL_PT_settings"

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "window_monitor")
        sub = col.column(align=True)
        sub.prop(s, "window_width")
        sub.prop(s, "window_height")
        col.prop(s, "window_borderless")
        col.separator()
        col.prop(s, "difficulty")
        col.prop(s, "fast_loading")
        col.prop(s, "launch_extra")
        _hint(self.layout, "Size applies when the game starts")


class HL_PT_folders(_Sub, bpy.types.Panel):
    bl_label = "Folders & Game Data"
    bl_parent_id = "HL_PT_settings"

    def draw(self, context):
        s = context.scene.hammerless
        layout = self.layout
        col = _settings(layout)
        col.prop(s, "game_root")
        col.prop(s, "output_dir")
        layout.separator()
        col = layout.column(align=True)
        col.operator("hammerless.load_game_data", icon="FILE_REFRESH")
        col.operator("hammerless.refresh_previews", icon="SHADING_TEXTURE")
        col.operator("hammerless.export_vmf", icon="EXPORT")
        layout.separator()
        layout.operator("hammerless.start_fresh", text="Start Fresh (delete this map's build)", icon="TRASH")


class HL_PT_scene(_Sub, bpy.types.Panel):
    bl_label = "Scene"
    bl_parent_id = "HL_PT_settings"

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "units_per_meter")
        col.prop(s, "default_material")
        col.separator()
        col.prop(s, "auto_seal")
        col.prop(s, "check_game_content")
        col.prop(s, "model_previews")


class HL_PT_debug(_Sub, bpy.types.Panel):
    bl_label = "Debug"
    bl_parent_id = "HL_PT_settings"

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "debug_log")
        sub = col.column()
        sub.enabled = s.debug_log
        sub.prop(s, "debug_interval")
        col.separator()
        col.prop(s, "autotest")
        _hint(self.layout, "The log goes to left4dead2/console.log")


class HL_PT_collection(bpy.types.Panel):
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "collection"
    bl_label = "Hammerless"

    @classmethod
    def poll(cls, context):
        return context.collection is not None and context.collection != context.scene.collection

    def draw(self, context):
        col = _settings(self.layout)
        col.prop(context.collection.hammerless, "role")
        col.prop(context.collection.hammerless, "brush_detail")


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + ([cur] if cur else [])


# ---------------------------------------------------------------- Add menu

def _category_menu(cat):
    class Menu(bpy.types.Menu):
        bl_idname = f"HL_MT_add_{cat.lower().replace(' ', '_')}"
        bl_label = cat

        def draw(self, context):
            for cls, d in sorted(CATALOG.items(), key=lambda kv: kv[1].label):
                if d.category == cat and not d.brush:
                    self.layout.operator("hammerless.add_entity", text=d.label).classname = cls
    Menu.__name__ = Menu.bl_idname
    return Menu


CATEGORY_MENUS = [_category_menu(c) for c in CATEGORIES if c != "Brush Entities"]


class HL_MT_add(bpy.types.Menu):
    bl_idname = "HL_MT_add"
    bl_label = "L4D2"

    def draw(self, context):
        layout = self.layout
        for key in ("START_SAFE_ROOM", "END_SAFE_ROOM"):
            layout.operator("hammerless.add_preset", text=PRESETS[key].label, icon="HOME").preset = key
        layout.separator()
        for key, icon in (("HORDE_TRIGGER", "GHOST_ENABLED"), ("HORDE_BUTTON", "GHOST_ENABLED"),
                          ("TANK_AMBUSH", "GHOST_ENABLED"), ("CRESCENDO_BUTTON", "GHOST_ENABLED"),
                          ("ZOMBIE_SPAWN_AREA", "MOD_MASK"), ("LADDER", "SORT_DESC"),
                          ("ZOMBIE_LADDER", "SORT_DESC"), ("ZOMBIE_CLIMB", "TRIA_UP"),
                          ("GATE_BUTTON", "SORT_ASC")):
            layout.operator("hammerless.add_preset", text=PRESETS[key].label, icon=icon).preset = key
        layout.separator()
        for m in CATEGORY_MENUS:
            layout.menu(m.bl_idname)
        layout.separator()
        layout.operator("hammerless.add_entity", text="Search Entities...", icon="VIEWZOOM")


def _add_menu(self, context):
    self.layout.menu(HL_MT_add.bl_idname, icon="WORLD")


CLASSES = (HL_UL_keyvalues, HL_UL_outputs,
           HL_PT_build, HL_PT_problems,
           HL_PT_object, HL_PT_obj_model, HL_PT_obj_material, HL_PT_obj_terrain, HL_PT_obj_keyvalues, HL_PT_obj_outputs,
           HL_PT_lighting, HL_PT_sky, HL_PT_view_light, HL_PT_light_advanced,
           HL_PT_visibility, HL_PT_view_vis, HL_PT_navmesh, HL_PT_view_nav, HL_PT_sound, HL_PT_view_sound,
           HL_PT_world, HL_PT_fog, HL_PT_director, HL_PT_logic,
           HL_PT_settings, HL_PT_compile, HL_PT_game, HL_PT_folders, HL_PT_scene, HL_PT_debug,
           HL_PT_collection, *CATEGORY_MENUS, HL_MT_add)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_add.append(_add_menu)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(_add_menu)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
