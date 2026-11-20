"""Import with Hammer Units (1:1): sizes read as in Hammer, the map is written back with the same numbers.

    blender --background --factory-startup --python tests/blender/check_hammer_units.py

Prints UNITS lines; exit 1 on a failure.
"""
import bpy, sys, os, tempfile, glob
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
def side(k, plane):
    return ('\t\tside\n\t\t{\n\t\t\t"id" "%d"\n\t\t\t"plane" "%s"\n\t\t\t"material" "DEV/DEV_MEASUREGENERIC01B"\n'
            '\t\t\t"uaxis" "[1 0 0 0] 0.25"\n\t\t\t"vaxis" "[0 -1 0 0] 0.25"\n\t\t\t"rotation" "0"\n'
            '\t\t\t"lightmapscale" "16"\n\t\t\t"smoothing_groups" "0"\n\t\t}\n' % (k, plane))
P = ["(-64 64 16) (64 64 16) (64 -64 16)", "(-64 -64 0) (64 -64 0) (64 64 0)", "(-64 64 16) (-64 -64 16) (-64 -64 0)",
     "(64 64 0) (64 -64 0) (64 -64 16)", "(64 64 16) (-64 64 16) (-64 64 0)", "(64 -64 0) (-64 -64 0) (-64 -64 16)"]
vmf = ('versioninfo\n{\n\t"mapversion" "1"\n}\nworld\n{\n\t"id" "1"\n\t"classname" "worldspawn"\n\tsolid\n\t{\n\t\t"id" "2"\n'
       + "".join(side(3 + k, p) for k, p in enumerate(P)) + '\t}\n}\n'
       'entity\n{\n\t"id" "20"\n\t"classname" "prop_static"\n\t"model" "models/props_junk/dumpster.mdl"\n\t"origin" "0 0 16"\n\t"angles" "0 0 0"\n}\n')
d = tempfile.mkdtemp(prefix="hl_test_upm_"); p = os.path.join(d, "hl_test_upm.vmf"); open(p, "w").write(vmf)
for o in list(bpy.data.objects): bpy.data.objects.remove(o, do_unlink=True)
s = bpy.context.scene.hammerless
s.output_dir = d
fails = []
bpy.ops.hammerless.import_vmf(filepath=p, hammer_units=True)
b = next(o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith("brush"))
pr = next(o for o in bpy.data.objects if o.hammerless.classname == "prop_static")
dims = tuple(round(v, 3) for v in b.dimensions)
print("UNITS per meter", s.units_per_meter, "system", bpy.context.scene.unit_settings.system, "brush", dims,
      "prop at", tuple(pr.location))
if dims != (128.0, 128.0, 16.0) or tuple(pr.location) != (0.0, 0.0, 16.0):
    fails.append("sizes aren't Hammer's")
if bpy.context.scene.unit_settings.system != "NONE" or s.units_per_meter != 1.0:
    fails.append("units not set")
s.map_name = "hl_test_upm_out"
r = bpy.ops.hammerless.export_vmf()
out = [f for f in glob.glob(os.path.join(d, "**", "*.vmf"), recursive=True) if "upm_out" in f]
t = open(out[0]).read() if out else ""
kept = sum(1 for x in P if x in t)
print("UNITS export", r, "planes kept", kept, "of 6, origin kept", '"origin" "0 0 16"' in t)
if kept != 6 or '"origin" "0 0 16"' not in t:
    fails.append("the map wasn't written back the same")
# a second import into a file that already holds a map at another scale: left alone, with a warning
s.units_per_meter = 52.49
bpy.ops.hammerless.import_vmf(filepath=p, hammer_units=True)
print("UNITS second import kept", s.units_per_meter)
if abs(s.units_per_meter - 52.49) > 1e-3:
    fails.append("the scale of a file with a map in it was changed")
print("UNITS", "FAILED: " + "; ".join(fails) if fails else "OK")
sys.exit(1 if fails else 0)
