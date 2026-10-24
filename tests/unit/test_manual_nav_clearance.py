import unittest

from hammerless.core.collision import CollisionWorld
from hammerless.core.geometry import box_brush
from hammerless.core.nav import NavRegion, NAV_AREA_BIT
from hammerless.core.navfile import NavArea, NavMesh
from hammerless.core.navpredict import add_nav_areas
from hammerless.core.vmf import Block, VMFWriter


class ManualNavClearanceTests(unittest.TestCase):
    def build(self, obstacles=(), height=128, support=True, manual_high=False):
        writer = VMFWriter()
        block = Block('world').kv('id', '1').kv('classname', 'worldspawn')
        solids = [((-100, -80, -16), (0, 80, height))]
        if support:
            solids.append(((0, -80, -16), (150, 80, 0)))
        for lo, hi in solids + list(obstacles):
            block.add(writer.solid(box_brush(lo, hi, 'concrete/floor')))
        world = CollisionWorld.from_vmf(writer.document(block, []))
        mesh = NavMesh(analyzed=False)
        if manual_high:
            mesh.areas = [NavArea(1, 0, (25, -50, 0), (125, 50, 0), 0, 0)]
            region = NavRegion((-100, -50, height), (0, 50, height + 8), NAV_AREA_BIT)
        else:
            mesh.areas = [NavArea(1, 0, (-100, -50, height), (0, 50, height), height, height)]
            region = NavRegion((25, -50, 0), (125, 50, 8), NAV_AREA_BIT)
        made, links = add_nav_areas(mesh, [region], world)
        return mesh, links

    def test_clear_drop_to_manual_area(self):
        _, links = self.build()
        self.assertEqual(links, {(1, 2)})

    def test_clear_drop_from_manual_area(self):
        _, links = self.build(manual_high=True)
        self.assertEqual(links, {(2, 1)})

    def test_wall_blocks_drop(self):
        _, links = self.build([((8, -80, 0), (16, 80, 256))])
        self.assertFalse(links)

    def test_ceiling_blocks_descent(self):
        _, links = self.build([((0, -80, 64), (150, 80, 80))])
        self.assertFalse(links)

    def test_low_overhang_blocks_departure(self):
        _, links = self.build([((-100, -80, 160), (150, 80, 176))])
        self.assertFalse(links)

    def test_missing_landing_floor(self):
        _, links = self.build(support=False)
        self.assertFalse(links)

    def test_level_link_through_wall(self):
        _, links = self.build([((8, -80, 0), (16, 80, 128))], height=0)
        self.assertFalse(links)

    def test_clear_level_link(self):
        _, links = self.build(height=0)
        self.assertEqual(links, {(1, 2), (2, 1)})

    def test_replacement_does_not_leave_stale_ids(self):
        mesh = NavMesh(analyzed=False)
        mesh.areas = [NavArea(1, 0, (-100, 0, 0), (0, 100, 0), 0, 0)]
        regions = [NavRegion((0, 0, 0), (100, 100, 8), NAV_AREA_BIT),
                   NavRegion((0, 0, 0), (100, 100, 8), NAV_AREA_BIT)]
        made, links = add_nav_areas(mesh, regions)
        self.assertEqual(made, [3])
        self.assertEqual(links, {(1, 3), (3, 1)})
        self.assertEqual(mesh.areas[0].connections[1], [3])

    def test_island_drop_cannot_start_above_a_low_ceiling(self):
        from hammerless.core.navpredict import link_islands
        writer = VMFWriter()
        block = Block('world').kv('id', '1').kv('classname', 'worldspawn')
        for lo, hi in [((-100, -80, -16), (0, 80, 128)),
                       ((0, -80, -16), (150, 80, 0)),
                       ((-100, -80, 130), (0, 80, 138))]:
            block.add(writer.solid(box_brush(lo, hi, 'concrete/floor')))
        world = CollisionWorld.from_vmf(writer.document(block, []))
        mesh = NavMesh(analyzed=False)
        high = NavArea(1, 0, (-100, -50, 128), (0, 50, 128), 128, 128)
        low = NavArea(2, 0, (25, -50, 0), (125, 50, 0), 0, 0)
        mesh.areas = [high, low]
        self.assertEqual(link_islands(mesh, world, {2}), 0)
        self.assertEqual(high.connections, [[], [], [], []])
