"""Paid barrier compilation, independent copies, navigation and persistence."""
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

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for title in ('Personal Kill Points', 'Purchase - Remove Barrier'):
    problems=[]
    build_example(bpy.context,next(i for i,e in enumerate(all_examples()) if e['title']==title),problems)
    assert not problems,problems
obj=next(o for o in bpy.context.scene.objects if o.name.endswith('barrier'))
slot=obj.hammerless.behaviours[0]
graph=slot.graph
copy=obj.copy()
copy.data=obj.data.copy()
bpy.context.scene.collection.objects.link(copy)
copy.location.x+=5
copy.hammerless.behaviours[0].values[graph.nodes['price_setting'].setting_id].integer=250
assert slot.values[graph.nodes['price_setting'].setting_id].integer==1000
assert not any(n.bl_idname in ('HL_NodeScriptCode','HL_NodeScriptValue')
               for g in bpy.data.node_groups if g.bl_idname=='HL_LogicTree' for n in g.nodes)

def check():
    report=Report()
    ir,_=extract_scene(bpy.context,report,defer_textures=True)
    compile_logic(bpy.context,ir,report)
    assert not report.errors,report.errors
    brushes=[e for e in ir.entities if e.classname=='func_brush']
    blockers=[e for e in ir.entities if e.classname=='func_nav_blocker']
    assert len(brushes)==len(blockers)==2,(len(brushes),len(blockers))
    script='\n'.join(ir.extra_scripts.values())
    assert script.count('HL_Interactions.append(')==2
    assert '1000' in script and '250' in script
    assert 'UnblockNav' in script and 'Kill' in script
    for e in blockers:
        assert any(o.target==e.keyvalues['targetname'] and o.input=='BlockNav'
                   for auto in ir.entities for o in auto.outputs)
    return script,brushes

script,brushes=check()
if '--script-out' in sys.argv:
    import json
    dest=Path(sys.argv[sys.argv.index('--script-out')+1])
    dest.write_text(script,encoding='utf-8')
    dest.with_suffix('.json').write_text(json.dumps([e.keyvalues['targetname'] for e in brushes]))
with tempfile.TemporaryDirectory() as tmp:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(tmp)/'barrier.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(Path(tmp)/'barrier.blend'))
    check()
print('PASS paid barrier groups, duplicate settings, navigation and save/reload')
hammerless.unregister()
