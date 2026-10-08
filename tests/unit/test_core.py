"""Core tests: run with `python -m unittest discover tests/unit` (no Blender needed)."""
import math
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

    def test_open_box_detected(self):
        # a box with its +Y face deleted: vbsp says "bounds out of range" without a name
        box = g.box_brush((0, 0, 0), (64, 64, 32), "x", "slab")
        n = [g.polygon_normal(f.verts) for f in box.faces]
        box.faces = [f for f, nn in zip(box.faces, n) if nn[1] < 0.5]
        probs = g.check_brush(box)
        self.assertEqual(len(probs), 1)
        self.assertIn("is open: its +Y side", probs[0].message)
        self.assertEqual(g.check_brush(g.box_brush((0, 0, 0), (64, 64, 32), "x", "ok")), [])

    def test_near_misses_found_and_welded(self):
        # two boxes whose tops miss by a quarter unit and whose edges overlap by 0.08:
        # the pattern that made vbsp cut a folded face and vrad bake black lightmaps
        a = g.box_brush((0, 0, 0), (100, 100, 407.245), "x", "Plane.001")
        b = g.box_brush((99.92, 0, 0), (200, 100, 406.99), "x", "Plane.103")
        c = g.box_brush((500, 500, 0), (600, 600, 406.99), "x", "far away")
        misses = g.near_misses([a, b, c])
        self.assertEqual({(m[0], m[1], m[2]) for m in misses}, {("Plane.001", "Plane.103", "X"), ("Plane.001", "Plane.103", "Z")})
        self.assertGreater(g.weld_near_misses([a, b, c]), 0)
        self.assertEqual(g.near_misses([a, b, c]), [])
        tops = {max(v[2] for f in br.faces for v in f.verts) for br in (a, b)}
        self.assertEqual(len(tops), 1)
        for br in (a, b, c):
            self.assertEqual(g.check_brush(br), [])

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

    def test_holes_sink_below_terrain(self):
        # a patch the terrain only partly covers: uncovered points must not make a shelf
        t = self.make_ramp(patches=1)
        for r in range(5, 9):
            for c in range(9):
                t.heights[r][c] = None
        (brush, disp), = build_patches(t)
        lowest = min(h for row in t.heights for h in row if h is not None)
        for i, row in enumerate(patch_vertex_positions(brush, disp)):
            for x, y, z in row:
                if i >= 5:
                    self.assertLess(z, lowest - 256)


class TestGeometryAudit(unittest.TestCase):
    """Fixes from the pre-release audit (geometry and map writing)."""

    def test_helper_entities_cant_leak_a_map_away_from_the_origin(self):
        # vbsp's leak check skips entities at exactly the origin; Hammerless's own logic entities go there
        ir = MapIR()
        ir.settings.name = "far"
        ir.settings.fog_enabled = True
        ir.brushes.append(g.box_brush((3000, 3000, -16), (3512, 3512, 0), "dev/dev_measuregeneric01b", "floor"))
        ir.entities.append(Entity("info_survivor_position", (3200, 3200, 1), (0, 0, 0), {"Order": "1"}))
        text, rep = build_vmf(ir)
        self.assertIsNotNone(text, rep.errors)
        for e in (b for b in parse(text) if b.name == "entity"):
            if e.get("classname") in ("logic_script", "logic_auto", "env_fog_controller"):
                self.assertEqual(e.get("origin"), "0 0 0", e.get("classname"))

    def test_plane_never_from_three_points_on_a_line(self):
        from hammerless.core.vmf import _plane_points
        # a face with extra points along its bottom edge (a knife cut): old picks were all on that edge
        face = [(16, 0, 0), (32, 0, 0), (48, 0, 0), (64, 0, 0), (64, 64, 0), (0, 64, 0), (0, 0, 0)]
        a, b, c = _plane_points(face)
        n = g.cross(g.sub(b, a), g.sub(c, a))
        self.assertGreater(abs(n[2]), 1000)

    def test_spawn_under_a_table_stays_on_the_floor(self):
        from hammerless.core.build import floor_below
        ir = box_room_ir()
        ir.brushes.append(g.box_brush((-32, -32, 28), (32, 32, 32), "dev/dev_measuregeneric01b", "table top"))
        self.assertEqual(floor_below(ir, 0, 0, 1), 0)          # not up onto the table (z 32)
        self.assertEqual(floor_below(ir, 0, 0, 30), 32)        # sunk into the table top: onto it

    def test_tool_brushes_stay_world(self):
        from hammerless.core.build import is_auto_detail
        for mat in ("tools/toolshint", "tools/toolsskip", "tools/toolsclip", "tools/toolsareaportal"):
            self.assertFalse(is_auto_detail(g.box_brush((0, 0, 0), (32, 32, 32), mat), "AUTO"), mat)

    def test_big_terrain_patches_get_a_lightmap_vbsp_accepts(self):
        n = 9
        t = Terrain(heights=[[0.0] * n for _ in range(n)], origin=(0, 0), spacing=2048 / 8, power=3,
                    material="nature/blend_grass_grass_01", source="hill")
        (brush, _disp), = build_patches(t)
        self.assertLessEqual(2048 / brush.faces[0].lightmap_scale, 125)


class TestEntityAudit(unittest.TestCase):
    """Fixes from the pre-release audit (entities, presets, generated scripts)."""

    def test_rotated_safe_room_marks_only_its_own_nav(self):
        import math
        from hammerless.core.nav import NavRegion, collect_regions, NAV_REGION
        ir = box_room_ir()
        c, s_ = math.cos(math.radians(45)), math.sin(math.radians(45))
        rot = lambda x, y: (x * c - y * s_, x * s_ + y * c)
        box = g.box_brush((-100, -100, 0), (100, 100, 128), "tools/toolstrigger", "room")
        for f in box.faces:
            f.verts = [(*rot(v[0], v[1]), v[2]) for v in f.verts]
        ir.entities.append(Entity("info_changelevel", None, (0, 0, 0), {"map": "c1m2_streets"}, [box]))
        (region,), _ = collect_regions(ir)
        self.assertTrue(region.contains((0, 0, 10)))
        self.assertFalse(region.contains((130, 130, 10)))      # inside the square box around it, outside the room
        self.assertTrue(region.contains((0, 0, -10)))          # padded down (areas sit on the floor)
        self.assertFalse(region.contains((0, 0, 140)))         # never up (a marked roof breaks the flow)

    def test_triggers_fire_for_players(self):
        from hammerless.core.entities import default_keyvalues
        self.assertEqual(default_keyvalues("trigger_once").get("spawnflags"), "1")
        ir = box_room_ir()
        ir.entities.append(Entity("trigger_once", None, (0, 0, 0), {}, [g.box_brush((-32, -32, 0), (32, 32, 64),
                                                                                    "tools/toolstrigger")]))
        text, rep = build_vmf(ir)
        trig = next(e for e in parse(text) if e.name == "entity" and e.get("classname") == "trigger_once")
        self.assertEqual(trig.get("spawnflags"), "1")          # old scenes without the key are fixed at build

    def test_names_in_scripts_are_escaped(self):
        from hammerless.core.nav import NavRegion, navmark_script
        nut = navmark_script([NavRegion((0, 0, 0), (1, 1, 1), 2048, 'End "B" \\')], "m")
        self.assertIn('End \\"B\\" \\\\', nut)

    def test_crescendo_name_with_spaces_starts(self):
        from hammerless.core.build import resolve_crescendo
        ir = box_room_ir()
        ir.crescendos["lift_event"] = [("PANIC", 1.0)]
        self.assertEqual(resolve_crescendo(ir, "Lift Event"), "lift_event")


class TestNavReportAudit(unittest.TestCase):
    def test_drop_off_roof_is_not_an_island_and_distance_is_walking(self):
        from hammerless.core.navanalysis import PLAYER_START, analyse
        from hammerless.core.navfile import NavArea, NavMesh

        def area(i, x, z=0.0):
            a = NavArea(i, 0, (x, 0.0, z), (x + 100.0, 100.0, z), z, z)
            a.connections = [[], [], [], []]
            return a
        start, street, roof, sealed = area(1, 0), area(2, 100), area(3, 300, 200.0), area(4, 600, 200.0)
        start.spawn_attributes = PLAYER_START
        start.connections[1] = [2]
        street.connections[3] = [1]
        roof.connections[3] = [2]            # one way: drop down into the street
        rep = analyse(NavMesh(areas=[start, street, roof, sealed]))
        self.assertEqual(rep.islands, [[4]])            # only the closed-off spot
        self.assertAlmostEqual(rep.distance[2], 100.0)


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

    def test_end_landmark_renamed(self):
        # Both presets default to 'landmark_1': the end room's copy is renamed, not an error.
        from hammerless.core.entities import end_safe_room, start_safe_room
        ir = box_room_ir()
        for preset, dx in ((start_safe_room("landmark_1"), 0), (end_safe_room("c1m2_streets", "landmark_1"), 2000)):
            for part in preset.parts:
                for b in ([part.brush] if part.brush else []) + (part.entity.brushes if part.entity else []):
                    for f in b.faces:
                        f.verts = [(x + dx, y, z) for x, y, z in f.verts]
                if part.entity and part.entity.origin is not None:
                    x, y, z = part.entity.origin
                    part.entity.origin = (x + dx, y, z)
                if part.brush:
                    ir.brushes.append(part.brush)
                if part.entity:
                    ir.entities.append(part.entity)
        text, rep = build_vmf(ir)
        self.assertTrue(rep.ok, rep.errors)
        self.assertIn('"targetname" "landmark_1_end"', text)
        self.assertIn('"landmark" "landmark_1_end"', text)
        self.assertEqual(text.count('"targetname" "landmark_1"'), 1)

    def test_next_map_fallback(self):
        # Empty or self-referencing Next Map breaks the Director's flow in-game.
        from hammerless.core.build import FALLBACK_NEXT_MAP
        for nxt in ("", "My_Map"):
            ir = box_room_ir()
            ir.settings.name = "my_map"
            vol = g.box_brush((0, 0, 0), (64, 64, 64), "tools/toolstrigger")
            ir.entities.append(Entity("info_changelevel", None, (0, 0, 0), {"map": nxt}, [vol]))
            text, rep = build_vmf(ir)
            self.assertTrue(any("Next Map" in w for w in rep.warnings))
            self.assertIn(f'"map" "{FALLBACK_NEXT_MAP}"', text)


class TestMapCheck(unittest.TestCase):
    """Walkable path from start to end room, and entity placement, before compiling."""

    def level(self, ground_to=4200, end_dz=0):
        from hammerless.core.entities import end_safe_room, start_safe_room
        ir = MapIR()
        ir.brushes.append(g.box_brush((-400, -1024, -200), (ground_to, 1024, 0), "dev/dev_measuregeneric01b", "ground"))
        if ground_to < 3800:   # a 96-unit gap, then more ground
            ir.brushes.append(g.box_brush((ground_to + 96, -1024, -200), (4400, 1024, 0),
                                          "dev/dev_measuregeneric01b", "ground2"))
        for preset, dx, dz in ((start_safe_room("a"), -330, 0), (end_safe_room("c1m2_streets", "b"), 3800, end_dz)):
            for part in preset.parts:
                for b in ([part.brush] if part.brush else []) + (part.entity.brushes if part.entity else []):
                    for f in b.faces:
                        f.verts = [(x + dx, y, z + dz) for x, y, z in f.verts]
                if part.entity and part.entity.origin is not None:
                    x, y, z = part.entity.origin
                    part.entity.origin = (x + dx, y, z + dz)
                if part.brush:
                    ir.brushes.append(part.brush)
                if part.entity:
                    ir.entities.append(part.entity)
        return ir

    def test_placement(self):
        from hammerless.core.mapcheck import check_map
        ir = self.level()
        ir.entities += [Entity("weapon_first_aid_kit_spawn", (1000, 0, -6), source="sunk"),
                        Entity("weapon_first_aid_kit_spawn", (1100, 0, -150), source="buried"),
                        Entity("weapon_first_aid_kit_spawn", (1200, 0, 100), source="floating"),
                        Entity("weapon_first_aid_kit_spawn", (1300, 0, 0), source="fine")]
        found = {p.source: p.message for p in check_map(ir)}
        self.assertIn("sunk 6", found["sunk"])
        self.assertIn("stuck in solid", found["buried"])
        self.assertIn("floats 100", found["floating"])
        self.assertNotIn("fine", found)

    def test_in_report(self):
        ir = self.level()
        ir.entities.append(Entity("weapon_first_aid_kit_spawn", (1000, 0, -150), source="buried"))
        text, rep = build_vmf(ir)
        self.assertTrue(rep.ok)                                      # still builds
        self.assertTrue(rep.problems and rep.problems[0].location)
        self.assertTrue(any("stuck in solid" in w for w in rep.warnings))


