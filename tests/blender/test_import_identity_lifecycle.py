import bpy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import vmfimport as vi
from hammerless.blender.entitycontext import entity_object
s=bpy.context.scene
text=bpy.data.texts.new('identity source');s[vi.SOURCE_KEY]=text.name
owner=bpy.data.objects.new('Owner',None);s.collection.objects.link(owner)
owner[vi.KIND]='entity';owner[vi.INDEX]=0;owner[vi.NAME]=owner.name
mesh=bpy.data.meshes.new('Brush');brush=bpy.data.objects.new('Brush',mesh);s.collection.objects.link(brush)
brush[vi.KIND]='solid';brush[vi.ID]='12';brush[vi.NAME]=brush.name;brush['hl_vmf_owner_index']=0
vi.seed_original_objects(s,[owner,brush])
assert entity_object(bpy.context,brush)==owner
copy=brush.copy();s.collection.objects.link(copy);copy.name='Copied Brush'
assert entity_object(bpy.context,copy)==copy
brush.name='Renamed original';assert vi._originals(list(s.objects),'solid')['12']==brush
bpy.data.objects.remove(brush,do_unlink=True)
assert '12' not in vi._originals(list(s.objects),'solid')
assert entity_object(bpy.context,copy)==copy
# A duplicate entity cannot take the deleted entity's place either.
oc=owner.copy();s.collection.objects.link(oc);oc.name='Copied Owner'
bpy.data.objects.remove(owner,do_unlink=True)
assert not vi._originals(list(s.objects),'entity',vi.INDEX)
import tempfile
tempdir=tempfile.TemporaryDirectory()
path=str(Path(tempdir.name)/'identity.blend')
bpy.ops.wm.save_as_mainfile(filepath=path)
bpy.ops.wm.open_mainfile(filepath=path)
assert not vi._originals(list(bpy.context.scene.objects),'entity',vi.INDEX)
assert not vi._originals(list(bpy.context.scene.objects),'solid')
print('DUPLICATE_DELETE_RENAME_RELOAD_OK',flush=True)
# Exercise Blender's actual undo stack.
bpy.context.preferences.edit.use_global_undo=True
obj=bpy.data.objects.new('Undo original',None);bpy.context.scene.collection.objects.link(obj)
obj[vi.KIND]='entity';obj[vi.INDEX]=5;obj[vi.NAME]=obj.name
vi.seed_original_objects(bpy.context.scene,[obj])
for o in bpy.context.selected_objects:o.select_set(False)
obj.select_set(True);bpy.context.view_layer.objects.active=obj
bpy.ops.ed.undo_push(message='Original')
bpy.ops.object.duplicate();bpy.ops.ed.undo_push(message='Duplicate')
assert vi._originals(list(bpy.context.scene.objects),'entity',vi.INDEX)[5].name=='Undo original'
bpy.ops.ed.undo()
assert vi._originals(list(bpy.context.scene.objects),'entity',vi.INDEX)[5].name=='Undo original'
bpy.ops.ed.redo()
assert vi._originals(list(bpy.context.scene.objects),'entity',vi.INDEX)[5].name=='Undo original'
print('UNDO_REDO_DUPLICATE_OK',flush=True)
# Delete the original through an undoable Blender operator, with a duplicate remaining.
for o in bpy.context.selected_objects:o.select_set(False)
original=bpy.data.objects['Undo original'];original.select_set(True);bpy.context.view_layer.objects.active=original
bpy.ops.object.delete();bpy.ops.ed.undo_push(message='Delete original')
assert 5 not in vi._originals(list(bpy.context.scene.objects),'entity',vi.INDEX)
bpy.ops.ed.undo()
assert vi._originals(list(bpy.context.scene.objects),'entity',vi.INDEX)[5].name=='Undo original'
bpy.ops.ed.redo()
assert 5 not in vi._originals(list(bpy.context.scene.objects),'entity',vi.INDEX)
print('UNDO_REDO_DELETE_OK',flush=True)

tempdir.cleanup()
