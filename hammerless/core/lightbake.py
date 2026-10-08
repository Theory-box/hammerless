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

import math
import re
import struct
from dataclasses import dataclass, field

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
PAD = 4                     # texels around each face in the bake image, filled from inside (keeps the
                            # denoiser from mixing neighbouring faces)


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
    lvecs: np.ndarray | None = None  # (2, 4) world -> luxel space (with mins: coord = p . v[:3] + v[3] - mins)
    mins: tuple = (0, 0)
    offset: np.ndarray | None = None  # brush entity origin (its faces are stored relative to it)
    verts: tuple = ()        # vertex numbers of the corners (for finding neighbours)
    smoothing: int = 0       # smoothing groups
    poly_lux: np.ndarray | None = None   # (k, 2) flat faces: the polygon in luxel space

    def to_luxel(self, world: np.ndarray) -> np.ndarray:
        """WorldToLuxelSpace."""
        p = world - (self.offset if self.offset is not None else 0.0)
        return p @ self.lvecs[:, :3].T + self.lvecs[:, 3] - np.asarray(self.mins, dtype=np.float64)

    def to_world(self, coord: np.ndarray) -> np.ndarray:
        """LuxelSpaceToWorld (on the face's plane)."""
        w, h = self.w, self.h
        ds, dt = (self.rect[1] - self.rect[0]) / w, (self.rect[3] - self.rect[0]) / h
        origin = self.rect[0] + 0.5 * ds + 0.5 * dt
        return origin + coord[..., :1] * ds + coord[..., 1:2] * dt


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
        planenum = struct.unpack_from("<H", faces, b)[0]     # (the side byte is already in the plane)
        first_edge, num_edges, ti, di = struct.unpack_from("<ihhh", faces, b + 4)
        styles = faces[b + 16:b + 20]
        light_ofs = struct.unpack_from("<i", faces, b + 20)[0]
        smoothing = struct.unpack_from("<I", faces, b + 52)[0]
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
        n = np.array(struct.unpack_from("<3f", planes, 20 * planenum), dtype=np.float64)   # already the face's
        se = surfedges[first_edge:first_edge + num_edges]
        vidx = np.where(se >= 0, edges[np.abs(se), 0], edges[np.abs(se), 1])
        corners = verts[vidx].astype(np.float64)
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
                            tuple(refl), rect, lvecs=lvecs, mins=mins,
                            offset=None if offset is None else np.array(offset, dtype=np.float64),
                            verts=tuple(int(v) for v in vidx), smoothing=smoothing,
                            poly_lux=lux if di < 0 else None))
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


SUB = 8          # sub-points per cell side when measuring the part of a sample cell on the face


@dataclass
class Samples:
    """Where vrad lights a flat face (BuildFacesamplesAndLuxels): the face cut into one-luxel cells
    [s, s+1) x [t, t+1); each cell with part of the face is one sample, lit at that part's balance point.
    Arrays are per sample; st is its cell, which is also its texel in the bake image."""
    st: np.ndarray          # (n, 2) int
    coord: np.ndarray       # (n, 2) the balance point, luxel space
    lo: np.ndarray          # (n, 2) the part's bounds, luxel space
    hi: np.ndarray
    pos: np.ndarray         # (n, 3) the balance point, world


def _edges(poly: np.ndarray):
    """Half-planes of a convex polygon, inside positive."""
    nxt = np.roll(poly, -1, 0)
    n = np.stack([poly[:, 1] - nxt[:, 1], nxt[:, 0] - poly[:, 0]], 1)
    d = (n * poly).sum(1)
    if np.mean(poly @ n.T - d) < 0:
        n, d = -n, -d
    return n, d


