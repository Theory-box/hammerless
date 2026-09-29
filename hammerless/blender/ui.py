"""Sidebar panels (N panel > Hammerless), Add menu, collection panel."""
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


class HL_PT_map(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "L4D2 Map"
    bl_order = 0

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.prop(s, "map_name")
        col.prop(s, "compile_preset", text="Quality")
        col.prop(s, "nav_source")
        row = col.row()
        row.enabled = s.nav_source == "BLENDER"
        row.prop(s, "wall_climbs")
        col.prop(s, "generate_nav", text="Rebuild Nav Mesh")
        col.label(text=_nav_status(context), icon="MOD_PHYSICS")
        big = col.row()
        big.scale_y = 1.6
        op = big.operator("hammerless.build", text="Build & Play", icon="PLAY")
        op.play = True
        from .problems import draw_panel
        draw_panel(col, context)
        row = col.row(align=True)
        row.operator("hammerless.export_vmf", icon="EXPORT")
        row = col.row(align=True)
        op = row.operator("hammerless.build", text="Compile Only", icon="FILE_REFRESH")
        op.play = False
        row.operator("hammerless.launch", icon="URL")
        row.operator("hammerless.load_leak", icon="ERROR")


def _nav_status(context) -> str:
    """One-line nav mesh status for the map (bots and zombies need one)."""
    import os
    import time
    from .ops import game_root
    s = context.scene.hammerless
    root = game_root(context)
    if not root:
        return "Nav mesh: L4D2 not found"
    nav = os.path.join(root, "left4dead2", "maps", f"{s.map_name}.nav")
    if not os.path.exists(nav):
        return "Nav mesh: none yet (built on next Build & Play)"
    age = time.strftime("%b %d %H:%M", time.localtime(os.path.getmtime(nav)))
    return f"Nav mesh: built {age}" + (" - rebuilding next time" if s.generate_nav else "")


class _SubPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_parent_id = "HL_PT_map"
    bl_options = {"DEFAULT_CLOSED"}


class HL_PT_logic(_SubPanel, bpy.types.Panel):
    bl_label = "Logic (nodes)"

    def draw(self, context):
        from .logic import logic_trees
        col = self.layout.column(align=True)
        trees = logic_trees()
        col.label(text=f"{len(trees)} logic graph(s)" if trees else "No logic graph yet", icon="NODETREE")
        col.operator("hammerless.logic_new", icon="ADD")
        col.operator("hammerless.logic_from_outputs", icon="NODE")
        sub = self.layout.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="Open any editor as 'L4D2 Logic' to edit,", icon="INFO")
        sub.label(text="Shift+A adds nodes, like the shader editor", icon="BLANK1")


class HL_PT_nav(_SubPanel, bpy.types.Panel):
    bl_label = "Nav Mesh (from the game)"

    def draw(self, context):
        from .navview import draw_panel
        draw_panel(self.layout, context)


class HL_PT_compile(_SubPanel, bpy.types.Panel):
    bl_label = "Compile"

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.use_property_split = True
        col.prop(s, "compile_preset")
        col.prop(s, "auto_detail")
        sub = col.column()
        sub.enabled = s.compile_preset == "CUSTOM"
        sub.prop(s, "vis_mode")
        sub.prop(s, "rad_mode")
        sub.prop(s, "hdr_mode")
        sub.prop(s, "static_prop_lighting")
        box = sub.box()
        box.label(text="Extra compiler options")
        box.prop(s, "extra_vbsp")
        box.prop(s, "extra_vvis")
        box.prop(s, "extra_vrad")
        if s.compile_preset != "CUSTOM":
            col.label(text="Choose Custom to change individual steps", icon="INFO")


