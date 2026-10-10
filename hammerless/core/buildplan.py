"""Smart builds: compare this build's VMF with the one the current BSP was compiled from and pick
the least work that gives the same map as a full compile.

  same      nothing that reaches the BSP changed
  entities  only entity keyvalues / outputs / point entities changed: vbsp -onlyents rewrites the
            entity lump (and static props) and keeps geometry, visibility and lighting
  lighting  lights or static props changed too (they're baked by vrad): -onlyents, then vrad
  full      geometry changed (world or brush-entity brushes, displacements, overlays, cubemaps,
            worldspawn settings...): the usual vbsp + vvis + vrad

Anything unknown counts as geometry, so a doubt always means a full compile.
"""
from __future__ import annotations

from .vmf import Block, parse

# whole entity is compiled into geometry / vis lumps by vbsp
GEOMETRY_CLASSES = {"func_detail", "info_overlay", "info_overlay_transition", "env_cubemap", "func_areaportal",
                    "func_areaportalwindow", "func_viscluster", "func_occluder", "water_lod_control",
                    "func_instance", "info_lighting_relative", "prop_detail", "func_ladder", "func_vehicleclip",
                    "func_dustmotes", "func_dustcloud", "func_smokevolume", "prop_detail_sprite",
                    "info_no_dynamic_shadow"}
# baked by vrad (lights, and static props: their lighting and shadows)
LIGHT_CLASSES = {"light", "light_spot", "light_environment", "light_directional", "info_lighting", "prop_static",
                 "static_prop"}
# brush entity keyvalues vrad reads
LIGHT_KEYS = {"_minlight", "vrad_brush_cast_shadows", "disableshadows", "_lightmode", "disablevertexlighting",
              "disableselfshadowing"}
SKIP_BLOCKS = {"versioninfo", "visgroups", "viewsettings", "cameras", "cordon", "cordons", "editor"}


def _canon(block: Block):
    """A block with its ids and editor-only parts dropped, as nested tuples."""
    out = []
    for it in block.items:
        if isinstance(it, Block):
            if it.name.lower() in SKIP_BLOCKS:
                continue
            out.append((it.name.lower(), _canon(it)))
        elif it[0].lower() not in ("id", "hammerid"):
            out.append((it[0].lower(), it[1]))
    return tuple(out)


def _split(text: str):
    """(geometry, lighting, entities) signatures of a VMF."""
    geometry, lighting, entities = [], [], []
    blocks = parse(text)
    # entities other classes compile from: a light's aim (its target's origin, read by the light compilers) is
    # lighting; an areaportal window's target brush (made see-through) is geometry
    aim, window = set(), set()
    for b in blocks:
        if b.name.lower() != "entity":
            continue
        cls = (b.get("classname") or "").lower()
        if cls in LIGHT_CLASSES and b.get("target"):
            aim.add(b.get("target").lower())
        if cls == "func_areaportalwindow" and b.get("target"):
            window.add(b.get("target").lower())
    for b in blocks:
        name = b.name.lower()
        if name in SKIP_BLOCKS:
            continue
        if name == "world":
            geometry.append(("world", _canon(b)))
            continue
        if name != "entity":
            geometry.append((name, _canon(b)))           # unknown top-level block: be safe
            continue
        cls = (b.get("classname") or "").lower()
        canon = _canon(b)
        tname = (b.get("targetname") or "").lower()
        if cls == "env_fog_controller":
            # (its far Z makes vis radial, which changes the visibility and which leaves see the sky)
            geometry.append(("fog farz", b.get("farz")))
            entities.append(canon)
        elif cls in GEOMETRY_CLASSES or (tname and tname in window):
            geometry.append(("entity", canon))
        elif cls in LIGHT_CLASSES or (tname and tname in aim and not b.blocks("solid")):
            lighting.append(canon)
        elif b.blocks("solid"):
            # brush entity: its brushes (and origin, class) are compiled into a model; the rest is entity data
            geometry.append(("brush entity", cls, b.get("origin"), tuple(_canon(s) for s in b.blocks("solid"))))
            lighting.append(tuple(kv for kv in canon if kv[0] in LIGHT_KEYS or (tname and tname in aim
                                                                               and kv[0] == "origin")))
            entities.append(tuple(kv for kv in canon if kv[0] != "solid" and kv[0] not in LIGHT_KEYS))
        else:
            entities.append(canon)
    return geometry, lighting, entities      # order kept: vbsp numbers light styles and static props by it


