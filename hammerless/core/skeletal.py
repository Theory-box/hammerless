"""Animated (skeletal) Custom Models: an armature's bones, the mesh's vertex weights and the armature's actions
as Valve's SMD text, and the QC that tells studiomdl to make them one model with an animation (sequence) per
action. models.py compiles it the same way as a static prop (the game's studiomdl); a ModelSpec with bones is
skeletal.

Conventions (SMD, as studiomdl reads it):
- skeleton frames: per bone, its position and rotation relative to its parent bone (roots: to the model), the
  rotation as Euler angles X, Y, Z in radians, applied X first, then Y, then Z (the matrix is Rz * Ry * Rx, the
  same as Blender's Euler 'XYZ').
- triangle vertices are in model space at the rest pose, each with up to three bone weights.
- studiomdl turns everything it reads 90 degrees about Z (models.py measured it on vertices: (x, y) -> (-y, x)).
  So that the model's own axes are Blender's, vertices are written turned the other way, and so are the root
  bones (their position turned, their rotation turned first): child bones are relative to their parent, so the
  turn reaches them through it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
MAX_WEIGHTS = 3            # bones per vertex studiomdl keeps
MAX_BONES = 128            # bones per model (MAXSTUDIOBONES)


@dataclass
class Bone:
    name: str
    parent: int                     # index of the parent bone, -1 for a root
    pos: Vec3                       # rest position, relative to the parent (Hammer units)
    rot: Vec3                       # rest rotation relative to the parent, Euler XYZ radians


@dataclass
class Animation:
    name: str
    frames: list[list[tuple[Vec3, Vec3]]]     # per frame, per bone: (position, Euler XYZ rotation), like Bone
    fps: float = 30.0
    loop: bool = False
    activity: str = ""                         # e.g. ACT_IDLE (optional)


@dataclass
class Attachment:
    name: str
    bone: int
    pos: Vec3                       # relative to the bone
    rot: Vec3 = (0.0, 0.0, 0.0)     # Euler XYZ radians, relative to the bone


@dataclass
class Skeleton:
    bones: list[Bone]
    weights: list[tuple[list[tuple[int, float]], ...]]   # per triangle of the model: per vertex [(bone, weight)]
    animations: list[Animation] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)


# ------------------------------------------------------------------ rotations (no mathutils: core is plain Python)

def euler_to_matrix(e: Vec3) -> list[list[float]]:
    """Rz * Ry * Rx (X applied first)."""
    x, y, z = e
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return [[cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
            [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
            [-sy, sx * cy, cx * cy]]


def matrix_to_euler(m) -> Vec3:
    """The inverse of euler_to_matrix (the X, Y, Z angles of Rz * Ry * Rx)."""
    sy = -m[2][0]
    if abs(sy) < 0.999999:
        y = math.asin(max(-1.0, min(1.0, sy)))
        x = math.atan2(m[2][1], m[2][2])
        z = math.atan2(m[1][0], m[0][0])
    else:                                       # gimbal lock: Z folded into X
        y = math.copysign(math.pi / 2, sy)
        x = math.atan2(-m[1][2], m[1][1])
        z = 0.0
    return (x, y, z)


_TURN = [[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]      # (x, y) -> (y, -x): undoes studiomdl's turn


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _turn_pos(p: Vec3) -> Vec3:
    return (p[1], -p[0], p[2])


def _turn_rot(e: Vec3, near: Vec3 | None = None) -> Vec3:
    return closest_euler(matrix_to_euler(_mul(_TURN, euler_to_matrix(e))), near)


def closest_euler(e: Vec3, near: Vec3 | None) -> Vec3:
    """The same rotation as e, written with the angles closest to near (the previous frame's): an animation's angles
    then never jump by a turn from one frame to the next (both forms of each rotation tried, each angle wrapped)."""
    if near is None:
        return e
    x, y, z = e
    best, best_d = e, None
    for cand in ((x, y, z), (x + math.pi, math.pi - y, z + math.pi)):
        wrapped = tuple(c + 2 * math.pi * round((n - c) / (2 * math.pi)) for c, n in zip(cand, near))
        d = sum(abs(c - n) for c, n in zip(wrapped, near))
        if best_d is None or d < best_d:
            best, best_d = wrapped, d
    return best


# ------------------------------------------------------------------ SMD text

def _f(v: float) -> str:
    return f"{0.0 if abs(v) < 5e-7 else v:.6f}"          # (never "-0.000000")


def nodes_block(bones: list[Bone]) -> str:
    return "nodes\n" + "".join(f'{i} "{b.name}" {b.parent}\n' for i, b in enumerate(bones)) + "end\n"


def _pose_line(i: int, bone: Bone, pos: Vec3, rot: Vec3) -> str:
    if bone.parent < 0:
        pos, rot = _turn_pos(pos), _turn_rot(rot)
    return f"{i} {_f(pos[0])} {_f(pos[1])} {_f(pos[2])} {_f(rot[0])} {_f(rot[1])} {_f(rot[2])}\n"


def rest_skeleton_block(bones: list[Bone]) -> str:
    return "skeleton\ntime 0\n" + "".join(_pose_line(i, b, b.pos, b.rot) for i, b in enumerate(bones)) + "end\n"


def _skinned_vertex_line(v, links) -> str:
    (x, y, z), (nx, ny, nz), (u, w) = v
    links = _clean_links(links)
    head = f"{links[0][0]} {_f(y)} {_f(-x)} {_f(z)} {_f(ny)} {_f(-nx)} {_f(nz)} {_f(u)} {_f(w)}"
    return head + f" {len(links)}" + "".join(f" {b} {_f(wt)}" for b, wt in links) + "\n"


def _clean_links(links) -> list[tuple[int, float]]:
    """At most MAX_WEIGHTS bones, the strongest, adding up to 1 (a vertex with none: the first bone)."""
    merged: dict[int, float] = {}
    for b, w in links:                          # (the same bone twice: one link)
        merged[b] = merged.get(b, 0.0) + w
    links = sorted(((b, w) for b, w in merged.items() if w > 1e-6), key=lambda bw: -bw[1])[:MAX_WEIGHTS]
    total = sum(w for _b, w in links)
    if total <= 0:
        return [(0, 1.0)]
    return [(b, w / total) for b, w in links]


def reference_smd(triangles, sk: Skeleton) -> str:
    """The mesh at its rest pose, skinned: every vertex with its bone weights."""
    out = ["version 1\n", nodes_block(sk.bones), rest_skeleton_block(sk.bones), "triangles\n"]
    for (material, verts), links in zip(triangles, sk.weights):
        out.append(material + "\n")
        out.extend(_skinned_vertex_line(v, l) for v, l in zip(verts, links))
    out.append("end\n")
    return "".join(out)


def collision_smd(pieces, sk: Skeleton) -> str:
    """The collision hulls (rest pose), all on the first bone: a rigid collision model with the model's skeleton."""
    from .models import collision_smd as plain
    body = plain(pieces).split("triangles\n", 1)[1]
    return "version 1\n" + nodes_block(sk.bones) + rest_skeleton_block(sk.bones) + "triangles\n" + body


def animation_smd(sk: Skeleton, anim: Animation) -> str:
    out = ["version 1\n", nodes_block(sk.bones), "skeleton\n"]
    near: dict[int, Vec3] = {}
    for t, frame in enumerate(anim.frames):
        out.append(f"time {t}\n")
        for i, b in enumerate(sk.bones):
            pos, rot = frame[i]
            if b.parent < 0:                      # (the turned root's angles kept continuous from frame to frame)
                rot = near[i] = _turn_rot(rot, near.get(i))
                pos = _turn_pos(pos)
                out.append(f"{i} {_f(pos[0])} {_f(pos[1])} {_f(pos[2])} {_f(rot[0])} {_f(rot[1])} {_f(rot[2])}\n")
            else:
                out.append(_pose_line(i, b, pos, rot))
    out.append("end\n")
    return "".join(out)


def sequence_file(anim: Animation) -> str:
    return "anim_" + "".join(c if c.isalnum() or c == "_" else "_" for c in anim.name) + ".smd"


def qc_lines(sk: Skeleton) -> list[str]:
    """The sequences and attachments (the rest of the QC is models.qc_text's)."""
    lines = []
    for a in sk.animations:
        opts = f" fps {a.fps:g}" + (" loop" if a.loop else "")
        if a.activity:
            opts += f" activity {a.activity} 1"
        lines.append(f'$sequence "{a.name}" "{sequence_file(a)}"{opts}')
    if not sk.animations:
        lines.append('$sequence idle "model.smd" fps 30')
    for at in sk.attachments:
        b = sk.bones[at.bone]
        roll, pitch, yaw = (math.degrees(c) for c in at.rot)       # (rotate takes pitch, yaw, roll: about Y, Z, X)
        lines.append(f'$attachment "{at.name}" "{b.name}" {_f(at.pos[0])} {_f(at.pos[1])} {_f(at.pos[2])} '
                     f'rotate {pitch:.3f} {yaw:.3f} {roll:.3f}')
    return lines


def problems(sk: Skeleton) -> list[str]:
    out = []
    if len(sk.bones) > MAX_BONES:
        out.append(f"{len(sk.bones)} bones: the game takes at most {MAX_BONES}")
    names = [b.name for b in sk.bones]
    if len(set(names)) != len(names):
        out.append("two bones have the same name")
    seqs = [a.name.lower() for a in sk.animations]
    if len(set(seqs)) != len(seqs):
        out.append("two animations have the same name")
    for a in sk.animations:
        if not a.frames:
            out.append(f"animation '{a.name}' has no frames")
    return out
