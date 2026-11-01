local calls=0,captures=0;
function Score(c,p,i,params){return {keep=true,score=p.x+params.bias+params.offset.x};}
local a=Bot(1),b=Bot(4),c=Bot(7),items=[a,null,b,c],params={bias=10,offset=Vector(2,0,0)};
local ctx={_stepSerial=1};
local capture=function(){++captures;return {items=items,kind="bots",rule=Score,params=params,limit=128};};
local r=HLCE.Step(ctx,"job",capture,2,false,true);
Check(r.busy && r.processed==2 && r.rejected==1 && r.total==4 && r.progress==0.5,"first budget includes invalid");
Check(r.results==null && r.items.len()==0 && r.rows.len()==0 && !r.found,"no partial public results");
r=HLCE.Step(ctx,"job",function(){throw "fanout captured";},256,true,false);
Check(r.busy && r.processed==2 && captures==1,"first invocation wins reset/cancel");
items.clear();params.bias=1000;params.offset.x=900;b.x=9;now=1.5;++ctx._stepSerial;
r=HLCE.Step(ctx,"job",function(){throw "resumed captured";},2,false,true);
Check(r.complete && r.age==1.5 && r.count==3 && r.score==21 && r.processed==4,"input/params snapshot, world reads at step time");
Check(r.results.rows[0].item==b && r.results.rows[0].position.x==9,"mixed-time sampled position");
Check(ctx._candidateJobs.job.inputs.len()==0 && ctx._candidateJobs.job.params==null && ctx._candidateJobs.job.rule==null,"completion drops input references");
r.results.rows[0].position.x=-800;r.results.rows[0].score=-100;r.results.items.clear();r.rows.clear();r.position.x=-100;
++ctx._stepSerial;
local fresh=HLCE.Step(ctx,"job",function(){throw "completed captured";},1,false,true);
Check(fresh.complete && fresh.score==21 && fresh.results.rows[0].position.x==9 && fresh.results.items.len()==3,"defensive completed snapshots");
items=[a];params={bias=0,offset=Vector(0,0,0)};++ctx._stepSerial;
r=HLCE.Step(ctx,"job",capture,1,true,false);
Check(r.cancelled && r.results==null && captures==1 && ctx._candidateJobs.job.inputs.len()==0,"disable wins reset without capture");
++ctx._stepSerial;r=HLCE.Step(ctx,"job",capture,1,false,true);
Check(r.complete && r.total==1 && captures==2 && r.score==1,"re-enable starts fresh");
local same=HLCE.Step(ctx,"job",capture,1,true,true);Check(captures==2,"same serial reset ignored");
++ctx._stepSerial;r=HLCE.Step(ctx,"job",capture,1,true,true);Check(captures==3 && r.complete,"next serial reset accepted");

// Incremental output equals immediate evaluation for each list type.
foreach(kind in ["bots","navareas"]) {
    local list=kind=="bots"?[a,null,b,c]:[areas[0],null,areas[1]];
    local captured={items=list,kind=kind,rule=Score,params=params,limit=2};
    local jobctx={_stepSerial=1};
    local result=HLCE.Step(jobctx,"equivalent",function(){return captured;},1,false,true);
    while(result.busy){++jobctx._stepSerial;result=HLCE.Step(jobctx,"equivalent",function(){throw "recaptured";},1,false,true);}
    local immediate=HLCE.Evaluate(list,kind,Score,params,2);
    Check(result.complete && result.count==immediate.count && result.errors==immediate.errors && result.rejected==immediate.rejected && result.truncated==immediate.truncated,"incremental counters match immediate");
    foreach(i,row in immediate.rows)Check(result.results.rows[i].item==row.item && result.results.rows[i].score==row.score && result.results.rows[i].index==row.index,"incremental order matches immediate");
}
local many=[];for(local i=0;i<300;++i)many.append(a);
local big={_stepSerial=1}, source={items=many,kind="bots",rule=Score,params=params,limit=999};
r=HLCE.Step(big,"big",function(){return source;},999,false,true);
Check(r.complete && r.total==256 && r.processed==256 && r.count==128 && r.truncated,"hard bounds");
local sliced={_stepSerial=1}, updates=1;
r=HLCE.Step(sliced,"default",function(){return source;},8,false,true);
while(r.busy){++sliced._stepSerial;++updates;r=HLCE.Step(sliced,"default",function(){throw "default job recaptured";},8,false,true);}
Check(updates==32 && r.complete && r.processed==256,"256 entries at budget8 complete in exactly32 updates");
local empty={_stepSerial=1};
r=HLCE.Step(empty,"empty",function(){return {items=[],kind="bots",rule=Score,params=params,limit=32};},8,false,true);
Check(r.complete && r.progress==1 && r.total==0 && !r.found && r.results!=null && !r.failed,"empty complete is distinct from error");
local fail={_stepSerial=1};
r=HLCE.Step(fail,"bad",function(){return {items=null,kind="bots",rule=Score,params=params,limit=32};},8,false,true);
Check(r.failed && r.status=="ERROR" && r.errors==1 && r.results==null,"capture error structured");
++fail._stepSerial;
r=HLCE.Step(fail,"bad",function(){throw "error recaptured";},8,false,true);Check(r.failed,"error latched until reset");
local invalid={_stepSerial=1};
r=HLCE.Step(invalid,"bad",function(){throw "invalid budget captured";},-1,false,true);Check(r.failed && r.processed==0 && r.error.find("Budget")!=null,"negative budget error without capture");
++invalid._stepSerial;r=HLCE.Step(invalid,"bad",capture,0,true,true);Check(r.complete && r.processed==1,"zero budget clamps to one");
local ruleFailure=function(c,p,i,params){throw "rule failed";}, rc={_stepSerial=1};
r=HLCE.Step(rc,"errors",function(){return {items=[a,b],kind="bots",rule=ruleFailure,params={},limit=32};},8,false,true);
Check(r.complete && r.errors==2 && !r.failed && r.error=="rule failed","rule errors counted without failing job");
local capped={_stepSerial=1};
for(local i=0;i<32;++i)Check(HLCE.Step(capped,"job"+i,function(){throw "disabled capture";},1,false,false).cancelled,"cancel slot");
r=HLCE.Step(capped,"overflow",capture,8,false,true);Check(r.failed && capped._candidateJobs.len()==32,"capacity error");
// Real action release clears jobs even when the entity disappeared.
ctx.bot<-null;ctx.overrides<-[];HLBA.Release({ctx=ctx,def={},released=false});
Check(!("_candidateJobs" in ctx),"invalid bot cleanup");
local cleanup={_stepSerial=1,_releasing=true};
r=HLCE.Step(cleanup,"cleanup",function(){throw "cleanup captured";},8,false,true);
Check(r.failed && !("_candidateJobs" in cleanup),"runtime cleanup guard");
local mutable=Vector(3,0,0), moving={IsValid=function(){return true;},GetHealth=function(){return 100;},GetClassname=function(){return "infected";},GetOrigin=function(){return mutable;}};
local sampled={_stepSerial=1};
local input={items=[moving,b],kind="bots",rule=Score,params=params,limit=32};
HLCE.Step(sampled,"sample",function(){return input;},1,false,true);mutable.x=500;++sampled._stepSerial;
r=HLCE.Step(sampled,"sample",function(){throw "recaptured";},1,false,true);
Check(r.results.rows[1].position.x==3,"previously sampled position is copied");
