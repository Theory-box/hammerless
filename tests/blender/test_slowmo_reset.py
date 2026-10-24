"""Export round cleanup and gate late headshots using ordinary nodes."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logic_examples import all_examples, build_example
from hammerless.blender.logic import compile_logic
from hammerless.blender.extract import extract_scene
from hammerless.core.build import Report
problems=[]
tree=build_example(bpy.context,next(i for i,e in enumerate(all_examples()) if e['title']=='Slow Motion Headshots'),problems)
assert not problems,problems
report=Report()
ir,_=extract_scene(bpy.context,report,defer_textures=True)
compile_logic(bpy.context,ir,report)
assert not report.errors, report.errors
script='\n'.join(ir.extra_scripts.values())
for event in ('round_start','round_end','mission_lost'):
    assert 'OnGameEvent_'+event in script,event
    handler='\n'.join(ir.logic_script_events[event])
    assert '"Reset"' in handler and '"CancelPending"' in handler,handler
    assert ('"Enable"' if event=='round_start' else '"Disable"') in handler,handler
    assert '"Start"' not in handler,handler
gate=next(e for e in ir.entities if e.keyvalues.get('targetname','').endswith('_active'))
assert gate.keyvalues['StartDisabled']=='1'
assert any(o.input=='Start' for o in gate.outputs)
assert all(e is gate for e in ir.entities for o in e.outputs if o.input=='Start')
assert any(o.input=='Reset' for e in ir.entities for o in e.outputs)
assert any(o.input=='CancelPending' for e in ir.entities for o in e.outputs)
print('PASS slow-motion reset export')
