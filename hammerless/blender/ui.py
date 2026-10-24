"""Sidebar panels (N panel > Hammerless), Add menu, collection panel."""
import os

import bpy

from ..core.control import _on
from ..core.entities import CATALOG, CATEGORIES, PRESETS
from .extract import effective_role
from .entitycontext import entity_object


class HL_UL_keyvalues(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        d = CATALOG.get(data.classname)
        label = next((k.label for k in d.keys if k.key == item.key), None) if d else None
        from .props import color_key_kind
        row = layout.row(align=True)
        row.prop(item, "key", text="", emboss=False)
        kind = color_key_kind(item.key)
        n = len(item.value.split())
        if kind and n in (3, 4) and not item.value.lstrip().startswith("-"):     # ("-1 -1 -1 1": HDR unset)
            row.prop(item, "color", text="")                 # (a swatch: click it for the colour wheel)
            if kind == "LIGHT":
                row.prop(item, "brightness", text="Brightness")
        else:
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


class _InPanel:
    """The panel as its draw code sees it, with its layout swapped for an indented one."""

    def __init__(self, panel, layout):
        self._panel, self.layout = panel, layout

    def __getattr__(self, name):
        return getattr(self._panel, name)


class _Nested:
    """A panel inside a subpanel: Blender lines every level up at the same edge, so its header and contents are
    moved right by `indent` steps (it reads as part of the panel above it, not as the next one)."""
    indent = 1

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        body, head = cls.__dict__.get("draw"), cls.__dict__.get("draw_header")
        if body is not None:
            cls.draw = _indented_draw(body)
        cls.draw_header = _indented_header(head)


def _indented_draw(body):
    def draw(self, context):            # (Blender wants exactly (self, context))
        row = self.layout.row()
        row.separator(factor=2.4 * self.indent)
        body(_InPanel(self, row.column()), context)
    return draw


def _indented_header(head):
    def draw_header(self, context):
        self.layout.separator(factor=2.4 * self.indent)
        if head is not None:
            head(self, context)
    return draw_header


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

def _cancel_button(layout, scale=1.6) -> bool:
    """While a build or bake runs: a Cancel button in place of the one that started it (True: drawn)."""
    from .ops import _BUILDING
    if not _BUILDING:
        return False
    row = layout.row()
    row.scale_y = scale
    row.alert = True
    row.operator("hammerless.cancel_build", text="Cancel", icon="CANCEL")
    return True


class HL_PT_build(_Panel, bpy.types.Panel):
    bl_label = "Map"
    bl_order = 0

    def draw_header_preset(self, context):
        from .lightview import bake_status
        from .ops import BUILD_PROGRESS, _BUILDING
        row = self.layout.row(align=True)
        actions = row.row(align=True)
        actions.enabled = not _BUILDING
        actions.operator("hammerless.build", text="Build & Play").play = True
        actions.operator("hammerless.build", text="Build").play = False
        row.operator("hammerless.launch", text="Play")
        if bake_status(context)[0] == "BUSY":
            vis = BUILD_PROGRESS["vis"]
            _status(self.layout, "Vis " + vis[4:] if vis else "Building...")
            return
        built = _built_ago(context)
        if built:
            _status(self.layout, built)

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
        from .mapcollection import map_collection
        col = _settings(layout)
        row = col.row(align=True)
        row.prop(s, "map_name")
        organized = map_collection(context.scene) is not None
        org = row.row(align=True)
        org.alert = not organized          # (not organized yet: the whole scene is the map)
        org.operator("hammerless.organize_scene", text="", icon="OUTLINER_COLLECTION")
        col.prop(s, "compile_preset", text="Quality")
        col.prop(s, "game_mode")
        if s.game_mode == "SURVIVAL":
            col.label(text="Stock Survival: use a Survival Start Button")
        layout.separator()
        if not _cancel_button(layout):
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


_built_cache = {"key": None, "t": None}


def _built_ago(context) -> str:
    """'Built 3 min ago' for the map's last build ('' if none). The file is looked at every few seconds, not on every
    redraw (finding the work folder creates it)."""
    import time
    key = (context.scene.hammerless.map_name, int(time.monotonic() // 3))
    if _built_cache["key"] != key:
        from .ops import work_dir
        try:
            t = os.path.getmtime(os.path.join(work_dir(context), context.scene.hammerless.map_name + ".bsp"))
        except (OSError, AttributeError):
            t = None
        _built_cache.update(key=key, t=t)
    t = _built_cache["t"]
    if t is None:
        return ""
    mins = int((time.time() - t) // 60)
    if mins < 1:
        return "Built just now"
    if mins < 60:
        return f"Built {mins} min ago"
    hours = mins // 60
    return f"Built {hours} h ago" if hours < 48 else "Built " + time.strftime("%b %d", time.localtime(t))


_leak_cache = {"key": None, "value": False}


def _leaked(context) -> bool:
    """The last build leaked: its leak file is newer than the map file it compiled. (Looked at every few
    seconds, not on every redraw: finding the work folder creates it.)"""
    import time
    key = (context.scene.hammerless.map_name, int(time.monotonic() // 3))
    if _leak_cache["key"] == key:
        return _leak_cache["value"]
    value = False
    try:
        from .ops import work_dir
        base = os.path.join(work_dir(context), context.scene.hammerless.map_name)
        value = os.path.getmtime(base + ".lin") >= os.path.getmtime(base + ".vmf") - 1.0
    except OSError:
        pass
    _leak_cache.update(key=key, value=value)
    return value


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
        obj = entity_object(context)
        hs = obj.hammerless
        layout = self.layout
        if obj != context.object:
            row = layout.row(align=True)
            row.label(text=f"Entity: {obj.name}", icon="LINKED")
            row.operator("hammerless.select_object", text="", icon="RESTRICT_SELECT_OFF").name = obj.name
        if hs.preset:
            draw_root(layout, obj)
            return
        draw_part(layout, obj)
        col = _settings(layout)
        eff = effective_role(obj)
        _enum_menu(col, hs, "role", "Kind", f"Auto ({_ROLE_NAMES.get(eff, eff.title())})" if hs.role == "AUTO" else None)
        if eff in ("ENTITY", "BRUSH_ENTITY"):
            row = col.row(align=True)
            row.prop(hs, "classname", text="Class")
            row.operator("hammerless.set_entity_class" if eff == "ENTITY" and obj == context.object else "hammerless.set_brush_entity",
                         text="", icon="VIEWZOOM")
            d = CATALOG.get(hs.classname)
            if d:
                layout.separator()
                box = layout.box().column(align=True)
                box.scale_y = 0.85
                for line in _wrap(d.description, 44):
                    box.label(text=line)
        if hs.classname == "hammerless_control":
            _draw_control(layout, hs)
        elif hs.classname == "func_precipitation" and any(k.key == "weather" for k in hs.keyvalues):
            _draw_weather(layout, hs)
        if eff == "BRUSH":
            _enum_menu(col, hs, "brush_detail", "Detail",
                       f"Auto ({_detail_note(context, obj)})" if hs.brush_detail == "AUTO" else None)
        if eff in ("BRUSH", "BRUSH_ENTITY"):
            col.prop(hs, "use_convex_hull")
        if obj.get("hl_placed"):
            from .placedchar import draw_panel as _placed_panel
            _placed_panel(col, obj)
        if obj.type == "ARMATURE" and obj.get("hl_game_model"):
            box = col.box()
            box.label(text="Game skeleton: " + obj["hl_game_model"].split("/")[-1], icon="ARMATURE_DATA")
            box.operator("hammerless.load_game_animation", icon="ACTION")
            box.operator("hammerless.import_game_mesh", icon="IMPORT")
            box.operator("hammerless.add_mannequin", icon="OUTLINER_OB_MESH")
        arm = obj.find_armature() if eff == "MODEL" and obj.type == "MESH" else None
        if arm is not None:                  # an animated model: always a Dynamic prop
            _draw_animated(col, obj, arm)
        elif eff == "MODEL":
            col.prop(hs, "model_kind")
            if hs.model_kind == "PHYSICS":
                col.prop(hs, "physics_class")
                col.prop(hs, "model_mass")
            else:
                col.prop(hs, "model_collision")
            if hs.model_kind == "DYNAMIC":
                col.label(text="Name it (Keyvalues: targetname) to use it from logic", icon="INFO")


def _draw_animated(col, obj, arm):
    """What an animated Custom Model becomes: its bones and animations (each action of the armature)."""
    from .extract import _armature_actions, _deform_bones
    box = col.box()
    bones = _deform_bones(arm)
    box.label(text=f"Animated by '{arm.name}': {len(bones)} bones", icon="ARMATURE_DATA")
    actions = _armature_actions(arm)
    game = arm.get("hl_game_model", "")
    if game:
        box.label(text="Borrows all the animations of " + game.split("/")[-1], icon="LINKED")
    if not actions and not game:
        box.label(text="No animations yet: add actions to the armature (its action, or NLA tracks)", icon="INFO")
    for act in actions[:12]:
        name = str(act.get("hl_name") or act.name)
        start, end = (int(round(f)) for f in act.frame_range)
        loops = bool(getattr(act, "use_cyclic", False))
        frames = end - start + (0 if loops and end > start else 1)       # (a loop's last frame is its first: once)
        box.label(text=f"{name}: {frames} frames" + (", loops" if loops else ""), icon="ACTION")
    if len(actions) > 12:
        box.label(text=f"... and {len(actions) - 12} more")
    box.label(text="A Dynamic prop playing " + ("its idle" if game else "'idle' (or its first animation)")
              + "; name it to use it from logic", icon="INFO")
    col.prop(obj.hammerless, "model_collision")


_ROLE_NAMES = {"BRUSH": "World Brush", "BRUSH_ENTITY": "Brush Entity", "ENTITY": "Entity", "TERRAIN": "Terrain",
               "LIGHT": "Light", "MODEL": "Custom Model", "IGNORE": "Not Exported"}


def _enum_menu(col, data, prop, label, text=None):
    """An enum setting as a dropdown whose button can say more than the choice's name (what Auto means)."""
    if text is None:
        col.prop(data, prop, text=label)
        return
    row = col.row(align=True)
    split = row.split(factor=0.4, align=True)
    left = split.row()
    left.alignment = "RIGHT"
    left.label(text=label)
    split.prop_menu_enum(data, prop, text=text)


_CONTROL_TICKS = (("nav", "Nav Mesh", "MOD_MESHDEFORM"), ("light", "Light Baking", "LIGHT_SUN"),
                  ("vis", "Visibility", "HIDE_OFF"), ("sound", "Sound", "OUTLINER_OB_SPEAKER"),
                  ("spawns", "Zombie Spawns", "GHOST_ENABLED"))


def _draw_control(layout, hs):
    """A Control Volume: Not inside / Only inside, and what it controls (its keyvalues, as buttons)."""
    kv = {k.key: k.value for k in hs.keyvalues}
    only = kv.get("mode", "EXCLUDE").upper() == "ONLY"
    box = layout.box()
    on = _on(kv.get("enabled", "1"))
    op = box.operator("hammerless.control_enable", text="Enabled" if on else "Disabled (does nothing)",
                      icon="CHECKBOX_HLT" if on else "CHECKBOX_DEHLT", depress=on)
    op.name, op.enabled = hs.id_data.name, not on
    row = box.row(align=True)
    row.enabled = on
    for value, label in (("EXCLUDE", "Not Inside"), ("ONLY", "Only Inside")):
        op = row.operator("hammerless.kv_set", text=label, depress=(value == "ONLY") == only)
        op.key, op.value = "mode", value
    grid = box.grid_flow(columns=2, align=True)
    grid.enabled = on
    for key, label, icon in _CONTROL_TICKS:
        on = _on(kv.get(key, "0"))
        cell = grid.row(align=True)
        cell.enabled = not (key == "spawns" and only)
        op = cell.operator("hammerless.kv_set", text=label, icon=icon, depress=on)
        op.key, op.value = key, "0" if on else "1"


class HL_MT_weather(bpy.types.Menu):
    bl_label = "Weather"
    bl_idname = "HL_MT_weather"

    def draw(self, context):
        from ..core.entities import WEATHER_TYPES
        for value, label in WEATHER_TYPES:
            op = self.layout.operator("hammerless.kv_set", text=label)
            op.key, op.value = "weather", value


def _draw_weather(layout, hs):
    """A Weather Volume: the weather (a dropdown), then the settings that weather has."""
    from ..core.entities import WEATHER, WEATHER_TYPES, WIND_CHOICES
    items = {k.key: k for k in hs.keyvalues}
    kind = items["weather"].value.strip().lower()
    col = _settings(layout.box())
    row = col.row(align=True)
    split = row.split(factor=0.4, align=True)
    left = split.row()
    left.alignment = "RIGHT"
    left.label(text="Weather")
    split.menu("HL_MT_weather", text=dict(WEATHER_TYPES).get(kind, kind or "?"))
    if "density" in items:
        col.prop(items["density"], "value", text="Amount (0-100)")
    if kind in WEATHER and WEATHER[kind][2] != "none":       # (bugs and smoke have no wind)
        wind = items["wind"].value.strip().lower() if "wind" in items else "auto"
        row = col.row(align=True)
        split = row.split(factor=0.4, align=True)
        left = split.row()
        left.alignment = "RIGHT"
        left.label(text="Wind")
        btns = split.row(align=True)
        for value, label in WIND_CHOICES:
            op = btns.operator("hammerless.kv_set", text=label if value != "auto" else "Auto", depress=value == wind)
            op.key, op.value = "wind", value


def _detail_note(context, obj) -> str:
    """What Auto means for this brush right now."""
    from .extract import detail_choice
    choice = detail_choice(obj)
    if choice != "AUTO":
        return f"{choice.title()}, from its collection"
    s = context.scene.hammerless
    if not s.auto_seal or s.auto_detail == "OFF":
        return "World"
    return "Detail" if s.auto_detail == "ALL" else "Detail if round or small"


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


class HL_PT_obj_brushes(_Panel, bpy.types.Panel):
    """Convert to Brushes: collision brushes (or invisible props) for any mesh."""
    bl_label = "Convert to Brushes"
    bl_parent_id = "HL_PT_object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "MESH" and obj.get("hl_collision_of") is None

    def draw(self, context):
        hs = context.object.hammerless
        col = _settings(self.layout)
        col.prop(hs, "brush_algorithm")
        if hs.brush_algorithm == "SLABS":
            col.prop(hs, "collision_min_area")
        else:
            if hs.brush_algorithm in ("AUTO", "EOF"):
                col.prop(hs, "brush_detail_depth")
            if hs.brush_algorithm in ("AUTO", "ORGANIC"):
                col.prop(hs, "brush_fit")
                col.prop(hs, "brush_max_pieces")
                col.prop(hs, "brush_time_limit")
            col.prop(hs, "brush_max_sides")
        col.prop(hs, "collision_mode", text="As")
        col.prop(hs, "collision_blocks_vis")
        row = self.layout.row()
        row.scale_y = 1.2
        row.operator("hammerless.make_collision", icon="MOD_SOLIDIFY")
        if hs.role == "MODEL" and hs.model_kind == "DYNAMIC":
            _hint(self.layout, "Brushes don't move with a Dynamic prop", icon="ERROR")
        elif hs.role == "MODEL" and hs.model_collision != "NONE":
            _hint(self.layout, "Set this model's Collision to None, so it doesn't collide twice")


class HL_PT_obj_brushes_tuning(_Sub, bpy.types.Panel):
    """Convert to Brushes > Fine Tuning: General's speed against quality."""
    bl_label = "Fine Tuning"
    bl_parent_id = "HL_PT_obj_brushes"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None and obj.type == "MESH" and obj.get("hl_collision_of") is None
                and obj.hammerless.brush_algorithm in ("AUTO", "ORGANIC"))

    def draw(self, context):
        hs = context.object.hammerless
        col = _settings(self.layout)
        col.prop(hs, "brush_quality")
        col.prop(hs, "brush_stop_gain")
        col.prop(hs, "brush_max_candidates")
        col.prop(hs, "brush_fit_tolerance")
        col.prop(hs, "brush_aim_precision")
        col.prop(hs, "brush_merge_pass")
        col.prop(hs, "brush_sliver_pass")


class HL_PT_obj_light(_Panel, bpy.types.Panel):
    bl_label = "Light"
    bl_parent_id = "HL_PT_object"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return _obj_role(context) == "LIGHT" and obj.type == "LIGHT" and obj.data.type != "SUN"

    def draw(self, context):
        from .extract import light_name
        obj = context.object
        hs = obj.hammerless
        col = self.layout.column()
        col.prop(hs, "light_pattern")
        if hs.light_pattern == "CUSTOM":
            col.prop(hs, "light_custom", text="Steps")
        col.prop(hs, "light_switchable")
        col.prop(hs, "light_start_off")
        from .extract import light_in_logic
        if light_in_logic(obj) and not hs.light_switchable:
            col.label(text="Switchable: logic uses it", icon="NODETREE")
        if hs.light_switchable or hs.light_pattern == "CUSTOM" or hs.light_start_off or light_in_logic(obj):
            col.label(text="Logic name: " + light_name(obj), icon="LINKED")
        elif hs.light_pattern != "0":
            col.label(text="Flickers on its own; Switchable to control it", icon="INFO")
        from .lightpresets import preset_slot
        slot = preset_slot(obj)
        if slot is not None:
            row = col.row(align=True)
            row.label(text=f"Pattern: Behaviour '{slot.graph.name}'", icon="NODETREE")
            op = row.operator("hammerless.behaviour_open", text="", icon="WINDOW")
            op.index = list(hs.behaviours).index(slot)


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
        col.separator()
        col.prop(mat.hammerless, "surface")
        col.prop(mat.hammerless, "texture_scale")
        col.prop(mat.hammerless, "lightmap_scale")


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


class HL_PT_obj_keyvalues(_Panel, bpy.types.Panel):
    bl_label = "Settings"
    bl_parent_id = "HL_PT_object"

    @classmethod
    def poll(cls, context):
        return entity_object(context) is not None and effective_role(entity_object(context)) in ("ENTITY", "BRUSH_ENTITY")

    def draw(self, context):
        hs = entity_object(context).hammerless
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
        return entity_object(context) is not None and effective_role(entity_object(context)) in ("ENTITY", "BRUSH_ENTITY")

    def draw_header_preset(self, context):
        n = len(entity_object(context).hammerless.outputs)
        if n:
            _status(self.layout, str(n))

    def draw(self, context):
        hs = entity_object(context).hammerless
        layout = self.layout
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


def _eye(layout, data, prop):
    layout.prop(data, prop, text="", icon="HIDE_OFF" if getattr(data, prop) else "HIDE_ON", emboss=False)


def _bake_header_slots(layout):
    """Matching columns even when a header has no sky toggle or a shorter status."""
    row = layout.row(align=True)
    slots = []
    for width in (3.0, 1.2, 1.2, 5.5):
        slot = row.row(align=True)
        slot.ui_units_x = width
        slots.append(slot)
    return slots


def _header_bake(layout, context, kind):
    s = context.scene.hammerless
    if kind in {"LIGHT", "VIS"}:
        if _cancel_button(layout, 1.0):
            return
        row = layout.row(align=True)
        if kind == "LIGHT":
            row.enabled = s.light_quality != "OFF" and (s.light_tool == "HAMMERLESS" or s.bake_area == "MAP")
            op = row.operator("hammerless.build", text="Bake")
            op.play, op.bake = False, True
            op.view, op.selected = s.bake_area == "VIEW", s.bake_area == "SELECTED"
        else:
            row.enabled = s.vis_mode != "SKIP"
            row.operator("hammerless.build", text="Bake").vis_only = True
    elif kind == "NAV":
        from .navview import _state
        if _state["busy"]:
            layout.operator("hammerless.nav_cancel", text="Cancel")
            return
        from ..core import compile as cc
        from .ops import game_root
        root = game_root(context)
        operator = "hammerless.nav_analyze" if root and not cc.Tools(root).missing() else "hammerless.nav_predict"
        layout.operator(operator, text="Bake")
    else:
        layout.operator("hammerless.sound_trace", text="Bake")


# ---------------------------------------------------------------- Bake (Lighting, Visibility, Nav Mesh, Sound and
# the Control Volumes that steer them, as its subpanels)

class HL_PT_bake(_Panel, bpy.types.Panel):
    bl_label = "Bake"
    bl_order = 4
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        pass


# ---------------------------------------------------------------- Lighting (with its baking, preview and environment)

class HL_PT_lighting(_Panel, bpy.types.Panel):
    bl_label = "Lighting"
    bl_parent_id = "HL_PT_bake"
    bl_order = 0
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        s = context.scene.hammerless
        bake, eye, sky, status = _bake_header_slots(self.layout)
        _header_bake(bake, context, "LIGHT")
        _eye(eye, s, "show_lightmap")
        sky.prop(s, "show_sky", text="", icon="WORLD", emboss=False)
        _status(status, _label(s, "light_quality"))

    def draw(self, context):
        s = context.scene.hammerless
        layout = self.layout
        ours = s.light_tool == "HAMMERLESS"
        col = _settings(layout)
        col.prop(s, "light_quality", text="Quality")
        on = col.column()
        on.enabled = s.light_quality != "OFF"
        on.prop(s, "lightmap_scale", text="Lightmap Scale")
        from .vmfimport import imported as _imported
        if _imported(context.scene):
            on.prop(s, "lightmap_scale_imported")
        on.prop(s, "static_prop_lighting")
        bm = on.column()
        bm.enabled = ours and not s.light_exact
        bm.prop(s, "light_bounce_method", text="Bounce")
        mx = on.column()
        mx.enabled = ours and not s.light_exact
        mx.prop(s, "light_mixing")
        if s.light_mixing and ours and not s.light_exact and s.light_bounce_method != "GI":
            _hint(on, "Live Light Mixing bounces light on the GPU")
        layout.separator()
        area = s.bake_area
        view = area == "VIEW"
        partial = area != "MAP"
        row = layout.row(align=True)
        row.scale_y = 1.3
        if not _cancel_button(row, 1.0):
            row.enabled = s.light_quality != "OFF" and (ours or not partial)
            op = row.operator("hammerless.build", text="Bake Lighting", icon="LIGHT_SUN")
            op.play, op.bake, op.view = False, True, view
            op.selected = area == "SELECTED"
        row.prop(s, "bake_area", text="")
        if view:
            dist = layout.row(align=True)
            dist.enabled = ours
            dist.prop(s, "light_view_distance", text="View Distance")
        # (only what needs acting on: explanations are the settings' tooltips)
        if s.light_quality != "OFF":
            if partial and not ours:
                _hint(layout, "Baking part of the map needs the", "Hammerless light compiler (Advanced)",
                      icon="ERROR")
            if s.vis_mode == "SKIP" and not (ours and s.light_bounce_method == "GI"):
                _hint(layout, "Visibility is Off: no bounced light", icon="ERROR")


class HL_PT_view_light(_Nested, _Panel, bpy.types.Panel):
    bl_label = "Baked Lighting"
    bl_parent_id = "HL_PT_lighting"

    def draw_header(self, context):
        from .lightview import shown
        if shown():
            _eye(self.layout, context.scene.hammerless, "show_lightmap")

    def draw_header_preset(self, context):
        from .lightview import header_status
        text, alert = header_status(context)
        _status(self.layout, text, alert)

    def draw(self, context):
        from .lightview import draw_panel
        draw_panel(self.layout, context)


class HL_PT_environment(_Nested, _Sub, bpy.types.Panel):
    bl_label = "Environment"
    bl_parent_id = "HL_PT_lighting"

    def draw_header_preset(self, context):
        _status(self.layout, context.scene.hammerless.skyname)

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "sky_choice")
        if s.sky_choice == "CUSTOM":
            col.prop(s, "skyname", text="Name")
        col.prop(s, "show_sky", text="Show in Viewport")
        col.prop(s, "sky_view_exposure", text="Sky Brightness")
        from .skyview import error
        if s.show_sky and error():
            _hint(self.layout, f"Sky: {error()}", icon="ERROR")
        col.prop(s, "sky_light")
        if s.sky_light == "IMAGE":
            col.template_ID(s, "sky_image", open="image.open")
            col.prop(s, "sky_rotation")
        if s.sky_light != "FLAT" and s.light_tool != "HAMMERLESS":
            _hint(self.layout, "Sky Light from the sky needs the", "Hammerless light compiler (Advanced)", icon="ERROR")


def _own_suns(context) -> list:
    from .mapcollection import map_objects
    inside = map_objects(context.scene)
    return [o for o in context.scene.objects if o.name in inside
            and o.type != "LIGHT" and o.hammerless.classname == "light_environment" and o.visible_get()]


class HL_PT_sun(_Nested, _Sub, bpy.types.Panel):
    indent = 2
    bl_label = "Sun"
    bl_parent_id = "HL_PT_environment"

    def draw_header_preset(self, context):
        if _own_suns(context):
            _status(self.layout, "the map's own")

    def draw(self, context):
        s = context.scene.hammerless
        layout = self.layout
        own = _own_suns(context)
        if own:                       # (an imported map's own sun: the settings below don't touch it)
            col = layout.column(align=True)
            for o in own[:4]:
                row = col.row(align=True)
                row.label(text=o.name, icon="LIGHT_SUN")
                row.operator("hammerless.select_object", text="Select", icon="RESTRICT_SELECT_OFF").name = o.name
            _hint(layout, "The map's own sun lights it: change its", "_light / _ambient keys, or delete it")
            layout.separator()
        col = _settings(layout)
        col.prop(s, "auto_sun")
        sub = col.column()
        sub.enabled = s.auto_sun and not own
        sub.prop(s, "sun_color")
        sub.prop(s, "sun_brightness")
        sub.prop(s, "sun_pitch")
        sub.prop(s, "sun_yaw")
        sub.separator()
        sky = col.column()
        sky.enabled = not own             # (the sky light: also without a sun, its entity's sun part at 0)
        if s.sky_light == "FLAT":
            sky.prop(s, "ambient_color")
        sky.prop(s, "ambient_brightness")
        if not own and not s.auto_sun:
            _hint(layout, "No sun: only the sky light is added (Sky Light Brightness 0: none)")


class HL_PT_fog(_Nested, _Sub, bpy.types.Panel):
    indent = 2
    bl_label = "Fog"
    bl_parent_id = "HL_PT_environment"

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


class HL_PT_light_quality(_Nested, _Sub, bpy.types.Panel):
    bl_label = "Quality Details"
    bl_parent_id = "HL_PT_lighting"

    def draw(self, context):
        s = context.scene.hammerless
        ours = s.light_tool == "HAMMERLESS"
        col = _settings(self.layout)
        col.enabled = s.light_quality != "OFF"
        full = col.column()
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
        if ours and not s.light_exact and s.light_bounce_method == "GI":
            full.prop(s, "light_gi_rays")
        else:
            full.prop(s, "light_patch_size")
        col.prop(s, "light_prop_polys")


class HL_PT_light_advanced(_Nested, _Sub, bpy.types.Panel):
    bl_label = "Advanced"
    bl_parent_id = "HL_PT_lighting"

    def draw_header_preset(self, context):
        _status(self.layout, _label(context.scene.hammerless, "light_tool"))

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "light_tool", text="Light Compiler")
        if s.light_tool == "HAMMERLESS":
            col.prop(s, "light_exact")
        if s.light_tool == "CYCLES":
            col.prop(s, "cycles_samples")
            col.prop(s, "cycles_stitch")
            col.prop(s, "cycles_denoise")
        col.separator()
        q = col.column()
        q.enabled = s.light_tool == "HAMMERLESS"
        q.prop(s, "light_fix_quirks")
        col.prop(s, "extra_vrad", text="Extra vrad Options")


# ---------------------------------------------------------------- Visibility

class HL_PT_visibility(_Panel, bpy.types.Panel):
    bl_label = "Visibility"
    bl_parent_id = "HL_PT_bake"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        from .visview import header_status
        s = context.scene.hammerless
        view = header_status(context)
        bake, eye, sky, status = _bake_header_slots(self.layout)
        _header_bake(bake, context, "VIS")
        eye.operator("hammerless.toggle_vis_preview", text="", emboss=False,
                             icon="HIDE_OFF" if s.vis_view != "OFF" else "HIDE_ON")
        sky.label(text="")
        _status(status, _label(s, "vis_mode") + (f" · {view}" if view else ""))

    def draw(self, context):
        from .visview import draw_panel
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "vis_mode", text="Quality")
        col.prop(s, "vis_tool", text="Vis Compiler")
        if s.vis_tool == "HAMMERLESS":
            col.prop(s, "vis_exact")
            if not s.vis_exact:
                col.prop(s, "vis_gpu")
                if s.vis_gpu:
                    col.row().prop(s, "vis_culling", expand=True)
        col.prop(s, "extra_vvis", text="Extra vvis Options")
        if not _cancel_button(self.layout, 1.3):
            row = self.layout.row()
            row.scale_y = 1.3
            row.enabled = s.vis_mode != "SKIP"
            row.operator("hammerless.build", text="Compute Visibility", icon="HIDE_OFF").vis_only = True
        self.layout.separator()
        draw_panel(self.layout, context)


# ---------------------------------------------------------------- Nav Mesh

class HL_PT_navmesh(_Panel, bpy.types.Panel):
    bl_label = "Nav Mesh"
    bl_parent_id = "HL_PT_bake"
    bl_order = 2
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        short, _long = _nav_status(context)
        bake, eye, sky, status = _bake_header_slots(self.layout)
        _header_bake(bake, context, "NAV")
        _eye(eye, context.scene.hammerless, "show_nav")
        sky.label(text="")
        _status(status, short, alert=short in ("Out of date", "L4D2 not found"))

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "nav_source")
        sub = col.column()
        sub.enabled = s.nav_source == "BLENDER"
        sub.prop(s, "nav_analysis")
        sub.prop(s, "wall_climbs")
        sub.prop(s, "nav_culling")
        col.prop(s, "generate_nav", text="Rebuild Nav Next Time")
        _hint(self.layout, _nav_status(context)[1])
        from .navview import draw_buttons
        draw_buttons(self.layout, context)


class HL_PT_view_nav(_Nested, _Panel, bpy.types.Panel):
    bl_label = "Preview"
    bl_parent_id = "HL_PT_navmesh"

    def draw_header(self, context):
        _eye(self.layout, context.scene.hammerless, "show_nav")

    def draw_header_preset(self, context):
        from .navview import header_status
        text = header_status()
        if text:
            _status(self.layout, text)

    def draw(self, context):
        from .navview import draw_panel
        draw_panel(self.layout, context)


# ---------------------------------------------------------------- Sound

class HL_PT_sound(_Panel, bpy.types.Panel):
    bl_label = "Sound"
    bl_parent_id = "HL_PT_bake"
    bl_order = 3
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        bake, eye, sky, status = _bake_header_slots(self.layout)
        _header_bake(bake, context, "SOUND")
        _eye(eye, context.scene.hammerless, "show_sound")
        sky.label(text="")
        _status(status, _label(context.scene.hammerless, "sound_mode"))

    def draw(self, context):
        from .sound import draw_panel
        col = _settings(self.layout)
        col.prop(context.scene.hammerless, "sound_mode")
        self.layout.separator()
        draw_panel(self.layout, context)


# ---------------------------------------------------------------- Control Volumes

def control_volume_objects(context) -> list:
    """The map's Control Volumes (and older volumes), by name."""
    from ..core.entities import CONTROL, NAV_CUT, NO_BAKE
    from .mapcollection import map_objects
    inside = map_objects(context.scene)
    return sorted((o for o in context.scene.objects if o.name in inside
                   and o.hammerless.classname in (CONTROL, NAV_CUT, NO_BAKE)), key=lambda o: o.name.lower())


class HL_PT_control_volumes(_Panel, bpy.types.Panel):
    bl_label = "Control Volumes"
    bl_parent_id = "HL_PT_bake"
    bl_order = 4
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        vols = control_volume_objects(context)
        if vols:
            on = sum(_on({k.key: k.value for k in o.hammerless.keyvalues}.get("enabled", "1")) for o in vols)
            bake, eye, sky, status = _bake_header_slots(self.layout)
            bake.label(text="")
            sky.label(text="")
            op = eye.operator("hammerless.control_enable", text="", emboss=False,
                                      icon="CHECKBOX_HLT" if on == len(vols) else
                                           "CHECKBOX_DEHLT" if not on else "REMOVE")
            op.all_volumes, op.enabled = True, on != len(vols)
            _status(status, f"{on} of {len(vols)} on")

    def draw(self, context):
        layout = self.layout
        vols = control_volume_objects(context)
        if not vols:
            _hint(layout, "No Control Volumes in the map", "Add one: Add > Volumes > Control Volume")
        for o in vols:
            kv = {k.key: k.value for k in o.hammerless.keyvalues}
            on = _on(kv.get("enabled", "1"))
            row = layout.row(align=True)
            op = row.operator("hammerless.control_enable", text="", emboss=False,
                              icon="CHECKBOX_HLT" if on else "CHECKBOX_DEHLT")
            op.name, op.enabled = o.name, not on
            sel = row.operator("hammerless.select_object", text=o.name, emboss=o == context.object,
                               icon="RESTRICT_SELECT_OFF")
            sel.name = o.name
            what = row.row(align=True)
            what.enabled = on
            if o.hammerless.classname == "hammerless_control":
                what.label(text="Only" if kv.get("mode", "EXCLUDE").upper() == "ONLY" else "Not")
                for key, label, icon in _CONTROL_TICKS:
                    if _on(kv.get(key, "0")):
                        what.label(text="", icon=icon)
            else:
                what.label(text="(older volume)")
        add = layout.row()
        add.operator("hammerless.spawn_item", text="Add Control Volume", icon="ADD").item = "entity:hammerless_control"


# ---------------------------------------------------------------- AI Director

class HL_PT_director(_Panel, bpy.types.Panel):
    bl_label = "AI Director"
    bl_order = 8
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        if context.scene.hammerless.game_mode == "CAMPAIGN":
            self.layout.prop(context.scene.hammerless, "director_enabled", text="")

    def draw_header_preset(self, context):
        if context.scene.hammerless.game_mode == "SURVIVAL" or not context.scene.hammerless.director_enabled:
            _status(self.layout, "game default")

    def draw(self, context):
        s = context.scene.hammerless
        layout = self.layout
        col = _settings(layout)
        if s.game_mode == "SURVIVAL":
            layout.label(text="Stock Survival controls attack timing and population.", icon="INFO")
            layout.label(text="Start the round with a Survival Start Button.")
            return
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


# ---------------------------------------------------------------- Logic Graphs

class HL_PT_logic(_Panel, bpy.types.Panel):
    bl_label = "Logic Graphs"
    bl_order = 9
    bl_options = {"DEFAULT_CLOSED"}

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
            for t in trees:          # which map each graph belongs to, and the brain it lives on
                row = col.row(align=True)
                row.label(text=t.name)
                row.prop_search(t, "scene_name", bpy.data, "scenes", text="")
                row = col.row(align=True)
                row.prop(t, "brain", text="", icon="OUTLINER_OB_EMPTY")
                if t.brain is not None:
                    row.operator("hammerless.select_object", text="", icon="RESTRICT_SELECT_OFF").name = t.brain.name
                else:
                    row.operator("hammerless.logic_give_brain", text="", icon="ADD").graph = t.name
                row.operator("hammerless.logic_open_graph", text="", icon="WINDOW").graph = t.name
                col.separator()
        col = layout.column(align=True)
        col.operator("hammerless.logic_new", icon="ADD")
        col.operator("hammerless.logic_from_outputs", icon="NODE")


# ---------------------------------------------------------------- Settings

class HL_PT_settings(_Panel, bpy.types.Panel):
    bl_label = "Settings"
    bl_order = 10
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        pass


class HL_PT_folders(_Sub, bpy.types.Panel):
    bl_label = "Game & Folders"
    bl_parent_id = "HL_PT_settings"

    def draw_header_preset(self, context):
        if setup_problem(context) is None:
            _status(self.layout, "L4D2 found")

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


class HL_PT_compilers(_Sub, bpy.types.Panel):
    bl_label = "Map & Model Compilers"
    bl_parent_id = "HL_PT_settings"

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "map_tool", text="Map Compiler")
        col.prop(s, "model_compiler", text="Model Compiler")
        col.prop(s, "extra_vbsp", text="Extra vbsp Options")
        col.prop(s, "general_native")


