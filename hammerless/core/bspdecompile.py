"""A compiled map (.bsp, Left 4 Dead 2) back to Hammer's .vmf, for importing the game's own maps.

What vbsp keeps is enough to rebuild most of the source:
- brushes: every brush of the map is stored as its planes (dbrush_t / dbrushside_t), the sides vbsp added to
  round off corners marked as bevels. Each side's polygon is its plane cut by the brush's other planes (as Hammer
  does), and its three plane points are taken from that polygon, wound the way Hammer wants (the normal out).
- textures: a side's texinfo is its texture's axes, scale and shift (textureVecs = axis / scale, shift) and its
  lightmap scale (lightmapVecs = axis / lightmap scale).
- which brushes are whose: a model's brushes are the ones in the leaves under its head node; brushes marked
  CONTENTS_DETAIL in the world were func_detail.
- entities: as they were, but brush entities point at a model ("model" "*N") and are given its brushes back.
- static props (the game lump 'sprp') become prop_static, cubemaps env_cubemap.
Lost: instances (already merged in), visgroups, hint / skip brushes, brush grouping.
"""
from __future__ import annotations

import math
import re
import struct

from .vmf import Block, _plane_points
from .vmfimport import _base_winding, _clip, _dedupe, _newell

# lump numbers
L_ENTITIES, L_PLANES, L_TEXDATA, L_VERTEXES, L_NODES, L_TEXINFO, L_FACES, L_LEAFS = 0, 1, 2, 3, 5, 6, 7, 10
L_EDGES, L_SURFEDGES, L_DISPINFO, L_DISPVERTS, L_DISPTRIS = 12, 13, 26, 33, 48
L_OVERLAYS, L_OVERLAY_FADES = 45, 60
L_MODELS, L_LEAFBRUSHES, L_BRUSHES, L_BRUSHSIDES = 14, 17, 18, 19
L_AREAPORTALS, L_GAME, L_CLIPPORTALVERTS, L_CUBEMAPS, L_TEXSTRDATA, L_TEXSTRTABLE = 21, 35, 41, 42, 43, 44

CONTENTS_SOLID, CONTENTS_WINDOW, CONTENTS_GRATE, CONTENTS_BLOCKLOS, CONTENTS_OPAQUE = 0x1, 0x2, 0x8, 0x40, 0x80
CONTENTS_AREAPORTAL, CONTENTS_PLAYERCLIP, CONTENTS_MONSTERCLIP = 0x8000, 0x10000, 0x20000
CONTENTS_DETAIL, CONTENTS_TRANSLUCENT, CONTENTS_LADDER = 0x8000000, 0x10000000, 0x20000000
CONTENTS_SLIME, CONTENTS_WATER, CONTENTS_TEAM1, CONTENTS_TEAM2 = 0x10, 0x20, 0x800, 0x1000
DEFAULT_LIGHTMAP_SCALE = 16
# compiling gives a side no face of its own its texture from another side with the same flags (vbsp's
# CompactTexinfos): a tool brush's sides come out nodraw or clip. Its contents say what it was.


def tool_texture(contents: int) -> str | None:
    """The tool texture a brush with these contents (and no visible face) had, or None for a solid brush."""
    if contents & (CONTENTS_WATER | CONTENTS_SLIME):
        return None                                  # (water's own sides are nodraw: as they were)
    if contents & CONTENTS_AREAPORTAL:
        return "TOOLS/TOOLSAREAPORTAL"
    if contents & CONTENTS_LADDER:
        # L4D2's tools/climb (infected-only climbing) also sets a team flag; Valve's invisible ladder doesn't
        return "TOOLS/CLIMB" if contents & (CONTENTS_TEAM1 | CONTENTS_TEAM2) else "TOOLS/TOOLSINVISIBLELADDER"
    clip = contents & (CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP)
    if clip == CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP:
        return "TOOLS/TOOLSCLIP"
    if clip == CONTENTS_PLAYERCLIP:
        return "TOOLS/TOOLSPLAYERCLIP"
    if clip == CONTENTS_MONSTERCLIP:
        return "TOOLS/TOOLSNPCCLIP"
    if contents & CONTENTS_SOLID:
        return None
    if contents & CONTENTS_OPAQUE:
        return "TOOLS/TOOLSBLOCKLIGHT"
    if contents & CONTENTS_BLOCKLOS:
        return "TOOLS/TOOLSBLOCK_LOS"
    if contents & (CONTENTS_GRATE | CONTENTS_TRANSLUCENT) and not contents & CONTENTS_WINDOW:
        return "TOOLS/TOOLSINVISIBLE"
    return None


_PATCHED = re.compile(r"^MAPS/[^/]+/(.+?)(?:_WVT_PATCH|_DEPTH_\d+|_-?\d+_-?\d+_-?\d+)*$")


def original_material(name: str) -> str:
    """A material vbsp made for the map (maps/<map>/X_x_y_z for cubemaps, X_wvt_patch, X_depth_N) -> X."""
    m = _PATCHED.match(name)
    return m.group(1) if m else name


