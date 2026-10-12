"""Placed characters: one of the game's characters standing, sitting, lying or leaning somewhere in the map, playing
one of its own animations in a loop (a prop_dynamic of the game's model: nothing is compiled). Storytelling infected,
sleepers and husks are placed characters plus logic (Examples 3).

In Blender the object shows the real character posed at a frame of its animation (the game's mesh, skinned to that
frame of the game's animation), so the scene can be set up as it will look. Its keyvalues are what the game uses:
model, DefaultAnim, body; solid 2 (its box: shots reach it), and a large health so its OnTakeDamage output fires
without it breaking.
"""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, IntProperty
from mathutils import Matrix, Quaternion, Vector

DEFAULT_ANIMATION = {"COMMON_MALE": "Sitting06", "COMMON_FEMALE": "Sitting06"}
# animations that aren't a whole pose of their own (layers, gestures, aim matrices...): left out of the list
_NOT_A_POSE = ("layer", "noise", "aim", "gesture", "delta", "@", "pose_param", "flinch_")


def _content(context):
    from .ops import game_content, game_root
    return game_content(game_root(context))


def posed_mesh(context, model_path: str, body: int, animation: str, frame: int):
    """The character's mesh (its body variant) skinned to one frame of one of its animations, facing +X as the game
    shows a model at angle 0, in metres, textured; cached per (model, body, animation, frame). None if unreadable."""
    from ..core.gamemesh import load_game_mesh
    from ..core.gamemodel import load_game_model, standing_rest
    from .gameskeleton import _material_path, game_animation_pose
    from .ops import cache_name, game_root
    from .preview import _model_material
    import os
    upm = context.scene.hammerless.units_per_meter
    name = cache_name("HL_pose_", f"{model_path}|{body}|{animation}|{frame}", upm)
    mesh = bpy.data.meshes.get(name)
    if mesh is not None:
        return mesh
    content = _content(context)
    gm = load_game_model(content, model_path) if content is not None else None
    data = load_game_mesh(content, model_path, body=body) if gm is not None else None
    if data is None:
        return None
    frames, _fps, _loop = game_animation_pose(content, gm, animation)
    if not frames:
        rest, _src = standing_rest(content, gm)
        frames = [gm.bone_world(rest)]
    world = frames[min(max(frame, 0), len(frames) - 1)]
    turn = Matrix.Rotation(1.5707963267948966, 4, "Z")        # (-Y, as the armatures face, to +X: angle 0 in game)
    bind = [Matrix.LocRotScale(Vector(p), Quaternion((q[3], q[0], q[1], q[2])), None) for p, q in gm.bone_world()]
    pose = [turn @ Matrix.LocRotScale(Vector(p), Quaternion((q[3], q[0], q[1], q[2])), None) for p, q in world]
    skin = [po @ bi.inverted() for po, bi in zip(pose, bind)]
    verts = []
    for co, links in zip(data.positions, data.weights):
        v = Vector((0.0, 0.0, 0.0))
        for b, w in links:
            v += w * (skin[b] @ Vector(co))
        verts.append(v / upm)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], [tuple(t) for t in data.triangles])
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for li, vi in zip(poly.loop_indices, poly.vertices):
            u, v = data.uvs[vi]
            uv_layer.data[li].uv = (u, 1.0 - v)
    game_dir = os.path.join(game_root(context), "left4dead2")
    slot_of = {}
    for mi in sorted(set(data.material_index)):
        slot_of[mi] = len(mesh.materials)
        path = _material_path(content, data.materials[mi], data.cdmaterials)
        mesh.materials.append(_model_material(path, content, game_dir))
    mesh.polygons.foreach_set("material_index", [slot_of[m] for m in data.material_index[:len(mesh.polygons)]])
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    mesh.update()
    mesh["hl_model"] = model_path
    return mesh


def _kv(obj, key, default=""):
    return next((kv.value for kv in obj.hammerless.keyvalues if kv.key == key), default)


def _set_kv(obj, key, value):
    kv = next((x for x in obj.hammerless.keyvalues if x.key == key), None) or obj.hammerless.keyvalues.add()
    kv.key, kv.value = key, str(value)


def refresh_preview(context, obj) -> bool:
    """The object's preview posed again from its keyvalues (model, DefaultAnim, body) and its pose frame."""
    try:
        body = int(_kv(obj, "body", "0") or 0)
    except ValueError:
        body = 0
    mesh = posed_mesh(context, _kv(obj, "model"), body, _kv(obj, "DefaultAnim"), int(obj.get("hl_pose_frame", 0)))
    if mesh is None:
        return False
    obj.data = mesh
    obj.display_type = "TEXTURED"
    obj.show_in_front = False
    return True


