import sys,pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]))
import bpy,hammerless
from unittest.mock import patch
hammerless.register()
from hammerless.blender import overlays as ov,extract
for o in list(bpy.data.objects):bpy.data.objects.remove(o,do_unlink=True)
bpy.ops.mesh.primitive_cube_add();near=bpy.context.object;near.name='Near';near.hammerless.role='BRUSH'
for i in range(50):
 bpy.ops.mesh.primitive_cube_add(location=(100+i*3,0,0));bpy.context.object.hammerless.role='BRUSH'
far=bpy.context.object;far.name='Far'
projector=bpy.data.objects.new('Overlay',None);bpy.context.collection.objects.link(projector)
projector.hl_overlay.enabled=True;projector.location=(0,0,2)
projector.hl_overlay.width=128;projector.hl_overlay.height=128;projector.hl_overlay.depth=128
bpy.context.view_layer.update()
with patch.object(extract,'mesh_to_brushes',wraps=extract.mesh_to_brushes) as convert, \
     patch.object(ov,'shader',return_value=None), patch.object(ov,'batch_for_shader',return_value=object()), \
     patch.object(ov,'texture',return_value=(object(),'ALPHA',False,0,'')):
 ov.rebuild(bpy.context)
 assert [c.args[0].name for c in convert.call_args_list]==['Near'],convert.call_args_list
 initial=ov._state['preview_signature'];batch=ov._state['batches'];tex=object();ov._state['textures']['sentinel']=tex
 convert.reset_mock()
 dup=far.copy();dup.data=far.data.copy();bpy.context.collection.objects.link(dup)
 bpy.context.view_layer.update()
 assert ov.preview_signature(bpy.context)==initial,'Far duplicate affected preview'
 # Same validation used by draw after undo, without requiring a GPU context.
 ov.history_pre();ov.history_post()
 assert ov.preview_signature(bpy.context)==initial
 assert ov._state['textures']['sentinel'] is tex
 assert ov._state['batches'] is batch
 assert convert.call_count==0
 # Old relevant receivers leaving the volume still invalidate after undo clears IDs.
 near.location.x=200;bpy.context.view_layer.update()
 assert ov._state['dirty'],'Leaving receiver missed after history'
 assert ov.preview_signature(bpy.context)!=initial
 near.location.x=0;bpy.context.view_layer.update()
 near.data.vertices[0].co.x-=.25;near.data.update();bpy.context.view_layer.update()
 assert ov.preview_signature(bpy.context)!=initial,'Geometry edit missed'
 ov.rebuild(bpy.context)
 assert convert.call_count==1,'Full map converted after history'
 initial=ov._state['preview_signature']
 dup=near.copy();dup.data=near.data.copy();bpy.context.collection.objects.link(dup);bpy.context.view_layer.update()
 assert ov.preview_signature(bpy.context)!=initial,'Nearby duplicate missed'
 bpy.data.objects.remove(dup,do_unlink=True);bpy.context.view_layer.update()
 assert ov.preview_signature(bpy.context)==initial
 projector.hl_overlay.width+=1
 assert ov.preview_signature(bpy.context)!=initial,'Overlay settings missed'
# Exercise actual Blender undo/redo, which replaces datablock IDs.
projector.hl_overlay.width-=1
bpy.context.view_layer.update()
initial=ov.preview_signature(bpy.context)
for o in bpy.context.selected_objects:o.select_set(False)
far.select_set(True);bpy.context.view_layer.objects.active=far
bpy.ops.ed.undo_push(message='Before far duplicate')
bpy.ops.object.duplicate()
bpy.ops.ed.undo_push(message='After far duplicate')
with patch.object(extract,'mesh_to_brushes',wraps=extract.mesh_to_brushes) as convert:
 for i in range(3):
  assert bpy.ops.ed.undo()=={'FINISHED'}
  assert ov._state['check_needed']
  assert ov.preview_signature(bpy.context)==initial
  assert bpy.ops.ed.redo()=={'FINISHED'}
  assert ov.preview_signature(bpy.context)==initial
 assert convert.call_count==0
print('OVERLAY_HISTORY_PASS')
hammerless.unregister()
