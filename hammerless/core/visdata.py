"""Visibility data for the viewport (blender/visview.py):

- the portals vis works through (vbsp's .prt file): how the map's open space was split up
- the rendering load: for each part of the map, how many faces the game draws from there (the
  compiled map's visibility data, so it works whichever vis compiler built it)
- the vis cost: how long each portal took (written by Hammerless's vis compiler as <map>.viscost),
  and the Blender objects whose brushes those portals were split along
"""
import json
import os
import re
import struct
from dataclasses import dataclass

import numpy as np

from .lightmap import FACE_SIZE, SURF_SKY, SURF_SKY2D, TEXINFO_SIZE, _displacement, _lumps

SURF_NODRAW = 0x80
SLIVER_AREA = 16.0           # square units: smaller portals are slivers (vbsp splits from near-miss brushes)
SLIVER_WIDTH = 1.0           # units: thinner ones too


# ---------------------------------------------------------------- portals

@dataclass
class Portals:
    clusters: int
    c0: np.ndarray            # the two clusters each portal joins
    c1: np.ndarray
    polys: list               # (n, 3) float64 arrays, Hammer units
    area: np.ndarray          # square units
    width: np.ndarray         # the polygon's narrowest extent, units

    @property
    def slivers(self) -> np.ndarray:
        return (self.area < SLIVER_AREA) | (self.width < SLIVER_WIDTH)


def _plane(poly: np.ndarray) -> tuple[np.ndarray, float]:
    """Newell normal (unit) and distance of a polygon."""
    nxt = np.roll(poly, -1, axis=0)
    n = np.array([((poly[:, 1] - nxt[:, 1]) * (poly[:, 2] + nxt[:, 2])).sum(),
                  ((poly[:, 2] - nxt[:, 2]) * (poly[:, 0] + nxt[:, 0])).sum(),
                  ((poly[:, 0] - nxt[:, 0]) * (poly[:, 1] + nxt[:, 1])).sum()])
    length = np.linalg.norm(n)
    if length == 0:
        return np.zeros(3), 0.0
    n = n / length
    return n, float(n @ poly.mean(axis=0))


def _canonical(n: np.ndarray, d: float) -> tuple[np.ndarray, float]:
    """The same plane facing the other way gets the same key: the first clearly non-zero component positive
    (not the largest: on a 45 degree wall x and y tie, and noise would pick either)."""
    for c in n:
        if abs(c) > 1e-3:
            return (-n, -d) if c < 0 else (n, d)
    return n, d


def _width(poly: np.ndarray, n: np.ndarray) -> float:
    """Narrowest extent of a convex polygon: the smallest, over its edges, of the farthest point from the edge."""
    best = np.inf
    for i in range(len(poly)):
        e = poly[(i + 1) % len(poly)] - poly[i]
        side = np.cross(n, e)
        length = np.linalg.norm(side)
        if length == 0:
            continue
        best = min(best, float(np.abs((poly - poly[i]) @ (side / length)).max()))
    return 0.0 if best == np.inf else best


def read_portals(path: str) -> Portals:
    with open(path, encoding="ascii", errors="replace") as f:
        lines = f.read().splitlines()
    if not lines or lines[0].strip() != "PRT1":
        raise ValueError("not a portal file")
    clusters, count = int(lines[1]), int(lines[2])
    c0, c1, polys, area, width = [], [], [], [], []
    for line in lines[3:3 + count]:
        head = line.split("(")[0].split()
        pts = np.array([[float(v) for v in m.split()] for m in re.findall(r"\(([^)]*)\)", line)], dtype=np.float64)
        n, _d = _plane(pts)
        c0.append(int(head[1])); c1.append(int(head[2]))
        polys.append(pts)
        nxt = np.roll(pts, -1, axis=0)
        area.append(0.5 * float(np.linalg.norm(np.cross(pts - pts[0], nxt - pts[0]).sum(axis=0))))
        width.append(_width(pts, n))
    return Portals(clusters, np.array(c0), np.array(c1), polys, np.array(area), np.array(width))


# ---------------------------------------------------------------- vis cost

@dataclass
class VisCost:
    seconds: np.ndarray       # per portal (both directions), thread-seconds
    flow_seconds: float       # the exact pass's wall time
    threads: int

    def share(self) -> np.ndarray:
        total = self.seconds.sum()
        return self.seconds / total if total > 0 else self.seconds


def read_costs(path: str, portals: int | None = None) -> VisCost | None:
    """<map>.viscost from hlvvis: a header line, then each portal's two directions in thread-seconds."""
    try:
        with open(path, encoding="ascii") as f:
            lines = f.read().split("\n")
        if not lines[0].startswith("hlvvis-cost 1"):
            return None
        meta = lines[1].split()
        n, threads, wall = int(meta[1]), int(meta[3]), float(meta[5])
        vals = np.array([[float(x) for x in line.split()] for line in lines[2:2 + n]], dtype=np.float64)
    except (OSError, ValueError, IndexError):
        return None
    vals = vals.reshape(-1, 2) if vals.size == 0 else vals      # (no portals: an empty table, not an error)
    if vals.shape != (n, 2) or (portals is not None and n != portals):
        return None
    return VisCost(vals.sum(axis=1), wall, threads)


