import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logic_examples import all_examples,build_example
from hammerless.core.wall_weapon import ITEMS
from hammerless.blender import exposed
index=next(i for i,e in enumerate(all_examples()) if e['title']=='Purchase - Any Weapon or Item')
problems=[];build_example(bpy.context,index,problems);assert not problems,problems
obj=next(o for o in bpy.context.scene.objects if o.name.endswith('purchase_display'))
slot=obj.hammerless.behaviours[0];node=slot.graph.nodes['item_setting'];uid=node.setting_id
copy=obj.copy();obj.users_collection[0].objects.link(copy)
original_mesh=copy.data;matrix=obj.matrix_world.copy()
slot.values[uid].choice='rifle_ak47'
assert not slot.preview_error,slot.preview_error
assert next(k.value for k in obj.hammerless.keyvalues if k.key=='model')==ITEMS['rifle_ak47']['model']
assert obj.data!=original_mesh and copy.data==original_mesh
assert obj.matrix_world==matrix
# Verify the real preview mesh when game assets are available, not merely the export keyvalue.
assert obj.data.get('hl_model')==ITEMS['rifle_ak47']['model'],dict(obj.data.items())
assert copy.hammerless.behaviours[0].values[uid].text=='first_aid_kit'
slot.preview_enabled=False
mesh=obj.data
slot.values[uid].choice='pumpshotgun'
assert obj.data==mesh
slot.preview_enabled=True
assert obj.data.get('hl_model')==ITEMS['pumpshotgun']['model']
slot.values[uid].overridden=False;exposed.sync_slot(slot);exposed.update_preview(slot)
assert obj.data.get('hl_model')==ITEMS['first_aid_kit']['model']
# AUTO derives the item from the display and must not rewrite it.
slot.values[uid].choice='AUTO';assert obj.data.get('hl_model')==ITEMS['first_aid_kit']['model']
name=obj.name
bpy.context.preferences.edit.use_global_undo=True
bpy.ops.ed.undo_push(message='Before model swap')
slot.values[uid].choice='rifle_ak47'
bpy.ops.ed.undo_push(message='After model swap')
bpy.ops.ed.undo()
assert bpy.data.objects[name].data.get('hl_model')==ITEMS['first_aid_kit']['model']
bpy.ops.ed.redo()
assert bpy.data.objects[name].data.get('hl_model')==ITEMS['rifle_ak47']['model']
# Copy-to-selected must apply the default item and preserve custom-display opt-out.
source=bpy.data.objects[name].hammerless.behaviours[0]
source.values[uid].overridden=False
exposed.sync_slot(source)
target_obj=bpy.data.objects.new('Copy target',bpy.data.meshes.new('Custom display'))
bpy.context.collection.objects.link(target_obj)
target=target_obj.hammerless.behaviours.add();target.graph=source.graph
exposed.copy_values(source,target)
assert target_obj.data.get('hl_model')==ITEMS['first_aid_kit']['model']
source.preview_enabled=False
source.values[uid].choice='pumpshotgun'
mesh=target_obj.data
exposed.copy_values(source,target)
assert not target.preview_enabled and target_obj.data==mesh
assert target.values[uid].overridden and target.values[uid].text=='pumpshotgun'
assert exposed.resolve(source.graph.nodes['item_setting'],target)=='pumpshotgun'
print('PASS copy defaults and custom-display preference')
print('PASS model-swap undo/redo')
print('PASS real model swaps, shared-mesh duplicate isolation, transform preservation, opt-out, re-enable and reset')
hammerless.unregister()

