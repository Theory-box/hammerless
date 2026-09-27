"""Operators: add entities/presets, validate, export, compile, play."""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from mathutils import Vector

from ..core import compile as cc
from ..core.build import Report, build_vmf, validate
from ..core.ir import Entity
from ..core.entities import CATALOG, CATEGORIES, PRESET_BUILDERS, PRESETS, default_keyvalues, preview_model
from ..core.gamefiles import game_files
from ..core.nav import collect_climbs, collect_regions
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
        from ..core.surfaces import load_surfaces
        from .props import set_surface_list
        set_surface_list(load_surfaces(_content_cache[root]))
    return _content_cache[root]


def compile_options(s) -> "cc.CompileOptions | str":
    if s.compile_preset != "CUSTOM":
        return s.compile_preset
    return cc.CompileOptions(vis=s.vis_mode, rad=s.rad_mode, hdr=s.hdr_mode,
                             static_prop_lighting=s.static_prop_lighting,
                             extra_vbsp=s.extra_vbsp, extra_vvis=s.extra_vvis, extra_vrad=s.extra_vrad)


def launch_options(s) -> cc.LaunchOptions:
    try:
        monitor = int(s.window_monitor)
    except (TypeError, ValueError):
        monitor = -1
    return cc.LaunchOptions(width=s.window_width, height=s.window_height,
                            borderless=s.window_borderless, monitor_index=monitor, extra=s.launch_extra,
                            difficulty=s.difficulty)


def _quoted_object(message: str) -> str:
    """First 'Name' in a message that is an object in the scene (for selecting it)."""
    import re
    return next((q for q in re.findall(r"'([^']+)'", message) if bpy.data.objects.get(q)), "")


def needs_nav(context, root) -> bool:
    """Generate nav when asked to, when the map has no nav mesh yet (without one, bots
    can't move and zombies can't spawn), or when safe rooms / spawn areas changed."""
    s = context.scene.hammerless
    tools = cc.Tools(root)
    nav = os.path.join(tools.maps_dir, f"{s.map_name}.nav")
    return s.generate_nav or not os.path.exists(nav) or cc.nav_marks_changed(tools, s.map_name)


