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
    ("MODEL", "Custom Model", "This mesh becomes a game model, placed here as a prop (any shape: not "
                              "limited to convex brushes). Copies sharing the mesh (Alt+D) share one model"),
    ("IGNORE", "Ignore", "Not exported"),
]

COLLECTION_ROLES = [
    ("NONE", "Inherit", "Use the parent collection's role"),
    ("BRUSH", "Brush", "Meshes inside are world brushes"),
    ("TERRAIN", "Terrain", "Meshes inside are terrain"),
    ("IGNORE", "Ignore", "Nothing inside is exported"),
]

DETAIL_CHOICES = [
    ("AUTO", "Auto", "Follow the map's Auto Detail setting (Settings > Compile)"),
    ("DETAIL", "Detail", "Always func_detail: doesn't slow down vvis, but doesn't block visibility or "
                         "seal the map. For furniture, trim, overlapping boxes"),
    ("WORLD", "World", "Never func_detail: blocks visibility, so the game skips drawing what's behind it. "
                       "For walls between areas"),
]

COMPILE_PRESETS = [
    ("QUICK", "Quick", "Geometry only (no vis, no lighting). Fastest; the map is fullbright"),
    ("FAST", "Fast", "Fast vis + fast lighting. Terrain shows lighting seams between patches"),
    ("NORMAL", "Normal", "Full vis + normal lighting. Small maps still compile in seconds"),
    ("FINAL", "Final", "Full quality. Slow"),
    ("CUSTOM", "Custom", "Choose each compile step yourself (see Compile settings)"),
]


def _sound_display(self, context):
    from .sound import on_display_change
    on_display_change(self, context)


def _vis_display(self, context):
    from .visview import on_view_change
    on_view_change(self, context)


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
    """The new class's keyvalues: its defaults, keeping values the old entity already had for the
    same keys (and always its name, which outputs and logic refer to)."""
    old = {kv.key: kv.value for kv in self.keyvalues if kv.key}
    new = dict(default_keyvalues(self.classname))
    for k in new:
        if k in old and old[k] != "":
            new[k] = old[k]
    for k in ("targetname", "parentname"):
        if old.get(k):
            new[k] = old[k]
    self.keyvalues.clear()
    for k, v in new.items():
        kv = self.keyvalues.add()
        kv.key, kv.value = k, v


_PHYSICS_CLASSES: list = []


def _physics_classes():
    """The game's physics prop classes (scripts/propdata.txt), read once; a short list without the game."""
    if not _PHYSICS_CLASSES:
        names = []
        try:
            import re
            from .ops import game_root, game_content
            content = game_content(game_root(bpy.context))
            text = content.read("scripts/propdata.txt").decode("latin-1") if content else ""
            names = [n for n in re.findall(r'^\s*"([A-Za-z]+\.[A-Za-z_]+)"\s*$', text, re.M)
                     if not n.endswith(".Base") and n != "PropData.txt"]
        except Exception:
            names = []
        names = names or ["Wooden.Small", "Wooden.Medium", "Wooden.Large", "Metal.Small", "Metal.Medium",
                          "Metal.Large", "Plastic.Small", "Plastic.Medium", "Cardboard.Medium", "Stone.Medium"]
        default = "Wooden.Medium" if "Wooden.Medium" in names else names[0]
        names.sort(key=lambda n: (n != default, n))
        _PHYSICS_CLASSES.extend((n, n, "") for n in names)
    return _PHYSICS_CLASSES


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
    brush_detail: EnumProperty(name="Detail", items=DETAIL_CHOICES, default="AUTO",
                               description="Whether this brush is func_detail")
    model_kind: EnumProperty(name="Prop", default="STATIC", items=[
        ("STATIC", "Static", "Part of the map: solid, lit like the world, can't move (prop_static)"),
        ("DYNAMIC", "Dynamic", "Logic can move, hide or animate it; doesn't fall (prop_dynamic)"),
        ("PHYSICS", "Physics", "Falls, can be pushed and shot around (prop_physics)")])
    model_collision: EnumProperty(name="Collision", default="HULLS", items=[
        ("HULLS", "Convex Pieces", "Solid: each loose part of the mesh wrapped in its convex hull"),
        ("NONE", "None", "Players and zombies walk through it (not for Physics props)")])
    physics_class: EnumProperty(name="Physics Class", items=lambda self, context: _physics_classes(),
                                description="How it behaves when hit: the game's own classes (scripts/propdata.txt): "
                                            "weight feel, health, breaking. A broken prop with no gibs just disappears")
    model_mass: FloatProperty(name="Mass", default=0.0, min=0.0, soft_max=1000.0, unit="MASS",
                              description="Physics props: kg (0 = worked out from its size)")
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
    brush_detail: EnumProperty(
        name="Detail", default="NONE",
        items=[("NONE", "Inherit", "Use the parent collection's choice (or the map's Auto Detail setting)")]
        + DETAIL_CHOICES[1:],
        description="Whether the brushes inside are func_detail (objects set to Detail or World override it)")


