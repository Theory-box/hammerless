"""Convert to Brushes, Extend-or-Fill: a solid mesh as few clean convex pieces, the way a mapper would build it with
carving tools - faces on the model's own planes, pieces that don't overlap (usable as world brushes), exact or
within a Detail tolerance. Developed and measured in 2D and 3D (docs/knowledge/models/collision.md).

Every inward crease (reflex edge, a dent) is one decision:
  FILL it - a shallow pocket (a groove, a hand-hold, a notch: at most Detail deep from its opening to the floor
  facing it) is covered over; priced by its depth, its volume and how much it grows its piece, so a shallow pocket
  running a whole side (under a counter's lip, beside a table leg) is never filled;
  or CUT it - a crack along one of the two faces meeting there (the mapper move), or on a curve the plane halving
  the crease's angle (a radial cut, like Hammer's arch tool). A crack covers only the region of the cut plane that
  holds the crease (it stops at the outline): that region is removed as a hair-thin slab (GAP), which separates the
  parts without leaving anything of zero thickness; the planes are snapped back together at the end.
The cheapest action per crease resolved wins; a few polishing passes take the 2nd or 3rd best now and then and the
best scoring result is kept (pieces, slivers, fill volume, crack directions). Curves stay exact: a curved surface
costs one piece per facet, as in any exact method.

Input in game units. Noise tolerances follow the model's size (set_scale); Detail and the sliver width are what a
player feels, so they stay in game units. Geometry kernel: manifold3d (vendored in hammerless/_vendor).

    pieces, info = convert(vertices, triangles, detail=8.0)     # pieces: [[(normal, d), ...]] (inside: n.p <= d)
"""
from __future__ import annotations

import math
import os
import random
import sys
import time

import numpy as np


def _kernel():
    """manifold3d: the vendored copy (hammerless/_vendor, for Blender's Python 3.11), else an installed one."""
    vendor = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_vendor")
    if os.path.isdir(vendor):               # (the bundled copy first: a known version, whatever else is installed)
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(vendor)
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
    import manifold3d                       # (raises ImportError if neither is there)
    for need in ("trim_by_plane", "min_gap", "hull_points", "split_by_plane"):
        if not hasattr(manifold3d.Manifold, need):
            raise ImportError(f"manifold3d {getattr(manifold3d, '__version__', '?')} is too old (no {need})")
    return manifold3d


K = _kernel()
Manifold, Mesh, CrossSection = K.Manifold, K.Mesh, K.CrossSection

# ------------------------------------------------------------------ tolerances

WELD = 0.25                 # units: points closer than this are one point
PLANE_ANGLE = 1.0           # degrees: planes this close in direction (and...)
PLANE_OFFSET = 0.25         # ...this close in offset are one plane
DENT_ANGLE = 1.0            # degrees: an edge folding inward by less than this is flat
CURVE_TURN = 35.0           # degrees: a crease folding less than this is a bend in a curve
GAP = 0.002                 # units: a crack removes a slab this thick (snapped shut at the end)
SNAP_MAX = 0.15             # units: corners move at most this far onto their planes (noise, not detail)
POCKET_SHRINK = 0.2         # units: pockets are measured inside the hull pulled in this much (noise layers vanish)
PROBE = 0.6                 # units: the square around a crease that finds its region on a cut plane
CRUMB = 1.0                 # units: a cut leftover thinner than this (and tiny) joins its neighbour
TOUCH = 0.25                # units: how near a pocket must come to a crease to hold it
REF_SIZE = 256.0            # the size the noise tolerances above were set for
TOL = 8.0                   # units: Detail - a pocket at most this deep may be filled
SLIVER = 4.0                # units: a piece thinner than this is a sliver
TRIES = 6                   # polishing passes (fewer on shapes with many creases)
FILL_CURVES = False         # curves stay exact (more pieces rather than a flattened curve)
_BASE = dict(WELD=WELD, PLANE_OFFSET=PLANE_OFFSET, GAP=GAP, SNAP_MAX=SNAP_MAX, POCKET_SHRINK=POCKET_SHRINK,
             PROBE=PROBE, CRUMB=CRUMB, TOUCH=TOUCH)


def set_scale(size: float) -> float:
    """Noise tolerances follow the model's size: a 25-unit lamp arm and a 2,500-unit building can't share the same
    absolute numbers. Detail (TOL) and SLIVER stay in game units: they're what a player feels."""
    g = globals()
    f = min(4.0, max(0.01, float(size) / REF_SIZE))
    for k, v in _BASE.items():
        g[k] = v * f
    return f


# ------------------------------------------------------------------ mesh analysis

def weld(V, T, tol=None):
    """Merge points closer than tol (grid hashing); triangles that collapse are dropped."""
    tol = WELD if tol is None else tol
    key = np.round(np.asarray(V, float) / tol).astype(np.int64)
    _u, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    newV = np.zeros((inv.max() + 1, 3))
    np.add.at(newV, inv, V)
    newV /= np.bincount(inv)[:, None]
    T2 = inv[np.asarray(T, int)]
    keep = (T2[:, 0] != T2[:, 1]) & (T2[:, 1] != T2[:, 2]) & (T2[:, 0] != T2[:, 2])
    return newV, T2[keep]


def tri_normals(V, T):
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    area = np.linalg.norm(n, axis=1)
    return n / np.maximum(area, 1e-12)[:, None], area / 2


