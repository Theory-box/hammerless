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
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    add_box("wall", (4, 0.5, 3), (0, 0, 1.5), mat)
    blocks, log = export()
    assert blocks, log
    mats = {s.get("material") for sol in world_solids(blocks) for s in sol.blocks("side")}
    assert "HAMMERLESS/TEST_MAP/MY_BRICK" in mats, mats
    vtf = os.path.join(FAKE_GAME, "left4dead2", "materials", "hammerless", "test_map", "my_brick.vtf")
    h = read_vtf_header(vtf)
    assert (h["width"], h["height"]) == (256, 128), h
    assert h["mips"] == 9
    with open(vtf.replace(".vtf", ".vmt")) as f:
        assert "hammerless/test_map/my_brick" in f.read()


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
