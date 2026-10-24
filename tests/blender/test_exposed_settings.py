"""Real Blender per-object settings, duplication, export and persistence checks."""
import sys,tempfile
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logic_examples import all_examples,build_example
from hammerless.blender.extract import extract_scene
from hammerless.blender.logic import compile_logic
from hammerless.blender import exposed
from hammerless.core.build import Report
index=next(i for i,e in enumerate(all_examples()) if e['title']=='Purchase - Any Weapon or Item')
problems=[];build_example(bpy.context,index,problems)
assert not problems,problems
obj=next(o for o in bpy.context.scene.objects if o.name.endswith('purchase_display'))
slot=obj.hammerless.behaviours[0];graph=slot.graph
assert len(slot.values)==4,(len(slot.values),[n.bl_idname for n in graph.nodes])
price=graph.nodes['price_setting'];item=graph.nodes['item_setting'];title=graph.nodes['title_setting']
slot.values[price.setting_id].integer=333
slot.values[item.setting_id].text='rifle_ak47'
copy=obj.copy();copy.name='Other station';obj.users_collection[0].objects.link(copy)
other=copy.hammerless.behaviours[0]
assert other.graph==graph
assert other.values[price.setting_id].integer==333
other.values[price.setting_id].integer=777
other.values[item.setting_id].text='first_aid_kit'
assert slot.values[price.setting_id].integer==333
assert slot.values[item.setting_id].text=='rifle_ak47'
# Labels and dropdown order must not remap saved values.
price.ui_name='Cost'
item.choices=';'.join(reversed(item.choices.split(';')))
assert exposed.resolve(price,slot)==333 and exposed.resolve(item,slot)=='rifle_ak47'
assert exposed.resolve(item,other)=='first_aid_kit'
# Defaults follow the graph only when not overridden.
title.default_text='Supplies'
assert exposed.resolve(title,slot)=='Supplies'
other.values[title.setting_id].text='Other shop';title.default_text='Shop'
assert exposed.resolve(title,slot)=='Shop' and exposed.resolve(title,other)=='Other shop'
report=Report();ir,_=extract_scene(bpy.context,report,defer_textures=True);compile_logic(bpy.context,ir,report)
assert not report.errors,report.errors
assert not report.warnings,report.warnings
script='\n'.join(ir.extra_scripts.values())
assert 'weapon = "rifle_ak47", title = "Shop", price = 333' in script,script[-2000:]
assert 'weapon = "first_aid_kit", title = "Other shop", price = 777' in script
# Graph copies keep their interface IDs; the object can retain its overrides.
graph_copy=graph.copy();other.graph=graph_copy
assert exposed.resolve(graph_copy.nodes['price_setting'],other)==777
# Deleted definitions do not destroy saved overrides. Restored schema ID recovers the value.
uid=graph_copy.nodes['price_setting'].setting_id
graph_copy.nodes.remove(graph_copy.nodes['price_setting'])
exposed.sync_slot(other);assert other.values.get(uid) is not None
restored=graph_copy.nodes.new('HL_NodeExposedSetting');restored.value_type='INT';restored.setting_id=uid
exposed.sync_slot(other);assert exposed.resolve(restored,other)==777
# Remove deleted-test copy; check real file persistence on original shared schema.
bpy.data.objects.remove(copy,do_unlink=True)
name=obj.name;graph_name=graph.name;price_id=price.setting_id;item_id=item.setting_id
with tempfile.TemporaryDirectory() as tmp:
    path=str(Path(tmp)/'exposed.blend');bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    loaded=bpy.data.objects[name].hammerless.behaviours[0]
    assert loaded.values[price_id].integer==333
    assert loaded.values[item_id].text=='rifle_ak47'
    assert loaded.graph==bpy.data.node_groups[graph_name]
print('PASS independent duplicate overrides, two station exports, schema rename/reorder, defaults, graph copy, deletion recovery, save/reload')
hammerless.unregister()
