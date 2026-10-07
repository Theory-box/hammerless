"""Visibility view: how the last build split the map for vis, what the game draws from each part of it,
and where the vis time went. Drawn by the add-on like the nav and lighting views: nothing is added to
the scene, saved or exported.

- Portals: the openings vis works through (vbsp's portal file); slivers in red
- Rendering Load: the map's faces coloured by how many faces the game draws from there
- Vis Cost: portals coloured by the time Hammerless's vis compiler spent on them, and the objects
  whose brushes those portals were split along
"""
import os
import time

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..core import visdata

_state = {"mode": None, "key": None, "stamp": None, "checked": 0.0, "error": None, "info": {}, "objects": [],
          "fill": None, "lines": None, "scale": None, "geom": None, "heaviest": None}
_handlers = []


def _base(context) -> str:
    from .ops import work_dir
    return os.path.join(work_dir(context), context.scene.hammerless.map_name)


def _sources(context) -> dict:
    base = _base(context)
    prt = base + ".built.prt" if os.path.exists(base + ".built.prt") else base + ".prt"
    return {"prt": prt, "bsp": base + ".bsp", "cost": base + ".viscost", "vmf": base + ".built.vmf"}


def _stamp(paths: dict):
    out = []
    for p in paths.values():
        try:
            out.append(os.path.getmtime(p))
        except OSError:
            out.append(None)
    return tuple(out)


def _gradient(t: np.ndarray) -> np.ndarray:
    """0 -> blue, 0.5 -> yellow, 1 -> red."""
    t = np.clip(t, 0.0, 1.0)[:, None]
    blue, yellow, red = np.array([0.15, 0.45, 1.0]), np.array([1.0, 0.9, 0.15]), np.array([1.0, 0.12, 0.1])
    return np.where(t < 0.5, blue + (yellow - blue) * (t / 0.5), yellow + (red - yellow) * ((t - 0.5) / 0.5))


def _polys_to_tris(polys, colors):
    pos, col, lines = [], [], []
    for poly, c in zip(polys, colors):
        for i in range(1, len(poly) - 1):
            pos += [poly[0], poly[i], poly[i + 1]]
            col += [c, c, c]
        for i in range(len(poly)):
            lines += [poly[i], poly[(i + 1) % len(poly)]]
    return np.array(pos), np.array(col), np.array(lines)


def load(context) -> str | None:
    """Read what the current view mode needs from the last build. Returns an error message, or None."""
    s = context.scene.hammerless
    mode = s.vis_view
    src = _sources(context)
    _state.update(mode=mode, key=(mode, src["bsp"]), stamp=_stamp(src), error=None, info={}, objects=[],
                  fill=None, lines=None, geom=None, heaviest=None)
    if mode == "OFF":
        return None
    try:
        if mode == "LOAD":
            if not os.path.exists(src["bsp"]):
                raise ValueError("No build of this map yet: press Build")
            with open(src["bsp"], "rb") as f:
                rl = visdata.render_load(f.read())
            span = max(rl.max_faces - rl.min_faces, 1)
            col = np.concatenate([_gradient((rl.value - rl.min_faces) / span), np.full((len(rl.value), 1), 0.85)], axis=1)
            _state["geom"] = ("tris", rl.positions, col, None)
            heavy = int(np.argmax(rl.value)) if len(rl.value) else None
            _state["heaviest"] = rl.positions[heavy - heavy % 3:heavy - heavy % 3 + 3].mean(axis=0) if heavy is not None else None
            _state["info"] = {"min": rl.min_faces, "max": rl.max_faces, "clusters": rl.clusters}
            return None
        if not os.path.exists(src["prt"]):
            raise ValueError("No portal file: press Build (Quality Fast or higher, which runs vis)")
        portals = visdata.read_portals(src["prt"])
        if mode == "PORTALS":
            col = np.where(portals.slivers[:, None], np.array([1.0, 0.15, 0.15, 0.85]), np.array([0.2, 0.75, 1.0, 0.18]))
            _state["info"] = {"portals": len(portals.polys), "clusters": portals.clusters,
                              "slivers": int(portals.slivers.sum())}
            pos, c, lines = _polys_to_tris(portals.polys, col)
            _state["geom"] = ("portals", pos, c, lines)
            return None
        cost = visdata.read_costs(src["cost"], len(portals.polys))
        if cost is None:
            raise ValueError("Vis Cost needs a build with Vis Compiler: Hammerless (faster), with Quality Normal or "
                             "Final (the full vis pass)")
        share = cost.share()
        t = np.log10(np.maximum(share, 1e-6) * len(share)) / 2 + 0.5     # 1/100 of average .. 100x average
        col = np.concatenate([_gradient(t), np.clip(0.15 + share / max(share.max(), 1e-12), 0.15, 0.9)[:, None]], axis=1)
        pos, c, lines = _polys_to_tris(portals.polys, col)
        _state["geom"] = ("portals", pos, c, lines)
        owners = visdata.portal_owners(portals, src["vmf"])
        _state["objects"] = [(name, sh, sh * cost.flow_seconds) for name, sh in visdata.cost_by_object(cost, owners)[:8]]
        _state["info"] = {"seconds": cost.flow_seconds, "portals": len(portals.polys)}
        return None
    except (OSError, ValueError) as ex:
        _state["error"] = str(ex)
        return _state["error"]
    finally:
        _redraw()


def clear() -> None:
    _state.update(mode=None, key=None, stamp=None, error=None, info={}, objects=[], fill=None, lines=None,
                  geom=None, heaviest=None)
    _redraw()