class Bsp:
    """The lumps of a compiled map, read as arrays of records."""

    def __init__(self, data: bytes):
        if data[:4] != b"VBSP":
            raise ValueError("not a compiled map (.bsp)")
        self.data = data
        self.version = struct.unpack_from("<i", data, 4)[0]

    def lump(self, i: int) -> bytes:
        # (L4D2's lump header: version, offset, length, fourCC)
        _ver, ofs, ln, _cc = struct.unpack_from("<4i", self.data, 8 + 16 * i)
        return self.data[ofs:ofs + ln]

    def records(self, i: int, fmt: str) -> list[tuple]:
        return list(struct.iter_unpack("<" + fmt, self.lump(i)))

    # -- the tables
    def planes(self):
        return [((a, b, c), d) for a, b, c, d, _t in self.records(L_PLANES, "4fi")]

    def texture_names(self) -> list[str]:
        strdata = self.lump(L_TEXSTRDATA)
        table = [t[0] for t in self.records(L_TEXSTRTABLE, "i")]
        names = [strdata[o:strdata.index(b"\0", o)].decode("latin-1") for o in table]
        # texdata: reflectivity (3f), name index, width, height, view width, view height
        return [names[t[3]] if 0 <= t[3] < len(names) else "" for t in self.records(L_TEXDATA, "3f5i")]

    def texinfos(self):
        """(texture axes [2][4], lightmap axes [2][4], flags, texdata) per texinfo."""
        out = []
        for r in self.records(L_TEXINFO, "16f2i"):
            out.append(((r[0:4], r[4:8]), (r[8:12], r[12:16]), r[16], r[17]))
        return out

    def models(self):
        """(mins, maxs, origin, headnode) per model; model 0 is the world."""
        return [(r[0:3], r[3:6], r[6:9], r[9]) for r in self.records(L_MODELS, "9f3i")]

    def brushes(self):
        return self.records(L_BRUSHES, "3i")                      # firstside, numsides, contents

    def brushsides(self):
        return self.records(L_BRUSHSIDES, "H2h2B")               # planenum, texinfo, dispinfo, bevel, thin

    def model_ranges(self) -> list[tuple[int, int]]:
        """Each model's brushes as (first, last) indexes: vbsp writes them in entity order, so a model's are the
        range from the lowest to the highest brush in its leaves (some brushes are in no leaf: buried in solid,
        or moved into the world, like func_detail's and areaportals'). (-1, -1): none."""
        out = []
        for _mins, _maxs, _origin, head in self.models():
            found = self.model_brushes(head)
            out.append((found[0], found[-1]) if found else (-1, -1))
        if out and out[0][0] >= 0:
            out[0] = (0, out[0][1])
        return out

    def model_brushes(self, headnode: int) -> list[int]:
        """The brushes in the leaves under a node, in brush order."""
        if not hasattr(self, "_tree"):
            self._tree = (self.records(L_NODES, "3i6h2H2h"),          # planenum, children[2], ...
                          self.records(L_LEAFS, "i2h6h4H2h"),         # ..., firstleafbrush, numleafbrushes, ...
                          [t[0] for t in self.records(L_LEAFBRUSHES, "H")])
        nodes, leafs, leafbrushes = self._tree
        found, todo = set(), [headnode]
        while todo:
            n = todo.pop()
            if n < 0:
                leaf = leafs[-1 - n]
                first, count = leaf[11], leaf[12]
                found.update(leafbrushes[first:first + count])
            else:
                todo.extend(nodes[n][1:3])
        return sorted(found)

    def entities(self) -> list[list[tuple[str, str]]]:
        text = self.lump(L_ENTITIES).decode("latin-1").rstrip("\0")
        out, cur = [], None
        for m in re.finditer(r'"([^"]*)"|(\{)|(\})', text):
            if m.group(2):
                cur = []
            elif m.group(3):
                if cur is not None:
                    out.append(cur)
                cur = None
            elif cur is not None:
                cur.append(m.group(1))
        # pairs (an entity's keys can repeat: outputs)
        return [list(zip(e[0::2], e[1::2])) for e in out]

    def static_props(self) -> list[dict]:
        lump = self.lump(L_GAME)
        if len(lump) < 4:
            return []
        for g in range(struct.unpack_from("<i", lump, 0)[0]):
            gid, _flags, version, gofs, glen = struct.unpack_from("<iHHii", lump, 4 + 16 * g)
            if gid != 0x73707270:                                   # 'sprp'
                continue
            data = self.data[gofs:gofs + glen]
            nd = struct.unpack_from("<i", data, 0)[0]
            names = [data[4 + 128 * i:4 + 128 * (i + 1)].split(b"\0", 1)[0].decode("latin-1") for i in range(nd)]
            p = 4 + 128 * nd
            p += 4 + 2 * struct.unpack_from("<i", data, p)[0]         # (the leaves)
            n = struct.unpack_from("<i", data, p)[0]
            p += 4
            size = (len(data) - p) // n if n else 0
            props = []
            for i in range(n):
                r = p + size * i
                origin = struct.unpack_from("<3f", data, r)
                angles = struct.unpack_from("<3f", data, r + 12)
                model, _firstleaf, _leafcount, solid, flags = struct.unpack_from("<3H2B", data, r + 24)
                skin, fademin, fademax = struct.unpack_from("<i2f", data, r + 32)
                lighting = struct.unpack_from("<3f", data, r + 44)
                fadescale = struct.unpack_from("<f", data, r + 56)[0]
                prop = {"model": names[model] if model < nd else "", "origin": origin, "angles": angles,
                        "solid": solid, "flags": flags, "skin": skin, "fademindist": fademin,
                        "fademaxdist": fademax, "lightingorigin": lighting, "fadescale": fadescale,
                        "version": version}
                if version >= 8 and size >= 68:
                    cpu_min, cpu_max, gpu_min, gpu_max = struct.unpack_from("<4B", data, r + 60)
                    color = struct.unpack_from("<4B", data, r + 64)
                    prop.update(mincpulevel=cpu_min, maxcpulevel=cpu_max, mingpulevel=gpu_min,
                                maxgpulevel=gpu_max, rendercolor=color)
                if version >= 9 and size >= 72:
                    prop["disableX360"] = data[r + 68]
                props.append(prop)
            return props
        return []

    def areaportals(self) -> dict[int, list[tuple]]:
        """Portal key (func_areaportal's portalnumber) -> its outline (each portal is stored once per side)."""
        verts = self.records(L_CLIPPORTALVERTS, "3f")
        out = {}
        for key, _other, first, count, _plane in self.records(L_AREAPORTALS, "4Hi"):
            if key and key not in out and count >= 3:
                out[key] = verts[first:first + count]
        return out

    def face_windings(self) -> list[tuple[int, list[tuple], int, int]]:
        """(plane, corners, texinfo, dispinfo) per face."""
        verts = self.records(L_VERTEXES, "3f")
        edges = self.records(L_EDGES, "2H")
        surf = [t[0] for t in self.records(L_SURFEDGES, "i")]
        out = []
        for f in self.records(L_FACES, "HBBihhhh4Bif2i2iiHHI"):
            plane, firstedge, numedges, texinfo, dispinfo = f[0], f[3], f[4], f[5], f[6]
            w = []
            for e in surf[firstedge:firstedge + numedges]:
                w.append(verts[edges[e][0]] if e >= 0 else verts[edges[-e][1]])
            out.append((plane, w, texinfo, dispinfo))
        return out

    def dispinfos(self) -> list[dict]:
        out = []
        for r in self.records(L_DISPINFO, "3f3iIfiHH2i88x10I"):
            out.append({"start": r[0:3], "vert_start": r[3], "tri_start": r[4], "power": r[5], "min_tess": r[6],
                        "map_face": r[9], "allowed": r[13:23]})
        return out

    def overlays(self) -> list[dict]:
        fades = self.records(L_OVERLAY_FADES, "2f")
        out = []
        for i, r in enumerate(self.records(L_OVERLAYS, "ihH64i4f12f3f3f")):
            count = r[2] & 0x3FFF
            out.append({"id": r[0], "texinfo": r[1], "render_order": r[2] >> 14, "faces": r[3:3 + count],
                        "u": r[67:69], "v": r[69:71], "uv": [r[71 + 3 * k:74 + 3 * k] for k in range(4)],
                        "origin": r[83:86], "normal": r[86:89], "fade": fades[i] if i < len(fades) else (0.0, 0.0)})
        return out

    def cubemaps(self):
        return [(r[0:3], r[3]) for r in self.records(L_CUBEMAPS, "4i")]


