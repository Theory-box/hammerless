"""Nav generator stage B: nav areas from the sampled nodes, and their connections.

Follows CreateNavAreasFromNodes / TestArea / BuildArea / ConnectGeneratedAreas and
the jump-down search (findJumpDownArea, testJumpDown) from Valve's Source SDK 2013
nav code, reimplemented independently. Merging and the later clean-up passes come
after this.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .navgen import (AREA_MAX_SIZE, CLIMB_UP_HEIGHT, DEATH_DROP, EAST, GENERATION_STEP, HALF_HUMAN_HEIGHT,
                     HUMAN_CROUCH_HEIGHT, JUMP_CROUCH_HEIGHT, MAX_TRAVERSABLE_HEIGHT, NAV_MESH_CROUCH,
                     NAV_MESH_NO_MERGE, NORTH, NORTH_EAST, OFF_PLANE_TOLERANCE, SOUTH, SOUTH_EAST, SOUTH_WEST,
                     STEP_HEIGHT, STEP_XY, WEST, Node, Sampler)


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
        if other is self or other in self.connect[d]:
            return
        self.connect[d].append(other)

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
        normal, pos = node.normal, node.pos
        d = -(normal[0] * pos[0] + normal[1] * pos[1] + normal[2] * pos[2])

        def off_plane(n: Node) -> bool:
            q = n.pos
            return abs(q[0] * normal[0] + q[1] * normal[1] + q[2] * normal[2] + d) > OFF_PLANE_TOLERANCE

        node_crouch = node.crouch[SOUTH_EAST]
        if node.blocked[SOUTH_EAST]:
            return False
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
                        return False
                elif north:
                    hc = horiz.crouch[SOUTH_EAST] or horiz.crouch[SOUTH_WEST]
                    if horiz.blocked[SOUTH_EAST] or horiz.blocked[SOUTH_WEST]:
                        return False
                elif west:
                    hc = horiz.crouch[SOUTH_EAST] or horiz.crouch[NORTH_EAST]
                    if horiz.blocked[SOUTH_EAST] or horiz.blocked[NORTH_EAST]:
                        return False
                else:                # south-east, south, east, interior
                    hc = bool(horiz.attributes & NAV_MESH_CROUCH)
                    if horiz.blocked_any():
                        return False
                if node_crouch != hc:
                    return False
                if (horiz.attributes & ~NAV_MESH_CROUCH) != node_attr:
                    return False
                if horiz.covered or not horiz.closed_cell():
                    return False
                if not self._check_obstacles(horiz, width, height, x, y):
                    return False
                horiz = horiz.to[EAST]
                if horiz is None:
                    return False
                if multi and off_plane(horiz):
                    return False
            if not self._check_obstacles(horiz, width, height, width, y):
                return False
            vert = vert.to[SOUTH]
            if vert is None:
                return False
            if multi and off_plane(vert):
                return False
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
            for _y in range(height):
                horiz = vert
                for _x in range(width):
                    if not self._valid_crouch_area(horiz):
                        return False
                    horiz = horiz.to[EAST]
                vert = vert.to[SOUTH]
        return True

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
        while uncovered > 0:
            for node in order:
                if node.covered or east.get(id(node), 0) < width or south.get(id(node), 0) < height:
                    continue
                if self.test_area(node, width, height):
                    uncovered -= self.build_area(node, width, height)
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

    def generate(self):
        self.sample()
        self.create_areas()
        self.connect_areas()
        return self.areas
