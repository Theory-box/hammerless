"""Lights are Blender lights: the Add panel's Point Light, Spot Light and Sun add Blender lights (each with the Light
panel: Pattern, Switchable, Start Off, the live light mixing), and light entities from older files become Blender
lights when the file opens, looking the same in the game (extract.light_entity is the way back)."""
from __future__ import annotations

import math

import bpy
from mathutils import Matrix

LAMP_OF = {"light": "POINT", "light_spot": "SPOT", "light_environment": "SUN"}
DEFAULT_LIGHT = {"POINT": "255 240 220 300", "SPOT": "255 240 220 600", "SUN": "255 245 225 400"}
_SKIP_KEYS = {"_light", "_cone", "_inner_cone", "pitch"}


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _apply_light(lamp, text: str, upm: float) -> None:
    """A game '_light' value ('r g b brightness') on a Blender light, as extract.light_entity writes it back."""
    parts = text.split()
    try:
        r, g, b = (max(0.0, min(255.0, float(v))) / 255.0 for v in parts[:3])
        bright = float(parts[3]) if len(parts) > 3 else 200.0
    except (ValueError, IndexError):
        return
    lamp.color = (_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b))
    if lamp.type == "SUN":
        lamp.energy = bright / 100.0
    else:
        lamp.energy = bright * 400.0 * math.pi / (upm * upm)


def add_lamp(context, kind: str) -> bpy.types.Object:
    """A Blender light of this kind ('POINT', 'SPOT', 'SUN') at the 3D cursor, selected."""
    upm = context.scene.hammerless.units_per_meter or 52.49
    name = {"POINT": "Point Light", "SPOT": "Spot Light", "SUN": "Sun"}[kind]
    lamp = bpy.data.lights.new(name, kind)
    _apply_light(lamp, DEFAULT_LIGHT[kind], upm)
    if kind == "SPOT":
        lamp.spot_size = math.radians(90)
        lamp.spot_blend = 0.3
    obj = bpy.data.objects.new(name, lamp)
    context.collection.objects.link(obj)
    obj.location = context.scene.cursor.location
    if kind == "SUN":
        obj.rotation_euler = (math.radians(40), 0.0, math.radians(30))     # (pitch -50, as the old Sun entity)
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def convert_light_entities() -> int:
    """Light entities (light, light_spot, light_environment point entities) become Blender lights doing the same:
    colour and brightness from '_light', a spot's cone, a light style as Pattern, 'initially dark' as Start Off;
    other keys are kept. Everything pointing at the old object points at the new one. Not: linked objects, preset
    parts (the preset makes its own), an imported Hammer map's objects. Returns how many."""
    from .props import LIGHT_PATTERNS
    patterns = {v for v, _l, _d in LIGHT_PATTERNS if v not in ("0", "CUSTOM")}
    n = 0
    for old in list(bpy.data.objects):
        hs = old.hammerless
        kind = LAMP_OF.get(hs.classname.strip())
        if (kind is None or old.type == "LIGHT" or old.library is not None or old.override_library is not None
                or hs.preset_part or hs.preset or old.get("hl_vmf_kind") or hs.role not in ("AUTO", "ENTITY")):
            continue
        scene = next(iter(old.users_scene), None)
        upm = (scene.hammerless.units_per_meter if scene is not None else 0) or 52.49
        kv = {k.key: k.value for k in hs.keyvalues}
        lamp = bpy.data.lights.new(old.name, kind)
        _apply_light(lamp, kv.get("_light", DEFAULT_LIGHT[kind]), upm)
        if kind == "SPOT":
            try:
                cone = float(kv.get("_cone", "45"))
                inner = float(kv.get("_inner_cone", str(cone * 0.7)))
                lamp.spot_size = math.radians(max(1.0, min(179.0, 2 * cone)))
                lamp.spot_blend = max(0.0, min(1.0, 1.0 - inner / cone)) if cone > 0 else 0.0
            except ValueError:
                pass
        new = bpy.data.objects.new(old.name + " (light)", lamp)
        for coll in old.users_collection:
            coll.objects.link(new)
        new.parent = old.parent
        # (a game light points along +X, a Blender light along its -Z: extract.light_entity turns it back)
        new.matrix_world = old.matrix_world @ Matrix.Rotation(math.radians(-90), 4, "Y")
        nh = new.hammerless
        style = kv.pop("style", "").strip()
        if style in patterns:
            nh.light_pattern = style
        elif style and style != "0":
            kv["style"] = style                    # (a style the dropdown doesn't list: kept as it was)
        flags = kv.get("spawnflags", "").strip()
        if flags == "1":
            nh.light_start_off = True
            kv.pop("spawnflags")
        for k, v in kv.items():
            if k in _SKIP_KEYS or (kind == "SUN" and k == "_ambient" and v == "140 160 190 80"):
                continue
            item = nh.keyvalues.add()
            item.key, item.value = k, v
        old.user_remap(new)                        # (logic nodes, parents, constraints: the new light now)
        name = old.name
        data = old.data
        bpy.data.objects.remove(old)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        new.name = name
        n += 1
    return n
