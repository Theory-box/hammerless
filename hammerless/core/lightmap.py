"""Baked lighting from a compiled map (.bsp), laid out for drawing in Blender.

Reads every lightmapped face (brushes and displacements) with its lightmap pixels, packs the
lightmaps into one atlas, and gives each triangle corner its atlas position, computed the way
the engine does (Source SDK 2013: the face's lightmapVecs, minus m_LightmapTextureMinsInLuxels,
+0.5 so samples land on luxel centres). Pixel values are linear light: ColorRGBExp32 decoded as
c * 2^exp / 255 (TexLightToLinear).

BSP v21 (L4D2) lumps used: 0 entities, 3 vertexes, 6 texinfo, 7 faces, 8 lighting (LDR),
12 edges, 13 surfedges, 14 models, 26 dispinfo, 33 dispverts, 53 lighting (HDR), 58 faces (HDR).
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

import numpy as np

FACE_SIZE = 56
TEXINFO_SIZE = 72
DISPINFO_SIZE = 176
DISPVERT_SIZE = 20
MODEL_SIZE = 48
SURF_SKY2D, SURF_SKY, SURF_NOLIGHT = 0x2, 0x4, 0x400
SURF_BUMPLIGHT = 0x800


@dataclass
class Lightmaps:
    positions: np.ndarray       # (n*3, 3) float32 triangle corners, Hammer units
    uvs: np.ndarray             # (n*3, 2) float32 atlas coordinates, 0..1
    atlas: np.ndarray           # (H, W, 4) float32 linear light, alpha 1
    faces: int                  # lit faces read
    luxels: int                 # lightmap samples read
    hdr: bool                   # read from the HDR lighting (else LDR)


def _lumps(data: bytes):
    if data[:4] != b"VBSP":
        raise ValueError("not a compiled map (.bsp)")

    def lump(i: int) -> bytes:
        _version, offset, length, _fourcc = struct.unpack_from("<4i", data, 8 + 16 * i)
        return data[offset:offset + length]
    return lump


def _entity_origins(text: bytes) -> dict[int, tuple[float, float, float]]:
    """Brush model number -> its entity's origin (vbsp stores brush entity faces relative to it)."""
    found = {}
    for block in re.findall(rb"\{([^{}]*)\}", text):
        kv = dict(re.findall(rb'"([^"]*)"\s+"([^"]*)"', block))
        model, origin = kv.get(b"model", b""), kv.get(b"origin")
        if model.startswith(b"*") and origin:
            try:
                found[int(model[1:])] = tuple(float(v) for v in origin.split()[:3])
            except ValueError:
                pass
    return found


def decode_luxels(raw: bytes) -> np.ndarray:
    """ColorRGBExp32 samples -> (n, 3) float32 linear light."""
    a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 4)
    exp = a[:, 3].view(np.int8).astype(np.float32)
    return a[:, :3].astype(np.float32) * (np.exp2(exp) / 255.0)[:, None]


def _pack(sizes: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], int, int]:
    """Shelf-pack (w, h) blocks: their (x, y) and the atlas size."""
    total = sum(w * h for w, h in sizes) or 1
    width = 256
    while width * width < total * 1.3:
        width *= 2
    width = max(width, max((w for w, _h in sizes), default=1))
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    at = [(0, 0)] * len(sizes)
    x = y = shelf = 0
    for i in order:
        w, h = sizes[i]
        if x + w > width:
            x, y, shelf = 0, y + shelf, 0
        at[i] = (x, y)
        x += w
        shelf = max(shelf, h)
    return at, width, y + shelf


