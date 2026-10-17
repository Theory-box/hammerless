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
    assert _detail_note(bpy.context, big) == "Detail if round or small"
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
    assert _detail_note(bpy.context, big) == "Detail, from its collection"
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
    from hammerless.core.gamefiles import director_input_script
    nut = os.path.join(FAKE_GAME, "left4dead2", "scripts", "vscripts",
                       director_input_script("test_map", "director") + ".nut")
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
    from hammerless.core.gamefiles import director_input_script
    assert params == sorted(f"director,ScriptedPanicEvent,{director_input_script('test_map', c)},0,1"
                            for c in ("crescendo_1", "crescendo_2")), params
    assert "crescendo" not in log.lower() or "WARNING" not in log, log


def test_surface_override_patch_material():
    reset_scene()
    mat = bpy.data.materials.new("concrete/concrete_floor_01")
    mat.hammerless.surface = "ice"
    add_box("ice floor", (4, 4, 0.5), (0, 0, 0), mat)
    blocks, log = export()
    assert blocks, log
    mats = {sd.get("material") for sol in world_solids(blocks) for sd in sol.blocks("side")}
    import hashlib
    tag = hashlib.sha1(b"concrete/concrete_floor_01").hexdigest()[:6]     # (patch names carry a hash of the base)
    assert f"HAMMERLESS/TEST_MAP/PATCH_CONCRETE_CONCRETE_FLOOR_01_{tag}_ICE".upper() in mats, mats
    vmt = os.path.join(FAKE_GAME, "left4dead2", "materials", "hammerless", "test_map",
                       f"patch_concrete_concrete_floor_01_{tag}_ice.vmt")
    assert '"$surfaceprop" "ice"' in open(vmt).read()


def test_custom_compile_options():
    from hammerless.blender.ops import compile_options, launch_options
    s = reset_scene()
    from hammerless.core import compile as cc
    s.light_tool = "VALVE"                          # (the presets are vrad's options; ours adds its own on top)
    assert compile_options(s).vrad_args() == cc.PRESETS["NORMAL"].vrad_args()
    s.light_tool = "HAMMERLESS"
    s.compile_preset = "FINAL"                      # (sets Lighting and Visibility)
    assert s.light_quality == "FINAL" and s.vis_mode == "FULL" and s.light_sky_rays == 16
    s.light_bounces = 50                            # (a lighting setting by hand: Custom, and the build shows Mixed)
    assert s.light_quality == "CUSTOM" and s.compile_preset == "CUSTOM"
    assert "-bounce" in compile_options(s).vrad_args()
    s.vis_mode, s.light_quality = "FAST", "OFF"
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


def test_behaviours():
    # a Behaviour graph shared by three objects: built once for each, Self being that object, with its own names
    from hammerless.blender.behaviours import new_behaviour_tree
    from hammerless.blender.logicgroups import new_group_tree, remove_unused_groups
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    boxes = [add_box(f"Blinker {c}", (0.5, 0.5, 0.5), (i * 2.0 - 2.0, 2, 0.25)) for i, c in enumerate("ABC")]
    bpy.ops.object.empty_add(location=(-4, 0, 0.1))
    sp = bpy.context.object
    sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
    tree = new_behaviour_tree("Blink")
    me = next(n for n in tree.nodes if n.bl_idname == "HL_NodeSelf")
    timer = tree.nodes.new("HL_NodeTimer")
    timer.seconds = 2.0
    sh = tree.nodes.new("HL_NodeShowHide")
    tree.links.new(me.outputs["Object"], sh.inputs["Object"])
    tree.links.new(timer.outputs["On Tick"], sh.inputs["Hide"])
    # a group used only inside the behaviour is in use (the cleanup keeps it)
    grp = new_group_tree("Blink Helper", keep=False)
    gnode = tree.nodes.new("HL_NodeGroup")
    gnode.group = grp
    for b in boxes:
        b.hammerless.behaviours.add().graph = tree
    from hammerless.blender.logic import logic_trees
    assert tree not in logic_trees(), "a Behaviour isn't a world graph"
    remove_unused_groups()
    assert bpy.data.node_groups.get("Blink Helper") is not None, "a group used by a Behaviour was removed"
    blocks, log = export()
    assert blocks, log
    timers = entities(blocks, "logic_timer")
    brushes = entities(blocks, "func_brush")
    assert len(timers) == 3 and len(brushes) == 3, (len(timers), len(brushes), log)
    names = {b.get("targetname") for b in brushes}
    hit = set()
    for t in timers:
        target = t.blocks("connections")[0].get("OnTimer").split(",")[0]
        assert target in names, (target, names)
        hit.add(target)
    assert hit == names, "each copy hides its own object"
    assert all("blinker" in n for n in names), names
    # Make Unique: one object gets its own copy, the others keep sharing
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = boxes[0]
    assert bpy.ops.hammerless.behaviour_unique(index=0) == {"FINISHED"}
    assert boxes[0].hammerless.behaviours[0].graph != tree and boxes[1].hammerless.behaviours[0].graph == tree
    assert boxes[0].hammerless.behaviours[0].graph.is_behaviour
    # Copy to Selected: the others get it once (shared), the active keeps its own
    extra = add_box("Blinker D", (0.5, 0.5, 0.5), (4, 2, 0.25))
    for o in (boxes[1], extra):
        o.select_set(True)
    bpy.context.view_layer.objects.active = boxes[1]
    boxes[1].hammerless.behaviours_index = 0
    assert bpy.ops.hammerless.behaviour_copy() == {"FINISHED"}
    assert [s.graph for s in extra.hammerless.behaviours] == [tree] and len(boxes[1].hammerless.behaviours) == 1
    # Self in a world graph is its brain (so it can be added there too)
    from hammerless.blender.logic import HL_NodeSelf
    world = bpy.data.node_groups.new("World", "HL_LogicTree")
    assert HL_NodeSelf.poll(world) and HL_NodeSelf.poll(tree)


def test_entity_events_self():
    # Entity Events in Self mode: the carriers' class, and built for each carrier it acts on that one
    from hammerless.blender import logic
    from hammerless.blender.behaviours import new_behaviour_tree
    from hammerless.blender.logicgroups import flatten
    from hammerless.core.build import Report
    reset_scene()
    btns = [add_box(f"Panel {i}", (0.1, 0.6, 0.6), (i * 2.0, 0, 1)) for i in range(2)]
    tree = new_behaviour_tree("Panel Press")
    for b in btns:
        b.hammerless.role, b.hammerless.classname = "BRUSH_ENTITY", "func_button"
        b.hammerless.behaviours.add().graph = tree
    ev = tree.nodes.new("HL_NodeObject")
    ev.use_self = True
    assert ev.classname == "func_button" and ev.auto_title.startswith("Self"), (ev.classname, ev.auto_title)
    world = bpy.data.node_groups.new("World", "HL_LogicTree")
    other = world.nodes.new("HL_NodeObject")
    assert not world.is_behaviour and not world.is_group     # (the Self toggle is only drawn in behaviours/groups)
    for b in btns:
        logic._SELF_OWNER[0] = b
        try:
            flat = flatten(bpy.context, tree, Report(), prefix=f"{b.name}/")
        finally:
            logic._SELF_OWNER[0] = None
        node = next(n for n in flat.nodes if n.kind == "OBJECT")
        assert node.obj == b.name and node.id.startswith(b.name + "/"), (node.obj, node.id)
    assert other.to_lnode(bpy.context).obj == ""


def test_brains():
    # world graphs live on a brain empty: an old graph gets one and builds the same; a brain outside the map turns its
    # logic off; a graph whose brain was deleted isn't built (said so); New Logic Graph makes one
    from hammerless.blender.logic import migrate_brains, new_tree
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    box = add_box("Gate Box", (0.5, 0.5, 0.5), (2, 2, 0.25))
    bpy.ops.object.empty_add(location=(-4, 0, 0.1))
    sp = bpy.context.object
    sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
    tree = bpy.data.node_groups.new("Old Logic", "HL_LogicTree")       # (a graph from before brains)
    timer = tree.nodes.new("HL_NodeTimer")
    sh = tree.nodes.new("HL_NodeShowHide")
    sh.inputs["Object"].value = box
    tree.links.new(timer.outputs["On Tick"], sh.inputs["Hide"])

    def names(blocks):
        return sorted((c, e.get("targetname") or "") for c in ("logic_timer", "func_brush")
                      for e in entities(blocks, c))
    blocks, log = export()
    before = names(blocks)
    assert ("logic_timer" in {c for c, _n in before}), log
    assert migrate_brains() == 1 and tree.brain is not None and tree.brain.get("hl_brain")
    assert migrate_brains() == 0
    blocks, log = export()
    assert names(blocks) == before, (names(blocks), before)            # (exactly as before)
    brain = tree.brain
    for c in list(brain.users_collection):                                # (outside the map: off)
        c.objects.unlink(brain)
    blocks, log = export()
    assert not entities(blocks, "logic_timer"), log
    bpy.context.scene.collection.objects.link(brain)
    bpy.data.objects.remove(brain)                                       # (deleted: not built, a warning)
    assert tree.brain is None
    blocks, log = export()
    assert not entities(blocks, "logic_timer")
    assert "has no brain" in log, log
    made = new_tree(bpy.context, "Fresh Logic")
    assert made.brain is not None and made.brain.name in bpy.context.scene.objects