class TestNavFile(unittest.TestCase):
    """The game's .nav files: read, write back identically, analyse."""

    def mesh(self, broken=False):
        from hammerless.core.navfile import NavArea, NavMesh
        # a row of 100x100 areas along +x: start room (1), path (2..4), end room (5)
        m = NavMesh(analyzed=True)
        for i in range(1, 6):
            z = 0.0 if not (broken and i >= 4) else -200.0
            m.areas.append(NavArea(i, 0, (i * 100.0, 0.0, z), (i * 100.0 + 100, 100.0, z), z, z))
        for a in m.areas:
            if a.id < 5 and not (broken and a.id == 3):
                a.connections[1].append(a.id + 1)          # east
            if a.id > 1 and not (broken and a.id == 4):
                a.connections[3].append(a.id - 1)          # west
        m.areas[0].spawn_attributes = 0x880
        m.areas[4].spawn_attributes = 0x800
        m.areas[2].visible = [(1, 2), (2, 3)]
        return m

    def test_round_trip(self):
        from hammerless.core.navfile import read_nav, write_nav
        data = write_nav(self.mesh())
        again = read_nav(data)
        self.assertEqual(write_nav(again), data)
        self.assertEqual(again.areas[0].spawn_attributes, 0x880)
        self.assertEqual(again.areas[2].visible, [(1, 2), (2, 3)])

    def test_clears(self):
        """Clear Analysis keeps the areas and drops only visibility / hiding spots; Clear Navmesh and
        Clear Bake delete their files (and what records how they were made)."""
        import tempfile
        from hammerless.core import compile as cc
        from hammerless.core.navfile import load_nav, write_nav
        root = tempfile.mkdtemp()
        maps = os.path.join(root, "left4dead2", "maps")
        hl = os.path.join(root, "left4dead2", "scripts", "vscripts", "hammerless")
        os.makedirs(maps)
        os.makedirs(hl)
        tools = cc.Tools(root)
        open(os.path.join(maps, "m.nav"), "wb").write(write_nav(self.mesh()))
        self.assertTrue(cc.nav_analyzed(tools, "m"))
        self.assertTrue(cc.clear_nav_analysis(tools, "m"))
        self.assertFalse(cc.nav_analyzed(tools, "m"))
        again = load_nav(os.path.join(maps, "m.nav"))
        self.assertEqual([a.id for a in again.areas], [1, 2, 3, 4, 5])
        self.assertEqual(again.areas[0].spawn_attributes, 0x880)
        self.assertEqual(again.areas[2].visible, [])
        self.assertFalse(cc.clear_nav_analysis(tools, "missing"))
        for f in ("navmaker_m.txt", "navmark_m.used", "navmark_m.nut"):
            open(os.path.join(hl, f), "w").write("x")
        self.assertEqual(cc.clear_nav(tools, "m"), [])
        self.assertFalse(os.path.exists(os.path.join(maps, "m.nav")))
        self.assertFalse(os.path.exists(os.path.join(hl, "navmaker_m.txt")))
        self.assertTrue(os.path.exists(os.path.join(hl, "navmark_m.nut")))      # the marks themselves stay
        work = tempfile.mkdtemp()
        for ext in (".bsp", ".stamp", ".built.vmf", ".built.opts", ".built.prt", ".vmf"):
            open(os.path.join(work, "m" + ext), "w").write("x")
        open(os.path.join(maps, "m.bsp"), "w").write("x")
        self.assertEqual(cc.clear_build(tools, os.path.join(work, "m"), "m"), [])
        self.assertEqual(sorted(os.listdir(work)), ["m.vmf"])                     # the export itself stays
        self.assertFalse(os.path.exists(os.path.join(maps, "m.bsp")))

    def test_game_files_round_trip(self):
        import glob
        from hammerless.core.navfile import read_nav, write_nav
        root = find_game_root()
        files = glob.glob(os.path.join(root, "left4dead2", "maps", "c1m1_hotel.nav")) if root else []
        if not files:
            self.skipTest("L4D2 not installed")
        data = open(files[0], "rb").read()
        self.assertEqual(write_nav(read_nav(data)), data)

    def test_dead_ledges(self):
        from hammerless.core.navanalysis import analyse
        from hammerless.core.navfile import NavArea, NavMesh
        for drop, expect in ((10, 0), (34, 1), (70, 0)):
            m = NavMesh()
            m.areas = [NavArea(1, 0, (0, 0, 0), (100, 100, 0), 0, 0),
                       NavArea(2, 0, (0, 125, -drop), (100, 225, -drop), -drop, -drop)]   # 25 apart, lower
            if drop == 10:
                m.areas[0].connections[2], m.areas[1].connections[0] = [2], [1]
            if drop == 70:
                m.areas[0].connections[2] = [2]                   # the game's one-way drop-down
            self.assertEqual(len(analyse(m).dead_ledges), expect, drop)

    def test_analysis(self):
        from hammerless.core.navanalysis import analyse
        rep = analyse(self.mesh())
        self.assertTrue(rep.end_reached)
        self.assertEqual(len(rep.reachable), 5)
        rep = analyse(self.mesh(broken=True))
        self.assertFalse(rep.end_reached)
        self.assertEqual(rep.break_area, 3)                     # the path stops at area 3
        self.assertEqual(sorted(rep.islands[0]), [4, 5])


def _crate_ir():
    ir = box_room_ir()
    ir.brushes.append(g.box_brush((-60, 40, 0), (4, 104, 48), "dev/dev_measuregeneric01b", "crate"))
    return ir


class TestSmartBuild(unittest.TestCase):
    """buildplan: the least compile work that gives the same map."""

    def vmf(self, speed="50", light="255 255 255 200", wall=128, prop_x=0):
        ir = box_room_ir()
        ir.brushes.append(g.box_brush((wall, -64, 0), (wall + 16, 64, 128), "dev/dev_measuregeneric01b", "wall"))
        ir.entities.append(Entity("light", (0, 0, 100), (0, 0, 0), {"_light": light}))
        ir.entities.append(Entity("prop_static", (prop_x, 50, 0), (0, 0, 0), {"model": "models/props/cs_office/box.mdl"}))
        ir.entities.append(Entity("logic_relay", (0, 0, 16), (0, 0, 0), {"targetname": "r", "delay": speed}))
        return build_vmf(ir)[0]

    def test_plans(self):
        from hammerless.core.buildplan import plan
        a = self.vmf()
        self.assertEqual(plan(None, a)[0], "full")
        self.assertEqual(plan(a, a)[0], "same")
        self.assertEqual(plan(a, self.vmf(speed="60"))[0], "entities")
        self.assertEqual(plan(a, self.vmf(light="255 0 0 200"))[0], "lighting")
        self.assertEqual(plan(a, self.vmf(prop_x=32))[0], "lighting")      # static props are baked
        self.assertEqual(plan(a, self.vmf(wall=160))[0], "full")

    def test_lights_get_hammers_falloff(self):
        # without attenuation keys vrad uses constant attenuation: the light never fades with distance
        ir = box_room_ir()
        ir.entities.append(Entity("light", (0, 0, 100), (0, 0, 0), {"_light": "255 255 255 200"}))
        ir.entities.append(Entity("light_spot", (0, 0, 90), (0, 0, 0), {"_light": "255 255 255 200",
                                                                       "_fifty_percent_distance": "300"}))
        text = build_vmf(ir)[0]
        point = text[text.index('"light"'):]
        spot = text[text.index('"light_spot"'):]
        self.assertIn('"_quadratic_attn" "1"', point[:point.index("}")])
        self.assertNotIn("_quadratic_attn", spot[:spot.index("}")])     # the mapper's own falloff stays

    def test_light_order_matters(self):
        # vbsp numbers switchable light styles (and static prop lighting files) in entity order:
        # swapping two named lights needs a relight, not 'same'
        from hammerless.core.buildplan import plan

        def two(first, second):
            ir = box_room_ir()
            for name in (first, second):
                ir.entities.append(Entity("light", (0, 0, 100), (0, 0, 0), {"_light": "255 255 255 200",
                                                                          "targetname": name}))
            return build_vmf(ir)[0]
        self.assertNotEqual(plan(two("lamp_a", "lamp_b"), two("lamp_b", "lamp_a"))[0], "same")

    def test_nav_outdated_after_a_new_compile(self):
        # Compile Only (or an Esc'd Build) compiles without a new nav: the next build must make one
        import struct
        import tempfile
        from hammerless.core import compile as cc
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "left4dead2", "maps"))
        tools = cc.Tools(root)

        def bsp(other: bytes) -> bytes:
            header = bytearray(b"VBSP" + struct.pack("<i", 21) + bytes(16 * 64) + struct.pack("<i", 1))
            struct.pack_into("<iiii", header, 8 + 16 * 1, 0, len(header), len(other), 0)
            return bytes(header) + other
        game_bsp = os.path.join(tools.maps_dir, "m.bsp")
        open(game_bsp, "wb").write(bsp(b"walls v1"))
        cc.set_nav_maker(tools, "m", "blender")
        self.assertFalse(cc.nav_outdated(tools, "m"))
        self.assertEqual(cc.nav_maker(tools, "m"), "blender")
        open(game_bsp, "wb").write(bsp(b"walls v2, moved"))
        self.assertTrue(cc.nav_outdated(tools, "m"))

    def test_strip_stale(self):
        import io
        import struct
        import tempfile
        import zipfile
        from hammerless.core.buildplan import strip_stale
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as f:
            f.writestr("materials/a.vtf", b"x" * 100)
            f.writestr("stale.txt", b"stale")
        pak = z.getvalue()
        header = bytearray(8 + 16 * 64 + 4)
        header[0:4] = b"VBSP"
        off = len(header)
        struct.pack_into("<iiii", header, 8 + 16 * 40, 0, off, len(pak), 0)
        with tempfile.NamedTemporaryFile(suffix=".bsp", delete=False) as t:
            t.write(bytes(header) + pak)
        self.assertTrue(strip_stale(t.name))
        data = open(t.name, "rb").read()
        _v, o, ln, _c = struct.unpack_from("<iiii", data, 8 + 16 * 40)
        names = zipfile.ZipFile(io.BytesIO(data[o:o + ln])).namelist()
        self.assertEqual(names, ["materials/a.vtf"])
        self.assertFalse(strip_stale(t.name))
        os.remove(t.name)


class TestNavAnalysis(unittest.TestCase):
    def test_visibility_compression_round_trip(self):
        # areas inherit a neighbour's list plus a difference (NOT_VISIBLE cancels); expanding the
        # stored form gives back exactly the lists we computed
        from hammerless.core.navfile import NavArea, NavMesh
        from hammerless.core.navanalyze import compress_visibility
        from hammerless.core.navvis import expand_game_lists
        areas = []
        for i in range(4):
            a = NavArea(i + 1, 0, (i * 100.0, 0.0, 0.0), (i * 100.0 + 100.0, 100.0, 0.0), 0.0, 0.0)
            a.connections = [[], [i + 2] if i < 3 else [], [], [i] if i > 0 else []]
            areas.append(a)
        mesh = NavMesh(areas=areas)
        lists = [{0: 2, 1: 2, 2: 1}, {0: 2, 1: 2, 2: 1, 3: 1}, {1: 3, 2: 2, 3: 2}, {2: 2, 3: 2}]
        compress_visibility(mesh, lists)
        self.assertTrue(any(a.inherit_visibility for a in mesh.areas))
        expanded = expand_game_lists(mesh)
        for i, a in enumerate(mesh.areas):
            self.assertEqual(expanded[a.id], {mesh.areas[k].id: v for k, v in lists[i].items()})

    def test_props_block_by_the_games_rules(self):
        # props whose model the game keeps block sight with their hull: a bounding-box static prop
        # as the upright box around its turned hull, an entity's unturned; props the game or vbsp
        # deletes (prop_physics without prop_data, ...) block nothing
        import struct
        from hammerless.core.navanalyze import _prop_pieces
        from hammerless.core.vmf import Block

        def mdl(flags, keyvalues=""):
            kv = keyvalues.encode()
            data = bytearray(400) + kv
            struct.pack_into("<6f", data, 104, -10, -20, 0, 10, 20, 40)      # hull_min, hull_max
            struct.pack_into("<i", data, 152, flags)
            struct.pack_into("<ii", data, 312, 400, len(kv))
            return bytes(data)

        class Content:
            files = {"models/box.mdl": mdl(0x10),
                     "models/phys.mdl": mdl(0x10, 'mdlkeyvalue { prop_data { "base" "Metal.Large" } }'),
                     "models/both.mdl": mdl(0x10, 'mdlkeyvalue { prop_data { "base" "Metal.Large" "allowstatic" "1" } }')}

            def read(self, path):
                return self.files.get(path)

        def ent(cls, model, yaw=0, solid="2"):
            b = Block("entity")
            b.items = [("classname", cls), ("model", model), ("origin", "100 0 0"), ("angles", f"0 {yaw} 0"),
                       ("solid", solid)]
            return b

        def bounds(pieces):
            (piece,) = pieces
            return tuple(round(v, 3) + 0.0 for v in tuple(piece.mins) + tuple(piece.maxs))
        c = Content()
        self.assertEqual(bounds(_prop_pieces(ent("prop_static", "models/box.mdl", 90), c)), (80, -10, 0, 120, 10, 40))
        self.assertEqual(bounds(_prop_pieces(ent("prop_dynamic", "models/box.mdl", 90), c)), (90, -20, 0, 110, 20, 40))
        self.assertEqual(_prop_pieces(ent("prop_static", "models/box.mdl", 0, "0"), c), [])      # not solid
        self.assertEqual(_prop_pieces(ent("prop_static", "models/phys.mdl"), c), [])      # vbsp: "Deleted."
        self.assertEqual(_prop_pieces(ent("prop_dynamic", "models/phys.mdl"), c), [])     # the game deletes it
        self.assertEqual(_prop_pieces(ent("prop_physics", "models/box.mdl"), c), [])      # no prop_data: deleted
        self.assertTrue(_prop_pieces(ent("prop_static", "models/both.mdl"), c))
        self.assertTrue(_prop_pieces(ent("prop_dynamic_override", "models/phys.mdl"), c))


