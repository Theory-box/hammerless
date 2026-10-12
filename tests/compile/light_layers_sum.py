"""Light layers: bake a map whole, then once per light with hlvrad -onlylights (each light alone, steady), and check
the layers add up to the whole (light is linear, bounces too). Works on a copy; prints how far off the sum is.

Run:  python tests/compile/light_layers_sum.py <map.bsp> [<map>.hlvrad_models]
"""
import os, re, shutil, subprocess, sys, time
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from hammerless.core.compile import Tools, find_game_root  # noqa: E402
from hammerless.core.lightmap import read_lightmaps, _lumps  # noqa: E402

HERE = os.path.join(os.environ.get("TEMP", "."), "hl_light_layers")
os.makedirs(HERE, exist_ok=True)
SRC = sys.argv[1]
MODELS = sys.argv[2] if len(sys.argv) > 2 else ""
EXE = os.path.join(os.path.dirname(__file__), "..", "..", "hammerless", "core", "hlvrad.exe")
GAME = Tools(find_game_root()).gamedir
ARGS = ["-hdr", "-final", "-gpu", "-embree", "-gi", "8", "-girays", "1024"]


def entities(data):
    text = _lumps(data)(0).decode("latin-1")
    out = []
    for i, block in enumerate(re.findall(r"\{(.*?)\}", text, re.S)):
        kv = dict(re.findall(r'"([^"]*)"\s+"([^"]*)"', block))
        out.append(kv)
    return out


def bake(name, spec=None):
    path = os.path.join(HERE, name + ".bsp")
    shutil.copy(SRC, path)
    cmd = [EXE] + ARGS + (["-onlylights", spec] if spec else []) + ["-game", GAME]
    cmd += (["-modeldir", MODELS] if MODELS else []) + [path]
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:]
    kept = [l for l in r.stdout.splitlines() if "kept" in l or "direct lights" in l]
    print(f"{name}: {time.time() - t:.1f} s {kept}")
    return read_lightmaps(open(path, "rb").read()).atlas[..., :3].astype(np.float64)


data = open(SRC, "rb").read()
ents = entities(data)
lights = [(i, e) for i, e in enumerate(ents) if e.get("classname", "").startswith("light")]
print("lights:", [(i, e["classname"], e.get("targetname", "")) for i, e in lights])
whole = bake("whole")
layers = {"sky": bake("l_sky", "sky"), "surface": bake("l_surface", "surface")}
for i, e in lights:
    layers[str(i)] = bake(f"l_{i}", str(i))
total = sum(layers.values())
lit = whole.sum(-1) > 1e-3
err = np.abs(total - whole)[lit].sum(-1) / whole[lit].sum(-1)
print(f"luxels {lit.sum()}: relative error median {np.median(err):.5f}, 99% {np.quantile(err, 0.99):.4f}, "
      f"max {err.max():.3f}; within 1%: {(err < 0.01).mean() * 100:.2f}%")
for k, v in layers.items():
    print(f"  layer {k}: luxels reached {(v.sum(-1) > whole.sum(-1) * 1e-3).sum()} of {lit.sum()}")
