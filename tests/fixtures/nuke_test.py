"""Builds demo/hl_test_nuke.blend: the demo level with the Power-Up: Nuke example (example 42), set so
the first common infected killed drops the nuke, and 40 common infected spread over the field.

Run:  blender --background --factory-startup --python tests/fixtures/nuke_test.py
Then open demo/hl_test_nuke.blend and Build & Play. demo/demo_level.blend isn't changed.
"""
import os
import sys

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import hammerless  # noqa: E402

hammerless.register()
from hammerless.blender.logic import TREE, new_tree  # noqa: E402
from hammerless.blender.logic_examples import all_examples, build_example  # noqa: E402

bpy.ops.wm.open_mainfile(filepath=os.path.join(ROOT, "demo", "demo_level.blend"))
s = bpy.context.scene.hammerless
s.map_name = "hl_test_nuke"
s.compile_preset = "FAST"                  # quick vrad lighting, no Cycles
s.vis_tool, s.light_tool = "VALVE", "VALVE"
for t in [t for t in bpy.data.node_groups if t.bl_idname == TREE]:
    bpy.data.node_groups.remove(t)

index = next(i for i, ex in enumerate(all_examples()) if ex["title"] == "Power-Up: Nuke")
tree = build_example(bpy.context, index)
tree.nodes["chance"].inputs["value"].value = 1.0          # for the test: the first kill drops it

# 40 common infected over the field (nav areas 600-3000 units along the path), 3 s after the start
helper = new_tree(bpy.context)
helper.name = "Test: commons to kill"
start = helper.nodes.new("HL_NodeMapStart")
wait = helper.nodes.new("HL_NodeDelay")
wait.seconds = 3.0
spawn = helper.nodes.new("HL_NodeScriptCode")
spawn.label = "40 commons on the field"
spawn.code = """local t = {}, spots = [];
NavMesh.GetAllAreas(t);
foreach (a in t) { local f = GetFlowDistanceForPosition(a.GetCenter()); if (f > 600 && f < 3000) spots.append(a); }
for (local i = 0; i < 40 && spots.len() > 0; i++)
    SpawnEntityFromTable("infected", { origin = spots[RandomInt(0, spots.len() - 1)].FindRandomSpot() });
printl("HAMMERLESS_TEST commons spawned on " + spots.len() + " areas");"""
helper.links.new(start.outputs["start"], wait.inputs["in"])
helper.links.new(wait.outputs["out"], spawn.inputs["run"])
wait.location, spawn.location = (250, 0), (500, 0)

out = os.path.join(ROOT, "demo", "hl_test_nuke.blend")
bpy.ops.wm.save_as_mainfile(filepath=out)
print("saved", out)
