// In-game probe used to verify Hammerless (2026-09-23). Coordinates are for the demo maps.
// Copy to left4dead2/scripts/vscripts/, then: left4dead2.exe -game left4dead2 -hijack +script_execute <name>
// Output (HL* lines) goes to left4dead2/console.log when the game runs with -condebug.

// Hammerless test probe: prints survivor positions and ground heights.
local p = null;
while ((p = Entities.FindByClassname(p, "player")) != null)
    printl("HLPLAYER " + p.GetPlayerName() + " " + p.GetOrigin());
local pts = [[524.9,-419.9],[629.9,524.9],[787.4,0],[2624.5,-524.9],[2887,419.9],[1417.2,-629.9],[2467,-157.5],[262.4,262.4],[3149.4,-262.4],[1732.2,577.4]];
foreach (xy in pts) {
    local t = { start = Vector(xy[0], xy[1], 400), end = Vector(xy[0], xy[1], -3000), mask = 33570827 };
    TraceLine(t);
    printl("HLTRACE " + xy[0] + " " + xy[1] + " " + (t.hit ? t.pos.z : "miss"));
}
