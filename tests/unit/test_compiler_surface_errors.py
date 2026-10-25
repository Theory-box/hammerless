import json
import tempfile
import unittest
from pathlib import Path
from hammerless.core.compile import parse_log
from hammerless.core.geometry import box_brush
from hammerless.core.vmf import Block, VMFWriter

LOG = '''Error: Bad surface extents - surface is too big to have a lightmap
    material BRICK/TEST
!! Trying Valve compiler
Bad surface extents point: 0 0 16
Bad surface extents point: 100 0 16
Bad surface extents point: 100 100 16
Bad surface extents point: 0 100 16
Bad surface extents - surface is too big to have a lightmap
    material BRICK/TEST around point (50 50 16)
    (dimension: 1, 128>126)
'''


class SurfaceErrorTests(unittest.TestCase):
    def test_multiline_valve_and_fallback(self):
        summary = parse_log(LOG)
        self.assertEqual(len(summary.errors), 1)
        self.assertIn('128>126', summary.errors[0])
        self.assertIn('BRICK/TEST', summary.errors[0])
        self.assertEqual(summary.problems[0].location, (50, 50, 16))
        self.assertEqual(summary.problems[0].source, '')

    def test_native_without_location_and_unrelated_error(self):
        summary = parse_log('Error: Bad surface extents - surface is too big to have a lightmap\n'
                            '\tmaterial brick/test\nError: Another failure\n')
        self.assertEqual(len(summary.errors), 2)
        self.assertIn('material brick/test', summary.problems[0].message)
        self.assertIsNone(summary.problems[0].location)

    def test_match_full_face_not_just_center(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'map.vmf'
            writer = VMFWriter()
            world = Block('world')
            world.add(writer.solid(box_brush((0, 0, 0), (100, 100, 16), 'brick/test', 'Plane (part 11)')))
            world.add(writer.solid(box_brush((40, 40, 0), (60, 60, 16), 'brick/test', 'Overlap')))
            path.write_text(writer.document(world, []))
            path.with_suffix('.brushes.json').write_text(json.dumps(writer.solid_sources))
            summary = parse_log(LOG, str(path))
            self.assertEqual(len(summary.problems), 1)
            self.assertEqual(summary.problems[0].source, 'Plane (part 11)')
            self.assertIn('exported side', summary.errors[0])
            # Without mapping, still provide a location, never guess an object.
            path.with_suffix('.brushes.json').unlink()
            summary = parse_log(LOG, str(path))
            self.assertEqual(summary.problems[0].source, '')
            self.assertEqual(summary.problems[0].location, (50, 50, 16))


if __name__ == '__main__':
    unittest.main()