def test_brush_tools():
    # Carve / Intersect / Clip on brush objects: convex pieces as loose parts, volumes right, cut faces the cutter's
    import bmesh
    from hammerless.blender.extract import _loose_parts

    def parts_and_volume(obj):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        n = len(_loose_parts(bm))
        vol = bm.calc_volume(signed=True) * obj.matrix_world.determinant()     # (world size)
        mats = {f.material_index for f in bm.faces}
        bm.free()
        return n, vol, mats

    def select(objs, active):
        for o in bpy.context.selected_objects:
            o.select_set(False)
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = active
    reset_scene()
    wall = add_box("Wall", (2, 2, 2), (0, 0, 1))
    wall.data.materials.append(bpy.data.materials.new("wall mat"))
    cutter = add_box("Door Cutter", (1, 1, 3), (0, 0, 1))
    cutter.data.materials.append(bpy.data.materials.new("cut mat"))
    select([wall, cutter], cutter)
    assert bpy.ops.hammerless.brush_carve() == {"FINISHED"}
    n, vol, mats = parts_and_volume(wall)
    assert n == 4 and abs(vol - (8.0 - 2.0)) < 1e-4, (n, vol)
    assert [m.name for m in wall.data.materials if m] == ["wall mat", "cut mat"] and mats == {0, 1}, mats
    # intersect: only the overlap
    block = add_box("Block", (2, 2, 2), (5, 0, 1))
    cut2 = add_box("Overlap", (2, 2, 2), (6, 0, 1))
    select([block, cut2], cut2)
    assert bpy.ops.hammerless.brush_carve(intersect=True) == {"FINISHED"}
    n, vol, _m = parts_and_volume(block)
    assert n == 1 and abs(vol - 4.0) < 1e-4, (n, vol)
    # clip at the cursor: two halves
    slab = add_box("Slab", (2, 2, 2), (10, 0, 1))
    bpy.context.scene.cursor.location = (10, 0, 0.5)
    select([slab], slab)
    assert bpy.ops.hammerless.brush_clip(axis="Z", keep="BOTH") == {"FINISHED"}
    n, vol, _m = parts_and_volume(slab)
    assert n == 2 and abs(vol - 8.0) < 1e-4, (n, vol)
    # merge: the two halves back into one
    assert bpy.ops.hammerless.brush_merge() == {"FINISHED"}
    n, vol, _m = parts_and_volume(slab)
    assert n == 1 and abs(vol - 8.0) < 1e-4, (n, vol)
    # a part that isn't convex is left as it was
    bpy.ops.mesh.primitive_monkey_add(location=(15, 0, 1))
    monkey = bpy.context.object
    before = len(monkey.data.vertices)
    cut3 = add_box("Monkey Cutter", (1, 1, 1), (15, 0, 1))
    select([monkey, cut3], cut3)
    bpy.ops.hammerless.brush_carve()
    assert len(monkey.data.vertices) == before                   # (the monkey isn't convex: kept as it was)


def test_draw_brush_maths_and_hollow():
    # the Draw Brush frame (axis-aligned on axis-aligned surfaces, along a slope otherwise), its box's corners on
    # its own planes; Hollow leaves exactly the walls
    import bmesh
    from mathutils import Vector
    from hammerless.blender.drawbrush import box_corners, box_sides, frame_for
    n, u, v = frame_for(Vector((0.0, 0.0001, 0.99999)))
    assert tuple(n) == (0, 0, 1) and tuple(u) == (1, 0, 0) and tuple(v) == (0, 1, 0), (n, u, v)
    n, u, v = frame_for(Vector((0.0, -0.6, 0.8)))
    assert abs(n.dot(u)) < 1e-6 and abs(n.dot(v)) < 1e-6 and abs(u.dot(v)) < 1e-6 and abs(n.length - 1) < 1e-6
    upm = 52.49
    corners = box_corners(n, u, v, 10.0, -16, 32, 8, -24, 40, upm)
    sides = box_sides(n, u, v, 10.0, -16, 32, 8, -24, 40, 0)
    for c in corners:
        g = c * upm
        assert all(nn[0] * g.x + nn[1] * g.y + nn[2] * g.z - d < 1e-3 for nn, d, _t in sides)
    # Hollow: a 2 m (104.98 unit) box with 8-unit walls
    reset_scene()
    box = add_box("Room", (2, 2, 2), (0, 0, 1))
    for o in bpy.context.selected_objects:
        o.select_set(False)
    box.select_set(True)
    bpy.context.view_layer.objects.active = box
    assert bpy.ops.hammerless.brush_hollow(thickness=8.0) == {"FINISHED"}
    bm = bmesh.new()
    bm.from_mesh(box.data)
    vol = bm.calc_volume(signed=True) * box.matrix_world.determinant()
    bm.free()
    t = 8.0 / bpy.context.scene.hammerless.units_per_meter
    want = 8.0 - (2 - 2 * t) ** 3
    assert abs(vol - want) < 1e-4, (vol, want)


def test_draw_brush_results():
    # what Draw Brush makes once a box is drawn (the mouse part needs a person): Add -> a closed brush on the
    # surface with the surface's material; Carve -> the box cut out of the brushes it touches, their own material
    import types
    import bmesh
    from mathutils import Vector
    from hammerless.blender.drawbrush import HL_OT_draw_brush, box_corners, box_sides, frame_for
    from hammerless.blender.extract import _loose_parts
    reset_scene()
    wall = add_box("Wall", (6, 0.3, 3), (0, 6, 1.5))
    wall.data.materials.append(bpy.data.materials.new("plaster"))
    upm = bpy.context.scene.hammerless.units_per_meter
    reports = []

    def fake(mode, n_world, d0, a0, a1, b0, b1, h):
        n, u, v = frame_for(Vector(n_world))
        f = types.SimpleNamespace(mode=mode, upm=upm, n=n, u=u, v=v, d0=d0, a0=a0, a1=a1, b0=b0, b1=b1, h=h,
                                  hit_obj=wall, hit_index=0, report=lambda kind, msg: reports.append(msg))
        f._box = lambda: box_corners(n, u, v, d0, a0, a1, b0, b1, h, upm)
        f._sides = lambda tag: box_sides(n, u, v, d0, a0, a1, b0, b1, h, tag)
        f._carve = lambda context, mat: HL_OT_draw_brush._carve(f, context, mat)
        return f
    # Add: on the floor (z = 0), 64 x 32, 48 high
    f = fake("ADD", (0, 0, 1), 0.0, 0, 64, 0, 32, 48)
    assert HL_OT_draw_brush._finish(f, bpy.context) == {"FINISHED"}
    b = bpy.context.object
    bm = bmesh.new()
    bm.from_mesh(b.data)
    vol = bm.calc_volume(signed=True) * upm ** 3
    assert len(bm.faces) == 6 and bm.is_valid and abs(vol - 64 * 32 * 48) < 1.0, (len(bm.faces), vol)
    bm.free()
    # Carve: a doorway through the wall (its near face is y = 5.85 m, facing -y), 2 m wide, 2 m tall, deep enough
    d0 = -5.85 * upm                                            # (the face plane: -y . p = d0)
    door = fake("CARVE", (0, -1, 0), d0, -1.0 * upm, 1.0 * upm, 0.0, 2.0 * upm, -0.5 * upm)
    assert HL_OT_draw_brush._carve(door, bpy.context, wall.data.materials[0]) == {"FINISHED"}
    bm = bmesh.new()
    bm.from_mesh(wall.data)
    parts = len(_loose_parts(bm))
    vol = bm.calc_volume(signed=True) * wall.matrix_world.determinant()
    bm.free()
    assert parts >= 2 and abs(vol - (6 * 0.3 * 3 - 2 * 0.3 * 2)) < 1e-3, (parts, vol, reports)
    assert {m.name for m in wall.data.materials if m} == {"plaster"}       # (the doorway's faces: the wall's own)


def test_light_pattern_presets():
    # a light's Pattern is a preset Behaviour: lights sharing one share a name (one light style), set once
    from hammerless.blender.lightpresets import PATTERNS, preset_slot
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.object.empty_add(location=(-4, 0, 0.1))
    sp = bpy.context.object
    sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
    lamps = []
    for i in range(3):
        data = bpy.data.lights.new(f"Hall Lamp {i}", "POINT")
        lamp = bpy.data.objects.new(f"Hall Lamp {i}", data)
        bpy.context.collection.objects.link(lamp)
        lamp.location = (i * 2.0 - 2.0, 0.0, 2.0)
        lamp.hammerless.light_pattern = "1"
        lamps.append(lamp)
    graphs = {preset_slot(l).graph for l in lamps}
    assert len(graphs) == 1, graphs
    blocks, log = export()
    assert blocks, log
    lights = entities(blocks, "light")
    names = {l.get("targetname") for l in lights}
    assert len(lights) == 3 and len(names) == 1 and next(iter(names)), (names, log)
    assert all(not l.get("style") for l in lights), [l.get("style") for l in lights]
    script = open(os.path.join(FAKE_GAME, "left4dead2", "scripts", "vscripts", "hammerless",
                               "logic_test_map.nut")).read()
    assert script.count(PATTERNS["1"]) == 1, script.count(PATTERNS["1"])
    assert script.count("SetPattern") == 1
    # another preset: the graph is replaced; Make Unique: its own name; Steady: no Behaviour, no name
    lamps[0].hammerless.light_pattern = "10"
    assert preset_slot(lamps[0]).graph.get("hl_light_preset") == "10" and len(lamps[0].hammerless.behaviours) == 1
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = lamps[1]
    assert bpy.ops.hammerless.behaviour_unique(index=0) == {"FINISHED"}
    lamps[2].hammerless.light_pattern = "0"
    assert preset_slot(lamps[2]) is None and not lamps[2].hammerless.behaviours
    blocks, log = export()
    by_name = {l.get("targetname") or "" for l in entities(blocks, "light")}
    assert len(by_name) == 3 and "" in by_name, by_name     # (Fluorescent, Lamp 1's own Flicker, a steady unnamed)


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


def test_custom_models_extract():
    # Custom Model meshes: linked copies share one model, each copy is a prop where the object is
    from mathutils import Vector
    from hammerless.blender.extract import extract_scene
    from hammerless.core.build import Report
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.mesh.primitive_monkey_add(location=(0, 2, 1))
    m = bpy.context.object
    m.hammerless.role = "MODEL"
    bpy.ops.object.duplicate_move_linked()
    m2 = bpy.context.object
    m2.location, m2.rotation_euler = (3, 2, 1), (0, 0, math.radians(90))
    bpy.ops.mesh.primitive_cylinder_add(location=(-3, 2, 1))
    c = bpy.context.object
    c.hammerless.role, c.hammerless.model_kind = "MODEL", "PHYSICS"
    bpy.ops.mesh.primitive_cube_add(location=(0, -3, 1))
    flipped = bpy.context.object
    flipped.hammerless.role, flipped.scale = "MODEL", (-1, 1, 1)
    rep = Report()
    ir, _ = extract_scene(bpy.context, rep)
    assert not rep.errors, rep.errors
    props = [e for e in ir.entities if e.classname.startswith("prop_")]
    monkeys = [e for e in props if e.keyvalues["model"].endswith("/suzanne.mdl")]
    assert len(monkeys) == 2, [e.keyvalues for e in props]
    assert abs(monkeys[1].angles[1] - 90) < 0.01 and abs(monkeys[1].origin[0] - 3 * 52.49) < 0.5
    spec = ir.models["hammerless/test_map/suzanne"]
    assert spec.kind == "STATIC" and len(spec.collision) == 3      # head and two eyes: three convex pieces
    phys = [e for e in props if e.classname == "prop_physics"]
    assert len(phys) == 1 and ir.models[phys[0].keyvalues["model"][7:-4]].kind == "PHYSICS"
    # a mirrored copy is its own model, with its triangles turned so they still face out
    cube = next(s for n, s in ir.models.items() if "cube" in n)
    (mat, verts) = cube.triangles[0]
    a, b, cc = (Vector(v[0]) for v in verts)
    assert (b - a).cross(cc - a).dot(Vector(verts[0][1])) > 0


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


