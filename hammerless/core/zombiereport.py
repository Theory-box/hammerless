"""Why a map has few common infected, in map terms: findings for the Problems list and a per-area "hidden spawn
spots ahead" score for the nav view.

Built on the Director's rules measured in game on 2026-10-11 (docs/dev/ZOMBIE_SPAWNING.md):
- wandering commons: a budget of ~0.03 per unit of route, handed to nav areas picked at random by count; an area that
  can't hold them (EMPTY, safe rooms, no route to the end) wastes its share;
- they appear 550..1500 units from the survivors and out of their sight, so a stretch of route with nothing hidden
  AHEAD of the survivors stays empty in front of them (measured: 1-8 hidden areas ahead on a long straight street
  vs 44-118 at the start; spawning picked up again at the bend);
- hordes come every MobSpawnMinTime..MaxTime seconds (90..180 by default): on a short route a run-through ends before
  the first one (measured: no horde in a 172 s walk of a 5182-unit route).
"""
from __future__ import annotations

import heapq
import math

EMPTY, PLAYER_START, CHECKPOINT = 0x2, 0x80, 0x800
SAFETY_RANGE, SPAWN_RANGE = 550.0, 1500.0
DEAD_AHEAD = 10            # hidden spawn areas ahead below this: a dead zone
RUN_SPEED = 220.0          # survivors' run speed (units / s); a fighting walk is about half
VALVE_ROUTES = "Valve's maps run 10,000-16,000 units (c1m1_hotel: 16,000)"


def _visible(by, aid, depth=0) -> set:
    a = by[aid]
    out = {i for i, _k in (a.visible or [])}
    if a.inherit_visibility and a.inherit_visibility in by and depth < 4:
        out |= _visible(by, a.inherit_visibility, depth + 1)
    return out


def _walk(sources, edges, centre):
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


class ZombieReport:
    def __init__(self):
        self.rows: list[tuple[str, str, tuple | None]] = []   # (severity, text, location or None)
        self.ahead: dict[int, int] = {}                        # area -> hidden spawn areas ahead of survivors there
        self.route = 0.0
        self.budget = 0
        self.analyzed = False


