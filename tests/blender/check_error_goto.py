"""A compiler error with a place, in the Problems list: clicking it selects the entity standing there and moves the
3D cursor to it.

    blender --background --factory-startup --python tests/blender/check_error_goto.py

Prints GOTO lines; exit 1 on a failure.
"""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless

hammerless.register()
from hammerless.blender import problems
from hammerless.core.build import Report
from hammerless.core.compile import parse_log
from hammerless.core.mapcheck import Problem

for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
s = bpy.context.scene.hammerless
upm = s.units_per_meter
bpy.ops.object.empty_add(location=(1234.0 / upm, -512.5 / upm, 96.0 / upm))
overlay = bpy.context.object
overlay.name = "info_overlay"
overlay.hammerless.role, overlay.hammerless.classname = "ENTITY", "info_overlay"
bpy.ops.object.empty_add(location=(0, 0, 0))
other = bpy.context.object
other.hammerless.role, other.hammerless.classname = "ENTITY", "prop_static"
other.select_set(True)

summ = parse_log("Error: Overlay touching too many faces (touching 65, max 64)\n"
                 "Overlay decals/debris_concrete001a at 1234.0 -512.5 96.0\n!! hlvbsp failed (exit code 1)\n")
failed = Report()                              # (as Build stores a failed compile)
failed.errors = [f"Compiler: {e}" for e in summ.errors[:20]]
failed.problems = [Problem(p.severity, f"Compiler: {p.message}", p.source, p.location)
                   for p in summ.problems if p.message in summ.errors[:20]]
problems.store(bpy.context, failed)
rows = [(p.name[:70], p.source, p.has_location) for p in s.problems]
print("GOTO rows", rows)
problems.go_to_problem(bpy.context, 0)
cur = bpy.context.scene.cursor.location
ok = (len(rows) == 1 and rows[0][1] == overlay.name and rows[0][2]
      and (cur - overlay.location).length < 1e-3 and overlay.select_get() and not other.select_get())
print("GOTO selected", [o.name for o in bpy.context.selected_objects], "cursor at the overlay",
      (cur - overlay.location).length < 1e-3)
print("GOTO", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
