"""The Add panel: category tiles, search, favorites, a description card and one
Add button for every preset and entity (core/spawnlist.py)."""
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty

from ..core.spawnlist import BRUSH_BOXES, BY_ID, CATEGORIES, CATEGORY_ICONS, CATEGORY_LABELS, \
    DEFAULT_BRUSH_BOX, SPAWN_ITEMS, filtered

BRUSH_MATERIALS = {"func_button": "dev/dev_hazzardstripe01a", "func_ladder": "tools/toolsinvisibleladder",
                   "hammerless_nav_region": "tools/toolstrigger"}


def favorites(scene) -> set[str]:
    return {f for f in scene.hammerless.spawn_favorites.split(",") if f}


def _selected_meshes(context):
    return [o for o in context.selected_objects if o.type == "MESH" and not o.hammerless.classname
            and not o.hammerless.preset_part]


def fill_list(scene) -> None:
    """(Re)build the scene's copy of the Add list when it's missing or out of date."""
    hs = scene.hammerless
    ids = [i.id for i in SPAWN_ITEMS]
    if [x.name for x in hs.spawn_items] == ids:
        return
    hs.spawn_items.clear()
    for i in ids:
        hs.spawn_items.add().name = i
    sync_index(scene)


def sync_index(scene) -> None:
    """Highlight the picked item's row."""
    hs = scene.hammerless
    n = next((n for n, x in enumerate(hs.spawn_items) if x.name == hs.spawn_selected), -1)
    if hs.spawn_index != n:
        hs.spawn_index = n


@persistent
def _on_load(_dummy=None):
    for scene in bpy.data.scenes:
        fill_list(scene)
        sync_index(scene)


def _fill_soon():
    _on_load()
    return None


class HL_OT_spawn_refresh(bpy.types.Operator):
    bl_idname = "hammerless.spawn_refresh"
    bl_label = "Load Add List"
    bl_description = "Fill the Add list for this scene"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        fill_list(context.scene)
        return {"FINISHED"}


