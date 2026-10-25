"""Build, duplicate, compile and reload the emergency knife example."""
import sys, tempfile
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logic_examples import all_examples, build_example
from hammerless.blender.logic import compile_logic
from hammerless.blender.extract import extract_scene
from hammerless.core.build import Report

for title in ('Personal Kill Points', 'Purchase - Emergency Knife'):
    problems=[]
    build_example(bpy.context, next(i for i,e in enumerate(all_examples()) if e['title']==title),problems)
    assert not problems,problems
obj=next(o for o in bpy.context.scene.objects if o.name.endswith('emergency_knife'))
slot=obj.hammerless.behaviours[0]
slot.preview_enabled=False
graph=slot.graph
graph_name=graph.name
other=obj.copy()
bpy.context.scene.collection.objects.link(other)
other.hammerless.behaviours[0].values[graph.nodes['price_setting'].setting_id].integer=750
assert slot.values[graph.nodes['price_setting'].setting_id].integer==500
for key in ('jockey', 'charger'):
    other.hammerless.behaviours[0].values[graph.nodes[key+'_setting'].setting_id].boolean=True
    assert not slot.values[graph.nodes[key+'_setting'].setting_id].boolean

def check():
    graph=bpy.data.node_groups[graph_name]
    shops=[o.hammerless.behaviours[0] for o in bpy.context.scene.objects
           if o.hammerless.behaviours and o.hammerless.behaviours[0].graph==graph]
    assert sorted(s.values[graph.nodes['charger_setting'].setting_id].boolean for s in shops)==[False,True]
    report=Report()
    ir,_=extract_scene(bpy.context,report,defer_textures=True)
    compile_logic(bpy.context,ir,report)
    assert not report.errors,report.errors
    script='\n'.join(ir.extra_scripts.values())
    assert script.count('::HL_KnifeUsers <- {}')==1
    assert script.count('HL_Interactions.append(')==2
    assert 'HL_KnifeCount(' in script and 'HL_KnifeGive(' in script
    assert 'Already carrying 2/2 emergency knives' in script
    assert 'Hold RELOAD while pinned' in script
    return script

source=check()
if '--script-out' in sys.argv:
    Path(sys.argv[sys.argv.index('--script-out')+1]).write_text(source,encoding='utf8')
with tempfile.TemporaryDirectory() as temp:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(temp)/'knife.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(Path(temp)/'knife.blend'))
    check()
print('PASS emergency knife behaviour, independent shop settings, shared runtime, save/reload')
hammerless.unregister()