class HL_PT_lighting(_SubPanel, bpy.types.Panel):
    bl_label = "Lighting & Sky"

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.use_property_split = True
        row = col.row(align=True)
        row.prop(s, "skyname")
        row.operator("hammerless.pick_sky", text="", icon="VIEWZOOM")
        col.prop(s, "lightmap_scale")
        col.separator()
        col.prop(s, "auto_sun")
        sub = col.column()
        sub.enabled = s.auto_sun
        sub.prop(s, "sun_color")
        sub.prop(s, "sun_brightness")
        sub.prop(s, "sun_pitch")
        sub.prop(s, "sun_yaw")
        sub.prop(s, "ambient_color")
        sub.prop(s, "ambient_brightness")
        col.label(text="A Blender Sun lamp in the scene overrides these", icon="LIGHT_SUN")


class HL_PT_fog(_SubPanel, bpy.types.Panel):
    bl_label = "Fog"

    def draw_header(self, context):
        self.layout.prop(context.scene.hammerless, "fog_enabled", text="")

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.use_property_split = True
        col.enabled = s.fog_enabled
        col.prop(s, "fog_color")
        col.prop(s, "fog_start")
        col.prop(s, "fog_end")
        col.prop(s, "fog_max_density")


class HL_PT_director(_SubPanel, bpy.types.Panel):
    bl_label = "AI Director"

    def draw_header(self, context):
        self.layout.prop(context.scene.hammerless, "director_enabled", text="")

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.use_property_split = True
        col.enabled = s.director_enabled
        col.prop(s, "dir_common_limit")
        r = col.column(align=True)
        r.prop(s, "dir_mob_min")
        r.prop(s, "dir_mob_max")
        r = col.column(align=True)
        r.prop(s, "dir_mob_interval_min")
        r.prop(s, "dir_mob_interval_max")
        col.prop(s, "dir_no_mobs")
        col.prop(s, "dir_no_wanderers")
        col.separator()
        col.prop(s, "dir_max_specials")
        col.prop(s, "dir_special_interval")
        col.prop(s, "dir_tank_limit")
        col.prop(s, "dir_witch_limit")
        if not s.director_enabled:
            self.layout.label(text="Off = the game's normal Director", icon="INFO")


class HL_PT_game(_SubPanel, bpy.types.Panel):
    bl_label = "Game Window"

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.use_property_split = True
        col.prop(s, "window_monitor")
        r = col.column(align=True)
        r.prop(s, "window_width")
        r.prop(s, "window_height")
        col.prop(s, "window_borderless")
        col.prop(s, "launch_extra")
        col.prop(s, "difficulty")
        col.label(text="Size applies when the game starts", icon="INFO")


class HL_PT_debug(_SubPanel, bpy.types.Panel):
    bl_label = "Debug"

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.use_property_split = True
        col.prop(s, "debug_log")
        sub = col.column()
        sub.enabled = s.debug_log
        sub.prop(s, "debug_interval")
        col.prop(s, "autotest")
        col.label(text="Log goes to left4dead2/console.log", icon="TEXT")


class HL_PT_advanced(_SubPanel, bpy.types.Panel):
    bl_label = "Advanced"

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.use_property_split = True
        col.prop(s, "units_per_meter")
        col.prop(s, "default_material")
        col.prop(s, "auto_seal")
        col.prop(s, "check_game_content")
        col.prop(s, "model_previews")
        col.separator()
        col.prop(s, "game_root")
        col.prop(s, "output_dir")
        col.operator("hammerless.load_game_data", icon="FILE_REFRESH")
        col.operator("hammerless.refresh_previews", icon="SHADING_TEXTURE")