class HL_PT_scene(_Sub, bpy.types.Panel):
    bl_label = "Map Defaults"
    bl_parent_id = "HL_PT_settings"

    def draw(self, context):
        s = context.scene.hammerless
        col = _settings(self.layout)
        col.prop(s, "auto_seal")
        col.prop(s, "auto_detail")
        col.separator()
        col.prop(s, "units_per_meter")
        col.prop(s, "default_material")
        col.separator()
        col.prop(s, "check_game_content")
        col.prop(s, "model_previews")


class HL_PT_game(_Sub, bpy.types.Panel):
    bl_label = "Game Window"
    bl_parent_id = "HL_PT_settings"

    def draw_header_preset(self, context):
        s = context.scene.hammerless
        _status(self.layout, f"{s.window_width}×{s.window_height}")

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
        sub = col.column()
        sub.enabled = s.game_mode == "CAMPAIGN"
        sub.prop(s, "autotest")
        if s.game_mode != "CAMPAIGN":
            col.label(text="Automated route walkthrough is Campaign-only.")


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
        for key, icon in (("SURVIVAL_START", "PLAY"), ("HORDE_TRIGGER", "GHOST_ENABLED"), ("HORDE_BUTTON", "GHOST_ENABLED"),
                          ("TANK_AMBUSH", "GHOST_ENABLED"), ("CRESCENDO_BUTTON", "GHOST_ENABLED"),
                          ("ZOMBIE_SPAWN_AREA", "MOD_MASK"), ("LADDER", "SORT_DESC"),
                          ("ZOMBIE_LADDER", "SORT_DESC"), ("ZOMBIE_CLIMB", "TRIA_UP"),
                          ("GATE_BUTTON", "SORT_ASC"), ("CAR_ALARM", "AUTO")):
            layout.operator("hammerless.add_preset", text=PRESETS[key].label, icon=icon).preset = key
        layout.separator()
        for m in CATEGORY_MENUS:
            layout.menu(m.bl_idname)
        layout.separator()
        layout.operator_menu_enum("hammerless.add_game_skeleton", "character", text="Game Skeleton",
                                  icon="ARMATURE_DATA")
        layout.operator_menu_enum("hammerless.add_placed_character", "character", text="Placed Character",
                                  icon="OUTLINER_OB_ARMATURE")
        layout.separator()
        layout.operator("hammerless.add_entity", text="Search Entities...", icon="VIEWZOOM")


def _add_menu(self, context):
    self.layout.menu(HL_MT_add.bl_idname, icon="WORLD")


CLASSES = (HL_UL_keyvalues, HL_UL_outputs, HL_MT_weather,
           HL_PT_build, HL_PT_problems,
           HL_PT_object, HL_PT_obj_model, HL_PT_obj_brushes, HL_PT_obj_brushes_tuning, HL_PT_obj_light, HL_PT_obj_material, HL_PT_obj_terrain, HL_PT_obj_keyvalues, HL_PT_obj_outputs,
           HL_PT_bake, HL_PT_lighting, HL_PT_view_light, HL_PT_environment, HL_PT_sun, HL_PT_fog, HL_PT_light_quality,
           HL_PT_light_advanced,
           HL_PT_visibility, HL_PT_navmesh, HL_PT_view_nav, HL_PT_sound, HL_PT_control_volumes, HL_PT_director,
           HL_PT_logic,
           HL_PT_settings, HL_PT_folders, HL_PT_compilers, HL_PT_scene, HL_PT_game, HL_PT_debug,
           HL_PT_collection, *CATEGORY_MENUS, HL_MT_add)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_add.append(_add_menu)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(_add_menu)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