def read_lightmaps(data: bytes, style: int = 0) -> Lightmaps:
    """The lit faces of a compiled map, triangulated, with their lightmaps packed into an atlas.
    style: which light style to show (0 = the map's static lighting)."""
    lump = _lumps(data)
    lighting, faces, hdr = lump(53), lump(58), True
    if not lighting:
        lighting, faces, hdr = lump(8), lump(7), False
    elif not faces:
        faces = lump(7)
    verts = np.frombuffer(lump(3), dtype="<f4").reshape(-1, 3)
    edges = np.frombuffer(lump(12), dtype="<u2").reshape(-1, 2)
    surfedges = np.frombuffer(lump(13), dtype="<i4")
    texinfo, dispinfo, dispverts, models = lump(6), lump(26), lump(33), lump(14)
    origins = _entity_origins(lump(0))

    # which brush model each face belongs to (for the entity origin offset)
    model_of = {}
    for m in range(len(models) // MODEL_SIZE):
        first, count = struct.unpack_from("<2i", models, MODEL_SIZE * m + 40)
        if m in origins:
            for f in range(first, first + count):
                model_of[f] = origins[m]

    blocks = []        # per face: (corner positions, corner luxel coords, triangles, (w, h), samples)
    luxels = 0
    for k in range(len(faces) // FACE_SIZE):
        b = FACE_SIZE * k
        first_edge, num_edges, ti, di = struct.unpack_from("<ihhh", faces, b + 4)
        styles = faces[b + 16:b + 20]
        light_ofs = struct.unpack_from("<i", faces, b + 20)[0]
        mins = struct.unpack_from("<2i", faces, b + 28)
        size = struct.unpack_from("<2i", faces, b + 36)
        if light_ofs < 0 or num_edges < 3 or ti < 0 or style not in styles:
            continue
        flags = struct.unpack_from("<i", texinfo, TEXINFO_SIZE * ti + 64)[0]
        if flags & (SURF_SKY | SURF_SKY2D | SURF_NOLIGHT):
            continue
        lvecs = np.array(struct.unpack_from("<8f", texinfo, TEXINFO_SIZE * ti + 32), dtype=np.float64).reshape(2, 4)
        w, h = size[0] + 1, size[1] + 1
        per_style = w * h * (4 if flags & SURF_BUMPLIGHT else 1)      # bumped: flat + 3 directions
        start = light_ofs + 4 * per_style * styles.index(style)
        raw = lighting[start:start + 4 * w * h]                         # the flat lightmap comes first
        if len(raw) < 4 * w * h:
            continue
        se = surfedges[first_edge:first_edge + num_edges]
        idx = np.where(se >= 0, edges[np.abs(se), 0], edges[np.abs(se), 1])
        corners = verts[idx].astype(np.float64)
        offset = model_of.get(k)
        if di >= 0 and num_edges == 4:
            pos, flat, tris = _displacement(corners, dispinfo, dispverts, di)
        else:
            pos, flat = corners, corners
            tris = np.array([(0, i, i + 1) for i in range(1, num_edges - 1)], dtype=np.int64)
        lux = flat @ lvecs[:, :3].T + lvecs[:, 3] - np.array(mins, dtype=np.float64)
        if offset is not None:
            pos = pos + np.array(offset)
        blocks.append((pos, lux, tris, (w, h), decode_luxels(raw).reshape(h, w, 3)))
        luxels += w * h

    at, width, height = _pack([blk[3] for blk in blocks])
    atlas = np.zeros((max(height, 1), width, 4), dtype=np.float32)
    atlas[..., 3] = 1.0
    positions, uvs = [], []
    for (pos, lux, tris, (w, h), samples), (x, y) in zip(blocks, at):
        atlas[y:y + h, x:x + w, :3] = samples
        uv = np.empty_like(lux)
        uv[:, 0] = (x + lux[:, 0] + 0.5) / width
        uv[:, 1] = (y + lux[:, 1] + 0.5) / atlas.shape[0]
        positions.append(pos[tris].reshape(-1, 3))
        uvs.append(uv[tris].reshape(-1, 2))
    return Lightmaps(
        positions=np.concatenate(positions).astype(np.float32) if positions else np.zeros((0, 3), np.float32),
        uvs=np.concatenate(uvs).astype(np.float32) if uvs else np.zeros((0, 2), np.float32),
        atlas=atlas, faces=len(blocks), luxels=luxels, hdr=hdr)


def _displacement(corners: np.ndarray, dispinfo: bytes, dispverts: bytes, di: int):
    """A displacement's grid: displaced positions, the flat positions they sit over (lightmap
    coordinates come from those, like the base face's), and its triangles."""
    o = DISPINFO_SIZE * di
    start = np.array(struct.unpack_from("<3f", dispinfo, o))
    vert_start, _tri_start, power = struct.unpack_from("<3i", dispinfo, o + 12)
    first = int(np.argmin(((corners - start) ** 2).sum(axis=1)))     # corner 0 is the start position
    c = np.roll(corners, -first, axis=0)
    n = (1 << power) + 1
    t = np.linspace(0.0, 1.0, n)
    # rows run from edge 0->1 to edge 3->2; columns across them (vbsp's layout)
    left = c[0] + (c[1] - c[0]) * t[:, None]
    right = c[3] + (c[2] - c[3]) * t[:, None]
    flat = (left[:, None, :] + (right - left)[:, None, :] * t[None, :, None]).reshape(-1, 3)
    dv = np.frombuffer(dispverts, dtype="<f4", count=5 * n * n, offset=DISPVERT_SIZE * vert_start).reshape(-1, 5)
    pos = flat + dv[:, :3].astype(np.float64) * dv[:, 3:4]
    tris = []
    for r in range(n - 1):
        for col in range(n - 1):
            a, b2, d, e = r * n + col, r * n + col + 1, (r + 1) * n + col, (r + 1) * n + col + 1
            tris += [(a, d, e), (a, e, b2)]
    return pos, flat, np.array(tris, dtype=np.int64)