def plan(old_text: str | None, new_text: str) -> tuple[str, str]:
    """('same' | 'entities' | 'lighting' | 'full', why)."""
    if not old_text:
        return "full", "no earlier compile to build on"
    try:
        og, ol, oe = _split(old_text)
        ng, nl, ne = _split(new_text)
    except Exception as ex:                       # never guess on a map we can't read
        return "full", f"couldn't compare with the last build ({ex})"
    if og != ng:
        return "full", "geometry changed"
    if ol != nl:
        return "lighting", "lights or static props changed, geometry didn't"
    if oe != ne:
        return "entities", "only entities changed"
    return "same", "nothing that reaches the map changed"


def _in_a_brush(blocks, p, eps: float = 0.1) -> bool:
    """The point is inside one of the map's brushes (real solid, not a room vbsp filled)."""
    from .vmfimport import parse_plane, plane_of
    for top in blocks:
        for so in top.blocks("solid"):
            inside = True
            for sd in so.blocks("side"):
                try:
                    n, d = plane_of(*parse_plane(sd.get("plane", "")))
                except (ValueError, TypeError):
                    inside = False
                    break
                if n[0] * p[0] + n[1] * p[1] + n[2] * p[2] > d + eps:
                    inside = False
                    break
            if inside and so.blocks("side"):
                return True
    return False


def filled_points(new_text: str, points) -> list:
    """The points not inside any of the map's brushes (where solid in the compiled map means vbsp filled a
    room, not a wall or ceiling a light or prop sits in)."""
    blocks = parse(new_text)
    return [p for p in points if not _in_a_brush(blocks, p)]


def new_entity_points(old_text: str, new_text: str) -> list[tuple[float, float, float]]:
    """Origins of point entities that are new or moved (as vbsp floods from them: 1 unit up)."""
    def origins(text):
        out = set()
        for b in parse(text):
            if b.name.lower() == "entity" and not b.blocks("solid") and b.get("origin"):
                try:
                    x, y, z = (float(c) for c in b.get("origin").split()[:3])
                except ValueError:
                    continue
                out.add((x, y, z + 1.0))
        return out
    return sorted(origins(new_text) - origins(old_text))


def any_in_solid(bsp_path: str, points) -> bool:
    """Whether any point is in a solid leaf of the compiled map: vbsp fills rooms no entity reaches, so an
    entity put in such a room needs the geometry compiled again (an entities-only build would keep it filled)."""
    import struct
    if not points:
        return False
    try:
        with open(bsp_path, "rb") as f:
            data = f.read()
        return _any_in_solid(data, points)
    except (OSError, struct.error):          # (no map to look in, or a damaged one: compile it all)
        return True


def _any_in_solid(data: bytes, points) -> bool:
    import struct

    def lump(i):
        _v, off, ln, _c = struct.unpack_from("<iiii", data, 8 + 16 * i)
        return data[off:off + ln]
    nodes, planes, leafs = lump(5), lump(1), lump(10)
    if len(nodes) < 32 or not leafs:
        return True
    for p in points:
        node = 0
        for _ in range(8192):
            plane, c0, c1 = struct.unpack_from("<3i", nodes, 32 * node)
            nx, ny, nz, dist = struct.unpack_from("<4f", planes, 20 * plane)
            child = c0 if nx * p[0] + ny * p[1] + nz * p[2] - dist >= 0 else c1
            if child < 0:
                leaf = -1 - child
                if 32 * leaf + 4 <= len(leafs) and struct.unpack_from("<i", leafs, 32 * leaf)[0] & 1:   # (SOLID)
                    return True
                break
            node = child
    return False


PAK_LUMP = 40


def strip_stale(bsp_path: str) -> bool:
    """vbsp -onlyents leaves 'stale.txt' in the BSP's pakfile, which makes the engine call the map
    not final. A smart build's map is equivalent to a full compile, so take it out again (the
    pakfile is rewritten in place, uncompressed like vbsp's). Returns whether it was there."""
    import io
    import struct
    import zipfile
    with open(bsp_path, "r+b") as f:
        data = bytearray(f.read())
        at = 8 + 16 * PAK_LUMP
        ver, off, length, cc = struct.unpack_from("<iiii", data, at)
        if length <= 0:
            return False
        old = zipfile.ZipFile(io.BytesIO(bytes(data[off:off + length])))
        names = [i.filename for i in old.infolist()]
        if "stale.txt" not in names:
            return False
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as new:
            for info in old.infolist():
                if info.filename != "stale.txt":
                    new.writestr(info, old.read(info.filename), compress_type=zipfile.ZIP_STORED)
        blob = buf.getvalue()
        if len(blob) > length:                    # can't happen (one file fewer), but never corrupt a map
            return False
        data[off:off + length] = blob + bytes(length - len(blob))
        struct.pack_into("<iiii", data, at, ver, off, len(blob), cc)
        f.seek(0)
        f.write(data)
    return True