def test_nav_area_volume():
    # Add > Volumes > Nav Area: never written into the map, but the nav builder gets it as one area of its own
    from hammerless.blender.ops import build_map_text, game_root
    from hammerless.core.nav import NAV_AREA_BIT, collect_regions
    reset_scene()
    add_box("floor", (20, 20, 0.5), (0, 0, -0.25))
    assert bpy.ops.hammerless.spawn_item(item="entity:hammerless_nav_area") == {"FINISHED"}
    area = bpy.context.object
    assert area.hammerless.classname == "hammerless_nav_area", area.hammerless.classname
    ir, text, rep = build_map_text(bpy.context, game_root(bpy.context))
    assert text is not None, rep.errors
    assert "hammerless_nav_area" not in text
    regions, problems = collect_regions(ir)
    mine = [r for r in regions if r.bits & NAV_AREA_BIT]
    assert len(mine) == 1 and mine[0].maxs[0] - mine[0].mins[0] > 100, [(r.mins, r.maxs) for r in mine]


def test_new_landmarks_get_free_names():
    # each Landmark added gets a name no other landmark has (two with one name stop the build)
    reset_scene()
    names = []
    for _ in range(3):
        bpy.ops.hammerless.add_entity(classname="info_landmark")
        o = bpy.context.object
        names.append(next(kv.value for kv in o.hammerless.keyvalues if kv.key == "targetname"))
    assert names == ["landmark_1", "landmark_2", "landmark_3"], names


def test_logic_examples_build():
    # every example graph builds, and compiles into the map with nothing to warn about. Entity event
    # nodes need the game's entity definitions: copied from the real game when it's installed
    import shutil
    from hammerless.blender.logic_examples import all_examples, build_example
    from hammerless.core.compile import find_game_root
    real = find_game_root()
    has_fgd = bool(real) and os.path.exists(os.path.join(real, "bin", "left4dead2.fgd"))
    if has_fgd:
        os.makedirs(os.path.join(FAKE_GAME, "bin"), exist_ok=True)
        for f in ("base.fgd", "left4dead2.fgd"):
            shutil.copy(os.path.join(real, "bin", f), os.path.join(FAKE_GAME, "bin", f))
    bad = []
    for i, ex in enumerate(all_examples()):
        if _needs_game_models(ex):
            print(f"SKIP example {i + 1} here (it reads the game's models: test_game_skeleton_example)")
            continue
        if not has_fgd and any(n["type"] in ("HL_NodeObject", "HL_NodeDirector") for n in ex["nodes"]):
            print(f"SKIP example {i + 1} (no game entity definitions here)")
            continue
        reset_scene()
        add_box("floor", (40, 40, 0.5), (0, 0, -0.25))
        bpy.ops.object.empty_add(location=(2, 0, 0.1))
        sp = bpy.context.object
        sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
        bpy.context.scene.cursor.location = (-10, -10, 0)
        problems = []
        tree = build_example(bpy.context, i, problems)
        assert not problems, (ex["title"], problems)
        assert len([n for n in tree.nodes if n.bl_idname != "NodeFrame"]) == len(ex["nodes"]), ex["title"]
        assert len(tree.links) == len(ex["links"]), (ex["title"], len(tree.links), len(ex["links"]))
        # (Blender draws wires in an event loop red; they work. A real circle of value wires shows up as
        # a build warning below)
        blocks, log = export()
        # (an example that needs cheats on purpose gets that warning, and only that one)
        needs_cheats = False                      # (none: Slow Motion Headshots uses func_timescale)
        warned = [ln for ln in log.splitlines() if tree.name in ln]
        if needs_cheats:
            ok = len(warned) == 1 and "Needs cheats" in warned[0]
        else:
            ok = not warned
        if blocks is None and _needs_studiomdl_only(log):
            print(f"SKIP build of example {i + 1}: its animated model needs studiomdl.exe, not in the test's game folder")
            continue
        if blocks is None or not ok:
            bad.append(f"{tree.name}:\n{log}")
    assert not bad, "\n".join(bad)


def _needs_studiomdl_only(log: str) -> bool:
    """The build stopped only because an animated Custom Model needs the game's studiomdl.exe, which the tests' fake
    game folder doesn't have (real builds use the game's)."""
    errors = [ln for ln in log.splitlines() if ln.startswith("ERROR")]
    return bool(errors) and all("need studiomdl.exe" in ln for ln in errors)


def test_logic_examples_into_main_graph():
    # Add Example puts the example into the main graph (the one open, else the scene's own), in a frame named after
    # it, right of what's there: every example into one graph keeps all its nodes and wires (names that clash get
    # Blender's .001) and the map still builds
    from hammerless.blender.logic import new_tree
    from hammerless.blender.logic_examples import all_examples, build_example, main_tree
    from hammerless.core.compile import find_game_root
    real = find_game_root()
    has_fgd = bool(real) and os.path.exists(os.path.join(real, "bin", "left4dead2.fgd"))
    reset_scene()
    add_box("floor", (40, 40, 0.5), (0, 0, -0.25))
    bpy.ops.object.empty_add(location=(2, 0, 0.1))
    sp = bpy.context.object
    sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
    mine = new_tree(bpy.context)
    mine.name = "L4D2 Logic"
    old = build_example(bpy.context, 0)                # (an example graph from an older version: not the main one)
    assert main_tree(bpy.context) == mine
    want_links, spans = 0, []
    for i, ex in enumerate(all_examples()):
        if _needs_game_models(ex):
            print(f"SKIP example {i + 1} here (it reads the game's models: test_game_skeleton_example)")
            continue
        if not has_fgd and any(n["type"] in ("HL_NodeObject", "HL_NodeDirector") for n in ex["nodes"]):
            continue
        before = set(mine.nodes)
        problems = []
        assert build_example(bpy.context, i, problems, into=mine) == mine
        assert not problems, (ex["title"], problems)
        made = [n for n in mine.nodes if n not in before]
        assert len([n for n in made if n.bl_idname != "NodeFrame"]) == len(ex["nodes"]), ex["title"]
        outer = [n for n in made if n.bl_idname == "NodeFrame" and n.parent is None]
        assert len(outer) == 1 and outer[0].label.startswith(f"Example {i + 1:02d}:"), (ex["title"], outer)
        xs = [getattr(n, "location_absolute", n.location)[0] for n in made if n.bl_idname != "NodeFrame"]
        if xs:                                # (an example whose logic is all in Behaviours: only its note here)
            spans.append((min(xs), max(xs)))
        want_links += len(ex["links"])
    assert len(mine.links) == want_links, (len(mine.links), want_links)
    assert all(b[0] > a[1] for a, b in zip(spans, spans[1:])), "examples overlap"
    bpy.data.node_groups.remove(old)
    blocks, log = export()
    assert blocks is not None or _needs_studiomdl_only(log), log


SMALL_VMF = """versioninfo
{
\t"editorversion" "400"
\t"mapversion" "7"
}
world
{
\t"id" "1"
\t"mapversion" "7"
\t"classname" "worldspawn"
\t"skyname" "sky_l4d_rural02_hdr"
\tsolid
\t{
\t\t"id" "2"
\t\tside { "id" "3" "plane" "(-64 64 0) (64 64 0) (64 -64 0)" "material" "DEV/DEV_MEASUREGENERIC01B" "uaxis" "[1 0 0 7] 0.25" "vaxis" "[0 -1 0 3] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "4" "plane" "(-64 -64 -16) (64 -64 -16) (64 64 -16)" "material" "TOOLS/TOOLSNODRAW" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 -1 0 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "5" "plane" "(-64 64 0) (-64 -64 0) (-64 -64 -16)" "material" "DEV/DEV_MEASUREGENERIC01B" "uaxis" "[0 1 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "6" "plane" "(64 64 -16) (64 -64 -16) (64 -64 0)" "material" "DEV/DEV_MEASUREGENERIC01B" "uaxis" "[0 1 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "7" "plane" "(64 64 0) (-64 64 0) (-64 64 -16)" "material" "DEV/DEV_MEASUREGENERIC01B" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "8" "plane" "(64 -64 -16) (-64 -64 -16) (-64 -64 0)" "material" "DEV/DEV_MEASUREGENERIC01B" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\teditor { "color" "0 255 0" "visgroupshown" "1" }
\t}
\tsolid
\t{
\t\t"id" "20"
\t\tside { "id" "21" "plane" "(0 48 64) (48 48 32) (48 0 32)" "material" "BRICK/BRICKWALL01" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 -1 0 0] 0.25" "rotation" "0" "lightmapscale" "8" "smoothing_groups" "1" }
\t\tside { "id" "22" "plane" "(0 0 0) (48 0 0) (48 48 0)" "material" "TOOLS/TOOLSNODRAW" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 -1 0 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "23" "plane" "(0 48 64) (0 0 64) (0 0 0)" "material" "BRICK/BRICKWALL01" "uaxis" "[0 1 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "24" "plane" "(48 48 0) (48 0 0) (48 0 32)" "material" "BRICK/BRICKWALL01" "uaxis" "[0 1 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "25" "plane" "(48 48 32) (0 48 64) (0 48 0)" "material" "BRICK/BRICKWALL01" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "26" "plane" "(48 0 0) (0 0 0) (0 0 64)" "material" "BRICK/BRICKWALL01" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t}
\thidden
\t{
\t\tsolid { "id" "90" }
\t}
}
entity
{
\t"id" "30"
\t"classname" "light"
\t"targetname" "lamp"
\t"_light" "255 240 200 300"
\t"origin" "8 16 48"
\tconnections
\t{
\t\t"OnUser1" "lamp,TurnOff,,2.5,3"
\t}
}
hidden
{
\tentity { "id" "40" "classname" "info_target" "origin" "0 0 0" }
}
entity
{
\t"id" "50"
\t"classname" "func_detail"
\tsolid
\t{
\t\t"id" "51"
\t\tside { "id" "52" "plane" "(-32 32 16) (32 32 16) (32 -32 16)" "material" "WOOD/WOODWALL009A" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 -1 0 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "53" "plane" "(-32 -32 0) (32 -32 0) (32 32 0)" "material" "WOOD/WOODWALL009A" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 -1 0 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "54" "plane" "(-32 32 16) (-32 -32 16) (-32 -32 0)" "material" "WOOD/WOODWALL009A" "uaxis" "[0 1 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "55" "plane" "(32 32 0) (32 -32 0) (32 -32 16)" "material" "WOOD/WOODWALL009A" "uaxis" "[0 1 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "56" "plane" "(32 32 16) (-32 32 16) (-32 32 0)" "material" "WOOD/WOODWALL009A" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t\tside { "id" "57" "plane" "(32 -32 0) (-32 -32 0) (-32 -32 16)" "material" "WOOD/WOODWALL009A" "uaxis" "[1 0 0 0] 0.25" "vaxis" "[0 0 -1 0] 0.25" "rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" }
\t}
}
cameras
{
\t"activecamera" "-1"
}
"""


