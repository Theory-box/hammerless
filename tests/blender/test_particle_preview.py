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

# Placing uses the normal exportable entity and leaves unrelated objects intact.
wm=bpy.context.window_manager
assert wm.hl_pfx_autoplay
wm.hl_pfx_autoplay=False
bpy.context.scene.cursor.location=(2,3,4)
assert bpy.ops.hammerless.place_particle(effect='gas_smoke')=={'FINISHED'}
obj=bpy.context.active_object
assert obj.hammerless.role=='ENTITY' and obj.hammerless.classname=='info_particle_system'
keys={k.key:k.value for k in obj.hammerless.keyvalues}
assert keys['effect_name']=='gas_smoke' and keys['start_active']=='1'
assert tuple(obj.location)==(2,3,4)
wm.hl_pfx_replace=True
assert bpy.ops.hammerless.place_particle(effect='small_sparks')=={'FINISHED'}
assert bpy.context.active_object==obj
assert len(bpy.data.objects)==before[0]+1
assert next(k.value for k in obj.hammerless.keyvalues if k.key=='effect_name')=='small_sparks'
print('PASS particle placement, replace, default game activation and scene preservation')