def launch(context, root, nav_written: bool = False) -> None:
    """nav_written: Hammerless just wrote the nav mesh; the game only analyzes it."""
    s = context.scene.hammerless
    generate = False if nav_written else needs_nav(context, root)
    cc.launch_game(cc.Tools(root), s.map_name, generate_nav=generate, window=launch_options(s),
                   analyze_nav=nav_written)


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
    import json
    with open(cc.sources_path(path), "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in rep2.solid_sources.items()}, f)
    rep2.info.append(f"Wrote {path}")
    if gamedir:
        files = game_files(ir)
        for rel, content in files.items():
            full = os.path.join(gamedir, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
        regions, _ = collect_regions(ir)
        rep2.nav_regions = regions
        rep2.nav_climbs = collect_climbs(ir)[0]
        rep2.info.append(f"Wrote {len(files)} script(s) to the game folder; nav marking covers "
                         f"{len(regions)} region(s)")
    return path, root, rep2


# ---------------------------------------------------------------- preview meshes

def preview_mesh(classname: str, scale: float, model: str = ""):
    """Shared wireframe box + forward arrow showing an entity's size and facing.
    With a model path, the box is the model's real bounds (read from the game)."""
    bounds = None
    if model:
        full = real_model_mesh(model, scale)
        if full is not None:
            return full
        name = f"HL_model_{model}"
        mesh = bpy.data.meshes.get(name)
        if mesh:
            return mesh
        bounds = model_bounds_from_game(model)
    if bounds is None:
        name = f"HL_preview_{classname}"
        mesh = bpy.data.meshes.get(name)
        if mesh:
            return mesh
        d = CATALOG.get(classname)
        bounds = d.preview_bounds() if d else ((-8, -8, -8), (8, 8, 8))
    return box_arrow_mesh(name, bounds, scale)


def real_model_mesh(model: str, scale: float):
    """The game model itself as a textured mesh, if model previews are on and it can be read."""
    ctx = bpy.context
    if not ctx.scene.hammerless.model_previews:
        return None
    root = game_root(ctx)
    content = game_content(root)
    if content is None:
        return None
    from .preview import model_mesh
    return model_mesh(model, content, os.path.join(root, "left4dead2"), scale)


def style_entity_object(obj) -> None:
    """Real models draw solid and textured; placeholder boxes draw as wireframes."""
    if obj.data is not None and obj.data.get("hl_model"):
        obj.display_type = "TEXTURED"
        obj.show_in_front = False
    else:
        obj.display_type = "WIRE"
        obj.show_in_front = True


def model_bounds_from_game(model: str):
    from ..core.vpk import model_bounds
    root = game_root(bpy.context)
    content = game_content(root)
    data = content.read(model) if content else None
    if not data:
        return None
    try:
        return model_bounds(data)
    except Exception:
        return None


def box_arrow_mesh(name, bounds, scale):
    (x0, y0, z0), (x1, y1, z1) = bounds
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
    model = preview_model(classname, {**default_keyvalues(classname), **(keyvalues or {})})
    obj = bpy.data.objects.new(name or classname, preview_mesh(classname, s.units_per_meter, model))
    style_entity_object(obj)
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


def box_mesh_object(context, name, mins, maxs, scale, collection, material_path=None, brush=None):
    """Box object. With `brush`, each face gets the material of the brush face pointing the same way."""
    x0, y0, z0 = (c / scale for c in mins)
    x1, y1, z1 = (c / scale for c in maxs)
    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    face_mats = sorted({f.material for f in brush.faces}) if brush else []
    if len(face_mats) > 1:
        from ..core.geometry import dot, polygon_normal
        for m in face_mats:
            obj.data.materials.append(game_material(m))
        normals = [(polygon_normal(f.verts), f.material) for f in brush.faces]
        for poly in mesh.polygons:
            best = max(normals, key=lambda nm: dot(nm[0], tuple(poly.normal)))
            poly.material_index = face_mats.index(best[1])
    elif material_path:
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
        if not path.startswith("tools/"):
            refresh_material_preview(mat)
    return mat


def refresh_material_preview(mat) -> bool:
    from .preview import apply_preview
    ctx = bpy.context
    root = game_root(ctx)
    content = game_content(root)
    if content is None:
        return False
    return apply_preview(mat, content, os.path.join(root, "left4dead2"), ctx.scene.hammerless.units_per_meter)


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
    next_map: StringProperty(name="Next Map", default="c1m2_streets",
                             description="End safe room only: the map to load when survivors close the door. "
                                         "Must be a different map, or zombies won't wander (no start-to-end path)")

    def draw(self, context):
        if self.preset in ("START_SAFE_ROOM", "END_SAFE_ROOM"):
            self.layout.prop(self, "landmark")
        if self.preset == "END_SAFE_ROOM":
            self.layout.prop(self, "next_map")

    def execute(self, context):
        s = context.scene.hammerless
        scale = s.units_per_meter
        builder = PRESET_BUILDERS[self.preset]
        if self.preset in ("START_SAFE_ROOM", "END_SAFE_ROOM"):
            # two rooms in one map can't share a landmark name (the dialog remembers the last one)
            used = {kv.value for o in bpy.data.objects for kv in o.hammerless.keyvalues
                    if o.hammerless.classname == "info_landmark" and kv.key == "targetname"}
            if self.landmark in used:
                n = 1
                while f"landmark_{n}" in used:
                    n += 1
                self.report({"INFO"}, f"Landmark '{self.landmark}' is already used here; using 'landmark_{n}'")
                self.landmark = f"landmark_{n}"
        if self.preset == "START_SAFE_ROOM":
            preset = builder(landmark=self.landmark)
        elif self.preset == "END_SAFE_ROOM":
            preset = builder(next_map=self.next_map, landmark=self.landmark)
        elif self.preset == "CRESCENDO_BUTTON":
            used = {kv.value for o in bpy.data.objects for kv in o.hammerless.keyvalues
                    if o.hammerless.classname == "hammerless_crescendo" and kv.key == "name"}
            n = 1
            while f"crescendo_{n}" in used:
                n += 1
            preset = builder(name=f"crescendo_{n}")
        else:
            preset = builder()
        from .presets import make_root, parent_to
        coll = context.collection
        base = context.scene.cursor.location.copy()
        root = make_root(context, self.preset, base, coll)
        parts = []
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
                                          e.brushes[0].faces[0].material, e.brushes[0])
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
            obj.hammerless.preset_part = part.name
            parts.append(obj)
        context.view_layer.update()
        for obj in parts:
            parent_to(obj, root)
        for o in context.selected_objects:
            o.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        self.report({"INFO"}, f"Added {preset.label}: select '{root.name}' to move it or change its settings")
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
        rep.problems = rep2.problems
        from .problems import store
        store(context, rep)
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


def _watch_load(before_launch: float, timing: str, nav: bool) -> None:
    """Once survivors are in the map, log how long Build & Play took; then show the game's
    start-to-end path report in the problem list (each map load sends one)."""
    from .problems import store_flow
    launch_id = cc.LOAD_STATUS["launch_id"]
    state = {"waited": 0.0, "logged": False, "flow_seq": cc.LOAD_STATUS["flow_seq"]}

    def check():
        if cc.LOAD_STATUS["launch_id"] != launch_id or state["waited"] > 300:
            return None
        state["waited"] += 1.0
        secs = cc.LOAD_STATUS["seconds"]
        if secs is not None and not state["logged"]:
            state["logged"] = True
            line = (f"Build & Play: {before_launch + secs:.0f}s total ({timing}, game load {secs:.1f}s"
                    + (", then nav mesh generation and two reloads" if nav else "") + ")")
            write_log([line], append=True)
            print("Hammerless:", line)
        if cc.LOAD_STATUS["flow_seq"] != state["flow_seq"] and cc.LOAD_STATUS["flow"]:
            state["flow_seq"] = cc.LOAD_STATUS["flow_seq"]
            report = cc.LOAD_STATUS["flow"]
            scene = bpy.context.scene
            scene.hammerless["ingame_flow"] = (
                f"In game: path from start to end works ({report['length']:.0f} units)"
                if report["state"] == "ok" else "In game: no path from start to end")
            store_flow(report)
            write_log([scene.hammerless["ingame_flow"]], append=True)
            try:        # the game saved its nav mesh by now: read it for the viewport and islands
                from . import navview
                navview.load(bpy.context)
                if navview.report() is not None:
                    from .problems import store_nav
                    store_nav(bpy.context, navview.mesh(), navview.report())
            except Exception as ex:
                print("Hammerless: couldn't read the nav mesh:", ex)
        return 1.0
    bpy.app.timers.register(check, first_interval=1.0)


def _start_nav_generation(vmf_path: str, regions, climbs=()) -> dict:
    """Run our copy of the game's nav generator on the VMF in a background thread."""
    import threading
    import time
    from ..core.navpredict import cached, predict
    with open(vmf_path, encoding="utf-8") as f:
        text = f.read()
    box = {"stage": "starting", "mesh": None, "error": None, "seconds": 0.0}
    reuse = cached(text, regions, climbs)  # Predict was pressed on this exact map: no waiting
    if reuse is not None:
        box["mesh"] = reuse
        box["thread"] = threading.Thread(target=lambda: None)
        box["thread"].start()
        box["thread"].join()
        return box

    def work():
        t0 = time.time()
        try:
            box["mesh"] = predict(text, regions, lambda stage, n: box.update(stage=stage.lower()), climbs)
        except Exception as ex:          # reported; the game makes the nav mesh instead
            box["error"] = str(ex)
        box["seconds"] = time.time() - t0
    box["thread"] = threading.Thread(target=work, daemon=True)
    box["thread"].start()
    return box


class HL_OT_build(bpy.types.Operator):
    bl_idname = "hammerless.build"
    bl_label = "Build"
    bl_description = "Export, compile and (optionally) launch Left 4 Dead 2 on the map"

    play: BoolProperty(name="Play", default=True)

    _timer = None
    _job: cc.CompileJob | None = None
    _root: str | None = None
    _t0 = 0.0
    _export_s = 0.0
    _nav: dict | None = None        # our nav generator running alongside the compile

    def execute(self, context):
        import time
        self._t0 = time.time()
        path, root, rep = export_vmf(self, context)
        self._export_s = time.time() - self._t0
        from .problems import store
        store(context, rep, new_build=True)
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
        self._job = cc.CompileJob(tools, path, compile_options(context.scene.hammerless),
                                  skip_if_unchanged=True)
        self._nav = None
        s = context.scene.hammerless
        if self.play and s.nav_source == "BLENDER" and (not self._job.up_to_date() or needs_nav(context, root)):
            self._nav = _start_nav_generation(path, rep.nav_regions, rep.nav_climbs)
        self._job.start()
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
            if summ and summ.errors and not summ.leaked:     # listed, so named brushes can be selected
                from .problems import store
                failed = Report()
                failed.errors = [f"Compiler: {e}" for e in summ.errors[:20]]
                store(context, failed)
            self.report({"ERROR"}, msg)
            return self._finish(context, {"CANCELLED"})
        import time
        s = context.scene.hammerless
        compiled = "Map unchanged, skipped compiling" if self._job.skipped else "Compiled"
        timing = f"Export {self._export_s:.1f}s, " + (", ".join(f"{n} {t:.1f}s" for n, t in self._job.timings)
                                                      or "compile skipped")
        write_log([timing], append=True)
        if self._job.lighting and not getattr(self, "_lighting_listed", False):
            from .problems import add_rows
            self._lighting_listed = True
            failed = "Bounce" in "".join(m for m, _l, _o in self._job.lighting) or any(
                "lighting failed" in m for m, _l, _o in self._job.lighting)
            add_rows(context, [("ERROR" if failed else "WARNING", m, o, loc) for m, loc, o in self._job.lighting])
            self.report({"WARNING"}, self._job.lighting[0][0][:200])
        if self.play and self._nav is not None:
            if self._nav["thread"].is_alive():
                context.workspace.status_text_set(f"Hammerless: building the nav mesh: {self._nav['stage']}...")
                return {"PASS_THROUGH"}
            if self._nav["mesh"] is None:
                self.report({"WARNING"}, f"Nav mesh couldn't be built in Blender ({self._nav['error']}); "
                                         "the game will make it")
                self._nav = None
            else:
                cc.write_generated_nav(cc.Tools(self._root), s.map_name, self._nav["mesh"])
                for problem in self._nav["mesh"].problems:
                    self.report({"WARNING"}, problem)
                if self._nav["mesh"].problems:
                    from .problems import add_rows
                    add_rows(context, [("WARNING", p, _quoted_object(p), None) for p in self._nav["mesh"].problems])
                if self._nav["mesh"].problems:
                    write_log(self._nav["mesh"].problems, append=True)
                s.generate_nav = False
                timing += f", nav mesh {self._nav['seconds']:.1f}s (during the compile)"
        if self.play:
            written = self._nav is not None
            nav = needs_nav(context, self._root) and not written
            nav_note = (" (the game adds its visibility data: one reload)" if written else
                        " (building its nav mesh first: the map reloads twice)" if nav else "")
            launch(context, self._root, nav_written=written)
            _watch_load(time.time() - self._t0, timing, nav)
            self.report({"INFO"}, f"{compiled}. Launching L4D2 on {s.map_name}{nav_note}  [{timing}]")
        else:
            self.report({"INFO"}, f"{compiled}  [{timing}]")
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
        launch(context, root)
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


_SKY_ITEMS: list[tuple[str, str, str]] = []


def _sky_items(self, context):
    root = game_root(context)
    content = game_content(root)
    _SKY_ITEMS.clear()
    if content:
        names = sorted({m[len("skybox/"):-2] for m in content.materials("skybox/") if m.endswith("bk")})
        for n in names:
            _SKY_ITEMS.append((n, n, "Skybox " + n))
    if not _SKY_ITEMS:
        _SKY_ITEMS.append(("sky_day01_09_hdr", "sky_day01_09_hdr", ""))
    return _SKY_ITEMS


class HL_OT_pick_sky(bpy.types.Operator):
    bl_idname = "hammerless.pick_sky"
    bl_label = "Pick Sky"
    bl_description = "Choose from every sky in Left 4 Dead 2"
    bl_property = "sky"

    sky: EnumProperty(name="Sky", items=_sky_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        context.scene.hammerless.skyname = self.sky
        return {"FINISHED"}


_MODEL_ITEMS: list[tuple[str, str, str]] = []
_MATERIAL_ITEMS: list[tuple[str, str, str]] = []


def _model_items(self, context):
    if not _MODEL_ITEMS:
        content = game_content(game_root(context))
        if content:
            for f in sorted(f for f in content.files if f.endswith(".mdl")):
                _MODEL_ITEMS.append((f, f[len("models/"):], f))
    return _MODEL_ITEMS or [("", "(Left 4 Dead 2 not found)", "")]


def _material_items(self, context):
    if not _MATERIAL_ITEMS:
        content = game_content(game_root(context))
        if content:
            for m in content.materials():
                _MATERIAL_ITEMS.append((m, m, m))
    return _MATERIAL_ITEMS or [("", "(Left 4 Dead 2 not found)", "")]


class HL_OT_pick_model(bpy.types.Operator):
    bl_idname = "hammerless.pick_model"
    bl_label = "Pick Game Model"
    bl_description = "Search all Left 4 Dead 2 models and use one for this entity"
    bl_property = "model"
    bl_options = {"REGISTER", "UNDO"}

    model: EnumProperty(name="Model", items=_model_items)

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.model:
            return {"CANCELLED"}
        s = context.scene.hammerless
        for obj in context.selected_objects or [context.object]:
            hs = obj.hammerless
            if not hs.classname:
                continue
            kv = next((x for x in hs.keyvalues if x.key == "model"), None) or hs.keyvalues.add()
            kv.key, kv.value = "model", self.model
            if obj.type == "MESH" and hs.role in ("ENTITY", "AUTO"):
                obj.data = preview_mesh(hs.classname, s.units_per_meter, self.model)
                style_entity_object(obj)
        self.report({"INFO"}, self.model)
        return {"FINISHED"}


class HL_OT_pick_material(bpy.types.Operator):
    bl_idname = "hammerless.pick_material"
    bl_label = "Pick Game Material"
    bl_description = "Search all Left 4 Dead 2 materials and use one on the active material slot"
    bl_property = "material"
    bl_options = {"REGISTER", "UNDO"}

    material: EnumProperty(name="Material", items=_material_items)

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.material:
            return {"CANCELLED"}
        mat = game_material(self.material)
        for obj in context.selected_objects or [context.object]:
            if obj.type != "MESH":
                continue
            if not obj.material_slots:
                obj.data.materials.append(mat)
            else:
                obj.material_slots[obj.active_material_index].material = mat
        self.report({"INFO"}, self.material)
        return {"FINISHED"}


class HL_OT_refresh_previews(bpy.types.Operator):
    bl_idname = "hammerless.refresh_previews"
    bl_label = "Refresh Previews"
    bl_description = ("Show real game textures on game materials (Material Preview view) and real "
                      "3D models on props and items")

    def execute(self, context):
        done = failed = 0
        for mat in bpy.data.materials:
            hs = mat.hammerless
            if not hs.source_material and "/" in mat.name and not mat.name.startswith("hammerless/"):
                hs.source_material = mat.name.lower()
            if not hs.source_material or hs.source_material.startswith("tools/"):
                continue
            if refresh_material_preview(mat):
                done += 1
            else:
                failed += 1
        models = 0
        s = context.scene.hammerless
        for obj in context.scene.objects:
            hs = obj.hammerless
            if obj.type != "MESH" or hs.role != "ENTITY" or not hs.classname:
                continue
            model = preview_model(hs.classname, {kv.key: kv.value for kv in hs.keyvalues})
            if not model:
                continue
            mesh = preview_mesh(hs.classname, s.units_per_meter, model)
            if mesh is not obj.data:
                obj.data = mesh
                style_entity_object(obj)
                models += 1
        self.report({"INFO"} if not failed else {"WARNING"},
                    f"Previewed {done} material(s), {models} model(s)"
                    + (f"; {failed} material(s) not found in the game" if failed else ""))
        return {"FINISHED"}


class HL_OT_load_game_data(bpy.types.Operator):
    bl_idname = "hammerless.load_game_data"
    bl_label = "Load Game Data"
    bl_description = "Read the game's surface list (friction etc.) and sky names"

    def execute(self, context):
        root = game_root(context)
        if not game_content(root):
            self.report({"ERROR"}, "Left 4 Dead 2 not found")
            return {"CANCELLED"}
        from .props import SURFACE_ITEMS
        self.report({"INFO"}, f"Loaded {len(SURFACE_ITEMS) - 1} surfaces")
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


CLASSES = (HL_OT_pick_sky, HL_OT_pick_model, HL_OT_pick_material, HL_OT_refresh_previews, HL_OT_load_game_data, HL_OT_add_entity, HL_OT_set_brush_entity, HL_OT_add_preset, HL_OT_validate,
           HL_OT_export_vmf, HL_OT_build, HL_OT_launch, HL_OT_load_leak,
           HL_OT_kv_add, HL_OT_kv_remove, HL_OT_output_add, HL_OT_output_remove, HL_OT_reset_keyvalues)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
