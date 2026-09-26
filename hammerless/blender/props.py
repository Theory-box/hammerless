"""Blender property groups: scene settings, object/collection tags, materials."""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)

from ..core.entities import default_keyvalues
from ..core.spawnlist import CATEGORIES as SPAWN_CATEGORIES

OBJECT_ROLES = [
    ("AUTO", "Auto", "Lights become lights, meshes follow their collection (default: brush), "
                     "empties are ignored unless they have an entity class"),
    ("BRUSH", "Brush", "World geometry. Each loose part must be a convex solid"),
    ("BRUSH_ENTITY", "Brush Entity", "Brush geometry that belongs to an entity (func_detail, triggers...)"),
    ("TERRAIN", "Terrain", "Heightfield mesh converted to displacements"),
    ("ENTITY", "Point Entity", "An entity at this object's origin (spawns, items, infected...)"),
    ("IGNORE", "Ignore", "Not exported"),
]

COLLECTION_ROLES = [
    ("NONE", "Inherit", "Use the parent collection's role"),
    ("BRUSH", "Brush", "Meshes inside are world brushes"),
    ("TERRAIN", "Terrain", "Meshes inside are terrain"),
    ("IGNORE", "Ignore", "Nothing inside is exported"),
]

COMPILE_PRESETS = [
    ("QUICK", "Quick", "Geometry only (no vis, no lighting). Fastest; the map is fullbright"),
    ("FAST", "Fast", "Fast vis + fast lighting. Terrain shows lighting seams between patches"),
    ("NORMAL", "Normal", "Full vis + normal lighting. Small maps still compile in seconds"),
    ("FINAL", "Final", "Full quality. Slow"),
    ("CUSTOM", "Custom", "Choose each compile step yourself (see Compile settings)"),
]


def _kv_changed(self, context):
    from .presets import on_kv_value
    on_kv_value(self, context)


def _param_changed(self, context):
    from .presets import on_output_param
    on_output_param(self, context)


def _target_changed(self, context):
    from .presets import on_output_target
    on_output_target(self, context)


class HL_KeyValue(bpy.types.PropertyGroup):
    key: StringProperty(name="Key")
    value: StringProperty(name="Value", update=_kv_changed)


class HL_Output(bpy.types.PropertyGroup):
    output: StringProperty(name="Output", default="OnTrigger",
                           description="Event on THIS entity, e.g. OnTrigger, OnPressed, OnStartTouch")
    target: StringProperty(name="Target", default="director", update=_target_changed,
                           description="Name (targetname) of the entity to send the input to")
    input: StringProperty(name="Input", default="ForcePanicEvent",
                          description="What the target should do, e.g. ForcePanicEvent, SpawnZombie, Open")
    parameter: StringProperty(name="Parameter", description="Optional value passed with the input",
                              update=_param_changed)
    delay: FloatProperty(name="Delay", min=0.0, description="Seconds to wait before sending")
    only_once: BoolProperty(name="Only Once", default=True)


def _on_classname_change(self, context):
    """Reset keyvalues to the catalog defaults when the classname changes."""
    self.keyvalues.clear()
    for k, v in default_keyvalues(self.classname).items():
        kv = self.keyvalues.add()
        kv.key, kv.value = k, v


class HL_ObjectSettings(bpy.types.PropertyGroup):
    role: EnumProperty(name="Role", items=OBJECT_ROLES, default="AUTO")
    classname: StringProperty(name="Class", description="Entity classname",
                              update=_on_classname_change)
    keyvalues: CollectionProperty(type=HL_KeyValue)
    keyvalues_index: IntProperty()
    outputs: CollectionProperty(type=HL_Output)
    outputs_index: IntProperty()
    preset: StringProperty(description="On a preset's parent Empty: which preset it is")
    preset_part: StringProperty(description="On a preset part: which part it is")
    use_convex_hull: BoolProperty(
        name="Use Convex Hull",
        description="Wrap each loose part in its convex hull instead of requiring it to be convex")
    terrain_power: EnumProperty(name="Detail", default="3", items=[
        ("2", "Low (power 2)", "5x5 vertices per patch"),
        ("3", "Medium (power 3)", "9x9 vertices per patch"),
        ("4", "High (power 4)", "17x17 vertices per patch"),
    ])
    terrain_patch_size: FloatProperty(
        name="Patch Size", default=512.0, min=64.0, max=2048.0,
        description="Size of each displacement patch in Hammer units")


