"""Read and write Left 4 Dead 2 nav mesh files (maps/<map>.nav).

Layout: Source nav format version 16 as saved by L4D2 (sub-version 14). It matches
the layout in Valve's Source SDK 2013 (nav_file.cpp) with L4D2's additions:
a string + u16 before the areas, and per area a u32 of spawn attributes (CHECKPOINT,
PLAYER_START, OBSCURED...) plus a u16, stored between the light intensities and the
visibility list. Verified by round-tripping the game's own files byte for byte.

Coordinates are Hammer units. Source's "north" is -Y: nw_corner is the (min x, min y)
corner and se_corner the (max x, max y) corner.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = 0xFEEDFACE
NORTH, EAST, SOUTH, WEST = range(4)


@dataclass
class HidingSpot:
    id: int
    pos: tuple[float, float, float]
    flags: int


@dataclass
class Encounter:
    from_id: int
    from_dir: int
    to_id: int
    to_dir: int
    spots: list[tuple[int, int]] = field(default_factory=list)   # (spot id, t 0..255)


def _f32(v: float) -> float:
    """Round to a 32-bit float (the game's Vector maths)."""
    import struct
    return struct.unpack("<f", struct.pack("<f", v))[0]


@dataclass
class NavArea:
    id: int
    flags: int                                   # base attributes (crouch, jump, stairs...)
    nw: tuple[float, float, float]
    se: tuple[float, float, float]
    ne_z: float
    sw_z: float
    connections: list[list[int]] = field(default_factory=lambda: [[], [], [], []])  # by direction
    hiding_spots: list[HidingSpot] = field(default_factory=list)
    encounters: list[Encounter] = field(default_factory=list)
    place: int = 0
    ladders: list[list[int]] = field(default_factory=lambda: [[], []])  # up, down
    occupy: tuple[float, float] = (0.0, 0.0)
    light: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    spawn_attributes: int = 0                    # L4D2 TerrorNavArea attributes
    spawn_extra: int = 0                         # L4D2 u16 after them (always 0 so far)
    visible: list[tuple[int, int]] = field(default_factory=list)   # (area id, attributes)
    inherit_visibility: int = 0

    @property
    def corners(self) -> list[tuple[float, float, float]]:
        """NW, NE, SE, SW corners."""
        (x0, y0, z_nw), (x1, y1, z_se) = self.nw, self.se
        return [(x0, y0, z_nw), (x1, y0, self.ne_z), (x1, y1, z_se), (x0, y1, self.sw_z)]

    @property
    def centre(self) -> tuple[float, float, float]:
        """CNavArea::GetCenter: halfway between the NW and SE corners (their heights only), in
        the game's 32-bit floats. The Director, the visibility analysis and VScript's GetCenter
        all use this point."""
        (x0, y0, z0), (x1, y1, z1) = self.nw, self.se
        return (_f32(x0 + x1) / 2, _f32(y0 + y1) / 2, _f32(z0 + z1) / 2)

    def z_at(self, x: float, y: float) -> float:
        (x0, y0, z_nw), (x1, y1, z_se) = self.nw, self.se
        u = 0.0 if x1 == x0 else min(1.0, max(0.0, (x - x0) / (x1 - x0)))
        v = 0.0 if y1 == y0 else min(1.0, max(0.0, (y - y0) / (y1 - y0)))
        north = z_nw + (self.ne_z - z_nw) * u
        south = self.sw_z + (z_se - self.sw_z) * u
        return north + (south - north) * v


@dataclass
class NavLadder:
    id: int
    width: float
    top: tuple[float, float, float]
    bottom: tuple[float, float, float]
    length: float
    direction: int
    top_forward: int = 0
    top_left: int = 0
    top_right: int = 0
    top_behind: int = 0
    bottom_area: int = 0


@dataclass
class NavMesh:
    version: int = 16
    sub_version: int = 14
    bsp_size: int = 0
    analyzed: bool = False
    places: list[str] = field(default_factory=list)
    has_unnamed_areas: bool = True
    pre_area_name: str = "default"               # L4D2 pre-area data
    pre_area_value: int = 0
    areas: list[NavArea] = field(default_factory=list)
    ladders: list[NavLadder] = field(default_factory=list)
    trailer: bytes = b"\x00\x00\x00\x00"          # L4D2 custom data after the ladders
    problems: list[str] = field(default_factory=list)   # not saved: notes from our generator

    def by_id(self) -> dict[int, NavArea]:
        return {a.id: a for a in self.areas}


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, fmt: str):
        v = struct.unpack_from("<" + fmt, self.data, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return v if len(v) > 1 else v[0]

    def ids(self) -> list[int]:
        n = self.take("I")
        v = list(struct.unpack_from(f"<{n}I", self.data, self.pos))
        self.pos += 4 * n
        return v


def read_nav(data: bytes) -> NavMesh:
    r = _Reader(data)
    magic, version, sub, bsp = r.take("IIII")
    if magic != MAGIC:
        raise ValueError("not a nav file")
    if version != 16:
        raise ValueError(f"nav version {version} not supported (L4D2 uses 16)")
    mesh = NavMesh(version=version, sub_version=sub, bsp_size=bsp, analyzed=bool(r.take("B")))
    for _ in range(r.take("H")):
        n = r.take("H")
        mesh.places.append(r.data[r.pos:r.pos + n].rstrip(b"\x00").decode("latin-1"))
        r.pos += n
    mesh.has_unnamed_areas = bool(r.take("B"))
    end = data.index(b"\x00", r.pos)
    mesh.pre_area_name = data[r.pos:end].decode("latin-1")
    r.pos = end + 1
    mesh.pre_area_value = r.take("H")
    for _ in range(r.take("I")):
        a = NavArea(r.take("I"), r.take("i"), r.take("3f"), r.take("3f"), r.take("f"), r.take("f"))
        a.connections = [r.ids() for _ in range(4)]
        a.hiding_spots = [HidingSpot(r.take("I"), r.take("3f"), r.take("B")) for _ in range(r.take("B"))]
        for _ in range(r.take("I")):
            e = Encounter(*r.take("IBIB"))
            e.spots = [r.take("IB") for _ in range(r.take("B"))]
            a.encounters.append(e)
        a.place = r.take("H")
        a.ladders = [r.ids(), r.ids()]
        a.occupy = r.take("2f")
        a.light = r.take("4f")
        a.spawn_attributes, a.spawn_extra = r.take("IH")
        a.visible = [r.take("IB") for _ in range(r.take("I"))]
        a.inherit_visibility = r.take("I")
        mesh.areas.append(a)
    for _ in range(r.take("I")):
        mesh.ladders.append(NavLadder(r.take("I"), r.take("f"), r.take("3f"), r.take("3f"), r.take("f"),
                                      *r.take("IIIIII")))
    mesh.trailer = data[r.pos:]
    return mesh


def write_nav(mesh: NavMesh) -> bytes:
    out = bytearray()

    def put(fmt, *v):
        out.extend(struct.pack("<" + fmt, *v))

    def put_ids(ids):
        put("I", len(ids))
        out.extend(struct.pack(f"<{len(ids)}I", *ids))

    put("IIII", MAGIC, mesh.version, mesh.sub_version, mesh.bsp_size)
    put("B", int(mesh.analyzed))
    put("H", len(mesh.places))
    for name in mesh.places:
        raw = name.encode("latin-1") + b"\x00"
        put("H", len(raw))
        out.extend(raw)
    put("B", int(mesh.has_unnamed_areas))
    out.extend(mesh.pre_area_name.encode("latin-1") + b"\x00")
    put("H", mesh.pre_area_value)
    put("I", len(mesh.areas))
    for a in mesh.areas:
        put("Ii", a.id, a.flags)
        put("3f", *a.nw)
        put("3f", *a.se)
        put("ff", a.ne_z, a.sw_z)
        for ids in a.connections:
            put_ids(ids)
        put("B", len(a.hiding_spots))
        for h in a.hiding_spots:
            put("I3fB", h.id, *h.pos, h.flags)
        put("I", len(a.encounters))
        for e in a.encounters:
            put("IBIB", e.from_id, e.from_dir, e.to_id, e.to_dir)
            put("B", len(e.spots))
            for spot in e.spots:
                put("IB", *spot)
        put("H", a.place)
        for ids in a.ladders:
            put_ids(ids)
        put("2f", *a.occupy)
        put("4f", *a.light)
        put("IH", a.spawn_attributes, a.spawn_extra)
        put("I", len(a.visible))
        for v in a.visible:
            put("IB", *v)
        put("I", a.inherit_visibility)
    put("I", len(mesh.ladders))
    for lad in mesh.ladders:
        put("If", lad.id, lad.width)
        put("3f", *lad.top)
        put("3f", *lad.bottom)
        put("f", lad.length)
        put("IIIIII", lad.direction, lad.top_forward, lad.top_left, lad.top_right, lad.top_behind, lad.bottom_area)
    out.extend(mesh.trailer)
    return bytes(out)


def load_nav(path: str) -> NavMesh:
    with open(path, "rb") as f:
        return read_nav(f.read())
