// Snapshot queries: at most 256 input handles examined, at most 128 filtered results.
::HLLT <- {};
HLLT.Number <- function(v, low, high) {
    if ((typeof v != "integer" && typeof v != "float") || v != v) return low;
    return v < low ? low : (v > high ? high : v);
};
HLLT.Position <- function(item, kind) {
    if (item == null) return null;
    try {
        if (kind == "navareas") {
            if (!("NavMesh" in getroottable()) || NavMesh.GetNavAreaByID(item.GetID()) != item) return null;
            return item.GetCenter();
        }
        if (!item.IsValid()) return null;
        local classname = item.GetClassname();
        if (classname != "infected" && classname != "witch" && (classname != "player" || !IsPlayerABot(item))) return null;
        if (item.GetHealth() <= 0) return null;
        return item.GetOrigin();
    } catch (error) { return null; }
};
HLLT.Query <- function(items, kind, mode, origin, radius, limit, index) {
    local r = {found=false, count=0, item=null, items=[], position=Vector(0,0,0), truncated=false};
    if (typeof items != "array" || (kind != "bots" && kind != "navareas")) return r;
    local n = items.len() < 256 ? items.len() : 256;
    r.truncated = items.len() > n;
    radius = Number(radius,0,32768); limit = Number(limit,0,128).tointeger();
    local validIndex = (typeof index == "integer" || typeof index == "float") && index == index && index >= 0 && index < n && index == index.tointeger();
    local bestDistance = null;
    for (local i=0; i<n; ++i) {
        local p = Position(items[i],kind);
        if (p == null) continue;
        local distance = (p-origin).LengthSqr();
        if (mode == "FILTER" && radius > 0 && distance > radius*radius) continue;
        ++r.count;
        if (mode == "FILTER") {
            if (r.items.len() < limit) r.items.append(items[i]); else r.truncated=true;
        }
        local pick = mode == "ITEM" ? validIndex && i == index :
                     mode == "CLOSEST" ? bestDistance == null || distance < bestDistance :
                     mode == "RANDOM" ? RandomInt(1,r.count) == 1 : false;
        if (pick) { r.item=items[i]; r.position=p; bestDistance=distance; }
    }
    if (mode == "FILTER") r.count=r.items.len();
    r.found = mode == "COUNT" || mode == "FILTER" ? r.count > 0 : r.item != null;
    return r;
};

// Set operations preserve handle identity and first-seen order, never mutate inputs.
// Membership covers only the examined prefix; Truncated explicitly reports that limit.
HLLT.SetQuery <- function(items, other, kind, mode, limit) {
    local r = {found=false, count=0, item=null, items=[], position=Vector(0,0,0), truncated=false};
    if (kind != "bots" && kind != "navareas") return r;
    if (mode != "UNION" && mode != "INTERSECTION" && mode != "DIFFERENCE" && mode != "UNIQUE") return r;
    if (typeof items != "array") items=[];
    if (typeof other != "array" || mode == "UNIQUE") other=[];
    local na=items.len()<256 ? items.len() : 256;
    local nb=other.len()<256 ? other.len() : 256;
    r.truncated=items.len()>na || other.len()>nb;
    limit=Number(limit,0,128).tointeger();
    local bset={}, seen={};
    for(local i=0;i<nb;++i) if(Position(other[i],kind)!=null) bset[other[i]] <- true;
    local n=mode=="UNION" ? na+nb : na;
    for(local i=0;i<n;++i) {
        local item=i<na ? items[i] : other[i-na];
        if(Position(item,kind)==null || item in seen) continue;
        seen[item] <- true;
        if(mode=="INTERSECTION" && !(item in bset)) continue;
        if(mode=="DIFFERENCE" && item in bset) continue;
        if(r.items.len()<limit) r.items.append(item); else r.truncated=true;
    }
    r.count=r.items.len(); r.found=r.count>0;
    return r;
};
