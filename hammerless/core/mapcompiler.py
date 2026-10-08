"""Hammerless's own map compiler (native/hlvbsp, a drop-in for vbsp.exe): the parts that need the
game's files. The game's materials live in VPKs, so here we work out, for every material a .vmf uses,
what vbsp would read from it, and write that as a table for hlvbsp:

    name \\t contents \\t surface flags \\t width \\t height \\t reflectivity r g b \\t surfaceprop \\t found

The rules follow vbsp's FindMiptex (Quake 2's texture flags, Valve's %compile keys): tool textures
(sky, nodraw, clip, hint, skip, trigger, ...), water and slime, see-through materials (window or
grate contents), and whether the shader needs a lightmap (bumped or not).
"""
from __future__ import annotations

import os
import re
import struct

from .gamematerials import read_texture_bytes, read_vmt

CONTENTS_SOLID = 0x1
CONTENTS_WINDOW = 0x2
CONTENTS_GRATE = 0x8
CONTENTS_SLIME = 0x10
CONTENTS_WATER = 0x20
CONTENTS_BLOCKLOS = 0x40
CONTENTS_OPAQUE = 0x80
CONTENTS_TEAM1 = 0x800
CONTENTS_TEAM2 = 0x1000
CONTENTS_PLAYERCLIP = 0x10000
CONTENTS_MONSTERCLIP = 0x20000
CONTENTS_ORIGIN = 0x1000000
CONTENTS_DETAIL = 0x8000000
CONTENTS_LADDER = 0x20000000

SURF_SKY2D = 0x2
SURF_SKY = 0x4
SURF_WARP = 0x8
SURF_TRANS = 0x10
SURF_NOPORTAL = 0x20
SURF_TRIGGER = 0x40
SURF_NODRAW = 0x80
SURF_HINT = 0x100
SURF_SKIP = 0x200
SURF_NOLIGHT = 0x400
SURF_BUMPLIGHT = 0x800
SURF_NOSHADOWS = 0x1000
SURF_NODECALS = 0x2000
SURF_NOCHOP = 0x4000

# shaders whose surfaces get a lightmap (L4D2's Water shader does: measured)
LIGHTMAPPED = {"water", "lightmappedgeneric", "worldvertextransition", "lightmapped_4wayblend", "lightmappedreflective",
               "lightmappedtwotexture", "worldtwotextureblend", "lightmappedgeneric_dx9", "worldvertextransition_dx9"}
BUMPED = {"lightmappedgeneric", "worldvertextransition", "lightmapped_4wayblend", "lightmappedgeneric_dx9",
          "worldvertextransition_dx9"}


def _true(v: str | None) -> bool:
    return v is not None and v.strip().lower() in ("1", "true")


def _texture_info(content, texture: str | None, game_dir: str | None) -> tuple[int, int, tuple[float, float, float]] | None:
    """Width, height and the reflectivity stored in a texture's VTF header."""
    if not texture:
        return None
    data = read_texture_bytes(content, texture.lower().replace("\\", "/"), game_dir)
    if not data or len(data) < 44 or data[:4] != b"VTF\0":
        return None
    w, h = struct.unpack_from("<HH", data, 16)
    refl = struct.unpack_from("<3f", data, 32)
    return w, h, refl


