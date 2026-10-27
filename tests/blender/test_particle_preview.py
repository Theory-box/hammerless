"""Registration/lifecycle check; GPU drawing requires an interactive viewport."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy, gpu, hammerless
hammerless.register()
from hammerless.blender import particlepreview
assert hasattr(bpy.ops.hammerless,'particle_preview')
for api in ('blend_get','depth_test_get','depth_mask_get'):
    assert hasattr(gpu.state,api),api
before=(len(bpy.data.objects),len(bpy.data.meshes),bpy.context.scene.cursor.location.copy())
particlepreview.stop()
particlepreview.on_load()
assert before==(len(bpy.data.objects),len(bpy.data.meshes),bpy.context.scene.cursor.location.copy())
assert not bpy.app.timers.is_registered(particlepreview.tick)
hammerless.unregister()
assert particlepreview.on_load not in bpy.app.handlers.load_pre
hammerless.register()
assert bpy.app.handlers.load_pre.count(particlepreview.on_load)==1
print('PASS particle preview registration, cleanup, no scene mutation; GPU rendering not exercised')
