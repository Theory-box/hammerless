"""Headless Blender tests.

Run:  blender --background --factory-startup --python tests/blender/run_tests.py
Exit code 0 = all passed. Builds fixture scenes from code (no .blend files).
"""
import math
import os
import sys
import tempfile
import traceback

import bpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

import hammerless  # noqa: E402
from hammerless.core import vmf  # noqa: E402
from hammerless.core.textures import read_vtf_header  # noqa: E402

hammerless.register()

TMP = tempfile.mkdtemp(prefix="hammerless_test_")
FAKE_GAME = os.path.join(TMP, "L4D2")
os.makedirs(os.path.join(FAKE_GAME, "left4dead2"))
open(os.path.join(FAKE_GAME, "left4dead2.exe"), "w").close()


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene.hammerless
    s.game_root = FAKE_GAME
    s.output_dir = os.path.join(TMP, "build")
    s.check_game_content = False
    s.map_name = "test_map"
    s.auto_detail = "OFF"      # tests count world brushes; auto detail has its own test
    return s


def add_box(name, size, loc, material=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = size
    if material:
        o.data.materials.append(material)
    return o


def export():
    try:
        result = bpy.ops.hammerless.export_vmf()
    except RuntimeError:  # operators called from Python raise on ERROR reports
        result = {"CANCELLED"}
    path = os.path.join(TMP, "build", "test_map.vmf")
    log = bpy.data.texts["hammerless_log"].as_string() if "hammerless_log" in bpy.data.texts else ""
    if result != {"FINISHED"}:
        return None, log
    with open(path, encoding="utf-8") as f:
        return vmf.parse(f.read()), log


def entities(blocks, classname=None):
    return [b for b in blocks if b.name == "entity" and (classname is None or b.get("classname") == classname)]


def world_solids(blocks):
    return next(b for b in blocks if b.name == "world").blocks("solid")


# ---------------------------------------------------------------- tests

def test_box_room_with_spawns():
    s = reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.context.scene.cursor.location = (0, 0, 0)
    for order in range(1, 5):
        bpy.ops.hammerless.add_entity(classname="info_survivor_position")
        o = bpy.context.object
        o.location = (order, 0, 0)
        next(kv for kv in o.hammerless.keyvalues if kv.key == "Order").value = str(order)
    blocks, log = export()
    assert blocks, log
    surv = entities(blocks, "info_survivor_position")
    assert len(surv) == 4, len(surv)
    assert sorted(e.get("Order") for e in surv) == ["1", "2", "3", "4"]
    # 1 m apart at 52.49 units/m
    xs = sorted(float(e.get("origin").split()[0]) for e in surv)
    assert abs(xs[1] - xs[0] - s.units_per_meter) < 0.01, xs
    assert len(world_solids(blocks)) == 1 + 6  # floor + auto seal
    for cls in ("info_director", "light_environment", "info_player_start"):
        assert entities(blocks, cls), cls


def test_rotated_and_scaled_brush_valid():
    reset_scene()
    o = add_box("ramp_block", (2, 1, 0.5), (0, 0, 0))
    o.rotation_euler = (0, math.radians(20), math.radians(33))
    blocks, log = export()
    assert blocks, log
    assert len(world_solids(blocks)) == 7


def test_nonconvex_rejected_then_hull():
    reset_scene()
    bpy.ops.mesh.primitive_torus_add(location=(0, 0, 2))
    torus = bpy.context.object
    blocks, log = export()
    assert blocks is None and "not convex" in log, log
    torus.hammerless.use_convex_hull = True
    blocks, log = export()
    assert blocks, log


def test_multiple_loose_parts_become_multiple_brushes():
    reset_scene()
    a = add_box("a", (1, 1, 1), (0, 0, 0))
    b = add_box("b", (1, 1, 1), (3, 0, 0))
    bpy.ops.object.select_all(action="DESELECT")
    a.select_set(True); b.select_set(True)
    bpy.context.view_layer.objects.active = a
    bpy.ops.object.join()
    blocks, log = export()
    assert blocks, log
    assert len(world_solids(blocks)) == 2 + 6


def test_terrain_displacements():
    reset_scene()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=40, y_subdivisions=40, size=20, location=(0, 0, 0))
    t = bpy.context.object
    for v in t.data.vertices:
        v.co.z = math.sin(v.co.x * 0.5) * 1.5 + math.cos(v.co.y * 0.3)
    t.hammerless.role = "TERRAIN"
    t.hammerless.terrain_power = "3"
    t.hammerless.terrain_patch_size = 512
    blocks, log = export()
    assert blocks, log
    disp_sides = [s for sol in world_solids(blocks) for s in sol.blocks("side") if s.blocks("dispinfo")]
    # 20 m * 52.49 = ~1050 units -> 3x3 patches of 512
    assert len(disp_sides) == 9, len(disp_sides)
    d = disp_sides[0].blocks("dispinfo")[0]
    assert d.get("power") == "3"
    assert len(d.blocks("distances")[0].get("row0").split()) == 9


