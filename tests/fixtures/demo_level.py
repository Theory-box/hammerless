"""Builds demo/demo_level.blend: a small playable test level.

Run:  blender --background --factory-startup --python tests/fixtures/demo_level.py
NOTE: demo/demo_level.blend has been hand-edited since; this script writes
demo/demo_level_generated.blend unless run with  -- --force.

Layout (Blender meters, +X = forward): start safe room -> hilly field with a few
buildings, items, a Witch and a Tank spawn -> end safe room.
"""
import math
import os
import sys

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import hammerless  # noqa: E402

hammerless.register()
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
s = scene.hammerless
s.map_name = "hl_demo"
upm = s.units_per_meter


def coll(name, role="NONE"):
    c = bpy.data.collections.new(name)
    scene.collection.children.link(c)
    c.hammerless.role = role
    return c


def move_to(obj, c):
    for old in obj.users_collection:
        old.objects.unlink(obj)
    c.objects.link(obj)


def mat(path):
    m = bpy.data.materials.get(path) or bpy.data.materials.new(path)
    m.hammerless.source_material = path
    return m


def box(name, size, loc, c, material):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = size
    o.data.materials.append(mat(material))
    move_to(o, c)
    return o


def entity(classname, loc, c, yaw=0.0, **kv):
    scene.cursor.location = loc
    bpy.ops.hammerless.add_entity(classname=classname)
    o = bpy.context.object
    o.rotation_euler = (0, 0, math.radians(yaw))
    for k, v in kv.items():
        item = next((x for x in o.hammerless.keyvalues if x.key == k), None) or o.hammerless.keyvalues.add()
        item.key, item.value = k, str(v)
    move_to(o, c)
    return o


# --- terrain: 72 x 30 m hilly field running from the start room door (x=-2) to the end room (x=70)
ground = coll("Terrain", "TERRAIN")
bpy.ops.mesh.primitive_grid_add(x_subdivisions=72, y_subdivisions=30, size=1, location=(34, 0, 0))
t = bpy.context.object
t.name = "Field"
t.scale = (72, 30, 1)
bpy.ops.object.transform_apply(scale=True)
for v in t.data.vertices:
    x, y = v.co.x, v.co.y                          # local: x in [-36, 36]
    hills = min(1.0, max(0.0, (36 - abs(x) - 8) / 10))  # flat for 8 m at each safe room, then a gentle rise
    hills *= min(1.0, max(0.0, (15 - abs(y)) / 3)) # and along the boundary walls
    v.co.z = -0.05 + hills * (0.8 * math.sin(y * 0.35) + 0.6 * math.cos(x * 0.2 + y * 0.1) + 0.6)
t.data.materials.append(mat("nature/blend_grass_grass_01"))
move_to(t, ground)

# --- buildings: convex boxes = brushes
world = coll("Buildings", "BRUSH")
box("Shed walls", (4, 3, 2.6), (22, 6, 1.3), world, "brick/brick_ext_08")
box("Shed roof", (4.6, 3.6, 0.2), (22, 6, 2.7), world, "wood/woodwall003a")
box("Wall", (0.4, 10, 2), (36, -6, 1), world, "concrete/concrete_ext_01")
box("Crate stack", (1.2, 1.2, 1.2), (30, 3, 0.6), world, "wood/woodwall003a")
box("Truck body", (6, 2.4, 2.8), (42, 4, 1.4), world, "metal/metalwall014a")
# boundary walls so players can't walk off the field
box("Boundary north", (72, 0.5, 5), (34, 15.25, 2), world, "brick/brick_ext_08")
box("Boundary south", (72, 0.5, 5), (34, -15.25, 2), world, "brick/brick_ext_08")

# --- safe rooms (preset collections), start at x=-8, end at x=64
scene.cursor.location = (-8, -2.4, 0)
bpy.ops.hammerless.add_preset(preset="START_SAFE_ROOM", landmark="landmark_0")
scene.cursor.location = (70, 2.4, 0)
bpy.ops.hammerless.add_preset(preset="END_SAFE_ROOM", landmark="landmark_1", next_map="hl_demo2")
# End room should face back toward the field: rotate its whole collection 180 degrees
end = bpy.data.collections["End Safe Room"]
pivot = (70 + 320 / upm / 2, 2.4 + 256 / upm / 2)
for o in end.objects:
    x, y = o.location.x - pivot[0], o.location.y - pivot[1]
    o.location.x, o.location.y = pivot[0] - x, pivot[1] - y
    o.rotation_euler.z += math.pi

# --- gameplay
play = coll("Gameplay")
entity("weapon_pain_pills_spawn", (22, 6, 0.1), play)
entity("weapon_molotov_spawn", (30, 3, 1.25), play)
entity("weapon_spawn", (42, 1.5, 0.1), play, weapon_selection="tier2_any")
entity("info_zombie_spawn", (48, -8, 0.5), play, yaw=180, population="witch")
entity("info_zombie_spawn", (56, 8, 0.5), play, population="tank")

# --- horde events
# The field is wide open, so mark it as a place the Director may spawn commons (OBSCURED)
for x in (25, 52):
    scene.cursor.location = (x, 0, 0)
    bpy.ops.hammerless.add_preset(preset="ZOMBIE_SPAWN_AREA")
# A horde starts when a survivor walks 15 m out; the trigger spans the whole field
scene.cursor.location = (15, 0, 0)
bpy.ops.hammerless.add_preset(preset="HORDE_TRIGGER")
bpy.data.objects["Horde Trigger trigger"].scale = (1, 6, 1)
# ...or when someone presses the button on the shed's west wall
scene.cursor.location = (20, 6, 0)
bpy.ops.hammerless.add_preset(preset="HORDE_BUTTON")
# A crescendo (hordes in waves) from the button on the truck's west side
scene.cursor.location = (39, 4, 0)
bpy.ops.hammerless.add_preset(preset="CRESCENDO_BUTTON")

# --- an icy slab near the start (friction test): game texture + "ice" surface
ice = box("Ice slab", (4, 4, 0.1), (6, -8, 0.05), world, "concrete/concrete_floor_01")
ice_mat = bpy.data.materials.new("Ice (concrete look)")
ice_mat.hammerless.source_material = "concrete/concrete_floor_01"
ice_mat.hammerless.surface = "ice"
ice.data.materials[0] = ice_mat

# --- sun
bpy.ops.object.light_add(type="SUN", rotation=(math.radians(40), math.radians(10), math.radians(30)))
bpy.context.object.data.energy = 4

# --- test-friendly settings
s.debug_log = True
s.autotest = True               # bots walk the level (turn off to play it yourself)
s.director_enabled = True       # custom Director settings (verified via the debug log)
s.dir_common_limit = 20
s.window_monitor = "0"          # leftmost monitor
s.window_width, s.window_height = 1856, 1000

out = os.path.join(ROOT, "demo", "demo_level.blend")
os.makedirs(os.path.dirname(out), exist_ok=True)
# The demo has since been hand-edited in Blender (terrain fixed at the safe room exit):
# never overwrite it unless explicitly asked. Otherwise write a separate copy.
if os.path.exists(out) and "--force" not in sys.argv:
    out = os.path.join(ROOT, "demo", "demo_level_generated.blend")
    print("demo_level.blend exists (hand-edited); writing", out, "instead. Pass -- --force to overwrite.")
s.output_dir = "//hammerless_build"
bpy.ops.wm.save_as_mainfile(filepath=out)
print("Saved", out)

# Export once so the result can be inspected (uses the real game install if found)
bpy.ops.hammerless.export_vmf()
print(bpy.data.texts["hammerless_log"].as_string())
