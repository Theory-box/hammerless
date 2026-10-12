"""Animated models the examples make for themselves (a mesh, an armature and its actions, built by code so an example
needs no files): they show what an animated Custom Model is made of, and are ordinary objects to edit afterwards."""
from __future__ import annotations

import math

import bpy
from mathutils import Vector

POD_RADIUS = 0.45          # metres
SIDES = (("side_px", (1, 0)), ("side_nx", (-1, 0)), ("side_py", (0, 1)), ("side_ny", (0, -1)))


def _pod_material():
    mat = bpy.data.materials.get("Example Pod Flesh")
    if mat is None:
        mat = bpy.data.materials.new("Example Pod Flesh")
        mat.diffuse_color = (0.55, 0.12, 0.10, 1.0)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (0.55, 0.12, 0.10, 1.0)
        mat.hammerless.source_material = "models/infected/boomer/boomer"      # (the Boomer's skin, in the game)
    return mat


def _key(pb, frame, loc):
    pb.location = loc
    pb.keyframe_insert("location", frame=frame)


def make_pod(context, name: str, where, collection):
    """A fleshy pod on the floor: a squashed sphere skinned to an armature (root, top, four sides) with two actions:
    'idle' (a slow pulse, looping) and 'swell' (it bulges to twice its size, once). Returns the mesh object (the
    Custom Model); the armature is its parent."""
    # the armature
    arm_data = bpy.data.armatures.new(name + " Bones")
    arm = bpy.data.objects.new(name + " Rig", arm_data)
    collection.objects.link(arm)
    arm.location = where
    view_layer = context.view_layer
    prev_active = view_layer.objects.active
    view_layer.objects.active = arm
    for o in context.selected_objects:
        o.select_set(False)
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones
    root = eb.new("root")
    root.head, root.tail = (0, 0, 0), (0, 0, 0.15)
    top = eb.new("top")
    top.head, top.tail = (0, 0, POD_RADIUS * 0.6), (0, 0, POD_RADIUS * 0.9)
    top.parent = root
    for bname, (dx, dy) in SIDES:
        b = eb.new(bname)
        b.head = (dx * POD_RADIUS * 0.5, dy * POD_RADIUS * 0.5, POD_RADIUS * 0.35)
        b.tail = (dx * POD_RADIUS * 0.8, dy * POD_RADIUS * 0.8, POD_RADIUS * 0.35)
        b.parent = root
    bpy.ops.object.mode_set(mode="OBJECT")
    # the mesh: a squashed sphere sitting on the floor
    mesh = bpy.data.meshes.new(name)
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=POD_RADIUS, calc_uvs=True)
    for v in bm.verts:
        v.co.z = v.co.z * 0.75 + POD_RADIUS * 0.75
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.parent = arm
    mesh.materials.append(_pod_material())
    for p in mesh.polygons:
        p.use_smooth = True
    # weights: the top and the four sides by direction, the rest to the root
    groups = {n: obj.vertex_groups.new(name=n) for n in ["root", "top"] + [s for s, _d in SIDES]}
    for v in mesh.vertices:
        c = v.co
        up = max(0.0, (c.z - POD_RADIUS * 0.75) / (POD_RADIUS * 0.75))
        groups["top"].add([v.index], up, "REPLACE")
        horiz = Vector((c.x, c.y)).length / POD_RADIUS
        for sname, (dx, dy) in SIDES:
            w = max(0.0, (c.x * dx + c.y * dy) / POD_RADIUS) * horiz * (1.0 - up)
            if w > 0:
                groups[sname].add([v.index], w, "REPLACE")
        groups["root"].add([v.index], 0.15, "REPLACE")
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    # the actions
    arm.animation_data_create()
    for aname, length, top_up, out, cyclic in (("idle", 40, 0.06, 0.04, True), ("swell", 60, 0.45, 0.35, False)):
        act = bpy.data.actions.new(f"{name} {aname}")
        arm.animation_data.action = act
        slots = list(getattr(act, "slots", []))
        if hasattr(arm.animation_data, "action_slot") and slots:
            arm.animation_data.action_slot = slots[0]
        tb = arm.pose.bones["top"]
        mid = length // 2 if cyclic else length
        for f, k in ((1, 0.0), (1 + mid, 1.0)) + (((1 + length, 0.0),) if cyclic else ()):
            _key(tb, f, (0.0, top_up * k, 0.0))           # (a bone's own Y runs along it: here, up)
            for sname, _d in SIDES:
                _key(arm.pose.bones[sname], f, (0.0, out * k, 0.0))    # (along the side bone: outward)
        act.use_frame_range = True
        act.frame_start, act.frame_end = 1, 1 + length
        act.use_cyclic = cyclic
        act.use_fake_user = True
        act["hl_name"] = aname
        track = arm.animation_data.nla_tracks.new()       # (each action on its own muted NLA track: kept with the rig)
        track.name = aname
        track.strips.new(aname, 1, act)
        track.mute = True
    arm.animation_data.action = None
    for pb in arm.pose.bones:
        pb.location = (0, 0, 0)
    view_layer.objects.active = prev_active
    obj.hammerless.role = "MODEL"
    obj.hammerless.model_kind = "DYNAMIC"
    return obj
