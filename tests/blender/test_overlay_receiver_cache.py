import sys,pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]))
import bpy,hammerless
from unittest.mock import patch
hammerless.register()
from hammerless.blender import overlays as ov,extract
for obj in list(bpy.data.objects):bpy.data.objects.remove(obj,do_unlink=True)
objects=[]
for i in range(3):
 bpy.ops.mesh.primitive_cube_add(location=(i*100,0,0))
 obj=bpy.context.object;obj.hammerless.role='BRUSH';objects.append(obj)
mat=bpy.data.materials.new('Receiver material');mat.hammerless.source_material='brick/test';objects[0].data.materials.append(mat)
bpy.context.view_layer.update();ov.receiver_faces(bpy.context)
with patch.object(extract,'mesh_to_brushes',wraps=extract.mesh_to_brushes) as convert:
 objects[1].location.y+=1;bpy.context.view_layer.update();ov.receiver_faces(bpy.context)
 assert [c.args[0] for c in convert.call_args_list]==[objects[1]]
 convert.reset_mock()
 mat.hammerless.source_material='tools/toolsnodraw';bpy.context.view_layer.update();ov.receiver_faces(bpy.context)
 assert [c.args[0] for c in convert.call_args_list]==[objects[0]]
 convert.reset_mock()
 objects[2].hammerless.role='ENTITY';bpy.context.view_layer.update();ov.receiver_faces(bpy.context)
 convert.reset_mock()
 objects[2].location.y+=1;bpy.context.view_layer.update();ov.receiver_faces(bpy.context)
 assert convert.call_count==0
 bpy.data.objects.remove(objects[1],do_unlink=True);bpy.context.view_layer.update()
 faces=ov.receiver_faces(bpy.context)
 assert len(ov._state['receiver_cache'])==1
 assert all(f[2]==objects[0].name for f in faces)
# An unrelated brush drag must not schedule a redraw rebuild at all.
bpy.ops.mesh.primitive_cube_add(location=(100,0,0))
far=bpy.context.object;far.hammerless.role='BRUSH'
bpy.context.view_layer.update();ov.receiver_faces(bpy.context)
ov._state['projection_bounds']=[((-100,-100,-100),(100,100,100))]
ov._state['object_count']=len(bpy.context.scene.objects)
ov._state['dirty']=False
with patch.object(extract,'mesh_to_brushes',wraps=extract.mesh_to_brushes) as convert:
 for i in range(20):
  far.location.x+=.01;bpy.context.view_layer.update()
  assert not ov._state['dirty'], 'Distant transform scheduled preview rebuild'
 assert convert.call_count==0
 # Entering the footprint must update the preview.
 far.location.x=0;bpy.context.view_layer.update()
 assert ov._state['dirty']
 ov.receiver_faces(bpy.context);ov._state['dirty']=False
 # Leaving must also remove its old projection.
 far.location.x=100;bpy.context.view_layer.update()
 assert ov._state['dirty']
print('PER_RECEIVER_CACHE_PASS')
hammerless.unregister()
