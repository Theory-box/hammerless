"""Compare Hammerless's vis compiler (hammerless/core/hlvvis.exe) with L4D2's vvis.exe on real maps.

    python tests/compile/compare_vis.py path/to/map.vmf [more.vmf ...] [--runs 3]

Needs L4D2 and its Authoring Tools. Each VMF is copied to a temp folder as hl_test_vis_<name> (nothing
goes into the game), compiled once with vbsp, then visibility is run several times with each tool, in
full and -fast mode. vvis itself isn't fully repeatable with several threads (a borderline pair or two
can flip between runs), so a result counts as matching when its file is byte-identical to one of
vvis's runs; otherwise the visibility is decoded and the cells vvis never produced are counted.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from hammerless.core import compile as cc  # noqa: E402


def lump(data: bytes, i: int) -> bytes:
    _v, off, ln, _ = struct.unpack_from("<iiii", data, 8 + 16 * i)
    return data[off:off + ln]


def decode_pvs(path: str) -> np.ndarray:
    """The visibility lump's PVS as a clusters x clusters bool matrix."""
    v = lump(open(path, "rb").read(), 4)
    n = struct.unpack_from("<i", v, 0)[0]
    nbytes = (n + 7) // 8
    rows = np.zeros((n, nbytes), np.uint8)
    for c in range(n):
        i, j = struct.unpack_from("<i", v, 4 + 8 * c)[0], 0
        while j < nbytes:
            if v[i]:
                rows[c, j] = v[i]
                i, j = i + 1, j + 1
            else:
                j += v[i + 1]
                i += 2
    return np.unpackbits(rows, axis=1, bitorder="little")[:, :n].astype(bool)


def run(cmd):
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return r.returncode, time.time() - t0, r.stdout


def compare_map(vmf: str, runs: int, tools: cc.Tools) -> list[str]:
    name = os.path.splitext(os.path.basename(vmf))[0]
    work = tempfile.mkdtemp(prefix="hl_vis_")
    base = os.path.join(work, "hl_test_vis_" + name)
    shutil.copyfile(vmf, base + ".vmf")
    rc, _t, out = run([tools.exe("vbsp"), "-game", tools.gamedir, base])
    if rc != 0 or not os.path.exists(base + ".prt"):
        return [f"{name}: vbsp failed or the map leaks"]
    shutil.copyfile(base + ".bsp", base + ".novis.bsp")
    shutil.copyfile(base + ".prt", base + ".novis.prt")
    lines = []
    for flag in ([], ["-fast"]):
        valve, vt, ot, results = [], [], [], []
        for k in range(runs):
            for ext in (".bsp", ".prt"):
                shutil.copyfile(base + ".novis" + ext, base + ext)
            rc, t, _ = run([tools.exe("vvis")] + flag + ["-game", tools.gamedir, base])
            valve.append(open(base + ".bsp", "rb").read()); vt.append(t)
        valve_pvs = []
        for k, data in enumerate(valve):
            p = f"{base}.valve{k}.bsp"
            open(p, "wb").write(data)
            valve_pvs.append(decode_pvs(p))
        always, ever = np.logical_and.reduce(valve_pvs), np.logical_or.reduce(valve_pvs)
        for k in range(runs):
            for ext in (".bsp", ".prt"):
                shutil.copyfile(base + ".novis" + ext, base + ext)
            rc, t, out = run([cc.HLVVIS] + flag + ["-game", tools.gamedir, base])
            if rc != 0:
                results.append(f"exit {rc}: {out.strip().splitlines()[-1] if out.strip() else ''}")
                continue
            ot.append(t)
            data = open(base + ".bsp", "rb").read()
            if any(data == v for v in valve):
                results.append("byte-identical")
                continue
            mine = decode_pvs(base + ".bsp")
            outside = int((mine & ~ever).sum() + (~mine & always).sum())
            others = [i for i in range(64) if i not in (4, 35) and lump(data, i) != lump(valve[0], i)]
            results.append(f"{outside} cells outside vvis's runs" + (f", lumps differing {others}" if others else ""))
        lines.append(f"{name} {'fast' if flag else 'full'}: vvis {min(vt):.1f} s, ours {min(ot) if ot else 0:.1f} s | "
                     + "; ".join(results))
    shutil.rmtree(work, ignore_errors=True)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vmf", nargs="+")
    ap.add_argument("--runs", type=int, default=3)
    a = ap.parse_args()
    tools = cc.Tools(cc.find_game_root())
    if not os.path.exists(cc.HLVVIS):
        sys.exit("hlvvis.exe isn't built: python native/build.py hlvvis")
    for vmf in a.vmf:
        for line in compare_map(vmf, a.runs, tools):
            print(line, flush=True)


if __name__ == "__main__":
    main()
