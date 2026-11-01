"""Compile editable groups and export actual action functions for Squirrel execution."""
import sys, json, re
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.behaviortemplates import make_template, PARAMETERS, Builder
from hammerless.blender.botactions import new_action
from hammerless.blender.logicgroups import flatten, new_item, sync
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR

def wire(tree, a, out, b, inp):
    tree.links.new(Builder.socket(a,out,True), Builder.socket(b,inp))

compiled = {}
for kind in PARAMETERS:
    # Both map-level group copies compile with distinct storage names.
    tree = bpy.data.node_groups.new('Temporal '+kind, 'HL_LogicTree')
    tree.scene_name = bpy.context.scene.name
    asset = make_template(kind)
    start = tree.nodes.new('HL_NodeMapStart')
    for i in range(2):
        node = tree.nodes.new('HL_NodeGroup'); node.group = asset
        wire(tree,start,'start',node,'run')
    r=SimpleNamespace(warnings=[]); f=flatten(bpy.context,tree,r)
    names={n.settings['name'] for n in f.nodes if n.kind=='GET_VAR'}
    assert len(names)==2, names
    ir=MapIR(); errors=compile_graph(f.nodes,f.links,ir)
    assert not errors and not r.warnings, (errors,r.warnings)
    # Action variants use the same editable arithmetic and explicit per-copy state.
    action = new_action('Temporal '+kind)
    entry = next(n for n in action.nodes if n.kind=='BA_ENTRY')
    gin = next(n for n in action.nodes if n.kind=='GROUP_IN')
    for uid,label,typ,default in PARAMETERS[kind]:
        if typ!='event': new_item(action,label,typ,False,uid)
    sync(action)
    asset = make_template(kind, action=True)
    for i in range(2):
        group=action.nodes.new('HL_NodeGroup');group.group=asset
        wire(action,entry,'update',group,'run')
        for uid,label,typ,default in PARAMETERS[kind]:
            if typ!='event': wire(action,gin,uid,group,uid)
        capture=action.nodes.new('HL_BAStateSet');capture.state_name='observed'+str(i)
        capture.value_type='bool' if kind=='SUSTAINED' else 'num'
        wire(action,group,'next',capture,'run')
        wire(action,group,'ready' if kind=='SUSTAINED' else 'value',capture,'value')
    f=flatten(bpy.context,action,SimpleNamespace(warnings=[]))
    names={n.settings['name'] for n in f.nodes if n.kind=='BA_STATE_GET'}
    assert len(names)==2, names
    # Legacy/default action states intentionally share a name across nested copies.
    reads=[n for n in asset.nodes if n.kind=='BA_STATE_GET']
    for node in reads: node.group_local=False
    legacy=flatten(bpy.context,action,SimpleNamespace(warnings=[]))
    assert {n.settings['name'] for n in legacy.nodes if n.kind=='BA_STATE_GET'}=={'value'}
    for node in reads: node.group_local=True
    assert not any(n.kind in {'SET_VAR','GET_VAR','SCRIPT_CODE','SCRIPT_VALUE'} for n in f.nodes)
    loop=tree.nodes.new('HL_BALoop');find=tree.nodes.new('HL_BAFind');apply=tree.nodes.new('HL_BAApply')
    ref=tree.nodes.new('HL_BAAction');ref.group=action
    for a,o,b,i in [(find,'bots',loop,'bots'),(loop,'each',apply,'run'),(loop,'current',apply,'bot'),(ref,'action',apply,'action')]:
        wire(tree,a,o,b,i)
    r=SimpleNamespace(warnings=[]);f=flatten(bpy.context,tree,r);ir=MapIR();errors=compile_graph(f.nodes,f.links,ir)
    assert not errors and not r.warnings,(errors,r.warnings)
    script='\n'.join(ir.extra_scripts.values())
    offset=script.index('updateFn = function(ctx) {')+len('updateFn = function(ctx) {')
    depth=1; end=offset
    while depth:
        if script[end]=='{': depth+=1
        elif script[end]=='}': depth-=1
        end+=1
    compiled[kind]={'body':script[offset:end-1], 'names':sorted(names)}
if '--' in sys.argv:
    Path(sys.argv[sys.argv.index('--')+1]).write_text(json.dumps(compiled),encoding='utf8')
print('BEHAVIOR_TEMPLATES_BLENDER_OK')

