"""Our own nav mesh generator, following the algorithm in Valve's published Source SDK
2013 nav code (nav_generate.cpp, nav_node.cpp) with Left 4 Dead's settings. This is
an independent reimplementation, checked against the game's own .nav output.

Stage A (this file so far): sampling walkable space. From seed points the generator
floods outward in 25-unit steps; each step sweeps a thin column (0.9 x 0.9 x 55
units) to the next grid point, drops to the floor below, and if blocked tries
climbing up to 200 units. The result is a graph of nodes: where the nav mesh can
exist and which steps connect.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .collision import CollisionWorld, Trace

GENERATION_STEP = 25.0
STEP_HEIGHT = 18.0
DEATH_DROP = 400.0              # TERROR: zombies take no fall damage
CLIMB_UP_HEIGHT = 200.0
HALF_HUMAN_HEIGHT = 35.5
HUMAN_HEIGHT = 71.0
DUCK_HULL_TOP = 55.0            # VEC_DUCK_HULL_MAX.z; start height for FindGroundForNode
SLOPE_LIMIT = 0.7               # nav_slope_limit
MAX_TRAVERSABLE_HEIGHT = STEP_HEIGHT
NODE_Z_TOLERANCE = 0.45 * GENERATION_STEP
COMMUTATIVE_Z = 50.0            # AddNode: links both ways when heights differ less than this
DISPLACEMENT_TEST = 10000.0     # nav_displacement_test
TRACE_MINS = (-0.45, -0.45, 0.0)
TRACE_MAXS = (0.45, 0.45, 55.0)  # HumanCrouchHeight

JUMP_CROUCH_HEIGHT = 64.0      # TERROR (non-CS) value
HALF_HUMAN_WIDTH = 16.0
HUMAN_CROUCH_HEIGHT = 55.0
AREA_MAX_SIZE = 50              # nav_area_max_size
OFF_PLANE_TOLERANCE = 5.0
NAV_MESH_CROUCH = 0x0001
NAV_MESH_NO_MERGE = 0x2000
NAV_MESH_CLIFF = 0x8000
CLIFF_HEIGHT = 300.0

NORTH, EAST, SOUTH, WEST = range(4)
NORTH_WEST, NORTH_EAST, SOUTH_EAST, SOUTH_WEST = range(4)
CORNER_VEC = {NORTH_WEST: (-1, -1), NORTH_EAST: (1, -1), SOUTH_EAST: (1, 1), SOUTH_WEST: (-1, 1)}
OPPOSITE = (SOUTH, WEST, NORTH, EAST)
STEP_XY = {NORTH: (0.0, -GENERATION_STEP), EAST: (GENERATION_STEP, 0.0),
           SOUTH: (0.0, GENERATION_STEP), WEST: (-GENERATION_STEP, 0.0)}


def round_to_units(val: float, unit: float) -> float:
    """nav.h RoundToUnits (truncating integer division, as in C)."""
    val = val + (-unit * 0.5 if val < 0.0 else unit * 0.5)
    return float(unit * int(int(val) / int(unit)))


def add_dir(pos, d: int, amount: float):
    """nav.h AddDirectionVector."""
    dx, dy = STEP_XY[d]
    return (pos[0] + dx / GENERATION_STEP * amount, pos[1] + dy / GENERATION_STEP * amount, pos[2])


MIN_LADDER_CLEARANCE = 32.0


@dataclass(eq=False)
class Ladder:
    """CNavLadder, as nav generation makes it from a func_simpleladder's bounds."""
    top: tuple[float, float, float]
    bottom: tuple[float, float, float]
    width: float
    dir: int
    length: float = 0.0
    bottom_area: object = None
    top_forward: object = None
    top_left: object = None
    top_right: object = None
    top_behind: object = None

    @property
    def normal(self):
        return add_dir((0.0, 0.0, 0.0), self.dir, 1.0)


