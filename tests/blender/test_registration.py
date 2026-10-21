"""Run with Blender --background --factory-startup --python tests/blender/test_registration.py."""
import sys
from pathlib import Path
import bpy
from bpy_restrict_state import RestrictBlend

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
from hammerless.blender import problems, ui

for cycle in range(2):
    # Match addon_utils.enable, unlike normal scripts that can already access scene data.
    with RestrictBlend():
        hammerless.register()
    panels = [c for c in ui.CLASSES if issubclass(c, bpy.types.Panel)]
    assert panels and all(c.is_registered for c in panels)
    assert bpy.app.timers.is_registered(problems._registration_ready)
    s = bpy.context.scene.hammerless
    s["ingame_flow"] = "In game: Director flow works (100 units)"
    problems._registration_ready()
    assert s.get("ingame_flow", "").startswith("Previous game test is stale")
    hammerless.unregister()
    assert not bpy.app.timers.is_registered(problems._registration_ready)
    assert problems._file_loaded not in bpy.app.handlers.load_post
    assert not any(c.is_registered for c in panels)
print("PASS restricted registration, all panels, deferred stale check, unregister and re-enable")