# ---------------------------------------------------------------- writing

def _num(x: float) -> str:
    """A coordinate as Hammer writes it: whole when it's within a hair of whole, else 3 decimals."""
    r = round(x)
    if abs(x - r) < 0.01:
        return str(int(r))
    return f"{x:.6f}".rstrip("0").rstrip(".")


def _vec(v) -> str:
    return " ".join(_num(c) for c in v)


def _exact(x: float) -> str:
    """A plane point's coordinate: as computed (plane_points already snaps whole points that stay on the plane;
    rounding one coordinate on its own tilts the plane)."""
    if x == round(x):
        return str(int(round(x)))
    return f"{x:.6f}".rstrip("0").rstrip(".")


def _pvec(v) -> str:
    return " ".join(_exact(c) for c in v)


def _axis(vec4) -> tuple[str, float]:
    """A texinfo row (axis / scale, shift) -> '[x y z shift] scale'."""
    x, y, z, shift = vec4
    ln = math.sqrt(x * x + y * y + z * z)
    if ln < 1e-9:
        return "[1 0 0 0] 0.25", 0.25
    scale = 1.0 / ln
    return f"[{_exact(x * scale)} {_exact(y * scale)} {_exact(z * scale)} {_exact(shift)}] {_exact(scale)}", scale


SPREAD = 256.0         # units between a side's three plane points
DISP_DEPTH = 8.0
PROP_NUDGE = 0.05      # units a prop exactly on a wall face moves out of it       # a rebuilt displacement brush's thickness behind its face


GRID_SNAP = 0.01       # a corner this close to whole numbers (and still on the plane) was a Hammer grid point


def grid_points(n, d, w):
    """Three of the face's corners that are whole numbers (Hammer's grid) and on the plane, spanning the biggest
    triangle, wound so the normal is n; None if there aren't three. The compiled plane was made by Hammer from such
    points and stored rounded to floats: the same points give vbsp exactly the same plane again."""
    cand = []
    for q in w:
        r = tuple(float(round(x)) for x in q)
        if max(abs(r[k] - q[k]) for k in range(3)) < GRID_SNAP and abs(sum(r[k] * n[k] for k in range(3)) - d) < GRID_SNAP:
            if r not in cand:
                cand.append(r)
    if len(cand) < 3:
        return None
    best, area = None, 0.0
    m = len(cand)
    for i in range(m):
        for j in range(i + 1, m):
            for k in range(j + 1, m):
                a, b, c = cand[i], cand[j], cand[k]
                u = [a[t] - b[t] for t in range(3)]
                v = [c[t] - b[t] for t in range(3)]
                x = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
                ar = math.sqrt(sum(t * t for t in x))
                if ar > area:
                    area, best = ar, (a, b, c, x)
    if best is None or area < 1.0:
        return None
    a, b, c, x = best
    nx = [t / area for t in x]
    if sum(nx[t] * n[t] for t in range(3)) < 0.999999:   # (the other way round, or not quite this plane)
        if sum(nx[t] * n[t] for t in range(3)) < -0.999999:
            a, c = c, a
        else:
            return None
    return [a, b, c]


