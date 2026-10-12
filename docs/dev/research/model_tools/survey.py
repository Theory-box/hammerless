import hashlib
from collections import Counter
from gamefs import gc
from mdlparse import MDL
g = gc()
pre = ("models/survivors/", "models/infected/", "models/v_models/", "models/weapons/arms/")
files = sorted(f for f in g.files if f.endswith(".mdl") and f.startswith(pre) and "/limbs/" not in f and "_w_" not in f and "shadertest" not in f and "/gibs/" not in f)
skel = {}
tot = Counter()
print(f"{'model':<52s} {'bones':>5s} {'skel':>6s} {'seq':>4s} {'anim':>5s} {'rle':>4s} {'frm':>4s} {'ani?':>4s} {'verts':>6s} {'flex':>4s} includes")
for f in files:
    m = MDL(g.read(f))
    names = tuple(b["name"] for b in m.bones)
    hs = hashlib.md5("|".join(f"{b['name']}<{b['parent']}" for b in m.bones).encode()).hexdigest()[:6]
    skel.setdefault(hs, []).append(f)
    ads = m.animdescs()
    rle = sum(1 for a in ads if not a["flags"] & 0x40)
    frm = len(ads) - rle
    tot["rle"] += rle; tot["frm"] += frm
    verts = sum(v for _, ms in m.bodyparts() for _, _, v in ms)
    inc = [x[1].replace("models/", "") for x in m.includemodels()]
    print(f"{f.replace('models/', ''):<52s} {len(m.bones):5d} {hs:>6s} {m.h['numlocalseq']:4d} {len(ads):5d} {rle:4d} {frm:4d} {('y' if m.animblockname else ''):>4s} {verts:6d} {m.h['numflexdesc']:4d} {inc}")
print(tot)
print("skeleton groups (identical bone names+parents, in order):")
for k, v in skel.items():
    print(" ", k, len(v), [x.replace("models/", "") for x in v][:8], "..." if len(v) > 8 else "")
