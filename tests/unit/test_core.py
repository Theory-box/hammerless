"""Core tests: run with `python -m unittest discover tests/unit` (no Blender needed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hammerless.core import geometry as g
from hammerless.core.build import build_vmf, validate
from hammerless.core.compile import find_game_root, parse_log
from hammerless.core.displacement import build_patches, patch_vertex_positions
from hammerless.core.entities import CATALOG, PRESETS
from hammerless.core.ir import Brush, Entity, MapIR, Polygon, Terrain
from hammerless.core.vmf import VMFWriter, parse

GAME_ROOT = find_game_root()


def plane_normal(plane_str: str):
    """Hammer convention: normal = (p1 - p2) x (p3 - p2)."""
    pts = [tuple(float(c) for c in p.split()) for p in plane_str.strip("()").split(") (")]
    p1, p2, p3 = pts
    return g.normalize(g.cross(g.sub(p1, p2), g.sub(p3, p2)))


def box_room_ir() -> MapIR:
    ir = MapIR()
    ir.settings.name = "test_box"
    ir.brushes.append(g.box_brush((-256, -256, -16), (256, 256, 0), "dev/dev_measuregeneric01b", "floor"))
    ir.entities.append(Entity("info_survivor_position", (0, 0, 1), (0, 90, 0), {"Order": "1"}))
    return ir


class TestBrushes(unittest.TestCase):
    def test_box_plane_matches_hammer(self):
        b = g.box_brush((-64, -64, -64), (64, 64, 64), "tools/toolsnodraw")
        side = VMFWriter().side(b.faces[0])  # top face
        # Exactly what Hammer writes for the top face of this box
        self.assertEqual(side.get("plane"), "(-64 64 64) (64 64 64) (64 -64 64)")

    def test_all_side_normals_point_outward(self):
        b = g.box_brush((0, 0, 0), (32, 64, 128), "tools/toolsnodraw")
        solid = VMFWriter().solid(b)
        center = (16, 32, 64)
        for s in solid.blocks("side"):
            n = plane_normal(s.get("plane"))
            p1 = tuple(float(c) for c in s.get("plane").strip("(").split(")")[0].split())
            self.assertGreater(g.dot(n, g.sub(p1, center)), 0, s.get("plane"))

    def test_triangulated_cube_merges_to_six_faces(self):
        b = g.box_brush((0, 0, 0), (64, 64, 64), "x")
        tris = []
        for f in b.faces:
            v = f.verts
            tris += [Polygon([v[0], v[1], v[2]], "x"), Polygon([v[0], v[2], v[3]], "x")]
        self.assertEqual(len(g.merge_coplanar(tris)), 6)
        self.assertEqual(g.check_brush(Brush(tris, "cube")), [])

    def test_nonconvex_detected(self):
        # L-shaped prism: fails convexity
        L = [(0, 0), (64, 0), (64, 32), (32, 32), (32, 64), (0, 64)]
        bottom = [(x, y, 0) for x, y in reversed(L)]
        top = [(x, y, 32) for x, y in L]
        sides = []
        for i in range(len(L)):
            (ax, ay), (bx, by) = L[i], L[(i + 1) % len(L)]
            sides.append(Polygon([(ax, ay, 0), (bx, by, 0), (bx, by, 32), (ax, ay, 32)]))
        probs = g.check_brush(Brush([Polygon(bottom), Polygon(top)] + sides, "L"))
        self.assertTrue(any("not convex" in p.message for p in probs))

    def test_world_texture_axes(self):
        self.assertEqual(g.world_texture_axes((0, 0, 1)), ((1, 0, 0), (0, -1, 0)))
        self.assertEqual(g.world_texture_axes((1, 0, 0)), ((0, 1, 0), (0, 0, -1)))
        self.assertEqual(g.world_texture_axes((0, -1, 0)), ((1, 0, 0), (0, 0, -1)))

    def test_texture_never_mirrored(self):
        # U x V must point INTO the face (right x down = away from the viewer), for every face
        for n in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]:
            u, v = g.world_texture_axes(n)
            self.assertLess(g.dot(g.cross(u, v), n), 0, n)


class TestDisplacement(unittest.TestCase):
    def make_ramp(self, power=3, patches=2):
        n = (1 << power) + 1
        size = (n - 1) * patches + 1
        spacing = 32.0
        heights = [[col * spacing * 0.25 + row * 2.0 for col in range(size)] for row in range(size)]
        return Terrain((0.0, 0.0), spacing, heights, power, source="ramp")

    def test_patch_count_and_heights(self):
        t = self.make_ramp()
        patches = build_patches(t)
        self.assertEqual(len(patches), 4)
        for brush, disp in patches:
            for row in patch_vertex_positions(brush, disp):
                for x, y, z in row:
                    expected = x * 0.25 + (y / 32.0) * 2.0
                    self.assertAlmostEqual(z, expected, places=2)

    def test_bad_grid_size_rejected(self):
        t = Terrain((0, 0), 32, [[0.0] * 10 for _ in range(10)], 3, source="bad")
        with self.assertRaises(ValueError):
            build_patches(t)

    def test_empty_patches_skipped(self):
        t = self.make_ramp(patches=2)
        for r in range(0, 9):
            for c in range(0, 9):
                t.heights[r][c] = None
        self.assertEqual(len(build_patches(t)), 3)


class TestBuild(unittest.TestCase):
    def test_box_room_builds(self):
        text, rep = build_vmf(box_room_ir())
        self.assertTrue(rep.ok, rep.errors)
        blocks = parse(text)
        world = next(b for b in blocks if b.name == "world")
        self.assertEqual(world.get("classname"), "worldspawn")
        self.assertEqual(len(world.blocks("solid")), 1 + 6)  # floor + seal
        classes = [b.get("classname") for b in blocks if b.name == "entity"]
        for c in ("info_survivor_position", "info_director", "light_environment", "info_player_start"):
            self.assertIn(c, classes)

    def test_ids_unique(self):
        text, _ = build_vmf(box_room_ir())
        ids = []

        def walk(b):
            if b.get("id") is not None:
                ids.append(b.get("id"))
            for c in b.items:
                if not isinstance(c, tuple):
                    walk(c)
        for b in parse(text):
            walk(b)
        # solid/side/entity ids share one counter here, which Hammer accepts
        self.assertEqual(len(ids), len(set(ids)))

    def test_invalid_brush_blocks_build(self):
        ir = box_room_ir()
        ir.brushes.append(Brush([Polygon([(0, 0, 0), (1, 0, 0), (0, 1, 0)])], "flat"))
        text, rep = build_vmf(ir)
        self.assertIsNone(text)
        self.assertFalse(rep.ok)

    def test_terrain_in_vmf(self):
        ir = box_room_ir()
        ir.terrains.append(TestDisplacement().make_ramp())
        text, rep = build_vmf(ir)
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(text.count("dispinfo"), 4)

    def test_presets_build_clean(self):
        for preset in PRESETS.values():
            ir = MapIR()
            for part in preset.parts:
                if part.brush:
                    ir.brushes.append(part.brush)
                if part.entity:
                    ir.entities.append(part.entity)
            text, rep = build_vmf(ir)
            self.assertTrue(rep.ok, (preset.key, rep.errors))


class TestBrushNormals(unittest.TestCase):
    def test_inverted_brush_reported(self):
        b = g.box_brush((0, 0, 0), (64, 64, 64), "x")
        for f in b.faces:
            f.verts.reverse()
        probs = g.check_brush(b)
        self.assertTrue(any("inward" in p.message for p in probs), probs)


class TestTextures(unittest.TestCase):
    def test_vtf_size_and_header(self):
        import tempfile
        import numpy as np
        from hammerless.core.textures import read_vtf_header, write_vtf
        rgba = np.full((64, 128, 4), 200, dtype=np.uint8)
        rgba[..., 3] = 255  # opaque -> BGR888
        path = os.path.join(tempfile.mkdtemp(), "t.vtf")
        write_vtf(path, rgba)
        h = read_vtf_header(path)
        self.assertEqual((h["width"], h["height"], h["format"], h["mips"]), (128, 64, 3, 8))
        mip_bytes = sum(max(1, 128 >> i) * max(1, 64 >> i) * 3 for i in range(8))
        self.assertEqual(os.path.getsize(path), 80 + mip_bytes)

        rgba[0, 0, 3] = 10  # any transparency -> BGRA8888
        write_vtf(path, rgba)
        self.assertEqual(read_vtf_header(path)["format"], 12)

    def test_non_pow2_rejected(self):
        import numpy as np
        from hammerless.core.textures import write_vtf
        with self.assertRaises(ValueError):
            write_vtf("unused.vtf", np.zeros((100, 64, 4), dtype=np.uint8))


class TestLandmarks(unittest.TestCase):
    def test_duplicate_landmark_names_rejected(self):
        ir = box_room_ir()
        for _ in range(2):
            ir.entities.append(Entity("info_landmark", (0, 0, 0), (0, 0, 0), {"targetname": "lm"}))
        self.assertTrue(any("both named 'lm'" in e for e in validate(ir).errors))


class TestNav(unittest.TestCase):
    def preset_ir(self):
        from hammerless.core.entities import end_safe_room, start_safe_room
        ir = MapIR()
        for preset in (start_safe_room("lm_a"), end_safe_room("next", "lm_b")):
            for part in preset.parts:
                if part.brush:
                    ir.brushes.append(part.brush)
                if part.entity:
                    ir.entities.append(part.entity)
        return ir

    def test_regions_from_presets(self):
        from hammerless.core.nav import collect_regions
        regions, problems = collect_regions(self.preset_ir())
        self.assertEqual(problems, [])
        self.assertEqual(sorted(r.bits for r in regions), [2048, 128 | 2048])

    def test_region_not_in_vmf(self):
        text, rep = build_vmf(self.preset_ir())
        self.assertTrue(rep.ok, rep.errors)
        self.assertNotIn("hammerless_nav_region", text)

    def test_script(self):
        from hammerless.core.nav import collect_regions, navmark_script
        regions, _ = collect_regions(self.preset_ir())
        nut = navmark_script(regions, "m")
        self.assertIn("SetSpawnAttributes", nut)
        self.assertIn(", 2176)", nut)  # PLAYER_START | CHECKPOINT
        self.assertIn('SendToConsole("nav_save")', nut)

    def test_unknown_attribute_reported(self):
        from hammerless.core.nav import parse_attributes
        bits, unknown = parse_attributes("checkpoint, BOGUS")
        self.assertEqual((bits, unknown), (2048, ["BOGUS"]))


class TestOutputs(unittest.TestCase):
    def preset_ir(self, key):
        from hammerless.core.entities import PRESET_BUILDERS
        ir = box_room_ir()
        for part in PRESET_BUILDERS[key]().parts:
            if part.entity:
                ir.entities.append(part.entity)
        return ir

    def test_horde_trigger_connection(self):
        text, rep = build_vmf(self.preset_ir("HORDE_TRIGGER"))
        self.assertTrue(rep.ok, rep.errors)
        trig = next(b for b in parse(text) if b.name == "entity" and b.get("classname") == "trigger_once")
        self.assertEqual(trig.blocks("connections")[0].get("OnTrigger"), "director,ForcePanicEvent,,0,1")
        self.assertEqual(rep.warnings, [])

    def test_tank_ambush_targets_resolve(self):
        text, rep = build_vmf(self.preset_ir("TANK_AMBUSH"))
        self.assertTrue(rep.ok, rep.errors)
        self.assertFalse([w for w in rep.warnings if "targets" in w], rep.warnings)
        self.assertIn('"OnTrigger" "tank_ambush_spawner,SpawnZombie,tank,0,1"', text)

    def test_missing_target_warns(self):
        from hammerless.core.ir import Output
        ir = box_room_ir()
        ir.entities.append(Entity("logic_relay", (0, 0, 0), outputs=[Output("OnTrigger", "nobody", "Kill")]))
        self.assertTrue(any("'nobody'" in w for w in validate(ir).warnings))

    def test_all_presets_build(self):
        from hammerless.core.entities import PRESET_BUILDERS
        for key in PRESET_BUILDERS:
            text, rep = build_vmf(self.preset_ir(key))
            self.assertTrue(rep.ok, (key, rep.errors))


class TestSettingsAndScripts(unittest.TestCase):
    def test_settings_entities(self):
        ir = box_room_ir()
        st = ir.settings
        st.name = "m"
        st.fog_enabled = True
        st.debug_log = True
        st.director_enabled = True
        st.dir_common_limit = 7
        st.sun_color, st.sun_brightness = (10, 20, 30), 123
        text, rep = build_vmf(ir)
        self.assertTrue(rep.ok, rep.errors)
        ents = {b.get("classname"): b for b in parse(text) if b.name == "entity"}
        self.assertEqual(ents["light_environment"].get("_light"), "10 20 30 123")
        self.assertEqual(ents["env_fog_controller"].get("fogenable"), "1")
        self.assertEqual(ents["logic_script"].get("vscripts"), "hammerless/debug_m")
        self.assertEqual(ents["logic_auto"].blocks("connections")[0].get("OnMapSpawn"),
                         "director,BeginScript,hammerless_m_director,1,1")
        from hammerless.core.gamefiles import game_files
        files = game_files(ir)
        self.assertIn("CommonLimit = 7", files["scripts/vscripts/hammerless_m_director.nut"])
        self.assertIn("RegisterScriptGameEventListener", files["scripts/vscripts/hammerless/debug_m.nut"])

    def test_crescendo_preset(self):
        from hammerless.core.entities import crescendo_button
        from hammerless.core.gamefiles import game_files
        ir = box_room_ir()
        ir.settings.name = "m"
        for part in crescendo_button("c7").parts:
            ir.entities.append(part.entity)
        text, rep = build_vmf(ir)
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(rep.warnings, [])
        self.assertNotIn("hammerless_crescendo", text)
        self.assertIn("director,ScriptedPanicEvent,hammerless_m_c7,0,1", text)
        nut = game_files(ir)["scripts/vscripts/hammerless_m_c7.nut"]
        # building again (outputs already rewritten) stays clean
        text2, rep2 = build_vmf(ir)
        self.assertEqual(rep2.warnings, [])
        self.assertIn("A_CustomFinale_StageCount = 5", nut)
        self.assertIn("A_CustomFinaleValue2 = 10", nut)

    def test_crescendo_keeps_map_director_limits(self):
        from hammerless.core.entities import crescendo_button
        from hammerless.core.gamefiles import game_files
        ir = box_room_ir()
        ir.settings.name = "m"
        ir.settings.director_enabled = True
        ir.settings.dir_common_limit = 20
        for part in crescendo_button("c1").parts:
            ir.entities.append(part.entity)
        build_vmf(ir)
        self.assertIn("CommonLimit = 20", game_files(ir)["scripts/vscripts/hammerless_m_c1.nut"])

    def test_crescendo_unknown_name_warns(self):
        from hammerless.core.ir import Output
        ir = box_room_ir()
        ir.entities.append(Entity("logic_relay", (0, 0, 0), outputs=[
            Output("OnTrigger", "director", "ScriptedPanicEvent", "hammerless/crescendo_nope")]))
        self.assertTrue(any("crescendo_nope" in w for w in validate(ir).warnings))

    def test_parse_stages(self):
        from hammerless.core.gamefiles import parse_stages
        self.assertEqual(parse_stages("panic 2, delay 5; TANK")[0], [("PANIC", 2), ("DELAY", 5), ("TANK", 1)])
        self.assertTrue(parse_stages("DANCE 3")[1])

    def test_compile_options(self):
        from hammerless.core.compile import CompileOptions
        o = CompileOptions(vis="SKIP", rad="FINAL", hdr="LDR", extra_vrad="-bounce 50")
        self.assertIsNone(o.vvis_args())
        self.assertEqual(o.vrad_args(), ["-ldr", "-final", "-StaticPropLighting", "-StaticPropPolys", "-bounce", "50"])

    def test_patch_vmt(self):
        from hammerless.core.surfaces import patch_vmt
        vmt = patch_vmt("concrete/concrete_floor_01", "ice")
        self.assertIn('"include" "materials/concrete/concrete_floor_01.vmt"', vmt)
        self.assertIn('"$surfaceprop" "ice"', vmt)


class TestAutotest(unittest.TestCase):
    def test_route(self):
        from hammerless.core.autotest import autotest_script, plan_route
        from hammerless.core.entities import end_safe_room, horde_button, horde_trigger, start_safe_room

        def place(preset, dx):
            for part in preset.parts:
                if part.entity:
                    e = part.entity
                    if e.origin is not None:
                        e.origin = (e.origin[0] + dx, e.origin[1], e.origin[2])
                    for b in e.brushes:
                        for f in b.faces:
                            f.verts = [(x + dx, y, z) for x, y, z in f.verts]
                    ir.entities.append(e)
        ir = MapIR()
        place(start_safe_room("a"), 0)
        place(horde_button(), 2000)
        place(horde_trigger(), 800)
        place(end_safe_room("next", "b"), 4000)
        route, _ = plan_route(ir)
        self.assertEqual([w.kind for w in route], ["TRIGGER", "BUTTON", "END"])
        self.assertEqual(route[-1].target, "checkpoint_exit")
        self.assertTrue(route[1].target)  # button got a name to press
        nut = autotest_script(route, "m")
        self.assertIn('kind = "BUTTON"', nut)
        ir.settings.autotest = True
        text, rep = build_vmf(ir)
        self.assertTrue(rep.ok, rep.errors)
        self.assertIn(route[1].target, text)   # the name is written onto the button
        self.assertIn("hammerless/autotest_", text)


class TestTexturePreviews(unittest.TestCase):
    def test_vtf_roundtrip(self):
        import tempfile
        import numpy as np
        from hammerless.core.textures import write_vtf
        from hammerless.core.vtf_read import read_vtf
        rgba = np.zeros((64, 128, 4), np.uint8)
        rgba[..., 0] = np.arange(128)[None, :] * 2       # red ramp left->right
        rgba[..., 1] = np.arange(64)[:, None] * 4        # green ramp top->bottom
        rgba[..., 3] = 255
        path = os.path.join(tempfile.mkdtemp(), "t.vtf")
        write_vtf(path, rgba)
        with open(path, "rb") as f:
            w, h, img = read_vtf(f.read(), 512)
        self.assertEqual((w, h, img.shape), (128, 64, (64, 128, 4)))
        self.assertTrue(np.array_equal(img, rgba))
        with open(path, "rb") as f:
            _, _, small = read_vtf(f.read(), 32)
        self.assertEqual(small.shape, (16, 32, 4))

    def test_dxt1_decode(self):
        import numpy as np
        from hammerless.core.vtf_read import _decode_dxt
        # one 4x4 block: colour0 = pure red (0xF800), colour1 = pure blue (0x001F), all indices 0 -> red
        block = bytes([0x00, 0xF8, 0x1F, 0x00, 0, 0, 0, 0])
        img = _decode_dxt(block, 13, 4, 4)
        self.assertTrue(np.all(img[..., 0] == 255) and np.all(img[..., 2] == 0))

    def test_patch_material_base_texture(self):
        import tempfile
        from hammerless.core.gamematerials import base_texture
        from hammerless.core.surfaces import write_patch_material
        game = tempfile.mkdtemp()
        os.makedirs(os.path.join(game, "materials", "test"))
        with open(os.path.join(game, "materials", "test", "floor.vmt"), "w") as f:
            f.write('"LightmappedGeneric"\n{\n\t"$basetexture" "test/floor_tex"\n}\n')
        write_patch_material(game, "hammerless/m/patch_floor_ice", "test/floor", "ice")
        self.assertEqual(base_texture(None, "hammerless/m/patch_floor_ice", game), "test/floor_tex")


class TestLogs(unittest.TestCase):
    def test_leak_detected(self):
        s = parse_log("Processing areas...\n**** leaked ****\nEntity light (0 0 0) leaked!\n")
        self.assertTrue(s.leaked)

    def test_clean(self):
        s = parse_log("Valve Software - vbsp.exe\nProcessing world...\nFinished\n")
        self.assertFalse(s.leaked)
        self.assertEqual(s.errors, [])


@unittest.skipUnless(GAME_ROOT, "Left 4 Dead 2 not installed")
class TestGameContent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hammerless.core.vpk import GameContent
        cls.content = GameContent(GAME_ROOT)

    def test_catalog_models_exist(self):
        for d in CATALOG.values():
            for k in d.keys:
                if k.key == "model" and k.default:
                    self.assertTrue(self.content.has_model(k.default), k.default)

    def test_preset_content_exists(self):
        for preset in PRESETS.values():
            ir = MapIR()
            for part in preset.parts:
                if part.brush:
                    ir.brushes.append(part.brush)
                if part.entity:
                    ir.entities.append(part.entity)
            rep = validate(ir, self.content)
            missing = [w for w in rep.warnings if "not found" in w]
            self.assertEqual(missing, [], preset.key)

    def test_models_load(self):
        import numpy as np
        from hammerless.core.mdl import find_material, load_model
        from hammerless.core.entities import PREVIEW_MODELS
        # v44 / v48 / v49 models, both VTX strip-group layouts, characters stood upright
        for path, height in [("models/props_junk/dumpster.mdl", 53), ("models/props_doors/doormain01.mdl", 104),
                             ("models/props_vehicles/car001a_hatchback.mdl", 56),
                             ("models/infected/witch.mdl", 68), ("models/infected/hulk.mdl", 87)]:
            mesh = load_model(self.content, path)
            tris = np.array(mesh.triangles)
            self.assertLess(tris.max(), len(mesh.positions), path)
            p = mesh.positions[np.unique(tris)]
            self.assertAlmostEqual(p[:, 2].max() - p[:, 2].min(), height, delta=2, msg=path)
            self.assertTrue(all(find_material(self.content, mesh, t) for t in mesh.materials[:1]), path)
        for cls, path in PREVIEW_MODELS.items():
            self.assertTrue(self.content.has_model(path), (cls, path))

    def test_default_sky_exists(self):
        self.assertTrue(self.content.has_material("skybox/sky_day01_09_hdrbk"))


if __name__ == "__main__":
    unittest.main()
