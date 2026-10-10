"""Static props' baked light, for the baked lighting view.

With Prop Lighting on, the light compiler (vrad or hlvrad) bakes a colour at every vertex of each static prop
and packs them into the map as sp_hdr_<prop>.vhv (sp_<prop>.vhv for LDR): per body part, per model, per LOD,
per mesh, per strip group, its .vtx vertexes' colours, 8-bit BGRA in the game's vertex-light gamma
((light)^(1/2.2) / 2). Here they're put back on the props' meshes (LOD 0 of each body part's first model,
as the game draws a prop) as triangles with linear light, in the same units as the lightmaps.
"""
from __future__ import annotations

import io
import math
import struct
import zipfile
from dataclasses import dataclass

import numpy as np

from .mdl import VVD_VERTEX_SIZE, read_vvd

PROP_RECORD = 72          # L4D2's static prop record (sprp version 8 and 9)


@dataclass
class PropLighting:
    positions: np.ndarray     # (n*3, 3) float32 triangle corners, Hammer units
    colors: np.ndarray        # (n*3, 3) float32 linear light (lightmap units: 2.0 shows white)
    props: int                # props drawn
    missing: int              # props with baked light whose model couldn't be read


def _i32(b: bytes, o: int) -> int:
    return struct.unpack_from("<i", b, o)[0]


def _static_props(bsp: bytes):
    """(model name, origin, angles) of every static prop, in the map's order."""
    _ver, ofs, ln, _cc = struct.unpack_from("<iiii", bsp, 8 + 16 * 35)
    lump = bsp[ofs:ofs + ln]
    if len(lump) < 4:
        return []
    for g in range(_i32(lump, 0)):
        gid, _flags, version, gofs, glen = struct.unpack_from("<iHHii", lump, 4 + 16 * g)
        if gid != 0x73707270:          # 'sprp'
            continue
        data = bsp[gofs:gofs + glen]
        nd = _i32(data, 0)
        names = [data[4 + 128 * i:4 + 128 * (i + 1)].split(b"\0", 1)[0].decode("latin-1") for i in range(nd)]
        p = 4 + 128 * nd
        p += 4 + 2 * _i32(data, p)                         # (the leaves)
        n = _i32(data, p)
        p += 4
        props = []
        for i in range(n):
            rec = p + PROP_RECORD * i
            origin = struct.unpack_from("<3f", data, rec)
            angles = struct.unpack_from("<3f", data, rec + 12)
            model = struct.unpack_from("<H", data, rec + 24)[0]
            props.append((names[model] if model < nd else "", origin, angles))
        return props
    return []


def _angle_matrix(angles) -> np.ndarray:
    """Source's AngleMatrix (pitch, yaw, roll in degrees) as a 3x3 rotation."""
    p, y, r = (math.radians(a) for a in angles)
    sp, cp, sy, cy, sr, cr = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(r), math.cos(r)
    return np.array([[cp * cy, sr * sp * cy - cr * sy, cr * sp * cy + sr * sy],
                     [cp * sy, sr * sp * sy + cr * cy, cr * sp * sy - sr * cy],
                     [-sp, sr * cp, cr * cp]], np.float64)


def _strip_group(vtx: bytes, at: int):
    """A strip group's vertexes (origMeshVertID each) and triangle-list indices."""
    nverts, vert_off, nidx, idx_off = struct.unpack_from("<4i", vtx, at)
    verts = [struct.unpack_from("<H", vtx, at + vert_off + 9 * v + 4)[0] for v in range(nverts)]
    idx = struct.unpack_from(f"<{nidx}H", vtx, at + idx_off) if nidx else ()
    return verts, idx


def _vhv_colors(vhv: bytes) -> list[np.ndarray]:
    """The .vhv's meshes' colours, each (n, 3) linear light."""
    _version, _checksum, _flags, _size, _total, nmeshes = struct.unpack_from("<6i", vhv, 0)
    out = []
    for i in range(nmeshes):
        _lod, nv, at = struct.unpack_from("<3i", vhv, 40 + 28 * i)
        c = np.frombuffer(vhv, np.uint8, 4 * nv, at).reshape(nv, 4)[:, [2, 1, 0]].astype(np.float32) / 255.0
        out.append(np.power(2.0 * c, 2.2).astype(np.float32))     # (the inverse of the vertex-light gamma)
    return out


