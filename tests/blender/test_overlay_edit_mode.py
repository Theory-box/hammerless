import pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]))
import bpy,bmesh,hammerless
from unittest.mock import patch
hammerless.register()
from hammerless.blender import overlays as ov,extract
for obj in list(bpy.data.objects):bpy.data.objects.remove(obj,do_unlink=True)
bpy.ops.mesh.primitive_cube_add(location=(100,0,0));obj=bpy.context.object;obj.hammerless.role='BRUSH'
bpy.context.view_layer.update();ov.receiver_faces(bpy.context)
ov._state.update(projection_bounds=[((-100,-100,-100),(100,100,100))],object_count=len(bpy.context.scene.objects),dirty=False)
bpy.ops.object.mode_set(mode='EDIT');bm=bmesh.from_edit_mesh(obj.data)
def move(delta):
 for v in bm.verts:v.co.x+=delta
 bmesh.update_edit_mesh(obj.data);bpy.context.view_layer.update()
with patch.object(extract,'mesh_to_brushes',wraps=extract.mesh_to_brushes) as convert:
 for i in range(20):
  move(.001)
  assert not ov._state['dirty'],'Unrelated edit scheduled rebuild'
 assert convert.call_count==0
 move(-100)
 assert ov._state['dirty'],'Edit entering footprint not detected'
 ov.receiver_faces(bpy.context);ov._state['dirty']=False
 move(100)
 assert ov._state['dirty'],'Edit leaving footprint not detected'
bpy.ops.object.mode_set(mode='OBJECT')
linked=obj.copy();linked.data=obj.data;linked.location.x=0;bpy.context.collection.objects.link(linked)
bpy.context.view_layer.update();ov.receiver_faces(bpy.context)
ov._state.update(object_count=len(bpy.context.scene.objects),dirty=False)
bpy.ops.object.mode_set(mode='EDIT');bm=bmesh.from_edit_mesh(obj.data)
move(.01)
assert ov._state['dirty'],'Shared mesh user under decal not invalidated'
bpy.ops.object.mode_set(mode='OBJECT')
print('OVERLAY_EDIT_MODE_PASS')
hammerless.unregister()
