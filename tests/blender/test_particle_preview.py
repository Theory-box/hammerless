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

assert obj.type=='EMPTY'
from hammerless.blender.particlebrowser import particle_empty
from hammerless.blender.extract import extract_scene
from hammerless.core.build import Report
mesh=bpy.data.meshes.new('Legacy particle planes');mesh['hl_particle']='gas_smoke'
legacy=bpy.data.objects.new('Legacy smoke',mesh);bpy.context.scene.collection.objects.link(legacy)
legacy.hammerless.role='ENTITY';legacy.hammerless.classname='info_particle_system'
for k in legacy.hammerless.keyvalues:
    if k.key=='effect_name':k.value='gas_smoke'
legacy.location=(7,8,9);legacy['custom_value']=42
parent=bpy.data.objects.new('Parent',None);bpy.context.scene.collection.objects.link(parent);legacy.parent=parent
child=bpy.data.objects.new('Child',None);bpy.context.scene.collection.objects.link(child);child.parent=legacy
marker=particle_empty(legacy)
assert marker.type=='EMPTY' and tuple(marker.location)==(7,8,9)
assert marker.parent==parent and child.parent==marker and marker['custom_value']==42
assert marker.hammerless.classname=='info_particle_system'
assert next(k.value for k in marker.hammerless.keyvalues if k.key=='effect_name')=='gas_smoke'
bpy.context.view_layer.update()
ir,_=extract_scene(bpy.context,Report())
assert any(e.classname=='info_particle_system' and e.keyvalues.get('effect_name')=='gas_smoke' for e in ir.entities)
print('PASS legacy plane migration preserves properties/parent/references; empty exports particle entity')


# No preview CPU, traces, scene migration or drawing while a build owns resources.
from hammerless.blender import ops
from unittest.mock import Mock
fake=Mock();particlepreview._state.update(effect_simulation=fake,last=0)
ops._BUILDING.add('particle-test-build')
try:
    assert particlepreview.work_busy()
    assert particlepreview.tick()==.25
    assert particlepreview.watch_selection()==.25
    particlepreview.draw()
    fake.advance.assert_not_called()
finally:
    ops._BUILDING.discard('particle-test-build');particlepreview._state.clear()
assert not particlepreview.work_busy()
assert all(not e.brushes for e in ir.entities if e.classname=='info_particle_system')
print('PASS particle previews suspend during builds; particle entities have no bake brush geometry')
