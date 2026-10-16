"""Valve's own rain indoors, for comparison with test_rain.py: loads a Hard Rain map, finds roofed spots inside its
rain volume (a trace straight up hits a non-sky surface within 400 units; not in a blocker) and takes a screenshot
at each, looking level. Run:  python tests/ingame/test_rain_valve.py [map] [shots folder] [count]"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from tests.ingame.bench import Bench  # noqa: E402

FIND = r'''
local lo = Vector(@LO@), hi = Vector(@HI@), blockers = [@BLOCKERS@];
local t = {}, out = [];
NavMesh.GetAllAreas(t);
foreach (a in t) {
    if (a.GetSizeX() < 100 || a.GetSizeY() < 100) continue;
    local c = a.GetCenter();
    if (c.x < lo.x || c.x > hi.x || c.y < lo.y || c.y > hi.y || c.z < lo.z || c.z > hi.z) continue;
    local inb = false;
    foreach (b in blockers) if (c.x > b[0].x && c.x < b[1].x && c.y > b[0].y && c.y < b[1].y && c.z > b[0].z - 64 && c.z < b[1].z) inb = true;
    if (inb) continue;
    local up = { start = c + Vector(0, 0, 70), end = c + Vector(0, 0, 470), mask = 1 };
    TraceLine(up);
    if (up.fraction >= 1.0) continue;
    // indoors: walls near on most sides (the four level traces hit within 300 units)
    local walls = 0;
    foreach (d in [Vector(1, 0, 0), Vector(-1, 0, 0), Vector(0, 1, 0), Vector(0, -1, 0)]) {
        local s = { start = c + Vector(0, 0, 64), end = c + Vector(0, 0, 64) + d * 300, mask = 1 };
        TraceLine(s);
        if (s.fraction < 1.0) walls++;
    }
    if (walls >= 3) out.append([c.x, c.y, c.z, up.fraction * 400]);
}
return out;
'''


def boxes(name):
    """(the rain volume's box, the blockers' boxes) from the map file (VScript can't read brush bounds)"""
    import re
    import struct
    from hammerless.core import compile as cc
    from hammerless.core.vpk import GameContent
    d = GameContent(cc.find_game_root()).read(f"maps/{name}.bsp")

    def lump(i):
        _v, o, ln, _c = struct.unpack_from("<4i", d, 8 + 16 * i)
        return d[o:o + ln]
    ents, models = lump(0).decode("latin-1"), lump(14)
    vol, blk = None, []
    for block in re.findall(r"\{[^{}]*\}", ents):
        kv = dict(re.findall(r'"([^"]+)" "([^"]*)"', block))
        if kv.get("classname", "").startswith("func_precipitation") and kv.get("model", "").startswith("*"):
            k = int(kv["model"][1:])
            box = (struct.unpack_from("<3f", models, 48 * k), struct.unpack_from("<3f", models, 48 * k + 12))
            if kv["classname"] == "func_precipitation":
                vol = box
            else:
                blk.append(box)
    return vol, blk


def main(argv):
    name = argv[0] if argv else "c4m1_milltown_a"
    shots = argv[1] if len(argv) > 1 else os.path.join(ROOT, "tests", "ingame", "shots", "rain")
    count = int(argv[2]) if len(argv) > 2 else 4
    with Bench() as b:
        b.load(name)
        b.console("sv_cheats 1", "god 1", "director_stop", "nb_blind 1", "noclip", wait=0.5)
        vol, blk = boxes(name)
        code = (FIND.replace("@LO@", ", ".join(map(str, vol[0]))).replace("@HI@", ", ".join(map(str, vol[1])))
                .replace("@BLOCKERS@", ", ".join(f"[Vector({a[0]}, {a[1]}, {a[2]}), Vector({c[0]}, {c[1]}, {c[2]})]"
                                               for a, c in blk)))
        spots = b.run(code, timeout=30)
        print(f"{len(spots)} roofed indoor spots in the rain volume", flush=True)
        step = max(1, len(spots) // count)
        for i, (x, y, z, roof) in enumerate(spots[::step][:count]):
            for yaw in (0, 90):
                b.console(f"setpos {x} {y} {z}", f"setang 0 {yaw} 0", wait=0.2)
                time.sleep(2.5)
                print(f"spot {i} roof {roof:.0f} up:", b.screenshot(f"valve_{name}_{i}_{yaw}", out_dir=shots),
                      flush=True)
        b.console("noclip", "nb_blind 0", "director_start", "god 0", "sv_cheats 0", wait=0.3)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
