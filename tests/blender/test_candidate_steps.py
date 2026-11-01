"""Actual registered node, editable rule, action flatten/export and SQVM parse."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import candidates
from hammerless.blender.botactions import new_action
from hammerless.blender.logicgroups import flatten
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR

def socket(n,key,out=False):
    return next(s for s in (n.outputs if out else n.inputs) if s.identifier==key)
def wire(tree,a,out,b,inp):tree.links.new(socket(a,out,True),socket(b,inp))

group=new_action('Stepped candidate test')
entry=next(n for n in group.nodes if n.kind=='BA_ENTRY')
step=group.nodes.new('HL_StepCandidates');step.list_type='bots'
assert socket(step,'enabled').value is True
assert socket(step,'reset').value is False
assert socket(step,'budget').value==8
assert socket(step,'item',True).bl_idname=='HL_BotSocket'
step.list_type='navareas';assert socket(step,'item',True).bl_idname=='HL_NavAreaSocket'
step.list_type='bots'
find=group.nodes.new('HL_BAFind');find.selection='COMMONS'
rule=group.nodes.new('HL_CandidateRule');rule.list_type='bots'
context=SimpleNamespace(space_data=SimpleNamespace(edit_tree=group))
assert candidates.HL_OT_candidate_rule_new.execute(SimpleNamespace(node_name=rule.name),context)=={'FINISHED'}
wire(group,entry,'start',step,'run');wire(group,entry,'update',step,'run')
wire(group,find,'bots',step,'items');wire(group,rule,'rule',step,'rule')

tree=bpy.data.node_groups.new('Stepped test map','HL_LogicTree');tree.scene_name=bpy.context.scene.name
start,selection,loop,apply,action=[tree.nodes.new(k) for k in ('HL_NodeMapStart','HL_BAFind','HL_BALoop','HL_BAApply','HL_BAAction')]
selection.selection='COMMONS';action.group=group
for a,out,b,inp in ((start,'start',loop,'start'),(selection,'bots',loop,'bots'),(loop,'each',apply,'run'),
                    (loop,'current',apply,'bot'),(action,'action',apply,'action')):wire(tree,a,out,b,inp)
report=SimpleNamespace(warnings=[]);flat=flatten(bpy.context,tree,report)
assert not report.warnings,report.warnings
ir=MapIR();errors=compile_graph(flat.nodes,flat.links,ir);assert not errors,errors
script='\n'.join(ir.extra_scripts.values())
assert 'HLCE.Step(ctx,' in script and 'function() { return {items=' in script
print('CANDIDATE_STEPS_BLENDER_OK')
if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
    import squirrel
    squirrel.SQVM().execute('compilestring('+json.dumps(script)+');')
    print('CANDIDATE_STEPS_GENERATED_SQUIRREL_OK')
