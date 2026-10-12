"""Read the MESH of one of Left 4 Dead 2's own models: .mdl (v49) + .vvd (v4) + .dx90.vtx (v7).

Structures from Source SDK 2013 public/studio.h and optimize.h, as the game's 32-bit files lay them out
(our writer of the same files, mdlwrite.py, shows them field by field). The skeleton, sequences and
hitboxes of the same model come from gamemodel.py.

- Vertices are in the model's bind space, as stored (the space of gamemodel's GameModel.bone_world()).
- Body: `body` is the game's packed body value (an entity's "body" / SetBodygroup result): bodypart i shows
  submodel (body // base_i) % nummodels_i. body 0 = the first submodel of every bodypart.
- Winding: the .vtx stores front faces clockwise (seen from the front); triangles here are turned to
  counter-clockwise, Blender's front-face convention, so a face's normal agrees with its vertex normals
  (measured: > 99 % of faces on the commons and survivors; the rest are slivers and folds).
- UV: as stored (v runs top-down); Blender's v is 1 - v.
- Not read: flexes (face morphs), eyeballs, skin families other than 0, tangents, LODs' material
  replacements. The .vvd's LOD fixup table is not needed (see _vvd_vertices).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

Vec = tuple[float, float, float]

# studiohdr_t (v49)
_H_TEXTURES, _H_CDTEXTURES, _H_SKINS, _H_BODYPARTS = 204, 212, 220, 232
_MODEL_SIZE, _MESH_SIZE, _VVD_VERT = 148, 116, 48
# optimize.h on-disk sizes (L4D2's v7: stripgroup header without the topology fields)
_VTX_MESH, _VTX_GROUP, _VTX_STRIP, _VTX_VERT = 9, 25, 27, 9
_STRIP_IS_TRILIST, _STRIP_IS_TRISTRIP = 0x1, 0x2


@dataclass
class GameMesh:
    positions: list[Vec] = field(default_factory=list)
    normals: list[Vec] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    weights: list[list[tuple[int, float]]] = field(default_factory=list)   # (bone, weight), 1..3 per vertex
    triangles: list[tuple[int, int, int]] = field(default_factory=list)    # counter-clockwise front faces
    material_index: list[int] = field(default_factory=list)               # per triangle, into materials
    materials: list[str] = field(default_factory=list)                    # the .mdl's texture table, in order
    cdmaterials: list[str] = field(default_factory=list)                  # where the game looks for them
    bodypart: list[str] = field(default_factory=list)                     # per triangle: its bodypart's name


def _cstr(b: bytes, at: int) -> str:
    if at <= 0 or at >= len(b):
        return ""
    return b[at:b.index(b"\0", at)].decode("latin-1")


def _i(b: bytes, at: int) -> int:
    return struct.unpack_from("<i", b, at)[0]


def _vvd_vertices(vvd: bytes) -> list[int]:
    """Byte offsets of the vertices, in order: the full (LOD 0) list, which every LOD's .vtx indexes.

    The fixup table only matters to a game that drops the top LODs from memory (a root LOD > 0, as
    r_rootlod does): it then packs the smaller list and rebases the meshes. Reading the whole list, as the
    game does at root LOD 0, every LOD is right (measured: applying the fixups for LOD 1 here broke 17 %
    of its faces)."""
    if vvd[:4] != b"IDSV":
        raise ValueError("not a .vvd")
    vstart = _i(vvd, 56)
    return [vstart + _VVD_VERT * k for k in range(_i(vvd, 16))]


def read_game_mesh(mdl: bytes, vvd: bytes, vtx: bytes, lod: int = 0, body: int = 0) -> GameMesh:
    """The mesh from the three files' bytes."""
    if mdl[:4] != b"IDST":
        raise ValueError("not a Source model")
    out = GameMesh()
    ntex, texat = _i(mdl, _H_TEXTURES), _i(mdl, _H_TEXTURES + 4)
    out.materials = [_cstr(mdl, texat + 64 * k + _i(mdl, texat + 64 * k)) for k in range(ntex)]
    ncd, cdat = _i(mdl, _H_CDTEXTURES), _i(mdl, _H_CDTEXTURES + 4)
    out.cdmaterials = [_cstr(mdl, _i(mdl, cdat + 4 * k)).replace("\\", "/") for k in range(ncd)]
    nskinref, skinat = _i(mdl, _H_SKINS), _i(mdl, _H_SKINS + 8)
    skin0 = list(struct.unpack_from(f"<{nskinref}h", mdl, skinat)) if nskinref else []

    vverts = _vvd_vertices(vvd)
    if _i(vtx, 0) != 7:
        raise ValueError("not a v7 .vtx")
    vtx_nlods, vtx_nparts, vtx_parts = _i(vtx, 20), _i(vtx, 28), _i(vtx, 32)
    lod = min(lod, max(vtx_nlods - 1, 0))

    nparts, partat = _i(mdl, _H_BODYPARTS), _i(mdl, _H_BODYPARTS + 4)
    remap: dict[int, int] = {}       # vvd vertex (index in this LOD's list) -> ours
    for p in range(min(nparts, vtx_nparts)):
        po = partat + 16 * p
        pname, nmodels, base, modelidx = struct.unpack_from("<iiii", mdl, po)
        if nmodels <= 0:
            continue
        sub = (body // max(base, 1)) % nmodels
        mo = po + modelidx + _MODEL_SIZE * sub
        nmesh, meshidx, _nv, vertexindex = struct.unpack_from("<iiii", mdl, mo + 72)
        model_first = vertexindex // _VVD_VERT
        name = _cstr(mdl, po + pname)
        # the .vtx's bodypart -> model -> LOD
        vpo = vtx_parts + 8 * p
        vmo = vpo + _i(vtx, vpo + 4) + 8 * sub
        vlo = vmo + _i(vtx, vmo + 4) + 12 * lod
        vmesh_n, vmesh_at = _i(vtx, vlo), vlo + _i(vtx, vlo + 4)
        for m in range(min(nmesh, vmesh_n)):
            meo = mo + meshidx + _MESH_SIZE * m
            material = _i(mdl, meo)
            mesh_first = model_first + _i(mdl, meo + 12)
            mat = skin0[material] if 0 <= material < len(skin0) else material
            vme = vmesh_at + _VTX_MESH * m
            ngroups, gat = _i(vtx, vme), vme + _i(vtx, vme + 4)
            for g in range(ngroups):
                go = gat + _VTX_GROUP * g
                nv, vat, ni, iat, nstrips, sat = struct.unpack_from("<iiiiii", vtx, go)
                gverts = [vtx[go + vat + _VTX_VERT * k + 4] | vtx[go + vat + _VTX_VERT * k + 5] << 8
                          for k in range(nv)]
                gidx = struct.unpack_from(f"<{ni}H", vtx, go + iat) if ni else ()

                def ours(k):
                    src = mesh_first + gverts[k]
                    v = remap.get(src)
                    if v is None:
                        v = remap[src] = len(out.positions)
                        o = vverts[src]
                        w = struct.unpack_from("<3f", vvd, o)
                        n = vvd[o + 15]
                        out.weights.append([(vvd[o + 12 + j], w[j]) for j in range(min(n, 3))])
                        out.positions.append(struct.unpack_from("<3f", vvd, o + 16))
                        out.normals.append(struct.unpack_from("<3f", vvd, o + 28))
                        out.uvs.append(struct.unpack_from("<2f", vvd, o + 40))
                    return v

                for s in range(nstrips):
                    so = go + sat + _VTX_STRIP * s
                    sni, sio, _snv, _svo, _nb, sflags = struct.unpack_from("<iiiihB", vtx, so)
                    idx = gidx[sio:sio + sni]
                    tris = []
                    if sflags & _STRIP_IS_TRISTRIP:
                        for k in range(len(idx) - 2):
                            a, b_, c = idx[k], idx[k + 1], idx[k + 2]
                            if k & 1:
                                a, b_ = b_, a
                            if a != b_ and b_ != c and a != c:
                                tris.append((a, b_, c))
                    else:
                        tris = [(idx[k], idx[k + 1], idx[k + 2]) for k in range(0, len(idx) - 2, 3)]
                    for a, b_, c in tris:
                        # clockwise in the file -> counter-clockwise here
                        out.triangles.append((ours(a), ours(c), ours(b_)))
                        out.material_index.append(mat)
                        out.bodypart.append(name)
    return out


def load_game_mesh(content, path: str, lod: int = 0, body: int = 0) -> GameMesh | None:
    """A game model's mesh through a vpk.GameContent; None when a file is missing or unreadable."""
    path = path.replace("\\", "/").lower()
    stem = path[:-4] if path.endswith(".mdl") else path
    mdl, vvd, vtx = content.read(stem + ".mdl"), content.read(stem + ".vvd"), content.read(stem + ".dx90.vtx")
    if mdl is None or vvd is None or vtx is None:
        return None
    try:
        return read_game_mesh(mdl, vvd, vtx, lod, body)
    except (ValueError, struct.error, IndexError):
        return None
