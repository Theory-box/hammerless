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
    ("AUTO", "Auto", "Follow the map's Auto Detail setting (Settings > Map Defaults)"),
    ("DETAIL", "Detail", "Always func_detail: doesn't slow down vvis, but doesn't block visibility or "
                         "seal the map. For furniture, trim, overlapping boxes"),
    ("WORLD", "World", "Never func_detail: blocks visibility, so the game skips drawing what's behind it. "
                       "For walls between areas"),
    ("VIS", "Vis Only", "Blocks visibility like a world brush, but nothing collides with it and it doesn't count "
                        "toward the map's 8,192 brushes (our map compiler). For the walls of a prop that collides "
                        "by itself, e.g. Make Collision's props"),
]

COMPILE_PRESETS = [   # (stored by position: new ones go last)
    ("QUICK", "Quick", "Geometry only (Visibility and Lighting off). Fastest; the map is fullbright"),
    ("FAST", "Fast", "Fast Visibility and quick Lighting (rougher shadow edges, less bounced light)"),
    ("NORMAL", "Normal", "Full Visibility, Normal Lighting. Small maps still compile in seconds"),
    ("FINAL", "Final", "Full Visibility, Final Lighting (16 times the sky rays)"),
    ("CUSTOM", "Mixed", "Visibility and Lighting are set to different levels in their own panels"),
    ("ULTRA", "Ultra", "Full Visibility, Ultra Lighting: beyond vrad (finer supersampling, vrad's oddities fixed). "
                       "Needs the Hammerless light compiler"),
]

LIGHT_QUALITY = [
    ("OFF", "Off", "No lighting: the map is fullbright"),
    ("FAST", "Fast", "Quick lighting for testing: no supersampling (rougher shadow edges), 2 bounces, no prop "
                     "lighting"),
    ("NORMAL", "Normal", "vrad's normal lighting"),
    ("FINAL", "Final", "16 times the sky rays: smoother sky light and shadows"),
    ("ULTRA", "Ultra", "Final, plus finer supersampling and vrad's oddities fixed (Hammerless light compiler)"),
    ("CUSTOM", "Custom", "Your own settings below"),
]


_SKY_CHOICES: list = []      # (kept: Blender holds on to the strings of a list from an items function)


def _sky_choice_items(self, context):
    """Every skybox in the game, then Custom (typed by name)."""
    from .ops import _sky_items
    names = [it[0] for it in _sky_items(self, context)]
    if len(_SKY_CHOICES) != len(names) + 1 or [it[0] for it in _SKY_CHOICES[1:]] != names:
        _SKY_CHOICES.clear()
        _SKY_CHOICES.append(("CUSTOM", "Custom", "Type a sky name (a skybox from another game or your own)", "", 0))
        _SKY_CHOICES.extend((n, n, "Skybox " + n, "", i + 1) for i, n in enumerate(names))
    return _SKY_CHOICES


def _sky_choice_get(self):
    if self.sky_custom:
        return 0
    name = self.skyname.strip().lower()
    for it in _sky_choice_items(self, bpy.context)[1:]:
        if it[0].lower() == name:
            return it[4]
    return 0


def _sky_choice_set(self, value):
    if value == 0:
        self.sky_custom = True
        return
    for it in _sky_choice_items(self, bpy.context)[1:]:
        if it[4] == value:
            self.skyname = it[0]
            self.sky_custom = False
            return


_BAKE_AREAS: list = []        # (kept: Blender holds on to the strings of a list from an items function)


def _bake_area_items(self, context):
    scene = self.id_data if isinstance(self.id_data, bpy.types.Scene) else (context or bpy.context).scene
    items = [("MAP", "Whole Map", "Bake the whole map's lighting", "WORLD", 0),
             ("VIEW", "What the View Sees", "Only what the 3D viewport sees, out to View Distance; the rest keeps "
                                            "the last bake's lighting", "HIDE_OFF", 1)]
    items.append(("SELECTED", "Selected Objects", "Bake only around the selected objects (their bounds); the rest "
                                                  "keeps the last bake's lighting", "RESTRICT_SELECT_OFF", 2))
    if [it[0] for it in items] != [it[0] for it in _BAKE_AREAS]:
        _BAKE_AREAS.clear()
        _BAKE_AREAS.extend(items)
    return _BAKE_AREAS


def _bake_area_get(self):
    for it in _bake_area_items(self, None):
        if it[0] == self.bake_area_name:
            return it[4]
    return 0                          # (a volume that's gone: the whole map)