def test_terrain_edge_not_sunk():
    """The terrain grid spans the terrain exactly, so every edge sample is on the terrain. (Its corner
    was rounded to whole units and its cells were fixed: edge samples off the mesh sank 512 units as
    'holes', making a sloped, unwalkable strip along the edges that cut off safe room doors.)"""
    reset_scene()
    from hammerless.blender.extract import MaterialResolver, mesh_to_terrain
    from hammerless.blender.logic import _Quiet
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=60, y_subdivisions=60, size=24, location=(1.217, -0.683, 0))
    t = bpy.context.object
    t.scale = (1.0, 1.37, 1.0)                     # not a whole number of cells either way
    t.hammerless.role = "TERRAIN"
    t.hammerless.terrain_power = "3"
    s = bpy.context.scene.hammerless
    ter = mesh_to_terrain(t, bpy.context.evaluated_depsgraph_get(), s.units_per_meter, MaterialResolver(s, None, _Quiet()))
    sunk = [(r, c) for r, row in enumerate(ter.heights) for c, h in enumerate(row) if h is None]
    assert not sunk, sunk[:10]
    upm = s.units_per_meter
    xs = [(t.matrix_world @ v.co).x * upm for v in t.data.vertices]
    ys = [(t.matrix_world @ v.co).y * upm for v in t.data.vertices]
    x0, y0 = ter.origin
    far_x = x0 + (len(ter.heights[0]) - 1) * ter.spacing
    far_y = y0 + (len(ter.heights) - 1) * ter.sy
    assert abs(x0 - min(xs)) < 1e-3 and abs(far_x - max(xs)) < 1e-3, (x0, far_x, min(xs), max(xs))
    assert abs(y0 - min(ys)) < 1e-3 and abs(far_y - max(ys)) < 1e-3, (y0, far_y, min(ys), max(ys))


def test_setup_warning():
    """Build & Play warns when L4D2 isn't found or the Authoring Tools are missing (and only then)."""
    reset_scene()
    from hammerless.blender import ops, ui
    from hammerless.core import compile as cc
    ui._setup_cache["key"] = None
    found = ops.game_root(bpy.context)
    real_root, real_missing = ops.game_root, cc.Tools.missing
    try:
        if found:
            assert ui.setup_problem(bpy.context) is None or "Authoring" in ui.setup_problem(bpy.context)[0]
        ops.game_root = lambda context: None
        ui._setup_cache["key"] = None
        assert ui.setup_problem(bpy.context)[0] == "Left 4 Dead 2 wasn't found"
        ops.game_root = lambda context: found or "C:/nowhere"
        cc.Tools.missing = lambda self: ["vbsp"]
        ui._setup_cache["key"] = None
        assert "Authoring Tools" in ui.setup_problem(bpy.context)[0]
    finally:
        ops.game_root, cc.Tools.missing = real_root, real_missing
        ui._setup_cache["key"] = None


def test_collection_role_terrain():
    reset_scene()
    coll = bpy.data.collections.new("Ground")
    bpy.context.scene.collection.children.link(coll)
    coll.hammerless.role = "TERRAIN"
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=8)
    g = bpy.context.object
    for c in g.users_collection:
        c.objects.unlink(g)
    coll.objects.link(g)
    blocks, log = export()
    assert blocks, log
    assert any(s.blocks("dispinfo") for sol in world_solids(blocks) for s in sol.blocks("side"))


def test_presets():
    reset_scene()
    bpy.context.scene.cursor.location = (0, 0, 0)
    bpy.ops.hammerless.add_preset(preset="START_SAFE_ROOM", landmark="landmark_0")
    bpy.context.scene.cursor.location = (30, 0, 0)
    bpy.ops.hammerless.add_preset(preset="END_SAFE_ROOM", landmark="landmark_1", next_map="c_next")
    blocks, log = export()
    assert blocks, log
    doors = entities(blocks, "prop_door_rotating_checkpoint")
    assert len(doors) == 2
    assert sorted(d.get("spawnpos") for d in doors) == ["0", "1"]
    cl = entities(blocks, "info_changelevel")
    assert len(cl) == 1 and cl[0].blocks("solid"), "changelevel needs a brush"
    assert cl[0].get("landmark") == "landmark_1" and cl[0].get("map") == "c_next"
    assert sorted(e.get("targetname") for e in entities(blocks, "info_landmark")) == ["landmark_0", "landmark_1"]
    assert len(entities(blocks, "info_survivor_position")) == 4
    assert len(entities(blocks, "weapon_first_aid_kit_spawn")) == 4
    # 8 wall/floor/ceiling brushes per room + seal
    assert len(world_solids(blocks)) == 16 + 6


def test_preset_parent_and_linked_settings():
    reset_scene()
    bpy.ops.hammerless.add_preset(preset="START_SAFE_ROOM", landmark="landmark_1")
    bpy.context.scene.cursor.location = (30, 0, 0)
    bpy.ops.hammerless.add_preset(preset="END_SAFE_ROOM")   # dialog default landmark_1 is taken
    from hammerless.blender.presets import PRESET_FIELDS, resolve
    root = bpy.context.object
    assert root.type == "EMPTY" and root.hammerless.preset == "END_SAFE_ROOM", root
    assert all(c.parent == root for c in root.children) and len(root.children) > 10
    (next_map, lm) = [resolve(root, f.targets[0]) for f in PRESET_FIELDS["END_SAFE_ROOM"]]
    assert getattr(*lm) == "landmark_2", getattr(*lm)
    setattr(lm[0], lm[1], "to_c2")            # editing the landmark updates the changelevel too
    setattr(next_map[0], next_map[1], "c2m1_highway")
    root.location.x += 5                      # moving the Empty moves the room
    blocks, log = export()
    assert blocks, log
    cl = entities(blocks, "info_changelevel")[0]
    assert cl.get("landmark") == "to_c2" and cl.get("map") == "c2m1_highway", (cl.get("landmark"), cl.get("map"))
    lms = {e.get("targetname"): e for e in entities(blocks, "info_landmark")}
    assert set(lms) == {"landmark_1", "to_c2"}
    assert float(lms["to_c2"].get("origin").split()[0]) > 34 * 64 * 0.9, lms["to_c2"].get("origin")
    assert "Renamed" not in log, log


