"""MapIR -> VMF text, with validation and automatic fixes (sealing, director, sun)."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import geometry as g
from .displacement import build_patches
from .entities import default_keyvalues
from .entities import PSEUDO_ENTITIES
from .gamefiles import collect_crescendos, script_path
from .ir import Entity, MapIR, Output, Vec3
from .nav import collect_regions
from .vmf import Block, VMFWriter

SURVIVOR_SPAWNS = {"info_player_start", "info_survivor_position"}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(ir: MapIR, content=None) -> Report:
    """content: optional vpk.GameContent to check materials/models exist."""
    r = Report()
    for b in ir.brushes + [b for e in ir.entities for b in e.brushes]:
        for p in g.check_brush(b):
            r.errors.append(f"Brush '{p.source}' {p.message}")

    classes = [e.classname for e in ir.entities]
    if not SURVIVOR_SPAWNS & set(classes):
        r.warnings.append("No survivor spawn (info_player_start / info_survivor_position): "
                          "players will spawn at the map origin.")
    if "info_player_start" not in classes and "info_survivor_position" in classes:
        r.info.append("No info_player_start. Adding one at the first survivor position.")
    if classes.count("info_director") > 1:
        r.errors.append("More than one info_director.")
    if "prop_door_rotating_checkpoint" not in classes:
        r.info.append("No safe room doors. Fine for testing, but a campaign map needs them.")
    names = [e.keyvalues.get("targetname") for e in ir.entities if e.classname == "info_landmark"]
    for n in sorted({n for n in names if names.count(n) > 1}):
        r.errors.append(f"Two info_landmark entities are both named '{n}'. The end safe room's landmark "
                        "must differ from the start safe room's (it pairs with the NEXT map's start room).")
    changelevels = [e for e in ir.entities if e.classname == "info_changelevel"]
    landmarks = {e.keyvalues.get("targetname") for e in ir.entities if e.classname == "info_landmark"}
    for cl in changelevels:
        if not cl.brushes:
            r.errors.append(f"info_changelevel '{cl.source}' has no brush volume.")
        if cl.keyvalues.get("landmark") and cl.keyvalues["landmark"] not in landmarks:
            r.warnings.append(f"info_changelevel refers to landmark '{cl.keyvalues['landmark']}' which doesn't exist.")
        if not cl.keyvalues.get("map"):
            r.warnings.append("info_changelevel has no 'map' set (next map).")

    names = {e.keyvalues.get("targetname") for e in ir.entities} | {"director"}  # director is auto-added
    for e in ir.entities:
        for o in e.outputs:
            if not o.target.startswith("!") and o.target not in names and o.target not in classes:
                r.warnings.append(f"'{e.source or e.classname}' output {o.output} targets '{o.target}', "
                                  "but no entity has that name.")

    regions, nav_problems = collect_regions(ir)
    r.errors += nav_problems
    ir.crescendos.clear()
    r.errors += collect_crescendos(ir)
    for e in ir.entities:
        for o in e.outputs:
            if o.input.lower() == "scriptedpanicevent":
                name = o.parameter.split("/")[-1].removeprefix("crescendo_")
                if name not in ir.crescendos:
                    r.warnings.append(f"'{e.source or e.classname}' starts crescendo '{o.parameter}', "
                                      "but no Crescendo Definition has that name.")
    if "info_changelevel" in classes and not any(x.bits & 128 for x in regions)             and "info_survivor_position" in classes:
        r.info.append("No PLAYER_START nav region. Survivors may not spawn in the start safe room. "
                      "The Start Safe Room preset includes one.")

    for t in ir.terrains:
        if t.power not in (2, 3, 4):
            r.errors.append(f"Terrain '{t.source}' power must be 2, 3 or 4.")

    if content is not None:
        mats = {f.material for b in ir.brushes for f in b.faces} | {t.material for t in ir.terrains}
        for m in sorted(mats):
            if not content.has_material(m):
                r.warnings.append(f"Material '{m}' not found in game files (will show as purple/black checkers).")
        for e in ir.entities:
            mdl = e.keyvalues.get("model", "")
            if mdl.endswith(".mdl") and not content.has_model(mdl):
                r.warnings.append(f"Model '{mdl}' on {e.classname} not found in game files.")

    all_pts = _all_points(ir)
    if all_pts:
        mins, maxs = g.bounds(all_pts)
        if min(mins) < -16000 or max(maxs) > 16000:
            r.errors.append("Map extends beyond ±16000 units. Check your scale setting.")
    return r


def _all_points(ir: MapIR) -> list[Vec3]:
    pts = [v for b in ir.brushes for f in b.faces for v in f.verts]
    pts += [v for e in ir.entities for b in e.brushes for f in b.faces for v in f.verts]
    pts += [e.origin for e in ir.entities if e.origin is not None]
    for t in ir.terrains:
        hs = [h for row in t.heights for h in row if h is not None]
        if hs:
            rows, cols = len(t.heights), len(t.heights[0])
            x1 = t.origin[0] + (cols - 1) * t.spacing
            y1 = t.origin[1] + (rows - 1) * t.spacing
            pts += [(t.origin[0], t.origin[1], min(hs) - 17), (x1, y1, max(hs))]
    return pts


def seal_brushes(ir: MapIR):
    """Six skybox brushes forming a hollow box around everything."""
    pts = _all_points(ir)
    if not pts:
        return []
    (x0, y0, z0), (x1, y1, z1) = g.bounds(pts)
    p, t = ir.settings.seal_padding, ir.settings.seal_thickness
    x0, y0, z0, x1, y1, z1 = x0 - p, y0 - p, z0 - p, x1 + p, y1 + p, z1 + p
    sky = "tools/toolsskybox"
    boxes = [
        ((x0 - t, y0 - t, z0 - t), (x1 + t, y1 + t, z0)),   # bottom
        ((x0 - t, y0 - t, z1), (x1 + t, y1 + t, z1 + t)),   # top
        ((x0 - t, y0, z0), (x0, y1, z1)),                   # -x
        ((x1, y0, z0), (x1 + t, y1, z1)),                   # +x
        ((x0 - t, y0 - t, z0), (x1 + t, y0, z1)),           # -y
        ((x0 - t, y1, z0), (x1 + t, y1 + t, z1)),           # +y
    ]
    return [g.box_brush(a, b, sky, "auto_seal") for a, b in boxes]


def build_vmf(ir: MapIR, content=None) -> tuple[str | None, Report]:
    report = validate(ir, content)
    if not report.ok:
        return None, report

    w = VMFWriter()
    s = ir.settings
    world = Block("world")
    world.kv("id", w.new_id()).kv("mapversion", 1).kv("classname", "worldspawn")
    world.kv("skyname", s.skyname).kv("detailmaterial", s.detail_material)
    world.kv("detailvbsp", s.detail_vbsp).kv("maxpropscreenwidth", -1)

    for b in ir.brushes:
        world.add(w.solid(b))
    n_patches = 0
    for t in ir.terrains:
        for brush, disp in build_patches(t):
            world.add(w.solid(brush, {0: disp}))
            n_patches += 1
    if s.auto_seal:
        for b in seal_brushes(ir):
            world.add(w.solid(b))
        report.info.append("Sealed the map in an automatic skybox shell.")

    entities = [e for e in ir.entities if e.classname not in PSEUDO_ENTITIES]
    classes = {e.classname for e in entities}
    if s.auto_director and "info_director" not in classes:
        entities.append(Entity("info_director", (0, 0, 0), (0, 0, 0), default_keyvalues("info_director")))
        report.info.append("Added info_director.")
    if s.auto_light_environment and "light_environment" not in classes:
        kv = {"_light": "{} {} {} {}".format(*s.sun_color, s.sun_brightness),
              "_ambient": "{} {} {} {}".format(*s.ambient_color, s.ambient_brightness),
              "pitch": f"{s.sun_pitch:g}"}
        entities.append(Entity("light_environment", (0, 0, 0), (s.sun_pitch, s.sun_yaw, 0), kv))
        report.info.append("No sun in the scene. Added one from the Lighting settings.")
    if s.fog_enabled and "env_fog_controller" not in classes:
        entities.append(Entity("env_fog_controller", (0, 0, 64), (0, 0, 0), {
            "targetname": "hammerless_fog", "fogenable": "1", "spawnflags": "1",
            "fogcolor": "{} {} {}".format(*s.fog_color), "fogcolor2": "{} {} {}".format(*s.fog_color),
            "fogstart": f"{s.fog_start:g}", "fogend": f"{s.fog_end:g}",
            "fogmaxdensity": f"{s.fog_max_density:g}", "farz": "-1"}))
    if s.debug_log:
        entities.append(Entity("logic_script", (0, 0, 32), (0, 0, 0), {
            "targetname": "hammerless_debug", "vscripts": script_path(f"debug_{s.name}"),
            "thinkfunction": "HL_Think"}))
        report.info.append("Debug log on: Director events and stats go to the console (HAMMERLESS_DEBUG).")
    if s.director_enabled:
        entities.append(Entity("logic_auto", (0, 0, 48), (0, 0, 0), {"spawnflags": "1"}, outputs=[
            Output("OnMapSpawn", "director", "BeginScript", script_path(f"director_{s.name}"), 1.0, 1)]))
    if "info_player_start" not in classes:
        first = next((e for e in entities if e.classname == "info_survivor_position"), None)
        origin = first.origin if first else (0.0, 0.0, 0.0)
        entities.append(Entity("info_player_start", origin, first.angles if first else (0, 0, 0), {}))

    ent_blocks = [w.entity(e, [w.solid(b) for b in e.brushes]) for e in entities]
    report.info.append(
        f"{len(ir.brushes)} brushes, {n_patches} terrain patches, {len(entities)} entities.")
    return w.document(world, ent_blocks), report
