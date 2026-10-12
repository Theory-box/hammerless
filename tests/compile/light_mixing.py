"""Live Light Mixing, end to end in Blender (no game): builds demo/demo_level.blend (never saved) with two closed
bunkers (a lamp in each), Live Light Mixing on, as hl_test_mix; then changes one lamp's colour and brightness and the
sun's, mixes the layers live (blender/lightmix.py) and compares that with baking the changed scene for real.

Run:  blender --background demo/demo_level.blend --python tests/compile/light_mixing.py
Prints PASS / FAIL lines; exit code 0 = all passed.
"""
import os
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
a = lamp("lamp A", 80, (1.0, 0.9, 0.8))
b = lamp("lamp B", 88, (0.8, 0.9, 1.0))


def build(tag):
    bpy.ops.hammerless.export_vmf()
    vmf = os.path.join(OUT, "hl_test_mix.vmf")
    tools = cc.Tools(cc.find_game_root())
    from hammerless.blender.ops import compile_options
    t = time.time()
    job = cc.CompileJob(tools, vmf, compile_options(s), copy_to_game=False).start().wait()
    log = "\n".join(job.log)
    print(f"[{tag}] built in {time.time() - t:.1f} s; failed {job.failed}")
    return job, log


job, log = build("mixing")
check("built", not job.failed, "")
check("our light compiler, no fallback", "running Valve's" not in log, "")
bsp = os.path.join(OUT, "hl_test_mix.bsp")
from hammerless.core.lightlayers import read_layers  # noqa: E402
from hammerless.core.lightmap import read_lightmaps  # noqa: E402
layers = read_layers(os.path.join(OUT, "hl_test_mix.hllayers"))
names = sorted((l.obj, l.info.get("kind")) for l in layers)
check("a layer per lamp, and the sun and sky", ("lamp A", "light") in names and ("lamp B", "light") in names
      and any(k == "sky" for _o, k in names), str(names))

from hammerless.blender import lightmix  # noqa: E402
data = read_lightmaps(open(bsp, "rb").read(), None)
mix = lightmix.load(bsp, data)
check("layers load for the view", mix is not None, "")
unchanged = np.abs(mix.mixed - data.atlas)[..., :3].max()
check("mixed as baked = the bake", unchanged < 0.02, f"largest difference {unchanged:.4f}")

# change things: lamp A redder and brighter, lamp B off, the sun dimmer
a.data.color, a.data.energy = (1.0, 0.3, 0.2), 150
b.data.energy = 0
sun = next((o for o in bpy.data.objects if o.type == "LIGHT" and o.data.type == "SUN"), None)
if sun is not None:
    sun.data.energy *= 0.5
else:
    s.sun_brightness = s.sun_brightness // 2
t = time.time()
changed = mix.set_weights(lightmix.current_weights(bpy.context, mix))
check("remix after the change", changed, f"{(time.time() - t) * 1000:.0f} ms")
live = mix.mixed[..., :3].copy()

if os.environ.get("HL_MIX_DUMP"):
    import shutil
    shutil.copy(os.path.join(OUT, "hl_test_mix.hllayers"), os.path.join(OUT, "before.hllayers"))
job, log = build("rebake")
real = read_lightmaps(open(bsp, "rb").read(), None).atlas[..., :3]
if os.environ.get("HL_MIX_DUMP"):
    np.savez(os.path.join(OUT, "dump.npz"), live=live, real=real, before=data.atlas[..., :3],
             weights=mix.weights, names=np.array([str(i) for i, _x, _v in mix.layers]))
lit = real.sum(-1) > 0.02
err = np.abs(live - real)[lit].sum(-1) / np.maximum(real[lit].sum(-1), 1e-6)
check("live mix = baking the change", np.median(err) < 0.01 and np.quantile(err, 0.99) < 0.1,
      f"median {np.median(err):.4f}, 99% {np.quantile(err, 0.99):.4f}, within 2% {(err < 0.02).mean() * 100:.2f}%")
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
