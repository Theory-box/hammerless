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

# shaders whose surfaces get a lightmap
LIGHTMAPPED = {"lightmappedgeneric", "worldvertextransition", "lightmapped_4wayblend", "lightmappedreflective",
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
    surfaceprop = -1
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
            if _true(g("%noportal")):
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
            if (not keep_light and shader.startswith("water")) or shader.startswith("unlitgeneric"):
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
        prop = (g("$surfaceprop") or "").lower()
        if prop:
            surfaceprop = surfaceprops.setdefault(prop, len(surfaceprops))
    clean = name.replace("\t", " ").replace("\n", " ")
    return (f"{clean}\t{contents}\t{flags}\t{width}\t{height}\t{refl[0]!r}\t{refl[1]!r}\t{refl[2]!r}"
            f"\t{surfaceprop}\t{found}")


def vmf_materials(vmf_text: str) -> list[str]:
    """Every material the .vmf's brush sides use (first spelling of each, in order)."""
    seen: dict[str, str] = {}
    for m in re.finditer(r'"material"\s+"([^"]*)"', vmf_text):
        seen.setdefault(m.group(1).lower(), m.group(1))
    return list(seen.values())


def write_material_table(path: str, vmf_path: str, content, game_dir: str | None) -> int:
    with open(vmf_path, encoding="utf-8", errors="replace") as f:
        names = vmf_materials(f.read())
    surfaceprops: dict[str, int] = {}
    rows = [material_row(content, n, game_dir, surfaceprops) for n in names]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(rows) + "\n")
    return len(rows)
