// In-game probe used to verify Hammerless (2026-09-23). Coordinates are for the demo maps.
// Copy to left4dead2/scripts/vscripts/, then: left4dead2.exe -game left4dead2 -hijack +script_execute <name>
// Output (HL* lines) goes to left4dead2/console.log when the game runs with -condebug.

// Hammerless transition test
function Attr(name, pos) {
    local a = NavMesh.GetNavArea(pos, 200);
    if (a == null) { printl("HLNAV " + name + " no-area"); return; }
    printl("HLNAV " + name + " attrs=" + a.GetSpawnAttributes() + " checkpoint=" + a.HasSpawnAttributes(2048) + " player_start=" + a.HasSpawnAttributes(128));
}
Attr("start_room", Vector(-260, 2, 10));
Attr("end_room", Vector(3834, 254, 10));
Attr("field", Vector(1700, 0, 10));
local i = 0; local p = null;
while ((p = Entities.FindByClassname(p, "player")) != null) {
    if (p.IsSurvivor()) {
        p.SetOrigin(Vector(3780 + (i % 2) * 60, 200 + (i / 2) * 60, 5));
        i++;
    }
}
printl("HLTRANS teleported " + i);
local door = Entities.FindByName(null, "checkpoint_exit");
printl("HLTRANS door=" + door);
EntFire("checkpoint_exit", "Close", "", 1.5);