def test_group_old_preset():
    reset_scene()
    bpy.ops.hammerless.add_preset(preset="END_SAFE_ROOM", landmark="lm_end")
    root = bpy.context.object
    # make it look like a scene made before parent Empties: its own collection, no parent
    coll = bpy.data.collections.new("End Safe Room")
    bpy.context.scene.collection.children.link(coll)
    for c in list(root.children):
        w = c.matrix_world.copy()
        c.parent = None
        c.matrix_world = w
        c.hammerless.preset_part = ""
        for uc in list(c.users_collection):
            uc.objects.unlink(c)
        coll.objects.link(c)
    bpy.data.objects.remove(root)
    part = coll.objects["End Safe Room door"]
    bpy.context.view_layer.objects.active = part
    bpy.ops.hammerless.group_preset()
    root = bpy.context.object
    assert root.hammerless.preset == "END_SAFE_ROOM" and len(root.children) == len(coll.objects) - 1
    assert coll.objects["End Safe Room changelevel"].hammerless.preset_part == "changelevel"
    blocks, log = export()
    assert blocks, log


def test_add_panel_items():
    from hammerless.core.spawnlist import SPAWN_ITEMS
    reset_scene()
    add_box("floor", (40, 40, 0.5), (0, 0, -0.25))
    before = set(bpy.data.objects)
    for it in SPAWN_ITEMS:                       # every item in the Add panel can be added
        assert bpy.ops.hammerless.spawn_item(item=it.id) == {"FINISHED"}, it.id
        new = set(bpy.data.objects) - before
        assert new, it.id
        before |= new
    s = bpy.context.scene.hammerless
    bpy.ops.hammerless.spawn_favorite(item="entity:weapon_smg_spawn")
    bpy.ops.hammerless.spawn_favorite(item="preset:LADDER")
    bpy.ops.hammerless.spawn_favorite(item="entity:weapon_smg_spawn")
    assert s.spawn_favorites == "preset:LADDER", s.spawn_favorites
    s.spawn_search = "medkit"
    s.spawn_category = "WEAPONS"
    assert s.spawn_search == "", "picking a category clears the search"
    # a volume turns the selected mesh into that entity
    wall = add_box("wall", (4, 0.2, 3), (0, 10, 1.5))
    for o in bpy.context.selected_objects:
        o.select_set(False)
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall
    bpy.ops.hammerless.spawn_item(item="entity:env_player_blocker", use_selection=True)
    assert wall.hammerless.classname == "env_player_blocker"


def test_map_check_problem_list():
    s = reset_scene()
    add_box("ground_a", (40, 20, 1), (0, 0, -0.5))
    add_box("ground_b", (20, 20, 1), (42, 0, -0.5))          # 12 m gap between the grounds
    bpy.context.scene.cursor.location = (-15, -2, 0)
    bpy.ops.hammerless.add_preset(preset="START_SAFE_ROOM")
    bpy.context.scene.cursor.location = (45, -2, 0)
    bpy.ops.hammerless.add_preset(preset="END_SAFE_ROOM")
    bpy.context.scene.cursor.location = (0, 0, -0.3)
    bpy.ops.hammerless.add_entity(classname="weapon_first_aid_kit_spawn")
    bpy.context.object.name = "Buried Medkit"
    bpy.ops.hammerless.validate()
    msgs = {p.name: p for p in s.problems}
    sunk = next(p for m, p in msgs.items() if "Buried Medkit" in m)
    assert sunk.source == "Buried Medkit"
    s.problem_index = list(msgs).index(sunk.name)               # clicking selects the object
    assert bpy.context.view_layer.objects.active.name == "Buried Medkit"
    # the predicted nav mesh finds the gap (the operator runs it in a thread; call it directly here)
    from hammerless.blender.extract import extract_scene
    from hammerless.blender.navview import finish_prediction
    from hammerless.core.build import Report, build_vmf
    from hammerless.core.nav import collect_regions
    from hammerless.core.navpredict import predict
    ir, _ = extract_scene(bpy.context, Report(), None)
    text, _rep = build_vmf(ir)
    finish_prediction(bpy.context, predict(text, collect_regions(ir)[0]))
    path = next(p for p in s.problems if p.ingame and p.severity == "ERROR")
    assert "built from the scene" in path.name and path.has_location
    assert 17 <= path.location.x <= 21, tuple(path.location)   # break at the edge of ground_a
    # the buttons turn into Clear buttons; clearing the analysis keeps the navmesh, Clear Navmesh removes it
    from hammerless.blender import navview
    from hammerless.core import navpredict
    from hammerless.core.navfile import HidingSpot
    assert navview.built_shown() and not navview.analysis_shown()
    navview.mesh().areas[0].hiding_spots = [HidingSpot(0, (0.0, 0.0, 0.0), 1)]
    assert navview.analysis_shown()
    assert bpy.ops.hammerless.nav_clear_analysis() == {"FINISHED"}
    assert navview.built_shown() and not navview.analysis_shown()
    assert bpy.ops.hammerless.nav_clear() == {"FINISHED"}
    assert not navview.built_shown() and navview.mesh() is None and navpredict._last["mesh"] is None


def test_auto_detail_default():
    s = reset_scene()
    s.auto_detail = "SMART"
    add_box("floor", (20, 20, 1), (0, 0, -0.5))
    add_box("crate", (1, 1, 1), (2, 2, 0.5))
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=1, depth=4, location=(-4, 0, 2))
    blocks, log = export()
    assert blocks, log
    detail = entities(blocks, "func_detail")
    assert len(detail) == 1 and len(detail[0].blocks("solid")) == 2, log   # crate + cylinder
    assert len(world_solids(blocks)) == 1 + 6                              # floor + seal