def _bake_area_set(self, value):
    for it in _bake_area_items(self, None):
        if it[4] == value:
            self.bake_area_name = it[0]
            return


def _sky_view(self, context):
    from .skyview import redraw
    redraw()


def _sky_pick_get(self):
    from . import quality
    return quality.get_sky_rays_pick(self)


def _sky_pick_set(self, value):
    from . import quality
    quality.set_sky_rays_pick(self, value)


def _q(name):
    """(an update calling quality.<name>)"""
    def update(self, context):
        from . import quality
        getattr(quality, name)(self, context)
    return update


def _sound_display(self, context):
    from .sound import on_display_change
    on_display_change(self, context)


def _vis_display(self, context):
    from .visview import on_view_change
    on_view_change(self, context)


def _kv_changed(self, context):
    from .presets import on_kv_value
    on_kv_value(self, context)
    if color_key_kind(self.key) == "LIGHT":         # (a light entity's colour or brightness: Live Light Mixing follows)
        try:
            from .lightview import remix
            remix(context)
        except Exception:
            pass


def _param_changed(self, context):
    from .presets import on_output_param
    on_output_param(self, context)


def _target_changed(self, context):
    from .presets import on_output_target
    on_output_target(self, context)


# keyvalues that are a colour (0-255 each, as the game writes them) and brightness, or a colour alone: drawn as a
# colour swatch (Blender's colour wheel) and a brightness field instead of the text
LIGHT_COLOR_KEYS = {"_light", "_ambient", "_lighthdr", "_ambienthdr"}
PLAIN_COLOR_KEYS = {"rendercolor", "_fogcolor", "fogcolor", "fogcolor2", "color", "_color", "skycolor"}


def color_key_kind(key: str) -> str:
    """'LIGHT' (colour + brightness), 'COLOR' (colour alone) or '' for a keyvalue's key."""
    k = key.strip().lower()
    return "LIGHT" if k in LIGHT_COLOR_KEYS else "COLOR" if k in PLAIN_COLOR_KEYS else ""


def _kv_numbers(self) -> list[float]:
    try:
        return [float(x) for x in self.value.split()]
    except ValueError:
        return []


def _kv_color_get(self):
    v = _kv_numbers(self)
    if len(v) < 3:
        return (1.0, 1.0, 1.0)
    return tuple(max(0.0, min(1.0, c / 255.0)) for c in v[:3])


def _kv_color_set(self, rgb):
    v = _kv_numbers(self)
    rest = v[3:] if len(v) >= 4 else ([200.0] if color_key_kind(self.key) == "LIGHT" else [])
    self.value = " ".join([str(int(round(max(0.0, min(1.0, c)) * 255))) for c in rgb] + [_num_text(x) for x in rest])


def _kv_brightness_get(self):
    v = _kv_numbers(self)
    return v[3] if len(v) >= 4 else 0.0


def _kv_brightness_set(self, b):
    v = _kv_numbers(self)
    rgb = v[:3] if len(v) >= 3 else [255.0, 255.0, 255.0]
    self.value = " ".join([_num_text(c) for c in rgb] + [_num_text(max(0.0, b))] + [_num_text(x) for x in v[4:]])


