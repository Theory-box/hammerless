"""Export VMF > Selected: only the selected objects, nothing added, the map's own file untouched.

    blender --background --factory-startup --python tests/blender/check_export_selected.py

Two cases: a map made in Blender, and an imported Hammer map (a world brush and one part of a func_detail selected).
Prints SELECTED lines; exit 1 on a failure.
"""
import os
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless

hammerless.register()
from hammerless.core.vmf import parse

fails = []


def check(ok, what):
    if not ok:
        fails.append(what)


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)


def select(objs):
    for o in bpy.data.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0] if objs else None


def read(path):
    with open(path, encoding="latin-1") as f:
        blocks = parse(f.read())
    world = next(b for b in blocks if b.name == "world")
    ents = [b for b in blocks if b.name == "entity"]
    return world, ents


# ---------------------------------------------------------------- a map made in Blender
clear()
s = bpy.context.scene.hammerless
out = tempfile.mkdtemp(prefix="hl_test_selected_")
s.map_name, s.output_dir, s.sound_mode = "hl_test_selected", out, "OFF"
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -0.25))
floor = bpy.context.object
floor.scale = (40, 40, 0.5)
bpy.ops.mesh.primitive_cube_add(size=2, location=(3, 0, 1))
crate = bpy.context.object
bpy.ops.mesh.primitive_cube_add(size=1, location=(-3, 0, 0.5))
other = bpy.context.object
bpy.ops.object.empty_add(location=(2, 2, 0.5))
prop = bpy.context.object
prop.hammerless.role, prop.hammerless.classname = "ENTITY", "prop_static"
bpy.ops.object.light_add(type="POINT", location=(0, 0, 3))
lamp = bpy.context.object

select([crate, prop])
r = bpy.ops.hammerless.export_vmf(selected=True)
path = os.path.join(out, "hl_test_selected_selected.vmf")
found = [os.path.join(b, f) for b, _d, fs in os.walk(out) for f in fs if f.endswith(".vmf")]
print("SELECTED new map:", r, [os.path.basename(f) for f in found])
check(len(found) == 1 and found[0].endswith("hl_test_selected_selected.vmf"), "new map: one file, the selection's")
if found:
    world, ents = read(found[0])
    classes = sorted(e.get("classname") for e in ents)
    upm = s.units_per_meter
    xs = [float(v) for so in world.blocks("solid") for sd in so.blocks("side")
          for v in sd.get("plane").replace("(", "").replace(")", "").split()[0::3]]
    print("SELECTED new map: world brushes", len(world.blocks("solid")), "entities", classes,
          "x from", min(xs) if xs else None, "to", max(xs) if xs else None)
    check(len(world.blocks("solid")) == 1, "new map: just the crate's brush")
    check(classes == ["prop_static"], "new map: just the prop, nothing added")
    check(xs and abs(min(xs) - 2 * upm) < 1 and abs(max(xs) - 4 * upm) < 1, "new map: the crate where it stands")

select([])
try:
    r = bpy.ops.hammerless.export_vmf(selected=True)
except RuntimeError as ex:
    r = "error: " + str(ex).strip().splitlines()[0][:80]
print("SELECTED nothing selected:", r)
check(r != {"FINISHED"}, "nothing selected: says so")

select([crate])
bpy.ops.hammerless.export_vmf()              # (All: the whole map, as before)
whole = os.path.join(os.path.dirname(found[0]) if found else out, "hl_test_selected.vmf")
world, ents = read(whole)
print("SELECTED All afterwards: world brushes", len(world.blocks("solid")), "entities", len(ents))
check(len(world.blocks("solid")) > 3 and len(ents) >= 3, "All still writes the whole map")


# ---------------------------------------------------------------- an imported Hammer map
def side(k, plane):
    return ('\t\tside\n\t\t{\n\t\t\t"id" "%d"\n\t\t\t"plane" "%s"\n\t\t\t"material" "DEV/DEV_MEASUREGENERIC01B"\n'
            '\t\t\t"uaxis" "[1 0 0 0] 0.25"\n\t\t\t"vaxis" "[0 -1 0 0] 0.25"\n\t\t\t"rotation" "0"\n'
            '\t\t\t"lightmapscale" "16"\n\t\t\t"smoothing_groups" "0"\n\t\t}\n' % (k, plane))