def samples(face: BakeFace) -> Samples:
    w, h = face.w, face.h
    n, d = _edges(face.poly_lux)
    j, i = np.mgrid[0:h, 0:w]
    cell = np.stack([i, j], -1).reshape(-1, 2).astype(np.float64)
    corners = cell[:, None, :] + np.array([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=np.float64)
    whole = np.all(corners @ n.T - d >= -1e-6, axis=(1, 2))     # cells entirely on the face
    coord, lo, hi = cell + 0.5, cell.copy(), cell + 1.0
    keep = whole.copy()
    part = np.where(~whole)[0]
    if len(part):
        k = (np.arange(SUB) + 0.5) / SUB
        sub = np.stack(np.meshgrid(k, k), -1).reshape(-1, 2)
        pts = cell[part][:, None, :] + sub[None]                # (cells, SUB^2, 2)
        on = np.all(pts @ n.T - d >= -1e-6, axis=-1)
        has = on.any(1)
        pts, on, part = pts[has], on[has], part[has]
        coord[part] = np.where(on[..., None], pts, 0).sum(1) / on.sum(1, keepdims=True)
        lo[part] = np.where(on[..., None], pts, np.inf).min(1) - 0.5 / SUB
        hi[part] = np.where(on[..., None], pts, -np.inf).max(1) + 0.5 / SUB
        keep[part] = True
    return Samples(cell[keep].astype(np.int64), coord[keep], lo[keep], hi[keep], face.to_world(coord[keep]))


def bake_triangles(face: BakeFace, smp: Samples | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(k, 3) world corners and (k, 2) lightmap coordinates to bake.

    Flat faces: one quad per sample covering its part of the face, mapped onto texel st (Cycles averages
    over it, like vrad's supersampling of the sample's area). Displacements: the surface itself."""
    if face.rect is None:
        return face.positions, face.lux
    smp = smp or samples(face)
    lo, hi = smp.lo, smp.hi
    st = np.stack([lo, np.stack([hi[:, 0], lo[:, 1]], 1), hi, np.stack([lo[:, 0], hi[:, 1]], 1)], 1)
    world = face.to_world(st)
    c = smp.st[:, None, :].astype(np.float64)
    tex = np.concatenate([c + (-0.5, -0.5), c + (0.5, -0.5), c + (0.5, 0.5), c + (-0.5, 0.5)], 1)
    order = [0, 1, 2, 0, 2, 3]
    return world[:, order].reshape(-1, 3), tex[:, order].reshape(-1, 2)


def neighbours(faces: list[BakeFace], cos_limit: float = 0.7071067) -> dict[int, list[BakeFace]]:
    """PairEdges: faces sharing a corner vertex, normals within 45 degrees (or a shared smoothing group;
    a hard-edge group never). Displacements aren't neighbours of flat faces."""
    by_vert: dict[int, list[BakeFace]] = {}
    for f in faces:
        for v in set(f.verts):
            by_vert.setdefault(v, []).append(f)
    out = {}
    for f in faces:
        found = {}
        for v in f.verts:
            for g in by_vert[v]:
                if g is f or g.index in found or (f.rect is not None and g.rect is None):
                    continue
                if f.smoothing == 0 and g.smoothing == 0:
                    if float(f.normal @ g.normal) < cos_limit:
                        continue
                else:
                    group = f.smoothing & g.smoothing
                    if group == 0 or group & 0x80000000:
                        continue
                found[g.index] = g
        out[f.index] = list(found.values())
    return out


def radial(face: BakeFace, own: tuple, others: list[tuple]) -> tuple[list[np.ndarray], np.ndarray]:
    """BuildLuxelRadial + SampleRadial: each luxel is the weighted mean of the samples near it, the face's
    own and its neighbours' (their sample areas taken into this face's luxel space). own / others:
    (BakeFace, Samples, [values (n, 3) per bump direction]). Returns (h, w, 3) maps and which luxels no
    sample reached (vrad leaves those black)."""
    w, h = face.w, face.h
    nmaps = 4 if face.bump else 1
    acc = np.zeros((nmaps, h * w, 3))
    weight = np.zeros(h * w)
    for g, smp, vals in [own] + others:
        if g is face:
            coord, lo, hi = smp.coord, smp.lo, smp.hi
        else:
            coord = face.to_luxel(smp.pos)
            lo = face.to_luxel(g.to_world(smp.lo))
            hi = face.to_luxel(g.to_world(smp.hi))
        # (as vrad: mins/maxs of the moved box are its moved corners, not re-sorted)
        s_min, t_min = np.trunc(lo[:, 0]).astype(int), np.trunc(lo[:, 1]).astype(int)
        s_max = np.trunc(hi[:, 0] + 0.9999).astype(int) + 1
        t_max = np.trunc(hi[:, 1] + 0.9999).astype(int) + 1
        s_min, t_min = np.maximum(s_min, 0), np.maximum(t_min, 0)
        s_max, t_max = np.minimum(s_max, w), np.minimum(t_max, h)
        span = int(max((s_max - s_min).max(initial=0), (t_max - t_min).max(initial=0)))
        if span <= 0:
            continue
        if face.bump and not g.bump:      # a flat neighbour gives its one value to every bump direction
            vals = [vals[0]] + [vals[0] * OO_SQRT_3] * 3
        for a in range(span):
            for b in range(span):
                s = s_min + a
                t = t_min + b
                ok = (s < s_max) & (t < t_max)
                s0 = np.maximum(lo[:, 0] - s, -1.0)
                t0 = np.maximum(lo[:, 1] - t, -1.0)
                s1 = np.minimum(hi[:, 0] - s, 1.0)
                t1 = np.minimum(hi[:, 1] - t, 1.0)
                area = (s1 - s0) * (t1 - t0)
                ok &= area > 0.001
                if not ok.any():
                    continue
                r = np.maximum(np.abs(coord[:, 0] - s), np.abs(coord[:, 1] - t))
                wgt = np.where(r < 0.1, area / 0.1, area / np.maximum(r, 1e-9))[ok]
                idx = (s + t * w)[ok]
                weight += np.bincount(idx, wgt, minlength=h * w)
                for m in range(nmaps):
                    v = vals[min(m, len(vals) - 1)][ok]
                    for c in range(3):
                        acc[m, :, c] += np.bincount(idx, v[:, c] * wgt, minlength=h * w)
    empty = weight <= 1e-6
    out = acc / np.where(empty, 1.0, weight)[None, :, None]
    return [o.reshape(h, w, 3) for o in out], empty.reshape(h, w)


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


# ---------------------------------------------------------------- lights, as vrad reads them

@dataclass
class BakeLight:
    kind: str                         # SUN / POINT / SPOT
    intensity: np.ndarray             # linear, vrad units (the sun's per W/m^2, a point light's at 100 units)
    origin: tuple = (0.0, 0.0, 0.0)
    direction: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, -1.0]))   # where light travels
    cone: float = 45.0                # spot: outer and inner half angles, degrees
    inner_cone: float = 10.0
    note: str = ""                    # how it differs from what vrad does with it (shown to the user)