def analyse(mesh, horde_min: float = 90.0, horde_max: float = 180.0, hordes_off: bool = False,
            wanderers_off: bool = False) -> ZombieReport:
    r = ZombieReport()
    if not mesh.areas:
        return r
    by = mesh.by_id()
    centre = {a.id: a.centre for a in mesh.areas}
    edges = {a.id: [i for d in a.connections for i in d if i in by] for a in mesh.areas}
    for lad in mesh.ladders:
        ends = [i for i in (lad.bottom_area, lad.top_forward, lad.top_left, lad.top_right, lad.top_behind) if i in by]
        for i in ends:
            edges[i] = edges[i] + [j for j in ends if j != i]
    start = [a.id for a in mesh.areas if a.spawn_attributes & PLAYER_START]
    end = [a.id for a in mesh.areas if a.spawn_attributes & CHECKPOINT and not a.spawn_attributes & PLAYER_START]
    if not start or not end:
        return r                                   # (the nav report already says the safe rooms are missing)
    fs = _walk(start, edges, centre)
    far = max((fs[e] for e in end if e in fs), default=0.0)
    end = [e for e in end if fs.get(e, -1.0) >= 0.5 * far] or end
    back = {a: [] for a in by}
    for a, out in edges.items():
        for b in out:
            back[b].append(a)
    te = _walk(end, back, centre)
    r.route = min((fs.get(e, math.inf) for e in end), default=math.inf)
    if not math.isfinite(r.route):
        return r                                   # (broken path: the nav report says so)
    r.budget = round(0.03 * r.route)
    valid = [a.id for a in mesh.areas if not a.spawn_attributes & (EMPTY | PLAYER_START | CHECKPOINT) and a.id in te]

    # 1. the budget lost to areas that can't hold wanderers (picked by count, not size)
    if not wanderers_off:
        lost = len(mesh.areas) - len(valid)
        share = lost / len(mesh.areas)
        if share > 0.40:          # (Valve's maps lose 30-35% and are full; the user's roofs map lost 41%)
            roofs = sum(1 for a in mesh.areas if a.spawn_attributes & EMPTY)
            r.rows.append(("WARNING", f"{share:.0%} of the wandering zombies are handed to nav areas that can't hold "
                           f"them ({roofs} no-spawn areas: roofs, ledges, your no-spawn boxes; plus safe rooms and "
                           "spots with no way to the end), so they never appear. Cut those places out of the nav "
                           "instead (Control Volume, Not inside, Nav Mesh): the game shares zombies out by nav area "
                           "count", None))

    # 2. a short route: a small budget for the whole map
    if not wanderers_off and r.route < 8000:
        r.rows.append(("INFO", f"Route {r.route:,.0f} units start to end: about {r.budget} wandering zombies for the "
                       f"whole map (30 at most alive at once). {VALVE_ROUTES}. A longer or winding route gets more",
                       None))

    # 3. hordes: does a run-through even reach the first one?
    run_fast, run_slow = r.route / RUN_SPEED, r.route / (RUN_SPEED / 2)
    if hordes_off:
        r.rows.append(("INFO", "Random hordes are off (Director panel: No Random Hordes): only your own triggers "
                       "start hordes", None))
    elif horde_min > run_slow:
        r.rows.append(("WARNING", f"No horde in a normal run-through: the first horde comes {horde_min:.0f}-"
                       f"{horde_max:.0f} s in, and the route takes about {run_fast:.0f}-{run_slow:.0f} s. Lower "
                       "'Horde Every' in the Director panel (e.g. 30-60 s), or start one with a trigger", None))

    # 4. dead zones: stretches of route with nothing hidden ahead of the survivors
    r.analyzed = any(a.visible for a in mesh.areas)
    if wanderers_off:
        return r
    if not r.analyzed:
        r.rows.append(("INFO", "Analyze the nav (Nav Mesh panel) to also find stretches where no zombies can "
                       "appear ahead of the survivors", None))
        return r
    vset = set(valid)
    on_route = [a for a in fs if a in te]
    in_ring = {}
    for a in on_route:
        seen = _visible(by, a)
        here, prog = centre[a], fs[a]
        ring = [b for b in vset if fs.get(b, -1.0) > prog and SAFETY_RANGE <= math.dist(here, centre[b]) <= SPAWN_RANGE]
        in_ring[a] = len(ring)
        r.ahead[a] = sum(1 for b in ring if b not in seen)
    # walk the route in 2% steps; a dead step: spots ahead exist but nearly all are in view (fewer than DEAD_AHEAD
    # hidden and under a quarter of them: near the end room the spots ahead just run out, which isn't a dead zone);
    # report runs of dead steps of 10% or more
    steps = []
    for k in range(0, 46):
        goal = k / 50 * r.route
        a = min(on_route, key=lambda x: abs(fs[x] - goal))
        dead = r.ahead.get(a, 0) < DEAD_AHEAD and r.ahead.get(a, 0) < 0.25 * in_ring.get(a, 0)
        steps.append((k * 2, a, r.ahead.get(a, 0), dead))
    run = []
    for pct, a, n, dead in steps + [(None, None, DEAD_AHEAD, False)]:
        if dead:
            run.append((pct, a, n))
            continue
        if len(run) >= 5:
            mid = run[len(run) // 2][1]
            r.rows.append(("WARNING", f"Dead zone {run[0][0]}-{run[-1][0]}% along the route: only "
                           f"{min(x[2] for x in run)}-{max(x[2] for x in run)} spots ahead of the survivors are hidden "
                           f"from them (Valve's streets: dozens), so few zombies appear in front of them here. Break "
                           "up the sightline: cars, walls, fences, a corner", centre[mid]))
        run = []
    return r