class TestNavGen(unittest.TestCase):
    """Our reimplementation of the game's nav sampling (stage A)."""

    def world(self, step=0.0):
        from hammerless.core.collision import CollisionWorld
        ir = box_room_ir()                       # 512x512 floor at z=0, walls, sealed
        if step:
            ir.brushes.append(g.box_brush((100, -256, 0), (256, 256, step), "dev/dev_measuregeneric01b", "step"))
        text, _ = build_vmf(ir)
        return text, CollisionWorld.from_vmf(text)

    def test_trace_hits_floor(self):
        text, w = self.world()
        tr = w.trace_hull((0, 0, 100), (0, 0, -100), (-0.45, -0.45, 0), (0.45, 0.45, 55))
        self.assertAlmostEqual(tr.endpos[2], 0.03125, places=3)     # stops DIST_EPSILON above the floor
        self.assertEqual(tr.normal, (0.0, 0.0, 1.0))
        self.assertTrue(w.trace_hull((0, 0, -8), (0, 0, -10), (-1, -1, 0), (1, 1, 1)).allsolid)   # inside the 16-unit floor

    def test_bevels(self):
        from hammerless.core.collision import Side, make_brush
        wedge = make_brush([Side((0, 0, -1), 0), Side((-1, 0, 0), 0), Side((0, -1, 0), 0), Side((0, 1, 0), 64),
                            Side((0.7071068, 0, 0.7071068), 45.254834)])
        self.assertEqual([s.bevel for s in wedge.sides[:6]].count(True), 2)       # +x and +z added
        self.assertEqual([s.normal for s in wedge.sides[:6]][4:], [(0.0, 0.0, -1.0), (0.0, 0.0, 1.0)])

    def sample(self, step=0.0):
        from hammerless.core.navgen import Sampler
        text, w = self.world(step)
        s = Sampler(w)
        s.add_seed((-150, 0, 10))
        s.sample()
        return s

    def test_native_matches_python(self):
        from hammerless.core import fastnav
        from hammerless.core.navgen import Sampler
        if not fastnav.available():
            self.skipTest("native DLL not built")
        text, w = self.world(step=16)
        runs = []
        for native in (None, False):
            s = Sampler(w)
            s.native = native
            s.add_seed((-150, 0, 10))
            s.sample()
            runs.append([(n.pos, n.normal, n.attributes, tuple(n.crouch), tuple(n.blocked), tuple(n.obstacle),
                          tuple(m.id if m else 0 for m in n.to)) for n in s.nodes])
        self.assertEqual(runs[0], runs[1])

    def test_sweep_memory_after_edits(self):
        # the native sweep memory, kept across runs and patched for changed brushes, gives the
        # nodes a fresh run gives: for adding, moving and removing a brush
        from hammerless.core import fastnav
        from hammerless.core.collision import CollisionWorld
        from hammerless.core.navgen import Sampler
        if not fastnav.available():
            self.skipTest("native DLL not built")
        _text, base = self.world(step=16)
        crate = CollisionWorld.from_vmf(build_vmf(_crate_ir())[0]).brushes

        def nodes(brushes, fresh):
            if fresh:
                fastnav._memo_keys = None
            s = Sampler(CollisionWorld(brushes))
            s.add_seed((-150, 0, 10))
            s.sample()
            return [(n.pos, n.normal, n.attributes, tuple(n.crouch), tuple(n.blocked), tuple(n.obstacle),
                     tuple(n.ground), tuple(m.id if m else 0 for m in n.to)) for n in s.nodes]
        edits = [base.brushes + crate[-1:],                  # add a crate
                 base.brushes[:-1],                          # remove the step
                 base.brushes[:-1] + crate[-1:]]             # and the crate instead of the step
        for edited in edits:
            nodes(base.brushes, True)                        # memory made with the original
            remembered = nodes(edited, False)
            self.assertEqual(fastnav.last_memo["mode"], "update")
            self.assertEqual(remembered, nodes(edited, True))

    def test_flat_room(self):
        s = self.sample()
        self.assertGreater(len(s.nodes), 300)
        self.assertTrue(all(abs(n.pos[2] - 0.03125) < 0.01 for n in s.nodes))

    def test_steps(self):
        low = self.sample(step=16)                # a step survivors walk up
        self.assertTrue(any(n.pos[2] > 15 for n in low.nodes))
        high = self.sample(step=250)              # higher than the 200-unit climb check: never reached
        self.assertFalse(any(n.pos[2] > 100 for n in high.nodes))


class TestLogicGraph(unittest.TestCase):
    def test_fgd_parse_and_inherit(self):
        from hammerless.core import fgd
        text = """
        @BaseClass = Targetname [ input Kill(void) : "Removes" output OnUser1(void) : "x" ]
        @SolidClass base(Targetname) = func_thing : "A thing" + " that moves"
        [
            speed(integer) : "Speed" : 10 : "desc [with brackets]"
            mode(choices) : "Mode" : 0 = [ 0 : "A" 1 : "B" ]
            input Open(void) : "Opens it"
            input SetPosition(string) : "Moves " + "there"
            output OnFullyOpen(void) : "Done"
        ]
        """
        classes = fgd.parse(text)
        ios = fgd.resolve(classes, "func_thing")
        names = [(io.kind, io.name) for io in ios]
        self.assertEqual(names[:3], [("input", "Open"), ("input", "SetPosition"), ("output", "OnFullyOpen")])
        self.assertIn(("input", "Kill"), names)
        self.assertTrue(next(io for io in ios if io.name == "SetPosition").takes_value)
        self.assertEqual(next(io for io in ios if io.name == "SetPosition").description, "Moves there")
        self.assertEqual(fgd.pretty("OnFullyOpen"), "On Fully Open")

    def test_compile_graph(self):
        from hammerless.core.logic import LLink, LNode, compile_graph
        ir = MapIR()
        btn = Entity("func_button", None, (0, 0, 0), {}, [g.box_brush((0, 0, 0), (8, 8, 8), "x")], "gate 1 button")
        gate = Entity("func_movelinear", None, (0, 0, 0), {"targetname": "gate_1"},
                      [g.box_brush((50, 0, 0), (58, 64, 128), "x")], "gate 1")
        ir.entities += [btn, gate]
        vol = g.box_brush((-100, 0, 0), (-50, 50, 50), "x", "zone")
        nodes = [LNode("Button", "OBJECT", {"outputs": ["OnPressed"], "inputs": []}, "gate 1 button"),
                 LNode("Gate", "OBJECT", {"outputs": [], "inputs": ["Open", "SetPosition"]}, "gate 1",
                       params={"SetPosition": "0.5"}),
                 LNode("Delay", "DELAY", {"seconds": 2.5}), LNode("Horde", "HORDE"),
                 LNode("Zone", "VOLUME", {"who": "SURVIVORS", "once": True}, "zone", [vol]),
                 LNode("Count", "COUNTER", {"count": 3}), LNode("Lonely", "OBJECT", {}, "missing")]
        links = [LLink("Button", "OnPressed", "Gate", "Open"), LLink("Button", "OnPressed", "Gate", "SetPosition"),
                 LLink("Button", "OnPressed", "Delay", "in"), LLink("Delay", "out", "Horde", "start"),
                 LLink("Zone", "all_inside", "Count", "add")]
        problems = compile_graph(nodes, links, ir)
        self.assertEqual(len(problems), 1)                       # the node with no real object
        self.assertEqual(btn.keyvalues["targetname"], "hl_gate_1_button")
        outs = {(o.output, o.target, o.input, o.parameter, o.delay, o.times) for o in btn.outputs}
        self.assertIn(("OnPressed", "gate_1", "Open", "", 0.0, -1), outs)
        self.assertIn(("OnPressed", "gate_1", "SetPosition", "0.5", 0.0, -1), outs)
        relay = next(e for e in ir.entities if e.classname == "logic_relay")
        # (plus switching on the Horde node's own "finished" listener)
        self.assertIn(("director", "ForcePanicEvent", 2.5), [(o.target, o.input, o.delay) for o in relay.outputs])
        trig = next(e for e in ir.entities if e.classname == "trigger_multiple")
        self.assertEqual(trig.brushes[0].faces[0].material, "tools/toolstrigger")
        self.assertEqual([(o.output, o.input, o.parameter, o.times) for o in trig.outputs],
                         [("OnEntireTeamStartTouch", "Add", "1", 1)])
        self.assertTrue(any(e.classname == "filter_activator_team" for e in ir.entities))
        self.assertEqual(sum(e.classname == "info_director" for e in ir.entities), 1)


class TestScriptNodes(unittest.TestCase):
    """Game functions and events as logic nodes (core/vscript.py catalogue)."""

    def test_catalogue(self):
        from hammerless.core import vscript as vs
        f = vs.function("player:GiveItem")
        self.assertEqual((f["on"], f["params"], vs.is_pure(f)), ("player", ["string"], False))
        self.assertTrue(vs.is_pure(vs.function("Director.GetFurthestSurvivorFlow")))
        hurt = {x["name"]: x for x in vs.event("player_hurt")["fields"]}
        self.assertTrue(hurt["userid"]["player"] and hurt["attacker"]["player"])
        self.assertTrue(hurt["attackerentid"]["entity"] and not hurt["attackerentid"]["player"])

    def test_event_into_action_into_entity_node(self):
        from hammerless.core.logic import LLink, LNode, compile_graph
        ir = MapIR()
        nodes = [LNode("Hurt", "SCRIPT_EVENT", {"event": "player_hurt"}),
                 LNode("Give", "SCRIPT_CALL", {"fn": "player:GiveItem"}, consts={"p0": "weapon_pain_pills"}),
                 LNode("Wait", "DELAY", {"seconds": 1.0}),
                 LNode("Flow", "SCRIPT_CALL", {"fn": "Director.GetFurthestSurvivorFlow"}),
                 LNode("Far", "COMPARE", {"op": "GREATER"}, consts={"b": 5000.0}),
                 LNode("When", "WHEN", {"once": True}),
                 LNode("Stagger", "SCRIPT_CALL", {"fn": "player:Stagger"})]
        links = [LLink("Hurt", "happened", "Give", "run"), LLink("Hurt", "userid", "Give", "target", data=True),
                 LLink("Give", "then", "Wait", "in"),
                 LLink("Flow", "result", "Far", "a", data=True), LLink("Far", "result", "When", "condition", data=True),
                 LLink("When", "true", "Stagger", "run")]
        self.assertEqual(compile_graph(nodes, links, ir), [])
        script = next(v for k, v in ir.extra_scripts.items() if "logic_" in k)
        self.assertIn("function OnGameEvent_player_hurt(params)", script)
        self.assertIn("HL_Ctx.userid <- GetPlayerFromUserID(params.userid)", script)
        target = next(line for line in script.splitlines() if "local t = " in line)
        self.assertIn('::HL_Ctx.userid', target)
        self.assertIn('t.GiveItem("weapon_pain_pills");', script)
        self.assertRegex(script, r'::HL_S_give <- function\(\) \{[^}]*\}[^}]*EntFire\("hl_wait", "Trigger"')
        self.assertIn("HL_S_give();", script)                              # the event runs it directly
        self.assertIn("(Director.GetFurthestSurvivorFlow() > 5000.0)", script)
        relay = next(e for e in ir.entities if e.keyvalues.get("targetname") == "hl_when_true")
        self.assertIn(("RunScriptCode", "HL_S_stagger()"), [(o.input, o.parameter) for o in relay.outputs])


class TestScriptBlocks(unittest.TestCase):
    """For Each, variables, tables, text, vectors and raw script nodes."""

    def compile(self, nodes, links):
        from hammerless.core.logic import compile_graph
        ir = MapIR()
        self.assertEqual(compile_graph(nodes, links, ir), [])
        return ir, next(v for k, v in ir.extra_scripts.items() if "logic_" in k)

    def test_for_each_survivor(self):
        from hammerless.core.logic import LLink, LNode
        nodes = [LNode("Start", "MAP_START"), LNode("Each", "FOR_EACH", {"what": "SURVIVORS"}),
                 LNode("Give", "SCRIPT_CALL", {"fn": "player:GiveItem"}, consts={"p0": "pain_pills"}),
                 LNode("Count", "SET_VAR", {"name": "given", "scope": "PLAYER", "op": "ADD"}, consts={"value": 1.0}),
                 LNode("Msg", "FORMAT_TEXT", {"template": "{a} has {b} pills"}),
                 LNode("Got", "GET_VAR", {"name": "given", "scope": "PLAYER", "kind": "num"}),
                 LNode("Show", "SCRIPT_CALL", {"fn": "ShowMessage"})]
        links = [LLink("Start", "start", "Each", "run"), LLink("Each", "each", "Give", "run"),
                 LLink("Each", "item", "Give", "target", data=True), LLink("Give", "then", "Count", "run"),
                 LLink("Each", "item", "Count", "player", data=True), LLink("Count", "then", "Show", "run"),
                 LLink("Each", "item", "Msg", "a", data=True), LLink("Got", "result", "Msg", "b", data=True),
                 LLink("Each", "item", "Got", "player", data=True), LLink("Msg", "result", "Show", "p0", data=True)]
        ir, script = self.compile(nodes, links)
        self.assertIn("foreach (v in HL_Players(1))", script)
        self.assertRegex(script, r'::HL_L\["each"\] <- v;[^\n]*\n\s*HL_S_give\(\);')
        self.assertIn('HL_PVarSet((("each" in ::HL_L)', script)
        self.assertIn('HL_Str((("each" in ::HL_L)', script)
        self.assertIn('" has "', script)
        self.assertEqual(script.count("::HL_Players <- function"), 1)
        logic_auto = next(e for e in ir.entities if e.classname == "logic_auto")
        self.assertIn("HL_S_each()", [o.parameter for o in logic_auto.outputs])

    def test_table_into_zspawn_and_script_node(self):
        from hammerless.core.logic import LLink, LNode
        nodes = [LNode("Start", "MAP_START"),
                 LNode("Spawn", "MAKE_TABLE", {"fields": "type: int, pos: vec"}, consts={"f_type": 8.0,
                                                                                         "f_pos": (10, 20, 30)}),
                 LNode("Z", "SCRIPT_CALL", {"fn": "ZSpawn"}),
                 LNode("Code", "SCRIPT_CODE", {"code": "result = a ? \"spawned\" : \"no room\";"}),
                 LNode("Ok", "CHECK", {"op": "IS_SET"})]
        links = [LLink("Start", "start", "Z", "run"), LLink("Spawn", "table", "Z", "p0", data=True),
                 LLink("Z", "then", "Code", "run"), LLink("Z", "result", "Code", "a", data=True),
                 LLink("Code", "result", "Ok", "a", data=True)]
        _ir, script = self.compile(nodes, links)
        self.assertIn('ZSpawn({ ["type"] = (8.0).tointeger(), ["pos"] = Vector(10, 20, 30) })', script)
        self.assertIn('local a = (("z" in ::HL_R) ? ::HL_R["z"] : null);', script)
        self.assertIn('result = a ? "spawned" : "no room";', script)
        self.assertIn('::HL_R["code"] <- result;', script)


