class Vec {
    x=0; y=0; z=0;
    constructor(a,b,c) { x=a; y=b; z=c; }
    function _sub(b) { return Vec(x-b.x,y-b.y,z-b.z); }
    function LengthSqr() { return x*x+y*y+z*z; }
}
function Vector(x,y,z) { return Vec(x,y,z); }
function Check(v,s) { if (!v) throw s; }
class Bot {
    x=0; valid=true; health=100; classname="infected"; bot=true;
    constructor(p) { x=p; }
    function IsValid() { return valid; }
    function GetClassname() { return classname; }
    function GetHealth() { return health; }
    function GetOrigin() { return Vector(x,0,0); }
}
function IsPlayerABot(b) { return b.bot; }
::randomCalls <- 0;
function RandomInt(a,b) { ++randomCalls; return 1; }
class Area {
    id=0;
    constructor(i) { id=i; }
    function GetID() { return id; }
    function GetCenter() { return Vector(id,0,0); }
}
::areas <- [Area(1),Area(2)];
::NavMesh <- {GetNavAreaByID=function(id) { foreach(a in areas) if(a.id==id) return a; return null; }};
function Q(items,kind,mode, radius=0,limit=128,index=0) {
    return HLLT.Query(items,kind,mode,Vector(0,0,0),radius,limit,index);
}
function RunTests() {
    local a=Bot(8), b=Bot(2), dead=Bot(1), human=Bot(0);
    dead.health=0; human.classname="player"; human.bot=false;
    local witch=Bot(4); witch.classname="witch";
    Check(Q([witch],"bots","COUNT").count==1,"Find Bots ALL witch dropped");
    local list=[a,null,dead,human,b];
    Check(Q(list,"bots","COUNT").count==2,"invalid/dead/human counted");
    Check(Q(list,"bots","CLOSEST").item==b,"closest selection");
    Check(!Q(list,"bots","ITEM",0,128,1).found,"invalid index should stay invalid");
    Check(Q(list,"bots","ITEM",0,128,4).item==b,"original index changed");
    Check(!Q(list,"bots","ITEM",0,128,0.5).found,"fractional index accepted");
    Check(Q(list,"bots","RANDOM").item==b && randomCalls==2,"random not one roll per valid item");
    local r=Q(list,"bots","FILTER",4);
    Check(r.count==1 && r.items[0]==b && !r.truncated,"radius filter");
    r=Q(list,"bots","FILTER",0,1);
    Check(r.count==1 && r.items[0]==a && r.truncated,"output truncation");
    r=Q(list,"bots","FILTER",0,0);
    Check(r.count==0 && !r.found && r.truncated,"zero result cap");
    local many=[]; for(local i=0;i<300;++i) many.append(a);
    r=Q(many,"bots","COUNT");
    Check(r.count==256 && r.truncated,"scan cap");
    Check(!Q(many,"bots","ITEM",0,128,256).found,"index beyond scan cap");
    r=Q(many,"bots","FILTER",0,9999);
    Check(r.count==128 && r.truncated,"filter cap");
    Check(Q([areas[0],Area(2),areas[1]],"navareas","COUNT").count==2,"stale nav handle accepted");
    Check(Q(areas,"navareas","CLOSEST").item==areas[0],"nav closest");
    Check(!Q(null,"bots","COUNT").found,"non-array input");
    r=HLLT.SetQuery([b,a,b,dead,null,human],[a,witch],"bots","UNION",128);
    Check(r.count==3 && r.items[0]==b && r.items[1]==a && r.items[2]==witch && !r.truncated,"stable union");
    r=HLLT.SetQuery([b,a,b,witch],[a,b,dead],"bots","INTERSECTION",128);
    Check(r.count==2 && r.items[0]==b && r.items[1]==a,"intersection order/duplicates");
    r=HLLT.SetQuery([b,a,b,witch],[a,b],"bots","DIFFERENCE",128);
    Check(r.count==1 && r.items[0]==witch,"difference");
    r=HLLT.SetQuery([a,a,b],null,"bots","UNIQUE",1);
    Check(r.count==1 && r.items[0]==a && r.truncated,"unique output cap");
    r=HLLT.SetQuery([a,a],null,"bots","UNIQUE",1);
    Check(r.count==1 && !r.truncated,"duplicates incorrectly truncate");
    r=HLLT.SetQuery([a],null,"bots","UNIQUE",0);
    Check(!r.found && r.truncated,"zero output cap");
    r=HLLT.SetQuery(null,[b],"bots","UNION",128);
    Check(r.found && r.items[0]==b,"missing first list treated as empty");
    r=HLLT.SetQuery([areas[0],Area(2),areas[0],areas[1]],[],"navareas","UNIQUE",128);
    Check(r.count==2 && r.items[1]==areas[1],"nav handle validation/identity");
    local prefix=[]; for(local i=0;i<256;++i) prefix.append(a); prefix.append(b);
    r=HLLT.SetQuery([b],prefix,"bots","DIFFERENCE",128);
    Check(r.count==1 && r.truncated,"membership beyond prefix must report incomplete");
    r=HLLT.SetQuery([b],prefix,"bots","INTERSECTION",128);
    Check(r.count==0 && r.truncated,"intersection prefix limit");
    local unique=[]; for(local i=0;i<260;++i) unique.append(Bot(i));
    r=HLLT.SetQuery(unique,[],"bots","UNIQUE",9999);
    Check(r.count==128 && r.truncated && unique.len()==260,"hard limits or input mutated");
    a.valid=false;
    Check(Q(list,"bots","COUNT").count==1,"deleted handle retained");
}
