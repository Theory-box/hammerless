"""Checks on a compiled map (.bsp) that catch problems the compilers only hint at.

A lit face folded to zero area (where two brushes almost, but not quite, line up) makes
vrad's full-quality bounce lighting run away: every bounce adds the same small amount
until it gives up at 100, and the baked lightmaps come out black ("it's night in game").
vrad's log only says "zero area child patch", with no location. Here we find the face in
the BSP and name the Blender objects around it.
"""
from __future__ import annotations

import json
import os
import re
import struct

FACE_SIZE = 56
LIGHTING_RUNAWAY = "Bounce #100"


def _lump(data: bytes, index: int) -> bytes:
    _version, offset, length, _fourcc = struct.unpack_from("<4i", data, 8 + 16 * index)   # L4D2 lump_t order
    return data[offset:offset + length]


def folded_lit_faces(bsp_path: str, max_area: float = 0.01) -> list[tuple[tuple[float, float, float], float]]:
    """(centre, stored area) of lightmapped faces whose corners enclose (almost) no area."""
    with open(bsp_path, "rb") as f:
        data = f.read()
    verts = _lump(data, 3)
    edges = _lump(data, 12)
    surfedges = _lump(data, 13)
    faces = _lump(data, 7)
    found = []
    for k in range(len(faces) // FACE_SIZE):
        face = faces[FACE_SIZE * k: FACE_SIZE * (k + 1)]
        first_edge, num_edges = struct.unpack_from("<ih", face, 4)
        light_ofs = struct.unpack_from("<i", face, 20)[0]
        if light_ofs < 0 or face[16] == 255 or num_edges < 3:
            continue
        pts = []
        for i in range(first_edge, first_edge + num_edges):
            se = struct.unpack_from("<i", surfedges, 4 * i)[0]
            v0, v1 = struct.unpack_from("<2H", edges, 4 * abs(se))
            pts.append(struct.unpack_from("<3f", verts, 12 * (v0 if se >= 0 else v1)))
        ax = ay = az = 0.0
        for i in range(1, len(pts) - 1):
            ux, uy, uz = (pts[i][j] - pts[0][j] for j in range(3))
            vx, vy, vz = (pts[i + 1][j] - pts[0][j] for j in range(3))
            ax += uy * vz - uz * vy
            ay += uz * vx - ux * vz
            az += ux * vy - uy * vx
        area = 0.5 * (ax * ax + ay * ay + az * az) ** 0.5
        if area < max_area:
            centre = tuple(sum(p[j] for p in pts) / len(pts) for j in range(3))
            found.append((centre, struct.unpack_from("<f", face, 24)[0]))
    return found


def objects_near(vmf_path: str, point, pad: float = 1.0) -> list[str]:
    """Blender objects whose exported brushes' bounds contain the point (brushes.json names them)."""
    try:
        with open(os.path.splitext(vmf_path)[0] + ".brushes.json", encoding="utf-8") as f:
            sources = json.load(f)
        with open(vmf_path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, ValueError):
        return []
    names = []
    for m in re.finditer(r'\n\tsolid\n\t\{\n\t\t"id" "(\d+)"(.*?)\n\t\}', text, re.S):
        pts = [tuple(float(v) for v in t) for t in re.findall(r"\(([-\d.e]+) ([-\d.e]+) ([-\d.e]+)\)", m.group(2))]
        if pts and all(min(p[i] for p in pts) - pad <= point[i] <= max(p[i] for p in pts) + pad for i in range(3)):
            name = sources.get(m.group(1))
            if name and name not in names:
                names.append(name)
    return names


def lighting_problems(bsp_path: str, vmf_path: str, log_text: str) -> list[tuple[str, tuple | None, str]]:
    """(message, location in Hammer units or None, object to select) for the problem list."""
    out = []
    runaway = LIGHTING_RUNAWAY in log_text
    for centre, _stored in folded_lit_faces(bsp_path):
        names = objects_near(vmf_path, centre)
        who = " and ".join(f"'{n}'" for n in names) or "two brushes"
        out.append((f"Lighting: {who} almost line up at the marker, leaving a folded sliver face "
                    f"({'the lighting failed because of it' if runaway else 'it can break the lighting'}). "
                    "Make their edges or tops meet exactly (snap the vertices), or move them clearly apart",
                    centre, names[0] if names else ""))
    if runaway and not out:
        out.append(("Lighting failed: vrad's light bounces never settled, so the baked light is black. Usually "
                    "two brushes that almost line up; the Fast quality preset avoids it for now", None, ""))
    return out
