"""Every logic example, added to an empty map and exported: what the export says about it.

    blender --background --factory-startup --python tests/blender/check_examples_export.py

Needs the game (entity definitions, models). Per example: the problems building it reported, and the export's errors
and warnings that mention logic, nodes or its objects. Prints EXAMPLE lines and a summary; exit 1 when any example
has a problem or an error.
"""
import os
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless

hammerless.register()
from hammerless.blender.logic_examples import all_examples, build_example

OUT = tempfile.mkdtemp(prefix="hl_test_examples_export_")
bad = 0
examples = all_examples()
for i, ex in enumerate(examples):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene.hammerless
    s.map_name, s.output_dir, s.sound_mode = "hl_test_example_check", OUT, "OFF"
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -0.25))
    floor = bpy.context.object
    floor.scale = (40, 40, 0.5)
    bpy.ops.object.empty_add(location=(2, 0, 0.1))
    sp = bpy.context.object
    sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
    bpy.context.scene.cursor.location = (-10, -10, 0)
    problems, lines = [], []
    try:
        build_example(bpy.context, i, problems)
        try:
            bpy.ops.hammerless.export_vmf()
        except RuntimeError as ex_err:          # (an ERROR report raises from Python)
            lines.append("ERROR: " + str(ex_err).strip().splitlines()[0][:300])
        log = bpy.data.texts.get("hammerless_log")
        for line in (log.as_string().splitlines() if log else []):
            low = line.lower()
            if line.startswith("ERROR") or (line.startswith("WARNING") and any(
                    w in low for w in ("logic", "node", "example", "behaviour", "self", "socket", "isn't an entity"))):
                lines.append(line[:300])
    except Exception as ex_err:
        import traceback
        lines.append("RAISED: " + repr(ex_err)[:200] + " @ " + traceback.format_exc().strip().splitlines()[-3].strip()[:150])
    lines = list(dict.fromkeys(lines))
    if problems or any(l.startswith(("ERROR", "RAISED")) for l in lines):
        bad += 1
    if problems or lines:
        print(f"EXAMPLE {i + 1:02d} {ex['title']}")
        for p in problems:
            print("    build problem:", p[:300])
        for l in lines:
            print("    " + l)
print(f"EXAMPLES checked {len(examples)}, with problems or errors: {bad}")
sys.exit(1 if bad else 0)
