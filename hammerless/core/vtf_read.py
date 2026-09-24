"""Read VTF textures (for previews in Blender). numpy only.

Supports VTF 7.0-7.5 and the formats L4D2 actually uses: DXT1/3/5 and the common
uncompressed ones. Decodes a single mip level (the largest one no bigger than
`max_size`), frame 0, face 0.
"""
from __future__ import annotations

import struct

import numpy as np

# format id -> (bytes per pixel, or None for block-compressed, block bytes)
FORMATS = {
    0: ("RGBA8888", 4), 1: ("ABGR8888", 4), 2: ("RGB888", 3), 3: ("BGR888", 3), 4: ("RGB565", 2),
    5: ("I8", 1), 6: ("IA88", 2), 8: ("A8", 1), 12: ("BGRA8888", 4), 13: ("DXT1", 8), 14: ("DXT3", 16),
    15: ("DXT5", 16), 16: ("BGRX8888", 4), 17: ("BGR565", 2), 20: ("DXT1_ONEBITALPHA", 8),
    22: ("UV88", 2), 24: ("RGBA16161616F", 8), 25: ("RGBA16161616", 8),
}
BLOCK_FORMATS = {13, 14, 15, 20}


def _mip_size(fmt: int, w: int, h: int) -> int:
    bpp = FORMATS[fmt][1]
    if fmt in BLOCK_FORMATS:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bpp
    return w * h * bpp


def _rgb565(c: np.ndarray) -> np.ndarray:
    r = ((c >> 11) & 31).astype(np.float32) * (255 / 31)
    g = ((c >> 5) & 63).astype(np.float32) * (255 / 63)
    b = (c & 31).astype(np.float32) * (255 / 31)
    return np.stack([r, g, b], axis=-1)


