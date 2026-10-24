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