def _world_solids(vmf_text: str):
    """(solid id, side planes, bounds) of the world's brushes (entity brushes don't split vis)."""
    world = vmf_text[:vmf_text.find("\nentity\n")] if "\nentity\n" in vmf_text else vmf_text
    for m in re.finditer(r'\n\tsolid\n\t\{\n\t\t"id" "(\d+)"(.*?)\n\t\}', world, re.S):
        pts = [np.array([float(v) for v in t]) for t in
               re.findall(r'"plane" "\(([-\d.e]+) ([-\d.e]+) ([-\d.e]+)\) \(([-\d.e]+) ([-\d.e]+) ([-\d.e]+)\) '
                          r'\(([-\d.e]+) ([-\d.e]+) ([-\d.e]+)\)"', m.group(2))]
        if not pts:
            continue
        planes, corners = [], []
        for t in pts:
            a, b, c = t[0:3], t[3:6], t[6:9]
            n = np.cross(c - a, b - a)
            length = np.linalg.norm(n)
            if length == 0:
                continue
            n = n / length
            planes.append(_canonical(n, float(n @ a)))
            corners += [a, b, c]
        corners = np.array(corners)
        yield m.group(1), planes, corners.min(axis=0), corners.max(axis=0)


def portal_owners(portals: Portals, vmf_path: str) -> list:
    """For each portal, the Blender object whose brush it was split along (a brush side on the same
    plane, the nearest one when several are), or None: vbsp's own splitting of open space."""
    try:
        with open(vmf_path, encoding="utf-8") as f:
            text = f.read()
        base = os.path.splitext(vmf_path)[0]
        base = base[:-len(".built")] if base.endswith(".built") else base       # (map.built.vmf -> map)
        with open(base + ".brushes.json", encoding="utf-8") as f:
            sources = json.load(f)
    except (OSError, ValueError):
        return [None] * len(portals.polys)
    by_normal: dict = {}
    for sid, planes, lo, hi in _world_solids(text):
        for n, d in planes:
            by_normal.setdefault(tuple(np.round(n, 3)), []).append((d, sid, lo, hi))
    owners = []
    for poly in portals.polys:
        n, d = _canonical(*_plane(poly))
        centre = poly.mean(axis=0)
        best, best_dist = None, np.inf
        for cand in by_normal.get(tuple(np.round(n, 3)), ()):
            cd, sid, lo, hi = cand
            if abs(cd - d) > 0.5:
                continue
            dist = float(np.linalg.norm(np.maximum(0, np.maximum(lo - centre, centre - hi))))
            if dist < best_dist:
                best, best_dist = sid, dist
        name = sources.get(best) if best is not None else None
        owners.append(re.sub(r" \(part \d+\)$", "", name) if name else None)
    return owners


def cost_by_object(cost: VisCost, owners: list) -> list[tuple[str | None, float]]:
    """(object or None for vbsp's own splits, share of the vis time), most first."""
    shares: dict = {}
    for name, s in zip(owners, cost.share()):
        shares[name] = shares.get(name, 0.0) + float(s)
    return sorted(shares.items(), key=lambda kv: -kv[1])


# ---------------------------------------------------------------- rendering load

@dataclass
class RenderLoad:
    positions: np.ndarray     # (n, 3) triangle corners, Hammer units: the world's visible faces
    value: np.ndarray         # (n,) faces in view from the part of the map each triangle is in
    clusters: int
    max_faces: int
    min_faces: int


