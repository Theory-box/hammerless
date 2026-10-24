"""Create the examples in an isolated factory scene; never load or save a user map."""
import sys
from pathlib import Path
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
from hammerless.blender.logic_examples import all_examples, build_example

hammerless.register()
examples = all_examples()
count = 0
for index, example in enumerate(examples):
    if not example["title"].startswith("Wall Weapon - "):
        continue
    problems = []
    build_example(bpy.context, index, problems)
    assert not problems, problems
    obj = bpy.data.objects[f"Example {index + 1:02d} wall_gun"]
    assert obj.hammerless.classname == "prop_dynamic_override"
    assert len(obj.hammerless.behaviours) == 1
    graph = obj.hammerless.behaviours[0].graph
    assert graph.is_behaviour and len(graph.links) == 1
    assert graph.nodes["buy"].price > 0
    assert graph.nodes["buy"].bl_idname == "HL_NodeWallWeapon"
    assert graph.nodes["self"].bl_idname == "HL_NodeSelf"
    count += 1
assert count == 3
print("PASS all three wall weapon examples, models and Self behaviour wiring")
hammerless.unregister()
