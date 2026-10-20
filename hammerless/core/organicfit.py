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

Whether a piece is within Detail is decided exactly against its triangles (certify: cubic cells over its hull,
refined until each is certainly within Detail or a hull point farther than Detail is found; a cell over the hull's
own faces and edges, or straddling a surface patch within Detail, passes at the coarsest level, so the cost follows
the air, not the surface). Where its worst air lies is found by a branch-and-bound search over the same kind of
cells (worst_air: exact distances at cell centres, to AIM_RES x Detail): a cut aimed there needs only roughly the
right place. (AIM_VOXELS: the older way, a voxel distance field of Detail / 4 per piece, kept for comparison.)
Developed against Extend-or-Fill, CoACD and V-HACD on organic and hard-surface test meshes
(docs/knowledge/models/collision.md).
Input in game units. Geometry kernel: manifold3d, via brushfit.

    meshes, info = convert(vertices, triangles, detail=4.0, max_pieces=32)   # meshes: [(corners, triangles)]
"""
from __future__ import annotations

import heapq
import itertools
import time

import numpy as np

from . import brushfit as _bf
from .brushfit import Manifold, NotSolid, arrays, fold_crumbs, plane_parts, solids, to_manifold, tri_normals, weld

MAX_PIECES = 32
H_FRAC = 4.0                # voxels of Fit / this (they aim the cuts at the worst air; the finish test is exact)
VOXEL_BUDGET = 8_000_000    # voxels in a part's grid at most (about 250 MB at peak); they only aim the cuts
AIM_VOXELS = False          # aim the cuts from a voxel distance field (the old way) instead of the worst-air search
AIM_RES = 0.25              # of Fit: the worst-air search stops at cells this size, and takes a spot this close to the worst
AIM_CAP = 100_000           # cells kept in the worst-air search's frontier at most (the best-bounded)
WORST_FIRST = 0.05          # a piece whose air averages more than this x Fit over its hull has its worst air searched
#                             before it is certified (see Piece.done): the search alone usually settles it
POOL_AFTER = 1.0            # seconds of cutting in one process before the worker processes start (small shapes never)
POOL_WORK = 100_000         # ...or a cut step this big (triangles x candidate cuts, ~0.1 s here): workers at once
SCORER_FROM = 1500          # triangles: a piece this big is scored without the kernel (brushfit.CutScorer); under it
#                             the kernel's split is the faster (0.8 ms a cut on 600 triangles, 1 ms the scorer)
MAX_SIDES = 64              # faces per piece (a curved hull has hundreds: its face directions in 64 groups)
SLIVER = 2.0                # units: a piece thinner than this is a sliver (it joins a neighbour if that stays in Fit)
MIN_BRUSH = 1.0             # units: thinner than this, a piece joins a neighbour regardless (the smallest safe brush)
TIME_LIMIT = 60.0           # seconds: past this every piece left is finished as it is (its hull)
GRID_SHIFT = np.array([0.2345, 0.3456, 0.4567])   # (voxels: on an integer grid, rays along exact edges miscount)

G: dict = {}
_UIDS = itertools.count()


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
    """Along one axis: out[i] = min_j f[j] + (i - j)^2 (exact squared distance, one axis at a time). The lower
    envelope of the parabolas (Felzenszwalb-Huttenlocher), every line of the grid at once: linear in the line's
    length, where a plain min over all (i, j) is quadratic (a 300-voxel line: 20x less work)."""
    f = np.moveaxis(f, axis, -1)
    shape = f.shape
    n = shape[-1]
    g = np.ascontiguousarray(f.reshape(-1, n), dtype=np.float64)
    L = len(g)
    if L * n * n <= 40_000_000:                         # (a small grid: every (i, j) at once is quicker)
        ar = np.arange(n, dtype=np.float64)
        d2 = (ar[:, None] - ar[None, :]) ** 2
        out = np.empty_like(g)
        step = max(1, int(4_000_000 // (n * n)))
        for s in range(0, L, step):
            out[s:s + step] = (g[s:s + step, None, :] + d2[None, :, :]).min(axis=2)
        return np.moveaxis(out.reshape(shape).astype(np.float32), -1, axis)
    out = g.copy()
    # (a line of one value - no solid anywhere on it, or all solid - is its own transform)
    work = np.nonzero(g.max(axis=1) > g.min(axis=1))[0]
    if len(work):
        out[work] = _envelope(g[work])
    return np.moveaxis(out.reshape(shape).astype(np.float32), -1, axis)


def _envelope(g):
    """out[i] = min_j g[j] + (i - j)^2 per row of g (L, n): the rows' parabola envelopes, built side by side."""
    L, n = g.shape
    q2 = np.arange(n, dtype=np.float64) ** 2
    gq = (g + q2).ravel()                               # (g[j] + j^2, flat: parabola j's height term)
    vf = np.zeros(L * n, np.int64)                      # (the envelope's parabolas, per row)
    zf = np.full(L * (n + 1), np.inf)                   # (where each takes over from the one before)
    base = np.arange(L, dtype=np.int64) * n
    basez = np.arange(L, dtype=np.int64) * (n + 1)
    zf[basez] = -np.inf
    k = np.zeros(L, np.int64)
    for q in range(1, n):
        fq = gq[base + q]
        while True:
            vk = vf.take(base + k)
            s = (fq - gq.take(base + vk)) / (2.0 * (q - vk))
            pop = (s <= zf.take(basez + k)) & (k > 0)
            if not pop.any():
                break
            k[pop] -= 1
        k += 1
        vf[base + k] = q
        zf[basez + k] = s
        zf[basez + k + 1] = np.inf
    out = np.empty_like(g)
    k[:] = 0
    gf = g.ravel()
    for q in range(n):
        while True:
            adv = zf.take(basez + k + 1) < q
            if not adv.any():
                break
            k[adv] += 1
        vk = vf.take(base + k)
        out[:, q] = (q - vk) ** 2 + gf.take(base + vk)
    return out


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
    # (voxels of Fit / H_FRAC: they only find each piece's worst air roughly, the finish test is exact. Coarser if the
    #  grid, the part plus its padding, would pass VOXEL_BUDGET voxels - a 1.5-unit button converted with a big
    #  object's Fit got 1,100^3 voxels once)
    span = size + 4.0 * D
    h = min(D / H_FRAC, span / 100)
    for _ in range(4):
        box = ext + 2 * (2 * D + 4 * h)
        h = max(h, float(np.prod(box) / VOXEL_BUDGET) ** (1.0 / 3.0))
    pad = 2 * D + 4 * h
    lo = V.min(0) - pad + h * GRID_SHIFT
    shape = np.ceil((V.max(0) + pad - lo) / h).astype(int) + 1
    occ = voxelise(V, T, lo, h, np.zeros(3, int), shape) if AIM_VOXELS else None
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


# ------------------------------------------------------------------ exact distance to the solid

def closest_on_triangles(P, A, B, C):
    """Each point's closest point on its triangle (Ericson), paired: P[i] against (A[i], B[i], C[i])."""
    ab, ac, ap = B - A, C - A, P - A
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = P - B
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = P - C
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)
    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    den = va + vb + vc
    den = np.where(np.abs(den) > 0, den, 1.0)
    v = vb / den
    w = vc / den
    out = A + ab * v[:, None] + ac * w[:, None]                     # (inside the face)
    t = (d4 - d3) / np.where((d4 - d3) + (d5 - d6) != 0, (d4 - d3) + (d5 - d6), 1.0)
    m6 = (va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0)
    out = np.where(m6[:, None], B + (C - B) * t[:, None], out)      # (edge bc)
    t = d2 / np.where(d2 - d6 != 0, d2 - d6, 1.0)
    m5 = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    out = np.where(m5[:, None], A + ac * t[:, None], out)           # (edge ac)
    m4 = (d6 >= 0) & (d5 <= d6)
    out = np.where(m4[:, None], C, out)                             # (corner c)
    t = d1 / np.where(d1 - d3 != 0, d1 - d3, 1.0)
    m3 = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    out = np.where(m3[:, None], A + ab * t[:, None], out)           # (edge ab)
    m2 = (d3 >= 0) & (d4 <= d3)
    out = np.where(m2[:, None], B, out)                             # (corner b)
    m1 = (d1 <= 0) & (d2 <= 0)
    out = np.where(m1[:, None], A, out)                             # (corner a)
    return out


def _segmin(val, seg, n):
    """Per segment id (seg ascending) the least value and where it is; inf / -1 for empty ones."""
    cnt = np.bincount(seg, minlength=n)
    starts = np.cumsum(cnt) - cnt
    out = np.full(n, np.inf)
    arg = np.full(n, -1, np.int64)
    has = cnt > 0
    if has.any():
        out[has] = np.minimum.reduceat(val, starts[has])
        win = val <= out[seg]
        first = np.unique(seg[win], return_index=True)
        arg[first[0]] = np.nonzero(win)[0][first[1]]
    return out, arg


def _segmin_into(out, val, seg):
    """out[s] = min(out[s], least val of segment s), seg ascending."""
    if len(seg):
        starts = np.r_[0, np.nonzero(seg[1:] != seg[:-1])[0] + 1]
        ids = seg[starts]
        out[ids] = np.minimum(out[ids], np.minimum.reduceat(val, starts))


def _expand(starts, counts):
    """[starts[i] + r for r in range(counts[i])] for all i, in order; and which i each came from."""
    tot = int(counts.sum())
    if tot == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    rep = np.repeat(np.arange(len(counts)), counts)
    r = np.arange(tot) - np.repeat(np.cumsum(counts) - counts, counts)
    return starts[rep] + r, rep


