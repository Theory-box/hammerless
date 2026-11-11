"""Live Lighting in Blender (no window needed): start it on the opened scene's last build, move a light, and see the
shown lightmaps follow.

    blender --background --factory-startup <map.blend> --python tests/blender/check_live_lighting.py [-- --light NAME]

The .blend is never saved and the map never written (the session only reads it). Prints a JSON summary; exit 1 when
the lighting didn't follow.
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

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
want = argv[argv.index("--light") + 1] if "--light" in argv else None
ctx = bpy.context
out = {"map": lightview.bsp_path(ctx)}


def pump(until, timeout):
    """Run the timer's work by hand (timers don't run without a window) until until() or the time is up."""
    end = time.time() + timeout
    while time.time() < end:
        if livelighting._tick() is None:
            return False
        if until():
            return True
        time.sleep(0.02)
    return False


ok = False
try:
    t0 = time.time()
    err = livelighting.start(ctx)
    out["start_error"] = err
    if err:
        raise SystemExit
    L = livelighting._live
    out["scene_lights"] = sorted(livelighting.scene_lights(ctx))
    out["followed"], out["unfollowed"] = len(L["ents"]), L["unfollowed"]
    assert pump(lambda: L["gen"] >= 0, 120), "no first lightmaps"
    out["first_lightmaps_s"] = round(time.time() - t0, 2)
    assert pump(lambda: L["state"] >= 2 and L["acked"] and not L["dirty"], 180), "never settled"
    out["settled_s"] = round(time.time() - t0, 2)
    out["relit_at_start"] = [l for l in L["session"].log if l.startswith("HLLIVE_LIGHTS")]
    atlas = lightview._state["data"].atlas
    before = atlas.copy()

    lamps = [o for o in ctx.scene.objects if o.type == "LIGHT" and o.data.type in ("POINT", "SPOT") and o.name in L["ents"]]
    lamp = next((o for o in lamps if o.name == want), lamps[0] if lamps else None)
    if lamp is None:                    # (no lamp: the sun turned a little and tinted)
        lamp = next((o for o in ctx.scene.objects if o.type == "LIGHT" and o.name in L["ents"]), None)
        assert lamp is not None, "no followed light in the scene"
        lamp.rotation_euler.x += 0.2
    else:
        lamp.location.x += 0.6
    out["light"] = lamp.name + " (" + lamp.data.type + ")"
    lamp.data.color = (0.2, 0.5, 1.0)
    ctx.view_layer.update()
    L["dirty"] = True                   # (no depsgraph handlers in a script's own edits without a window redraw)
    t1 = time.time()
    gen = L["gen"]
    assert pump(lambda: L["gen"] != gen and L["acked"], 60), "the edit gave no result"
    out["edit_first_result_s"] = round(time.time() - t1, 3)
    first = lightview._state["data"].atlas.copy()
    assert pump(lambda: L["state"] >= 2 and L["acked"], 180), "the edit never settled"
    out["edit_settled_s"] = round(time.time() - t1, 3)
    after = lightview._state["data"].atlas
    out["texels_changed_first"] = int((abs(first - before).max(axis=2) > 1e-4).sum())
    out["texels_changed_settled"] = int((abs(after - before).max(axis=2) > 1e-4).sum())
    out["status"] = livelighting.status()
    ok = out["texels_changed_first"] > 0 and out["texels_changed_settled"] > 0
except SystemExit:
    pass
except AssertionError as ex:
    out["failed"] = str(ex)
    out["log_tail"] = livelighting._live["session"].log[-8:] if livelighting.running() else livelighting._live.get("error")
finally:
    livelighting.stop(reload=False)
out["ok"] = ok
print("LIVE_LIGHTING_RESULT " + json.dumps(out))
sys.exit(0 if ok else 1)
