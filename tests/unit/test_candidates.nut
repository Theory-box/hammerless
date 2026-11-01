local a=Bot(8),b=Bot(2),dead=Bot(3); dead.health=0;
local r=HLCE.Evaluate([a,null,b,dead],"bots",compiledRule,{},128);
Check(r.count==2 && r.examined==4 && r.rejected==2 && r.errors==0,"counters");
Check(r.item==a && r.score==0 && r.rows[1].score==-2,"compiled custom score");
local read=HLCE.Item(r,1,"bots");
Check(read.found && read.item==b && read.index==2 && read.score==-2,"rank inspection");
Check(!HLCE.Item(r,0.5,"bots").found && !HLCE.Item(r,0,"navareas").found,"invalid rank/type");
b.valid=false; Check(!HLCE.Item(r,1,"bots").found,"expired handle"); b.valid=true;
function equalRule(c,p,i,params) {return {keep=true,score=7};}
r=HLCE.Evaluate([a,b,a],"bots",equalRule,{},128);
Check(r.rows[0].index==0 && r.rows[1].index==1 && r.rows[2].index==2,"stable tie order");
local many=[]; for(local i=0;i<300;++i) many.append(a);
r=HLCE.Evaluate(many,"bots",equalRule,{},10000);
Check(r.examined==256 && r.count==128 && r.truncated,"bounds");
r=HLCE.Evaluate([a],"bots",equalRule,{},0);
Check(!r.found && r.truncated,"zero cap");
function reject(c,p,i,params) {return {keep=false,score=1};}
r=HLCE.Evaluate([a],"bots",reject,{},128); Check(r.rejected==1 && !r.found,"keep filter");
function broken(c,p,i,params) {throw "broken";}
r=HLCE.Evaluate([a,b],"bots",broken,{},128); Check(r.errors==2 && !r.found && r.error=="broken","rule exceptions");
function bad(c,p,i,params) {return {keep=1,score=2};}
r=HLCE.Evaluate([a],"bots",bad,{},128); Check(r.errors==1,"invalid output type");
r=HLCE.Evaluate(areas,"navareas",equalRule,{},128);
Check(r.count==2 && HLCE.Item(r,1,"navareas").item==areas[1],"nav areas");
Check(HLCE.Evaluate(null,"bots",equalRule,{},128).count==0,"null input");
