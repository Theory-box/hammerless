import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import exposed
obj=bpy.context.object
obj.name='Setting Owner'
graph=bpy.data.node_groups.new('Settings','HL_LogicTree');graph.is_behaviour=True
node=graph.nodes.new('HL_NodeExposedSetting');node.value_type='ENUM';node.choices='a=Alpha;b=Beta';node.default_text='a'
uid=node.setting_id
slot=obj.hammerless.behaviours.add();slot.graph=graph
slot.values[uid].choice='b'
assert slot.values[uid].text=='b'
bpy.context.preferences.edit.use_global_undo=True
bpy.ops.ed.undo_push(message='Before duplicate')
bpy.ops.object.duplicate()
duplicate=bpy.context.object;duplicate.name='Duplicated Owner'
duplicate.hammerless.behaviours[0].values[uid].choice='a'
bpy.ops.ed.undo_push(message='Duplicate with own setting')
assert obj.hammerless.behaviours[0].values[uid].text=='b'
bpy.ops.ed.undo()
assert bpy.data.objects.get('Duplicated Owner') is None
assert bpy.data.objects['Setting Owner'].hammerless.behaviours[0].values[uid].text=='b'
bpy.ops.ed.redo()
assert bpy.data.objects['Duplicated Owner'].hammerless.behaviours[0].values[uid].text=='a'
assert bpy.data.objects['Setting Owner'].hammerless.behaviours[0].values[uid].text=='b'
# Delete then undo the object, preserving its local value.
bpy.ops.object.delete()
bpy.ops.ed.undo_push(message='Deleted duplicate')
bpy.ops.ed.undo()
assert bpy.data.objects['Duplicated Owner'].hammerless.behaviours[0].values[uid].text=='a'
print('PASS actual Object Mode duplication, dropdown selection, undo/redo, delete/undo')
hammerless.unregister()
