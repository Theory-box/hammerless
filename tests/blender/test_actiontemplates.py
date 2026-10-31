"""Create editable action templates and compile them as live bot loop inputs."""
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.actiontemplates import wait_action, move_action, WAIT_PARAMETERS, MOVE_PARAMETERS
from hammerless.blender.logicgroups import flatten
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR

for factory, parameters, expected in ((wait_action, WAIT_PARAMETERS, ('deadline', 'SUCCESS')),
                                     (move_action, MOVE_PARAMETERS, ('MOVE', 'RESET', 'SUCCESS', 'FAILED'))):
    tree = bpy.data.node_groups.new('Template test', 'HL_LogicTree')
    tree.scene_name = bpy.context.scene.name
    start, find, loop, action, apply = [tree.nodes.new(k) for k in
        ('HL_NodeMapStart', 'HL_BAFind', 'HL_BALoop', 'HL_BAAction', 'HL_BAApply')]
    find.selection = 'COMMONS'
    action.group = factory()
    assert not any(getattr(n, 'kind', '') in {'SCRIPT_CODE', 'SCRIPT_VALUE'} for n in action.group.nodes)
    for uid, label, kind, default in parameters:
        next(s for s in action.inputs if s.identifier == uid).value = default
    for a, out, b, inp in ((start, 'On Map Start', loop, 'Start'), (find, 'Bots', loop, 'Bots'),
                         (loop, 'Each Bot', apply, 'Run'), (loop, 'Current Bot', apply, 'Bot'),
                         (action, 'Action', apply, 'Action')):
        tree.links.new(a.outputs[out], b.inputs[inp])
    report = SimpleNamespace(warnings=[])
    flat = flatten(bpy.context, tree, report)
    assert not report.warnings, report.warnings
    ir = MapIR()
    problems = compile_graph(flat.nodes, flat.links, ir)
    assert not problems, problems
    script = '\n'.join(ir.extra_scripts.values())
    for token in expected:
        assert token in script, token
    if factory == wait_action:
        assert not action.group.action_channels
    else:
        assert action.group.action_channels == 'Movement'
        assert action.group.action_supports == 'COMMONS'
print('ACTION_TEMPLATES_BLENDER_OK')
