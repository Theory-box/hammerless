"""Game skeletons: an armature made from one of L4D2's own characters (a common infected, a survivor, a special), so a
mesh weighted to it becomes a model that borrows that character's animations ($includemodel), hitboxes and IK.

The armature is built in the game's own space (characters face -Y, as in the game's files) at the game's rest pose.
Blender bones can't take every orientation a game bone has (a Blender bone's Y axis runs along it), so the exact
rest pose comes from the game's model at build time, never from the edit bones; the armature only says which game
model it is (`hl_game_model`), and its bones are matched by name. A bone moved in Edit Mode is reported.
"""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty
from mathutils import Matrix, Quaternion, Vector

# (label, the game's mesh model whose skeleton and libraries are used)
CHARACTERS = [
    ("COMMON_MALE", "Common Infected (male)", "models/infected/common_male_tshirt_cargos.mdl"),
    ("COMMON_FEMALE", "Common Infected (female)", "models/infected/common_female_tshirt_skirt.mdl"),
    ("NICK", "Survivor: Nick", "models/survivors/survivor_gambler.mdl"),
    ("ROCHELLE", "Survivor: Rochelle", "models/survivors/survivor_producer.mdl"),
    ("COACH", "Survivor: Coach", "models/survivors/survivor_coach.mdl"),
    ("ELLIS", "Survivor: Ellis", "models/survivors/survivor_mechanic.mdl"),
    ("HUNTER", "Hunter", "models/infected/hunter.mdl"),
    ("SMOKER", "Smoker", "models/infected/smoker.mdl"),
    ("BOOMER", "Boomer", "models/infected/boomer.mdl"),
    ("TANK", "Tank", "models/infected/hulk.mdl"),
    ("WITCH", "Witch", "models/infected/witch.mdl"),
]
BONE_LENGTH = 3.0        # Hammer units: a game bone has no length, a Blender bone needs one


def _load(context, path):
    from ..core.gamemodel import load_game_model
    from .ops import game_content, game_root
    content = game_content(game_root(context))
    if content is None:
        return None, None, "Set the Left 4 Dead 2 folder (Settings > Game & Folders): the skeleton comes from the game"
    gm = load_game_model(content, path)
    if gm is None:
        return None, None, f"'{path}' isn't in the game's files"
    return gm, content, ""


def game_matrices(gm, upm: float, rest) -> list[Matrix]:
    """Each bone's rest pose (a local pose of the game model's bones) in model space, as Blender matrices in metres."""
    out = []
    for pos, quat in gm.bone_world(rest):
        x, y, z, w = quat
        m = Quaternion((w, x, y, z)).to_matrix().to_4x4()
        m.translation = Vector(pos) / upm
        out.append(m)
    return out