class HL_CollectionSettings(bpy.types.PropertyGroup):
    role: EnumProperty(name="Role", items=COLLECTION_ROLES, default="NONE")


# Surface list for the material panel. Starts with the built-in list and is replaced
# by the game's full list (104 surfaces) once game files have been read.
SURFACE_ITEMS: list[tuple[str, str, str]] = []


def set_surface_list(surfaces) -> None:
    from ..core.surfaces import friction_label
    SURFACE_ITEMS.clear()
    SURFACE_ITEMS.append(("DEFAULT", "Default", "Game materials keep their own surface; custom ones use concrete"))
    for s in surfaces:
        SURFACE_ITEMS.append((s.name, s.name, friction_label(s)))


def _surface_items(self, context):
    if not SURFACE_ITEMS:
        from ..core.surfaces import FALLBACK
        set_surface_list(FALLBACK)
    return SURFACE_ITEMS


_MONITOR_ITEMS: list[tuple[str, str, str]] = []


def _monitor_items(self, context):
    from ..core.window import monitors
    _MONITOR_ITEMS.clear()  # module-level list keeps the strings alive for Blender
    _MONITOR_ITEMS.append(("-1", "Game decides", "Let the game place its window"))
    for m in monitors():
        _MONITOR_ITEMS.append((str(m.index), m.label, f"Open the game on monitor {m.label}"))
    return _MONITOR_ITEMS


def _on_texture_scale(self, context):
    from .preview import update_mapping
    for mat in bpy.data.materials:
        if mat.hammerless == self:
            update_mapping(mat, context.scene.hammerless.units_per_meter)
            break


class HL_MaterialSettings(bpy.types.PropertyGroup):
    source_material: StringProperty(
        name="Game Material",
        description="L4D2 material path, e.g. concrete/concrete_floor_01. Leave empty to "
                    "convert this material's image texture into a custom material")
    texture_scale: FloatProperty(name="Texture Scale", default=0.25, min=0.01, max=16.0, update=_on_texture_scale,
                                 description="Hammer texture scale (0.25 = Hammer default; bigger = larger texture)")
    surface: EnumProperty(name="Surface", items=_surface_items,
                          description="Friction, footstep and impact sounds for faces with this material")
    lightmap_scale: IntProperty(name="Lightmap Scale", default=0, min=0, max=128,
                                description="Shadow detail for these faces (0 = map default, 8 = sharper, "
                                            "32 = blurrier but faster)")
    surfaceprop: StringProperty(options={"HIDDEN"})  # v0.1 setting, kept so old files load


def _on_spawn_category(self, context):
    self.spawn_search = ""   # picking a tile shows that category, not old search results


def _on_spawn_index(self, context):
    if 0 <= self.spawn_index < len(self.spawn_items):
        self.spawn_selected = self.spawn_items[self.spawn_index].name


def _nav_display(self, context):
    from .navview import _on_display_change
    _on_display_change(self, context)


def _on_problem_index(self, context):
    from .problems import go_to_problem
    go_to_problem(context, self.problem_index)


class HL_Problem(bpy.types.PropertyGroup):
    """One map check result (name = message)."""
    severity: StringProperty()              # ERROR / WARNING / INFO
    source: StringProperty()                # object to select
    location: FloatVectorProperty(size=3, subtype="TRANSLATION")   # Blender units
    has_location: BoolProperty()
    ingame: BoolProperty()                  # reported by the game after Build & Play