# Surface list for the material panel. Starts with the built-in list and is replaced
# by the game's full list (104 surfaces) once game files have been read.
SURFACE_ITEMS: list[tuple[str, str, str]] = []


def set_surface_list(surfaces) -> None:
    from ..core.surfaces import friction_label
    SURFACE_ITEMS.clear()
    SURFACE_ITEMS.append(("DEFAULT", "Default", "Game materials keep their own surface; custom ones use concrete"))
    for s in surfaces:
        SURFACE_ITEMS.append((s.name, s.name, friction_label(s)))


def clean_map_name(name: str) -> str:
    """A map name the game, its console and file paths all accept: lowercase a-z, 0-9 and _."""
    import re
    return re.sub(r"[^a-z0-9_]", "", re.sub(r"[\s\-.]+", "_", name.strip().lower())).strip("_")


def _on_map_name(self):
    clean = clean_map_name(self.map_name)
    if clean and clean != self.map_name:
        self.map_name = clean           # (runs this again once, with nothing left to change)


def _surface_items(self, context):
    # Blender stores the choice as a position in this list, so it must always be the game's list
    # when the game is there (the short fallback list would make saved choices read as nothing)
    if not SURFACE_ITEMS:
        try:
            from .ops import game_content, game_root
            game_content(game_root(context))           # fills SURFACE_ITEMS from the game
        except Exception:
            pass
    if not SURFACE_ITEMS:
        from ..core.surfaces import FALLBACK
        set_surface_list(FALLBACK)
    return SURFACE_ITEMS


_MONITOR_ITEMS: list[tuple[str, str, str]] = []


def monitor_key(m) -> str:
    return f"{m.x},{m.y},{m.width}x{m.height}"


def _monitor_items(self, context):
    # Blender stores the choice as each item's number: a number made from the screen's place and
    # size keeps meaning the same screen when others are plugged in or out
    import zlib
    from ..core.window import monitors
    items = [("-1", "Game decides", "Let the game place its window", 0)]
    for m in monitors():
        key = monitor_key(m)
        items.append((key, m.label, f"Open the game on monitor {m.label}", zlib.crc32(key.encode()) & 0x3FFFFFFF or 1))
    if items != _MONITOR_ITEMS:
        _MONITOR_ITEMS[:] = items        # (kept in a module list: Blender only borrows the strings)
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


def _light_display(self, context):
    from .lightview import on_display_change
    on_display_change(self, context)


def _nav_display(self, context):
    from .navview import _on_display_change
    _on_display_change(self, context)


_QUIET = [False]        # set while Hammerless itself resets the problem selection


def set_problem_index(s, value: int) -> None:
    """Select a problem row without jumping to it (Blender 5 no longer lets s["problem_index"] skip
    the update)."""
    _QUIET[0] = True
    try:
        s.problem_index = value
    finally:
        _QUIET[0] = False


def _on_problem_index(self, context):
    if _QUIET[0]:
        return
    from .problems import go_to_problem
    go_to_problem(context, self.problem_index)


def _on_game_root(self, context):
    if self.game_root.startswith("//"):
        self.game_root = bpy.path.abspath(self.game_root)     # (a relative path breaks on Save As)


class HL_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__.rsplit(".", 1)[0]

    game_root: StringProperty(name="L4D2 Folder", subtype="DIR_PATH", update=_on_game_root,
                              description="Left 4 Dead 2's install folder (the one with left4dead2.exe), for every "
                                          "file. Empty: found automatically. A scene's own L4D2 Folder overrides it")

    def draw(self, context):
        self.layout.prop(self, "game_root")


def preferences():
    try:
        return bpy.context.preferences.addons[HL_Preferences.bl_idname].preferences
    except (KeyError, AttributeError):
        return None