def _tree(blocks):
    def t(b):
        return (b.name.lower(), [t(i) if not isinstance(i, tuple) else i for i in b.items])
    return [t(b) for b in blocks]


def _imported_export(text):
    """Export the scene and return its blocks without Build's own helper entities (ids it made up)."""
    blocks, log = export()
    assert blocks is not None, log
    ids = {b.get("id") for b in vmf.parse(text)}
    return [b for b in blocks if not (b.name == "entity" and b.get("id") not in ids)]


def test_vmf_import_roundtrip():
    # a Hammer map imported and exported unchanged comes back exactly (Build only adds its helper script);
    # a moved brush gets new planes (the rest of its sides kept), a deleted entity is gone
    from hammerless.blender import vmfimport
    reset_scene()
    path = os.path.join(TMP, "small.vmf")
    with open(path, "w", encoding="latin-1") as f:
        f.write(SMALL_VMF)
    nb, ne = vmfimport.import_text(bpy.context, SMALL_VMF, path)
    bpy.context.scene.hammerless.map_name = "test_map"
    assert (nb, ne) == (3, 2), (nb, ne)
    original = _tree(vmf.parse(SMALL_VMF))
    assert _tree(_imported_export(SMALL_VMF)) == original
    lamp = next(o for o in bpy.data.objects if o.get("hl_vmf_kind") == "entity" and o.hammerless.classname == "light")
    assert lamp.hammerless.outputs[0].target == "lamp"
    brush = next(o for o in bpy.data.objects if o.get("hl_vmf_id") == 20)
    brush.location.z += 32 / bpy.context.scene.hammerless.units_per_meter
    bpy.data.objects.remove(lamp)
    bpy.context.view_layer.update()
    out = _imported_export(SMALL_VMF)
    assert not [b for b in out if b.name == "entity" and b.get("classname") == "light"]
    world = next(b for b in out if b.name == "world")
    moved = next(s for s in world.blocks("solid") if s.get("id") == "20")
    top = next(s for s in moved.blocks("side") if s.get("id") == "21")
    assert top.get("plane") != "(0 48 64) (48 48 32) (48 0 32)" and top.get("lightmapscale") == "8"
    assert top.get("smoothing_groups") == "1" and top.get("uaxis") == "[1 0 0 0] 0.25"
    kept = next(s for s in world.blocks("solid") if s.get("id") == "2")
    assert _tree([kept]) == _tree([vmf.parse(SMALL_VMF)[1].blocks("solid")[0]])
    assert world.blocks("hidden"), "the world's hidden blocks stay"


def test_vmf_import_copied_entities():
    # a copy of an imported point entity (Shift+D on a prop or a light) is built where it is, with the original's
    # settings; the original stays as imported. A brush entity's copy still isn't (its shape is in the original's parts)
    from hammerless.blender import vmfimport
    reset_scene()
    path = os.path.join(TMP, "small_copy.vmf")
    with open(path, "w", encoding="latin-1") as f:
        f.write(SMALL_VMF)
    vmfimport.import_text(bpy.context, SMALL_VMF, path)
    bpy.context.scene.hammerless.map_name = "test_map"
    upm = bpy.context.scene.hammerless.units_per_meter
    lamp = next(o for o in bpy.data.objects if o.get("hl_vmf_kind") == "entity" and o.hammerless.classname == "light")
    copy = lamp.copy()
    lamp.users_collection[0].objects.link(copy)
    copy.location.x += 100 / upm
    detail = next(o for o in bpy.data.objects if o.get("hl_vmf_kind") == "entity"
                  and o.hammerless.classname == "func_detail")
    dcopy = detail.copy()
    detail.users_collection[0].objects.link(dcopy)
    bpy.context.view_layer.update()
    blocks, log = export()
    assert blocks is not None, log
    lights = [b for b in blocks if b.name == "entity" and b.get("classname") == "light"]
    assert len(lights) == 2, [b.get("origin") for b in lights]
    orig = next(b for b in lights if b.get("id") == "30")
    assert orig.get("origin") == "8 16 48", orig.get("origin")
    new = next(b for b in lights if b.get("id") != "30")
    assert [round(float(c)) for c in new.get("origin").split()] == [108, 16, 48], new.get("origin")
    assert new.get("_light") == "255 240 200 300" and new.get("targetname") == "lamp"
    assert "copy of an imported brush entity" in log, log


def test_vmf_import_lightmap_scale():
    # an imported map's brushes keep their own lightmap scale unless Imported Brushes Too is on (then the scene's,
    # or the material's own)
    from hammerless.blender import vmfimport
    reset_scene()
    path = os.path.join(TMP, "small.vmf")
    with open(path, "w", encoding="latin-1") as f:
        f.write(SMALL_VMF)
    vmfimport.import_text(bpy.context, SMALL_VMF, path)
    s = bpy.context.scene.hammerless
    s.map_name = "test_map"
    s.lightmap_scale = 32

    def scales():
        world = next(b for b in _imported_export(SMALL_VMF) if b.name == "world")
        return {sd.get("lightmapscale") for sol in world.blocks("solid") for sd in sol.blocks("side")}
    assert scales() == {"16", "8"}, scales()                       # (as in the map)
    s.lightmap_scale_imported = True
    assert scales() == {"32"}, scales()
    bpy.data.materials["brick/brickwall01"].hammerless.lightmap_scale = 4
    assert scales() == {"32", "4"}, scales()


def test_vmf_import_sky():
    # an imported map's sky is the Sky setting: set from the map on import, written back from it
    from hammerless.blender import vmfimport
    reset_scene()
    path = os.path.join(TMP, "small.vmf")
    with open(path, "w", encoding="latin-1") as f:
        f.write(SMALL_VMF)
    vmfimport.import_text(bpy.context, SMALL_VMF, path)
    s = bpy.context.scene.hammerless
    s.map_name = "test_map"
    assert s.skyname == "sky_l4d_rural02_hdr", s.skyname
    assert _tree(_imported_export(SMALL_VMF)) == _tree(vmf.parse(SMALL_VMF))     # (unchanged: exactly as read)
    s.skyname = "sky_l4d_c1_2_hdr"
    world = next(b for b in _imported_export(SMALL_VMF) if b.name == "world")
    assert world.get("skyname") == "sky_l4d_c1_2_hdr"


def test_example_objects_in_map_collection():
    # an example's objects go into the map collection when there is one (outside it they aren't built: Slow
    # Motion Headshots' Time Scale entity was missing from an imported map, so its Start / Stop did nothing)
    from hammerless.blender.logic_examples import all_examples, build_example
    from hammerless.blender.mapcollection import map_collection
    from hammerless.core.compile import find_game_root
    real = find_game_root()
    if not (real and os.path.exists(os.path.join(real, "bin", "left4dead2.fgd"))):
        return
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.hammerless.add_entity(classname="info_survivor_position")
    bpy.ops.hammerless.organize_scene()
    coll = map_collection(bpy.context.scene)
    assert coll is not None
    i = next(k for k, e in enumerate(all_examples()) if e["title"] == "Slow Motion Headshots")
    problems = []
    build_example(bpy.context, i, problems)
    assert not problems, problems
    slowmo = next(o for o in bpy.data.objects if o.name.endswith("slowmo"))
    assert slowmo.name in coll.all_objects, [c.name for c in slowmo.users_collection]
    path = os.path.join(TMP, "build", "test_map.vmf")
    data, log = export()
    assert data is not None, log
    assert "func_timescale" in open(path, encoding="utf-8").read(), log


def test_logic_node_groups():
    # a Group node is built as the nodes inside it: wires in and out of it go straight to what's inside, a value
    # typed on it reaches the nodes inside, each copy has its own names ('This Group Copy' variables too), groups
    # can be inside groups, and a group graph isn't built on its own
    from hammerless.blender.logic import new_tree
    from hammerless.blender.logicgroups import flatten, new_group_tree, new_item, sync
    from hammerless.core.build import Report
    reset_scene()
    inner = new_group_tree("Add One")
    new_item(inner, "Run", "event", False, uid="run")
    new_item(inner, "Amount", "num", False, uid="amount")
    new_item(inner, "Then", "event", True, uid="then")
    gin, gout = inner.nodes.new("HL_NodeGroupInput"), inner.nodes.new("HL_NodeGroupOutput")
    sync(inner)
    add = inner.nodes.new("HL_NodeSetVariable")
    add.name, add.var_name, add.scope, add.op = "add", "count", "GROUP", "ADD"
    inner.links.new(gin.outputs["Run"], add.inputs["Run"])
    inner.links.new(gin.outputs["Amount"], add.inputs["Value"])
    inner.links.new(add.outputs["Then"], gout.inputs["Then"])
    outer = new_group_tree("Twice")
    new_item(outer, "Run", "event", False, uid="run")
    new_item(outer, "Done", "event", True, uid="done")
    oin, oout = outer.nodes.new("HL_NodeGroupInput"), outer.nodes.new("HL_NodeGroupOutput")
    sync(outer)
    a, b = outer.nodes.new("HL_NodeGroup"), outer.nodes.new("HL_NodeGroup")
    a.name, b.name = "first", "second"
    a.group = b.group = inner
    a.inputs["amount"].value, b.inputs["amount"].value = 2.0, 5.0
    outer.links.new(oin.outputs["run"], a.inputs["run"])
    outer.links.new(a.outputs["then"], b.inputs["run"])
    outer.links.new(b.outputs["then"], oout.inputs["done"])
    main = new_tree(bpy.context)
    start, g, msg = main.nodes.new("HL_NodeMapStart"), main.nodes.new("HL_NodeGroup"), main.nodes.new("HL_NodeMessage")
    g.name, msg.name = "twice", "msg"
    g.group = outer
    main.links.new(start.outputs[0], g.inputs["run"])
    main.links.new(g.outputs["done"], msg.inputs[0])
    rep = Report()
    flat = flatten(bpy.context, main, rep)
    ids = sorted(n.id for n in flat.nodes)
    assert ids == sorted(["msg", start.name, "twice/first/add", "twice/second/add"]), ids
    wires = sorted((l.from_node, l.from_socket, l.to_node, l.to_socket) for l in flat.links)
    assert wires == sorted([(start.name, "start", "twice/first/add", "run"),
                            ("twice/first/add", "then", "twice/second/add", "run"),
                            ("twice/second/add", "then", "msg", msg.inputs[0].identifier)]), wires
    by = {n.id: n for n in flat.nodes}
    assert by["twice/first/add"].consts["value"] == 2.0 and by["twice/second/add"].consts["value"] == 5.0
    assert by["twice/first/add"].settings["name"] == "twice/first/count", by["twice/first/add"].settings
    assert by["twice/first/add"].settings["scope"] == "MAP"
    from hammerless.blender.logic import logic_trees
    assert [t.name for t in logic_trees(bpy.context.scene)] == [main.name]
    assert inner.use_fake_user and outer.use_fake_user
    # a group can't go inside itself
    from hammerless.blender.logicgroups import _ancestors_ok
    assert not _ancestors_ok(inner, outer) and _ancestors_ok(outer, inner)
    # a group that passes its input straight to its output: one wire through it, not two
    through = new_group_tree("Through")
    new_item(through, "In", "event", False, uid="i")
    new_item(through, "Out", "event", True, uid="o")
    tin, tout = through.nodes.new("HL_NodeGroupInput"), through.nodes.new("HL_NodeGroupOutput")
    sync(through)
    through.links.new(tin.outputs["i"], tout.inputs["o"])
    main2 = new_tree(bpy.context)
    st, gt, ms = main2.nodes.new("HL_NodeMapStart"), main2.nodes.new("HL_NodeGroup"), main2.nodes.new("HL_NodeMessage")
    gt.name, ms.name = "pass", "msg"
    gt.group = through
    main2.links.new(st.outputs[0], gt.inputs["i"])
    main2.links.new(gt.outputs["o"], ms.inputs[0])
    flat2 = flatten(bpy.context, main2, Report())
    assert [(l.from_node, l.to_node) for l in flat2.links] == [(st.name, "msg")], flat2.links


