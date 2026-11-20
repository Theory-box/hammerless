"""A build that fails keeps its logs ("<build folder>/failed builds/<date time> <map>/"), and the Developer panel's
buttons find the logs.

    blender --factory-startup --python tests/blender/check_failed_build_logs.py        (with a window: Build is modal)

Needs the game. The build is made to fail by naming the map after one of the game's own (c1m1_hotel), which Build
refuses before compiling. Prints FAILEDLOG lines and quits Blender; exit 1 on a failure.
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
from hammerless.blender import ops

os.environ["HAMMERLESS_NO_WINDOWS"] = "1"          # (the open-folder buttons: no Explorer windows from a test)
OUT = tempfile.mkdtemp(prefix="hl_test_failed_")
fails = []
state = {"step": 0, "t": time.time()}


def check(ok, what):
    if not ok:
        fails.append(what)


def folders():
    top = os.path.join(OUT, ops.FAILED_BUILDS)
    return sorted(os.listdir(top)) if os.path.isdir(top) else []


def finish():
    print("FAILEDLOG", "FAILED: " + "; ".join(fails) if fails else "OK")
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
        s.output_dir, s.sound_mode = OUT, "OFF"
        # 1. the function itself, and that only the newest are kept
        s.map_name = "hl_test_failed"
        ops.write_log(["a build log line"])
        first = ops.keep_failed_build(bpy.context, "a made-up reason")
        names = sorted(os.listdir(first)) if first else []
        info = open(os.path.join(first, "info.txt"), encoding="utf-8").read() if first else ""
        print("FAILEDLOG kept", names, "| info starts:", info.splitlines()[:3])
        check("hammerless_log.txt" in names and "info.txt" in names, "the kept folder has the log and the info")
        check("a made-up reason" in info and "Blender " in info and "map_tool" in info, "info names the reason and setup")
        check("a build log line" in open(os.path.join(first, "hammerless_log.txt"), encoding="utf-8").read(),
              "the build log's text is kept")
        top = os.path.join(OUT, ops.FAILED_BUILDS)
        for k in range(ops.FAILED_KEPT + 5):
            os.makedirs(os.path.join(top, f"2000-01-01 00.00.{k:02d} old"), exist_ok=True)
        ops.keep_failed_build(bpy.context, "again")
        print("FAILEDLOG folders after trimming:", len(folders()), "(kept at most", ops.FAILED_KEPT, ")")
        check(len(folders()) == ops.FAILED_KEPT and os.path.basename(first) in folders(), "only the newest are kept")
        # 2. the Developer panel's buttons
        for place in ("BUILD", "FAILED", "LOG", "FREEZE", "GAME"):
            try:
                r = bpy.ops.hammerless.open_logs(place=place)
            except RuntimeError as ex:
                r = "error " + str(ex)[:80]
            print("FAILEDLOG open", place, r)
            check(r in ({"FINISHED"}, {"CANCELLED"}), f"open {place}")
        check(os.path.exists(os.path.join(OUT, "hl_test_failed.hammerless_log.txt")), "Save Build Log wrote the file")
        # 3. a real build that fails
        state["before"] = folders()
        s.map_name = "c1m1_hotel"
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -0.25))
        try:
            with bpy.context.temp_override(window=win, area=area):
                state["r"] = bpy.ops.hammerless.build("INVOKE_DEFAULT")
        except RuntimeError as ex:               # (an ERROR report raises from Python: the build did stop)
            state["r"] = "stopped: " + str(ex).strip().splitlines()[0][:90]
        print("FAILEDLOG build started:", state["r"])
        state["step"], state["t"] = 1, time.time()
        return 0.5
    new = [f for f in folders() if f not in state["before"]]
    if not new and time.time() - state["t"] < 60:
        return 0.5
    print("FAILEDLOG after the failed build, new folders:", new)
    check(len(new) == 1 and new[0].endswith("c1m1_hotel"), "the failed build left one folder of logs")
    if new:
        info = open(os.path.join(OUT, ops.FAILED_BUILDS, new[0], "info.txt"), encoding="utf-8").read()
        print("FAILEDLOG its reason:", info.splitlines()[0][:160])
        check("already in the game" in info, "the folder says why the build failed")
    finish()


bpy.app.timers.register(run, first_interval=2.0)
