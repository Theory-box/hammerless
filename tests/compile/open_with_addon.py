"""Open Blender with Hammerless loaded from this folder (no install needed), then
refresh texture and model previews once the file has loaded. Doesn't build anything.

Run:  blender <file.blend> --python tests/compile/open_with_addon.py
"""
import os
import sys

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import hammerless  # noqa: E402

hammerless.register()


def _refresh():
    try:
        bpy.ops.hammerless.refresh_previews()
    except Exception as ex:  # shown in the console; the file still opens normally
        print("Hammerless: preview refresh failed:", ex)
    return None


bpy.app.timers.register(_refresh, first_interval=2.0)
