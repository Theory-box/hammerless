// HLWQ_REQUIRES_AMBIENT
::HL_AmbientData <- null;
// __HL_AMBIENT_DATA__
function HLWQ_Ambient(p) {
    local missing = {available=false,color=Vector(0,0,0),brightness=0.0,fallback=false};
    if (!("HL_AmbientData" in getroottable()) || HL_AmbientData == null || p == null) return missing;
    local d=HL_AmbientData, n=d.root, steps=0;
    while (n >= 0) {
        if (++steps > d.nodes.len() || n >= d.nodes.len()) return missing;
        local node=d.nodes[n], plane=d.planes[node[0]];
        n=node[(p.x*plane[0]+p.y*plane[1]+p.z*plane[2]-plane[3] < 0) ? 2 : 1];
    }
    local leaf=-1-n;
    if (leaf < 0 || leaf >= d.leaves.len() || d.leaves[leaf][3]) return missing;
    local item=d.leaves[leaf], fallback=false;
    // VRAD's empty-leaf index points to another LEAF, not a sample index.
    if (item[1] == 0) {
        leaf=item[2];
        if (leaf < 0 || leaf >= d.leaves.len()) return missing;
        item=d.leaves[leaf]; fallback=true;
        if (item[1] == 0 || item[3]) return missing;
    }
    local color=Vector(0,0,0), total=0.0;
    for (local i=0; i<item[1]; ++i) {
        local s=d.probes[item[0]+i];
        local dx=p.x-s[0], dy=p.y-s[1], dz=p.z-s[2];
        local w=1.0/(1.0+dx*dx+dy*dy+dz*dz);
        color += Vector(s[3],s[4],s[5])*w; total+=w;
    }
    if (total <= 0) return missing;
    color=color*(1.0/total);
    return {available=true,color=color,brightness=color.x*0.2126+color.y*0.7152+color.z*0.0722,fallback=fallback};
}
