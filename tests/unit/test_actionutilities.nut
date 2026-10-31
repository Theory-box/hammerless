::clock <- 0.0;
::Time <- function() {return clock;};
class Vec {
 x=0;y=0;z=0;
 constructor(a,b,c){x=a;y=b;z=c;}
 function _sub(v){return Vec(x-v.x,y-v.y,z-v.z);}
 function LengthSqr(){return x*x+y*y+z*z;}
 function Length(){return sqrt(LengthSqr());}
}
::Vector <- function(x,y,z){return Vec(x,y,z);};
function check(v,s){if(!v)throw s;}
local ctx={state={}}, other={state={}};
check(HLBAU.Cooldown(ctx,"a",2,false).passed,"first passes");
clock=1;
local r=HLBAU.Cooldown(ctx,"a",2,false);
check(r.blocked && r.remaining==1,"repeat blocked");
check(HLBAU.Cooldown(other,"a",2,false).passed,"bot state isolated");
clock=2;
check(HLBAU.Cooldown(ctx,"a",2,false).passed,"boundary passes");
check(HLBAU.Cooldown(ctx,"a",2,true).passed,"reset passes");
check(HLBAU.Cooldown(ctx,"zero",0,false).passed && HLBAU.Cooldown(ctx,"zero",0,false).passed,"zero allows repeats");
local bad=false;try{HLBAU.Cooldown(ctx,"bad",-1,false);}catch(e){bad=true;}check(bad,"negative rejected");
check(!HLBAU.Changed(ctx,"b",1,"num",false,false).changed,"first establishes baseline");
check(HLBAU.Changed(ctx,"b",2,"num",false,false).changed,"change observed");
check(HLBAU.Changed(ctx,"b",2,"num",false,false).unchanged,"same unchanged");
check(HLBAU.Changed(ctx,"b",2,"num",true,true).changed,"reset initial event");
local v=Vector(1,2,3);
HLBAU.Changed(ctx,"v",v,"vec",false,false);v.x=4;
check(HLBAU.Changed(ctx,"v",v,"vec",false,false).changed,"vector copy");
local bot={p=Vector(100,0,0),GetOrigin=function(){return p;}};
ctx.bot <- bot;clock=0;
local goal=Vector(0,0,0);
r=HLBAU.Movement(ctx,"m",goal,5,8,3,10,false);
check(r.distance==100 && !r.arrived && !r.stalled,"first movement snapshot");
clock=2;bot.p=Vector(90,0,0);
r=HLBAU.Movement(ctx,"m",goal,5,8,3,10,false);
check(r.progress==10 && !r.stalled,"progress refreshes stall timer");
clock=5;r=HLBAU.Movement(ctx,"m",goal,5,8,3,10,false);
check(r.stalled && !r.timed_out,"stalled boundary");
clock=10;r=HLBAU.Movement(ctx,"m",goal,5,8,3,10,false);
check(r.timed_out,"overall timeout");
goal.x=40;r=HLBAU.Movement(ctx,"m",goal,5,8,3,10,false);
check(r.elapsed==0 && !r.stalled && !r.timed_out,"new goal resets history including mutated vector");
clock=50;bot.p=Vector(43,0,0);r=HLBAU.Movement(ctx,"m",goal,5,8,3,10,false);
check(r.arrived && !r.stalled && !r.timed_out,"arrival wins");
bot.p=Vector(100,0,0);r=HLBAU.Movement(ctx,"m",goal,5,8,0,0,false);
check(!r.stalled && !r.timed_out,"zero disables timeout and stall");
check(ctx.state.len()==0,"private state does not pollute user memory");
