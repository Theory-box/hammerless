"""General's worker pipeline: the scorer cache in a worker (brushfit._organic_task) and scoring the heap's next
piece ahead (organicfit.prescore_next) change nothing about the picks; the cut scorer (brushfit.CutScorer)
answers as the kernel does, and the compiled one (brushfit.NativeScorer, core/_hlgeneral.dll) as the Python one,
with Python as the fallback and Settings' toggle honoured. Run with `python -m unittest discover tests/unit` (no
Blender needed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np  # noqa: E402

from hammerless.core import brushfit as B  # noqa: E402  (first: it puts the vendored kernel on the path)
from hammerless.core import organicfit as OF  # noqa: E402
from manifold3d import Manifold  # noqa: E402


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


def hollow_and_bars():
    """A lumpy solid with a hollow inside and a second shell beside it (a composed piece: a part with a crumb)."""
    m = Manifold.sphere(40.0, 48) - Manifold.sphere(18.0, 24).translate([5.0, 0.0, 3.0])
    m += Manifold.cube([110.0, 14.0, 14.0], True).translate([30.0, 0.0, 10.0])
    m += Manifold.cube([8.0, 8.0, 8.0], True).translate([0.0, 60.0, 0.0])
    V, T = OF.arrays(m)
    return np.asarray(V, float), np.asarray(T, int), m


def kernel_score(m, n, d, D, pvol):
    got = B.plane_parts(m, n, d, D, B.SLIVER, pvol)
    if got is None:
        return None
    groups, _owner = B.fold_crumbs(*got)
    return [B.group_score(g) for g in groups]


class TestCutScorer(unittest.TestCase):
    """The cut scorer (brushfit.CutScorer) that scores a cut from the mesh alone: its answers against the kernel's."""

    def planes(self, V, rnd):
        c = V.mean(0)
        ext = V.max(0) - V.min(0)
        out = []
        for _ in range(40):
            n = rnd.normal(size=3)
            n /= np.linalg.norm(n)
            out.append((n, float(n @ (c + rnd.uniform(-0.45, 0.45, 3) * ext))))
        for axis in range(3):                               # (axis planes through vertices: faces in the plane)
            for q in (0.3, 0.5, 0.7):
                n = np.zeros(3)
                n[axis] = 1.0
                out.append((n, float(np.quantile(V[:, axis], q))))
        return out

    def check_against_kernel(self, V, T, m, composed):
        D = 3.0
        pvol = float(m.volume())
        sc = B.CutScorer(V, T, composed)
        self.assertTrue(sc.ok)
        rnd = np.random.default_rng(7)
        scored = 0
        for n, d in self.planes(V, rnd):
            ours = sc.score(n, d, D, B.SLIVER, pvol)
            if ours is B.KERNEL:
                continue
            ref = kernel_score(m, n, d, D, pvol)
            if ours is None and ref is not None:
                # (a plane between two shells, crossing no edge: the scorer answers None, as it always has)
                side = (V @ n - d > 0)[T]
                self.assertTrue(np.all(side.all(1) | ~side.any(1)), f"plane {n} {d}: kernel {ref} ours None")
                continue
            if ref is None or ours is None:
                self.assertIs(ours, ref, f"plane {n} {d}: kernel {ref} ours {ours}")
                continue
            self.assertEqual(len(ref), len(ours), f"plane {n} {d}: kernel {ref} ours {ours}")
            scored += 1
            for (v1, a1), (v2, a2) in zip(sorted(ref), sorted(ours)):
                self.assertAlmostEqual(v1, v2, delta=1e-6 * pvol, msg=f"plane {n} {d}")
                self.assertLessEqual(abs(a1 - a2), 0.02 * max(a1, a2) + 1e-5 * pvol, f"plane {n} {d}: air {a1} vs {a2}")
        self.assertGreater(scored, 20)
        return sc

    def test_against_kernel(self):
        V, T = lumpy()
        self.check_against_kernel(V, T, OF.to_manifold(V, T), False)

    def test_against_kernel_hollow_shells(self):
        # (not composed: the cube shell doesn't touch the body, and a composed piece's shells are taken to)
        V, T, m = hollow_and_bars()
        self.check_against_kernel(V, T, m, False)


