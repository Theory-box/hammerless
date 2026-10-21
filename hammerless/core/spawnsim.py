"""Where the AI Director puts its wandering common infected: a simulation from a seed, for the nav view.

The Director's rule (read from server.dll, checked against in-game counts: docs/dev/ZOMBIE_SPAWNING.md):
- a budget of round(0.03 x the escape route's length) wanderers (z_wandering_density);
- each one goes to a nav area picked at random BY COUNT among ALL areas (a big street area is no likelier than a
  small roof area);
- it's lost if that area can't hold wanderers (EMPTY, the safe rooms, no route to the end);
- of the rest, the ones 550..1500 units from the survivors and out of their sight appear right away (at most 30
  alive); the others wait until the survivors come near.
Checked: as built the user's map predicts 30 at the start with 7 on the street (measured 30 / 4-6); with the roofs
marked no-spawn, 15 (measured 12-15). One measured case doesn't fit (a full-map nav with roofs marked: predicted
~10, measured 0-1), so it's a guide, not the game.
"""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field

EMPTY, PLAYER_START, CHECKPOINT = 0x2, 0x80, 0x800
DENSITY = 0.03            # z_wandering_density
SAFETY_RANGE = 550.0      # z_spawn_safety_range: never closer to a survivor
SPAWN_RANGE = 1500.0      # z_spawn_range: appear only this close
COMMON_LIMIT = 30         # z_common_limit


@dataclass
class Zombie:
    pos: tuple[float, float, float]
    kind: str             # "now": appears with the survivors there; "later": waits; "lost": its area can't hold one
    area: int


@dataclass
class SpawnPlan:
    zombies: list[Zombie] = field(default_factory=list)
    budget: int = 0
    route: float = 0.0
    survivors: tuple[float, float, float] | None = None
    why_lost: dict = field(default_factory=dict)      # reason -> count

    def count(self, kind: str) -> int:
        return sum(1 for z in self.zombies if z.kind == kind)


def _walk(mesh, sources, edges, centre):
    d = {s: 0.0 for s in sources}
    heap = [(0.0, s) for s in sources]
    while heap:
        x, a = heapq.heappop(heap)
        if x > d.get(a, math.inf):
            continue
        for b in edges.get(a, ()):
            y = x + math.dist(centre[a], centre[b])
            if y < d.get(b, math.inf):
                d[b] = y
                heapq.heappush(heap, (y, b))
    return d


def simulate(mesh, seed: int = 1, progress: float = 0.0, density_scale: float = 1.0,
             common_limit: int = COMMON_LIMIT) -> SpawnPlan:
    """The wanderers for one seed, with the survivors `progress` (0..1) of the way along the route. common_limit: the
    most commons alive at once (z_common_limit 30, or the map's Director CommonLimit); it doesn't add wanderers."""
    plan = SpawnPlan()
    by = mesh.by_id()
    centre = {a.id: a.centre for a in mesh.areas}
    edges = {a.id: [i for d in a.connections for i in d if i in by] for a in mesh.areas}
    for lad in mesh.ladders:                       # (ladders: both ways between their ends)
        ends = [i for i in (lad.bottom_area, lad.top_forward, lad.top_left, lad.top_right, lad.top_behind) if i in by]
        for i in ends:
            edges[i] = edges[i] + [j for j in ends if j != i]
    # Elevator travel is runtime connectivity, not an ordinary serialized walk link.
    for a, b in mesh.elevator_links:
        if a in by and b in by and b not in edges[a]:
            edges[a].append(b)
    start = [a.id for a in mesh.areas if a.spawn_attributes & PLAYER_START]
    end = [a.id for a in mesh.areas if a.spawn_attributes & CHECKPOINT and not a.spawn_attributes & PLAYER_START]
    if not start or not end or not mesh.areas:
        return plan
    from_start = _walk(mesh, start, edges, centre)
    # the end room: the far checkpoint areas (Valve marks some areas by the start door CHECKPOINT too)
    far = max((from_start[e] for e in end if e in from_start), default=0.0)
    end = [e for e in end if from_start.get(e, -1.0) >= 0.5 * far] or end
    back = {a: [] for a in by}
    for a, out in edges.items():
        for b in out:
            back[b].append(a)
    to_end = _walk(mesh, end, back, centre)
    plan.route = min((from_start.get(e, math.inf) for e in end), default=math.inf)
    if not math.isfinite(plan.route):
        return plan
    plan.budget = round(DENSITY * density_scale * plan.route)
    # where the survivors stand: the start room, or the area that far along the route
    if progress <= 0:
        sx = [centre[s] for s in start]
        here = tuple(sum(p[i] for p in sx) / len(sx) for i in range(3))
        seen_from = start
    else:
        goal = progress * plan.route
        at = min((a for a in from_start if a in to_end), key=lambda a: abs(from_start[a] - goal))
        here, seen_from = centre[at], [at]
    plan.survivors = here
    seen = set()
    for s in seen_from:
        seen |= {i for i, _k in (by[s].visible or [])}
    rng = random.Random(seed)
    areas = mesh.areas
    now = 0
    for _ in range(plan.budget):
        a = areas[rng.randrange(len(areas))]
        (x0, y0), (x1, y1) = a.nw[:2], a.se[:2]
        x, y = rng.uniform(min(x0, x1), max(x0, x1)), rng.uniform(min(y0, y1), max(y0, y1))
        pos = (x, y, a.z_at(x, y))
        reason = ("start room" if a.spawn_attributes & PLAYER_START else
                  "end room" if a.spawn_attributes & CHECKPOINT else
                  "no-spawn mark (roofs, ledges, your boxes)" if a.spawn_attributes & EMPTY else
                  "no route to the end" if a.id not in to_end else None)
        if reason:
            plan.zombies.append(Zombie(pos, "lost", a.id))
            plan.why_lost[reason] = plan.why_lost.get(reason, 0) + 1
            continue
        d = math.dist(here, pos)
        if SAFETY_RANGE <= d <= SPAWN_RANGE and a.id not in seen and now < common_limit:
            plan.zombies.append(Zombie(pos, "now", a.id))
            now += 1
        else:
            plan.zombies.append(Zombie(pos, "later", a.id))
    return plan