def render_load(data: bytes) -> RenderLoad:
    """How many faces the game draws from each part of the map (its PVS), shown on the map's own faces."""
    lump = _lumps(data)
    vis = lump(4)
    if len(vis) < 4:
        raise ValueError("the map has no visibility data (Quality: Quick skips vis)")
    nclusters = struct.unpack_from("<i", vis, 0)[0]
    rowbytes = (nclusters + 7) >> 3
    leafs = lump(10)
    nleafs = len(leafs) // 32
    leaf_cluster = np.array([struct.unpack_from("<h", leafs, 32 * k + 4)[0] for k in range(nleafs)])
    first_face = np.array([struct.unpack_from("<HH", leafs, 32 * k + 20) for k in range(nleafs)])
    leaffaces = np.frombuffer(lump(16), dtype="<u2")
    faces, texinfo = lump(7), lump(6)
    nfaces = len(faces) // FACE_SIZE
    face_cluster = np.full(nfaces, -1)
    cluster_faces = [set() for _ in range(nclusters)]
    for k in range(nleafs):
        c = leaf_cluster[k]
        if c < 0 or c >= nclusters:
            continue
        for f in leaffaces[first_face[k, 0]:first_face[k, 0] + first_face[k, 1]]:
            cluster_faces[c].add(int(f))
            if face_cluster[f] < 0:
                face_cluster[f] = c
    # displacements aren't in the leaf faces: each goes in the cluster of the leaf at its middle
    nodes, planes = lump(5), lump(1)
    dispinfo_l, dispverts_l = lump(26), lump(33)
    verts_l = np.frombuffer(lump(3), dtype="<f4").reshape(-1, 3)
    edges_l = np.frombuffer(lump(12), dtype="<u2").reshape(-1, 2)
    surfedges_l = np.frombuffer(lump(13), dtype="<i4")

    def leaf_at(p) -> int:
        node = 0
        for _ in range(4096):
            plane, c0, c1 = struct.unpack_from("<3i", nodes, 32 * node)
            nx, ny, nz, dist = struct.unpack_from("<4f", planes, 20 * plane)
            child = c0 if nx * p[0] + ny * p[1] + nz * p[2] - dist >= 0 else c1
            if child < 0:
                return -1 - child
            node = child
        return -1
    if len(nodes) >= 32:
        for f in range(nfaces):
            b = FACE_SIZE * f
            first_edge, num_edges, _ti, di = struct.unpack_from("<ihhh", faces, b + 4)
            if di < 0 or num_edges != 4 or face_cluster[f] >= 0:
                continue
            se = surfedges_l[first_edge:first_edge + num_edges]
            corners = verts_l[np.where(se >= 0, edges_l[np.abs(se), 0], edges_l[np.abs(se), 1])].astype(np.float64)
            pos, _flat, _tris = _displacement(corners, dispinfo_l, dispverts_l, di)
            lf = leaf_at(pos.mean(axis=0) + np.array([0.0, 0.0, 1.0]))
            c = leaf_cluster[lf] if 0 <= lf < nleafs else -1
            if 0 <= c < nclusters:
                cluster_faces[c].add(f)
                face_cluster[f] = c
    # what the game draws from a cluster: every face of the clusters it sees, each once (a face in leaves of
    # several clusters is drawn once a frame, not once per cluster)
    members = [np.fromiter(s, dtype=np.int64, count=len(s)) for s in cluster_faces]
    load = np.zeros(nclusters, dtype=np.int64)
    for c in range(nclusters):
        off = struct.unpack_from("<i", vis, 4 + 8 * c)[0]
        row = bytearray(rowbytes)
        i, j = off, 0
        while j < rowbytes:                      # Quake's run-length decoding
            if vis[i]:
                row[j] = vis[i]; i += 1; j += 1
            else:
                j += vis[i + 1]; i += 2
        bits = np.unpackbits(np.frombuffer(bytes(row), np.uint8), bitorder="little")[:nclusters].astype(bool)
        seen = [members[k] for k in np.nonzero(bits)[0] if len(members[k])]
        load[c] = len(np.unique(np.concatenate(seen))) if seen else 0
    verts = np.frombuffer(lump(3), dtype="<f4").reshape(-1, 3)
    edges = np.frombuffer(lump(12), dtype="<u2").reshape(-1, 2)
    surfedges = np.frombuffer(lump(13), dtype="<i4")
    dispinfo, dispverts = lump(26), lump(33)
    positions, values = [], []
    for f in range(nfaces):
        c = face_cluster[f]
        if c < 0:
            continue
        b = FACE_SIZE * f
        first_edge, num_edges, ti, di = struct.unpack_from("<ihhh", faces, b + 4)
        if num_edges < 3 or ti < 0:
            continue
        if struct.unpack_from("<i", texinfo, TEXINFO_SIZE * ti + 64)[0] & (SURF_SKY | SURF_SKY2D | SURF_NODRAW):
            continue
        se = surfedges[first_edge:first_edge + num_edges]
        corners = verts[np.where(se >= 0, edges[np.abs(se), 0], edges[np.abs(se), 1])].astype(np.float64)
        if di >= 0 and num_edges == 4:
            pos, _flat, tris = _displacement(corners, dispinfo, dispverts, di)
        else:
            pos, tris = corners, np.array([(0, i, i + 1) for i in range(1, num_edges - 1)], dtype=np.int64)
        tri_pos = pos[tris].reshape(-1, 3)
        positions.append(tri_pos)
        values.append(np.full(len(tri_pos), load[c]))
    used = load[np.unique(face_cluster[face_cluster >= 0])] if (face_cluster >= 0).any() else load
    return RenderLoad(np.concatenate(positions) if positions else np.zeros((0, 3)),
                      np.concatenate(values) if values else np.zeros(0, np.int64),
                      nclusters, int(used.max()) if len(used) else 0, int(used.min()) if len(used) else 0)