class TestPathProgressSpawns(unittest.TestCase):
    def test_at_least_one_tank(self):
        from hammerless.core.logic import LLink, LNode, compile_graph
        ir = MapIR()
        ir.settings.name = "m"
        nodes = [LNode("Tank At", "PATH_PROGRESS", {"from": 0.8, "to": 0.1}),
                 LNode("Tank", "SPAWN", {"what": "tank", "fewer_than": 1}),
                 LNode("Here", "SPAWN", {"what": "witch"}, pos=(10.0, 20.0, 30.0)),
                 LNode("Commons", "SPAWN", {"what": "common"})]
        links = [LLink("Tank At", "reached", "Tank", "spawn"), LLink("Tank At", "reached", "Here", "spawn")]
        problems = compile_graph(nodes, links, ir)
        self.assertEqual(len(problems), 1)                   # commons need a Where
        script = ir.extra_scripts["scripts/vscripts/hammerless/logic_m.nut"]
        self.assertIn('relay = "hl_tank_at", lo = 0.100, hi = 0.800', script)   # From/To in order
        self.assertIn("if (HL_Count.tank >= 1) return;", script)
        self.assertIn('HL_TrySpawn(8, "tank", 1);', script)
        self.assertIn("function OnGameEvent_tank_spawn", script)
        self.assertIn("HL_Retry();", script)
        logic = next(e for e in ir.entities if e.classname == "logic_script")
        self.assertEqual(logic.keyvalues.get("thinkfunction"), "HL_Think")
        relay = next(e for e in ir.entities if e.keyvalues.get("targetname") == "hl_tank_at")
        outs = {(o.target, o.input, o.parameter) for o in relay.outputs}
        self.assertIn(("hl_logic", "RunScriptCode", "HL_Spawn_tank()"), outs)
        spawner = next(e for e in ir.entities if e.classname == "commentary_zombie_spawner")
        self.assertIn((spawner.keyvalues["targetname"], "SpawnZombie", "witch"), outs)   # a Where, no limit: direct


class TestValueNodes(unittest.TestCase):
    def test_random_point_spawn_graph(self):
        # Path Progress >= Random Value -> When -> Spawn Tank; Random x 0.5 -> second When
        from hammerless.core.logic import LLink, LNode, compile_graph
        ir = MapIR()
        ir.settings.name = "m"
        nodes = [LNode("Path", "PROGRESS"), LNode("Rand", "RANDOM_VALUE", consts={"min": 0.2, "max": 1.0}),
                 LNode("Half", "MATH", {"op": "MULTIPLY"}, consts={"b": 0.5}),
                 LNode("Cmp", "COMPARE", {"op": "GREATER_EQUAL"}), LNode("Cmp2", "COMPARE", {"op": "GREATER_EQUAL"}),
                 LNode("When", "WHEN", {"once": True}), LNode("When2", "WHEN"), LNode("Tank", "SPAWN", {"what": "tank"}),
                 LNode("Alive", "INFECTED_COUNT", {"what": "tank", "mode": "ALIVE"}),
                 LNode("Has", "COMPARE", {"op": "GREATER_EQUAL"}, consts={"b": 1.0}), LNode("If", "IF"),
                 LNode("Msg", "MESSAGE")]
        links = [LLink("Path", "furthest", "Cmp", "a", True), LLink("Rand", "value", "Cmp", "b", True),
                 LLink("Cmp", "result", "When", "condition", True), LLink("When", "true", "Tank", "spawn"),
                 LLink("Rand", "value", "Half", "a", True), LLink("Path", "furthest", "Cmp2", "a", True),
                 LLink("Half", "value", "Cmp2", "b", True), LLink("Cmp2", "result", "When2", "condition", True),
                 LLink("Alive", "count", "Has", "a", True), LLink("Has", "result", "If", "condition", True),
                 LLink("When", "true", "If", "in"), LLink("If", "true", "Msg", "show")]
        self.assertEqual(compile_graph(nodes, links, ir), [])
        script = ir.extra_scripts["scripts/vscripts/hammerless/logic_m.nut"]
        self.assertIn("function HL_When_when() { return (HL_PathFurthest() >= HL_Random_rand()); }", script)
        self.assertIn("(HL_PathFurthest() >= (HL_Random_rand() * 0.5))", script)
        self.assertIn("RandomFloat(0.2, 1.0)", script)
        self.assertEqual(script.count("function HL_Random_rand()"), 1)     # one roll, shared
        self.assertIn("if ((HL_Alive(8) >= 1.0)) {\n    EntFire(\"hl_msg\", \"ShowHint\"", script)   # runs in script
        self.assertIn("w.fn.call(this)", script)
        self.assertIn("HL_ZSpawn(type)", script)                            # lifts the Director's limit
        self.assertIn("HL_When_Think();", script)

    def test_director_spawns_switch(self):
        from hammerless.core.gamefiles import director_option_lines
        ir = MapIR()
        ir.settings.director_enabled = True
        ir.settings.dir_spawns = {**ir.settings.dir_spawns, "tank": False, "hunter": False}
        lines = director_option_lines(ir)
        self.assertIn("    TankLimit = 0", lines)
        self.assertIn("    HunterLimit = 0", lines)
        self.assertEqual(sum(l.strip().startswith("TankLimit") for l in lines), 1)


class TestCollisionNode(unittest.TestCase):
    def test_nav_runs_through_solid_wall(self):
        # Collision: Blocks Players on, Blocks Nav Mesh off -> solid, but the nav mesh goes through it
        from hammerless.core import navpredict
        from hammerless.core.logic import LNode, compile_graph
        counts = {}
        for players, nav in ((True, True), (True, False), (False, True)):
            ir = MapIR()
            ir.settings.name = "c"
            ir.brushes.append(g.box_brush((-600, -128, -64), (600, 128, 0), "dev/dev_measuregeneric01b", "floor"))
            ir.brushes.append(g.box_brush((-8, -128, 0), (8, 128, 200), "dev/dev_measuregeneric01b", "wall"))
            ir.entities.append(Entity("info_landmark", (-400, 0, 32), (0, 0, 0), {"targetname": "a"}))
            ir.entities.append(Entity("info_survivor_position", (-400, 40, 2), (0, 0, 0), {"Order": "1"}))
            self.assertEqual(compile_graph([LNode("Col", "COLLISION", {"players": players, "nav": nav}, "wall")], [], ir), [])
            text, _ = build_vmf(ir)
            m = navpredict._predict(text, [])
            counts[(players, nav)] = sum(1 for a in m.areas if a.centre[0] > 50)
            classes = {e.classname for e in ir.entities}
            if (players, nav) == (True, False):
                self.assertIn("func_brush", classes)
            if (players, nav) == (False, True):
                self.assertTrue({"func_illusionary", "func_nav_blocker"} <= classes)
        self.assertEqual(counts[(True, True)], 0)          # plain wall: no nav beyond
        self.assertGreater(counts[(True, False)], 0)       # nav goes through


class TestMover(unittest.TestCase):
    def test_gate_button_export(self):
        import re
        from hammerless.core.entities import gate_button
        ir = MapIR()
        ir.brushes.append(g.box_brush((-256, -256, -64), (256, 256, 0), "dev/dev_measuregeneric01b", "floor"))
        ir.entities.append(Entity("info_survivor_position", (-100, 0, 2), (0, 0, 0), {"Order": "1"}))
        for part in gate_button().parts:
            ir.entities.append(part.entity)
        text, rep = build_vmf(ir)
        self.assertIsNotNone(text, rep.errors)
        gate = re.search(r'"classname" "func_movelinear"[^}]*', text).group(0)
        self.assertIn('"movedir" "90 0 0"', gate)          # down
        self.assertIn('"movedistance" "128"', gate)        # auto = its height
        self.assertIn('"speed" "32"', gate)                # 128 units / 4 seconds
        self.assertNotIn("move_time", text)
        self.assertRegex(text, r'"OnPressed" "gate_1.Open')
        self.assertRegex(text, r'"OnPressed" "director.ForcePanicEvent')


class TestLaddersAndClimbs(unittest.TestCase):
    """Ladders and Zombie Climbs in the nav mesh. The ladder numbers are the game's own
    (nav_generate on the same map: top 192.03, bottom -1, length 194, facing west)."""

    def tower_map(self, height=192.0, zombies=False, climb=False):
        from hammerless.core.entities import zombie_climb, ladder, zombie_ladder
        ir = MapIR()
        m = "dev/dev_measuregeneric01b"
        ir.brushes.append(g.box_brush((-512, -512, -64), (512, 512, 0), m, "ground"))
        ir.brushes.append(g.box_brush((128, -256, 0), (512, 256, height), m, "tower"))
        ir.entities.append(Entity("info_landmark", (-300, 0, 32), (0, 0, 0), {"targetname": "lm"}))
        # the preset's climbable side faces +X; turn it to face -X (west), away from the tower
        preset = zombie_ladder(height) if zombies else ladder(height)
        vol = preset.parts[0].entity
        for b in vol.brushes:
            for f in b.faces:
                f.verts = [(124 - x, -160 - y, z) for x, y, z in f.verts]   # turned 180 degrees
        ir.entities.append(vol)
        if climb:
            for part in zombie_climb("wall").parts:
                ir.entities.append(part.entity)
        return ir

    def test_ladder_matches_game(self):
        from hammerless.core.navpredict import predict
        text, _rep = build_vmf(self.tower_map())
        mesh = predict(text, [])
        self.assertEqual(len(mesh.ladders), 1)
        lad = mesh.ladders[0]
        self.assertEqual(lad.direction, 3)                   # WEST
        self.assertAlmostEqual(lad.bottom[2], -1.0)
        self.assertAlmostEqual(lad.top[2], 192.03125)
        self.assertAlmostEqual(lad.length, 194.0)
        self.assertEqual(lad.width, 34.0)
        by = mesh.by_id()
        self.assertGreater(by[lad.top_forward].nw[2], 190)   # the tower top
        self.assertLess(by[lad.bottom_area].nw[2], 1)        # the floor
        self.assertIn(lad.id, by[lad.bottom_area].ladders[0])
        self.assertTrue(any(a.nw[2] > 190 for a in mesh.areas))   # sampling carried on up the ladder

    def test_native_area_pipeline_matches_python(self):
        # the DLL's area stages give exactly the Python mesh: ladders, a slope, a ledge, stairs,
        # wall climbs and a Zombie Climb
        from hammerless.core import fastnav
        from hammerless.core.navpredict import check_native
        if not fastnav.available():
            self.skipTest("native DLL not built")
        ir = self.tower_map(climb=True)
        m = "dev/dev_measuregeneric01b"
        ir.brushes.append(g.box_brush((-400, 200, 0), (-200, 400, 40), m, "ledge"))
        for i in range(6):                                   # stairs up to the ledge
            ir.brushes.append(g.box_brush((-400 + 32 * i, 100, 0), (-368 + 32 * i, 200, 8 * (i + 1)), m, f"stair{i}"))
        text, _rep = build_vmf(ir)
        self.assertIsNone(check_native(text, [], [], True))

    def test_zombie_ladder_export(self):
        text, _rep = build_vmf(self.tower_map(zombies=True))
        self.assertIn('"classname" "func_simpleladder"', text)
        self.assertIn('"team" "2"', text)
        self.assertIn('"normal.x" "-1.000000"', text)
        text, _rep = build_vmf(self.tower_map(zombies=False))
        self.assertIn('"classname" "func_ladder"', text)
        self.assertNotIn('"team"', text)

    def test_zombie_climb_link(self):
        from hammerless.core.nav import collect_climbs
        from hammerless.core.navpredict import predict
        ir = self.tower_map(height=128.0)
        ir.entities = [e for e in ir.entities if e.classname != "func_ladder"]
        ir.entities.append(Entity("info_landmark", (300, 0, 160), (0, 0, 0), {"targetname": "lm_top"}))
        from hammerless.core.entities import zombie_climb
        bottom, top = zombie_climb("wall").parts
        bottom.entity.origin, top.entity.origin = (100.0, 0.0, 1.0), (160.0, 0.0, 129.0)
        ir.entities += [bottom.entity, top.entity]
        climbs, problems = collect_climbs(ir)
        self.assertEqual(len(climbs), 1)
        self.assertEqual(problems, [])
        text, _rep = build_vmf(ir)
        self.assertNotIn("hammerless_zombie_climb", text)    # never written to the map
        mesh = predict(text, [], None, climbs)
        self.assertEqual(mesh.problems, [])
        by = mesh.by_id()
        ups = [(a, by[i]) for a in mesh.areas for c in a.connections for i in c
               if a.nw[2] < 1 and by[i].nw[2] > 120]
        self.assertTrue(ups)

    def test_wall_climbs(self):
        # Zombies Climb Walls: links up (and back down) a 128-unit wall, none up a 300-unit one
        from hammerless.core.navpredict import predict
        for height, expect_up in ((128.0, True), (300.0, False)):
            ir = self.tower_map(height=height)
            ir.entities = [e for e in ir.entities if e.classname != "func_ladder"]
            ir.entities.append(Entity("info_landmark", (300, 0, height + 32), (0, 0, 0), {"targetname": "lm_top"}))
            text, _rep = build_vmf(ir)
            for on in (False, True):
                mesh = predict(text, [], None, (), on)
                by = mesh.by_id()
                ups = [1 for a in mesh.areas for c in a.connections for i in c if a.nw[2] < 1 and by[i].nw[2] > height - 5]
                self.assertEqual(bool(ups), on and expect_up, (height, on))

    def test_climb_problems(self):
        from hammerless.core.entities import zombie_climb
        from hammerless.core.nav import collect_climbs
        ir = MapIR()
        bottom, top = zombie_climb("tall").parts
        top.entity.origin = (48.0, 0.0, 300.0)
        ir.entities += [bottom.entity, top.entity]
        _c, problems = collect_climbs(ir)
        self.assertTrue(any("Zombie Ladder" in p for p in problems))
        ir.entities = [bottom.entity]
        _c, problems = collect_climbs(ir)
        self.assertTrue(any("both a bottom and a top" in p for p in problems))


