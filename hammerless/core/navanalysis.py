"""Facts about a real nav mesh (read from the game's .nav file): what survivors can
reach from the start room, where the path to the end room breaks, islands zombies
can spawn on but never leave, and which links are drops or jump-ups."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .navfile import NavMesh

PLAYER_START, CHECKPOINT, OBSCURED, FINALE = 0x80, 0x800, 0x1000, 0x40
EMPTY, NO_MOBS = 0x2, 0x2000          # no wandering commons / no hordes from here
STEP_HEIGHT = 18.0            # nav.h StepHeight: above this a link needs a jump
JUMP_DOWN_MIN = 64.0          # JumpCrouchHeight: L4D2 only makes drop-down links for drops above this
EDGE_GAP = 26.0               # neighbouring areas can be one node step (25) apart at a ledge


@dataclass
class Link:
    a: int
    b: int
    kind: str                 # "walk" | "drop" (one way down) | "jump" (up more than a step) | "oneway"
    dz: float


@dataclass
class NavReport:
    total: int = 0
    start: list[int] = field(default_factory=list)
    end: list[int] = field(default_factory=list)
    reachable: set[int] = field(default_factory=set)       # from the start room, following links
    end_reached: bool = False
    break_area: int | None = None                          # reached area closest to the end room
    islands: list[list[int]] = field(default_factory=list)  # groups not connected to the start room
    links: list[Link] = field(default_factory=list)
    distance: dict[int, float] = field(default_factory=dict)  # walking distance from the start room
    dead_ledges: list[tuple[tuple[float, float, float], float]] = field(default_factory=list)  # (spot, height)

    @property
    def unreachable(self) -> int:
        return self.total - len(self.reachable)


def _edge_z(mesh_by_id, a, b) -> float:
    """Height change stepping from area a to area b, measured where they meet."""
    A, B = mesh_by_id[a], mesh_by_id[b]
    cx = min(max(B.centre[0], A.nw[0]), A.se[0])
    cy = min(max(B.centre[1], A.nw[1]), A.se[1])
    return B.z_at(cx, cy) - A.z_at(cx, cy)


def analyse(mesh: NavMesh) -> NavReport:
    by_id = mesh.by_id()
    rep = NavReport(total=len(mesh.areas))
    out: dict[int, set[int]] = {a.id: {b for d in a.connections for b in d if b in by_id} for a in mesh.areas}
    for lad in mesh.ladders:     # ladders connect their bottom area and top areas both ways
        tops = [t for t in (lad.top_forward, lad.top_left, lad.top_right, lad.top_behind) if t in by_id]
        if lad.bottom_area in by_id:
            for t in tops:
                out[lad.bottom_area].add(t)
                out[t].add(lad.bottom_area)
    for a in mesh.areas:
        if a.spawn_attributes & PLAYER_START:
            rep.start.append(a.id)
        elif a.spawn_attributes & (CHECKPOINT | FINALE):
            rep.end.append(a.id)

    for a, targets in out.items():
        for b in targets:
            dz = _edge_z(by_id, a, b)
            back = a in out[b]
            if dz < -STEP_HEIGHT and not back:
                kind = "drop"
            elif dz > STEP_HEIGHT:
                kind = "jump"
            elif not back:
                kind = "oneway"
            else:
                kind = "walk"
            rep.links.append(Link(a, b, kind, dz))

    # walk from the start room (breadth first, remembering distance)
    queue = deque(rep.start)
    rep.distance = {s: 0.0 for s in rep.start}
    while queue:
        a = queue.popleft()
        for b in out[a]:
            if b not in rep.distance:
                rep.distance[b] = rep.distance[a] + math.dist(by_id[a].centre, by_id[b].centre)
                queue.append(b)
    rep.reachable = set(rep.distance)
    rep.end_reached = any(e in rep.reachable for e in rep.end)
    if rep.end and rep.reachable and not rep.end_reached:
        goal = tuple(sum(by_id[e].centre[i] for e in rep.end) / len(rep.end) for i in range(3))
        rep.break_area = min(rep.reachable, key=lambda i: math.dist(by_id[i].centre, goal))

    # islands: connected groups (ignoring direction) that the start room can't reach
    undirected: dict[int, set[int]] = {i: set() for i in out}
    for a, targets in out.items():
        for b in targets:
            undirected[a].add(b)
            undirected[b].add(a)
    seen = set(rep.reachable)
    for a in by_id:
        if a in seen:
            continue
        group, q = [], deque([a])
        seen.add(a)
        while q:
            x = q.popleft()
            group.append(x)
            for y in undirected[x]:
                if y not in seen:
                    seen.add(y)
                    q.append(y)
        rep.islands.append(group)
    rep.islands.sort(key=len, reverse=True)
    rep.dead_ledges = find_dead_ledges(mesh, out)
    return rep


def find_dead_ledges(mesh: NavMesh, out: dict[int, set[int]]):
    """Places where two neighbouring areas differ in height by 19-64 units and aren't linked:
    L4D2 makes neither a step (18 or less) nor a drop-down (over 64) there, so bots, zombies and
    the Director treat it as the end of the path. Returns (spot on the upper edge, height) per
    ledge, one per 100-unit stretch."""
    cell = 128.0
    grid: dict[tuple[int, int], list] = {}
    for a in mesh.areas:
        for gx in range(int((a.nw[0] - EDGE_GAP) // cell), int((a.se[0] + EDGE_GAP) // cell) + 1):
            for gy in range(int((a.nw[1] - EDGE_GAP) // cell), int((a.se[1] + EDGE_GAP) // cell) + 1):
                grid.setdefault((gx, gy), []).append(a)
    found, seen_pairs = [], set()
    for cellset in grid.values():
        for a in cellset:
            for b in cellset:
                if a.id >= b.id or (a.id, b.id) in seen_pairs:
                    continue
                seen_pairs.add((a.id, b.id))
                spot = _shared_edge(a, b)
                if spot is None:
                    continue
                x, y = spot
                za, zb = a.z_at(x, y), b.z_at(x, y)
                dz = abs(za - zb)
                if STEP_HEIGHT < dz <= JUMP_DOWN_MIN and b.id not in out[a.id] and a.id not in out[b.id]:
                    found.append(((x, y, max(za, zb)), dz))
    kept = []
    for spot, dz in sorted(found, key=lambda f: (f[0][0], f[0][1])):
        if all(math.dist(spot, k[0]) > 100 for k in kept):
            kept.append((spot, dz))
    return kept


def _shared_edge(a, b):
    """Midpoint of the stretch where two areas face each other across a small gap, or None."""
    for lo, hi in ((a, b), (b, a)):
        if 0 <= hi.nw[0] - lo.se[0] <= EDGE_GAP:          # lo is west of hi
            y0, y1 = max(lo.nw[1], hi.nw[1]), min(lo.se[1], hi.se[1])
            if y1 - y0 >= 20:
                return ((lo.se[0] + hi.nw[0]) / 2, (y0 + y1) / 2)
        if 0 <= hi.nw[1] - lo.se[1] <= EDGE_GAP:          # lo is north of hi
            x0, x1 = max(lo.nw[0], hi.nw[0]), min(lo.se[0], hi.se[0])
            if x1 - x0 >= 20:
                return ((x0 + x1) / 2, (lo.se[1] + hi.nw[1]) / 2)
    return None