def cluster_planes(V, T, offset=None):
    """Faces grouped into planes: a triangle belongs to a plane when every corner is within `offset` of it and it
    faces the same way (loosely: the corners decide). Seeded largest first, then every plane refitted to its points,
    near-duplicate planes merged and triangles reassigned; a few rounds settle it."""
    offset = PLANE_OFFSET if offset is None else offset
    n, area = tri_normals(V, T)
    C = V[T]
    cos_loose = math.cos(math.radians(15.0))
    plane_of = np.full(len(T), -1)
    planes = []
    for t in np.argsort(-area):
        if plane_of[t] >= 0:
            continue
        pn, pd = n[t], float(np.dot(n[t], C[t, 0]))
        on = (plane_of < 0) & (n @ pn > cos_loose) & (np.max(np.abs(C @ pn - pd), axis=1) < offset)
        plane_of[on] = len(planes)
        plane_of[t] = len(planes)
        planes.append((pn, pd))
    for _round in range(4):
        N = np.zeros((len(planes), 3))
        D = np.zeros(len(planes))
        for k in range(len(planes)):
            idx = np.unique(T[plane_of == k].ravel())
            if len(idx) < 3:
                N[k], D[k] = planes[k]
                continue
            P = V[idx]
            c = P.mean(axis=0)
            _u, _s, vt = np.linalg.svd(P - c)
            pn = vt[2] if np.dot(vt[2], planes[k][0]) > 0 else -vt[2]
            N[k], D[k] = pn, float(np.dot(pn, c))
        sizes = np.bincount(plane_of, weights=area, minlength=len(planes))
        target = np.arange(len(planes))
        near = (N @ N.T) > math.cos(math.radians(3.0 * PLANE_ANGLE))
        for i in np.argsort(-sizes):
            if target[i] != i:
                continue
            for j in np.nonzero(near[i])[0]:
                if j == i or target[j] != j or sizes[j] > sizes[i]:
                    continue
                idx = np.unique(T[plane_of == j].ravel())
                if len(idx) and float(np.max(np.abs(V[idx] @ N[i] - D[i]))) < offset:
                    target[j] = i
        plane_of = target[plane_of]
        dist = np.max(np.abs(np.einsum("tcx,px->tcp", C, N) - D[None, None, :]), axis=1)
        dist[~(n @ N.T > cos_loose)] = np.inf
        alive = np.zeros(len(planes), bool)
        alive[np.unique(plane_of)] = True
        dist[:, ~alive] = np.inf
        best = np.argmin(dist, axis=1)
        ok = dist[np.arange(len(T)), best] < offset
        new_of = np.where(ok, best, plane_of)
        changed = int(np.sum(new_of != plane_of))
        used = np.unique(new_of)
        remap = np.full(len(planes), -1)
        remap[used] = np.arange(len(used))
        plane_of = remap[new_of]
        planes = [(N[k], float(D[k])) for k in used]
        if not changed:
            break
    return planes, plane_of


# ------------------------------------------------------------------ kernel helpers

def to_manifold(V, T):
    return Manifold(Mesh(vert_properties=np.asarray(V, np.float32), tri_verts=np.asarray(T, np.uint32)))


def arrays(m):
    mm = m.to_mesh64() if hasattr(m, "to_mesh64") else m.to_mesh()
    return np.asarray(mm.vert_properties, float)[:, :3], np.asarray(mm.tri_verts, int)


def hull_planes(P):
    """The planes of the convex hull of points P: [(normal, d)] (via the kernel's hull)."""
    h = Manifold.hull_points(np.asarray(P, float).tolist())
    Vh, Th = arrays(h)
    if len(Th) == 0:
        return []
    n, area = tri_normals(Vh, Th)
    planes = []
    for t in np.argsort(-area):
        if area[t] < 1e-12:
            continue
        nrm, d = n[t], float(np.dot(n[t], Vh[Th[t][0]]))
        if not any(np.dot(nrm, q[0]) > 1 - 1e-9 and abs(d - q[1]) < 1e-6 for q in planes):
            planes.append((tuple(nrm), d))
    return planes


def frame_of(normal):
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    return np.array([u, np.cross(n, u), n])


def section(m, R, height):
    return m.transform(np.hstack([R, np.zeros((3, 1))])).slice(height)


def solids(m):
    """The separate solids of m (decompose() returns a hollow's inner surface as its own inside-out shell:
    each part intersected with m again gets its hollows back)."""
    parts = m.decompose()
    comps = [q for q in parts if q.volume() > 1e-3]
    if len(comps) == len(parts):
        return comps
    return [q ^ m for q in comps]


def extent(m, n) -> float:
    Vm, _ = arrays(m)
    x = Vm @ np.asarray(n)
    return float(x.max() - x.min()) if len(x) else 0.0


def is_convex(m, rel=1e-5) -> bool:
    v = m.volume()
    return v > 0 and m.hull().volume() <= v * (1 + rel) + 1e-6


# ------------------------------------------------------------------ creases

_CREASES: dict = {}


def creases_of(m):
    """[(edge point a, b, face plane 1, face plane 2, fold angle, length)]: one per inward crease (a connected chain
    of dent edges between the same two faces). Cached per piece object."""
    hit = _CREASES.get(id(m))
    if hit is not None and hit[0] is m:
        return hit[1]
    out = _creases_of(m)
    if len(_CREASES) > 20000:
        _CREASES.clear()
    _CREASES[id(m)] = (m, out)
    return out


