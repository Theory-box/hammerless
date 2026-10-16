"""Rain in the game: where does L4D2's particle rain (func_precipitation, Hard Rain's settings) show up? Builds
demo/demo_level.blend (never saved) as hl_test_rain with one rainstorm volume over three structures, then takes
screenshots from inside and outside each:
  brush house  - world brushes (walls with a doorway, roof)
  model house  - the same shape as one Custom Model (static prop)
  open shed    - a brush roof on four posts, open on every side
Run:  python tests/ingame/test_rain.py <shots folder>
Nothing is checked automatically: the pictures are the result (rain is client-side particles).
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from tests.ingame.bench import Bench  # noqa: E402

UPM = 52.49
BLEND = os.path.join(ROOT, "demo", "demo_level.blend")
# (metres in Blender; each house 4 x 4 m inside, 3 m tall, doorway on its -x side)
HOUSES = {"brush": (6.0, 0.0), "model": (6.0, 7.0), "shed": (6.0, -7.0)}
PREP = r'''
from hammerless.blender.logic import TREE
for t in [t for t in bpy.data.node_groups if t.bl_idname == TREE]:
    bpy.data.node_groups.remove(t)

DARK = bpy.data.materials.new("rain test dark")       # (black walls: light rain streaks show against them)
DARK.hammerless.source_material = "tools/toolsblack"

def box(name, lo, hi):
    c = [(a + b) / 2 for a, b in zip(lo, hi)]
    bpy.ops.mesh.primitive_cube_add(size=1, location=c)
    o = bpy.context.object
    o.name = name
    o.data.materials.append(DARK)
    o.scale = [b - a for a, b in zip(lo, hi)]
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o

def house(prefix, cx, cy, walls=True):
    t, h, w = 0.2, 3.0, 2.0          # wall thickness, height, half inside width
    parts = [box(prefix + " roof", (cx - w - t, cy - w - t, h), (cx + w + t, cy + w + t, h + t))]
    if walls:
        parts += [
            box(prefix + " back", (cx + w, cy - w - t, 0), (cx + w + t, cy + w + t, h)),
            box(prefix + " left", (cx - w - t, cy - w - t, 0), (cx + w, cy - w, h)),
            box(prefix + " right", (cx - w - t, cy + w, 0), (cx + w, cy + w + t, h)),
            box(prefix + " front a", (cx - w - t, cy - w, 0), (cx - w, cy - 0.6, h)),     # doorway 1.2 m wide,
            box(prefix + " front b", (cx - w - t, cy + 0.6, 0), (cx - w, cy + w, h)),     # 2.2 m tall
            box(prefix + " front top", (cx - w - t, cy - 0.6, 2.2), (cx - w, cy + 0.6, h)),
        ]
    else:
        for sx in (-1, 1):
            for sy in (-1, 1):
                x, y = cx + sx * w, cy + sy * w
                parts.append(box(prefix + " post", (x - 0.1, y - 0.1, 0), (x + 0.1, y + 0.1, h)))
    return parts

house("brush house", *HOUSES["brush"])
parts = house("model house", *HOUSES["model"])
bpy.ops.object.select_all(action="DESELECT")
for p in parts:
    p.select_set(True)
bpy.context.view_layer.objects.active = parts[0]
bpy.ops.object.join()
m = bpy.context.object
m.name = "model house"
m.hammerless.role = "MODEL"
house("open shed", *HOUSES["shed"], walls=False)

# the Weather Volume as a user adds it (Add panel), default weather (Rain Storm), sized over the houses
bpy.context.scene.cursor.location = (6.0, 0.0, 0.1)
bpy.ops.hammerless.spawn_item(item="entity:func_precipitation")
rain = bpy.context.object
rain.dimensions = (24.0, 32.0, 9.0)
assert rain.hammerless.classname == "func_precipitation" and rain.display_type == "WIRE"
'''.replace("HOUSES", repr(HOUSES))


def main(argv):
    build = "--no-build" not in argv          # (--no-build: the map is already loaded)
    argv = [a for a in argv if a != "--no-build"]
    shots = argv[0] if argv else os.path.join(ROOT, "tests", "ingame", "shots", "rain")
    with Bench() as b:
        if build:
            b.build(BLEND, "hl_test_rain", prep=PREP)
        b.console("sv_cheats 1", "god 1", "director_stop", "nb_blind 1", "noclip", wait=0.5)
        print("precipitation entities:", b.run('local n = 0, e = null; while (e = Entities.FindByClassname(e, '
                                                '"func_precipitation")) n++; return n;'), flush=True)
        print("rain type / amount:", b.run('local e = Entities.FindByName(null, "rain"); return [NetProps.GetPropInt(e, '
                                           '"m_nPrecipType"), NetProps.GetPropInt(e, "m_clrRender")];'), flush=True)
        print("wind:", b.value('Entities.FindByClassname(null, "env_wind") != null'), flush=True)
        time.sleep(2)
        for name, (cx, cy) in HOUSES.items():
            x, y = cx * UPM, cy * UPM
            # the ground inside (the demo field is sloped terrain): the camera stands on it
            ground = b.run(f"local t = {{ start = Vector({x}, {y}, 140), end = Vector({x}, {y}, -600), "
                             f"mask = 1 }}; TraceLine(t); return t.pos.z;")
            print(name, "ground z", round(ground), flush=True)
            # inside: the back wall (black), out of the doorway, up at the roof; outside, at its doorway side
            for view, pos, ang in (("inside_back", (x - 60, y, ground), (0, 0)),
                                   ("inside_door", (x + 60, y, ground), (5, 180)),
                                   ("inside_up", (x, y, ground), (-50, 180)),
                                   ("outside", (x - 7 * UPM, y, ground), (5, 0))):
                b.console("setpos {} {} {}".format(*pos), "setang {} {} 0".format(*ang), wait=0.2)
                time.sleep(2.5)         # (the rain's particles follow the player: let them settle where it is)
                print(b.screenshot(f"rain_{name}_{view}", out_dir=shots), flush=True)
        b.console("noclip", "nb_blind 0", "director_start", "god 0", "sv_cheats 0", wait=0.3)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
