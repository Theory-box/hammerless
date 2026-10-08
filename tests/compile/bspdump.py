"""Dump an L4D2 .bsp (v21) as text, one record per line, so two compiles can be diffed
(our own map compiler against Valve's vbsp).

    python tests/compile/bspdump.py map.bsp [lump ...]          (all lumps, or only these numbers/names)
    python tests/compile/bspdump.py a.bsp b.bsp --diff          (first differing record per lump)
"""
from __future__ import annotations

import io
import struct
import sys
import zipfile

NAMES = ["entities", "planes", "texdata", "vertexes", "visibility", "nodes", "texinfo", "faces", "lighting",
         "occlusion", "leafs", "faceids", "edges", "surfedges", "models", "worldlights", "leaffaces",
         "leafbrushes", "brushes", "brushsides", "areas", "areaportals", "propcollision", "prophulls",
         "prophullverts", "proptris", "dispinfo", "originalfaces", "physdisp", "physcollide", "vertnormals",
         "vertnormalindices", "disp_lightmap_alphas", "dispverts", "disp_lightmap_sample_positions", "game",
         "leafwaterdata", "primitives", "primverts", "primindices", "pakfile", "clipportalverts", "cubemaps",
         "texdata_string_data", "texdata_string_table", "overlays", "leafmindisttowater",
         "face_macro_texture_info", "disp_tris", "prop_blob", "wateroverlays", "leaf_ambient_index_hdr",
         "leaf_ambient_index", "lighting_hdr", "worldlights_hdr", "leaf_ambient_lighting_hdr",
         "leaf_ambient_lighting", "xzippakfile", "faces_hdr", "map_flags", "overlay_fades",
         "overlay_system_levels", "physlevel", "disp_multiblend"]

# record layouts (struct format, field names) of the fixed-size lumps
RECORDS = {
    1: ("<4fi", "normal3 dist type"),
    2: ("<3f5i", "reflectivity3 name width height view_w view_h"),
    3: ("<3f", "xyz3"),
    5: ("<3i6h2H2h", "plane front back mins3 maxs3 firstface numfaces area pad"),
    6: ("<16f2i", "tex_s4 tex_t4 lm_s4 lm_t4 flags texdata"),
    7: ("<H2Bi4h4BIf4ii2HI", "plane side onnode firstedge numedges texinfo dispinfo fogvolume styles4 lightofs "
                            "area lm_mins2 lm_size2 origface numprims firstprim smoothing"),
    10: ("<ihh6h4Hhh", "contents cluster area_flags mins3 maxs3 firstface numfaces firstbrush numbrushes water pad"),
    11: ("<H", "id"),
    12: ("<2H", "v2"),
    13: ("<i", "edge"),
    14: ("<9f3i", "mins3 maxs3 origin3 headnode firstface numfaces"),
    16: ("<H", "face"),
    17: ("<H", "brush"),
    18: ("<3i", "firstside numsides contents"),
    19: ("<H2h2B", "plane texinfo dispinfo bevel thin"),
    20: ("<2i", "numportals firstportal"),
    21: ("<4Hi", "key otherarea firstclip numclip plane"),
    27: ("<H2Bi4h4BIf4ii2HI", "plane side onnode firstedge numedges texinfo dispinfo fogvolume styles4 lightofs "
                             "area lm_mins2 lm_size2 origface numprims firstprim smoothing"),
    30: ("<3f", "xyz3"),
    31: ("<H", "index"),
    33: ("<3f2f", "vec3 dist alpha"),
    37: ("<B4H", "type firstindex numindices firstvert numverts"),
    38: ("<3f", "xyz3"),
    39: ("<H", "index"),
    41: ("<3f", "xyz3"),
    42: ("<4i", "origin3 size"),
    44: ("<i", "offset"),
    46: ("<H", "dist"),
    47: ("<H", "id"),
    48: ("<H", "tags"),
    59: ("<I", "flags"),
}


def lumps(data: bytes) -> list[tuple[int, int, int, bytes]]:
    """(version, offset, length, bytes) per lump, in L4D2's lump_t order (version first)."""
    if data[:4] != b"VBSP":
        raise ValueError("not a BSP")
    out = []
    for i in range(64):
        ver, off, length, _cc = struct.unpack_from("<4i", data, 8 + 16 * i)
        out.append((ver, off, length, data[off:off + length]))
    return out