class TriBuckets:
    """The triangles of a mesh in cubic buckets of size b, for exact nearest-triangle queries up to b away."""

    def __init__(self, V, T, b):
        self.A, self.B, self.C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        self.b = float(b)
        self.lo = np.minimum(np.minimum(self.A, self.B), self.C)
        self.cen = (self.A + self.B + self.C) / 3.0
        self.hi = np.maximum(np.maximum(self.A, self.B), self.C)
        self.org = self.lo.min(0) - 0.5 * b
        ia = np.floor((self.lo - self.org) / b).astype(np.int64)
        ib = np.floor((self.hi - self.org) / b).astype(np.int64)
        self.dim = ib.max(0) + 2
        n = ib - ia + 1
        cnt = n[:, 0] * n[:, 1] * n[:, 2]
        big = cnt > 512                      # (a big slanted triangle: only the buckets its plane crosses)
        cnt = np.where(big, 0, cnt)
        local, tri = _expand(np.zeros(len(cnt), np.int64), cnt)
        k = local % n[tri, 2]
        j = (local // n[tri, 2]) % n[tri, 1]
        i = local // (n[tri, 2] * n[tri, 1])
        keys = ((ia[tri, 0] + i) * self.dim[1] + ia[tri, 1] + j) * self.dim[2] + ia[tri, 2] + k
        more_k, more_t = [], []
        for ti in np.nonzero(big)[0]:
            kk = self._slab(ti, ia[ti], ib[ti])
            more_k.append(kk)
            more_t.append(np.full(len(kk), ti, np.int64))
        if more_k:
            keys = np.concatenate([keys] + more_k)
            tri = np.concatenate([tri] + more_t)
        order = np.argsort(keys, kind="stable")
        keys, self.tri = keys[order], tri[order]
        self.keys, self.start, self.count = np.unique(keys, return_index=True, return_counts=True)
        self.offsets = np.array([(i, j, k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)])
        self._lv = None

    def _slab(self, ti, ia, ib):
        """Bucket keys of one triangle's box that its plane passes through (each column along the normal's main
        axis: the plane's range over it)."""
        A, B, C = self.A[ti], self.B[ti], self.C[ti]
        nrm = np.cross(B - A, C - A)
        ax = int(np.argmax(np.abs(nrm)))
        o = [a for a in range(3) if a != ax]
        if abs(nrm[ax]) < 1e-18:
            ax, o = 0, [1, 2]
        g0 = np.arange(ia[o[0]], ib[o[0]] + 1)
        g1 = np.arange(ia[o[1]], ib[o[1]] + 1)
        G0, G1 = np.meshgrid(g0, g1, indexing="ij")
        G0, G1 = G0.ravel(), G1.ravel()
        lo0 = self.org[o[0]] + G0 * self.b
        lo1 = self.org[o[1]] + G1 * self.b
        # (height of the plane over the column's four corners)
        d = nrm @ A
        hs = []
        for c0 in (lo0, lo0 + self.b):
            for c1 in (lo1, lo1 + self.b):
                hs.append((d - nrm[o[0]] * c0 - nrm[o[1]] * c1) / nrm[ax])
        hmin = np.minimum.reduce(hs)
        hmax = np.maximum.reduce(hs)
        ka = np.maximum(np.floor((hmin - self.org[ax]) / self.b).astype(np.int64), ia[ax])
        kb = np.minimum(np.floor((hmax - self.org[ax]) / self.b).astype(np.int64), ib[ax])
        cnt = np.maximum(kb - ka + 1, 0)
        kk, col = _expand(ka, cnt)
        idx = np.empty((len(kk), 3), np.int64)
        idx[:, ax] = kk
        idx[:, o[0]] = G0[col]
        idx[:, o[1]] = G1[col]
        return (idx[:, 0] * self.dim[1] + idx[:, 1]) * self.dim[2] + idx[:, 2]

    PAIRS = 1_500_000           # (point, triangle) pairs worked on at once
    FEW = 8                     # a cell's triangles are measured outright (no walking down) when this few or fewer

    def _tree(self):
        """The buckets as a pyramid (each level's cells twice the size, merging 8): per level the cells' keys (in
        level-0 numbering), their integer coordinates, the tight box of the surface in each (a triangle's box
        clipped to the bucket: its part in the bucket lies in both), one point of the surface near each (on its
        first triangle) and the cell's triangles (each once). A query walks it down: a cell farther than the
        nearest surface point found so far is dropped with everything in it; one with few triangles is measured
        outright."""
        if self._lv is None:
            dim = self.dim
            keys = self.keys
            co = np.c_[keys // (dim[1] * dim[2]), (keys // dim[2]) % dim[1], keys % dim[2]]
            # (level 0: the triangles' boxes clipped to their bucket, the tightest over each bucket)
            seg = np.repeat(np.arange(len(keys)), self.count)
            clo = self.org + co[seg] * self.b
            lo = np.minimum.reduceat(np.maximum(self.lo[self.tri], clo), self.start, axis=0)
            hi = np.maximum.reduceat(np.minimum(self.hi[self.tri], clo + self.b), self.start, axis=0)
            # (a surface point near each bucket: its first triangle's nearest point to the bucket's centre - a
            #  vertex of a big triangle could be far away, a useless bound)
            t0 = self.tri[self.start]
            rep = closest_on_triangles(self.org + (co + 0.5) * self.b, self.A[t0], self.B[t0], self.C[t0])
            tri, start, count = self.tri, self.start, self.count
            levels = [(keys, co, lo, hi, rep, tri, start, count)]
            while len(keys) > 1:
                pco = co // 2
                pk = (pco[:, 0] * dim[1] + pco[:, 1]) * dim[2] + pco[:, 2]
                keys, first, inv = np.unique(pk, return_index=True, return_inverse=True)
                inv = inv.ravel()
                plo = np.full((len(keys), 3), np.inf)
                phi = np.full((len(keys), 3), -np.inf)
                np.minimum.at(plo, inv, lo)
                np.maximum.at(phi, inv, hi)
                # (the parents' triangles: the children's, each once per parent)
                pair = np.unique(inv[seg] * len(self.A) + tri)
                cell, tri = pair // len(self.A), pair % len(self.A)
                count = np.bincount(cell, minlength=len(keys))
                start = np.cumsum(count) - count
                seg = cell
                co, lo, hi, rep = pco[first], plo, phi, rep[first]
                levels.append((keys, co, lo, hi, rep, tri, start, count))
            self._lv = levels
        return self._lv

    def _ranges(self, P):
        """Per point its 27 neighbouring buckets' (count, start) into tri."""
        N = len(P)
        cell = np.floor((P - self.org) / self.b).astype(np.int64)
        cnt = np.zeros((N, 27), np.int64)
        st = np.zeros((N, 27), np.int64)
        if len(self.keys) == 0:
            return cnt, st
        c = cell[:, None, :] + self.offsets[None, :, :]                   # (N, 27, 3): all neighbours at once
        ok = ((c >= 0) & (c < self.dim)).all(axis=2)
        key = (c[:, :, 0] * self.dim[1] + c[:, :, 1]) * self.dim[2] + c[:, :, 2]
        pos = np.minimum(np.searchsorted(self.keys, key.ravel()), len(self.keys) - 1).reshape(N, 27)
        hit = ok & (self.keys[pos] == key)
        cnt[hit] = self.count[pos[hit]]
        st[hit] = self.start[pos[hit]]
        return cnt, st

    def query(self, P, cap=None):
        """(distance, triangle, closest point) of each point's nearest triangle within cap (b unless given):
        farther, (inf, -1, nan). Exact: the pyramid is walked down, keeping only the cells no farther than the
        nearest surface point known for the point (cap at first, then the cells' sample points, then measured
        triangles), to the triangles of the cells left. Among triangles equally near, the lowest."""
        d = np.full(len(P), np.inf)
        t = np.full(len(P), -1, np.int64)
        Q = np.full((len(P), 3), np.nan)
        if len(self.keys) == 0 or len(P) == 0:
            return d, t, Q
        cap = self.b if cap is None else float(cap)
        levels = self._tree()
        for s in range(0, len(P), 50_000):
            self._query(P[s:s + 50_000], cap, levels, d[s:s + 50_000], t[s:s + 50_000], Q[s:s + 50_000])
        return d, t, Q

    def _query(self, P, cap, levels, d, t, Q):
        N = len(P)
        dim = self.dim
        top = len(levels) - 1
        ub = np.full(N, cap)
        # (the walk starts where the cells are at least cap: the 27 around a point's hold all within cap of it - at
        #  cap b that is the buckets themselves, one level; with no cap, the top)
        L = top if not np.isfinite(cap) else min(top, max(0, int(np.ceil(np.log2(max(cap / self.b, 1e-300))))))
        if L == top and len(levels[top][0]) == 1:
            pt = np.arange(N)
            cell = np.zeros(N, np.int64)
        else:
            c = np.floor((P - self.org) / self.b).astype(np.int64) >> L
            c = (c[:, None, :] + self.offsets[None, :, :]).reshape(-1, 3)
            ok = ((c >= 0) & ((c << L) < dim)).all(axis=1)
            kk = (c[:, 0] * dim[1] + c[:, 1]) * dim[2] + c[:, 2]
            lk = levels[L][0]
            pos = np.minimum(np.searchsorted(lk, kk), len(lk) - 1)
            hit = ok & (lk[pos] == kk)
            pt, cell = np.repeat(np.arange(N), 27)[hit], pos[hit]
        while len(pt):
            keys, co, lo, hi, rep, tri, start, count = levels[L]
            Pp = P[pt]
            # (the nearest surface point known: the cell's sample point tightens it)
            _segmin_into(ub, np.linalg.norm(Pp - rep[cell], axis=1), pt)
            lb = np.linalg.norm(np.maximum(np.maximum(lo[cell] - Pp, Pp - hi[cell]), 0.0), axis=1)
            keep = lb <= ub[pt]
            pt, cell = pt[keep], cell[keep]
            # (cells of few triangles, and every cell at the bottom: measured)
            few = count[cell] <= self.FEW if L > 0 else np.ones(len(cell), bool)
            if few.any():
                idx, blk = _expand(start[cell[few]], count[cell[few]])
                self._measure(P, pt[few][blk], tri[idx], ub, d, t, Q)
            if L == 0:
                break
            pt, cell = pt[~few], cell[~few]
            kids = (co[cell][:, None, :] * 2 + CORNERS[None]).reshape(-1, 3)
            kk = (kids[:, 0] * dim[1] + kids[:, 1]) * dim[2] + kids[:, 2]
            nk = levels[L - 1][0]
            pos = np.minimum(np.searchsorted(nk, kk), len(nk) - 1)
            hit = nk[pos] == kk
            pt, cell = np.repeat(pt, 8)[hit], pos[hit]
            L -= 1

    def _measure(self, P, pt, tri, ub, d, t, Q):
        """The (point, triangle) pairs' real distances into d, t, Q and the bound ub, each pair once, in a fixed
        order; a triangle's box no nearer than the nearest surface point known is dropped before the real
        distance."""
        N = len(P)
        pair = np.sort(pt * len(self.A) + tri)                   # (each pair once: sorted, repeats dropped - the
        pair = pair[np.r_[True, pair[1:] != pair[:-1]]]          #  same as np.unique, which hashes: 5x slower here)
        pt, tri = pair // len(self.A), pair % len(self.A)
        for s in range(0, len(pt), self.PAIRS):
            ps, ts = pt[s:s + self.PAIRS], tri[s:s + self.PAIRS]
            Pp = P[ps]
            # (the triangle of the nearest centroid first, its real distance the bound for the rest: a point by a
            #  dense surface then measures a few triangles, not every one in reach of the cells' sample points)
            _c, argc = _segmin(np.linalg.norm(self.cen[ts] - Pp, axis=1), ps, N)
            one = argc[argc >= 0]
            p1, t1 = ps[one], ts[one]
            X1 = closest_on_triangles(P[p1], self.A[t1], self.B[t1], self.C[t1])
            ub[p1] = np.minimum(ub[p1], np.linalg.norm(X1 - P[p1], axis=1))
            box = np.linalg.norm(np.maximum(np.maximum(self.lo[ts] - Pp, Pp - self.hi[ts]), 0.0), axis=1)
            keep = box <= ub[ps]
            ps, ts = ps[keep], ts[keep]
            if len(ps) == 0:
                continue
            X = closest_on_triangles(P[ps], self.A[ts], self.B[ts], self.C[ts])
            dist = np.linalg.norm(X - P[ps], axis=1)
            best, arg = _segmin(dist, ps, N)
            tb = np.where(arg >= 0, ts[np.maximum(arg, 0)], -1)
            got = (arg >= 0) & (best <= ub) & ((best < d) | ((best == d) & (tb < t)))
            d[got], t[got], Q[got] = best[got], tb[got], X[arg[got]]
            ub[got] = best[got]


class Clearance:
    """Is nothing of the mesh within r of a point? Buckets of size b marked where a triangle's box reaches, summed
    (a 3D summed-area table): a box of buckets around the point's is empty in one look."""

    def __init__(self, tb):
        self.b, self.org, self.dim = tb.b, tb.org, tb.dim
        dim = tuple(int(x) for x in tb.dim)
        g = np.zeros(dim, np.int32)
        g.ravel()[tb.keys] = 1
        for ax in range(3):
            g = np.cumsum(g, axis=ax, dtype=np.int32)
        self.sat = np.pad(g, ((1, 0), (1, 0), (1, 0)))

    def clear(self, P, r):
        """Per point: certainly no triangle within r."""
        k = int(np.ceil(r / self.b))
        c = np.floor((P - self.org) / self.b).astype(np.int64)
        lo = np.clip(c - k, 0, self.dim - 1)
        hi = np.clip(c + k, 0, self.dim - 1) + 1
        # (points so far outside the grid that their box reaches nothing: all clear)
        out = (c + k < 0).any(axis=1) | (c - k >= self.dim).any(axis=1)
        S = self.sat
        n = (S[hi[:, 0], hi[:, 1], hi[:, 2]] - S[lo[:, 0], hi[:, 1], hi[:, 2]] - S[hi[:, 0], lo[:, 1], hi[:, 2]]
             - S[hi[:, 0], hi[:, 1], lo[:, 2]] + S[lo[:, 0], lo[:, 1], hi[:, 2]] + S[lo[:, 0], hi[:, 1], lo[:, 2]]
             + S[hi[:, 0], lo[:, 1], lo[:, 2]] - S[lo[:, 0], lo[:, 1], lo[:, 2]])
        return out | (n == 0)


def _ray_bucket(V):
    """RayParity's column size for a mesh: its answer is exact at any size, the size only sets how many triangles a
    ray tests (measured on a 45k-triangle body at Fit: 150 a point, 55 s of a 54 s conversion; at 1/512 of the
    extent, a few; at 1/512 the table itself cost 45 s: a cut's cap triangles span the extent)."""
    return max(float((V.max(0) - V.min(0)).max()) / 128, 1e-6)


class RayParity:
    """Inside/outside of a closed mesh for any points: rays along +x, crossings counted with a consistent
    tie-break (a ray exactly through an edge or corner counts once, not once per triangle)."""

    def __init__(self, V, T, b):
        A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        self.d2 = (B[:, 1] - A[:, 1]) * (C[:, 2] - A[:, 2]) - (C[:, 1] - A[:, 1]) * (B[:, 2] - A[:, 2])
        self.A, self.B, self.C = A, B, C
        self.b = float(b)
        # (a triangle wholly behind a point along x is never crossed: skipped before the crossing test, with a
        #  margin far above the rounding of the crossing's x)
        self.xmax = np.maximum(np.maximum(A[:, 0], B[:, 0]), C[:, 0])
        self.tiny = 1e-9 * float((V.max(0) - V.min(0)).max()) if len(V) else 0.0
        lo = np.minimum(np.minimum(A, B), C)[:, 1:]
        hi = np.maximum(np.maximum(A, B), C)[:, 1:]
        self.org = lo.min(0) - 0.5 * b
        ia = np.floor((lo - self.org) / b).astype(np.int64)
        ib = np.floor((hi - self.org) / b).astype(np.int64)
        self.dim = ib.max(0) + 2
        n = ib - ia + 1
        cnt = np.where(np.abs(self.d2) > 0, n[:, 0] * n[:, 1], 0)     # (edge-on triangles: no crossing)
        local, tri = _expand(np.zeros(len(cnt), np.int64), cnt)
        j = local % n[tri, 1]
        i = local // n[tri, 1]
        keys = (ia[tri, 0] + i) * self.dim[1] + ia[tri, 1] + j
        order = np.argsort(keys, kind="stable")
        keys, self.tri = keys[order], tri[order]
        self.keys, self.start, self.count = np.unique(keys, return_index=True, return_counts=True)

    @staticmethod
    def _side(ay, az, by, bz, py, pz):
        e = (by - ay) * (pz - az) - (bz - az) * (py - ay)
        s = np.sign(e)
        tie = s == 0                                   # (on the edge's line: the point nudged by (eps, eps^2))
        s = np.where(tie, -np.sign(bz - az), s)
        s = np.where(tie & (s == 0), np.sign(by - ay), s)
        return s

    PAIRS = 1_000_000           # point-triangle pairs tested at once at most (memory: ~20 arrays of them)

    def inside(self, P, chunk=100_000):
        out = np.zeros(len(P), bool)
        for s in range(0, len(P), chunk):
            out[s:s + chunk] = self._inside(P[s:s + chunk])
        return out

    def _inside(self, P):
        N = len(P)
        if len(self.keys) == 0:
            return np.zeros(N, bool)
        c = np.floor((P[:, 1:] - self.org) / self.b).astype(np.int64)
        ok = np.all((c >= 0) & (c < self.dim), axis=1)
        key = c[:, 0] * self.dim[1] + c[:, 1]
        pos = np.minimum(np.searchsorted(self.keys, key), len(self.keys) - 1)
        hit = ok & (self.keys[pos] == key)
        cnt = np.where(hit, self.count[pos], 0)
        if cnt.sum() > self.PAIRS and N > 1:          # (too many pairs for one go: halves - 6.7M on a 45k-tri body)
            h = N // 2
            return np.concatenate([self._inside(P[:h]), self._inside(P[h:])])
        idx, pt = _expand(np.where(hit, self.start[pos], 0), cnt)
        if len(idx) == 0:
            return np.zeros(N, bool)
        tri = self.tri[idx]
        ahead = self.xmax[tri] > P[pt, 0] - self.tiny
        tri, pt = tri[ahead], pt[ahead]
        A, B, C = self.A[tri], self.B[tri], self.C[tri]
        py, pz = P[pt, 1], P[pt, 2]
        s1 = self._side(A[:, 1], A[:, 2], B[:, 1], B[:, 2], py, pz)
        s2 = self._side(B[:, 1], B[:, 2], C[:, 1], C[:, 2], py, pz)
        s3 = self._side(C[:, 1], C[:, 2], A[:, 1], A[:, 2], py, pz)
        within = (s1 == s2) & (s2 == s3) & (s1 != 0)
        d2 = self.d2[tri]
        u = ((py - A[:, 1]) * (C[:, 2] - A[:, 2]) - (C[:, 1] - A[:, 1]) * (pz - A[:, 2])) / d2
        v = ((B[:, 1] - A[:, 1]) * (pz - A[:, 2]) - (py - A[:, 1]) * (B[:, 2] - A[:, 2])) / d2
        x = A[:, 0] + u * (B[:, 0] - A[:, 0]) + v * (C[:, 0] - A[:, 0])
        cross = within & (x > P[pt, 0])
        return (np.bincount(pt[cross], minlength=N) & 1).astype(bool)


def facet_planes(Vh, Th):
    """Every distinct facet plane of a hull (normals told apart at 1e-5), each holding the whole hull; big first."""
    n, area = tri_normals(Vh, Th)
    keep = area > 1e-12
    n, area = n[keep], area[keep]
    if len(n) == 0:
        return np.zeros((0, 3)), np.zeros(0)
    q = np.round(n * 1e5).astype(np.int64)
    _u, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    ar = np.bincount(inv.ravel(), weights=area, minlength=len(first))
    N = n[first][np.argsort(-ar)]
    return N, (Vh @ N.T).max(0)


CERT_TOL = 1e-3            # of Fit: a piece is certified within Fit + this
CELL_CAP = 2_000_000       # cells in one refinement level at most: past it, what is left is accepted as is
CELL_SHIFT = np.array([0.2345, 0.3456, 0.4567])
TRACE = False
SQ3 = float(np.sqrt(3.0))


def certify(V, T, Vh, Th, D):
    """Is every point of the hull (Vh, Th) within D of the solid (V, T)? Exact against the triangles, to a
    tolerance of CERT_TOL * D: cubic cells over the hull are refined until each is certainly within D (the
    distance to a single triangle is convex, so a cell is within the farthest its corners are from the nearest
    triangle of one of them; a cell with a corner inside the solid farther than its diagonal from the surface is
    inside), or a point of the hull outside the solid farther than D is found - the witness.
    -> (ok, witness point or None, its distance)"""
    if len(T) == 0 or len(Th) == 0:
        return True, None, 0.0
    _tp = time.time()
    lo, hi = Vh.min(0), Vh.max(0)
    ext = float((hi - lo).max())
    if ext <= 0:
        return True, None, 0.0
    N, Dp = facet_planes(Vh, Th)
    if len(N) == 0:
        return True, None, 0.0
    eps = 1e-9 * ext
    absN1 = np.abs(N).sum(1)
    ray = RayParity(V, T, _ray_bucket(V))
    # (only faces with solid behind them: a cut exactly along a cavity's ceiling leaves a sheet of no thickness
    #  across the cavity - by its triangles the cavity would be within Fit of it)
    nt, area = tri_normals(V, T)
    cen = (V[T[:, 0]] + V[T[:, 1]] + V[T[:, 2]]) / 3.0
    real = (area > 0) & ray.inside(cen - nt * (1e-4 * D))
    if not real.all():
        T = T[real]
        if len(T) == 0:
            return True, None, 0.0
    grids = {}

    def buckets(b):
        if b not in grids:
            grids[b] = TriBuckets(V, T, b)
        return grids[b]
    tb = buckets(D)
    # (which triangles lie on a hull plane: the hull coincides with the solid there - no air on them)
    coin = _coincident(tb, N, Dp)
    # (how far from the surface the far points are, coarsely: buckets of D, or coarser for a huge part)
    bc = D
    while np.prod(np.ceil((V.max(0) - V.min(0)) / bc) + 3) > 20_000_000:
        bc *= 2
    clearance = Clearance(tb if bc == D else TriBuckets(V, T, bc))

    # (cell sizes: 0.55 Fit doubled up to about an eighth of the hull - cells straddling a flat surface certify
    #  from there down - and halved to the tolerance)
    levels = max(0, int(np.ceil(np.log2(ext / (8 * 0.55 * D))))) if ext > 8 * 0.55 * D else 0
    s0 = 0.55 * D * 2 ** levels
    s_min = CERT_TOL * D / SQ3
    org = lo - s0 * CELL_SHIFT
    n0 = np.ceil((hi - org) / s0).astype(np.int64) + 1
    I, J, K = np.meshgrid(*(np.arange(n) for n in n0), indexing="ij")
    cells = np.c_[I.ravel(), J.ravel(), K.ravel()]
    pc = np.repeat(np.arange(len(cells)), len(N))             # (active (cell, plane) pairs: all at first)
    pp = np.tile(np.arange(len(N)), len(cells))
    s = s0
    hb = None
    if TRACE:
        print(f"      certify setup {time.time() - _tp:.3f} s ({len(T)} tris, {len(N)} hull planes)")
    while True:
        half = 0.5 * s
        centre = org + (cells + 0.5) * s
        # (cells wholly outside a hull plane are dropped; planes wholly holding a cell hold its children too)
        v = np.einsum("ij,ij->i", N[pp], centre[pc]) - Dp[pp]
        w = half * absN1[pp]
        out = np.zeros(len(cells), bool)
        out[pc[v - w > eps]] = True
        live = ~(v - w > eps) & ~(v + w <= eps)
        pc, pp = pc[live], pp[live]
        if out.any():
            keep = ~out
            remap = np.cumsum(keep) - 1
            cells = cells[keep]
            m = keep[pc]
            pc, pp = remap[pc[m]], pp[m]
        if len(cells) == 0:
            return True, None, 0.0
        # (the corners, each once)
        corner = (cells[:, None, :] + CORNERS[None]).reshape(-1, 3)
        dim = cells.max(0) + 2
        key = (corner[:, 0] * dim[1] + corner[:, 1]) * dim[2] + corner[:, 2]
        ukey, inv = np.unique(key, return_inverse=True)
        inv = inv.ravel().reshape(len(cells), 8)
        pts = np.c_[ukey // (dim[1] * dim[2]), (ukey // dim[2]) % dim[1], ukey % dim[2]]
        P = org + pts * s
        # (which corners are in the hull: the planes still active for the cell decide, the rest hold it whole)
        cin = np.ones((len(cells), 8), bool)
        for a in range(0, len(pc), 200_000):
            pcs, pps = pc[a:a + 200_000], pp[a:a + 200_000]
            hc = np.einsum("ijk,ik->ij", P[inv[pcs]], N[pps]) - Dp[pps][:, None] > eps
            np.logical_and.at(cin, pcs, ~hc)
        b = D
        _t0 = time.time()
        d, t, _X = tb.query(P)
        capped = ~np.isfinite(d)
        dl = np.where(capped, D, d)                              # (a lower bound where nothing was within D)
        _t1 = time.time()
        ins = ray.inside(P)
        _t2 = time.time()
        # (a hull point outside the solid beyond D: the piece is not within Fit; the farthest of them, roughly)
        far = ~ins & (capped | (d > D))
        hit = far[inv] & cin
        if hit.any():
            cand = np.unique(inv[hit])
            for r in (1.25, 1.5, 2.0, 3.0, 4.0, 8.0, 16.0):
                ok_r = clearance.clear(P[cand], r * D)
                dl[cand[ok_r]] = np.maximum(dl[cand[ok_r]], r * D)
            k = cand[np.argmax(dl[cand])]
            return False, P[k], float(dl[k])
        # (a cell with no corner in the hull - a thin piece's hull slips between them - is judged at the hull's
        #  nearest point to its centre; farther than the cell reaches, the cell misses the hull)
        none_in = ~cin.any(axis=1)
        if none_in.any():
            if hb is None:
                hb = TriBuckets(Vh, Th, max(s0 * SQ3, ext / 64))
            rows = np.nonzero(none_in)[0]
            c = centre[rows]
            dh, _th, wit = hb.query(c)
            near = dh <= half * SQ3 * (1 + 1e-9)
            miss = rows[~near]
            if near.any():
                dw, _tw, _Xw = tb.query(wit[near])
                dw = np.where(np.isfinite(dw), dw, D * (1 + 1e-9))
                bad = dw > D
                if bad.any():
                    k = np.argmax(dw)
                    return False, wit[near][k], float(dw[k])
        else:
            miss = np.zeros(0, np.int64)
        # (per cell: inside the solid; or within D by the corner bound)
        # (a corner inside the solid with no surface within the cell's diagonal: the cell is inside)
        reach = s * SQ3
        deep = ins & (clearance.clear(P, reach) if reach > D else (dl >= reach))
        dc, tc, insc = dl[inv], t[inv], ins[inv]
        ok = deep[inv].any(axis=1)
        ok[miss] = True
        rest = np.nonzero(~ok)[0]
        # (three sufficient tests, the cheap one first and the others only on what it leaves: on a dense organic
        #  mesh the corners' triangles settle three cells in four at a tenth of the cost of the other two)
        if len(rest):
            pts20, valid = _clipped(P, inv[rest], rest, pc, pp, N, Dp, len(cells))
            tbs = buckets(float(max(D, half)))
            ub = _corner_bound(pts20, valid, dc[rest], tc[rest], insc[rest], tb, tbs, D)
            fine = ub <= D
            ok[rest[fine]] = True
            rest = rest[~fine]
            pts20, valid = pts20[~fine], valid[~fine]
        if len(rest):
            airless = _no_air(pts20, valid, insc[rest], tbs, coin, N, Dp, D)
            ok[rest[airless]] = True
            rest = rest[~airless]
            pts20, valid = pts20[~airless], valid[~airless]
        if len(rest):
            pb = _patch_bound(pts20, valid, insc[rest], tbs, coin, s, eps)
            ok[rest[pb <= D]] = True
        rest = np.nonzero(~ok)[0]
        if TRACE:
            print(f"      certify s {s / D:.4f} D: {len(cells)} cells, {len(P)} points, {len(rest)} left; b {b / D:.2f} D "
                  f"query {_t1 - _t0:.3f} inside {_t2 - _t1:.3f} bound {time.time() - _t2:.3f}")
        if len(rest) == 0:
            return True, None, 0.0
        if s <= s_min or len(rest) > CELL_CAP:
            # (finest: every cell left has a hull point within D - a corner, or the nearest to its centre - and
            #  the rest of it is within the tolerance of that)
            return True, None, 0.0
        cells = (cells[rest][:, None, :] * 2 + CORNERS[None]).reshape(-1, 3)
        remap = np.full(len(ok), -1)
        remap[rest] = np.arange(len(rest))
        m = remap[pc] >= 0
        pc, pp = remap[pc[m]], pp[m]
        pc = (pc[:, None] * 8 + np.arange(8)[None]).ravel()
        pp = np.repeat(pp, 8)
        s = half


CORNERS = np.array([(i, j, k) for i in (0, 1) for j in (0, 1) for k in (0, 1)])


EDGES = np.array([(a, b_) for a in range(8) for b_ in range(a + 1, 8) if bin(a ^ b_).count("1") == 1])


class SurfaceDist:
    """Exact distance from any points to a mesh's surface: triangle buckets of Fit (built when first needed),
    walked as a pyramid for any distance."""

    def __init__(self, V, T, b):
        self.V, self.T, self.b = V, T, float(b)
        self.tb = None

    def __call__(self, P, cap=None):
        """Distances; inf where farther than cap (or than the whole mesh)."""
        if self.tb is None:
            self.tb = TriBuckets(self.V, self.T, self.b)
        return self.tb.query(P, cap=np.inf if cap is None else cap)[0]


def worst_air(p, D):
    """(distance, point): about the hull point of the piece farthest from its solid - its worst air - to AIM_RES
    x Fit, by branch and bound over cells of the hull's box: a cell can hold no point farther than its centre's
    distance plus its half diagonal (or the half diagonal, with its centre inside the solid), so cells that
    cannot beat the best point found by AIM_RES x Fit are dropped, the rest halved, down to cells of AIM_RES x
    Fit; then the winning cell is followed down to Fit / 64 (its best child's centre each time: the spot is a
    real local worst, not a cell centre). The cost follows the air, not the box: no voxel grid, and the same at
    any Fit. (best, None) when no air point was found (a cell centre in the hull and out of the solid)."""
    return worst_air_of(p.V, p.T, arrays(p.hull)[0], p.N, p.Dp, D)


def judge(V, T, Vh, Th, air, vol, D):
    """Is a piece (solid V, T; hull Vh, Th; its air and volume) within Fit? -> (ok, (witness point or None, its
    distance), worst air (best, spot) or None where it wasn't searched). The worst air first, for a piece with
    air to spare - thicker than WORST_FIRST x Fit over its hull on average: a real point of the hull, outside the
    solid and farther than Fit plus the tolerance from it, is what certify would have to find - the piece isn't
    within Fit, no need to look again - and it is where the cut is aimed anyway (far). certify's own search runs
    only when the worst air found is within Fit or the tolerance. Either order gives the same answer; measured
    on a statue certify failed 24 pieces in 32, at 0.25 s each, and the search then ran anyway. A piece with
    thinner air likely passes certify at its coarsest cells, where the search would still have to look
    everywhere. Arrays in, plain values out: it runs in a worker process too (brushfit._judge_task)."""
    worst = None
    if not AIM_VOXELS and air > WORST_FIRST * D * _area(Vh, Th):
        N, Dp = hull_planes(Vh, Th)
        worst = worst_air_of(V, T, Vh, N, Dp, D)
        best, spot = worst
        if spot is not None and best > D * (1 + CERT_TOL):
            return False, (spot, best), worst
    ok, wit, dw = certify(V, T, Vh, Th, D)
    return ok, (wit, dw), worst


def _area(V, T):
    return float(tri_normals(V, T)[1].sum()) if len(T) else 0.0


def worst_air_of(V, T, Vh, N, Dp, D):
    """worst_air of a solid (V, T) with the hull corners Vh and hull planes (N, Dp)."""
    lo, hi = Vh.min(0), Vh.max(0)
    ext = float((hi - lo).max())
    best, spot, cell_at = 0.0, None, None
    if ext <= 0 or len(T) == 0 or len(N) == 0:
        return best, spot
    dist = SurfaceDist(V, T, max(D, ext / 64.0))         # (buckets no finer than the piece needs: exact either way)
    ray = RayParity(V, T, _ray_bucket(V))
    eps = AIM_RES * D
    s_min = 2.0 * eps / SQ3                 # (the last cells: half a diagonal of eps - a centre is within eps of the worst)
    levels = max(0, int(np.ceil(np.log2(ext / (8.0 * s_min))))) if ext > 8.0 * s_min else 0
    s = s_min * 2 ** levels
    org = lo - s * CELL_SHIFT
    n0 = np.ceil((hi - org) / s).astype(np.int64) + 1
    I, J, K = np.meshgrid(*(np.arange(n) for n in n0), indexing="ij")
    cells = np.c_[I.ravel(), J.ravel(), K.ravel()]
    absN1 = np.abs(N).sum(1)
    tiny = 1e-9 * ext
    while len(cells):
        half = 0.5 * s
        r = half * SQ3
        centre = org + (cells + 0.5) * s
        # (cells wholly outside a hull plane are dropped)
        v = centre @ N.T - Dp[None, :]
        keep = ~(v - half * absN1[None, :] > tiny).any(axis=1)
        cells, centre, v = cells[keep], centre[keep], v[keep]
        if len(cells) == 0:
            break
        _t0 = time.time()
        ins = ray.inside(centre)
        _t1 = time.time()
        d = np.full(len(centre), np.inf)
        out = np.nonzero(~ins)[0]
        if len(out):
            d[out] = dist(centre[out])
        if TRACE:
            print(f"      worst_air s {s / D:.3f} D: {len(cells)} cells, {len(out)} out; inside {_t1 - _t0:.3f} "
                  f"dist {time.time() - _t1:.3f} best {best / D:.2f} D")
        # (a centre in the hull and out of the solid is a point of the air: the best of them may be the spot)
        cand = ~ins & (v <= tiny).all(axis=1) & np.isfinite(d)
        if cand.any():
            k = np.argmax(np.where(cand, d, -np.inf))
            if d[k] > best:
                best, spot, cell_at = float(d[k]), centre[k].copy(), (cells[k].copy(), s)
        if s <= s_min:
            break
        # (a cell's points are within its centre's distance plus r of the surface; with the centre inside the
        #  solid, a point outside is within r minus that distance - deeper than r, the cell is all solid)
        bound = d + r
        inside = np.nonzero(ins)[0]
        if len(inside):
            di = dist(centre[inside], cap=r)
            bound[inside] = np.where(np.isfinite(di), r - di, 0.0)
        live = np.nonzero(bound > best + eps)[0]
        if len(live) > AIM_CAP:                                     # (the best-bounded ones; a fixed order)
            live = live[np.lexsort((live, -bound[live]))[:AIM_CAP]]
            live.sort()
        cells = (cells[live][:, None, :] * 2 + CORNERS[None]).reshape(-1, 3)
        s = half
    if cell_at is not None:
        # (polish: the winning cell's children, the best child's children... to a 64th of Fit)
        cell, s = cell_at
        org_c = org
        while s > D / 64.0:
            kids = cell[None, :] * 2 + CORNERS
            s *= 0.5
            centre = org_c + (kids + 0.5) * s
            v = centre @ N.T - Dp[None, :]
            ins = ray.inside(centre)
            d = np.where(~ins & (v <= tiny).all(axis=1), dist(centre), -np.inf)
            k = int(np.argmax(d))
            if d[k] > best:
                best, spot = float(d[k]), centre[k].copy()
            cell = kids[k]
    return best, spot


def _clipped(P, inv, rest, pc, pp, N, Dp, ncells):
    """The cells' corners, clipped by the hull plane each sticks out of most (a cell on a hull face: only its part
    inside the hull counts; with several planes the one is still a sound bound): (n, 20, 3) points, which are
    real. rest: the cells' indices among all ncells; (pc, pp): the active (cell, plane) pairs."""
    n = len(inv)
    Q = np.full((n, 20, 3), np.nan)
    Q[:, :8] = P[inv]
    valid = np.zeros((n, 20), bool)
    valid[:, :8] = True
    if len(pc) == 0:
        return Q, valid
    row = np.full(ncells, -1)
    row[rest] = np.arange(n)
    r = row[pc]
    m = r >= 0
    r, pl = r[m], pp[m]
    if len(r) == 0:
        return Q, valid
    v = np.einsum("ijk,ik->ij", Q[r, :8], N[pl]) - Dp[pl][:, None]          # (corner heights over the plane)
    vmax = v.max(axis=1)
    order = np.lexsort((-vmax, r))
    r, pl, v, vmax = r[order], pl[order], v[order], vmax[order]
    first = np.unique(r, return_index=True)[1]
    r, pl, v, vmax = r[first], pl[first], v[first], vmax[first]
    cut = vmax > 0
    r, v = r[cut], v[cut]
    if len(r) == 0:
        return Q, valid
    valid[r, :8] = v <= 0
    va, vb = v[:, EDGES[:, 0]], v[:, EDGES[:, 1]]
    crosses = (va > 0) != (vb > 0)
    f = va / np.where(va - vb != 0, va - vb, 1.0)
    Pa, Pb = Q[r][:, EDGES[:, 0]], Q[r][:, EDGES[:, 1]]
    X = Pa + (Pb - Pa) * f[:, :, None]
    Q[r, 8:] = np.where(crosses[:, :, None], X, np.nan)
    valid[r, 8:] = crosses
    return Q, valid


def _corner_bound(Q, valid, dc, tc, insc, tb, tbs, D):
    """Per cell (its points Q (n, k, 3) with validity, the 8 corners' distances dc, nearest triangles tc and
    insideness insc): the least, over the corners' nearest triangles, of the farthest point from that triangle -
    every point of the cell is within it of the solid. Nearest-corner's and farthest-corner's triangles first; the
    rest only where needed."""
    n = len(Q)
    ub = np.full(n, np.inf)
    first = np.argmin(dc, axis=1)
    last = np.argmax(np.where(tc >= 0, dc, -np.inf), axis=1)
    tried = np.zeros((n, 8), bool)
    rows = np.arange(n)
    for pick in (first, last):
        t = tc[rows, pick]
        tried[rows, pick] = True
        has = np.nonzero((t >= 0) & (ub > D))[0]
        if len(has) == 0:
            continue
        ub[has] = np.minimum(ub[has], _bound_with(Q[has], valid[has], t[has], insc[has], tb, tbs))
    for col in range(8):
        has = np.nonzero(~tried[:, col] & (tc[:, col] >= 0) & (ub > D))[0]
        if len(has) == 0:
            continue
        ub[has] = np.minimum(ub[has], _bound_with(Q[has], valid[has], tc[has, col], insc[has], tb, tbs))
    return ub


def _bound_with(Q, valid, t, insc, tb, tbs):
    """Farthest of each cell's points from its given triangle - or, where the cell meets the surface only on
    that triangle's plane (no triangle off the plane touches the cell, the corners behind the plane are inside
    the solid), its farthest point in front of the face: a cell straddling a flat face is within Fit as soon as
    its outside part is. A point in front counts only where it projects onto the face itself; a point on or
    behind the plane is in the solid once a corner behind it is (the cell's half behind the plane holds no
    surface, so it is all inside with that corner) - it need not project onto the triangle: on a face of two
    triangles, half the cells' points land on the other one."""
    k = Q.shape[1]
    out = np.empty(len(Q))
    step = max(1, 400_000 // k)
    for s in range(0, len(Q), step):
        Qf = Q[s:s + step].reshape(-1, 3)
        va = valid[s:s + step]
        vf = va.reshape(-1, 1)
        ts = t[s:s + step]
        tt = np.repeat(ts, k)
        A, B, C = tb.A[tt], tb.B[tt], tb.C[tt]
        Qz = np.where(vf, Qf, A)
        X = closest_on_triangles(Qz, A, B, C)
        dist = np.linalg.norm(X - Qz, axis=1).reshape(-1, k)
        far = np.where(va, dist, -np.inf).max(axis=1)
        n = np.cross(B - A, C - A)
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-300)[:, None]
        ht = np.einsum("ij,ij->i", Qz - A, n)                          # (height over the face's plane, out +)
        Y = Qz - ht[:, None] * n
        tol = 1e-9 * (1.0 + np.abs(ht))
        onface = (np.linalg.norm(X - Y, axis=1) <= tol).reshape(-1, k)
        hk = ht.reshape(-1, k)
        hc = hk[:, :8]
        ins = insc[s:s + step]
        behind = ((hc < -tol.reshape(-1, k)[:, :8]) & ins).any(axis=1)         # (a corner inside, behind the plane)
        onplane = hk <= tol.reshape(-1, k)
        flat = (onface | ~va | (onplane & behind[:, None])).all(axis=1) & ((hc >= 0) | ins).all(axis=1)
        if flat.any():
            flat[flat] = _only_plane(Q[s:s + step][flat, :8], ts[flat], tbs)
        front = np.where(va, np.maximum(hk, 0.0), -np.inf).max(axis=1)
        out[s:s + step] = np.where(flat, np.minimum(far, front), far)
    return out


def _coincident(tb, N, Dp):
    """Per triangle of tb: its unit normal, plane offset, and the hull plane (N, Dp) facing most its way (-1
    with no hull planes) -> (n, d, j)."""
    n = np.cross(tb.B - tb.A, tb.C - tb.A)
    ln = np.linalg.norm(n, axis=1)
    n = n / np.maximum(ln, 1e-300)[:, None]
    d = np.einsum("ij,ij->i", n, tb.A)
    j = np.full(len(n), -1, np.int64)
    if len(N):
        for s in range(0, len(n), 20_000):
            j[s:s + 20_000] = np.argmax(n[s:s + 20_000] @ N.T, axis=1)
    j[ln <= 0] = -1
    return n, d, j


def _no_air(Q, valid, insc, tb, coin, N, Dp, D):
    """Per cell (its clipped points Q (n, 20, 3) with validity, the 8 corners' insideness): holds it no air -
    every hull point in it inside the solid, or as good as? True where every triangle touching the cell's box
    lies on a plane behind which the cell is solid (a corner behind all those planes by an eighth of the cell,
    and inside: the cell's part behind them holds no surface, so it is all inside with that corner), and the
    hull keeps within delta in front of each plane across the cell - the hull plane facing its way does (the
    gap at the cell's corners is exact for the cell: at a slightly bent quad it closes as the cells shrink), or
    every clipped point is. A hull point then reaches the solid part within delta x cell diagonal / margin,
    14 delta at most: delta is CERT_TOL x Fit / 64, far inside the tolerance. The cells over the hull's own
    faces and edges pass at the coarsest level; only cells over real air are ever refined."""
    n = len(Q)
    out = np.zeros(n, bool)
    if n == 0:
        return out
    corners = Q[:, :8]
    lo, hi = corners.min(axis=1), corners.max(axis=1)
    c = 0.5 * (lo + hi)
    cnt, st = tb._ranges(c)
    idx, blk = _expand(st.ravel(), cnt.ravel())
    cell = blk // 27
    tri = tb.tri[idx]
    pair = np.unique(cell * len(tb.A) + tri)                     # (a triangle in several buckets: once per cell)
    cell, tri = pair // len(tb.A), pair % len(tb.A)
    touch = np.all((tb.lo[tri] <= hi[cell]) & (tb.hi[tri] >= lo[cell]), axis=1)
    cell, tri = cell[touch], tri[touch]
    if len(cell) == 0:
        return out
    nt, dt, jh = coin
    delta = CERT_TOL * D / 64.0
    margin = 0.125 * (hi - lo).max(axis=1)
    h = np.einsum("ijk,ik->ij", Q[cell], nt[tri]) - dt[tri][:, None]     # (pairs, 20): heights over the plane
    hc = h[:, :8]
    # (corners behind every touching plane by the margin: AND over the cell's pairs)
    behind = _segall(hc <= -margin[cell][:, None], cell, n)
    solid_behind = (behind & insc).any(axis=1)
    # (each plane: the hull's facing plane within delta in front of it over the cell, or every clipped point)
    j = jh[tri]
    has = j >= 0
    gap = np.full(len(cell), np.inf)
    if has.any():
        hj = np.einsum("ijk,ik->ij", corners[cell[has]], N[j[has]]) - Dp[j[has]][:, None]
        gap[has] = (hc[has] - hj).max(axis=1)
    plane_ok = (gap <= delta) | (np.where(valid[cell], h, -np.inf) <= delta).all(axis=1)
    all_ok = _segall(plane_ok[:, None], cell, n)[:, 0]
    touched = np.bincount(cell, minlength=n) > 0
    return touched & solid_behind & all_ok


def _patch_bound(Q, valid, insc, tb, coin, s, eps):
    """Per cell: how far at most its clipped points are from the solid, from the surface patch around it - inf
    where that gives nothing. Every triangle in the cell's bucket neighbourhood B' lies on a plane; with a corner
    inside and behind all those planes by an eighth of the cell, the part of B' behind them holds no surface and
    is solid with that corner. A clipped point in front of some planes is then within reach of its projection
    into that part (dropped onto the plane it is most in front of, up to four times): the distance stands as the
    bound for the point, the farthest point for the cell. A cell straddling a curved or folded surface within
    Fit of the hull certifies at the coarsest level whose neighbourhood holds a corner deep enough inside."""
    n = len(Q)
    out = np.full(n, np.inf)
    if n == 0:
        return out
    corners = Q[:, :8]
    c = 0.5 * (corners.min(axis=1) + corners.max(axis=1))
    cnt, st = tb._ranges(c)
    idx, blk = _expand(st.ravel(), cnt.ravel())
    cell = blk // 27
    tri = tb.tri[idx]
    pair = np.unique(cell * len(tb.A) + tri)
    cell, tri = pair // len(tb.A), pair % len(tb.A)
    if len(cell) == 0:
        return out
    nt, dt, _j = coin
    h = np.einsum("ijk,ik->ij", corners[cell], nt[tri]) - dt[tri][:, None]
    behind = _segall(h <= -0.125 * s, cell, n)
    good = (behind & insc).any(axis=1) & (np.bincount(cell, minlength=n) > 0)
    if not good.any():
        return out
    keep = good[cell]                                   # (only the cells that can pass go through the projections)
    cell, tri = cell[keep], tri[keep]
    k = Q.shape[1]
    q = np.where(valid[:, :, None], Q, 0.0)
    npair = len(cell)
    # ((point, cell) segments, point-major: the pairs are sorted by cell, so each point's run is sorted too)
    seg = (np.arange(k)[:, None] * n + cell[None, :]).ravel()
    va = valid[cell]
    nt_tri, dt_tri = nt[tri], dt[tri]

    def heights():
        hq = np.einsum("ijk,ik->ij", q[cell], nt_tri) - dt_tri[:, None]
        return np.where(va, hq, -np.inf).T.ravel()                       # (k x npair, point-major)
    for _ in range(4):
        hq = heights()
        hmax = _segmax(hq, seg, n * k)
        over = hmax > eps
        if not over.any():
            break
        is_max = (hq >= hmax[seg]) & over[seg]
        sel = np.full(n * k, -1, np.int64)
        sel[seg[is_max]] = np.nonzero(is_max)[0] % npair              # (one pair per point: whichever wrote last)
        pts = np.nonzero(sel >= 0)[0]
        pr = sel[pts]
        q[pts % n, pts // n] -= hmax[pts][:, None] * nt_tri[pr]
    hmax = _segmax(heights(), seg, n * k).reshape(k, n).T
    # (the projection must have stayed in the neighbourhood B', where the planes are known to hold the surface)
    cidx = np.floor((c - tb.org) / tb.b)
    blo = tb.org + (cidx - 1) * tb.b - eps
    bhi = tb.org + (cidx + 2) * tb.b + eps
    inb = np.all((q >= blo[:, None, :]) & (q <= bhi[:, None, :]), axis=2)
    ok = (hmax <= eps) & inb
    dq = np.linalg.norm(q - np.where(valid[:, :, None], Q, 0.0), axis=2)
    dq = np.where(valid, np.where(ok, dq, np.inf), -np.inf)
    bound = dq.max(axis=1)
    out[good] = bound[good]
    return out


def _segall(m, seg, n):
    """Per segment id (seg ascending) the AND over its rows of m (rows, c); True for empty ones -> (n, c)."""
    cnt = np.bincount(seg, minlength=n)
    starts = np.cumsum(cnt) - cnt
    out = np.ones((n, m.shape[1]), bool)
    has = cnt > 0
    if has.any():
        out[has] = np.logical_and.reduceat(m, starts[has], axis=0)
    return out


def _segmax(val, seg, n):
    """Per segment id (seg ascending) the greatest value; -inf for empty ones."""
    cnt = np.bincount(seg, minlength=n)
    starts = np.cumsum(cnt) - cnt
    out = np.full(n, -np.inf)
    has = cnt > 0
    if has.any():
        out[has] = np.maximum.reduceat(val, starts[has])
    return out


def _only_plane(corners, t, tb):
    """Per cell (its 8 corners, a triangle): does no triangle off that triangle's plane touch the cell's box?
    (tb's buckets at least half the cell: its neighbourhood then holds every triangle touching the cell)"""
    lo, hi = corners.min(axis=1), corners.max(axis=1)
    c = 0.5 * (lo + hi)
    cnt, st = tb._ranges(c)
    idx, blk = _expand(st.ravel(), cnt.ravel())
    cell = blk // 27
    tri = tb.tri[idx]
    touch = np.all((tb.lo[tri] <= hi[cell]) & (tb.hi[tri] >= lo[cell]), axis=1)
    cell, tri = cell[touch], tri[touch]
    A0, B0, C0 = tb.A[t[cell]], tb.B[t[cell]], tb.C[t[cell]]
    n0 = np.cross(B0 - A0, C0 - A0)
    n0 /= np.maximum(np.linalg.norm(n0, axis=1), 1e-300)[:, None]
    scale = 1e-9 * (1.0 + np.abs(A0).max(axis=1))
    heights = np.c_[np.einsum("ij,ij->i", tb.A[tri] - A0, n0), np.einsum("ij,ij->i", tb.B[tri] - A0, n0),
                    np.einsum("ij,ij->i", tb.C[tri] - A0, n0)]
    coplanar = np.all(np.abs(heights) <= scale[:, None], axis=1)
    return np.bincount(cell[~coplanar], minlength=len(t)) == 0


# ------------------------------------------------------------------ pieces

class Piece:
    """A solid, its hull and (with AIM_VOXELS) its voxels (a crop of the model grid: `sub`, grid index of its
    corner `a`). The voxels and the hull's planes are worked out only when asked for: a cut is tried ~100 ways per
    step and almost none of those parts are ever looked at again (measured: 2-2.5x faster, the same pieces).
    Whether it is within Fit (done) is exact, from its triangles; a piece found within Fit never needs its voxels."""

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
        self._ok = self._wit = None
        self._worst = None                  # (worst_air's answer, found by done: far reuses it)
        self._pending = None                # (done's answer on its way from a worker process: see judge_later)
        self._faces = self._snapfaces = None    # (its face planes, for candidates and snapped)
        self._pre = None                    # (candidates scored in the workers while it was judged: see prescore)
        self.uid = next(_UIDS)
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

    def done(self):
        """Exact (to CERT_TOL of Fit): no point of the hull is farther than Fit from the solid."""
        if self._ok is None:
            if self.air <= 1e-9 * max(self.vol, 1e-12):
                # (no air: the hull IS the solid, so every hull point is in it - within Fit, nothing to measure. A
                #  boxy prop's peel search asks this of dozens of exactly convex parts per step)
                self._ok, self._wit = True, (None, 0.0)
                return self._ok
            got = None
            if self._pending is not None:           # (judged in a worker process meanwhile: its answer)
                try:
                    got = self._pending.result()
                except Exception as e:              # (a worker died, or the pool was shut: judged here instead)
                    print(f"General: a worker's answer was lost ({e!r}); judging the piece here")
                self._pending = None
            if got is None:
                Vh, Th = arrays(self.hull)
                got = judge(self.V, self.T, Vh, Th, self.air, self.vol, G["D"])
            self._ok, self._wit, self._worst = got
        return self._ok

    def judge_later(self, ex):
        """Have a worker process judge the piece (done, and its worst air) now, while the main process goes on
        cutting: a cut's parts are judged side by side instead of one after another when each is popped."""
        if self._ok is None and self._pending is None and self.air > 1e-9 * max(self.vol, 1e-12) and \
                len(self.T) >= SCORER_FROM and not AIM_VOXELS:
            Vh, Th = arrays(self.hull)
            try:
                with _bf._NoMain():
                    self._pending = ex.submit(_bf._judge_task, (self.V, self.T, Vh, Th, self.air, self.vol, G["D"]))
            except Exception:
                self._pending = None

    def far(self):
        """(largest distance from the hull's air to the solid, where it is); (0, None) when within Fit. The spot is
        the worst air the search (worst_air) finds - or, with AIM_VOXELS, the worst voxel of the piece's air -
        (coarse: a cut aimed at it needs only roughly the right place), or the certification's witness where
        nothing was found."""
        if self._far is None:
            if self.done():
                self._far = (0.0, None)
                return self._far
            h, D = G["h"], G["D"]
            wit, dw = self._wit
            far = (max(dw, D * (1 + 1e-9)), wit)
            if not AIM_VOXELS:
                best, spot = self._worst if self._worst is not None else worst_air(self, D)
                self._far = (best, spot) if spot is not None else far      # (nothing found: the witness)
                return self._far
            if self.sub.any():
                dist = distance_to(self.sub, h)
                cand = np.argwhere(dist > max(D - h, 0.0))       # (air the voxels see as about Fit or beyond)
                if len(cand):
                    P = (G["lo"] + (cand + self.a) * h).astype(np.float32)
                    inh = self.in_hull(P)
                    if inh.any():
                        dc = dist[tuple(cand[inh].T)]
                        k = int(np.argmax(dc))
                        far = (max(far[0], float(dc[k])), P[inh][k])
            self._far = far
        return self._far


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


def _face_planes(p):
    """The piece's face planes grouped (to a thousandth in direction, half a unit in offset): normals, offsets,
    their areas, and the hull's corners; kept on the piece."""
    if p._faces is None:
        V, T = p.V, p.T
        n_t, area = tri_normals(V, T)
        ok = area > 1e-9
        n_t = n_t[ok]
        d_t = np.einsum("ij,ij->i", n_t, V[T[ok, 0]])
        q = np.c_[np.round(n_t * 1000).astype(np.int64), np.round(d_t / 0.5).astype(np.int64)]
        _u, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
        Ng, Dg = n_t[first], d_t[first]
        Ag = np.bincount(inv.ravel(), weights=area[ok], minlength=len(first))
        Vh = arrays(p.hull)[0]
        if len(Vh) == 0:
            Vh = V
        p._faces = (Ng, Dg, Ag, Vh)
    return p._faces


def _through_first(Vh, Ng, Dg, order, want):
    """The first `want` planes of `order` that really cut the piece: a plane's reach over the solid is that over
    its hull's corners. Tested only as far as the picks go, in chunks: all vertices x all planes was 7.7 GB on a
    26k-face body."""
    got = []
    for s0 in range(0, len(order), 256):
        ks = order[s0:s0 + 256]
        S = Vh @ Ng[ks].T - Dg[ks]
        for k in ks[(S.max(0) > 0.5) & (S.min(0) < -0.5)]:
            got.append(int(k))
            if len(got) >= want:
                return got
    return got


def _plain_cuts(p):
    """Plain cuts across the piece: a quarter, half and three quarters along each axis."""
    V = p.V
    lo, hi = V.min(0), V.max(0)
    out = []
    for ax in range(3):
        e = np.zeros(3)
        e[ax] = 1.0
        for f in (0.25, 0.5, 0.75):
            out.append(("blind", e, float(lo[ax] + f * (hi[ax] - lo[ax])), False))
    return out


def blind_candidates(p):
    """The candidates that don't depend on where the piece's worst air is - its biggest faces, the plain cuts,
    and their snaps: scored in the workers while the piece is still being judged (prescore). The same planes,
    by value, as candidates() lists them."""
    Ng, Dg, Ag, Vh = _face_planes(p)
    out = [("face", Ng[k], float(Dg[k]), True) for k in _through_first(Vh, Ng, Dg, np.argsort(-Ag, kind="stable"), 12)]
    out += _plain_cuts(p)
    return out + snapped(p, out)


def candidates(p):
    """[(kind, normal, offset, may_peel)]"""
    D = G["D"]
    _far, spot = p.far()
    out = []
    V = p.V
    if spot is not None:
        Ng, Dg, Ag, Vh = _face_planes(p)
        gap = np.abs(Ng @ spot - Dg)
        picked = _through_first(Vh, Ng, Dg, np.argsort(gap, kind="stable"), 10)     # (the faces nearest the worst air...)
        picked += [k for k in _through_first(Vh, Ng, Dg, np.argsort(-Ag, kind="stable"), 12)
                   if k not in picked]                                              # (...the biggest: peels)
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
    out += _plain_cuts(p)
    return out + snapped(p, out)


def snapped(p, cuts):
    """Each cut nearly along one of the piece's own faces (within 2 degrees, and near it) also tried exactly on that
    face: a cut a little off the underside of a beam leaves a thin slice of the beam on the post below, and the
    post's hull grows a slanted wedge. On the face, the post stays a box."""
    if p._snapfaces is None:
        V, T = p.V, p.T
        n_t, area = tri_normals(V, T)
        ok = area > 1e-9
        n_t = n_t[ok]
        d_t = np.einsum("ij,ij->i", n_t, V[T[ok, 0]])
        q = np.c_[np.round(n_t * 1000).astype(np.int64), np.round(d_t / 0.05).astype(np.int64)]
        _u, first = np.unique(q, axis=0, return_index=True)
        p._snapfaces = (n_t[first], d_t[first])
    Nf, Df = p._snapfaces
    if len(Nf) == 0:
        return []
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
                # (the air-free parts, found again among the real parts: the scores' order isn't theirs)
                if parts is not None and all(q.done() for q in parts if q.air <= 0.005 * q.vol + 1e-6):
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


def _plane_key(c):
    _kind, n, d, _pk = c
    return (float(n[0]), float(n[1]), float(n[2]), float(d))


def _submit(p, cands, ex):
    """The candidates' scoring handed to the workers: -> (cands, chunks, futures). A few batches per worker: a
    worker still judging a part (judge_later) or slowed by a kernel cut takes fewer; the mesh goes over with
    each batch (1.6 MB on a 45k-triangle body, 2 ms) but its scorer is built once per worker (brushfit._LAST)."""
    n_chunks = min(len(cands), 3 * _bf.WORKERS)
    chunks = [list(range(k, len(cands), n_chunks)) for k in range(n_chunks)]
    with _bf._NoMain():
        futs = [ex.submit(_bf._organic_task, (p.V, p.T, [(cands[i][1], cands[i][2]) for i in ch], G["D"], SLIVER,
                                              p.vol, len(p._parts) > 1, len(p.T) >= SCORER_FROM, p.uid))
                for ch in chunks]
    return cands, chunks, futs


def _collect(sub):
    """The workers' answers: {plane key: info}."""
    cands, chunks, futs = sub
    out = {}
    for ch, res in zip(chunks, [f.result() for f in futs]):
        for i, info in zip(ch, res):
            out[_plane_key(cands[i])] = info
    return out


def prescore(p):
    """While a popped piece is still being judged in a worker (judge_later), the candidates that don't need its
    worst air (blind_candidates) are scored in the other workers: _scores finds them done. Only for a piece with
    air to spare (WORST_FIRST, as judge): one likely to be cut."""
    ex = _bf.active_pool()
    if ex is None or p._pending is None or p._ok is not None or p._pre is not None or len(p.T) < SCORER_FROM or \
            _bf.WORKERS < 2 or p.air <= WORST_FIRST * G["D"] * p.hull.surface_area():
        return
    try:
        p._pre = _submit(p, blind_candidates(p), ex)
    except Exception:
        p._pre = None


def _scores(p, cands):
    """Every candidate cut's parts as [(volume, air)], scored without the kernel (brushfit.CutScorer: the chosen
    cut is then really made, by parts_of) - in worker processes once cutting has taken a while, else here.
    -> [(info, parts or None)]"""
    ex = None
    composed = len(p._parts) > 1
    scorer = len(p.T) >= SCORER_FROM
    work = len(p.T) * len(cands)        # (triangles x cuts: a 40k-triangle piece is worth it from the first cut)
    if _bf.WORKERS > 1 and len(cands) >= 16 and not _bf._BROKEN[0] and             (work > POOL_WORK or G.get("cut_time", 0.0) > POOL_AFTER):
        ex = _bf.pool(_bf.WORKERS)
    if ex is not None:
        from concurrent.futures.process import BrokenProcessPool
        try:
            known = _collect(p._pre) if p._pre is not None else {}      # (scored while the piece was judged)
            p._pre = None
            todo = [i for i, c in enumerate(cands) if _plane_key(c) not in known]
            got = _collect(_submit(p, [cands[i] for i in todo], ex)) if todo else {}
            out = [None] * len(cands)
            for i, c in enumerate(cands):
                k = _plane_key(c)
                out[i] = (known[k] if k in known else got[k], None)
            return out
        except BrokenProcessPool as e:
            print(f"General: worker processes failed ({e}); carrying on in one process")
            _bf._BROKEN[0] = True
            _bf.shutdown_pool()
    t = time.time()
    out = []
    sc = _bf.CutScorer(p.V, p.T, composed) if scorer else None
    for _kind, n, d, _pk in cands:
        if time.time() > G.get("deadline", float("inf")):       # (out of time: the rest aren't tried)
            out.append((None, None))
            continue
        if sc is not None and sc.ok:
            got = sc.score(n, d, G["D"], SLIVER, p.vol)
            if got is not _bf.KERNEL:
                out.append((got, None))
                continue
        parts = split(p, n, d)                                   # (small, along a face, or not closed: the kernel)
        # (the parts are kept for parts_of only while they are small: hundreds of cuts of a 45k-triangle body
        #  kept would be gigabytes; the chosen one is cut again)
        out.append((None if parts is None else [(q.vol, q.air) for q in parts], parts if work <= POOL_WORK else None))
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
    tried = set()                   # (pairs found not to join: the same pair isn't tested again from the other side)
    apart = set()                   # (pairs found not to touch: a piece never changes, so that stands too)
    while changed and time.time() < deadline:
        changed = False
        order = sorted(range(len(done)), key=lambda i: done[i].vol)
        lo = np.array([p.V.min(0) for p in done])
        hi = np.array([p.V.max(0) for p in done])
        for i in order:
            if time.time() > deadline:
                break
            # (only pairs whose boxes come within reach are measured in the kernel: 35 finished pieces of a statue
            #  are 600 pairs, and the kernel's gap took most of the pass)
            gap = np.maximum(np.maximum(lo - hi[i], lo[i] - hi), 0.0).max(axis=1)
            for j in np.nonzero(gap <= 0.25)[0]:
                if i == j:
                    continue
                A_, B_ = done[i], done[j]
                pair = (min(A_.uid, B_.uid), max(A_.uid, B_.uid))
                if pair in tried or pair in apart:
                    continue
                if A_.m.min_gap(B_.m, 0.5) > 0.25:
                    apart.add(pair)
                    continue
                tried.add(pair)
                # (the joined piece's air from the hulls alone: its hull is the hull of theirs and its volume their
                #  sum - they don't overlap. The boolean join is made only for a pair that adds little enough)
                vol = A_.vol + B_.vol
                if Manifold.batch_hull([A_.hull, B_.hull]).volume() - vol - A_.air - B_.air >= max(D ** 3, 0.01 * vol):
                    continue
                m = A_.m + B_.m
                q = Piece(m, source=lambda A_=A_, B_=B_: union_mask(A_, B_), check=False)
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
    k = max_sides - 6                # (the box it starts from can keep up to 6 sides of its own)
    if k <= 0:
        lo, hi = Vh.min(0), Vh.max(0)
        return Manifold.cube((hi - lo).tolist()).translate(lo.tolist())
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
    ext = float((hi - lo).max())
    # (a plane through the hull's own corners can cut a hair inside it in the kernel - 0.005% of a statue's piece:
    #  that is rounding, kept. Short by more, the planes go out a little and it is tried again; the last resort is
    #  the hull itself, never its box - the box once stood 4.5x the piece in a statue's chest)
    for margin in (0.0, 1e-5, 1e-3):
        out = Manifold.cube((hi - lo).tolist()).translate(lo.tolist())
        for c, d in zip(C, Dp):
            out = out.trim_by_plane((-c).tolist(), float(-(d + margin * ext)))
        if not out.is_empty() and out.volume() >= hull.volume() * (1 - 1e-3):
            return out
    return hull


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
                cand = Piece(m, source=lambda p=p, q=done[j]: union_mask(p, q), check=False)
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
    if _bf.WORKERS > 1 and len(T) >= SCORER_FROM and not _bf._BROKEN[0]:
        _bf.warm_pool(_bf.WORKERS)      # (the workers spawn while the first piece is judged here)
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
            if budget >= 2:
                prescore(p)             # (its blind candidates are scored while its judgement is on the way)
            if budget < 2 or p.done():
                done.append(p)
                continue
            best = best_cut(p, budget)
            if best is None:
                done.append(p)
                continue
            cuts += 1
            ex = _bf.active_pool()
            for q in best[1]:
                heapq.heappush(heap, (-q.air, uid, q))
                uid += 1
                if ex is not None:
                    # (the parts are judged in the workers at once, side by side - each will be popped and asked;
                    #  measured on a statue, judging the pieces one by one here was a third of the conversion)
                    q.judge_later(ex)
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