def plane_points(n, d, w):
    """Three points on the plane n.x = d, wound so Hammer's normal points out (n). Hammer's own grid points when
    the face has them (grid_points); else spread SPREAD apart around the face's middle, not the face's corners
    (a tiny face's corners, rounded, tilt the plane)."""
    g = grid_points(n, d, w)
    if g is not None:
        return g
    c = [sum(p[k] for p in w) / len(w) for k in range(3)]
    off = sum(c[k] * n[k] for k in range(3)) - d
    c = [c[k] - n[k] * off for k in range(3)]
    a = (0.0, 0.0, 1.0) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = (a[1] * n[2] - a[2] * n[1], a[2] * n[0] - a[0] * n[2], a[0] * n[1] - a[1] * n[0])
    ln = math.sqrt(sum(x * x for x in u))
    u = tuple(x / ln for x in u)
    v = (n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0])      # u x v = n
    pts = []
    for p in (tuple(c[k] + u[k] * SPREAD for k in range(3)), tuple(c), tuple(c[k] + v[k] * SPREAD for k in range(3))):
        r = tuple(float(round(x)) for x in p)
        on = abs(sum(r[k] * n[k] for k in range(3)) - d) < 1e-6      # (looser tilts far planes)
        pts.append(r if on else p)
    return pts                     # (p0 - p1) x (p2 - p1) = SPREAD^2 (u x v): along n


