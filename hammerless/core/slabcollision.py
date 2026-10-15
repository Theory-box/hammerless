"""Collision brushes for a mesh (an archviz house: single-sided, unclosed surfaces), one thin slab per flat region.

1. Flat regions: triangles grouped by plane (normal and offset, rounded) and split into pieces that share edges
   (vertices welded by position first). Regions smaller than min_area are left out (trim, details).
2. Each region is cut into a few convex pieces in 2D on its plane (Hertel-Mehlhorn: its own triangles merged across
   shared edges, longest first, while the merged polygon stays convex).
3. Each piece becomes a brush: the polygon extruded `thickness` BEHIND the surface (the side its triangles face
   away from), so floors and walls are exactly where they are drawn and doorways keep their width. Behind the
   surface, neighbouring slabs already meet at corners (no edge push: pushing every edge out made lips stick past
   each other at every corner); `overlap` can still push edges out (sharp corners cut off flat, no spikes).
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
    paired_walls: int = 0                          # two-sided walls made one brush each
    thin: int = 0                                  # strips narrower than THIN_IN left out


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


COS_TOL = math.cos(math.radians(1.0))       # faces within this angle are one plane
MAX_WALL_IN = 12.0                          # a wall's two faces at most this far apart (inches) make one brush
GAP_IN = 6.0                                # gaps narrower than this in a surface are filled (nobody fits through)
THIN_IN = 2.0                               # areas narrower than this get no brush (trim edges, bevels)
RECT_DEG = 1.5                              # outline edges this close to a wall's own directions count as square


def _wall_basis(n):
    """Directions along a plane that its walls and openings line up with: along the ground and up the wall for a
    wall or roof (an angled wall too), x and y for a floor. u x v = n (so counter-clockwise faces n)."""
    n = np.asarray(n, float)
    if abs(n[2]) > 0.9:
        u = np.cross([0.0, 1.0, 0.0], n)
    else:
        u = np.cross([0.0, 0.0, 1.0], n)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def _rectilinear(ts, tri, co, u, v, short) -> bool:
    """Whether a region's outline only runs along u or v: within RECT_DEG of them (a wall a hair off true still
    counts), edges shorter than `short` (bevels, chamfers) aside."""
    count = defaultdict(int)
    for t in ts:
        a, b, c = (int(x) for x in tri[t])
        for e in ((a, b), (b, c), (c, a)):
            count[(min(e), max(e))] += 1
    slope = math.tan(math.radians(RECT_DEG))
    for (a, b), k in count.items():
        if k != 1:
            continue
        d = co[b] - co[a]
        du, dv = abs(float(d @ u)), abs(float(d @ v))
        if max(du, dv) < short:
            continue
        if min(du, dv) > slope * max(du, dv):
            return False
    return True


def _bbox_overlap(ta, tb) -> float:
    la, ha = ta.reshape(-1, 2).min(0), ta.reshape(-1, 2).max(0)
    lb, hb = tb.reshape(-1, 2).min(0), tb.reshape(-1, 2).max(0)
    w = np.minimum(ha, hb) - np.maximum(la, lb)
    return float(max(0.0, w[0]) * max(0.0, w[1]))


def _grid_lines(tuv, q):
    """Grid lines at every corner position of the triangles (rounded to q), along u and along v."""
    p = tuv.reshape(-1, 2)
    U = np.unique(np.round(p[:, 0] / q)) * q
    V = np.unique(np.round(p[:, 1] / q)) * q
    return U, V


def _fill(tuv, U, V):
    """Grid cells (between the lines) whose centre lies in one of the triangles."""
    mask = np.zeros((max(0, len(U) - 1), max(0, len(V) - 1)), bool)
    if not mask.size:
        return mask
    cu = (U[:-1] + U[1:]) / 2
    cv = (V[:-1] + V[1:]) / 2
    for t in tuv:
        lo, hi = t.min(0), t.max(0)
        i0, i1 = np.searchsorted(cu, lo[0]), np.searchsorted(cu, hi[0], side="right")
        j0, j1 = np.searchsorted(cv, lo[1]), np.searchsorted(cv, hi[1], side="right")
        if i0 >= i1 or j0 >= j1:
            continue
        X, Y = np.meshgrid(cu[i0:i1], cv[j0:j1], indexing="ij")
        a, b, c = t
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-18:
            continue
        l1 = ((b[1] - c[1]) * (X - c[0]) + (c[0] - b[0]) * (Y - c[1])) / den
        l2 = ((c[1] - a[1]) * (X - c[0]) + (a[0] - c[0]) * (Y - c[1])) / den
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (1 - l1 - l2 >= -1e-9)
        mask[i0:i1, j0:j1] |= inside
    return mask


def _width(poly) -> float:
    """A convex polygon's smallest width (over its edge directions)."""
    best = float("inf")
    m = len(poly)
    for i in range(m):
        e = poly[(i + 1) % m] - poly[i]
        ln = np.linalg.norm(e)
        if ln < 1e-12:
            continue
        nrm2 = np.array([-e[1], e[0]]) / ln
        proj = poly @ nrm2
        best = min(best, float(proj.max() - proj.min()))
    return best


