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

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.prop(s, "map_name")
        big = col.row()
        big.scale_y = 1.6
        op = big.operator("hammerless.build", text="Build & Play", icon="PLAY")
        op.play = True
        row = col.row(align=True)
        row.operator("hammerless.validate", icon="CHECKMARK")
        row.operator("hammerless.export_vmf", icon="EXPORT")
        row = col.row(align=True)
        op = row.operator("hammerless.build", text="Compile Only", icon="FILE_REFRESH")
        op.play = False
        row.operator("hammerless.launch", icon="URL")
        row.operator("hammerless.load_leak", icon="ERROR")


class HL_PT_map_settings(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Settings"
    bl_parent_id = "HL_PT_map"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.hammerless
        col = self.layout.column()
        col.prop(s, "compile_preset")
        col.prop(s, "generate_nav")
        col.separator()
        col.prop(s, "units_per_meter")
        col.prop(s, "skyname")
        col.prop(s, "default_material")
        col.prop(s, "auto_seal")
        col.prop(s, "check_game_content")
        col.separator()
        col.prop(s, "game_root")
        col.prop(s, "output_dir")


class HL_PT_object(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Selected Object"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        obj = context.object
        hs = obj.hammerless
        col = self.layout.column()
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
            box.prop(mat.hammerless, "source_material")
            if not mat.hammerless.source_material:
                box.label(text="Empty = convert image texture", icon="IMAGE_DATA")
                box.prop(mat.hammerless, "surfaceprop")
            box.prop(mat.hammerless, "texture_scale")


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
                          ("TANK_AMBUSH", "GHOST_ENABLED"), ("ZOMBIE_SPAWN_AREA", "MOD_MASK")):
            layout.operator("hammerless.add_preset", text=PRESETS[key].label, icon=icon).preset = key
        layout.separator()
        for m in CATEGORY_MENUS:
            layout.menu(m.bl_idname)
        layout.separator()
        layout.operator("hammerless.add_entity", text="Search Entities...", icon="VIEWZOOM")


def _add_menu(self, context):
    self.layout.menu(HL_MT_add.bl_idname, icon="WORLD")


CLASSES = (HL_UL_keyvalues, HL_UL_outputs, HL_PT_map, HL_PT_map_settings, HL_PT_object, HL_PT_collection,
           *CATEGORY_MENUS, HL_MT_add)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_add.append(_add_menu)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(_add_menu)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