class HL_UL_spawn(bpy.types.UIList):
    """Rows of the Add panel. Filtering uses the panel's tiles and search box."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        it = BY_ID.get(item.name)
        if it is None:
            return
        s = context.scene.hammerless
        row = layout.row(align=True)
        row.operator("hammerless.spawn_favorite", text="", emboss=False,
                     icon="SOLO_ON" if it.id in favorites(context.scene) else "SOLO_OFF").item = it.id
        text = it.label if not s.spawn_search else f"{it.label}  ·  {CATEGORY_LABELS[it.category]}"
        row.label(text=text, icon={"preset": "HOME", "volume": "MOD_WIREFRAME"}.get(it.kind, "OUTLINER_OB_EMPTY"))

    def draw_filter(self, context, layout):
        pass   # the panel draws its own search box and tiles

    def filter_items(self, context, data, propname):
        s = context.scene.hammerless
        rows = getattr(data, propname)
        shown = [i.id for i in filtered(s.spawn_category, s.spawn_search, favorites(context.scene))]
        rank = {i: n for n, i in enumerate(shown)}
        flags = [self.bitflag_filter_item if r.name in rank else 0 for r in rows]
        order = sorted(range(len(rows)), key=lambda n: rank.get(rows[n].name, len(rank) + n))
        new_order = [0] * len(rows)
        for pos, n in enumerate(order):
            new_order[n] = pos
        return flags, new_order


class HL_OT_spawn_favorite(bpy.types.Operator):
    bl_idname = "hammerless.spawn_favorite"
    bl_label = "Favorite"
    bl_description = "Star this to find it under Favorites"
    bl_options = {"INTERNAL", "UNDO"}

    item: StringProperty()

    def execute(self, context):
        favs = favorites(context.scene)
        favs.symmetric_difference_update({self.item})
        context.scene.hammerless.spawn_favorites = ",".join(sorted(favs))
        return {"FINISHED"}


class HL_OT_spawn_item(bpy.types.Operator):
    bl_idname = "hammerless.spawn_item"
    bl_label = "Add"
    bl_description = "Add the picked item at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    item: StringProperty()
    use_selection: BoolProperty(default=False, description="Turn the selected meshes into this instead")

    def execute(self, context):
        it = BY_ID.get(self.item or context.scene.hammerless.spawn_selected)
        if it is None:
            self.report({"WARNING"}, "Pick something in the list first")
            return {"CANCELLED"}
        if it.kind == "preset":
            return bpy.ops.hammerless.add_preset(preset=it.key)
        if it.kind == "entity":
            return bpy.ops.hammerless.add_entity(classname=it.key)
        # volume: convert the selected meshes, or add a box
        if not self.use_selection:
            from .ops import box_mesh_object
            mins, maxs = BRUSH_BOXES.get(it.key, DEFAULT_BRUSH_BOX)
            scale = context.scene.hammerless.units_per_meter
            obj = box_mesh_object(context, it.label, mins, maxs, scale, context.collection,
                                  BRUSH_MATERIALS.get(it.key))
            obj.location = context.scene.cursor.location
            for o in context.selected_objects:
                o.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
        n = len([o for o in context.selected_objects if o.type == "MESH"])
        bpy.ops.hammerless.set_brush_entity(classname=it.key)
        if self.use_selection:
            self.report({"INFO"}, f"Turned {n} mesh{'es' if n != 1 else ''} into {it.label}")
        return {"FINISHED"}


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + ([cur] if cur else [])


def _text(layout, text, width, icon="NONE"):
    col = layout.column(align=True)
    col.scale_y = 0.8
    for i, line in enumerate(_wrap(text, width)):
        col.label(text=line, icon=icon if i == 0 else ("BLANK1" if icon != "NONE" else "NONE"))


class HL_PT_add(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Add"
    bl_order = 1

    def draw_header(self, context):
        self.layout.label(icon="ADD")

    def draw(self, context):
        s = context.scene.hammerless
        layout = self.layout
        width = max(24, int(context.region.width / (7.2 * context.preferences.view.ui_scale)))
        favs = favorites(context.scene)

        px = context.region.width / context.preferences.view.ui_scale
        grid = layout.grid_flow(row_major=True, columns=2 if px < 300 else (3 if px < 430 else 4),
                                even_columns=True, even_rows=True, align=True)
        for key, label, icon in CATEGORIES:
            grid.prop_enum(s, "spawn_category", key, text=label, icon=icon)

        layout.prop(s, "spawn_search", text="", icon="VIEWZOOM", placeholder="Search everything")
        if not s.spawn_items:
            layout.operator("hammerless.spawn_refresh", icon="FILE_REFRESH")
            return
        items = filtered(s.spawn_category, s.spawn_search, favs)
        if not items:
            layout.label(text="Nothing matches" if s.spawn_search else "Star items to collect them here",
                         icon="INFO" if s.spawn_search else "SOLO_OFF")
        else:
            layout.template_list("HL_UL_spawn", "", s, "spawn_items", s, "spawn_index",
                                 rows=min(max(len(items), 3), 10))

        it = BY_ID.get(s.spawn_selected)
        if it is None:
            return
        card = layout.box()
        card.label(text=it.label, icon=CATEGORY_ICONS[it.category])
        kind = card.row()
        kind.scale_y = 0.7
        kind.label(text=it.kind_label, icon="BLANK1")
        _text(card, it.description, width)
        if it.tip:
            _text(card, it.tip, width - 3, icon="INFO")
        card.label(text="Settings: see Selected after", icon="RESTRICT_SELECT_OFF")

        meshes = _selected_meshes(context) if it.kind == "volume" else []
        big = layout.row()
        big.scale_y = 1.5
        if meshes:
            op = big.operator("hammerless.spawn_item", text=f"Turn Selected into {it.label}", icon="MOD_WIREFRAME")
            op.item, op.use_selection = it.id, True
            op = layout.operator("hammerless.spawn_item", text="Add New Box Instead", icon="ADD")
            op.item, op.use_selection = it.id, False
        else:
            op = big.operator("hammerless.spawn_item", text="Add at Cursor", icon="ADD")
            op.item, op.use_selection = it.id, False


CLASSES = (HL_OT_spawn_refresh, HL_UL_spawn, HL_OT_spawn_favorite, HL_OT_spawn_item, HL_PT_add)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_fill_soon, first_interval=0.1)   # bpy.data isn't writable during register


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
