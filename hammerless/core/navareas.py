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
                     SOUTH_EAST, SOUTH_WEST, STEP_HEIGHT, STEP_XY, WEST, DUCK_HULL_TOP, Node, Sampler)

COPLANAR_SLOPE_LIMIT = 0.99       # nav_coplanar_slope_limit
COPLANAR_SLOPE_LIMIT_DISP = 0.7   # nav_coplanar_slope_limit_displacement
SLOPE_TOLERANCE = 0.1             # nav_slope_tolerance
NAV_MESH_JUMP = 0x0002
NAV_MESH_STAIRS = 0x1000


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
        return (bool(a.nodes) and not a.attributes & NAV_MESH_NO_MERGE
                and bool(b.nodes) and not b.attributes & NAV_MESH_NO_MERGE)

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
        failed and whose neighbourhood hasn't changed fails again, so we remember those ('clean')
        and only re-test areas a merge touched. Same merges, same order."""
        limit = GENERATION_STEP * AREA_MAX_SIZE
        clean: set[int] = set()
        while True:
            merged = None
            for area in self.areas:
                if id(area) in clean:
                    continue
                if not area.nodes or area.attributes & NAV_MESH_NO_MERGE:
                    clean.add(id(area))
                    continue
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
                            for o in touched + [area] + [o for dd in range(4) for o in area.connect[dd] + area.incoming[dd]]:
                                clean.discard(id(o))
                            merged = area
                            break
                    if merged:
                        break
                if merged:
                    break
                clean.add(id(area))
            if not merged:
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
        for other in self.areas:
            other.forget(area)
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
        return self.areas
