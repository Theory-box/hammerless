"""Operators: add entities/presets, validate, export, compile, play."""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from mathutils import Vector

from ..core import compile as cc
from ..core.build import Report, build_vmf, validate
from ..core.ir import Entity
from ..core.entities import CATALOG, CATEGORIES, PRESET_BUILDERS, PRESETS, default_keyvalues
from ..core.nav import collect_regions, navmark_script
from ..core.vpk import GameContent
from .extract import extract_scene

LOG_TEXT = "hammerless_log"
_content_cache: dict[str, GameContent] = {}


# ---------------------------------------------------------------- helpers

def game_root(context) -> str | None:
    s = context.scene.hammerless
    root = bpy.path.abspath(s.game_root) if s.game_root else ""
    return cc.find_game_root([root.rstrip("\\/")] if root else None)


def game_content(root: str | None) -> GameContent | None:
    if not root:
        return None
    if root not in _content_cache:
        _content_cache[root] = GameContent(root)
    return _content_cache[root]


def work_dir(context) -> str:
    s = context.scene.hammerless
    d = bpy.path.abspath(s.output_dir or "//hammerless_build")
    if d.startswith("//") or not os.path.isabs(d):  # unsaved .blend
        d = os.path.join(bpy.app.tempdir or os.path.expanduser("~"), "hammerless_build")
    os.makedirs(d, exist_ok=True)
    return d


def write_log(lines: list[str], append: bool = False) -> None:
    txt = bpy.data.texts.get(LOG_TEXT) or bpy.data.texts.new(LOG_TEXT)
    if not append:
        txt.clear()
    txt.write("\n".join(lines) + "\n")


def report_lines(rep: Report) -> list[str]:
    out = [f"ERROR: {e}" for e in rep.errors]
    out += [f"WARNING: {w}" for w in rep.warnings]
    out += [f"info: {i}" for i in rep.info]
    return out


def surface_report(op, rep: Report) -> None:
    write_log(report_lines(rep))
    for e in rep.errors[:5]:
        op.report({"ERROR"}, e)
    if rep.errors:
        op.report({"ERROR"}, f"{len(rep.errors)} error(s). See the '{LOG_TEXT}' text block for details")
    elif rep.warnings:
        op.report({"WARNING"}, f"{len(rep.warnings)} warning(s). See the '{LOG_TEXT}' text block")


