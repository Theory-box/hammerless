// Results are fresh local snapshots; rules never share mutable per-bot state.
::HLCE <- {};
HLCE.Empty <- function() {
    return {found=false, count=0, examined=0, rejected=0, errors=0, truncated=false,
            item=null, items=[], score=0.0, index=-1, position=Vector(0,0,0), rows=[], kind="", error=""};
};
HLCE.Evaluate <- function(items, kind, rule, params, limit) {
    local r=Empty(); r.kind=kind;
    if (typeof items != "array") return r;
    local n=items.len()<256 ? items.len() : 256;
    r.truncated=items.len()>n;
    limit=HLLT.Number(limit,0,128).tointeger();
    for (local i=0;i<n;++i) {
        ++r.examined;
        local p=HLLT.Position(items[i],kind);
        if (p==null) { ++r.rejected; continue; }
        local v=null;
        try { v=rule(items[i],p,i,params); }
        catch(error) {
            ++r.errors;
            if(r.error=="") { local message=error.tostring(); r.error=message.len()>256 ? message.slice(0,256) : message; }
            continue;
        }
        if (typeof v!="table" || !("keep" in v) || typeof v.keep!="bool" ||
            !("score" in v) || (typeof v.score!="integer" && typeof v.score!="float") ||
            v.score!=v.score || v.score>1.0e30 || v.score< -1.0e30) {
            ++r.errors;
            if(r.error=="") r.error="Rule must return boolean Keep and finite numeric Score between -1e30 and 1e30";
            continue;
        }
        if (!v.keep) { ++r.rejected; continue; }
        r.rows.append({item=items[i],position=p,index=i,score=v.score});
    }
    r.rows.sort(function(a,b) { return a.score>b.score ? -1 : (a.score<b.score ? 1 : (a.index<b.index ? -1 : (a.index>b.index ? 1 : 0))); });
    if (r.rows.len()>limit) { r.truncated=true; r.rows.resize(limit); }
    foreach(row in r.rows) r.items.append(row.item);
    r.count=r.rows.len(); r.found=r.count>0;
    if(r.found) { local v=r.rows[0]; r.item=v.item; r.score=v.score; r.position=v.position; r.index=v.index; }
    return r;
};
HLCE.Item <- function(result,index,kind) {
    local r=Empty();
    if(typeof result!="table" || !("kind" in result) || result.kind!=kind || !("rows" in result) ||
       (typeof index!="integer" && typeof index!="float") || index!=index || index<0 || index>=result.rows.len() || index!=floor(index)) return r;
    local v=result.rows[index.tointeger()];
    if(HLLT.Position(v.item,kind)==null) return r;
    r.found=true; r.item=v.item; r.score=v.score; r.position=v.position; r.index=v.index;
    return r;
};