def _close_gaps(mask, U, V, gap):
    """Empty runs narrower than gap with filled cells on both sides (along u, then along v) are filled: a railing's
    balusters or deck boards become one surface (nobody fits through, and it's one brush instead of dozens)."""
    m = mask.copy()
    for axis, lines in ((0, U), (1, V)):
        mm = m if axis == 0 else m.T
        widths = np.diff(lines)
        for row in mm.T if axis == 0 else mm:
            pass
        out = mm.copy()
        k_len = mm.shape[0]
        for col in range(mm.shape[1]):
            line = mm[:, col]
            filled = np.flatnonzero(line)
            for a, b in zip(filled[:-1], filled[1:]):
                if b - a > 1 and float(lines[b] - lines[a + 1]) < gap:
                    out[a + 1:b, col] = True
        m = out if axis == 0 else out.T
    return m


def _cover(mask, U, V) -> list:
    """Rectangles (u0, u1, v0, v1) covering every filled cell, allowed to overlap: each grows from the first cell
    not yet covered, as far as filled cells go, trying both directions first and keeping the bigger."""
    if not mask.any():
        return []
    # identical neighbouring rows / columns merge (fewer cells to walk)
    rows = [0] + [i for i in range(1, mask.shape[0]) if (mask[i] != mask[i - 1]).any()]
    m1 = mask[rows]
    cols = [0] + [j for j in range(1, m1.shape[1]) if (m1[:, j] != m1[:, j - 1]).any()]
    m = m1[:, cols]
    ue = np.append(U[rows], U[-1])
    ve = np.append(V[cols], V[-1])
    H, W = m.shape
    covered = np.zeros_like(m)
    out = []
    for i, j in np.argwhere(m):
        if covered[i, j]:
            continue
        best = None
        for first in ("u", "v"):
            if first == "u":
                i1 = i + 1
                while i1 < H and m[i1, j]:
                    i1 += 1
                j1 = j + 1
                while j1 < W and m[i:i1, j1].all():
                    j1 += 1
            else:
                j1 = j + 1
                while j1 < W and m[i, j1]:
                    j1 += 1
                i1 = i + 1
                while i1 < H and m[i1, j:j1].all():
                    i1 += 1
            a = (ue[i1] - ue[i]) * (ve[j1] - ve[j])
            if best is None or a > best[0]:
                best = (a, i1, j1)
        _a, i1, j1 = best
        covered[i:i1, j:j1] = True
        out.append((ue[i], ue[i1], ve[j], ve[j1]))
    return out


def _brush_planes(vs, m):
    faces = brush_faces(m)
    out = []
    centre = vs.mean(0)
    for f in faces:
        p = vs[list(f)]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        ln = np.linalg.norm(n)
        if ln < 1e-12:
            continue
        n /= ln
        if n @ (p[0] - centre) < 0:
            n = -n
        out.append((n, float(n @ p[0])))
    return out


