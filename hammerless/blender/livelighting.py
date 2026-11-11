"""Live Lighting: the Baked Lighting view follows the scene's lights as they're edited.

The Hammerless light compiler stays running on the last build's map (core/livelight.py: hlvrad -live). The scene's
lights are compared with the ones it has (each light entity carries its object's name, "_hlobject"); what changed is
sent, and each result it makes (the changed light at once, then the bounced light settling) replaces the lightmaps
the view shows. The map's geometry is the last build's: walls moved since need a Build.
"""
import os
import re
import struct
import time

import bpy
import numpy as np

from ..core import compile as cc
from ..core import livelight
from ..core.lightmap import FACE_SIZE, TEXINFO_SIZE, SURF_BUMPLIGHT, decode_luxels

LIGHT_CLASSES = ("light", "light_spot", "light_environment")
STATES = {0: "updating", 1: "bounce light settling", 2: "up to date", 3: "up to date"}

_live = {"session": None}


def running() -> bool:
    return _live["session"] is not None


def for_engine() -> bool:
    """Whether the L4D2 Preview engine started the session (it ends when no viewport has shown it for a while)."""
    return bool(_live.get("engine"))


def _engine_viewport() -> bool:
    """Whether any 3D viewport is showing the engine's Built-Map Lighting."""
    try:
        from .l4d2preview import HAMMERLESS_L4D2Preview
        for window in bpy.context.window_manager.windows:
            scene = window.scene
            if scene.render.engine != HAMMERLESS_L4D2Preview.bl_idname or getattr(scene, "hl_preview_lighting", "") != "LIVE":
                continue
            for area in window.screen.areas:
                if area.type == "VIEW_3D" and any(sp.type == "VIEW_3D" and sp.shading.type == "RENDERED" for sp in area.spaces[:1]):
                    return True
    except Exception:
        return True                     # (can't tell: keep it)
    return False


PACE_MS = {"FAST": 0, "BALANCED": 40, "LIGHT": 150}