@unittest.skipIf(B.native_lib() is None, "the compiled scorer (_hlgeneral.dll) isn't built")
class TestNativeScorer(unittest.TestCase):
    """The compiled scorer answers as the Python one: the same None / KERNEL / parts, volumes equal to rounding, the
    same point sets in the same order where the plane meets no vertex, and its hull is the exact one."""

    def check_same(self, V, T, m, composed):
        D, pvol = 3.0, float(m.volume())
        py, nat = B.CutScorer(V, T, composed), B.NativeScorer(V, T, composed)
        self.assertEqual((py.ok, py.sheet), (nat.ok, nat.sheet))
        rnd = np.random.default_rng(11)
        captured = []
        _hp = B.Manifold.hull_points

        def hp(pts, *a):
            captured.append(np.asarray(pts))
            return _hp(pts, *a)
        scored = 0
        try:
            for n, d in TestCutScorer.planes(self, V, rnd):
                captured.clear()
                B.Manifold.hull_points = staticmethod(hp)
                a = py.score(n, d, D, B.SLIVER, pvol)
                B.Manifold.hull_points = staticmethod(_hp)
                b = nat.score(n, d, D, B.SLIVER, pvol)
                if a is None or a is B.KERNEL:
                    self.assertIs(a, b, f"plane {n} {d}: python {a} native {b}")
                    continue
                self.assertNotIn(b, (None, B.KERNEL), f"plane {n} {d}: python {a} native {b}")
                self.assertEqual(len(a), len(b), f"plane {n} {d}: python {a} native {b}")
                scored += 1
                for (v1, a1), (v2, a2) in zip(sorted(a), sorted(b)):
                    self.assertAlmostEqual(v1, v2, delta=1e-9 * pvol, msg=f"plane {n} {d}")
                    self.assertLessEqual(abs(a1 - a2), 0.02 * max(a1, a2) + 1e-5 * pvol, f"plane {n} {d}: air {a1} vs {a2}")
                if np.any(np.abs(V @ n - d) <= 1e-9 * max(nat_scale(V), 1.0)):
                    continue                    # (a vertex on the plane: the Python one folds a noise sheet of its own)
                pts_py = sorted(captured[-len(b):], key=len)
                pts_nat = sorted([nat.last_points(g) for g in range(len(b))], key=len)
                for pa, pb in zip(pts_py, pts_nat):
                    self.assertEqual(pa.shape, pb.shape, f"plane {n} {d}: point counts")
                    self.assertTrue(np.allclose(pa, pb, rtol=0, atol=1e-9), f"plane {n} {d}: point sets or order")
        finally:
            B.Manifold.hull_points = staticmethod(_hp)
        self.assertGreater(scored, 20)

    def test_same_as_python(self):
        V, T = lumpy()
        self.check_same(V, T, OF.to_manifold(V, T), False)

    def test_same_as_python_hollow_shells(self):
        V, T, m = hollow_and_bars()
        self.check_same(V, T, m, True)

    def test_against_kernel(self):
        V, T, m = hollow_and_bars()
        t = TestCutScorer()
        CutScorer = B.CutScorer
        try:
            B.CutScorer = B.NativeScorer
            t.check_against_kernel(V, T, m, False)
        finally:
            B.CutScorer = CutScorer

    def test_hull_is_exact(self):
        # (the kernel's hull is order-dependent and was measured up to 6% too big on real parts; ours is the exact
        #  volume: a box with points inside, a tetrahedron, a sphere's points)
        rnd = np.random.default_rng(5)
        pts = np.concatenate([rnd.uniform(-10, 10, (500, 3)), [[-10, -10, -10], [10, 10, 10], [-10, 10, 10], [10, -10, -10],
                                                               [10, 10, -10], [-10, -10, 10], [10, -10, 10], [-10, 10, -10]]])
        self.assertAlmostEqual(B.native_hull_volume(pts), 8000.0, places=9)
        self.assertAlmostEqual(B.native_hull_volume(np.array([[0, 0, 0], [6, 0, 0], [0, 6, 0], [0, 0, 6]], float)), 36.0, places=12)
        self.assertEqual(B.native_hull_volume(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], float)), 0.0)
        Vs, _T = OF.arrays(Manifold.sphere(10.0, 64))
        self.assertAlmostEqual(B.native_hull_volume(Vs), float(Manifold.sphere(10.0, 64).volume()), delta=1e-6)

    def test_fallback_and_toggle(self):
        V, T = lumpy()
        self.assertIsInstance(B.cut_scorer(V, T), B.NativeScorer)              # (on by default)
        self.assertIsInstance(B.cut_scorer(V, T, native="python"), B.CutScorer)
        self.assertIsInstance(B.cut_scorer(V, T, native="native"), B.NativeScorer)
        native, state = B.NATIVE[0], dict(B._native)
        try:
            B.NATIVE[0] = False                                              # (Settings: the toggle off)
            self.assertIsInstance(B.cut_scorer(V, T), B.CutScorer)
            self.assertIsInstance(B.cut_scorer(V, T, native=True), B.CutScorer)
            B.NATIVE[0] = True
            B._native["lib"] = False                                         # (the DLL missing or not loading)
            self.assertIsNone(B.native_lib())
            self.assertIsInstance(B.cut_scorer(V, T), B.CutScorer)
            self.assertIsInstance(B.cut_scorer(V, T, native="native"), B.CutScorer)
            B._native.update(state)
            B.native_crashed()                                               # (a worker died with the DLL in use)
            self.assertIsNone(B.native_lib())
            self.assertIsInstance(B.cut_scorer(V, T), B.CutScorer)
        finally:
            B.NATIVE[0] = native
            B._native.clear()
            B._native.update(state)
        self.assertIsInstance(B.cut_scorer(V, T), B.NativeScorer)

    def test_worker_task_mode(self):
        V, T = lumpy()
        D, cuts = 2.0, [(np.array([1.0, 0.0, 0.0]), 10.0)]
        vol = float(OF.to_manifold(V, T).volume())
        B._LAST.clear()
        B._organic_task((V, T, cuts, D, 2.0, vol, False, "python", 31))
        self.assertIsInstance(B._LAST[31]["sc"], B.CutScorer)
        B._organic_task((V, T, cuts, D, 2.0, vol, False, "native", 32))
        self.assertIsInstance(B._LAST[32]["sc"], B.NativeScorer)
        B._LAST.clear()
        m = OF.to_manifold(V, T)
        OF.setup(V, T, D)
        p = OF.Piece(m, np.zeros(3, int), OF.G["occ"])
        self.assertEqual(OF._scorer_mode(p), "native")
        native = B.NATIVE[0]
        try:
            B.NATIVE[0] = False
            self.assertEqual(OF._scorer_mode(p), "python")
        finally:
            B.NATIVE[0] = native


def nat_scale(V):
    return float(np.linalg.norm(V.max(0) - V.min(0)))


if __name__ == "__main__":
    unittest.main()
