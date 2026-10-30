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
assert action.group.action_supports=='SPECIALS'
assert action.group.action_channels=='Movement'
assert not any(getattr(n, "kind", "") in {'SCRIPT_CODE','SCRIPT_VALUE'} for n in action.group.nodes)
assert any(getattr(n, 'kind', '')=='TIME' for n in action.group.nodes)
assert len([n for n in action.group.nodes if getattr(n, 'kind', '')=='BA_COMMAND'])==2
assert next(n for n in tree.nodes if getattr(n, 'kind', '')=='BA_FIND').selection=='SPECIALS'
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