def _creases_of(m):
    V, T = arrays(m)
    if len(T) == 0:
        return []
    planes, plane_of = cluster_planes(V, T)
    n, _area = tri_normals(V, T)
    sheet = sheet_triangles(V, T, planes, plane_of)

    def exact_plane(t):
        """The cut plane for a face at a crease: fitted to the triangles of its group lying exactly flat with it
        (a fit that lumps a bevel strip in with a face is off by a hair, and a cut a hair off leaves a sliver)."""
        pn, pd = n[t], float(np.dot(n[t], V[T[t][0]]))
        group = np.nonzero(plane_of == plane_of[t])[0]
        flat = group[(np.max(np.abs(V[T[group]] @ pn - pd), axis=1) < 1e-3) & (n[group] @ pn > 0.999)]
        idx = np.unique(T[flat].ravel())
        if len(idx) >= 3:
            P = V[idx]
            c = P.mean(axis=0)
            _u, _s, vt = np.linalg.svd(P - c)
            q = vt[2] if np.dot(vt[2], pn) > 0 else -vt[2]
            return q, float(np.dot(q, c))
        return pn, pd

    edges = {}
    for ti, (a, b, c) in enumerate(T):
        if ti in sheet:
            continue
        for u, w in ((a, b), (b, c), (c, a)):
            edges.setdefault((min(u, w), max(u, w)), []).append(ti)
    found = []
    for (u, w), ts in edges.items():
        if len(ts) != 2:
            continue
        t1, t2 = ts
        pa, pb = plane_of[t1], plane_of[t2]
        if pa == pb:
            continue
        other = [x for x in T[t2] if x not in (u, w)][0]
        if np.dot(V[other] - V[u], n[t1]) <= 1e-6:
            continue
        ang = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(planes[pa][0], planes[pb][0]))))))
        if ang > DENT_ANGLE:
            found.append((u, w, pa, pb, ang, t1, t2))
    out = []
    by_pair = {}
    for e in found:
        by_pair.setdefault((min(e[2], e[3]), max(e[2], e[3])), []).append(e)
    for es in by_pair.values():
        parent = {}

        def find(x):
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for e in es:
            parent[find(e[0])] = find(e[1])
        chains = {}
        for e in es:
            chains.setdefault(find(e[0]), []).append(e)
        for ch in chains.values():
            longest = max(ch, key=lambda e: float(np.linalg.norm(V[e[1]] - V[e[0]])))
            total = sum(float(np.linalg.norm(V[e[1]] - V[e[0]])) for e in ch)
            u, w, _a, _b, ang, t1, t2 = longest
            out.append([V[u], V[w], exact_plane(t1), exact_plane(t2), ang, total])
    return out


def sheet_triangles(V, T, planes, plane_of):
    """Triangles of zero-thickness sheets (two faces on one plane facing opposite ways and overlapping): not
    surface, crease finding skips them."""
    out = set()
    for i, (ni, di) in enumerate(planes):
        for j, (nj, dj) in enumerate(planes):
            if j <= i or np.dot(ni, nj) > -1 + 1e-6 or abs(di + dj) > 1e-3:
                continue
            R = frame_of(ni)

            def tris2d(k):
                idx = np.where(plane_of == k)[0]
                return idx, [CrossSection([(R @ V[T[t]].T).T[:, :2].tolist()], K.FillRule.NonZero) for t in idx]
            ia, pa = tris2d(i)
            ib, pb = tris2d(j)
            ua = CrossSection.batch_boolean(pa, K.OpType.Add) if len(pa) > 1 else pa[0]
            ub = CrossSection.batch_boolean(pb, K.OpType.Add) if len(pb) > 1 else pb[0]
            over = ua.offset(1e-6, K.JoinType.Miter) ^ ub.offset(1e-6, K.JoinType.Miter)
            if over.area() < 1e-6:
                continue
            for idx, polys in ((ia, pa), (ib, pb)):
                for t, q in zip(idx, polys):
                    if q.area() > 0 and (q ^ over).area() > 0.99 * q.area():
                        out.add(int(t))
    return out


# ------------------------------------------------------------------ cuts and fills

def cut(piece, normal, offset, crease_mid):
    """A crack along n.p = offset covering only the cut plane's region that holds the crease, removed as a
    GAP-thin slab (parts separate only where the crack runs; nothing of zero thickness is left)."""
    n = np.asarray(normal, float)
    ln = np.linalg.norm(n)
    n, offset = n / ln, offset / ln
    R = frame_of(n)
    sec = section(piece, R, offset - GAP * 0.25) + section(piece, R, offset + GAP * 0.25)
    if sec.is_empty():
        return None
    mid2 = (R @ np.asarray(crease_mid))[:2]
    probe = CrossSection.square([PROBE, PROBE], True).translate(mid2.tolist())
    region = None
    for poly in sec.decompose():
        if (poly ^ probe).area() > 1e-6:
            region = poly if region is None else region + poly
    if region is None:
        return None
    slab = region.offset(GAP, K.JoinType.Miter)
    cutter = Manifold.extrude(slab, GAP).translate([0.0, 0.0, offset - GAP / 2])
    cutter = cutter.transform(np.hstack([R.T, np.zeros((3, 1))]))
    parts = absorb_crumbs(solids(piece - cutter), n, piece.volume())
    return parts if len(parts) > 1 else None


def absorb_crumbs(parts, n, total):
    """A cut through a face a fraction of a degree off flat leaves hair-thin wedges: a part thinner than CRUMB
    along the cut and under 0.5% of the piece joins the neighbour it touches."""
    big = [p for p in parts if not (p.volume() < 0.005 * total and extent(p, n) < CRUMB)]
    for c in [p for p in parts if not any(p is b for b in big)]:
        if not big:
            big.append(c)
            continue
        k = min(range(len(big)), key=lambda i: big[i].min_gap(c, 4 * GAP))
        big[k] = big[k] + c
    return big