class Decompiler:
    def __init__(self, data: bytes):
        self.bsp = Bsp(data)
        self.planes = self.bsp.planes()
        self.names = self.bsp.texture_names()
        self.texinfos = self.bsp.texinfos()
        self.brushes = self.bsp.brushes()
        self.sides = self.bsp.brushsides()
        self._id = 0
        self.disp_side_ids: dict[int, str] = {}  # dispinfo -> its rebuilt brush's displacement side id
        self.side_polys: dict[int, list] = {}      # plane -> [(side block, polygon)] (world brushes: for disps, overlays)
        self.notes: list[str] = []
        self.skipped_brushes = 0

    def new_id(self) -> int:
        self._id += 1
        return self._id

    def material(self, texinfo: int) -> str:
        if texinfo < 0 or texinfo >= len(self.texinfos):
            return "TOOLS/TOOLSNODRAW"
        td = self.texinfos[texinfo][3]
        return original_material((self.names[td] if 0 <= td < len(self.names) else "TOOLS/TOOLSNODRAW").upper())

    def solid(self, b: int, offset=(0.0, 0.0, 0.0)) -> Block | None:
        """Brush b as a VMF solid (moved by offset: a brush entity's brushes are stored around its origin)."""
        first, count, contents = self.brushes[b]
        sides = [s for s in self.sides[first:first + count] if not s[3]]          # (not bevels)
        tool = tool_texture(contents)
        if tool and not all(self.material(s[1]).startswith("TOOLS/") for s in sides):
            tool = None                                   # (a visible brush: its hidden sides stay as they are)
        planes = []
        for s in sides:
            n, d = self.planes[s[0]]
            planes.append((n, d + sum(n[k] * offset[k] for k in range(3))))
        out = Block("solid")
        out.kv("id", self.new_id())
        made = 0
        for i, (s, (n, d)) in enumerate(zip(sides, planes)):
            w = _base_winding(n, d)
            for j, (n2, d2) in enumerate(planes):
                if j != i and w:
                    w = _clip(w, n2, d2)
            w = _dedupe(w, 1e-3)
            if len(w) < 3:
                continue
            if sum(a * b for a, b in zip(_newell(w), n)) < 0:      # counter-clockwise around the normal
                w.reverse()
            p1, p2, p3 = plane_points(n, d, w)
            side = out.add(Block("side"))
            side.kv("id", self.new_id())
            side.kv("plane", f"({_pvec(p1)}) ({_pvec(p2)}) ({_pvec(p3)})")
            mat = self.material(s[1])
            if tool:
                mat = tool
            side.kv("material", mat)
            self.side_polys.setdefault(s[0], []).append((side, w, d))
            self.texture_keys(side, s[1], offset)
            made += 1
        if made < 4:
            self.skipped_brushes += 1
            return None
        ed = out.add(Block("editor"))
        ed.kv("color", "0 180 255").kv("visgroupshown", 1).kv("visgroupautoshown", 1)
        return out

    def add_displacements(self) -> None:
        """Each displacement face's surface onto the world brush side it came from (the same plane, the same four
        corners: vbsp copies the side's)."""
        infos = self.bsp.dispinfos()
        if not infos:
            return
        verts = self.bsp.records(L_DISPVERTS, "5f")
        tris = [t[0] for t in self.bsp.records(L_DISPTRIS, "H")]
        faces = self.bsp.face_windings()
        lost = 0
        for info in infos:
            if info["map_face"] >= len(faces) or len(faces[info["map_face"]][1]) != 4:
                lost += 1
                continue
            plane, w, ti, dnum = faces[info["map_face"]]
            self.world.add(self.disp_solid(plane, w, ti, self.dispinfo_block(info, verts, tris, self.planes[plane][0])))
            self.disp_side_ids[dnum] = self.last_disp_side
        if lost:
            self.notes.append(f"{lost} displacements had no face to rebuild them from and were left out")

    def dispinfo_block(self, info: dict, verts, tris, normal) -> Block:
        p = info["power"]
        n = (1 << p) + 1
        d = Block("dispinfo")
        d.kv("power", p)
        d.kv("startposition", f"[{_vec(info['start'])}]")
        mt = info["min_tess"]
        d.kv("flags", mt & 0x7FFFFFFF if mt & 0x80000000 else 0)
        d.kv("elevation", 0).kv("subdiv", 0)
        blocks = {name: Block(name) for name in ("normals", "distances", "offsets", "offset_normals", "alphas")}
        nrm = _vec(normal)
        for i in range(n):
            nrow, drow, arow = [], [], []
            for j in range(n):
                x, y, z, dist, alpha = verts[info["vert_start"] + i * n + j]
                ln = math.sqrt(x * x + y * y + z * z)
                if ln > 1e-9:
                    nrow.append(f"{_exact(x / ln)} {_exact(y / ln)} {_exact(z / ln)}")
                    drow.append(_exact(ln * dist))
                else:
                    nrow.append(nrm)
                    drow.append("0")
                arow.append(_exact(alpha))
            blocks["normals"].kv(f"row{i}", " ".join(nrow))
            blocks["distances"].kv(f"row{i}", " ".join(drow))
            blocks["offsets"].kv(f"row{i}", " ".join(["0 0 0"] * n))
            blocks["offset_normals"].kv(f"row{i}", " ".join([nrm] * n))
            blocks["alphas"].kv(f"row{i}", " ".join(arow))
        for b in blocks.values():
            d.add(b)
        tags = Block("triangle_tags")
        per = 2 * (n - 1)
        for i in range(n - 1):
            row = []
            for j in range(per):
                t = tris[info["tri_start"] + i * per + j] if info["tri_start"] + i * per + j < len(tris) else 0
                row.append(str((1 if t & 2 else 0) | (8 if t & 4 else 0)))     # (the map's bits -> Hammer's)
            tags.kv(f"row{i}", " ".join(row))
        d.add(tags)
        allowed = Block("allowed_verts")
        allowed.kv("10", " ".join(str(a - (1 << 32) if a & 0x80000000 else a) for a in info["allowed"]))
        d.add(allowed)
        return d

    def side_index(self):
        """Brush sides by plane (rounded), for finding the side a face of the map is on."""
        idx = {}
        for plane, polys in self.side_polys.items():
            n = self.planes[plane][0]
            for side, w, d in polys:
                key = (round(n[0], 3), round(n[1], 3), round(n[2], 3), round(d))
                idx.setdefault(key, []).append((side, w))
        return idx

    @staticmethod
    def _inside(p, poly, n, eps=0.5) -> bool:
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            e = [b[k] - a[k] for k in range(3)]
            q = [p[k] - a[k] for k in range(3)]
            c = (e[1] * q[2] - e[2] * q[1], e[2] * q[0] - e[0] * q[2], e[0] * q[1] - e[1] * q[0])
            if sum(c[k] * n[k] for k in range(3)) < -eps * math.sqrt(sum(x * x for x in e)):
                return False
        return True

    def face_side_id(self, face, idx, offset=(0.0, 0.0, 0.0)) -> str | None:
        plane, w, _ti, disp = face
        if disp >= 0:
            return self.disp_side_ids.get(disp)
        n, d = self.planes[plane]
        d += sum(n[k] * offset[k] for k in range(3))      # (a brush entity's faces are stored around its origin)
        c = [sum(p[k] for p in w) / len(w) + offset[k] for k in range(3)]
        for key in {(round(n[0], 3), round(n[1], 3), round(n[2], 3), round(d) + o) for o in (0, -1, 1)}:
            for side, poly in idx.get(key, ()):
                if self._inside(c, poly, n):
                    return side.get("id")
        return None

    def face_offsets(self) -> dict[int, tuple]:
        """Faces of brush entities with an origin -> that origin (their faces are stored around it)."""
        models = self.bsp.records(14, "9f3i")
        out = {}
        for e in self.bsp.entities():
            d = dict(e)
            m, o = d.get("model", ""), d.get("origin", "")
            if not m.startswith("*") or not o:
                continue
            try:
                mi, org = int(m[1:]), tuple(float(x) for x in o.split()[:3])
            except ValueError:
                continue
            if 0 < mi < len(models) and any(org):
                first, num = models[mi][10], models[mi][11]
                out.update({f: org for f in range(first, first + num)})
        return out

    def overlay_entities(self, accessors: dict[int, str]) -> list[Block]:
        """info_overlay from the map's overlays (vbsp removes the entities): their faces become the sides they
        were on, the fades come back from their squares, a name from its info_overlay_accessor."""
        overlays = self.bsp.overlays()
        if not overlays:
            return []
        faces = self.bsp.face_windings()
        idx = self.side_index()
        offsets = self.face_offsets()
        out, lost = [], 0
        for o in overlays:
            ids = []
            for f in o["faces"]:
                if 0 <= f < len(faces):
                    sid = self.face_side_id(faces[f], idx, offsets.get(f, (0.0, 0.0, 0.0)))
                    if sid is not None and sid not in ids:
                        ids.append(sid)
            if not ids:
                lost += 1
                continue
            n = o["normal"]
            bu = tuple(o["uv"][k][2] for k in range(3))
            bv = (n[1] * bu[2] - n[2] * bu[1], n[2] * bu[0] - n[0] * bu[2], n[0] * bu[1] - n[1] * bu[0])
            ln = math.sqrt(sum(c * c for c in bv)) or 1.0
            bv = tuple(c / ln * (-1 if o["uv"][3][2] == 1 else 1) for c in bv)
            kv = [("classname", "info_overlay"), ("material", self.material(o["texinfo"]).lower()),
                  ("sides", " ".join(str(i) for i in ids)), ("RenderOrder", str(o["render_order"])),
                  ("StartU", _exact(o['u'][0])), ("EndU", _exact(o['u'][1])),
                  ("StartV", _exact(o['v'][0])), ("EndV", _exact(o['v'][1])),
                  ("BasisOrigin", _vec(o["origin"])), ("BasisNormal", _vec(n)),
                  ("BasisU", _vec(bu)), ("BasisV", _vec(bv)), ("origin", _vec(o["origin"]))]
            for k in range(4):
                kv.append((f"uv{k}", f"{_exact(o['uv'][k][0])} {_exact(o['uv'][k][1])} 0"))
            fmin, fmax = o["fade"]
            kv += [("fademindist", _exact(math.sqrt(fmin)) if fmin > 0 else "-1"),
                   ("fademaxdist", _exact(math.sqrt(fmax)) if fmax > 0 else "0")]
            if o["id"] in accessors:
                kv.append(("targetname", accessors[o["id"]]))
            out.append(self.entity_block(kv))
        if lost:
            self.notes.append(f"{lost} overlays aren't on any face of the map (they draw nothing) and were left out")
        return out

    def off_walls(self, origin, brushes) -> tuple:
        """A prop's origin exactly on a solid world brush's face counts as inside the wall or not depending on
        how vbsp happens to split the map, and a prop "outside the map" is dropped: such an origin moves
        PROP_NUDGE out of the wall (Valve's maps have props placed exactly on wall faces)."""
        if not hasattr(self, "_solid_boxes"):
            self._solid_boxes = [(b, self.brush_bounds(b)) for b in brushes
                                 if not self.brushes[b][2] & CONTENTS_DETAIL]
        for b, box in self._solid_boxes:
            if box is None or any(origin[k] < box[0][k] - 0.01 or origin[k] > box[1][k] + 0.01 for k in range(3)):
                continue
            first, count, contents = self.brushes[b]
            planes = [self.planes[s[0]] for s in self.sides[first:first + count] if not s[3]]
            ds = [sum(n[k] * origin[k] for k in range(3)) - d for n, d in planes]
            if not ds or max(ds) > 1e-3:
                continue                                   # (not inside or on this brush)
            on = [i for i, x in enumerate(ds) if x > -1e-3]
            if not on:
                continue                                   # (really inside: leave it as the map had it)
            out = [sum(planes[i][0][k] for i in on) for k in range(3)]     # (an edge or corner: out of both)
            ln = math.sqrt(sum(x * x for x in out)) or 1.0
            return tuple(origin[k] + out[k] / ln * PROP_NUDGE for k in range(3))
        return origin

    def all_trigger(self, b: int) -> bool:
        first, count, _c = self.brushes[b]
        return all(self.material(s[1]) == "TOOLS/TOOLSTRIGGER" for s in self.sides[first:first + count] if not s[3])

    def brush_bounds(self, b: int):
        first, count, _c = self.brushes[b]
        planes = [self.planes[s[0]] for s in self.sides[first:first + count] if not s[3]]
        pts = []
        for i, (n, d) in enumerate(planes):
            w = _base_winding(n, d)
            for j, (n2, d2) in enumerate(planes):
                if j != i and w:
                    w = _clip(w, n2, d2)
            pts += w
        if not pts:
            return None
        return (tuple(min(p[k] for p in pts) for k in range(3)), tuple(max(p[k] for p in pts) for k in range(3)))

    def match_portals(self, portals: dict, brushes: list[int]) -> dict[int, int]:
        """Portal key -> its areaportal brush: in order when there are as many (vbsp numbers them as it reads
        them), else the brush whose box holds the portal's outline."""
        keys = sorted(portals)
        if len(keys) == len(brushes):
            return dict(zip(keys, brushes))
        out, boxes = {}, {b: self.brush_bounds(b) for b in brushes}
        for k in keys:
            c = [sum(p[i] for p in portals[k]) / len(portals[k]) for i in range(3)]
            for b, bb in boxes.items():
                if bb and b not in out.values() and all(bb[0][i] - 1 <= c[i] <= bb[1][i] + 1 for i in range(3)):
                    out[k] = b
                    break
        return out

    def texture_keys(self, side: Block, texinfo: int, offset=(0.0, 0.0, 0.0)) -> None:
        if 0 <= texinfo < len(self.texinfos):
            (tu, tv), (lu, _lv), _flags, _td = self.texinfos[texinfo]
            # (moved: the shift follows the texture across the offset)
            tu = (*tu[:3], tu[3] - sum(tu[k] * offset[k] for k in range(3)))
            tv = (*tv[:3], tv[3] - sum(tv[k] * offset[k] for k in range(3)))
            ua, _ = _axis(tu)
            va, _ = _axis(tv)
            lm = math.sqrt(sum(c * c for c in lu[:3]))
            lmscale = round(1.0 / lm) if lm > 1e-9 else DEFAULT_LIGHTMAP_SCALE
        else:
            ua, va, lmscale = "[1 0 0 0] 0.25", "[0 -1 0 0] 0.25", DEFAULT_LIGHTMAP_SCALE
        side.kv("uaxis", ua)
        side.kv("vaxis", va)
        side.kv("rotation", 0)
        side.kv("lightmapscale", lmscale)
        side.kv("smoothing_groups", 0)

    def disp_solid(self, plane: int, w: list[tuple], texinfo: int, dispinfo: Block) -> Block:
        """A displacement's brush (vbsp doesn't keep it): its face, carrying the displacement, and a nodraw back
        DISP_DEPTH behind it."""
        n, d = self.planes[plane]
        if sum(a * b for a, b in zip(_newell(w), n)) < 0:
            w = list(reversed(w))
        back = [tuple(p[k] - n[k] * DISP_DEPTH for k in range(3)) for p in w]
        out = Block("solid")
        out.kv("id", self.new_id())
        faces = [((n, d), w, texinfo, dispinfo)]
        nb = tuple(-c for c in n)
        faces.append(((nb, -(d - DISP_DEPTH)), list(reversed(back)), -1, None))
        m = len(w)
        for i in range(m):
            j = (i + 1) % m
            quad = [w[j], w[i], back[i], back[j]]          # (counter-clockwise seen from outside)
            qn = _newell(quad)
            ln = math.sqrt(sum(c * c for c in qn)) or 1.0
            qn = tuple(c / ln for c in qn)
            faces.append(((qn, sum(qn[k] * w[i][k] for k in range(3))), quad, -1, None))
        for (fn, fd), poly, ti, disp in faces:
            p1, p2, p3 = plane_points(fn, fd, poly)
            side = out.add(Block("side"))
            side.kv("id", self.new_id())
            side.kv("plane", f"({_pvec(p1)}) ({_pvec(p2)}) ({_pvec(p3)})")
            side.kv("material", self.material(ti) if ti >= 0 else "TOOLS/TOOLSNODRAW")
            self.texture_keys(side, ti)
            if disp is not None:
                side.add(disp)
                self.last_disp_side = side.get("id")
        ed = out.add(Block("editor"))
        ed.kv("color", "0 180 255").kv("visgroupshown", 1).kv("visgroupautoshown", 1)
        return out

    def portal_solid(self, outline: list[tuple]) -> Block | None:
        """A func_areaportal's brush (compiling removed it): its outline, one unit thick, areaportal on its two
        faces and nodraw round the edge."""
        n = _newell(outline)
        ln = math.sqrt(sum(c * c for c in n))
        if ln < 1e-6:
            return None
        n = tuple(c / ln for c in n)
        front = [tuple(p[k] + n[k] * 0.5 for k in range(3)) for p in outline]
        back = [tuple(p[k] - n[k] * 0.5 for k in range(3)) for p in outline]
        faces = [(list(reversed(front)), "TOOLS/TOOLSAREAPORTAL"), (back, "TOOLS/TOOLSAREAPORTAL")]
        m = len(outline)
        for i in range(m):
            j = (i + 1) % m
            faces.append(([front[i], front[j], back[j], back[i]], "TOOLS/TOOLSNODRAW"))
        out = Block("solid")
        out.kv("id", self.new_id())
        for w, mat in faces:
            p1, p2, p3 = _plane_points(w)
            side = out.add(Block("side"))
            side.kv("id", self.new_id())
            side.kv("plane", f"({_pvec(p1)}) ({_pvec(p2)}) ({_pvec(p3)})")
            side.kv("material", mat)
            side.kv("uaxis", "[1 0 0 0] 0.25").kv("vaxis", "[0 -1 0 0] 0.25").kv("rotation", 0)
            side.kv("lightmapscale", DEFAULT_LIGHTMAP_SCALE).kv("smoothing_groups", 0)
        return out

    def entity_block(self, pairs: list[tuple[str, str]], solids: list[Block] | None = None) -> Block:
        e = Block("entity")
        e.kv("id", self.new_id())
        conn = None
        for k, v in pairs:
            if k in ("hammerid", "world_mins", "world_maxs") or k == "model" and v.startswith("*"):
                continue
            if "\x1b" in v or (v.count(",") >= 4 and k[:2] in ("On", "on")):   # an output
                conn = conn or Block("connections")
                conn.kv(k, v.replace("\x1b", ","))
                continue
            e.kv(k, v)
        if conn is not None:
            e.add(conn)
        for s in solids or []:
            e.add(s)
        ed = e.add(Block("editor"))
        ed.kv("color", "220 30 220").kv("visgroupshown", 1).kv("visgroupautoshown", 1).kv("logicalpos", "[0 0]")
        return e

    def decompile(self) -> str:
        models = self.bsp.models()
        ranges = self.bsp.model_ranges()
        world_end = ranges[0][1] + 1 if ranges and ranges[0][1] >= 0 else 0
        covered = set(range(0, world_end))
        for a, z in ranges[1:]:
            if a >= 0:
                covered.update(range(a, z + 1))
        # brushes in no model's range are in world coordinates: trigger-textured ones are func_viscluster's (vbsp
        # uses them for vis and removes the entity, leaving the brushes), the rest the world's
        unclaimed = [i for i in range(len(self.brushes)) if i not in covered]
        viscluster = [i for i in unclaimed if self.all_trigger(i)]
        world_brushes = list(range(0, world_end)) + [i for i in unclaimed if i not in viscluster]
        portal_brushes = [b for b in world_brushes if self.brushes[b][2] & CONTENTS_AREAPORTAL]
        world = self.world = Block("world")
        world.kv("id", self.new_id())
        entities: list[Block] = []
        detail: list[Block] = []
        ents = self.bsp.entities()
        portals = self.bsp.areaportals()
        portal_owner = self.match_portals(portals, portal_brushes)
        unmatched = len(set(portal_brushes) - set(portal_owner.values()))
        if unmatched:
            self.notes.append(f"{unmatched} areaportal brushes matched no func_areaportal and were left out")
        for e in ents:
            if dict(e).get("classname") == "worldspawn":
                for k, v in e:
                    if k not in ("id", "hammerid", "world_mins", "world_maxs"):
                        world.kv(k, v)
                break
        for b in world_brushes:
            if self.brushes[b][2] & CONTENTS_AREAPORTAL:
                continue                                     # (their func_areaportal's)
            s = self.solid(b)
            if s is None:
                continue
            c = self.brushes[b][2]
            if c & CONTENTS_DETAIL:
                detail.append(s)
            else:
                world.add(s)
        self.add_displacements()
        if detail:
            entities.append(self.entity_block([("classname", "func_detail")], detail))
        for b in viscluster:
            sb = self.solid(b)
            if sb is not None:
                entities.append(self.entity_block([("classname", "func_viscluster")], [sb]))
        accessors = {}
        for e in ents:
            d = dict(e)
            if d.get("classname") == "info_overlay_accessor":
                try:
                    accessors[int(d.get("OverlayID", "-1"))] = d.get("targetname", "")
                except ValueError:
                    pass
        for e in ents:
            d = dict(e)
            cls = d.get("classname", "")
            if cls in ("worldspawn", "info_overlay_accessor"):
                continue
            solids = None
            model = d.get("model", "")
            if model.startswith("*"):
                try:
                    mi = int(model[1:])
                except ValueError:
                    mi = -1
                if 0 < mi < len(models) and ranges[mi][0] >= 0:
                    origin = tuple(float(x) for x in d.get("origin", "0 0 0").split()[:3]) \
                        if d.get("origin") else (0.0, 0.0, 0.0)
                    a, z = ranges[mi]
                    solids = [s for s in (self.solid(b, origin) for b in range(a, z + 1)) if s]
                if not solids:
                    self.notes.append(f"{cls} '{d.get('targetname', model)}' has no brushes left in the map: "
                                      "kept without them")
            if cls in ("func_areaportal", "func_areaportalwindow") and not solids:
                key = int(d.get("portalnumber", "0") or 0)
                outline = portals.get(key)
                b = portal_owner.get(key)
                s = self.solid(b) if b is not None else (self.portal_solid(outline) if outline else None)
                if s is None:
                    self.notes.append(f"func_areaportal {d.get('portalnumber')}: no outline found, left out")
                    continue
                solids = [s]
            entities.append(self.entity_block(e, solids))
        solid_world = [b for b in world_brushes if self.brushes[b][2] & CONTENTS_SOLID]
        lighting = {}                                    # lighting origin -> its info_lighting's name
        for p in self.bsp.static_props():
            origin = self.off_walls(p["origin"], solid_world)
            kv = [("classname", "prop_static"), ("model", p["model"]), ("origin", _vec(origin)),
                  ("angles", _vec(p["angles"])), ("solid", str(p["solid"])), ("skin", str(p["skin"])),
                  ("fademindist", _num(p["fademindist"])), ("fademaxdist", _num(p["fademaxdist"])),
                  ("fadescale", _num(p["fadescale"])),
                  ("disableshadows", "1" if p["flags"] & 0x10 else "0"),
                  ("screenspacefade", "1" if p["flags"] & 0x20 else "0"),
                  ("disablevertexlighting", "1" if p["flags"] & 0x40 else "0"),
                  ("disableselfshadowing", "1" if p["flags"] & 0x80 else "0"),
                  ("ignorenormals", "1" if p["flags"] & 0x08 else "0")]
            if "mincpulevel" in p:
                kv += [("mincpulevel", str(p["mincpulevel"])), ("maxcpulevel", str(p["maxcpulevel"])),
                       ("mingpulevel", str(p["mingpulevel"])), ("maxgpulevel", str(p["maxgpulevel"])),
                       ("rendercolor", " ".join(str(c) for c in p["rendercolor"][:3]))]
            if "disableX360" in p:
                kv.append(("disableX360", str(p["disableX360"])))
            if "rendercolor" in p:
                kv.append(("renderamt", str(p["rendercolor"][3])))
            if p["flags"] & 0x2:                            # (lit from a point other than its origin)
                key = tuple(round(c, 2) for c in p["lightingorigin"])
                if key not in lighting:
                    lighting[key] = f"sprp_lighting_{len(lighting)}"
                    entities.append(self.entity_block([("classname", "info_lighting"), ("targetname", lighting[key]),
                                                       ("origin", _vec(p["lightingorigin"]))]))
                kv.append(("lightingorigin", lighting[key]))
            entities.append(self.entity_block(kv))
        entities += self.overlay_entities(accessors)
        for origin, size in self.bsp.cubemaps():
            entities.append(self.entity_block([("classname", "env_cubemap"), ("origin", _vec(origin)),
                                               ("cubemapsize", str(size))]))
        return _document(world, entities)


def _document(world: Block, entities: list[Block]) -> str:
    out: list[str] = []
    vi = Block("versioninfo")
    vi.kv("editorversion", 400).kv("editorbuild", 6157).kv("mapversion", 1).kv("formatversion", 100).kv("prefab", 0)
    vi.write(out, 0)
    for name in ("visgroups", "viewsettings"):
        Block(name).write(out, 0)
    world.write(out, 0)
    for e in entities:
        e.write(out, 0)
    cam = Block("cameras")
    cam.kv("activecamera", -1)
    cam.write(out, 0)
    return "".join(out)


def decompile(data: bytes) -> tuple[str, list[str]]:
    """A compiled map's bytes -> (VMF text, notes about what couldn't be recovered)."""
    d = Decompiler(data)
    text = d.decompile()
    if d.skipped_brushes:
        d.notes.append(f"{d.skipped_brushes} brushes had no volume and were left out")
    return text, d.notes
