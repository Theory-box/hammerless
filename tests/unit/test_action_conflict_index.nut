::events<-[]; ::reports<-[]; ::phase<-0; ::clock<-0.0;
function Time(){return clock;}
function printl(s){reports.append(s);}
function IsPlayerABot(b){return true;}
function check(v,message){if(!v)throw message;}
class ConflictBot {
    id=0;hp=100;valid=true;speed=7;
    constructor(n){id=n;}
    function IsValid(){return valid;}
    function GetHealth(){return hp;}
    function GetClassname(){return "infected";}
}
::NetProps<-{
    HasProp=function(b,k){return k=="speed";},
    GetPropInt=function(b,k){return b.speed;},
    SetPropInt=function(b,k,v){b.speed=v;}
};
function Def(id,channels) {
    local name=id;
    local d={id=id,
        startFn=function(ctx){events.append("start:"+name+":"+ctx.bot.id);},
        updateFn=function(ctx){events.append("update:"+name+":"+ctx.bot.id);},
        stopFn=function(ctx){events.append("stop:"+name+":"+ctx.bot.id);}};
    if(channels!=null)d.channels<-channels;
    return d;
}
function Snapshot() {
    local state=[];
    foreach(id,loop in HLBA.loops) {
        foreach(r in loop.requests)state.append("request:"+id+":"+r.applyid+":"+r.bot.id+":"+r.def.id+":"+r.rejected);
        foreach(a in loop.active)state.append("active:"+id+":"+a.applyid+":"+a.bot.id+":"+a.def.id+":"+a.ctx.status);
        state.append("loop:"+id+":"+loop.stopped);
    }
    state.sort();
    local text="";
    // Preserve exact report and lifecycle order; canonicalize only state maps.
    foreach(v in reports)text+=v+"|";
    foreach(v in events)text+=v+"|";
    foreach(v in state)text+=v+"|";
    return text;
}
function MixedCase(seed) {
    HLBA.StopAll();HLBA.errors=[];events=[];reports=[];phase=0;clock=0;
    local bots=[];for(local i=0;i<5;++i)bots.append(ConflictBot(i));
    local channels=[["movement"],["look"],["movement","look"],[],null,["movement","movement"]];
    for(local n=0;n<4;++n) {
        local loopIndex=n, selected=bots, localSeed=seed;
        local definitions=[];
        for(local k=0;k<6;++k)definitions.append(Def("def"+n+"_"+k,channels[(k+n+seed)%channels.len()]));
        local defs=definitions;
        HLBA.Start("loop"+n,[0.1,0.3,1.0,0.2][n],function(){return selected;},function(ctx){
            local i=ctx.bot.id, first=(i+loopIndex+phase+localSeed)%6;
            local enabled=(i+phase+loopIndex+localSeed)%5!=0;
            HLBA.Request(ctx,"primary",ctx.bot,defs[first],enabled,{});
            if((i+phase+localSeed)%2==0)
                HLBA.Request(ctx,"secondary",ctx.bot,defs[(first+2)%6],true,{});
            // Pre-rejected duplicate requests still coexist with other pairs.
            if((i+phase+loopIndex+localSeed)%11==0)
                HLBA.Request(ctx,"primary",ctx.bot,defs[first],enabled,{});
        });
    }
    local trace="";
    foreach(t in [0.0,0.1,0.2,0.35,0.7,1.2]) {
        clock=t;
        if(phase==2)bots[1].hp=0;
        if(phase==3)bots[2].valid=false;
        if(phase==4){bots[1].hp=100;bots[2].valid=true;}
        HLBA.TickAll(t);
        check(!HLBA.ticking && HLBA.scope==null && HLBA.collecting==null,"tick leaked scope");
        trace+="tick:"+phase+"|"+Snapshot()+"\n";
        ++phase;
    }
    HLBA.StopAll();trace+="final|"+Snapshot();
    return trace;
}
function NonDueOwnerCase() {
    HLBA.StopAll();HLBA.errors=[];events=[];reports=[];clock=0;
    local bot=ConflictBot(0), ownerStarts=0,ownerStops=0,challengerStarts=0;
    local owner={id="owner",channels=["movement"],
        startFn=function(ctx){++ownerStarts;HLBA.Override(ctx,"speed","int",0);},
        stopFn=function(ctx){++ownerStops;}};
    local challenger={id="challenger",channels=["movement"],startFn=function(ctx){++challengerStarts;}};
    HLBA.Start("owner",1.0,function(){return [bot];},function(ctx){HLBA.Request(ctx,"a",bot,owner,true);});
    HLBA.TickAll(0);
    check(ownerStarts==1 && bot.speed==0,"owner did not acquire");
    HLBA.Start("challenger",0.1,function(){return [bot];},function(ctx){HLBA.Request(ctx,"b",bot,challenger,true);});
    HLBA.TickAll(0.1);
    check(ownerStops==0 && challengerStarts==0 && bot.speed==0,"non-due owner lost ownership");
    check(HLBA.loops.owner.active.len()==1 && HLBA.loops.challenger.active.len()==0,"non-due conflict active state");
    check(HLBA.loops.challenger.requests[0].rejected,"due challenger was not rejected");
    HLBA.TickAll(1.1);
    check(ownerStarts==1 && ownerStops==1 && challengerStarts==0 && bot.speed==7,"both due should reject and restore owner");
    check(HLBA.loops.owner.active.len()==0 && HLBA.loops.challenger.active.len()==0,"both due conflict retained action");
    HLBA.StopAll();
}
function DistinctBotsCase() {
    HLBA.StopAll();HLBA.errors=[];events=[];reports=[];
    local bots=[],starts=0,updates=0,pairs=0;
    for(local i=0;i<100;++i)bots.append(ConflictBot(i));
    local def={id="distinct",channels=["movement"],
        startFn=function(ctx){++starts;},updateFn=function(ctx){++updates;}};
    HLBA.Start("distinct",0.1,function(){return bots;},function(ctx){HLBA.Request(ctx,"a",ctx.bot,def,true);});
    local original=HLBA.Clashes;
    HLBA.Clashes=function(a,b){++pairs;return original(a,b);};
    HLBA.TickAll(0);HLBA.TickAll(0.2);
    HLBA.Clashes=original;
    check(starts==100 && updates==100 && HLBA.loops.distinct.active.len()==100,"distinct bot actions missing");
    check(pairs==0,"distinct bot actions still compare conflict pairs");
    check(reports.len()==0,"distinct bot actions falsely conflicted");
    HLBA.StopAll();
}