class TestNavPredict(unittest.TestCase):
    """The nav mesh our generator predicts, marked and analysed like the game's."""

    def predict(self, gap=False, end_dz=0.0):
        from hammerless.core.entities import end_safe_room, start_safe_room
        from hammerless.core.navanalysis import analyse
        from hammerless.core.navpredict import predict
        from hammerless.core.nav import collect_regions
        ir = MapIR()
        ir.brushes.append(g.box_brush((-400, -300, -64), (600 if gap else 1500, 500, 0),
                                      "dev/dev_measuregeneric01b", "ground"))
        if gap:
            ir.brushes.append(g.box_brush((700, -300, -64), (1500, 500, 0), "dev/dev_measuregeneric01b", "far"))
        for preset, dx, dz in ((start_safe_room("a"), -330, 0.0), (end_safe_room("c1m2_streets", "b"), 1100, end_dz)):
            for part in preset.parts:
                for b in ([part.brush] if part.brush else []) + (part.entity.brushes if part.entity else []):
                    for f in b.faces:
                        f.verts = [(x + dx, y, z + dz) for x, y, z in f.verts]
                if part.entity and part.entity.origin is not None:
                    x, y, z = part.entity.origin
                    part.entity.origin = (x + dx, y, z + dz)
                if part.brush:
                    ir.brushes.append(part.brush)
                if part.entity:
                    ir.entities.append(part.entity)
        text, rep = build_vmf(ir)
        regions, _ = collect_regions(ir)
        return analyse(predict(text, regions))

    def test_walkable(self):
        rep = self.predict()
        self.assertTrue(rep.start and rep.end)
        self.assertTrue(rep.end_reached)

    def test_gap_breaks_path(self):
        rep = self.predict(gap=True)
        self.assertFalse(rep.end_reached)
        self.assertIsNotNone(rep.break_area)

    def test_34_unit_step_breaks_path(self):      # L4D2 won't link a 19-64 unit ledge
        self.assertFalse(self.predict(end_dz=34).end_reached)


class TestNavVisibility(unittest.TestCase):
    def lists(self, wall):
        from hammerless.core.collision import CollisionWorld
        from hammerless.core.navfile import NavArea
        from hammerless.core.navvis import Visibility
        ir = box_room_ir()
        if wall:
            ir.brushes.append(g.box_brush((-16, -256, 0), (16, 256, 256), "dev/dev_measuregeneric01b", "wall"))
        text, _ = build_vmf(ir)
        areas = [NavArea(1, 0, (-200, -50, 0), (-100, 50, 0), 0, 0), NavArea(2, 0, (100, -50, 0), (200, 50, 0), 0, 0)]
        return Visibility(CollisionWorld.from_vmf(text), areas).run()

    def test_open_room(self):
        v = self.lists(False)
        self.assertIn(2, v[1])
        self.assertIn(1, v[1])                    # an area always sees itself

    def test_wall_blocks(self):
        v = self.lists(True)
        self.assertNotIn(2, v[1])
        self.assertNotIn(1, v[2])


class TestFlowReport(unittest.TestCase):
    def test_parse(self):
        from hammerless.core.compile import parse_flow
        self.assertEqual(parse_flow("HAMMERLESS_FLOW ok 2380.31"), {"state": "ok", "length": 2380.31})
        self.assertEqual(parse_flow("HAMMERLESS_FLOW broken at 87.5 3512.5 0.97"),
                         {"state": "broken", "connected": False, "location": (87.5, 3512.5, 0.97)})
        self.assertTrue(parse_flow("HAMMERLESS_FLOW broken connected 1 2 3")["connected"])
        self.assertEqual(parse_flow("HAMMERLESS_FLOW nonav"), {"state": "nonav"})
        self.assertIsNone(parse_flow("something else"))

    def test_ready_script_reports(self):
        from hammerless.core.gamefiles import READY_SCRIPT
        self.assertIn("HLR_FlowReport();", READY_SCRIPT)
        self.assertIn("GetMaxFlowDistance", READY_SCRIPT)


class TestAutoDetail(unittest.TestCase):
    def test_rules(self):
        from hammerless.core.build import is_auto_detail
        wall = g.box_brush((0, 0, 0), (512, 16, 256), "concrete/wall")
        crate = g.box_brush((0, 0, 0), (64, 64, 64), "wood/crate")
        sky = g.box_brush((0, 0, 0), (64, 64, 64), "tools/toolsskybox")
        cyl = Brush([Polygon([(math.cos(a) * 64, math.sin(a) * 64, 0), (math.cos(a + 0.4) * 64, math.sin(a + 0.4) * 64, 0),
                              (math.cos(a + 0.4) * 64, math.sin(a + 0.4) * 64, 256), (math.cos(a) * 64, math.sin(a) * 64, 256)])
                     for a in [i * 0.4 for i in range(16)]])
        self.assertFalse(is_auto_detail(wall, "SMART"))
        self.assertTrue(is_auto_detail(crate, "SMART"))
        self.assertFalse(is_auto_detail(sky, "ALL"))
        self.assertTrue(is_auto_detail(wall, "ALL"))
        self.assertFalse(is_auto_detail(crate, "OFF"))
        self.assertGreaterEqual(len(g.merge_coplanar(cyl.faces)), 9)

    def test_object_choice(self):
        from hammerless.core.build import is_detail
        wall = g.box_brush((0, 0, 0), (512, 16, 256), "concrete/wall")
        crate = g.box_brush((0, 0, 0), (64, 64, 64), "wood/crate")
        clip = g.box_brush((0, 0, 0), (512, 16, 256), "tools/toolsplayerclip")
        self.assertFalse(is_detail(wall, "SMART"))
        wall.detail = "DETAIL"
        self.assertTrue(is_detail(wall, "SMART"))
        self.assertTrue(is_detail(wall, "OFF"))                   # the object's choice beats the map's rule
        crate.detail = "WORLD"
        self.assertFalse(is_detail(crate, "ALL"))
        clip.detail = "DETAIL"
        self.assertFalse(is_detail(clip, "ALL"))                  # tool brushes stay world

    def test_vmf_object_choice(self):
        ir = box_room_ir()
        big = g.box_brush((0, 0, 0), (512, 512, 300), "wood/crate", "big_box")
        big.detail = "DETAIL"
        small = g.box_brush((600, 0, 0), (632, 32, 32), "wood/crate", "thin_wall")
        small.detail = "WORLD"
        ir.brushes += [big, small]
        text, rep = build_vmf(ir)
        self.assertEqual(text.count('"func_detail"'), 1)
        sid = {src: i for i, src in rep.solid_sources.items()}
        at = text.index('"func_detail"')                         # (the world's solids come before it)
        self.assertGreater(text.index(f'"id" "{sid["big_box"]}"'), at)
        self.assertLess(text.index(f'"id" "{sid["thin_wall"]}"'), at)
        self.assertTrue(any("1 brush(es) set to Detail" in i for i in rep.info), rep.info)
        # with the auto shell off, the chosen detail still happens, with a note about sealing
        ir.settings.auto_seal = False
        text, rep = build_vmf(ir)
        self.assertEqual(text.count('"func_detail"'), 1)
        self.assertTrue(any("don't seal" in i and "big_box" in i for i in rep.info), rep.info)

    def test_vmf(self):
        ir = box_room_ir()
        ir.brushes.append(g.box_brush((0, 0, 0), (32, 32, 32), "wood/crate", "crate"))
        text, rep = build_vmf(ir)
        self.assertEqual(text.count('"classname" "func_detail"'), 1)
        ir.settings.auto_seal = False           # func_detail doesn't seal: only with the shell
        text, rep = build_vmf(ir)
        self.assertNotIn("func_detail", text)


class TestAcoustics(unittest.TestCase):
    """Automatic soundscapes: a roofed room (x < 512) beside open ground, all on one floor at z 0."""

    @staticmethod
    def cast(origin, direction, distance):
        x, y, z = origin
        dx, dy, dz = direction
        if dz < 0:
            if x < 512 and z > 256:                  # the roof, from above
                return ((z - 256) / -dz, 1.0) if (z - 256) / -dz <= distance else None
            return (z / -dz, 1.0) if z / -dz <= distance else None
        if dz > 0 and x < 512 and z < 256:           # the ceiling, from below
            return ((256 - z) / dz, -1.0) if (256 - z) / dz <= distance else None
        return None

    def test_zones_and_files(self):
        from hammerless.core import acoustics as a
        spots, spacing = a.sample_spots(((0, 0, 0), (1024, 512, 300)), self.cast)
        self.assertTrue(any(s.floor_z == 256 for s in spots))            # the roof is a surface...
        spots = a.reachable(spots, [(100, 100, 0)])
        self.assertTrue(all(s.floor_z == 0 for s in spots))              # ...nobody can reach
        a.analyse(spots, self.cast, rays=48)
        for s in spots:
            self.assertEqual(s.kind, "INDOOR" if s.pos[0] < 512 else "OUTDOOR", s.pos)
        zl = a.zones(spots, spacing)
        self.assertEqual(sorted(z.kind for z in zl), ["INDOOR", "OUTDOOR"])
        indoor = next(z for z in zl if z.kind == "INDOOR")
        self.assertTrue(0 < len(indoor.positions) <= a.MAX_POSITIONS)
        self.assertEqual(len(indoor.volumes), len(indoor.positions))
        self.assertTrue(all(abs(p[0] - 512) <= spacing for p in indoor.positions))   # at the opening
        text = a.soundscape_file("m", "URBAN", zl)
        self.assertIn('"hammerless.m.outdoor"', text)
        self.assertEqual(text.count('"dsp"\t"1"'), 3)                    # outdoor, sheltered, the indoor zone
        self.assertIn('"positionoverride"\t"0"', text)
        ents = a.entities(zl, "m", spacing)
        triggers = [e for e in ents if e.classname == "trigger_soundscape"]
        self.assertEqual(len(triggers), 2)
        self.assertEqual(sum(len(e.brushes) for e in triggers), len(spots))
        env = [e for e in ents if e.classname == "env_soundscape_triggerable"]
        self.assertEqual({e.keyvalues["soundscape"] for e in env},
                         {"hammerless.m.outdoor", a.soundscape_name("m", zl, zl.index(indoor))})
        self.assertEqual(sum(e.classname == "info_target" for e in ents), len(indoor.positions))
        reverb_only = a.soundscape_file("m", None, zl)                   # the engine's reverb, no ambience
        self.assertEqual(reverb_only.count('"dsp"\t"1"'), 3)
        self.assertNotIn("playsoundscape", reverb_only)
        self.assertNotIn("playlooping", reverb_only)


