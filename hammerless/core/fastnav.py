"""Optional native speed-up for the nav generator (native/hlnav.c, built to _hlnav.dll).

Gives bit-identical results to the Python code in collision.py / navgen.py (checked by
comparing whole generated meshes); when the DLL is missing or can't load (another OS),
everything runs in Python.
"""
from __future__ import annotations

import ctypes
import os

_DLL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hlnav.dll")
import threading
LOCK = threading.RLock()     # the DLL keeps one loaded world (and the analysis' slots): one user at a time
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
                lib.hl_world_stash.argtypes = [ctypes.c_int] * 3
                lib.hl_world_restore.argtypes = [ctypes.c_int]
                lib.hl_world_drop.argtypes = [ctypes.c_int]
                lib.hl_memo_invalidate.restype = ctypes.c_int
                lib.hl_memo_hits.restype = ctypes.c_longlong
                lib.hl_memo_misses.restype = ctypes.c_longlong
                _lib = lib
            except OSError:
                _lib = False
    return bool(_lib)


def check_memory() -> None:
    """After native work: raise if one of its allocations failed (the DLL has already freed its
    state and stopped; its sweep memory is gone too)."""
    global _memo_keys
    if _lib and _lib.hl_oom():
        _memo_keys = None
        raise MemoryError("not enough memory for the nav mesh (close other programs, or check the map for "
                          "something huge: a leak to the void, a giant terrain)")


def _arr(ctype, values):
    return (ctype * max(1, len(values)))(*values)


_current = None          # the CollisionWorld the DLL holds
_memo_keys = None        # brushes the DLL's sweep memory was made with
MEMO_MAX_CHANGED = 2000  # more changed brushes than this: start the memory afresh
MEMO_CEILING = 600000    # entries (about 660 bytes each): above this the memory starts afresh
MEMO_MAX_TESTS = 5e7     # entries x changed brushes: dropping entries one by one would take longer than
                         # starting afresh (it runs on one thread, holding the lock)
last_memo = {}           # what the last load did to the memory (for tests and the status line)


def _update_memo(world, bounds, sides7, bevel, flags, first, count) -> None:
    """Keep the DLL's sweep memory valid for this world: drop results near brushes that changed.
    Brushes are compared by their exact planes. The ones that stayed must keep their order (trace
    results can depend on brush order on exact ties), otherwise the memory starts afresh."""
    global _memo_keys
    keys = []
    for i in range(len(world.brushes)):
        f, c = first[i], count[i]
        keys.append((tuple(bounds[6 * i:6 * i + 6]), tuple(sides7[7 * f:7 * (f + c)]),
                     tuple(bevel[f:f + c]), tuple(flags[f:f + c])))
    old, _memo_keys = _memo_keys, keys
    if old is None:
        _lib.hl_memo_clear()
        last_memo.update(mode="fresh", changed=len(keys), dropped=0)
        return
    from collections import Counter
    co, cn = Counter(old), Counter(keys)
    gone, added = co - cn, cn - co
    changed = list(gone.elements()) + list(added.elements())
    if not changed:
        last_memo.update(mode="reuse", changed=0, dropped=0)
        return

    def stayed(seq, extra):
        extra = Counter(extra)
        out = []
        for k in seq:
            if extra[k]:
                extra[k] -= 1
            else:
                out.append(k)
        return out
    if len(changed) > MEMO_MAX_CHANGED or stayed(old, gone) != stayed(keys, added):
        _lib.hl_memo_clear()
        last_memo.update(mode="fresh", changed=len(changed), dropped=0)
        return
    size = _lib.hl_memo_size()
    # (old positions pile up across edits; the cap follows the current map, not the biggest one this
    # session: one huge build used to let it grow to a gigabyte, and switching scenes then stalled)
    if size > min(MEMO_CEILING, max(200000, 3 * _last_nodes[0])) or size * len(changed) > MEMO_MAX_TESTS:
        _lib.hl_memo_clear()
        last_memo.update(mode="fresh", changed=len(changed), dropped=0)
        return
    boxes = [v for k in changed for v in k[0]]
    dropped = _lib.hl_memo_invalidate(ctypes.c_int(len(changed)), _arr(ctypes.c_double, boxes))
    last_memo.update(mode="update", changed=len(changed), dropped=dropped)


class _Tracer:
    """CollisionWorld.trace_hull done by the DLL (same results; for the steps after sampling)."""

    def __init__(self):
        D = ctypes.c_double
        self.buf = [(D * 3)(), (D * 3)(), (D * 3)(), (D * 3)()]
        self.out = (D * 10)()
        self.fn = _lib.hl_trace
        self.fn.restype = None
        self.args = [ctypes.byref(b) for b in self.buf] + [ctypes.byref(self.out)]

    def __call__(self, start, end, mins, maxs):
        from .collision import DISPLACEMENT, Trace
        a, b, c, d = self.buf
        a[0], a[1], a[2] = start[0], start[1], start[2]
        b[0], b[1], b[2] = end[0], end[1], end[2]
        c[0], c[1], c[2] = mins[0], mins[1], mins[2]
        d[0], d[1], d[2] = maxs[0], maxs[1], maxs[2]
        self.fn(*self.args)
        o = self.out
        tr = Trace(o[0], (o[1], o[2], o[3]), (o[4], o[5], o[6]), bool(o[7]), bool(o[8]))
        flags = int(o[9])
        if flags & 1:
            tr.material = "tools/toolsskybox"
        elif flags & 2:
            tr.material = DISPLACEMENT
        return tr