def material_row(content, name: str, game_dir: str | None, surfaceprops: dict[str, int]) -> str:
    p = read_vmt(content, name.lower().replace("\\", "/"), game_dir)
    found = 1 if p else 0
    contents = flags = 0
    width = height = 0
    refl = (0.0, 0.0, 0.0)
    surfaceprop = surfaceprop2 = detailtype = bottom = "-"
    if p:
        g = p.get
        shader = g("shader", "")
        if _true(g("%compilesky")):
            flags |= SURF_SKY | SURF_NOLIGHT
        elif _true(g("%compile2dsky")):
            flags |= SURF_SKY | SURF_SKY2D | SURF_NOLIGHT
        elif _true(g("%compilehint")):
            flags |= SURF_NODRAW | SURF_NOLIGHT | SURF_HINT
        elif _true(g("%compileskip")):
            flags |= SURF_NODRAW | SURF_NOLIGHT | SURF_SKIP
        elif _true(g("%compileorigin")):
            contents |= CONTENTS_ORIGIN | CONTENTS_DETAIL
            flags |= SURF_NODRAW | SURF_NOLIGHT
        elif _true(g("%compileclip")):
            contents |= CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP
            flags |= SURF_NODRAW | SURF_NOLIGHT
        elif _true(g("%playerclip")):
            contents |= CONTENTS_PLAYERCLIP
            flags |= SURF_NODRAW | SURF_NOLIGHT
        elif _true(g("%compilenpcclip")):
            contents |= CONTENTS_MONSTERCLIP
            flags |= SURF_NODRAW | SURF_NOLIGHT
        elif _true(g("%compilenochop")):
            flags |= SURF_NOCHOP
        elif _true(g("%compiletrigger")):
            flags |= SURF_NOLIGHT | SURF_TRIGGER
        elif _true(g("%compilenolight")) and not _true(g("%compilewater")):
            flags |= SURF_NOLIGHT
        else:
            if _true(g("%compileladder")):
                contents |= CONTENTS_LADDER
            team = (g("%compileteam") or "").strip()
            if team[:1].isdigit() and int(team.split()[0]) in (1, 2):
                contents |= CONTENTS_TEAM1 if int(team.split()[0]) == 1 else CONTENTS_TEAM2
            if _true(g("%noportal")):
                flags |= SURF_NOPORTAL
            if _true(g("%hotsurface")):          # (L4D2's vbsp marks these like %noPortal)
                flags |= SURF_NOPORTAL
            if _true(g("%compilepassbullets")):
                contents &= ~CONTENTS_SOLID
                contents |= CONTENTS_GRATE
            if shader in BUMPED and g("$bumpmap"):
                flags |= SURF_BUMPLIGHT
            if shader in LIGHTMAPPED:
                flags &= ~SURF_NOLIGHT
            else:
                flags |= SURF_NOLIGHT
            if _true(g("%compilenodraw")):
                flags |= SURF_NODRAW | SURF_NOLIGHT
            if _true(g("%compileinvisible")):
                contents &= ~CONTENTS_SOLID
                contents |= CONTENTS_GRATE
                flags |= SURF_NODRAW | SURF_NOLIGHT
            check_window = True
            if _true(g("%compilenonsolid")):
                contents = CONTENTS_OPAQUE
                check_window = False
            if _true(g("%compileblocklos")):
                contents = CONTENTS_BLOCKLOS
                check_window = False
            if _true(g("%compiledetail")):
                contents |= CONTENTS_DETAIL
            keep_light = _true(g("%compilekeeplight"))
            if _true(g("%compilewater")):
                contents &= ~(CONTENTS_SOLID | CONTENTS_DETAIL)
                contents |= CONTENTS_WATER
                flags |= SURF_WARP | SURF_NOSHADOWS | SURF_NODECALS
            if _true(g("%compilenoshadows")):
                flags |= SURF_NOSHADOWS
            if shader.startswith("unlitgeneric"):       # (L4D2's vbsp no longer forces water unlit)
                flags |= SURF_NOLIGHT
            if _true(g("%compileslime")):
                contents &= ~(CONTENTS_SOLID | CONTENTS_DETAIL)
                contents |= CONTENTS_SLIME
                flags |= SURF_NODECALS
            translucent = _true(g("$translucent")) or _true(g("$additive"))
            alphatest = _true(g("$alphatest"))
            if check_window and (translucent or alphatest):
                if not contents & (CONTENTS_GRATE | CONTENTS_WATER):
                    contents |= CONTENTS_WINDOW
                contents &= ~CONTENTS_SOLID
                if translucent:
                    flags |= SURF_TRANS
            if flags & SURF_NOLIGHT:
                flags &= ~SURF_BUMPLIGHT
        info = _texture_info(content, g("$basetexture"), game_dir)
        if info:
            width, height, refl = info
        else:
            # no base texture: the size of the tool texture, the material system's default reflectivity
            refl = (0.2, 0.2, 0.2)
            tool = _texture_info(content, g("%tooltexture"), game_dir)
            if tool:
                width, height = tool[0], tool[1]
        surfaceprop = _name(g("$surfaceprop"))
        surfaceprop2 = _name(g("$surfaceprop2"))
        detailtype = _name(g("%detailtype"))
        bottom = _name(g("$bottommaterial"))
    clean = name.replace("\t", " ").replace("\n", " ")
    return (f"{clean}\t{contents}\t{flags}\t{width}\t{height}\t{refl[0]!r}\t{refl[1]!r}\t{refl[2]!r}"
            f"\t{surfaceprop}\t{found}\t{surfaceprop2}\t{detailtype}\t{bottom}")


