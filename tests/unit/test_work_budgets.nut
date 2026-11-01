::now<-0.0;
function Time(){return now;}
function Vector(x,y,z){return {x=x,y=y,z=z};}
function printl(s){}
function IsPlayerABot(b){return true;}
function check(ok,message){if(!ok)throw message;}
class BudgetBot {
    id=0;valid=true;
    constructor(i){id=i;}
    function IsValid(){return valid;}
    function GetClassname(){return "infected";}
    function GetHealth(){return 100;}
    function GetOrigin(){return Vector(id,0,0);}
}
function Context(id,loop="fast") {
    return {bot=BudgetBot(id),loopid=loop,status="RUNNING",_stepSerial=1,overrides=[],state={},params={},applyid="apply"};
}
function ResetBudget() {
    HLWB.pools={};HLWB.claims={};HLWB.cycle=0;HLWB.nextId=1;HLWB.active=false;
}
local fast={id="fast",stopped=false},slow={id="slow",stopped=false},pool={id="shared",limit=1};
local contexts=[Context(0),Context(1),Context(2)],totals=[0,0,0];
HLWB.Begin([fast],0);
foreach(ctx in contexts) {
    local r=HLWB.Request(ctx,"request",pool,8,true);
    check(r.waiting && r.queued && r.granted==0 && r.grant!=null,"new demand queues with valid zero credit");
}
HLWB.End();
for(local cycle=0;cycle<9;++cycle) {
    HLWB.Begin([fast],cycle+1);
    local sum=0;
    foreach(i,ctx in contexts) {
        local r=HLWB.Request(ctx,"request",pool,8,true);
        local take=HLWB.Take(ctx,r.grant,256);
        check(take.valid,"canonical grant accepted");sum+=take.taken;totals[i]+=take.taken;
        local repeated=HLWB.Request(ctx,"request",pool,0,false);
        check(repeated.grant==r.grant && repeated.remaining==0,"request fanout first call wins");
    }
    check(sum==1,"strict shared allocation cap");HLWB.End();
}
check(totals[0]==3 && totals[1]==3 && totals[2]==3,"round robin does not starve later callers");

// Removing the next recipient advances to its successor, not the first entry.
ResetBudget();contexts=[Context(0),Context(1),Context(2)];
HLWB.Begin([fast],0);foreach(ctx in contexts)HLWB.Request(ctx,"q",pool,1,true);HLWB.End();
HLWB.Begin([fast],1);
foreach(i,ctx in contexts){local ticket=HLWB.Request(ctx,"q",pool,1,true);check(ticket.granted==(i==0?1:0),"cursor setup");}
HLWB.End();HLWB.Cleanup(contexts[1]);
HLWB.Begin([fast],2);
check(HLWB.Request(contexts[2],"q",pool,1,true).granted==1,"removed cursor preserves successor fairness");
check(HLWB.Request(contexts[0],"q",pool,1,true).granted==0,"removed cursor does not restart at first claimant");HLWB.End();

// Non-due contexts keep their request but do not receive credits.
ResetBudget();local a=Context(10),b=Context(11,"slow");pool={id="intervals",limit=4};
HLWB.Begin([fast,slow],0);HLWB.Request(a,"a",pool,4,true);HLWB.Request(b,"b",pool,4,true);HLWB.End();
HLWB.Begin([fast],1);local grant=HLWB.Request(a,"a",pool,4,true);
check(grant.granted==4 && HLWB.claims.len()==2,"only due loop receives credits");HLWB.End();
check(HLWB.claims.len()==2,"non-due demand retained without renewal");
HLWB.Begin([slow],2);grant=HLWB.Request(b,"b",pool,4,true);
check(grant.granted==4,"slow loop eventually receives its budget");
local old=grant.grant;
check(!HLWB.Inspect(a,old).valid,"cross-owner receipt rejected");
check(!HLWB.Inspect(b,{id=old.id,cycle=old.cycle}).valid,"forged clone rejected");
check(!HLWB.Inspect(b,null).valid,"missing receipt rejected");
HLWB.End();check(!HLWB.Inspect(b,old).valid,"receipt expires at end");
HLWB.Begin([slow],3);check(!HLWB.Inspect(b,old).valid,"previous cycle receipt rejected");HLWB.End();
check(HLWB.claims.len()==1,"eligible unrenewed request removed");
HLWB.Cleanup(a);check(HLWB.claims.len()==0 && !("_workBudgetClaims" in a),"cleanup removes claims");

// Disabled first request is latched until End, without retaining a pool.
ResetBudget();a=Context(1);HLWB.Begin([fast],0);
local disabled=HLWB.Request(a,"cancel",null,-99,false);
check(disabled.error=="" && !disabled.queued,"disabled cancels regardless of amount/descriptor");
check(!HLWB.Request(a,"cancel",{id="p",limit=3},3,true).queued,"same-cycle enable cannot bypass cancellation");
HLWB.End();check(HLWB.claims.len()==0,"cancel tombstone expires");

