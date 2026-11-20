"""Enabling the add-on in a file that doesn't use it does nothing you can see or find: no folders or files made, no
game data read, nothing added to the .blend, nothing drawn in the viewport.

    blender --factory-startup --python tests/blender/check_startup_idle.py        (with a window: timers and drawing)

Snapshot, enable the add-on, let its timers run for a few seconds, look around the view, snapshot again. Prints
IDLE lines and quits Blender; exit 1 when something was made, read or drawn.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PLACES = [tempfile.gettempdir(), bpy.app.tempdir, os.path.expanduser("~"),
          os.path.join(os.path.expanduser("~"), "Documents")]
KINDS = ("objects", "meshes", "materials", "images", "texts", "node_groups", "collections", "lights", "worlds",
         "actions")


def listing():
    out = set()
    for place in PLACES:
        try:
            out |= {os.path.join(place, f) for f in os.listdir(place)}
        except OSError:
            pass
    return out


def data():
    return {k: {x.name for x in getattr(bpy.data, k)} for k in KINDS}


state = {"step": 0}
fails = []


def finish():
    print("IDLE", "FAILED: " + "; ".join(fails) if fails else "OK")
    sys.stdout.flush()
    os._exit(1 if fails else 0)


def pixels(win, area):
    """The 3D view's picture (rendered offscreen with the view's own draw handlers), as a rough signature."""
    import gpu
    region = next(r for r in area.regions if r.type == "WINDOW")
    space = area.spaces.active
    w, h = 320, 200
    off = gpu.types.GPUOffScreen(w, h)
    with bpy.context.temp_override(window=win, area=area, region=region):
        rv = space.region_3d
        off.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region, rv.view_matrix, rv.window_matrix,
                        do_color_management=False)
    with off.bind():
        buf = gpu.state.active_framebuffer_get().read_color(0, 0, w, h, 4, 0, "UBYTE")
    buf.dimensions = w * h * 4
    off.free()
    return bytes(buf)


def run():
    try:
        return step()
    except Exception as ex:
        import traceback
        traceback.print_exc()
        fails.append(f"raised {ex!r}")
        finish()


def step():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    if state["step"] == 0:
        state["files"], state["data"] = listing(), data()
        state["picture"] = pixels(win, area)
        t = time.time()
        import hammerless
        hammerless.register()
        state["enable_s"] = time.time() - t
        state["step"] = 1
        return 6.0                       # (its timers: a tenth of a second to a second after enabling)
    files, now = listing(), data()
    new_files = sorted(f for f in files - state["files"]
                       if "hammerless" in os.path.basename(f).lower() or "hl_" in os.path.basename(f).lower())
    added = {k: sorted(now[k] - state["data"][k]) for k in KINDS if now[k] - state["data"][k]}
    from hammerless.blender import ops
    from hammerless.core import compile as cc
    read = sorted(ops._content_cache) + sorted(getattr(cc, "_CONTENT", {}))
    picture = pixels(win, area)
    changed = sum(1 for a, b in zip(picture[::4], state["picture"][::4]) if abs(a - b) > 8)
    s = bpy.context.scene.hammerless
    on = [k for k in ("show_sky", "show_problem_markers", "debug_log", "autotest") if getattr(s, k, False)]
    # a saved file (a test file of its own): looking at the build's paths, as the panels and views do, makes no folder
    from hammerless.blender import lightview, livelighting, visview
    saved = tempfile.mkdtemp(prefix="hl_test_idle_")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(saved, "hl_test_idle.blend"))
    for look in (lightview.bsp_path, livelighting.bsp_path, visview._base, ops.build_dir):
        look(bpy.context)
    made = sorted(f for f in os.listdir(saved) if f != "hl_test_idle.blend")
    print("IDLE beside a saved .blend after looking:", made)
    if made:
        fails.append("a folder was made beside the .blend without building")
    if s.show_sky:
        fails.append("the sky view is on by default")
    print(f"IDLE enabling took {state['enable_s']:.2f} s")
    print("IDLE new hammerless files or folders:", new_files)
    print("IDLE added to the file:", added)
    print("IDLE game data read:", read)
    print("IDLE view pixels changed by enabling:", changed, "of", len(picture) // 4)
    print("IDLE on by default:", on)
    if new_files:
        fails.append("files or folders were made")
    if added:
        fails.append("data was added to the .blend")
    if read:
        fails.append("the game's files were read")
    if changed > len(picture) // 4 // 200:
        fails.append("the viewport looks different")
    finish()


bpy.app.timers.register(run, first_interval=2.0)
