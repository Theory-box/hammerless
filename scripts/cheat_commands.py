"""Build hammerless/core/data/cheat_commands.txt: the console settings and commands of L4D2 that only work with
cheats on (sv_cheats 1), as the game itself reports them. Used to warn when a map's logic needs cheats
(core/cheats.py).

- settings: 'cvarlist' marks them "cheat";
- commands: cvarlist doesn't say (they check when run), so each commonly used one below is run once with cheats
  off and kept if the game refuses it ("Can't use cheat command ...").

Run with the game closed or idle (it loads a test map through the test bench):
    python scripts/cheat_commands.py
"""
import os
import re
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from tests.ingame.bench import Bench  # noqa: E402

OUT = os.path.join(ROOT, "hammerless", "core", "data", "cheat_commands.txt")
# commands a map's logic might send (harmless on the test map when allowed; ones that kill the player or
# restart the sound are left out)
COMMANDS = ["z_spawn", "z_spawn_old", "z_add", "give", "noclip", "notarget", "buddha",
            "director_force_panic_event", "director_stop", "director_start", "director_force_tank",
            "director_force_witch", "director_debug", "ent_fire", "ent_create", "ent_remove", "ent_remove_all",
            "ent_teleport", "setpos", "setang", "setpos_exact", "warp_to_start_area", "warp_all_survivors_here",
            "nb_delete_all", "nb_blind", "nb_move_to_cursor", "sb_add", "sb_hold_position",
            "thirdperson", "firstperson", "thirdpersonshoulder", "impulse", "fire_test", "stopsound",
            "r_screenoverlay", "mat_fullbright", "cl_drawhud", "crosshair", "fog_override",
            "script", "script_execute", "scripted_user_func", "map", "changelevel", "say", "echo"]


def main():
    with Bench() as b:
        b.load("hl_test_examples")
        m = b.mark()
        b.console("cvarlist", wait=3.0)
        time.sleep(2.0)
        lines = b.lines_since(m)
        # "host_timescale     : 1        : , "sv", "cheat", "rep"  : Prescale the clock by this amount."
        names = {ln.split(":")[0].strip().lower() for ln in lines
                 if ln.count(":") >= 2 and re.search(r'"cheat"', ln.split(":")[2])}
        if len(names) < 50:
            raise SystemExit(f"only {len(names)} cheat settings found: is cvarlist's output in the console log?")
        b.console("sv_cheats 0", "god 1", wait=0.5)
        refused = []
        for c in COMMANDS:
            m = b.mark()
            b.console(c if c not in ("map", "changelevel") else c + " __no_such_map__", wait=0.4)
            out = b.lines_since(m, "cheat")
            if any(c in ln for ln in out):
                refused.append(c)
        names |= set(refused)
        b.console("director_start", wait=0.2)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# L4D2 console settings marked \"cheat\" (cvarlist) and commands refused without sv_cheats,\n"
                "# made by scripts/cheat_commands.py\n")
        f.write("\n".join(sorted(names)) + "\n")
    print(f"{len(names)} names ({len(refused)} commands: {', '.join(refused)}) -> {OUT}")


if __name__ == "__main__":
    main()
