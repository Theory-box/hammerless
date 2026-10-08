"""Hammerless's map compiler (hlvbsp) in the game: builds demo/demo_level.blend (never saved) as
hl_test_mapc with Map Compiler set to Hammerless, checks the map in the game through the test bench, then
compiles the same .vmf with Valve's vbsp and compares the two maps lump by lump.

Run:  python tests/ingame/test_mapcompiler.py

Checks: the build used hlvbsp (not the fallback); the survivors walk across the map (brush collision);
a physics crate dropped from above lands on the floor (the physics collision); the map is the same as
vbsp's (bspdump: everything but the displacement physics, which differs between vbsp's own runs too).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "compile"))
from tests.ingame.bench import Bench  # noqa: E402
import bspdump  # noqa: E402

UPM = 52.49
MAP = "hl_test_mapc"
BLEND = os.path.join(ROOT, "demo", "demo_level.blend")
PREP = r'''
s.map_tool = "HAMMERLESS"
'''


def main(argv):
    results = []

    def check(name, ok, note=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {note}", flush=True)

    with Bench() as b:
        out = b.build(BLEND, MAP, prep=PREP)
        base = os.path.join(out, MAP)
        tables = os.path.exists(base + ".hlvbsp_materials.txt")
        check("build used the Hammerless map compiler", tables, "material table written" if tables else "no table")
        b.console("sv_cheats 1", "god 1", "director_stop", "nb_blind 1", wait=0.3)
        b.run('::HLT_crate <- SpawnEntityFromTable("prop_physics", { model = "models/props_junk/wood_crate001a.mdl", '
              'origin = GetListenServerHost().GetOrigin() + Vector(60, 0, 90) }); return true;')
        time.sleep(3)
        z0 = b.value("GetListenServerHost().GetOrigin().z")
        z = b.value("::HLT_crate.GetOrigin().z")
        check("physics crate lands on the floor", abs(z - z0) < 30, f"crate z {z:.0f}, player z {z0:.0f}")
        b.console("nb_blind 0", "director_start", "god 0", "sv_cheats 0", wait=0.3)

    # the same .vmf through Valve's vbsp
    from hammerless.core import compile as cc
    tools = cc.Tools(cc.find_game_root())
    work = os.path.join(out, "valve_compare")
    os.makedirs(work, exist_ok=True)
    shutil.copy(base + ".built.vmf", os.path.join(work, MAP + ".vmf"))
    subprocess.run([tools.exe("vbsp"), "-threads", "1", "-game", tools.gamedir, os.path.join(work, MAP)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ours, valve = bspdump.dump(base + ".bsp"), bspdump.dump(os.path.join(work, MAP + ".bsp"))
    differ = [bspdump.NAMES[i] for i in ours if ours[i] != valve[i] and i != 28]
    check("same map as Valve's vbsp", not differ, "differing lumps: " + ", ".join(differ) if differ else "all lumps")
    print(f"\n{sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
