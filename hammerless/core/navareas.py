"""Nav generator stage B: nav areas from the sampled nodes, and their connections.

Follows CreateNavAreasFromNodes / TestArea / BuildArea / ConnectGeneratedAreas and
the jump-down search (findJumpDownArea, testJumpDown) from Valve's Source SDK 2013
nav code, reimplemented independently. Merging and the later clean-up passes come
after this.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .navgen import (AREA_MAX_SIZE, CLIMB_UP_HEIGHT, DEATH_DROP, EAST, GENERATION_STEP, HALF_HUMAN_HEIGHT,
                     HUMAN_CROUCH_HEIGHT, JUMP_CROUCH_HEIGHT, MAX_TRAVERSABLE_HEIGHT, NAV_MESH_CROUCH,
                     NAV_MESH_NO_MERGE, NORTH, NORTH_EAST, OFF_PLANE_TOLERANCE, OPPOSITE, SLOPE_LIMIT, SOUTH,
                     SOUTH_EAST, SOUTH_WEST, STEP_HEIGHT, STEP_XY, WEST, DUCK_HULL_TOP, HALF_HUMAN_WIDTH,
                     HUMAN_HEIGHT, Node, Sampler, add_dir)

COPLANAR_SLOPE_LIMIT = 0.99       # nav_coplanar_slope_limit
COPLANAR_SLOPE_LIMIT_DISP = 0.7   # nav_coplanar_slope_limit_displacement
SLOPE_TOLERANCE = 0.1             # nav_slope_tolerance
NAV_MESH_JUMP = 0x0002
# L4D2 merges far less than the SDK code (its nav_area_max_size is 50, but that only limits
# the first areas). Fitted on four test maps against the game's own navs: allowing a merge
# only while the result has at most 32 cells (25x25 each) gives the most identical areas.
MERGE_MAX_TOTAL_CELLS = 32
NAV_MESH_STAIRS = 0x1000


_CHECK_FORGET = False          # tests: prove split_edit's neighbour-only forget misses nothing


@dataclass(eq=False)
class Area:
    id: int
    nw: tuple[float, float, float]
    se: tuple[float, float, float]
    ne_z: float
    sw_z: float
    nodes: list                      # NW, NE, SE, SW
    attributes: int = 0
    connect: list = field(default_factory=lambda: [[], [], [], []])
    incoming: list = field(default_factory=lambda: [[], [], [], []])   # one-way links into this area
    ladders: list = field(default_factory=lambda: [[], []])           # ladders going up, down

    def add_ladder(self, ladder, up: bool):
        """AddLadderUp / AddLadderDown (each first drops the ladder from both lists)."""
        for lst in self.ladders:
            if ladder in lst:
                lst.remove(ladder)
        self.ladders[0 if up else 1].append(ladder)
    seq: int = 0                     # creation order (= order in the generator's area list)

    def z_at(self, x: float, y: float) -> float:
        dx, dy = self.se[0] - self.nw[0], self.se[1] - self.nw[1]
        if dx <= 0 or dy <= 0:
            return self.ne_z
        u = min(1.0, max(0.0, (x - self.nw[0]) / dx))
        v = min(1.0, max(0.0, (y - self.nw[1]) / dy))
        north = self.nw[2] + u * (self.ne_z - self.nw[2])
        south = self.sw_z + u * (self.se[2] - self.sw_z)
        return north + v * (south - north)

    def overlaps(self, x: float, y: float, tol: float = 0.0) -> bool:
        return (x + tol >= self.nw[0] and x - tol <= self.se[0]
                and y + tol >= self.nw[1] and y - tol <= self.se[1])

    def connect_to(self, other: "Area", d: int):
        """CNavArea::ConnectTo, including the incoming-connection bookkeeping."""
        if other is self or other in self.connect[d]:
            return
        self.connect[d].append(other)
        if other in self.incoming[d]:
            self.incoming[d].remove(other)
        opp = OPPOSITE[d]
        if self not in other.connect[opp] and self not in other.incoming[opp]:
            other.incoming[opp].append(self)

    def is_connected(self, other: "Area", d: int) -> bool:
        return other is self or other in self.connect[d]

    def disconnect(self, other: "Area"):
        for d in range(4):
            if other in self.connect[d]:
                self.connect[d].remove(other)
                opp = OPPOSITE[d]
                if other.is_connected(self, opp):
                    if other not in self.incoming[d]:
                        self.incoming[d].append(other)
                elif self in other.incoming[opp]:
                    other.incoming[opp].remove(self)

    def forget(self, dead: "Area"):
        """OnDestroyNotify: drop every reference to a deleted area."""
        for d in range(4):
            if dead in self.connect[d]:
                self.connect[d].remove(dead)
            if dead in self.incoming[d]:
                self.incoming[d].remove(dead)

    def overlaps_x(self, other: "Area") -> bool:
        return other.nw[0] < self.se[0] and other.se[0] > self.nw[0]

    def overlaps_y(self, other: "Area") -> bool:
        return other.nw[1] < self.se[1] and other.se[1] > self.nw[1]

    def overlaps_area(self, other: "Area") -> bool:
        return self.overlaps_x(other) and self.overlaps_y(other)

    def extent_z(self):
        zs = (self.nw[2], self.se[2], self.ne_z, self.sw_z)
        return min(zs), max(zs)

    def roughly_square(self) -> bool:
        if self.size_y == 0:
            return False
        aspect = self.size_x / self.size_y
        return 1.0 / 3.01 <= aspect <= 3.01

    def corners(self):
        return [self.nw, (self.se[0], self.nw[1], self.ne_z), self.se, (self.nw[0], self.se[1], self.sw_z)]

    @property
    def size_x(self):
        return self.se[0] - self.nw[0]

    @property
    def size_y(self):
        return self.se[1] - self.nw[1]

    def normal(self, alternate: bool = False):
        if not alternate:
            u = (self.se[0] - self.nw[0], 0.0, self.ne_z - self.nw[2])
            v = (0.0, self.se[1] - self.nw[1], self.sw_z - self.nw[2])
        else:
            u = (self.nw[0] - self.se[0], 0.0, self.sw_z - self.se[2])
            v = (0.0, self.nw[1] - self.se[1], self.ne_z - self.se[2])
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        length = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
        return (n[0] / length, n[1] / length, n[2] / length)

    @property
    def on_disp(self) -> bool:
        return any(n is not None and n.on_disp for n in self.nodes)

    def is_flat(self) -> bool:
        a, b = self.normal(), self.normal(True)
        tol = COPLANAR_SLOPE_LIMIT_DISP if self.on_disp else COPLANAR_SLOPE_LIMIT
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] > tol

    def is_coplanar(self, other: "Area") -> bool:
        if not self.on_disp and not self.is_flat():
            return False
        if not other.on_disp and not other.is_flat():
            return False
        a, b = self.normal(), other.normal()
        tol = COPLANAR_SLOPE_LIMIT_DISP if self.on_disp else COPLANAR_SLOPE_LIMIT
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] > tol

    def refresh_corners(self):
        nw, ne, se, sw = self.nodes
        self.nw, self.se, self.ne_z, self.sw_z = nw.pos, se.pos, ne.pos[2], sw.pos[2]

    def closest_point(self, p):
        x = min(max(p[0], self.nw[0]), self.se[0])
        y = min(max(p[1], self.nw[1]), self.se[1])
        return (x, y, self.z_at(x, y))

    def portal(self, other: "Area", d: int):
        """ComputePortal: (centre, half width) of the edge towards 'other' in direction d."""
        if d in (NORTH, SOUTH):
            cy = self.nw[1] if d == NORTH else self.se[1]
            left = min(max(max(self.nw[0], other.nw[0]), self.nw[0]), self.se[0])
            right = min(max(min(self.se[0], other.se[0]), self.nw[0]), self.se[0])
            return ((left + right) / 2, cy, 0.0), (right - left) / 2
        cx = self.nw[0] if d == WEST else self.se[0]
        top = min(max(max(self.nw[1], other.nw[1]), self.nw[1]), self.se[1])
        bottom = min(max(min(self.se[1], other.se[1]), self.nw[1]), self.se[1])
        return (cx, (top + bottom) / 2, 0.0), (bottom - top) / 2

    @property
    def centre(self):
        return ((self.nw[0] + self.se[0]) / 2, (self.nw[1] + self.se[1]) / 2, (self.nw[2] + self.se[2]) / 2)


def _steps(lo: float, hi: float):
    """Points every generation step along an edge, starting half a step in."""
    v = lo + GENERATION_STEP / 2
    while v < hi:
        yield v
        v += GENERATION_STEP


def _add_dir(pos, d, amount):
    dx, dy = STEP_XY[d]
    k = amount / GENERATION_STEP
    return (pos[0] + dx * k, pos[1] + dy * k, pos[2])


class Generator(Sampler):
    """Stage A sampling plus stage B area creation and connection."""
    GRID = 300.0

    def __init__(self, world):
        super().__init__(world)
        self.areas: list[Area] = []
        self.area_grid: dict[tuple[int, int], list[Area]] = {}

    # ------------------------------------------------ CreateNavAreasFromNodes
    def _runs(self, d):
        """Upper bound of the closed-cell run from each node in direction d. Only a speed-up:
        TestArea(w, h) always fails when the run east is shorter than w or south than h."""
        run: dict[int, int] = {}
        for start in self.nodes:
            if id(start) in run:
                continue
            chain, seen, n = [], set(), start
            while n is not None and id(n) not in run and id(n) not in seen and n.closed:
                chain.append(n)
                seen.add(id(n))
                n = n.to[d]
            base = run.get(id(n), 0) if n is not None else 0
            for i, c in enumerate(reversed(chain)):
                run[id(c)] = base + i + 1
            run.setdefault(id(start), 0)
        return run

    def test_area(self, node: Node, width: int, height: int) -> bool:
        """TestArea. On failure, self._fail holds where it failed (see _still_fails)."""
        self._fail = None
        normal, pos = node.normal, node.pos
        d = -(normal[0] * pos[0] + normal[1] * pos[1] + normal[2] * pos[2])

        def off_plane(n: Node) -> bool:
            q = n.pos
            return abs(q[0] * normal[0] + q[1] * normal[1] + q[2] * normal[2] + d) > OFF_PLANE_TOLERANCE

        def fail(kind, x=0, y=0, multi_only=False):
            self._fail = (kind, x, y, multi_only)
            return False

        node_crouch = node.crouch[SOUTH_EAST]
        if node.blocked[SOUTH_EAST]:
            return fail("dead")
        node_attr = node.attributes & ~NAV_MESH_CROUCH
        multi = width > 1 or height > 1
        vert = node
        for y in range(height):
            horiz = vert
            for x in range(width):
                # the game's case order boils down to: NW corner, north edge, west edge, the rest
                west, north = x == 0, y == 0
                if north and west:
                    hc = horiz.crouch[SOUTH_EAST]
                    if horiz.blocked[SOUTH_EAST]:
                        return fail("cell", x, y)
                elif north:
                    hc = horiz.crouch[SOUTH_EAST] or horiz.crouch[SOUTH_WEST]
                    if horiz.blocked[SOUTH_EAST] or horiz.blocked[SOUTH_WEST]:
                        return fail("cell", x, y)
                elif west:
                    hc = horiz.crouch[SOUTH_EAST] or horiz.crouch[NORTH_EAST]
                    if horiz.blocked[SOUTH_EAST] or horiz.blocked[NORTH_EAST]:
                        return fail("cell", x, y)
                else:                # south-east, south, east, interior
                    hc = bool(horiz.attributes & NAV_MESH_CROUCH)
                    if horiz.blocked_any():
                        return fail("cell", x, y)
                if node_crouch != hc:
                    return fail("cell", x, y)
                if (horiz.attributes & ~NAV_MESH_CROUCH) != node_attr:
                    return fail("cell", x, y)
                if horiz.covered or not horiz.closed:
                    return fail("cell", x, y)
                if not self._check_obstacles(horiz, width, height, x, y):
                    return False
                horiz = horiz.to[EAST]
                if horiz is None:
                    return fail("east", x + 1, y)
                if multi and off_plane(horiz):
                    return fail("east", x + 1, y, True)
            if not self._check_obstacles(horiz, width, height, width, y):
                return False
            vert = vert.to[SOUTH]
            if vert is None:
                return fail("south", 0, y + 1)
            if multi and off_plane(vert):
                return fail("south", 0, y + 1, True)
        if multi:
            horiz = vert
            for x in range(width):
                if not self._check_obstacles(horiz, width, height, x, height):
                    return False
                horiz = horiz.to[EAST]
                if horiz is None or off_plane(horiz):
                    return False
            if not self._check_obstacles(horiz, width, height, width, height):
                return False
        if node_crouch:
            vert = node
            for y in range(height):
                horiz = vert
                for x in range(width):
                    if not self._valid_crouch_area(horiz):
                        return fail("cell", x, y)
                    horiz = horiz.to[EAST]
                vert = vert.to[SOUTH]
        return True

    @staticmethod
    def _still_fails(f, width: int, height: int) -> bool:
        """Would TestArea still fail at the remembered spot for this (smaller) size? Every
        remembered reason is permanent (covered stays covered, links and heights don't change),
        so while the block still includes that spot the answer is the same."""
        if f is None:
            return False
        kind, x, y, multi_only = f
        if multi_only and width == 1 and height == 1:
            return False
        if kind == "dead":
            return True
        if kind == "cell":
            return x < width and y < height
        if kind == "east":
            return x <= width and y < height
        return y <= height                       # "south"

    @staticmethod
    def _check_obstacles(n: Node, width, height, x, y) -> bool:
        if width > 1 or height > 1:
            if x > 0 and n.obstacle[WEST] > MAX_TRAVERSABLE_HEIGHT:
                return False
            if y > 0 and n.obstacle[NORTH] > MAX_TRAVERSABLE_HEIGHT:
                return False
            if x < width - 1 and n.obstacle[EAST] > MAX_TRAVERSABLE_HEIGHT:
                return False
            if y < height - 1 and n.obstacle[SOUTH] > MAX_TRAVERSABLE_HEIGHT:
                return False
        return True

    def _valid_crouch_area(self, n: Node) -> bool:
        p = n.pos
        tr = self.world.trace_hull(p, (p[0], p[1], p[2] + JUMP_CROUCH_HEIGHT), (0.0, 0.0, 0.0),
                                   (GENERATION_STEP, GENERATION_STEP, HUMAN_CROUCH_HEIGHT))
        return not tr.allsolid

    def build_area(self, node: Node, width: int, height: int) -> int:
        covered = 0
        vert, ne = node, None
        for y in range(height):
            horiz = vert
            for _x in range(width):
                horiz.covered = True
                covered += 1
                horiz = horiz.to[EAST]
            if y == 0:
                ne = horiz
            vert = vert.to[SOUTH]
        sw = horiz = vert
        for _x in range(width):
            horiz = horiz.to[EAST]
        se = horiz
        self._seq = getattr(self, "_seq", 0) + 1
        area = Area(self._seq, node.pos, se.pos, ne.pos[2], sw.pos[2], [node, ne, se, sw], seq=self._seq)
        last, v = ne, node           # AssignNodes: all nodes except the east and south edges
        while v is not sw:
            h = v
            while h is not last:
                h.area = area
                h = h.to[EAST]
            last = last.to[SOUTH]
            v = v.to[SOUTH]
        area.attributes = node.attributes
        m = MAX_TRAVERSABLE_HEIGHT
        if (node.obstacle[SOUTH] > m or node.obstacle[EAST] > m or ne.obstacle[WEST] > m or ne.obstacle[SOUTH] > m
                or se.obstacle[NORTH] > m or se.obstacle[WEST] > m or sw.obstacle[EAST] > m or sw.obstacle[NORTH] > m):
            area.attributes |= NAV_MESH_NO_MERGE
        if (area.attributes & NAV_MESH_CROUCH) and not node.crouch[SOUTH_EAST]:
            area.attributes &= ~NAV_MESH_CROUCH
        self.areas.append(area)
        return covered

    def create_areas(self):
        if getattr(self, "native_nodes", False):      # same result, done in the DLL
            from . import fastnav
            for i, width, height in fastnav.create_areas(self.world, len(self.nodes)):
                self.build_area(self.nodes[i], width, height)
            self._index_areas()
            return
        for n in self.nodes:           # links don't change after sampling
            n.closed = n.closed_cell()
        width = height = AREA_MAX_SIZE
        uncovered = len(self.nodes)
        east, south = self._runs(EAST), self._runs(SOUTH)
        order = list(reversed(self.nodes))       # CNavNode::m_list: newest first
        memo: dict[int, tuple] = {}
        while uncovered > 0:
            for node in order:
                if node.covered or east.get(id(node), 0) < width or south.get(id(node), 0) < height:
                    continue
                if self._still_fails(memo.get(id(node)), width, height):
                    continue
                if self.test_area(node, width, height):
                    uncovered -= self.build_area(node, width, height)
                elif self._fail is not None:
                    memo[id(node)] = self._fail
            if width >= height:
                width -= 1
            else:
                height -= 1
            if width <= 0 or height <= 0:
                break
        self._index_areas()

    def _index_areas(self):
        self.area_grid = {}
        for a in self.areas:
            for gx in range(int(a.nw[0] // self.GRID), int(a.se[0] // self.GRID) + 1):
                for gy in range(int(a.nw[1] // self.GRID), int(a.se[1] // self.GRID) + 1):
                    self.area_grid.setdefault((gx, gy), []).append(a)

    # ------------------------------------------------ GetNavArea and the jump-down search
    def get_nav_area(self, pos, beneath_limit: float = 120.0) -> Area | None:
        tx, ty, tz = pos[0], pos[1], pos[2] + 5.0
        use, use_z = None, -99999999.9
        for a in self.area_grid.get((int(pos[0] // self.GRID), int(pos[1] // self.GRID)), ()):
            if a.overlaps(tx, ty):
                z = a.z_at(tx, ty)
                if z > tz or z < pos[2] - beneath_limit:
                    continue
                if z > use_z:
                    use, use_z = a, z
        return use

    def find_first_area_in_direction(self, start, d, rng, beneath_limit):
        pos = start
        for _ in range(int(rng / GENERATION_STEP + 0.5)):
            pos = _add_dir(pos, d, GENERATION_STEP)
            if self.hull(start, pos).fraction < 1.0:
                break
            area = self.get_nav_area(pos, beneath_limit)
            if area:
                return area, (pos[0], pos[1], area.z_at(pos[0], pos[1]))
        return None, None

    def test_jump_down(self, frm, to) -> bool:
        dz = frm[2] - to[2]
        if dz <= JUMP_CROUCH_HEIGHT or dz >= DEATH_DROP:
            return False
        up, b = 1.0, None
        while up <= CLIMB_UP_HEIGHT:
            tr = self.hull(frm, (frm[0], frm[1], frm[2] + up))
            if not (tr.fraction <= 0.0 or tr.startsolid):
                a = (frm[0], frm[1], tr.endpos[2] - 0.5)
                b = (to[0], to[1], a[2])
                tr = self.hull(a, b)
                if tr.fraction == 1.0 and not tr.startsolid:
                    break
            up += 1.0
        if up > CLIMB_UP_HEIGHT:
            return False
        end = (b[0], b[1], to[2] + 2.0)
        tr = self.hull(b, end)
        if tr.fraction <= 0.0 or tr.startsolid:
            return False
        return tr.endpos[2] <= end[2] + STEP_HEIGHT

    def find_jump_down_area(self, frm, d) -> Area | None:
        start = _add_dir((frm[0], frm[1], frm[2] + HALF_HUMAN_HEIGHT), d, GENERATION_STEP / 2.0)
        area, to = self.find_first_area_in_direction(start, d, 4.0 * GENERATION_STEP, DEATH_DROP)
        if area and self.test_jump_down(frm, to):
            return area
        return None

    # ------------------------------------------------ ConnectGeneratedAreas
    def _link(self, area, node, d, back):
        adj = node.to[d]
        if adj is not None and adj.area is not None and adj.to[back] is node:
            area.connect_to(adj.area, d)
        else:
            down = self.find_jump_down_area(node.pos, d)
            if down and down is not area:
                area.connect_to(down, d)

    def _edge_drop(self, area, node, d):
        """Nodes along the south/east edge that no area owns: only jump-down links."""
        if node.area is not None:
            return
        adj = node.to[d]
        if node.blocked_any() or (adj is not None and adj.blocked_any()):
            return
        if adj is None or adj.area is None:
            down = self.find_jump_down_area(node.pos, d)
            if down and down is not area:
                area.connect_to(down, d)

    def connect_areas(self):
        for area in self.areas:
            nw, ne, se, sw = area.nodes
            n = nw
            while n is not ne:
                self._link(area, n, NORTH, SOUTH)
                n = n.to[EAST]
            n = nw
            while n is not sw:
                self._link(area, n, WEST, EAST)
                n = n.to[SOUTH]
            n = sw.to[NORTH]
            if n is not None:
                end = se.to[NORTH]
                while n is not None and n is not end:
                    self._link(area, n, SOUTH, NORTH)
                    n = n.to[EAST]
            n = sw
            while n is not None and n is not se:
                self._edge_drop(area, n, SOUTH)
                n = n.to[EAST]
            n = ne.to[WEST]
            if n is not None:
                end = se.to[WEST]
                while n is not None and n is not end:
                    self._link(area, n, EAST, WEST)
                    n = n.to[SOUTH]
            n = ne
            while n is not None and n is not se:
                self._edge_drop(area, n, EAST)
                n = n.to[SOUTH]

    # ------------------------------------------------ MarkJumpAreas
    def simple_ground(self, pos):
        """GetSimpleGroundHeight: a ray straight down."""
        tr = self.world.trace_hull(pos, (pos[0], pos[1], pos[2] - 9999.9), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        if tr.startsolid:
            return None
        return tr.endpos[2], tr.normal

    def mark_jump_areas(self):
        for area in self.areas:
            if not area.nodes:
                continue
            low = min(area.normal()[2], area.normal(True)[2])
            if low < SLOPE_LIMIT:
                area.attributes |= NAV_MESH_JUMP | NAV_MESH_NO_MERGE
            elif low < SLOPE_LIMIT + SLOPE_TOLERANCE:
                c = area.centre
                ground = self.simple_ground((c[0], c[1], c[2] + HALF_HUMAN_HEIGHT))
                if ground and abs(ground[1][2] - low) > SLOPE_TOLERANCE:
                    area.attributes |= NAV_MESH_JUMP | NAV_MESH_NO_MERGE

    # ------------------------------------------------ MergeGeneratedAreas
    @staticmethod
    def _can_merge(a: Area, b: Area) -> bool:
        cells = (round(a.size_x / GENERATION_STEP) * round(a.size_y / GENERATION_STEP)
                 + round(b.size_x / GENERATION_STEP) * round(b.size_y / GENERATION_STEP))
        return (bool(a.nodes) and not a.attributes & NAV_MESH_NO_MERGE
                and bool(b.nodes) and not b.attributes & NAV_MESH_NO_MERGE and cells <= MERGE_MAX_TOTAL_CELLS)

    @staticmethod
    def _assign(src: Area, owner: Area):
        nw, ne, se, sw = src.nodes
        last, v = ne, nw
        while v is not sw:
            h = v
            while h is not last:
                h.area = owner
                h = h.to[EAST]
            last = last.to[SOUTH]
            v = v.to[SOUTH]

    def _finish_merge(self, area: Area, adj: Area):
        area.refresh_corners()
        self._assign(adj, area)
        for d in range(4):                                    # MergeAdjacentConnections
            for other in list(adj.connect[d]):
                if other is not adj and other is not area:
                    area.connect_to(other, d)
        area.disconnect(adj)
        # every area that references adj is among its links (connections and incoming ones);
        # handled in list order like the game's loop over all areas
        near = {id(o): o for d in range(4) for o in adj.connect[d] + adj.incoming[d]}
        for other in sorted(near.values(), key=lambda o: o.seq):
            if other is area or other is adj:
                continue
            for d in range(4):
                if adj in other.connect[d]:
                    other.disconnect(adj)
                    other.disconnect(area)
                    other.connect_to(area, d)
        self.areas.remove(adj)
        for other in near.values():
            other.forget(adj)
        area.forget(adj)

    MERGE_TESTS = (
        (NORTH, 1, lambda a, b: a.nodes[0] is b.nodes[3] and a.nodes[1] is b.nodes[2], (0, 1), (0, 1)),
        (SOUTH, 1, lambda a, b: b.nodes[0] is a.nodes[3] and b.nodes[1] is a.nodes[2], (3, 2), (3, 2)),
        (WEST, 0, lambda a, b: a.nodes[0] is b.nodes[1] and a.nodes[3] is b.nodes[2], (0, 3), (0, 3)),
        (EAST, 0, lambda a, b: b.nodes[0] is a.nodes[1] and b.nodes[3] is a.nodes[2], (1, 2), (1, 2)),
    )

    def merge_areas(self):
        """MergeGeneratedAreas: after each merge the game rescans from the first area. An area that
        failed and whose neighbourhood hasn't changed fails again, so only areas a merge touched
        ('dirty') need re-testing, and the rescan goes straight to the first dirty one (areas are
        in creation order here, so a heap on seq finds it). Same merges, same order."""
        import heapq
        limit = GENERATION_STEP * AREA_MAX_SIZE
        alive = {id(a): a for a in self.areas}
        assert all(a.seq < b.seq for a, b in zip(self.areas, self.areas[1:])), "areas out of creation order"
        heap = [(a.seq, id(a)) for a in self.areas]          # sorted, so already a heap
        dirty = set(alive)
        while heap:
            _seq, aid = heapq.heappop(heap)
            if aid not in dirty or aid not in alive:
                continue
            dirty.discard(aid)
            area = alive[aid]
            if not area.nodes or area.attributes & NAV_MESH_NO_MERGE:
                continue
            merged = False
            for d, axis, test, mine, theirs in self.MERGE_TESTS:
                for adj in list(area.connect[d]):
                    if not self._can_merge(area, adj):
                        continue
                    size = (area.size_y + adj.size_y) if axis == 1 else (area.size_x + adj.size_x)
                    if size > limit:
                        continue
                    if test(area, adj) and area.attributes == adj.attributes and area.is_coplanar(adj):
                        touched = [o for dd in range(4) for o in
                                   area.connect[dd] + area.incoming[dd] + adj.connect[dd] + adj.incoming[dd]]
                        area.nodes[mine[0]], area.nodes[mine[1]] = adj.nodes[theirs[0]], adj.nodes[theirs[1]]
                        self._finish_merge(area, adj)
                        del alive[id(adj)]
                        for o in touched + [area] + [o for dd in range(4) for o in area.connect[dd] + area.incoming[dd]]:
                            if id(o) in alive and id(o) not in dirty:
                                dirty.add(id(o))
                                heapq.heappush(heap, (o.seq, id(o)))
                        merged = True
                        break
                if merged:
                    break
        self._index_areas()


    # ------------------------------------------------ area splitting (SplitEdit)
    def _new_area(self, nw, se, attributes=0) -> Area:
        self._seq = getattr(self, "_seq", 0) + 1
        return Area(self._seq, nw, se, 0.0, 0.0, [None, None, None, None], attributes, seq=self._seq)

    def split_edit(self, area: Area, along_x: bool, edge: float):
        """CNavArea::SplitEdit: replace an area by two, split along X (at y = edge) or Y (x = edge)."""
        if along_x:
            if edge <= area.nw[1] + 1.0 or edge >= area.se[1] - 1.0:
                return None
            ase = (area.se[0], edge, area.z_at(area.se[0], edge))
            alpha = self._new_area(area.nw, ase)
            bnw = (area.nw[0], edge, area.z_at(area.nw[0], edge))
            beta = self._new_area(bnw, area.se)
            alpha.connect_to(beta, SOUTH)
            beta.connect_to(alpha, NORTH)
            self._finish_split(area, alpha, SOUTH)
            self._finish_split(area, beta, NORTH)
        else:
            if edge <= area.nw[0] + 1.0 or edge >= area.se[0] - 1.0:
                return None
            ase = (edge, area.se[1], area.z_at(edge, area.se[1]))
            alpha = self._new_area(area.nw, ase)
            bnw = (edge, area.nw[1], area.z_at(edge, area.nw[1]))
            beta = self._new_area(bnw, area.se)
            alpha.connect_to(beta, EAST)
            beta.connect_to(alpha, WEST)
            self._finish_split(area, alpha, EAST)
            self._finish_split(area, beta, WEST)
        self.areas.remove(area)
        # only areas linked with it (either way) can hold a reference to it
        for other in {id(o): o for d in range(4) for o in area.connect[d] + area.incoming[d]}.values():
            other.forget(area)
        if _CHECK_FORGET:
            assert not any(area in o.connect[d] or area in o.incoming[d] for o in self.areas for d in range(4))
        return alpha, beta

    def _finish_split(self, old: Area, new: Area, ignore: int):
        new.attributes = old.attributes
        new.ne_z = old.z_at(new.se[0], new.nw[1])
        new.sw_z = old.z_at(new.nw[0], new.se[1])
        for d in range(4):
            if d == ignore:
                continue
            for adj in list(old.connect[d]):
                overlap = (new.overlaps_x(adj) if d in (NORTH, SOUTH) else new.overlaps_y(adj))
                if overlap:
                    new.connect_to(adj, d)
                    if adj.is_connected(old, OPPOSITE[d]):
                        adj.connect_to(new, OPPOSITE[d])
                # (sic) the game re-links incoming connections inside this loop, so only for
                # directions that have at least one adjacent area
                for inc in list(old.incoming[d]):
                    if (new.overlaps_x(inc) if d in (NORTH, SOUTH) else new.overlaps_y(inc)):
                        inc.connect_to(new, OPPOSITE[d])
        self.areas.append(new)
        if all(n is not None for n in old.nodes):
            new.nodes = list(old.nodes)
            d, corners = {NORTH: (SOUTH, (0, 1)), SOUTH: (NORTH, (3, 2)),
                          EAST: (WEST, (1, 2)), WEST: (EAST, (0, 3))}[ignore]
            guard = 0
            while not new.overlaps(new.nodes[corners[0]].pos[0], new.nodes[corners[0]].pos[1], GENERATION_STEP / 2):
                for c in corners:
                    new.nodes[c] = new.nodes[c].to[d]
                guard += 1
                if guard > 10000 or any(new.nodes[c] is None for c in corners):
                    new.nodes = [None, None, None, None]
                    break
            if all(n is not None for n in new.nodes):
                self._assign(new, new)
                new.ne_z, new.sw_z = new.nodes[1].pos[2], new.nodes[3].pos[2]
                new.nw = (new.nw[0], new.nw[1], new.nodes[0].pos[2])
                new.se = (new.se[0], new.se[1], new.nodes[2].pos[2])

    @staticmethod
    def _snap(v: float) -> float:
        from .navgen import round_to_units
        return round_to_units(v, GENERATION_STEP)

    def _split_x(self, area: Area):
        if area.roughly_square():
            return
        split = self._snap(area.size_x / 2.0 + area.nw[0])
        if abs(split - area.nw[0]) < 0.1 or abs(split - area.se[0]) < 0.1:
            return
        res = self.split_edit(area, False, split)
        if res:
            self._split_x(res[0])
            self._split_x(res[1])

    def _split_y(self, area: Area):
        if area.roughly_square():
            return
        split = self._snap(area.size_y / 2.0 + area.nw[1])
        if abs(split - area.nw[1]) < 0.1 or abs(split - area.se[1]) < 0.1:
            return
        res = self.split_edit(area, True, split)
        if res:
            self._split_y(res[0])
            self._split_y(res[1])

    def square_up_areas(self):
        """SquareUpAreas. The 2013 SDK loop skips the area that slides into a split area's place;
        L4D2 splits them all (measured: every long cliff strip gets squared), so no skipping."""
        i = 0
        while i < len(self.areas):
            area = self.areas[i]
            if all(n is not None for n in area.nodes) and not area.roughly_square():
                if area.size_x > area.size_y:
                    self._split_x(area)
                else:
                    self._split_y(area)
                if i < len(self.areas) and self.areas[i] is not area:
                    continue            # the next area moved into this slot
            i += 1
        self._index_areas()

    def split_areas_under_overhangs(self):
        restart = True
        while restart:
            restart = False
            for area in list(self.areas):
                if restart:
                    break
                lo_a, hi_a = area.extent_z()
                for d in range(4):
                    if restart:
                        break
                    for other in list(area.connect[d]):
                        if not area.overlaps_area(other):
                            continue
                        lo_o, hi_o = other.extent_z()
                        if not (lo_a > hi_o + HUMAN_CROUCH_HEIGHT) and not (lo_o > hi_a + HUMAN_CROUCH_HEIGHT):
                            continue
                        below, above, a2b = area, other, OPPOSITE[d]
                        if lo_o < lo_a:
                            below, above, a2b = other, area, OPPOSITE[a2b]
                        b2a = OPPOSITE[a2b]
                        if a2b in (EAST, WEST):
                            along_x = False
                            edge_size = below.se[0] - below.nw[0]
                            if above.se[0] < below.se[0]:
                                coord, length = above.se[0], above.se[0] - below.nw[0]
                            else:
                                coord, length = above.nw[0], below.se[0] - above.nw[0]
                        else:
                            along_x = True
                            edge_size = below.se[1] - below.nw[1]
                            if above.se[1] < below.se[1]:
                                coord, length = above.se[1], above.se[1] - below.nw[1]
                            else:
                                coord, length = above.nw[1], below.se[1] - above.nw[1]
                        if length < GENERATION_STEP:
                            if length < GENERATION_STEP * 0.3 or edge_size <= GENERATION_STEP * 2:
                                continue
                            coord += (GENERATION_STEP - length) * (-1 if a2b in (NORTH, WEST) else 1)
                        from_below = below.is_connected(above, b2a) and above is not below
                        if from_below:
                            below.disconnect(above)
                        from_above = above.is_connected(below, a2b)
                        if from_above:
                            above.disconnect(below)
                        res = self.split_edit(below, along_x, coord)
                        if res:
                            keep = res[0] if a2b in (NORTH, WEST) else res[1]
                            if from_above:
                                above.connect_to(keep, a2b)
                            # (the game also links the deleted lower area here, a use-after-free; skipped)
                            restart = True
                            break
        self._index_areas()

    # ------------------------------------------------ MarkStairAreas
    def _is_stairs(self, start, end, ret):
        if ret == "no":
            return ret
        inc = 5.0
        min_step = inc * math.tan(math.acos(SLOPE_LIMIT))
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if abs(start[0] - end[0]) > abs(start[1] - end[1]):
            mins, maxs = (-8.0, -inc / 2, 0.0), (8.0, inc / 2, 1.0)
        else:
            mins, maxs = (-inc / 2, -8.0, 0.0), (inc / 2, 8.0, 1.0)
        off = DUCK_HULL_TOP
        if abs(start[2] - end[2]) > STEP_HEIGHT:
            tr = self.world.trace_hull((start[0], start[1], start[2] + off), (start[0], start[1], start[2] - off), mins, maxs)
            if tr.startsolid or tr.displacement:
                return "no"
            prior = tr.endpos[2]
            step = inc / length if length else 1.0
            t = 0.0
            while t <= 1.0:
                p = tuple(start[k] + t * (end[k] - start[k]) for k in range(3))
                tr = self.world.trace_hull((p[0], p[1], p[2] + off), (p[0], p[1], p[2] - off), mins, maxs)
                if tr.startsolid or tr.displacement:
                    return "no"
                h = tr.endpos[2]
                if t == 0.0 and abs(h - start[2]) > STEP_HEIGHT:
                    return "no"
                if t == 1.0 and abs(h - end[2]) > STEP_HEIGHT:
                    return "no"
                if tr.normal[2] < 0.97:
                    return "no"
                dz = abs(h - prior)
                if min_step <= dz <= STEP_HEIGHT:
                    ret = "yes"
                elif dz > STEP_HEIGHT:
                    return "no"
                prior = h
                t += step
        return ret

    def mark_stair_areas(self):
        for a in self.areas:
            a.attributes &= ~NAV_MESH_STAIRS
            if a.size_x <= GENERATION_STEP and a.size_y <= GENERATION_STEP:
                continue
            n1, n2 = a.normal(), a.normal(True)
            if n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2] < 0.95:
                continue
            c = a.corners()
            nw, ne, se, sw = c
            ins = 5.0
            ret = "maybe"
            for s_, e_ in (((nw[0] + ins, nw[1] + ins, nw[2]), (ne[0] - ins, ne[1] + ins, ne[2])),
                           ((sw[0] + ins, sw[1] - ins, sw[2]), (se[0] - ins, se[1] - ins, se[2])),
                           ((nw[0] + ins, nw[1] + ins, nw[2]), (sw[0] + ins, sw[1] - ins, sw[2])),
                           ((ne[0] - ins, ne[1] + ins, ne[2]), (se[0] - ins, se[1] - ins, se[2])),
                           (tuple((nw[k] + ne[k]) / 2 for k in range(3)), tuple((sw[k] + se[k]) / 2 for k in range(3))),
                           (tuple((ne[k] + se[k]) / 2 for k in range(3)), tuple((nw[k] + sw[k]) / 2 for k in range(3)))):
                ret = self._is_stairs(s_, e_, ret)
            if ret == "yes":
                a.attributes = NAV_MESH_STAIRS          # (sic) SetAttributes replaces all flags


    # ------------------------------------------------ FixUpGeneratedAreas
    def _nodes_along(self, area: Area, d: int):
        """CNavArea::GetNodes: the area's nodes along its edge facing d."""
        start, end, step = {NORTH: (0, 1, EAST), SOUTH: (3, 2, EAST), EAST: (1, 2, SOUTH), WEST: (0, 3, SOUTH)}[d]
        out, n, guard = [], area.nodes[start], 0
        while n is not None and n is not area.nodes[end] and guard < 100000:
            out.append(n)
            n = n.to[step]
            guard += 1
        if n is not None and n is area.nodes[end]:
            out.append(n)
        return out

    def _closest_node(self, area: Area, pos, d: int):
        if not all(n is not None for n in area.nodes):
            return None
        best, best_d = None, float("inf")
        for n in self._nodes_along(area, d):
            dd = (pos[0] - n.pos[0]) ** 2 + (pos[1] - n.pos[1]) ** 2 + (pos[2] - n.pos[2]) ** 2
            if dd < best_d:
                best, best_d = n, dd
        return best

    def fix_connections(self):
        """FixConnections: stairs whose sides are more than a step apart drop links where a step
        can't be climbed; then every 'skip' link (A->C while A->B->C goes the same way) goes."""
        edge = {NORTH: (0, 1), SOUTH: (3, 2), EAST: (1, 2), WEST: (0, 3)}
        for area in self.areas:
            if not area.attributes & NAV_MESH_STAIRS or not all(n is not None for n in area.nodes):
                continue
            corners = area.corners()
            for d in range(4):
                c0, c1 = edge[d]
                if abs(corners[c0][2] - corners[c1][2]) < STEP_HEIGHT:
                    continue
                drop = []
                for adj in list(area.connect[d]):
                    if not all(n is not None for n in adj.nodes):
                        continue
                    centre, _w = area.portal(adj, d)
                    adj_pos = adj.closest_point(centre)
                    node = self._closest_node(area, centre, d)
                    adj_node = self._closest_node(adj, adj_pos, OPPOSITE[d])
                    if node is None or adj_node is None:
                        continue
                    a0, a1 = edge[OPPOSITE[d]]
                    pos, apos = node.pos, adj_node.pos
                    if (node.ground[c0] > STEP_HEIGHT or node.ground[c1] > STEP_HEIGHT
                            or apos[2] + adj_node.ground[a0] > pos[2] + STEP_HEIGHT
                            or apos[2] + adj_node.ground[a1] > pos[2] + STEP_HEIGHT):
                        drop.append(adj)
                for adj in drop:
                    area.disconnect(adj)
        for area in self.areas:
            drop = []
            for d in range(4):
                for adj in list(area.connect[d]):
                    for far in list(adj.connect[d]):
                        if area.is_connected(far, d):
                            drop.append(far)
            for far in drop:
                area.disconnect(far)

    def fix_corner_on_corner_areas(self):
        """FixCornerOnCornerAreas: where two areas touch only at a corner, add a small area in the
        notch so bots can get from one to the other."""
        max_drop = STEP_HEIGHT
        vec = {NORTH: (0.0, -1.0), EAST: (1.0, 0.0), SOUTH: (0.0, 1.0), WEST: (-1.0, 0.0)}
        half = GENERATION_STEP * 0.5
        i = 0
        while i < len(self.areas):             # (areas added here get their turn too)
            area = self.areas[i]
            i += 1
            for corner in range(4):
                right, left = corner, (corner + 3) % 4
                if area.connect[left] or area.connect[right] or area.incoming[left] or area.incoming[right]:
                    continue
                cp = area.corners()[corner]
                for along_other, along_ours in ((left, (left + 3) % 4), (right, (right + 1) % 4)):
                    vo = (vec[along_other][0] * half, vec[along_other][1] * half)
                    other_pos = (cp[0] + vo[0], cp[1] + vo[1], cp[2])
                    other = self.get_nav_area(other_pos)
                    if other is None:
                        continue
                    ok, _tr = self.trace_adjacent(0, cp, other_pos, max_drop)
                    if not ok:
                        continue
                    if other.corners()[(corner + 2) % 4] != cp:
                        continue
                    vu = (vec[along_ours][0] * half, vec[along_ours][1] * half)
                    c = [(cp[0] + vo[0] + vu[0], cp[1] + vo[1] + vu[1], cp[2]), other_pos, cp, (cp[0] + vu[0], cp[1] + vu[1], cp[2])]
                    ok1, _ = self.trace_adjacent(0, c[1], c[0], max_drop)
                    ok2, tr = (self.trace_adjacent(0, c[3], c[0], max_drop) if ok1 else (False, None))
                    if not (ok1 and ok2):
                        continue
                    if self.get_nav_area(c[0]) is not None:
                        continue
                    c[0] = tr.endpos
                    nw = ne = se = sw = c[0]          # ClassifyCorners
                    for p in c:
                        if p[0] <= nw[0] and p[1] <= nw[1]:
                            nw = p
                        if p[0] >= ne[0] and p[1] <= ne[1]:
                            ne = p
                        if p[0] >= se[0] and p[1] >= se[1]:
                            se = p
                        if p[0] <= sw[0] and p[1] >= sw[1]:
                            sw = p
                    new = self._new_area(nw, se, area.attributes)
                    new.ne_z, new.sw_z = ne[2], sw[2]
                    self.areas.append(new)
                    self._index_areas()
                    area.connect_to(new, along_other)
                    new.connect_to(area, OPPOSITE[along_other])
                    other.connect_to(new, along_ours)
                    new.connect_to(other, OPPOSITE[along_ours])

    # ------------------------------------------------ ladders
    def get_nearest_nav_area(self, pos, max_dist: float = 10000.0):
        """GetNearestNavArea(pos, anyZ, maxDist, checkLOS=false, checkGround=true): the area whose
        closest point is nearest in 3D, searched in rings over the 300-unit area grid."""
        ok, z, _n = self.ground_height(pos)
        if not ok:
            return None
        source = (pos[0], pos[1], z + HALF_HUMAN_HEIGHT)
        lo_x = min(a.nw[0] for a in self.areas)
        lo_y = min(a.nw[1] for a in self.areas)
        size_x = int((max(a.se[0] for a in self.areas) - lo_x) / self.GRID) + 1
        size_y = int((max(a.se[1] for a in self.areas) - lo_y) / self.GRID) + 1
        gx = lambda x: min(max(int((x - lo_x) / self.GRID), 0), size_x - 1)
        gy = lambda y: min(max(int((y - lo_y) / self.GRID), 0), size_y - 1)
        grid: dict = {}
        for a in self.areas:
            for x in range(gx(a.nw[0]), gx(a.se[0]) + 1):
                for y in range(gy(a.nw[1]), gy(a.se[1]) + 1):
                    grid.setdefault((x, y), []).append(a)
        ox, oy = gx(pos[0]), gy(pos[1])
        close, close_d = None, max_dist * max_dist
        seen = set()
        shift, limit = 0, math.ceil(max_dist / self.GRID)
        while shift <= limit:
            for x in range(ox - shift, ox + shift + 1):
                if x < 0 or x >= size_x:
                    continue
                for y in range(oy - shift, oy + shift + 1):
                    if y < 0 or y >= size_y:
                        continue
                    if ox - shift < x < ox + shift and oy - shift < y < oy + shift:
                        continue
                    for a in grid.get((x, y), ()):
                        if id(a) in seen:
                            continue
                        seen.add(id(a))
                        c = a.closest_point(source)
                        d = (c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2 + (c[2] - pos[2]) ** 2
                        if d >= close_d:
                            continue
                        close, close_d = a, d
                        limit = shift + 1
            shift += 1
        return close

    def connect_ladders(self):
        """ConnectGeneratedLadder for every ladder, once the areas are final."""
        self._index_areas()
        near = 75.0
        for lad in self.ladders:
            centre = add_dir((lad.bottom[0], lad.bottom[1], lad.bottom[2] + GENERATION_STEP), lad.dir, HALF_HUMAN_WIDTH)
            lad.bottom_area = self.get_nearest_nav_area(centre)
            if lad.bottom_area is not None:
                lad.bottom_area.add_ladder(lad, True)
            centre = add_dir((lad.top[0], lad.top[1], lad.top[2] + GENERATION_STEP), lad.dir, HALF_HUMAN_WIDTH)
            beneath = min(120.0, lad.top[2] - lad.bottom[2] + HALF_HUMAN_WIDTH)

            def first(d, rng):
                area, _p = self.find_first_area_in_direction(centre, d, rng, beneath)
                return None if area is lad.bottom_area else area
            lad.top_forward = first(OPPOSITE[lad.dir], near)
            lad.top_left = first((lad.dir + 3) % 4, near)
            lad.top_right = first((lad.dir + 1) % 4, near)
            lad.top_behind = first(lad.dir, 2.0 * near)
            for a in (lad.top_forward, lad.top_left, lad.top_right):
                if a is not None:
                    a.add_ladder(lad, False)
            if lad.top_behind is not None:
                behind = lad.top_behind
                behind.add_ladder(lad, False)
                # CNavLadder::Disconnect: the ladder forgets the first of its areas that is this one
                # (the area keeps its reference to the ladder)
                for attr in ("top_forward", "top_left", "top_right", "top_behind"):
                    if getattr(lad, attr) is behind:
                        setattr(lad, attr, None)
                        break
            tops = [lad.top_forward, lad.top_left, lad.top_right, lad.top_behind]
            top_z, adjusted = lad.bottom[2] + 5.0, False
            for a in tops:
                if a is not None:
                    z = a.closest_point(lad.top)[2]
                    if top_z < z:
                        top_z, adjusted = z, True
            if adjusted:
                lad.top = (lad.top[0], lad.top[1], top_z)
            if lad.bottom_area is not None:       # "dangling": the bottom is out of reach
                spot = lad.bottom_area.closest_point(lad.bottom)
                if lad.bottom[2] - spot[2] > HUMAN_HEIGHT:
                    for lst in lad.bottom_area.ladders:
                        if lad in lst:
                            lst.remove(lad)

    # ------------------------------------------------ hand-made climb links
    def add_climb(self, bottom, top) -> str | None:
        """One-way link from the area under 'bottom' up to the area under 'top' (what mappers
        add by hand in the game's nav editor so zombies climb a wall). None, or what's wrong."""
        self._index_areas()
        low = self.get_nav_area(bottom) or self.get_nearest_nav_area(bottom, 64.0)
        high = self.get_nav_area(top) or self.get_nearest_nav_area(top, 64.0)
        if low is None:
            return "no nav mesh under the bottom point"
        if high is None:
            return "no nav mesh under the top point"
        if low is high:
            return "bottom and top are on the same nav area"
        from .nav import climb_direction
        low.connect_to(high, climb_direction(bottom, top))
        return None

    def add_wall_climbs(self, max_height: float) -> int:
        """'Zombies Climb Walls': a climb link up every wall 19..max_height units high (commons
        climb a nav link like that; see Zombie Climb), and a way back down where there is none
        (L4D2 makes no drop link for 19-64 unit ledges). Only where a wall really stands between
        the two: walking straight ahead from the low side must be blocked. Returns links added."""
        self._index_areas()
        added = 0
        for low in list(self.areas):
            for d in range(4):
                if d in (NORTH, SOUTH):
                    y = low.nw[1] if d == NORTH else low.se[1]
                    spots = [(x, y) for x in _steps(low.nw[0], low.se[0])]
                else:
                    x = low.se[0] if d == EAST else low.nw[0]
                    spots = [(x, y) for y in _steps(low.nw[1], low.se[1])]
                for x, y in spots:
                    z = low.z_at(x, y)
                    for dist in (GENERATION_STEP * 0.5, GENERATION_STEP, GENERATION_STEP * 2):
                        q = _add_dir((x, y, z + max_height + 1.0), d, dist)
                        high = self.get_nav_area(q, max_height + 1.0 - STEP_HEIGHT)
                        if high is None or high is low:
                            continue
                        dz = high.z_at(q[0], q[1]) - z
                        if not STEP_HEIGHT < dz <= max_height:
                            continue
                        if low.is_connected(high, d):
                            break
                        start = (x, y, z + STEP_HEIGHT + 1.0)
                        if self.hull(start, (q[0], q[1], start[2])).fraction >= 1.0:
                            break           # nothing to climb: open space under a higher floor
                        low.connect_to(high, d)
                        added += 1
                        if not high.is_connected(low, OPPOSITE[d]):
                            high.connect_to(low, OPPOSITE[d])
                            added += 1
                        break
        return added

    # ------------------------------------------------ StichAndRemoveJumpAreas
    def _try_connect_many(self, jump: Area, sources, dest, out_dir):
        for src in list(sources):
            if not src.is_connected(jump, out_dir):
                continue
            if src.attributes & NAV_MESH_JUMP:
                inc = OPPOSITE[out_dir]
                self._try_connect_many(jump, src.incoming[inc], dest, out_dir)
                self._try_connect_many(jump, src.connect[inc], dest, out_dir)
                continue
            self._try_connect(src, dest, out_dir)

    @staticmethod
    def _try_connect(src: Area, dest, out_dir):
        for dst in list(dest):
            if dst.attributes & NAV_MESH_JUMP:
                continue
            centre, half = src.portal(dst, out_dir)
            if half <= 0.0:
                continue
            sp, dp = src.closest_point(centre), dst.closest_point(centre)
            if src.attributes & NAV_MESH_STAIRS and sp[2] + STEP_HEIGHT < dp[2]:
                continue
            if math.hypot(sp[0] - dp[0], sp[1] - dp[1]) < GENERATION_STEP * 3:
                src.connect_to(dst, out_dir)

    def stitch_and_remove_jump_areas(self):
        for jump in list(self.areas):
            if not jump.attributes & NAV_MESH_JUMP:
                continue
            for inc in range(4):
                out = OPPOSITE[inc]
                self._try_connect_many(jump, jump.incoming[inc], jump.connect[out], out)
                self._try_connect_many(jump, jump.connect[inc], jump.connect[out], out)
        dead = [a for a in self.areas if a.attributes & NAV_MESH_JUMP]
        for a in dead:
            self.areas.remove(a)
            for other in self.areas:
                other.forget(a)
        self._index_areas()

    def generate(self):
        self.sample()
        self.create_areas()
        self.connect_areas()
        self.mark_jump_areas()
        self.merge_areas()
        self.split_areas_under_overhangs()
        self.square_up_areas()
        self.mark_stair_areas()
        self.stitch_and_remove_jump_areas()
        # (HandleObstacleTopAreas: L4D2 makes no fence-top areas; measured, so skipped)
        self.fix_corner_on_corner_areas()
        self.fix_connections()
        self.connect_ladders()
        return self.areas
