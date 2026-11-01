"""Registered editable cache example: export and execute with mocked engine boundaries."""
import json, os, re, sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logicgroups import flatten
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR
assert bpy.ops.hammerless.shared_shade_scan()=={'FINISHED'}
tree=next(t for t in bpy.data.node_groups if t.bl_idname=='HL_LogicTree' and t.name.startswith('Shared Shade Scan'))
action=next(n for n in tree.nodes if n.bl_idname=='HL_BAAction')
assert next(s for s in action.inputs if s.identifier=='cache').bl_idname=='HL_QueryCacheSocket'
report=SimpleNamespace(warnings=[])
flat=flatten(bpy.context,tree,report)
assert not report.warnings,report.warnings
ir=MapIR(); errors=compile_graph(flat.nodes,flat.links,ir)
assert not errors,errors
assert ir._world_query_ambient
visited=set()
def check_editable(group):
    if group.name in visited:return
    visited.add(group.name)
    for node in group.nodes:
        assert getattr(node,'kind','') not in {'SCRIPT_CODE','SCRIPT_VALUE'},node.name
        child=getattr(node,'group',None)
        if child is not None:check_editable(child)
check_editable(tree)
script='\n'.join(ir.extra_scripts.values())
assert 'HLQC.ReadNearby' in script and 'HLQC.ReadAmbient' in script
assert script.count('HLQC.Clear();')==4
assert os.environ.get('SQUIRREL_PYTHONPATH'),'Real Squirrel VM required'
sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
import squirrel
squirrel.SQVM().execute('compilestring('+json.dumps(script)+');')
start=re.search(r'::(HL_BA_Start_\w+) <-',script).group(1)
rule=re.search(r'function (HL_CandidateRule\w*)\(candidate, position, index, params\)',script).group(1)
prelude=r'''
::clock<-0.0;::logs<-[];function Time(){return clock;}function printl(s){logs.append(s);}
function IsPlayerABot(b){return true;}
class V {x=0;y=0;z=0;constructor(a,b,c){x=a;y=b;z=c;}function _add(v){return V(x+v.x,y+v.y,z+v.z);}function _mul(v){return V(x*v,y*v,z*v);}}
function Vector(x,y,z){return V(x,y,z);}
class Bot {function IsValid(){return true;}function GetHealth(){return 100;}function GetClassname(){return "infected";}function GetOrigin(){return Vector(0,0,0);}}
class Area {id=0;constructor(i){id=i;}function GetID(){return id;}function GetCenter(){return Vector(id,0,0);}function GetDistanceSquaredToPoint(p){return (id-p.x)*(id-p.x);}}
::bots<-[Bot(),Bot(),Bot()];::areas<-[Area(1),Area(2),Area(3)];::navCalls<-0;::ambientCalls<-0;::navFail<-false;
::Entities<-{FindByClassname=function(previous,name){if(name!="infected")return null;local i=previous==null?0:bots.find(previous)+1;return i<bots.len()?bots[i]:null;}};
::NavMesh<-{GetNavAreaCount=function(){return areas.len();},GetNavAreaByID=function(id){foreach(a in areas)if(a.id==id)return a;return null;},GetNavAreasInRadius=function(p,r,out){++navCalls;if(navFail)throw "nav unavailable";foreach(i,a in areas)out[i]<-a;}};
function check(v,s){if(!v)throw s;}
'''
setup=r'''
::HL_AmbientData<-{root=-1,nodes=[],planes=[],leaves=[[0,1,0,false]],probes=[[0,0,0,0.2,0.3,0.4]]};
local ambient=HLWQ_Ambient;HLWQ_Ambient=function(p){++ambientCalls;return ambient(p);};
'''
success=r'''
HLBA.TickAll(clock);
check(navCalls==1 && ambientCalls==0,"initial shared nav query or grant latency wrong");
clock=0.2;HLBA.TickAll(clock);
check(navCalls==1 && ambientCalls==3,"shared cache did not reuse identical nav/light queries");
local n=0;foreach(loop in HLBA.loops)foreach(a in loop.active){++n;check(a.ctx.status=="SUCCESS","scan did not report success");}
check(n==3,"expected three common actions");
HLBA.StopAll();check(HLWB.claims.len()==0,"claims retained after stop");
'''
squirrel.SQVM().execute(prelude+script+setup+'\n'+start+'();\n'+success)
# Force backend failure through the actual example's error branch.
failure=r'''
navFail=true;HLBA.TickAll(clock);
local n=0;foreach(loop in HLBA.loops)foreach(a in loop.active){++n;check(a.ctx.status=="FAILED","query failure left action running");}
check(n==3 && ambientCalls==0 && HLWB.claims.len()==0,"failed query acquired work or evaluated lighting");
check(logs.len()>0,"query failure was not diagnosed");
'''
squirrel.SQVM().execute(prelude+script+setup+'\n'+start+'();\n'+failure)
# Inject failures at public helper boundaries to verify the generated graph's
# grant-error and candidate-failed branches independently of cache behavior.
for injection in (
    'HLWB.Request=function(ctx,key,pool,wanted,enabled){return HLWB.Empty("injected grant failure");};',
    'HLCE.Step=function(...){return {failed=true,error="injected scan failure"};};',
):
    cases=r'''
    HLBA.TickAll(clock);
    local n=0;foreach(loop in HLBA.loops)foreach(a in loop.active){++n;check(a.ctx.status=="FAILED","helper error did not fail action");}
    check(n==3 && ambientCalls==0 && HLWB.claims.len()==0,"helper failure leaked work claims or queried lighting");
    check(logs.len()>0,"helper error lacked diagnostic");
    '''
    squirrel.SQVM().execute(prelude+script+setup+injection+'\n'+start+'();\n'+cases)
# Missing baked data produces a completed empty result and the example's
# explicit no-usable-observation failure, rather than an endless running scan.
missing=r'''
HL_AmbientData=null;HLBA.TickAll(clock);clock=0.2;HLBA.TickAll(clock);
local n=0;foreach(loop in HLBA.loops)foreach(a in loop.active){++n;check(a.ctx.status=="FAILED","missing baked data did not fail observation");}
check(n==3 && ambientCalls==9,"unavailable samples were incorrectly cached");
check(HLWB.claims.len()==0 && logs.len()>0,"missing-data failure retained claims or lacked diagnostics");
'''
squirrel.SQVM().execute(prelude+script+setup+'\n'+start+'();\n'+missing)
# TTL zero ensures the pure rule's two outputs only query once per evaluation,
# independent of the cross-evaluation cache itself.
memo='''
local params={cache={id="memo",capacity=64},age=0};
local a=RULE(areas[0],Vector(1,0,0),0,params);
check(a.keep && a.score<0 && ambientCalls==1,"pure cached sample evaluated twice for keep/score");
RULE(areas[0],Vector(1,0,0),0,params);
check(ambientCalls==2,"pure sample memo leaked across evaluations");
'''.replace('RULE',rule)
squirrel.SQVM().execute(prelude+script+setup+memo)
print('QUERY_CACHE_BLENDER_GENERATED_EXECUTION_OK')