@dataclass(eq=False)
class Node:
    pos: tuple[float, float, float]
    normal: tuple[float, float, float]
    parent: "Node | None" = None
    id: int = 0
    to: list = field(default_factory=lambda: [None, None, None, None])
    obstacle: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    visited: int = 0
    attributes: int = 0
    crouch: list = field(default_factory=lambda: [False, False, False, False])
    blocked: list = field(default_factory=lambda: [False, False, False, False])
    crouch_checked: bool = False
    cliff_checked: bool = False
    covered: bool = False
    area: "Area | None" = None
    on_disp: bool = False
    closed: bool = False           # closed_cell(), cached once sampling is done
    ground: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])   # m_groundHeightAboveNode

    def blocked_any(self) -> bool:
        return any(self.blocked)

    def bi_linked(self, d: int) -> bool:
        """Linked both ways, for building areas. Measured in L4D2 (differs from the 2013 SDK
        code): nodes more than a step (18) apart in height never share an area, so the game
        makes no area and no walk connection across a 19-64 unit ledge (drops over 64 get a
        one-way jump-down link instead)."""
        n = self.to[d]
        return (n is not None and n.to[OPPOSITE[d]] is self
                and abs(n.pos[2] - self.pos[2]) <= STEP_HEIGHT)

    def closed_cell(self) -> bool:
        """IsClosedCell: NW corner of a quad of nodes all linked both ways."""
        if not (self.bi_linked(SOUTH) and self.bi_linked(EAST)):
            return False
        e, s = self.to[EAST], self.to[SOUTH]
        return e.bi_linked(SOUTH) and s.bi_linked(EAST) and e.to[SOUTH] is s.to[EAST]

    def has_visited(self, d: int) -> bool:
        return bool(self.visited & (1 << d))

    def mark_visited(self, d: int):
        self.visited |= 1 << d