def test_detail_choice():
    from hammerless.blender.ui import _detail_note
    s = reset_scene()
    s.auto_detail = "SMART"
    add_box("floor", (20, 20, 1), (0, 0, -0.5))
    big = add_box("big_box", (8, 8, 8), (0, 0, 4))                 # 420 units: the map rule keeps it world
    crate = add_box("crate", (1, 1, 1), (6, 6, 0.5))               # small: the map rule makes it detail
    assert _detail_note(bpy.context, big) == "Map setting: Detail if round or small"
    big.hammerless.brush_detail = "DETAIL"
    crate.hammerless.brush_detail = "WORLD"
    blocks, log = export()
    assert blocks, log
    detail = entities(blocks, "func_detail")
    assert len(detail) == 1 and len(detail[0].blocks("solid")) == 1, log    # just the big box
    assert len(world_solids(blocks)) == 2 + 6, log                          # floor + crate + seal
    # a collection's choice applies to the brushes inside unless they choose for themselves
    big.hammerless.brush_detail = crate.hammerless.brush_detail = "AUTO"
    coll = bpy.data.collections.new("Detail Stuff")
    bpy.context.scene.collection.children.link(coll)
    inner = bpy.data.collections.new("Inner")
    coll.children.link(inner)
    for o in (big, crate):
        for c in list(o.users_collection):
            c.objects.unlink(o)
        inner.objects.link(o)
    coll.hammerless.brush_detail = "DETAIL"
    crate.hammerless.brush_detail = "WORLD"
    assert _detail_note(bpy.context, big) == "From its collection: Detail"
    blocks, log = export()
    detail = entities(blocks, "func_detail")
    assert len(detail) == 1 and len(detail[0].blocks("solid")) == 1, log
    assert len(world_solids(blocks)) == 2 + 6, log


def test_game_nav_view():
    from hammerless.core.navfile import NavArea, NavMesh, write_nav
    from hammerless.blender import navview
    s = reset_scene()
    m = NavMesh(analyzed=True)
    for i in range(1, 7):                        # 1-3 connected with the start, 4-6 cut off
        m.areas.append(NavArea(i, 0, (i * 100.0, 0, 0), (i * 100.0 + 100, 100, 0), 0, 0))
    for a in m.areas:
        if a.id not in (3, 6):
            a.connections[1].append(a.id + 1)
        if a.id not in (1, 4):
            a.connections[3].append(a.id - 1)
    m.areas[0].spawn_attributes = 0x880
    m.areas[5].spawn_attributes = 0x800
    maps = os.path.join(FAKE_GAME, "left4dead2", "maps")
    os.makedirs(maps, exist_ok=True)
    with open(os.path.join(maps, "test_map.nav"), "wb") as f:
        f.write(write_nav(m))
    assert bpy.ops.hammerless.nav_load() == {"FINISHED"}
    rep = navview.report()
    assert rep and not rep.end_reached and rep.break_area == 3
    broken = [p for p in s.problems if p.ingame and p.severity == "ERROR"]
    assert broken and broken[0].has_location
    assert abs(broken[0].location.x - 400 / s.units_per_meter) < 0.1, tuple(broken[0].location)   # edge nearest the end


def test_horde_presets_and_outputs():
    reset_scene()
    add_box("floor", (20, 20, 0.5), (0, 0, -0.25))
    for key, loc in (("HORDE_TRIGGER", (0, 0, 0)), ("HORDE_BUTTON", (4, 0, 0)),
                     ("TANK_AMBUSH", (-6, 4, 0)), ("ZOMBIE_SPAWN_AREA", (5, 5, 0))):
        bpy.context.scene.cursor.location = loc
        bpy.ops.hammerless.add_preset(preset=key)
    blocks, log = export()
    assert blocks, log
    trig = [e for e in entities(blocks, "trigger_once") if e.blocks("connections")]
    assert len(trig) == 2, len(trig)
    btn = entities(blocks, "func_button")
    assert btn and btn[0].blocks("connections")[0].get("OnPressed") == "director,ForcePanicEvent,,0,1"
    assert entities(blocks, "commentary_zombie_spawner")
    assert not entities(blocks, "hammerless_nav_region")  # nav regions never reach the map
    assert "targets" not in log, log


def test_scene_settings_reach_map():
    s = reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    s.fog_enabled = True
    s.fog_end = 2000
    s.director_enabled = True
    s.dir_common_limit = 12
    s.debug_log = True
    s.sun_brightness = 777
    s.lightmap_scale = 32
    blocks, log = export()
    assert blocks, log
    assert entities(blocks, "env_fog_controller")[0].get("fogend") == "2000"
    assert entities(blocks, "light_environment")[0].get("_light").endswith(" 777")
    assert entities(blocks, "logic_script")
    lm = {sd.get("lightmapscale") for sol in world_solids(blocks)[:1] for sd in sol.blocks("side")}
    assert lm == {"32"}, lm
    nut = os.path.join(FAKE_GAME, "left4dead2", "scripts", "vscripts", "hammerless_test_map_director.nut")
    assert "CommonLimit = 12" in open(nut).read()


def test_crescendo_names_unique():
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.hammerless.add_preset(preset="CRESCENDO_BUTTON")
    bpy.context.scene.cursor.location = (3, 0, 0)
    bpy.ops.hammerless.add_preset(preset="CRESCENDO_BUTTON")
    blocks, log = export()
    assert blocks, log
    params = sorted(b.blocks("connections")[0].get("OnPressed") for b in entities(blocks, "func_button"))
    assert params == ["director,ScriptedPanicEvent,hammerless_test_map_crescendo_1,0,1",
                      "director,ScriptedPanicEvent,hammerless_test_map_crescendo_2,0,1"], params
    assert "crescendo" not in log.lower() or "WARNING" not in log, log


