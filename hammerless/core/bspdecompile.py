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
L_ENTITIES, L_PLANES, L_TEXDATA, L_NODES, L_TEXINFO, L_LEAFS = 0, 1, 2, 5, 6, 10
L_MODELS, L_LEAFBRUSHES, L_BRUSHES, L_BRUSHSIDES = 14, 17, 18, 19
L_AREAPORTALS, L_GAME, L_CLIPPORTALVERTS, L_CUBEMAPS, L_TEXSTRDATA, L_TEXSTRTABLE = 21, 35, 41, 42, 43, 44

CONTENTS_SOLID, CONTENTS_WINDOW, CONTENTS_GRATE, CONTENTS_BLOCKLOS, CONTENTS_OPAQUE = 0x1, 0x2, 0x8, 0x40, 0x80
CONTENTS_AREAPORTAL, CONTENTS_PLAYERCLIP, CONTENTS_MONSTERCLIP = 0x8000, 0x10000, 0x20000
CONTENTS_DETAIL, CONTENTS_TRANSLUCENT, CONTENTS_LADDER = 0x8000000, 0x10000000, 0x20000000
DEFAULT_LIGHTMAP_SCALE = 16
# compiling gives a side no face of its own its texture from another side with the same flags (vbsp's
# CompactTexinfos): a tool brush's sides come out nodraw or clip. Its contents say what it was.
TOOL_GENERIC = ("TOOLS/TOOLSNODRAW", "TOOLS/TOOLSCLIP")


def tool_texture(contents: int) -> str | None:
    """The tool texture a brush with these contents (and no visible face) had, or None for a solid brush."""
    if contents & CONTENTS_AREAPORTAL:
        return "TOOLS/TOOLSAREAPORTAL"
    if contents & CONTENTS_LADDER:
        return "TOOLS/CLIMB"
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
        nodes = self.records(L_NODES, "3i6h2H2h")                 # planenum, children[2], ...
        leafs = self.records(L_LEAFS, "i2h6h4H2h")                # ..., firstleafbrush, numleafbrushes, ...
        leafbrushes = [t[0] for t in self.records(L_LEAFBRUSHES, "H")]
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
        for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|(\{)|(\})', text):
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


def _axis(vec4) -> tuple[str, float]:
    """A texinfo row (axis / scale, shift) -> '[x y z shift] scale'."""
    x, y, z, shift = vec4
    ln = math.sqrt(x * x + y * y + z * z)
    if ln < 1e-9:
        return "[1 0 0 0] 0.25", 0.25
    scale = 1.0 / ln
    return (f"[{x * scale:.6g} {y * scale:.6g} {z * scale:.6g} {shift:.6g}] {scale:.6g}"), scale


SPREAD = 256.0         # units between a side's three plane points


def plane_points(n, d, w):
    """Three points on the plane n.x = d, wound so Hammer's normal points out (n): spread SPREAD apart around the
    face's middle, not the face's corners (a tiny face's corners, rounded, tilt the plane), and whole numbers
    wherever the plane passes through them."""
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
        on = abs(sum(r[k] * n[k] for k in range(3)) - d) < 1e-3
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
            side.kv("plane", f"({_vec(p1)}) ({_vec(p2)}) ({_vec(p3)})")
            mat = self.material(s[1])
            if tool and (mat in TOOL_GENERIC or mat.startswith("TOOLS/")):
                mat = tool
            side.kv("material", mat)
            self.side_polys.setdefault(s[0], []).append((side, w))
            if 0 <= s[1] < len(self.texinfos):
                (tu, tv), (lu, _lv), _flags, _td = self.texinfos[s[1]]
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
            made += 1
        if made < 4:
            self.skipped_brushes += 1
            return None
        ed = out.add(Block("editor"))
        ed.kv("color", "0 180 255").kv("visgroupshown", 1).kv("visgroupautoshown", 1)
        return out

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
            side.kv("plane", f"({_vec(p1)}) ({_vec(p2)}) ({_vec(p3)})")
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
        first_model = min((r[0] for r in ranges[1:] if r[0] >= 0), default=len(self.brushes))
        covered = set(range(0, first_model))
        for a, z in ranges[1:]:
            if a >= 0:
                covered.update(range(a, z + 1))
        # brushes in no model's range are in world coordinates: trigger-textured ones are func_viscluster's (vbsp
        # uses them for vis and removes the entity, leaving the brushes), the rest the world's
        unclaimed = [i for i in range(len(self.brushes)) if i not in covered]
        viscluster = [i for i in unclaimed if self.all_trigger(i)]
        world_brushes = list(range(0, first_model)) + [i for i in unclaimed if i not in viscluster]
        portal_brushes = [b for b in world_brushes if self.brushes[b][2] & CONTENTS_AREAPORTAL]
        world = Block("world")
        world.kv("id", self.new_id())
        entities: list[Block] = []
        detail: list[Block] = []
        ents = self.bsp.entities()
        portals = self.bsp.areaportals()
        portal_owner = self.match_portals(portals, portal_brushes)
        for e in ents:
            if dict(e).get("classname") == "worldspawn":
                for k, v in e:
                    if k not in ("id",):
                        world.kv(k, v)
                break
        for b in world_brushes:
            if self.brushes[b][2] & CONTENTS_AREAPORTAL:
                continue                                     # (their func_areaportal's)
            s = self.solid(b)
            if s is None:
                continue
            c = self.brushes[b][2]
            if c & CONTENTS_DETAIL and c & (CONTENTS_SOLID | CONTENTS_TRANSLUCENT):
                detail.append(s)
            else:
                world.add(s)
        if detail:
            entities.append(self.entity_block([("classname", "func_detail")], detail))
        for b in viscluster:
            sb = self.solid(b)
            if sb is not None:
                entities.append(self.entity_block([("classname", "func_viscluster")], [sb]))
        for e in ents:
            d = dict(e)
            cls = d.get("classname", "")
            if cls == "worldspawn":
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
        for p in self.bsp.static_props():
            kv = [("classname", "prop_static"), ("model", p["model"]), ("origin", _vec(p["origin"])),
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
            entities.append(self.entity_block(kv))
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