def export_vmf(op, context) -> tuple[str | None, str | None, Report]:
    """Extract + build + write. Returns (vmf_path, game_root, report)."""
    s = context.scene.hammerless
    root = game_root(context)
    rep = Report()
    gamedir = os.path.join(root, "left4dead2") if root else None
    ir, _mats = extract_scene(context, rep, gamedir)
    if rep.errors:
        return None, root, rep
    text, rep2 = build_vmf(ir, game_content(root) if s.check_game_content else None)
    rep2.errors[:0] = rep.errors
    rep2.warnings[:0] = rep.warnings
    if text is None:
        return None, root, rep2
    path = os.path.join(work_dir(context), f"{s.map_name}.vmf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    rep2.info.append(f"Wrote {path}")
    if gamedir:
        regions, _ = collect_regions(ir)
        nut = os.path.join(gamedir, "scripts", "vscripts", "hammerless", f"navmark_{s.map_name}.nut")
        os.makedirs(os.path.dirname(nut), exist_ok=True)
        with open(nut, "w", encoding="utf-8") as f:
            f.write(navmark_script(regions, s.map_name))
        rep2.info.append(f"Nav marking script: {len(regions)} region(s)")
    return path, root, rep2


# ---------------------------------------------------------------- preview meshes

def preview_mesh(classname: str, scale: float):
    """Shared wireframe box + forward arrow showing an entity's size and facing."""
    name = f"HL_preview_{classname}"
    mesh = bpy.data.meshes.get(name)
    if mesh:
        return mesh
    d = CATALOG.get(classname)
    (x0, y0, z0), (x1, y1, z1) = d.preview_bounds() if d else ((-8, -8, -8), (8, 8, 8))
    x0, y0, z0, x1, y1, z1 = (c / scale for c in (x0, y0, z0, x1, y1, z1))
    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    # forward (+X) arrow at mid height
    zm = (z0 + z1) / 2
    reach = max(x1, 16 / scale) + 12 / scale
    verts += [(x1, -6 / scale, zm), (reach, 0, zm), (x1, 6 / scale, zm)]
    faces.append((8, 9, 10))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    return mesh


def make_entity_object(context, classname: str, location, collection=None, rotation=(0, 0, 0),
                       keyvalues: dict | None = None, name: str | None = None):
    s = context.scene.hammerless
    obj = bpy.data.objects.new(name or classname, preview_mesh(classname, s.units_per_meter))
    obj.display_type = "WIRE"
    obj.show_in_front = True
    obj.hide_render = True
    obj.location = location
    obj.rotation_euler = rotation
    (collection or context.collection).objects.link(obj)
    obj.hammerless.role = "ENTITY"
    obj.hammerless.classname = classname  # fills default keyvalues
    for k, v in (keyvalues or {}).items():
        kv = next((x for x in obj.hammerless.keyvalues if x.key == k), None) or obj.hammerless.keyvalues.add()
        kv.key, kv.value = k, v
    return obj


def apply_entity_data(obj, ent) -> None:
    """Copy an IR entity's keyvalues and outputs onto a tagged Blender object."""
    for k, v in ent.keyvalues.items():
        kv = next((x for x in obj.hammerless.keyvalues if x.key == k), None) or obj.hammerless.keyvalues.add()
        kv.key, kv.value = k, v
    for o in ent.outputs:
        item = obj.hammerless.outputs.add()
        item.output, item.target, item.input = o.output, o.target, o.input
        item.parameter, item.delay, item.only_once = o.parameter, o.delay, o.times == 1


def box_mesh_object(context, name, mins, maxs, scale, collection, material_path=None):
    x0, y0, z0 = (c / scale for c in mins)
    x1, y1, z1 = (c / scale for c in maxs)
    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material_path:
        obj.data.materials.append(game_material(material_path))
    return obj


def game_material(path: str):
    """Blender material standing in for a game material (name = game path)."""
    mat = bpy.data.materials.get(path)
    if mat is None:
        mat = bpy.data.materials.new(path)
        mat.hammerless.source_material = path
        colors = {"tools/toolsnodraw": (0.9, 0.8, 0.1, 1), "tools/toolstrigger": (0.9, 0.5, 0.1, 0.4),
                  "tools/toolsskybox": (0.4, 0.7, 1.0, 1), "tools/toolsclip": (0.6, 0.2, 0.8, 0.5),
                  "tools/toolsplayerclip": (0.8, 0.2, 0.8, 0.5)}
        mat.diffuse_color = colors.get(path, (0.55, 0.55, 0.55, 1))
    return mat


# ---------------------------------------------------------------- operators

def _entity_enum(self, context):
    items = []
    for cat in CATEGORIES:
        for cls, d in sorted(CATALOG.items(), key=lambda kv: kv[1].label):
            if d.category == cat and not d.brush:
                items.append((cls, f"{d.label}", f"{cls}: {d.description}"))
    return items


def _brush_entity_enum(self, context):
    return [(cls, d.label, f"{cls}: {d.description}")
            for cls, d in sorted(CATALOG.items(), key=lambda kv: kv[1].label) if d.brush]


class HL_OT_add_entity(bpy.types.Operator):
    bl_idname = "hammerless.add_entity"
    bl_label = "Add L4D2 Entity"
    bl_description = "Add a point entity (spawn, item, weapon, infected...) at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "classname"

    classname: EnumProperty(name="Entity", items=_entity_enum)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        for o in context.selected_objects:
            o.select_set(False)
        d = CATALOG[self.classname]
        obj = make_entity_object(context, self.classname, context.scene.cursor.location.copy(),
                                 name=d.label)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class HL_OT_set_brush_entity(bpy.types.Operator):
    bl_idname = "hammerless.set_brush_entity"
    bl_label = "Make Brush Entity"
    bl_description = "Turn the selected meshes into brush entities (func_detail, triggers, blockers...)"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "classname"

    classname: EnumProperty(name="Entity", items=_brush_entity_enum)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        for o in context.selected_objects:
            if o.type == "MESH":
                o.hammerless.role = "BRUSH_ENTITY"
                o.hammerless.classname = self.classname
                if self.classname in ("info_changelevel", "trigger_once", "trigger_multiple",
                                      "env_player_blocker", "func_playerinfected_clip"):
                    o.display_type = "WIRE"
                    mat = "tools/toolstrigger" if "trigger" in self.classname or self.classname == "info_changelevel" \
                        else "tools/toolsplayerclip"
                    o.data.materials.clear()
                    o.data.materials.append(game_material(mat))
        return {"FINISHED"}


class HL_OT_add_preset(bpy.types.Operator):
    bl_idname = "hammerless.add_preset"
    bl_label = "Add Preset"
    bl_description = "Add a ready-made group (safe room with door, spawns, items...) at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=[(k, p.label, p.description) for k, p in PRESETS.items()])
    landmark: StringProperty(
        name="Landmark Name", default="landmark_1",
        description="Links this safe room to the neighbouring map. A map's END room and the NEXT "
                    "map's START room must use the same name")
    next_map: StringProperty(name="Next Map", default="",
                             description="End safe room only: the map to load when survivors close the door")

    def draw(self, context):
        if self.preset in ("START_SAFE_ROOM", "END_SAFE_ROOM"):
            self.layout.prop(self, "landmark")
        if self.preset == "END_SAFE_ROOM":
            self.layout.prop(self, "next_map")

    def execute(self, context):
        s = context.scene.hammerless
        scale = s.units_per_meter
        builder = PRESET_BUILDERS[self.preset]
        if self.preset == "START_SAFE_ROOM":
            preset = builder(landmark=self.landmark)
        elif self.preset == "END_SAFE_ROOM":
            preset = builder(next_map=self.next_map, landmark=self.landmark)
        else:
            preset = builder()
        coll = bpy.data.collections.new(preset.label)
        context.scene.collection.children.link(coll)
        base = context.scene.cursor.location.copy()
        for part in preset.parts:
            if part.brush is not None:
                pts = [v for f in part.brush.faces for v in f.verts]
                mins = tuple(min(p[i] for p in pts) for i in range(3))
                maxs = tuple(max(p[i] for p in pts) for i in range(3))
                obj = box_mesh_object(context, f"{preset.label} {part.name}", mins, maxs, scale, coll,
                                      part.brush.faces[0].material)
                obj.location += base
                obj.hammerless.role = "BRUSH"
            elif part.entity is not None:
                e = part.entity
                if e.brushes:
                    pts = [v for b in e.brushes for f in b.faces for v in f.verts]
                    mins = tuple(min(p[i] for p in pts) for i in range(3))
                    maxs = tuple(max(p[i] for p in pts) for i in range(3))
                    obj = box_mesh_object(context, f"{preset.label} {part.name}", mins, maxs, scale, coll,
                                          e.brushes[0].faces[0].material)
                    obj.location += base
                    obj.display_type = "WIRE"
                    obj.hammerless.role = "BRUSH_ENTITY"
                    obj.hammerless.classname = e.classname
                    apply_entity_data(obj, e)
                    if e.classname == "func_button":
                        obj.display_type = "TEXTURED"
                else:
                    import math
                    loc = base + Vector(e.origin) / scale
                    rot = (math.radians(e.angles[2]), math.radians(e.angles[0]), math.radians(e.angles[1]))
                    obj = make_entity_object(context, e.classname, loc, coll, rot, e.keyvalues,
                                             f"{preset.label} {part.name}")
                    apply_entity_data(obj, Entity(e.classname, outputs=e.outputs))
        self.report({"INFO"}, f"Added {preset.label}")
        return {"FINISHED"}


class HL_OT_validate(bpy.types.Operator):
    bl_idname = "hammerless.validate"
    bl_label = "Check for Problems"
    bl_description = "Check brushes, terrain and entities without compiling"

    def execute(self, context):
        s = context.scene.hammerless
        rep = Report()
        root = game_root(context)
        ir, _ = extract_scene(context, rep, None)
        rep2 = validate(ir, game_content(root) if s.check_game_content else None)
        rep.errors += rep2.errors; rep.warnings += rep2.warnings; rep.info += rep2.info
        surface_report(self, rep)
        if rep.ok and not rep.warnings:
            self.report({"INFO"}, "No problems found")
        return {"FINISHED"}


class HL_OT_export_vmf(bpy.types.Operator):
    bl_idname = "hammerless.export_vmf"
    bl_label = "Export VMF"
    bl_description = "Write the Hammer .vmf file (open it in Hammer to inspect or edit)"

    def execute(self, context):
        path, _root, rep = export_vmf(self, context)
        surface_report(self, rep)
        if path:
            self.report({"INFO"}, f"Exported {path}")
        return {"FINISHED"} if path else {"CANCELLED"}


class HL_OT_build(bpy.types.Operator):
    bl_idname = "hammerless.build"
    bl_label = "Build"
    bl_description = "Export, compile and (optionally) launch Left 4 Dead 2 on the map"

    play: BoolProperty(name="Play", default=True)

    _timer = None
    _job: cc.CompileJob | None = None
    _root: str | None = None

    def execute(self, context):
        path, root, rep = export_vmf(self, context)
        surface_report(self, rep)
        if not path:
            return {"CANCELLED"}
        if not root:
            self.report({"ERROR"}, "Left 4 Dead 2 not found. Set the L4D2 Folder in the Hammerless panel")
            return {"CANCELLED"}
        tools = cc.Tools(root)
        if tools.missing():
            self.report({"ERROR"}, "L4D2 Authoring Tools not installed (Steam > Library > Tools > "
                                   "Left 4 Dead 2 Authoring Tools). VMF was still exported")
            return {"CANCELLED"}
        write_log(report_lines(rep) + ["", "Compiling..."])
        self._root = root
        self._job = cc.CompileJob(tools, path, context.scene.hammerless.compile_preset).start()
        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({"INFO"}, "Compiling... (see the hammerless_log text block)")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            self.report({"WARNING"}, "Stopped watching the compile (it continues in the background)")
            return self._finish(context, {"CANCELLED"})
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        new = self._job.poll()
        if new:
            write_log(new, append=True)
            context.workspace.status_text_set(f"Hammerless: {new[-1][:120]}")
        if not self._job.done:
            return {"PASS_THROUGH"}
        summ = self._job.summary
        if self._job.failed:
            msg = "Map leaks! Use 'Load Leak' to see where." if summ and summ.leaked else \
                  (summ.errors[0] if summ and summ.errors else "Compile failed, see the log")
            self.report({"ERROR"}, msg)
            return self._finish(context, {"CANCELLED"})
        s = context.scene.hammerless
        if self.play:
            cc.launch_game(cc.Tools(self._root), s.map_name, generate_nav=s.generate_nav)
            self.report({"INFO"}, f"Compiled! Launching L4D2 on {s.map_name}")
        else:
            self.report({"INFO"}, "Compiled successfully")
        return self._finish(context, {"FINISHED"})

    def _finish(self, context, result):
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        return result


class HL_OT_launch(bpy.types.Operator):
    bl_idname = "hammerless.launch"
    bl_label = "Launch Game"
    bl_description = "Launch Left 4 Dead 2 on the last compiled map"

    def execute(self, context):
        root = game_root(context)
        if not root:
            self.report({"ERROR"}, "Left 4 Dead 2 not found")
            return {"CANCELLED"}
        s = context.scene.hammerless
        cc.launch_game(cc.Tools(root), s.map_name, generate_nav=s.generate_nav)
        return {"FINISHED"}


class HL_OT_load_leak(bpy.types.Operator):
    bl_idname = "hammerless.load_leak"
    bl_label = "Load Leak"
    bl_description = "Show the leak path from the last compile as a red line"

    def execute(self, context):
        s = context.scene.hammerless
        lin = os.path.join(work_dir(context), f"{s.map_name}.lin")
        if not os.path.exists(lin):
            self.report({"INFO"}, "No leak file. The last compile didn't leak")
            return {"CANCELLED"}
        pts = cc.read_pointfile(lin)
        curve = bpy.data.curves.new("HL_leak", "CURVE")
        curve.dimensions = "3D"
        curve.bevel_depth = 2 / s.units_per_meter
        spline = curve.splines.new("POLY")
        spline.points.add(len(pts) - 1)
        for p, (x, y, z) in zip(spline.points, pts):
            p.co = (x / s.units_per_meter, y / s.units_per_meter, z / s.units_per_meter, 1)
        old = bpy.data.objects.get("HL_leak")
        if old:
            bpy.data.objects.remove(old)
        obj = bpy.data.objects.new("HL_leak", curve)
        obj.hammerless.role = "IGNORE"
        obj.color = (1, 0, 0, 1)
        obj.show_in_front = True
        context.scene.collection.objects.link(obj)
        self.report({"INFO"}, "Leak line added (HL_leak). Follow it from inside the map to the hole")
        return {"FINISHED"}


class HL_OT_kv_add(bpy.types.Operator):
    bl_idname = "hammerless.kv_add"
    bl_label = "Add Keyvalue"
    bl_options = {"UNDO"}

    def execute(self, context):
        context.object.hammerless.keyvalues.add()
        return {"FINISHED"}


class HL_OT_kv_remove(bpy.types.Operator):
    bl_idname = "hammerless.kv_remove"
    bl_label = "Remove Keyvalue"
    bl_options = {"UNDO"}

    def execute(self, context):
        hs = context.object.hammerless
        if 0 <= hs.keyvalues_index < len(hs.keyvalues):
            hs.keyvalues.remove(hs.keyvalues_index)
        return {"FINISHED"}


class HL_OT_output_add(bpy.types.Operator):
    bl_idname = "hammerless.output_add"
    bl_label = "Add Output"
    bl_description = "Add an output: when this entity's event fires, tell another entity to do something"
    bl_options = {"UNDO"}

    def execute(self, context):
        hs = context.object.hammerless
        hs.outputs.add()
        hs.outputs_index = len(hs.outputs) - 1
        return {"FINISHED"}


class HL_OT_output_remove(bpy.types.Operator):
    bl_idname = "hammerless.output_remove"
    bl_label = "Remove Output"
    bl_options = {"UNDO"}

    def execute(self, context):
        hs = context.object.hammerless
        if 0 <= hs.outputs_index < len(hs.outputs):
            hs.outputs.remove(hs.outputs_index)
        return {"FINISHED"}


class HL_OT_reset_keyvalues(bpy.types.Operator):
    bl_idname = "hammerless.reset_keyvalues"
    bl_label = "Reset to Defaults"
    bl_options = {"UNDO"}

    def execute(self, context):
        hs = context.object.hammerless
        hs.keyvalues.clear()
        for k, v in default_keyvalues(hs.classname).items():
            kv = hs.keyvalues.add()
            kv.key, kv.value = k, v
        return {"FINISHED"}


CLASSES = (HL_OT_add_entity, HL_OT_set_brush_entity, HL_OT_add_preset, HL_OT_validate,
           HL_OT_export_vmf, HL_OT_build, HL_OT_launch, HL_OT_load_leak,
           HL_OT_kv_add, HL_OT_kv_remove, HL_OT_output_add, HL_OT_output_remove, HL_OT_reset_keyvalues)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
