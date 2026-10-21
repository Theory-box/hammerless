"""Import semantics and conservative spawn marking regressions."""
import unittest
from hammerless.core.ir import MapIR
from hammerless.core.vmf import parse
from hammerless.core.vmfimport import semantic_entities
from hammerless.core.build import validate
from hammerless.core.nav import NavRegion
from hammerless.core.navfile import NavArea, NavMesh
from hammerless.core.navpredict import mark_survivor_starts, survivor_positions

class ImportGameplayTests(unittest.TestCase):
    def test_semantic_entities_and_deleted_entities(self):
        blocks = parse('entity { "classname" "info_player_start" "origin" "10 20 30" connections { "OnUser1" "door,Open,,0.25,3" } }')
        entities = semantic_entities(blocks + [None])
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].origin, (10, 20, 30))
        self.assertEqual(entities[0].outputs[0].times, 3)
        ir = MapIR(imported_entities=entities)
        self.assertFalse(any("No survivor spawn" in w for w in validate(ir).warnings))
        self.assertEqual(ir.entities, [])

    def test_imported_end_volume_is_marked(self):
        from hammerless.core.geometry import box_brush
        from hammerless.core.vmf import Block, VMFWriter
        from hammerless.core.nav import collect_regions
        block = Block("entity").kv("classname", "info_changelevel")
        block.add(VMFWriter().solid(box_brush((0, 0, 0), (128, 128, 96), "tools/toolstrigger")))
        ir = MapIR(imported_entities=semantic_entities([block]))
        regions, problems = collect_regions(ir)
        self.assertEqual(problems, [])
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].bits, 2048)
        self.assertTrue(regions[0].contains((64, 64, 0)))
        self.assertFalse(regions[0].contains((64, 64, 100)))
        ir.imported_entities = semantic_entities([None])
        self.assertEqual(collect_regions(ir)[0], [])

    def test_spawn_floor_not_roof_or_other_storey(self):
        floor = NavArea(1, 0, (0, 0, 0), (100, 100, 0), 0, 0)
        roof = NavArea(2, 0, (0, 0, 16), (100, 100, 16), 16, 16)
        basement = NavArea(3, 0, (0, 0, -128), (100, 100, -128), -128, -128)
        mesh = NavMesh(areas=[floor, roof, basement])
        self.assertEqual(mark_survivor_starts(mesh, [(50, 50, 2)], []), [])
        self.assertEqual(floor.spawn_attributes, 128)  # no inferred CHECKPOINT: changes game spawn selection
        self.assertEqual(roof.spawn_attributes | basement.spawn_attributes, 0)
        self.assertTrue(mark_survivor_starts(mesh, [(200, 200, 2)], []))

    def test_explicit_region_wins_and_rescues_are_not_starts(self):
        floor = NavArea(1, 0, (0, 0, 0), (100, 100, 0), 0, 0)
        mesh = NavMesh(areas=[floor])
        region = NavRegion((0, 0, 0), (100, 100, 10), 128)
        mark_survivor_starts(mesh, [(50, 50, 2)], [region])
        self.assertEqual(floor.spawn_attributes, 0)
        text = 'entity { "classname" "info_player_start" "origin" "1 2 3" } entity { "classname" "info_survivor_rescue" "origin" "4 5 6" }'
        self.assertEqual(survivor_positions(text), [(1, 2, 3)])

if __name__ == "__main__":
    unittest.main()
