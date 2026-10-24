import sys,tempfile
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logic_examples import all_examples,build_example
from hammerless.blender.extract import extract_scene
from hammerless.core.build import Report
index=next(i for i,e in enumerate(all_examples()) if e['title']=='Purchase - Any Weapon or Item')
problems=[]
build_example(bpy.context,index,problems)
assert not problems,problems
obj=next(o for o in bpy.context.scene.objects if o.name.endswith('purchase_display'))
graph=obj.hammerless.behaviours[0].graph
node=graph.nodes['buy']
assert node.item=='first_aid_kit'
slot=obj.hammerless.behaviours[0]
slot.values[graph.nodes['item_setting'].setting_id].choice='rifle_ak47'
slot.values[graph.nodes['title_setting'].setting_id].text='AK Shop'
ir,_=extract_scene(bpy.context,Report(),defer_textures=True)
from hammerless.blender.logic import compile_logic
report=Report();compile_logic(bpy.context,ir,report)
assert not report.errors,report.errors
assert any('weapon = "rifle_ak47"' in v for v in ir.extra_scripts.values())
assert any('PrecacheEntityFromTable' in v for v in ir.extra_scripts.values())
graph_name=graph.name
with tempfile.TemporaryDirectory() as tmp:
    path=str(Path(tmp)/'generic.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    loaded=bpy.data.node_groups[graph_name]
    slot=next(o for o in bpy.context.scene.objects if o.name.endswith('purchase_display')).hammerless.behaviours[0]
    assert slot.values[loaded.nodes['item_setting'].setting_id].text=='rifle_ak47'
print('PASS generic example, independent item selection, export, precache, save/reload')
hammerless.unregister()