def test_surface_override_patch_material():
    reset_scene()
    mat = bpy.data.materials.new("concrete/concrete_floor_01")
    mat.hammerless.surface = "ice"
    add_box("ice floor", (4, 4, 0.5), (0, 0, 0), mat)
    blocks, log = export()
    assert blocks, log
    mats = {sd.get("material") for sol in world_solids(blocks) for sd in sol.blocks("side")}
    assert "HAMMERLESS/TEST_MAP/PATCH_CONCRETE_CONCRETE_FLOOR_01_ICE" in mats, mats
    vmt = os.path.join(FAKE_GAME, "left4dead2", "materials", "hammerless", "test_map",
                       "patch_concrete_concrete_floor_01_ice.vmt")
    assert '"$surfaceprop" "ice"' in open(vmt).read()


def test_custom_compile_options():
    from hammerless.blender.ops import compile_options, launch_options
    s = reset_scene()
    assert compile_options(s) == "NORMAL"
    s.compile_preset = "CUSTOM"
    s.vis_mode, s.rad_mode, s.hdr_mode = "FAST", "SKIP", "LDR"
    o = compile_options(s)
    assert o.vvis_args() == ["-fast"] and o.vrad_args() is None
    s.window_width, s.window_height = 1280, 720
    lo = launch_options(s)
    assert (lo.width, lo.height) == (1280, 720)


def test_props_and_doors():
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    for cls in ("prop_static", "prop_physics", "prop_dynamic", "prop_door_rotating"):
        bpy.ops.hammerless.add_entity(classname=cls)
    blocks, log = export()
    assert blocks, log
    assert entities(blocks, "prop_static")[0].get("model") == "models/props_junk/dumpster.mdl"
    assert entities(blocks, "prop_physics")[0].get("model") == "models/props_c17/oildrum001.mdl"
    door = entities(blocks, "prop_door_rotating")[0]
    assert door.get("spawnflags") == "8192" and door.get("model") == "models/props_doors/doormain01.mdl"


def test_nav_generated_when_missing():
    from hammerless.blender.ops import needs_nav
    s = reset_scene()
    s.generate_nav = False
    maps = os.path.join(FAKE_GAME, "left4dead2", "maps")
    os.makedirs(maps, exist_ok=True)
    nav = os.path.join(maps, "test_map.nav")
    if os.path.exists(nav):
        os.remove(nav)
    assert needs_nav(bpy.context, FAKE_GAME)          # no nav yet -> generate anyway
    open(nav, "w").close()
    marks = os.path.join(FAKE_GAME, "left4dead2", "scripts", "vscripts", "hammerless")
    os.makedirs(marks, exist_ok=True)
    open(os.path.join(marks, "navmark_test_map.nut"), "w").write("marks v1")
    assert needs_nav(bpy.context, FAKE_GAME)          # marks never used for this nav -> rebuild
    open(os.path.join(marks, "navmark_test_map.used"), "w").write("marks v1")
    assert needs_nav(bpy.context, FAKE_GAME)          # nobody recorded who made this nav -> rebuild
    open(os.path.join(marks, "navmaker_test_map.txt"), "w").write("blender")
    assert not needs_nav(bpy.context, FAKE_GAME)      # nav exists, marks same, toggle off -> skip
    s.nav_source = "GAME"
    assert needs_nav(bpy.context, FAKE_GAME)          # switched to the game's nav -> regenerate
    assert needs_nav(bpy.context, FAKE_GAME, by_game=True)
    open(os.path.join(marks, "navmaker_test_map.txt"), "w").write("game")
    assert needs_nav(bpy.context, FAKE_GAME)          # the game was asked but never saved a nav
    later = os.path.getmtime(os.path.join(marks, "navmaker_test_map.txt")) + 5
    os.utime(nav, (later, later))                     # ...now it has
    assert not needs_nav(bpy.context, FAKE_GAME)
    s.nav_source = "BLENDER"
    assert needs_nav(bpy.context, FAKE_GAME)          # back to ours: Build makes it
    assert not needs_nav(bpy.context, FAKE_GAME, by_game=True)   # Launch doesn't make the game replace it
    open(os.path.join(marks, "navmaker_test_map.txt"), "w").write("blender")
    open(os.path.join(marks, "navmark_test_map.nut"), "w").write("marks v2")
    assert needs_nav(bpy.context, FAKE_GAME)          # safe rooms moved -> rebuild
    open(os.path.join(marks, "navmark_test_map.used"), "w").write("marks v2")
    s.generate_nav = True
    assert needs_nav(bpy.context, FAKE_GAME)


def test_brush_entity_func_detail():
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    crate = add_box("crate", (1, 1, 1), (0, 0, 0.5))
    bpy.ops.object.select_all(action="DESELECT")
    crate.select_set(True)
    bpy.context.view_layer.objects.active = crate
    bpy.ops.hammerless.set_brush_entity(classname="func_detail")
    blocks, log = export()
    assert blocks, log
    fd = entities(blocks, "func_detail")
    assert len(fd) == 1 and len(fd[0].blocks("solid")) == 1