def test_logic_example_groups_reused():
    # an example's node groups are made once: adding the example again uses the same groups
    from hammerless.blender.logic_examples import all_examples, build_example
    reset_scene()
    i = next(k for k, e in enumerate(all_examples()) if e["title"] == "Flashlight Battery")
    problems = []
    build_example(bpy.context, i, problems)
    build_example(bpy.context, i, problems)
    assert not problems, problems
    groups = sorted(t.name for t in bpy.data.node_groups if getattr(t, "is_group", False))
    assert groups == ["Flashlight Battery", "Flashlight Is On", "Flashlight Off"], groups
    # one edited since: adding the example again makes a fresh copy of that one, the others are used again
    bpy.data.node_groups["Flashlight Off"].nodes["bit"].inputs["b"].value = 8.0
    build_example(bpy.context, i, problems)
    groups = sorted(t.name for t in bpy.data.node_groups if getattr(t, "is_group", False))
    assert groups == ["Flashlight Battery", "Flashlight Battery.001", "Flashlight Is On", "Flashlight Off",
                      "Flashlight Off.001"], groups


def test_animated_custom_model():
    # a Custom Model with an armature is built as an animated model: its deform bones, its vertex weights, every
    # action on its NLA tracks an animation (Cyclic: looping), a bone-parented empty an attachment; the prop is a
    # prop_dynamic playing 'idle' on its own; the scene's frame and the rig's action are left as they were
    from hammerless.blender import ops
    from hammerless.blender.examplemodels import make_pod
    s = reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.hammerless.add_entity(classname="info_survivor_position")
    pod = make_pod(bpy.context, "Pod", (2.0, 0.0, 0.0), bpy.context.scene.collection)
    rig = pod.parent
    tip = bpy.data.objects.new("tip", None)
    bpy.context.scene.collection.objects.link(tip)
    tip.parent, tip.parent_type, tip.parent_bone = rig, "BONE", "top"
    bpy.context.scene.frame_set(7)
    ir, text, rep = ops.build_map_text(bpy.context, None)
    assert text is not None, rep.errors
    specs = [sp for sp in ir.models.values() if sp.skeleton is not None]
    assert len(specs) == 1, list(ir.models)
    sk = specs[0].skeleton
    assert [b.name for b in sk.bones] == ["root", "top", "side_px", "side_nx", "side_py", "side_ny"], sk.bones
    assert [(a.name, a.loop, len(a.frames)) for a in sk.animations] == [("idle", True, 40), ("swell", False, 61)]
    assert [a.name for a in sk.attachments] == ["tip"]
    assert len(sk.weights) == len(specs[0].triangles)
    # the top bone moves 0.45 m in 'swell' (first frame to last), in Hammer units (relative to its parent)
    a, b = sk.animations[1].frames[0][1][0], sk.animations[1].frames[-1][1][0]
    moved = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
    assert abs(moved - 0.45 * s.units_per_meter) < 0.5, (moved, a, b)
    props = [e for e in ir.entities if e.classname == "prop_dynamic"]
    assert props and props[0].keyvalues.get("DefaultAnim") == "idle", props
    assert bpy.context.scene.frame_current == 7 and rig.animation_data.action is None
    assert rig.data.pose_position == "POSE"
    from hammerless.core import models as m
    qc = m.qc_text(specs[0])
    assert "$staticprop" not in qc and '$sequence "idle"' in qc and '$sequence "swell"' in qc, qc
    # the rig scaled 2x (the mesh with it), left in Rest Position, a bone posed by hand: bones scale like the
    # vertices (once), the actions still animate, and the rig is left as it was
    top_rest = sum(c * c for c in sk.bones[1].pos) ** 0.5
    rig.scale = (2.0, 2.0, 2.0)
    rig.data.pose_position = "REST"
    rig.pose.bones["side_px"].location = (0.0, 0.1, 0.0)
    bpy.context.view_layer.update()
    ir, text, rep = ops.build_map_text(bpy.context, None)
    sk2 = next(sp for sp in ir.models.values() if sp.skeleton is not None).skeleton
    top2 = sum(c * c for c in sk2.bones[1].pos) ** 0.5
    assert abs(top2 - 2 * top_rest) < 0.05, (top_rest, top2)
    a, b = sk2.animations[1].frames[0][1][0], sk2.animations[1].frames[-1][1][0]
    moved = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
    assert abs(moved - 2 * 0.45 * s.units_per_meter) < 1.0, moved
    assert rig.data.pose_position == "REST"
    assert abs(rig.pose.bones["side_px"].location.y - 0.1) < 1e-6, rig.pose.bones["side_px"].location
    assert rig.pose.bones["top"].location.length < 1e-6


def _needs_game_models(ex) -> bool:
    """An example whose objects are built from the game's own models (a game skeleton): the tests' fake game folder
    has none of those."""
    return any(str(spec[0]).startswith("MANNEQUIN:") for spec in (ex.get("objects") or {}).values())


def test_game_skeleton_example():
    # Reskinned Commons, with the real game's files: a game skeleton (the common's 33 bones, standing) and a
    # mannequin weighted to it; built, the model is in the game's space with the commons' animation libraries in
    # their order, their 23 hitboxes and every bone kept; the prop plays 'Idle', turned a quarter from the object
    from hammerless.blender.logic_examples import all_examples, build_example
    from hammerless.blender import ops
    from hammerless.core.compile import find_game_root
    real = find_game_root()
    if not real or not os.path.exists(os.path.join(real, "left4dead2", "pak01_dir.vpk")):
        print("SKIP (no game here)")
        return
    s = reset_scene()
    s.game_root = real
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.hammerless.add_entity(classname="info_survivor_position")
    i = next(k for k, e in enumerate(all_examples()) if e["title"] == "Reskinned Commons")
    problems = []
    build_example(bpy.context, i, problems)
    assert not problems, problems
    man = bpy.data.objects["Example %02d mannequin" % (i + 1)]
    arm = man.find_armature()
    assert arm is not None and arm["hl_game_model"].endswith("common_male_tshirt_cargos.mdl")
    assert len(arm.data.bones) == 33
    head = arm.data.bones["ValveBiped.Bip01_Head1"].head_local.z * s.units_per_meter
    assert 50 < head < 70, head                                  # (standing, not the lying stored rest pose)
    ir, text, rep = ops.build_map_text(bpy.context, real)
    assert text is not None, rep.errors
    sk = next(sp for sp in ir.models.values() if sp.skeleton is not None).skeleton
    assert sk.valve_space and sk.includes == ["infected/anim_common.mdl", "infected/anim_common_vomit.mdl",
                                              "infected/anim_common_male_exp.mdl"], sk.includes
    assert len(sk.bones) == 33 and len(sk.hitboxes) == 23 and sk.default_animation == "Idle"
    prop = next(e for e in ir.entities if e.classname == "prop_dynamic")
    assert prop.keyvalues.get("DefaultAnim") == "Idle" and abs(prop.angles[1] + 90) < 0.01, (prop.keyvalues, prop.angles)
    # a game animation loaded to look at moves the bones as the game would, and isn't built (the game has it)
    from hammerless.blender.gameskeleton import load_game_animation
    act, why = load_game_animation(bpy.context, arm, "Walk_Neutral_01")
    assert act is not None, why
    bpy.context.scene.frame_set(10)
    head = arm.pose.bones["ValveBiped.Bip01_Head1"].matrix.translation.z * s.units_per_meter
    assert 50 < head < 70, head
    ir, text, rep = ops.build_map_text(bpy.context, real)
    sk = next(sp for sp in ir.models.values() if sp.skeleton is not None).skeleton
    assert sk.animations == [], [a.name for a in sk.animations]
    # the game's own mesh on it: standing like the skeleton (skinned from the stored rest pose), weighted to its
    # bones, with the game's materials; its materials are used as they are (a patch including the original)
    from hammerless.blender.gameskeleton import import_game_mesh
    mesh_obj, why = import_game_mesh(bpy.context, arm)
    assert mesh_obj is not None, why
    assert len(mesh_obj.data.vertices) == 2404, len(mesh_obj.data.vertices)
    zs = [v.co.z * s.units_per_meter for v in mesh_obj.data.vertices]
    assert min(zs) > -5 and 60 < max(zs) < 80, (min(zs), max(zs))           # (standing on the floor)
    assert "ValveBiped.Bip01_Head1" in mesh_obj.vertex_groups
    assert any(m.hammerless.source_material.startswith("models/infected/common/") for m in mesh_obj.data.materials)