def light_value(text: str, hdr: bool = True) -> np.ndarray | None:
    """LightForString: "r g b brightness" (optionally 8 numbers: LDR then HDR) -> linear intensity."""
    try:
        v = [float(x) for x in text.split()]
    except ValueError:
        return None
    if len(v) == 8:
        v = v[4:] if hdr else v[:4]
    if len(v) == 1:
        v = v * 3
    if len(v) not in (3, 4) or min(v) < 0:
        return None
    rgb = np.array([(c / 255.0) ** 2.2 * 255 for c in v[:3]])
    return rgb * (v[3] / 255.0) if len(v) == 4 else rgb


def _key_light(e: dict, key: str, hdr: bool) -> np.ndarray | None:
    """_lightHDR / _ambientHDR win in HDR when valid ("-1 -1 -1 1" means unset)."""
    if hdr and e.get(key + "HDR"):
        v = light_value(e[key + "HDR"], hdr)
        if v is not None:
            return v
    return light_value(e.get(key, ""), hdr)


def _floats(e: dict, key: str) -> list:
    try:
        return [float(x) for x in e.get(key, "").split()]
    except ValueError:
        return []


def _num(e: dict, key: str, default: float = 0.0) -> float:
    v = _floats(e, key)
    return v[0] if v else default


def light_normal(e: dict) -> np.ndarray:
    """SetupLightNormalFromProps: yaw from "angle" or angles, pitch from "pitch" or -angles pitch."""
    angles = (_floats(e, "angles") + [0.0, 0.0, 0.0])[:3]
    yaw = _num(e, "angle") or angles[1]
    pitch = _num(e, "pitch") or -angles[0]
    if yaw == -1:
        return np.array([0.0, 0.0, 1.0])
    if yaw == -2:
        return np.array([0.0, 0.0, -1.0])
    p, y = math.radians(pitch), math.radians(yaw)
    return np.array([math.cos(y) * math.cos(p), math.sin(y) * math.cos(p), math.sin(p)])


