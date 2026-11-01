"""Execute actual Blender-generated stable-selection functions in Squirrel."""
import os,sys,json
from pathlib import Path
if os.environ.get('SQUIRREL_PYTHONPATH'):sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
import squirrel
root=Path(__file__).resolve().parents[2]
prelude=(root/'tests/unit/test_listtools.nut').read_text().split('function RunTests()')[0]
data=json.loads(Path(sys.argv[1]).read_text())
for typ,spec in data.items():
    vm=squirrel.SQVM()
    vm.execute(prelude+'\n'+(root/'hammerless/core/list_tools_runtime.nut').read_text()+'\n'+(root/'hammerless/core/candidate_runtime.nut').read_text())
    vm.execute('::step <- function(ctx) {'+spec['body']+'};')
    vm.execute('::kind <- "'+typ+'";')
    vm.execute('''
    local first=kind=="bots" ? Bot(1) : areas[0];
    local second=kind=="bots" ? Bot(2) : areas[1];
    local s1=5.0,s2=4.0;
    local scorer=function(c,p,i,params) {return {keep=true,score=c==first?s1:s2};};
    local snap=function(items) {return HLCE.Evaluate(items,kind,scorer,{},128);};
    local ctx={state={},params={results=snap([first,second]),reset=false,delta=0.5,margin=1.0,hold=2.0}};
    local other={state={},params=clone ctx.params};
    function near(a,b) {Check(fabs(a-b)<0.0001,"number mismatch "+a+" vs "+b);}
    step(ctx);Check(ctx.state["Group/item"]==first && ctx.state["Group/changed"],"initial selection");
    near(ctx.state["Group/held"],0);Check(!ctx.state["Group/missing"],"initial wrongly missing");
    s2=20;ctx.params.results=snap([first,second]);
    step(ctx);Check(ctx.state["Group/item"]==first && !ctx.state["Group/changed"],"hold time ignored");
    step(ctx);step(ctx);step(ctx);
    Check(ctx.state["Group/item"]==second && ctx.state["Group/changed"],"better challenger not selected");
    near(ctx.state["Group/held"],0);
    s1=21;ctx.params.delta=10;ctx.params.results=snap([first,second]);step(ctx);
    Check(ctx.state["Group/item"]==second && !ctx.state["Group/changed"],"margin tie should retain");
    s1=21.1;ctx.params.results=snap([first,second]);step(ctx);
    Check(ctx.state["Group/item"]==first,"strict score improvement missing");
    // Rescore retained item, do not compare against its stale acquisition score.
    s1=1;s2=2.1;ctx.params.results=snap([first,second]);step(ctx);
    Check(ctx.state["Group/item"]==second,"current score not refreshed");
    ctx.params.delta=-1;local held=ctx.state["Group/held"];step(ctx);near(ctx.state["Group/held"],held);
    // Membership loss bypasses hold, including deliberately truncated snapshots.
    ctx.params.results=HLCE.Evaluate([first,second],kind,function(c,p,i,a){return {keep=true,score=0};},{},1);
    step(ctx);Check(ctx.state["Group/item"]==first && ctx.state["Group/missing"] && ctx.state["Group/truncated"],"snapshot loss");
    ctx.params.results=snap([]);step(ctx);
    Check(!ctx.state["Group/has"] && ctx.state["Group/item"]==null && ctx.state["Group/changed"],"empty clear");
    step(ctx);Check(!ctx.state["Group/changed"],"empty repeat changed");
    ctx.params.results=snap([first,second]);step(ctx);
    ctx.params.reset=true;step(ctx);Check(!ctx.state["Group/has"] && ctx.state["Group/changed"],"reset");
    step(ctx);Check(!ctx.state["Group/changed"],"repeat reset");
    // Separate copies and separate bot contexts have isolated memory.
    step(other);Check(other.state["Group/has"] && !ctx.state["Group/has"],"cross-bot state");
    Check(other.state["Group/item"]==other.state["Group.001/item"],"copy outcome");
    other.state["Group/held"] = 999;Check(other.state["Group.001/held"]!=999,"copy isolation");
    ''')
print('STABLE_SELECTION_VM_OK')
