import unittest

from hammerless.core.geometry import box_brush
from hammerless.core.ir import Entity
from hammerless.core.vmf import Block, VMFWriter
from hammerless.core import navelevator
from hammerless.core.nav import NavRegion
from hammerless.core.navfile import read_nav, write_nav
from hammerless.core.navpredict import _predict, check_native
from hammerless.core.navanalysis import analyse


def fixture():
    w = VMFWriter()
    world = Block("world").kv("id", "1").kv("classname", "worldspawn")
    # Two separated floors with the lift beside them. No intermediate ladder, stairs or drop route.
    for z in (0, 512):
        world.add(w.solid(box_brush((-240, -160, z - 16), (0, 160, z), "concrete/floor")))
    lift = w.entity(Entity("func_elevator", (80, 0, -8), (0, 0, 0),
                           {"targetname": "lift", "top": "80 0 504", "bottom": "80 0 -8"},
                           []), [w.solid(box_brush((0, -80, -16), (160, 80, 0), "metal/floor"))])
    start = w.entity(Entity("info_player_start", (-100, 0, 2)))
    text = w.document(world, [lift, start])
    return text, [NavRegion((-240, -160, 511), (0, 160, 520), 2048)]


class ElevatorTests(unittest.TestCase):
    def test_stops_geometry_and_unrelated_marker(self):
        text, _ = fixture()
        text += '\nentity { "classname" "info_elevator_floor" "origin" "80 0 248" }'
        text += '\nentity { "classname" "info_elevator_floor" "origin" "1000 0 100" }'
        elevators, problems = navelevator.collect(text)
        self.assertEqual(problems, [])
        self.assertEqual([elevators[0].landing_z(p) for p in elevators[0].stops], [0, 256, 512])
        brushes, seeds = navelevator.platforms(elevators)
        self.assertEqual([b.maxs[2] for b in brushes], [0, 256, 512])
        self.assertEqual([p[2] for p in seeds], [16, 272, 528])

    def test_generated_route_uses_elevator_not_walk_or_jump(self):
        text, regions = fixture()
        mesh = _predict(text, regions, native_areas=False)
        self.assertTrue(mesh.elevator_links)
        rep = analyse(mesh)
        self.assertTrue(rep.end_reached, mesh.problems)
        self.assertTrue(any(l.kind == "elevator" for l in rep.links))
        for a, b in mesh.elevator_links:
            self.assertNotIn(b, sum(mesh.by_id()[a].connections, []))
        # The game supplies travel links at runtime; no invented cross-floor walk edges are serialized.
        loaded = read_nav(write_nav(mesh))
        self.assertFalse(loaded.elevator_links)
        self.assertFalse(analyse(loaded).end_reached)
        navelevator.associate(loaded, navelevator.collect(text)[0])
        self.assertTrue(analyse(loaded).end_reached)

    def test_native_parity(self):
        from hammerless.core import fastnav
        if not fastnav.available():
            self.skipTest("native DLL unavailable")
        text, regions = fixture()
        self.assertIsNone(check_native(text, regions))

    def test_bad_elevator_reports_missing_geometry(self):
        elevators, problems = navelevator.collect('entity { "classname" "func_elevator" "targetname" "bad" }')
        self.assertFalse(elevators)
        self.assertIn("bad", problems[0])

    def test_named_landing_preferred_to_nearby_limit(self):
        text, _ = fixture()
        text += '\nentity { "classname" "info_elevator_floor" "origin" "80 0 472" }'
        elevators, _ = navelevator.collect(text)
        self.assertEqual([elevators[0].landing_z(p) for p in elevators[0].stops], [0, 480])

    def test_missing_landing_is_reported_not_bridged(self):
        text, regions = fixture()
        mesh = _predict(text, regions, native_areas=False)
        mesh.areas = [a for a in mesh.areas if a.centre[2] < 100]
        issues = navelevator.associate(mesh, navelevator.collect(text)[0])
        self.assertTrue(any("no boarding nav" in p for p in issues))
        self.assertEqual(mesh.elevator_links, [])


if __name__ == "__main__":
    unittest.main()