def pockets(piece):
    """The piece's cavities: pockets between its convex hull (pulled in by POCKET_SHRINK, so a noisy face's hair-
    thin layer doesn't join every pocket into one) and itself, each with its depth: from its opening (lid faces on
    the hull) to the floor facing it (side walls don't count: the gap between two legs is open below, its only floor
    is the seat far above)."""
    hull = piece.hull()
    Vh, _t = arrays(hull)
    planes = hull_planes(Vh)
    if not planes:
        return []
    N = np.array([p[0] for p in planes])
    D = np.array([p[1] for p in planes])
    hull_in = hull
    for nrm, d in zip(N, D):
        hull_in = hull_in.trim_by_plane((-nrm).tolist(), float(-(d - POCKET_SHRINK)))
    if hull_in.is_empty():
        hull_in = hull
    else:
        D = D - POCKET_SHRINK
    out = []
    cos_face = math.cos(math.radians(30.0))
    for q in solids(hull_in - piece):
        if q.volume() < 1e-2:
            continue
        Vq, Tq = arrays(q)
        nq, _a = tri_normals(Vq, Tq)
        lids, floor = set(), []
        for t in range(len(Tq)):
            c = Vq[Tq[t]]
            on = [k for k in range(len(N)) if np.dot(N[k], nq[t]) > 1 - 1e-4 and np.max(np.abs(c @ N[k] - D[k])) < 1e-3]
            if on:
                lids.update(on)
            else:
                floor.append(t)
        if not lids or not floor:
            continue
        depth = -1.0
        for t in floor:
            opp = [k for k in lids if np.dot(nq[t], N[k]) < -cos_face]
            if not opp:
                continue
            c = Vq[Tq[t]]
            P = np.array([c[0], c[1], c[2], c.mean(axis=0)])
            depth = max(depth, float(np.max(np.min(np.abs(D[opp][None, :] - P @ N[opp].T), axis=1))))
        if depth < 0:
            depth = math.inf
        grow = POCKET_SHRINK * 1.5
        offs = np.array([[x, y, z] for x in (-grow, 0, grow) for y in (-grow, 0, grow) for z in (-grow, 0, grow)])
        around = Manifold.hull_points((Vq[:, None, :] + offs[None, :, :]).reshape(-1, 3).tolist())
        full = (hull - piece) ^ around
        out.append((full if full.volume() > q.volume() else q, depth + POCKET_SHRINK))
    return out


def touches(m, point, r=None) -> bool:
    r = TOUCH if r is None else r
    c = Manifold.cube([2 * r] * 3, True).translate(list(map(float, point)))
    return (m ^ c).volume() > 1e-6


def snap_dir(nrm, through):
    """A cut normal within half a degree of a world axis becomes that axis (through the crease): a face a hair off
    its axis still gets an axis-aligned cut."""
    for d in np.eye(3):
        if abs(float(np.dot(nrm, d))) > math.cos(math.radians(0.5)):
            d = d if np.dot(nrm, d) > 0 else -d
            return d, float(np.dot(d, through))
    return nrm, float(np.dot(nrm, through))


def candidates(cr):
    a, b, p1, p2, ang, _len = cr
    mid = (a + b) / 2
    n1, d1 = snap_dir(np.asarray(p1[0]), mid)
    n2, d2 = snap_dir(np.asarray(p2[0]), mid)
    out = [(n1, d1, "extend"), (n2, d2, "extend")]
    if ang <= CURVE_TURN:
        e = (b - a) / np.linalg.norm(b - a)
        nrm = np.cross(e, np.asarray(p1[0]) + np.asarray(p2[0]))
        if np.linalg.norm(nrm) > 1e-9:
            nrm /= np.linalg.norm(nrm)
            out.append((nrm, float(np.dot(nrm, a)), "radial"))
    return out


def planarize(V, T):
    """Corners of faces that aren't exactly flat (noise) snap onto their fitted planes, at most SNAP_MAX: a messy
    mesh becomes its clean geometry, so cuts along its faces are exact. Exactly flat faces (real, even tiny ones
    like a bevel strip) are left alone."""
    _ep, exact_of = cluster_planes(V, T, offset=1e-3)
    planes, plane_of = cluster_planes(V, T, offset=SNAP_MAX)
    exact_tri = np.bincount(exact_of)[exact_of] >= 2
    clean = np.ones(len(V), bool)
    clean[T[~exact_tri].ravel()] = False
    owners = [set() for _ in range(len(V))]
    for t, k in enumerate(plane_of):
        for v in T[t]:
            owners[v].add(int(k))
    out = V.copy()
    lam = 1e-4
    for i, ks in enumerate(owners):
        if not ks or clean[i]:
            continue
        A = np.array([planes[k][0] for k in ks])
        b = np.array([planes[k][1] for k in ks])
        x, *_ = np.linalg.lstsq(np.vstack([A, math.sqrt(lam) * np.eye(3)]),
                                np.concatenate([b, math.sqrt(lam) * V[i]]), rcond=None)
        if np.linalg.norm(x - V[i]) < SNAP_MAX:
            out[i] = x
    return out


# ------------------------------------------------------------------ the search

_OPTS: dict = {}


# ------------------------------------------------------------------ worker processes (all cores)

