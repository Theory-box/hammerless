"""Compile coordination UI nodes in world and owned-action contexts."""
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.botactions import new_action
from hammerless.blender.logicgroups import flatten
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR

def link(tree,a,o,b,i):tree.links.new(next(s for s in a.outputs if s.identifier==o),next(s for s in b.inputs if s.identifier==i))
tree=bpy.data.node_groups.new('Coordination test','HL_LogicTree');tree.scene_name=bpy.context.scene.name
start=tree.nodes.new('HL_NodeMapStart');pub=tree.nodes.new('HL_SignalPublish');read=tree.nodes.new('HL_SignalRead');clear=tree.nodes.new('HL_SignalClear')
for a,o,b,i in [(start,'start',pub,'run'),(pub,'next',read,'run'),(read,'next',clear,'run')]:link(tree,a,o,b,i)
group=new_action('Reserve destination');group.action_supports='COMMONS'
entry=next(n for n in group.nodes if n.kind=='BA_ENTRY')
claim=group.nodes.new('HL_ClaimDestination');release=group.nodes.new('HL_ReleaseDestination')
link(group,entry,'start',claim,'run');link(group,entry,'stop',release,'run')
find=tree.nodes.new('HL_BAFind');find.selection='COMMONS';loop=tree.nodes.new('HL_BALoop');apply=tree.nodes.new('HL_BAApply');ref=tree.nodes.new('HL_BAAction');ref.group=group
for a,o,b,i in [(find,'bots',loop,'bots'),(loop,'each',apply,'run'),(loop,'current',apply,'bot'),(ref,'action',apply,'action')]:link(tree,a,o,b,i)
r=SimpleNamespace(warnings=[]);flat=flatten(bpy.context,tree,r);ir=MapIR();errors=compile_graph(flat.nodes,flat.links,ir)
assert not errors and not r.warnings,(errors,r.warnings)
script='\n'.join(ir.extra_scripts.values())
assert 'hiding_spot_1' in script and 'alarm' in script
hammerless.unregister();hammerless.register();hammerless.unregister()
print('COORDINATION_BLENDER_OK')
