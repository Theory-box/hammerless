"""Minimal read-only VPK reader.

Lists file paths (to check that materials/models exist in the game) and reads
individual files (e.g. model headers for their bounding boxes).
Supports VPK v1 and v2 `*_dir.vpk` files.
"""
from __future__ import annotations

import os
import struct

VPK_SIGNATURE = 0x55AA1234
DIR_ARCHIVE = 0x7FFF  # entry data stored in the _dir.vpk itself


def _read_cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\0", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


class VPK:
    def __init__(self, dir_vpk_path: str):
        self.path = dir_vpk_path
        self.entries: dict[str, tuple[int, int, int, bytes]] = {}
        with open(dir_vpk_path, "rb") as f:
            sig, version, tree_size = struct.unpack("<III", f.read(12))
            if sig != VPK_SIGNATURE:
                raise ValueError(f"Not a VPK directory file: {dir_vpk_path}")
            if version == 2:
                f.read(16)
                header_size = 28
            elif version == 1:
                header_size = 12
            else:
                raise ValueError(f"Unsupported VPK version {version}")
            tree = f.read(tree_size)
        self._data_start = header_size + tree_size

        pos = 0
        while True:
            ext, pos = _read_cstr(tree, pos)
            if not ext:
                break
            while True:
                path, pos = _read_cstr(tree, pos)
                if not path:
                    break
                while True:
                    name, pos = _read_cstr(tree, pos)
                    if not name:
                        break
                    _crc, preload, archive, offset, size, _term = struct.unpack_from("<IHHIIH", tree, pos)
                    pos += 18
                    preload_data = tree[pos:pos + preload]
                    pos += preload
                    full = name if path.strip() == "" else f"{path}/{name}"
                    if ext.strip() != "":
                        full += "." + ext
                    self.entries[full.lower()] = (archive, offset, size, preload_data)

    def read(self, path: str) -> bytes:
        archive, offset, size, preload = self.entries[path.lower().replace("\\", "/")]
        if size == 0:
            return preload
        if archive == DIR_ARCHIVE:
            file, offset = self.path, offset + self._data_start
        else:
            file = self.path.replace("_dir.vpk", f"_{archive:03d}.vpk")
        with open(file, "rb") as f:
            f.seek(offset)
            return preload + f.read(size)


def list_vpk_files(dir_vpk_path: str) -> list[str]:
    return list(VPK(dir_vpk_path).entries)


def find_game_vpks(game_root: str) -> list[str]:
    """All *_dir.vpk files one level under the L4D2 install. Later content
    (update, dlc) is listed first so it overrides the base game."""
    found = []
    for sub in sorted(os.listdir(game_root), reverse=True):
        d = os.path.join(game_root, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith("_dir.vpk"):
                found.append(os.path.join(d, name))
    return found


def model_bounds(mdl_bytes: bytes) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Hull min/max from a studiohdr_t (.mdl) header."""
    # id, version, checksum, name[64], length, eyeposition, illumposition, hull_min, hull_max
    mins = struct.unpack_from("<3f", mdl_bytes, 104)
    maxs = struct.unpack_from("<3f", mdl_bytes, 116)
    return mins, maxs


class GameContent:
    """Index of material/model paths available in the game's VPKs."""

    def __init__(self, game_root: str):
        self.game_root = game_root
        self.vpks: list[VPK] = []
        for p in find_game_vpks(game_root):
            try:
                self.vpks.append(VPK(p))
            except (OSError, ValueError):
                pass
        self.files: set[str] = set()
        for v in self.vpks:
            self.files.update(v.entries)

    def read(self, path: str) -> bytes | None:
        path = path.lower().replace("\\", "/")
        for v in self.vpks:
            if path in v.entries:
                return v.read(path)
        return None

    def has_material(self, name: str) -> bool:
        return f"materials/{name.lower().replace(chr(92), '/')}.vmt" in self.files

    def has_model(self, path: str) -> bool:
        return path.lower().replace("\\", "/") in self.files

    def materials(self, prefix: str = "") -> list[str]:
        prefix = prefix.lower()
        return sorted(
            f[len("materials/"):-4]
            for f in self.files
            if f.startswith("materials/") and f.endswith(".vmt") and f[len("materials/"):].startswith(prefix)
        )
