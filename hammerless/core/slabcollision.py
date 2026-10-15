"""Collision brushes for a mesh (an archviz house: single-sided, unclosed surfaces), one thin slab per flat region.

1. Flat regions: triangles grouped by plane (normal and offset, rounded) and split into pieces that share edges
   (vertices welded by position first). Regions smaller than min_area are left out (trim, details).
2. Each region is cut into a few convex pieces in 2D on its plane (Hertel-Mehlhorn: its own triangles merged across
   shared edges, longest first, while the merged polygon stays convex).
3. Each piece becomes a brush: the polygon pushed out by `overlap` on every edge (so neighbouring slabs overlap at
   corners; a sharp corner is cut off flat instead of spiking out), extruded `thickness` BEHIND the surface (the
   side its triangles face away from), so floors and walls are exactly where they are drawn and doorways keep
   their width.
4. Identical brushes and slivers are dropped.

Everything is in the mesh's own units; `inch` says how long an inch is in them.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np


@dataclass
class SlabResult:
    brushes: list = field(default_factory=list)   # (verts (2m, 3): the back face then the front face, m, region)
    skipped: list = field(default_factory=list)   # triangle indices left out (small regions)
    regions: int = 0
    kept_regions: int = 0
    kept_share: float = 0.0                        # share of the surface area in kept regions
    skipped_area: float = 0.0


def _weld(co, tri, tol):
    key = np.round(co / tol).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    out = np.zeros((len(uniq), 3))
    out[inv] = co
    return out, inv[tri]


def _basis(n):
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def _convex(loop, P) -> bool:
    m = len(loop)
    sgn = 0
    for i in range(m):
        a, b, c = P[loop[i - 1]], P[loop[i]], P[loop[(i + 1) % m]]
        z = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(z) < 1e-12:
            continue
        s = 1 if z > 0 else -1
        if sgn == 0:
            sgn = s
        elif s != sgn:
            return False
    return True


def _rotated(L, x, y):
    """L turned so it starts at y and ends at x, when it holds the edge x -> y; else None."""
    m = len(L)
    for i in range(m):
        if L[i] == x and L[(i + 1) % m] == y:
            return L[i + 1:] + L[:i + 1]
    return None


def convex_pieces(tris_2d: dict, P: dict) -> list:
    """Hertel-Mehlhorn: {id: [a, b, c] (counter-clockwise)} and {vertex: (x, y)} -> convex polygons (vertex lists)."""
    loops = dict(tris_2d)
    par = {t: t for t in loops}

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    em = defaultdict(list)
    for t, lp in loops.items():
        for i in range(3):
            a, b = lp[i], lp[(i + 1) % 3]
            em[(min(a, b), max(a, b))].append(t)
    shared = [(e, ts) for e, ts in em.items() if len(ts) == 2]
    shared.sort(key=lambda it: -math.dist(P[it[0][0]], P[it[0][1]]))
    for (a, b), (t1, t2) in shared:
        r1, r2 = find(t1), find(t2)
        if r1 == r2:
            continue
        p, q = _rotated(loops[r1], a, b), _rotated(loops[r2], b, a)
        if p is None or q is None:
            p, q = _rotated(loops[r1], b, a), _rotated(loops[r2], a, b)
            if p is None or q is None:
                continue
        merged = p + q[1:-1]
        if len(set(merged)) == len(merged) and _convex(merged, P):
            par[r2] = r1
            loops[r1] = merged
            del loops[r2]
    return [L for r, L in loops.items() if find(r) == r]


def _drop_collinear(pts):
    keep = []
    m = len(pts)
    for i in range(m):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % m]
        z = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(z) > 1e-6 * max(1e-12, np.linalg.norm(b - a) * np.linalg.norm(c - b)):
            keep.append(b)
    return np.array(keep)


def offset_convex(pts, h):
    """Push every edge of a counter-clockwise convex polygon out by h. A corner whose new point would land more
    than 3h away is cut off flat (two points) so a sharp corner can't spike out."""
    m = len(pts)
    out = []
    for i in range(m):
        prev, cur, nxt = pts[i - 1], pts[i], pts[(i + 1) % m]
        d1, d2 = cur - prev, nxt - cur
        l1, l2 = np.linalg.norm(d1), np.linalg.norm(d2)
        if l1 < 1e-12 or l2 < 1e-12:
            continue
        n1 = np.array([d1[1], -d1[0]]) / l1
        n2 = np.array([d2[1], -d2[0]]) / l2
        bis = n1 + n2
        bl = np.linalg.norm(bis)
        if bl > 1e-9 and 2.0 / bl <= 3.0:            # (h / cos(half turn) <= 3h)
            out.append(cur + bis / bl * (2.0 * h / bl))
        else:
            out.append(cur + n1 * h)
            out.append(cur + n2 * h)
    return np.array(out)


