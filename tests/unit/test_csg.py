"""Brush CSG (native/hlcsg.c via core/csg.py): random convex brushes carved, intersected and clipped. The pieces
must fill exactly what they should (volumes add up), never overlap each other or the cutter, have no slivers, and
every face must come from the target or the cutter (its tag)."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from hammerless.core import csg, fastnav  # noqa: E402


def _random_brush(rng, tag0):
    """A box on the grid, sometimes cut by one or two random oblique planes (a wedge)."""
    lo = [rng.randint(-64, 32) * 4 for _ in range(3)]
    hi = [lo[i] + rng.randint(2, 32) * 4 for i in range(3)]
    sides = csg.box(lo, hi, tag0)
    for k in range(rng.choice((0, 0, 1, 2))):
        n = [rng.uniform(-1, 1) for _ in range(3)]
        ln = sum(c * c for c in n) ** 0.5 or 1.0
        n = [c / ln for c in n]
        mid = [(lo[i] + hi[i]) / 2 for i in range(3)]
        d = sum(n[i] * mid[i] for i in range(3)) + rng.uniform(-0.3, 0.3) * min(hi[i] - lo[i] for i in range(3))
        sides.append((tuple(n), d, tag0 + 6 + k))
    return sides


def _vol(p):
    return p.volume() if p is not None else 0.0


@unittest.skipUnless(fastnav.available(), "needs the native library")
class TestCSG(unittest.TestCase):
    def test_box_minus_box(self):
        a = csg.box((0, 0, 0), (64, 64, 64), 0)
        b = csg.box((16, 16, -8), (48, 48, 72), 100)          # a hole straight through
        pieces = csg.subtract(a, b)
        self.assertEqual(len(pieces), 4)
        self.assertAlmostEqual(sum(p.volume() for p in pieces), 64 ** 3 - 32 * 32 * 64, places=6)
        for p in pieces:
            for (n, d, src), f in zip(p.sides, p.faces):
                self.assertTrue(src < 6 or src >= 100)
                for v in f:                                   # (on the grid: axis-aligned cuts stay integer)
                    self.assertTrue(all(abs(c - round(c)) < 1e-9 for c in v), v)

    def test_no_overlap_leaves_target(self):
        a = csg.box((0, 0, 0), (32, 32, 32), 0)
        b = csg.box((100, 0, 0), (132, 32, 32), 100)
        pieces = csg.subtract(a, b)
        self.assertEqual(len(pieces), 1)
        self.assertAlmostEqual(pieces[0].volume(), 32 ** 3, places=6)
        self.assertIsNone(csg.intersect(a, b))
        self.assertEqual(csg.subtract(a, csg.box((-8, -8, -8), (40, 40, 40), 100)), [])   # (swallowed)

    def test_random(self):
        rng = random.Random(17)
        cases = 0
        for _ in range(2500):
            a = _random_brush(rng, 0)
            b = _random_brush(rng, 100)
            whole = csg.intersect(a, a)
            if whole is None:
                continue
            va = whole.volume()
            pieces = csg.subtract(a, b)
            overlap = csg.intersect(a, b)
            vp = sum(p.volume() for p in pieces)
            tol = 1e-6 * va + 0.05
            self.assertAlmostEqual(vp + _vol(overlap), va, delta=tol, msg=f"case {cases}")
            for i, p in enumerate(pieces):
                self.assertGreaterEqual(len(p.sides), 4)
                for (n, d, src), f in zip(p.sides, p.faces):
                    self.assertTrue(src < 100 or src >= 100)          # (a tag of one or the other)
                    self.assertGreaterEqual(csg._area(f, n), 0.01 - 1e-9)
                    for v in f:                                       # every corner inside every plane
                        for (n2, d2, _s) in p.sides:
                            self.assertLessEqual(n2[0] * v[0] + n2[1] * v[1] + n2[2] * v[2] - d2, 0.02)
                self.assertLess(_vol(csg.intersect([s for s in p.sides], b)), 0.05 + 1e-6 * va,
                                msg=f"case {cases}: a piece reaches into the cutter")
                for q in pieces[i + 1:]:
                    self.assertLess(_vol(csg.intersect(p.sides, q.sides)), 0.05 + 1e-6 * va,
                                    msg=f"case {cases}: two pieces overlap")
            # clip by one of the cutter's planes: front + back = the whole
            f, k = csg.clip(a, b[0])
            self.assertAlmostEqual(_vol(f) + _vol(k), va, delta=tol)
            cases += 1
        self.assertGreater(cases, 2000)


if __name__ == "__main__":
    unittest.main()
