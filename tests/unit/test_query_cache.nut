::now<-0.0;function Time(){return now;}
function check(ok,message){if(!ok)throw message;}
class CacheVector {
    x=0;y=0;z=0;
    constructor(a,b,c){x=a;y=b;z=c;}
    function _add(v){return CacheVector(x+v.x,y+v.y,z+v.z);}
    function _mul(v){return CacheVector(x*v,y*v,z*v);}
}
function Vector(x,y,z){return CacheVector(x,y,z);}
class CacheArea {
    id=0;position=null;
    constructor(i){id=i;position=Vector(i,0,0);}
    function GetID(){return id;}
    function GetCenter(){return position;}
    function GetDistanceSquaredToPoint(p){local d=position.x-p.x;return d*d;}
}
::areas<-[CacheArea(1),CacheArea(2),CacheArea(3)];
::navReady<-true;::navThrow<-false;::nearbyCalls<-0;::ambientCalls<-0;
::NavMesh<-{
    GetNavAreaCount=function(){return navReady?areas.len():0;},
    GetNavAreaByID=function(id){foreach(a in areas)if(a.id==id)return a;return null;},
    GetNavAreasInRadius=function(p,r,out){++nearbyCalls;if(navThrow)throw "nav failed";foreach(i,a in areas)out[i]<-a;}
};
::HL_AmbientData<-{root=-1,nodes=[],planes=[],leaves=[[0,1,0,false]],probes=[[0,0,0,0.2,0.3,0.4]]};
local actualAmbient=HLWQ_Ambient;
HLWQ_Ambient=function(p){++ambientCalls;return actualAmbient(p);};
local pool={id="shared",capacity=64},p=Vector(0,0,0);
local r=HLQC.ReadNearby(pool,p,512,2,0.25,false);
check(r.available && r.refreshed && !r.cache_hit && nearbyCalls==1 && r.count==2,"first actual nav query");
now=0.1;r=HLQC.ReadNearby(pool,p,512,2,0.25,false);
check(r.cache_hit && !r.refreshed && r.age==0.1 && nearbyCalls==1,"hit avoids actual nearby query");
// Each caller chooses its own TTL; reading with a longer TTL doesn't renew age.
r=HLQC.ReadNearby(pool,p,512,2,0.05,false);check(!r.cache_hit && nearbyCalls==2,"stricter reader refreshes");
now=0.35;r=HLQC.ReadNearby(pool,p,512,2,0.25,false);check(!r.cache_hit && nearbyCalls==3,"age equal TTL expires");
r.areas.clear();r.position.x=800;
r=HLQC.ReadNearby(pool,p,512,2,1,false);
check(r.cache_hit && r.areas.len()==2 && r.position.x==1,"snapshot mutations do not corrupt cache");
// Engine-owned mutable vectors are copied when inserting too.
areas[0].position.x=77;r=HLQC.ReadNearby(pool,p,512,2,1,false);check(r.position.x==1,"cached sample position copied");areas[0].position.x=1;
r=HLQC.ReadNearby(pool,p,512,1,1,false);check(!r.cache_hit && r.count==1,"limit participates in exact key");
r=HLQC.ReadNearby(pool,Vector(0.000001,0,0),512,1,1,false);check(!r.cache_hit,"nearby positions not string-rounded together");
r=HLQC.ReadNearby(pool,p,9000,999,1,false);local before=nearbyCalls;
r=HLQC.ReadNearby(pool,p,4096,128,1,false);check(r.cache_hit && nearbyCalls==before,"equivalent clamped arguments share key");
// A replaced nav handle invalidates the whole cached query and reruns it.
areas[0]=CacheArea(1);before=nearbyCalls;r=HLQC.ReadNearby(pool,p,512,2,1,false);
check(!r.cache_hit && nearbyCalls==before+1 && r.areas[0]==areas[0],"stale nav handles refresh entire result");
navReady=false;r=HLQC.ReadNearby(pool,p,512,2,1,false);check(!r.available && r.error!="" && !r.cache_hit,"nav readiness checked on hits");
navReady=true;r=HLQC.ReadNearby(pool,p,512,2,1,false);check(!r.cache_hit && r.available,"unavailable result not cached");
// Refresh failure deletes the prior success; there is no stale fallback.
navThrow=true;r=HLQC.ReadNearby(pool,p,512,2,1,true);check(!r.available && r.error=="nav failed" && r.refreshed,"forced failure reports query error");
navThrow=false;before=nearbyCalls;r=HLQC.ReadNearby(pool,p,512,2,1,false);
check(!r.cache_hit && nearbyCalls==before+1,"failed force refresh cannot resurrect old success");
r=HLQC.ReadNearby(pool,p,512,2,0,false);check(!r.cache_hit,"zero TTL bypasses");
before=nearbyCalls;r=HLQC.ReadNearby(pool,p,512,2,1,false);check(!r.cache_hit && nearbyCalls==before+1,"zero TTL removes old entry and does not store");
before=nearbyCalls;r=HLQC.ReadNearby(pool,p,512,2,-1,false);check(r.error!="" && nearbyCalls==before,"invalid TTL performs no query");
r=HLQC.ReadNearby(pool,Vector(1e35,0,0),512,2,1,false);check(r.error!="" && nearbyCalls==before,"invalid position performs no query");

