"""Slab collision brushes (core/slabcollision.py) on small hand-made meshes."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from hammerless.core.slabcollision import brush_faces, slab_brushes  # noqa: E402

INCH = 0.0254          # metres


def quad(co, tri, a, b, c, d):
    """A quad (counter-clockwise seen from the side it faces) as two triangles."""
    base = len(co)
    co += [a, b, c, d]
    tri += [(base, base + 1, base + 2), (base, base + 2, base + 3)]


def room(size=3.0):
    """A closed box room whose faces look inward."""
    s = size
    co, tri = [], []
    quad(co, tri, (0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0))          # floor, facing up
    quad(co, tri, (0, 0, s), (0, s, s), (s, s, s), (s, 0, s))          # ceiling, facing down
    quad(co, tri, (0, 0, 0), (0, 0, s), (s, 0, s), (s, 0, 0))          # wall y=0, facing +y
    quad(co, tri, (0, s, 0), (s, s, 0), (s, s, s), (0, s, s))          # wall y=s, facing -y
    quad(co, tri, (0, 0, 0), (0, s, 0), (0, s, s), (0, 0, s))          # wall x=0, facing +x
    quad(co, tri, (s, 0, 0), (s, 0, s), (s, s, s), (s, s, 0))          # wall x=s, facing -x
    return np.array(co, float), np.array(tri)


class SlabCollision(unittest.TestCase):
    def test_room_one_brush_per_face_behind_it(self):
        co, tri = room()
        r = slab_brushes(co, tri, INCH, 0.05)
        self.assertEqual(len(r.brushes), 6)
        self.assertEqual(r.kept_regions, 6)
        for vs, m, _ri in r.brushes:
            self.assertEqual(m, 4)
            lo, hi = vs.min(0), vs.max(0)
            # every brush lies outside the room (behind its face), 1 inch thick
            thin = int(np.argmin(hi - lo))
            self.assertAlmostEqual(hi[thin] - lo[thin], INCH, places=6)
            self.assertTrue(hi[thin] <= 1e-9 or lo[thin] >= 3.0 - 1e-9, (lo, hi))

    def test_floor_exact(self):
        co, tri = room()
        r = slab_brushes(co, tri, INCH, 0.05)
        floors = [vs for vs, m, _ in r.brushes if abs(vs[:, 2].max()) < 1e-9]
        self.assertEqual(len(floors), 1)              # its top is exactly the floor (z = 0)

    def test_wall_with_door(self):
        # a 4 m x 3 m wall (facing +y) with a 1 m x 2 m doorway: 8 vertices around the hole
        co = [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3), (1.5, 0, 0), (2.5, 0, 0), (2.5, 0, 2), (1.5, 0, 2)]
        tri = [(0, 4, 7), (0, 7, 3), (7, 6, 3), (6, 2, 3), (6, 5, 1), (6, 1, 2)]
        r = slab_brushes(np.array(co, float), np.array(tri), INCH, 0.05)
        self.assertGreaterEqual(len(r.brushes), 2)
        self.assertLessEqual(len(r.brushes), 4)
        # nothing covers the middle of the doorway
        for vs, m, _ in r.brushes:
            lo, hi = vs.min(0), vs.max(0)
            self.assertFalse(lo[0] < 2.0 < hi[0] and lo[2] < 1.0 < hi[2], (lo, hi))

    def test_small_regions_skipped(self):
        co, tri = room()
        co = list(map(tuple, co))
        tri = list(map(tuple, tri))
        quad(co, tri, (1, 1, 1), (1.1, 1, 1), (1.1, 1.1, 1), (1, 1.1, 1))   # 0.01 m2: trim-sized
        r = slab_brushes(np.array(co, float), np.array(tri), INCH, 0.05)
        self.assertEqual(len(r.brushes), 6)
        self.assertEqual(len(r.skipped), 2)

    def test_two_sided_wall_is_one_brush(self):
        # a 4 m x 3 m wall 6 inches thick: front face (y=0.1524) faces +y, back face (y=0) faces -y
        t = 6 * INCH
        co, tri = [], []
        quad(co, tri, (0, t, 0), (0, t, 3), (4, t, 3), (4, t, 0))          # facing +y? check below
        quad(co, tri, (0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3))
        co, tri = np.array(co, float), np.array(tri)
        r = slab_brushes(co, tri, INCH, 0.05)
        self.assertEqual(r.paired_walls, 1)
        self.assertEqual(len(r.brushes), 1)
        vs = r.brushes[0][0]
        self.assertAlmostEqual(vs[:, 1].min(), 0.0, places=6)
        self.assertAlmostEqual(vs[:, 1].max(), t, places=6)

    def test_wall_split_by_the_mesh_merges(self):
        # one 4 m wall made of two quads that don't share vertices (a T-junction / split vertices)
        co, tri = [], []
        quad(co, tri, (0, 0, 0), (0, 0, 3), (2, 0, 3), (2, 0, 0))
        quad(co, tri, (2, 0, 0), (2, 0, 1.5), (4, 0, 1.5), (4, 0, 0))
        quad(co, tri, (2, 0, 1.5), (2, 0, 3), (4, 0, 3), (4, 0, 1.5))
        r = slab_brushes(np.array(co, float), np.array(tri), INCH, 0.05)
        self.assertEqual(len(r.brushes), 1)

    def test_gable_top_uses_convex_pieces(self):
        # a wall whose top is a triangle (not square-cornered): still covered, exactly on the plane
        co = [(0, 0, 0), (4, 0, 0), (4, 0, 3), (2, 0, 4.5), (0, 0, 3)]
        tri = [(0, 2, 1), (0, 4, 2), (4, 3, 2)]
        r = slab_brushes(np.array(co, float), np.array(tri), INCH, 0.05)
        self.assertGreaterEqual(len(r.brushes), 1)
        top = max(vs[:, 2].max() for vs, _m, _g in r.brushes)
        self.assertGreater(top, 4.4)

    def test_very_thin_two_sided_wall_keeps_a_slab_thickness(self):
        t = 0.2 * INCH                     # two faces 0.2 in apart (a panel): the brush is still 1 inch thick
        co, tri = [], []
        quad(co, tri, (0, t, 0), (0, t, 3), (4, t, 3), (4, t, 0))
        quad(co, tri, (0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3))
        r = slab_brushes(np.array(co, float), np.array(tri), INCH, 0.05)
        self.assertEqual(len(r.brushes), 1)
        vs = r.brushes[0][0]
        self.assertGreaterEqual(vs[:, 1].max() - vs[:, 1].min(), INCH - 1e-9)

    def test_faces_wound_outward(self):
        co, tri = room()
        r = slab_brushes(co, tri, INCH, 0.05)
        vs, m, _ = r.brushes[0]
        centre = vs.mean(0)
        for f in brush_faces(m):
            p = vs[list(f)]
            nrm = np.cross(p[1] - p[0], p[2] - p[0])
            self.assertGreater(float(nrm @ (p.mean(0) - centre)), 0)


if __name__ == "__main__":
    unittest.main()
