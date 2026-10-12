"""Live Light Mixing, end to end in Blender (no game): builds demo/demo_level.blend (never saved) with three closed
bunkers (a lamp in each, the third switchable: a light style of its own), Live Light Mixing on, as hl_test_mix. Then:
- changes lamps' colour and brightness and the sun's, mixes the layers live (blender/lightmix.py);
- builds: the smart build must remix (hlvrad -remix, no bake); changes them again and builds again (a remix of a
  remix);
- bakes the same scene from scratch and compares: the remixed map's lightmaps (every page and style), leaf ambient
  light (what models are lit by) and world lights; and the live mix with a bake of the first change.

Run:  blender --background demo/demo_level.blend --python tests/compile/light_mixing.py
Prints PASS / FAIL lines; exit code 0 = all passed.
"""
import os
import shutil
import struct
import sys
import time

import bpy
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import hammerless  # noqa: E402
from hammerless.core import compile as cc  # noqa: E402

hammerless.register()
OUT = os.path.join(os.environ.get("TEMP", "."), "hl_test_mix")
os.makedirs(OUT, exist_ok=True)
results = []


def check(name, ok, note=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}: {note}", flush=True)


s = bpy.context.scene.hammerless
s.map_name, s.output_dir = "hl_test_mix", OUT
s.light_tool, s.light_exact, s.light_mixing = "HAMMERLESS", False, True
for t in [t for t in bpy.data.node_groups if t.bl_idname == "HL_LogicTree"]:
    bpy.data.node_groups.remove(t)
wall = bpy.data.materials.new("bunker wall")
wall.hammerless.source_material = "dev/dev_measuregeneric01b"


