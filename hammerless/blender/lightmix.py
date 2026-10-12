"""Live Light Mixing in the Baked Lighting view: a build with it on leaves <map>.hllayers beside the map (each
light's own baked light, core/lightlayers.py). The view then shows

    rest + sum(layer * weight)

rest being the bake without the layers, and a layer's weight its light's colour and brightness now over what they
were when baked, per colour: changing a light's colour or brightness shows what baking again would give, at once.
Only the luxels a layer lights are touched when its weight changes. Moving a light, or anything else, still needs a
bake (the view says so).
"""
from __future__ import annotations

import os

import bpy
import numpy as np

from ..core.lightlayers import baked_light, read_layers, weight


class Mix:
    def __init__(self, atlas: np.ndarray, layers: list):
        h, w = atlas.shape[:2]
        self.shape = (h, w)
        self.layers = layers                 # [(info, flat atlas indices, (n, 3) values)]
        self.weights = np.ones((len(layers), 3), np.float32)
        self.baked = [baked_light(info) for info, _i, _v in layers]
        self.mixed = atlas.copy()            # (H, W, 4): what's shown (rest + layers * weights)
        self.rest = atlas[..., :3].reshape(-1, 3).copy()
        for _info, idx, vals in layers:
            np.subtract.at(self.rest, idx, vals)

    def set_weights(self, weights: np.ndarray) -> bool:
        """Show the layers at these weights; True if anything changed."""
        changed = False
        flat = self.mixed.reshape(-1, 4)
        for k, (_info, idx, vals) in enumerate(self.layers):
            delta = weights[k] - self.weights[k]
            if not np.any(np.abs(delta) > 1e-6):
                continue
            flat[idx, :3] += vals * delta
            changed = True
        if changed:
            self.weights = weights.astype(np.float32).copy()
        return changed


def layers_path(bsp: str) -> str:
    return os.path.splitext(bsp)[0] + ".hllayers"


def load(bsp: str, data) -> Mix | None:
    """The build's layers for the shown lighting (data: read_lightmaps(..., style=None)), or None (no layers, or
    from another bake: older than the map's lighting by more than the build takes)."""
    path = layers_path(bsp)
    if not os.path.exists(path) or os.path.getmtime(path) < os.path.getmtime(bsp) - 600:
        return None
    try:
        raw = read_layers(path)
    except (OSError, ValueError, Exception):
        return None
    h, w = data.atlas.shape[:2]
    layers = []
    for layer in raw:
        idx, vals = [], []
        for face, values in layer.faces.items():
            at = data.face_at.get(face)
            if at is None:
                continue
            x, y, fw, fh = at
            if fw * fh != len(values):
                return None                  # (another bake's faces: don't show nonsense)
            rows = (np.arange(fh)[:, None] + y) * w + (np.arange(fw)[None, :] + x)
            idx.append(rows.ravel())
            vals.append(values)
        layers.append((layer.info, np.concatenate(idx) if idx else np.zeros(0, np.int64),
                       np.concatenate(vals) if vals else np.zeros((0, 3), np.float32)))
    mix = Mix(data.atlas, layers)
    mix.set_weights(current_weights(bpy.context, mix))
    return mix


def _light_now(context, info: dict) -> np.ndarray | None:
    """A layer's light as the scene has it now (None: as baked)."""
    from ..core.lightbake import _key_light
    name = info.get("_hlobject", "")
    sky = info.get("kind") == "sky"
    key = "_ambient" if sky else "_light"
    if name == "*sun":                       # (the sun the build added from the Lighting settings)
        s = context.scene.hammerless
        if sky:
            text = "{} {} {} {}".format(*_rgb(s.ambient_color), s.ambient_brightness)
        else:
            text = "{} {} {} {}".format(*_rgb(s.sun_color), s.sun_brightness if s.auto_sun else 0)
        return _key_light({key: text}, key, True)
    obj = bpy.data.objects.get(name)
    if obj is None:
        return np.zeros(3)                   # (deleted since: dark)
    if obj.type == "LIGHT":
        from .extract import light_entity
        kv = light_entity(obj, context.scene.hammerless.units_per_meter).keyvalues
    else:                                    # (a light entity: its own keyvalues)
        kv = {k.key: k.value for k in obj.hammerless.keyvalues}
    if obj.hide_get() or obj.hide_viewport:
        return np.zeros(3)
    return _key_light(kv, key, True)


def _rgb(color) -> tuple:
    return tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in color)


def current_weights(context, mix: Mix) -> np.ndarray:
    out = np.ones((len(mix.layers), 3), np.float32)
    for k, (info, _i, _v) in enumerate(mix.layers):
        try:
            out[k] = weight(mix.baked[k], _light_now(context, info))
        except Exception:
            pass
    return out
