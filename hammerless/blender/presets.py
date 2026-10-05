"""Preset groups: one parent Empty per preset, with its parts parented to it.

The Empty carries the preset key (object.hammerless.preset) and is what the user
selects to move the whole thing or edit its settings. Each part carries its part
name (object.hammerless.preset_part). Preset settings (core PRESET_FIELDS) are
stored on the parts; editing the first target copies the value to the others.
"""
import re

import bpy
from mathutils import Vector

from ..core.entities import PRESET_FIELDS, PRESETS

_syncing = False


def preset_root(obj):
    """The preset Empty this object belongs to (itself if it is one), or None."""
    while obj is not None:
        if obj.hammerless.preset:
            return obj
        obj = obj.parent
    return None


def part_object(root, part: str):
    for child in root.children_recursive:
        if child.hammerless.preset_part == part:
            return child
    return None


def resolve(root, target):
    """(data, property name) holding a field target's value, or None."""
    part, kind, key = target
    obj = part_object(root, part)
    if obj is None:
        return None
    hs = obj.hammerless
    if kind == "kv":
        kv = next((x for x in hs.keyvalues if x.key == key), None)
        return (kv, "value") if kv else None
    out = next((o for o in hs.outputs if o.input == key), None)
    if out is None:
        return None
    return out, ("parameter" if kind == "output_param" else "target")


def _sync(data, prop: str):
    """After a keyvalue/output edit: copy it to the other targets of its field."""
    global _syncing
    if _syncing:
        return
    obj = data.id_data
    if not isinstance(obj, bpy.types.Object) or not obj.hammerless.preset_part:
        return
    root = preset_root(obj.parent)
    if root is None:
        return
    for field in PRESET_FIELDS.get(root.hammerless.preset, []):
        spots = [resolve(root, t) for t in field.targets]
        if not any(s and s[0] == data and s[1] == prop for s in spots):
            continue
        value = getattr(data, prop)
        _syncing = True
        try:
            for s in spots:
                if s and getattr(s[0], s[1]) != value:
                    setattr(s[0], s[1], value)
        finally:
            _syncing = False


def on_kv_value(self, context):
    _sync(self, "value")


def on_output_param(self, context):
    _sync(self, "parameter")


def on_output_target(self, context):
    _sync(self, "target")


def make_root(context, key: str, location, collection):
    """Create the parent Empty for a preset."""
    root = bpy.data.objects.new(PRESETS[key].label, None)
    root.empty_display_type = "ARROWS"
    root.empty_display_size = 1.0
    root.location = location
    root.hammerless.preset = key
    root.hammerless.role = "IGNORE"
    collection.objects.link(root)
    return root


def parent_to(obj, root):
    world = obj.matrix_world.copy()
    obj.parent = root
    obj.matrix_parent_inverse = root.matrix_world.inverted()
    obj.matrix_world = world


_SUFFIX = re.compile(r"\.\d{3}$")


def ungrouped_preset(obj):
    """(preset key, collection) if obj is an old-style preset part (a collection
    named after a preset, no parent Empty), else None."""
    if obj is None or preset_root(obj) is not None:
        return None
    for coll in obj.users_collection:
        name = _SUFFIX.sub("", coll.name)
        for key, p in PRESETS.items():
            if name == p.label and obj.name.startswith(p.label + " "):
                return key, coll
    return None


class HL_OT_group_preset(bpy.types.Operator):
    bl_idname = "hammerless.group_preset"
    bl_label = "Group into Preset"
    bl_description = ("Parent this preset's parts to one Empty you can select to move it and "
                      "edit its settings")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        found = ungrouped_preset(context.object)
        if found is None:
            self.report({"WARNING"}, "Not part of an ungrouped preset")
            return {"CANCELLED"}
        key, coll = found
        label = PRESETS[key].label
        parts = [o for o in coll.objects if o.parent is None and o.name.startswith(label + " ")
                 and not o.hammerless.preset]
        if not parts:
            self.report({"WARNING"}, "This preset's parts are already grouped under another object")
            return {"CANCELLED"}
        corners = [o.matrix_world @ Vector(c) for o in parts for c in o.bound_box]
        base = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
        root = make_root(context, key, base, coll)
        context.view_layer.update()
        for o in parts:
            o.hammerless.preset_part = _SUFFIX.sub("", o.name[len(label) + 1:])
            parent_to(o, root)
        for o in context.selected_objects:
            o.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        self.report({"INFO"}, f"Grouped {len(parts)} parts under '{root.name}'")
        return {"FINISHED"}


class HL_OT_select_object(bpy.types.Operator):
    bl_idname = "hammerless.select_object"
    bl_label = "Select"
    bl_description = "Select this object"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty()
    children: bpy.props.BoolProperty(default=False, description="Also select everything under it")

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None:
            return {"CANCELLED"}
        for o in context.selected_objects:
            o.select_set(False)
        if self.children:
            for c in obj.children_recursive:
                if c.visible_get():
                    c.select_set(True)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


def draw_root(layout, root):
    """Settings and parts of a preset Empty."""
    p = PRESETS.get(root.hammerless.preset)
    if p is None:
        return
    box = layout.box()
    box.label(text=p.label, icon="HOME")
    for field in PRESET_FIELDS.get(root.hammerless.preset, []):
        spot = resolve(root, field.targets[0])
        if spot:
            box.prop(spot[0], spot[1], text=field.label)
        else:
            box.label(text=f"{field.label}: (part missing)", icon="ERROR")
    op = box.operator("hammerless.select_object", text="Select All Parts", icon="RESTRICT_SELECT_OFF")
    op.name, op.children = root.name, True
    col = layout.column(align=True)
    col.label(text="Parts")
    for child in sorted(root.children, key=lambda o: o.name):
        row = col.row(align=True)
        name = child.hammerless.preset_part or child.name
        row.operator("hammerless.select_object", text=name, icon="OBJECT_DATA").name = child.name


def draw_part(layout, obj):
    """'Part of <preset>' line for a preset part, or the group button for old scenes."""
    root = preset_root(obj.parent) if obj.parent else None
    if root is not None:
        row = layout.row(align=True)
        row.label(text=f"Part of: {root.name}", icon="LINKED")
        row.operator("hammerless.select_object", text="Select", icon="HOME").name = root.name
    elif ungrouped_preset(obj):
        layout.operator("hammerless.group_preset", icon="HOME")


CLASSES = (HL_OT_group_preset, HL_OT_select_object)
