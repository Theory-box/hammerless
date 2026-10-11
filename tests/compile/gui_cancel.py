"""Cancel, in Blender's UI: a build stopped mid-compile, a Cycles bake stopped mid-bake, then a Cycles bake to the end.

Run:  blender demo/demo_level.blend --python tests/compile/gui_cancel.py
Builds as hl_test_cancel (never the .blend's own map name; the .blend is never saved). Prints and writes
<work folder>/gui_cancel_result.txt, then quits.
"""
import os
import subprocess
import sys
import time

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import hammerless  # noqa: E402

hammerless.register()
from hammerless.blender import ops  # noqa: E402
from hammerless.core import compile as cc  # noqa: E402

s = bpy.context.scene.hammerless
s.map_name = "hl_test_cancel"
s.sound_mode = "OFF"
OUT = ops.work_dir(bpy.context)
GAME_BSP = os.path.join(cc.Tools(cc.find_game_root()).maps_dir, "hl_test_cancel.bsp")
COMPILERS = ("vbsp.exe", "vvis.exe", "vrad.exe", "hlvbsp.exe", "hlvvis.exe", "hlvrad.exe")
results = []
st = {"step": 0, "t": time.time()}


def log():
    t = bpy.data.texts.get("hammerless_log")
    return t.as_string() if t else ""


def compilers_running():
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    return [c for c in COMPILERS if c in out]


def check(name, ok, detail=""):
    results.append(("PASS" if ok else "FAIL") + f" {name}" + (f": {detail}" if detail and not ok else ""))


def press(**kw):
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(window=win, area=area):
        return bpy.ops.hammerless.build("INVOKE_DEFAULT", **kw)


def cancel():
    bpy.ops.hammerless.cancel_build()


def finish():
    text = "\n".join(results)
    print(text)
    with open(os.path.join(OUT, "gui_cancel_result.txt"), "w") as f:
        f.write(text + "\n")
    sys.stdout.flush()
    os._exit(0)


def tick():
    step, waited = st["step"], time.time() - st["t"]
    if waited > 900:
        results.append(f"FAIL step {step} timed out")
        finish()
    nxt = None
    if step == 0:                         # a full build (Valve's tools: slow enough to stop mid-vis)
        if os.path.exists(GAME_BSP):
            os.remove(GAME_BSP)
        s.light_tool, s.vis_tool, s.compile_preset = "VALVE", "VALVE", "NORMAL"
        press(play=False)
        nxt = 1
    elif step == 1 and "==== vvis" in log():
        time.sleep(0.5)
        st["midway"] = "Copied" not in log()      # (the compile still running when Cancel is pressed)
        cancel()
        nxt = 2
    elif step == 2 and not ops._BUILDING:
        time.sleep(1.0)
        check("build stopped", "Build cancelled" in log(), log()[-400:])
        check("no compiler left running", not compilers_running(), str(compilers_running()))
        check("cancelled mid-compile", st["midway"])
        check("nothing copied to the game", not os.path.exists(GAME_BSP))
        s.light_tool, s.cycles_samples = "CYCLES", 4096
        press(bake=True)                  # Cycles bake: cancelled while the background Blender bakes
        nxt = 3
    elif step == 3 and ops._BAKES:
        st["child"] = ops._BAKES[0]["proc"]
        time.sleep(3.0)
        cancel()
        nxt = 4
    elif step == 4 and not ops._BUILDING:
        time.sleep(1.0)
        check("Cycles bake process stopped", st["child"].poll() is not None)
        check("bake list empty", not ops._BAKES)
        check("no compiler left running", not compilers_running(), str(compilers_running()))
        st["mark"] = len(log())
        s.cycles_samples = 16
        press(bake=True)                  # and one to the end
        nxt = 5
    elif step == 5 and not ops._BUILDING:
        new = log()[st["mark"]:]
        check("Cycles bake finished", "Cycles:" in new and "Cycles bake failed" not in new, new[-800:])
        check("responsive while baking", st.get("ticks_baking", 0) > 0)
        finish()
    if step == 5 and ops._BAKES:
        st["ticks_baking"] = st.get("ticks_baking", 0) + 1     # (this timer ran while the bake ran)
    if nxt is not None:
        st["step"], st["t"] = nxt, time.time()
    return 0.25


bpy.app.timers.register(tick, first_interval=3.0)
