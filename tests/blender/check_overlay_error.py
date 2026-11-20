"""The compiler's "Overlay touching too many faces" error, end to end on an imported Hammer map: Build fails, the
Problems entry goes to the overlay, and the failed build's logs are kept.

    blender --factory-startup --python tests/blender/check_overlay_error.py        (with a window: Build is modal)

Needs the game and the compilers. The map: a sealed room with 70 separate floor blocks and one overlay assigned to
the top of every block (the limit is 64 faces). Prints OVERLAYERR lines and quits Blender; exit 1 on a failure.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless

hammerless.register()
from hammerless.blender import ops, problems

os.environ["HAMMERLESS_NO_WINDOWS"] = "1"
OUT = tempfile.mkdtemp(prefix="hl_test_ovl_")
N = int(os.environ.get("HL_TEST_OVERLAY_FACES", "70"))
ids = [100]


def side(plane, material="DEV/DEV_MEASUREGENERIC01B"):
    ids[0] += 1
    return ids[0], ('\t\tside\n\t\t{\n\t\t\t"id" "%d"\n\t\t\t"plane" "%s"\n\t\t\t"material" "%s"\n'
                    '\t\t\t"uaxis" "[1 0 0 0] 0.25"\n\t\t\t"vaxis" "[0 -1 0 0] 0.25"\n\t\t\t"rotation" "0"\n'
                    '\t\t\t"lightmapscale" "16"\n\t\t\t"smoothing_groups" "0"\n\t\t}\n' % (ids[0], plane, material))


def box(x0, y0, z0, x1, y1, z1):
    """(text, id of the top side)"""
    planes = [f"({x0} {y1} {z1}) ({x1} {y1} {z1}) ({x1} {y0} {z1})", f"({x0} {y0} {z0}) ({x1} {y0} {z0}) ({x1} {y1} {z0})",
              f"({x0} {y1} {z1}) ({x0} {y0} {z1}) ({x0} {y0} {z0})", f"({x1} {y1} {z0}) ({x1} {y0} {z0}) ({x1} {y0} {z1})",
              f"({x1} {y1} {z1}) ({x0} {y1} {z1}) ({x0} {y1} {z0})", f"({x1} {y0} {z0}) ({x0} {y0} {z0}) ({x0} {y0} {z1})"]
    sides = [side(p) for p in planes]
    ids[0] += 1
    return '\tsolid\n\t{\n\t\t"id" "%d"\n' % ids[0] + "".join(t for _i, t in sides) + '\t}\n', sides[0][0]


W = 48 * N                                   # blocks 32 wide, 16 apart, in a row along x
room = [box(-64, -256, -64, W + 64, 256, -32), box(-64, -256, 256, W + 64, 256, 288),
        box(-96, -256, -64, -64, 256, 288), box(W + 64, -256, -64, W + 96, 256, 288),
        box(-64, -288, -64, W + 64, -256, 288), box(-64, 256, -64, W + 64, 288, 288)]
blocks = [box(48 * k, -16, -32, 48 * k + 32, 16, 0) for k in range(N)]
tops = " ".join(str(top) for _t, top in blocks)
OVERLAY_AT = (W / 2, 0.0, 0.0)
vmf = ('versioninfo\n{\n\t"mapversion" "1"\n}\nworld\n{\n\t"id" "1"\n\t"classname" "worldspawn"\n'
       '\t"skyname" "sky_l4d_urban01_hdr"\n' + "".join(t for t, _i in room + blocks) + '}\n'
       'entity\n{\n\t"id" "9001"\n\t"classname" "info_overlay"\n\t"material" "decals/decal_crater001a"\n'
       '\t"Sides" "%s"\n\t"RenderOrder" "0"\n\t"StartU" "0"\n\t"EndU" "1"\n\t"StartV" "0"\n\t"EndV" "1"\n'
       '\t"BasisOrigin" "%g 0 0"\n\t"BasisU" "1 0 0"\n\t"BasisV" "0 1 0"\n\t"BasisNormal" "0 0 1"\n'
       '\t"uv0" "%g -16 0"\n\t"uv1" "%g 16 0"\n\t"uv2" "%g 16 0"\n\t"uv3" "%g -16 0"\n'
       '\t"origin" "%g 0 0"\n}\n'
       'entity\n{\n\t"id" "9002"\n\t"classname" "info_player_start"\n\t"origin" "-32 0 16"\n\t"angles" "0 0 0"\n}\n'
       'entity\n{\n\t"id" "9003"\n\t"classname" "light"\n\t"_light" "255 255 255 300"\n\t"origin" "64 64 128"\n}\n'
       % (tops, W / 2, -W / 2, -W / 2, W / 2, W / 2, W / 2))
SRC = os.path.join(OUT, "hl_test_ovl_in.vmf")
with open(SRC, "w", encoding="latin-1") as f:
    f.write(vmf)

state = {"step": 0, "t": time.time()}
fails = []


def finish():
    print("OVERLAYERR", "FAILED: " + "; ".join(fails) if fails else "OK")
    sys.stdout.flush()
    os._exit(1 if fails else 0)


def run():
    try:
        return step()
    except Exception as ex:
        import traceback
        traceback.print_exc()
        fails.append(f"raised {ex!r}")
        finish()


def step():
    s = bpy.context.scene.hammerless
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    if state["step"] == 0:
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.ops.hammerless.import_vmf(filepath=SRC)
        s.output_dir, s.map_name, s.sound_mode = OUT, "hl_test_ovl", "OFF"
        if hasattr(s, "compile_preset"):
            s.compile_preset = "FAST" if "FAST" in [i.identifier for i in
                                                    s.bl_rna.properties["compile_preset"].enum_items] else s.compile_preset
        try:
            with bpy.context.temp_override(window=win, area=area):
                r = bpy.ops.hammerless.build("INVOKE_DEFAULT")
        except RuntimeError as ex:
            r = "stopped: " + str(ex).strip().splitlines()[0][:120]
        print("OVERLAYERR build started:", r, "| overlay faces", N)
        state["step"], state["t"] = 1, time.time()
        return 1.0
    top = os.path.join(OUT, ops.FAILED_BUILDS)
    kept = sorted(os.listdir(top)) if os.path.isdir(top) else []
    built = os.path.exists(os.path.join(OUT, "hl_test_ovl.bsp")) and not kept
    if not kept and time.time() - state["t"] < 240 and not (built and time.time() - state["t"] > 60):
        return 1.0
    rows = [(p.severity, p.name[:150], p.source, p.has_location) for p in s.problems]
    for row in rows[:6]:
        print("OVERLAYERR problem:", row)
    print("OVERLAYERR failed-build folders:", kept)
    hit = next((i for i, p in enumerate(s.problems) if "touching too many faces" in p.name), None)
    if hit is None:
        fails.append("no overlay error in the Problems list")
        finish()
    p = s.problems[hit]
    upm = s.units_per_meter
    print("OVERLAYERR entry: source", repr(p.source), "has place", p.has_location,
          "place (map units)", tuple(round(v * upm, 1) for v in p.location))
    if not p.has_location:
        fails.append("the overlay error has no place")
    elif max(abs(p.location[i] * upm - OVERLAY_AT[i]) for i in range(3)) > 1.0:
        fails.append("the place isn't the overlay's")
    problems.go_to_problem(bpy.context, hit)
    sel = [o.name for o in bpy.context.selected_objects]
    print("OVERLAYERR after clicking it, selected:", sel)
    if not any(bpy.data.objects[n].hammerless.classname == "info_overlay" for n in sel):
        fails.append("clicking it doesn't select the overlay")
    if not kept:
        fails.append("the failed build's logs weren't kept")
    else:
        names = sorted(os.listdir(os.path.join(top, kept[-1])))
        info = open(os.path.join(top, kept[-1], "info.txt"), encoding="utf-8").read().splitlines()[0]
        print("OVERLAYERR kept:", names, "|", info[:150])
        if "hl_test_ovl.lastbuild.log" not in names:
            fails.append("the compile's log isn't in the kept folder")
    finish()


bpy.app.timers.register(run, first_interval=2.0)
