"""The game's stringtable dictionary in a compiled map: keeping it from one build to the next.

L4D2 compresses its networked string tables (precached models, sounds, scenes...) with a dictionary stored in
the map's pakfile as stringtable_dictionary.dct: the strings, each ending in a zero byte. A map without one
gets it built while it loads, and the game writes it back into maps/<map>.bsp when the map unloads. Measured
on a real map: building it costs about 10 s of every load after a fresh build (map start 12-13 s against
2.9 s). It needn't match the map exactly: one from an earlier build (strings added and missing) loads as fast,
while one missing most of the game's sounds and scenes is as slow as none (or, nearly empty, fails the load).

So each build takes the dictionary the game saved into the previous build of the map, else the last one the
game saved for any Hammerless map (kept in the game folder), and puts it in the new map's pakfile.
"""
from __future__ import annotations

import io
import os
import struct
import zipfile

DICT_NAME = "stringtable_dictionary.dct"
PAKFILE = 40
_MIN_STRINGS = 10000      # (a dictionary with fewer can't hold the game's own sounds and scenes: not worth keeping)


def _pakfile(data: bytes) -> tuple[int, int]:
    _ver, off, length, _cc = struct.unpack_from("<iiii", data, 8 + 16 * PAKFILE)
    return off, length


def read_dictionary(bsp_path: str) -> bytes | None:
    """The map's stringtable dictionary, or None (no map, none in it, or unreadable)."""
    try:
        with open(bsp_path, "rb") as f:
            header = f.read(8 + 16 * 64)
            if header[:4] != b"VBSP":
                return None
            off, length = _pakfile(header)
            if length <= 0:
                return None
            f.seek(off)
            pak = f.read(length)
        with zipfile.ZipFile(io.BytesIO(pak)) as z:
            return z.read(DICT_NAME) if DICT_NAME in z.namelist() else None
    except (OSError, zipfile.BadZipFile, struct.error, KeyError):
        return None


def for_map(dictionary: bytes, map_name: str) -> bytes:
    """The dictionary with its map's own files (maps/<name>.bsp, .nav: its first strings) renamed to map_name."""
    strings = dictionary.split(b"\0")
    old = strings[0][len(b"maps/"):].rsplit(b".", 1)[0] if strings and strings[0].startswith(b"maps/") else None
    if old:
        prefix, new = b"maps/" + old + b".", b"maps/" + map_name.encode() + b"."
        strings = [new + s[len(prefix):] if s.startswith(prefix) else s for s in strings]
    return b"\0".join(strings)


def usable(dictionary: bytes | None) -> bool:
    return bool(dictionary) and dictionary.count(b"\0") >= _MIN_STRINGS


def put_dictionary(bsp_path: str, dictionary: bytes) -> None:
    """Write the dictionary into the map's pakfile (replacing one there). The pakfile is rewritten in place
    when it's the file's last lump (as our compilers write it), else appended at the end: the other lumps stay
    where they are."""
    with open(bsp_path, "rb") as f:
        data = bytearray(f.read())
    off, length = _pakfile(data)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as out:     # (the game reads only stored entries)
        if length > 0:
            with zipfile.ZipFile(io.BytesIO(bytes(data[off:off + length]))) as old:
                for info in old.infolist():
                    if info.filename != DICT_NAME:
                        out.writestr(info, old.read(info.filename))
        out.writestr(DICT_NAME, dictionary)
    pak = buf.getvalue()
    last = length > 0 and off + length >= len(data.rstrip(b"\0")) and all(
        struct.unpack_from("<iiii", data, 8 + 16 * i)[1] + struct.unpack_from("<iiii", data, 8 + 16 * i)[2] <= off
        for i in range(64) if i != PAKFILE)
    if last:
        del data[off:]
    else:
        data += b"\0" * ((-len(data)) % 4)
        off = len(data)
    data += pak
    struct.pack_into("<ii", data, 8 + 16 * PAKFILE + 4, off, len(pak))
    tmp = bsp_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(data))
    os.replace(tmp, bsp_path)


def cache_path(gamedir: str) -> str:
    return os.path.join(gamedir, "scripts", "vscripts", "hammerless", "stringtable_dictionary.dct")


def keep_for_next_build(game_bsp: str, gamedir: str) -> bytes | None:
    """Before a new build replaces the game's copy of a map: the dictionary to give it (the one the game saved
    into this map, else the last one saved for any Hammerless map). Remembers the map's own for other maps."""
    own = read_dictionary(game_bsp)
    cache = cache_path(gamedir)
    if usable(own):
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "wb") as f:
                f.write(own)
        except OSError:
            pass
        return own
    try:
        with open(cache, "rb") as f:
            cached = f.read()
        return cached if usable(cached) else None
    except OSError:
        return None
