import sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.childactions import retry_action
from hammerless.blender.actiontemplates import wait_action
from hammerless.blender.logicgroups import flatten
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR

tree=bpy.data.node_groups.new('Retry test','HL_LogicTree');tree.scene_name=bpy.context.scene.name
start,find,loop,apply,retry,task,recovery=[tree.nodes.new(k) for k in ('HL_NodeMapStart','HL_BAFind','HL_BALoop','HL_BAApply','HL_BAAction','HL_BAAction','HL_BAAction')]
find.selection='COMMONS';retry.group=retry_action();task.group=wait_action();recovery.group=wait_action()
retry.inputs['Maximum Attempts'].value=3
for a,out,b,inp in ((start,'On Map Start',loop,'Start'),(find,'Bots',loop,'Bots'),(loop,'Each Bot',apply,'Run'),(loop,'Current Bot',apply,'Bot'),(retry,'Action',apply,'Action'),(task,'Action',retry,'Task'),(recovery,'Action',retry,'Recovery')):tree.links.new(a.outputs[out],b.inputs[inp])
report=SimpleNamespace(warnings=[]);flat=flatten(bpy.context,tree,report);assert not report.warnings,report.warnings
ir=MapIR();errors=compile_graph(flat.nodes,flat.links,ir);assert not errors,errors
script='\n'.join(ir.extra_scripts.values());assert 'HLBA.ChildStep' in script and 'HLBA.InheritChannels' in script
assert not any(getattr(n,'kind','') in ('SCRIPT_CODE','SCRIPT_VALUE') for n in retry.group.nodes)
(Path(__file__).resolve().parents[4]/'outputs'/'childactions-generated.nut').write_text(script)
print('RETRY_ACTION_BLENDER_OK')

import os
if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
    import squirrel
    assert hasattr(squirrel,'SQVM'), 'Squirrel VM dependency unavailable'
    vm=squirrel.SQVM()
    vm.execute(script + r"""
::printl <- function(s){};
::Time <- function(){return 0.0;};
::IsPlayerABot <- function(b){return true;};
class Bot {function IsValid(){return true;} function GetHealth(){return 100;} function GetClassname(){return "infected";}}
function check(v,msg){if(!v)throw msg;}
local bot=Bot(), attempts=0,recoveries=0,stops=0;
HL_Action_bot_action_group_001.startFn=function(c){attempts++;c.status=attempts>=3?"SUCCESS":"FAILED";};
HL_Action_bot_action_group_001.stopFn=function(c){stops++;};
HL_Action_bot_action_group_002.startFn=function(c){recoveries++;c.status="SUCCESS";};
local ctx={bot=bot,params={task={},recovery={},attempts=3},state={},overrides=[],loopid="test",applyid="test",status="RUNNING"};
local instance={ctx=ctx,bot=bot,def=HL_Action_bot_action_group,released=false};
HLBA.Step(instance,true);check(attempts==1&&recoveries==0,"first attempt only");
HLBA.Step(instance,false);check(attempts==1&&recoveries==1,"recovery separate tick");
HLBA.Step(instance,false);check(attempts==2&&recoveries==1,"second attempt");
HLBA.Step(instance,false);HLBA.Step(instance,false);
check(ctx.status=="SUCCESS"&&attempts==3&&recoveries==2&&stops==3,"retry success and cleanup");
HLBA.Step(instance,false);check(attempts==3,"completion latched");
attempts=0;recoveries=0;
ctx={bot=bot,params={task={},recovery={},attempts=1.9},state={},overrides=[],loopid="test",applyid="test",status="RUNNING"};
instance={ctx=ctx,bot=bot,def=HL_Action_bot_action_group,released=false};
HLBA.Step(instance,true);check(ctx.status=="FAILED"&&attempts==1&&recoveries==0,"whole attempt limit");
""")
    print('RETRY_GENERATED_SQUIRREL_OK')