def scene_lights(entity_text: str, hdr: bool = True) -> tuple[BakeLight | None, np.ndarray, list[BakeLight], list[str]]:
    """The static lights vrad bakes into style 0: (sun, sky ambient intensity, point/spot lights, notes).
    Named lights (switchable, their own light style) and lights with a style are left to vrad."""
    ents = [dict(re.findall(r'"([^"]*)"\s+"([^"]*)"', b)) for b in re.findall(r"\{([^{}]*)\}", entity_text)]
    sun, ambient, lights, notes = None, np.zeros(3), [], []
    for e in ents:
        cls = e.get("classname", "")
        if cls not in ("light", "light_spot", "light_environment"):
            continue
        scale = _num(e, "_lightscaleHDR", 1.0) if hdr else 1.0
        if cls == "light_environment":
            if sun is not None:
                continue                                  # vrad uses the first one
            i = _key_light(e, "_light", hdr)
            if i is None:
                continue
            sun = BakeLight("SUN", i * scale, direction=light_normal(e))
            a = _key_light(e, "_ambient", hdr)
            ambient = i * 0.5 if a is None else a
            if hdr:
                ambient = ambient * _num(e, "_AmbientScaleHDR", 1.0)
            continue
        if e.get("targetname") or _num(e, "style"):
            continue                                      # its own light style: vrad's lightmaps keep it
        i = _key_light(e, "_light", hdr)
        if i is None or not i.any():
            continue
        name = cls
        origin = tuple((_floats(e, "origin") + [0.0, 0.0, 0.0])[:3])
        c, l, q = (max(_num(e, k), 0.0) for k in ("_constant_attn", "_linear_attn", "_quadratic_attn"))
        note = ""
        if _num(e, "_fifty_percent_distance"):
            q, note = 1.0, "uses 50%/0% falloff distances; Cycles uses inverse square"
        elif c < 1e-3 and l < 1e-3 and q < 1e-3:
            c = 1.0
        if c > 1e-3 or l > 1e-3:
            note = "constant/linear falloff; Cycles uses inverse square"
        if not note:                    # vrad scales the brightness to be its value at 100 units; Cycles
            i = i * (c + 100 * l + 10000 * q) / 10000           # bakes inverse square with that value there
        light = BakeLight("POINT", i, origin)
        if cls == "light_spot":
            inner = _num(e, "_inner_cone") or 10.0
            outer = max(_num(e, "_cone") or inner, inner)
            if not (inner == 180 and outer == 180):
                light = BakeLight("SPOT", i, origin, light_normal(e), min(outer, 90.0), min(inner, 90.0))
        if note:
            light.note = note
            notes.append(f"{name} at ({origin[0]:.0f} {origin[1]:.0f} {origin[2]:.0f}) {note}")
        lights.append(light)
    return sun, ambient, lights, notes


# ---------------------------------------------------------------- seam stitching
#
# Every face has its own lightmap, so where two faces meet the game shows two separately filtered
# lightmaps and they can disagree: a seam. Stitching changes the luxels next to shared edges as little as
# possible (least squares) so both faces show the same light all along the edge.

def _bilinear(face: BakeFace, coord: np.ndarray):
    """The game's lightmap filtering: luxel (i, j) sits at luxel coordinate (i, j); a point is the bilinear
    mix of the 4 luxels around it. Returns (n, 4) luxel numbers and (n, 4) weights."""
    s = np.clip(coord[:, 0], 0, face.w - 1)
    t = np.clip(coord[:, 1], 0, face.h - 1)
    s0 = np.minimum(np.floor(s).astype(int), max(face.w - 2, 0))
    t0 = np.minimum(np.floor(t).astype(int), max(face.h - 2, 0))
    fs, ft = s - s0, t - t0
    s1, t1 = np.minimum(s0 + 1, face.w - 1), np.minimum(t0 + 1, face.h - 1)
    idx = np.stack([s0 + t0 * face.w, s1 + t0 * face.w, s0 + t1 * face.w, s1 + t1 * face.w], 1)
    wgt = np.stack([(1 - fs) * (1 - ft), fs * (1 - ft), (1 - fs) * ft, fs * ft], 1)
    return idx, wgt