def _dxt_colors(blocks: np.ndarray, dxt1: bool) -> tuple[np.ndarray, np.ndarray]:
    """blocks: (N, 8) uint8 colour blocks -> (N,16,3) colours, (N,16) alpha mask (DXT1 1-bit)."""
    c0 = blocks[:, 0].astype(np.uint16) | (blocks[:, 1].astype(np.uint16) << 8)
    c1 = blocks[:, 2].astype(np.uint16) | (blocks[:, 3].astype(np.uint16) << 8)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    four = (c0 > c1) | (not dxt1)
    four = four[:, None]
    p2 = np.where(four, (2 * p0 + p1) / 3, (p0 + p1) / 2)
    p3 = np.where(four, (p0 + 2 * p1) / 3, 0)
    palette = np.stack([p0, p1, p2, p3], axis=1)                      # (N,4,3)
    bits = blocks[:, 4:8].astype(np.uint32)
    idx_word = bits[:, 0] | (bits[:, 1] << 8) | (bits[:, 2] << 16) | (bits[:, 3] << 24)
    idx = (idx_word[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3  # (N,16)
    colors = np.take_along_axis(palette, idx[..., None].astype(np.int64), axis=1)
    alpha = np.where((~four) & (idx == 3), 0, 255) if dxt1 else np.full(idx.shape, 255)
    return colors, alpha


def _dxt5_alpha(ablocks: np.ndarray) -> np.ndarray:
    """ablocks: (N, 8) uint8 alpha blocks -> (N, 16) alpha values."""
    a0 = ablocks[:, 0].astype(np.float32)[:, None]
    a1 = ablocks[:, 1].astype(np.float32)[:, None]
    k = np.arange(1, 7, dtype=np.float32)[None, :]
    eight = np.concatenate([a0, a1, ((7 - k) * a0 + k * a1) / 7], axis=1)              # a0 > a1
    k4 = np.arange(1, 5, dtype=np.float32)[None, :]
    six = np.concatenate([a0, a1, ((5 - k4) * a0 + k4 * a1) / 5,
                          np.zeros_like(a0), np.full_like(a0, 255)], axis=1)          # a0 <= a1
    table = np.where(a0 > a1, eight, six)
    bits = np.zeros(len(ablocks), dtype=np.uint64)
    for n in range(6):
        bits |= ablocks[:, 2 + n].astype(np.uint64) << np.uint64(8 * n)
    idx = (bits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)
    return np.take_along_axis(table, idx.astype(np.int64), axis=1)


def _decode_dxt(data: bytes, fmt: int, w: int, h: int) -> np.ndarray:
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    raw = np.frombuffer(data, dtype=np.uint8, count=bw * bh * FORMATS[fmt][1])
    if fmt in (13, 20):
        colors, alpha = _dxt_colors(raw.reshape(-1, 8), dxt1=True)
    else:
        blk = raw.reshape(-1, 16)
        colors, _ = _dxt_colors(blk[:, 8:], dxt1=False)
        if fmt == 15:
            alpha = _dxt5_alpha(blk[:, :8])
        else:  # DXT3: explicit 4-bit alpha
            nib = np.stack([blk[:, :8] & 15, blk[:, :8] >> 4], axis=-1).reshape(-1, 16)
            alpha = nib.astype(np.float32) * 17
    rgba = np.concatenate([colors, alpha[..., None]], axis=-1)            # (N,16,4)
    img = rgba.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.clip(img[:h, :w] + 0.5, 0, 255).astype(np.uint8)


def _decode_plain(data: bytes, fmt: int, w: int, h: int) -> np.ndarray:
    name, bpp = FORMATS[fmt]
    a = np.frombuffer(data, dtype=np.uint8, count=w * h * bpp).reshape(h, w, bpp)
    full = lambda *ch: np.stack(ch, axis=-1)  # noqa: E731
    one = np.full((h, w), 255, np.uint8)
    if name == "RGBA8888":
        return a.copy()
    if name == "ABGR8888":
        return full(a[..., 3], a[..., 2], a[..., 1], a[..., 0])
    if name == "RGB888":
        return full(a[..., 0], a[..., 1], a[..., 2], one)
    if name in ("BGR888",):
        return full(a[..., 2], a[..., 1], a[..., 0], one)
    if name == "BGRA8888":
        return full(a[..., 2], a[..., 1], a[..., 0], a[..., 3])
    if name == "BGRX8888":
        return full(a[..., 2], a[..., 1], a[..., 0], one)
    if name == "I8":
        return full(a[..., 0], a[..., 0], a[..., 0], one)
    if name == "IA88":
        return full(a[..., 0], a[..., 0], a[..., 0], a[..., 1])
    if name == "A8":
        return full(one, one, one, a[..., 0])
    if name == "UV88":
        return full(a[..., 0], a[..., 1], np.full((h, w), 255, np.uint8), one)
    if name in ("RGB565", "BGR565"):
        c = np.frombuffer(data, dtype="<u2", count=w * h).reshape(h, w)
        rgb = _rgb565(c).astype(np.uint8)
        if name == "BGR565":
            rgb = rgb[..., ::-1]
        return np.concatenate([rgb, one[..., None]], axis=-1)
    raise ValueError(f"unsupported VTF format {name}")


def read_vtf(data: bytes, max_size: int = 512) -> tuple[int, int, np.ndarray]:
    """Returns (full width, full height, rgba uint8 image of the chosen mip, top row first)."""
    sig, major, minor, header_size = struct.unpack_from("<4s3I", data, 0)
    if sig != b"VTF\0":
        raise ValueError("not a VTF file")
    w, h, flags, frames, first_frame = struct.unpack_from("<HHIHH", data, 16)
    fmt, mips, lr_fmt, lr_w, lr_h = struct.unpack_from("<iBiBB", data, 52)
    depth = struct.unpack_from("<H", data, 63)[0] if minor >= 2 else 1
    faces = 6 if flags & 0x4000 else 1  # environment map
    if fmt not in FORMATS:
        raise ValueError(f"unsupported VTF format id {fmt}")

    image_offset = None
    if minor >= 3:
        num_res = struct.unpack_from("<I", data, 68)[0]
        for i in range(num_res):
            tag, _flags, off = struct.unpack_from("<3sBI", data, 80 + i * 8)
            if tag == b"\x30\x00\x00":
                image_offset = off
    if image_offset is None:
        lr_size = _mip_size(13, lr_w, lr_h) if lr_fmt == 13 and lr_w else 0
        image_offset = header_size + lr_size

    # mips are stored smallest first; each mip holds frames x faces x slices.
    # Pick the largest mip that fits in max_size (or the smallest one if none do).
    offset = image_offset
    chosen = None
    for level in range(mips - 1, -1, -1):
        mw, mh = max(1, w >> level), max(1, h >> level)
        if chosen is None or max(mw, mh) <= max_size:
            chosen = (offset, mw, mh)
        offset += _mip_size(fmt, mw, mh) * frames * faces * max(1, depth)
    off, mw, mh = chosen
    blob = data[off:off + _mip_size(fmt, mw, mh)]
    img = _decode_dxt(blob, fmt, mw, mh) if fmt in BLOCK_FORMATS else _decode_plain(blob, fmt, mw, mh)
    return w, h, img
