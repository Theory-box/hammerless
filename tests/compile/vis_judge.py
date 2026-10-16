"""Judge every 'SEEN' sight line in a vis_check log with portal semantics: walk it through the BSP (chain_walk.py),
reject it when it crosses between two areas with no portal, when it passes less than MARGIN inside a crossed portal,
or grazes one (angle under MIN_DEG); the survivors go to hlvvis's exact single-chain replay (HL_REPLAY) as the final
judge. Prints one verdict per line and a summary.

    python tests/compile/vis_judge.py map.bsp map.prt vis_check_output.log
"""
import os
import re
import subprocess
import sys

import numpy as np

MARGIN, MIN_DEG = 0.01, 0.5
HLVVIS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hammerless", "core", "hlvvis.exe")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from hammerless.core.compile import find_game_root  # noqa: E402
GAME = os.path.join(find_game_root(), "left4dead2")
bsp, prt, log = sys.argv[1:4]
here = os.path.dirname(os.path.abspath(__file__))
lines = open(prt).read().split("\n")
npt = int(lines[2])
W = []
for ln in lines[3:3 + npt]:
    h, rest = ln.split(" (", 1)
    c0, c1 = map(int, h.split()[1:3])
    W.append((c0, c1, np.array([list(map(float, q.strip(" )").split())) for q in ("(" + rest).split("(")[1:]])))


def inside_margin(w, x):
    nrm = np.cross(w[0] - w[1], w[2] - w[1]); nrm /= np.linalg.norm(nrm)
    worst = 1e9
    c = w.mean(0)
    for k in range(len(w)):
        e = w[(k + 1) % len(w)] - w[k]
        en = np.cross(nrm, e); en /= np.linalg.norm(en)
        if (c - w[k]) @ en < 0: en = -en                 # (pointing inward)
        worst = min(worst, (x - w[k]) @ en)
    return worst, nrm


seen = [l for l in open(log) if "SEEN" in l]
verdicts = {}
for l in seen:
    m = re.search(r"(\d+)-(\d+) SEEN: \(([-\d. ]+)\) -> \(([-\d. ]+)\)", l)
    A, B, a, b = m.group(1), m.group(2), m.group(3), m.group(4)
    av, bv = np.array(list(map(float, a.split()))), np.array(list(map(float, b.split())))
    ext = bv + (bv - av) / np.linalg.norm(bv - av) * 0.05
    rf = os.path.join(os.path.dirname(os.path.abspath(log)), f"arb_{A}_{B}.txt")
    out = subprocess.run([sys.executable, os.path.join(here, "vis_chain_walk.py"), bsp, prt, a, " ".join(f"{x:.5f}" for x in ext), rf],
                         capture_output=True, text=True).stdout
    if "no portal between" in out:
        verdicts[(A, B)] = "NO PORTAL (the line slips between areas with no portal)"; print(A, B, verdicts[(A, B)]); continue
    chain = [(int(m2.group(1)), np.array(list(map(float, m2.group(2).split()))))
             for m2 in re.finditer(r"portal +(\d+) +cluster \d+ -> \d+ +at \[([-\d. e]+)\]", out)]
    d = (bv - av) / np.linalg.norm(bv - av)
    bad = None
    for one_way, x in chain:
        w = W[one_way // 2][2]
        mg, nrm = inside_margin(w, x)
        ang = np.degrees(np.arcsin(min(1, abs(d @ nrm))))
        if mg < MARGIN: bad = f"passes only {mg:.4f} inside portal {one_way}"; break
        if ang < MIN_DEG: bad = f"grazes portal {one_way} at {ang:.3f} deg"; break
    if bad:
        verdicts[(A, B)] = "NOT PROOF: " + bad; print(A, B, verdicts[(A, B)]); continue
    r = subprocess.run([HLVVIS, "-game", GAME, os.path.splitext(bsp)[0]], env={**os.environ, "HL_REPLAY": rf},
                       capture_output=True, text=True).stdout
    if "chopped away" in r or "CLIPPED AWAY" in r:
        verdicts[(A, B)] = "NOT PROOF: the exact clipping along its chain drops it"
    else:
        verdicts[(A, B)] = "REAL: valid sight line along its whole chain"
    print(A, B, verdicts[(A, B)], flush=True)
real = [k for k, v in verdicts.items() if v.startswith("REAL")]
print(f"\n{len(seen)} lines judged: {len(real)} REAL misses, {len(seen) - len(real)} not proof")