def build_armature(context, gm, content, name: str, collection):
    """An armature with the game model's bones standing at their rest pose (game space, metres; gamemodel.standing_rest),
    named exactly as the game's."""
    from ..core.gamemodel import standing_rest
    rest, _source = standing_rest(content, gm)
    upm = context.scene.hammerless.units_per_meter
    data = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, data)
    collection.objects.link(arm)
    arm.location = context.scene.cursor.location
    arm["hl_game_model"] = gm.path
    view_layer = context.view_layer
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in context.selected_objects:
        o.select_set(False)
    view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    mats = game_matrices(gm, upm, rest)
    ebs = []
    for b, m in zip(gm.bones, mats):
        eb = data.edit_bones.new(b.name)
        eb.length = BONE_LENGTH / upm
        eb.matrix = m                       # (its Y axis is the game bone's Y: not along the limb, as in the game)
        ebs.append(eb)
    for b, eb in zip(gm.bones, ebs):
        if b.parent >= 0:
            eb.parent = ebs[b.parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    data.display_type = "STICK"
    arm.show_in_front = True
    view_layer.objects.active = arm
    return arm


def game_skeleton(obj, arm, mesh, tri_vis, size, report, content):
    """The skeleton of a mesh weighted to a game-skeleton armature: the game model's own bones (exact rest pose),
    hitboxes, attachments, IK chains and animation libraries; the mesh's weights by bone name; its own actions as
    extra animations. None (with a problem reported) when the game model can't be read."""
    from ..core import skeletal
    from ..core.gamemodel import all_sequence_names, load_game_model, standing_rest
    path = arm.get("hl_game_model", "")
    gm = load_game_model(content, path) if content is not None else None
    if gm is None:
        report.errors.append(f"Custom Model '{obj.name}': its armature '{arm.name}' is the game's '{path}', which "
                             "can't be read (set the Left 4 Dead 2 folder)")
        return None, None
    upm = bpy.context.scene.hammerless.units_per_meter
    if any(abs(c - 1.0) > 1e-3 for c in arm.matrix_world.to_scale()):
        report.warnings.append(f"Custom Model '{obj.name}': its game skeleton '{arm.name}' is scaled, but a model on a "
                               "game skeleton keeps the game's size (scale it back to 1)")
    standing, _source = standing_rest(content, gm)        # (the pose the armature was made in: the model's rest)
    bones = []
    for b, (pos, quat) in zip(gm.bones, standing):
        x, y, z, w = quat
        e = Quaternion((w, x, y, z)).to_euler("XYZ")
        bones.append(skeletal.Bone(b.name, b.parent, tuple(pos), (e.x, e.y, e.z)))
    index = {b.name: i for i, b in enumerate(gm.bones)}
    # bones moved in Blender since they were made: reported (the game's rest pose is used)
    rest = game_matrices(gm, upm, standing)
    moved = [b.name for b in arm.data.bones if b.name in index
             and (b.matrix_local.translation - rest[index[b.name]].translation).length * upm > 0.5]
    if moved:
        report.warnings.append(f"Custom Model '{obj.name}': bones of the game skeleton were moved ({', '.join(moved[:4])}"
                               f"{'...' if len(moved) > 4 else ''}): the game's own rest pose is used, so the mesh "
                               "may not follow them as in Blender")
    unknown = [g.name for g in obj.vertex_groups if g.name not in index and g.name in arm.data.bones]
    if unknown:
        report.warnings.append(f"Custom Model '{obj.name}': bones not in the game's skeleton aren't built "
                               f"({', '.join(unknown[:4])}): weight to the game's bones")
    group_bone = {g.index: index[g.name] for g in obj.vertex_groups if g.name in index}
    per_vertex = [[(group_bone[g.group], g.weight) for g in v.groups if g.group in group_bone] for v in mesh.vertices]
    weights = [tuple(per_vertex[i] for i in vis) for vis in tri_vis]
    used = {b for links in per_vertex for b, w in links if w > 0}
    atts = []
    for a in gm.attachments:
        m = a.matrix                       # (3 x 4, row by row: rotation and position relative to the bone)
        rows = [m[r * 4:r * 4 + 4] for r in range(3)] if len(m) == 12 else m
        rot3 = [[rows[r][c] for c in range(3)] for r in range(3)]
        atts.append(skeletal.Attachment(a.name, a.bone, (rows[0][3], rows[1][3], rows[2][3]),
                                        skeletal.matrix_to_euler(rot3)))
    iks = []
    for ch in getattr(gm, "ikchains", []):
        if ch.links:
            knee = next((k for _b, k in ch.links if any(abs(c) > 1e-6 for c in k)), (0.0, 0.0, 0.0))
            iks.append((ch.name, ch.links[-1][0], tuple(knee)))
    includes = [p[len("models/"):] if p.lower().startswith("models/") else p for p in gm.includes]
    names = all_sequence_names(content, gm)
    default = (next((n for n in names if n.lower() == "idle"), "")
               or next((n for n in names if n.lower().startswith(("idle_standing", "idle_neutral", "idle_calm"))), "")
               or next((n for n in names if n.lower().startswith("idle") and "upper" not in n.lower()
                        and "layer" not in n.lower()), "")
               or next((n for n in names if "idle" in n.lower()), ""))
    sk = skeletal.Skeleton(bones, weights, [], atts, valve_space=True, includes=includes,
                           hitboxes=[(h.bone, h.group, tuple(h.mins), tuple(h.maxs)) for h in gm.hitboxes],
                           keep=[i for i in range(len(bones)) if i not in used], ikchains=iks, default_animation=default)
    for p in skeletal.problems(sk):
        report.warnings.append(f"Custom Model '{obj.name}': {p}")
    return sk, rest


def game_pose(arm, sk, rest, upm):
    """The armature's current pose as the game skeleton's local bone poses (each bone's game orientation kept: a
    Blender bone and its game bone differ by a fixed turn, measured at rest)."""
    world = []
    for i, b in enumerate(sk.bones):
        pb = arm.pose.bones.get(b.name)
        if pb is None:
            world.append(None)
            continue
        fix = pb.bone.matrix_local.inverted() @ rest[i]
        world.append(pb.matrix @ fix)
    out = []
    for i, b in enumerate(sk.bones):
        if world[i] is None or (b.parent >= 0 and world[b.parent] is None):
            out.append((b.pos, b.rot))
            continue
        m = world[i] if b.parent < 0 else world[b.parent].inverted() @ world[i]
        loc, q, _s = m.decompose()
        e = q.to_euler("XYZ")
        out.append(((loc.x * upm, loc.y * upm, loc.z * upm), (e.x, e.y, e.z)))
    return out


def game_animation_pose(content, gm, name: str):
    """Every frame of one of the character's game animations (its own or an included library's) as model-space bone
    poses facing -Y like the armature (the animations' +90 deg turn taken off), and the animation's fps."""
    from ..core.gamemodel import _UNTURN, _qmul, animation_sources, retarget
    for src in animation_sources(content, gm):
        seq = next((s for s in src.sequences if s.name.lower() == name.lower()), None)
        if seq is None:
            continue
        frames = []
        for f in range(max(seq.frames, 1)):
            try:
                pose = retarget(src, gm, src.frame(seq, f))
            except ValueError:
                return None, 30.0, False
            pose = [((p[1], -p[0], p[2]), _qmul(_UNTURN, q)) if gm.bones[k].parent < 0 else (p, q)
                    for k, (p, q) in enumerate(pose)]
            frames.append(gm.bone_world(pose))
        return frames, seq.fps or 30.0, seq.loop
    return None, 30.0, False


def load_game_animation(context, arm, name: str):
    """A preview action on a game-skeleton armature: the game's animation `name`, keyed on every bone (location and
    rotation). Marked `hl_preview`: it's the game's own, so it isn't built as one of the model's animations."""
    from ..core.gamemodel import load_game_model, standing_rest
    from .ops import game_content, game_root
    content = game_content(game_root(context))
    gm = load_game_model(content, arm.get("hl_game_model", "")) if content is not None else None
    if gm is None:
        return None, "The armature's game model can't be read (set the Left 4 Dead 2 folder)"
    frames, fps, loop = game_animation_pose(content, gm, name)
    if frames is None:
        return None, f"'{name}' isn't one of this character's animations"
    upm = context.scene.hammerless.units_per_meter
    standing, _src = standing_rest(content, gm)
    game_rest = game_matrices(gm, upm, standing)
    index = {b.name: i for i, b in enumerate(gm.bones)}
    bones = [b for b in arm.data.bones if b.name in index]
    fix = {b.name: b.matrix_local.inverted() @ game_rest[index[b.name]] for b in bones}   # Blender bone -> game bone
    act = bpy.data.actions.new(f"{name} (game)")
    act["hl_preview"] = True
    act["hl_name"] = name
    act.use_fake_user = True
    ad = arm.animation_data or arm.animation_data_create()
    ad.action = act
    slots = list(getattr(act, "slots", []))
    if slots and hasattr(ad, "action_slot"):
        ad.action_slot = slots[0]
    for f, world in enumerate(frames, 1):
        pose = {}
        for b in bones:
            pos, q = world[index[b.name]]
            x, y, z, w = q
            g = Quaternion((w, x, y, z)).to_matrix().to_4x4()
            g.translation = Vector(pos) / upm
            pose[b.name] = g @ fix[b.name].inverted()               # (the Blender bone's pose, armature space)
        for b in bones:
            pb = arm.pose.bones[b.name]
            if b.parent is not None and b.parent.name in pose:
                rest_local = b.parent.matrix_local.inverted() @ b.matrix_local
                basis = rest_local.inverted() @ (pose[b.parent.name].inverted() @ pose[b.name])
            else:
                basis = b.matrix_local.inverted() @ pose[b.name]
            loc, rot, _s = basis.decompose()
            pb.location = loc
            pb.keyframe_insert("location", frame=f)
            if pb.rotation_mode == "QUATERNION":         # (keyed in the bone's own rotation mode)
                pb.rotation_quaternion = rot
                pb.keyframe_insert("rotation_quaternion", frame=f)
            elif pb.rotation_mode == "AXIS_ANGLE":
                axis, angle = rot.to_axis_angle()
                pb.rotation_axis_angle = (angle, *axis)
                pb.keyframe_insert("rotation_axis_angle", frame=f)
            else:
                pb.rotation_euler = rot.to_euler(pb.rotation_mode, pb.rotation_euler)
                pb.keyframe_insert("rotation_euler", frame=f)
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, max(len(frames), 2)
    act.use_cyclic = loop
    scene = context.scene
    scene.frame_start, scene.frame_end = 1, len(frames)      # (to play it; the scene's fps is left as it is)
    return act, ""


def _sequence_items(self, context):
    """The game animations of the active armature's character (for the search list)."""
    arm = context.active_object
    from .ops import game_root as _root
    key = (_root(context), arm.get("hl_game_model", "")) if arm is not None else ("", "")
    cache = _sequence_items.cache
    if cache.get("key") != key or not cache.get("ok"):
        from ..core.gamemodel import all_sequence_names, load_game_model
        from .ops import game_content, game_root
        names = []
        content = game_content(game_root(context)) if key[1] else None
        gm = load_game_model(content, key[1]) if content is not None else None
        if gm is not None:
            names = [n for n in all_sequence_names(content, gm) if not n.startswith("@")]
        cache["key"], cache["ok"] = key, bool(names)
        cache["items"] = [(n, n, "") for n in names] or [("", "(none)", "")]
    return cache["items"]


_sequence_items.cache = {}


class HL_OT_load_game_animation(bpy.types.Operator):
    bl_idname = "hammerless.load_game_animation"
    bl_label = "Load Game Animation"
    bl_description = ("Preview one of the game's animations of this character on the selected game skeleton (an action "
                      "you can play in Blender; it isn't built: the model borrows all of them from the game)")
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "sequence"

    sequence: EnumProperty(name="Animation", items=_sequence_items)

    @classmethod
    def poll(cls, context):
        a = context.active_object
        return a is not None and a.type == "ARMATURE" and bool(a.get("hl_game_model"))

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.sequence:
            return {"CANCELLED"}
        act, why = load_game_animation(context, context.active_object, self.sequence)
        if act is None:
            self.report({"ERROR"}, why)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Loaded '{self.sequence}' as the action '{act.name}' (play it with Space)")
        return {"FINISHED"}


class HL_OT_add_game_skeleton(bpy.types.Operator):
    bl_idname = "hammerless.add_game_skeleton"
    bl_label = "Add Game Skeleton"
    bl_description = ("An armature with the bones of one of the game's characters, at the 3D cursor (facing -Y, as in "
                      "the game's files). Weight a mesh to it (Armature modifier) and make the mesh a Custom Model: in "
                      "the game it can play all that character's animations, and has its hitboxes")
    bl_options = {"REGISTER", "UNDO"}

    character: EnumProperty(name="Character", items=[(k, label, path) for k, label, path in CHARACTERS])

    def execute(self, context):
        path = dict((k, p) for k, _l, p in CHARACTERS)[self.character]
        gm, content, why = _load(context, path)
        if gm is None:
            self.report({"ERROR"}, why)
            return {"CANCELLED"}
        label = dict((k, l) for k, l, _p in CHARACTERS)[self.character]
        from .mapcollection import map_collection
        coll = map_collection(context.scene) or context.scene.collection
        arm = build_armature(context, gm, content, f"{label} Skeleton", coll)
        self.report({"INFO"}, f"Added '{arm.name}': {len(gm.bones)} bones from {path}")
        return {"FINISHED"}


def _material_path(content, name: str, cdmaterials) -> str:
    """A game mesh's material as a path under materials/ (its name under each of the model's material folders)."""
    name = name.replace("\\", "/")
    for cd in cdmaterials:
        cand = (cd.strip("/") + "/" + name) if cd.strip("/") else name
        if content.has_material(cand):
            return cand.lower()
    return name.lower()


def import_game_mesh(context, arm, body: int = 0):
    """The game character's own mesh (the body variant `body`) on its game-skeleton armature: skinned from the game's
    stored rest pose into the armature's standing one, weighted to its bones, with the game's materials. A Custom
    Model to edit, or to learn from."""
    import bmesh
    from ..core.gamemesh import load_game_mesh
    from ..core.gamemodel import _qrot, load_game_model, standing_rest
    from .ops import game_content, game_root
    content = game_content(game_root(context))
    path = arm.get("hl_game_model", "")
    gm = load_game_model(content, path) if content is not None else None
    mesh_data = load_game_mesh(content, path, body=body) if gm is not None else None
    if mesh_data is None:
        return None, "The armature's game model can't be read (set the Left 4 Dead 2 folder)"
    upm = context.scene.hammerless.units_per_meter
    standing, _src = standing_rest(content, gm)
    bind = [Matrix.LocRotScale(Vector(p), Quaternion((q[3], q[0], q[1], q[2])), None) for p, q in gm.bone_world()]
    stand = [Matrix.LocRotScale(Vector(p), Quaternion((q[3], q[0], q[1], q[2])), None)
             for p, q in gm.bone_world(standing)]
    skin = [st @ bi.inverted() for st, bi in zip(stand, bind)]   # stored rest -> standing, per bone (game units)
    positions, normals = [], []
    for co, n, links in zip(mesh_data.positions, mesh_data.normals, mesh_data.weights):
        v, nn = Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.0, 0.0))
        for b, w in links:
            v += w * (skin[b] @ Vector(co))
            nn += w * (skin[b].to_3x3() @ Vector(n))
        positions.append(v / upm)
        normals.append(nn.normalized())
    name = arm.name.replace(" Skeleton", "") + " Mesh"
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    verts = [bm.verts.new(p) for p in positions]
    uv_layer = bm.loops.layers.uv.new()
    mat_slots: dict[int, int] = {}
    for tri, mi in zip(mesh_data.triangles, mesh_data.material_index):
        try:
            f = bm.faces.new([verts[k] for k in tri])
        except ValueError:                   # (a triangle twice: skipped)
            continue
        if mi not in mat_slots:
            mat_slots[mi] = len(mat_slots)
        f.material_index = mat_slots[mi]
        f.smooth = True
        for loop, k in zip(f.loops, tri):
            u, v = mesh_data.uvs[k]
            loop[uv_layer].uv = (u, 1.0 - v)
    bm.to_mesh(me)
    bm.free()
    for mi in sorted(mat_slots, key=lambda m: mat_slots[m]):
        mpath = _material_path(content, mesh_data.materials[mi], mesh_data.cdmaterials)
        label = mpath.split("/")[-1]
        mat = bpy.data.materials.get(label) or bpy.data.materials.new(label)
        mat.hammerless.source_material = mpath
        me.materials.append(mat)
    obj = bpy.data.objects.new(name, me)
    (arm.users_collection[0] if arm.users_collection else context.scene.collection).objects.link(obj)
    obj.parent = arm
    groups = {}                              # (only bones something is weighted to get a group)
    for vi, links in enumerate(mesh_data.weights):
        for b, w in links:
            if b not in groups:
                groups[b] = obj.vertex_groups.new(name=gm.bones[b].name)
            groups[b].add([vi], w, "ADD")
    obj.modifiers.new("Armature", "ARMATURE").object = arm
    obj.hammerless.role = "MODEL"
    obj.hammerless.model_kind = "DYNAMIC"
    obj.hammerless.model_collision = "NONE"
    return obj, ""


