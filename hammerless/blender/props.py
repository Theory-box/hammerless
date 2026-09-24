"""Blender property groups: scene settings, object/collection tags, materials."""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from ..core.entities import default_keyvalues

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
]


class HL_KeyValue(bpy.types.PropertyGroup):
    key: StringProperty(name="Key")
    value: StringProperty(name="Value")


class HL_Output(bpy.types.PropertyGroup):
    output: StringProperty(name="Output", default="OnTrigger",
                           description="Event on THIS entity, e.g. OnTrigger, OnPressed, OnStartTouch")
    target: StringProperty(name="Target", default="director",
                           description="Name (targetname) of the entity to send the input to")
    input: StringProperty(name="Input", default="ForcePanicEvent",
                          description="What the target should do, e.g. ForcePanicEvent, SpawnZombie, Open")
    parameter: StringProperty(name="Parameter", description="Optional value passed with the input")
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


class HL_MaterialSettings(bpy.types.PropertyGroup):
    source_material: StringProperty(
        name="Game Material",
        description="L4D2 material path, e.g. concrete/concrete_floor_01. Leave empty to "
                    "convert this material's image texture into a custom material")
    texture_scale: FloatProperty(name="Texture Scale", default=0.25, min=0.01, max=16.0,
                                 description="Hammer texture scale (0.25 = Hammer default)")
    surfaceprop: StringProperty(name="Surface", default="concrete",
                                description="$surfaceprop for custom materials (footstep sounds, impacts)")


class HL_SceneSettings(bpy.types.PropertyGroup):
    map_name: StringProperty(name="Map Name", default="my_map",
                             description="File name of the map (lowercase, no spaces)")
    game_root: StringProperty(name="L4D2 Folder", subtype="DIR_PATH",
                              description="Left 4 Dead 2 install folder (auto-detected if empty)")
    output_dir: StringProperty(name="Work Folder", subtype="DIR_PATH", default="//hammerless_build",
                               description="Where the .vmf and compile files are written")
    units_per_meter: FloatProperty(
        name="Units per Meter", default=52.49, min=1.0, max=200.0,
        description="Hammer units per Blender meter. 52.49 makes real-world-size buildings "
                    "match Valve's maps (doors, stairs). 39.37 = true inches")
    skyname: StringProperty(name="Sky", default="sky_day01_09_hdr")
    default_material: StringProperty(name="Default Material", default="dev/dev_measuregeneric01b",
                                     description="Used for faces without a material")
    auto_seal: BoolProperty(name="Auto Seal (skybox shell)", default=True,
                            description="Wrap the map in a skybox box so it can never leak")
    compile_preset: EnumProperty(name="Compile", items=COMPILE_PRESETS, default="NORMAL")
    generate_nav: BoolProperty(
        name="Generate Nav Mesh", default=False,
        description="Run nav_generate when the game launches (needed for bots and zombies). "
                    "Only needed after geometry changes")
    check_game_content: BoolProperty(name="Check Materials/Models", default=True,
                                     description="Warn about materials and models missing from the game")
    last_log: StringProperty()


CLASSES = (HL_KeyValue, HL_Output, HL_ObjectSettings, HL_CollectionSettings, HL_MaterialSettings, HL_SceneSettings)


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
