"""Read Source models (.mdl + .vvd + .dx90.vtx) into plain triangle meshes for previews.

Only what a preview needs: LOD 0 of the first model in each body part, positions,
UVs, triangles, and the material each triangle uses (skin family 0).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

VVD_VERTEX_SIZE = 48  # bone weights (16) + position (12) + normal (12) + uv (8)


@dataclass
class ModelMesh:
    positions: np.ndarray                 # (N, 3) float32, model space (Hammer units)
    uvs: np.ndarray                       # (N, 2) float32, Blender convention (v up)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)
    triangle_materials: list[int] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)   # material paths (no extension), e.g. models/props_junk/dumpster
    material_dirs: list[str] = field(default_factory=list)


def _cstr(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode("utf-8", "replace")


# ---------------------------------------------------------------- VVD

def read_vvd(data: bytes) -> tuple[np.ndarray, np.ndarray]:
    """LOD-0 vertex positions and UVs, in the order the MDL/VTX expect."""
    ident, version, checksum, num_lods = struct.unpack_from("<4s3i", data, 0)
    if ident != b"IDSV":
        raise ValueError("not a VVD file")
    lod_counts = struct.unpack_from("<8i", data, 16)
    num_fixups, fixup_start, vertex_start, _tangent_start = struct.unpack_from("<4i", data, 48)
    total = lod_counts[0]
    raw = np.frombuffer(data, dtype=np.uint8, count=total * VVD_VERTEX_SIZE, offset=vertex_start)
    raw = raw.reshape(total, VVD_VERTEX_SIZE)
    if num_fixups:
        order = []
        for i in range(num_fixups):
            lod, src, count = struct.unpack_from("<3i", data, fixup_start + i * 12)
            if lod >= 0:   # every fixup applies to LOD 0
                order.extend(range(src, src + count))
        raw = raw[np.array(order, dtype=np.int64)]
    pos = raw[:, 16:28].copy().view("<f4").reshape(-1, 3)
    uv = raw[:, 40:48].copy().view("<f4").reshape(-1, 2).copy()
    uv[:, 1] = 1.0 - uv[:, 1]
    return pos, uv


# ---------------------------------------------------------------- VTX

def _read_strip_groups(vtx: bytes, first: int, n_groups: int, size: int) -> list[int] | None:
    """Triangle list (as origMeshVertIDs) for one mesh's strip groups, or None if the data
    doesn't make sense with this strip-group header size."""
    tris: list[int] = []
    for gi in range(n_groups):
        grp = first + gi * size
        if grp + 24 > len(vtx):
            return None
        nverts, vert_off, nidx, idx_off = struct.unpack_from("<4i", vtx, grp)
        if (nverts < 0 or nidx < 0 or nidx % 3 or grp + vert_off + nverts * 9 > len(vtx)
                or grp + idx_off + nidx * 2 > len(vtx) or vert_off < 0 or idx_off < 0):
            return None
        verts = [struct.unpack_from("<H", vtx, grp + vert_off + v * 9 + 4)[0] for v in range(nverts)]
        idx = struct.unpack_from(f"<{nidx}H", vtx, grp + idx_off)
        if idx and max(idx) >= nverts:
            return None
        tris.extend(verts[i] for i in idx)
    return tris


def read_vtx_indices(vtx: bytes, mdl_version: int) -> list[list[list[int]]]:
    """[bodypart][mesh] -> triangle-list indices into that mesh's VTX vertices, paired with
    origMeshVertID. Returns [bodypart][mesh] = list of origMeshVertID triples (flattened)."""
    version, _cache, _mbs, _mbt, _mbv, _checksum, num_lods, _mat_off, num_bp, bp_off = \
        struct.unpack_from("<iiHHiiiiii", vtx, 0)
    if version != 7:
        raise ValueError(f"unsupported VTX version {version}")
    result = []
    for b in range(num_bp):
        bp = bp_off + b * 8
        num_models, model_off = struct.unpack_from("<ii", vtx, bp)
        meshes_out = []
        if num_models:
            model = bp + model_off                       # first model only
            _nlods, lod_off = struct.unpack_from("<ii", vtx, model)
            lod = model + lod_off                         # LOD 0
            num_meshes, mesh_off, _switch = struct.unpack_from("<iif", vtx, lod)
            for m in range(num_meshes):
                mesh = lod + mesh_off + m * 9
                num_groups, group_off = struct.unpack_from("<ii", vtx, mesh)
                # Strip group headers are 25 bytes, or 33 in newer compiles (two extra ints);
                # the MDL version doesn't reliably say which, so use whichever parses cleanly.
                order = (33, 25) if mdl_version >= 49 else (25, 33)
                tris = None
                for size in order:
                    tris = _read_strip_groups(vtx, mesh + group_off, num_groups, size)
                    if tris is not None:
                        break
                meshes_out.append(tris or [])
        result.append(meshes_out)
    return result


