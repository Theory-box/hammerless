"""Exercise Blender's restricted addon-enable path, not direct register()."""
import sys
from pathlib import Path
import bpy
import addon_utils

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
errors = []
def fail(*args):
    import traceback
    errors.append(traceback.format_exc())

module = addon_utils.enable("hammerless", default_set=False, handle_error=fail)
assert module is not None and not errors, errors
assert module.__addon_enabled__
from hammerless.blender import botactions
assert bpy.app.timers.is_registered(botactions.repair_common_examples)
assert any(cls.__module__.startswith("hammerless") for cls in bpy.types.Panel.__subclasses__())
botactions.repair_common_examples()  # Now outside the restricted registration context.
addon_utils.disable("hammerless", default_set=False)
assert not bpy.app.timers.is_registered(botactions.repair_common_examples)
assert botactions.repair_common_examples not in bpy.app.handlers.load_post
module = addon_utils.enable("hammerless", default_set=False, handle_error=fail)
assert module is not None and not errors, errors
addon_utils.disable("hammerless", default_set=False)
print("PASS normal addon enable, panels, deferred repair, disable and re-enable")