def test_custom_texture_material():
    reset_scene()
    img = bpy.data.images.new("brick_test", 300, 200)  # not power of two -> resized to 256x128
    img.generated_type = "COLOR_GRID"
    mat = bpy.data.materials.new("My Brick")
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    # through a Hue/Saturation node, with an unrelated (normal) map in the material: the base colour wins
    hue = mat.node_tree.nodes.new("ShaderNodeHueSaturation")
    mat.node_tree.links.new(tex.outputs["Color"], hue.inputs["Color"])
    mat.node_tree.links.new(hue.outputs["Color"], bsdf.inputs["Base Color"])
    normal = mat.node_tree.nodes.new("ShaderNodeTexImage")
    normal.image = bpy.data.images.new("normal_test", 64, 64)
    mat.node_tree.nodes.move(normal, 0) if hasattr(mat.node_tree.nodes, "move") else None
    add_box("wall", (4, 0.5, 3), (0, 0, 1.5), mat)
    blocks, log = export()
    assert blocks, log
    from hammerless.blender.extract import texture_file_name
    name = texture_file_name("My Brick")
    assert name.startswith("my_brick_") and name != texture_file_name("My_Brick"), name   # no overwriting
    mats = {s.get("material") for sol in world_solids(blocks) for s in sol.blocks("side")}
    assert f"HAMMERLESS/TEST_MAP/{name.upper()}" in mats, mats
    vtf = os.path.join(FAKE_GAME, "left4dead2", "materials", "hammerless", "test_map", name + ".vtf")
    h = read_vtf_header(vtf)
    assert (h["width"], h["height"]) == (256, 128), h          # the brick image, not the 64x64 one
    assert h["mips"] == 9
    with open(vtf.replace(".vtf", ".vmt")) as f:
        assert f"hammerless/test_map/{name}" in f.read()


def test_collection_instances_export():
    # a kit collection placed twice with Add > Collection Instance: both copies export as brushes
    # at the copies' places; the kit itself (excluded from the view layer) doesn't
    reset_scene()
    add_box("floor", (10, 10, 0.2), (0, 0, -0.1))
    kit = bpy.data.collections.new("Kit")
    bpy.context.scene.collection.children.link(kit)
    piece = add_box("pillar", (0.5, 0.5, 2), (0, 0, 1))
    for c in list(piece.users_collection):
        c.objects.unlink(piece)
    kit.objects.link(piece)
    bpy.context.view_layer.layer_collection.children["Kit"].exclude = True
    for x in (-3.0, 3.0):
        e = bpy.data.objects.new(f"Kit copy {x}", None)
        e.instance_type = "COLLECTION"
        e.instance_collection = kit
        e.location = (x, 2.0, 0.0)
        bpy.context.scene.collection.objects.link(e)
    blocks, log = export()
    assert blocks, log
    scale = bpy.context.scene.hammerless.units_per_meter
    import re
    xs = []
    for sol in world_solids(blocks):
        pts = [tuple(map(float, p.split())) for sd in sol.blocks("side") for p in re.findall(r"\(([^)]*)\)", sd.get("plane"))]
        if (max(p[0] for p in pts) - min(p[0] for p in pts) < 1.0 * scale       # pillar: 0.5 m wide,
                and abs(max(p[2] for p in pts) - 2.0 * scale) < 1):                 # 2 m tall (not the shell)
            xs.append(round(sum(p[0] for p in pts) / len(pts) / scale))
    assert sorted(xs) == [-3, 3], (xs, log)
    assert "hidden object" not in log, log          # the kit's own objects aren't 'hidden' walls


def test_entity_tools_audit():
    # pre-release audit, round 2: hidden entities, Turn Selected into, preset copies, Change Entity
    reset_scene()
    add_box("floor", (10, 10, 0.2), (0, 0, -0.1))
    bpy.ops.hammerless.add_preset(preset="TANK_AMBUSH")
    bpy.context.scene.cursor.location = (3, 0, 0)
    bpy.ops.hammerless.add_preset(preset="TANK_AMBUSH")
    names = sorted(kv.value for o in bpy.data.objects for kv in o.hammerless.keyvalues
                   if kv.key == "targetname" and o.hammerless.classname == "commentary_zombie_spawner")
    assert len(names) == 2 and names[0] != names[1], names          # each copy has its own spawner
    trig_targets = sorted(op.target for o in bpy.data.objects for op in o.hammerless.outputs)
    assert trig_targets == names, (trig_targets, names)              # ...and its trigger points at its own

    bpy.context.scene.cursor.location = (0, 0, 0)
    bpy.ops.hammerless.add_entity(classname="weapon_first_aid_kit_spawn")
    kit = bpy.context.object
    wall = add_box("wall", (1, 1, 1), (2, 2, 0.5))
    twin = wall.copy()                                               # shares the wall's mesh
    bpy.context.scene.collection.objects.link(twin)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    kit.select_set(True)
    wall.select_set(True)
    bpy.ops.hammerless.set_brush_entity(classname="trigger_once")
    assert kit.hammerless.classname == "weapon_first_aid_kit_spawn" and kit.hammerless.role == "ENTITY"
    assert wall.hammerless.classname == "trigger_once" and wall.data is not twin.data   # twin untouched

    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = kit
    bpy.ops.hammerless.set_entity_class(classname="weapon_pain_pills_spawn")
    assert kit.hammerless.classname == "weapon_pain_pills_spawn"     # changed in place, no new object
    assert not [o for o in bpy.data.objects if o.hammerless.classname == "weapon_first_aid_kit_spawn"]

    kit.hide_set(True)
    blocks, log = export()
    assert blocks, log
    assert not entities(blocks, "weapon_pain_pills_spawn"), "a hidden entity was exported"
    assert "hidden object" in log, log


def test_game_material_by_name():
    reset_scene()
    mat = bpy.data.materials.new("concrete/concrete_floor_01")
    add_box("floor", (4, 4, 0.5), (0, 0, 0), mat)
    blocks, log = export()
    assert blocks, log
    mats = {s.get("material") for sol in world_solids(blocks) for s in sol.blocks("side")}
    assert "CONCRETE/CONCRETE_FLOOR_01" in mats, mats


