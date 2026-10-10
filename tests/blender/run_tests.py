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
    assert compile_options(s).vrad_args() == cc.PRESETS["NORMAL"].vrad_args()
    s.compile_preset = "FINAL"                      # (sets Lighting and Visibility)
    assert s.light_quality == "FINAL" and s.vis_mode == "FULL" and s.light_sky_rays == 16
    s.light_bounces = 50                            # (a lighting setting by hand: Custom, and the build shows Mixed)
    assert s.light_quality == "CUSTOM" and s.compile_preset == "CUSTOM"
    assert "-bounce" in compile_options(s).vrad_args()
    s.vis_mode, s.light_quality, s.hdr_mode = "FAST", "OFF", "LDR"
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
        if blocks is None or tree.name in log:
            bad.append(f"{tree.name}:\n{log}")
    assert not bad, "\n".join(bad)


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
    # Lighting's Bake choice: the whole map, the view, and each No Bake Volume set to Bake only inside
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
    assert ids == ["MAP", "VIEW", "SELECTED", "VOL:test area"], ids   # (a Don't-bake volume isn't a choice)
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
    s.bake_area = "VOL:test area"
    assert s.bake_area == "VOL:test area" and s.bake_area_name == "VOL:test area"
    bpy.data.objects.remove(vol)
    assert s.bake_area == "MAP"                                  # (gone: the whole map)


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
    assert values(cut) == {"mode": "EXCLUDE", "nav": "1", "light": "0", "vis": "0", "sound": "0", "spawns": "0"}
    assert values(nb)["mode"] == "ONLY" and values(nb)["light"] == "1" and values(nb)["nav"] == "0"
    assert [it[0] for it in _bake_area_items(s, bpy.context)][-1] == "VOL:old bake"
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
