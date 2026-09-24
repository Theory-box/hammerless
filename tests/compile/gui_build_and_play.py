"""Open Blender's UI, then press Build & Play exactly like the button does.

Run:  blender <file.blend> --python tests/compile/gui_build_and_play.py
The hammerless_log text block is mirrored to <work folder>/gui_log.txt.
"""
import os
import sys

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import hammerless  # noqa: E402

hammerless.register()
bpy.context.scene.hammerless.generate_nav = os.environ.get("HL_NAV", "1") == "1"
if os.environ.get("HL_PLAYTEST") == "1":   # hands-on run: no bot test, easy difficulty
    bpy.context.scene.hammerless.autotest = False
    bpy.context.scene.hammerless.difficulty = "Easy"
_state = {"pressed": False, "last": ""}


def _log_path():
    from hammerless.blender.ops import work_dir
    return os.path.join(work_dir(bpy.context), "gui_log.txt")


def tick():
    win = bpy.context.window_manager.windows[0]
    area = next((a for a in win.screen.areas if a.type == "VIEW_3D"), None)
    if not _state["pressed"]:
        if area is None:
            return 1.0
        with bpy.context.temp_override(window=win, area=area):
            if area.spaces.active and hasattr(area.spaces.active, "show_region_ui"):
                area.spaces.active.show_region_ui = True  # open the N panel so it's visible
            bpy.ops.hammerless.build("INVOKE_DEFAULT", play=True)
        _state["pressed"] = True
    txt = bpy.data.texts.get("hammerless_log")
    if txt is not None:
        text = txt.as_string()
        if text != _state["last"]:
            _state["last"] = text
            with open(_log_path(), "w", encoding="utf-8") as f:
                f.write(text)
    return 1.0


bpy.app.timers.register(tick, first_interval=3.0)