def test_lights():
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.object.light_add(type="SUN", rotation=(math.radians(30), 0, 0))
    bpy.ops.object.light_add(type="POINT", location=(0, 0, 2))
    blocks, log = export()
    assert blocks, log
    env = entities(blocks, "light_environment")
    assert len(env) == 1  # the sun, no auto-added one
    assert float(env[0].get("pitch")) < -45, env[0].get("pitch")  # 30deg off vertical = -60
    assert len(entities(blocks, "light")) == 1


def test_mirrored_object():
    reset_scene()
    o = add_box("mirror", (1, 1, 1), (0, 0, 0))
    o.scale = (-1, 1, 1)
    blocks, log = export()
    assert blocks, log


def test_hidden_objects_skipped():
    reset_scene()
    add_box("floor", (4, 4, 0.5), (0, 0, 0))
    bpy.ops.mesh.primitive_torus_add()
    bpy.context.object.hide_set(True)  # invalid brush, but hidden
    blocks, log = export()
    assert blocks, log


def test_logic_graph_exports():
    # a Volume that shows a message, and Map Start -> Delay -> Horde, wired as nodes
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    zone = add_box("zone", (2, 2, 2), (-3, 0, 1))
    bpy.ops.object.empty_add(location=(2, 0, 0.1))
    sp = bpy.context.object
    sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
    tree = bpy.data.node_groups.new("Map Logic", "HL_LogicTree")
    vol = tree.nodes.new("HL_NodeVolume")
    vol.inputs["Mesh"].value = zone
    msg = tree.nodes.new("HL_NodeMessage")
    msg.text = "Get to the gate"
    start, delay, horde = (tree.nodes.new(t) for t in ("HL_NodeMapStart", "HL_NodeDelay", "HL_NodeHorde"))
    delay.seconds = 10
    tree.links.new(vol.outputs["On Enter"], msg.inputs["Show"])
    tree.links.new(start.outputs["On Map Start"], delay.inputs["In"])
    tree.links.new(delay.outputs["Out"], horde.inputs["Start"])
    blocks, log = export()
    assert blocks, log
    trig = entities(blocks, "trigger_multiple")
    assert len(trig) == 1 and trig[0].blocks("solid"), log
    conns = trig[0].blocks("connections")[0]
    assert conns.get("OnStartTouch").startswith("hl_show_message,ShowHint"), conns.get("OnStartTouch")
    hint = entities(blocks, "env_instructor_hint")[0]
    assert hint.get("hint_caption") == "Get to the gate"
    sends = [v for r in entities(blocks, "logic_relay") for c in r.blocks("connections")
             for k, v in c.items if not isinstance(v, type(c)) and k == "OnTrigger"]
    assert "director,ForcePanicEvent,,10,-1" in sends, sends
    # the volume's mesh became the trigger, not a solid wall
    assert all("toolstrigger" in sd.get("material").lower() for sd in trig[0].blocks("solid")[0].blocks("side"))
    assert not any(sd.get("material").lower() == "tools/toolstrigger"
                   for so in world_solids(blocks) for sd in so.blocks("side"))


def test_logic_graph_editor_audit():
    # reroutes and muted nodes pass wires through; a graph of another scene stays out of this map
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    tree = bpy.data.node_groups.new("Map Logic", "HL_LogicTree")
    tree.scene_name = bpy.context.scene.name
    start, delay, horde = (tree.nodes.new(t) for t in ("HL_NodeMapStart", "HL_NodeDelay", "HL_NodeHorde"))
    reroute = tree.nodes.new("NodeReroute")
    tree.links.new(start.outputs["On Map Start"], reroute.inputs[0])
    tree.links.new(reroute.outputs[0], delay.inputs["In"])
    tree.links.new(delay.outputs["Out"], horde.inputs["Start"])
    delay.mute = True                                           # skip the wait, like Blender draws it
    other = bpy.data.scenes.new("Other map")
    elsewhere = bpy.data.node_groups.new("Other Logic", "HL_LogicTree")
    elsewhere.scene_name = other.name
    s2, h2 = elsewhere.nodes.new("HL_NodeMapStart"), elsewhere.nodes.new("HL_NodeCrescendo")
    elsewhere.links.new(s2.outputs["On Map Start"], h2.inputs[0])
    blocks, log = export()
    assert blocks, log
    autos = [v for e in entities(blocks, "logic_auto") for c in e.blocks("connections")
             for k, v in c.items if k == "OnMapSpawn" and isinstance(v, str)]
    assert any(a.startswith("director,ForcePanicEvent,,") for a in autos), (autos, log)
    assert not any("crescendo" in a.lower() or "ScriptedPanicEvent" in a for a in autos), autos


def test_graph_from_outputs_keeps_delay_and_once():
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.hammerless.add_entity(classname="logic_relay")
    relay = bpy.context.object
    o = relay.hammerless.outputs.add()
    o.output, o.target, o.input, o.delay, o.only_once = "OnTrigger", "director", "ForcePanicEvent", 3.0, True
    before, _log = export()
    assert before
    bpy.ops.hammerless.logic_from_outputs()
    tree = next(t for t in bpy.data.node_groups if t.bl_idname == "HL_LogicTree")
    assert tree.use_fake_user and tree.scene_name == bpy.context.scene.name
    kinds = sorted(n.bl_idname for n in tree.nodes)
    if relay.hammerless.outputs:                               # no game definitions here: kept, not lost
        assert len(relay.hammerless.outputs) == 1
        return
    assert "HL_NodeDelay" in kinds and "HL_NodeOnce" in kinds, kinds
    delay = next(n for n in tree.nodes if n.bl_idname == "HL_NodeDelay")
    assert abs(delay.seconds - 3.0) < 1e-6


