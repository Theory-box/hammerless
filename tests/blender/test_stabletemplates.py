"""Compile both typed editable stable selection variants in ordinary and action graphs."""
import sys,json
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.stabletemplates import make_template,PARAMETERS,Builder
from hammerless.blender.botactions import new_action
from hammerless.blender.logicgroups import flatten,new_item,sync
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR

def wire(t,a,o,b,i):t.links.new(Builder.socket(a,o,True),Builder.socket(b,i))
compiled={}
for typ in ('bots','navareas'):
    tree=bpy.data.node_groups.new('Stable '+typ,'HL_LogicTree');tree.scene_name=bpy.context.scene.name
    start=tree.nodes.new('HL_NodeMapStart')
    asset=make_template(typ)
    for i in range(2):
        group=tree.nodes.new('HL_NodeGroup');group.group=asset
        wire(tree,start,'start',group,'run')
    f=flatten(bpy.context,tree,SimpleNamespace(warnings=[]));ir=MapIR();errors=compile_graph(f.nodes,f.links,ir)
    assert not errors,errors
    names={n.settings['name'] for n in f.nodes if n.kind=='GET_VAR'}
    assert len(names)==16,names
    action=new_action('Select '+typ)
    entry=next(n for n in action.nodes if n.kind=='BA_ENTRY');gin=next(n for n in action.nodes if n.kind=='GROUP_IN')
    for uid,label,kind,default in PARAMETERS:
        if kind!='event':new_item(action,label,kind,False,uid)
    sync(action)
    asset=make_template(typ,True)
    allowed={'GROUP_IN','GROUP_OUT','BA_STATE_GET','BA_STATE_SET','CAND_FIND','CAND_ITEM','BOOL_MATH','COMPARE','MATH','IF'}
    assert all(n.kind in allowed for n in asset.nodes),{n.kind for n in asset.nodes}
    for i in range(2):
        group=action.nodes.new('HL_NodeGroup');group.group=asset
        wire(action,entry,'update',group,'run')
        for uid,label,kind,default in PARAMETERS:
            if kind!='event':wire(action,gin,uid,group,uid)
    loop=tree.nodes.new('HL_BALoop');find=tree.nodes.new('HL_BAFind');apply=tree.nodes.new('HL_BAApply');ref=tree.nodes.new('HL_BAAction');ref.group=action
    for a,o,b,i in [(find,'bots',loop,'bots'),(loop,'each',apply,'run'),(loop,'current',apply,'bot'),(ref,'action',apply,'action')]:wire(tree,a,o,b,i)
    f=flatten(bpy.context,tree,SimpleNamespace(warnings=[]));ir=MapIR();errors=compile_graph(f.nodes,f.links,ir)
    assert not errors,errors
    script='\n'.join(ir.extra_scripts.values());offset=script.index('updateFn = function(ctx) {')+len('updateFn = function(ctx) {')
    depth=1;end=offset
    while depth:
        if script[end]=='{':depth+=1
        elif script[end]=='}':depth-=1
        end+=1
    compiled[typ]={'body':script[offset:end-1]}
if '--' in sys.argv:Path(sys.argv[sys.argv.index('--')+1]).write_text(json.dumps(compiled))
print('STABLE_SELECTION_BLENDER_OK')