// Several independent jobs can share one receipt without multiplying credit.
ResetBudget();a=Context(5);pool={id="candidates",limit=3};
local captured=0,items=[BudgetBot(1),BudgetBot(2),BudgetBot(3)];
local capture=function(){++captured;return {items=items,kind="bots",params={},limit=32,
    rule=function(c,p,i,params){return {keep=true,score=i};}};};
HLWB.Begin([fast],0);grant=HLWB.Request(a,"request",pool,3,true);
local r=HLCE.Step(a,"A",capture,2,false,true,grant.grant,true);
check(r.waiting && r.busy && r.processed==0 && captured==0,"zero grant pauses without capture");HLWB.End();
++a._stepSerial;HLWB.Begin([fast],1);grant=HLWB.Request(a,"request",pool,3,true);
local ra=HLCE.Step(a,"A",capture,2,false,true,grant.grant,true);
local rb=HLCE.Step(a,"B",capture,2,false,true,grant.grant,true);
local rc=HLCE.Step(a,"C",capture,2,false,true,grant.grant,true);
check(ra.processed==2 && rb.processed==1 && rc.processed==0 && rc.waiting && captured==2,"shared receipt enforces actual entry cap across jobs");
check(HLWB.Inspect(a,grant.grant).remaining==0,"all and only three credits consumed");
HLCE.Step(a,"A",capture,256,true,true,grant.grant,true);
check(captured==2,"same-step fanout cannot reset/spend again");old=grant.grant;HLWB.End();
++a._stepSerial;HLWB.Begin([fast],2);grant=HLWB.Request(a,"request",pool,3,true);
ra=HLCE.Step(a,"A",capture,2,false,true,grant.grant,true);
rb=HLCE.Step(a,"B",capture,2,false,true,grant.grant,true);
check(ra.complete && rb.complete && captured==2 && HLWB.Inspect(a,grant.grant).remaining==0,"resumed jobs consume only remaining entries");
rc=HLCE.Step(a,"foreign",capture,8,false,true,old,true);
check(rc.failed && captured==2,"expired connected receipt never falls back to unlimited work");HLWB.End();
// Existing unconnected stepped evaluation keeps its original behavior.
++a._stepSerial;r=HLCE.Step(a,"unshared",capture,8,false,true);
check(r.complete && r.processed==3,"unconnected budget remains opt-in");
a.bot.valid=false;HLBA.Release({ctx=a,def={},released=false});
check(HLWB.claims.len()==0 && !("_candidateJobs" in a),"real invalid-bot release clears jobs and claims");

// Deterministic global caps and descriptor validation.
ResetBudget();HLWB.Begin([fast],0);a=Context(1);
check(HLWB.Request(a,"bad",{id="bad",limit=0},8,true).error!="","invalid pool rejected");
check(HLWB.Request(a,"bad2",{id="ok",limit=1},-1,true).error!="","negative demand rejected");
for(local i=0;i<32;++i)check(HLWB.Request(a,"pool"+i,{id="pool"+i,limit=2},1,true).queued,"pool capacity setup");
check(HLWB.Request(a,"overflow",{id="overflow",limit=2},1,true).error!="","pool cap");
local other=Context(2);check(HLWB.Request(other,"conflict",{id="pool0",limit=3},1,true).error!="","immutable pool mismatch");
HLWB.End();ResetBudget();HLWB.Begin([fast],0);a=Context(3);
for(local i=0;i<512;++i)check(HLWB.Request(a,"claim"+i,{id="p",limit=4096},256,true).queued,"claim cap setup");
check(HLWB.Request(a,"extra",{id="p",limit=4096},1,true).error!="","global claim cap");
HLWB.Cleanup(a);HLWB.End();check(HLWB.claims.len()==0,"bulk cleanup");

// Execute the production scheduler hooks, not just the isolated allocator.
ResetBudget();HLBA.StopAll();local bots=[BudgetBot(0),BudgetBot(1),BudgetBot(2)],scoreCalls=0,progress=[0,0,0];
local candidateItems=[];for(local i=0;i<32;++i)candidateItems.append(BudgetBot(i));
local tickPool={id="scheduler",limit=2};
local run=function(ctx) {
    local ticket=HLWB.Request(ctx,"request",tickPool,8,true);
    local result=HLCE.Step(ctx,"job",function(){return {items=candidateItems,kind="bots",params={},limit=32,
        rule=function(c,p,i,params){++scoreCalls;return {keep=true,score=i};}};},8,false,true,ticket.grant,true);
    check(!result.failed,"scheduler receipt rejected");progress[ctx.bot.id]=result.processed;
};
local def={id="budgeted",channels=[],startFn=run,updateFn=run};
HLBA.Start("fast",0.1,function(){return bots;},function(ctx){HLBA.Request(ctx,"apply",ctx.bot,def,true);});
HLBA.TickAll(0);check(scoreCalls==0,"first real scheduler cycle queues demand only");
for(local i=1;i<=9;++i) {
    scoreCalls=0;HLBA.TickAll(i*0.2);
    check(scoreCalls==2,"production scheduler enforces total candidate cap");
}
check(progress[0]==6 && progress[1]==6 && progress[2]==6,"production scheduler grants fairly over repeated updates");
HLBA.StopAll();check(HLWB.claims.len()==0,"scheduler stop releases all claims");
