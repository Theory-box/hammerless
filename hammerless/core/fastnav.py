"""Optional native speed-up for the nav generator (native/hlnav.c, built to _hlnav.dll).

Gives bit-identical results to the Python code in collision.py / navgen.py (checked by
comparing whole generated meshes); when the DLL is missing or can't load (another OS),
everything runs in Python.
"""
from __future__ import annotations

import ctypes
import math
import os

_DLL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hlnav.dll")
_lib = None


def available() -> bool:
    global _lib
    if _lib is None:
        if os.environ.get("HAMMERLESS_NO_NATIVE") or not os.path.exists(_DLL):
            _lib = False
        else:
            try:
                lib = ctypes.CDLL(_DLL)
                lib.hl_sample.restype = ctypes.c_int
                lib.hl_add_seed.restype = ctypes.c_int
                lib.hl_add_seed.argtypes = [ctypes.c_double] * 3
                lib.hl_trace_count.restype = ctypes.c_longlong
                lib.hl_sample_from.restype = ctypes.c_int
                lib.hl_sample_from.argtypes = [ctypes.c_double] * 6 + [ctypes.c_int]
                lib.hl_has_node.restype = ctypes.c_int
                lib.hl_has_node.argtypes = [ctypes.c_double] * 3
                lib.hl_node_count.restype = ctypes.c_int
                lib.hl_create_areas.restype = ctypes.c_int
                _lib = lib
            except OSError:
                _lib = False
    return bool(_lib)


def _arr(ctype, values):
    return (ctype * max(1, len(values)))(*values)


def load_world(world) -> None:
    """Hand a CollisionWorld's brushes and brush grid to the DLL."""
    from .collision import DISPLACEMENT, SKY
    bounds, first, count, sides7, bevel, flags = [], [], [], [], [], []
    for b in world.brushes:
        first.append(len(bevel))
        count.append(len(b.sides))
        bounds += [b.mins[0], b.mins[1], b.mins[2], b.maxs[0], b.maxs[1], b.maxs[2]]
        for s in b.sides:
            n = s.normal
            sides7 += [n[0], n[1], n[2], s.dist, abs(n[0]), abs(n[1]), abs(n[2])]
            bevel.append(1 if s.bevel else 0)
            flags.append((1 if s.material in SKY else 0) | (2 if s.material == DISPLACEMENT else 0))
    keys = list(world.grid)
    if keys:
        cx0, cx1 = min(k[0] for k in keys), max(k[0] for k in keys)
        cy0, cy1 = min(k[1] for k in keys), max(k[1] for k in keys)
    else:
        cx0 = cx1 = cy0 = cy1 = 0
    w, h = cx1 - cx0 + 1, cy1 - cy0 + 1
    start, cnt, ids = [0] * (w * h), [0] * (w * h), []
    for gx in range(w):
        for gy in range(h):
            lst = world.grid.get((cx0 + gx, cy0 + gy), [])
            start[gx * h + gy] = len(ids)
            cnt[gx * h + gy] = len(lst)
            ids += lst
    D, I = ctypes.c_double, ctypes.c_int
    _lib.hl_world(I(len(world.brushes)), _arr(D, bounds), _arr(I, first), _arr(I, count), I(len(bevel)),
                  _arr(D, sides7), _arr(I, bevel), _arr(I, flags), D(world.CELL), I(cx0), I(cy0), I(w), I(h),
                  _arr(I, start), _arr(I, cnt), I(len(ids)), _arr(I, ids))


def sample(world, raw_seeds, max_nodes: int = 500000):
    """Run the flood fill natively. Returns navgen.Node objects linked like Sampler.sample makes them."""
    start(world, raw_seeds, max_nodes)
    return collect(world)


def start(world, raw_seeds, max_nodes: int = 500000) -> None:
    """Flood fill from the seeds (the nodes stay in the DLL until collect())."""
    load_world(world)
    _lib.hl_reset()
    for p in raw_seeds:
        _lib.hl_add_seed(p[0], p[1], p[2])
    _lib.hl_sample(ctypes.c_int(max_nodes))


def sample_from(pos, normal, max_nodes: int = 500000) -> None:
    _lib.hl_sample_from(pos[0], pos[1], pos[2], normal[0], normal[1], normal[2], max_nodes)


def has_node(pos) -> bool:
    return bool(_lib.hl_has_node(pos[0], pos[1], pos[2]))


def node_count() -> int:
    return _lib.hl_node_count()


def collect(world):
    """The sampled nodes as navgen.Node objects."""
    from .navgen import Node
    n = _lib.hl_node_count()
    d14 = (ctypes.c_double * (14 * max(1, n)))()
    i15 = (ctypes.c_int * (15 * max(1, n)))()
    _lib.hl_nodes(d14, i15)
    nodes = []
    for i in range(n):
        d = d14[14 * i:14 * i + 14]
        k = i15[15 * i:15 * i + 15]
        node = Node((d[0], d[1], d[2]), (d[3], d[4], d[5]), None, i + 1)
        node.obstacle = [d[6], d[7], d[8], d[9]]
        node.ground = [d[10], d[11], d[12], d[13]]
        node.attributes = k[5]
        node.crouch = [bool(v) for v in k[6:10]]
        node.blocked = [bool(v) for v in k[10:14]]
        node.on_disp = bool(k[14])
        node.crouch_checked = node.cliff_checked = True
        node.visited = 15
        nodes.append(node)
    for i in range(n):
        k = i15[15 * i:15 * i + 5]
        nodes[i].to = [nodes[j] if j >= 0 else None for j in k[:4]]
        nodes[i].parent = nodes[k[4]] if k[4] >= 0 else None
    world.traces += int(_lib.hl_trace_count())
    return nodes


def create_areas(world, count: int):
    """CreateNavAreasFromNodes on the DLL's nodes (which must be the ones collect() returned, unchanged):
    the areas to build, in order, as (node index, width, height)."""
    cap = max(1, count)
    while True:
        out = (ctypes.c_int * (3 * cap))()
        before = int(_lib.hl_trace_count())
        n = _lib.hl_create_areas(out, ctypes.c_int(cap))
        if n <= cap:
            world.traces += int(_lib.hl_trace_count()) - before
            return [(out[3 * i], out[3 * i + 1], out[3 * i + 2]) for i in range(n)]
        cap = n
