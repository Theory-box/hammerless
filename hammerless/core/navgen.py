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
    covered: bool = False
    area: "Area | None" = None
    on_disp: bool = False

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

    def trace_adjacent(self, depth, start, end) -> tuple[bool, Trace]:
        tr = self.hull(start, end)
        if tr.startsolid:
            return False, tr
        if end[0] == tr.endpos[0] and end[1] == tr.endpos[1]:
            return self.stay_on_floor(tr)
        dx, dy = tr.endpos[0] - start[0], tr.endpos[1] - start[1]
        if depth and dx * dx + dy * dy < 1.0:
            return False, tr
        ok, tr = self.stay_on_floor(tr)
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
        p = (round_to_units(pos[0], GENERATION_STEP), round_to_units(pos[1], GENERATION_STEP), pos[2])
        p, normal, ok = self.find_ground(p)
        if ok:
            self.seeds.append(((round_to_units(p[0], GENERATION_STEP), round_to_units(p[1], GENERATION_STEP), p[2]),
                               normal))

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
        return node if new else None

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
                t = self.world.trace_hull(start, start, mins, (maxs_xy[0], maxs_xy[1], HUMAN_HEIGHT))
                return not t.startsolid
            h += 1.0
        node.blocked[corner] = True
        return False

    # ------------------------------------------------------------ SampleStep
    def sample(self, max_nodes: int = 500000) -> list[Node]:
        seed_i = 0
        current: Node | None = None
        while len(self.nodes) < max_nodes:
            if current is None:
                if seed_i >= len(self.seeds):
                    break
                pos, normal = self.seeds[seed_i]
                seed_i += 1
                if self.get_node(pos) is not None:
                    break           # GetNextWalkableSeedNode returns NULL here: sampling ends (sic)
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
        return self.add_node(to, to_normal, d, current, obstacle_height, result.displacement)


def sample_map(vmf_text: str, seed_positions) -> Sampler:
    s = Sampler(CollisionWorld.from_vmf(vmf_text))
    for p in seed_positions:
        s.add_seed(p)
    s.sample()
    return s
