"""Animation Preview: an animated Custom Model's animations played in the viewport, one at a time, looping.

Picking an animation puts it on the model's armature, sets the scene's frame range to it and plays; Stop puts the
armature's own action, the frame range and the current frame back as they were. Nothing is exported differently:
the picked action is one of the model's animations already."""
from __future__ import annotations

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, StringProperty

_state: dict = {"arm": None, "action": None, "slot": None, "range": None, "frame": None}
_ITEMS: list = []        # (kept: Blender holds on to the strings of a list from an items function)


def _armature(context):
    obj = context.object
    if obj is None or obj.type != "MESH":
        return None
    from .extract import effective_role
    return obj.find_armature() if effective_role(obj) == "MODEL" else None


def _actions(arm) -> list:
    """The model's animations, as the build exports them (the picked one included while previewing)."""
    from .extract import _armature_actions
    return _armature_actions(arm)


def _label(act) -> str:
    return str(act.get("hl_name") or act.name)


def _items(self, context):
    arm = _armature(context) if context else None
    items = [("NONE", "Pick an animation", "Choose one to play it here, looping", "NONE", 0)]
    for i, act in enumerate(_actions(arm) if arm is not None else []):
        start, end = (int(round(f)) for f in act.frame_range)
        items.append((act.name, _label(act), f"{end - start} frames", "ACTION", i + 1))
    _ITEMS[:] = items
    return _ITEMS


def previewing() -> bool:
    return _state["arm"] is not None


def stop(context) -> None:
    """Back as it was: the armature's own action, the frame range, the current frame."""
    if _state["arm"] is None:
        return
    screen = context.screen
    if screen is not None and screen.is_animation_playing:
        try:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        except RuntimeError:
            pass
    arm = bpy.data.objects.get(_state["arm"])
    if arm is not None and arm.animation_data is not None:
        arm.animation_data.action = _state["action"]
        slot = _state["slot"]
        if slot is not None and hasattr(arm.animation_data, "action_slot"):
            try:
                arm.animation_data.action_slot = slot
            except (TypeError, RuntimeError, ReferenceError):
                pass
    scene = context.scene
    if _state["range"] is not None:
        scene.frame_start, scene.frame_end = _state["range"]
    if _state["frame"] is not None:
        scene.frame_set(_state["frame"])
    _state.update(arm=None, action=None, slot=None, range=None, frame=None)
    context.window_manager.hl_anim_preview = "NONE"


def play(context, arm, act) -> None:
    """Puts act on the armature and loops it over its own frames."""
    scene = context.scene
    if _state["arm"] is not None and _state["arm"] != arm.name:
        stop(context)
    ad = arm.animation_data_create()
    if _state["arm"] is None:            # (what to put back: kept once, from before the first pick)
        _state.update(arm=arm.name, action=ad.action, slot=getattr(ad, "action_slot", None),
                      range=(scene.frame_start, scene.frame_end), frame=scene.frame_current)
    ad.action = act
    slots = list(getattr(act, "slots", []))
    if hasattr(ad, "action_slot") and slots:
        ad.action_slot = slots[0]
    start, end = (int(round(f)) for f in act.frame_range)
    if getattr(act, "use_cyclic", False) and end > start:
        end -= 1                          # (a loop's last frame is its first: played once per cycle)
    scene.frame_start, scene.frame_end = start, max(start, end)
    scene.frame_set(start)
    if context.screen is not None and not context.screen.is_animation_playing:
        bpy.ops.screen.animation_play()


def _on_pick(self, context):
    name = self.hl_anim_preview
    if name == "NONE":
        return
    arm = _armature(context)
    act = bpy.data.actions.get(name)
    if arm is not None and act is not None:
        play(context, arm, act)


class HL_OT_anim_preview_stop(bpy.types.Operator):
    bl_idname = "hammerless.anim_preview_stop"
    bl_label = "Stop"
    bl_description = "Stop the preview and put the armature's action and the frame range back as they were"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        stop(context)
        return {"FINISHED"}


class HL_OT_anim_preview_play(bpy.types.Operator):
    bl_idname = "hammerless.anim_preview_play"
    bl_label = "Play"
    bl_description = "Play this animation in the viewport, looping"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    action: StringProperty()

    def execute(self, context):
        arm = _armature(context)
        act = bpy.data.actions.get(self.action)
        if arm is None or act is None:
            return {"CANCELLED"}
        play(context, arm, act)
        return {"FINISHED"}


class HL_PT_obj_anim_preview(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Animation Preview"
    bl_parent_id = "HL_PT_object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        arm = _armature(context)
        return arm is not None and bool(_actions(arm))

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        arm = _armature(context)
        row = layout.row(align=True)
        row.prop(wm, "hl_anim_preview", text="")
        on = previewing() and _state["arm"] == arm.name
        if on:
            playing = context.screen is not None and context.screen.is_animation_playing
            row.operator("screen.animation_play", text="", icon="PAUSE" if playing else "PLAY")
            row.operator("hammerless.anim_preview_stop", text="", icon="SNAP_FACE")
            act = arm.animation_data.action if arm.animation_data else None
            if act is not None:
                start, end = context.scene.frame_start, context.scene.frame_end
                col = layout.column(align=True)
                col.scale_y = 0.8
                col.label(text=f"{_label(act)}: frames {start}-{end}" +
                          (", loops" if getattr(act, "use_cyclic", False) else ", plays once (looped here)"),
                          icon="ACTION")
                col.label(text="Stop (the square) puts the rig and timeline back", icon="BLANK1")


CLASSES = (HL_OT_anim_preview_stop, HL_OT_anim_preview_play, HL_PT_obj_anim_preview)


@persistent
def _forget_on_load(*_args):
    _state.update(arm=None, action=None, slot=None, range=None, frame=None)


def register():
    bpy.types.WindowManager.hl_anim_preview = EnumProperty(
        name="Animation", items=_items, update=_on_pick,
        description="Play one of this model's animations in the viewport, looping")
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(_forget_on_load)


def unregister():
    if _forget_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_forget_on_load)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    del bpy.types.WindowManager.hl_anim_preview