class HL_OT_import_game_mesh(bpy.types.Operator):
    bl_idname = "hammerless.import_game_mesh"
    bl_label = "Import Game Mesh"
    bl_description = ("The game character's own mesh on the selected game skeleton (standing, weighted, with the game's "
                      "materials): a Custom Model to edit into your own")
    bl_options = {"REGISTER", "UNDO"}

    body: bpy.props.IntProperty(name="Body Variant", default=0, min=0,
                                description="Which of the character's body / head variants (0: the first of each)")

    @classmethod
    def poll(cls, context):
        a = context.active_object
        return a is not None and a.type == "ARMATURE" and bool(a.get("hl_game_model"))

    def execute(self, context):
        obj, why = import_game_mesh(context, context.active_object, self.body)
        if obj is None:
            self.report({"ERROR"}, why)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported '{obj.name}': {len(obj.data.vertices)} vertices")
        return {"FINISHED"}


class HL_OT_add_mannequin(bpy.types.Operator):
    bl_idname = "hammerless.add_mannequin"
    bl_label = "Add Mannequin"
    bl_description = ("A stand-in mesh on the selected game skeleton (a box per bone, weighted to it), a Custom Model: "
                      "try the game's animations before a real mesh is made")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        a = context.active_object
        return a is not None and a.type == "ARMATURE" and bool(a.get("hl_game_model"))

    def execute(self, context):
        arm = context.active_object
        coll = arm.users_collection[0] if arm.users_collection else context.scene.collection
        obj = make_mannequin(context, arm, arm.name.replace(" Skeleton", "") + " Mannequin", coll)
        self.report({"INFO"}, f"Added '{obj.name}' ({len(obj.vertex_groups)} bones weighted)")
        return {"FINISHED"}