# candidate cuts tested in this many worker processes (0: here, one after another). Started per conversion (about
# 0.2 s) and closed after it; past 8 there is little gain (measured 3x at 8, 3.6x at 15 on curved props)
# (under 4 cores: none, one or two workers lose to their own start-up and copying)
WORKERS = 0 if (os.cpu_count() or 1) < 4 else min(8, os.cpu_count() - 1)
PARALLEL_FROM = 12          # (only shapes with at least this many creases use them: small ones are instant anyway)
_POOL = None
_POOL_SIZE = 0
_BIG = [False]
_BROKEN = [False]           # (a worker died or couldn't start this conversion: the rest of it runs here)
_TUNABLES = ("WELD", "PLANE_OFFSET", "GAP", "SNAP_MAX", "POCKET_SHRINK", "PROBE", "CRUMB", "TOUCH", "TOL", "SLIVER")


def to_manifold64(V, T):
    # (fresh, writable copies: the binding refuses read-only arrays, like a mesh's own or unpickled ones)
    return Manifold(K.Mesh64(vert_properties=np.array(V, np.float64, order="C", copy=True),
                             tri_verts=np.array(T, np.uint64, order="C", copy=True)))


def _set_tunables(values):
    globals().update(values)


def _cut_task(args):
    """In a worker: one candidate cut of one piece, with the creases of what it makes (meshes in, meshes out)."""
    V, T, nrm, off, mid, values = args
    _set_tunables(values)
    res = cut(to_manifold64(V, T), nrm, off, mid)
    if res is None:
        return None
    out = []
    for r in res:
        Vr, Tr = arrays(r)
        out.append((Vr, Tr, _creases_of(r), extent(r, nrm)))
    return out


def plane_parts(m, n, d, D, sliver, pvol):
    """General's (organicfit) full plane cut of solid m: (big, crumbs) as [(solid, side)], or None if it doesn't
    split it into two real parts. Crumbs - under 5% of the piece's volume and thinner than max(D, sliver) along
    n - fold into a big part. Here (not in organicfit) so worker processes, which load this file alone, run the
    same code."""
    n = np.asarray(n, float)
    a, b = m.split_by_plane(n.tolist(), float(d))
    parts = [(q, 1) for q in solids(a) if q.volume() > 1e-6] + [(q, -1) for q in solids(b) if q.volume() > 1e-6]
    if len(parts) < 2:
        return None
    big, crumbs = [], []
    for q, sg in parts:
        Vq, _ = arrays(q)
        ext = float((Vq @ n).max() - (Vq @ n).min())
        (crumbs if q.volume() < 0.05 * pvol and ext < max(D, sliver) else big).append((q, sg))
    if len(big) < 2:
        return None
    return big, crumbs


def fold_crumbs(big, crumbs):
    """The final parts of a plane cut: each crumb joins the biggest part (as it is then). -> (solids, owner)"""
    ms = [m for m, _ in big]
    owner = []
    for cm, _sg in crumbs:
        j = int(np.argmax([m.volume() for m in ms]))
        ms[j] = ms[j] + cm
        owner.append(j)
    return ms, owner


def _organic_task(args):
    """In a worker: General's candidate cuts of one piece, scored only: per cut None or [(volume, air)] per part."""
    V, T, cuts, D, sliver, pvol = args
    m = to_manifold64(V, T)
    out = []
    for n, d in cuts:
        got = plane_parts(m, n, d, D, sliver, pvol)
        if got is None:
            out.append(None)
            continue
        ms, _owner = fold_crumbs(*got)
        out.append([(q.volume(), max(0.0, q.hull().volume() - q.volume())) for q in ms])
    return out


STABLE_NAME = "hl_brushfit"   # (this module's name in the workers: an installed add-on's own module path,
#                               bl_ext.<repo>.hammerless..., exists only inside Blender)


def _python_exe():
    """The Python to start workers with (None: none found, work here). Blender since 2.91 sets sys.executable to its
    own bundled Python; an older or unusual build may leave it as the blender binary itself, never to be spawned."""
    exe = sys.executable
    if os.path.basename(exe).lower().startswith("blender"):
        exe = os.path.join(sys.prefix, "bin", "python.exe" if os.name == "nt" else "python3")
        if not os.path.isfile(exe):
            return None
    return exe


class _NoMain:
    """While workers start: hide the main script from multiprocessing, which would otherwise run it again in every
    worker (a script run from Blender's text editor, or a test). Workers need only this file."""

    def __enter__(self):
        m = sys.modules.get("__main__")
        self.saved = (m, m.__dict__.pop("__file__", None), m.__dict__.get("__spec__")) if m else None
        if m:
            m.__spec__ = None
        return self

    def __exit__(self, *exc):
        if self.saved:
            m, f, spec = self.saved
            if f is not None:
                m.__file__ = f
            if spec is not None or "__spec__" in m.__dict__:
                m.__spec__ = spec


def pool(n):
    """The worker processes, kept between conversions (starting them is the slow part)."""
    global _POOL, _POOL_SIZE
    if n <= 0 or _BROKEN[0]:
        return None
    if _POOL is None or _POOL_SIZE != n:
        exe = _python_exe()
        if exe is None:
            return None
        import concurrent.futures
        import multiprocessing
        if _POOL is not None:
            _POOL.shutdown(cancel_futures=True)
        mod = sys.modules[__name__]
        sys.modules[STABLE_NAME] = mod               # (tasks are pickled by module name: the stable one)
        _cut_task.__module__ = STABLE_NAME
        _organic_task.__module__ = STABLE_NAME       # (General's: picked from the same module)
        ctx = multiprocessing.get_context("spawn")
        ctx.set_executable(exe)          # (process-wide for multiprocessing: Blender's own Python, the same anyway)
        # (the worker's setup runs through exec, a built-in every Python has: it loads this file as STABLE_NAME)
        loader = "; ".join([
            "import importlib.util, sys",
            f"spec = importlib.util.spec_from_file_location({STABLE_NAME!r}, {os.path.abspath(__file__)!r})",
            "mod = importlib.util.module_from_spec(spec)",
            f"sys.modules[{STABLE_NAME!r}] = mod",
            "spec.loader.exec_module(mod)"])
        _POOL = concurrent.futures.ProcessPoolExecutor(n, mp_context=ctx, initializer=exec, initargs=(loader, {}))
        _POOL_SIZE = n
    return _POOL


