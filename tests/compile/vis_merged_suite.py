"""The merged vis against the exact one on many maps: time, looseness, and every pair it hides ray-checked.

    python tests/compile/vis_merged_suite.py [--rays 2000000] map.bsp [more.bsp ...]
    python tests/compile/vis_merged_suite.py --found      # every compiled map the other test tools left in %TEMP%

Each map (its .bsp and .prt side by side, compiled but any vis is fine: it's redone) is copied to a temp folder and
run through hammerless/core/hlvvis.exe twice: -exact (vvis's own result) and the default (merged windows). Then
vis_check.py's comparison: pairs the merged result hides are fired at with random sight lines; any that gets
through fails the map. Prints one line per map and a summary.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import vis_check  # noqa: E402
from hammerless.core import compile as cc  # noqa: E402

HLVVIS = os.path.join(ROOT, "hammerless", "core", "hlvvis.exe")
OUT = os.path.join(tempfile.gettempdir(), "hammerless_vis_merged")


def found() -> list[str]:
    t = tempfile.gettempdir()
    maps = glob.glob(os.path.join(t, "hammerless_decompile", "dc_*", "hl_test_dc_*.bsp"))
    maps += glob.glob(os.path.join(t, "hammerless_mapcompiler_suite", "real_*", "*.bsp"))
    return [m for m in sorted(maps) if os.path.exists(os.path.splitext(m)[0] + ".prt")]


def run(bsp: str, rays: int, game: str) -> bool:
    name = os.path.splitext(os.path.basename(bsp))[0]
    work = os.path.join(OUT, name)
    os.makedirs(work, exist_ok=True)
    times = {}
    for mode in ("exact", "merged"):
        base = os.path.join(work, mode)
        shutil.copy2(bsp, base + ".bsp")
        shutil.copy2(os.path.splitext(bsp)[0] + ".prt", base + ".prt")
        t0 = time.time()
        r = subprocess.run([HLVVIS] + (["-exact"] if mode == "exact" else []) + ["-game", game, base],
                           capture_output=True, text=True)
        times[mode] = time.time() - t0
        if r.returncode != 0:
            print(f"FAIL {name}: hlvvis {mode} failed: {(r.stdout + r.stderr).strip().splitlines()[-1:]}", flush=True)
            return False
    ref, res = vis_check.pvs(os.path.join(work, "exact.bsp")), vis_check.pvs(os.path.join(work, "merged.bsp"))
    hidden = sorted({tuple(sorted(p)) for p in ref - res})
    seen = []
    if hidden:
        vis_check.build()
        out = subprocess.run([vis_check.EXE, os.path.join(work, "exact.bsp"), os.path.splitext(bsp)[0] + ".prt",
                              str(rays)] + [str(x) for p in hidden for x in p], capture_output=True, text=True).stdout
        seen = [ln for ln in out.splitlines() if "VISIBLE" in ln]
    loose = 100 * len(res - ref) / max(len(ref), 1)
    ok = not seen
    print(f"{'SAFE' if ok else 'UNSAFE'} {name}: exact {times['exact']:.1f} s, merged {times['merged']:.1f} s "
          f"({times['exact'] / max(times['merged'], 1e-3):.1f}x); {loose:.2f}% looser; hides {len(hidden)} cluster "
          f"pairs, {len(seen)} proven visible by a ray", flush=True)
    return ok


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("maps", nargs="*")
    ap.add_argument("--rays", type=int, default=2_000_000)
    ap.add_argument("--found", action="store_true")
    a = ap.parse_args(argv)
    maps = a.maps + (found() if a.found else [])
    game = os.path.join(cc.find_game_root(), "left4dead2")
    results = [run(m, a.rays, game) for m in maps]
    print(f"\n{sum(results)} of {len(results)} maps safe")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
