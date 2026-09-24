"""Write uncompressed VTF 7.2 textures + VMT materials.

Uncompressed (BGR888 / BGRA8888) keeps this dependency-free. Files are ~4-6x
larger than DXT; DXT compression will come with srctools later.
"""
from __future__ import annotations

import os
import struct

import numpy as np

FMT_BGR888 = 3
FMT_BGRA8888 = 12
FLAG_EIGHTBITALPHA = 0x2000
FLAG_NORMAL = 0x0080


def is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def build_mips(rgba: np.ndarray) -> list[np.ndarray]:
    """rgba: HxWx4 uint8 (top row first). Returns mips largest -> smallest (1x1)."""
    mips = [rgba]
    cur = rgba.astype(np.float32)
    while cur.shape[0] > 1 or cur.shape[1] > 1:
        h, w = cur.shape[:2]
        if h > 1:
            cur = (cur[0::2] + cur[1::2]) / 2
        if w > 1:
            cur = (cur[:, 0::2] + cur[:, 1::2]) / 2
        mips.append(np.clip(cur + 0.5, 0, 255).astype(np.uint8))
    return mips


def write_vtf(path: str, rgba: np.ndarray, normal_map: bool = False) -> None:
    """rgba: HxWx4 uint8, top row first. Width and height must be powers of two."""
    h, w = rgba.shape[:2]
    if not (is_pow2(w) and is_pow2(h)):
        raise ValueError(f"texture size {w}x{h} must be powers of two")
    has_alpha = bool((rgba[..., 3] < 255).any())
    fmt = FMT_BGRA8888 if has_alpha else FMT_BGR888
    flags = (FLAG_EIGHTBITALPHA if has_alpha else 0) | (FLAG_NORMAL if normal_map else 0)
    mips = build_mips(rgba)
    lin = (rgba[..., :3].astype(np.float32) / 255.0) ** 2.2
    reflectivity = tuple(float(c) for c in lin.reshape(-1, 3).mean(axis=0))

    header = struct.pack(
        "<4s2IIHHIHH4x3f4xfIBIBBH",
        b"VTF\0", 7, 2, 80, w, h, flags, 1, 0,
        *reflectivity, 1.0, fmt, len(mips),
        0xFFFFFFFF, 0, 0,  # no low-res thumbnail
        1,                 # depth
    )
    header = header.ljust(80, b"\0")

    with open(path, "wb") as f:
        f.write(header)
        for mip in reversed(mips):  # smallest first
            if has_alpha:
                data = mip[..., [2, 1, 0, 3]]
            else:
                data = mip[..., [2, 1, 0]]
            f.write(np.ascontiguousarray(data).tobytes())


def read_vtf_header(path: str) -> dict:
    with open(path, "rb") as f:
        raw = f.read(80)
    vals = struct.unpack_from("<4s2IIHHIHH4x3f4xfIBIBBH", raw)
    keys = ["sig", "major", "minor", "header_size", "width", "height", "flags", "frames",
            "first_frame", "r", "g", "b", "bump", "format", "mips", "lr_format", "lr_w", "lr_h", "depth"]
    return dict(zip(keys, vals))


def vmt_text(basetexture: str, shader: str = "LightmappedGeneric", surfaceprop: str = "concrete",
             bumpmap: str | None = None, translucent: bool = False, extra: dict | None = None) -> str:
    lines = [f'"{shader}"', "{", f'\t"$basetexture" "{basetexture}"', f'\t"$surfaceprop" "{surfaceprop}"']
    if bumpmap:
        lines.append(f'\t"$bumpmap" "{bumpmap}"')
    if translucent:
        lines.append('\t"$translucent" "1"')
    for k, v in (extra or {}).items():
        lines.append(f'\t"{k}" "{v}"')
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_material(game_dir: str, material_path: str, rgba: np.ndarray, **vmt_kwargs) -> tuple[str, str]:
    """Write <game_dir>/materials/<material_path>.vtf/.vmt. Returns their paths."""
    base = os.path.join(game_dir, "materials", *material_path.split("/"))
    os.makedirs(os.path.dirname(base), exist_ok=True)
    write_vtf(base + ".vtf", rgba)
    with open(base + ".vmt", "w", encoding="utf-8") as f:
        f.write(vmt_text(material_path, **vmt_kwargs))
    return base + ".vtf", base + ".vmt"
