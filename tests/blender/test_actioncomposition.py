"""Verify composition sockets, nested asset flattening and normal registration."""
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.actiontemplates import wait_action,move_action
from hammerless.blender.logicgroups import flatten
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR
tree=bpy.data.node_groups.new('Composition test','HL_LogicTree');tree.scene_name=bpy.context.scene.name
wait=tree.nodes.new('HL_BAAction');wait.group=wait_action()
move=tree.nodes.new('HL_BAAction');move.group=move_action()
choose=tree.nodes.new('HL_BAChoose');sequence=tree.nodes.new('HL_BASequence');priority=tree.nodes.new('HL_BAPriority')
loop=tree.nodes.new('HL_BALoop');find=tree.nodes.new('HL_BAFind');find.selection='COMMONS';apply=tree.nodes.new('HL_BAApply')
def link(a,o,b,i):tree.links.new(next(s for s in a.outputs if s.identifier==o),next(s for s in b.inputs if s.identifier==i))
for a,o,b,i in [(wait,'action',choose,'false_action'),(move,'action',choose,'true_action'),(choose,'action',sequence,'action_1'),(wait,'action',sequence,'action_2'),(sequence,'action',priority,'action_1'),(priority,'action',apply,'action'),(loop,'each',apply,'run'),(loop,'current',apply,'bot'),(find,'bots',loop,'bots')]:link(a,o,b,i)
r=SimpleNamespace(warnings=[]);f=flatten(bpy.context,tree,r);ir=MapIR();errors=compile_graph(f.nodes,f.links,ir)
assert not errors and not r.warnings,(errors,r.warnings)
s='\n'.join(ir.extra_scripts.values())
assert s.count('HLBA.Compose(')==3
assert 'ctx.status = "SUCCESS"' in s or 'ctx.status="SUCCESS"' in s
hammerless.unregister();hammerless.register();hammerless.unregister()
print('ACTION_COMPOSITION_BLENDER_OK')