def test_organize_scene():
    # Organize Scene: the map collection named after the map, sorted by kind; the map builds the same; then only
    # what's inside it is built; renaming the map renames it
    from hammerless.blender.mapcollection import map_collection
    s = reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    add_box("wall", (0.5, 10, 3), (5, 0, 1.5))
    group = bpy.data.collections.new("my details")                # (Detail set on a collection: kept on the move)
    bpy.context.scene.collection.children.link(group)
    pillar = add_box("pillar", (0.5, 0.5, 3), (2, 2, 1.5))
    for c in list(pillar.users_collection):
        c.objects.unlink(pillar)
    group.objects.link(pillar)
    group.hammerless.brush_detail = "DETAIL"
    bpy.ops.hammerless.add_entity(classname="info_survivor_position")
    bpy.ops.hammerless.add_entity(classname="weapon_first_aid_kit_spawn")
    bpy.ops.object.light_add(type="POINT", location=(1, 1, 2))
    bpy.ops.object.camera_add(location=(0, -5, 2))
    cam = bpy.context.object
    before, log = export()
    assert before is not None, log
    assert map_collection(bpy.context.scene) is None
    assert "Organize Scene" in log
    bpy.ops.hammerless.organize_scene()
    coll = map_collection(bpy.context.scene)
    assert coll is not None and coll.name == "test_map"
    kids = {c.get("hl_category"): c for c in coll.children}
    assert {"World", "Detail", "Lights", "Entities"} <= set(kids), set(kids)
    assert bpy.data.objects["floor"].name in kids["World"].objects
    assert "pillar" in kids["Detail"].objects and bpy.data.objects["pillar"].hammerless.brush_detail == "DETAIL"
    assert cam.name not in coll.all_objects                       # (not built: stays outside)
    assert not bpy.data.collections["my details"].objects        # (emptied; kept: it has a Detail setting)
    after, log = export()
    assert after is not None, log
    assert _tree(before) == _tree(after), "organizing changed the map"
    # outside the map collection: not built
    wall = bpy.data.objects["wall"]
    for c in list(wall.users_collection):
        c.objects.unlink(wall)
    bpy.context.scene.collection.objects.link(wall)
    out, log = export()
    assert len(world_solids(out)) == len(world_solids(after)) - 1
    assert "outside the map collection" in log and "wall" in log
    s.map_name = "renamed_map"
    assert coll.name == "renamed_map"


def test_new_objects_sorted():
    # after Organize Scene, what's added goes where its kind goes (even when added outside the map); a change of
    # kind moves it; objects in your own collections inside the map, cameras, and renames are left alone
    from hammerless.blender import mapcollection as mc
    reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    bpy.ops.hammerless.organize_scene()
    coll = mc.map_collection(bpy.context.scene)

    def settle():
        bpy.context.view_layer.update()
        mc._apply_pending()                                        # (the timer doesn't run in background mode)

    def where(o):
        return {c.get("hl_category") or c.name for c in o.users_collection}
    settle()
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection   # (outside the map)
    wall = add_box("wall", (0.5, 10, 3), (5, 0, 1.5))
    bpy.ops.hammerless.add_entity(classname="weapon_first_aid_kit_spawn")
    kit = bpy.context.object
    bpy.ops.object.light_add(type="POINT", location=(1, 1, 2))
    lamp = bpy.context.object
    bpy.ops.object.camera_add(location=(0, -5, 2))
    cam = bpy.context.object
    settle()
    assert where(wall) == {"World"}, where(wall)
    assert where(kit) == {"Items"}, where(kit)
    assert where(lamp) == {"Lights"}, where(lamp)
    assert cam.name not in coll.all_objects                       # (not built)
    bpy.ops.hammerless.add_preset(preset="START_SAFE_ROOM", landmark="landmark_0")
    settle()
    room = next(o for o in bpy.data.objects if o.hammerless.preset == "START_SAFE_ROOM")
    assert where(room) == {"Prefabs"}, where(room)
    assert all(where(c) == {"Prefabs"} for c in room.children_recursive)      # (its parts with it)
    # a change of kind: a brush becomes a trigger
    wall.hammerless.role, wall.hammerless.classname = "BRUSH_ENTITY", "trigger_once"
    wall.update_tag()
    settle()
    assert where(wall) == {"Brush Entities"}, where(wall)
    # your own collection inside the map: kept; renaming isn't "new"
    mine = bpy.data.collections.new("my stuff")
    coll.children.link(mine)
    for c in list(kit.users_collection):
        c.objects.unlink(kit)
    mine.objects.link(kit)
    settle()
    kit.name = "renamed kit"
    kit.update_tag()
    settle()
    assert where(kit) == {"my stuff"}, where(kit)


def test_bake_area_choices():
    # Lighting's Bake choice: the whole map, the view or the selection (volumes aren't choices: every bake uses them)
    s = reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    vol = add_box("test area", (2, 2, 2), (0, 0, 1))
    vol.hammerless.role, vol.hammerless.classname = "BRUSH_ENTITY", "hammerless_no_bake"
    kv = vol.hammerless.keyvalues.add()
    kv.key, kv.value = "invert", "1"
    skip = add_box("under", (2, 2, 2), (5, 5, -3))
    skip.hammerless.role, skip.hammerless.classname = "BRUSH_ENTITY", "hammerless_no_bake"
    from hammerless.blender.props import _bake_area_items
    ids = [it[0] for it in _bake_area_items(s, bpy.context)]
    assert ids == ["MAP", "VIEW", "SELECTED"], ids
    # Selected Objects: the selection's bounds, turned with it
    from hammerless.blender.ops import _selection_volume
    from hammerless.core.lightvolumes import in_volume
    for o in bpy.context.selected_objects:
        o.select_set(False)
    floor = bpy.data.objects["floor"]
    floor.select_set(True)
    sel = _selection_volume(bpy.context)
    upm = s.units_per_meter
    assert sel.startswith("1 6 ")
    assert not in_volume(sel, (0, 0, -0.25 * upm))              # (inside the floor's box: baked)
    assert in_volume(sel, (0, 0, 3 * upm))                      # (above it: kept)
    assert s.bake_area == "MAP"
    s.bake_area_name = "VOL:test area"                           # (a file saved when volumes were choices)
    assert s.bake_area == "MAP"


def test_texture_read_back():
    # a painted face reads back the alignment it was painted with: mirrored and tilted objects, World and Face
    from hammerless.blender import texturing
    from hammerless.core.texalign import Alignment
    s = reset_scene()
    ctx = bpy.context
    texturing.set_active(ctx, "brick/brick_ext_01")
    t = ctx.scene.hl_tex
    for name, scale, rot in (("plain", (1, 1, 1), (0, 0, 0)), ("mirrored", (-1, 1, 1), (0, 0, 0)),
                             ("mirrored_tilted", (1, -1, 1), (0.4, 0.3, 0))):
        ob = add_box(name, (2, 2, 2), (0, 0, 1))
        ob.scale, ob.rotation_euler = scale, rot
        for mode in ("WORLD", "FACE"):
            texturing.paint_faces(ctx, ob, al=Alignment(0.5, 0.25, 12, 40, 30, mode))
            t.mode = mode
            for i in range(len(ob.data.polygons)):
                texturing.read_face(ctx, ob, i)
                got = (round(t.scale_u, 3), round(t.scale_v, 3), round(t.shift_u % 256, 2) % 256,
                       round(t.shift_v % 256, 2) % 256, round(t.rotation % 360, 2))
                assert got == (0.5, 0.25, 12.0, 40.0, 30.0), (name, mode, i, got)


def test_texture_painting():
    # painted faces export with their own axes (from their UVs); the rest stay world-aligned; Fit; Replace;
    # an imported map's painted face writes its new axes on its original side
    import bmesh
    from hammerless.blender import texturing
    from hammerless.core.texalign import Alignment, axes
    s = reset_scene()
    box = add_box("box", (2, 2, 2), (0, 0, 1))
    bpy.context.view_layer.update()
    t = bpy.context.scene.hl_tex
    t.active = "concrete/concretefloor001a"
    mat = texturing.material_for(t.active)
    # the face pointing +x
    upm = s.units_per_meter
    face = next(p.index for p in box.data.polygons if (box.matrix_world.to_3x3() @ p.normal).x > 0.9)
    texturing.paint_faces(bpy.context, box, [face], mat, Alignment(0.5, 0.5, 16, 8, 90, "WORLD"))
    blocks, log = export()
    assert blocks is not None, log
    sides = [sd for sol in world_solids(blocks) for sd in sol.blocks("side")]
    painted = [sd for sd in sides if sd.get("material", "").lower() == "concrete/concretefloor001a"]
    assert len(painted) == 1, [sd.get("material") for sd in sides]
    poly = box.data.polygons[face]
    pts = [tuple(c * upm for c in (box.matrix_world @ box.data.vertices[v].co)) for v in poly.vertices]
    (u, us, uscale), (v, vs, vscale) = axes(pts, Alignment(0.5, 0.5, 16, 8, 90, "WORLD"))
    from hammerless.core.vmfimport import parse_axis
    pu, pus, pusc = parse_axis(painted[0].get("uaxis"))
    pv, pvs, pvsc = parse_axis(painted[0].get("vaxis"))
    assert all(abs(pu[i] / pusc - u[i] / uscale) < 1e-3 for i in range(3)), (pu, pusc, u, uscale)
    assert abs(pus - us) < 0.05 and abs(pvs - vs) < 0.05, (pus, pvs)
    others = [sd for sd in sides if sd is not painted[0]]
    assert all(sd.get("uaxis").endswith("] 0.25") and " 0]" in sd.get("uaxis") for sd in others)  # (world-aligned)
    # Fit: one copy covers the face
    bpy.context.scene["hl_tex_last_obj"], bpy.context.scene["hl_tex_last_face"] = box.name, face
    bpy.ops.hammerless.tex_justify(how="FIT")
    assert abs(t.scale_u - 2 * upm / 512) < 1e-3, t.scale_u          # (a 2 m face, a 512-texel texture)
    # Replace in Map
    t.replace_from = "concrete/concretefloor001a"
    t.active = "dev/dev_measuregeneric01b"
    bpy.ops.hammerless.tex_replace()
    assert any((sl.material.hammerless.source_material or "") == "dev/dev_measuregeneric01b" for sl in box.material_slots)
    # an imported map: the painted face's side gets the new axes, the rest stay exactly as read
    from hammerless.blender import vmfimport
    reset_scene()
    path = os.path.join(TMP, "small.vmf")
    with open(path, "w", encoding="latin-1") as f:
        f.write(SMALL_VMF)
    vmfimport.import_text(bpy.context, SMALL_VMF, path)
    bpy.context.scene.hammerless.map_name = "test_map"
    brush = next(o for o in bpy.data.objects if o.get("hl_vmf_id") == 20)
    texturing.paint_faces(bpy.context, brush, [0], None, Alignment(0.5, 0.5, 4, 4, 0, "WORLD"))
    out = _imported_export(SMALL_VMF)
    world = next(b for b in out if b.name == "world")
    solid = next(sl for sl in world.blocks("solid") if sl.get("id") == "20")
    changed = [sd for sd in solid.blocks("side") if "0.5" in sd.get("uaxis", "")]
    assert len(changed) == 1, [sd.get("uaxis") for sd in solid.blocks("side")]
    kept = next(sl for sl in world.blocks("solid") if sl.get("id") == "2")
    assert _tree([kept]) == _tree([vmf.parse(SMALL_VMF)[1].blocks("solid")[0]])


