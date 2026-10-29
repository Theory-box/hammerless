// Read-only, bounded world queries. Nav areas are never entity handles.
::HLWQ <- {};
HLWQ.Empty <- function() {
    return {available=false, found=false, area=null, areas=[], count=0,
            position=Vector(0,0,0), id=-1, blocked=false, underwater=false, exists=false};
};
HLWQ.Bound <- function(v, low, high) {
    if ((typeof v != "integer" && typeof v != "float") || v != v) return low;
    return v < low ? low : (v > high ? high : v);
};
HLWQ.Ready <- function() {
    return "NavMesh" in getroottable() && NavMesh.GetNavAreaCount() > 0;
};
HLWQ.Area <- function(a) {
    if (a == null) return null;
    try { return NavMesh.GetNavAreaByID(a.GetID()) == a ? a : null; }
    catch (error) { return null; }
};
HLWQ.Nearest <- function(p, radius, los, ground) {
    local r=Empty();
    if (!Ready()) return r;
    r.available=true;
    local a=NavMesh.GetNearestNavArea(p, Bound(radius,1,4096),los,ground);
    if (a != null) { r.found=true; r.area=a; r.position=a.GetCenter(); r.id=a.GetID(); }
    return r;
};
HLWQ.Nearby <- function(p, radius, limit) {
    local r=Empty();
    if (!Ready()) return r;
    r.available=true;
    local t={}, ranked=[];
    NavMesh.GetNavAreasInRadius(p,Bound(radius,1,4096),t);
    foreach (a in t) ranked.append({area=a, distance=a.GetDistanceSquaredToPoint(p), id=a.GetID()});
    ranked.sort(function(a,b) { return a.distance < b.distance ? -1 : (a.distance > b.distance ? 1 : (a.id < b.id ? -1 : (a.id > b.id ? 1 : 0))); });
    local n=Bound(limit,1,128).tointeger();
    for (local i=0; i<ranked.len() && i<n; ++i) r.areas.append(ranked[i].area);
    r.count=r.areas.len(); r.found=r.count>0;
    if (r.found) { r.area=r.areas[0]; r.position=r.area.GetCenter(); }
    return r;
};
HLWQ.Info <- function(a, team) {
    local r=Empty();
    if (!Ready()) return r;
    r.available=true; a=Area(a);
    if (a == null) return r;
    r.found=true; r.area=a; r.id=a.GetID(); r.position=a.GetCenter();
    r.blocked=a.IsBlocked(team,false); r.underwater=a.IsUnderwater();
    return r;
};
HLWQ.Item <- function(areas,index) {
    local r=Empty();
    if (!Ready()) return r;
    r.available=true;
    if (typeof areas != "array" || (typeof index != "integer" && typeof index != "float") ||
        index != index || index < 0 || index >= areas.len()) return r;
    local a=Area(areas[index.tointeger()]);
    if (a != null) { r.found=true; r.area=a; r.position=a.GetCenter(); r.id=a.GetID(); }
    return r;
};
HLWQ.Path <- function(a,b,length,team,ignore) {
    local r=Empty();
    if (!Ready()) return r;
    r.available=true; a=Area(a); b=Area(b);
    if (a == null || b == null) return r;
    r.found=true;
    if (!ignore && (a.IsBlocked(team,false) || b.IsBlocked(team,false))) return r;
    r.exists=NavMesh.NavAreaBuildPath(a,b,b.GetCenter(),Bound(length,1,32768),team,ignore);
    return r;
};