def _build(scale: float) -> None:
    kind, pos, col, lines = _state["geom"]
    smooth = gpu.shader.from_builtin("SMOOTH_COLOR")
    uni = gpu.shader.from_builtin("UNIFORM_COLOR")
    p = (pos / scale).astype(np.float32)
    _state["fill"] = (smooth, batch_for_shader(smooth, "TRIS", {"pos": p, "color": col.astype(np.float32)}))
    _state["lines"] = ((uni, batch_for_shader(uni, "LINES", {"pos": (lines / scale).astype(np.float32)}))
                       if lines is not None and len(lines) else None)
    _state["scale"] = scale


def _draw():
    context = bpy.context
    s = context.scene.hammerless
    if s.vis_view == "OFF":
        return
    now = time.time()
    if _state["mode"] != s.vis_view:
        load(context)
    elif now - _state["checked"] > 1.0:          # a new build of the map replaces the view
        _state["checked"] = now
        src = _sources(context)
        if (s.vis_view, src["bsp"]) != _state["key"] or _stamp(src) != _state["stamp"]:
            load(context)
    if _state["geom"] is None:
        return
    if _state["fill"] is None or _state["scale"] != s.units_per_meter:
        _build(s.units_per_meter)
    mvp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE" if s.vis_xray else "LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    gpu.state.face_culling_set("NONE")
    shader, batch = _state["fill"]
    shader.bind()
    shader.uniform_float("ModelViewProjectionMatrix", mvp)
    batch.draw(shader)
    if _state["lines"] is not None:
        shader, batch = _state["lines"]
        shader.bind()
        shader.uniform_float("ModelViewProjectionMatrix", mvp)
        shader.uniform_float("color", (0.7, 0.9, 1.0, 0.35))
        batch.draw(shader)
    gpu.state.depth_mask_set(True)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def _redraw():
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def on_view_change(self, context):
    _state["mode"] = None               # (load on the next draw)
    _redraw()


def header_status(context) -> str:
    s = context.scene.hammerless
    return {"OFF": "", "PORTALS": "Portals", "LOAD": "Rendering load", "COST": "Vis cost"}[s.vis_view]


# ---------------------------------------------------------------- operators and panel

class HL_OT_vis_select(bpy.types.Operator):
    bl_idname = "hammerless.vis_select"
    bl_label = "Select"
    bl_description = "Select this object"
    name: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.scene.objects.get(self.name)
        if obj is None or obj.name not in context.view_layer.objects:
            self.report({"WARNING"}, f"'{self.name}' isn't in this scene's view layer")
            return {"CANCELLED"}
        for o in context.selected_objects:
            o.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class HL_OT_vis_heaviest(bpy.types.Operator):
    bl_idname = "hammerless.vis_heaviest"
    bl_label = "3D Cursor to the Heaviest Spot"
    bl_description = "Put the 3D cursor where the game draws the most of the map"

    def execute(self, context):
        p = _state["heaviest"]
        if p is None:
            return {"CANCELLED"}
        context.scene.cursor.location = tuple(p / context.scene.hammerless.units_per_meter)
        return {"FINISHED"}


def _note(layout, lines, icon="INFO"):
    col = layout.column(align=True)
    col.scale_y = 0.8
    for i, line in enumerate(lines):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def draw_panel(layout, context):
    s = context.scene.hammerless
    col = layout.column()
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(s, "vis_view")
    if s.vis_view == "OFF":
        _note(layout, ["How the last build split the map for", "visibility, what the game draws from",
                       "each spot, and where the vis time went"])
        return
    col.prop(s, "vis_xray")
    layout.separator()
    if _state["error"]:
        _note(layout, [_state["error"][i:i + 44] for i in range(0, len(_state["error"]), 44)], icon="ERROR")
        return
    info = _state["info"]
    if s.vis_view == "PORTALS" and info:
        _note(layout, [f"{info['portals']:,} portals between {info['clusters']:,} areas",
                       "Vis works through each one: fewer is faster"])
        if info["slivers"]:
            _note(layout, [f"{info['slivers']} slivers (red): tiny openings, usually", "two brushes that almost line up"],
                  icon="ERROR")
    elif s.vis_view == "LOAD" and info:
        _note(layout, [f"Faces the game draws: {info['min']:,} to {info['max']:,}",
                       "Blue: least, red: most (frame-rate", "trouble spots)"])
        layout.operator("hammerless.vis_heaviest", icon="PIVOT_CURSOR")
    elif s.vis_view == "COST" and info:
        _note(layout, [f"Vis took {info['seconds']:.1f} s ({info['portals']:,} portals)",
                       "Red portals took the longest"])
        box = layout.box().column(align=True)
        for name, share, secs in _state["objects"]:
            row = box.row(align=True)
            label = name if name else "Open space (vbsp's own splits)"
            row.label(text=f"{label}: {share:.0%} ({secs:.1f} s)")
            if name:
                row.operator("hammerless.vis_select", text="", icon="RESTRICT_SELECT_OFF").name = name
        _note(layout, ["Portals are split along brush faces:", "making a decorative object Detail",
                       "(Selected Object panel) removes its splits"])


CLASSES = (HL_OT_vis_select, HL_OT_vis_heaviest)


@bpy.app.handlers.persistent
def _forget_on_load(*_args):
    clear()


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(_forget_on_load)
    _handlers.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW"))


def unregister():
    if _forget_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_forget_on_load)
    for h in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
    _handlers.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