def _fmt(v) -> str:
    return repr(v) if isinstance(v, float) else str(v)


def _mask_ivp_points(solid: bytes) -> bytes:
    """A collision solid (VPHY + IVP compact surface) with the unused 4th word of every ledge point
    zeroed: vphysics leaves it uninitialised, so it differs from run to run even in vbsp's output."""
    body = bytearray(solid)
    if body[:4] != b"VPHY":
        return bytes(body)
    cs = 28
    try:
        (root,) = struct.unpack_from("<i", body, cs + 32)
        stack, seen = [cs + root], set()
        while stack:
            node = stack.pop()
            if node in seen or not 0 <= node <= len(body) - 28:
                break
            seen.add(node)
            right, ledge_off = struct.unpack_from("<ii", body, node)
            if right == 0:
                ledge = node + ledge_off
                point_off, _client, _flags, ntri = struct.unpack_from("<iiih", body, ledge)
                used = set()
                for t in range(ntri):
                    for e in range(3):
                        used.add(struct.unpack_from("<I", body, ledge + 16 + 16 * t + 4 + 4 * e)[0] & 0xFFFF)
                for i in used:
                    p = ledge + point_off + 16 * i + 12
                    body[p:p + 4] = bytes(4)
            else:
                stack.append(node + 28)
                stack.append(node + right)
    except struct.error:
        pass
    return bytes(body)


def _static_props_v9(b: bytes) -> list[str]:
    """L4D2's static props: dictionary, leaves, 72-byte records. vbsp leaves the lighting origin
    unset (unless the prop has one) and the 3 bytes after disableX360 uninitialised: masked."""
    out = []
    n = struct.unpack_from("<i", b, 0)[0]
    at = 4
    out += ["  model " + b[at + 128 * i:at + 128 * i + 128].split(b"\0")[0].decode("latin-1") for i in range(n)]
    at += 128 * n
    nl = struct.unpack_from("<i", b, at)[0]
    at += 4
    out.append("  leaves " + " ".join(str(x) for x in struct.unpack_from(f"<{nl}H", b, at)))
    at += 2 * nl
    np_ = struct.unpack_from("<i", b, at)[0]
    at += 4
    for i in range(np_):
        p = bytearray(b[at + 72 * i:at + 72 * i + 72])
        if not p[31] & 0x2:
            p[44:56] = bytes(12)
        p[69:72] = bytes(3)
        out.append("  prop " + p.hex())
    return out


def _detail_props_v4(b: bytes) -> list[str]:
    """Detail props: model dictionary, sprite dictionary, 52-byte records (padding masked; a model's
    unused scale too, which vbsp leaves unset)."""
    out = []
    n = struct.unpack_from("<i", b, 0)[0]
    at = 4
    out += ["  model " + b[at + 128 * i:at + 128 * i + 128].split(b"\0")[0].decode("latin-1") for i in range(n)]
    at += 128 * n
    ns = struct.unpack_from("<i", b, at)[0]
    at += 4
    out += ["  sprite " + " ".join(repr(x) for x in struct.unpack_from("<8f", b, at + 32 * i)) for i in range(ns)]
    at += 32 * ns
    no = struct.unpack_from("<i", b, at)[0]
    at += 4
    for i in range(no):
        p = bytearray(b[at + 52 * i:at + 52 * i + 52])
        p[41:44] = bytes(3)                 # padding after the orientation
        p[45:48] = bytes(3)                 # and after the type
        if p[44] == 0:                      # a model: no scale
            p[48:52] = bytes(4)
        o = struct.unpack_from("<6f", p, 0)
        out.append("  detail " + " ".join(f"{x!r}" for x in o) + " " + p[24:52].hex())
    return out


