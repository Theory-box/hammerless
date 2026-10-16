"""A sight line through a compiled map -> the ordered one-way portals it crosses (hlvvis numbering: prt line i
gives 2i = c0 -> c1 and 2i+1 = c1 -> c0) and the crossing points. Writes a replay file for hlvvis HL_REPLAY:
    line 1: base portal;  then one line per portal crossed after it: "portal x y z" (crossing point);
    last line: "start x y z" (where the line crosses the base portal)

    python tests/compile/vis_chain_walk.py map.bsp map.prt "x y z" "x y z" out.txt
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bspdump  # noqa: E402

bsp, prt, a, b, out = sys.argv[1:6]
a = np.array(list(map(float, a.split()))); b = np.array(list(map(float, b.split())))
L = bspdump.lumps(open(bsp, "rb").read())
planes = np.frombuffer(L[1][3], dtype=np.dtype([("n", "<f4", 3), ("d", "<f4"), ("t", "<i4")]))
nodes = np.frombuffer(L[5][3], dtype=np.dtype([("plane", "<i4"), ("child", "<i4", 2), ("mins", "<i2", 3),
                                               ("maxs", "<i2", 3), ("ff", "<u2"), ("nf", "<u2"), ("area", "<i2"),
                                               ("pad", "<i2")]))
lf = L[10][3]
leaf_cluster = np.array([struct.unpack_from("<h", lf, 32 * k + 4)[0] for k in range(len(lf) // 32)])
pn, pd = planes["n"].astype(np.float64), planes["d"].astype(np.float64)


def leaf_of(p):
    node = np.zeros(len(p), np.int64); out_ = np.full(len(p), -1, np.int64); act = np.ones(len(p), bool)
    while act.any():
        i = np.nonzero(act)[0]; nd = node[i]; pl = nodes["plane"][nd]
        side = ((p[i] * pn[pl]).sum(1) - pd[pl]) < 0
        ch = nodes["child"][nd, side.astype(int)]
        lv = ch < 0
        out_[i[lv]] = -1 - ch[lv]; act[i[lv]] = False; node[i[~lv]] = ch[~lv]
    return out_


n = int(np.linalg.norm(b - a) / 0.01) + 2
t = np.linspace(0, 1, n)
pts = a + np.outer(t, b - a)
cl = leaf_cluster[leaf_of(pts)]
if (cl < 0).any():
    print("WARNING: the line passes through solid (cluster -1) at", int((cl < 0).sum()), "samples")
# portals
lines = open(prt).read().split("\n")
npt = int(lines[2])
P = []
for ln in lines[3:3 + npt]:
    head, rest = ln.split(" (", 1)
    c0, c1 = map(int, head.split()[1:3])
    pts_ = np.array([list(map(float, q.strip(" )").split())) for q in ("(" + rest).split("(")[1:]])
    P.append((c0, c1, pts_))


def crossing(i, a, b):
    c0, c1, w = P[i]
    nrm = np.cross(w[0] - w[1], w[2] - w[1]); nrm /= np.linalg.norm(nrm)
    da, db = (a - w[0]) @ nrm, (b - w[0]) @ nrm
    if da == db: return None, 1e9
    s = da / (da - db)
    if s < -1e-6 or s > 1 + 1e-6: return None, 1e9
    x = a + s * (b - a)
    # distance outside the polygon (0 if inside)
    out = 0.0
    for k in range(len(w)):
        e = w[(k + 1) % len(w)] - w[k]
        en = np.cross(nrm, e); en /= np.linalg.norm(en)
        out = max(out, (x - w[k]) @ en * -1 if False else 0)
    # use a 2D winding test via signed areas
    sgn = None; worst = 0.0
    for k in range(len(w)):
        e = w[(k + 1) % len(w)] - w[k]
        c = np.cross(e, x - w[k]) @ nrm / np.linalg.norm(e)
        if sgn is None and abs(c) > 1e-9: sgn = np.sign(c)
        if sgn is not None and np.sign(c) != sgn: worst = max(worst, abs(c))
    return x, worst


chain = []
k = 0
while k < n - 1:
    if cl[k + 1] != cl[k] and cl[k] >= 0 and cl[k + 1] >= 0:
        A, B = int(cl[k]), int(cl[k + 1])
        cands = [i for i, (c0, c1, _w) in enumerate(P) if {c0, c1} == {A, B}]
        best = None
        for i in cands:
            x, outside = crossing(i, pts[max(0, k - 50)], pts[min(n - 1, k + 51)])
            if x is not None and (best is None or outside < best[2]):
                best = (i, x, outside)
        if best is None:
            print(f"no portal between {A} and {B} at sample {k}"); k += 1; continue
        i, x, outside = best
        one_way = 2 * i if P[i][0] == A else 2 * i + 1
        chain.append((one_way, x, outside, A, B))
    k += 1
print(f"{len(chain)} portals crossed:")
for one_way, x, outside, A, B in chain:
    print(f"  portal {one_way:6d}  cluster {A} -> {B}  at {np.round(x, 3)}  outside its winding by {outside:.4f}")
with open(out, "w") as f:
    f.write(f"{chain[0][0]}\n")
    for one_way, x, _o, _a, _b in chain[1:]:
        f.write(f"{one_way} {x[0]:.6f} {x[1]:.6f} {x[2]:.6f}\n")
    x0 = chain[0][1]
    f.write(f"start {x0[0]:.6f} {x0[1]:.6f} {x0[2]:.6f}\n")
print("replay file:", out)
