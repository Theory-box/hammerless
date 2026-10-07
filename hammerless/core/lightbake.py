"""Lightmaps baked outside vrad (Cycles), written into a compiled map in vrad's own layout.

vrad runs first (it lays out every face's lightmap, writes the world lights, ambient samples and prop
lighting); this module reads that layout and replaces the lightmap values. Format (Source SDK 2013
vrad, read for behaviour):

- a lit face's data starts at lightofs in the lighting lump (53 HDR, 8 LDR); per light style it holds
  the flat lightmap, then for bumped materials (SURF_BUMPLIGHT) 3 more, one per bump direction; each
  is (sizeInLuxels[0]+1) x (sizeInLuxels[1]+1) samples, row by row, 4 bytes each: ColorRGBExp32
  (r, g, b mantissas, signed exponent; the largest mantissa in 128..255, truncated)
- before lightofs, one ColorRGBExp32 per style in reverse style order: the median of the flat samples
- values are linear light in vrad's units (a decoded 1.0 is 255 of them)
- the bump directions are g_localBumpBasis turned into the face's texture frame (GetBumpNormals)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from .lightmap import (DISPINFO_SIZE, FACE_SIZE, MODEL_SIZE, SURF_BUMPLIGHT, SURF_NOLIGHT, SURF_SKY, SURF_SKY2D,
                       TEXINFO_SIZE, _displacement, _entity_origins, _lumps)

OO_SQRT_2 = 0.70710676908493042
OO_SQRT_3 = 0.57735025882720947
OO_SQRT_6 = 0.40824821591377258
OO_SQRT_2_OVER_3 = 0.81649661064147949
LOCAL_BUMP_BASIS = np.array([[OO_SQRT_2_OVER_3, 0.0, OO_SQRT_3],
                             [-OO_SQRT_6, OO_SQRT_2, OO_SQRT_3],
                             [-OO_SQRT_6, -OO_SQRT_2, OO_SQRT_3]])
PAD = 2                     # texels between faces in the bake image (edge samples are filled from inside)


@dataclass
class BakeFace:
    index: int
    light_ofs: int
    style_slot: int          # which of the face's styles is style 0 (the static light)
    nstyles: int
    w: int
    h: int
    bump: bool
    positions: np.ndarray    # (k, 3) triangle corners, Hammer units
    lux: np.ndarray          # (k, 2) their lightmap coordinates (luxel units, 0 = first sample)
    normal: np.ndarray       # the face's plane normal
    bump_normals: np.ndarray  # (3, 3) the bump directions (bumped faces)
    reflectivity: tuple      # the material's average colour (what vrad bounces light with)
    rect: np.ndarray | None = None   # (4, 3) flat faces: the lightmap rectangle, half a sample past each edge
    atlas: tuple = (0, 0)    # where its samples go in the bake image


def bump_normals(s_vec, t_vec, normal) -> np.ndarray:
    """GetBumpNormals with the face normal as the smoothed normal."""
    s_vec, t_vec, normal = (np.asarray(v, dtype=np.float64) for v in (s_vec, t_vec, normal))
    left = float(np.cross(s_vec, t_vec) @ normal) < 0
    b1 = np.cross(normal, s_vec)
    b1 /= np.linalg.norm(b1)
    b0 = np.cross(b1, normal)
    b0 /= np.linalg.norm(b0)
    if left:
        b1 = -b1
    basis = np.stack([b0, b1, normal])
    return LOCAL_BUMP_BASIS @ basis          # each row: local.x * b0 + local.y * b1 + local.z * n


def read_faces(data: bytes) -> tuple[list[BakeFace], int]:
    """The lit faces with their lightmap blocks, and which lighting lump they use (53 HDR / 8 LDR)."""
    lump = _lumps(data)
    lump_no, faces = 53, lump(58)
    if not lump(53):
        lump_no, faces = 8, lump(7)
    elif not faces:
        faces = lump(7)
    planes = lump(1)
    verts = np.frombuffer(lump(3), dtype="<f4").reshape(-1, 3)
    edges = np.frombuffer(lump(12), dtype="<u2").reshape(-1, 2)
    surfedges = np.frombuffer(lump(13), dtype="<i4")
    texinfo, texdata, dispinfo, dispverts, models = lump(6), lump(2), lump(26), lump(33), lump(14)
    origins = _entity_origins(lump(0))
    model_of = {}
    for m in range(len(models) // MODEL_SIZE):
        first, count = struct.unpack_from("<2i", models, MODEL_SIZE * m + 40)
        if m in origins:
            for f in range(first, first + count):
                model_of[f] = origins[m]
    out = []
    for k in range(len(faces) // FACE_SIZE):
        b = FACE_SIZE * k
        planenum, side = struct.unpack_from("<HB", faces, b)
        first_edge, num_edges, ti, di = struct.unpack_from("<ihhh", faces, b + 4)
        styles = faces[b + 16:b + 20]
        light_ofs = struct.unpack_from("<i", faces, b + 20)[0]
        mins = struct.unpack_from("<2i", faces, b + 28)
        size = struct.unpack_from("<2i", faces, b + 36)
        if light_ofs < 0 or num_edges < 3 or ti < 0 or 0 not in styles:
            continue
        flags = struct.unpack_from("<i", texinfo, TEXINFO_SIZE * ti + 64)[0]
        if flags & (SURF_SKY | SURF_SKY2D | SURF_NOLIGHT):
            continue
        tvecs = np.array(struct.unpack_from("<8f", texinfo, TEXINFO_SIZE * ti), dtype=np.float64).reshape(2, 4)
        lvecs = np.array(struct.unpack_from("<8f", texinfo, TEXINFO_SIZE * ti + 32), dtype=np.float64).reshape(2, 4)
        td = struct.unpack_from("<i", texinfo, TEXINFO_SIZE * ti + 68)[0]
        refl = struct.unpack_from("<3f", texdata, 32 * td) if 0 <= td < len(texdata) // 32 else (0.5, 0.5, 0.5)
        n = np.array(struct.unpack_from("<3f", planes, 20 * planenum), dtype=np.float64)
        if side:
            n = -n
        se = surfedges[first_edge:first_edge + num_edges]
        corners = verts[np.where(se >= 0, edges[np.abs(se), 0], edges[np.abs(se), 1])].astype(np.float64)
        if di >= 0 and num_edges == 4:
            pos, flat, tris = _displacement(corners, dispinfo, dispverts, di)
        else:
            pos, flat = corners, corners
            tris = np.array([(0, i, i + 1) for i in range(1, num_edges - 1)], dtype=np.int64)
        lux = flat @ lvecs[:, :3].T + lvecs[:, 3] - np.array(mins, dtype=np.float64)
        offset = model_of.get(k)
        if offset is not None:
            pos = pos + np.array(offset)
        bump = bool(flags & SURF_BUMPLIGHT)
        rect = None
        if di < 0:
            # the samples on a face's edges lie exactly on them: bake the whole sample rectangle (half a
            # sample past the edges) so each sample's texel is covered. Lightmap (s, t) -> world: solve
            # lvec_s . p = s + mins_s - off_s, lvec_t . p = t + mins_t - off_t, n . p = plane distance
            m = np.stack([lvecs[0, :3], lvecs[1, :3], n])
            if abs(np.linalg.det(m)) > 1e-12:
                dist = float(n @ corners[0])
                w, h = size[0] + 1, size[1] + 1
                st = [(-0.5, -0.5), (w - 0.5, -0.5), (w - 0.5, h - 0.5), (-0.5, h - 0.5)]
                rect = np.array([np.linalg.solve(m, [a + mins[0] - lvecs[0, 3], b + mins[1] - lvecs[1, 3], dist])
                                 for a, b in st])
                if offset is not None:
                    rect = rect + np.array(offset)
        nstyles = next((i for i in range(4) if styles[i] == 255), 4)
        out.append(BakeFace(k, light_ofs, styles.index(0), nstyles, size[0] + 1, size[1] + 1, bump,
                            pos[tris].reshape(-1, 3), lux[tris].reshape(-1, 2), n,
                            bump_normals(tvecs[0, :3], tvecs[1, :3], n) if bump else np.zeros((3, 3)),
                            tuple(refl), rect))
    return out, lump_no


def pack(faces: list[BakeFace]) -> tuple[int, int]:
    """Place each face's samples in the bake image, PAD texels apart. Returns the image size."""
    sizes = [(f.w + 2 * PAD, f.h + 2 * PAD) for f in faces]
    total = sum(w * h for w, h in sizes) or 1
    width = 256
    while width * width < total * 1.3:
        width *= 2
    width = max(width, max((w for w, _h in sizes), default=1))
    order = sorted(range(len(faces)), key=lambda i: -sizes[i][1])
    x = y = shelf = 0
    for i in order:
        w, h = sizes[i]
        if x + w > width:
            x, y, shelf = 0, y + shelf, 0
        faces[i].atlas = (x + PAD, y + PAD)
        x += w
        shelf = max(shelf, h)
    return width, y + shelf


