"""The compiled map's PVS (potentially visible set), read the way the engine reads it, for the
nav visibility analysis: CNavArea::SetupPVS adds the cluster of every sample point of an area
(engine->AddOriginToPVS) and ComputeVisibility first asks engine->CheckBoxInPVS whether the
other area's eye-height box is in it.

BSP v21 layout: planes (lump 1, 20 bytes), visibility (lump 4), nodes (lump 5, 32 bytes),
leafs (lump 10, version 1: 32 bytes), models (lump 14: the world model's head node).
All maths in the engine's 32-bit floats.
"""
from __future__ import annotations

import struct

_F = struct.Struct("<f")


def f32(v: float) -> float:
    return _F.unpack(_F.pack(v))[0]


class BspPVS:
    def __init__(self, bsp_path: str, fat: float = 0.0, solid_cluster: str = "skip"):
        """fat: AddOriginToPVS gathers the leaves in a box this far around the point (0: just
        the point's leaf). solid_cluster: what a leaf with cluster -1 contributes ('skip')."""
        with open(bsp_path, "rb") as f:
            b = f.read()
        if b[:4] != b"VBSP":
            raise ValueError("not a BSP")

        def lump(i):
            _ver, off, length, _cc = struct.unpack_from("<iiii", b, 8 + 16 * i)
            return b[off:off + length]
        planes = lump(1)
        self.planes = []
        for k in range(len(planes) // 20):
            nx, ny, nz, dist, typ = struct.unpack_from("<ffffi", planes, 20 * k)
            signbits = (1 if nx < 0 else 0) | (2 if ny < 0 else 0) | (4 if nz < 0 else 0)
            self.planes.append((nx, ny, nz, dist, typ, signbits))
        nodes = lump(5)
        self.nodes = [struct.unpack_from("<iii", nodes, 32 * k) for k in range(len(nodes) // 32)]
        leafs = lump(10)
        self.leaf_cluster = [struct.unpack_from("<ih", leafs, 32 * k)[1] for k in range(len(leafs) // 32)]
        models = lump(14)
        self.headnode = struct.unpack_from("<i", models, 36)[0]
        vis = lump(4)
        self.numclusters = struct.unpack_from("<i", vis, 0)[0] if vis else 0
        self.vis = vis
        self.rowbytes = (self.numclusters + 7) >> 3
        self.fat = fat
        self.solid_cluster = solid_cluster
        self._rows: dict[int, bytes] = {}

    # ---------------------------------------------------------------- tree walks
    def point_leaf(self, p) -> int:
        """CM_PointLeafnum: d < 0 goes to the back child."""
        num = self.headnode
        while num >= 0:
            planenum, front, back = self.nodes[num]
            nx, ny, nz, dist, typ, _sb = self.planes[planenum]
            if typ < 3:
                d = f32(p[typ] - dist)
            else:
                d = f32(f32(f32(f32(nx * p[0]) + f32(ny * p[1])) + f32(nz * p[2])) - dist)
            num = back if d < 0 else front
        return -1 - num

    @staticmethod
    def _box_side(lo, hi, plane) -> int:
        """BOX_ON_PLANE_SIDE: 1 front, 2 back, 3 both."""
        nx, ny, nz, dist, typ, sb = plane
        if typ < 3:
            if dist <= lo[typ]:
                return 1
            if dist >= hi[typ]:
                return 2
            return 3
        n = (nx, ny, nz)
        far = [hi[i] if not sb & (1 << i) else lo[i] for i in range(3)]
        near = [lo[i] if not sb & (1 << i) else hi[i] for i in range(3)]
        d1 = f32(f32(f32(n[0] * far[0]) + f32(n[1] * far[1])) + f32(n[2] * far[2]))
        d2 = f32(f32(f32(n[0] * near[0]) + f32(n[1] * near[1])) + f32(n[2] * near[2]))
        sides = 0
        if d1 >= dist:
            sides = 1
        if d2 < dist:
            sides |= 2
        return sides

    def box_leaves(self, lo, hi) -> list[int]:
        """CM_BoxLeafnums: every leaf the box touches (front child first)."""
        out, stack = [], [self.headnode]
        while stack:
            num = stack.pop()
            while True:
                if num < 0:
                    out.append(-1 - num)
                    break
                planenum, front, back = self.nodes[num]
                s = self._box_side(lo, hi, self.planes[planenum])
                if s == 1:
                    num = front
                elif s == 2:
                    num = back
                else:
                    stack.append(back)
                    num = front
        return out

    # ---------------------------------------------------------------- PVS rows
    def cluster_row(self, cluster: int) -> bytes:
        """Decompressed PVS row of a cluster (run-length zeros: a 0 byte, then a count)."""
        row = self._rows.get(cluster)
        if row is not None:
            return row
        if cluster < 0 or cluster >= self.numclusters:
            row = bytes(self.rowbytes)
        else:
            ofs = struct.unpack_from("<i", self.vis, 4 + 8 * cluster)[0]
            out = bytearray()
            i = ofs
            while len(out) < self.rowbytes:
                c = self.vis[i]
                if c:
                    out.append(c)
                    i += 1
                else:
                    out.extend(bytes(self.vis[i + 1]))
                    i += 2
            row = bytes(out[:self.rowbytes])
        self._rows[cluster] = row
        return row

    def new_pvs(self) -> bytearray:
        return bytearray(self.rowbytes)

    def add_origin(self, pvs: bytearray, origin) -> None:
        """engine->AddOriginToPVS."""
        if self.fat:
            lo = [f32(origin[i] - self.fat) for i in range(3)]
            hi = [f32(origin[i] + self.fat) for i in range(3)]
            leaves = self.box_leaves(lo, hi)
        else:
            leaves = [self.point_leaf(origin)]
        for leaf in leaves:
            cluster = self.leaf_cluster[leaf]
            if cluster < 0:
                if self.solid_cluster == "all":
                    for k in range(len(pvs)):
                        pvs[k] = 0xFF
                continue
            row = self.cluster_row(cluster)
            for k in range(len(pvs)):
                pvs[k] |= row[k]

    def box_in_pvs(self, lo, hi, pvs: bytes) -> bool:
        """engine->CheckBoxInPVS (CM_BoxVisible): any leaf of the box with its cluster in the PVS."""
        for leaf in self.box_leaves(lo, hi):
            cluster = self.leaf_cluster[leaf]
            if cluster < 0:
                continue
            if pvs[cluster >> 3] & (1 << (cluster & 7)):
                return True
        return False


def bsp_brushes(bsp_path: str, model: int | None = 0, mask: int = 0x4041):
    """The compiled map's own collision brushes (exact planes, bevels and contents), as
    collision.CollisionBrush objects, keeping those whose contents meet 'mask'. model: the
    brushes of one BSP model (0 = world, incl. detail), or None for every brush."""
    from .collision import CollisionBrush, Side
    with open(bsp_path, "rb") as f:
        b = f.read()

    def lump(i):
        _v, off, length, _c = struct.unpack_from("<iiii", b, 8 + 16 * i)
        return b[off:off + length]
    planes = lump(1)
    P = [struct.unpack_from("<ffff", planes, 20 * k) for k in range(len(planes) // 20)]
    brushes, bsides = lump(18), lump(19)
    wanted = None
    if model is not None:
        # the brushes reachable from the model's head node through its leaves
        models, nodes, leafs, leafbrushes = lump(14), lump(5), lump(10), lump(17)
        head = struct.unpack_from("<i", models, 48 * model + 36)[0]
        wanted, stack = set(), [head]
        while stack:
            num = stack.pop()
            if num < 0:
                leaf = -1 - num
                first, count = struct.unpack_from("<HH", leafs, 32 * leaf + 24)
                for j in range(count):
                    wanted.add(struct.unpack_from("<H", leafbrushes, 2 * (first + j))[0])
            else:
                _pl, front, back = struct.unpack_from("<iii", nodes, 32 * num)
                stack += [front, back]
    out = []
    for k in range(len(brushes) // 12):
        if wanted is not None and k not in wanted:
            continue
        first, num, contents = struct.unpack_from("<iii", brushes, 12 * k)
        if not contents & mask:
            continue
        sides = []
        for j in range(num):
            planenum, _tex, _disp, bevel, _thin = struct.unpack_from("<HhhBB", bsides, 8 * (first + j))
            nx, ny, nz, dist = P[planenum]
            sides.append(Side((nx, ny, nz), dist, "", bool(bevel)))
        # bounds from the axial planes (every compiled brush has all six)
        lo, hi = [-1e9] * 3, [1e9] * 3
        for s in sides:
            for i in range(3):
                if s.normal[i] == 1.0:
                    hi[i] = s.dist
                elif s.normal[i] == -1.0:
                    lo[i] = -s.dist
        out.append(CollisionBrush(sides, tuple(lo), tuple(hi), f"bsp brush {k} contents {contents:#x}"))
    return out