def _num_text(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


class HL_KeyValue(bpy.types.PropertyGroup):
    key: StringProperty(name="Key")
    value: StringProperty(name="Value", update=_kv_changed)
    color: FloatVectorProperty(name="Color", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0,
                               get=_kv_color_get, set=_kv_color_set,
                               description="The colour (click for the colour wheel); the game stores it as 0-255 each")
    brightness: FloatProperty(name="Brightness", min=0.0, soft_max=2000.0, step=100, precision=0,
                              get=_kv_brightness_get, set=_kv_brightness_set,
                              description="How bright the light is (the fourth number of the key)")


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


def _physics_class_get(self):
    """The class by name (physics_class_name): a list position would read back as another class when the list
    changes (the game found or not). Older files kept only the position: read as it was."""
    items = _physics_classes()
    name = self.physics_class_name
    if name:
        for i, it in enumerate(items):
            if it[0] == name:
                return i
        return 0
    old = self.get("physics_class")
    return old if isinstance(old, int) and 0 <= old < len(items) else 0


def _physics_class_set(self, value):
    items = _physics_classes()
    if 0 <= value < len(items):
        self.physics_class_name = items[value][0]


# the game's built-in light styles (the light's "style" key)
LIGHT_PATTERNS = [
    ("0", "Steady", "Always on at full brightness"),
    ("1", "Flicker", "An irregular flicker"),
    ("6", "Flicker 2", "Another irregular flicker"),
    ("10", "Fluorescent Flicker", "A failing fluorescent tube: mostly on, dropping out"),
    ("3", "Candle", "A candle's waver"),
    ("7", "Candle 2", "Another candle's waver"),
    ("8", "Candle 3", "A third candle's waver"),
    ("5", "Gentle Pulse", "Slowly brighter and dimmer"),
    ("2", "Slow Strong Pulse", "Slowly from dark to bright and back"),
    ("11", "Slow Pulse (never dark)", "Slowly brighter and dimmer, never going dark"),
    ("4", "Fast Strobe", "Quickly on and off"),
    ("9", "Slow Strobe", "Slowly on and off"),
    ("12", "Underwater", "An underwater shimmer"),
    ("CUSTOM", "Custom", "Your own steps (makes the light switchable)"),
]


def _light_pattern_changed(self, _context):
    obj = self.id_data
    if isinstance(obj, bpy.types.Object) and obj.type == "LIGHT":
        from .lightpresets import apply_pattern
        apply_pattern(obj)


def _is_behaviour(_self, tree) -> bool:
    return tree.bl_idname == "HL_LogicTree" and getattr(tree, "is_behaviour", False)


class HL_BehaviourSlot(bpy.types.PropertyGroup):
    """One Behaviour an object carries: a logic graph it runs as Self (several objects can share one, as materials)"""
    graph: PointerProperty(type=bpy.types.NodeTree, name="Behaviour", poll=_is_behaviour,
                           description="The logic graph this object runs (Self in it is this object). Objects "
                                       "sharing one all run it; Make Unique gives this object its own copy")


class HL_ObjectSettings(bpy.types.PropertyGroup):
    role: EnumProperty(name="Role", items=OBJECT_ROLES, default="AUTO")
    classname: StringProperty(name="Class", description="Entity classname",
                              update=_on_classname_change)
    keyvalues: CollectionProperty(type=HL_KeyValue)
    keyvalues_index: IntProperty()
    behaviours: CollectionProperty(type=HL_BehaviourSlot)
    behaviours_index: IntProperty()
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
    collision_min_area: FloatProperty(
        name="Smallest Face", default=0.05, min=0.0, soft_max=1.0, precision=3,
        description="Convert to Brushes, Slabs: flat areas smaller than this (square metres) get no brush (trim, "
                    "small details)")
    collision_mode: EnumProperty(name="Collision As", default="BRUSHES", items=[
        ("BRUSHES", "Brushes", "Invisible brushes: the engine's cheapest collision, but they count toward the map's "
                               "8,192 brushes"),
        ("PROPS", "Props", "Invisible static props of up to 20 pieces each (as fast as brushes, measured): no brushes "
                           "used. Their material is tools/toolsnodraw; set it to tools/toolsinvisible so bullet holes "
                           "stick to them")])
    brush_algorithm: EnumProperty(name="Algorithm", default="AUTO", items=[
        ("AUTO", "Auto", "Hard Surface when the mesh is clean enough for it (props built from flat faces), else "
                         "General. The message says which one ran"),
        ("EOF", "Hard Surface", "Exact (Extend-or-Fill): few clean convex pieces the way a mapper would build them, "
                                "faces on the mesh's own "
                                  "planes, no overlaps, exact (curves too: one piece per curve segment) except "
                                  "shallow detail up to Detail deep, which is covered over. The mesh must be a closed "
                                  "solid. Experimental: noisy meshes can give extra pieces, and glass isn't kept out of "
                                  "Blocks Vis yet"),
        ("ORGANIC", "General", "Any closed shape, curved, sculpted or scanned too (rocks, creatures, stumps): few "
                               "convex pieces cut from the solid, each within Fit of the mesh (no point of its collision farther "
                               "from the surface than Fit, a share of the object's size), at most Max Pieces. The mesh must be a closed solid. "
                               "Experimental; glass isn't kept out of Blocks Vis yet"),
        ("SLABS", "Slabs", "One thin slab behind each flat area (the earlier Make Collision): for big shells like a "
                           "house, any mesh, closed or not")])
    brush_detail_depth: FloatProperty(
        name="Detail", default=0.1524, min=0.0, soft_max=0.5, subtype="DISTANCE", unit="LENGTH",
        description="Hard Surface: pockets at most this deep (grooves, hand-holds, notches) are covered over "
                    "instead of cut around (default 8 game units). 0: everything exact")
    brush_fit: FloatProperty(
        name="Fit", default=2.5, min=0.1, soft_max=10.0, max=50.0, subtype="PERCENTAGE", precision=2,
        description="General: how far the collision may stand off the mesh, as a share of the object's size (its "
                    "diagonal), so a pebble and a statue come out alike. Smaller: tighter, more pieces")
    brush_max_pieces: IntProperty(
        name="Max Pieces", default=32, min=2, max=256,
        description="General: at most this many pieces. Fewer pieces fit more loosely")
    brush_time_limit: IntProperty(
        name="Time Limit (s)", default=60, min=5, soft_max=600, max=3600,
        description="General: seconds of cutting at most. Pieces still uncut then are kept as their hull (looser "
                    "than Fit, can be boxy): raise it for big, dense meshes")
    collision_blocks_vis: BoolProperty(
        name="Blocks Vis", default=False,
        description="Convert to Brushes: the pieces also block visibility like world brushes, so the game skips "
                    "drawing what's behind the walls. Brushes: they're world brushes. Props: Vis Only brushes as "
                    "well, which use none of the map's brushes")
    physics_class_name: StringProperty(options={"HIDDEN"})
    physics_class: EnumProperty(name="Physics Class", items=lambda self, context: _physics_classes(),
                                get=_physics_class_get, set=_physics_class_set,
                                description="How it behaves when hit: the game's own classes (scripts/propdata.txt): "
                                            "weight feel, health, breaking. A broken prop with no gibs just disappears")
    model_mass: FloatProperty(name="Mass", default=0.0, min=0.0, soft_max=1000.0, unit="MASS",
                              description="Physics props: kg (0 = worked out from its size)")
    use_convex_hull: BoolProperty(
        name="Use Convex Hull",
        description="Wrap each loose part in its convex hull instead of requiring it to be convex")
    light_pattern: EnumProperty(name="Pattern", default="0", items=LIGHT_PATTERNS, update=_light_pattern_changed,
                                description="How the light's brightness changes over time in the game (its baked "
                                            "light flickers, pulses or strobes): a preset Behaviour on the light "
                                            "(Behaviours: open it to change how it does it)")
    light_custom: StringProperty(name="Custom", default="mmamammmmammamamaaamammma",
                                 description="Brightness steps, ten a second: a = dark, m = normal, z = double")
    light_switchable: BoolProperty(name="Switchable", default=False,
                                   description="Logic can turn it on and off (TurnOn, TurnOff, Toggle, SetPattern). "
                                               "Its light is baked into a layer of its own: at most 3 switchable or "
                                               "patterned lights may reach the same surface, 32 switchable in a map")
    light_start_off: BoolProperty(name="Starts Off", default=False,
                                  description="Dark when the map starts, until logic turns it on")
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


MAP_NAME_MAX = 31        # (measured: with a 32-character map name the game can't join its own server)


def clean_map_name(name: str) -> str:
    """A map name the game, its console and file paths all accept: lowercase a-z, 0-9 and _, at most 31 characters."""
    import re
    clean = re.sub(r"[^a-z0-9_]", "", re.sub(r"[\s\-.]+", "_", name.strip().lower())).strip("_")
    return clean[:MAP_NAME_MAX].rstrip("_")


def _on_map_name(self):
    clean = clean_map_name(self.map_name)
    if clean and clean != self.map_name:
        self.map_name = clean           # (runs this again once, with nothing left to change)
        return
    coll = self.map_collection          # (the map collection is named after the map)
    if coll is not None and clean and coll.name != clean:
        coll.name = clean


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
        ("AHEAD", "Zombies can appear ahead?", "Standing here, how many spots ahead along the route (550-1500 "
                  "units away) are hidden from the survivors, so zombies can appear there. Red: under 10 (a dead zone: "
                  "add cover). Yellow: under 30. Green: plenty. Needs the nav analyzed"),
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
    show_zombie_spawns: BoolProperty(
        name="Zombie Spawns", default=False, update=lambda self, c: _nav_display(self, c),
        description="Where the AI Director would put its wandering commons, simulated from a seed (the game picks "
                    "nav areas at random by count). Red: they appear with the survivors there. Grey: they wait "
                    "until survivors come near. Purple: lost on an area that can't hold them (roofs, safe rooms)")
    zombie_seed: IntProperty(name="Seed", default=1, min=1, update=lambda self, c: _nav_display(self, c),
                             description="A different seed is a different roll of the Director's dice")
    zombie_progress: FloatProperty(
        name="Survivors At", default=0.0, min=0.0, max=1.0, subtype="FACTOR",
        update=lambda self, c: _nav_display(self, c),
        description="How far along the route (start room to end room) the survivors are: 0 = leaving the start")
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
        ("GAME", "Textured", "The compiled map as the game draws it: each surface's game texture times its baked "
                 "light, props too (their model textures and baked vertex light)"),
    ])
    lightmap_exposure: FloatProperty(name="Exposure", default=0.0, soft_min=-4.0, soft_max=4.0, step=10,
                                     update=lambda self, c: _light_display(self, c),
                                     description="Brighten or darken the view, in stops (+1 = twice as bright)")
    lightmap_xray: BoolProperty(name="X-Ray", default=False, update=lambda self, c: _light_display(self, c),
                                description="Draw the baked lighting through walls")
    bake_area: EnumProperty(name="Bake", items=_bake_area_items, get=_bake_area_get, set=_bake_area_set,
                            description="What Bake Lighting bakes: the whole map, what the 3D viewport sees, the "
                                        "selected objects, or a Control Volume set to Only inside with Light Baking. The rest "
                                        "keeps the last bake's lighting. Builds bake inside every Only inside volume (the whole map if there's none)")
    bake_area_name: StringProperty(default="MAP", options={"HIDDEN"})
    light_view_distance: FloatProperty(name="Distance", default=3000.0, min=64.0, soft_max=20000.0, step=1000,
                                       precision=0, description="Bake What the View Sees: how far from the viewport to "
                                       "bake, in Hammer units (beyond it and outside the view keep the last bake's "
                                       "lighting)")
    lightmap_props: BoolProperty(name="Props", default=True, update=lambda self, c: _light_display(self, c),
                                 description="Draw static props with the light baked at their vertexes (Prop "
                                             "Lighting): the compiled map's props, from the game's model files")
    show_problem_markers: BoolProperty(name="Show Markers", default=True,
                                       description="Draw numbered markers in the viewport where problems are")
    spawn_index: IntProperty(default=-1, update=_on_spawn_index)
    spawn_favorites: StringProperty(description="Starred Add panel items (comma separated)")
    # --- build
    map_collection: PointerProperty(name="Map Collection", type=bpy.types.Collection,
                                    description="The collection the map is built from (Organize Scene makes it): "
                                                "only what's in it is exported, compiled and baked")
    map_name: StringProperty(name="Map Name", default="my_map", update=lambda self, c: _on_map_name(self),
                             description="File name of the map: lowercase letters, digits and _ (spaces "
                                         "become _)")
    compile_preset: EnumProperty(name="Quality", items=COMPILE_PRESETS, default="NORMAL",
                                 update=_q("on_build_quality"),
                                 description="Sets Lighting's and Visibility's quality together (each can still be "
                                             "changed in its own panel: then this shows Mixed)")
    settings_version: IntProperty(default=0)      # (quality.migrate: files saved before per-system qualities)
    vis_tool: EnumProperty(name="Vis Compiler", default="HAMMERLESS", items=[
        ("VALVE", "Valve vvis", "L4D2's own visibility compiler (vvis.exe)"),
        ("HAMMERLESS", "Hammerless (faster)",
         "Hammerless's visibility compiler: many times faster than vvis (a city map: 4 s instead of 2 min 19 s), "
         "never hiding anything visible; about 1% looser than vvis. Match vvis Exactly gives vvis's own result "
         "instead. If it ever fails, Valve's vvis runs")])
    vis_exact: BoolProperty(
        name="Match vvis Exactly", default=False,
        description="Hammerless's vis compiler follows vvis's own method and gives its result byte for byte (about 3 "
                    "times faster than vvis, several times slower than the default). The default merges what each "
                    "opening shows: about 1% looser, never hides a view through an opening wider than vvis's "
                    "own 0.01-unit tolerance")
    vis_gpu: BoolProperty(
        name="Vis on the GPU", default=True,
        description="Hammerless's vis compiler runs on the graphics card (about twice as fast as on the CPU on big "
                    "maps, the same quality). Off: on the CPU. A card that can't, or a failure on the way, falls back "
                    "to the CPU by itself")
    vis_culling: EnumProperty(
        name="Vis", default="FAST",
        description="How tightly the vis on the graphics card works out what each spot can see",
        items=[("FAST", "Fast", "About twice as fast; the game draws about 10% more than Tight (measured on a big "
                                "map). Never hides anything visible"),
               ("TIGHT", "Tight", "A second pass that trims what Fast keeps: the least drawing, about twice the "
                                  "time. For finished maps")])
    map_tool: EnumProperty(name="Map Compiler", default="HAMMERLESS", items=[
        ("HAMMERLESS", "Hammerless",
         "Hammerless's map compiler: the same map as vbsp, byte for byte on 132 of Valve's 134 sample maps. "
         "If it ever fails, Valve's vbsp runs (and the build says so)"),
        ("VALVE", "Valve vbsp", "L4D2's own map compiler (vbsp.exe)")])
    model_compiler: EnumProperty(name="Model Compiler", default="HAMMERLESS", items=[
        ("HAMMERLESS", "Hammerless", "Hammerless writes Custom Models' game files itself (no extra tools needed)"),
        ("STUDIOMDL", "Valve studiomdl", "L4D2's own model compiler (studiomdl.exe, from the Authoring Tools)")])
    light_tool: EnumProperty(name="Light Compiler", default="HAMMERLESS", update=_q("on_light_tool"), items=[
        ("VALVE", "Valve vrad", "L4D2's own lighting compiler (vrad.exe)"),
        ("HAMMERLESS", "Hammerless",
         "Hammerless's lighting compiler: the same lighting as vrad (a few shadow-edge pixels differ very "
         "slightly), several times faster (on the GPU), with sky light from the sky, ray-traced bounce and "
         "Live Light Mixing. If it ever fails, Valve's vrad runs (and the build says so)"),
        ("CYCLES", "Cycles",
         "vrad lays out the lighting, then Blender's Cycles bakes the lightmaps with the same lights (its GPU "
         "if it has one). Prop lighting and switchable lights stay vrad's")])
    light_mixing: BoolProperty(name="Live Light Mixing", default=False,
                               description="Bake every light's light into a layer of its own too (about a third of a "
                                           "second more a light). Then Baked Lighting follows your lights' colour and "
                                           "brightness live, without baking again. Needs the Hammerless light "
                                           "compiler on the graphics card")
    light_exact: BoolProperty(name="Exact Lighting (match vrad)", default=False, update=_q("on_light_tool"),
                              description="Off: the Hammerless light compiler uses the graphics card, much faster "
                                          "and it looks the same (the CPU when there's no suitable card). On: it "
                                          "reproduces vrad's lighting bit for bit on the CPU (slower)")
    cycles_samples: IntProperty(name="Samples", default=1024, min=1, max=65536, soft_max=4096,
                                description="Cycles samples per lightmap sample: more is smoother and slower")
    cycles_stitch: BoolProperty(name="Stitch Seams", default=True,
                                description="Make neighbouring faces' lighting meet exactly along the edges they "
                                            "share (each face has its own lightmap, so they otherwise show seams)")
    cycles_denoise: BoolProperty(name="Denoise", default=False,
                                 description="Run Blender's denoiser (OpenImageDenoise, on the GPU when there is "
                                             "one) on the Cycles bake")
    vis_mode: EnumProperty(name="Quality", default="FULL", update=_q("on_vis_quality"), items=[
        ("SKIP", "Off", "No visibility pass: everything always renders (slow in-game on big maps)"),
        ("FAST", "Fast", "Quick visibility pass"),
        ("FULL", "Full", "Full visibility pass (best in-game performance)")])
    # (before per-system qualities: kept so older files' Custom choice can be read, see quality.migrate)
    rad_mode: EnumProperty(name="Lighting (vrad)", default="NORMAL", items=[
        ("SKIP", "Skip (fullbright)", "No lighting pass"),
        ("FAST", "Fast", "Quick lighting; terrain may show seams"),
        ("NORMAL", "Normal", "Normal lighting"),
        ("FINAL", "Final", "Highest quality lighting; slow")])
    static_prop_lighting: BoolProperty(name="Prop Lighting", default=True, update=_q("on_light_setting"),
                                       description="Light static props per vertex in the build (-StaticPropLighting): "
                                                   "better prop lighting, and the game doesn't have to do it at every "
                                                   "map load")
    # --- lighting quality: its presets fill these in (quality.py)
    light_quality: EnumProperty(name="Quality", items=LIGHT_QUALITY, default="NORMAL", update=_q("on_light_quality"))
    light_sky_rays: FloatProperty(name="Sky Rays", default=1.0, min=0.25, max=64.0, soft_max=32.0, step=100,
                                  update=_q("on_light_setting"),
                                  description="Times vrad's number of sky rays per luxel (-extrasky): more is smoother "
                                              "sky light and sun spread. Normal uses 4 on the GPU (1 otherwise), "
                                              "Final 16")
    light_sky_rays_pick: EnumProperty(
        name="Sky Rays", description="Times vrad's number of sky rays per luxel: more is smoother sky light and sun "
                                     "spread (on the GPU nearly free; on the CPU each doubling about doubles its time)",
        items=[(str(x), f"{x}\u00d7", f"{x} times vrad's sky rays", i) for i, x in enumerate((1, 2, 4, 8, 16, 32, 64))]
        + [("OTHER", "Other", "A value of your own (below)", 99)],
        get=lambda self: _sky_pick_get(self), set=lambda self, v: _sky_pick_set(self, v))
    light_supersample: BoolProperty(name="Supersampling", default=True, update=_q("on_light_setting"),
                                    description="Light luxels again at several points where the light changes "
                                                "sharply (shadow edges): cleaner edges")
    light_ss_points: IntProperty(name="Points", default=4, min=1, max=16, update=_q("on_light_setting"),
                                 description="Points across a luxel when supersampling (4: vrad's 4 x 4; Ultra 8 x 8). "
                                             "Hammerless light compiler only")
    light_ss_passes: IntProperty(name="Passes", default=4, min=1, max=16, update=_q("on_light_setting"),
                                 description="How many times supersampling spreads to the neighbours of luxels it "
                                             "changed (vrad: 4). Hammerless light compiler only")
    light_ss_threshold: FloatProperty(name="Edge Threshold", default=0.0625, min=0.005, max=0.5, step=0.5,
                                      precision=4, update=_q("on_light_setting"),
                                      description="The brightness step between neighbouring luxels that triggers "
                                                  "supersampling: lower catches softer edges (vrad: 0.0625). "
                                                  "Hammerless light compiler only")
    light_bounces: IntProperty(name="Bounces", default=100, min=0, max=1000, soft_max=200, update=_q("on_light_setting"),
                               description="Light bounces between surfaces (0: direct light only)")
    light_bounce_method: EnumProperty(name="Bounce Method", default="PATCHES", update=_q("on_light_setting"), items=[
        ("PATCHES", "vrad's Patches", "vrad's way: surfaces cut into patches that pass light to the patches they see"),
        ("GI", "Ray Traced (GPU)", "Every luxel sends rays and takes the light of what they hit (final gathering): "
                                   "bounced light in finer detail, no patch blotches or leaks. Hammerless light "
                                   "compiler on the GPU only (not Exact); experimental")])
    light_gi_rays: IntProperty(name="Bounce Rays", default=1024, min=64, max=8192, soft_max=4096,
                               update=_q("on_light_setting"),
                               description="Rays each luxel sends for bounced light: more is smoother (256 shows "
                                           "a little grain; 4096 is about 3 times slower than 1024)")
    light_prop_polys: BoolProperty(name="Prop Shadows from Full Model", default=True, update=_q("on_light_setting"),
                                   description="Props cast shadows with their full model, not their simpler "
                                               "collision shape (-StaticPropPolys)")
    light_patch_size: FloatProperty(name="Bounce Patch Size", default=4.0, min=1.0, max=16.0, step=100,
                                    update=_q("on_light_setting"),
                                    description="Size of the pieces bounced light is worked out on, in luxels "
                                                "(vrad: 4). Smaller is finer bounce light in corners, and much slower")
    light_fix_quirks: BoolProperty(name="Fix vrad's Quirks", default=False, update=_q("on_light_setting"),
                                   description="Go beyond vrad: leave out its oddities (edge luxels it leaves dark, "
                                               "bounced light from surfaces it shouldn't see, props' bounced light "
                                               "missing far surfaces). Hammerless light compiler only")
    extra_vbsp: StringProperty(name="vbsp", description="Extra command-line options for vbsp")
    extra_vvis: StringProperty(name="vvis", description="Extra command-line options for vvis")
    extra_vrad: StringProperty(name="vrad", description="Extra command-line options for vrad, e.g. -bounce 50")

    # --- lighting & sky
    skyname: StringProperty(name="Sky", default="sky_day01_09_hdr", update=_sky_view,
                            description="Skybox name (the name of its six materials in materials/skybox/, without "
                                        "the side: sky_day01_09_hdr for sky_day01_09_hdrbk and so on)")
    sky_choice: EnumProperty(name="Sky", items=_sky_choice_items, get=_sky_choice_get, set=_sky_choice_set,
                             description="The map's sky: every skybox in Left 4 Dead 2, or Custom to type a name")
    sky_custom: BoolProperty(default=False, options={"HIDDEN"})
    show_sky: BoolProperty(name="Show in Viewport", default=True, update=_sky_view,
                           description="Draw the sky behind the scene in the 3D viewport (any shading mode; "
                                       "nothing is added to the scene)")
    sky_view_exposure: FloatProperty(name="Brightness", default=0.0, soft_min=-4.0, soft_max=4.0, step=10,
                                     update=_sky_view, description="The viewport sky's brightness, in stops")
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
    sky_light: EnumProperty(name="Sky Light", default="FLAT", items=[
        ("FLAT", "One Colour", "The sky lights the map with the Sky Light Color from every direction (what "
                               "Valve's vrad does)"),
        ("SKYBOX", "From the Skybox", "Each part of the sky lights the map with its own colour, taken from the "
                                      "map's skybox: warm from a sunset horizon, blue from above. Needs the "
                                      "Hammerless light compiler"),
        ("IMAGE", "From an Image (HDRI)", "Like From the Skybox, but from a panorama image (an HDRI): the one "
                                         "picked here, or the World's Environment Texture. The game still "
                                         "shows the skybox. Needs the Hammerless light compiler")])
    sky_image: PointerProperty(name="Sky Image", type=bpy.types.Image,
                               description="Panorama (equirectangular) image to light the map with; empty: the "
                                           "World's Environment Texture")
    sky_rotation: FloatProperty(name="Sky Rotation", default=0.0, min=-360.0, max=360.0, subtype="NONE",
                                description="Turns the sky image round the vertical (degrees)")
    ambient_brightness: IntProperty(name="Sky Light Brightness", default=80, min=0, max=2000,
                                    description="Light from the sky dome that fills shadows")
    lightmap_scale: IntProperty(name="Lightmap Scale", default=16, min=1, max=128,
                                description="Shadow detail on brush faces: 8 = sharp, 16 = Valve default, "
                                            "32+ = soft but faster to compile")
    lightmap_scale_imported: BoolProperty(name="Imported Brushes Too", default=False,
                                          description="An imported map's brushes keep the lightmap scale each face "
                                                      "had in the map: use this Lightmap Scale (or the material's "
                                                      "own) for them too")

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
                                             "generator) and analyzes it (visibility, hiding spots): the game "
                                             "loads the map once"),
               ("GAME", "Made by the game", "The game generates it after loading: slower (two extra reloads), "
                                           "kept as a fallback")])
    nav_analysis: EnumProperty(
        name="Nav Analysis", default="BLENDER",
        description="Who works out what each nav area can see and where the hiding spots are",
        items=[("BLENDER", "In Blender", "Hammerless analyzes the nav mesh after the compile; the game loads the map "
                                         "once (no analysis reload)"),
               ("GAME", "By the game", "The game analyzes it after loading, then reloads the map")])
    wall_climbs: BoolProperty(
        name="Auto Climb Links", default=False,
        description="A nav link up every wall up to 160 units (about 3 m) high, and back down: common infected "
                    "climb them like a ladder. The game has no zombies-only link, so the Director's flow and the "
                    "bots treat them as walkable too: a route that only works with them is blocked for survivors. "
                    "Only with Nav Mesh: Made in Blender")
    nav_culling: BoolProperty(
        name="Nav Culling", default=True,
        description="On (like the game's own generator): nav only grows from the item spawns and Landmarks across what "
                    "can be walked, so roofs and ledges nothing leads up to get none. Put a Landmark on each roof you "
                    "want nav on (smoker spots).\n"
                    "Off: every flat top inside the map big enough to stand on gets nav too (roofs, ledges, "
                    "walkways). The Director spreads its wandering commons over every nav area, and those survivors "
                    "can't walk to get none (smokers and the other specials still spawn there), so lots of roof "
                    "areas mean fewer commons on the streets. Only with Nav Mesh: Made in Blender")
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
    fast_loading: BoolProperty(name="Fast Map Loading", default=True,
                               description="Test as a LAN-only game (sv_lan 1): maps load about 6 seconds faster "
                                           "because the game doesn't register with Steam's servers. Untick it to "
                                           "let friends join your game over the internet")
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


CLASSES = (HL_Problem, HL_SpawnListItem, HL_KeyValue, HL_Output, HL_BehaviourSlot, HL_ObjectSettings, HL_CollectionSettings, HL_MaterialSettings, HL_SceneSettings)


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
