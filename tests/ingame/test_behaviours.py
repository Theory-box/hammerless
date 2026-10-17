"""Behaviours in the game: builds demo/demo_level.blend (never saved) as hl_test_behaviours with
  - three lamps on the Flicker preset (one shared Behaviour graph -> one shared light name, SetPattern at start)
  - a 'Blink' Behaviour (Timer 1 s -> Hide Self) on two boxes, each hiding itself
and checks them through the test bench.
Run:  python tests/ingame/test_behaviours.py
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from tests.ingame.bench import Bench  # noqa: E402

BLEND = os.path.join(ROOT, "demo", "demo_level.blend")
PREP = r'''
from hammerless.blender.logic import TREE
for t in [t for t in bpy.data.node_groups if t.bl_idname == TREE]:
    bpy.data.node_groups.remove(t)
for i in range(3):
    data = bpy.data.lights.new(f"Behaviour Lamp {i}", "POINT")
    lamp = bpy.data.objects.new(f"Behaviour Lamp {i}", data)
    bpy.context.collection.objects.link(lamp)
    lamp.location = (4.0 + i * 1.5, -2.0, 2.5)
    lamp.hammerless.light_pattern = "1"
from hammerless.blender.behaviours import new_behaviour_tree
tree = new_behaviour_tree("Blink")
me = next(n for n in tree.nodes if n.bl_idname == "HL_NodeSelf")
timer = tree.nodes.new("HL_NodeTimer")
timer.seconds = 1.0
sh = tree.nodes.new("HL_NodeShowHide")
tree.links.new(me.outputs["Object"], sh.inputs["Object"])
tree.links.new(timer.outputs["On Tick"], sh.inputs["Hide"])
for i in range(2):
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(4.0 + i * 1.5, 2.0, 0.6))
    box = bpy.context.object
    box.name = f"Blink Box {i}"
    box.hammerless.behaviours.add().graph = tree
'''


def main(argv):
    flicker = "mmnmmommommnonmmonqnmmo"            # (the game's style 1, lightpresets.PATTERNS["1"])
    results = []

    def check(name, ok, note=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {note}", flush=True)

    with Bench() as b:
        b.build(BLEND, "hl_test_behaviours", prep=PREP)
        b.console("sv_cheats 1", "god 1", "director_stop", "nb_blind 1", wait=0.3)
        lights = b.run('local out = [], e = null; while (e = Entities.FindByClassname(e, "light")) '
                       'if (e.GetName().find("hl_lightpattern_") == 0) out.append([e.GetName(), '
                       'NetProps.GetPropInt(e, "m_iStyle"), NetProps.GetPropString(e, "m_iszPattern")]); return out;')
        check("three lamps share one preset name and one light style",
              len(lights) == 3 and len({x[0] for x in lights}) == 1 and len({x[1] for x in lights}) == 1
              and lights[0][1] >= 32, f"{lights}")
        # (a pattern belongs to the light style: set on one of them, it's the style's, so all three flicker)
        check("the preset's pattern was set on their style", any(x[2] == flicker for x in lights), f"{lights}")
        time.sleep(2.0)
        boxes = b.run('local out = [], e = null; while (e = Entities.FindByClassname(e, "func_brush")) '
                      'if (e.GetName().find("blink_box") >= 0) out.append([e.GetName(), '
                      'NetProps.GetPropInt(e, "m_iDisabled")]); return out;')
        check("each box hid itself", len(boxes) == 2 and all(x[1] == 1 for x in boxes)
              and len({x[0] for x in boxes}) == 2, f"{boxes}")
        b.console("nb_blind 0", "director_start", "god 0", "sv_cheats 0", wait=0.3)
    print(f"\n{sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