def make_placed_character(context, character: str, location, collection=None, name: str | None = None,
                          animation: str | None = None, body: int = 0):
    """A placed character object (see the module's docstring)."""
    from .gameskeleton import CHARACTERS
    from .ops import make_entity_object
    path = dict((k, p) for k, _l, p in CHARACTERS)[character]
    anim = animation or DEFAULT_ANIMATION.get(character)
    if not anim:                             # (its own plain standing idle)
        from ..core.gamemodel import all_sequence_names, load_game_model
        from .gameskeleton import whole_body_idle
        content = _content(context)
        gm = load_game_model(content, path) if content is not None else None
        anim = (whole_body_idle(all_sequence_names(content, gm)) if gm is not None else "") or "Idle"
    taken = {kv.value for o in bpy.data.objects for kv in o.hammerless.keyvalues if kv.key == "targetname"}
    k = 1
    while f"placed_{k}" in taken:
        k += 1
    obj = make_entity_object(context, "prop_dynamic", location, collection=collection, name=name or f"Placed {k}",
                             keyvalues={"model": path, "DefaultAnim": anim, "body": str(body), "solid": "2",
                                        "health": "100000", "targetname": name.lower().replace(" ", "_") if name
                                        else f"placed_{k}"})
    obj["hl_placed"] = character
    obj["hl_pose_frame"] = 0
    refresh_preview(context, obj)
    return obj


def _animation_items(self, context):
    obj = context.active_object
    model = _kv(obj, "model") if obj is not None else ""
    cache = _animation_items.cache
    if cache.get("key") != model or not cache.get("ok"):
        from ..core.gamemodel import all_sequence_names, load_game_model
        content = _content(context)
        gm = load_game_model(content, model) if content is not None and model else None
        names = [n for n in all_sequence_names(content, gm) if not any(x in n.lower() for x in _NOT_A_POSE)] \
            if gm is not None else []
        cache["key"], cache["ok"] = model, bool(names)
        cache["items"] = [(n, n, "") for n in names] or [("", "(none)", "")]
    return cache["items"]


_animation_items.cache = {}


class HL_OT_add_placed_character(bpy.types.Operator):
    bl_idname = "hammerless.add_placed_character"
    bl_label = "Add Placed Character"
    bl_description = ("One of the game's characters at the 3D cursor, playing one of its own animations (sitting, lying, "
                      "leaning, banging on a door...): storytelling infected, sleepers and husks start from one")
    bl_options = {"REGISTER", "UNDO"}

    character: EnumProperty(name="Character", items=lambda self, context: _character_items())

    def execute(self, context):
        from .mapcollection import map_collection
        coll = map_collection(context.scene) or context.collection
        obj = make_placed_character(context, self.character, context.scene.cursor.location.copy(), coll)
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if obj.data is None or not obj.data.get("hl_model"):
            self.report({"WARNING"}, "Placed, but its preview needs the game's files (set the Left 4 Dead 2 folder)")
        return {"FINISHED"}


def _character_items():
    from .gameskeleton import CHARACTERS
    return [(k, label, path) for k, label, path in CHARACTERS]


class HL_OT_placed_animation(bpy.types.Operator):
    bl_idname = "hammerless.placed_animation"
    bl_label = "Animation"
    bl_description = "Which of the character's animations it plays (it loops; the preview shows the Pose Frame)"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "animation"

    animation: EnumProperty(name="Animation", items=_animation_items)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and bool(context.active_object.get("hl_placed"))

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.animation:
            return {"CANCELLED"}
        obj = context.active_object
        _set_kv(obj, "DefaultAnim", self.animation)
        obj["hl_pose_frame"] = 0
        refresh_preview(context, obj)
        return {"FINISHED"}


class HL_OT_placed_refresh(bpy.types.Operator):
    bl_idname = "hammerless.placed_refresh"
    bl_label = "Update Preview"
    bl_description = "Pose the preview again (after changing the Pose Frame, body or animation by hand)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and bool(context.active_object.get("hl_placed"))

    def execute(self, context):
        if not refresh_preview(context, context.active_object):
            self.report({"WARNING"}, "The preview needs the game's files (set the Left 4 Dead 2 folder)")
        return {"FINISHED"}


def draw_panel(layout, obj):
    box = layout.box()
    box.label(text="Placed character: " + _kv(obj, "model").split("/")[-1], icon="ARMATURE_DATA")
    row = box.row(align=True)
    row.operator("hammerless.placed_animation", text=_kv(obj, "DefaultAnim") or "Animation", icon="ACTION")
    row = box.row(align=True)
    row.prop(obj, '["hl_pose_frame"]', text="Pose Frame")
    row.operator("hammerless.placed_refresh", text="", icon="FILE_REFRESH")
    box.label(text="It loops its animation in the game; the preview shows one frame", icon="INFO")


CLASSES = (HL_OT_add_placed_character, HL_OT_placed_animation, HL_OT_placed_refresh)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
