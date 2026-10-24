"""Compile real editable examples, independent copies and save/reload."""
import sys
import tempfile
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logic_examples import all_examples, build_example
from hammerless.blender.logic import compile_logic
from hammerless.blender.extract import extract_scene
from hammerless.core.build import Report

for title in ('Personal Kill Points', 'Purchase - Any Weapon or Item'):
    problems = []
    build_example(bpy.context, next(i for i,e in enumerate(all_examples()) if e['title']==title), problems)
    assert not problems, problems
assert not any(n.bl_idname in ('HL_NodePersonalPoints','HL_NodeWallWeapon','HL_NodeScriptCode','HL_NodeScriptValue')
               for g in bpy.data.node_groups if g.bl_idname=='HL_LogicTree' for n in g.nodes)
obj = next(o for o in bpy.context.scene.objects if o.name.endswith('purchase_display'))
slot = obj.hammerless.behaviours[0]
slot.preview_enabled = False
graph = slot.graph
slot.values[graph.nodes['item_setting'].setting_id].choice = 'rifle_ak47'
copy = obj.copy()
bpy.context.scene.collection.objects.link(copy)
copy.hammerless.behaviours[0].values[graph.nodes['price_setting'].setting_id].integer = 900
assert slot.values[graph.nodes['price_setting'].setting_id].integer == 250

def check():
    report=Report()
    ir,_=extract_scene(bpy.context,report,defer_textures=True)
    compile_logic(bpy.context,ir,report)
    assert not report.errors,report.errors
    script='\n'.join(ir.extra_scripts.values())
    assert 'HL_GiveInventory' in script and 'HL_KeySet' in script
    assert 'HL_WallBuy' not in script and 'HL_PointsAward' not in script
    assert 'rifle_ak47' in script
    assert script.count('HL_Interactions.append(')==2
    return script

script=check()
if '--script-out' in sys.argv:
    Path(sys.argv[sys.argv.index('--script-out')+1]).write_text(script,encoding='utf-8')
with tempfile.TemporaryDirectory() as tmp:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(tmp)/'modular.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(Path(tmp)/'modular.blend'))
    check()
print('PASS editable groups, separate object settings, compile, save/reload')
hammerless.unregister()