class TestVisData(unittest.TestCase):
    """The Visibility viewer's data: portals, rendering load, vis cost and who caused it."""

    def _fixture(self, name):
        import zipfile
        return zipfile.ZipFile(os.path.join(os.path.dirname(__file__), "..", "fixtures", "vis", "rooms_portal.zip")).read(name)

    def test_portals_and_load(self):
        import tempfile
        from hammerless.core import visdata
        with tempfile.TemporaryDirectory() as d:
            prt = os.path.join(d, "m.prt")
            with open(prt, "wb") as f:
                f.write(self._fixture("rooms_portal.prt"))
            p = visdata.read_portals(prt)
        self.assertGreater(len(p.polys), 0)
        self.assertEqual(len(p.area), len(p.polys))
        self.assertTrue((p.area > 0).all())
        rl = visdata.render_load(self._fixture("rooms_portal.vvis_full.bsp"))
        self.assertEqual(len(rl.positions) % 3, 0)
        self.assertEqual(len(rl.value), len(rl.positions))
        self.assertTrue(0 < rl.min_faces <= rl.max_faces)
        with self.assertRaises(ValueError):                  # no vis yet: say so
            visdata.render_load(self._fixture("rooms_portal.bsp"))

    def test_cost_and_owners(self):
        import json
        import tempfile
        import numpy as np
        from hammerless.core import visdata
        with tempfile.TemporaryDirectory() as d:
            # one world brush (a box 0..64), and two portals: one on the extended plane of its x=64 side,
            # one in open space
            vmf = os.path.join(d, "m.built.vmf")
            T, N = "\t", "\n"
            sides = "".join(f'{T * 2}side{N}{T * 2}{{{N}{T * 3}"plane" "{pl}"{N}{T * 2}}}{N}' for pl in (
                "(64 0 0) (64 64 0) (64 64 64)", "(0 0 0) (0 64 64) (0 64 0)", "(0 0 64) (64 0 64) (64 64 64)",
                "(0 0 0) (64 64 0) (64 0 0)", "(0 0 0) (64 0 0) (64 0 64)", "(0 64 0) (0 64 64) (64 64 64)"))
            with open(vmf, "w", encoding="utf-8") as f:
                f.write(f'world{N}{{{N}{T}solid{N}{T}{{{N}{T * 2}"id" "7"{N}' + sides + f"{T}}}{N}}}{N}")
            with open(os.path.join(d, "m.brushes.json"), "w", encoding="utf-8") as f:
                json.dump({"7": "Crate (part 2)"}, f)
            portals = visdata.Portals(2, np.array([0, 0]), np.array([1, 1]),
                                      [np.array([[64.0, 100, 0], [64, 200, 0], [64, 200, 64], [64, 100, 64]]),
                                       np.array([[300.0, 0, 10], [400, 0, 10], [400, 100, 10], [300, 100, 10]])],
                                      np.array([6400.0, 10000]), np.array([64.0, 100]))
            owners = visdata.portal_owners(portals, vmf)
            self.assertEqual(owners, ["Crate", None])
            cost = os.path.join(d, "m.viscost")
            with open(cost, "w") as f:
                f.write(N.join(["hlvvis-cost 1", "portals 2 threads 4 flow_seconds 2.0", "0.3 0.1", "1.2 0.4", ""]))
            c = visdata.read_costs(cost, 2)
            self.assertAlmostEqual(float(c.share().sum()), 1.0)
            self.assertIsNone(visdata.read_costs(cost, 3))        # from another build: ignored
            ranking = visdata.cost_by_object(c, owners)
            self.assertEqual(ranking[0][0], None)                 # the open-space portal cost more
            self.assertAlmostEqual(ranking[1][1], 0.2)


class TestVisCompiler(unittest.TestCase):
    """Settings > Compile > Vis Compiler: our hlvvis.exe in place of vvis.exe, falling back to vvis."""

    def _job(self, opts):
        import tempfile
        from hammerless.core import compile as cc
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "left4dead2", "maps"))
        work = tempfile.mkdtemp()
        vmf = os.path.join(work, "m.vmf")
        open(vmf, "w").write("world {}")
        return cc.CompileJob(cc.Tools(root), vmf, opts, copy_to_game=False)

    def test_steps(self):
        from hammerless.core import compile as cc
        vvis = lambda job: dict(job.steps)["vvis"]
        self.assertTrue(vvis(self._job(cc.CompileOptions()))[0].endswith("vvis.exe"))
        if os.path.exists(cc.HLVVIS):
            job = self._job(cc.CompileOptions(vis_tool="HAMMERLESS", vis="FAST"))
            self.assertEqual(vvis(job)[0], cc.HLVVIS)
            self.assertIn("-fast", vvis(job))
            self.assertTrue(job._valve_vvis[0].endswith("vvis.exe"))
        # vvis options ours doesn't know: Valve's vvis
        job = self._job(cc.CompileOptions(vis_tool="HAMMERLESS", extra_vvis="-radius_override 2000"))
        self.assertTrue(vvis(job)[0].endswith("vvis.exe"))

    def test_choice_isnt_part_of_the_build_stamp(self):
        from hammerless.core import compile as cc
        a = self._job(cc.CompileOptions())
        b = self._job(cc.CompileOptions(vis_tool="HAMMERLESS"))
        b.vmf, b._vmf_bytes = a.vmf, getattr(a, "_vmf_bytes", None)
        self.assertEqual(cc._opts_rest(repr(a._opts)), cc._opts_rest(repr(b._opts)))

    def test_hlvvis_matches_vvis(self):
        """hlvvis on a small compiled map (three rooms, an area portal) writes the same file as L4D2's vvis,
        in full and -fast mode. The fixture's pakfile is empty (no game files in the repo)."""
        import subprocess
        import tempfile
        import zipfile
        from hammerless.core import compile as cc
        if not os.path.exists(cc.HLVVIS):
            self.skipTest("hlvvis.exe not built")
        z = zipfile.ZipFile(os.path.join(os.path.dirname(__file__), "..", "fixtures", "vis", "rooms_portal.zip"))
        for mode in ("full", "fast"):
            with tempfile.TemporaryDirectory() as d:
                base = os.path.join(d, "rooms_portal")
                for ext in (".bsp", ".prt"):
                    with open(base + ext, "wb") as f:
                        f.write(z.read("rooms_portal" + ext))
                r = subprocess.run([cc.HLVVIS] + (["-fast"] if mode == "fast" else []) + [base],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout)
                with open(base + ".bsp", "rb") as f:
                    self.assertEqual(f.read(), z.read(f"rooms_portal.vvis_{mode}.bsp"), mode)
                self.assertFalse(os.path.exists(base + ".hlvvis.tmp"))

    def test_falls_back_to_valve(self):
        import sys
        from hammerless.core import compile as cc
        job = self._job(cc.CompileOptions(vis_tool="HAMMERLESS"))
        old = cc.HLVVIS
        try:
            cc.HLVVIS = sys.executable                    # a stand-in "ours" that exits with 3 (radial vis)
            job.steps = [("vvis", [sys.executable, "-c", "import sys; sys.exit(3)"])]
            job._valve_vvis = [sys.executable, "-c", "print('VALVE VVIS RAN')"]
            job._run_steps()
        finally:
            cc.HLVVIS = old
        lines = []
        while not job._q.empty():
            lines.append(job._q.get())
        text = " | ".join(str(x) for x in lines)
        self.assertIn("radial", text)
        self.assertIn("VALVE VVIS RAN", text)
        self.assertIn(("OK",), lines)


class TestCompileSkip(unittest.TestCase):
    def test_bsp_has_lighting(self):
        # a lit map loaded into a running game switches mat_fullbright back off (the engine leaves it
        # on after a map without lighting): detect lighting from the LDR / HDR lighting lumps
        import struct
        import tempfile
        from hammerless.core.compile import bsp_has_lighting
        with tempfile.TemporaryDirectory() as d:
            # lump_t in L4D2: version, offset, length, fourCC. A map compiled without lighting still has
            # every lump's offset set, with length 0 (that read as lit before: Analyze and Quick maps)
            for lump, length, lit in ((None, 0, False), (8, 4, True), (53, 4, True), (53, 0, False)):
                head = bytearray(b"VBSP" + struct.pack("<i", 21) + bytes(16 * 64))
                for k in range(64):
                    struct.pack_into("<iiii", head, 8 + 16 * k, 0, len(head), 0, 0)
                if lump is not None:
                    struct.pack_into("<iiii", head, 8 + 16 * lump, 0, len(head), length, 0)
                path = os.path.join(d, "m.bsp")
                with open(path, "wb") as f:
                    f.write(head + bytes(4))
                self.assertEqual(bsp_has_lighting(path), lit)
            self.assertFalse(bsp_has_lighting(os.path.join(d, "missing.bsp")))

    def test_up_to_date(self):
        import tempfile
        from hammerless.core import compile as cc
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "left4dead2", "maps"))
        work = tempfile.mkdtemp()
        vmf = os.path.join(work, "m.vmf")
        open(vmf, "w").write("world {}")
        import struct

        def bsp(pak: bytes, other: bytes = b"geometry") -> bytes:
            # a minimal BSP: lump 1 holds 'other', lump 40 (the pakfile) holds 'pak'
            header = bytearray(b"VBSP" + struct.pack("<i", 21) + bytes(16 * 64) + struct.pack("<i", 1))
            body = other + pak
            base = len(header)
            struct.pack_into("<iiii", header, 8 + 16 * 1, 0, base, len(other), 0)
            struct.pack_into("<iiii", header, 8 + 16 * 40, 0, base + len(other), len(pak), 0)
            return bytes(header) + body
        open(os.path.join(work, "m.bsp"), "wb").write(bsp(b"pak"))
        tools = cc.Tools(root)
        job = cc.CompileJob(tools, vmf, "FAST", skip_if_unchanged=True)
        self.assertFalse(job.up_to_date())                       # never compiled
        open(os.path.join(work, "m.stamp"), "w").write(job._stamp())
        open(os.path.join(work, "m.built.opts"), "w").write(repr(job._opts))
        self.assertFalse(job.up_to_date())                       # game has no BSP yet
        open(os.path.join(tools.maps_dir, "m.bsp"), "wb").write(bsp(b"pak"))
        self.assertTrue(job.up_to_date())
        # the game saves its stringtable into its copy's pakfile on every load: still the same map
        open(os.path.join(tools.maps_dir, "m.bsp"), "wb").write(bsp(b"pak + stringtable dictionary"))
        self.assertTrue(job.up_to_date())
        open(os.path.join(tools.maps_dir, "m.bsp"), "wb").write(bsp(b"pak", b"other map"))
        self.assertFalse(job.up_to_date())                       # a different compile
        open(os.path.join(tools.maps_dir, "m.bsp"), "wb").write(bsp(b"pak"))
        self.assertFalse(cc.CompileJob(tools, vmf, "NORMAL").up_to_date())   # other options
        open(vmf, "w").write("world { changed }")
        self.assertFalse(job.up_to_date())

    def test_lighting_bake_reused(self):
        """A lighting bake (fast vis) serves a later bake as is; a full build only adds the full vis."""
        import tempfile
        from dataclasses import replace
        from hammerless.core import compile as cc
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "left4dead2", "maps"))
        work = tempfile.mkdtemp()
        vmf = os.path.join(work, "m.vmf")
        open(vmf, "w").write("world { }")
        tools = cc.Tools(root)
        full = cc.PRESETS["NORMAL"]
        bake = replace(full, vis="FAST")
        for name in ("m.bsp", os.path.join(tools.maps_dir, "m.bsp")):
            open(os.path.join(work, name), "wb").write(b"VBSP" + bytes(1100))
        open(os.path.join(work, "m.built.vmf"), "w").write("world { }")
        open(os.path.join(work, "m.built.opts"), "w").write(repr(bake))
        job = cc.CompileJob(tools, vmf, bake, skip_if_unchanged=True)
        open(os.path.join(work, "m.stamp"), "w").write(job._stamp())
        self.assertTrue(job.up_to_date())                                    # bake again: nothing to do
        later = cc.CompileJob(tools, vmf, full, skip_if_unchanged=True)
        self.assertFalse(later.up_to_date())                                 # needs the full vis
        later._vmf_bytes = b"world { }"
        self.assertEqual(len(later._choose_steps()), 3)                     # no portal file kept: full compile
        open(os.path.join(work, "m.built.prt"), "w").write("PRT1")
        steps = [n for n, _cmd in later._choose_steps()]
        self.assertEqual(steps, ["vbsp (entities only)", "vvis"])           # lighting kept, vis added
        self.assertEqual(later._built_vis, "FULL")
        open(os.path.join(work, "m.built.opts"), "w").write(repr(full))     # after a full build...
        self.assertTrue(cc.CompileJob(tools, vmf, bake, skip_if_unchanged=True).up_to_date())   # ...a bake is free

    def test_lighting_tracked_apart(self):
        """A compile for the nav analysis (no lighting) is finished by Build with vrad alone; when lights
        change while the vis is being completed, vvis runs before vrad."""
        import tempfile
        from dataclasses import replace
        from hammerless.core import compile as cc
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "left4dead2", "maps"))
        work = tempfile.mkdtemp()
        vmf = os.path.join(work, "m.vmf")
        open(vmf, "w").write("world { }")
        tools = cc.Tools(root)
        full = cc.PRESETS["NORMAL"]
        for name in ("m.bsp", os.path.join(tools.maps_dir, "m.bsp")):
            open(os.path.join(work, name), "wb").write(b"VBSP" + bytes(1100))
        open(os.path.join(work, "m.built.vmf"), "w").write("world { }")
        open(os.path.join(work, "m.built.prt"), "w").write("PRT1")
        open(os.path.join(work, "m.built.opts"), "w").write(repr(replace(full, rad="SKIP")))   # Analyze compiled it
        analysis = cc.CompileJob(tools, vmf, replace(full, rad="SKIP"), copy_to_game=False, skip_if_unchanged=True)
        open(os.path.join(work, "m.stamp"), "w").write(analysis._stamp())
        self.assertTrue(analysis.up_to_date())                               # Analyze again: nothing to compile
        build = cc.CompileJob(tools, vmf, full, skip_if_unchanged=True)
        self.assertFalse(build.up_to_date())                                 # no lighting yet
        build._vmf_bytes = b"world { }"
        self.assertEqual([n for n, _c in build._choose_steps()], ["vbsp (entities only)", "vrad"])
        self.assertEqual((build._built_vis, build._built_rad), ("FULL", "NORMAL"))
        open(os.path.join(work, "m.built.opts"), "w").write(repr(full))
        self.assertTrue(cc.CompileJob(tools, vmf, replace(full, rad="SKIP"), copy_to_game=False,
                                      skip_if_unchanged=True).up_to_date())    # a lit build serves the analysis
        # a fast-vis bake, then the lights change: full vis first, then the lighting
        open(os.path.join(work, "m.built.opts"), "w").write(repr(replace(full, vis="FAST")))
        open(os.path.join(work, "m.built.vmf"), "w").write('world { } entity { "classname" "light" }')
        build = cc.CompileJob(tools, vmf, full, skip_if_unchanged=True)
        build._vmf_bytes = b'world { } entity { "classname" "light" "_light" "255 0 0 200" }'
        steps = [n for n, _c in build._choose_steps()]
        self.assertEqual(steps[1:], ["vvis", "vrad"], steps)

    def test_fast_is_one_lighting_pass(self):
        from hammerless.core.compile import PRESETS
        self.assertIn("-hdr", PRESETS["FAST"].vrad_args())
        self.assertIn("-hdr", PRESETS["NORMAL"].vrad_args())