def box(sid, x0, y0, z0, x1, y1, z1):
    p = [f"({x0} {y1} {z1}) ({x1} {y1} {z1}) ({x1} {y0} {z1})", f"({x0} {y0} {z0}) ({x1} {y0} {z0}) ({x1} {y1} {z0})",
         f"({x0} {y1} {z1}) ({x0} {y0} {z1}) ({x0} {y0} {z0})", f"({x1} {y1} {z0}) ({x1} {y0} {z0}) ({x1} {y0} {z1})",
         f"({x1} {y1} {z1}) ({x0} {y1} {z1}) ({x0} {y1} {z0})", f"({x1} {y0} {z0}) ({x0} {y0} {z0}) ({x0} {y0} {z1})"]
    return '\tsolid\n\t{\n\t\t"id" "%d"\n' % sid + "".join(side(sid * 10 + k, q) for k, q in enumerate(p)) + '\t}\n', p


a, pa = box(2, -64, -64, 0, 64, 64, 16)
b, pb = box(3, 128, -64, 0, 256, 64, 179)
c, pc = box(5, 300, 0, 0, 316, 16, 64)
d, pd = box(6, 400, 0, 0, 416, 16, 552)
vmf = ('versioninfo\n{\n\t"mapversion" "1"\n}\nworld\n{\n\t"id" "1"\n\t"classname" "worldspawn"\n'
       '\t"skyname" "sky_l4d_urban01_hdr"\n' + a + b + '}\n'
       'entity\n{\n\t"id" "4"\n\t"classname" "func_detail"\n' + c + d + '}\n'
       'entity\n{\n\t"id" "20"\n\t"classname" "prop_static"\n\t"model" "models/props_junk/dumpster.mdl"\n'
       '\t"origin" "0 0 16"\n\t"angles" "0 0 0"\n}\n'
       'entity\n{\n\t"id" "21"\n\t"classname" "info_player_start"\n\t"origin" "0 0 32"\n\t"angles" "0 0 0"\n}\n')
clear()
src = os.path.join(out, "hl_test_selected_in.vmf")
with open(src, "w", encoding="latin-1") as f:
    f.write(vmf)
bpy.ops.hammerless.import_vmf(filepath=src)
s.output_dir = out
s.map_name = "hl_test_selected_imp"
wall = next(o for o in bpy.data.objects if o.get("hl_vmf_id") == 3 or o.name == "brush 3")
part = next(o for o in bpy.data.objects if o.name.endswith("brush 6"))
select([wall, part])
r = bpy.ops.hammerless.export_vmf(selected=True)
found = [os.path.join(q, f) for q, _d, fs in os.walk(out) for f in fs if f == "hl_test_selected_imp_selected.vmf"]
print("SELECTED imported map:", r, bool(found))
check(bool(found), "imported map: the file")
if found:
    text = open(found[0], encoding="latin-1").read()
    world, ents = read(found[0])
    classes = sorted(e.get("classname") for e in ents)
    kept_b = sum(1 for q in pb if q in text)
    kept_d = sum(1 for q in pd if q in text)
    gone = sum(1 for q in pa + pc if q in text)
    print("SELECTED imported map: world brushes", len(world.blocks("solid")), "entities", classes,
          "| wall planes kept", kept_b, "of 6, part planes kept", kept_d, "of 6, unselected planes present", gone)
    check(len(world.blocks("solid")) == 1 and kept_b == 6, "imported map: the wall alone, unchanged")
    check(classes == ["func_detail"] and len(ents[0].blocks("solid")) == 1 and kept_d == 6,
          "imported map: the func_detail with its selected part alone")
    check(gone == 0, "imported map: nothing unselected")
check(not any(f.endswith("hl_test_selected_imp.vmf") for q, _d, fs in os.walk(out) for f in fs),
      "imported map: the map's own file isn't written")

print("SELECTED", "FAILED: " + "; ".join(fails) if fails else "OK")
sys.exit(1 if fails else 0)