def slab(x0, x1, y0, y1, z0, z1):
    bpy.ops.mesh.primitive_cube_add(size=1, location=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    o = bpy.context.object
    o.scale = (x1 - x0, y1 - y0, z1 - z0)
    o.data.materials.append(wall)


def bunker(x):
    t = 0.25
    slab(x - t, x + 4 + t, -t, 4 + t, -t, 0)
    slab(x - t, x + 4 + t, -t, 4 + t, 3, 3 + t)
    slab(x - t, x, 0, 4, 0, 3)
    slab(x + 4, x + 4 + t, 0, 4, 0, 3)
    slab(x - t, x + 4 + t, -t, 0, 0, 3)
    slab(x - t, x + 4 + t, 4, 4 + t, 0, 3)


def lamp(name, x, color):
    data = bpy.data.lights.new(name, "POINT")
    data.energy, data.color = 60, color
    o = bpy.data.objects.new(name, data)
    o.location = (x + 2, 2, 2.6)
    bpy.context.scene.collection.objects.link(o)
    return o


bunker(80)
bunker(88)
bunker(96)
a = lamp("lamp A", 80, (1.0, 0.9, 0.8))
b = lamp("lamp B", 88, (0.8, 0.9, 1.0))
c = lamp("lamp C", 96, (1.0, 1.0, 1.0))
c.hammerless.light_switchable = True
bsp = os.path.join(OUT, "hl_test_mix.bsp")


def build(tag, smart=False):
    bpy.ops.hammerless.export_vmf()
    vmf = os.path.join(OUT, "hl_test_mix.vmf")
    tools = cc.Tools(cc.find_game_root())
    from hammerless.blender.ops import compile_options
    t = time.time()
    job = cc.CompileJob(tools, vmf, compile_options(s), copy_to_game=False, skip_if_unchanged=smart).start().wait()
    log = "\n".join(job.log)
    print(f"[{tag}] built in {time.time() - t:.1f} s ({job.plan}); failed {job.failed}", flush=True)
    return job, log


def lumps(path):
    from hammerless.core.lightmap import _lumps
    return _lumps(open(path, "rb").read())


def lightmaps(path):
    """(face, style) -> (pages, luxels, 3) linear light, from the HDR lumps."""
    lump = lumps(path)
    faces, light, texinfo = lump(58), lump(53), lump(6)
    out = {}
    for k in range(len(faces) // 56):
        b = 56 * k
        ti = struct.unpack_from("<h", faces, b + 10)[0]
        styles = faces[b + 16:b + 20]
        ofs = struct.unpack_from("<i", faces, b + 20)[0]
        w, h = (v + 1 for v in struct.unpack_from("<2i", faces, b + 36))
        if ofs < 0 or ti < 0:
            continue
        pages = 4 if struct.unpack_from("<i", texinfo, 72 * ti + 64)[0] & 0x800 else 1
        for si, st in enumerate(styles):
            if st == 255:
                break
            out[(k, st)] = rgbe(light[ofs + 4 * w * h * pages * si:ofs + 4 * w * h * pages * (si + 1)]).reshape(pages, w * h, 3)
    return out


def rgbe(raw):
    raw = np.frombuffer(raw, np.uint8).reshape(-1, 4).astype(np.float64)
    return raw[:, :3] * (2.0 ** raw[:, 3].astype(np.int8).astype(np.float64))[:, None]


def compare(name, mine, real, floor):
    lit = real.sum(-1) > floor
    err = np.abs(mine - real)[lit].sum(-1) / np.maximum(real[lit].sum(-1), 1e-9)
    ok = len(err) > 0 and np.median(err) < 0.01 and np.quantile(err, 0.99) < 0.1
    check(name, ok, f"{lit.sum()} values: median {np.median(err):.4f}, 99% {np.quantile(err, 0.99):.4f}, "
                    f"within 2% {(err < 0.02).mean() * 100:.2f}%" if len(err) else "nothing lit")


job, log = build("mixing")
check("built", not job.failed, "")
check("our light compiler, no fallback", "running Valve's" not in log, "")
from hammerless.core.lightlayers import read_layers  # noqa: E402
from hammerless.core.lightmap import read_lightmaps  # noqa: E402
layers = read_layers(os.path.join(OUT, "hl_test_mix.hllayers"))
names = sorted((l.obj, l.info.get("kind"), l.info.get("style")) for l in layers)
check("a layer per lamp, and the sun and sky", ("lamp A", "light", "0") in names and ("lamp B", "light", "0") in names
      and any(o == "lamp C" and st != "0" for o, _k, st in names) and any(k == "sky" for _o, k, _s in names), str(names))

from hammerless.blender import lightmix  # noqa: E402
data = read_lightmaps(open(bsp, "rb").read(), None)
mix = lightmix.load(bsp, data)
check("layers load for the view", mix is not None, "")
# every light is in a layer: with all of them at 0 the map is (nearly) dark; back at 1 it's the bake again
applied = mix.weights.copy()
mix.set_weights(np.zeros_like(applied))
left = np.clip(mix.mixed[..., :3], 0, None).sum() / max(data.atlas[..., :3].sum(), 1e-9)
check("the layers hold all the light (all at 0: dark)", left < 0.02, f"{left * 100:.2f}% left")
mix.set_weights(applied)
back = np.abs(mix.mixed - data.atlas)[..., :3].max()
check("and back at their weights: the bake", back < 1e-3, f"largest difference {back:.5f}")

# change things: lamp A redder and brighter, lamp B off, the switchable lamp C dimmer and blue, the sun dimmer
a.data.color, a.data.energy = (1.0, 0.3, 0.2), 150
b.data.energy = 0
c.data.color, c.data.energy = (0.4, 0.6, 1.0), 30
sun = next((o for o in bpy.data.objects if o.type == "LIGHT" and o.data.type == "SUN"), None)
if sun is not None:
    sun.data.energy *= 0.5
else:
    s.sun_brightness = s.sun_brightness // 2
t = time.time()
changed = mix.set_weights(lightmix.current_weights(bpy.context, mix))
check("remix after the change", changed, f"{(time.time() - t) * 1000:.0f} ms")
live = mix.mixed[..., :3].copy()

job, log = build("remix 1", smart=True)
check("the smart build remixes (no bake)", job.plan == "remix" and not job.failed and "running Valve's" not in log,
      f"plan {job.plan}")
remix1 = read_lightmaps(open(bsp, "rb").read(), None).atlas[..., :3]
compare("the remixed map = the live mix (flat pages)", remix1, live, 0.02)
shutil.copy(bsp, os.path.join(OUT, "remix1.bsp"))

a.data.color, a.data.energy = (0.2, 1.0, 0.3), 90                # (again: a remix of a remix)
c.data.energy = 80
job, log = build("remix 2", smart=True)
check("again: remixes", job.plan == "remix" and not job.failed, f"plan {job.plan}")
remixed = os.path.join(OUT, "remixed.bsp")
shutil.copy(bsp, remixed)
mix2 = lightmix.load(bsp, read_lightmaps(open(bsp, "rb").read(), None))
check("the view after a remix shows the map as it is",
      mix2 is not None and not mix2.set_weights(lightmix.current_weights(bpy.context, mix2)), "")

job, log = build("rebake")
rebaked = os.path.join(OUT, "rebaked.bsp")
shutil.copy(bsp, rebaked)
mine_lm, real_lm = lightmaps(remixed), lightmaps(rebaked)
keys = sorted(set(mine_lm) & set(real_lm))
only = [k for k in set(mine_lm) ^ set(real_lm) if (mine_lm.get(k, real_lm.get(k))).max() > 0.5]
check("the same faces and styles lit", not only, f"{len(keys)} face styles; only in one: {only[:5]}")


def cat(d, pick=lambda k, v: v):
    parts = [pick(k, d[k]).reshape(-1, 3) for k in keys]
    return np.concatenate([p for p in parts if len(p)] or [np.zeros((0, 3))])


compare("remixed lightmaps = baking it (every page, every style)", cat(mine_lm), cat(real_lm), 2.0)
for label, pick in (("flat pages", lambda k, v: v[:1] if k[1] == 0 else v[:0]),
                    ("bump pages", lambda k, v: v[1:]),
                    ("the switchable lamp's style", lambda k, v: v if k[1] != 0 else v[:0])):
    compare(f"  ... {label}", cat(mine_lm, pick), cat(real_lm, pick), 2.0)


def leaf_ambient(path):
    """(leaf, sample position bytes) -> the cube's 6 colours (18 values)."""
    lump = lumps(path)
    index, samples = lump(51), lump(55)
    out = {}
    for leaf in range(len(index) // 4):
        count, first = struct.unpack_from("<2H", index, 4 * leaf)
        for j in range(first, first + count):
            rec = samples[28 * j:28 * j + 28]
            out[(leaf, rec[24:27])] = rgbe(rec[:24]).ravel()
    return out


ma, ra = leaf_ambient(remixed), leaf_ambient(rebaked)
both = sorted(set(ma) & set(ra))
check("leaf ambient: most of the bake's samples are in the remix too", len(both) > 0.9 * len(ra),
      f"{len(both)} of {len(ra)} (the bake drops samples its neighbours predict, so the sets differ a little)")
compare("remixed leaf ambient (models' light) = baking it",
        np.array([ma[k] for k in both]).reshape(-1, 3), np.array([ra[k] for k in both]).reshape(-1, 3), 0.05)


def worldlights(path):
    return np.frombuffer(lumps(path)(54), np.float32).reshape(-1, 25)[:, 3:6].astype(np.float64)


compare("remixed world lights = baking it", worldlights(remixed), worldlights(rebaked), 1e-4)

# the live mix was of the first change: bake that and compare
a.data.color, a.data.energy = (1.0, 0.3, 0.2), 150
c.data.energy = 30
job, log = build("rebake of the first change")
real = read_lightmaps(open(bsp, "rb").read(), None).atlas[..., :3]
compare("live mix = baking the change", live, real, 0.02)
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