def _area2d(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def slab_brushes(co, tri, inch: float, min_area: float, thickness_in: float = 1.0,
                 overlap_in: float = 0.5) -> SlabResult:
    """co (n, 3), tri (m, 3) -> SlabResult. min_area in the mesh's units squared."""
    co = np.asarray(co, np.float64)
    tri = np.asarray(tri, np.int64)
    res = SlabResult()
    if not len(tri):
        return res
    co, tri = _weld(co, tri, 0.01 * inch)
    A, B, C = co[tri[:, 0]], co[tri[:, 1]], co[tri[:, 2]]
    cr = np.cross(B - A, C - A)
    area = np.linalg.norm(cr, axis=1) / 2
    ok = np.flatnonzero(area > 1e-12)
    nrm = np.zeros_like(cr)
    nrm[ok] = cr[ok] / (2 * area[ok])[:, None]
    cent = (A + B + C) / 3
    # planes: normal rounded (sign made canonical), offset rounded to a quarter inch
    nq = np.round(nrm / 0.02) * 0.02
    nq /= np.maximum(np.linalg.norm(nq, axis=1), 1e-12)[:, None]
    flip = (nq[:, 0] < -1e-6) | ((np.abs(nq[:, 0]) <= 1e-6) & ((nq[:, 1] < -1e-6) |
                                                                  ((np.abs(nq[:, 1]) <= 1e-6) & (nq[:, 2] < 0))))
    nq[flip] *= -1
    dq = np.round((nq * cent).sum(1) / (0.25 * inch)) * (0.25 * inch)
    gid = np.full(len(tri), -1, np.int64)
    keys: dict = {}
    for i in ok:
        k = (round(nq[i, 0], 3), round(nq[i, 1], 3), round(nq[i, 2], 3), round(float(dq[i]) / inch, 2))
        gid[i] = keys.setdefault(k, len(keys))
    # regions: same plane and sharing an edge
    parent = np.arange(len(tri))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    edge_tris = defaultdict(list)
    for t in ok:
        a, b, c = tri[t]
        for e in ((a, b), (b, c), (c, a)):
            edge_tris[(min(e), max(e))].append(t)
    for ts in edge_tris.values():
        for t in ts[1:]:
            if gid[t] == gid[ts[0]]:
                ra, rb = find(ts[0]), find(t)
                if ra != rb:
                    parent[ra] = rb
    groups = defaultdict(list)
    for t in ok:
        groups[find(t)].append(int(t))
    regions = list(groups.values())
    r_area = np.array([area[r].sum() for r in regions])
    keep = r_area >= min_area
    res.regions, res.kept_regions = len(regions), int(keep.sum())
    total = float(r_area.sum()) or 1.0
    res.kept_share = float(r_area[keep].sum()) / total
    res.skipped_area = float(r_area[~keep].sum())
    res.skipped = [t for r, k in zip(regions, keep) if not k for t in r]

    thick, over = thickness_in * inch, overlap_in * inch
    seen = set()
    for ri in np.flatnonzero(keep):
        ts = regions[ri]
        n = nq[ts[0]]
        d = float((cent[ts] @ n * area[ts]).sum() / area[ts].sum())   # (the real plane, not the rounded key)
        facing = float((nrm[ts] @ n * area[ts]).sum())
        lo, hi = (-thick, 0.0) if facing >= 0 else (0.0, thick)     # behind the surface
        u, v = _basis(n)
        verts = sorted({int(x) for t in ts for x in tri[t]})
        P = {vi: (float(co[vi] @ u), float(co[vi] @ v)) for vi in verts}
        tris2 = {}
        seen_t = set()
        for t in ts:
            a, b, c = (int(x) for x in tri[t])
            fs = frozenset((a, b, c))
            if fs in seen_t:
                continue
            seen_t.add(fs)
            pa, pb, pc = P[a], P[b], P[c]
            z = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
            if abs(z) < 1e-14:
                continue
            tris2[t] = [a, b, c] if z > 0 else [a, c, b]
        for loop in convex_pieces(tris2, P):
            poly = _drop_collinear(np.array([P[x] for x in loop]))
            if len(poly) < 3:
                continue
            if _area2d(poly) < 0:
                poly = poly[::-1]
            if _area2d(poly) < (0.5 * inch) ** 2:                 # a sliver
                continue
            poly = offset_convex(poly, over)
            if len(poly) < 3:
                continue
            base = np.outer(poly[:, 0], u) + np.outer(poly[:, 1], v) + n * d
            vs = np.concatenate([base + n * lo, base + n * hi])
            k = tuple(sorted(map(tuple, np.round(vs / (0.05 * inch)).astype(np.int64))))
            if k in seen:
                continue
            seen.add(k)
            res.brushes.append((vs, len(poly), int(ri)))
    return res


def brush_faces(m: int, start: int = 0) -> list:
    """Faces of a brush from slab_brushes (2m verts: back face, then front face), wound outward."""
    faces = [tuple(start + i for i in range(m))[::-1], tuple(start + m + i for i in range(m))]
    for i in range(m):
        j = (i + 1) % m
        faces.append((start + i, start + j, start + m + j, start + m + i))
    return faces
