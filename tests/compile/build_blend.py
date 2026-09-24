"""Compile test: export a .blend and run the real compilers, printing the full log.

Run:  blender --background <file.blend> --python tests/compile/build_blend.py -- [PRESET]
PRESET is QUICK / FAST / NORMAL / FINAL (default FAST). Exit code 0 = compiled.
"""
import os
import sys

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import hammerless  # noqa: E402
from hammerless.core import compile as cc  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
preset = args[0] if args else "FAST"

hammerless.register()
try:
    bpy.ops.hammerless.export_vmf()
except RuntimeError as ex:
    print("EXPORT FAILED:", ex)
print(bpy.data.texts["hammerless_log"].as_string())

s = bpy.context.scene.hammerless
root = cc.find_game_root()
vmf = os.path.join(bpy.path.abspath(s.output_dir), f"{s.map_name}.vmf")
if not os.path.exists(vmf):
    sys.exit(2)
tools = cc.Tools(root)
if tools.missing():
    print("Missing tools:", tools.missing())
    sys.exit(3)
job = cc.CompileJob(tools, vmf, preset).start().wait()
print("\n".join(job.log))
print("\n==== SUMMARY ====")
print("failed:", job.failed, "leaked:", job.summary.leaked)
for e in job.summary.errors:
    print("ERROR:", e)
for w in job.summary.warnings[:40]:
    print("WARN:", w)
sys.exit(1 if job.failed else 0)
