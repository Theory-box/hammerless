"""Importing Hammer maps (.vmf) and writing them back exactly.

A brush in a .vmf is a list of sides, each a plane (three points), a material and texture axes; its polygons
are each plane cut by all the others. The import builds those polygons (and displacements' surfaces) for
Blender, keeping every side's id. The export starts from the original file: a brush whose faces still lie on
their sides' planes, with the same materials, is written exactly as it was read (texture axes, displacement,
ids and all); a changed face gets a new plane from its polygon and keeps the rest of its side; a new face gets
a side of its own. Entities keep their text where their keys, outputs and placement are unchanged. Everything
else in the file (editor data, visgroups, cameras) is written back as read.

No bpy here: the Blender side (blender/vmfimport.py) makes and reads the objects.
"""
from __future__ import annotations

import math
import re

from .vmf import Block, _plane_points, fmt_vec, parse

Vec3 = tuple[float, float, float]

PLANE_DIST_TOL = 0.05        # a face is on its side's plane within these (finer than Hammer's grid)
PLANE_NORMAL_TOL = 1e-4


# ---------------------------------------------------------------- vectors

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    length = math.sqrt(_dot(a, a))
    return (a[0] / length, a[1] / length, a[2] / length) if length > 0 else (0.0, 0.0, 0.0)


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


_NUM = r"[-+0-9.eE]+"


def parse_plane(text: str) -> tuple[Vec3, Vec3, Vec3]:
    nums = [float(x) for x in re.findall(_NUM, text)]
    if len(nums) < 9:
        raise ValueError(f"bad plane '{text}'")
    return tuple(nums[0:3]), tuple(nums[3:6]), tuple(nums[6:9])


def plane_of(p0: Vec3, p1: Vec3, p2: Vec3) -> tuple[Vec3, float]:
    """Hammer's plane from three points (vbsp's PlaneFromPoints): the normal points out of the brush."""
    n = _norm(_cross(_sub(p0, p1), _sub(p2, p1)))
    return n, _dot(p0, n)


def parse_axis(text: str) -> tuple[Vec3, float, float]:
    """'[x y z shift] scale' -> (axis, shift, scale)."""
    nums = [float(x) for x in re.findall(_NUM, text)]
    if len(nums) < 5:
        raise ValueError(f"bad texture axis '{text}'")
    return (nums[0], nums[1], nums[2]), nums[3], nums[4] or 1.0


# ---------------------------------------------------------------- brush polygons

