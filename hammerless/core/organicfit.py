"""Convert to Brushes, Organic: a solid mesh with no clean faces to build from (a rock, a creature, a stump) as a few
convex pieces that stay within Detail of it.

The solid is cut with planes, one cut at a time, always on the piece with the most AIR (its convex hull minus
itself: the collision a player could bump into where nothing is drawn). The cut kept is the one that leaves the
least air, chosen from planes aimed at the piece's worst air - the point of its hull farthest from the solid -
(the mesh's own faces near it and its biggest faces, the axes, the piece's main axes and the hull faces over it,
through that point and Detail either side) plus a few plain cuts across the piece. On a hollow (an open box, a
frame) no single cut removes air - peeling a wall leaves the same hull - so a cut that peels off finished,
air-free blocks along a face is taken first. A piece is finished when no point of its hull is more than Detail
from the solid; its collision is its hull. Leftovers thinner than Detail fold into their neighbour, and at the
end neighbours whose joined piece is still within Detail and adds little air are joined.

The distances are measured on voxels (Detail / 4, the model voxelised once; every piece keeps the voxels of its
own box). Developed against Extend-or-Fill, CoACD and V-HACD on organic and hard-surface test meshes
(docs/knowledge/models/collision.md). Input in game units. Geometry kernel: manifold3d, via brushfit.

    meshes, info = convert(vertices, triangles, detail=4.0, max_pieces=32)   # meshes: [(corners, triangles)]
"""
from __future__ import annotations

import heapq
import time

import numpy as np

from . import brushfit as _bf
from .brushfit import Manifold, NotSolid, arrays, fold_crumbs, plane_parts, solids, to_manifold, tri_normals, weld

MAX_PIECES = 32
VOXEL_BUDGET = 48_000_000   # voxels in a part's grid at most (about 1-1.5 GB at peak): Fit finer than 2 voxels of it is raised
POOL_AFTER = 1.0            # seconds of cutting in one process before the worker processes start (small shapes never)
POOL_WORK = 100_000         # ...or a cut step this big (triangles x candidate cuts, ~0.1 s here): workers at once
MAX_SIDES = 64              # faces per piece (a curved hull has hundreds: its face directions in 64 groups)
SLIVER = 2.0                # units: a piece thinner than this is a sliver (it joins a neighbour if that stays in Fit)
MIN_BRUSH = 1.0             # units: thinner than this, a piece joins a neighbour regardless (the smallest safe brush)
TIME_LIMIT = 60.0           # seconds: past this every piece left is finished as it is (its hull)
GRID_SHIFT = np.array([0.2345, 0.3456, 0.4567])   # (voxels: on an integer grid, rays along exact edges miscount)

G: dict = {}


# ------------------------------------------------------------------ voxels

def parity_mask(V, T, lo, h, a, b):
    """Voxels with grid index in [a, b) (grid origin lo, size h) whose centre is inside the closed mesh (V, T):
    rays along x, inside between pairs of crossings."""
    xs, ys, zs = (lo[i] + np.arange(a[i], b[i]) * h for i in range(3))
    occ = np.zeros((len(xs), len(ys), len(zs)), bool)
    if len(T) == 0 or occ.size == 0:
        return occ
    Av, Bv, Cv = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    # (only triangles whose yz shadow can hold a ray of this box)
    ymin = np.minimum(np.minimum(Av[:, 1], Bv[:, 1]), Cv[:, 1])
    ymax = np.maximum(np.maximum(Av[:, 1], Bv[:, 1]), Cv[:, 1])
    zmin = np.minimum(np.minimum(Av[:, 2], Bv[:, 2]), Cv[:, 2])
    zmax = np.maximum(np.maximum(Av[:, 2], Bv[:, 2]), Cv[:, 2])
    keep = (ymax >= ys[0]) & (ymin <= ys[-1]) & (zmax >= zs[0]) & (zmin <= zs[-1])
    Av, Bv, Cv = Av[keep], Bv[keep], Cv[keep]
    ymin, ymax, zmin, zmax = ymin[keep], ymax[keep], zmin[keep], zmax[keep]
    d = (Bv[:, 1] - Av[:, 1]) * (Cv[:, 2] - Av[:, 2]) - (Cv[:, 1] - Av[:, 1]) * (Bv[:, 2] - Av[:, 2])
    ok = np.abs(d) > 1e-12
    dd = np.where(ok, d, 1.0)
    for iz, z in enumerate(zs):
        rows = np.nonzero(ok & (zmin <= z) & (zmax >= z))[0]
        if len(rows) == 0:
            continue
        a_, b_, c_ = Av[rows], Bv[rows], Cv[rows]
        py = ys[:, None]
        u = ((py - a_[:, 1]) * (c_[:, 2] - a_[:, 2]) - (c_[:, 1] - a_[:, 1]) * (z - a_[:, 2])) / dd[rows]
        v = ((b_[:, 1] - a_[:, 1]) * (z - a_[:, 2]) - (py - a_[:, 1]) * (b_[:, 2] - a_[:, 2])) / dd[rows]
        hit = (u >= 0) & (v >= 0) & (u + v <= 1)
        x = a_[:, 0] + u * (b_[:, 0] - a_[:, 0]) + v * (c_[:, 0] - a_[:, 0])
        for iy in np.nonzero(hit.any(axis=1))[0]:
            xi = np.sort(x[iy][hit[iy]])
            for s0, s1 in zip(xi[0::2], xi[1::2]):
                i0, i1 = np.searchsorted(xs, [s0, s1])
                occ[i0:i1, iy, iz] = True
    return occ