def _name(v: str | None) -> str:
    """A surface property name for the table: one word, '-' for none."""
    v = (v or "").strip()
    return v.split()[0] if v else "-"


def vmf_materials(vmf_text: str) -> list[str]:
    """Every material the .vmf's brush sides use (first spelling of each, in order)."""
    seen: dict[str, str] = {}
    for m in re.finditer(r'"material"\s+"([^"]*)"', vmf_text):
        seen.setdefault(m.group(1).lower(), m.group(1))
    return list(seen.values())


def write_cubemap_materials(path: str, vmf_path: str, content, game_dir: str | None) -> int:
    """For env_cubemap: which materials are specular and their patch .vmt templates (core/cubemappatch)."""
    from .cubemappatch import write_cubemap_table
    from .gamematerials import _read_text
    with open(vmf_path, encoding="utf-8", errors="replace") as f:
        names = vmf_materials(f.read())
    return write_cubemap_table(path, names, lambda n: _read_text(content, f"materials/{n}.vmt", game_dir))


def write_surfaceprops(path: str, content) -> int:
    """The game's surface property scripts (in the manifest's order) for hlvbsp to hand to vphysics:
    blocks of name \\n byte count \\n text."""
    manifest = content.read("scripts/surfaceproperties_manifest.txt") if content else None
    names = re.findall(r'"file"\s+"([^"]+)"', manifest.decode("utf-8", "replace")) if manifest else []
    n = 0
    with open(path, "wb") as f:
        for name in names:
            data = content.read(name)
            if data is None:
                continue
            f.write(name.encode("latin-1") + b"\n" + str(len(data)).encode() + b"\n" + data)
            n += 1
    return n


STUDIOHDR_FLAGS_STATIC_PROP = 0x10


def _model_bytes(content, path: str, game_dir: str | None) -> bytes | None:
    rel = path.replace("\\", "/").lower()
    if game_dir:
        full = os.path.join(game_dir, *rel.split("/"))
        if os.path.isfile(full):
            with open(full, "rb") as f:
                return f.read()
    return content.read(rel) if content else None


def prop_model_record(content, model: str, game_dir: str | None) -> list[str]:
    """What vbsp reads from a prop_static's model: whether it may be a static prop, and each mesh's
    vertices (all body parts and models; the .vvd's vertices as stored, without LOD fixups, as
    vbsp reads them) for the hull it tests leaves against."""
    mdl = _model_bytes(content, model, game_dir)
    if not mdl or mdl[:4] not in (b"IDST", b"IDAG"):
        return [f"model {model} missing"]
    version = struct.unpack_from("<i", mdl, 4)[0]
    flags = struct.unpack_from("<i", mdl, 152)[0]
    if not flags & STUDIOHDR_FLAGS_STATIC_PROP:
        return [f"model {model} notstatic"]
    kv_index, kv_size = struct.unpack_from("<ii", mdl, 312)
    keyvalues = mdl[kv_index:kv_index + kv_size].decode("latin-1", "replace") if kv_size > 0 else ""
    m = re.search(r'prop_data\s*\{([^}]*)\}', keyvalues, re.IGNORECASE)
    if m:
        allow = re.search(r'"?allowstatic"?\s+"?([-\d.]+)', m.group(1), re.IGNORECASE)
        if not allow or int(float(allow.group(1))) == 0:
            return [f"model {model} dynamic"]
    vvd = _model_bytes(content, os.path.splitext(model)[0] + ".vvd", game_dir)
    if not vvd or vvd[:4] != b"IDSV":
        return [f"model {model} missing"]
    vertex_start, tangent_start = struct.unpack_from("<ii", vvd, 56)
    raw_count = (tangent_start - vertex_start) // 48 if tangent_start > vertex_start else (len(vvd) - vertex_start) // 48
    num_bp, bp_idx = struct.unpack_from("<ii", mdl, 232)
    lines = [f"model {model} ok"]
    for b in range(num_bp):
        bp = bp_idx + b * 16
        _name, num_models, _base, model_off = struct.unpack_from("<4i", mdl, bp)
        for k in range(num_models):
            sub = bp + model_off + k * 148
            num_meshes, mesh_off, _nv, vertex_index = struct.unpack_from("<4i", mdl, sub + 72)
            first = vertex_index // 48
            for mm in range(num_meshes):
                mesh = sub + mesh_off + mm * 116
                _material, _model_idx, nverts, vertex_offset = struct.unpack_from("<4i", mdl, mesh)
                pts = []
                for i in range(nverts):
                    v = first + vertex_offset + i
                    if v >= raw_count:
                        break
                    pts.extend(struct.unpack_from("<3f", vvd, vertex_start + 48 * v + 16))
                lines.append(f"mesh {len(pts) // 3} " + " ".join(repr(x) for x in pts))
    del version
    return lines


