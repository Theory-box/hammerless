import unittest
from hammerless.core.geometry import box_brush
from hammerless.core.ir import Entity
from hammerless.core.vmf import Block, VMFWriter
from hammerless.core.collision import CollisionWorld


class NavMaterialTests(unittest.TestCase):
    def test_blocklight_does_not_block_doorway(self):
        for owner in ("world", "func_detail"):
            for material, blocked in (("tools/toolsblocklight", False), ("tools/toolsnodraw", True)):
                with self.subTest(owner=owner, material=material):
                    writer = VMFWriter()
                    world = Block("world").kv("classname", "worldspawn")
                    solid = writer.solid(box_brush((387, 5604, 2656), (388, 5660, 2768), material))
                    entities = []
                    if owner == "world":
                        world.add(solid)
                    else:
                        entities.append(writer.entity(Entity(owner, None), [solid]))
                    text = writer.document(world, entities)
                    trace = CollisionWorld.from_vmf(text).trace_hull(
                        (355, 5632, 2656.1), (425, 5632, 2656.1), (-16, -16, 0), (16, 16, 72))
                    self.assertEqual(trace.fraction < 1, blocked)
                    self.assertIn(material, text.lower())  # Lighting geometry remains exported.

