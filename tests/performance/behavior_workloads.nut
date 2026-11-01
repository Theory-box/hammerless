// Deliberately cheap mock entities. No native engine query is timed here.
function Vector(x,y,z) { return {x=x,y=y,z=z}; }
function Time() { return Bench.now; }
function printl(s) { throw "Unexpected runtime diagnostic: "+s; }
class BenchBot {
    position=null;
    constructor(i) {position=Vector(i*16,0,0);}
    function IsValid(){return true;}
    function GetClassname(){return "infected";}
    function GetHealth(){return 100;}
    function GetOrigin(){return position;}
}
function IsPlayerABot(bot){return true;}
::Bench <- {
    now=0.0, items=[], botCount=0, callbacks=0,
    plain=function(candidate,position,index,params){return {keep=true,score=index*0.5+params.bias};},
    SetupCandidates=function(bots,count) {
        items=[]; botCount=bots;
        for(local i=0;i<count;++i) items.append(BenchBot(i));
    },
    Candidates=function(updates,generated) {
        local checksum=0.0, examined=0, returned=0;
        local rule=generated ? BenchGeneratedRule : plain;
        for(local u=0;u<updates;++u) for(local b=0;b<botCount;++b) {
            local result=HLCE.Evaluate(items,"bots",rule,{bias=b},128);
            if(result.errors!=0 || result.rejected!=0 || !result.found) throw "Candidate workload did not execute correctly";
            checksum+=result.score;
            examined+=result.examined;returned+=result.count;
        }
        local expected=updates*(botCount*(items.len()-1)*0.5+botCount*(botCount-1)*0.5);
        if(checksum!=expected || examined!=updates*botCount*items.len() || returned!=updates*botCount*(items.len()<128?items.len():128))
            throw "Candidate workload counters disagree";
        return checksum;
    },
    ActionDef=function() {
        return {id="benchmark",channels=["movement"],
                startFn=function(ctx){++Bench.callbacks;},updateFn=function(ctx){++Bench.callbacks;}};
    },
    SetupActions=function(bots,loopCount,retained) {
        HLBA.StopAll(); now=0;callbacks=0;botCount=bots;
        local groups=[];for(local i=0;i<loopCount;++i) groups.append([]);
        for(local i=0;i<bots;++i) groups[i%loopCount].append(BenchBot(i));
        foreach(i,group in groups) {
            local selected=group, def=ActionDef();
            HLBA.Start("bench"+i,0.05,
                function(){return selected;},
                function(ctx){HLBA.Request(ctx,"apply",ctx.bot,def,true,{});});
        }
        if(retained) HLBA.TickAll(now);
        callbacks=0;
    },
    Actions=function(updates,fresh) {
        callbacks=0;
        for(local u=0;u<updates;++u) {
            // Clearing outside TickAll models no previous active instances; this
            // tiny harness cost is included in fresh-batch timings.
            if(fresh) foreach(loop in HLBA.loops) {HLBA.ReleaseLoop(loop);}
            now+=0.1;HLBA.TickAll(now);
        }
        if(callbacks!=updates*botCount || HLBA.errors.len()!=0) throw "Action workload did not execute correctly";
        return callbacks;
    },
    CountActionPairs=function() {
        local original=HLBA.Clashes,count=0;
        HLBA.Clashes=function(a,b){++count;return original(a,b);};
        now+=0.1;HLBA.TickAll(now);
        HLBA.Clashes=original;
        if(count!=botCount*(botCount-1)/2) throw "Unexpected action-pair count";
        local seen={}, active=0;
        foreach(loop in HLBA.loops) foreach(instance in loop.active) {
            if(instance.bot in seen) throw "Action loops selected the same bot twice";
            seen[instance.bot] <- true; ++active;
        }
        if(active!=botCount || seen.len()!=botCount) throw "Action loop population is incomplete";
        return count;
    }
};