def test_logic_nodes_convert_plain_meshes():
    # Button and Move Over Time turn ordinary meshes into a button and a mover (with a nav blocker)
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    btn = add_box("btn", (0.1, 0.6, 0.6), (-2, 0, 1))
    gate = add_box("gate", (0.2, 3, 2.5), (2, 0, 1.25))
    bpy.ops.object.empty_add(location=(-4, 0, 0.1))
    sp = bpy.context.object
    sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
    tree = bpy.data.node_groups.new("Map Logic", "HL_LogicTree")
    b = tree.nodes.new("HL_NodeButton")
    b.inputs["Mesh"].value = btn
    info = tree.nodes.new("HL_NodeObjectInfo")
    info.target = gate
    mv = tree.nodes.new("HL_NodeMove")
    tree.links.new(info.outputs["Object"], mv.inputs["Object"])
    tree.links.new(b.outputs["On Press"], mv.inputs["Go"])
    blocks, log = export()
    assert blocks, log
    button = entities(blocks, "func_button")
    mover = entities(blocks, "func_movelinear")
    assert len(button) == 1 and len(mover) == 1, log
    assert button[0].get("spawnflags") == "1025"
    assert mover[0].get("movedir") == "90 0 0" and float(mover[0].get("speed")) > 0
    name = mover[0].get("targetname")
    conns = button[0].blocks("connections")[0]
    assert conns.get("OnPressed").startswith(name + ",Open"), conns.get("OnPressed")
    # no nav blocker by default: L4D2 blocks from map load and the Director then puts no wandering
    # zombies behind the closed gate; it's there when asked for
    assert not entities(blocks, "func_nav_blocker"), "gate blocks the nav by default"
    mv.block_nav = True
    blocks2, log2 = export()
    assert entities(blocks2, "func_nav_blocker"), "no nav blocker when Block Nav While Closed is on"
    # the meshes left the world: only the floor (plus the sky shell) stays world geometry
    assert len(world_solids(blocks)) == 1 + 6, len(world_solids(blocks))


def test_logic_value_wires():
    # grey/pink value wires from Blender reach the map script
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.object.empty_add(location=(2, 0, 0.1))
    sp = bpy.context.object
    sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
    tree = bpy.data.node_groups.new("Map Logic", "HL_LogicTree")
    path, rand, cmp_, when, spawn = (tree.nodes.new(t) for t in (
        "HL_NodeProgress", "HL_NodeRandomValue", "HL_NodeCompare", "HL_NodeWhen", "HL_NodeSpawn"))
    rand.inputs["Min"].value, rand.inputs["Max"].value = 0.25, 0.75
    tree.links.new(path.outputs["Furthest Survivor"], cmp_.inputs["A"])
    tree.links.new(rand.outputs["Value"], cmp_.inputs["B"])
    tree.links.new(cmp_.outputs["Result"], when.inputs["Condition"])
    tree.links.new(when.outputs["On True"], spawn.inputs["Spawn"])
    blocks, log = export()
    assert blocks, log
    path_ = os.path.join(FAKE_GAME, "left4dead2", "scripts", "vscripts", "hammerless", "logic_test_map.nut")
    script = open(path_, encoding="utf-8").read()
    assert "RandomFloat(0.25, 0.75)" in script, script
    assert "(HL_PathFurthest() >= HL_Random_" in script, script
    assert entities(blocks, "logic_script") and any(e.get("thinkfunction") == "HL_Think"
                                                    for e in entities(blocks, "logic_script"))


def test_logic_loops():
    # Blender marks wires that loop back invalid: a loop of event wires still works (a timer that
    # stops itself), a circle of value wires is left out with a warning
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    tree = bpy.data.node_groups.new("Map Logic", "HL_LogicTree")
    timer, delay = tree.nodes.new("HL_NodeTimer"), tree.nodes.new("HL_NodeDelay")
    tree.links.new(timer.outputs["On Tick"], delay.inputs["In"])
    tree.links.new(delay.outputs["Out"], timer.inputs["Stop"])
    m1, m2 = tree.nodes.new("HL_NodeMath"), tree.nodes.new("HL_NodeMath")
    tree.links.new(m1.outputs["Value"], m2.inputs["A"])
    tree.links.new(m2.outputs["Value"], m1.inputs["A"])
    assert not all(l.is_valid for l in tree.links)
    blocks, log = export()
    assert blocks, log
    sends = [v for r in entities(blocks, "logic_relay") for c in r.blocks("connections")
             for k, v in c.items if not isinstance(v, type(c)) and k == "OnTrigger"]
    assert any(",Disable," in v for v in sends), sends
    assert "circle of value wires" in log, log


def test_logic_examples_build():
    # every example graph builds, and compiles into the map with nothing to warn about
    from hammerless.blender.logic_examples import EXAMPLES, build_example
    bad = []
    for i, ex in enumerate(EXAMPLES):
        reset_scene()
        add_box("floor", (40, 40, 0.5), (0, 0, -0.25))
        bpy.ops.object.empty_add(location=(2, 0, 0.1))
        sp = bpy.context.object
        sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
        bpy.context.scene.cursor.location = (-10, -10, 0)
        tree = build_example(bpy.context, i)
        assert len([n for n in tree.nodes if n.bl_idname != "NodeFrame"]) == len(ex["nodes"]), ex["title"]
        assert len(tree.links) == len(ex["links"]), (ex["title"], len(tree.links), len(ex["links"]))
        assert all(l.is_valid for l in tree.links), ex["title"]        # (Blender marks loops invalid)
        blocks, log = export()
        if blocks is None or tree.name in log:
            bad.append(f"{tree.name}:\n{log}")
    assert not bad, "\n".join(bad)


# ---------------------------------------------------------------- runner

def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


main()
