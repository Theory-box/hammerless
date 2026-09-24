"""Builds demo/demo_level2.blend: the map after hl_demo (tests the safe room transition).

Run:  blender --background --factory-startup --python tests/fixtures/demo_level2.py
Start safe room with landmark_1 (matching hl_demo's end room) opening onto a courtyard.
"""
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
s.map_name = "hl_demo2"


def box(name, size, loc, material):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = size
    m = bpy.data.materials.get(material) or bpy.data.materials.new(material)
    m.hammerless.source_material = material
    o.data.materials.append(m)
    return o


scene.cursor.location = (-8, -2.4, 0)
bpy.ops.hammerless.add_preset(preset="START_SAFE_ROOM", landmark="landmark_1")

# courtyard: floor + 3 walls, open toward the safe room door (+X side of the room)
box("Courtyard floor", (20, 16, 0.5), (8, 0, -0.25), "concrete/concrete_floor_01")
box("Courtyard wall N", (20, 0.5, 4), (8, 8.25, 2), "brick/brick_ext_08")
box("Courtyard wall S", (20, 0.5, 4), (8, -8.25, 2), "brick/brick_ext_08")
box("Courtyard wall E", (0.5, 16, 4), (18.25, 0, 2), "brick/brick_ext_08")

# custom texture test: a generated colour-grid image on a wall facing the safe room door
img = bpy.data.images.new("hl_test_grid", 512, 512)
img.generated_type = "COLOR_GRID"
img.pack()
mat = bpy.data.materials.new("Hammerless Test Grid")
mat.use_nodes = True
tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
tex.image = img
mat.node_tree.links.new(tex.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
mat.hammerless.texture_scale = 0.5
bpy.ops.mesh.primitive_cube_add(size=1, location=(10, 0, 1.5))
grid_wall = bpy.context.object
grid_wall.name = "Custom texture wall"
grid_wall.scale = (0.5, 6, 3)
grid_wall.data.materials.append(mat)

scene.cursor.location = (12, 0, 0)
bpy.ops.hammerless.add_entity(classname="weapon_first_aid_kit_spawn")

s.debug_log = True
s.window_monitor = "0"
s.window_width, s.window_height = 1856, 1000

out = os.path.join(ROOT, "demo", "demo_level2.blend")
s.output_dir = "//hammerless_build"
bpy.ops.wm.save_as_mainfile(filepath=out)
print("Saved", out)
