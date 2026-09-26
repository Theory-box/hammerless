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
                     SOUTH_EAST, SOUTH_WEST, STEP_HEIGHT, STEP_XY, WEST, Node, Sampler)

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
            while n is not None and id(n) not in run and id(n) not in seen and n.closed_cell():
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
                if horiz.covered or not horiz.closed_cell():
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
        area = Area(len(self.areas) + 1, node.pos, se.pos, ne.pos[2], sw.pos[2], [node, ne, se, sw])
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
        for other in self.areas:
            if other is area or other is adj:
                continue
            for d in range(4):
                if adj in other.connect[d]:
                    other.disconnect(adj)
                    other.disconnect(area)
                    other.connect_to(area, d)
        self.areas.remove(adj)
        for other in self.areas:
            other.forget(adj)

    MERGE_TESTS = (
        (NORTH, 1, lambda a, b: a.nodes[0] is b.nodes[3] and a.nodes[1] is b.nodes[2], (0, 1), (0, 1)),
        (SOUTH, 1, lambda a, b: b.nodes[0] is a.nodes[3] and b.nodes[1] is a.nodes[2], (3, 2), (3, 2)),
        (WEST, 0, lambda a, b: a.nodes[0] is b.nodes[1] and a.nodes[3] is b.nodes[2], (0, 3), (0, 3)),
        (EAST, 0, lambda a, b: b.nodes[0] is a.nodes[1] and b.nodes[3] is a.nodes[2], (1, 2), (1, 2)),
    )

    def merge_areas(self):
        limit = GENERATION_STEP * AREA_MAX_SIZE
        merged = True
        while merged:
            merged = False
            for area in self.areas:
                if not area.nodes or area.attributes & NAV_MESH_NO_MERGE:
                    continue
                for d, axis, test, mine, theirs in self.MERGE_TESTS:
                    for adj in list(area.connect[d]):
                        if not self._can_merge(area, adj):
                            continue
                        size = (area.size_y + adj.size_y) if axis == 1 else (area.size_x + adj.size_x)
                        if size > limit:
                            continue
                        if test(area, adj) and area.attributes == adj.attributes and area.is_coplanar(adj):
                            area.nodes[mine[0]], area.nodes[mine[1]] = adj.nodes[theirs[0]], adj.nodes[theirs[1]]
                            self._finish_merge(area, adj)
                            merged = True
                            break
                    if merged:
                        break
                if merged:
                    break
        self._index_areas()

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
        self.stitch_and_remove_jump_areas()
        return self.areas
