"""The L4D2 Preview engine showing the built map with the live light compiler's lighting, in a real window.

    blender --factory-startup <map.blend> --python tests/blender/check_engine_vrad.py -- --out <folder>

Sets the engine and Rendered shading, waits for the lighting to settle, saves a screenshot, changes a light the way a
user does, waits, saves another, then quits without saving. Prints ENGINE_VRAD_RESULT {json}.
"""
import json
import os
import sys
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless

hammerless.register()
from hammerless.blender import lightview, livelighting

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out_dir = argv[argv.index("--out") + 1] if "--out" in argv else os.getcwd()
os.makedirs(out_dir, exist_ok=True)
out = {"steps": []}
state = {"phase": 0, "t": time.time(), "gen": None, "before": None}


def view3d():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                return window, area
    return None, None


def shot(name):
    window, area = view3d()
    path = os.path.join(out_dir, name)
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.screen.screenshot_area(filepath=path)
    return path


def finish(ok, why=""):
    out["ok"], out["why"] = ok, why
    out["status"] = livelighting.status()
    out["engine_session"] = livelighting.for_engine()
    from hammerless.blender import l4d2preview
    st = lightview._state
    out["vrad"] = dict(l4d2preview._VRAD, failed=str(l4d2preview._VRAD["failed"]))
    out["drawn"] = {"tbatches": None if st["tbatches"] is None else len(st["tbatches"]), "texture": st["texture"] is not None,
                    "faces": None if st["data"] is None else st["data"].faces}
    livelighting.stop(reload=False)
    print("ENGINE_VRAD_RESULT " + json.dumps(out), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def step():
    L = livelighting._live
    now = time.time()
    try:
        if state["phase"] == 0:
            scene = bpy.context.scene
            window, area = view3d()
            lamp = next((o for o in scene.objects if o.type == "LIGHT" and o.data.type != "SUN"), None)
            rv3d = area.spaces[0].region_3d         # (looking at a lamp's surroundings, from a little way off)
            if lamp is not None:
                rv3d.view_perspective = "PERSP"
                rv3d.view_location = lamp.matrix_world.translation
                rv3d.view_distance = 14.0
            out["shot_solid"] = shot("engine_vrad_solid.png")
            scene.render.engine = "HAMMERLESS_L4D2_PREVIEW"
            area.spaces[0].shading.type = "RENDERED"
            state.update(phase=1, t=now)
        elif state["phase"] == 1:
            if now - state["t"] > 150:
                return finish(False, "never settled: " + str(L.get("error")))
            if livelighting.running() and L["gen"] >= 0 and L["state"] >= 2 and L["acked"] and not L["dirty"]:
                out["steps"].append(["settled", round(now - state["t"], 2)])
                state.update(phase=2, t=now)
        elif state["phase"] == 2:                   # (a moment for the viewport to draw the settled lighting)
            if now - state["t"] > 1.0:
                bpy.context.scene.hammerless.show_sky = False
                view3d()[1].tag_redraw()
                state.update(phase=22, t=now)
        elif state["phase"] == 22:
            if now - state["t"] > 1.0:
                out["shot_nosky"] = shot("engine_vrad_nosky.png")
                bpy.context.scene.hammerless.show_sky = True
                out["shot_before"] = shot("engine_vrad_before.png")
                lamp = next(o for o in bpy.context.scene.objects if o.type == "LIGHT" and o.name in L["ents"])
                state["before"] = lightview._state["data"].atlas.copy()
                state["gen"] = L["gen"]
                lamp.data.color = (1.0, 0.35, 0.2)
                lamp.rotation_euler.z += 0.5
                state.update(phase=3, t=now)
        elif state["phase"] == 3:
            if now - state["t"] > 150:
                return finish(False, "the edit was never followed")
            if livelighting.running() and L["gen"] != state["gen"] and L["state"] >= 2 and L["acked"] and not L["dirty"]:
                out["steps"].append(["edit settled", round(now - state["t"], 2)])
                state.update(phase=4, t=now)
        elif state["phase"] == 4:
            if now - state["t"] > 1.0:
                out["shot_after"] = shot("engine_vrad_after.png")
                changed = int((abs(lightview._state["data"].atlas - state["before"]).max(axis=2) > 1e-4).sum())
                out["texels_changed"] = changed
                return finish(changed > 0, "" if changed else "the lighting didn't change")
    except Exception as ex:
        return finish(False, repr(ex))
    return 0.1


bpy.app.timers.register(step, first_interval=2.0)
