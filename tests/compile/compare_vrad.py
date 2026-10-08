"""Light maps with Valve's vrad and Hammerless's hlvrad from the same compiled map and compare them.

    python tests/compile/compare_vrad.py path/to/map.vmf [more.vmf ...]
    python tests/compile/compare_vrad.py --dir "C:/.../sdk_content" [--vis]

Each map is compiled with Valve's vbsp (and with --vis, Valve's vvis -fast), then lit by both
compilers. hlvrad is being built in stages: only the lumps it writes so far are compared (DONE);
the others are listed as not yet done. vrad itself isn't deterministic in its light values, so
those will be compared within its own run-to-run differences once hlvrad computes them.
Output in %TEMP%/hammerless_vrad_compare/<name>.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
from hammerless.core.compile import find_game_root  # noqa: E402

HLVRAD = os.path.join(ROOT, "hammerless", "core", "hlvrad.exe")
OUT = os.path.join(tempfile.gettempdir(), "hammerless_vrad_compare")
DONE = {10: "leaves (sky flags)", 30: "vertex normals", 31: "vertex normal indices", 54: "world lights (HDR)",
        58: "faces (HDR): styles, lightmap offsets"}
NOT_YET = {35: "game lump (detail prop lighting)", 51: "leaf ambient index", 52: "leaf ambient index (LDR)", 53: "lightmaps (HDR)",
           55: "leaf ambient lighting", 56: "leaf ambient lighting (LDR)"}


def lumps(path: str) -> dict[int, tuple[int, bytes]]:
    d = open(path, "rb").read()
    out = {}
    for i in range(64):
        ver, ofs, ln, _cc = struct.unpack_from("<iiii", d, 8 + 16 * i)
        out[i] = (ver, d[ofs:ofs + ln])
    return out


def light_map(name: str, vmf: str, root: str, vis: bool) -> list[str] | None:
    game = os.path.join(root, "left4dead2")
    d = os.path.join(OUT, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    base = os.path.join(d, "hl_test_" + name[:40])
    # instances are found relative to the map's folder: compile a copy inside a copy of that folder
    src_dir = os.path.dirname(os.path.abspath(vmf))
    tree = os.path.join(OUT, "_src", src_dir.replace(":", "").replace("\\", "_").replace("/", "_")[-80:])
    if not os.path.isdir(tree):
        shutil.copytree(src_dir, tree)
    stem = os.path.join(tree, "hl_test_" + name[:40])
    shutil.copy(vmf, stem + ".vmf")
    r = subprocess.run([os.path.join(root, "bin", "vbsp.exe"), "-game", game, stem], capture_output=True, text=True)
    open(base + ".vbsp.log", "w").write(r.stdout + r.stderr)
    for ext in (".vmf", ".bsp", ".prt", ".lin", ".log"):
        if os.path.exists(stem + ext):
            shutil.move(stem + ext, base + ext)
    if not os.path.exists(base + ".bsp"):
        return None                  # (vbsp makes no map from it: instance pieces without a world)
    if vis:
        subprocess.run([os.path.join(root, "bin", "vvis.exe"), "-fast", "-game", game, base], capture_output=True)
    ours = os.path.join(d, "ours")
    shutil.copy(base + ".bsp", ours + ".bsp")
    r = subprocess.run([os.path.join(root, "bin", "vrad.exe"), "-hdr", "-game", game, base], capture_output=True,
                       text=True)
    open(base + ".vrad.log", "w").write(r.stdout + r.stderr)
    r = subprocess.run([HLVRAD, "-hdr", "-game", game, ours], capture_output=True, text=True)
    open(ours + ".log", "w").write(r.stdout + r.stderr)
    if r.returncode:
        return ["hlvrad failed: " + (r.stderr or r.stdout).strip()[-200:]]
    a, b = lumps(base + ".bsp"), lumps(ours + ".bsp")
    problems = []
    for i in range(64):
        if i in NOT_YET or a[i] == b[i]:
            continue
        problems.append(f"lump {i} {DONE.get(i, '(should be untouched)')}")
    return problems


def main(argv: list[str]) -> int:
    paths = [a for a in argv if not a.startswith("--")]
    if "--dir" in argv:
        d = argv[argv.index("--dir") + 1]
        paths = [p for p in paths if p != d]
        for dirpath, _, files in os.walk(d):
            paths += [os.path.join(dirpath, f) for f in sorted(files) if f.lower().endswith(".vmf")]
    root = find_game_root()
    same = differ = skipped = 0
    for path in paths:
        parent = os.path.basename(os.path.dirname(os.path.abspath(path))).lower()
        name = parent + "_" + os.path.splitext(os.path.basename(path))[0].lower()
        t = time.time()
        problems = light_map(name, path, root, "--vis" in argv)
        if problems is None:
            skipped += 1
            print(f"SKIP {name}: vbsp makes no map from it", flush=True)
            continue
        same += not problems
        differ += bool(problems)
        print(f"{'SAME' if not problems else 'DIFF'} {name} ({time.time() - t:.0f} s)"
              + (": " + ", ".join(problems) if problems else ""), flush=True)
    print(f"\n{same} same, {differ} different (comparing: {', '.join(DONE.values())}; "
          f"not yet: {', '.join(NOT_YET.values())})")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
