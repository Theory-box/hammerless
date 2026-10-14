"""The game's particle effects: read from its .pcf files (Valve's DMX binary format) for the effect browser.

A .pcf holds elements; each DmeParticleSystemDefinition is an effect the game can spawn by name (an
info_particle_system's effect_name). Measured on L4D2's 52 files: DMX "binary 4" (string count int32, string
indices int16, element names and string values from the string table) and "binary 3" (string count int16,
element names and string values written inline); every file reads to its last byte.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# DMX attribute types (DmAttributeType_t as these files use them): element, int, float, bool, string, binary,
# object id, color, vector2, vector3, vector4, qangle, quaternion, matrix; +14 is an array of the type
_FIXED = {2: 4, 3: 4, 4: 1, 7: 16, 8: 4, 9: 8, 10: 12, 11: 16, 12: 12, 13: 16, 14: 64}


@dataclass
class Element:
    type: str
    name: str
    attrs: dict = field(default_factory=dict)


def read_dmx(data: bytes) -> list[Element]:
    """Every element of a DMX binary file (encodings 3 and 4), with its attributes. Element references are
    ("elem", index) into the returned list."""
    head_end = data.index(b"\n\x00") + 2
    head = data[:head_end].decode("latin-1")
    ver = int(head.split("binary", 1)[1].split()[0])
    p = head_end
    if ver >= 4:
        (n,) = struct.unpack_from("<i", data, p)
        p += 4
    else:
        (n,) = struct.unpack_from("<h", data, p)
        p += 2
    table = []
    for _ in range(n):
        e = data.index(b"\x00", p)
        table.append(data[p:e].decode("latin-1"))
        p = e + 1

    def inline() -> str:
        nonlocal p
        e = data.index(b"\x00", p)
        s = data[p:e].decode("latin-1")
        p = e + 1
        return s

    def index() -> str:
        nonlocal p
        (i,) = struct.unpack_from("<h", data, p)
        p += 2
        return table[i]

    (count,) = struct.unpack_from("<i", data, p)
    p += 4
    elems: list[Element] = []
    for _ in range(count):
        kind = index()
        name = index() if ver >= 4 else inline()
        p += 16                                   # (the element's id)
        elems.append(Element(kind, name))

    def value(t: int, in_array: bool = False):
        nonlocal p
        if t == 1:
            (i,) = struct.unpack_from("<i", data, p)
            p += 4
            return ("elem", i)
        if t == 5:
            return inline() if (ver < 4 or in_array) else index()
        if t == 6:
            (ln,) = struct.unpack_from("<i", data, p)
            p += 4 + ln
            return None
        size = _FIXED[t]
        raw = data[p:p + size]
        p += size
        if t == 2:
            return struct.unpack("<i", raw)[0]
        if t == 3:
            return struct.unpack("<f", raw)[0]
        if t == 4:
            return raw[0] != 0
        if t == 8:
            return tuple(raw)
        if t in (9, 10, 11, 12, 13, 14):
            return struct.unpack(f"<{size // 4}f", raw)
        return raw

    for el in elems:
        (na,) = struct.unpack_from("<i", data, p)
        p += 4
        for _ in range(na):
            key = index()
            t = data[p]
            p += 1
            if t >= 15:
                (cnt,) = struct.unpack_from("<i", data, p)
                p += 4
                el.attrs[key] = [value(t - 14, True) for _ in range(cnt)]
            else:
                el.attrs[key] = value(t)
    if p != len(data):
        raise ValueError(f"DMX: {len(data) - p} bytes left over")
    return elems


@dataclass
class Effect:
    name: str              # what info_particle_system's effect_name takes
    file: str              # the .pcf it comes from
    material: str          # its sprite material ("" for effects that only hold child effects)
    radius: float          # the particles' starting size
    color: tuple           # (r, g, b, a) 0-255
    children: list         # names of the effects it spawns
    is_child: bool = False  # spawned by another effect (rarely placed on its own)


def effects_in(data: bytes, file: str) -> list[Effect]:
    """The effects (particle system definitions) a .pcf defines."""
    elems = read_dmx(data)
    out = []
    for el in elems:
        if el.type != "DmeParticleSystemDefinition":
            continue
        kids = []
        for ref in el.attrs.get("children", []) or []:
            if isinstance(ref, tuple) and 0 <= ref[1] < len(elems):
                child = elems[ref[1]]
                target = child.attrs.get("child")
                if isinstance(target, tuple) and 0 <= target[1] < len(elems):
                    kids.append(elems[target[1]].name)
        color = tuple(el.attrs.get("color", (255, 255, 255, 255)))
        radius = float(el.attrs.get("radius", 5.0))
        for ref in el.attrs.get("initializers", []) or []:      # (a "Color Random" rule sets the colour)
            if isinstance(ref, tuple) and 0 <= ref[1] < len(elems):
                init = elems[ref[1]].attrs
                if init.get("functionName") == "Color Random" and "color1" in init and "color2" in init:
                    c1, c2 = init["color1"], init["color2"]
                    color = tuple((x + y) // 2 for x, y in zip(c1, c2))
                elif init.get("functionName") == "Radius Random" and "radius_max" in init:   # (the size it spawns at)
                    radius = (float(init.get("radius_min", init["radius_max"])) + float(init["radius_max"])) / 2
        out.append(Effect(el.name, file, str(el.attrs.get("material") or ""), radius, color, kids))
    return out


def sheet_first_frame(vtf: bytes):
    """A sprite sheet's first frame as (u0, v0, u1, v1) in 0-1 (the VTF's 'sheet' resource: particle textures hold
    their animation frames side by side), or None for a plain texture."""
    try:
        sig, major, minor = struct.unpack_from("<4sII", vtf, 0)
        if sig != b"VTF\0" or minor < 3:
            return None
        (count,) = struct.unpack_from("<I", vtf, 68)
        for i in range(count):
            tag, _fl, off = struct.unpack_from("<3sBI", vtf, 80 + i * 8)
            if tag != b"\x10\x00\x00":
                continue
            p = off + 4                                   # (after the resource's byte length)
            version, sequences = struct.unpack_from("<ii", vtf, p)
            p += 8
            if sequences < 1:
                return None
            _seq, _clamp, frames, _total = struct.unpack_from("<iiif", vtf, p)
            p += 16
            if frames < 1:
                return None
            p += 4                                        # (the first frame's time)
            u0, v0, u1, v1 = struct.unpack_from("<4f", vtf, p)
            if 0 <= u0 < u1 <= 1.0001 and 0 <= v0 < v1 <= 1.0001:
                return u0, v0, u1, v1
            return None
    except struct.error:
        return None
    return None


def sprite(content, material: str, size: int = 128):
    """An effect's sprite as (rgba float 0-1, additive): its material's texture (a sprite sheet's first frame),
    or None."""
    import numpy as np
    from .gamematerials import read_texture_bytes, read_vmt, texture_path
    from .vtf_read import read_vtf
    mat = material.lower().replace("\\", "/")
    mat = mat[:-4] if mat.endswith(".vmt") else mat
    params = read_vmt(content, mat)
    tex = params.get("$basetexture")
    if not tex:
        return None
    data = read_texture_bytes(content, texture_path(tex))
    if not data:
        return None
    try:
        _w, _h, img = read_vtf(data, max_size=512)
    except (ValueError, struct.error, KeyError):
        return None
    rect = sheet_first_frame(data)
    if rect is not None:
        h, w = img.shape[:2]
        u0, v0, u1, v1 = rect
        img = img[int(v0 * h):max(int(v0 * h) + 1, int(round(v1 * h))),
                  int(u0 * w):max(int(u0 * w) + 1, int(round(u1 * w)))]
    else:
        img = _first_cell(img)
    rgba = img.astype(np.float32) / 255.0
    additive = params.get("$additive", "0").strip() in ("1", "1.0") or "add" in params.get("shader", "")
    return rgba, additive


def _runs(filled):
    """Runs of True in a 1-D bool array, as (start, end)."""
    out, start = [], None
    for i, f in enumerate(filled):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(filled)))
    return out


def _first_cell(img):
    """A sprite sheet without its frame list (some textures have none): frames sit in a grid with see-through gaps
    between them, so the first frame is the first run of filled columns within the first run of filled rows.
    A plain sprite (one blob, or no clear grid) comes back as it is."""
    import numpy as np
    a = img[..., 3].astype(np.float32) if img.shape[2] == 4 else img.max(axis=2).astype(np.float32)
    if a.max() <= 0:
        return img
    rows = _runs(a.max(axis=1) > 8)
    cols = _runs(a.max(axis=0) > 8)
    if len(rows) < 2 and len(cols) < 2:
        return img
    r0, r1 = rows[0]
    band_cols = _runs(a[r0:r1].max(axis=0) > 8)
    c0, c1 = band_cols[0] if band_cols else (0, img.shape[1])
    if (r1 - r0) < 4 or (c1 - c0) < 4:
        return img
    return img[r0:r1, c0:c1]


def _resize(rgba, size: int):
    """Nearest-neighbour-free resize (box filter down, bilinear up) with numpy only."""
    import numpy as np
    h, w = rgba.shape[:2]
    ys = np.clip((np.arange(size) + 0.5) * h / size - 0.5, 0, h - 1)
    xs = np.clip((np.arange(size) + 0.5) * w / size - 0.5, 0, w - 1)
    y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    fy, fx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    a = rgba[y0][:, x0] * (1 - fx) + rgba[y0][:, x1] * fx
    b = rgba[y1][:, x0] * (1 - fx) + rgba[y1][:, x1] * fx
    return a * (1 - fy) + b * fy


def thumbnail(content, eff: Effect, by_name: dict, size: int = 128, _cache: dict | None = None):
    """An effect's picture as rgba float (size x size), straight alpha: its own sprite and its children's (two
    levels), each tinted by its colour and drawn at its size relative to the biggest, centred; additive sprites
    add light, the others are layered over. None when none of them has a sprite."""
    import numpy as np
    cache = _cache if _cache is not None else {}
    parts = []

    def collect(e, depth):
        if e.material:
            key = e.material.lower()
            if key not in cache:
                cache[key] = sprite(content, e.material)
            if cache[key] is not None:
                parts.append((cache[key], e))
        if depth < 2:
            for k in e.children:
                child = by_name.get(k.lower())
                if child is not None and child is not e:
                    collect(child, depth + 1)
    collect(eff, 0)
    if not parts:
        return None
    biggest = max(max(p[1].radius, 1.0) for p in parts)
    parts = [p for p in parts if max(p[1].radius, 1.0) >= 0.3 * biggest]   # (not debris and specks: the main look)
    color = np.zeros((size, size, 3), np.float32)        # (premultiplied while compositing)
    alpha = np.zeros((size, size), np.float32)
    for (img, additive), e in sorted(parts, key=lambda p: -p[1].radius):
        side = int(max(8, min(size, size * (0.35 + 0.6 * max(e.radius, 1.0) / biggest))))
        spr = _resize(img, side)
        tint = np.array(e.color[:3], np.float32) / 255.0
        a = spr[..., 3] * (e.color[3] / 255.0 if len(e.color) > 3 else 1.0)
        rgb = spr[..., :3] * tint
        o = (size - side) // 2
        region = (slice(o, o + side), slice(o, o + side))
        if additive:                        # (light added: its brightness is its visibility)
            color[region] += rgb * a[..., None]
            alpha[region] = np.maximum(alpha[region], np.clip(rgb.max(axis=2) * a, 0, 1))
        else:
            color[region] = rgb * a[..., None] + color[region] * (1 - a[..., None])
            alpha[region] = a + alpha[region] * (1 - a)
    rgb = np.where(alpha[..., None] > 1e-4, color / np.maximum(alpha[..., None], 1e-4), 0)
    return np.dstack([np.clip(rgb, 0, 1), np.clip(alpha, 0, 1)])


def write_png(path: str, rgba) -> None:
    """An rgba float (0-1) image as a PNG, with zlib only (no imaging library in Blender's Python)."""
    import zlib
    import numpy as np
    img = (np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8)
    h, w = img.shape[:2]
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def game_effects(content) -> list[Effect]:
    """Every effect the game loads: the files its particles_manifest.txt lists (in that order), each effect once
    (a later file's definition of a name replaces an earlier one, as in the game). Effects only ever spawned by
    another effect are marked is_child."""
    manifest = content.read("particles/particles_manifest.txt") or b""
    files = []
    for line in manifest.decode("latin-1", "replace").splitlines():
        parts = line.replace('"', " ").split()
        if len(parts) >= 2 and parts[0].lower() == "file":
            files.append(parts[1].lstrip("!").replace("\\", "/"))
    by_name: dict[str, Effect] = {}
    for f in files:
        data = content.read(f)
        if not data:
            continue
        try:
            for eff in effects_in(data, f):
                by_name[eff.name.lower()] = eff
        except (ValueError, IndexError, struct.error):
            continue                     # (a file this reader can't follow: its effects are left out)
    spawned = {k.lower() for e in by_name.values() for k in e.children}
    for key, eff in by_name.items():
        eff.is_child = key in spawned
    return list(by_name.values())