def _prop_triangles(mdl: bytes, vvd: bytes, vtx: bytes, colors: list[np.ndarray]):
    """The prop's LOD-0 triangles (model space) with their vertexes' colours, walking the .vtx as the light
    compiler wrote the .vhv (every model, LOD, mesh and strip group in order)."""
    positions, _uv = read_vvd(vvd)
    num_bp, bp_index = struct.unpack_from("<ii", mdl, 232)
    vtx_lods, vtx_bp = _i32(vtx, 20), _i32(vtx, 32)
    tris, cols = [], []
    k = 0                                    # the .vhv mesh
    for b in range(num_bp):
        bp = bp_index + 16 * b
        nmodels, model_index = _i32(mdl, bp + 4), _i32(mdl, bp + 12)
        xbp = vtx_bp + 8 * b
        for m in range(nmodels):
            sub = bp + model_index + 148 * m
            nm, mesh_index = _i32(mdl, sub + 72), _i32(mdl, sub + 76)
            first = _i32(mdl, sub + 84) // VVD_VERTEX_SIZE
            xmodel = xbp + _i32(vtx, xbp + 4) + 8 * m
            for lod in range(vtx_lods):
                xlod = xmodel + _i32(vtx, xmodel + 4) + 12 * lod
                for mm in range(nm):
                    vofs = _i32(mdl, sub + mesh_index + 116 * mm + 12)
                    xmesh = xlod + _i32(vtx, xlod + 4) + 9 * mm
                    ngroups = _i32(vtx, xmesh)
                    for g in range(ngroups):
                        if k >= len(colors):
                            return tris, cols
                        c = colors[k]
                        k += 1
                        if m or lod:
                            continue
                        verts, idx = _strip_group(vtx, xmesh + _i32(vtx, xmesh + 4) + 25 * g)
                        if len(c) < len(verts):
                            continue
                        for i in range(0, len(idx) - 2, 3):
                            tri = (idx[i], idx[i + 2], idx[i + 1])
                            ids = [first + vofs + verts[v] for v in tri]
                            if max(ids) >= len(positions):
                                continue
                            tris.append(positions[ids])
                            cols.append(c[list(tri)])
    return tris, cols


def read_prop_lighting(bsp: bytes, content) -> PropLighting:
    """content: vpk.GameContent (the props' models)."""
    empty = PropLighting(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), 0, 0)
    _ver, ofs, ln, _cc = struct.unpack_from("<iiii", bsp, 8 + 16 * 40)
    if ln < 22:
        return empty
    try:
        pak = zipfile.ZipFile(io.BytesIO(bsp[ofs:ofs + ln]))
        names = {n.lower(): n for n in pak.namelist()}
    except zipfile.BadZipFile:
        return empty
    models: dict[str, tuple | None] = {}
    all_pos, all_col = [], []
    drawn = missing = 0
    for i, (name, origin, angles) in enumerate(_static_props(bsp)):
        vhv = names.get(f"sp_hdr_{i}.vhv") or names.get(f"sp_{i}.vhv")
        if not vhv or not name:
            continue
        if name not in models:
            base = name[:-4] if name.lower().endswith(".mdl") else name
            files = (content.read(base + ".mdl"), content.read(base + ".vvd"),
                     content.read(base + ".dx90.vtx") or content.read(base + ".vtx"))
            models[name] = files if all(files) else None
        files = models[name]
        if files is None:
            missing += 1
            continue
        try:
            tris, cols = _prop_triangles(*files, _vhv_colors(pak.read(vhv)))
        except (struct.error, ValueError, IndexError):
            missing += 1
            continue
        if not tris:
            continue
        pos = np.concatenate(tris).astype(np.float64)
        pos = pos @ _angle_matrix(angles).T + np.array(origin)
        all_pos.append(pos.astype(np.float32))
        all_col.append(np.concatenate(cols).astype(np.float32))
        drawn += 1
    if not all_pos:
        return PropLighting(empty.positions, empty.colors, 0, missing)
    return PropLighting(np.concatenate(all_pos), np.concatenate(all_col), drawn, missing)
