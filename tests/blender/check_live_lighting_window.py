"""Live Lighting in a real Blender window: the timer, the depsgraph handler and the viewport's texture upload.

    blender --factory-startup <map.blend> --python tests/blender/check_live_lighting_window.py

Starts Live Lighting, waits for it to settle, moves a light the way a user's edit does (the depsgraph handler must
notice it), waits for the view to follow, then quits without saving. Prints LIVE_WINDOW_RESULT {json}.
"""
import json
import sys
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless

hammerless.register()
from hammerless.blender import lightview, livelighting

out = {"steps": []}
state = {"phase": 0, "t": time.time(), "gen": None, "before": None}


def finish(ok, why=""):
    out["ok"], out["why"] = ok, why
    out["status"] = livelighting.status()
    out["texture"] = lightview._state["texture"] is not None
    livelighting.stop(reload=False)
    print("LIVE_WINDOW_RESULT " + json.dumps(out), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def step():
    L = livelighting._live
    now = time.time()
    try:
        if state["phase"] == 0:
            res = bpy.ops.hammerless.live_lighting()
            if "FINISHED" not in res:
                return finish(False, "the operator didn't start")
            state.update(phase=1, t=now)
        elif state["phase"] == 1:
            if not livelighting.running():
                return finish(False, "stopped: " + str(L.get("error")))
            if L["gen"] >= 0 and L["state"] >= 2 and L["acked"] and not L["dirty"]:
                out["steps"].append(["settled", round(now - state["t"], 2)])
                lamp = next(o for o in bpy.context.scene.objects if o.type == "LIGHT" and o.name in L["ents"])
                state["before"] = lightview._state["data"].atlas.copy()
                state["gen"] = L["gen"]
                lamp.data.color = (1.0, 0.3, 0.2)       # (no dirty flag set by hand: the handler's job)
                lamp.rotation_euler.z += 0.3
                state.update(phase=2, t=now)
            elif now - state["t"] > 120:
                return finish(False, "never settled")
        elif state["phase"] == 2:
            if not livelighting.running():
                return finish(False, "stopped: " + str(L.get("error")))
            if L["gen"] != state["gen"] and L["state"] >= 2 and L["acked"] and not L["dirty"]:
                out["steps"].append(["edit settled", round(now - state["t"], 2)])
                changed = int((abs(lightview._state["data"].atlas - state["before"]).max(axis=2) > 1e-4).sum())
                out["texels_changed"] = changed
                return finish(changed > 0, "" if changed else "the lighting didn't change")
            elif now - state["t"] > 120:
                return finish(False, "the edit was never followed")
    except Exception as ex:
        return finish(False, repr(ex))
    return 0.1


bpy.app.timers.register(step, first_interval=2.0)