class HL_PT_object(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Selected Object"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        from .presets import draw_part, draw_root
        obj = context.object
        hs = obj.hammerless
        if hs.preset:
            draw_root(self.layout, obj)
            return
        col = self.layout.column()
        draw_part(col, obj)
        col.prop(hs, "role")
        eff = effective_role(obj)
        if hs.role == "AUTO":
            col.label(text=f"Exports as: {eff.replace('_', ' ').title()}", icon="INFO")

        if eff in ("ENTITY", "BRUSH_ENTITY"):
            row = col.row(align=True)
            row.prop(hs, "classname", text="Class")
            if eff == "ENTITY":
                row.operator("hammerless.add_entity", text="", icon="VIEWZOOM")
            else:
                row.operator("hammerless.set_brush_entity", text="", icon="VIEWZOOM")
            d = CATALOG.get(hs.classname)
            if d:
                box = col.box()
                for line in _wrap(d.description, 44):
                    box.label(text=line)
            if any(kv.key == "model" for kv in hs.keyvalues):
                row = col.row(align=True)
                mkv = next(kv for kv in hs.keyvalues if kv.key == "model")
                row.prop(mkv, "value", text="Model")
                row.operator("hammerless.pick_model", text="", icon="VIEWZOOM")
            col.label(text="Keyvalues")
            row = col.row()
            row.template_list("HL_UL_keyvalues", "", hs, "keyvalues", hs, "keyvalues_index", rows=4)
            sub = row.column(align=True)
            sub.operator("hammerless.kv_add", text="", icon="ADD")
            sub.operator("hammerless.kv_remove", text="", icon="REMOVE")
            sub.operator("hammerless.reset_keyvalues", text="", icon="FILE_REFRESH")

            col.label(text="Outputs (when X happens, tell Y to do Z)")
            row = col.row()
            row.template_list("HL_UL_outputs", "", hs, "outputs", hs, "outputs_index", rows=2)
            sub = row.column(align=True)
            sub.operator("hammerless.output_add", text="", icon="ADD")
            sub.operator("hammerless.output_remove", text="", icon="REMOVE")
            if 0 <= hs.outputs_index < len(hs.outputs):
                o = hs.outputs[hs.outputs_index]
                box = col.box()
                box.prop(o, "output")
                box.prop(o, "target")
                box.prop(o, "input")
                box.prop(o, "parameter")
                r = box.row()
                r.prop(o, "delay")
                r.prop(o, "only_once")

        if eff in ("BRUSH", "BRUSH_ENTITY"):
            col.prop(hs, "use_convex_hull")
        if eff == "TERRAIN":
            col.prop(hs, "terrain_power")
            col.prop(hs, "terrain_patch_size")
            col.label(text="Vertex color (red) = texture blend", icon="BRUSH_DATA")

        mat = obj.active_material
        if mat is not None and eff in ("BRUSH", "BRUSH_ENTITY", "TERRAIN"):
            box = col.box()
            box.label(text=f"Material: {mat.name}", icon="MATERIAL")
            row = box.row(align=True)
            row.prop(mat.hammerless, "source_material")
            row.operator("hammerless.pick_material", text="", icon="VIEWZOOM")
            if not mat.hammerless.source_material:
                box.label(text="Empty = convert image texture", icon="IMAGE_DATA")
            box.prop(mat.hammerless, "surface")
            box.prop(mat.hammerless, "texture_scale")
            box.prop(mat.hammerless, "lightmap_scale")
            box.operator("hammerless.refresh_previews", icon="SHADING_TEXTURE")


class HL_PT_collection(bpy.types.Panel):
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "collection"
    bl_label = "Hammerless"

    @classmethod
    def poll(cls, context):
        return context.collection is not None and context.collection != context.scene.collection

    def draw(self, context):
        self.layout.prop(context.collection.hammerless, "role")


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


CLASSES = (HL_UL_keyvalues, HL_UL_outputs, HL_PT_map, HL_PT_logic, HL_PT_nav, HL_PT_compile, HL_PT_lighting, HL_PT_fog,
           HL_PT_director, HL_PT_game, HL_PT_debug, HL_PT_advanced, HL_PT_object, HL_PT_collection,
           *CATEGORY_MENUS, HL_MT_add)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_add.append(_add_menu)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(_add_menu)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
