"""General's sliver pass (organicfit.join_slivers): a piece thinner than the smallest safe brush joins a neighbour
when the joined hull is within Fit of the whole model, is thickened into a safe brush when every join would
bridge air, and joins regardless when it is a speck; certify's cache gives the same answers as a fresh call.
Run with `python -m unittest discover tests/unit` (no Blender needed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np  # noqa: E402

from hammerless.core import brushfit as B  # noqa: E402  (first: it puts the vendored kernel on the path)
from hammerless.core import organicfit as OF  # noqa: E402
from manifold3d import Manifold  # noqa: E402


def box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return Manifold.cube((hi - lo).tolist()).translate(lo.tolist())


def model_of(m, D=0.5):
    """Set General up on a solid, as _convert_one does."""
    V, T = OF.arrays(m)
    V, T = OF.weld(np.asarray(V, float), np.asarray(T, int))
    OF.setup(V, T, D)
    return V, T


def piece(m):
    return OF.Piece(m, check=False)


class TestJoinSlivers(unittest.TestCase):
    def test_thin_part_joins_when_the_joined_hull_fits(self):
        # a block and a 0.95-unit slab on its side: together one box, within Fit of the model
        block, slab = box([0, 0, 0], [40, 40, 10]), box([40, 0, 0], [40.95, 40, 10])
        model_of(block + slab)
        done = OF.join_slivers([piece(block), piece(slab)])
        self.assertEqual(len(done), 1)
        self.assertAlmostEqual(done[0].hull.volume(), 40.95 * 40 * 10, places=3)

    def test_thin_part_is_thickened_when_every_join_bridges_air(self):
        # a 0.95-unit fin standing up from the block's edge: joining would fill the air above the block
        block, fin = box([0, 0, 0], [40, 40, 10]), box([40, 0, 0], [40.95, 40, 30])
        model_of(block + fin)
        done = OF.join_slivers([piece(block), piece(fin)])
        self.assertEqual(len(done), 2)
        thick = [p for p in done if getattr(p, "_thick", False)]
        self.assertEqual(len(thick), 1)
        q = thick[0]
        self.assertGreaterEqual(OF.thinnest(q), OF.MIN_BRUSH * (1 - 1e-6))     # (a safe brush now)
        self.assertTrue(OF.fits_model(q.hull))                                  # (and still within Fit)
        self.assertLess(q.hull.volume(), 1.2 * fin.volume() * OF.MIN_BRUSH / 0.95)

    def test_speck_joins_regardless(self):
        # a 0.5-unit speck on the block's edge, standing up: too small to be a brush, it joins as before
        block, speck = box([0, 0, 0], [40, 40, 10]), box([40, 0, 0], [40.5, 3, 13])
        model_of(block + speck)
        self.assertLess(speck.hull().volume(), OF.SPECK)
        done = OF.join_slivers([piece(block), piece(speck)])
        self.assertEqual(len(done), 1)
        self.assertFalse(getattr(done[0], "_thick", False))

    def test_a_thin_mesh_stays_thin_between_min_brush_and_sliver(self):
        # a 1.5-unit fin: a sliver, but a safe brush; joining would fill air, so it stays as it is
        block, fin = box([0, 0, 0], [40, 40, 10]), box([40, 0, 0], [41.5, 40, 30])
        model_of(block + fin)
        done = OF.join_slivers([piece(block), piece(fin)])
        self.assertEqual(len(done), 2)
        self.assertFalse(any(getattr(p, "_thick", False) for p in done))


class TestFitsModel(unittest.TestCase):
    def test_buried_and_bridging_hulls(self):
        block, fin = box([0, 0, 0], [40, 40, 10]), box([40, 0, 0], [40.95, 40, 30])
        model_of(block + fin)
        self.assertTrue(OF.fits_model(box([5, 5, 1], [30, 30, 9])))              # (inside the solid)
        self.assertTrue(OF.fits_model(box([0, 0, 0], [40.4, 40, 10])))           # (0.4 out: within Fit 0.5)
        self.assertFalse(OF.fits_model(box([0, 0, 0], [41.6, 40, 10])))          # (0.65 out)
        self.assertFalse(OF.fits_model((block + fin).hull()))                    # (bridges the air above)

    def test_certify_cache_answers_alike(self):
        m = Manifold.sphere(10.0, 24) + Manifold.cube([30.0, 6.0, 6.0], True).translate([10.0, 0.0, 3.0])
        V, T = model_of(m, 1.0)
        hulls = [box([-4, -4, -4], [4, 4, 4]), box([-8, -2, -2], [25.5, 2, 2]), box([0, 0, 0], [27, 3, 3]),
                 box([-11, -2, -2], [-4, 2, 2]), m.hull()]
        cache = {}
        for h in hulls:
            Vh, Th = OF.arrays(h)
            fresh = OF.certify(V, T, Vh, Th, 1.0)
            cached = OF.certify(V, T, Vh, Th, 1.0, cache=cache)
            self.assertEqual(fresh[0], cached[0])
            self.assertAlmostEqual(fresh[2], cached[2], places=9)
            if fresh[1] is not None:
                np.testing.assert_allclose(fresh[1], cached[1])
        self.assertIn("ray", cache)


if __name__ == "__main__":
    unittest.main()