CLASSES = (HL_OT_add_game_skeleton, HL_OT_load_game_animation, HL_OT_add_mannequin, HL_OT_import_game_mesh)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


def make_mannequin(context, arm, name: str, collection, thickness: float = 2.5):
    """A stand-in mesh on a game-skeleton armature: a box from each bone to its child, weighted to that bone (a stick
    figure to try the game's animations with before a real mesh is made). Returns the mesh object, a Custom Model."""
    import bmesh
    upm = context.scene.hammerless.units_per_meter
    bm = bmesh.new()
    groups = []                 # (bone name, vertex indices)
    for b in arm.data.bones:
        if b.name.lower().startswith(("valvebiped.weapon", "valvebiped.attachment", "valvebiped.hlp", "valvebiped.exp")):
            continue
        children = [c for c in b.children if not c.name.lower().startswith(("valvebiped.weapon", "valvebiped.attach",
                                                                              "valvebiped.hlp", "valvebiped.exp"))]
        if not children:
            continue
        a = b.head_local
        for c in children[:1] if len(children) == 1 else children:
            z = c.head_local
            d = z - a
            if d.length < 0.5 / upm:
                continue
            h = thickness / upm / 2
            side = d.normalized().orthogonal().normalized() * h
            up = d.normalized().cross(side).normalized() * h
            first = len(bm.verts)
            vs = [bm.verts.new(p) for p in (a + side + up, a - side + up, a - side - up, a + side - up,
                                              z + side + up, z - side + up, z - side - up, z + side - up)]
            for f in ((0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)):
                bm.faces.new([vs[k] for k in f])
            groups.append((b.name, list(range(first, first + 8))))
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.parent = arm
    for bone, verts in groups:
        g = obj.vertex_groups.get(bone) or obj.vertex_groups.new(name=bone)
        g.add(verts, 1.0, "REPLACE")
    obj.modifiers.new("Armature", "ARMATURE").object = arm
    from .examplemodels import flat_material
    mesh.materials.append(flat_material("Mannequin", (0.42, 0.36, 0.30)))
    obj.hammerless.role = "MODEL"
    obj.hammerless.model_kind = "DYNAMIC"
    obj.hammerless.model_collision = "NONE"
    return obj
