::printl <- function(s){};
::IsPlayerABot <- function(b){return true;};
function check(v,msg){if(!v)throw msg;}
class Bot {function IsValid(){return true;} function GetHealth(){return 100;} function GetClassname(){return "player";}}
local bot=Bot(), starts=0,stops=0;
local def={id="child",channels=["Movement"],startFn=function(c){starts++;c.state.x<-1;},updateFn=function(c){c.status="FAILED";},stopFn=function(c){stops++;}};
local ctx={bot=bot,params={},state={},overrides=[],loopid="x",applyid="x",status="RUNNING",_stepSerial=1};
local r=HLBA.ChildStep(ctx,"slot",def,{});check(r.running&&r.attempt==1&&starts==1,"start");
r=HLBA.ChildStep(ctx,"slot",def,{},true);check(starts==1&&stops==0,"reset bounded in callback");
ctx._stepSerial++;r=HLBA.ChildStep(ctx,"slot",def,{});check(r.failed&&stops==1,"completion cleanup");
ctx._stepSerial++;r=HLBA.ChildStep(ctx,"slot",def,{});check(r.failed&&starts==1&&stops==1,"latched result");
ctx._stepSerial++;r=HLBA.ChildStep(ctx,"slot",def,{},true);check(r.running&&r.attempt==2&&starts==2,"restart fresh");
local other=HLBA.ChildStep(ctx,"other",def,{});check(other.failed&&other.error.len()>0&&starts==2,"conflicting slots blocked");
local wrapper={id="wrapper",channels=[]};HLBA.InheritChannels(wrapper,[def]);check(wrapper.channels[0]=="Movement"&&wrapper.depth==2,"inherit ownership");
local all=[];HLBA.Contexts(ctx,all);check(all.len()==3,"children included in ownership traversal");
HLBA.Release({ctx=ctx,def=wrapper});check(stops==2,"parent releases running child exactly once");
HLBA.Release({ctx=ctx,def=wrapper});check(stops==2,"descendant cleanup idempotent");
local unsupported={id="unsupported",channels=[],supportsFn=function(b){return false;},startFn=function(c){throw "must not run";},stopFn=function(c){throw "must not stop";}};
ctx={bot=bot,params={},state={},overrides=[],loopid="y",applyid="y",status="RUNNING",_stepSerial=1};
r=HLBA.ChildStep(ctx,"unsupported",unsupported,{});check(r.failed&&r.error=="Unsupported bot","unsupported safe failure");

ctx._stepSerial++;r=HLBA.ChildStep(ctx,"cancel",def,{});check(r.running,"cancellable child starts");
ctx._stepSerial++;r=HLBA.ChildStep(ctx,"cancel",def,{},false,false);check(r.idle&&!r.failed,"disable cancels to Idle");
ctx._stepSerial++;r=HLBA.ChildStep(ctx,"cancel",def,{});check(r.running&&r.attempt==2,"enable starts fresh child");
HLBA.Release({ctx=ctx,def=wrapper});local rejected=false;
try {HLBA.ChildStep(ctx,"cancel",def,{});}catch(e){rejected=true;}
check(rejected,"cleanup context cannot resurrect children");
