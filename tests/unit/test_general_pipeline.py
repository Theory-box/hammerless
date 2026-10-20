"""General's worker pipeline: the scorer cache in a worker (brushfit._organic_task) and scoring the heap's next
piece ahead (organicfit.prescore_next) change nothing about the picks. Run with
`python -m unittest discover tests/unit` (no Blender needed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np  # noqa: E402
from manifold3d import Manifold  # noqa: E402

from hammerless.core import brushfit as B  # noqa: E402
from hammerless.core import organicfit as OF  # noqa: E402


def lumpy():
    """A closed solid with air in its hull - a ball with two bars sticking out - of a few thousand triangles:
    big enough for the scorer and the workers (SCORER_FROM, POOL_WORK)."""
    m = Manifold.sphere(40.0, 72)
    m += Manifold.cube([110.0, 14.0, 14.0], True).translate([30.0, 0.0, 10.0])
    m += Manifold.cube([12.0, 90.0, 12.0], True).translate([-5.0, 25.0, -12.0])
    V, T = OF.arrays(m)
    return np.asarray(V, float), np.asarray(T, int)


class TestWorkerCache(unittest.TestCase):
    def test_two_pieces_kept_by_key(self):
        V, T = lumpy()
        D, cuts = 2.0, [(np.array([1.0, 0.0, 0.0]), 10.0), (np.array([0.0, 0.0, 1.0]), -3.0)]
        vol = float(OF.to_manifold(V, T).volume())
        B._LAST.clear()
        fresh = B._organic_task((V, T, cuts, D, 2.0, vol, False, True, 1))
        sc1 = B._LAST[1]["sc"]
        B._organic_task((V, T, cuts, D, 2.0, vol, False, True, 2))
        self.assertEqual(list(B._LAST), [1, 2])
        again = B._organic_task((V, T, cuts, D, 2.0, vol, False, True, 1))
        self.assertIs(B._LAST[1]["sc"], sc1)                      # (kept: not built again)
        self.assertEqual(list(B._LAST), [2, 1])                   # (used now: to the back)
        self.assertEqual(repr(fresh), repr(again))
        B._organic_task((V, T, cuts, D, 2.0, vol, False, True, 3))
        self.assertEqual(list(B._LAST), [1, 3])                   # (the longest unused one went)
        B._LAST.clear()


class TestPrescoreNext(unittest.TestCase):
    def test_same_pieces_with_and_without_workers(self):
        if (os.cpu_count() or 1) < 4:
            self.skipTest("fewer than 4 cores: no workers")
        V, T = lumpy()
        self.assertGreaterEqual(len(T), OF.SCORER_FROM)
        workers = B.WORKERS
        ahead = []
        _submit = OF._submit

        def submit(p, cands, ex):
            if p._precands is None and len(cands) >= 16 and p._pre is None and p._ok is False:
                ahead.append(p.uid)             # (a full set for a judged piece: prescore_next's)
            return _submit(p, cands, ex)
        try:
            B.WORKERS = 0
            alone, info0 = OF.convert(V, T, detail=1.5, max_pieces=8)
            B.WORKERS = 2
            OF._submit = submit
            pooled, info2 = OF.convert(V, T, detail=1.5, max_pieces=8)
        finally:
            B.WORKERS = workers
            OF._submit = _submit
            B.shutdown_pool()
        self.assertGreater(info0["cuts"], 2)
        self.assertEqual(info0["cuts"], info2["cuts"])
        self.assertGreater(len(ahead), 0, "no piece was scored ahead")
        vols = lambda ms: sorted(round(float(OF.to_manifold(Vh, Th).volume()), 3) for Vh, Th in ms)
        self.assertEqual(vols(alone), vols(pooled))
        self.assertAlmostEqual(info0["air"], info2["air"], places=6)


if __name__ == "__main__":
    unittest.main()
