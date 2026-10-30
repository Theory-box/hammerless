class Vector {
 x=0; y=0; z=0;
 constructor(a,b,c) { x=a; y=b; z=c; }
 function _add(b) { return Vector(x+b.x,y+b.y,z+b.z); }
}
now <- 0.0; brightness <- 0.3; available <- true; candidateAvailable <- true;
candidates <- true; navFound <- true; pathExists <- true;
searches <- 0; moves <- 0; resets <- 0;
botPosition <- Vector(0,0,0); lastDestination <- null;
function Time() { return now; }
function HL_GetProp(a,b,c,d) { return botPosition; }
function HLWQ_Ambient(p) {
 return {available=p.x==0 ? available : candidateAvailable,brightness=(p.x==0 ? brightness : 0.04)/100000.0};
}
HLWQ <- {
 Nearest=function(a,b,c,d) { return {found=navFound,area=1}; },
 Nearby=function(a,b,c) { searches++; return {areas=candidates ? [2] : []}; },
 Info=function(a,b) { return {found=true,blocked=false,underwater=false,position=Vector(100,0,0)}; },
 Path=function(a,b,c,d,e) { return {exists=pathExists}; }
};
HLBA <- {Command=function(ctx,cmd,t,p) { if(cmd=="MOVE") { moves++; lastDestination=p; } else if(cmd=="RESET") resets++; }};
function check(ok,msg) { if(!ok) throw msg; }
function context() { return {bot=null,state={},params={enter=0.15,exit=0.08,improvement=0.02,radius=768,candidates=8,retry=3,path=2048}}; }
// GENERATED_ACTION_HERE
local action=HL_Action_bot_action_group, ctx=context();
action.startFn(ctx);
check(moves==1 && searches==1 && ctx.state.movement_owned,"bright start must move");
now=0.25; action.updateFn(ctx);
check(moves==1 && searches==1,"cooldown must avoid repeated searches and moves");
brightness=0.10; now=0.5; action.updateFn(ctx);
check(ctx.state.movement_owned && resets==0,"hysteresis must retain escape between thresholds");
brightness=0.08; action.updateFn(ctx); action.updateFn(ctx);
check(resets==0 && ctx.state.movement_owned && ctx.state.holding_shade,"safe brightness must retain shade ownership");
check(moves==3,"safe position must be maintained each tick");
botPosition=Vector(40,0,0);
brightness=0.3; now=3; action.updateFn(ctx);
check(moves==4 && searches==1,"holding must return to saved shade without searching");
check(lastDestination.x==0,"displaced bot must return to original saved shade");
botPosition=Vector(0,0,0);
available=false; action.updateFn(ctx); action.updateFn(ctx);
check(resets==1 && !ctx.state.movement_owned && !ctx.state.holding_shade,"unavailable data must release once");
available=true; now=6; candidates=false; action.updateFn(ctx);
local count=searches; now=6.25; action.updateFn(ctx);
check(searches==count && moves==4,"failed search must also be throttled");
candidates=true; now=9; action.updateFn(ctx);
check(moves==5,"search resumes after failed-search deadline");
action.stopFn(ctx); action.stopFn(ctx);
check(resets==2 && !ctx.state.movement_owned,"stop releases exactly once");
// Invalid thresholds and unavailable candidate samples must not issue commands.
ctx=context(); ctx.params.exit=0.2; action.startFn(ctx);
check(moves==5,"invalid hysteresis must not move");
ctx=context(); candidateAvailable=false; action.startFn(ctx);
check(moves==5,"missing candidate light must not become darkness");
candidateAvailable=true; pathExists=false; ctx=context(); action.startFn(ctx);
check(moves==5,"unreachable shade must not move");
pathExists=true; ctx=context(); action.startFn(ctx);
check(moves==6,"reachable shade resumes");
now=12; candidates=false; action.updateFn(ctx); action.updateFn(ctx);
check(resets==3 && !ctx.state.movement_owned,"no remaining destination releases owned move once");