def _base_winding(n: Vec3, d: float, size: float = 65536.0) -> list[Vec3]:
    up = (0.0, 0.0, 1.0) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = _norm(_cross(up, n))
    v = _cross(n, u)
    c = (n[0] * d, n[1] * d, n[2] * d)
    return [tuple(c[k] + (u[k] * su + v[k] * sv) * size for k in range(3))
            for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def _clip(w: list[Vec3], n: Vec3, d: float, eps: float = 1e-6) -> list[Vec3]:
    """Keep the part of the polygon behind the plane (inside the brush)."""
    out = []
    for i, p in enumerate(w):
        q = w[(i + 1) % len(w)]
        dp, dq = _dot(p, n) - d, _dot(q, n) - d
        if dp <= eps:
            out.append(p)
        if (dp < -eps and dq > eps) or (dp > eps and dq < -eps):
            out.append(_lerp(p, q, dp / (dp - dq)))
    return out


def _dedupe(w: list[Vec3], eps: float = 1e-4) -> list[Vec3]:
    out = []
    for p in w:
        if not out or max(abs(p[k] - out[-1][k]) for k in range(3)) > eps:
            out.append(p)
    if len(out) > 1 and max(abs(out[0][k] - out[-1][k]) for k in range(3)) <= eps:
        out.pop()
    return out


def solid_faces(solid: Block) -> list[tuple[Block, list[Vec3]]]:
    """Each side with its polygon (Hammer units, counter-clockwise seen from outside, as Blender's faces).
    Sides that end up with no area (bevels, duplicates) are left out."""
    sides = solid.blocks("side")
    planes = [plane_of(*parse_plane(s.get("plane", ""))) for s in sides]
    out = []
    for i, (side, (n, d)) in enumerate(zip(sides, planes)):
        w = _base_winding(n, d)
        for j, (n2, d2) in enumerate(planes):
            if j != i and w:
                w = _clip(w, n2, d2)
        w = _dedupe(w)
        if len(w) < 3:
            continue
        # counter-clockwise around the outward normal
        if _dot(_newell(w), n) < 0:
            w.reverse()
        out.append((side, w))
    return out


def _newell(w: list[Vec3]) -> Vec3:
    nx = ny = nz = 0.0
    for i, p in enumerate(w):
        q = w[(i + 1) % len(w)]
        nx += (p[1] - q[1]) * (p[2] + q[2])
        ny += (p[2] - q[2]) * (p[0] + q[0])
        nz += (p[0] - q[0]) * (p[1] + q[1])
    return (nx, ny, nz)


def face_on_side(verts: list[Vec3], side: Block) -> bool:
    """The polygon still lies on the side's plane (and faces the same way)."""
    n0, d0 = plane_of(*parse_plane(side.get("plane", "")))
    n = _norm(_newell(verts))
    if _dot(n, n0) < 1 - PLANE_NORMAL_TOL:
        return False
    return all(abs(_dot(p, n0) - d0) <= PLANE_DIST_TOL for p in verts)


# ---------------------------------------------------------------- displacements

def _rows(block: Block | None, name: str, cols: int, per: int) -> list[list[tuple]]:
    sub = block.blocks(name)[0] if block and block.blocks(name) else None
    rows = []
    for r in range(cols):
        vals = [float(x) for x in (sub.get(f"row{r}", "") if sub else "").split()]
        vals += [0.0] * (cols * per - len(vals))
        rows.append([tuple(vals[c * per:(c + 1) * per]) for c in range(cols)])
    return rows


def disp_surface(side: Block, verts: list[Vec3]) -> tuple[list[list[Vec3]], list[list[float]]] | None:
    """A displacement's surface: rows of points (and their alphas). The face's four corners are taken
    from the one nearest the start position, in the side's winding (as the compiler does)."""
    info = side.blocks("dispinfo")
    if not info or len(verts) != 4:
        return None
    info = info[0]
    power = int(float(info.get("power", "3")))
    cols = (1 << power) + 1
    start = tuple(float(x) for x in re.findall(_NUM, info.get("startposition", "0 0 0"))[:3])
    corners = list(reversed(verts))            # (Hammer's winding: clockwise from outside)
    first = min(range(4), key=lambda i: sum((corners[i][k] - start[k]) ** 2 for k in range(3)))
    c = corners[first:] + corners[:first]
    normals = _rows(info, "normals", cols, 3)
    dists = _rows(info, "distances", cols, 1)
    offsets = _rows(info, "offsets", cols, 3)
    alphas = _rows(info, "alphas", cols, 1)
    elevation = float(info.get("elevation", "0") or 0)
    fn = _norm(_newell(verts))
    grid = []
    for r in range(cols):
        a = _lerp(c[0], c[1], r / (cols - 1))
        b = _lerp(c[3], c[2], r / (cols - 1))
        row = []
        for col in range(cols):
            p = _lerp(a, b, col / (cols - 1))
            nn, dd, oo = normals[r][col], dists[r][col][0], offsets[r][col]
            row.append(tuple(p[k] + nn[k] * dd + oo[k] + fn[k] * elevation for k in range(3)))
        grid.append(row)
    return grid, [[alphas[r][col][0] for col in range(cols)] for r in range(cols)]


# ---------------------------------------------------------------- texture coordinates

def side_uv(side: Block, p: Vec3, width: int, height: int) -> tuple[float, float]:
    """A point's texture coordinate on a side (Blender's: v up)."""
    try:
        ua, ushift, uscale = parse_axis(side.get("uaxis", ""))
        va, vshift, vscale = parse_axis(side.get("vaxis", ""))
    except ValueError:
        return 0.0, 0.0
    u = (_dot(p, ua) / uscale + ushift) / max(width, 1)
    v = (_dot(p, va) / vscale + vshift) / max(height, 1)
    return u, -v


# ---------------------------------------------------------------- the document

class Document:
    """A parsed .vmf: the world, the entities, and the blocks around them, kept in their order."""

    def __init__(self, text: str):
        self.blocks = parse(text)
        self.world = next((b for b in self.blocks if b.name.lower() == "world"), Block("world"))
        self.entities = [b for b in self.blocks if b.name.lower() == "entity"]
        self.solids: dict[int, Block] = {}
        self.sides: dict[int, Block] = {}
        self.ents: dict[int, Block] = {}
        for holder in [self.world] + self.entities:
            for s in holder.blocks("solid"):
                self.solids[_id(s)] = s
                for side in s.blocks("side"):
                    self.sides[_id(side)] = side
        for e in self.entities:
            self.ents[_id(e)] = e

    def max_id(self) -> int:
        best = 0

        def walk(b: Block):
            nonlocal best
            for it in b.items:
                if isinstance(it, Block):
                    walk(it)
                elif it[0].lower() == "id":
                    try:
                        best = max(best, int(float(it[1])))
                    except ValueError:
                        pass
        for b in self.blocks:
            walk(b)
        return best


def _id(b: Block) -> int:
    try:
        return int(float(b.get("id", "0")))
    except ValueError:
        return 0


def map_points(world: Block, entities: list[Block]) -> list[Vec3]:
    """Points spanning a map's brushes and entities (sides' plane points, entity origins): for its bounds."""
    pts: list[Vec3] = []
    for holder in [world] + list(entities):
        for s in holder.blocks("solid"):
            for side in s.blocks("side"):
                try:
                    pts.extend(parse_plane(side.get("plane", "")))
                except ValueError:
                    pass
        if holder is not world and holder.get("origin") is not None:
            nums = [float(x) for x in re.findall(_NUM, holder.get("origin"))[:3]]
            if len(nums) == 3:
                pts.append(tuple(nums))
    return pts


def entity_solids(e: Block) -> list[Block]:
    return e.blocks("solid")


# ---------------------------------------------------------------- writing back

def side_for_face(face_verts: list[Vec3], material: str, original: Block | None, new_id: int,
                  default_side) -> Block:
    """The side a face is written as: the original (exactly) while the face is on its plane with the same
    material; else the original with a new plane (and material); a face with no original: default_side()."""
    if original is not None:
        same_material = (original.get("material", "") or "").lower() == (material or "").lower()
        if same_material and face_on_side(face_verts, original):
            return original
        side = Block("side", list(original.items))
        side.items = [it for it in side.items if not (isinstance(it, Block) and it.name.lower() == "dispinfo")]
        p1, p2, p3 = _plane_points(list(reversed(face_verts)))
        _set(side, "plane", f"({fmt_vec(p1)}) ({fmt_vec(p2)}) ({fmt_vec(p3)})")
        if material:
            _set(side, "material", material.upper())
        return side
    side = default_side()
    _set(side, "id", str(new_id))
    return side


MAX_DISP_LUXELS = 125      # a displacement's lightmap, luxels a side (vbsp can't split one to fit; 126 errors)


def _disp_min_scale(side: Block, verts: list[Vec3]) -> int:
    """The smallest lightmap scale a displacement side's lightmap fits at (its extent along the texture axes)."""
    need = 1
    for key in ("uaxis", "vaxis"):
        try:
            axis = _norm(parse_axis(side.get(key, ""))[0])
        except (ValueError, ZeroDivisionError):
            continue
        d = [_dot(p, axis) for p in verts]
        lo, hi = min(d), max(d)
        s = max(1, math.ceil((hi - lo) / MAX_DISP_LUXELS))
        while math.ceil(hi / s) - math.floor(lo / s) > MAX_DISP_LUXELS:
            s += 1
        need = max(need, s)
    return need


def set_lightmap_scale(solid: Block, scale_of) -> int:
    """Every side's lightmap scale from scale_of(material) (an int, or None: keep the side's own). A displacement
    gets at least the scale its lightmap fits at. Returns how many displacement sides were raised for that."""
    raised = 0
    disp = {id(s): w for s, w in solid_faces(solid) if s.blocks("dispinfo")}
    for side in solid.blocks("side"):
        v = scale_of(side.get("material", ""))
        if not v:
            continue
        v = int(v)
        if id(side) in disp:
            need = _disp_min_scale(side, disp[id(side)])
            if need > v:
                v, raised = need, raised + 1
        _set(side, "lightmapscale", str(v))
    return raised


def _set(b: Block, key: str, value: str) -> None:
    for i, it in enumerate(b.items):
        if isinstance(it, tuple) and it[0].lower() == key.lower():
            b.items[i] = (it[0], value)
            return
    b.items.append((key, value))


def solid_block(original: Block | None, faces: list[tuple[int | None, list[Vec3], str]], next_id,
                default_side) -> Block:
    """A brush as written: the original block when every face is still on its side (same sides, same
    materials); else rebuilt face by face. faces: (side id or None, polygon, material)."""
    sides = {_id(s): s for s in original.blocks("side")} if original is not None else {}
    if original is not None:
        kept = [sides.get(sid) for sid, _v, _m in faces]
        if (len(faces) == len(_area_sides(original)) and all(kept)
                and all(side_for_face(v, m, s, 0, default_side) is s for (sid, v, m), s in zip(faces, kept))):
            return original
    out = Block("solid")
    out.kv("id", _id(original) if original is not None else next_id())
    used = set()
    for sid, verts, material in faces:
        orig = sides.get(sid) if sid not in used else None
        used.add(sid)
        out.add(side_for_face(verts, material, orig, next_id(), lambda v=verts, m=material: default_side(v, m)))
    for b in (original.blocks("editor") if original is not None else []):
        out.add(b)
    return out


def _area_sides(solid: Block) -> list[Block]:
    return [s for s, _w in solid_faces(solid)]


def set_entity_values(original: Block, values: dict[str, str], outputs: list[tuple[str, str]] | None) -> Block:
    """An entity with keys (and outputs) as given: keys in their original places, new ones after, missing
    ones dropped; the original block when nothing changed. Solids are left as they are (see with_solids)."""
    orig_values = {it[0].lower(): it[1] for it in original.items if isinstance(it, tuple)}
    orig_outputs = [it for c in original.blocks("connections") for it in c.items if isinstance(it, tuple)]
    if ({k.lower(): v for k, v in values.items()} == {k: v for k, v in orig_values.items() if k != "id"}
            and (outputs is None or outputs == orig_outputs)):
        return original
    out = Block(original.name)
    lower = {k.lower(): k for k in values}
    done = set()
    for it in original.items:
        if isinstance(it, tuple):
            key = it[0].lower()
            if key == "id":
                out.items.append(it)
            elif key in lower:
                out.items.append((it[0], values[lower[key]]))
                done.add(key)
        elif it.name.lower() == "connections" and outputs is not None:
            continue
        else:
            out.items.append(it)
    for k, v in values.items():
        if k.lower() not in done:
            # (before the entity's blocks, as Hammer writes keys first)
            at = next((i for i, it in enumerate(out.items) if isinstance(it, Block)), len(out.items))
            out.items.insert(at, (k, v))
    if outputs:
        conn = Block("connections", list(outputs))
        at = next((i for i, it in enumerate(out.items) if isinstance(it, Block)), len(out.items))
        out.items.insert(at, conn)
    return out


def with_solids(entity: Block, solids: list[Block]) -> Block:
    """The entity block with these solids in place of its own (in their order, where the first one was)."""
    old = entity.blocks("solid")
    if len(old) == len(solids) and all(a is b for a, b in zip(old, solids)):
        return entity
    out = Block(entity.name)
    placed = False
    for it in entity.items:
        if isinstance(it, Block) and it.name.lower() == "solid":
            if not placed:
                out.items.extend(solids)
                placed = True
            continue
        out.items.append(it)
    if not placed:
        at = next((i for i, it in enumerate(out.items) if isinstance(it, Block) and it.name.lower() == "editor"),
                  len(out.items))
        out.items[at:at] = solids
    return out


def write_document(doc: Document, world: Block, entities: list[Block | None], new_entities: list[Block] = ()) -> str:
    """The file: the original's blocks in their order (hidden ones too, where they were), the world as given,
    each original entity replaced where it stands by entities[i] (None: deleted), new entities after the last
    original one."""
    out: list[str] = []
    k = 0
    last = max((i for i, b in enumerate(doc.blocks) if b.name.lower() == "entity"), default=None)
    for i, b in enumerate(doc.blocks):
        n = b.name.lower()
        if n == "world":
            world.write(out)
        elif n == "entity":
            e = entities[k] if k < len(entities) else b
            k += 1
            if e is not None:
                e.write(out)
        else:
            if last is None and n in ("cameras", "cordon", "cordons"):
                for e in new_entities:
                    e.write(out)
                new_entities = ()
            b.write(out)
        if i == last:
            for e in new_entities:
                e.write(out)
            new_entities = ()
    for e in new_entities:
        e.write(out)
    return "".join(out)
