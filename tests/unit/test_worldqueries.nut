function Vector(x,y,z) { return {x=x,y=y,z=z}; }
function Check(value,message) { if (!value) throw message; }
class Area {
    id=0; distance=0; blocked=false;
    constructor(i,d) { id=i; distance=d; }
    function GetID() { return id; }
    function GetCenter() { return Vector(id,0,0); }
    function GetDistanceSquaredToPoint(p) { return distance; }
    function IsBlocked(team,ignore) { return blocked; }
    function IsUnderwater() { return false; }
}
::areas <- [Area(1,4),Area(2,1),Area(3,1)];
::NavMesh <- {
    ready=true, lastRadius=0, calls=0, lastLength=0, lastIgnore=null,
    GetNavAreaCount=function() { return ready ? areas.len() : 0; },
    GetNavAreaByID=function(id) { foreach(a in areas) if (a.id==id) return a; return null; },
    GetNearestNavArea=function(p,r,los,ground) { lastRadius=r; return areas[0]; },
    GetNavAreasInRadius=function(p,r,t) { lastRadius=r; foreach(i,a in areas) t[i] <- a; },
    NavAreaBuildPath=function(a,b,p,length,team,ignore) { ++calls; lastLength=length; lastIgnore=ignore; return a.id!=3; }
};
function RunTests() {
    NavMesh.ready=false;
    Check(!HLWQ.Nearest(Vector(0,0,0),100,true,true).available,"missing nav falsely available");
    NavMesh.ready=true;
    local r=HLWQ.Nearest(Vector(0,0,0),999999,true,true);
    Check(r.available && r.found && r.area==areas[0] && NavMesh.lastRadius==4096,"nearest bounds");
    r=HLWQ.Nearby(Vector(0,0,0),10000,2);
    Check(r.count==2 && r.areas[0].id==2 && r.areas[1].id==3,"distance/id order and cap");
    Check(!HLWQ.Info(Area(1,0),3).found,"stale area accepted");
    Check(!HLWQ.Item(r.areas,2).found,"out of range index");
    Check(!HLWQ.Item(r.areas,null).found,"invalid index");
    Check(HLWQ.Item(r.areas,0).area==areas[1],"typed item");
    Check(!HLWQ.Path(null,areas[0],50,3,false).found,"null endpoint");
    areas[0].blocked=true;
    Check(!HLWQ.Path(areas[0],areas[1],500,3,false).exists && NavMesh.calls==0,"blocked endpoint searched");
    Check(HLWQ.Path(areas[0],areas[1],999999,3,true).exists && NavMesh.lastLength==32768 && NavMesh.lastIgnore,"path limits");
    Check(!HLWQ.Path(areas[2],areas[1],50,3,false).exists,"disconnected path");
    print("PASS world queries runtime\n");
}
