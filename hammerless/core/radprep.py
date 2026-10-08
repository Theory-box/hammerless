"""What Hammerless's lighting compiler (hlvrad) reads from the game besides the map.

hlvrad lights the map like vrad; static props cast shadows with their collision models (or, for a
model without one, hulls around its meshes), so their model files are copied out of the game's VPKs (or loose files) into a folder it is given with -modeldir.
"""
from __future__ import annotations

import os
import struct

MODEL_EXTENSIONS = (".mdl", ".phy", ".vvd")


def static_prop_models(bsp: bytes) -> list[str]:
    """The model names in a compiled map's static prop dictionary (game lump 'sprp')."""
    _ver, ofs, ln, _cc = struct.unpack_from("<iiii", bsp, 8 + 16 * 35)
    lump = bsp[ofs:ofs + ln]
    if len(lump) < 4:
        return []
    count = struct.unpack_from("<i", lump, 0)[0]
    for g in range(count):
        gid, _flags, _version, gofs, glen = struct.unpack_from("<iHHii", lump, 4 + 16 * g)
        if gid != 0x73707270:          # 'sprp'
            continue
        data = bsp[gofs:gofs + glen]
        n = struct.unpack_from("<i", data, 0)[0]
        return [data[4 + 128 * i:4 + 128 * (i + 1)].split(b"\0", 1)[0].decode("latin-1") for i in range(n)]
    return []


def export_prop_models(bsp_path: str, content, out_dir: str) -> int:
    """Copy the static props' .mdl, .phy and .vvd files into out_dir (same relative paths). Returns how many
    files were written."""
    with open(bsp_path, "rb") as f:
        bsp = f.read()
    written = 0
    for name in static_prop_models(bsp):
        stem = os.path.splitext(name.replace("\\", "/"))[0]
        for ext in MODEL_EXTENSIONS:
            data = content.read(stem + ext)
            if not data:
                continue
            path = os.path.join(out_dir, *(stem + ext).split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            written += 1
    return written