def _drop_contained(brushes, eps):
    """Identical brushes once; a brush whose corners all lie inside another brush goes."""
    keep, seen = [], set()
    for b in brushes:
        k = tuple(sorted(map(tuple, np.round(b[0] / eps).astype(np.int64))))
        if k not in seen:
            seen.add(k)
            keep.append(b)
    lo = np.array([b[0].min(0) for b in keep])
    hi = np.array([b[0].max(0) for b in keep])
    vol = np.prod(hi - lo, axis=1)
    order = np.argsort(-vol, kind="stable")          # bigger first: only a bigger one can hold a smaller
    gone = np.zeros(len(keep), bool)
    planes = {}
    for idx in order:
        if gone[idx]:
            continue
        inside = np.flatnonzero(np.all(lo >= lo[idx] - eps, 1) & np.all(hi <= hi[idx] + eps, 1) & ~gone)
        inside = inside[inside != idx]
        if not len(inside):
            continue
        if idx not in planes:
            planes[idx] = _brush_planes(keep[idx][0], keep[idx][1])
        for j in inside:
            pts = keep[j][0]
            if all((pts @ n - d <= eps).all() for n, d in planes[idx]):
                gone[j] = True
    return [b for b, g in zip(keep, gone) if not g]


def slab_brushes(co, tri, inch: float, min_area: float, thickness_in: float = 1.0,
                 overlap_in: float = 0.0) -> SlabResult:
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
    # 1. planes, with a tolerance: normals within about a degree (biggest triangles first set the direction),
    # then offsets along it within a quarter inch; each plane split by which way its triangles face
    canon = nrm.copy()
    flip = (canon[:, 0] < -1e-6) | ((np.abs(canon[:, 0]) <= 1e-6) & ((canon[:, 1] < -1e-6) |
                                                                      ((np.abs(canon[:, 1]) <= 1e-6) & (canon[:, 2] < 0))))
    canon[flip] *= -1
    reps = np.zeros((0, 3))
    ncl = np.full(len(tri), -1, np.int64)
    for t in ok[np.argsort(-area[ok], kind="stable")]:
        if len(reps):
            dots = reps @ canon[t]
            j = int(np.argmax(dots))
            if dots[j] > COS_TOL:
                ncl[t] = j
                continue
        reps = np.vstack([reps, canon[t]])
        ncl[t] = len(reps) - 1
    gid = np.full(len(tri), -1, np.int64)
    groups = []                                  # (normal, offset, facing +1/-1, normal cluster)
    gap = 0.25 * inch
    for j in range(len(reps)):
        ts = np.flatnonzero(ncl == j)
        n = reps[j]
        dv = cent[ts] @ n
        order = np.argsort(dv)
        runs = np.split(order, np.flatnonzero(np.diff(dv[order]) > gap) + 1)
        for run in runs:
            tr = ts[run]
            for sgn in (1, -1):
                part = tr[(nrm[tr] @ n >= 0) == (sgn > 0)]
                if len(part):
                    w = area[part]
                    gid[part] = len(groups)
                    groups.append((n, float((cent[part] @ n * w).sum() / w.sum()), sgn, j))
    # regions: same plane group and sharing an edge; small ones are left out
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
    comps = defaultdict(list)
    for t in ok:
        comps[find(t)].append(int(t))
    regions = list(comps.values())
    r_area = np.array([area[r].sum() for r in regions])
    keep = r_area >= min_area
    res.regions, res.kept_regions = len(regions), int(keep.sum())
    total = float(r_area.sum()) or 1.0
    res.kept_share = float(r_area[keep].sum()) / total
    res.skipped_area = float(r_area[~keep].sum())
    res.skipped = [t for r, k in zip(regions, keep) if not k for t in r]

    thick, over = thickness_in * inch, overlap_in * inch
    bases = [_wall_basis(g[0]) for g in groups]
    rect_tris = defaultdict(list)                # group -> triangles whose region outline is square-cornered
    other = defaultdict(list)                    # group -> regions cut into convex pieces instead
    for ri in np.flatnonzero(keep):
        ts = regions[ri]
        g = int(gid[ts[0]])
        u, v = bases[g]
        tuv = co[np.unique(tri[ts])] @ np.stack([u, v], 1)
        if min(np.ptp(tuv[:, 0]), np.ptp(tuv[:, 1])) < THIN_IN * inch:     # the whole area is a thin strip
            res.thin += 1
            res.skipped += ts
            continue
        if _rectilinear(ts, tri, co, u, v, THIN_IN * inch):
            rect_tris[g] += ts
        else:
            other[g].append(ts)

    def uv_tris(g, ts):
        u, v = bases[g]
        p = co[tri[ts]]                          # (k, 3, 3)
        return np.stack([p @ u, p @ v], -1)      # (k, 3, 2)

    def emit(g, poly_uv, lo, hi):
        n = groups[g][0]
        u, v = bases[g]
        base = np.outer(poly_uv[:, 0], u) + np.outer(poly_uv[:, 1], v)
        vs = np.concatenate([base + n * lo, base + n * hi])
        res.brushes.append((vs, len(poly_uv), g))

    def emit_rects(g, rects, lo, hi):
        for (u0, u1, v0, v1) in rects:
            poly = np.array([(u0 - over, v0 - over), (u1 + over, v0 - over), (u1 + over, v1 + over),
                             (u0 - over, v1 + over)])
            emit(g, poly, lo, hi)

    def slab(g):
        d, sgn = groups[g][1], groups[g][2]
        return (d - thick, d) if sgn > 0 else (d, d + thick)

    # 2. two-sided walls: a front plane and the plane behind it facing the other way, close enough
    paired = {}
    cands = []
    by_cluster = defaultdict(list)
    for g in rect_tris:
        by_cluster[groups[g][3]].append(g)
    for gs in by_cluster.values():
        for ga in gs:
            if groups[ga][2] < 0:
                continue
            for gb in gs:
                if groups[gb][2] > 0:
                    continue
                t = groups[ga][1] - groups[gb][1]           # (front faces +n, back faces -n, behind it)
                if 0.1 * inch < t <= MAX_WALL_IN * inch:
                    ov = _bbox_overlap(uv_tris(ga, rect_tris[ga]), uv_tris(gb, rect_tris[gb]))
                    if ov > 0:
                        cands.append((ov, ga, gb))
    for ov, ga, gb in sorted(cands, reverse=True):
        if ga not in paired and gb not in paired:
            paired[ga], paired[gb] = gb, ga
    done = set()
    for g, ts in rect_tris.items():
        if g in done:
            continue
        if g in paired:
            ga, gb = (g, paired[g]) if groups[g][2] > 0 else (paired[g], g)
            done |= {ga, gb}
            ta, tb = uv_tris(ga, rect_tris[ga]), uv_tris(gb, rect_tris[gb])
            U, V = _grid_lines(np.concatenate([ta, tb]), 0.01 * inch)
            ma = _close_gaps(_fill(ta, U, V), U, V, GAP_IN * inch)
            mb = _close_gaps(_fill(tb, U, V), U, V, GAP_IN * inch)
            emit_rects(ga, _cover(ma & mb, U, V), min(groups[gb][1], groups[ga][1] - thick),   # the whole wall
                       groups[ga][1])                                               # (never thinner than a slab)
            emit_rects(ga, _cover(ma & ~mb, U, V), *slab(ga))
            emit_rects(gb, _cover(mb & ~ma, U, V), *slab(gb))
        else:
            done.add(g)
            tu = uv_tris(g, ts)
            U, V = _grid_lines(tu, 0.01 * inch)
            emit_rects(g, _cover(_close_gaps(_fill(tu, U, V), U, V, GAP_IN * inch), U, V), *slab(g))
    res.paired_walls = len(paired) // 2

    # 3. everything else: convex pieces of the region's own triangles (Hertel-Mehlhorn)
    for g, regs in other.items():
        u, v = bases[g]
        lo, hi = slab(g)
        for ts in regs:
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
                if len(poly) >= 3:
                    emit(g, poly, lo, hi)

    # 4. duplicates and brushes inside other brushes go
    res.brushes = _drop_contained(res.brushes, 0.05 * inch)
    return res


def brush_faces(m: int, start: int = 0) -> list:
    """Faces of a brush from slab_brushes (2m verts: back face, then front face), wound outward."""
    faces = [tuple(start + i for i in range(m))[::-1], tuple(start + m + i for i in range(m))]
    for i in range(m):
        j = (i + 1) % m
        faces.append((start + i, start + j, start + m + j, start + m + i))
    return faces
