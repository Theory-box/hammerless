"""Light layers: the baked light of each light kept apart, for mixing their colour and brightness live.

Light adds up (bounced light too), so a bake is the sum of what each light gives it. With Live Light Mixing on, the
build tags each light entity with a layer ("_hllayer" n, a light_environment's sky light "_hllayersky" n, and the
Blender object it came from, "_hlobject") and hlvrad -layers <map>.hllayers keys bakes the map as usual and also
writes, for each layer, the light it alone gives every luxel. Blender then shows

    rest + sum(layer * weight)

where rest is the bake minus the layers and weight is how much brighter, per colour, the light is now than when it
was baked: what baking again would give, without baking.

File (hlvrad's WriteLayers): "HLLY", version 3, faces, layers (4 ints); per layer: what it was baked with (an int
length, then "key=value" lines: kind=light|sky, classname, style, intensity (its lights' added up, vrad's), applied
(the weight the map's lighting has it at now: hlvrad -remix changes it), _hlobject, _light, _ambient, their HDR keys),
how many faces it lights, then per face: its number, its luxel count, its pages (1, or 4 on a bumped face: flat first)
and their light (3 half floats a luxel, vrad's units: the lightmap's decoded value times 255), page after page.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

MAGIC = 0x594C4C48


@dataclass
class Layer:
    info: dict                                       # what it was baked with (see the module's docstring)
    faces: dict = field(default_factory=dict)        # face number -> (pages, luxels, 3) float32, the atlas's units

    @property
    def obj(self) -> str:
        return self.info.get("_hlobject", "")

    @property
    def sky(self) -> bool:
        return self.info.get("kind") == "sky"

    @property
    def applied(self) -> np.ndarray:
        """The weight the map's lighting has this layer at (1: as baked; a remix changes it)."""
        try:
            return np.array([float(x) for x in self.info.get("applied", "1 1 1").split()][:3], np.float32)
        except ValueError:
            return np.ones(3, np.float32)


def read_layers(path: str) -> list[Layer]:
    with open(path, "rb") as f:
        data = f.read()
    magic, version, _faces, count = struct.unpack_from("<4i", data, 0)
    if magic != MAGIC or version != 3:
        raise ValueError(f"{path}: not a light layers file this version reads")
    at, layers = 16, []
    for _ in range(count):
        (n,) = struct.unpack_from("<i", data, at)
        text = data[at + 4:at + 4 + n].decode("utf-8", "replace")
        at += 4 + n
        info = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        layer = Layer(info)
        (nfaces,) = struct.unpack_from("<i", data, at)
        at += 4
        for _ in range(nfaces):
            face, luxels, pages = struct.unpack_from("<3i", data, at)
            at += 12
            values = np.frombuffer(data, "<f2", 3 * luxels * pages, at).astype(np.float32).reshape(pages, luxels, 3)
            layer.faces[face] = values / 255.0
            at += 6 * luxels * pages
        layers.append(layer)
    return layers


def layer_atlas(layer: Layer, face_at: dict, shape) -> np.ndarray:
    """One layer laid out like the lightmap atlas (read_lightmaps' face_at), (H, W, 3)."""
    out = np.zeros((shape[0], shape[1], 3), np.float32)
    for face, values in layer.faces.items():
        if face not in face_at:
            continue
        x, y, w, h = face_at[face]
        out[y:y + h, x:x + w] = values[0].reshape(h, w, 3)          # (the flat page)
    return out


def baked_light(info: dict) -> np.ndarray | None:
    """The layer's light as baked (linear, per colour): its "_light" (a sky layer: "_ambient")."""
    from .lightbake import _key_light
    return _key_light(info, "_ambient" if info.get("kind") == "sky" else "_light", True)


def weight(baked: np.ndarray | None, now: np.ndarray | None) -> np.ndarray:
    """How much of the layer to show: the light now over the light baked, per colour (1 if unknown)."""
    if baked is None or now is None:
        return np.ones(3, np.float32)
    return np.where(baked > 0, now / np.maximum(baked, 1e-12), 0.0).astype(np.float32)