def test_control_volumes():
    # older No Nav / No Bake volumes become Control Volumes doing the same; the toggles set its keyvalues; one set to
    # Light Baking, Only inside, is a Bake choice
    from hammerless.blender.mapcollection import convert_old_volumes
    from hammerless.blender.props import _bake_area_items
    s = reset_scene()
    add_box("floor", (10, 10, 0.5), (0, 0, -0.25))
    cut = add_box("old cut", (2, 2, 2), (3, 0, 1))
    cut.hammerless.role, cut.hammerless.classname = "BRUSH_ENTITY", "hammerless_nav_cut"
    nb = add_box("old bake", (2, 2, 2), (-3, 0, 1))
    nb.hammerless.role, nb.hammerless.classname = "BRUSH_ENTITY", "hammerless_no_bake"
    kv = nb.hammerless.keyvalues.add()
    kv.key, kv.value = "invert", "1"
    assert convert_old_volumes() == 2

    def values(o):
        return {k.key: k.value for k in o.hammerless.keyvalues}
    assert cut.hammerless.classname == "hammerless_control"
    assert values(cut) == {"enabled": "1", "mode": "EXCLUDE", "nav": "1", "light": "0", "vis": "0", "sound": "0",
                           "spawns": "0"}
    assert values(nb)["mode"] == "ONLY" and values(nb)["light"] == "1" and values(nb)["nav"] == "0"
    assert values(nb)["enabled"] == "1" and values(cut)["enabled"] == "1"     # (converted: on)
    assert not any(it[0].startswith("VOL:") for it in _bake_area_items(s, bpy.context))
    # the toggles
    bpy.context.view_layer.objects.active = cut
    bpy.ops.hammerless.kv_set(key="vis", value="1")
    assert values(cut)["vis"] == "1"
    blocks, log = export()
    assert blocks is not None, log
    assert not entities(blocks, "hammerless_control")               # (never written to the map)


def test_register_cycle():
    # the add-on can be disabled and enabled again (an update does that): every module unregisters cleanly
    hammerless.unregister()
    hammerless.register()
    assert hasattr(bpy.types.Scene, "hl_tex") and hasattr(bpy.types.Scene, "hammerless")


def test_sky_dropdown():
    # the sky dropdown lists the game's skies then Custom; it sets the map's skyname, Custom keeps a typed one
    from hammerless.blender.props import _sky_choice_items
    reset_scene()
    s = bpy.context.scene.hammerless
    items = _sky_choice_items(s, bpy.context)
    assert items[0][0] == "CUSTOM"
    if len(items) < 3:
        return                                                     # (no game here: nothing to pick from)
    s.sky_choice = items[2][0]
    assert s.skyname == items[2][0] and s.sky_choice == items[2][0]
    s.sky_choice = "CUSTOM"
    assert s.sky_custom and s.skyname == items[2][0]               # (typed below the dropdown)
    s.skyname = "my_own_sky"
    assert s.sky_choice == "CUSTOM"
    s.sky_custom = False
    assert s.sky_choice == "CUSTOM"                                # (a name the game doesn't have)


def test_light_patterns():
    # a light's Pattern / Switchable / Starts Off become the game's keys; logic sees a Blender light as an entity
    from hammerless.blender.extract import light_entity
    from hammerless.blender.logic import _object_class
    reset_scene()

    def lamp(name, kind="POINT", **settings):
        o = bpy.data.objects.new(name, bpy.data.lights.new(name, kind))
        bpy.context.scene.collection.objects.link(o)
        for k, v in settings.items():
            setattr(o.hammerless, k, v)
        return o

    kv = light_entity(lamp("Plain"), 52.49).keyvalues
    assert not {"style", "pattern", "targetname", "spawnflags"} & set(kv), kv
    kv = light_entity(lamp("Tube", light_pattern="10"), 52.49).keyvalues
    # (a Pattern is a preset Behaviour: the lights of one preset share its name, no game style key)
    assert "style" not in kv and kv["targetname"].startswith("hl_lightpattern_"), kv
    kv = light_entity(lamp("Hall Lamp", light_switchable=True, light_pattern="3"), 52.49).keyvalues
    assert kv["targetname"] == "hl_light_hall_lamp" and "style" not in kv, kv    # (switchable: its own name)
    kv = light_entity(lamp("Siren", "SPOT", light_pattern="CUSTOM", light_custom="az Z9m"), 52.49).keyvalues
    assert kv["pattern"] == "azzm" and kv["targetname"] == "hl_light_siren", kv
    kv = light_entity(lamp("Dark", light_start_off=True), 52.49).keyvalues
    assert kv["spawnflags"] == "1" and kv["targetname"] == "hl_light_dark", kv
    named = lamp("Named", light_switchable=True)
    k = named.hammerless.keyvalues.add()
    k.key, k.value = "targetname", "my_lamp"
    assert light_entity(named, 52.49).keyvalues["targetname"] == "my_lamp"   # (its own name wins)
    assert _object_class(named) == "light"
    assert _object_class(lamp("Spot", "SPOT")) == "light_spot"
    assert _object_class(lamp("Sun", "SUN")) == ""


def test_keyvalue_colors():
    # a light entity's "_light" shown as a colour swatch and a brightness: both write the key's text
    reset_scene()
    o = bpy.data.objects.new("e", None)
    bpy.context.scene.collection.objects.link(o)
    kv = o.hammerless.keyvalues.add()
    kv.key, kv.value = "_light", "255 240 220 30"
    assert abs(kv.color[1] - 240 / 255) < 1e-4 and kv.brightness == 30
    kv.color = (1.0, 0.5, 0.0)
    assert kv.value == "255 128 0 30", kv.value
    kv.brightness = 120
    assert kv.value == "255 128 0 120", kv.value
    k2 = o.hammerless.keyvalues.add()
    k2.key, k2.value = "rendercolor", "10 20 30"
    k2.color = (0.0, 1.0, 0.0)
    assert k2.value == "0 255 0", k2.value


def test_node_headers_show_type():
    # a logic node's header starts with its type: "Text Check: A pod?", never just an example's title
    from hammerless.blender.logic import migrate_labels, new_tree
    reset_scene()
    tree = new_tree(bpy.context)
    n = tree.nodes.new("HL_NodeTextCheck")
    n.note = "A pod?"
    assert n.draw_label() == "Text Check: A pod?", n.draw_label()
    old = tree.nodes.new("HL_NodeCompare")
    old.label = "Still waiting?"                       # (a file from before: the title in Blender's Label)
    ev = tree.nodes.new("HL_NodeOverride")
    ev.label = "Override: AllowTakeDamage"
    migrate_labels(tree)
    assert old.label == "" and old.draw_label() == "Compare: Still waiting?", old.draw_label()
    assert ev.label == "" and ev.draw_label() == "Override: AllowTakeDamage", ev.draw_label()


def test_entity_property_nodes():
    # Get / Set Entity Property: sockets follow the property's type from the game's list (an Index for a list),
    # the node says the type for a name the list doesn't have, and the picker lists described ones as Common
    from hammerless.blender.logic import _prop_items, new_tree
    reset_scene()
    tree = new_tree(bpy.context)
    get = tree.nodes.new("HL_NodeGetProperty")
    get.key = "m_iAmmo"
    assert [s.identifier for s in get.inputs] == ["entity", "index"], [s.identifier for s in get.inputs]
    assert get.draw_label() == "Get Entity Property: m_iAmmo", get.draw_label()
    put = tree.nodes.new("HL_NodeSetProperty")
    put.key = "m_flModelScale"
    assert [s.identifier for s in put.inputs] == ["run", "entity", "value"], [s.identifier for s in put.inputs]
    assert put.inputs["value"].bl_idname == "HL_FloatSocket", put.inputs["value"].bl_idname
    put.key = "m_clrRender"                         # (a whole number: an exact input, colours with alpha in billions)
    assert put.inputs["value"].bl_idname == "HL_IntSocket", put.inputs["value"].bl_idname
    put.inputs["value"].value = 1191182335
    assert put.to_lnode(bpy.context).consts["value"] == 1191182335
    put.key = "m_hViewEntity"
    assert put.inputs["value"].bl_idname == "HL_ThingSocket", put.inputs["value"].bl_idname
    put.key = "m_nNotInTheList"
    put.prop_type, put.list = "vector", True
    assert [s.identifier for s in put.inputs] == ["run", "entity", "index", "value"], [s.identifier for s in put.inputs]
    assert put.inputs["value"].bl_idname == "HL_VectorSocket", put.inputs["value"].bl_idname

    class Ctx:                                         # (the picker reads the node's For through the editor)
        space_data = None
    _prop_items.cache = {}
    items = _prop_items(type("Op", (), {"node_name": ""})(), Ctx())
    names = [i[0] for i in items]
    assert "m_iHealth" in names and len(names) > 2000, len(names)
    assert next(i for i in items if i[0] == "m_flModelScale")[1].startswith("Common:"), "described ones are Common"


def test_example_groups_dont_pile_up():
    # adding an example, deleting its nodes and adding it again leaves one copy of each group, no '.001's, and
    # Remove Unused Groups clears groups nothing uses (a group you made and kept goes only with that button)
    from hammerless.blender.logic_examples import all_examples, build_example, main_tree
    from hammerless.blender.logicgroups import new_group_tree, remove_unused_groups
    reset_scene()
    i = next(k for k, e in enumerate(all_examples()) if e["title"] == "Swelling Pod")
    probs = []
    build_example(bpy.context, i, probs)
    assert not probs, probs
    names = sorted(g.name for g in bpy.data.node_groups if getattr(g, "is_group", False))
    tree = main_tree(bpy.context)
    for n in list(tree.nodes):
        tree.nodes.remove(n)
    build_example(bpy.context, i, probs)
    again = sorted(g.name for g in bpy.data.node_groups if getattr(g, "is_group", False))
    assert again == names and not any(".0" in n for n in again), (names, again)
    for t in [t for t in bpy.data.node_groups if t.bl_idname == "HL_LogicTree" and not t.is_group]:
        for n in list(t.nodes):
            t.nodes.remove(n)
    mine = new_group_tree("My Group")                  # (kept: a fake user)
    remove_unused_groups(examples_only=True)
    left = sorted(g.name for g in bpy.data.node_groups if getattr(g, "is_group", False))
    assert left == ["My Group"], left
    stale = new_group_tree("Explosion", keep=False)
    stale["hl_example_group"] = "x"
    remove_unused_groups()
    assert not [g for g in bpy.data.node_groups if getattr(g, "is_group", False)], "unused groups left"
    del mine


