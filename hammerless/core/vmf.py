"""VMF (Hammer map) writer and a tiny reader used by tests.

A VMF is nested KeyValues text:  name { "key" "value"  child { ... } }
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import geometry as g
from .ir import Brush, Entity, Polygon, Vec3


def fmt(x: float) -> str:
    s = f"{round(x, 3):.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def fmt_vec(v: Vec3) -> str:
    return " ".join(fmt(c) for c in v)


# ---------------------------------------------------------------- KV tree

@dataclass
class Block:
    name: str
    items: list = field(default_factory=list)  # (key, value) tuples or Blocks

    def kv(self, key: str, value) -> "Block":
        self.items.append((key, str(value)))
        return self

    def add(self, block: "Block") -> "Block":
        self.items.append(block)
        return block

    # reader helpers
    def get(self, key: str, default=None):
        for it in self.items:
            if isinstance(it, tuple) and it[0].lower() == key.lower():
                return it[1]
        return default

    def blocks(self, name: str) -> list["Block"]:
        return [b for b in self.items if isinstance(b, Block) and b.name.lower() == name.lower()]

    def write(self, out: list[str], depth: int = 0) -> None:
        ind = "\t" * depth
        out.append(f"{ind}{self.name}\n{ind}{{\n")
        for it in self.items:
            if isinstance(it, Block):
                it.write(out, depth + 1)
            else:
                k, v = it
                k = k.replace('"', "'").replace("\n", " ").replace("\r", " ")
                v = v.replace('"', "'").replace("\n", " ").replace("\r", " ")
                out.append(f'{ind}\t"{k}" "{v}"\n')
        out.append(f"{ind}}}\n")


def _plane_points(r):
    """Three of a face's points, in winding order, spanning the largest triangle: a face with extra
    points along one straight edge would otherwise give three points on a line (no plane)."""
    n = len(r)
    if n <= 3:
        return r[0], r[1 % n], r[2 % n]

    def area(a, b, c):
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        return ((u[1] * v[2] - u[2] * v[1]) ** 2 + (u[2] * v[0] - u[0] * v[2]) ** 2 + (u[0] * v[1] - u[1] * v[0]) ** 2)
    if n <= 24:
        best = max(((i, j, k) for i in range(n) for j in range(i + 1, n) for k in range(j + 1, n)),
                   key=lambda t: area(r[t[0]], r[t[1]], r[t[2]]))
    else:
        best = (0, n // 3, (2 * n) // 3)
    return tuple(r[i] for i in best)


def parse(text: str) -> list[Block]:
    """Parse VMF/KeyValues text into Blocks (enough for tests and tools)."""
    tokens = re.findall(r'"[^"]*"|\{|\}|[^\s{}"]+', re.sub(r"//[^\n]*", "", text))
    root = Block("root")
    stack = [root]
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "}":
            stack.pop()
            i += 1
        elif i + 1 < len(tokens) and tokens[i + 1] == "{":
            stack.append(stack[-1].add(Block(t.strip('"'))))
            i += 2
        else:
            stack[-1].kv(t.strip('"'), tokens[i + 1].strip('"'))
            i += 2
    return [b for b in root.items if isinstance(b, Block)]


# ---------------------------------------------------------------- writer

class VMFWriter:
    def __init__(self):
        self._next_id = 1
        self.solid_sources: dict[int, str] = {}   # solid id -> Blender object (compiler messages use ids)

    def new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    # -- sides & solids
    def side(self, face: Polygon, dispinfo: Block | None = None) -> Block:
        # Blender winding is CCW from outside; Hammer wants clockwise -> reverse.
        r = list(reversed(face.verts))
        p1, p2, p3 = _plane_points(r)
        normal = g.polygon_normal(face.verts)
        u, v = g.world_texture_axes(normal)
        s = Block("side")
        s.kv("id", self.new_id())
        s.kv("plane", f"({fmt_vec(p1)}) ({fmt_vec(p2)}) ({fmt_vec(p3)})")
        s.kv("material", face.material.upper())
        s.kv("uaxis", f"[{fmt_vec(u)} 0] {fmt(face.texture_scale)}")
        s.kv("vaxis", f"[{fmt_vec(v)} 0] {fmt(face.texture_scale)}")
        s.kv("rotation", 0)
        s.kv("lightmapscale", face.lightmap_scale)
        s.kv("smoothing_groups", 0)
        if dispinfo is not None:
            s.add(dispinfo)
        return s

    def solid(self, brush: Brush, dispinfos: dict[int, Block] | None = None) -> Block:
        """dispinfos maps face index (in brush.faces) -> dispinfo block."""
        sb = Block("solid")
        sid = self.new_id()
        sb.kv("id", sid)
        if brush.source:
            self.solid_sources[sid] = brush.source
        dispinfos = dispinfos or {}
        if dispinfos:
            faces = list(enumerate(brush.faces))  # displacement brushes are exact boxes
        else:
            merged = g.merge_coplanar(brush.faces)
            faces = [(None, f) for f in merged]
        for idx, face in faces:
            sb.add(self.side(face, dispinfos.get(idx)))
        ed = sb.add(Block("editor"))
        ed.kv("color", "0 180 255").kv("visgroupshown", 1).kv("visgroupautoshown", 1)
        return sb

    # -- entities
    def entity(self, ent: Entity, solids: list[Block] | None = None) -> Block:
        e = Block("entity")
        e.kv("id", self.new_id())
        e.kv("classname", ent.classname)
        if ent.origin is not None:
            e.kv("origin", fmt_vec(ent.origin))
        if any(ent.angles) or ent.origin is not None:
            e.kv("angles", fmt_vec(ent.angles))
        for k, v in ent.keyvalues.items():
            if k in ("classname", "origin", "angles", "id"):
                continue
            e.kv(k, v)
        if ent.outputs:
            conn = e.add(Block("connections"))
            for o in ent.outputs:
                conn.kv(o.output, o.vmf_value())
        for s in solids or []:
            e.add(s)
        ed = e.add(Block("editor"))
        ed.kv("color", "220 30 220").kv("visgroupshown", 1).kv("visgroupautoshown", 1)
        ed.kv("logicalpos", "[0 0]")
        return e

    def document(self, world: Block, entities: list[Block]) -> str:
        out: list[str] = []
        vi = Block("versioninfo")
        vi.kv("editorversion", 400).kv("editorbuild", 6157).kv("mapversion", 1)
        vi.kv("formatversion", 100).kv("prefab", 0)
        vi.write(out)
        Block("visgroups").write(out)
        vs = Block("viewsettings")
        vs.kv("bSnapToGrid", 1).kv("bShowGrid", 1).kv("bShowLogicalGrid", 0)
        vs.kv("nGridSpacing", 16).kv("bShow3DGrid", 0)
        vs.write(out)
        world.write(out)
        for e in entities:
            e.write(out)
        Block("cameras").kv("activecamera", -1).write(out)
        c = Block("cordon")
        c.kv("mins", "(-1024 -1024 -1024)").kv("maxs", "(1024 1024 1024)").kv("active", 0)
        c.write(out)
        return "".join(out)
