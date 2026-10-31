"""Compile the new blocks from actual Blender sockets and group flattening."""
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
tree=bpy.data.node_groups.new('Building blocks integration','HL_LogicTree');tree.scene_name=bpy.context.scene.name
group=new_action('Observe and choose');group.action_supports='COMMONS'
entry=next(n for n in group.nodes if n.kind=='BA_ENTRY')
find=group.nodes.new('HL_BAFind');find.selection='COMMONS'
cool=group.nodes.new('HL_BACooldown');changed=group.nodes.new('HL_BAChanged');changed.initial=True
motion=group.nodes.new('HL_BAMovement')
nodes=[group.nodes.new(x) for x in ['HL_ListFilter','HL_ListCount','HL_ListItem','HL_ListClosest','HL_ListRandom']]
def link(t,a,out,b,inp):
 t.links.new(next(s for s in a.outputs if s.identifier==out),next(s for s in b.inputs if s.identifier==inp))
link(group,entry,'update',cool,'run');link(group,cool,'passed',changed,'run');link(group,changed,'changed',motion,'run')
prev=motion
for n in nodes:
 link(group,prev,'next',n,'run');link(group,find,'bots',n,'items');prev=n
start=tree.nodes.new('HL_NodeMapStart');select=tree.nodes.new('HL_BAFind');select.selection='COMMONS'
loop=tree.nodes.new('HL_BALoop');apply=tree.nodes.new('HL_BAApply');ref=tree.nodes.new('HL_BAAction');ref.group=group
for a,o,b,i in [(start,'start',loop,'start'),(select,'bots',loop,'bots'),(loop,'each',apply,'run'),(loop,'current',apply,'bot'),(ref,'action',apply,'action')]:link(tree,a,o,b,i)
r=SimpleNamespace(warnings=[]);flat=flatten(bpy.context,tree,r);ir=MapIR();errors=compile_graph(flat.nodes,flat.links,ir)
assert not errors and not r.warnings,(errors,r.warnings)
script='\n'.join(ir.extra_scripts.values())
for name in ['HLBAU.Cooldown(ctx','HLBAU.Changed(ctx','HLBAU.Movement(ctx','HLLT.Query(HLBA.Find(']:assert name in script,name
assert '::HLBAU <- {}' in script
hammerless.unregister()
print('BUILDING_BLOCKS_BLENDER_OK')
