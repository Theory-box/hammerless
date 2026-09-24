printl("HLFLOW left_safe_area=" + Director.HasAnySurvivorLeftSafeArea());
printl("HLFLOW furthest_flow=" + Director.GetFurthestSurvivorFlow());
foreach (name, pos in { start = Vector(-260, 2, 10), doorway = Vector(-60, 2, 10), field = Vector(1200, 0, 40), truck = Vector(2500, -300, 10), end = Vector(3834, 254, 10) }) {
    local a = NavMesh.GetNavArea(pos, 300);
    printl("HLFLOW " + name + " area=" + (a ? a.GetID() : "none") + " flow=" + GetFlowDistanceForPosition(pos) + " pct=" + GetFlowPercentForPosition(pos, false));
}
local areas = {}; NavMesh.GetAllAreas(areas);
local n = 0; foreach (k, v in areas) n++;
printl("HLFLOW total_areas=" + n);