def vmf_static_prop_models(vmf_text: str) -> list[str]:
    """The models of the .vmf's prop_static entities (first spelling of each, in order)."""
    from .vmf import parse
    seen: dict[str, str] = {}
    for block in parse(vmf_text):
        if block.name.lower() != "entity" or (block.get("classname") or "") not in ("prop_static", "static_prop"):
            continue
        model = block.get("model")
        if model:
            seen.setdefault(model.lower().replace("\\", "/"), model)
    return list(seen.values())


def detail_vbsp_name(vmf_text: str) -> str:
    """The detail kinds file the map names (worldspawn detailvbsp), as vbsp picks it."""
    m = re.search(r'"detailvbsp"\s+"([^"]*)"', vmf_text)
    return m.group(1) if m and m.group(1) else "detail.vbsp"


def detail_models(detail_text: str, vmf_text: str) -> list[str]:
    """Models detail props may use: the detail kinds' "model" keys and prop_detail entities."""
    from .vmf import parse
    names = re.findall(r'"model"\s+"([^"]+)"', detail_text)
    for b in parse(vmf_text):
        if b.name.lower() == "entity" and (b.get("classname") or "") in ("prop_detail", "detail_prop") and b.get("model"):
            names.append(b.get("model"))
    return names


def write_detail_file(path: str, vmf_path: str, content, game_dir: str | None) -> str:
    """The game's detail kinds (detail.vbsp, or the map's own choice) for hlvbsp; returns its text."""
    with open(vmf_path, encoding="utf-8", errors="replace") as f:
        name = detail_vbsp_name(f.read())
    data = _model_bytes(content, name, game_dir) or b""
    with open(path, "wb") as f:
        f.write(data)
    return data.decode("latin-1")


def write_prop_table(path: str, vmf_path: str, content, game_dir: str | None, detail_text: str = "") -> int:
    with open(vmf_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    seen: dict[str, str] = {}
    for model in vmf_static_prop_models(text) + detail_models(detail_text, text):
        seen.setdefault(model.lower().replace("\\", "/"), model)
    models = list(seen.values())
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for model in models:
            f.write("\n".join(prop_model_record(content, model, game_dir)) + "\n")
    return len(models)


def write_material_table(path: str, vmf_path: str, content, game_dir: str | None) -> int:
    with open(vmf_path, encoding="utf-8", errors="replace") as f:
        names = vmf_materials(f.read())
    surfaceprops: dict[str, int] = {}
    rows = [material_row(content, n, game_dir, surfaceprops) for n in names]
    # water materials' $bottommaterial: the underside of the water uses it
    known = {n.lower() for n in names}
    for row in list(rows):
        bottom = row.split("\t")[12]
        if bottom != "-" and bottom.lower() not in known:
            known.add(bottom.lower())
            rows.append(material_row(content, bottom, game_dir, surfaceprops))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(rows) + "\n")
    return len(rows)


# what hlvbsp doesn't do yet: maps with these are compiled by Valve's vbsp
UNSUPPORTED_CLASSES = {
    "info_overlay_transition": "water overlays",
    "func_occluder": "occluders", "func_viscluster": "vis clusters",
    "sky_camera": "3D skyboxes", "func_instance": "instances", "info_no_dynamic_shadow": "shadow blockers",
}


def unsupported(vmf_text: str) -> list[str]:
    """Reasons hlvbsp can't compile this map exactly like vbsp yet (empty: it can)."""
    from .vmf import parse
    why: list[str] = []

    def add(reason: str) -> None:
        if reason not in why:
            why.append(reason)
    for b in parse(vmf_text):
        if b.name.lower() == "entity":
            cls = (b.get("classname") or "").lower()
            if cls in UNSUPPORTED_CLASSES:
                add(UNSUPPORTED_CLASSES[cls])
    return why
