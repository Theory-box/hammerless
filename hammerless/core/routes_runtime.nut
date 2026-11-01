::HLRoute <- {};
HLRoute.Result <- function(){return {found=false,position=Vector(0,0,0),index=-1,count=0,distance=0.0,direction=1,completed=false,error=""};};
HLRoute.Number <- function(v){return (typeof v=="integer"||typeof v=="float")&&v==v&&v<1e30&&v> -1e30;};
HLRoute.Valid <- function(route){
    if(typeof route!="array"||route.len()==0||route.len()>256)return false;
    foreach(p in route){try{if(!HLRoute.Number(p.x)||!HLRoute.Number(p.y)||!HLRoute.Number(p.z))return false;}catch(e){return false;}}
    return true;
};
HLRoute.Point <- function(route,index){
    local r=HLRoute.Result();
    if(!HLRoute.Valid(route)){r.error="Missing or invalid route";return r;}
    r.count=route.len();
    if(!HLRoute.Number(index)||index!=floor(index)||index<0||index>=r.count){r.error="Route index out of range";return r;}
    r.index=index.tointeger();local p=route[r.index];r.position=Vector(p.x,p.y,p.z);r.found=true;return r;
};
HLRoute.Nearest <- function(route,origin){
    local r=HLRoute.Result();
    if(!HLRoute.Valid(route)){r.error="Missing or invalid route";return r;}
    try{if(!HLRoute.Number(origin.x)||!HLRoute.Number(origin.y)||!HLRoute.Number(origin.z))throw "bad";}catch(e){r.error="Invalid origin";return r;}
    local best=null,chosen=0;
    foreach(i,p in route){local x=p.x-origin.x,y=p.y-origin.y,z=p.z-origin.z;local d=x*x+y*y+z*z;if(best==null||d<best){best=d;chosen=i;}}
    r=HLRoute.Point(route,chosen);r.distance=sqrt(best);return r;
};
HLRoute.Advance <- function(route,index,direction,mode){
    local r=HLRoute.Point(route,index);if(!r.found)return r;
    if(!HLRoute.Number(direction)||direction==0){r.found=false;r.error="Direction must be positive or negative";return r;}
    if(mode!="ONCE"&&mode!="LOOP"&&mode!="PINGPONG"){r.found=false;r.error="Invalid route mode";return r;}
    local step=direction>0?1:-1,next=r.index+step;
    if(next<0||next>=r.count){
        if(mode=="ONCE"){r.completed=true;r.direction=step;return r;}
        if(mode=="LOOP")next=next<0?r.count-1:0;
        else{step= -step;next=r.count==1?0:r.index+step;}
    }
    r=HLRoute.Point(route,next);r.direction=step;return r;
};