def load_world(world, memo: bool = True) -> None:
    """Hand a CollisionWorld's brushes and brush grid to the DLL (its traces then go there too).
    memo=False: a world for something else (the nav analysis' slots): the sweep memory and the
    'current world' bookkeeping are left alone."""
    global _current
    if memo:
        if _current is not None and _current is not world:
            _current.native_trace = None
        _current = world
        world.native_trace = None          # not while loading
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
    if memo:
        _update_memo(world, bounds, sides7, bevel, flags, first, count)
    _lib.hl_world(I(len(world.brushes)), _arr(D, bounds), _arr(I, first), _arr(I, count), I(len(bevel)),
                  _arr(D, sides7), _arr(I, bevel), _arr(I, flags), D(world.CELL), I(cx0), I(cy0), I(w), I(h),
                  _arr(I, start), _arr(I, cnt), I(len(ids)), _arr(I, ids))
    if memo:
        world.native_trace = _Tracer()


NAV_STASH_SLOT = 1023


def stash_current():
    """Set the nav generator's world aside (the nav analysis loads its own worlds)."""
    if _current is None:
        return None
    _lib.hl_world_stash(NAV_STASH_SLOT, 0, 1)
    return _current


def restore_current(prev) -> None:
    if prev is not None:
        _lib.hl_world_restore(NAV_STASH_SLOT)


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


_last_nodes = [0]          # nodes the last sampling made (the memo's useful size)


def node_count() -> int:
    n = _lib.hl_node_count()
    _last_nodes[0] = n if n else _last_nodes[0]
    return n


def collect(world):
    """The sampled nodes as navgen.Node objects."""
    from .navgen import Node
    n = _lib.hl_node_count()
    d14 = (ctypes.c_double * (14 * max(1, n)))()
    i15 = (ctypes.c_int * (15 * max(1, n)))()
    _lib.hl_nodes(d14, i15)
    D, K = list(d14), list(i15)                # one conversion; slicing ctypes arrays is slow
    nodes = []
    for i in range(n):
        d = D[14 * i:14 * i + 14]
        k = K[15 * i:15 * i + 15]
        node = Node((d[0], d[1], d[2]), (d[3], d[4], d[5]), None, i + 1)
        node.obstacle = d[6:10]
        node.ground = d[10:14]
        node.attributes = k[5]
        node.crouch = [bool(v) for v in k[6:10]]
        node.blocked = [bool(v) for v in k[10:14]]
        node.on_disp = bool(k[14])
        node.crouch_checked = node.cliff_checked = True
        node.visited = 15
        nodes.append(node)
    for i in range(n):
        k = K[15 * i:15 * i + 5]
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


AREA_STAGES = ("build", "connect", "mark jump", "merge", "overhangs", "square up", "stairs",
               "remove jump areas", "corners", "connections")


def run_areas(upto: int = len(AREA_STAGES)):
    """The area stages (navareas.Generator from create_areas through fix_connections) on the
    DLL's nodes. Returns (areas, last seq): per area in list order (nw, se, ne_z, sw_z, seq,
    attributes, node indices, connect[4], incoming[4]) with links as list positions (-1: an
    area no longer listed)."""
    n = _lib.hl_areas_run(ctypes.c_int(upto))
    d8 = (ctypes.c_double * (8 * max(1, n)))()
    i6 = (ctypes.c_int * (6 * max(1, n)))()
    cn8 = (ctypes.c_int * (8 * max(1, n)))()
    need = _lib.hl_areas_get(d8, i6, cn8, (ctypes.c_int * 1)(), 0)
    links = (ctypes.c_int * max(1, need))()
    _lib.hl_areas_get(d8, i6, cn8, links, need)
    D, I, C, L = list(d8), list(i6), list(cn8), list(links)
    out, li = [], 0
    for k in range(n):
        lists = []
        for c in C[8 * k:8 * k + 8]:
            lists.append(L[li:li + c])
            li += c
        out.append(((D[8 * k], D[8 * k + 1], D[8 * k + 2]), (D[8 * k + 3], D[8 * k + 4], D[8 * k + 5]),
                    D[8 * k + 6], D[8 * k + 7], I[6 * k], I[6 * k + 1], tuple(I[6 * k + 2:6 * k + 6]),
                    lists[:4], lists[4:]))
    return out, _lib.hl_areas_seq()
