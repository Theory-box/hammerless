import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hammerless.blender import props, collisiontool
props.register(); collisiontool.register()
bpy.context.preferences.edit.use_global_undo=True
bpy.ops.mesh.primitive_cube_add()
src=bpy.context.object; src.name='Source'; src.location=(2,3,4); src.scale=(1,2,3)
src.hammerless.brush_algorithm='EOF'
bpy.context.view_layer.update()
assert bpy.ops.hammerless.make_collision()=={'FINISHED'}
out=collisiontool._outputs(src,False)[0]
assert out.parent is None
assert out.hammerless.brush_detail == "WORLD"
assert bpy.data.objects.get('Source') is src
assert out.matrix_world.is_identity
print('PASS: keep source, independent world-space output',flush=True)
# Remove prior result, then test the removal conversion as a single undo state.
bpy.data.objects.remove(out,do_unlink=True)
src.hammerless.brush_remove_original=True
bpy.ops.ed.undo_push(message='Before conversion')
assert bpy.ops.hammerless.make_collision()=={'FINISHED'}
assert bpy.data.objects.get('Source') is None
out=bpy.context.object
assert out.parent is None and out.get(collisiontool.OF) is None
assert out.select_get()
bpy.context.view_layer.update()
# Background Python does not have the interactive operator event loop's undo push.
bpy.ops.ed.undo_push(message='Convert to Brushes')
assert bpy.ops.ed.undo()=={'FINISHED'}
assert bpy.data.objects.get('Source') is not None
assert bpy.data.objects.get('Source Collision') is None
assert bpy.context.object.name=='Source'
print('PASS: undo restores original and selection, removes output',flush=True)
assert bpy.ops.ed.redo()=={'FINISHED'}
assert bpy.data.objects.get('Source') is None
assert bpy.data.objects.get('Source Collision') is not None
print('PASS: redo restores conversion',flush=True)