class TestPresetFields(unittest.TestCase):
    def test_targets_exist(self):
        from hammerless.core.entities import PRESET_FIELDS
        for key, fields in PRESET_FIELDS.items():
            parts = {p.name: p.entity for p in PRESETS[key].parts}
            for f in fields:
                for part, kind, k in f.targets:
                    e = parts.get(part)
                    self.assertIsNotNone(e, (key, part))
                    if kind == "kv":
                        self.assertIn(k, e.keyvalues, (key, part, k))
                    else:
                        self.assertTrue(any(o.input == k for o in e.outputs), (key, part, k))


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

    def test_region_excludes_roof(self):
        # Roof nav right above the room must not be marked CHECKPOINT (breaks the flow).
        from hammerless.core.entities import ROOM_Z
        from hammerless.core.nav import collect_regions
        regions, _ = collect_regions(self.preset_ir())
        for r in regions:
            self.assertLessEqual(r.maxs[2], ROOM_Z + 1, r)
            self.assertLess(r.mins[2], 0)

    def test_marks_changed(self):
        import tempfile
        from hammerless.core import compile as cc
        root = tempfile.mkdtemp()
        d = os.path.join(root, "left4dead2", "scripts", "vscripts", "hammerless")
        os.makedirs(d)
        tools = cc.Tools(root)
        self.assertFalse(cc.nav_marks_changed(tools, "m"))          # no marks at all
        open(os.path.join(d, "navmark_m.nut"), "w").write("a")
        self.assertTrue(cc.nav_marks_changed(tools, "m"))           # never built with marks
        open(os.path.join(d, "navmark_m.used"), "w").write("a")
        self.assertFalse(cc.nav_marks_changed(tools, "m"))
        open(os.path.join(d, "navmark_m.nut"), "w").write("b")
        self.assertTrue(cc.nav_marks_changed(tools, "m"))

    def test_region_not_in_vmf(self):
        text, rep = build_vmf(self.preset_ir())
        self.assertTrue(rep.ok, rep.errors)
        self.assertNotIn("hammerless_nav_region", text)

    def test_script(self):
        from hammerless.core.nav import collect_regions, navmark_script
        regions, _ = collect_regions(self.preset_ir())
        nut = navmark_script(regions, "m")
        self.assertIn("SetSpawnAttributes", nut)
        self.assertIn(", 2176, [", nut)  # PLAYER_START | CHECKPOINT (then the room's shape)
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
        self.assertEqual([w for w in rep.warnings if not w.startswith("No End Safe Room")], [])

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
        self.assertEqual([w for w in rep.warnings if not w.startswith("No End Safe Room")], [])
        self.assertNotIn("hammerless_crescendo", text)
        self.assertIn("director,ScriptedPanicEvent,hammerless_m_c7,0,1", text)
        nut = game_files(ir)["scripts/vscripts/hammerless_m_c7.nut"]
        # building again (outputs already rewritten) stays clean
        text2, rep2 = build_vmf(ir)
        self.assertEqual([w for w in rep2.warnings if not w.startswith("No End Safe Room")], [])
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


class TestSpawnSnapping(unittest.TestCase):
    def test_vertical_span(self):
        b = g.box_brush((0, 0, -16), (64, 64, 1.05), "x")
        lo, hi = g.vertical_span(b, 10, 10)
        self.assertAlmostEqual(lo, -16, places=3)
        self.assertAlmostEqual(hi, 1.05, places=3)
        self.assertIsNone(g.vertical_span(b, 100, 10))

    def spawn_z(self, ir):
        text, rep = build_vmf(ir)
        self.assertTrue(rep.ok, rep.errors)
        ents = [b for b in parse(text) if b.name == "entity"]
        return {b.get("classname"): float(b.get("origin").split()[2]) for b in ents
                if b.get("classname") in ("info_survivor_position", "info_player_start")}

    def test_spawn_on_floor_surface_is_lifted(self):
        # the user's map: floor top and spawn origin both at 1.05 units
        ir = MapIR()
        ir.brushes.append(g.box_brush((-512, -512, -220), (512, 512, 1.05), "x", "ground"))
        ir.entities.append(Entity("info_survivor_position", (0, 140, 1.05), (0, 0, 0), {"Order": "1"}))
        text, rep = build_vmf(ir)
        pos = {b.get("classname"): [float(c) for c in b.get("origin").split()]
               for b in parse(text) if b.name == "entity"}
        self.assertAlmostEqual(pos["info_survivor_position"][2], 3.05, places=2)
        self.assertAlmostEqual(pos["info_player_start"][2], 3.05, places=2)
        # the auto-added player start must not sit on the survivor spawn (nobody spawns)
        dx = pos["info_player_start"][0] - pos["info_survivor_position"][0]
        dy = pos["info_player_start"][1] - pos["info_survivor_position"][1]
        self.assertGreaterEqual((dx * dx + dy * dy) ** 0.5, 63.9)

    def test_floating_spawn_dropped_and_far_spawn_left(self):
        ir = MapIR()
        ir.brushes.append(g.box_brush((-512, -512, -16), (512, 512, 0), "x", "ground"))
        ir.entities.append(Entity("info_survivor_position", (0, 0, 30), (0, 0, 0), {"Order": "1"}))
        ir.entities.append(Entity("info_survivor_position", (100, 0, 400), (0, 0, 0), {"Order": "2"}))  # e.g. on a roof prop
        text, rep = build_vmf(ir)
        zs = sorted(float(b.get("origin").split()[2]) for b in parse(text)
                    if b.name == "entity" and b.get("classname") == "info_survivor_position")
        self.assertEqual(zs, [2.0, 400.0])

    def test_spawn_on_terrain(self):
        ir = MapIR()
        ir.terrains.append(TestDisplacement().make_ramp())   # z = x*0.25 + row*2
        ir.entities.append(Entity("info_survivor_position", (160, 64, 0), (0, 0, 0), {"Order": "1"}))
        z = self.spawn_z(ir)["info_survivor_position"]
        self.assertAlmostEqual(z, 160 * 0.25 + 2 * 2 + 2, delta=0.5)


class TestNavSeed(unittest.TestCase):
    def landmarks(self, ir):
        text, rep = build_vmf(ir)
        self.assertTrue(rep.ok, rep.errors)
        return [b for b in parse(text) if b.name == "entity" and b.get("classname") == "info_landmark"]

    def test_seed_added_without_landmark(self):
        lm = self.landmarks(box_room_ir())
        self.assertEqual([b.get("targetname") for b in lm], ["hammerless_nav_seed"])
        self.assertEqual(float(lm[0].get("origin").split()[2]), 2 + 32)   # above the snapped spawn

    def test_no_seed_when_map_has_landmark(self):
        ir = box_room_ir()
        ir.entities.append(Entity("info_landmark", (0, 0, 32), (0, 0, 0), {"targetname": "landmark_1"}))
        self.assertEqual([b.get("targetname") for b in self.landmarks(ir)], ["landmark_1"])


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


class TestDenseBrush(unittest.TestCase):
    """Brush checks on dense meshes: quick (Check used to freeze Blender comparing every face with
    every other), and only the compiler's real limits (measured with L4D2's vbsp)."""

    def sphere(self, seg=96, rings=48, r=100.0):
        import math
        from hammerless.core.ir import Polygon

        def p(i, j):
            th, ph = 2 * math.pi * (i % seg) / seg, math.pi * j / rings
            return (r * math.sin(ph) * math.cos(th), r * math.sin(ph) * math.sin(th), r * math.cos(ph))
        faces = [Polygon([p(i, j), p(i + 1, j), p(i + 1, j + 1), p(i, j + 1)], "m")
                 for i in range(seg) for j in range(1, rings - 1)]
        faces += [Polygon([p(i, 0), p(i + 1, 1), p(i, 1)], "m") for i in range(seg)]                 # caps
        faces += [Polygon([p(i, rings), p(i, rings - 1), p(i + 1, rings - 1)], "m") for i in range(seg)]
        return [Polygon(f.verts[::-1], "m") for f in faces]          # wound so the normals point outward

    def cylinder(self, k, r=200.0, h=64.0):
        import math
        from hammerless.core.ir import Polygon
        ring = [(r * math.cos(2 * math.pi * i / k), r * math.sin(2 * math.pi * i / k)) for i in range(k)]
        sides = [Polygon([(*ring[i], 0.0), (*ring[(i + 1) % k], 0.0), (*ring[(i + 1) % k], h), (*ring[i], h)], "m")
                 for i in range(k)]
        return sides + [Polygon([(x, y, h) for x, y in ring], "m"), Polygon([(x, y, 0.0) for x, y in ring[::-1]], "m")]

    def test_dense_sphere_is_quick_and_valid(self):
        import time
        from hammerless.core import geometry as g
        from hammerless.core.ir import Brush
        faces = self.sphere()
        t = time.time()
        self.assertEqual(g.check_brush(Brush(faces, source="sphere")), [])          # vbsp takes 4000+ sides
        self.assertLess(time.time() - t, 10.0)
        holed = Brush(faces[:100] + faces[101:], source="holed")                     # the large-mesh open test
        self.assertIn("is open", g.check_brush(holed)[0].message)

    def test_face_corner_limit(self):
        from hammerless.core import geometry as g
        from hammerless.core.ir import Brush
        self.assertEqual(g.check_brush(Brush(self.cylinder(64), source="c64")), [])
        problems = g.check_brush(Brush(self.cylinder(65), source="c65"))
        self.assertEqual(len(problems), 1)
        self.assertIn("65 corners", problems[0].message)


class TestPackaging(unittest.TestCase):
    def test_bl_info_matches_manifest(self):
        """Blender 4.0 / 4.1 read bl_info (old-style add-on); 4.2+ read the manifest: same version."""
        import ast
        import re
        root = os.path.join(os.path.dirname(__file__), "..", "..", "hammerless")
        tree = ast.parse(open(os.path.join(root, "__init__.py"), encoding="utf-8").read())
        info = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and getattr(n.targets[0], "id", "") == "bl_info")
        manifest = open(os.path.join(root, "blender_manifest.toml"), encoding="utf-8").read()
        version = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
        self.assertEqual(".".join(map(str, info["version"])), version)
        self.assertEqual(info["blender"], (4, 0, 0))


class TestMapOwner(unittest.TestCase):
    def test_owner_round_trip(self):
        import tempfile
        from types import SimpleNamespace
        from hammerless.core import compile as cc
        with tempfile.TemporaryDirectory() as d:
            tools = SimpleNamespace(gamedir=d)
            self.assertIsNone(cc.map_owner(tools, "hl_x"))
            cc.set_map_owner(tools, "hl_x", os.path.join(d, "a", "hl_x.vmf"))
            self.assertEqual(cc.map_owner(tools, "hl_x"), os.path.abspath(os.path.join(d, "a", "hl_x.vmf")))
            self.assertIsNone(cc.map_owner(tools, "hl_y"))