class Sampler:
    def __init__(self, world: CollisionWorld):
        self.world = world
        self.nodes: list[Node] = []
        self.hash: dict[tuple[float, float], list[Node]] = {}
        self.seeds: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
        self.cuts: list = []          # No Nav Volumes (nav.nav_cuts): no node is made inside one
        self.raw_seeds: list[tuple[float, float, float]] = []
        self.native = None           # None: use the DLL when it loads; False: always Python
        self.ladders: list[Ladder] = []

    # ------------------------------------------------------------ traces
    def hull(self, start, end) -> Trace:
        return self.world.trace_hull(start, end, TRACE_MINS, TRACE_MAXS)

    def find_ground(self, pos):
        """FindGroundForNode: returns (pos, normal, ok)."""
        start = (pos[0], pos[1], pos[2] + DUCK_HULL_TOP - 0.1)
        end = (pos[0], pos[1], pos[2] - DEATH_DROP)
        tr = self.hull(start, end)
        return tr.endpos, tr.normal, not tr.allsolid

    def stay_on_floor(self, tr: Trace, z_limit: float = DEATH_DROP) -> tuple[bool, Trace]:
        start = tr.endpos
        tr = self.hull(start, (start[0], start[1], start[2] - z_limit))
        if tr.startsolid or tr.fraction >= 1.0:
            return False, tr
        if tr.normal[2] < SLOPE_LIMIT:
            return False, tr
        return True, tr

    def trace_adjacent(self, depth, start, end, z_limit: float = DEATH_DROP) -> tuple[bool, Trace]:
        tr = self.hull(start, end)
        if tr.startsolid:
            return False, tr
        if end[0] == tr.endpos[0] and end[1] == tr.endpos[1]:
            return self.stay_on_floor(tr, z_limit)
        dx, dy = tr.endpos[0] - start[0], tr.endpos[1] - start[1]
        if depth and dx * dx + dy * dy < 1.0:
            return False, tr
        ok, tr = self.stay_on_floor(tr, z_limit)
        if not ok:
            return False, tr
        top = tr.endpos
        tr = self.hull(top, (top[0], top[1], top[2] + STEP_HEIGHT))
        fwd = tr.endpos
        return self.trace_adjacent(depth + 1, fwd, (end[0], end[1], fwd[2]))

    def node_overlapped(self, pos, ox, oy) -> bool:
        """IsNodeOverlapped (no existing areas during a full generation: only the traces)."""
        mins, maxs = (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)
        start = (pos[0], pos[1], pos[2] + HALF_HUMAN_HEIGHT)
        end = (start[0] + ox * GENERATION_STEP, start[1] + oy * GENERATION_STEP, start[2])
        tr = self.world.trace_hull(start, end, mins, maxs)
        if tr.startsolid or tr.allsolid or tr.fraction < 0.1:
            return True
        start = tr.endpos
        end = (end[0], end[1], end[2] - HALF_HUMAN_HEIGHT * 2)
        tr = self.world.trace_hull(start, end, mins, maxs)
        return tr.startsolid or tr.allsolid or tr.fraction == 1.0 or tr.normal[2] < 0.7

    # ------------------------------------------------------------ nodes
    def get_node(self, pos) -> Node | None:
        for n in self.hash.get((pos[0], pos[1]), ()):
            if abs(n.pos[2] - pos[2]) < NODE_Z_TOLERANCE:
                return n
        return None

    def new_node(self, pos, normal, parent, on_disp: bool = False) -> Node:
        n = Node(tuple(pos), tuple(normal), parent, len(self.nodes) + 1, on_disp=on_disp)
        self.nodes.append(n)
        self.hash.setdefault((n.pos[0], n.pos[1]), []).insert(0, n)   # newest first, like m_nextAtXY
        return n

    def add_seed(self, pos):
        """AddWalkableSeeds for one spot: snap to the grid, find the ground under it."""
        self.raw_seeds.append(tuple(pos))
        p = (round_to_units(pos[0], GENERATION_STEP), round_to_units(pos[1], GENERATION_STEP), pos[2])
        p, normal, ok = self.find_ground(p)
        if ok and self.cuts:
            from .nav import in_cut
            ok = not in_cut(self.cuts, p)
        if ok:
            self.seeds.append(((round_to_units(p[0], GENERATION_STEP), round_to_units(p[1], GENERATION_STEP), p[2]),
                               normal))

    # ------------------------------------------------------------ ladders
    def line(self, start, end) -> Trace:
        return self.world.trace_hull(start, end, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

    def add_ladder(self, mins, maxs):
        """BuildLadders / CreateLadder for one func_simpleladder (mins/maxs: its world bounds,
        which the engine pads by a unit on each side)."""
        top = ((mins[0] + maxs[0]) / 2.0, (mins[1] + maxs[1]) / 2.0, maxs[2])
        bottom = (top[0], top[1], mins[2])
        xs, ys = maxs[0] - mins[0], maxs[1] - mins[1]
        half = GENERATION_STEP / 2
        if xs > ys:
            tr = self.line((bottom[0], bottom[1] + GENERATION_STEP, bottom[2] + half),
                           (top[0], top[1] + GENERATION_STEP, top[2] - half))
            d, width = (NORTH if tr.fraction != 1.0 or tr.startsolid else SOUTH), xs
        else:
            tr = self.line((bottom[0] + GENERATION_STEP, bottom[1], bottom[2] + half),
                           (top[0] + GENERATION_STEP, top[1], top[2] - half))
            d, width = (WEST if tr.fraction != 1.0 or tr.startsolid else EAST), ys
        lad = Ladder(top, bottom, width, d)
        n = lad.normal
        along = (top[0] - bottom[0], top[1] - bottom[1], top[2] - bottom[2])
        length = (along[0] ** 2 + along[1] ** 2 + along[2] ** 2) ** 0.5
        along = tuple(v / length for v in along) if length else along

        def clear(on):
            out = (on[0] + n[0] * MIN_LADDER_CLEARANCE, on[1] + n[1] * MIN_LADDER_CLEARANCE, on[2])
            tr = self.line(on, out)
            return tr.fraction == 1.0 and not tr.startsolid
        t = 0.0
        while t <= length:            # move the bottom up past anything in front of it
            on = tuple(bottom[i] + t * along[i] for i in range(3))
            if clear(on):
                lad.bottom = on
                break
            t += 10.0
        t = 0.0
        while t <= length:            # and the top down
            on = tuple(top[i] - t * along[i] for i in range(3))
            if clear(on):
                lad.top = on
                break
            t += 10.0
        lad.length = sum((lad.top[i] - lad.bottom[i]) ** 2 for i in range(3)) ** 0.5
        self.ladders.append(lad)
        return lad

    def ground_height(self, pos):
        """CNavMesh::GetGroundHeight: (ok, z, normal) of the first floor below with room to stand."""
        to_z, from_z = pos[2] - 10000.0, pos[2] + HALF_HUMAN_HEIGHT + 1e-3
        while to_z - pos[2] < 100.0:
            tr = self.line((pos[0], pos[1], from_z), (pos[0], pos[1], to_z))
            if not tr.startsolid and (tr.fraction == 1.0 or from_z - tr.endpos[2] >= HALF_HUMAN_HEIGHT):
                n = tr.normal if any(tr.normal) else (0.0, 0.0, 1.0)
                return True, tr.endpos[2], n
            to_z = from_z if tr.startsolid else tr.endpos[2]
            from_z = to_z + HALF_HUMAN_HEIGHT + 1e-3
        return False, 0.0, (0.0, 0.0, 1.0)

    def ladder_end_search(self, pos, mount_dir: int, has_node):
        """LadderEndSearch: a new place to continue sampling next to a ladder's end."""
        center = add_dir(pos, mount_dir, HALF_HUMAN_WIDTH)
        for k in range(-1, 8):
            t = center
            if k >= 4:
                t = add_dir(t, k - 4, 2.0 * GENERATION_STEP)
            elif k >= 0:
                t = add_dir(t, k, GENERATION_STEP)
            t = (round_to_units(t[0], GENERATION_STEP), round_to_units(t[1], GENERATION_STEP), t[2] + GENERATION_STEP)
            ok, z, normal = self.ground_height(t)
            if not ok:
                continue
            t = (t[0], t[1], z)
            tr = self.hull((center[0], center[1], center[2] + 4.0), (t[0], t[1], t[2] + 4.0))
            if tr.fraction != 1.0 or tr.startsolid:
                continue
            if not has_node(t):
                return t, normal
        return None

    def next_ladder_seed(self, has_node):
        """Sampling from the seeds is exhausted: carry on from the ends of ladders."""
        from .nav import in_cut
        for lad in self.ladders:
            for end in (lad.bottom, lad.top):
                found = self.ladder_end_search(end, lad.dir, has_node)
                if found and not (self.cuts and in_cut(self.cuts, found[0])):
                    return found
        return None

    def add_node(self, dest, normal, d, source: Node, obstacle_height: float, on_disp: bool = False):
        node = self.get_node(dest)
        new = node is None
        if new:
            node = self.new_node(dest, normal, source, on_disp)
        source.to[d] = node
        source.obstacle[d] = obstacle_height
        dz = source.pos[2] - dest[2]
        if abs(dz) < COMMUTATIVE_Z:
            if obstacle_height > 0:
                obstacle_height = max(obstacle_height + dz, 0.0)
            node.to[OPPOSITE[d]] = source
            node.obstacle[OPPOSITE[d]] = obstacle_height
            node.mark_visited(OPPOSITE[d])
        self.check_crouch(node)
        if not node.cliff_checked:
            node.cliff_checked = True
            for d in range(4):
                if self.check_cliff(node.pos, d):
                    node.attributes |= NAV_MESH_CLIFF
                    break
        return node if new else None

    def check_cliff(self, pos, d, exhaustive: bool = True) -> bool:
        """CheckCliff: would stepping this way drop more than 300 units? The 2013 SDK code
        switches this off; L4D2 has it on (its nav marks cliff edges, which then form their own
        narrow areas)."""
        to = (pos[0] + STEP_XY[d][0], pos[1] + STEP_XY[d][1], pos[2])
        ok, tr = self.trace_adjacent(0, pos, to, DEATH_DROP * 10)
        if ok and not tr.allsolid and not tr.startsolid:
            dz = pos[2] - tr.endpos[2]
            if dz > CLIFF_HEIGHT:
                return True
            if d in (SOUTH, EAST) and abs(dz) < STEP_HEIGHT and exhaustive:
                return self.check_cliff(tr.endpos, d, False)
        return False

    def check_crouch(self, node: Node):
        """CNavNode::CheckCrouch: can a standing (71) or crouching (55) player fit at each corner?"""
        if node.crouch_checked:
            return
        node.crouch_checked = True
        for corner in range(4):
            vx, vy = CORNER_VEC[corner]
            mins = (min(0.0, vx * HALF_HUMAN_WIDTH), min(0.0, vy * HALF_HUMAN_WIDTH), 0.0)
            maxs = (max(0.0, vx * HALF_HUMAN_WIDTH), max(0.0, vy * HALF_HUMAN_WIDTH))
            if not self.test_crouch_area(node, corner, mins, maxs):
                node.attributes |= NAV_MESH_CROUCH
                node.crouch[corner] = True

    def test_crouch_area(self, node: Node, corner: int, mins, maxs_xy) -> bool:
        p = node.pos
        tr = self.hull(p, (p[0], p[1], p[2] + JUMP_CROUCH_HEIGHT))
        max_height = tr.endpos[2] - p[2]
        h = 0.0
        while h <= max_height:
            start = (p[0], p[1], p[2] + h)
            t = self.world.trace_hull(start, start, mins, (maxs_xy[0], maxs_xy[1], HUMAN_CROUCH_HEIGHT))
            if not t.startsolid:
                node.ground[corner] = start[2] - p[2]
                t = self.world.trace_hull(start, start, mins, (maxs_xy[0], maxs_xy[1], HUMAN_HEIGHT))
                return not t.startsolid
            h += 1.0
        node.ground[corner] = JUMP_CROUCH_HEIGHT
        node.blocked[corner] = True
        return False

    # ------------------------------------------------------------ SampleStep
    @property
    def node_count(self) -> int:
        return len(self.nodes) or getattr(self, "_native_count", 0)

    def sample(self, max_nodes: int = 500000, collect: bool = True) -> list[Node]:
        """collect=False (native only): the nodes stay in the DLL, for Generator.native_areas."""
        from . import fastnav
        if self.native is not False and fastnav.available():
            fastnav.start(self.world, self.raw_seeds, max_nodes, self.cuts)
            while self.ladders and fastnav.node_count() < max_nodes:
                found = self.next_ladder_seed(fastnav.has_node)
                if not found:
                    break
                fastnav.sample_from(found[0], found[1], max_nodes)
            if not collect:
                self.nodes, self.hash = [], {}
                self._native_count = fastnav.node_count()
                self.native_nodes = True
                return self.nodes
            self.nodes = fastnav.collect(self.world)
            self.native_nodes = True            # the DLL still holds these nodes (create_areas uses them)
            self.hash = {}
            for n in reversed(self.nodes):          # newest first per (x, y), like new_node
                self.hash.setdefault((n.pos[0], n.pos[1]), []).append(n)
            return self.nodes
        seed_i = 0
        current: Node | None = None
        while len(self.nodes) < max_nodes:
            if current is None:
                if seed_i >= len(self.seeds):
                    found = self.next_ladder_seed(lambda p: self.get_node(p) is not None)
                    if not found:
                        break
                    current = self.new_node(found[0], found[1], None)
                    continue
                pos, normal = self.seeds[seed_i]
                seed_i += 1
                if self.get_node(pos) is not None:
                    # The 2013 SDK code stops sampling here; L4D2 skips the covered seed and goes on
                    # (measured: a map in two separate halves gets nav on both)
                    continue
                current = self.new_node(pos, normal, None)
            d = next((d for d in range(4) if not current.has_visited(d)), None)
            if d is None:
                current = current.parent
                continue
            current.mark_visited(d)
            nxt = self.step(current, d)
            if nxt is not None:
                current = nxt
        return self.nodes

    def step(self, current: Node, d: int) -> Node | None:
        """One attempted move. Returns the new node if one was created (it becomes current)."""
        cx = round_to_units(current.pos[0], GENERATION_STEP) + STEP_XY[d][0]
        cy = round_to_units(current.pos[1], GENERATION_STEP) + STEP_XY[d][1]
        frm = current.pos
        pos = (cx, cy, frm[2])
        obstacle_height = 0.0
        ok, result = self.trace_adjacent(0, frm, pos)
        if ok:
            to, to_normal = result.endpos, result.normal
        else:
            success = False
            h = STEP_HEIGHT
            while h <= CLIMB_UP_HEIGHT:
                tr = self.hull((frm[0], frm[1], frm[2] + h), (pos[0], pos[1], pos[2] + h))
                if not tr.startsolid and tr.fraction == 1.0:
                    floor_ok, tr = self.stay_on_floor(tr)
                    if not floor_ok:
                        break
                    to, to_normal = tr.endpos, tr.normal
                    tr2 = self.hull(frm, (frm[0], frm[1], frm[2] + h))
                    if tr2.fraction < 1.0:
                        break
                    obstacle_height = h
                    success = True
                    break
                h += 1.0
            if not success:
                return None
        if result.sky:
            return None
        if (self.node_overlapped(to, 1, 1) and self.node_overlapped(to, -1, 1)
                and self.node_overlapped(to, 1, -1) and self.node_overlapped(to, -1, -1)):
            return None
        up = self.hull(to, (to[0], to[1], to[2] + DISPLACEMENT_TEST))
        if up.fraction > 0:
            down = self.hull(up.endpos, to)
            if down.fraction < 1 and down.endpos[2] > to[2] + STEP_HEIGHT:
                return None
        dz = to[2] - current.pos[2]
        if obstacle_height < MAX_TRAVERSABLE_HEIGHT or dz > obstacle_height - 2.0:
            obstacle_height = 0.0
        if self.cuts:
            from .nav import in_cut
            if in_cut(self.cuts, to):
                return None              # (a No Nav Volume: like a wall)
        return self.add_node(to, to_normal, d, current, obstacle_height, result.displacement)


