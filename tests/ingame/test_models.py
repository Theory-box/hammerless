"""Custom Models in the game: builds demo/demo_level.blend (never saved) with a few Custom Model objects as
hl_test_models and checks them through the test bench; saves screenshots to look at.

Run:  python tests/ingame/test_models.py [--shots <folder>]

Checks: the static and dynamic props exist, the physics prop falls and rests on the ground, a static model
is solid (a crate dropped on it rests on top), the nav mesh leaves a hole where a static model stands.
Use it to compare a new model writer against studiomdl's models.
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from tests.ingame.bench import Bench  # noqa: E402

UPM = 52.49
BLEND = os.path.join(ROOT, "demo", "demo_level.blend")
PREP = r'''
from hammerless.blender.logic import TREE
for t in [t for t in bpy.data.node_groups if t.bl_idname == TREE]:
    bpy.data.node_groups.remove(t)
wood = bpy.data.materials.new("crate wood"); wood.hammerless.source_material = "wood/woodwall003a"
bpy.ops.mesh.primitive_monkey_add(location=(4, 0, 1.2)); m = bpy.context.object; m.name = "monkey"
m.hammerless.role = "MODEL"
bpy.ops.object.duplicate_move_linked(); m2 = bpy.context.object; m2.location = (4, 2.5, 1.2); m2.rotation_euler = (0, 0, 1.2)
bpy.ops.mesh.primitive_cube_add(size=0.6, location=(4, -2.5, 2.5)); c = bpy.context.object; c.name = "crate"
c.data.materials.append(wood); c.hammerless.role, c.hammerless.model_kind = "MODEL", "PHYSICS"
bpy.ops.mesh.primitive_torus_add(location=(6, 0, 1.0)); t = bpy.context.object; t.name = "ring"
t.data.materials.append(wood); t.hammerless.role, t.hammerless.model_kind = "MODEL", "DYNAMIC"
kv = t.hammerless.keyvalues.add(); kv.key, kv.value = "targetname", "hl_ring"
'''


def main(argv):
    shots = argv[argv.index("--shots") + 1] if "--shots" in argv else None
    results = []

    def check(name, ok, note=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {note}", flush=True)

    with Bench() as b:
        b.build(BLEND, "hl_test_models", prep=PREP)
        b.console("sv_cheats 1", "god 1", "director_stop", "nb_blind 1", wait=0.3)
        phys = b.run('local out = [], e = null; while (e = Entities.FindByClassname(e, "prop_physics")) '
                     'if (e.GetModelName().find("hammerless") != null) out.append(e.GetOrigin().z); return out;')
        check("physics prop exists and fell", len(phys) == 1 and phys[0] < 2.5 * UPM - 20, f"z {phys}")
        check("dynamic prop exists (named)", b.value('Entities.FindByName(null, "hl_ring") != null'))
        mx = 4 * UPM
        b.run(f'::HLT_drop <- SpawnEntityFromTable("prop_physics", {{ model = "models/props_junk/wood_crate001a.mdl", '
              f'origin = Vector({mx}, 0, 250) }}); return true;')
        time.sleep(3)
        z = b.value("::HLT_drop.GetOrigin().z")
        check("static model is solid", z > 1.2 * UPM, f"a crate dropped on it rests at z {z:.0f}")
        n = b.run(f'local t = {{}}, n = 0; NavMesh.GetAllAreas(t); foreach (a in t) {{ local c = a.GetCenter(); '
                  f'if (fabs(c.x - {mx}) < 30 && fabs(c.y) < 30) n++; }} return n;')
        check("nav mesh goes around it", n == 0, f"{n} nav areas under the monkey")
        if shots:
            b.look_at((4.5 * UPM, 0.8 * UPM, 1.2 * UPM), distance=200)
            print("screenshot:", b.screenshot("models_front", out_dir=shots))
            b.console("noclip", wait=0.2)
        b.console("nb_blind 0", "director_start", "god 0", "sv_cheats 0", wait=0.3)
    print(f"\n{sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
