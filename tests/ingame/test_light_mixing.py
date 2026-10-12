"""Live Light Mixing's kept result in the game: builds demo/demo_level.blend (never saved) with the bunkers of
test_lightstyles.py and Live Light Mixing on, as hl_test_mixing, then:

1. measures bunker A's wall (lamp A, switchable) and bunker C's (lamp C, a plain lamp);
2. builds again with lamp A at half brightness and lamp C red: the smart build must mix the lighting from the layers
   (no bake); measures again, and checks lamp A still switches off (its style kept);
3. bakes the same scene for real (the last build's record removed) and measures again: the remix must look like it.

Run:  python tests/ingame/test_light_mixing.py [--shots <folder>]
"""
from __future__ import annotations

import os
import statistics
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from tests.ingame.bench import Bench  # noqa: E402

UPM = 52.49
BLEND = os.path.join(ROOT, "demo", "demo_level.blend")
MAP = "hl_test_mixing"
BUNKERS = {"A": 80.0, "C": 96.0}
SCENE = r'''
from hammerless.blender.logic import TREE
for t in [t for t in bpy.data.node_groups if t.bl_idname == TREE]:
    bpy.data.node_groups.remove(t)
s.compile_preset, s.vis_tool, s.light_tool = "NORMAL", "HAMMERLESS", "HAMMERLESS"
s.light_exact, s.light_mixing = False, True
wall = bpy.data.materials.new("bunker wall"); wall.hammerless.source_material = "dev/dev_measuregeneric01b"
def slab(x0, x1, y0, y1, z0, z1):
    bpy.ops.mesh.primitive_cube_add(size=1, location=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    o = bpy.context.object; o.scale = (x1 - x0, y1 - y0, z1 - z0)
    o.data.materials.append(wall)
def bunker(x):
    t = 0.25
    slab(x - t, x + 4 + t, -t, 4 + t, -t, 0); slab(x - t, x + 4 + t, -t, 4 + t, 3, 3 + t)
    slab(x - t, x, 0, 4, 0, 3); slab(x + 4, x + 4 + t, 0, 4, 0, 3)
    slab(x - t, x + 4 + t, -t, 0, 0, 3); slab(x - t, x + 4 + t, 4, 4 + t, 0, 3)
def lamp(name, x, energy, color, **settings):
    data = bpy.data.lights.new(name, "POINT"); data.energy, data.color = energy, color
    o = bpy.data.objects.new(name, data); o.location = (x + 2, 2, 2.6)
    bpy.context.scene.collection.objects.link(o)
    for k, v in settings.items():
        setattr(o.hammerless, k, v)
'''
FIRST = SCENE + r'''
bunker(80); lamp("lamp A", 80, 60, (1, 1, 1), light_switchable=True)
bunker(96); lamp("lamp C", 96, 60, (1, 1, 1))
'''
CHANGED = SCENE + r'''
bunker(80); lamp("lamp A", 80, 30, (1, 1, 1), light_switchable=True)
bunker(96); lamp("lamp C", 96, 60, (1, 0.3, 0.3))
'''
REBAKE = CHANGED + r'''
import os
for ext in (".built.vmf", ".hllayers"):           # (no record of the last build: it bakes from scratch)
    p = os.path.join(bpy.path.abspath(s.output_dir), s.map_name + ext)
    if os.path.exists(p):
        os.remove(p)
'''


def colour(path: str):
    """Mean colour (0..255) of the middle third of a screenshot."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.crop((w // 3, h // 3, 2 * w // 3, 2 * h // 3)).resize((64, 32))
    return tuple(statistics.fmean(c.tobytes()) for c in px.split())


def main(argv):
    shots = argv[argv.index("--shots") + 1] if "--shots" in argv else os.path.join(ROOT, "tests", "ingame",
                                                                                     "shots", "lightmixing")
    results = []

    def check(name, ok, note=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {note}", flush=True)

    def setup(b):
        b.console("sv_cheats 1", "god 1", "director_stop", "nb_blind 1", "noclip", "sb_stop 1",
                  "mat_dynamic_tonemapping 0", "mat_force_tonemap_scale 1", "mat_bloomscale 0", wait=0.5)
        b.run('for (local p = null; p = Entities.FindByClassname(p, "player");) { if (p.IsSurvivor() && '
              'p != GetListenServerHost()) p.SetOrigin(Vector(0, 0, 60)); NetProps.SetPropInt(p, "m_fEffects", '
              'NetProps.GetPropInt(p, "m_fEffects") & ~4); } return true;')

    def shot(b, tag, name):
        x = BUNKERS[tag]
        time.sleep(0.5)
        return colour(b.screenshot(name, pos=((x + 2) * UPM, 1.0 * UPM, 1.0 * UPM), ang=(10, 90), out_dir=shots))

    def log_text(out):
        p = os.path.join(out, MAP + ".lastbuild.log")
        return open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""

    with Bench() as b:
        out = b.build(BLEND, MAP, prep=FIRST)
        check("first build: our compilers", "running Valve's" not in log_text(out), "")
        setup(b)
        a0, c0 = shot(b, "A", "a_baked"), shot(b, "C", "c_baked")

        out = b.build(BLEND, MAP, prep=CHANGED)
        text = log_text(out)
        check("second build: mixed from the layers, no bake", "mixing the lighting from its layers" in text
              and "running Valve's" not in text, "")
        setup(b)
        a1, c1 = shot(b, "A", "a_remixed"), shot(b, "C", "c_remixed")
        check("lamp A at half: darker", sum(a1) < 0.9 * sum(a0), f"{a0} -> {a1}")
        check("lamp C red: green and blue darker, red kept", c1[1] < 0.8 * c0[1] and c1[2] < 0.8 * c0[2]
              and abs(c1[0] - c0[0]) < 0.1 * c0[0] + 2, f"{c0} -> {c1}")
        b.console("ent_fire hl_light_lamp_a TurnOff", wait=1.0)
        off = shot(b, "A", "a_remixed_off")
        check("lamp A still switches off after the remix", sum(off) < 0.1 * sum(a1), f"{off}")

        out = b.build(BLEND, MAP, prep=REBAKE)
        check("third build: a real bake", "mixing the lighting" not in log_text(out), "")
        setup(b)
        a2, c2 = shot(b, "A", "a_rebaked"), shot(b, "C", "c_rebaked")
        for name, mine, real in (("A", a1, a2), ("C", c1, c2)):
            worst = max(abs(m - r) for m, r in zip(mine, real))
            check(f"bunker {name}: the remix looks like the bake", worst <= 3.0, f"remix {mine}, bake {real}")

    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