def apply_settings(scene) -> None:
    """The scene's Live Quality and Update Pace to the session (when one runs)."""
    session = _live.get("session")
    if session is None:
        return
    try:
        rays = _live.get("rays_full") or 1024
        if getattr(scene, "hl_live_quality", "FULL") == "DRAFT":
            rays = max(64, rays // 4)
        if rays != _live.get("rays"):
            session.rays(rays)
            _live["rays"] = rays
            _live["state"] = min(_live.get("state", 0), 1)
        pace = PACE_MS.get(getattr(scene, "hl_live_pace", "FAST"), 0)
        if pace != _live.get("pace"):
            session.pace(pace)
            _live["pace"] = pace
    except livelight.LiveError:
        pass                            # (it has ended: the timer says so)


def busy() -> bool:
    """Whether results are still coming (starting, or the lighting hasn't settled)."""
    return running() and (_live["gen"] < 0 or _live["state"] < 2 or _live["dirty"] or not _live["acked"])


def status() -> str:
    """A line for the panel: what the session is doing."""
    if not running():
        return _live.get("error") or ""
    L = _live
    if L["gen"] < 0:
        return "Live Lighting: starting (the map is being lit)..."
    text = "Live Lighting: " + STATES.get(L["state"], "updating")
    if L["unfollowed"]:
        text += " (%d lights not followed: Build)" % L["unfollowed"]
    return text


# ---------------------------------------------------------------- the scene's lights as entity keys
def scene_lights(context) -> dict[str, dict[str, str]]:
    """Object name -> its light entity's keys as a build writes them (classname, origin and angles among them); the
    sun the Lighting settings add when the scene has none: "*sun". Area lights aren't followed yet."""
    from .extract import light_entity, scene_settings_to_ir, effective_role, source_angles, object_keyvalues
    from ..core.ir import Entity
    from .mapcollection import map_objects
    from ..core.build import ATTENUATION_KEYS, default_light_environment
    from ..core.vmf import fmt_vec
    s = context.scene.hammerless
    scale = s.units_per_meter
    inside = map_objects(context.scene)
    lights, sun = {}, False

    def keys(e):
        kv = dict(e.keyvalues)
        if e.classname in ("light", "light_spot") and not any(kv.get(k, "").strip() for k in ATTENUATION_KEYS):
            kv.update(_constant_attn="0", _linear_attn="0", _quadratic_attn="1")
        kv["classname"] = e.classname
        kv["origin"] = fmt_vec(e.origin)
        kv["angles"] = fmt_vec(e.angles)
        return kv

    for obj in context.scene.objects:
        if obj.name not in inside or not obj.visible_get() or obj.get("hl_vmf_kind"):
            continue
        role = effective_role(obj)
        if role == "ENTITY" and obj.hammerless.classname in LIGHT_CLASSES:     # (a light placed as a point entity)
            e = Entity(obj.hammerless.classname, tuple(c * scale for c in obj.matrix_world.translation),
                       source_angles(obj.matrix_world), object_keyvalues(obj))
        elif role == "LIGHT" and obj.type == "LIGHT" and obj.data.type != "AREA":
            e = light_entity(obj, scale)
        else:
            continue
        sun |= e.classname == "light_environment"
        lights[obj.name] = keys(e)
    if not sun:
        e = default_light_environment(scene_settings_to_ir(s))
        if e is not None:
            lights["*sun"] = keys(e)
    return lights


def _bsp_lights(raw: bytes) -> list[tuple[int, dict[str, str]]]:
    """The compiled map's light entities: (entity index, keys)."""
    _ver, ofs, ln, _four = struct.unpack_from("<4i", raw, 8)
    found = []
    for i, block in enumerate(re.findall(rb"\{([^{}]*)\}", raw[ofs:ofs + ln])):
        kv = {k.decode("latin-1"): v.decode("latin-1") for k, v in re.findall(rb'"([^"]*)"\s+"([^"]*)"', block)}
        if kv.get("classname") in LIGHT_CLASSES and kv.get("_hl_surface_sample") != "1":
            found.append((i, kv))
    return found


def _match(scene: dict, bsp: list) -> tuple[dict[str, int], int]:
    """Which entity each scene light is: by the object name the build wrote; a map built before that, by class and
    place. Returns (name -> entity, how many of the map's lights stay unfollowed)."""
    ents, taken = {}, set()
    for i, kv in bsp:
        name = kv.get("_hlobject")
        if name and name not in ents:
            ents[name] = i
            taken.add(i)
    for name, want in scene.items():
        if name in ents:
            continue
        try:
            at = np.array([float(v) for v in want["origin"].split()])
        except ValueError:
            continue
        for i, kv in bsp:
            if i in taken or "_hlobject" in kv or kv.get("classname") != want["classname"]:
                continue
            try:
                d = np.abs(np.array([float(v) for v in kv.get("origin", "").split()]) - at).max()
            except ValueError:
                continue
            if want["classname"] == "light_environment" or d < 0.5:
                ents[name] = i
                taken.add(i)
                break
    return ents, sum(1 for i, _kv in bsp if i not in taken)


# ---------------------------------------------------------------- start, stop
ENGINE_IDLE = 20.0      # (seconds without a viewport showing it before the engine's session ends)


def start(context, for_engine: bool = False) -> str | None:
    """Start following the scene's lights on the last build's map. Returns an error message, or None."""
    from . import lightview
    from .ops import compile_options, game_root
    stop()
    s = context.scene.hammerless
    path = lightview.bsp_path(context)
    if not os.path.exists(path):
        return "This map hasn't been built yet: press Build first"
    opts = compile_options(s)
    if opts.rad == "SKIP" or not cc.use_hlvrad(opts) or opts.light_exact:
        return "Live Lighting needs the Hammerless light compiler on the GPU (Lighting settings: not Exact, not Off)"
    root = game_root(context)
    if not root:
        return "Live Lighting needs the game folder (Settings)"
    tools = cc.Tools(root)
    base = os.path.splitext(path)[0]
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if not os.path.isdir(base + ".hlvrad_models"):
            cc.prepare_hlvrad(tools, base)
    except Exception as ex:
        return f"Couldn't read the compiled map: {ex}"
    args = opts.vrad_args(True) + opts.hlvrad_args()
    if "-gi" not in args:               # (the session's bounced light is the GPU's final gathering)
        args += ["-gi", str(max(opts.bounces, 1)), "-girays", str(opts.gi_rays)]
    args += ["-threads", str(max(2, (os.cpu_count() or 4) // 2)), "-game", tools.gamedir]
    if os.path.exists(base + ".hlsky"):
        args += ["-skymap", base + ".hlsky"]
    args += ["-modeldir", base + ".hlvrad_models", base]
    try:
        ents, unfollowed = _match(scene_lights(context), _bsp_lights(raw))
    except Exception as ex:
        return f"Couldn't read the scene's lights: {ex}"
    try:
        session = livelight.LiveLight(cc.HLVRAD, args, queue_events=False)
    except Exception as ex:
        return f"Couldn't start the light compiler: {ex}"
    _live.update(session=session, path=path, mtime=os.path.getmtime(path), raw=raw, ents=ents, sent={n: {} for n in ents},
                 unfollowed=unfollowed, serial=0, acked=True, dirty=True, gen=-1, state=0, index=None, error=None,
                 started=time.time(), engine=for_engine, seen=time.time(), rays_full=opts.gi_rays, rays=opts.gi_rays, pace=0)
    err = lightview.load(context, path)
    if err:
        stop()
        return err
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.05, persistent=True)
    return None


def stop(error: str | None = None, reload: bool = True) -> None:
    session = _live.get("session")
    _live.clear()
    _live.update(session=None, error=error)
    if session is not None:
        session.close()
        if not reload:
            return
        try:                            # (the view back to the build's own lighting)
            from . import lightview
            if lightview.shown():
                lightview.load(bpy.context)
        except Exception:
            pass


# ---------------------------------------------------------------- edits out, lightmaps in
def _sync(context) -> None:
    """The scene's lights against what the session has: the differences sent, and lit."""
    L = _live
    L["dirty"] = False
    session = L["session"]
    want = scene_lights(context)
    changed = False
    for name, kv in want.items():
        if name not in L["ents"]:
            L["ents"][name] = session.add({})
            L["sent"][name] = {}
        ent, last = L["ents"][name], L["sent"][name]
        for k, v in kv.items():
            if last.get(k) != v and "\n" not in v:
                session.set(ent, k, v)
                last[k] = v
                changed = True
    for name in [n for n in L["ents"] if n not in want]:
        if name in L["sent"]:           # (a light the scene no longer has)
            session.delete(L["ents"][name])
            changed = True
        del L["ents"][name]
        L["sent"].pop(name, None)
    if changed:
        L["serial"] = session.go()
        L["acked"] = False


def _index(data, raw: bytes, faces: bytes):
    """Where each atlas texel's light is in the lighting bytes (style 0's flat page): (texels in the atlas, luxels)."""
    lump_ofs = lambda i: struct.unpack_from("<4i", raw, 8 + 16 * i)[1:3]
    tofs, _tlen = lump_ofs(6)
    width = data.atlas.shape[1]
    dst, src = [], []
    for f, (x, y, w, h) in data.face_at.items():
        b = FACE_SIZE * f
        styles = faces[b + 16:b + 20]
        ofs = struct.unpack_from("<i", faces, b + 20)[0]
        if ofs < 0 or 0 not in styles:
            continue
        ti = struct.unpack_from("<h", faces, b + 10)[0]
        flags = struct.unpack_from("<i", raw, tofs + TEXINFO_SIZE * ti + 64)[0]
        per_style = w * h * (4 if flags & SURF_BUMPLIGHT else 1)
        first = ofs // 4 + per_style * styles.index(0)
        rows = (np.arange(h, dtype=np.int64)[:, None] + y) * width + x + np.arange(w, dtype=np.int64)[None, :]
        dst.append(rows.ravel())
        src.append(first + np.arange(w * h, dtype=np.int64))
    if not dst:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    return np.concatenate(dst), np.concatenate(src)


def _show(faces: bytes, light: bytes) -> None:
    from . import lightview
    data = lightview._state["data"]
    if data is None or lightview._state["mix"] is not None:
        return
    L = _live
    key = (id(data), len(light))
    if L["index"] is None or L["index"][0] != key or L["index"][1] != faces:
        L["index"] = (key, faces, _index(data, L["raw"], faces))
    dst, src = L["index"][2]
    if len(light) < 4 or (len(src) and int(src.max()) >= len(light) // 4):
        return
    lux = decode_luxels(light[:len(light) // 4 * 4])
    data.atlas.reshape(-1, 4)[dst, :3] = lux[src]
    if lightview._state["batch"] is not None or lightview._state["texture"] is not None:
        lightview._upload()
    lightview._redraw()


def _tick():
    L = _live
    session = L.get("session")
    if session is None:
        return None
    try:
        if not session.alive():
            stop("Live Lighting stopped: " + (session.log[-1] if session.log else "the light compiler ended"))
            return None
        if L["engine"] and _engine_viewport():
            L["seen"] = time.time()
        if L["engine"] and time.time() - L["seen"] > ENGINE_IDLE:      # (no viewport shows it any more)
            stop(reload=False)
            return None
        try:                            # (a new build of the map: the session starts again on it)
            if os.path.getmtime(L["path"]) != L["mtime"] and time.time() - os.path.getmtime(L["path"]) > 1.0:
                err = start(bpy.context, for_engine=L["engine"])
                if err:
                    _live["error"] = err
                return None if err else 0.05
        except OSError:
            pass
        last = session.last_update()
        if last is not None and last[0] != L["gen"]:
            faces, light, info = session.snapshot()
            _show(faces, light)
            L["gen"], L["state"] = last[0], info["state"]
            if info["serial"] >= L["serial"]:
                L["acked"] = True
            for window in bpy.context.window_manager.windows:      # (the panel's status line)
                for area in window.screen.areas:
                    if area.type == "VIEW_3D":
                        area.tag_redraw()
        if L["dirty"] and L["acked"] and L["gen"] >= 0:
            _sync(bpy.context)
    except livelight.LiveError as ex:
        stop("Live Lighting stopped: " + str(ex).splitlines()[0])
        return None
    except Exception as ex:             # (never an error out of a timer: it would repeat forever)
        stop(f"Live Lighting stopped: {ex}")
        return None
    return 0.03


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if not running():
        return
    for u in depsgraph.updates:
        i = u.id
        if isinstance(i, (bpy.types.Light, bpy.types.Scene, bpy.types.Collection)) or (
                isinstance(i, bpy.types.Object) and (i.type == "LIGHT" or i.name in _live.get("ents", ()))):
            _live["dirty"] = True
            return


@bpy.app.handlers.persistent
def _on_load(*_args):
    stop(reload=False)


class HL_OT_live_lighting(bpy.types.Operator):
    bl_idname = "hammerless.live_lighting"
    bl_label = "Live Lighting"
    bl_description = ("The lighting view follows the lights as you move and change them: the light compiler stays "
                      "running on the last build's map. Walls moved since the build need a Build")

    def execute(self, context):
        if running():
            stop()
            return {"FINISHED"}
        err = start(context)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        s = context.scene.hammerless
        s.show_bake_visualizations = True
        s.show_lightmap = True
        return {"FINISHED"}


def draw_panel(layout, context) -> None:
    row = layout.row(align=True)
    row.operator("hammerless.live_lighting", text="Stop Live Lighting" if running() else "Live Lighting",
                 icon="PAUSE" if running() else "PLAY", depress=running())
    text = status()
    if text:
        layout.label(text=text, icon="INFO")


def register():
    bpy.utils.register_class(HL_OT_live_lighting)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_pre.append(_on_load)


def unregister():
    stop(reload=False)
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if _on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    bpy.utils.unregister_class(HL_OT_live_lighting)