def shutdown_pool():
    global _POOL, _POOL_SIZE
    if _POOL is not None:
        _POOL.shutdown(wait=False, cancel_futures=True)     # (the workers exit on their own: waiting took ~3 s)
    _POOL, _POOL_SIZE = None, 0


def piece_options(piece, crs):
    hit = _OPTS.get(id(piece))
    if hit is not None and hit[0] is piece:
        return hit[1]
    out = []
    for q, depth in pockets(piece):
        if depth > TOL:
            continue
        held = [cr for cr in crs if touches(q, (cr[0] + cr[1]) / 2)]
        if not held or (not FILL_CURVES and any(cr[4] <= CURVE_TURN for cr in held)):
            continue
        filled = piece + q
        resolved = len(crs) - len(creases_of(filled))
        if resolved <= 0:
            continue
        cost = (0.05 + 0.9 * depth / TOL) / resolved + q.volume() / (20.0 * TOL ** 3)
        cost += 10.0 * q.volume() / max(piece.volume(), 1e-6)
        if cost > 2.0:
            continue
        out.append((cost, [filled], None, "fill", None, q.volume()))
    tasks = [(nrm, off, kind, (cr[0] + cr[1]) / 2) for cr in crs for nrm, off, kind in candidates(cr)]
    ex = pool(WORKERS) if len(tasks) > 1 and _BIG[0] else None
    if ex is not None:
        V, T = arrays(piece)
        values = {k: globals()[k] for k in _TUNABLES}
        from concurrent.futures.process import BrokenProcessPool
        try:
            with _NoMain():                           # (workers start on demand, in here)
                futs = [ex.submit(_cut_task, (V, T, nrm, off, mid, values)) for nrm, off, _k, mid in tasks]
            done = [f.result() for f in futs]
        except BrokenProcessPool as e:
            print(f"Extend-or-Fill: worker processes failed ({e}); carrying on in one process")
            _BROKEN[0] = True
            shutdown_pool()
            ex = None
    if ex is not None:
        results = []
        for got in done:
            if got is None:
                results.append(None)
                continue
            res, exts = [], []
            for Vr, Tr, crs_r, ext in got:
                r = to_manifold64(Vr, Tr)
                _CREASES[id(r)] = (r, crs_r)          # (the worker found its creases already)
                res.append(r)
                exts.append(ext)
            results.append((res, exts))
    else:
        results = []
        for nrm, off, _k, mid in tasks:
            res = cut(piece, nrm, off, mid)
            results.append(None if res is None else (res, [extent(r, nrm) for r in res]))
    for (nrm, off, kind, _mid), got in zip(tasks, results):
        if got is None:
            continue
        res, exts = got
        resolved = len(crs) - sum(len(creases_of(r)) for r in res)
        if resolved <= 0:
            continue
        cost = (len(res) - 1) / resolved + 0.6 * sum(1 for e in exts if e < SLIVER)
        out.append((cost, res, nrm, kind, off / np.linalg.norm(nrm), 0.0))
    if len(_OPTS) > 20000:
        _OPTS.clear()
    _OPTS[id(piece)] = (piece, out)
    return out


def run_once(start, rnd=None, cancel=None):
    model, first = start
    pieces = list(first)
    dirs, cut_planes, log = [], [], []
    filled = 0.0
    for _step in range(400):
        if cancel is not None and cancel():
            log.append("cancelled")
            break
        crease_lists = [creases_of(p) for p in pieces]
        if sum(len(c) for c in crease_lists) == 0:
            break
        opts = []
        for pi, crs in enumerate(crease_lists):
            if not crs:
                continue
            for cost, res, nrm, kind, off, vol in piece_options(pieces[pi], crs):
                if nrm is not None and kind != "radial" and \
                        not any(abs(abs(float(np.dot(nrm, d))) - 1) < 1e-3 for d in dirs):
                    cost += 0.12
                opts.append((cost, pi, res, nrm, kind, off, vol))
        if not opts:
            fixed = False                     # (repair: a stuck piece whose hull adds little becomes its hull)
            for pi, crs in enumerate(crease_lists):
                if not crs:
                    continue
                h = pieces[pi].hull()
                deep = max((d for _q, d in pockets(pieces[pi])), default=0.0)
                if h.volume() <= pieces[pi].volume() * 1.05 and deep <= TOL:
                    filled += h.volume() - pieces[pi].volume()
                    pieces[pi] = h
                    fixed = True
            if fixed:
                log.append("repair: hull")
                continue
            log.append("stuck")
            break
        opts.sort(key=lambda o: o[0])
        pick = 0
        if rnd is not None and len(opts) > 1:
            k = min(3, len(opts))
            pick = rnd.choices(range(k), weights=[0.6, 0.25, 0.15][:k])[0]
        cost, pi, res, nrm, kind, off, vol = opts[pick]
        pieces = pieces[:pi] + pieces[pi + 1:] + res
        filled += vol
        if nrm is not None:
            dirs.append(nrm)
            cut_planes.append((nrm, off))
        log.append(f"{kind}, cost {cost:.2f}")
    pieces = [p for p in merge_convex(pieces) if p.volume() > 1e-2]
    return pieces, cut_planes, log, filled, model.volume()


