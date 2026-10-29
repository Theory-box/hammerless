::printl <- function(s) {};
::Time <- function() {return 0.0;};
::IsPlayerABot <- function(b) {return b.bot;};
::NetProps <- {
 HasProp=function(b,k){return k in b.props;},
 GetPropInt=function(b,k){return b.props[k];}, SetPropInt=function(b,k,v){b.props[k]=v;},
 GetPropFloat=function(b,k){return b.props[k];}, SetPropFloat=function(b,k,v){b.props[k]=v;},
 GetPropVector=function(b,k){return b.props[k];}, SetPropVector=function(b,k,v){b.props[k]=v;}
};
class Bot {
 valid=true; hp=100; bot=true; props=null;
 constructor(){props={speed=7, flags=2};}
 function IsValid(){return valid;}
 function GetHealth(){return hp;}
 function GetClassname(){return "player";}
}
function check(v,msg){if(!v)throw msg;}
local b=Bot(), c=Bot(), selected=[b,c], enabled=true, starts=0, updates=0, stops=0;
local freeze={id="freeze",channels=["movement"],
 startFn=function(ctx){starts++;HLBA.Override(ctx,"speed","int",0);},
 updateFn=function(ctx){updates++;}, stopFn=function(ctx){stops++;}};
HLBA.Start("main",0.1,function(){return selected;},function(s){HLBA.Request(s,"apply",s.bot,freeze,enabled);});
HLBA.TickAll(0);check(starts==2 && b.props.speed==0 && c.props.speed==0,"separate instances");
HLBA.TickAll(0.2);check(starts==2 && updates==2,"true updates rather than restarting");
selected=[b];HLBA.TickAll(0.4);check(c.props.speed==7 && stops==1,"selection removal cleanup");
enabled=false;HLBA.TickAll(0.6);check(b.props.speed==7 && stops==2,"false releases");
enabled=true;HLBA.TickAll(0.8);b.props.speed=3;HLBA.Stop("main");
check(b.props.speed==3,"external changes not overwritten");
// Simultaneous incompatible actions must not execute either action.
local a={id="a",channels=["movement"],startFn=function(ctx){throw "must not start conflict";}};
local z={id="z",channels=["movement"],startFn=function(ctx){throw "must not start conflict";}};
HLBA.Start("conflict",0.1,function(){return [b];},function(s){HLBA.Request(s,"a",b,a,true);HLBA.Request(s,"z",b,z,true);});
HLBA.TickAll(1);check(HLBA.loops.conflict.active.len()==0,"conflict rejected");HLBA.StopAll();
// Switching actions releases previous effects before new Start.
local choose=false, order=[];
a={id="a",channels=["movement"],startFn=function(ctx){order.append("startA");},stopFn=function(ctx){order.append("stopA");}};
z={id="z",channels=["movement"],startFn=function(ctx){order.append("startZ");}};
HLBA.Start("switch",0.1,function(){return [b];},function(s){HLBA.Request(s,"slot",b,choose?z:a,true);});
HLBA.TickAll(2);choose=true;HLBA.TickAll(2.2);
check(order.len()==3 && order[1]=="stopA" && order[2]=="startZ","release before acquire");HLBA.StopAll();
// Stop issued during Start wins; managed changes restore in the same tick.
a={id="stop",channels=[],startFn=function(ctx){HLBA.Override(ctx,"speed","int",0);HLBA.Stop(ctx.loopid);}};
HLBA.Start("selfstop",0.1,function(){return [b];},function(s){HLBA.Request(s,"slot",b,a,true);});
HLBA.TickAll(3);check(!("selfstop" in HLBA.loops) && b.props.speed==3,"stop wins");
// Flags preserve unrelated modifications, and callbacks throwing clean up.
a={id="flags",channels=[],startFn=function(ctx){HLBA.Override(ctx,"flags","flags",1,1);ctx.bot.props.flags=ctx.bot.props.flags|4;throw "intentional";}};
HLBA.Start("flags",0.1,function(){return [b];},function(s){HLBA.Request(s,"slot",b,a,true);});
HLBA.TickAll(4);check(b.props.flags==6,"flag cleanup preserves unrelated bits");HLBA.StopAll();
// Dead entities never receive restoration writes or Stop callbacks.
local deadStops=0;
a={id="dead",channels=[],stopFn=function(ctx){deadStops++;}};
HLBA.Start("dead",0.1,function(){return [b];},function(s){HLBA.Request(s,"slot",b,a,true);});
HLBA.TickAll(5);b.hp=0;HLBA.TickAll(5.2);check(deadStops==0,"dead callbacks skipped");HLBA.StopAll();b.hp=100;
// Cross-loop conflicting requests in the same tick cannot both acquire.
HLBA.Start("one",0.1,function(){return [b];},function(s){HLBA.Request(s,"x",b,freeze,true);});
HLBA.Start("two",0.1,function(){return [b];},function(s){HLBA.Request(s,"x",b,freeze,true);});
HLBA.TickAll(6);check(HLBA.loops.one.active.len()==0 && HLBA.loops.two.active.len()==0,"cross-loop conflict");HLBA.StopAll();
// Collection exceptions release previous ownership, rather than leaving effects stuck.
local fail=false;
HLBA.Start("failure",0.1,function(){if(fail)throw "selection failed";return [b];},function(s){HLBA.Request(s,"x",b,freeze,true);});
HLBA.TickAll(7);fail=true;HLBA.TickAll(7.2);check(b.props.speed==3,"failed selection releases");HLBA.StopAll();
check(!HLBA.ticking && HLBA.collecting==null,"scheduler exits cleanly");
// A nested Start cannot mutate traversal or execute early; nested Tick is ignored.
local childStarts=0;
a={id="parent",channels=[],startFn=function(ctx){
 HLBA.Start("child",0.1,function(){return [c];},function(s){childStarts++;});
 HLBA.TickAll(8);
}};
HLBA.Start("parent",0.1,function(){return [b];},function(s){HLBA.Request(s,"x",b,a,true);});
HLBA.TickAll(8);check(childStarts==0,"nested start deferred");HLBA.TickAll(8.2);check(childStarts==1,"child runs next tick");HLBA.StopAll();
// A previous update's collector capability cannot request an action again.
local oldScope=null;
HLBA.Start("scope",0.1,function(){return [b];},function(s){
 if(oldScope==null)oldScope=s;else HLBA.Request(oldScope,"x",b,freeze,true);
});
HLBA.TickAll(9);HLBA.TickAll(9.2);check(HLBA.loops.scope.active.len()==0,"stale scope rejected");HLBA.StopAll();
// Duplicate selector entries are evaluated once. Duplicate apply requests reject the slot.
local visits=0;
HLBA.Start("dupes",0.1,function(){return [b,b];},function(s){visits++;HLBA.Request(s,"x",b,freeze,true);HLBA.Request(s,"x",b,freeze,true);});
HLBA.TickAll(10);check(visits==1 && HLBA.loops.dupes.active.len()==0,"duplicates safe");HLBA.StopAll();
// Invalid human/prop targets never reach action callbacks.
local human=Bot();human.bot=false;local calls=0;
HLBA.Start("human",0.1,function(){return [human];},function(s){calls++;});
HLBA.TickAll(11);check(calls==0,"human filtered");HLBA.StopAll();
// Failures terminate the loop rather than retrying/reapplying on every tick.
local attempts=0;
a={id="throw",channels=[],startFn=function(ctx){attempts++;throw "stop me";}};
HLBA.Start("throw",0.1,function(){return [b];},function(s){HLBA.Request(s,"x",b,a,true);});
HLBA.TickAll(12);HLBA.TickAll(12.2);check(attempts==1 && !("throw" in HLBA.loops),"failed action not retried");
local selects=0;
HLBA.Start("badselect",0.1,function(){selects++;throw "stop me";},function(s){});
HLBA.TickAll(13);HLBA.TickAll(13.2);check(selects==1 && !("badselect" in HLBA.loops),"failed selector not retried");
// Missing channel declarations still cannot corrupt managed ownership.
a={id="owner",channels=[],startFn=function(ctx){HLBA.Override(ctx,"speed","int",0);}};
z={id="intruder",channels=[],startFn=function(ctx){HLBA.Override(ctx,"speed","int",1);}};
b.props.speed=7;
HLBA.Start("owners",0.1,function(){return [b];},function(s){HLBA.Request(s,"a",b,a,true);HLBA.Request(s,"z",b,z,true);});
HLBA.TickAll(14);check(b.props.speed==7 && !("owners" in HLBA.loops),"property collision restores original");
local captured=0, parameters={nested=[{value=5}]};
a={id="snapshot",channels=[],startFn=function(ctx){captured=ctx.params.nested[0].value;}};
HLBA.Start("snapshot",0.1,function(){return [b];},function(s){HLBA.Request(s,"a",b,a,true,parameters);parameters.nested[0].value=9;});
HLBA.TickAll(15);check(captured==5,"parameters snapshotted before later graph mutations");HLBA.StopAll();
local before=HLBA.errors.len();HLBA.Report("same warning");HLBA.Report("same warning");
check(HLBA.errors.len()==before+1,"diagnostics deduplicated");
foreach(bad in [0,-1,"fast",null,true,0.01,1.0e35]) {
 HLBA.Start("badinterval",bad,function(){return [];},function(s){});
 check(!("badinterval" in HLBA.loops),"invalid dynamic interval rejected");
}
// Explicit engine housekeeping overrides may restore after death; authored Stop never runs.
local deathStops=0;
b.props.speed=7;b.props.flags=2;
a={id="deathcleanup",channels=[],startFn=function(ctx){
 HLBA.Override(ctx,"speed","int",0,null,true);
 HLBA.Override(ctx,"flags","int",0);
},stopFn=function(ctx){deathStops++;}};
HLBA.Start("deathcleanup",0.1,function(){return [b];},function(s){HLBA.Request(s,"a",b,a,true);});
HLBA.TickAll(16);b.hp=-10;HLBA.TickAll(16.2);
check(b.props.speed==7 && b.props.flags==0 && deathStops==0,"death cleanup is explicit and does not run Stop");HLBA.StopAll();
// Death cleanup still respects external ownership changes.
b.hp=100;b.props.speed=7;
HLBA.Start("deathchanged",0.1,function(){return [b];},function(s){HLBA.Request(s,"a",b,a,true);});
HLBA.TickAll(17);b.props.speed=3;b.hp=-1;HLBA.TickAll(17.2);
check(b.props.speed==3,"death cleanup preserves external change");HLBA.StopAll();
// Deleted handles receive no restoration, even for restore-on-death entries.
b.hp=100;b.props.speed=7;
HLBA.Start("deleted",0.1,function(){return [b];},function(s){HLBA.Request(s,"a",b,a,true);});
HLBA.TickAll(18);b.valid=false;HLBA.TickAll(18.2);
check(b.props.speed==0 && deathStops==0,"deleted handle not restored");HLBA.StopAll();
