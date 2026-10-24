import unittest
from hammerless.core.navareas import Area, Generator
from hammerless.core.collision import CollisionWorld
from hammerless.core.geometry import box_brush
from hammerless.core.vmf import Block, VMFWriter


class ClimbClearanceTests(unittest.TestCase):
    def generator(self, wall_top=64, ceiling=False):
        w = VMFWriter()
        world = Block('world').kv('id','1').kv('classname','worldspawn')
        bounds = [((-128,-100,-16),(0,100,0)), ((0,-100,-16),(128,100,64))]
        if wall_top > 64: bounds += [((0,-100,64),(16,100,wall_top))]
        if ceiling: bounds += [((-128,-100,100),(128,100,120))]
        for a,b in bounds: world.add(w.solid(box_brush(a,b,'concrete/floor')))
        gen = Generator(CollisionWorld.from_vmf(w.document(world, [])))
        gen.areas = [Area(1,(-100,-50,0),(0,50,0),0,0,[]),
                     Area(2,(25,-50,64),(125,50,64),64,64,[])]
        return gen

    def test_open_ledge(self):
        self.assertGreater(self.generator().add_wall_climbs(200), 0)

    def test_wall_continues_above_landing(self):
        self.assertEqual(self.generator(256).add_wall_climbs(200), 0)

    def test_no_room_above_climber(self):
        self.assertEqual(self.generator(ceiling=True).add_wall_climbs(200), 0)

    def manual_mesh(self, wall_top=64, ceiling=False, enabled=True):
        from hammerless.core.nav import NavRegion, NAV_AREA_BIT
        from hammerless.core.navpredict import _finish
        gen = self.generator(wall_top, ceiling)
        gen.areas = gen.areas[:1]  # the sampler did not discover the sill
        regions = [NavRegion((0, -50, 64), (30, 50, 70), NAV_AREA_BIT, "sill")]
        return _finish(gen, regions, None, (), enabled)

    def test_manual_sill_gets_climb_and_drop(self):
        mesh = self.manual_mesh()
        low, high = mesh.areas
        self.assertIn(high.id, low.connections[1])
        self.assertIn(low.id, high.connections[3])

    def test_manual_sill_cannot_climb_through_wall(self):
        low, high = self.manual_mesh(wall_top=256).areas
        self.assertNotIn(high.id, [i for links in low.connections for i in links])

    def test_manual_sill_requires_headroom(self):
        low, high = self.manual_mesh(ceiling=True).areas
        self.assertNotIn(high.id, [i for links in low.connections for i in links])

    def test_manual_sill_respects_auto_climb_toggle(self):
        low, high = self.manual_mesh(enabled=False).areas
        self.assertNotIn(high.id, [i for links in low.connections for i in links])
        self.assertIn(low.id, high.connections[3])

    def test_replacing_generated_area_uses_manual_bounds(self):
        from hammerless.core.nav import NavRegion, NAV_AREA_BIT
        from hammerless.core.navpredict import _finish
        gen = self.generator()
        regions = [NavRegion((0, -50, 64), (30, 50, 70), NAV_AREA_BIT, "sill")]
        mesh = _finish(gen, regions, None, (), True)
        low, high = mesh.areas
        self.assertEqual(high.nw, (0, -50, 64))
        self.assertEqual(high.se, (30, 50, 64))
        self.assertIn(high.id, low.connections[1])
        valid = {a.id for a in mesh.areas}
        self.assertTrue(all(i in valid for a in mesh.areas for links in a.connections for i in links))