def merge_convex(pieces):
    """Neighbouring pieces (within a crack's GAP) whose union is convex become one: their hull, closing the gap."""
    pieces = list(pieces)
    changed = True
    while changed:
        changed = False
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                a, b = pieces[i], pieces[j]
                if a.min_gap(b, 4 * GAP) > 3 * GAP:
                    continue
                h = (a + b).hull()
                slack = GAP * min(a.surface_area(), b.surface_area()) + 1e-5 * h.volume()
                if h.volume() <= a.volume() + b.volume() + slack:
                    pieces = [p for k, p in enumerate(pieces) if k not in (i, j)] + [h]
                    changed = True
                    break
            if changed:
                break
    return pieces


def result_score(pieces, cut_planes, log, filled, volume):
    if "stuck" in log:
        return 1e9
    if not all(is_convex(p, rel=1e-3) for p in pieces):
        return 1e8
    thin = 0
    for p in pieces:
        Vp, _ = arrays(p)
        pl = hull_planes(Vp)
        if not pl or min(float((Vp @ np.asarray(q[0])).max() - (Vp @ np.asarray(q[0])).min()) for q in pl) < SLIVER:
            thin += 1
    dirs = []
    for nrm, _o in cut_planes:
        if not any(abs(abs(float(np.dot(nrm, d))) - 1) < 1e-3 for d in dirs):
            dirs.append(nrm)
    return len(pieces) + 0.7 * thin + 20.0 * filled / max(volume, 1.0) + 0.1 * len(dirs)


def dedupe_planes(refs, n_tol=1e-5):
    out = []
    for pn, pd in refs:
        pn = np.asarray(pn, float)
        if not any(np.dot(pn, q) > 1 - n_tol and abs(pd - qd) < 3 * GAP for q, qd in out):
            out.append((pn, float(pd)))
    return out


def snap(planes, refs):
    out = []
    for nrm, d in planes:
        best = None
        for rn, rd in refs:
            if np.dot(nrm, rn) > 1 - 1e-5 and abs(d - rd) <= 3 * GAP and (best is None or abs(d - rd) < abs(d - best[1])):
                best = (rn, rd)
        out.append((tuple(best[0]), float(best[1])) if best else (nrm, d))
    return out


class NotSolid(ValueError):
    """The mesh isn't a closed solid (holes, flipped or overlapping parts)."""


class TooBig(ValueError):
    """The mesh is too detailed (too many creases) or took longer than the time limit."""


MAX_CREASES = 300           # more inward creases than this: too detailed for Extend-or-Fill (use Slabs)
TIME_LIMIT = 30.0           # seconds: a run taking longer stops (Blender is waiting on it)


def convert(V, T, detail=TOL, tries=None, cancel=None, time_limit=None):
    """Mesh (game units) -> convex pieces (corners, triangles), and info (pieces, log, seconds, filled volume).
    Raises NotSolid (not a closed solid / inside out) and TooBig (too many creases, or over the time limit)."""
    global TOL
    t0 = time.time()
    limit = TIME_LIMIT if time_limit is None else time_limit
    user_cancel = cancel
    cancel = lambda: (time.time() - t0 > limit) or (user_cancel is not None and user_cancel())
    V = np.asarray(V, float)
    T = np.asarray(T, int)
    set_scale(float(np.linalg.norm(V.max(axis=0) - V.min(axis=0))))
    TOL = float(detail)
    V, T = weld(V, T)
    V = planarize(V, T)
    _CREASES.clear()
    _OPTS.clear()
    model = to_manifold(V, T)
    status = str(model.status())
    if model.is_empty() or "NoError" not in status:
        raise NotSolid(f"the mesh isn't a closed solid ({status.split('.')[-1]}): fill its holes and remove loose or "
                       "overlapping parts first")
    if model.volume() <= 0:
        raise NotSolid("the mesh is inside out (its normals point inward): recalculate normals outside first")
    n0 = len(creases_of(model))
    if n0 > MAX_CREASES:
        raise TooBig(f"too detailed for Hard Surface ({n0} inward creases, at most {MAX_CREASES}): use Slabs, or "
                     "simplify the mesh")
    start = (model, solids(model))
    _BIG[0] = n0 >= PARALLEL_FROM
    _BROKEN[0] = False
    try:
        best = run_once(start, cancel=cancel)
        best_s = result_score(*best)
        if tries is None:
            tries = TRIES if n0 <= 16 else (2 if n0 <= 40 else 1)
        rnd = random.Random(7)
        for _ in range(tries):
            if cancel is not None and cancel():
                break
            cand = run_once(start, rnd, cancel=cancel)
            sc = result_score(*cand)
            if sc < best_s - 1e-9:
                best, best_s = cand, sc
    finally:
        _BIG[0] = False
        shutdown_pool()                 # (no idle processes left behind: starting them again is cheap)
    pieces, cut_planes, log, filled, volume = best
    if "cancelled" in log:
        raise TooBig(f"took longer than {limit:.0f} s: use Slabs, raise Detail, or simplify the mesh")
    refs = [(np.asarray(pn), pd) for pn, pd in cluster_planes(V, T)[0]] + [(np.asarray(c[0]), c[1]) for c in cut_planes]
    refs = dedupe_planes(refs + [(-pn, -pd) for pn, pd in refs])
    meshes = [snapped_hull(p, refs) for p in pieces]
    meshes = [m for m in meshes if m is not None]
    return meshes, dict(pieces=len(meshes), log=log, seconds=time.time() - t0, filled=filled, volume=volume,
                        stuck="stuck" in log)


