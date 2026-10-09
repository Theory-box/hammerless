"""Sky light from a picture of the sky, for Hammerless's light compiler (hlvrad -skymap).

vrad lights a map from the sky with one colour. With a sky map, each sky ray takes the colour of the sky
in its direction: blue from above, warm from a sunset horizon. The map comes from the skybox the map uses
(its six HDR faces, so the light matches the sky you see) or from an HDRI image.

The map handed to hlvrad is a small equirectangular panorama in the game's axes (x, y, z up): column u
looks along atan2(y, x) = 2 pi u - pi, row v looks up at pi/2 - pi v (row 0 straight up). It's blurred to
the spacing of the sky rays (so a small bright spot like a sun doesn't make blotches) and scaled so that a
floor under open sky gets brightness 1 (cosine-weighted over the upper half); hlvrad multiplies by the
brightness of the map's Sky Light setting, so the overall level stays what it was, with the sky's colours.
"""
from __future__ import annotations

import re
import struct

import numpy as np

MAP_W, MAP_H = 128, 64
BLUR_DEGREES = 9.0          # about the spacing of vrad's 162 sky rays (4 pi / 162 sr each)

# Source's sky faces: the face, and (axis it looks along, s axis, t axis) as direction = forward + s * right
# + t * up for s, t in -1..1; texture u = (s + 1) / 2, v = (1 - t) / 2. Measured on L4D2's skies: the sides
# join seamlessly going right rt -> ft -> lf -> bk, and the top's edges meet each side's top row this way.
FACES = {
    "rt": ((1, 0, 0), (0, -1, 0), (0, 0, 1)),
    "ft": ((0, -1, 0), (-1, 0, 0), (0, 0, 1)),
    "lf": ((-1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "bk": ((0, 1, 0), (1, 0, 0), (0, 0, 1)),
    "up": ((0, 0, 1), (0, -1, 0), (-1, 0, 0)),
    "dn": ((0, 0, -1), (0, -1, 0), (1, 0, 0)),
}


def _vmt_value(text: str, key: str) -> str | None:
    m = re.search(r'"?\$' + key + r'"?\s+"?([^"\n]+?)"?\s*(\n|$)', text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _face(content, sky: str, side: str):
    """One sky face as linear float RGB (h, w, 3) and how much of the face it covers vertically (1, or 0.5 when
    the material shows it at double height: its bottom row then fills the lower half), or None."""
    from .vtf_read import read_vtf
    vmt = content.read(f"materials/skybox/{sky}{side}.vmt")
    if not vmt:
        return None
    text = vmt.decode("latin-1")
    hdr = _vmt_value(text, "hdrcompressedtexture")
    tex = hdr or _vmt_value(text, "hdrbasetexture") or _vmt_value(text, "basetexture")
    if not tex:
        return None
    data = content.read("materials/" + tex.replace("\\", "/").lower() + ".vtf")
    if not data:
        return None
    _w, _h, rgba = read_vtf(data, max_size=256)
    rgba = rgba.astype(np.float32) / 255.0
    if hdr:                                    # compressed HDR: colour times 16 x alpha (Valve's sky shader)
        rgb = rgba[..., :3] * (rgba[..., 3:4] * 16.0)
    else:                                      # an ordinary texture: sRGB
        rgb = np.where(rgba[..., :3] <= 0.04045, rgba[..., :3] / 12.92, ((rgba[..., :3] + 0.055) / 1.055) ** 2.4)
    color = _vmt_value(text, "color")
    if color:
        nums = [float(x) for x in re.findall(r"[-\d.]+", color)[:3]]
        if len(nums) == 3:                     # ("{r g b}" is 0..255, "[r g b]" 0..1)
            rgb = rgb * (np.array(nums, np.float32) / (255.0 if "{" in color else 1.0))
    cover = 1.0
    transform = _vmt_value(text, "basetexturetransform")
    if transform and re.search(r"scale\s+1\s+2", transform):
        cover = 0.5                            # (the sides: the texture is the upper half, clamped below)
    return rgb, cover


def _directions(w: int, h: int) -> np.ndarray:
    """Unit direction of each panorama texel's centre (h, w, 3)."""
    lon = (np.arange(w) + 0.5) / w * 2 * np.pi - np.pi
    lat = np.pi / 2 - (np.arange(h) + 0.5) / h * np.pi
    lon, lat = np.meshgrid(lon, lat)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def from_skybox(content, sky: str, w: int = 256, h: int = 128) -> np.ndarray | None:
    """The skybox's six faces as a panorama (h, w, 3), or None when its faces can't be read."""
    faces = {side: _face(content, sky, side) for side in FACES}
    if not any(faces.values()):
        return None
    dirs = _directions(w, h)
    out = np.zeros((h, w, 3), np.float32)
    for side, (fwd, right, up) in FACES.items():
        fwd, right, up = (np.array(v, np.float32) for v in (fwd, right, up))
        along = dirs @ fwd
        mask = along > 0
        with np.errstate(divide="ignore", invalid="ignore"):
            s = (dirs @ right) / along
            t = (dirs @ up) / along
        mask &= (np.abs(s) <= 1.0001) & (np.abs(t) <= 1.0001)
        face = faces[side]
        if face is None:                       # (no bottom face: the sides' bottom rows' average)
            continue
        img, cover = face
        fh, fw, _ = img.shape
        u = np.clip((s[mask] + 1) / 2 * fw, 0, fw - 1).astype(int)
        v = (1 - t[mask]) / 2                  # 0 top .. 1 bottom of the face
        v = np.clip(v / cover * fh, 0, fh - 1).astype(int)
        out[mask] = img[v, u]
    if faces.get("dn") is None:                # no bottom face: the sides' bottom rows' colour (what the game
        rows = [f[0][-1] for s, f in faces.items() if f is not None and s in ("rt", "ft", "lf", "bk")]
        if rows:                               # shows below the horizon, stretched down)
            below = (dirs[..., 2] < 0) & ~np.any(out != 0, axis=-1)
            out[below] = np.concatenate(rows).mean(axis=0)
    return out


def from_equirect_image(rgb: np.ndarray, rotation_degrees: float = 0.0, w: int = 256, h: int = 128) -> np.ndarray:
    """An HDRI (h, w, 3 linear, row 0 at the top, Blender's world mapping: the image's centre looks along +x, its
    columns turning towards -y to the right) as our panorama, turned by rotation_degrees about the vertical."""
    ih, iw, _ = rgb.shape
    dirs = _directions(w, h)
    rot = np.radians(rotation_degrees)
    x = dirs[..., 0] * np.cos(-rot) - dirs[..., 1] * np.sin(-rot)
    y = dirs[..., 0] * np.sin(-rot) + dirs[..., 1] * np.cos(-rot)
    # Blender (Cycles' and EEVEE's environment texture): u = 0.5 - atan2(dir.y, dir.x) / (2 pi),
    # v = atan2(dir.z, hypot(dir.x, dir.y)) / pi + 0.5 (from the bottom)
    u = 0.5 - np.arctan2(y, x) / (2 * np.pi)
    v = np.arctan2(dirs[..., 2], np.hypot(x, y)) / np.pi + 0.5
    px = np.clip((u * iw).astype(int), 0, iw - 1)
    py = np.clip(((1 - v) * ih).astype(int), 0, ih - 1)
    return rgb[py, px, :3].astype(np.float32)


def blur(pano: np.ndarray, degrees: float = BLUR_DEGREES, w: int = MAP_W, h: int = MAP_H) -> np.ndarray:
    """Average the panorama over a cone round each output direction (weights: solid angle, smooth falloff)."""
    while pano.shape[0] > h and pano.shape[0] % 2 == 0 and pano.shape[1] % 2 == 0:   # (2x2 averages first:
        pano = 0.25 * (pano[0::2, 0::2] + pano[1::2, 0::2] + pano[0::2, 1::2] + pano[1::2, 1::2])   # far
    sh, sw, _ = pano.shape                                                          # under the cone's size)
    src_dirs = _directions(sw, sh).reshape(-1, 3)
    lat = np.pi / 2 - (np.arange(sh) + 0.5) / sh * np.pi
    area = np.repeat(np.cos(lat), sw).astype(np.float64)       # (a texel's solid angle, up to a constant)
    src = pano.reshape(-1, 3).astype(np.float64)
    dst_dirs = _directions(w, h).reshape(-1, 3)
    cos_r = np.cos(np.radians(degrees))
    out = np.zeros((dst_dirs.shape[0], 3))
    for i in range(0, dst_dirs.shape[0], 256):
        c = dst_dirs[i:i + 256] @ src_dirs.T                    # (n, src)
        wgt = np.clip((c - cos_r) / (1 - cos_r), 0, None) * area  # (1 at the centre, 0 at the cone's edge)
        out[i:i + 256] = (wgt @ src) / np.maximum(wgt.sum(axis=1, keepdims=True), 1e-12)
    return out.reshape(h, w, 3).astype(np.float32)


def normalize(pano: np.ndarray) -> np.ndarray:
    """Scaled so a floor under the open sky gets brightness 1: the cosine-weighted average of the upper half."""
    h, w, _ = pano.shape
    dirs = _directions(w, h)
    lat = np.pi / 2 - (np.arange(h) + 0.5) / h * np.pi
    wgt = np.clip(dirs[..., 2], 0, None) * np.cos(lat)[:, None]
    lum = pano @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    avg = float((lum * wgt).sum() / max(wgt.sum(), 1e-12))
    return pano / avg if avg > 0 else pano


def write_skymap(path: str, pano: np.ndarray) -> None:
    h, w, _ = pano.shape
    with open(path, "wb") as f:
        f.write(b"HLSK" + struct.pack("<ii", w, h))
        f.write(np.ascontiguousarray(pano, dtype="<f4").tobytes())


def make_skymap(path: str, pano: np.ndarray, key: str = "") -> None:
    """Blur, scale and write a panorama for hlvrad (not again while `key` is the one it was made from)."""
    import os
    stamp = path + ".key"
    if key and os.path.exists(path) and os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as f:
            if f.read() == key:
                return
    write_skymap(path, normalize(blur(pano)))
    if key:
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(key)