def _clip(poly: list, axis: int, value: float, keep_above: bool) -> list:
    """Sutherland-Hodgman: the part of a convex polygon on one side of s (axis 0) or t (axis 1) = value."""
    out = []
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        ina = (a[axis] >= value) if keep_above else (a[axis] <= value)
        inb = (b[axis] >= value) if keep_above else (b[axis] <= value)
        if ina:
            out.append(a)
        if ina != inb:
            f = (value - a[axis]) / (b[axis] - a[axis])
            out.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
    return out


def bake_triangles(face: BakeFace) -> tuple[np.ndarray, np.ndarray]:
    """(k, 3) world corners and (k, 2) lightmap coordinates to bake.

    Flat faces: one small quad per sample, mapped onto that sample's whole texel. Like vrad, a sample is
    lit at the middle of the part of its square that lies on the face (an edge sample's square is half
    off the face), so the quad covers that part's bounds. Displacements: the surface itself."""
    if face.rect is None:
        return face.positions, face.lux
    w, h = face.w, face.h
    origin = face.rect[0] - (face.rect[1] - face.rect[0]) / w * -0.5 - (face.rect[3] - face.rect[0]) / h * -0.5
    ds, dt = (face.rect[1] - face.rect[0]) / w, (face.rect[3] - face.rect[0]) / h
    tri = face.lux.reshape(-1, 3, 2)
    poly = [tuple(tri[0, 0])] + [tuple(t[1]) for t in tri] + [tuple(tri[-1, 2])]
    lo, hi = np.min(poly, axis=0), np.max(poly, axis=0)
    corners, coords = [], []
    for j in range(h):
        for i in range(w):
            box = (i - 0.5, j - 0.5, i + 0.5, j + 0.5)
            if lo[0] <= box[0] and box[2] <= hi[0] and lo[1] <= box[1] and box[3] <= hi[1] and _inside(poly, box):
                s0, t0, s1, t1 = box
            else:
                part = poly
                for axis, value, above in ((0, box[0], True), (0, box[2], False), (1, box[1], True), (1, box[3], False)):
                    part = _clip(part, axis, value, above) if part else part
                if len(part) >= 3:
                    (s0, t0), (s1, t1) = np.min(part, axis=0), np.max(part, axis=0)
                else:
                    s0, t0, s1, t1 = box        # no part on the face: vrad has no sample there either
            quad = [(s0, t0), (s1, t0), (s1, t1), (s0, t1)]
            corners += [origin + a * ds + b * dt for a, b in quad]
            coords += [(i - 0.5, j - 0.5), (i + 0.5, j - 0.5), (i + 0.5, j + 0.5), (i - 0.5, j + 0.5)]
    corners, coords = np.array(corners).reshape(-1, 4, 3), np.array(coords).reshape(-1, 4, 2)
    idx = [0, 1, 2, 0, 2, 3]
    return corners[:, idx].reshape(-1, 3), coords[:, idx].reshape(-1, 2)