def snapped_hull(piece, refs):
    """A piece's convex hull as (corners, triangles), each corner moved onto the snapped planes of the faces meeting
    there (least squares): the crack gaps close, and the hull's own shape and topology are kept (rebuilding corners
    from dozens of nearly coplanar planes, as a noisy piece has, is numerically fragile)."""
    h = piece.hull()
    Vh, Th = arrays(h)
    if len(Th) < 4:
        return None
    n, area = tri_normals(Vh, Th)
    tri_plane = []
    for t in range(len(Th)):
        nrm, d = n[t], float(np.dot(n[t], Vh[Th[t][0]]))
        tri_plane.append(snap([(nrm, d)], refs)[0] if area[t] > 1e-9 else (tuple(nrm), d))
    out = Vh.copy()
    lam = 1e-6
    for v in range(len(Vh)):
        inc = np.nonzero((Th == v).any(axis=1))[0]
        pl = []
        for t in inc:
            pn, pd = tri_plane[t]
            if not any(np.dot(pn, q) > 1 - 1e-9 and abs(pd - qd) < 1e-9 for q, qd in pl):
                pl.append((np.asarray(pn), pd))
        if not pl:
            continue
        A = np.array([q for q, _d in pl])
        b = np.array([d for _q, d in pl])
        x, *_ = np.linalg.lstsq(np.vstack([A, math.sqrt(lam) * np.eye(3)]),
                                np.concatenate([b, math.sqrt(lam) * Vh[v]]), rcond=None)
        if np.linalg.norm(x - Vh[v]) < 4 * GAP:
            out[v] = x
    return out, Th


# ------------------------------------------------------------------ pieces as polygons

def piece_polygons(planes):
    """A convex piece's corners and faces (each face's corners in order, wound outward) from its planes."""
    import itertools
    N = np.array([p[0] for p in planes], float)
    Dv = np.array([p[1] for p in planes], float)
    pts = []
    for i, j, k in itertools.combinations(range(len(planes)), 3):
        Mx = N[[i, j, k]]
        if abs(np.linalg.det(Mx)) < 1e-9:
            continue
        x = np.linalg.solve(Mx, Dv[[i, j, k]])
        if np.all(N @ x <= Dv + 1e-4):
            pts.append(x)
    if len(pts) < 4:
        return np.zeros((0, 3)), []
    P = np.array(pts)
    keep = []                                   # (merge corners closer than 1e-4)
    for p in P:
        if not any(np.linalg.norm(p - q) < 1e-4 for q in keep):
            keep.append(p)
    P = np.array(keep)
    faces = []
    for n, d in zip(N, Dv):
        on = np.nonzero(np.abs(P @ n - d) < 1e-3)[0]
        if len(on) < 3:
            continue
        c = P[on].mean(axis=0)
        u = P[on[0]] - c
        u /= np.linalg.norm(u) or 1.0
        v = np.cross(n, u)
        ang = np.arctan2((P[on] - c) @ v, (P[on] - c) @ u)
        faces.append([int(on[k]) for k in np.argsort(ang)])
    return P, faces


def hull_faces(V, T):
    """A convex piece's faces as polygons: its coplanar triangles merged, each face's corners in order (wound
    outward like the triangles)."""
    n, area = tri_normals(V, T)
    size = float(np.linalg.norm(V.max(axis=0) - V.min(axis=0))) if len(V) else 1.0
    eps = 1e-5 * size + 1e-6
    groups = []
    for t in np.argsort(-area):              # (big triangles first: they set each face's plane)
        if area[t] < 1e-12:
            continue
        d = float(np.dot(n[t], V[T[t][0]]))
        for g in groups:
            # (a sliver triangle's own normal is noise: it joins a face when its corners lie on that face's plane)
            if np.dot(g[0], n[t]) > 0.9 and np.max(np.abs(V[T[t]] @ g[0] - g[1])) < eps:
                g[2].append(t)
                break
        else:
            groups.append([n[t], d, [t]])
    faces = []
    for nrm, _d, ts in groups:
        idx = np.unique(T[ts].ravel())
        c = V[idx].mean(axis=0)
        u = V[idx[0]] - c
        u /= np.linalg.norm(u) or 1.0
        v = np.cross(nrm, u)
        ring = _outline(np.c_[(V[idx] - c) @ u, (V[idx] - c) @ v], eps)
        if len(ring) >= 3:
            faces.append([int(idx[k]) for k in ring])
    return faces


def _outline(P, eps):
    """Indices of the 2D points' convex outline, counter-clockwise (monotone chain): a corner inside the face or
    on a straight stretch of its edge is left out, so a face never folds over itself."""
    order = sorted(range(len(P)), key=lambda k: (P[k][0], P[k][1]))

    def turn(o, a, b):
        return (P[a][0] - P[o][0]) * (P[b][1] - P[o][1]) - (P[a][1] - P[o][1]) * (P[b][0] - P[o][0])
    tol = eps * eps
    lower, upper = [], []
    for k in order:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], k) <= tol:
            lower.pop()
        lower.append(k)
    for k in reversed(order):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], k) <= tol:
            upper.pop()
        upper.append(k)
    return lower[:-1] + upper[:-1]