class HL_Problem(bpy.types.PropertyGroup):
    """One map check result (name = message)."""
    severity: StringProperty()              # ERROR / WARNING / INFO
    source: StringProperty()                # object to select
    location: FloatVectorProperty(size=3, subtype="TRANSLATION")   # Blender units
    has_location: BoolProperty()
    ingame: BoolProperty()                  # reported by the game after Build & Play
    kind: StringProperty()                  # "flow" (the game's path report), "nav" (nav mesh checks),
                                            # "navgen" (nav build warnings): each replaces only its own rows


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
        ("SPAWN", "Zombie spawn marks", "Red: no zombies spawn (EMPTY + NO_MOBS). Pink: no wanderers. "
                 "Violet: no hordes. Orange: Zombie Spawn Area (OBSCURED). Light blue: normal"),
        ("WHY", "Where zombies can spawn", "Why an area gets zombies or not, for survivors stepping out of the "
                "start room (or at the 3D cursor): green can spawn, red no-spawn mark, orange too close (walking "
                "distance), yellow in view"),
        ("VIS", "What can be seen from the 3D cursor", "From the area under the 3D cursor (Shift + right-click to "
                "place it): green completely visible, yellow partly, grey not visible (needs an analyzed nav)"),
    ])
    show_hiding_spots: BoolProperty(name="Hiding Spots", default=True, update=lambda self, c: _nav_display(self, c),
                                    description="Hiding spots from the nav analysis: blue in cover, orange exposed")
    show_nav_links: BoolProperty(name="Drops and Jumps", default=True, update=lambda self, c: _nav_display(self, c),
                                 description="Arrows for one-way drop-downs (orange) and jump-ups (cyan)")
    spawn_from: EnumProperty(name="Survivors At", default="START", update=lambda self, c: _nav_display(self, c), items=[
        ("START", "Leaving the start room", "Where survivors stand when they step out of the start room: the "
                  "Director places its first zombies then"),
        ("CURSOR", "3D cursor", "Survivors standing in the area under the 3D cursor"),
    ])
    nav_xray: BoolProperty(name="X-Ray", default=False, update=lambda self, c: _nav_display(self, c),
                           description="Draw the nav mesh through walls")
    sound_mode: EnumProperty(name="Sound", default="REVERB", items=[
        ("OFF", "Off", "No soundscapes: the map plays without the engine's room reverb"),
        ("REVERB", "Automatic Reverb", "Soundscapes everywhere with the engine's own automatic reverb (it traces the "
                                       "room around you while playing, as Valve's maps do)"),
        ("URBAN", "Reverb + City Ambience", "Also city ambience outdoors, and indoors a room tone with the outside "
                                            "coming in through the doorways and windows (sound portals)")])
    show_sound: BoolProperty(name="Show Sound", default=True, update=lambda self, c: _sound_display(self, c),
                             description="Show the last sound trace in the viewport")
    vis_view: EnumProperty(name="View", default="OFF", update=lambda self, c: _vis_display(self, c), items=[
        ("OFF", "Off", "Show nothing"),
        ("PORTALS", "Portals", "The openings vis works through, from the last build: how the map was split up. "
                               "Tiny slivers are red"),
        ("LOAD", "Rendering Load", "The map coloured by how much the game draws from each spot (from the last "
                                   "build's visibility): red spots are where frame rate suffers first"),
        ("COST", "Vis Cost", "Where the last vis compile spent its time, and the objects behind it (needs Vis "
                             "Compiler: Hammerless)")])
    vis_xray: BoolProperty(name="See Through Walls", default=True, update=lambda self, c: _vis_display(self, c),
                           description="Draw over the scene instead of hidden behind its walls")
    show_lightmap: BoolProperty(name="Show Baked Lighting", default=True, update=lambda self, c: _light_display(self, c),
                                description="Draw the last compile's baked lighting in the viewport")
    lightmap_mode: EnumProperty(name="Mode", default="LIGHT", update=lambda self, c: _light_display(self, c), items=[
        ("LIGHT", "Lighting Only", "The baked light on its own: easiest for spotting leaks, harsh shadows and "
                  "dark corners"),
        ("LIT", "Lit", "The baked light multiplied over what the viewport shows (best in Solid view with Flat "
                "lighting and Texture colour): shadows darken your textures, coloured light tints them"),
    ])
    lightmap_exposure: FloatProperty(name="Exposure", default=0.0, soft_min=-4.0, soft_max=4.0, step=10,
                                     update=lambda self, c: _light_display(self, c),
                                     description="Brighten or darken the view, in stops (+1 = twice as bright)")
    lightmap_xray: BoolProperty(name="X-Ray", default=False, update=lambda self, c: _light_display(self, c),
                                description="Draw the baked lighting through walls")
    show_problem_markers: BoolProperty(name="Show Markers", default=True,
                                       description="Draw numbered markers in the viewport where problems are")
    spawn_index: IntProperty(default=-1, update=_on_spawn_index)
    spawn_favorites: StringProperty(description="Starred Add panel items (comma separated)")
    # --- build
    map_name: StringProperty(name="Map Name", default="my_map", update=lambda self, c: _on_map_name(self),
                             description="File name of the map: lowercase letters, digits and _ (spaces "
                                         "become _)")
    compile_preset: EnumProperty(name="Quality", items=COMPILE_PRESETS, default="NORMAL")
    vis_tool: EnumProperty(name="Vis Compiler", default="VALVE", items=[
        ("VALVE", "Valve vvis", "L4D2's own visibility compiler (vvis.exe)"),
        ("HAMMERLESS", "Hammerless (faster)",
         "Hammerless's visibility compiler: the same results as vvis, about 3 times faster. If it ever fails, "
         "Valve's vvis runs instead")])
    model_compiler: EnumProperty(name="Model Compiler", default="HAMMERLESS", items=[
        ("HAMMERLESS", "Hammerless", "Hammerless writes Custom Models' game files itself (no extra tools needed)"),
        ("STUDIOMDL", "Valve studiomdl", "L4D2's own model compiler (studiomdl.exe, from the Authoring Tools)")])
    light_tool: EnumProperty(name="Light Compiler", default="VALVE", items=[
        ("VALVE", "Valve vrad", "L4D2's own lighting compiler (vrad.exe)"),
        ("CYCLES", "Cycles",
         "vrad lays out the lighting, then Blender's Cycles bakes the lightmaps with the same lights (its GPU "
         "if it has one). Prop lighting and switchable lights stay vrad's")])
    cycles_samples: IntProperty(name="Samples", default=1024, min=1, max=65536, soft_max=4096,
                                description="Cycles samples per lightmap sample: more is smoother and slower")
    cycles_stitch: BoolProperty(name="Stitch Seams", default=True,
                                description="Make neighbouring faces' lighting meet exactly along the edges they "
                                            "share (each face has its own lightmap, so they otherwise show seams)")
    cycles_denoise: BoolProperty(name="Denoise", default=False,
                                 description="Run Blender's denoiser (OpenImageDenoise, on the GPU when there is "
                                             "one) on the Cycles bake")
    vis_mode: EnumProperty(name="Visibility (vvis)", default="FULL", items=[
        ("SKIP", "Skip", "No visibility pass: everything always renders (slow in-game on big maps)"),
        ("FAST", "Fast", "Quick visibility pass"),
        ("FULL", "Full", "Full visibility pass (best in-game performance)")])
    rad_mode: EnumProperty(name="Lighting (vrad)", default="NORMAL", items=[
        ("SKIP", "Skip (fullbright)", "No lighting pass"),
        ("FAST", "Fast", "Quick lighting; terrain may show seams"),
        ("NORMAL", "Normal", "Normal lighting"),
        ("FINAL", "Final", "Highest quality lighting; slow")])
    hdr_mode: EnumProperty(name="HDR", default="HDR", items=[
        ("HDR", "HDR only", "What L4D2 uses (Valve's own maps only have HDR lighting)"),
        ("BOTH", "LDR + HDR", "Also bakes an LDR copy, which L4D2 doesn't use: twice the lighting time"),
        ("LDR", "LDR only", "Not used by L4D2: the game shows flat lighting")])
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
    sun_color: FloatVectorProperty(name="Sun Color", subtype="COLOR_GAMMA", size=3, min=0, max=1,
                                   default=(1.0, 0.96, 0.88))
    sun_brightness: IntProperty(name="Sun Brightness", default=400, min=0, max=5000)
    sun_pitch: FloatProperty(name="Sun Height", default=-50.0, min=-90.0, max=0.0,
                             description="-90 = straight down (noon), near 0 = low evening sun")
    sun_yaw: FloatProperty(name="Sun Direction", default=30.0, min=0.0, max=360.0,
                           description="Compass direction the sunlight travels towards")
    ambient_color: FloatVectorProperty(name="Sky Light Color", subtype="COLOR_GAMMA", size=3, min=0, max=1,
                                       default=(0.55, 0.63, 0.75))
    ambient_brightness: IntProperty(name="Sky Light Brightness", default=80, min=0, max=2000,
                                    description="Light from the sky dome that fills shadows")
    lightmap_scale: IntProperty(name="Lightmap Scale", default=16, min=1, max=128,
                                description="Shadow detail on brush faces: 8 = sharp, 16 = Valve default, "
                                            "32+ = soft but faster to compile")

    # --- fog
    fog_enabled: BoolProperty(name="Fog", default=False)
    fog_color: FloatVectorProperty(name="Color", subtype="COLOR_GAMMA", size=3, min=0, max=1,
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
    dir_spawn_tank: BoolProperty(name="Tanks", default=True, description="The Director spawns Tanks itself (off: only your logic does)")
    dir_spawn_witch: BoolProperty(name="Witches", default=True, description="The Director spawns Witches itself")
    dir_spawn_smoker: BoolProperty(name="Smokers", default=True, description="The Director spawns Smokers itself")
    dir_spawn_boomer: BoolProperty(name="Boomers", default=True, description="The Director spawns Boomers itself")
    dir_spawn_hunter: BoolProperty(name="Hunters", default=True, description="The Director spawns Hunters itself")
    dir_spawn_charger: BoolProperty(name="Chargers", default=True, description="The Director spawns Chargers itself")
    dir_spawn_jockey: BoolProperty(name="Jockeys", default=True, description="The Director spawns Jockeys itself")
    dir_spawn_spitter: BoolProperty(name="Spitters", default=True, description="The Director spawns Spitters itself")

    # --- game
    nav_source: EnumProperty(
        name="Nav Mesh", default="BLENDER",
        description="Who builds the nav mesh (how bots and zombies find their way)",
        items=[("BLENDER", "Made in Blender", "Hammerless builds it while the map compiles (a copy of the game's "
                                             "generator), then the game only adds its visibility data: one reload"),
               ("GAME", "Made by the game", "The game generates it after loading: slower (two extra reloads), "
                                           "kept as a fallback")])
    nav_analysis: EnumProperty(
        name="Nav Analysis", default="BLENDER",
        description="Who works out what each nav area can see and where the hiding spots are",
        items=[("BLENDER", "In Blender", "Hammerless analyzes the nav mesh after the compile; the game loads the map "
                                         "once (no analysis reload)"),
               ("GAME", "By the game", "The game analyzes it after loading, then reloads the map")])
    wall_climbs: BoolProperty(
        name="Zombies Climb Walls", default=False,
        description="Common infected can climb any wall up to 160 units (about 3 m) high, as if it had a "
                    "ladder, and get back down. Only with Nav Mesh: Made in Blender")
    generate_nav: BoolProperty(
        name="Rebuild Nav Mesh", default=False,
        description="Force a fresh nav mesh (how bots and zombies find their way) on the next Build & Play. "
                    "Normally not needed: the nav is rebuilt automatically when walls, floors, terrain or "
                    "safe rooms change")
    window_monitor: EnumProperty(name="Monitor", items=_monitor_items,
                                 description="Which monitor the game window opens on")
    window_width: IntProperty(name="Width", default=1600, min=640, max=7680)
    window_height: IntProperty(name="Height", default=900, min=480, max=4320)
    window_borderless: BoolProperty(name="Borderless", default=False)
    launch_extra: StringProperty(name="Launch Options", description="Extra game options, e.g. -high")
    difficulty: EnumProperty(name="Difficulty", default="Normal", items=[
        ("KEEP", "Keep Current", "Don't change the game's difficulty"),
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
    game_root: StringProperty(name="L4D2 Folder", subtype="DIR_PATH", update=_on_game_root,
                              description="Left 4 Dead 2 install folder for this file (empty: the one in the add-on's "
                                          "Preferences, or found automatically)")
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
               ("SMART", "Round and Small", "Cylinders, arches and pieces under 256 units (recommended)"),
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
    bpy.utils.register_class(HL_Preferences)
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
    bpy.utils.unregister_class(HL_Preferences)
