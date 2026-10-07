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


def radial(face: BakeFace, own: tuple, others: list[tuple]) -> list[np.ndarray]:
    """BuildLuxelRadial + SampleRadial: each luxel is the weighted mean of the samples near it, the face's
    own and its neighbours' (their sample areas taken into this face's luxel space). own / others:
    (BakeFace, Samples, [values (n, 3) per bump direction]). Returns (h, w, 3) maps."""
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
                np.add.at(weight, idx, wgt)
                for m in range(nmaps):
                    np.add.at(acc[m], idx, vals[min(m, len(vals) - 1)][ok] * wgt[:, None])
    empty = weight <= 1e-6
    out = acc / np.where(empty, 1.0, weight)[None, :, None]
    return [o.reshape(h, w, 3) for o in out]


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
