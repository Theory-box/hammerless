"""Build and compile the fully editable shade-seeking action in real Blender."""
import sys
import os
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.fearlight import fear_light_example
from hammerless.blender.logicgroups import flatten
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR

tree=bpy.data.node_groups.new('Fear light integration', 'HL_LogicTree')
tree.scene_name=bpy.context.scene.name
action=fear_light_example(tree)
assert action.group.action_supports=='COMMONS'
assert action.group.action_channels=='Movement'
assert not any(getattr(n, "kind", "") in {'SCRIPT_CODE','SCRIPT_VALUE'} for n in action.group.nodes)
assert any(getattr(n, 'kind', '')=='TIME' for n in action.group.nodes)
assert len([n for n in action.group.nodes if getattr(n, 'kind', '')=='BA_COMMAND'])==3
assert next(n for n in tree.nodes if getattr(n, 'kind', '')=='BA_FIND').selection=='COMMONS'
assert next(n for n in tree.nodes if getattr(n, 'kind', '')=='BA_LOOP').inputs['Interval (seconds)'].value==.25
report=SimpleNamespace(warnings=[])
flat=flatten(bpy.context, tree, report)
assert not report.warnings, report.warnings
ir=MapIR()
problems=compile_graph(flat.nodes,flat.links,ir)
assert not problems, problems
script='\n'.join(ir.extra_scripts.values())
for needle in ('Time()', 'next_search', 'movement_owned', 'found_destination', 'best_brightness', '"MOVE"', '"RESET"'):
    assert needle in script, needle
# Export for a real engine test without requiring a saved user scene.
output=os.environ.get('HL_FEAR_LIGHT_OUTPUT')
if output:
    Path(output).write_text(script)
print('FEAR_LIGHT_BLENDER_OK', output or '(no export requested)')

from hammerless.blender.fearlight import HELP, PARAMETERS, HL_OT_fear_light_help
assert action.group["hl_fear_light_score_version"] == 1
assert len(HELP) == len(PARAMETERS)
for uid, name, default in PARAMETERS:
    assert HL_OT_fear_light_help.description(None, SimpleNamespace(field=uid)) == HELP[uid]
    assert abs(action.inputs[name].value-default)<0.00001

# Query registered RNA, not just the Python description callback.
for uid, name, _ in PARAMETERS:
    operator = getattr(bpy.ops.hammerless, 'fear_help_' + uid)
    assert operator.get_rna_type().description == HELP[uid]
    assert len(operator.get_rna_type().description) > len(name) + 30
