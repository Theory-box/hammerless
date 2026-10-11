"""Are there enough zombies at the start? The bar (the user's): 5 s after the survivors spawn (nobody moves), more
than 12 commons on the STREET right outside the start safe room. Counts commons within RADIUS of the first survivor
AND within 120 units of its height (roofs and walkways above the street don't count: they're hidden spots the
Director fills first, and a horizontal radius alone counts them as near), at 5 and 10 s; lists every common
(x, y, z, distance) and screenshots the area from above. Waits for %TEMP%\\hl_game.lock if another test holds the
game. See docs/dev/ZOMBIE_SPAWNING.md.

    python tests/ingame/spawncheck.py <map> [radius]
"""
import os, subprocess, sys, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from tests.ingame.bench import Bench  # noqa: E402
MAP = sys.argv[1]
RADIUS = float(sys.argv[2]) if len(sys.argv) > 2 else 1400.0
LOCK = os.path.join(os.environ["TEMP"], "hl_game.lock")
OUT = os.path.join(os.environ["TEMP"], "hammerless_spawncheck")
os.makedirs(OUT, exist_ok=True)
while os.path.exists(LOCK):
    print("game busy:", open(LOCK).read().strip(), flush=True); time.sleep(10)
open(LOCK, "w").write("main (spawncheck)")
SNAP = '''
local start = null; local p = null;
while (p = Entities.FindByClassname(p, "player")) if (p.IsSurvivor()) { start = p.GetOrigin(); break; }
local e = null; local all = []; local near = 0;
while (e = Entities.FindByClassname(e, "infected")) {
    local o = e.GetOrigin(); local d = (o - start).Length();
    if (d < %f) near++;
    all.append([o.x.tointeger(), o.y.tointeger(), o.z.tointeger(), d.tointeger()]);
}
return {near = near, total = all.len(), start = [start.x.tointeger(), start.y.tointeger(), start.z.tointeger()], all = all};''' % RADIUS
try:
    subprocess.run(["taskkill", "/IM", "left4dead2.exe", "/F"], capture_output=True); time.sleep(3)
    with Bench() as b:
        b.load(MAP, settle=0.0)
        t0 = time.time()
        for t in (5, 10):
            time.sleep(max(0.0, t0 + t - time.time()))
            r = b.run(SNAP)
            street = sum(1 for x, y, z, d in r["all"] if d < RADIUS and abs(z - r["start"][2]) < 120)
            ok = "PASS" if street > 12 else "FAIL"
            print(f"{MAP} {t} s after spawn: {street} commons on the STREET within {RADIUS:.0f} of the start "
                  f"({r['near']} within {RADIUS:.0f} at any height, {r['total']} on the map) -> {ok}", flush=True)
        print("survivor start", r["start"], "commons (x, y, z, dist):", sorted(r["all"], key=lambda a: a[3])[:40], flush=True)
        s = r["start"]
        b.console("sv_cheats 1", wait=0.5)          # (after the counts: only the camera move needs it)
        b.screenshot(f"{MAP}_start", out_dir=OUT, pos=(s[0], s[1], s[2] + 900), ang=(70, 90))
        print("screenshot in", OUT, flush=True)
finally:
    subprocess.run(["taskkill", "/IM", "left4dead2.exe", "/F"], capture_output=True)
    try:
        os.remove(LOCK)
    except OSError:
        pass