def shared_edges(faces: list[BakeFace], cos_limit: float = 0.7071067, per_luxel: int = 2) -> list:
    """Points along every stretch of edge two flat faces share (also where one face's corner lands
    mid-edge on the other), for faces within 45 degrees of each other, per_luxel per luxel along it.
    Edges are grouped by the line they lie on, then overlapping ones of different faces paired.
    Returns [(face a, face b, (n, 3) points)]."""
    flat = [f for f in faces if f.rect is not None and f.poly_lux is not None]
    lines: dict = {}
    for f in flat:
        p = f.to_world(f.poly_lux)
        q = np.roll(p, -1, 0)
        d = q - p
        length = np.linalg.norm(d, axis=1)
        for a, b, ln, dd in zip(p, q, length, d):
            if ln < 0.1:
                continue
            u = dd / ln
            k = int(np.argmax(np.abs(u)))
            if u[k] < 0:                                   # one direction per line
                u = -u
            foot = a - (a @ u) * u                          # the line's point nearest the origin
            key = (tuple(np.round(u * 1000).astype(int)), tuple(np.round(foot / 0.05).astype(int)))
            lines.setdefault(key, []).append((f, a @ u, b @ u, a, u))
    pairs: dict = {}
    for group in lines.values():
        if len(group) < 2:
            continue
        for i, (fa, a0, a1, pa, u) in enumerate(group):
            for fb, b0, b1, _pb, _u in group[i + 1:]:
                if fa is fb or float(fa.normal @ fb.normal) < cos_limit:
                    continue
                lo = max(min(a0, a1), min(b0, b1))
                hi = min(max(a0, a1), max(b0, b1))
                if hi - lo < 0.25:
                    continue
                luxel = 1.0 / max(np.linalg.norm(fa.lvecs[0, :3]), np.linalg.norm(fa.lvecs[1, :3]),
                                  np.linalg.norm(fb.lvecs[0, :3]), np.linalg.norm(fb.lvecs[1, :3]))
                n = max(2, int(np.ceil((hi - lo) / luxel * per_luxel)) + 1)
                k = lo + (hi - lo) * (np.arange(n) + 0.5) / n
                pts = pa + (k - pa @ u)[:, None] * u
                key = (fa.index, fb.index) if fa.index < fb.index else (fb.index, fa.index)
                pairs.setdefault(key, (fa, fb, []))[2].append(pts)
    return [(a, b, np.concatenate(p)) for a, b, p in pairs.values()]


def _edge_values(face: BakeFace, lm: np.ndarray, pts: np.ndarray) -> np.ndarray:
    idx, wgt = _bilinear(face, face.to_luxel(pts))
    return (lm.reshape(-1, 3)[idx] * wgt[..., None]).sum(1)


def seam_error(edges: list, maps: dict, m: int = 0) -> float:
    """Mean difference between the two faces' lighting along their shared edges, relative to its level."""
    diff = level = 0.0
    for a, b, pts in edges:
        if len(maps.get(a.index, ())) <= m or len(maps.get(b.index, ())) <= m:
            continue
        va = _edge_values(a, maps[a.index][m], pts)
        vb = _edge_values(b, maps[b.index][m], pts)
        diff += float(np.abs(va - vb).sum())
        level += float((np.abs(va) + np.abs(vb)).sum()) / 2
    return diff / max(level, 1e-9)


