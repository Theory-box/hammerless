"""Switchable and flickering baked lights in the game: builds demo/demo_level.blend (never saved) with two closed,
dark bunkers next to the field, lit with Hammerless's own compilers (vis + light), as hl_test_lightstyles:

- bunker A: a point light named hl_light_lamp_a (a named light gets its own switchable light style);
- bunker B: an unnamed point light with style 10 (one of the game's flicker patterns).

Checks, from screenshots of each bunker's wall (mean brightness of the middle of the picture):
- A is lit, goes dark after TurnOff and comes back after TurnOn;
- B changes brightness from shot to shot (it flickers);
- the build used our light compiler (no fallback to Valve's vrad).

Run:  python tests/ingame/test_lightstyles.py [--shots <folder>] [--no-build]
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
MAP = "hl_test_lightstyles"
# bunker floors at z 0, inside 4 x 4 x 3 m; A spans x 80..84, B x 88..92 (the field ends at x 70)
BUNKERS = {"A": 80.0, "B": 88.0, "C": 96.0}
PREP = r'''
from hammerless.blender.logic import TREE
for t in [t for t in bpy.data.node_groups if t.bl_idname == TREE]:
    bpy.data.node_groups.remove(t)
s.compile_preset, s.vis_tool, s.light_tool = "NORMAL", "HAMMERLESS", "HAMMERLESS"
wall = bpy.data.materials.new("bunker wall"); wall.hammerless.source_material = "dev/dev_measuregeneric01b"
def slab(name, x0, x1, y0, y1, z0, z1):
    bpy.ops.mesh.primitive_cube_add(size=1, location=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    o = bpy.context.object; o.name = name; o.scale = (x1 - x0, y1 - y0, z1 - z0)
    o.data.materials.append(wall)
def bunker(tag, x):
    t = 0.25
    slab(tag + " floor", x - t, x + 4 + t, -t, 4 + t, -t, 0)
    slab(tag + " roof", x - t, x + 4 + t, -t, 4 + t, 3, 3 + t)
    slab(tag + " west", x - t, x, 0, 4, 0, 3)
    slab(tag + " east", x + 4, x + 4 + t, 0, 4, 0, 3)
    slab(tag + " south", x - t, x + 4 + t, -t, 0, 0, 3)
    slab(tag + " north", x - t, x + 4 + t, 4, 4 + t, 0, 3)
def lamp(name, x, **settings):
    data = bpy.data.lights.new(name, "POINT"); data.energy = 60
    o = bpy.data.objects.new(name, data); o.location = (x + 2, 2, 2.6)
    bpy.context.scene.collection.objects.link(o)
    for k, v in settings.items():
        setattr(o.hammerless, k, v)
bunker("A", {A}); lamp("lamp A", {A}, light_switchable=True)
bunker("B", {B}); lamp("lamp B", {B}, light_pattern="10")
bunker("C", {C}); lamp("lamp C", {C}, light_start_off=True)
'''.format(A=BUNKERS["A"], B=BUNKERS["B"], C=BUNKERS["C"])


def brightness(path: str) -> float:
    """Mean brightness (0..255) of the middle third of a screenshot."""
    from PIL import Image
    im = Image.open(path).convert("L")
    w, h = im.size
    return statistics.fmean(im.crop((w // 3, h // 3, 2 * w // 3, 2 * h // 3)).resize((64, 32)).tobytes())


def main(argv):
    shots = argv[argv.index("--shots") + 1] if "--shots" in argv else os.path.join(ROOT, "tests", "ingame",
                                                                                     "shots", "lightstyles")
    results = []

    def check(name, ok, note=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {note}", flush=True)

    with Bench() as b:
        if "--no-build" in argv:
            b.console(f"map {MAP}")
            b._wait_loaded(MAP, b.state()[1] - 1, 240)
        else:
            out = b.build(BLEND, MAP, prep=PREP)
            log = os.path.join(out, MAP + ".lastbuild.log")
            text = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
            valve = [ln for ln in text.splitlines() if "running Valve's" in ln]
            check("built with our compilers (no fallback)", bool(text) and not valve,
                  "; ".join(valve) or ("log missing" if not text else "none"))
        # a fixed exposure: the game's auto exposure would brighten a dark picture and hide the change
        b.console("sv_cheats 1", "god 1", "director_stop", "nb_blind 1", "noclip", "mat_dynamic_tonemapping 0",
                  "mat_force_tonemap_scale 1", "mat_bloomscale 0", "mat_fullbright 0", wait=0.5)
        # the host's flashlight would light the wall too (bots stay out by the field)
        if b.value("(NetProps.GetPropInt(GetListenServerHost(), \"m_fEffects\") & 4) != 0"):
            b.console("impulse 100", wait=0.3)

        def shot(tag, name):
            x = BUNKERS[tag]
            # standing in the middle of the bunker, eye at 1.6 m, looking at the north wall
            pos = ((x + 2) * UPM, 1.0 * UPM, 1.0 * UPM)
            time.sleep(0.5)
            return brightness(b.screenshot(name, pos=pos, ang=(10, 90), out_dir=shots))

        on = shot("A", "a_on")
        b.console("ent_fire hl_light_lamp_a TurnOff", wait=1.0)
        off = shot("A", "a_off")
        b.console("ent_fire hl_light_lamp_a TurnOn", wait=1.0)
        back = shot("A", "a_back_on")
        check("switchable light is lit", on > 25, f"{on:.1f}")
        check("TurnOff darkens it", off < on * 0.35, f"on {on:.1f} -> off {off:.1f}")
        check("TurnOn brings it back", abs(back - on) < on * 0.15, f"{back:.1f}")

        b.console("ent_fire hl_light_lamp_a SetPattern a", wait=1.0)        # 'a' = darkest step of a pattern
        dim = shot("A", "a_pattern_a")
        check("SetPattern a dims it", dim < on * 0.35, f"{dim:.1f}")
        b.console("ent_fire hl_light_lamp_a SetPattern m", wait=1.0)        # 'm' = normal brightness
        b.console("ent_fire hl_light_lamp_a TurnOn", wait=0.5)

        shot("B", "b_settle")
        values = []
        for i in range(10):                     # (pattern 10 steps ten times a second: shots far apart in phase)
            time.sleep(0.13)
            values.append(brightness(b.screenshot(f"b_{i}", out_dir=shots)))
        spread = max(values) - min(values)
        check("style 10 flickers", spread > 15 and max(values) > 25,
              " ".join(f"{v:.0f}" for v in values))

        dark = shot("C", "c_start")
        b.console("ent_fire hl_light_lamp_c TurnOn", wait=1.0)
        lit = shot("C", "c_on")
        check("Starts Off is dark, then TurnOn lights it", dark < 5 and lit > 25, f"{dark:.1f} -> {lit:.1f}")

    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