class HL_SpawnListItem(bpy.types.PropertyGroup):
    """One row of the Add panel list (name = spawn item id). Filled from core/spawnlist.py."""


class HL_SceneSettings(bpy.types.PropertyGroup):
    # --- Add panel
    spawn_category: EnumProperty(
        name="Category", default="EVENTS", update=_on_spawn_category,
        items=[(k, label, f"Show {label.lower()}", icon, i) for i, (k, label, icon) in enumerate(SPAWN_CATEGORIES)])
    spawn_search: StringProperty(name="Search", options={"TEXTEDIT_UPDATE"},
                                 description="Search everything you can add")
    spawn_selected: StringProperty(default="preset:HORDE_TRIGGER")
    spawn_items: CollectionProperty(type=HL_SpawnListItem)
    # --- map check
    problems: CollectionProperty(type=HL_Problem)
    problem_index: IntProperty(default=-1, update=_on_problem_index)
    problems_checked: BoolProperty(description="The map has been checked since the file opened")
    show_nav: BoolProperty(name="Show Nav Mesh", default=True, update=lambda self, c: _nav_display(self, c),
                           description="Draw the game's nav mesh for this map in the viewport")
    nav_color_mode: EnumProperty(name="Colour", default="REACH", update=lambda self, c: _nav_display(self, c), items=[
        ("REACH", "Can survivors reach it?", "Green: reachable from the start room. Red: not"),
        ("FLOW", "Distance from the start", "Heat map of walking distance along the path"),
        ("SPAWN", "Zombie spawn marks", "Orange: areas marked OBSCURED by Zombie Spawn Areas"),
    ])
    show_nav_links: BoolProperty(name="Drops and Jumps", default=True, update=lambda self, c: _nav_display(self, c),
                                 description="Arrows for one-way drop-downs (orange) and jump-ups (cyan)")
    nav_xray: BoolProperty(name="X-Ray", default=False, update=lambda self, c: _nav_display(self, c),
                           description="Draw the nav mesh through walls")
    show_problem_markers: BoolProperty(name="Show Markers", default=True,
                                       description="Draw numbered markers in the viewport where problems are")
    spawn_index: IntProperty(default=-1, update=_on_spawn_index)
    spawn_favorites: StringProperty(description="Starred Add panel items (comma separated)")
    # --- build
    map_name: StringProperty(name="Map Name", default="my_map",
                             description="File name of the map (lowercase, no spaces)")
    compile_preset: EnumProperty(name="Quality", items=COMPILE_PRESETS, default="NORMAL")
    vis_mode: EnumProperty(name="Visibility (vvis)", default="FULL", items=[
        ("SKIP", "Skip", "No visibility pass: everything always renders (slow in-game on big maps)"),
        ("FAST", "Fast", "Quick visibility pass"),
        ("FULL", "Full", "Full visibility pass (best in-game performance)")])
    rad_mode: EnumProperty(name="Lighting (vrad)", default="NORMAL", items=[
        ("SKIP", "Skip (fullbright)", "No lighting pass"),
        ("FAST", "Fast", "Quick lighting; terrain may show seams"),
        ("NORMAL", "Normal", "Normal lighting"),
        ("FINAL", "Final", "Highest quality lighting; slow")])
    hdr_mode: EnumProperty(name="HDR", default="BOTH", items=[
        ("BOTH", "LDR + HDR", "Works with either game video setting"),
        ("LDR", "LDR only", "Faster; HDR players see flat lighting"),
        ("HDR", "HDR only", "Faster; LDR players see flat lighting")])
    static_prop_lighting: BoolProperty(name="Per-vertex Prop Lighting",
                                       description="Light static props per vertex (-StaticPropLighting)")
    extra_vbsp: StringProperty(name="vbsp", description="Extra command-line options for vbsp")
    extra_vvis: StringProperty(name="vvis", description="Extra command-line options for vvis")
    extra_vrad: StringProperty(name="vrad", description="Extra command-line options for vrad, e.g. -bounce 50")

    # --- lighting & sky
    skyname: StringProperty(name="Sky", default="sky_day01_09_hdr",
                            description="Skybox texture name (use the list button to pick one)")
    auto_sun: BoolProperty(name="Add Sun if Missing", default=True,
                           description="Add a sun from these settings when the scene has no Blender sun lamp")
    sun_color: FloatVectorProperty(name="Sun Color", subtype="COLOR", size=3, min=0, max=1,
                                   default=(1.0, 0.96, 0.88))
    sun_brightness: IntProperty(name="Sun Brightness", default=400, min=0, max=5000)
    sun_pitch: FloatProperty(name="Sun Height", default=-50.0, min=-90.0, max=0.0,
                             description="-90 = straight down (noon), near 0 = low evening sun")
    sun_yaw: FloatProperty(name="Sun Direction", default=30.0, min=0.0, max=360.0,
                           description="Compass direction the sunlight travels towards")
    ambient_color: FloatVectorProperty(name="Sky Light Color", subtype="COLOR", size=3, min=0, max=1,
                                       default=(0.55, 0.63, 0.75))
    ambient_brightness: IntProperty(name="Sky Light Brightness", default=80, min=0, max=2000,
                                    description="Light from the sky dome that fills shadows")
    lightmap_scale: IntProperty(name="Lightmap Scale", default=16, min=1, max=128,
                                description="Shadow detail on brush faces: 8 = sharp, 16 = Valve default, "
                                            "32+ = soft but faster to compile")

    # --- fog
    fog_enabled: BoolProperty(name="Fog", default=False)
    fog_color: FloatVectorProperty(name="Color", subtype="COLOR", size=3, min=0, max=1,
                                   default=(0.43, 0.47, 0.51))
    fog_start: FloatProperty(name="Start", default=512.0, min=0.0, description="Fog begins (Hammer units)")
    fog_end: FloatProperty(name="End", default=4096.0, min=1.0, description="Fog is thickest (Hammer units)")
    fog_max_density: FloatProperty(name="Max Density", default=0.8, min=0.0, max=1.0)

    # --- director
    director_enabled: BoolProperty(
        name="Custom Director Settings", default=False,
        description="Override how the AI Director spawns infected on this map")
    dir_common_limit: IntProperty(name="Max Common Infected", default=30, min=0, max=200)
    dir_mob_min: IntProperty(name="Horde Size Min", default=10, min=0, max=200)
    dir_mob_max: IntProperty(name="Horde Size Max", default=30, min=0, max=200)
    dir_mob_interval_min: IntProperty(name="Horde Every Min (s)", default=90, min=0, max=3600)
    dir_mob_interval_max: IntProperty(name="Horde Every Max (s)", default=180, min=0, max=3600)
    dir_max_specials: IntProperty(name="Max Special Infected", default=4, min=0, max=16)
    dir_special_interval: IntProperty(name="Special Respawn (s)", default=45, min=0, max=600)
    dir_tank_limit: IntProperty(name="Max Tanks", default=1, min=0, max=8)
    dir_witch_limit: IntProperty(name="Max Witches", default=1, min=0, max=16)
    dir_no_mobs: BoolProperty(name="No Random Hordes", description="Only your triggers start hordes")
    dir_no_wanderers: BoolProperty(name="No Wandering Zombies")

    # --- game
    generate_nav: BoolProperty(
        name="Rebuild Nav Mesh", default=False,
        description="Rebuild the nav mesh (how bots and zombies find their way) on the next Build & Play. "
                    "Tick it after changing walls, floors or terrain. A map that has no nav mesh yet "
                    "gets one automatically")
    window_monitor: EnumProperty(name="Monitor", items=_monitor_items,
                                 description="Which monitor the game window opens on")
    window_width: IntProperty(name="Width", default=1600, min=640, max=7680)
    window_height: IntProperty(name="Height", default=900, min=480, max=4320)
    window_borderless: BoolProperty(name="Borderless", default=False)
    launch_extra: StringProperty(name="Launch Options", description="Extra game options, e.g. -high")
    difficulty: EnumProperty(name="Difficulty", default="Normal", items=[
        ("", "Keep Current", "Don't change the game's difficulty"),
        ("Easy", "Easy", ""), ("Normal", "Normal", ""), ("Hard", "Advanced", ""),
        ("Impossible", "Expert", "")])

    # --- debug
    debug_log: BoolProperty(
        name="Debug Log", default=False,
        description="Build a script into the map that logs Director events and stats to the game "
                    "console (lines starting HAMMERLESS_DEBUG). No cheats needed")
    debug_interval: FloatProperty(name="Stats Every (s)", default=5.0, min=1.0, max=60.0)
    autotest: BoolProperty(
        name="Bot Walkthrough Test", default=False,
        description="Build a test into the map: the survivor bots walk through every horde trigger, "
                    "press every button and finish in the end safe room (survivors can't die meanwhile). "
                    "Results in the console (HAMMERLESS_AUTOTEST). Turn off for real play")

    # --- advanced / paths
    game_root: StringProperty(name="L4D2 Folder", subtype="DIR_PATH",
                              description="Left 4 Dead 2 install folder (auto-detected if empty)")
    output_dir: StringProperty(name="Work Folder", subtype="DIR_PATH", default="//hammerless_build",
                               description="Where the .vmf and compile files are written")
    units_per_meter: FloatProperty(
        name="Units per Meter", default=52.49, min=1.0, max=200.0,
        description="Hammer units per Blender meter. 52.49 makes real-world-size buildings "
                    "match Valve's maps (doors, stairs). 39.37 = true inches")
    default_material: StringProperty(name="Default Material", default="dev/dev_measuregeneric01b",
                                     description="Used for faces without a material")
    auto_detail: EnumProperty(
        name="Auto Detail", default="SMART",
        description="Turn brushes into func_detail so the visibility compile (vvis) stays fast. "
                    "Detail brushes still block players and cast shadows",
        items=[("OFF", "Off", "Every brush cuts visibility, like plain Hammer (slowest vvis)"),
               ("SMART", "Round and Small", "Cylinders, arches and pieces under 128 units (recommended)"),
               ("ALL", "Everything", "Fastest vvis: the map becomes one visibility region. Fine for "
                                     "small maps; big maps may render more than needed")])
    auto_seal: BoolProperty(name="Auto Seal (skybox shell)", default=True,
                            description="Wrap the map in a skybox box so it can never leak")
    model_previews: BoolProperty(name="3D Model Previews", default=True,
                                 description="Show props and items as their real game models (textured). "
                                             "Off = simple boxes (faster in huge scenes)")
    check_game_content: BoolProperty(name="Check Materials/Models", default=True,
                                     description="Warn about materials and models missing from the game")
    last_log: StringProperty()


CLASSES = (HL_Problem, HL_SpawnListItem, HL_KeyValue, HL_Output, HL_ObjectSettings, HL_CollectionSettings, HL_MaterialSettings, HL_SceneSettings)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.hammerless = PointerProperty(type=HL_ObjectSettings)
    bpy.types.Collection.hammerless = PointerProperty(type=HL_CollectionSettings)
    bpy.types.Material.hammerless = PointerProperty(type=HL_MaterialSettings)
    bpy.types.Scene.hammerless = PointerProperty(type=HL_SceneSettings)


def unregister():
    del bpy.types.Scene.hammerless
    del bpy.types.Material.hammerless
    del bpy.types.Collection.hammerless
    del bpy.types.Object.hammerless
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