def records(index: int, blob: bytes) -> list[str]:
    """One text line per record of lump `index`."""
    if not blob:
        return []
    if index == 0:
        return blob.decode("latin-1").rstrip("\0").splitlines()
    if index == 43:
        return [s.decode("latin-1") for s in blob.split(b"\0")]
    if index == 40:
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                return [f"{i.filename} {i.file_size} {i.CRC:08x}" for i in z.infolist()]
        except zipfile.BadZipFile:
            return [f"<bad zip {len(blob)} bytes>"]
    if index == 35:
        n = struct.unpack_from("<i", blob, 0)[0]
        out = [f"count {n}"]
        first = None
        for k in range(n):
            gid, flags, ver, off, length = struct.unpack_from("<4sHHii", blob, 4 + 16 * k)
            out.append(f"{gid[::-1].decode('latin-1')} flags {flags} version {ver} length {length}")
            if first is None:
                first = off - (4 + 16 * n)          # file offset of the game lump itself
            data = blob[off - first:off - first + length]
            if gid == b"prps" and ver == 9:
                out += _static_props_v9(data)
            elif gid == b"prpd" and ver == 4:
                out += _detail_props_v4(data)
            else:
                out.append("  data " + data.hex())
        return out
    if index == 29:
        out, at = [], 0
        while at + 16 <= len(blob):
            model, size, keysize, count = struct.unpack_from("<4i", blob, at)
            out.append(f"model {model} datasize {size} keysize {keysize} solids {count}")
            if model == -1:
                break
            at += 16
            for _ in range(count):
                n = struct.unpack_from("<i", blob, at)[0]
                out.append(f"  solid {n} bytes: " + _mask_ivp_points(blob[at + 4:at + 4 + n]).hex())
                at += 4 + n
            out.append("  text: " + blob[at:at + keysize].decode("latin-1").replace("\n", " | "))
            at += keysize
        return out
    if index == 26:
        # dispinfo: 176-byte records; vbsp leaves its padding bytes uninitialised, so they're masked
        out = []
        for k in range(len(blob) // 176):
            r = bytearray(blob[176 * k:176 * k + 176])
            r[38:40] = bytes(2)
            for e in range(8):
                r[48 + 6 * e + 5] = 0
                if r[48 + 6 * e:48 + 6 * e + 2] == bytes([255, 255]):          # no neighbour: the rest is unset
                    r[48 + 6 * e + 2:48 + 6 * e + 5] = bytes(3)
            for c in range(4):
                r[96 + 10 * c + 9] = 0
                count = min(r[96 + 10 * c + 8], 4)           # neighbour slots past the count are unset
                r[96 + 10 * c + 2 * count:96 + 10 * c + 8] = bytes(8 - 2 * count)
            out.append(r.hex())
        return out
    if index in RECORDS:
        fmt, names = RECORDS[index]
        size = struct.calcsize(fmt)
        return [" ".join(_fmt(v) for v in struct.unpack_from(fmt, blob, k * size))
                for k in range(len(blob) // size)] + ([f"<{len(blob) % size} extra bytes>"] if len(blob) % size else [])
    return [blob[k:k + 32].hex() for k in range(0, len(blob), 32)]


def dump(path: str, only: list[int] | None = None) -> dict[int, list[str]]:
    with open(path, "rb") as f:
        data = f.read()
    return {i: records(i, blob) for i, (_v, _o, _l, blob) in enumerate(lumps(data)) if only is None or i in only}


def _lump_index(name: str) -> int:
    return int(name) if name.isdigit() else NAMES.index(name)


def main(argv: list[str]) -> int:
    paths = [a for a in argv if a.endswith(".bsp")]
    only = [_lump_index(a) for a in argv if not a.endswith(".bsp") and not a.startswith("--")] or None
    if "--diff" in argv:
        a, b = (dump(p, only) for p in paths[:2])
        same = 0
        for i in sorted(a):
            if a[i] == b[i]:
                same += 1
                continue
            first = next((k for k in range(max(len(a[i]), len(b[i])))
                          if k >= len(a[i]) or k >= len(b[i]) or a[i][k] != b[i][k]), None)
            print(f"[{i} {NAMES[i]}] {len(a[i])} vs {len(b[i])} records; first difference at {first}")
            if first is not None:
                print("   a:", a[i][first] if first < len(a[i]) else "-")
                print("   b:", b[i][first] if first < len(b[i]) else "-")
        print(f"{same} lumps identical")
        return 0
    for i, rows in dump(paths[0], only).items():
        if rows:
            print(f"[{i} {NAMES[i]}] {len(rows)}")
            for k, r in enumerate(rows):
                print(f"  {k}: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