def test_lights_are_blender_lights():
    # the Add panel's lights are Blender lights; older light entities become Blender lights giving the same game light
    import math
    from hammerless.blender.extract import light_entity
    from hammerless.blender.lamps import convert_light_entities
    s = reset_scene()
    for key, kind in (("light", "POINT"), ("light_spot", "SPOT"), ("light_environment", "SUN")):
        assert bpy.ops.hammerless.spawn_item(item="entity:" + key) == {"FINISHED"}
        o = bpy.context.object
        assert o.type == "LIGHT" and o.data.type == kind, (key, o.type)
    upm = s.units_per_meter
    olds = {}
    for cls, kv, rot in (("light", {"_light": "255 128 64 300", "style": "10", "spawnflags": "1",
                                    "targetname": "lamp_a", "_quadratic_attn": "1"}, (0, 0, 0)),
                         ("light_spot", {"_light": "200 220 255 650", "_cone": "40", "_inner_cone": "25"},
                          (0, math.radians(35), math.radians(70)))):
        assert bpy.ops.hammerless.add_entity(classname=cls) == {"FINISHED"}
        o = bpy.context.object
        o.rotation_euler = rot
        o.hammerless.keyvalues.clear()
        for k, v in kv.items():
            item = o.hammerless.keyvalues.add()
            item.key, item.value = k, v
        bpy.context.view_layer.update()
        from hammerless.blender.extract import source_angles
        olds[o.name] = (cls, kv, tuple(round(a, 3) for a in source_angles(o.matrix_world)))
    tree = bpy.data.node_groups.new("lamp logic", "HL_LogicTree")
    node = tree.nodes.new("HL_NodeObject")
    first = next(iter(olds))
    node.target = bpy.data.objects[first]
    assert convert_light_entities() == 2
    for name, (cls, kv, angles) in olds.items():
        new = bpy.data.objects[name]
        assert new.type == "LIGHT", name
        e = light_entity(new, upm)
        assert e.classname == cls, (e.classname, cls)
        got = [int(v) for v in e.keyvalues["_light"].split()]
        want = [int(v) for v in kv["_light"].split()]
        assert all(abs(g - w) <= 1 for g, w in zip(got, want)), (got, want)
        assert all(abs(a - b) < 0.05 or abs(abs(a - b) - 360) < 0.05 for a, b in zip(e.angles, angles)) or cls == "light",             (e.angles, angles)
        if cls == "light":
            from hammerless.blender.lightpresets import preset_slot
            assert new.hammerless.light_pattern == "10" and new.hammerless.light_start_off
            assert preset_slot(new).graph.get("hl_light_preset") == "10"         # (its pattern: a Behaviour)
            assert "style" not in e.keyvalues and e.keyvalues.get("spawnflags") == "1"
            assert e.keyvalues.get("targetname") == "lamp_a" and e.keyvalues.get("_quadratic_attn") == "1"
        else:
            assert abs(float(e.keyvalues["_cone"]) - 40) <= 1 and abs(float(e.keyvalues["_inner_cone"]) - 25) <= 1,                 e.keyvalues
    assert node.target is bpy.data.objects[first] and node.target.type == "LIGHT"     # (logic follows it)
    assert convert_light_entities() == 0                                              # (once)


def test_make_collision_modes():
    # Make Collision: brushes as one object, or invisible static props of <= 20 pieces; pressing again replaces
    # the last result (either mode) and keeps a material set by hand on it
    import bmesh
    from mathutils import Matrix
    from hammerless.blender.collisiontool import OF, MAX_PROP_PIECES
    from hammerless.blender.ops import game_material
    from hammerless.core.models import write_model_materials
    reset_scene()
    bm = bmesh.new()
    for i in range(8):                              # 64 separate boxes: one brush each
        for j in range(8):
            bmesh.ops.create_cube(bm, size=0.5, matrix=Matrix.Translation((i * 1.2, j * 1.2, 0)))
    me = bpy.data.meshes.new("boxes")
    bm.to_mesh(me)
    house = bpy.data.objects.new("Boxes", me)
    bpy.context.collection.objects.link(house)
    house.hammerless.role = "MODEL"
    house.hammerless.model_collision = "NONE"
    house.hammerless.collision_min_area = 0.0

    def press():
        bpy.context.view_layer.objects.active = house
        assert bpy.ops.hammerless.make_collision() == {"FINISHED"}
        return [o for o in bpy.data.objects if o.get(OF) == house.name]

    b = press()
    assert len(b) == 1 and b[0].hammerless.role == "BRUSH", [o.name for o in b]
    faces = len(b[0].data.polygons)
    assert faces == 64 * 6, faces
    assert b[0].parent == house and b[0].users_collection[0] == house.users_collection[0]
    v0 = b[0].matrix_world @ b[0].data.vertices[0].co
    house.location.x += 3.0                         # (parented: it moves with the mesh)
    bpy.context.view_layer.update()
    v1 = b[0].matrix_world @ b[0].data.vertices[0].co
    assert abs(v1.x - v0.x - 3.0) < 1e-5, (v0, v1)
    house.location.x -= 3.0
    bpy.context.view_layer.update()
    mat = game_material("tools/toolsinvisible")
    b[0].data.materials[0] = mat
    house.hammerless.collision_mode = "PROPS"
    p = press()
    assert len(p) == 4 and all(len(o.data.polygons) <= MAX_PROP_PIECES * 6 for o in p), [o.name for o in p]
    assert sum(len(o.data.polygons) for o in p) == faces
    assert all(o.hammerless.role == "MODEL" and o.hammerless.model_kind == "STATIC"
               and o.hammerless.model_collision == "HULLS" and o.data.materials[0] == mat for o in p)
    # Apply to Selected on one collision prop (a Custom Model) gives the whole set the texture; pressing again keeps it
    from hammerless.blender.texturing import paint_faces, selected_targets
    nd = game_material("tools/toolsnodraw")
    for o in p:
        o.data.materials[0] = nd
    bpy.ops.object.select_all(action="DESELECT")
    p[0].select_set(True)
    targets = selected_targets(bpy.context)
    assert len(targets) == 4, targets
    for o, f in targets:
        paint_faces(bpy.context, o, f, mat)
    assert all(list(o.data.materials) == [mat] for o in p), [list(o.data.materials) for o in p]
    house.hammerless.collision_mode = "BRUSHES"
    b = press()
    assert len(b) == 1 and b[0].data.materials[0] == mat
    # Blocks Vis: world brushes; with props, a Vis Only copy too (exported as func_hl_visblock, not in the world)
    house.hammerless.collision_blocks_vis = True
    b = press()
    assert len(b) == 1 and b[0].hammerless.brush_detail == "WORLD"
    house.hammerless.collision_mode = "PROPS"
    p = press()
    vis = [o for o in p if o.hammerless.brush_detail == "VIS"]
    props = [o for o in p if o.hammerless.role == "MODEL"]
    assert len(vis) == 1 and len(props) == 4 and all(o.data.materials[0] == mat for o in props), [o.name for o in p]
    assert vis[0].data.materials[0] != mat
    doc, log = export()
    assert doc is not None, log
    ents = [e for e in doc if e.name == "entity" and e.get("classname") == "func_hl_visblock"]
    assert len(ents) == 1 and len(ents[0].blocks("solid")) == 64, len(ents)
    world = next(e for e in doc if e.name == "world")
    assert len(world.blocks("solid")) == 6, len(world.blocks("solid"))      # (only the automatic seal's)
    # collision props get one leaf from our compiler only while they're nodraw (toolsinvisible ones are drawn)
    props_kv = [e for e in doc if e.name == "entity" and e.get("classname") == "prop_static"]
    assert len(props_kv) == 5 and not any(e.get("hl_collision_only") for e in props_kv)     # (the mesh + 4)
    for o in props:
        o.data.materials[0] = game_material("tools/toolsnodraw")
    doc, log = export()
    props_kv = [e for e in doc if e.name == "entity" and e.get("classname") == "prop_static"]
    assert sum(e.get("hl_collision_only") == "1" for e in props_kv) == 4, log      # (not the mesh itself)
    house.hammerless.collision_blocks_vis = False
    assert not [o for o in press() if o.hammerless.brush_detail == "VIS"]
    # a duplicated house (with its collision): each one's Make Collision replaces only its own
    bpy.ops.object.select_all(action="DESELECT")
    house.select_set(True)
    for o in house.children:
        o.select_set(True)
    bpy.context.view_layer.objects.active = house
    bpy.ops.object.duplicate()
    copy = next(o for o in bpy.context.selected_objects if o.get(OF) is None)
    assert copy != house and len(copy.children) == len(house.children) == 4
    copy_kids = set(copy.children)
    press()
    assert set(copy.children) == copy_kids and len(house.children) == 4
    bpy.context.view_layer.objects.active = copy
    assert bpy.ops.hammerless.make_collision() == {"FINISHED"}
    assert len(copy.children) == 4 and not (set(copy.children) & copy_kids) and len(house.children) == 4
    # tools/toolsinvisible on a model: a see-through alpha-tested material with its own texture (decals stick)
    d = os.path.join(TMP, "game_inv")
    written = write_model_materials(d, "models/hammerless/x", {
        "a": ("tools/toolsinvisible", False, False, False, False, "invisible"),
        "b": ("tools/toolsnodraw", False, False, False, False, "nodraw")})
    folder = os.path.join(d, "materials", "models", "hammerless", "x")
    a = open(os.path.join(folder, "a.vmt")).read()
    assert '"$alphatest" "1"' in a and "models/hammerless/x/hl_invisible" in a and "$no_draw" not in a, a
    assert "$no_draw" in open(os.path.join(folder, "b.vmt")).read()
    assert os.path.exists(os.path.join(folder, "hl_invisible.vtf"))
    assert any(w.endswith("hl_invisible.vtf") for w in written)


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