class TestLightmap(unittest.TestCase):
    def test_decode_luxels(self):
        import numpy as np
        from hammerless.core.lightmap import decode_luxels
        raw = bytes([255, 128, 0, 0]) + bytes([128, 128, 128, 256 - 7])       # exp 0, then exp -7
        got = decode_luxels(raw)
        np.testing.assert_allclose(got[0], [1.0, 128 / 255, 0.0], rtol=1e-6)
        np.testing.assert_allclose(got[1], [128 * 2 ** -7 / 255] * 3, rtol=1e-6)

    def test_pack_no_overlap(self):
        import random
        from hammerless.core.lightmap import _pack
        rnd = random.Random(1)
        sizes = [(rnd.randint(1, 33), rnd.randint(1, 33)) for _ in range(500)]
        at, w, h = _pack(sizes)
        taken = set()
        for (x, y), (bw, bh) in zip(at, sizes):
            self.assertLessEqual(x + bw, w)
            self.assertLessEqual(y + bh, h)
            cells = {(x + i, y + j) for i in range(bw) for j in range(bh)}
            self.assertFalse(cells & taken)
            taken |= cells

    def test_real_map_luxels_on_grid(self):
        """Every face's lightmap corners land inside its own luxel block (needs a compiled, lit map)."""
        import glob
        from hammerless.core.lightmap import read_lightmaps
        maps = [p for p in glob.glob(os.path.join(os.path.dirname(__file__), "..", "..", "demo", "hammerless_build",
                                                  "*.bsp"))]
        data = None
        for p in maps:
            with open(p, "rb") as f:
                d = f.read()
            try:
                lm = read_lightmaps(d)
            except ValueError:
                continue
            if lm.faces:
                data = lm
                break
        if data is None:
            self.skipTest("no compiled map with lighting in demo/hammerless_build")
        h, w = data.atlas.shape[:2]
        self.assertTrue((data.uvs[:, 0] >= 0.5 / w - 1e-6).all() and (data.uvs[:, 0] <= 1 - 0.5 / w + 1e-6).all())
        self.assertTrue((data.uvs[:, 1] >= 0.5 / h - 1e-6).all() and (data.uvs[:, 1] <= 1 - 0.5 / h + 1e-6).all())


class TestOverrideAnswer(unittest.TestCase):
    def test_answer_node_answers_from_what_was_asked(self):
        # Override (damage) -> Asked -> Answer, Allow worked out from the attacker by a Script Value
        from hammerless.core.logic import LLink, LNode, compile_graph, hook_function
        ir = MapIR()
        ir.settings.name = "m"
        ir.entities.append(Entity("info_player_start", (0, 0, 0), (0, 0, 0), {}))
        nodes = [LNode("Ask", "OVERRIDE", {"hook": "AllowTakeDamage"}),
                 LNode("Ff", "SCRIPT_VALUE", {"expr": "a == null"}),
                 LNode("Ans", "OVERRIDE_ANSWER", {"hook": "AllowTakeDamage"})]
        links = [LLink("Ask", "asked", "Ans", "run"), LLink("Ask", "attacker", "Ff", "a", True),
                 LLink("Ff", "result", "Ans", "answer", True)]
        self.assertEqual(compile_graph(nodes, links, ir), [])
        script = ir.extra_scripts["scripts/vscripts/hammerless/logic_m.nut"]
        self.assertIn('::HL_Ans.answer <- (function(a, b, c, d) { return a == null; })((("attacker" in ::HL_Ctx', script)
        hook = hook_function("AllowTakeDamage", ir.logic_hooks["AllowTakeDamage"])
        self.assertIn("::HL_Ans <- {};\n    HL_S_ans();", hook)
        self.assertIn('if ("answer" in ::HL_Ans) answer = answer && ::HL_Ans.answer;', hook)
        # the logic entity comes first, so questions asked while the map's entities are made are answered
        self.assertEqual(ir.entities[0].keyvalues.get("targetname"), "hl_logic")

    def test_script_result_feeds_other_nodes(self):
        # a Script node's Result, wired into Set Variable, is what its code left in result
        from hammerless.core.logic import LLink, LNode, compile_graph
        ir = MapIR()
        ir.settings.name = "m"
        nodes = [LNode("Start", "MAP_START"), LNode("Code", "SCRIPT_CODE", {"code": "result = 7;"}),
                 LNode("Keep", "SET_VAR", {"name": "n", "kind": "num"})]
        links = [LLink("Start", "start", "Code", "run"), LLink("Code", "then", "Keep", "run"),
                 LLink("Code", "result", "Keep", "value", True)]
        self.assertEqual(compile_graph(nodes, links, ir), [])
        script = ir.extra_scripts["scripts/vscripts/hammerless/logic_m.nut"]
        self.assertIn('HL_VarSet("n", (("code" in ::HL_R) ? ::HL_R["code"] : null)', script)

    def test_questions_normally_answered_no(self):
        # Can Pickup Object and Should Avoid Item are "no" unless an answer says yes (Valve's Holdout
        # allows chosen props; mutations have bots avoid removed weapons)
        from hammerless.core.logic import hook_function
        from hammerless.core.ir import MapIR
        from hammerless.core import gamefiles
        hook = hook_function("CanPickupObject", [("    HL_S_x();", None, None)])
        self.assertIn("local answer = false;", hook)
        self.assertIn('answer = answer || ::HL_Ans.answer;', hook)
        self.assertIn("local answer = true;", hook_function("AllowWeaponSpawn", []))
        ir = MapIR()
        ir.settings.name = "m"
        ir.scripted_mode, ir.logic_hooks = True, {"CanPickupObject": []}
        mode = gamefiles.mode_files(ir)["scripts/vscripts/m_hammerless.nut"]
        self.assertIn('::HL_Map == "m"', mode)                  # only this map's logic answers
        self.assertIn("::HL_Hook_CanPickupObject.call(::HL_Scope, object) : false", mode)

    def test_answer_node_needs_a_question_with_an_answer(self):
        from hammerless.core.logic import LLink, LNode, compile_graph
        ir = MapIR()
        ir.settings.name = "m"
        problems = compile_graph([LNode("Ans", "OVERRIDE_ANSWER", {"hook": "InterceptChat"})], [], ir)
        self.assertTrue(any("takes an answer" in p for p in problems), problems)


class TestCustomModels(unittest.TestCase):
    def test_qc_by_prop_kind(self):
        from hammerless.core.models import ModelSpec, qc_text
        tri = ("mat", (((0, 0, 0), (0, 0, 1), (0, 0)), ((1, 0, 0), (0, 0, 1), (1, 0)), ((0, 1, 0), (0, 0, 1), (0, 1))))
        piece = ([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)])
        static = qc_text(ModelSpec("hammerless/m/a", [tri], [piece], "STATIC", materials_dir="models/hammerless/m/"))
        self.assertIn("$staticprop", static)
        self.assertNotIn("prop_data", static)
        self.assertIn('$cdmaterials "models/hammerless/m/"', static)
        phys = qc_text(ModelSpec("hammerless/m/b", [tri], [piece, piece], "PHYSICS", mass=12, physics_class="Metal.Small"))
        self.assertNotIn("$staticprop", phys)        # the game deletes physics props made from static models
        self.assertIn('prop_data { "base" "Metal.Small" }', phys)   # and ones without prop_data
        self.assertIn("$concave", phys)
        self.assertIn("$mass 12", phys)

    def test_collision_faces_point_out(self):
        from hammerless.core.models import collision_smd
        # a tetrahedron with every triangle listed the wrong way round
        pts = [(0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)]
        text = collision_smd([(pts, [(0, 2, 1), (0, 3, 2), (0, 1, 3), (1, 2, 3)][::1])])
        rows = [l.split() for l in text.splitlines() if l.startswith("0 ") and len(l.split()) == 9]
        pts_all = [tuple(map(float, r[1:4])) for r in rows]                # (written turned for studiomdl)
        centre = tuple(sum(p[k] for p in pts_all) / len(pts_all) for k in range(3))
        for i in range(0, len(rows), 3):
            p = [tuple(map(float, r[1:4])) for r in rows[i:i + 3]]
            n = tuple(map(float, rows[i][4:7]))
            u = [p[1][k] - p[0][k] for k in range(3)]
            v = [p[2][k] - p[0][k] for k in range(3)]
            wound = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            out = [p[0][k] - centre[k] for k in range(3)]
            self.assertGreater(sum(wound[k] * out[k] for k in range(3)), 0)      # counter-clockwise from outside
            self.assertGreater(sum(n[k] * out[k] for k in range(3)), 0)

    def test_smd_turned_for_studiomdl(self):
        # studiomdl turns what it reads 90 degrees about Z (measured); the SMD is written turned back
        from hammerless.core.models import reference_smd
        text = reference_smd([("m", (((40, 0, 0), (1, 0, 0), (0, 1)), ((0, 20, 0), (0, 1, 0), (0, 0)),
                                      ((0, 0, 10), (0, 0, 1), (1, 0))))])
        rows = [l.split() for l in text.splitlines() if l.startswith("0 ") and len(l.split()) == 9]
        self.assertEqual([float(c) for c in rows[0][1:7]], [0.0, -40.0, 0.0, 0.0, -1.0, 0.0])
        self.assertEqual([float(c) for c in rows[1][1:4]], [20.0, 0.0, 0.0])

    def test_own_model_writer(self):
        # our .mdl / .vvd / .dx90.vtx for a two-material box: consistent headers, shared checksum, all corners
        import struct
        from hammerless.core.mdlwrite import build
        s = 8.0
        quads = [((0, 0, 1), [(-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)], "top"),
                 ((0, 0, -1), [(-s, s, -s), (s, s, -s), (s, -s, -s), (-s, -s, -s)], "side"),
                 ((1, 0, 0), [(s, -s, -s), (s, s, -s), (s, s, s), (s, -s, s)], "side")]
        uv = [(0, 0), (1, 0), (1, 1), (0, 1)]
        tris = []
        for n, c, mat in quads:
            for t in ((0, 1, 2), (0, 2, 3)):
                tris.append((mat, tuple((c[i], n, uv[i]) for i in t)))
        f = build("hammerless/m/box", tris, "models/hammerless/m/", "metal", 5.0, 1234)
        mdl, vvd, vtx = f[".mdl"], f[".vvd"], f[".dx90.vtx"]
        self.assertEqual(struct.unpack_from("<4sii", mdl, 0), (b"IDST", 49, 1234))
        self.assertEqual(struct.unpack_from("<i", mdl, 76)[0], len(mdl))                 # length
        self.assertEqual(struct.unpack_from("<4sii", vvd, 0), (b"IDSV", 4, 1234))
        self.assertEqual(struct.unpack_from("<i", vvd, 16)[0], 12)                       # 3 faces x 4 corners
        self.assertEqual(struct.unpack_from("<ii", vtx, 0), (7, 24))
        self.assertEqual(struct.unpack_from("<i", vtx, 16)[0], 1234)                     # checksum
        self.assertEqual(struct.unpack_from("<i", mdl, 204)[0], 2)                       # two materials
        self.assertIn(b"metal\0", mdl)
        self.assertIn(b"models\\hammerless\\m\\\0", mdl)
        # the box's hull and lighting centre
        self.assertEqual(struct.unpack_from("<3f", mdl, 104), (-8.25, -8.25, -8.25))
        self.assertEqual(struct.unpack_from("<3f", mdl, 92), (0.0, 0.0, 0.0))

    def test_own_collision_writer(self):
        # our .phy: read back by the reader as the same pieces, the layout studiomdl uses for a cube
        import struct
        from hammerless.core.phywrite import build_phy
        from hammerless.core.phy import read_phy_pieces
        s = 16.0
        pts = [(x, y, z) for x in (-s, s) for y in (-s, s) for z in (-s, s)]
        idx = {p: i for i, p in enumerate(pts)}
        faces = [[(-s, -s, -s), (-s, s, -s), (s, s, -s), (s, -s, -s)], [(-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)],
                 [(-s, -s, -s), (s, -s, -s), (s, -s, s), (-s, -s, s)], [(s, -s, -s), (s, s, -s), (s, s, s), (s, -s, s)],
                 [(s, s, -s), (-s, s, -s), (-s, s, s), (s, s, s)], [(-s, s, -s), (-s, -s, -s), (-s, -s, s), (-s, s, s)]]
        tris = []
        for f in faces:
            q = [idx[v] for v in f]
            tris += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
        moved = [(x + 100, y, z) for x, y, z in pts]
        data = build_phy([(pts, tris), (moved, tris)], 77, "m", "wood", 20.0)
        self.assertEqual(struct.unpack_from("<iiii", data, 0), (16, 0, 1, 77))
        pieces = read_phy_pieces(data)
        self.assertEqual(len(pieces), 2)
        got = sorted(tuple(round(c, 3) for c in p) for piece in pieces for p in piece[0])
        want = sorted(tuple(float(c) for c in p) for p in pts + moved)
        self.assertEqual(got, want)
        # one cube alone is the same size as studiomdl's (691 bytes), with its sphere and box sizes
        one = build_phy([(pts, tris)], 1, "cube_phys", "wood_crate", 20.0)
        self.assertEqual(len(one) - len(b'"name" "cube_phys"') + len(b'"name" "cube_phys"'), 691)
        node = 20 + 28 + 384
        self.assertEqual(struct.unpack_from("<4B", one, node + 24), (145, 145, 145, 0))
        self.assertIn(b'"volume" "32768.0', one)

    def test_surface_density(self):
        from hammerless.core.surfaces import surface_density

        class Content:
            files = {"scripts/surfaceproperties_manifest.txt": b'surfaceproperties_manifest { "file" "scripts/s.txt" }',
                     "scripts/s.txt": b'"default" { "density" "2000" } "wood" { "density" "700" } "wood_crate" { "base" "wood" }'}

            def read(self, path):
                return self.files.get(path)
        self.assertEqual(surface_density(Content(), "wood_crate"), 700.0)
        self.assertEqual(surface_density(Content(), "nothing"), 2000.0)
        self.assertEqual(surface_density(None, "wood"), 2000.0)

    def test_base_texture_of_vmt(self):
        from hammerless.core.models import base_texture_of
        self.assertEqual(base_texture_of('"LightmappedGeneric"\n{\n\t"$basetexture" "Wood\\WoodWall003a"\n}'),
                         "Wood/WoodWall003a")
        self.assertEqual(base_texture_of('LightmappedGeneric { $basetexture concrete/floor01 }'), "concrete/floor01")


if __name__ == "__main__":
    unittest.main()