def _min_plus(f, axis):
    """Along one axis: out[i] = min_j f[j] + (i - j)^2 (exact squared distance, one axis at a time)."""
    f = np.moveaxis(f, axis, -1)
    shape = f.shape
    n = shape[-1]
    g = f.reshape(-1, n)
    ar = np.arange(n, dtype=np.float32)
    d2 = (ar[:, None] - ar[None, :]) ** 2
    out = np.empty_like(g)
    step = max(1, int(4_000_000 // (n * n)))
    for s in range(0, len(g), step):
        out[s:s + step] = (g[s:s + step, None, :] + d2[None, :, :]).min(axis=2)
    return np.moveaxis(out.reshape(shape), -1, axis)


def distance_to(solid, h):
    """Exact Euclidean distance from every voxel to the nearest solid voxel (0 on solid), in units."""
    n0 = solid.shape[0]
    big = np.float32(3.0 * (sum(solid.shape) ** 2) + 1.0)
    # (axis 0 by two sweeps: the nearest solid voxel along each line)
    idx = np.arange(n0, dtype=np.float32)[:, None, None]
    last = np.where(solid, idx, -np.inf)
    last = np.maximum.accumulate(last, axis=0)
    nxt = np.where(solid, idx, np.inf)
    nxt = np.minimum.accumulate(nxt[::-1], axis=0)[::-1]
    d0 = np.minimum(idx - last, nxt - idx)
    f = np.where(np.isfinite(d0), d0 * d0, big).astype(np.float32)
    f = _min_plus(f, 1)
    f = _min_plus(f, 2)
    return np.sqrt(f) * h


def surface_mask(V, T, lo, h, a, b):
    """Voxels (grid index in [a, b)) the surface passes through: points every h/2 over each triangle. A wall thinner
    than a voxel has no voxel centre inside it; this way it still counts as solid."""
    occ = np.zeros(tuple(b - a), bool)
    if len(T) == 0 or occ.size == 0:
        return occ
    P0, P1, P2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    edge = np.maximum(np.linalg.norm(P1 - P0, axis=1), np.linalg.norm(P2 - P0, axis=1))
    edge = np.maximum(edge, np.linalg.norm(P2 - P1, axis=1))
    steps = np.clip(np.ceil(edge / (0.5 * h)).astype(int), 1, 400)
    for m in np.unique(steps):
        rows = np.nonzero(steps == m)[0]
        u, v = np.meshgrid(np.arange(m + 1), np.arange(m + 1), indexing="ij")
        ok = u + v <= m
        u, v = (u[ok] / m)[None, :, None], (v[ok] / m)[None, :, None]
        for s in range(0, len(rows), max(1, 200_000 // len(u[0]))):
            r = rows[s:s + max(1, 200_000 // len(u[0]))]
            P = (P0[r][:, None] + u * (P1[r] - P0[r])[:, None] + v * (P2[r] - P0[r])[:, None]).reshape(-1, 3)
            idx = np.round((P - lo) / h).astype(int) - a
            idx = idx[np.all((idx >= 0) & (idx < (b - a)), axis=1)]
            occ[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    return occ


def _grow(m):
    """m with every voxel's 26 neighbours added."""
    out = m.copy()
    for ax in range(3):
        g = out.copy()
        sl_a = [slice(None)] * 3
        sl_b = [slice(None)] * 3
        sl_a[ax], sl_b[ax] = slice(1, None), slice(None, -1)
        g[tuple(sl_a)] |= out[tuple(sl_b)]
        g[tuple(sl_b)] |= out[tuple(sl_a)]
        out = g
    return out


def voxelise(V, T, lo, h, a, b):
    """Inside (ray parity), plus the surface's voxels where no inside voxel is near: walls thinner than a voxel.
    (Surface voxels everywhere would bring every distance a voxel closer and finish pieces early.)"""
    inside = parity_mask(V, T, lo, h, a, b)
    return inside | (surface_mask(V, T, lo, h, a, b) & ~_grow(inside))


def setup(V, T, D):
    size = float(np.linalg.norm(V.max(0) - V.min(0)))
    ext = V.max(0) - V.min(0)
    # (voxels of Fit/4 - as fine as Fit needs - unless the grid, the part plus its padding, would pass VOXEL_BUDGET
    #  voxels: then the finest that fits. The padding is part of the count: sized from the part alone, a 1.5-unit
    #  button converted with a big object's Fit got 1,100^3 voxels)
    span = size + 4.0 * D
    h = min(D / 4.0, span / 150)
    for _ in range(4):
        box = ext + 2 * (2 * D + 4 * h)
        h = max(h, float(np.prod(box) / VOXEL_BUDGET) ** (1.0 / 3.0))
    # (Fit can't be finer than 2 voxels: below, nearly every empty voxel reads as "too far" and a finish check tests
    #  millions of them - a statue's body at Fit 0.25% took minutes per step. Raised here, said in the message)
    D = max(D, 2.0 * h)
    pad = 2 * D + 4 * h
    lo = V.min(0) - pad + h * GRID_SHIFT
    shape = np.ceil((V.max(0) + pad - lo) / h).astype(int) + 1
    occ = voxelise(V, T, lo, h, np.zeros(3, int), shape)
    G.clear()
    G.update(h=h, lo=lo, shape=shape, D=D, occ=occ)


def hull_planes(Vh, Th):
    """(N, Dp) of a hull mesh: one row per distinct facet plane (largest first), Dp from the hull's own corners
    (a sliver facet's normal is noise; this way every plane still holds the whole hull)."""
    n, area = tri_normals(Vh, Th)
    keep = area > 1e-9
    n = n[keep]
    if len(n) == 0:
        return np.zeros((0, 3)), np.zeros(0)
    q = np.round(n * 1000).astype(np.int64)
    _u, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    ar = np.bincount(inv.ravel(), weights=area[keep], minlength=len(first))
    N = n[first][np.argsort(-ar)]
    return N, (Vh @ N.T).max(0)


# ------------------------------------------------------------------ pieces

class Piece:
    """A solid, its hull and its voxels (a crop of the model grid: `sub`, grid index of its corner `a`).
    The voxels and the hull's planes are worked out only when asked for: a cut is tried ~100 ways per step and
    almost none of those parts are ever looked at again (measured: 2-2.5x faster, the same pieces)."""

    def __init__(self, m, a=None, sub=None, check=True, source=None, extra=()):
        # (m with its folded crumbs `extra`: kept apart until something needs the joined solid - see fold_crumbs)
        self._parts = [m] + list(extra)
        self._m = m if not extra else None
        both = Manifold.compose(self._parts) if extra else m      # (their meshes side by side: no boolean)
        self.V, self.T = arrays(both)
        self.vol = sum(q.volume() for q in self._parts)
        self.area = sum(q.surface_area() for q in self._parts)
        self.hull = Manifold.batch_hull(self._parts) if extra else m.hull()
        self.air = max(0.0, self.hull.volume() - self.vol)
        self._source = source if source is not None else (lambda: (a, sub))     # -> (corner index, voxels)
        self._check = check
        self._a = self._sub = None
        self._N = self._Dp = None
        self._far = None
        self._vox = None

    @property
    def m(self):
        if self._m is None:                 # (joined only for a piece that is cut again, merged or kept)
            self._m = Manifold.batch_boolean(self._parts, _bf.K.OpType.Add)
        return self._m

    def _voxels(self):
        if self._sub is not None:
            return
        a, sub = self._source()
        h, shape = G["h"], G["shape"]
        na = np.maximum(np.floor((self.V.min(0) - G["lo"]) / h).astype(int) - 2, 0)
        nb = np.minimum(np.ceil((self.V.max(0) - G["lo"]) / h).astype(int) + 3, shape)
        box = np.zeros(nb - na, bool)
        ia, ib = np.maximum(a, na), np.minimum(a + np.array(sub.shape), nb)
        if np.all(ib > ia):
            box[ia[0] - na[0]:ib[0] - na[0], ia[1] - na[1]:ib[1] - na[1], ia[2] - na[2]:ib[2] - na[2]] = \
                sub[ia[0] - a[0]:ib[0] - a[0], ia[1] - a[1]:ib[1] - a[1], ia[2] - a[2]:ib[2] - a[2]]
        if self._check and abs(box.sum() * h ** 3 - self.vol) > 0.15 * self.vol + self.area * h:
            box = voxelise(self.V, self.T, G["lo"], h, na, nb)                 # (bookkeeping off: voxelise it)
        self._a, self._sub = na, box
        self._source = None

    @property
    def a(self):
        self._voxels()
        return self._a

    @property
    def sub(self):
        self._voxels()
        return self._sub

    def _planes(self):
        if self._N is None:
            Vh, Th = arrays(self.hull)
            self._N, self._Dp = hull_planes(Vh, Th)

    @property
    def N(self):
        self._planes()
        return self._N

    @property
    def Dp(self):
        self._planes()
        return self._Dp

    def in_hull(self, P):
        out = np.zeros(len(P), bool)
        if len(self.N) == 0:
            return out
        N, Dp = self.N.astype(np.float32), (self.Dp + 1e-4).astype(np.float32)
        for s in range(0, len(P), 1_000_000):              # (a million points at a time: memory stays small)
            Q = P[s:s + 1_000_000]
            alive = np.arange(len(Q))
            for j in range(0, len(N), 16):                 # (the big planes first, then only the points left)
                alive = alive[np.all(Q[alive] @ N[j:j + 16].T <= Dp[j:j + 16], axis=1)]
                if len(alive) == 0:
                    break
            out[s + alive] = True
        return out

    def far(self):
        """(largest distance from the hull's air to the solid, where it is); 0 when it is within Detail."""
        if self._far is None:
            h, lo, D = G["h"], G["lo"], G["D"]
            if not self.sub.any():
                self._far = (0.0, None)          # (smaller than a voxel: finished)
                return self._far
            dist = distance_to(self.sub, h)
            cand = np.argwhere(dist > D)
            if len(cand) == 0:
                self._far = (0.0, None)
                return self._far
            P = (G["lo"] + (cand + self.a) * h).astype(np.float32)
            inh = self.in_hull(P)
            if not inh.any():
                self._far = (0.0, None)
            else:
                dc = dist[tuple(cand[inh].T)]
                k = int(np.argmax(dc))
                self._far = (float(dc[k]), P[inh][k])
        return self._far

    def done(self):
        return self.far()[0] <= G["D"]


def side_masks(p, n, d, parts):
    """The piece's voxels for each part of a plane split: by side; several parts on one side each take the side's
    voxels inside them."""
    h, lo = G["h"], G["lo"]
    if p._vox is None:
        idx = np.argwhere(p.sub)
        p._vox = (lo + (idx + p.a) * h, np.ravel_multi_index(idx.T, p.sub.shape))
    P, flat = p._vox
    s = P @ n - d
    out = []
    for sign, group in ((1, [q for q in parts if q[1] > 0]), (-1, [q for q in parts if q[1] < 0])):
        if not group:
            continue
        side = np.zeros(p.sub.size, bool)
        side[flat[(s >= 0) if sign > 0 else (s < 0)]] = True
        side = side.reshape(p.sub.shape)
        if len(group) == 1:
            out.append((group[0][0], side))
            continue
        b = p.a + np.array(p.sub.shape)
        for m, _sg in group:
            Vm, Tm = arrays(m)
            out.append((m, side & voxelise(Vm, Tm, lo, h, p.a, b)))
    return out


def split(p, n, d):
    """Full plane cut; leftovers (tiny and thinner than Detail along n) fold into the biggest part."""
    got = plane_parts(p.m, n, d, G["D"], SLIVER, p.vol)
    if got is None:
        return None
    big, crumbs = got
    groups, owner = fold_crumbs(big, crumbs)
    cache = {}

    def masks():                       # (the parts' voxels: worked out once, only if one of them is asked)
        if "r" not in cache:
            # (side_masks answers side by side, not in the order asked: each part takes its own voxels by identity)
            got = {id(m): mk for m, mk in side_masks(p, n, d, big + crumbs)}
            mk = [got[id(m)] for m, _sg in big] + [None] * (len(groups) - len(big))
            for j, (cm, _sg) in zip(owner, crumbs):     # (a crumb touching no part is a part of its own)
                mk[j] = got[id(cm)] if mk[j] is None else mk[j] | got[id(cm)]
            cache["r"] = (p.a, mk)
        return cache["r"]
    return [Piece(g[0], extra=g[1:], source=(lambda i=i: (masks()[0], masks()[1][i]))) for i, g in enumerate(groups)]


def candidates(p):
    """[(kind, normal, offset, may_peel)]"""
    D = G["D"]
    _far, spot = p.far()
    out = []
    V, T = p.V, p.T
    if spot is not None:
        n_t, area = tri_normals(V, T)
        ok = area > 1e-9
        n_t = n_t[ok]
        d_t = np.einsum("ij,ij->i", n_t, V[T[ok, 0]])
        q = np.c_[np.round(n_t * 1000).astype(np.int64), np.round(d_t / 0.5).astype(np.int64)]
        _u, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
        Ng, Dg = n_t[first], d_t[first]
        Ag = np.bincount(inv.ravel(), weights=area[ok], minlength=len(first))
        S = V @ Ng.T - Dg
        through = (S.max(0) > 0.5) & (S.min(0) < -0.5)          # (planes that really cut the piece)
        Ng, Dg, Ag = Ng[through], Dg[through], Ag[through]
        gap = np.abs(Ng @ spot - Dg)
        picked = list(np.argsort(gap)[:10])                     # (the faces nearest the worst air...)
        picked += [k for k in np.argsort(-Ag)[:12] if k not in picked]     # (...and the biggest: peels)
        out += [("face", Ng[k], float(Dg[k]), True) for k in picked]
        dirs = list(np.eye(3))
        _u, _s, vt = np.linalg.svd(V - V.mean(0), full_matrices=False)
        dirs += list(vt)
        if len(p.N):
            dirs += list(p.N[np.argsort(p.Dp - p.N @ spot)[:3]])
        for nn in dirs:
            nn = nn / np.linalg.norm(nn)
            for off in (-D, 0.0, D):
                out.append(("spot", nn, float(np.dot(nn, spot)) + off, False))
    lo, hi = V.min(0), V.max(0)
    for ax in range(3):
        e = np.zeros(3)
        e[ax] = 1.0
        for f in (0.25, 0.5, 0.75):
            out.append(("blind", e, float(lo[ax] + f * (hi[ax] - lo[ax])), False))
    return out + snapped(p, out)


def snapped(p, cuts):
    """Each cut nearly along one of the piece's own faces (within 2 degrees, and near it) also tried exactly on that
    face: a cut a little off the underside of a beam leaves a thin slice of the beam on the post below, and the
    post's hull grows a slanted wedge. On the face, the post stays a box."""
    V, T = p.V, p.T
    n_t, area = tri_normals(V, T)
    ok = area > 1e-9
    if not ok.any():
        return []
    n_t = n_t[ok]
    d_t = np.einsum("ij,ij->i", n_t, V[T[ok, 0]])
    q = np.c_[np.round(n_t * 1000).astype(np.int64), np.round(d_t / 0.05).astype(np.int64)]
    _u, first = np.unique(q, axis=0, return_index=True)
    Nf, Df = n_t[first], d_t[first]
    reach = 2.0 * max(G["D"], SLIVER)
    cos2 = np.cos(np.radians(2.0))
    out = []
    for kind, n, d, _peel in cuts:
        c = Nf @ n
        for k in np.nonzero(np.abs(c) > cos2)[0]:
            sign = 1.0 if c[k] > 0 else -1.0
            nn, dd = sign * Nf[k], sign * float(Df[k])
            if abs(dd - d) <= reach and (abs(dd - d) > 1e-6 or np.dot(nn, n) < 1 - 1e-9):
                out.append(("snap-" + kind, nn, dd, True))
    seen, uniq = set(), []                 # (one of each plane)
    for c in out:
        key = (tuple(np.round(c[1], 5)), round(c[2], 4))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def best_cut(p, budget):
    """A peel first (a face cut moving at least 15% of the piece into finished, air-free parts: hollows, frames);
    else the cut leaving the least air (each part beyond two pays), if it removes Detail^3 or 2% of the air;
    else (the piece still stands off its hull by more than Detail, but no one cut removes air - a curved shell, a
    bowl: halving it leaves the same air in two hulls until the pieces are nearly flat) the cut whose worst part
    has the least air, so the pieces keep shrinking."""
    D = G["D"]
    need = max(D ** 3, 0.02 * p.air)
    cands = candidates(p)
    scores = _scores(p, cands)          # (per cut: None or [(volume, air)] per part; parts kept when cut here)
    made = {}

    def parts_of(i):                    # (the real parts of cut i: as scored here, or cut again here)
        if i not in made:
            _k, n, d, _pk = cands[i]
            made[i] = scores[i][1] if scores[i][1] is not None else split(p, n, d)
        return made[i]
    best, peel, even = None, None, None
    for i, (kind, n, d, may_peel) in enumerate(cands):
        info = scores[i][0]
        if info is None or len(info) > budget:
            continue
        a = sum(air for _v, air in info) + need * (len(info) - 2)
        if best is None or a < best[0]:
            best = (a, kind, i)
        worst = max(air for _v, air in info)
        if len(info) == 2 and (even is None or worst < even[0]):
            even = (worst, kind, i)
        if may_peel:
            exact = [k for k, (v, air) in enumerate(info) if air <= 0.005 * v + 1e-6]
            moved = sum(info[k][0] for k in exact)
            # (peels moving about the same volume - within 1% of the piece - are told apart by the air they leave:
            #  a rounding hair's difference in volume must not pick the one that leaves a curved front on a post)
            key = (round(moved / (0.01 * p.vol)), -sum(air for _v, air in info))
            if moved >= 0.15 * p.vol and (peel is None or key > peel[0]) and \
                    time.time() < G.get("deadline", float("inf")):
                parts = parts_of(i)
                if parts is not None and all(parts[k].done() for k in exact):
                    peel = (key, "peel", i)
    pick = None
    if peel is not None:
        pick = peel
    elif best is not None and p.air - best[0] >= need:
        pick = best
    elif even is not None and even[0] < 0.75 * p.air and p.air >= 8 * D ** 3:   # (a shell's air, not a pinhole's)
        pick = (even[0], "halve", even[2])
    if pick is None:
        return None
    parts = parts_of(pick[2])
    return (pick[1], parts) if parts is not None else None


def _scores(p, cands):
    """Every candidate cut's parts as [(volume, air)] - in worker processes once cutting has taken a while (they
    only score; the chosen cut is made again here), else here, keeping the parts. -> [(info, parts or None)]"""
    ex = None
    work = len(p.T) * len(cands)        # (triangles x cuts: a 40k-triangle piece is worth it from the first cut)
    if _bf.WORKERS > 1 and len(cands) >= 16 and not _bf._BROKEN[0] and             (work > POOL_WORK or G.get("cut_time", 0.0) > POOL_AFTER):
        ex = _bf.pool(_bf.WORKERS)
    if ex is not None:
        from concurrent.futures.process import BrokenProcessPool
        V, T = p.V, p.T
        n_chunks = min(len(cands), _bf.WORKERS)          # (one chunk per worker: the mesh goes over once each)
        chunks = [list(range(k, len(cands), n_chunks)) for k in range(n_chunks)]
        try:
            with _bf._NoMain():
                futs = [ex.submit(_bf._organic_task, (V, T, [(cands[i][1], cands[i][2]) for i in ch], G["D"], SLIVER,
                                                      p.vol)) for ch in chunks]
            got = [f.result() for f in futs]
            out = [None] * len(cands)
            for ch, res in zip(chunks, got):
                for i, info in zip(ch, res):
                    out[i] = (info, None)
            return out
        except BrokenProcessPool as e:
            print(f"General: worker processes failed ({e}); carrying on in one process")
            _bf._BROKEN[0] = True
            _bf.shutdown_pool()
    t = time.time()
    out = []
    for _kind, n, d, _pk in cands:
        if time.time() > G.get("deadline", float("inf")):       # (out of time: the rest aren't tried)
            out.append((None, None))
            continue
        parts = split(p, n, d)
        out.append((None if parts is None else [(q.vol, q.air) for q in parts], parts))
    G["cut_time"] = G.get("cut_time", 0.0) + time.time() - t
    return out


def union_mask(p, q):
    a = np.minimum(p.a, q.a)
    b = np.maximum(p.a + np.array(p.sub.shape), q.a + np.array(q.sub.shape))
    out = np.zeros(b - a, bool)
    for r in (p, q):
        o = r.a - a
        s = np.array(r.sub.shape)
        out[o[0]:o[0] + s[0], o[1]:o[1] + s[1], o[2]:o[2] + s[2]] |= r.sub
    return a, out


def merge_pass(done, deadline):
    """Neighbours whose joined piece is still within Detail and adds little air become one."""
    D = G["D"]
    changed = True
    while changed and time.time() < deadline:
        changed = False
        order = sorted(range(len(done)), key=lambda i: done[i].vol)
        for i in order:
            if time.time() > deadline:
                break
            for j in range(len(done)):
                if i == j:
                    continue
                A_, B_ = done[i], done[j]
                if A_.m.min_gap(B_.m, 0.5) > 0.25:
                    continue
                m = A_.m + B_.m
                if m.hull().volume() - m.volume() - A_.air - B_.air >= max(D ** 3, 0.01 * m.volume()):
                    continue
                q = Piece(m, *union_mask(A_, B_), check=False)
                if q.done():
                    done = [x for k, x in enumerate(done) if k not in (i, j)] + [q]
                    changed = True
                    break
            if changed:
                break
    return done


def fewer_sides(hull, max_sides):
    """A hull with at most max_sides faces (a curved piece's hull has hundreds of sliver facets, and as a brush
    every one is a side). Its facet directions are grouped into max_sides clusters and each becomes one plane
    pushed out to touch the hull: it only ever grows, so nothing goes missing (measured on a hand, a rock and
    Suzanne: 64 sides adds 0.4-2.7 points of invisible collision; keeping the biggest facets instead adds 3-10)."""
    Vh, Th = arrays(hull)
    n, area = tri_normals(Vh, Th)
    keep = area > 1e-9
    n, area = n[keep], area[keep]
    if len(np.unique(np.round(n * 1000).astype(np.int64), axis=0)) <= max_sides:
        return hull
    # (directions: the facets' normals grouped into max_sides clusters, area-weighted (k-means on the sphere);
    #  each plane is pushed out to touch the hull, so the result holds all of it)
    k = max(4, max_sides - 6)        # (the box it starts from can keep up to 6 sides of its own)
    rng = np.random.default_rng(1)
    C = n[rng.choice(len(n), min(k, len(n)), replace=False, p=area / area.sum())]
    for _ in range(12):
        lab = np.argmax(n @ C.T, axis=1)
        S = np.zeros_like(C)
        np.add.at(S, lab, n * area[:, None])
        ln = np.linalg.norm(S, axis=1)
        C = np.where(ln[:, None] > 1e-12, S / np.maximum(ln, 1e-12)[:, None], C)
    Dp = (Vh @ C.T).max(0)
    lo, hi = Vh.min(0), Vh.max(0)
    box = Manifold.cube((hi - lo).tolist()).translate(lo.tolist())
    out = box
    for c, d in zip(C, Dp):
        out = out.trim_by_plane((-c).tolist(), float(-d))
    return out if out.volume() >= hull.volume() * (1 - 1e-6) else box


def thinnest(p):
    """The piece's smallest width across its hull's face directions."""
    if len(p.N) == 0:
        return 0.0
    Vh, _ = arrays(p.hull)
    w = Vh @ p.N.T
    return float((w.max(axis=0) - w.min(axis=0)).min())


def join_slivers(done):
    """A piece thinner than SLIVER (a hair-thin leftover between two cuts) joins the touching neighbour whose
    joined hull adds the least air - as long as the joined piece is still within Fit. A piece that is thin because
    the mesh is thin there (a hat's crown at its real size) stays: joined regardless, it filled the hat's cavity."""
    kept = set()
    changed = True
    while changed:
        changed = False
        for i, p in enumerate(done):
            if id(p) in kept or thinnest(p) >= SLIVER:
                continue
            if thinnest(p) >= MIN_BRUSH and time.time() > G.get("deadline", float("inf")) + 15.0:
                continue                    # (late: only brushes too thin to keep are still joined)
            options = []
            for j, q in enumerate(done):
                if j == i or p.m.min_gap(q.m, 0.5) > 0.25:
                    continue
                m = p.m + q.m
                options.append((m.hull().volume() - m.volume() - p.air - q.air, j, m))
            joined = None
            must = thinnest(p) < MIN_BRUSH
            for _added, j, m in sorted(options, key=lambda o: o[0]):
                cand = Piece(m, *union_mask(p, done[j]), check=False)
                if must or cand.done():
                    joined = (j, cand)
                    break
            if joined is None:
                kept.add(id(p))
                continue
            j, cand = joined
            done = [x for k, x in enumerate(done) if k not in (i, j)] + [cand]
            changed = True
            break
    return done


def loose_parts(V, T):
    """The mesh's loose parts as [(V, T)]: each outer shell with the hollows inside it (a hollow's surface is a loose
    part of its own, facing inward: negative volume). Overlapping closed meshes in one object are separate parts
    here - as one mesh the kernel would count their overlap twice and the voxels would read it as outside."""
    n = len(V)
    parent = np.arange(n)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b, c in T:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[find(rc)] = ra
    roots = np.array([find(i) for i in range(n)])
    tri_root = roots[T[:, 0]]
    shells = []
    for r in np.unique(tri_root):
        ts = T[tri_root == r]
        P0, P1, P2 = V[ts[:, 0]], V[ts[:, 1]], V[ts[:, 2]]
        vol = float(np.einsum("ij,ij->i", P0, np.cross(P1, P2)).sum() / 6.0)
        idx = np.unique(ts)
        shells.append((vol, ts, V[idx].min(0), V[idx].max(0)))
    outer = [k for k, sh in enumerate(shells) if sh[0] > 0]
    if len(outer) <= 1:
        return [(V, T)]
    groups = {k: [shells[k][1]] for k in outer}
    for k, (vol, ts, lo, hi) in enumerate(shells):
        if vol > 0:
            continue
        # (a hollow belongs to the smallest outer shell whose box holds it)
        holders = [j for j in outer if np.all(shells[j][2] <= lo + 1e-6) and np.all(shells[j][3] >= hi - 1e-6)]
        j = min(holders, key=lambda j: shells[j][0]) if holders else max(outer, key=lambda j: shells[j][0])
        groups[j].append(ts)
    out = []
    for k in outer:
        ts = np.vstack(groups[k])
        idx, inv = np.unique(ts, return_inverse=True)
        out.append((V[idx], inv.reshape(-1, 3)))
    return out


def convert(V, T, detail=4.0, max_pieces=MAX_PIECES, time_limit=None, cancel=None, max_sides=MAX_SIDES):
    """Mesh (game units) -> convex pieces [(corners, triangles)] and info (pieces, seconds, air, volume, cuts,
    stopped). Raises NotSolid (not a closed solid / inside out). Each loose part is converted on its own (Max
    Pieces shared out by volume, at least 2 each)."""
    t0 = time.time()
    limit = TIME_LIMIT if time_limit is None else time_limit
    V, T = weld(np.asarray(V, float), np.asarray(T, int))
    parts, pinched = [], 0
    for Vp, Tp in loose_parts(V, T):
        # (a loose part can still be several solids touching only at single vertices - a detail welded on by one
        #  point: to the kernel they are separate, and every cut shatters into bits. Each becomes a part of its own)
        m = to_manifold(Vp, Tp)
        comps = solids(m) if "NoError" in str(m.status()) else [m]
        if len(comps) > 1:
            pinched += len(comps) - 1
            for c in comps:
                Vc, Tc = arrays(c)
                if len(Tc):
                    parts.append((Vc, Tc))
        else:
            parts.append((Vp, Tp))
    if len(parts) == 1:
        return _convert_one(V, T, detail, max_pieces, limit, cancel, max_sides)
    vols = []
    for Vp, Tp in parts:
        P0, P1, P2 = Vp[Tp[:, 0]], Vp[Tp[:, 1]], Vp[Tp[:, 2]]
        vols.append(abs(float(np.einsum("ij,ij->i", P0, np.cross(P1, P2)).sum() / 6.0)))
    total = sum(vols) or 1.0
    meshes, info = [], dict(pieces=0, seconds=0.0, air=0.0, volume=0.0, cuts=0, stopped=False)
    for (Vp, Tp), v in sorted(zip(parts, vols), key=lambda x: -x[1]):
        cap = max(2, int(round(max_pieces * v / total)))
        left = max(limit - (time.time() - t0), 1.0)
        ms, inf = _convert_one(Vp, Tp, detail, cap, left, cancel, max_sides)
        meshes += ms
        for k in ("air", "volume", "cuts"):
            info[k] += inf[k]
        info["fit"] = max(info.get("fit", 0.0), inf.get("fit", 0.0))
        info["stopped"] |= inf["stopped"]
    info.update(pieces=len(meshes), seconds=time.time() - t0, parts=len(parts), pinched=pinched)
    return meshes, info


def _convert_one(V, T, detail, max_pieces, limit, cancel, max_sides):
    """One loose part (its hollows included) -> convex pieces and info."""
    t0 = time.time()
    D = max(float(detail), 0.5)
    V, T = weld(np.asarray(V, float), np.asarray(T, int))
    model = to_manifold(V, T)
    status = str(model.status())
    if model.is_empty() or "NoError" not in status:
        raise NotSolid(f"the mesh isn't a closed solid ({status.split('.')[-1]}): fill its holes and remove loose or "
                       "overlapping parts first")
    if model.volume() <= 0:
        raise NotSolid("the mesh is inside out (its normals point inward): recalculate normals outside first")
    setup(V, T, D)
    G["deadline"] = t0 + limit
    heap, done, uid, cuts, stopped = [], [], 0, 0, False
    for s in solids(model):
        p = Piece(s, np.zeros(3, int), G["occ"])
        heapq.heappush(heap, (-p.air, uid, p))
        uid += 1
    _bf._BROKEN[0] = False
    try:
        while heap:
            _neg, _u, p = heapq.heappop(heap)
            if time.time() - t0 > limit or (cancel is not None and cancel()):
                stopped = True
                done.append(p)
                continue
            budget = max_pieces - (len(done) + len(heap))
            if budget < 2 or p.done():
                done.append(p)
                continue
            best = best_cut(p, budget)
            if best is None:
                done.append(p)
                continue
            cuts += 1
            for q in best[1]:
                heapq.heappush(heap, (-q.air, uid, q))
                uid += 1
    finally:
        _bf.shutdown_pool()             # (no idle processes left behind)
    done = merge_pass(done, t0 + limit * 1.25)
    done = join_slivers(done)
    meshes, total = [], 0.0
    for p in done:
        h = fewer_sides(p.hull, max_sides)
        Vh, Th = arrays(h)
        if len(Th):
            meshes.append((Vh, Th))
            total += h.volume()
    return meshes, dict(pieces=len(meshes), seconds=time.time() - t0, air=max(0.0, total - model.volume()),
                        fit=G["D"],
                        volume=model.volume(), cuts=cuts, stopped=stopped)