r=HLQC.ReadAmbient(pool,p,1,false);check(r.available && r.refreshed && ambientCalls==1,"actual ambient query");
local brightness=r.brightness;r.color.x=99;
r=HLQC.ReadAmbient(pool,p,1,false);check(r.cache_hit && ambientCalls==1 && r.color.x<1 && r.brightness==brightness,"ambient vector snapshot isolation");
HL_AmbientData=null;r=HLQC.ReadAmbient(pool,p,1,true);check(!r.available && r.error!="","missing ambient not cached");
before=ambientCalls;r=HLQC.ReadAmbient(pool,p,1,false);check(ambientCalls==before+1 && !r.cache_hit,"missing ambient repeats query instead of caching failure");
HL_AmbientData={root=-1,nodes=[],planes=[],leaves=[[0,1,0,false]],probes=[[0,0,0,0.2,0.3,0.4]]};
r=HLQC.ReadAmbient(pool,p,1,false);now=0.01;before=ambientCalls;r=HLQC.ReadAmbient(pool,p,1,false);
check(!r.cache_hit && ambientCalls==before+1,"backwards clock clears old caches");
check(HLQC.Invalidate(pool).removed==1 && HLQC.count==0,"explicit invalidation reports removed count");

// Per-pool LRU respects hits; global LRU evicts across pool boundaries.
HLQC.Clear();now=1;pool={id="small",capacity=2};
HLQC.ReadAmbient(pool,Vector(1,0,0),60,false);HLQC.ReadAmbient(pool,Vector(2,0,0),60,false);
HLQC.ReadAmbient(pool,Vector(1,0,0),60,false);HLQC.ReadAmbient(pool,Vector(3,0,0),60,false);
check(HLQC.ReadAmbient(pool,Vector(1,0,0),60,false).cache_hit,"recent pool entry survives");
check(!HLQC.ReadAmbient(pool,Vector(2,0,0),60,false).cache_hit,"least recently used pool entry evicted");
HLQC.Clear();
for(local group=0;group<4;++group)for(local i=0;i<128;++i)HLQC.ReadAmbient({id="p"+group,capacity=128},Vector(i,0,0),60,false);
check(HLQC.count==512,"global capacity filled");
HLQC.ReadAmbient({id="p0",capacity=128},Vector(0,0,0),60,false);
HLQC.ReadAmbient({id="extra",capacity=128},p,60,false);
check(HLQC.count==512 && HLQC.ReadAmbient({id="p0",capacity=128},Vector(0,0,0),60,false).cache_hit,"global recent entry survives");
check(!HLQC.ReadAmbient({id="p0",capacity=128},Vector(1,0,0),60,false).cache_hit,"global oldest entry evicted");
HLQC.Clear();
for(local i=0;i<16;++i)check(HLQC.ReadAmbient({id="pool"+i,capacity=1},p,1,false).available,"pool setup");
before=ambientCalls;r=HLQC.ReadAmbient({id="overflow",capacity=1},p,1,false);
check(r.error!="" && ambientCalls==before && HLQC.pools.len()==16,"pool cap no backend work");
r=HLQC.ReadAmbient({id="pool0",capacity=2},p,1,false);check(r.error!="","conflicting descriptor rejected");
HLQC.Clear();check(HLQC.count==0 && HLQC.pools.len()==0 && HLQC.lastTime==null,"round reset clears definitions and entries");