def _inside(poly: list, box: tuple) -> bool:
    """All four corners of the box inside the convex polygon (either winding)."""
    sign = 0.0
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
            c = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
            if abs(c) < 1e-9:
                continue
            if sign == 0.0:
                sign = c
            elif (c > 0) != (sign > 0):
                return False
    return True


def uvs(face: BakeFace, width: int, height: int, lux: np.ndarray | None = None) -> np.ndarray:
    """Bake image coordinates of the face's corners: sample (i, j) at the centre of texel (x+i, y+j)."""
    x, y = face.atlas
    lux = face.lux if lux is None else lux
    return np.stack([(x + lux[:, 0] + 0.5) / width, (y + lux[:, 1] + 0.5) / height], axis=1)


def encode(values: np.ndarray) -> np.ndarray:
    """(n, 3) linear light in vrad units -> (n, 4) uint8 ColorRGBExp32, as VectorToColorRGBExp32."""
    v = np.maximum(np.asarray(values, dtype=np.float64), 0.0)
    m = v.max(axis=1)
    exp = np.zeros(len(v), dtype=np.int64)
    nz = m > 0
    # the exponent that puts the largest channel in 128..255 (vrad halves / doubles until it does)
    e = np.floor(np.log2(np.where(nz, m, 1.0) / 128.0)).astype(np.int64)
    for _ in range(2):                       # fix float edge cases exactly like the loops
        scaled = np.where(nz, m * np.exp2(-e.astype(np.float64)), 128.0)
        e = np.where(scaled > 255.0, e + 1, np.where(scaled < 128.0, e - 1, e))
    exp[nz] = e[nz]
    exp = np.clip(exp, -128, 127)
    mant = np.minimum(v * np.exp2(-exp.astype(np.float64))[:, None], 255.0).astype(np.uint8)
    out = np.empty((len(v), 4), dtype=np.uint8)
    out[:, :3] = mant
    out[:, 3] = exp.astype(np.int8).view(np.uint8)
    return out


def write(data: bytes, lump_no: int, faces: list[BakeFace], samples: dict) -> bytes:
    """Replace style 0's lightmaps. samples[face.index] = list of (h, w, 3) arrays in vrad units:
    the flat one, then the 3 bump directions for bumped faces."""
    buf = bytearray(data)
    _v, base, length, _cc = struct.unpack_from("<iiii", data, 8 + 16 * lump_no)
    for f in faces:
        maps = samples.get(f.index)
        if maps is None:
            continue
        count = 4 if f.bump else 1
        n = f.w * f.h
        for b in range(count):
            enc = encode(maps[b].reshape(-1, 3))
            ofs = base + f.light_ofs + (f.style_slot * count + b) * n * 4
            if ofs + n * 4 > base + length:
                raise ValueError(f"face {f.index}: lightmap runs past the lighting lump")
            buf[ofs:ofs + n * 4] = enc.tobytes()
        flat = maps[0].reshape(-1, 3)
        median = np.array([np.sort(flat[:, c])[len(flat) // 2] for c in range(3)])
        avg = base + f.light_ofs - 4 * (f.style_slot + 1)        # before lightofs, reverse style order
        buf[avg:avg + 4] = encode(median[None])[0].tobytes()
    return bytes(buf)