# ---------------------------------------------------------------- MDL

def read_model(mdl: bytes, vvd: bytes, vtx: bytes) -> ModelMesh:
    ident, version = struct.unpack_from("<4si", mdl, 0)
    if ident != b"IDST":
        raise ValueError("not an MDL file")
    num_tex, tex_idx, num_cd, cd_idx = struct.unpack_from("<4i", mdl, 204)
    num_skinref, num_families, skin_idx, num_bp, bp_idx = struct.unpack_from("<5i", mdl, 220)

    textures = []
    for t in range(num_tex):
        base = tex_idx + t * 64
        name_off = struct.unpack_from("<i", mdl, base)[0]
        textures.append(_cstr(mdl, base + name_off).replace("\\", "/"))
    cd_dirs = []
    for c in range(num_cd):
        off = struct.unpack_from("<i", mdl, cd_idx + c * 4)[0]
        cd_dirs.append(_cstr(mdl, off).replace("\\", "/"))
    skin0 = list(struct.unpack_from(f"<{num_skinref}h", mdl, skin_idx)) if num_skinref else []

    positions, uvs = read_vvd(vvd)
    vtx_meshes = read_vtx_indices(vtx, version)

    out = ModelMesh(positions=positions, uvs=uvs, materials=textures, material_dirs=cd_dirs)
    for b in range(num_bp):
        bp = bp_idx + b * 16
        _name, num_models, _base, model_off = struct.unpack_from("<4i", mdl, bp)
        if not num_models or b >= len(vtx_meshes):
            continue
        model = bp + model_off                            # first model of the body part
        num_meshes, mesh_off, _nv, vertex_index = struct.unpack_from("<4i", mdl, model + 72)
        model_first = vertex_index // VVD_VERTEX_SIZE
        for m in range(min(num_meshes, len(vtx_meshes[b]))):
            mesh = model + mesh_off + m * 116
            material, _model_idx, _mnv, vertex_offset = struct.unpack_from("<4i", mdl, mesh)
            tex = skin0[material] if material < len(skin0) else material
            ids = vtx_meshes[b][m]
            first = model_first + vertex_offset
            for i in range(0, len(ids) - 2, 3):
                # VTX winding is clockwise; flip for Blender's counter-clockwise faces
                out.triangles.append((first + ids[i], first + ids[i + 2], first + ids[i + 1]))
                out.triangle_materials.append(tex)
    return out


def stand_upright(mesh: ModelMesh) -> bool:
    """Some character models (Tank, commons, Charger, Spitter) are stored lying along Y in
    their bind pose; the game stands them up through animation, which previews don't play.
    Rotate such meshes +90 degrees about X (Y becomes up) and put their feet at z=0.
    Returns True if the mesh was rotated."""
    used = np.unique(np.array(mesh.triangles, dtype=np.int64)) if mesh.triangles else np.arange(len(mesh.positions))
    p = mesh.positions[used]
    ext = p.max(0) - p.min(0)
    if ext[2] >= 0.6 * ext[1]:
        return False
    x, y, z = mesh.positions[:, 0].copy(), mesh.positions[:, 1].copy(), mesh.positions[:, 2].copy()
    mesh.positions = np.stack([x, -z, y], axis=1).astype(np.float32)
    mesh.positions[:, 2] -= mesh.positions[used, 2].min()
    return True


CHARACTER_DIRS = ("models/infected/", "models/survivors/")


def load_model(content, model_path: str) -> ModelMesh | None:
    """content: vpk.GameContent. model_path like 'models/props_junk/dumpster.mdl'."""
    base = model_path[:-4] if model_path.lower().endswith(".mdl") else model_path
    mdl = content.read(base + ".mdl")
    vvd = content.read(base + ".vvd")
    vtx = content.read(base + ".dx90.vtx") or content.read(base + ".vtx")
    if not (mdl and vvd and vtx):
        return None
    mesh = read_model(mdl, vvd, vtx)
    if model_path.lower().startswith(CHARACTER_DIRS):
        stand_upright(mesh)
    return mesh


def find_material(content, mesh: ModelMesh, texture_name: str) -> str | None:
    """Resolve a model texture name against its $cdmaterials dirs -> material path."""
    name = texture_name.lower()
    for d in mesh.material_dirs or [""]:
        path = (d.strip("/") + "/" + name).strip("/").lower()
        if content.has_material(path):
            return path
    return None