def fill_empty(lm: np.ndarray, empty: np.ndarray) -> np.ndarray:
    """Luxels no sample reached (vrad leaves them black): grown in from their filled neighbours."""
    lm, empty = lm.copy(), empty.copy()
    h, w = empty.shape
    for _ in range(h + w):
        if not empty.any() or empty.all():
            break
        acc = np.zeros_like(lm)
        cnt = np.zeros((h, w))
        have = ~empty
        for sl_dst, sl_src in (((slice(None), slice(1, None)), (slice(None), slice(None, -1))),
                               ((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                               ((slice(1, None), slice(None)), (slice(None, -1), slice(None))),
                               ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
            ok = have[sl_src]
            acc[sl_dst] += np.where(ok[..., None], lm[sl_src], 0)
            cnt[sl_dst] += ok
        grow = empty & (cnt > 0)
        lm[grow] = acc[grow] / cnt[grow][:, None]
        empty &= ~grow
    return lm


def stitch(edges: list, maps: dict, keep: dict | None = None, strength: float = 100.0,
           iterations: int = 80, tol: float = 1e-3) -> dict:
    """Least squares: the luxels' change from the bake (weighted by keep[face], (h, w): how firmly each
    luxel holds its baked value; luxels off the face move freely) plus strength x the two faces' difference
    at every edge point. Each lightmap (flat, then bump directions) is stitched on its own."""
    out = {k: [m.copy() for m in v] for k, v in maps.items()}
    nmaps = max((len(v) for v in maps.values()), default=0)
    for m in range(nmaps):
        terms = []                                          # (row numbers, face, luxels, weights)
        r = 0
        for a, b, pts in edges:
            if len(maps.get(a.index, ())) <= m or len(maps.get(b.index, ())) <= m:
                continue
            ia, wa = _bilinear(a, a.to_luxel(pts))
            ib, wb = _bilinear(b, b.to_luxel(pts))
            rows = np.repeat(np.arange(r, r + len(pts)), 4)
            terms.append((rows, a, ia.ravel(), wa.ravel()))
            terms.append((rows, b, ib.ravel(), -wb.ravel()))
            r += len(pts)
        if not r:
            continue
        offset, start = {}, 0
        for _rows, face, _idx, _w in terms:
            if face.index not in offset:
                offset[face.index] = (start, face)
                start += face.w * face.h
        R = np.concatenate([t[0] for t in terms])
        V = np.concatenate([t[3] for t in terms])
        all_x0 = np.zeros((start, 3))
        all_mu = np.ones(start)
        for key, (o, face) in offset.items():
            all_x0[o:o + face.w * face.h] = maps[key][m].reshape(-1, 3)
            if keep is not None and key in keep:
                all_mu[o:o + face.w * face.h] = keep[key].ravel()
        # only the luxels some edge point uses are unknowns
        used, C = np.unique(np.concatenate([offset[t[1].index][0] + t[2] for t in terms]), return_inverse=True)
        x0, mu = all_x0[used], all_mu[used]
        n_var = len(used)

        def apply(x):                                       # (strength C^T C + diag(mu)) x
            y = np.empty_like(x)
            for c in range(3):
                cx = np.bincount(R, V * x[C, c], minlength=r)
                y[:, c] = np.bincount(C, V * cx[R], minlength=n_var)
            return strength * y + mu[:, None] * x

        rhs = mu[:, None] * x0
        inv = 1.0 / (strength * np.bincount(C, V * V, minlength=n_var) + mu)[:, None]   # Jacobi
        x = x0.copy()                                       # preconditioned CG, starting from the bake
        res = rhs - apply(x)
        z = res * inv
        p = z.copy()
        rz = (res * z).sum(0)
        stop = (tol * tol) * np.maximum((rhs * rhs).sum(0), 1e-30)
        for _ in range(iterations):
            if np.all((res * res).sum(0) <= stop):
                break
            ap = apply(p)
            alpha = rz / np.maximum((p * ap).sum(0), 1e-30)
            x += alpha * p
            res -= alpha * ap
            z = res * inv
            new = (res * z).sum(0)
            p = z + (new / np.maximum(rz, 1e-30)) * p
            rz = new
        all_x0[used] = np.maximum(x, 0.0)
        for key, (o, face) in offset.items():
            out[key][m] = all_x0[o:o + face.w * face.h].reshape(face.h, face.w, 3)
    return out


# ---------------------------------------------------------------- surfaces (bake charts)
#
# The bake isn't laid out like the game's lightmaps: connected faces in one plane (a floor vbsp cut into
# 20 faces) are baked as one continuous picture, finer than the lightmaps, with wide gaps between
# pictures. That suits the denoiser (it sees surfaces, not thousands of unrelated tiles) and coplanar
# faces can't disagree. Each lightmap sample then reads its value from the picture, averaged over the
# part of the face it stands for.

CHART_DENSITY = 2       # bake texels per luxel, each way
CHART_PAD = 8           # texels between pictures (filled by extending their edges)


@dataclass
class Chart:
    faces: list
    origin: np.ndarray          # world point at texel coordinate (0, 0)
    u: np.ndarray               # world units per texel along x / y (vectors in the plane)
    v: np.ndarray
    size: tuple = (1, 1)
    atlas: tuple = (0, 0)

    def to_px(self, world: np.ndarray) -> np.ndarray:
        """Texel coordinates (texel i spans [i, i+1))."""
        d = world - self.origin
        return np.stack([d @ self.u / (self.u @ self.u), d @ self.v / (self.v @ self.v)], -1)

    def uv(self, world: np.ndarray, width: int, height: int) -> np.ndarray:
        p = self.to_px(world)
        return np.stack([(self.atlas[0] + p[:, 0]) / width, (self.atlas[1] + p[:, 1]) / height], 1)


def _coplanar(a: BakeFace, b: BakeFace) -> bool:
    return float(a.normal @ b.normal) > 0.99999 and abs(float(a.normal @ (b.positions[0] - a.positions[0]))) < 0.01


def make_charts(faces: list[BakeFace], edges: list) -> list[Chart]:
    """Connected coplanar flat faces (sharing a corner or a stretch of edge) -> one chart each."""
    flat = [f for f in faces if f.rect is not None]
    parent = {f.index: f.index for f in flat}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    links = [(a, b) for a, b, _pts in edges]
    near = neighbours(flat, cos_limit=0.99999)
    by = {f.index: f for f in flat}
    links += [(f, g) for f in flat for g in near[f.index]]
    for a, b in links:
        if a.index in parent and b.index in parent and _coplanar(a, b):
            parent[root(a.index)] = root(b.index)
    groups: dict = {}
    for f in flat:
        groups.setdefault(root(f.index), []).append(f)
    charts = []
    for members in groups.values():
        first = members[0]
        n = first.normal
        s_dir = first.lvecs[0, :3] - (first.lvecs[0, :3] @ n) * n
        s_dir /= np.linalg.norm(s_dir)
        t_dir = np.cross(n, s_dir)
        if t_dir @ first.lvecs[1, :3] < 0:
            t_dir = -t_dir
        luxel = min(1.0 / max(np.linalg.norm(f.lvecs[0, :3]), np.linalg.norm(f.lvecs[1, :3])) for f in members)
        texel = luxel / CHART_DENSITY
        pts = np.concatenate([f.positions for f in members])
        a, b = pts @ s_dir, pts @ t_dir
        origin = pts[0] + (a.min() - a[0]) * s_dir + (b.min() - b[0]) * t_dir
        size = (int(np.ceil((a.max() - a.min()) / texel)) + 1, int(np.ceil((b.max() - b.min()) / texel)) + 1)
        charts.append(Chart(members, origin, s_dir * texel, t_dir * texel, size))
    return charts


def pack_blocks(sizes: list[tuple[int, int]], pad: int) -> tuple[list[tuple[int, int]], int, int]:
    """Shelf-pack rectangles pad texels apart. Returns their corners and the image size."""
    padded = [(w + 2 * pad, h + 2 * pad) for w, h in sizes]
    total = sum(w * h for w, h in padded) or 1
    width = 256
    while width * width < total * 1.3:
        width *= 2
    width = max(width, max((w for w, _h in padded), default=1))
    order = sorted(range(len(sizes)), key=lambda i: -padded[i][1])
    corners = [(0, 0)] * len(sizes)
    x = y = shelf = 0
    for i in order:
        w, h = padded[i]
        if x + w > width:
            x, y, shelf = 0, y + shelf, 0
        corners[i] = (x + pad, y + pad)
        x += w
        shelf = max(shelf, h)
    return corners, width, y + shelf


def chart_values(chart: Chart, face: BakeFace, smp: "Samples", image: np.ndarray, k: int = 3) -> np.ndarray:
    """Each sample's value: the picture averaged over k x k points across the part of the face the
    sample stands for (bilinear between texel centres)."""
    g = (np.arange(k) + 0.5) / k
    gs, gt = np.meshgrid(g, g)
    off = np.stack([gs.ravel(), gt.ravel()], 1)                       # (k^2, 2)
    coord = smp.lo[:, None, :] + off[None] * (smp.hi - smp.lo)[:, None, :]
    px = chart.to_px(face.to_world(coord).reshape(-1, 3))
    x = chart.atlas[0] + px[:, 0] - 0.5
    y = chart.atlas[1] + px[:, 1] - 0.5
    h, w = image.shape[:2]
    x0 = np.clip(np.floor(x).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(y).astype(int), 0, h - 2)
    fx = np.clip(x - x0, 0, 1)[:, None]
    fy = np.clip(y - y0, 0, 1)[:, None]
    val = (image[y0, x0] * (1 - fx) * (1 - fy) + image[y0, x0 + 1] * fx * (1 - fy)
           + image[y0 + 1, x0] * (1 - fx) * fy + image[y0 + 1, x0 + 1] * fx * fy)
    return val.reshape(len(smp.st), k * k, 3).mean(1)


# ---------------------------------------------------------------- faces nobody sees
#
# Baking a face nobody can see is wasted time. From where players can be (eye points over the nav mesh),
# the map's visibility data (PVS) says which parts of the map they can see; a face there counts as seen
# if one of those eye points is in front of it. Unseen faces keep vrad's lighting (already in the map),
# and still block and bounce light in the bake.

def eye_points(areas, heights=(64.0, 128.0), inset: float = 4.0) -> np.ndarray:
    """Points over nav areas (navfile.NavArea): the corners (pulled in a little) and the middle, at
    standing eye height and jump height."""
    pts = []
    for a in areas:
        (x0, y0, z_nw), (x1, y1, z_se) = a.nw, a.se
        xs = (min(x0 + inset, (x0 + x1) / 2), max(x1 - inset, (x0 + x1) / 2))
        ys = (min(y0 + inset, (y0 + y1) / 2), max(y1 - inset, (y0 + y1) / 2))
        corners = [(xs[0], ys[0], z_nw), (xs[1], ys[0], a.ne_z), (xs[1], ys[1], z_se), (xs[0], ys[1], a.sw_z),
                   ((x0 + x1) / 2, (y0 + y1) / 2, (z_nw + z_se + a.ne_z + a.sw_z) / 4)]
        for x, y, z in corners:
            for h in heights:
                pts.append((x, y, z + h))
    return np.array(pts, dtype=np.float64).reshape(-1, 3)


def _point_leaves(data: bytes, pts: np.ndarray) -> np.ndarray:
    """CM_PointLeafnum for many points (d < 0 goes to the back child)."""
    lump = _lumps(data)
    planes = np.frombuffer(lump(1), dtype=np.dtype([("n", "<3f4"), ("d", "<f4"), ("t", "<i4")]))
    nodes = np.frombuffer(lump(5), dtype="<i4").reshape(-1, 8)
    head = struct.unpack_from("<i", lump(14), 36)[0]
    num = np.full(len(pts), head, dtype=np.int64)
    live = num >= 0
    while live.any():
        k = num[live]
        pl = planes[nodes[k, 0]]
        d = (pts[live] * pl["n"].astype(np.float64)).sum(1) - pl["d"]
        num[live] = np.where(d < 0, nodes[k, 2], nodes[k, 1])
        live = num >= 0
    return -1 - num


def seen_faces(data: bytes, faces: list[BakeFace], eyes: np.ndarray, margin: float = 1.0) -> set[int] | None:
    """The faces some eye point can see (by the PVS, and in front of the face). None: no visibility data
    or no eye points to judge from (bake everything). Faces outside the world's leaves (brush entities,
    displacements) always count as seen."""
    lump = _lumps(data)
    vis = lump(4)
    if len(vis) < 4 or not len(eyes):
        return None
    nclusters = struct.unpack_from("<i", vis, 0)[0]
    rowbytes = (nclusters + 7) >> 3
    leafs = lump(10)
    nleafs = len(leafs) // 32
    leaf_cluster = np.frombuffer(leafs, dtype="<i2").reshape(nleafs, 16)[:, 2].astype(np.int64)
    first = np.frombuffer(leafs, dtype="<u2").reshape(nleafs, 16)[:, 10:12].astype(np.int64)
    leaffaces = np.frombuffer(lump(16), dtype="<u2")

    eye_cluster = leaf_cluster[_point_leaves(data, eyes)]
    inside = eye_cluster >= 0
    eyes, eye_cluster = eyes[inside], eye_cluster[inside]
    if not len(eyes):
        return None
    ec, which = np.unique(eye_cluster, return_inverse=True)
    sees = np.zeros((len(ec), nclusters), dtype=bool)          # eye cluster -> clusters it can see
    for i, c in enumerate(ec):
        ofs = struct.unpack_from("<i", vis, 4 + 8 * int(c))[0]
        row, j, p = bytearray(rowbytes), 0, ofs
        while j < rowbytes:
            if vis[p]:
                row[j] = vis[p]
                j += 1
                p += 1
            else:
                j += vis[p + 1]
                p += 2
        sees[i] = np.unpackbits(np.frombuffer(bytes(row), np.uint8), bitorder="little")[:nclusters].astype(bool)
    lo = np.full((len(ec), 3), np.inf)          # each eye cluster's eye points, as a box (its corners:
    hi = np.full((len(ec), 3), -np.inf)         # in front of a face if any eye could be)
    np.minimum.at(lo, which, eyes)
    np.maximum.at(hi, which, eyes)
    box = np.stack([np.stack([np.where(m & 1, hi[:, 0], lo[:, 0]), np.where(m & 2, hi[:, 1], lo[:, 1]),
                              np.where(m & 4, hi[:, 2], lo[:, 2])], 1) for m in range(8)], 1)   # (ec, 8, 3)

    face_clusters: dict[int, set] = {}
    for leaf in range(nleafs):
        c = leaf_cluster[leaf]
        if c < 0:
            continue
        for f in leaffaces[first[leaf, 0]:first[leaf, 0] + first[leaf, 1]]:
            face_clusters.setdefault(int(f), set()).add(int(c))
    out = set()
    for f in faces:
        cl = face_clusters.get(f.index)
        if not cl:
            out.add(f.index)                                    # not a world leaf face: keep
            continue
        who = sees[:, sorted(cl)].any(1)
        if who.any() and ((box[who] @ f.normal - float(f.normal @ f.positions[0])) > margin).any():
            out.add(f.index)
    return out
