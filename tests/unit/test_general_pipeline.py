"""General's worker pipeline: the scorer cache in a worker (brushfit._organic_task) and scoring the heap's next
piece ahead (organicfit.prescore_next) change nothing about the picks; the cut scorer (brushfit.CutScorer)
answers as the kernel does. Run with `python -m unittest discover tests/unit` (no Blender needed)."""
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
    """The cut scorer (brushfit.CutScorer) that scores a cut from the mesh alone, its cost following the cut: its
    spanning forest and Euler tour, the boxes around the cycles of the non-tree edges, and its answers against the
    kernel's, through grid cells or one dense pass alike."""

    def test_tour_and_cycle_boxes(self):
        V, T, _m = hollow_and_bars()
        sc = B.CutScorer(V, T)
        self.assertTrue(sc.ok)
        m, ne = len(T), sc.ne
        self.assertEqual(sc.nshell, 3)                     # (the outer shell with its bar, the hollow, the cube)
        roots = sc.parent == np.arange(m)
        self.assertEqual(int(roots.sum()), 3)
        self.assertTrue(np.all(sc.tree_edge[roots] == ne))
        others = np.nonzero(~roots)[0]
        te = sc.tree_edge[others]
        self.assertTrue(np.all(te < ne))
        # (a tree edge joins the triangle and its parent)
        pair = np.sort(sc.et[te], axis=1)
        self.assertTrue(np.all(pair == np.sort(np.c_[others, sc.parent[others]], axis=1)))
        # (tin/tout: a child's range inside its parent's; the ranges a permutation; sizes add up)
        self.assertTrue(np.all(sc.tin[sc.parent[others]] < sc.tin[others]))
        self.assertTrue(np.all(sc.tout[others] <= sc.tout[sc.parent[others]]))
        self.assertEqual(sorted(sc.tin.tolist()), list(range(m)))
        size = np.ones(m, int)
        order = np.argsort(-sc.tin)                        # (deepest first: children before parents)
        for t in order:
            if not roots[t]:
                size[sc.parent[t]] += size[t]
        self.assertTrue(np.all(sc.tout - sc.tin == size))
        self.assertEqual(sorted((sc.tout - sc.tin)[roots].tolist()), sorted(np.bincount(sc.shell).tolist()))
        # (every non-tree edge's tree path lies in its cycle box)
        depth = np.zeros(m, int)
        for t in np.argsort(sc.tin):
            depth[t] = 0 if roots[t] else depth[sc.parent[t]] + 1
        for i, e in enumerate(sc.nontree):
            a, b = sc.et[e]
            lo, hi = np.minimum(sc.tlo[a], sc.tlo[b]), np.maximum(sc.thi[a], sc.thi[b])
            while a != b:
                if depth[a] < depth[b]:
                    a, b = b, a
                lo, hi = np.minimum(lo, sc.tlo[a]), np.maximum(hi, sc.thi[a])
                a = sc.parent[a]
            self.assertTrue(np.all(sc.plo[i] <= lo + 1e-9) and np.all(sc.phi[i] >= hi - 1e-9), f"edge {e}")

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

    def test_cells_same_as_dense(self):
        V, T, m = hollow_and_bars()
        D, pvol = 3.0, float(m.volume())
        sc = B.CutScorer(V, T, True)
        rnd = np.random.default_rng(3)
        planes = self.planes(V, rnd)
        dense = [sc.score(n, d, D, B.SLIVER, pvol) for n, d in planes]
        sc.dense = False
        cells = [sc.score(n, d, D, B.SLIVER, pvol) for n, d in planes]
        self.assertEqual(repr(dense), repr(cells))
        self.assertGreater(sum(x not in (None, B.KERNEL) for x in dense), 20)


if __name__ == "__main__":
    unittest.main()
