"""Read Left 4 Dead 2's own animated models (.mdl v49 + .ani): skeleton, sequences, poses.

Ported from the research reader (docs/dev/research/model_tools/mdlparse.py); the findings
behind it are in docs/dev/research/L4D2_GAME_MODELS.md.

Spaces (quaternions are (x, y, z, w), units are game inches):
- Bind pose (GameBone.pos/quat, bone_world()): the mesh's rest pose (equal to the inverse of
  its poseToBone). Survivors stand on z 0 facing -Y; the commons' rest pose has the pelvis at
  the origin, unrotated, so the body lies along +Y (measured; their poseToBone agrees).
- Animation poses (frame()): the same space turned +90 deg about Z, carried by the root bone
  alone, so a composed animation pose faces +X like the character does in game.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

Vec = tuple[float, float, float]
Quat = tuple[float, float, float, float]
Pose = list[tuple[Vec, Quat]]

_IDENTITY: Quat = (0.0, 0.0, 0.0, 1.0)
_ZERO: Vec = (0.0, 0.0, 0.0)

# studiohdr_t offsets (Source SDK 2013 public/studio.h, v49)
_H_FLAGS, _H_BONES, _H_HITBOXSETS, _H_ANIMS, _H_SEQS = 152, 156, 172, 180, 188
_H_ATTACHMENTS, _H_IKCHAINS, _H_POSEPARAMS = 240, 284, 300
_H_INCLUDES, _H_ANIMBLOCKNAME, _H_ANIMBLOCKS = 336, 348, 352

_BONE_SIZE, _ANIM_SIZE, _SEQ_SIZE, _HITBOX_SIZE, _ATTACH_SIZE = 216, 100, 212, 68, 92

_ANIM_DELTA = 0x4
_ANIM_FRAMEANIM = 0x40   # L4D2's per-frame layout (not in the 2013 SDK); 96% of its animations
_SEQ_LOOPING = 0x1


@dataclass
class GameBone:
    name: str
    parent: int
    pos: Vec      # bind pose relative to the parent bone, as stored
    quat: Quat


@dataclass
class GameSequence:
    name: str
    activity: str
    fps: float
    frames: int
    loop: bool
    anim_index: int   # animation of the model that holds the sequence (its first blend)


@dataclass
class GameHitbox:
    bone: int
    group: int
    mins: tuple
    maxs: tuple
    name: str


@dataclass
class GameIKChain:
    name: str
    links: list[tuple[int, Vec]]   # (bone, ideal bend direction = knee vector), as in mstudioiklink_t


@dataclass
class GameAttachment:
    name: str
    bone: int
    matrix: tuple   # 3x4 row-major, relative to the bone


def _cstr(b: bytes, at: int) -> str:
    if at <= 0 or at >= len(b):
        return ""
    return b[at:b.index(b"\0", at)].decode("latin-1")


def _i(b: bytes, at: int) -> int:
    return struct.unpack_from("<i", b, at)[0]


class GameModel:
    """One .mdl (and its .ani). Sequences are this model's own; includemodels are listed, not merged."""

    def __init__(self, data: bytes, ani: bytes | None = None, path: str = ""):
        if data[:4] != b"IDST":
            raise ValueError(f"not a Source model: {path or '(bytes)'}")
        self._b = b = data
        self._ani = ani
        self.path = path
        self.flags = _i(b, _H_FLAGS)
        self.bones: list[GameBone] = []
        self._rot: list[Vec] = []           # euler rest pose and scales: the RLE format's base and units
        self._posscale: list[Vec] = []
        self._rotscale: list[Vec] = []
        n, at = _i(b, _H_BONES), _i(b, _H_BONES + 4)
        for k in range(n):
            o = at + _BONE_SIZE * k
            nm, parent = struct.unpack_from("<ii", b, o)
            self.bones.append(GameBone(_cstr(b, o + nm), parent, struct.unpack_from("<3f", b, o + 32),
                                       struct.unpack_from("<4f", b, o + 44)))
            self._rot.append(struct.unpack_from("<3f", b, o + 60))
            self._posscale.append(struct.unpack_from("<3f", b, o + 72))
            self._rotscale.append(struct.unpack_from("<3f", b, o + 84))
        self.ikchains: list[GameIKChain] = []
        n, at = _i(b, _H_IKCHAINS), _i(b, _H_IKCHAINS + 4)
        for k in range(n):
            o = at + 16 * k
            nm, _linktype, nlinks, li = struct.unpack_from("<iiii", b, o)
            links = [struct.unpack_from("<i3f", b, o + li + 28 * j) for j in range(nlinks)]
            self.ikchains.append(GameIKChain(_cstr(b, o + nm), [(l[0], l[1:]) for l in links]))
        # (name, flags, start, end, loop); loop is the wrap range, e.g. 360 for a yaw
        self.poseparams: list[tuple[str, int, float, float, float]] = []
        n, at = _i(b, _H_POSEPARAMS), _i(b, _H_POSEPARAMS + 4)
        for k in range(n):
            o = at + 20 * k
            nm, flags, start, end, loop = struct.unpack_from("<iifff", b, o)
            self.poseparams.append((_cstr(b, o + nm), flags, start, end, loop))
        # In the stored order: the game merges the includes' sequences in this order.
        self.includes: list[str] = []
        n, at = _i(b, _H_INCLUDES), _i(b, _H_INCLUDES + 4)
        for k in range(n):
            o = at + 8 * k
            name = _cstr(b, o + _i(b, o + 4)).replace("\\", "/").lower()
            self.includes.append(name if name.startswith("models/") else "models/" + name)
        self.ani_name = _cstr(b, _i(b, _H_ANIMBLOCKNAME)).replace("\\", "/").lower()
        n, at = _i(b, _H_ANIMBLOCKS), _i(b, _H_ANIMBLOCKS + 4)
        self._animblocks = [struct.unpack_from("<ii", b, at + 8 * k) for k in range(n)]
        self._anims = [self._animdesc(k) for k in range(_i(b, _H_ANIMS))]
        self.sequences = [self._seqdesc(k) for k in range(_i(b, _H_SEQS))]
        self.hitboxes = self._hitboxes()
        self.attachments: list[GameAttachment] = []
        n, at = _i(b, _H_ATTACHMENTS), _i(b, _H_ATTACHMENTS + 4)
        for k in range(n):
            o = at + _ATTACH_SIZE * k
            self.attachments.append(GameAttachment(_cstr(b, o + _i(b, o)), _i(b, o + 8),
                                                   struct.unpack_from("<12f", b, o + 12)))

    # ---- tables ----
    def _animdesc(self, k: int) -> dict:
        b = self._b
        o = _i(b, _H_ANIMS + 4) + _ANIM_SIZE * k
        _base, _nm, fps, flags, frames = struct.unpack_from("<iifii", b, o)
        block, index = struct.unpack_from("<ii", b, o + 52)
        sectionindex, sectionframes = struct.unpack_from("<ii", b, o + 80)
        return dict(off=o, fps=fps, flags=flags, frames=frames, block=block, index=index,
                    sectionindex=sectionindex, sectionframes=sectionframes)

    def _seqdesc(self, k: int) -> GameSequence:
        b = self._b
        o = _i(b, _H_SEQS + 4) + _SEQ_SIZE * k
        _base, label, act, flags = struct.unpack_from("<iiii", b, o)
        animindexindex = _i(b, o + 60)
        gx, gy = struct.unpack_from("<2i", b, o + 68)
        anim = struct.unpack_from("<h", b, o + animindexindex)[0] if gx * gy else -1
        a = self._anims[anim] if 0 <= anim < len(self._anims) else None
        return GameSequence(_cstr(b, o + label), _cstr(b, o + act), a["fps"] if a else 30.0,
                            a["frames"] if a else 0, bool(flags & _SEQ_LOOPING), anim)

    def _hitboxes(self) -> list[GameHitbox]:
        b = self._b
        if not _i(b, _H_HITBOXSETS):
            return []
        o = _i(b, _H_HITBOXSETS + 4)       # the first set: the one the game uses unless told otherwise
        n, idx = struct.unpack_from("<ii", b, o + 4)
        out = []
        for k in range(n):
            q = o + idx + _HITBOX_SIZE * k
            bone, group = struct.unpack_from("<ii", b, q)
            nm = _i(b, q + 32)
            out.append(GameHitbox(bone, group, struct.unpack_from("<3f", b, q + 8),
                                  struct.unpack_from("<3f", b, q + 20), _cstr(b, q + nm) if nm else ""))
        return out

    # ---- poses ----
    def bone_world(self, local: Pose | None = None) -> Pose:
        """Bones in model space: the bind pose by default, or a local pose from frame() composed
        through the parents (which stays in animation space: bind space turned +90 deg about Z)."""
        if local is None:
            local = [(bn.pos, bn.quat) for bn in self.bones]
        out: Pose = []
        for bn, (p, q) in zip(self.bones, local):
            if bn.parent < 0:
                out.append((tuple(p), tuple(q)))
            else:
                pp, pq = out[bn.parent]
                r = _qrot(pq, p)
                out.append(((pp[0] + r[0], pp[1] + r[1], pp[2] + r[2]), _qmul(pq, q)))
        return out

    def frame(self, seq: GameSequence, f: int) -> Pose:
        """Local (parent-relative) pose of every bone at integer frame f (clamped) of one of this
        model's own sequences. Bones the animation leaves out keep the bind pose. A DELTA animation
        (gesture/layer) is returned as its raw delta, as stored, with identity/zero for bones it
        leaves out; the game adds it on top of another pose, so it is not added to the bind here."""
        a = self._anims[seq.anim_index]
        f = max(0, min(int(f), max(a["frames"] - 1, 0)))
        delta = bool(a["flags"] & _ANIM_DELTA)
        buf, o, lf = self._anim_data(a, f)
        if buf is None:
            raise ValueError(f"{self.path}: animation data is in the .ani file, which was not loaded")
        if a["flags"] & _ANIM_FRAMEANIM:
            return self._decode_frameanim(buf, o, lf, delta)
        return self._decode_rle(buf, o, lf, delta)

    def _anim_data(self, a: dict, f: int):
        """(buffer, offset, frame within it). Long animations are cut into sections, each with its
        own block; block 0 is this file, others are ranges of the .ani file."""
        lf = f
        if a["sectionframes"]:
            sf, n = a["sectionframes"], a["frames"]
            if n > sf and f == n - 1:     # (the last frame of a long animation is a section of its own)
                sec, lf = n // sf + 1, 0
            else:
                sec, lf = f // sf, f % sf
            blk, idx = struct.unpack_from("<ii", self._b, a["off"] + a["sectionindex"] + 8 * sec)
        else:
            blk, idx = a["block"], a["index"]
        if blk == 0:
            return self._b, a["off"] + idx, lf
        if self._ani is None or blk >= len(self._animblocks):
            return None, 0, lf
        return self._ani, self._animblocks[blk][0] + idx, lf

    def _decode_frameanim(self, buf: bytes, o: int, lf: int, delta: bool) -> Pose:
        """L4D2's frame-anim: {constantsoffset, frameoffset, framelength, unused[3]}, one flag byte
        per bone, then the constants and framelength bytes per frame (rotation read before position)."""
        constoff, frameoff, framelen = struct.unpack_from("<3i", buf, o)
        nb = len(self.bones)
        flags = buf[o + 24:o + 24 + nb]
        consts = buf[o + constoff:o + frameoff]
        fr = buf[o + frameoff + lf * framelen:o + frameoff + (lf + 1) * framelen]
        ci = fi = 0
        out: Pose = []
        for bn, fl in zip(self.bones, flags):
            q = p = None
            if fl & 0x02:
                q = _quat48(consts, ci); ci += 6
            elif fl & 0x40:
                q = _quat48s(consts, ci); ci += 6
            if fl & 0x08:
                q = _quat48(fr, fi); fi += 6
            elif fl & 0x80:
                q = _quat48s(fr, fi); fi += 6
            if fl & 0x01:
                p = struct.unpack_from("<3e", consts, ci); ci += 6
            elif fl & 0x20:
                p = struct.unpack_from("<3f", consts, ci); ci += 12
            if fl & 0x04:
                p = struct.unpack_from("<3e", fr, fi); fi += 6
            elif fl & 0x10:
                p = struct.unpack_from("<3f", fr, fi); fi += 12
            if q is None:
                q = _IDENTITY if delta else bn.quat
            if p is None:
                p = _ZERO if delta else bn.pos
            out.append((tuple(p), tuple(q)))
        return out

    def _decode_rle(self, buf: bytes, o: int, lf: int, delta: bool) -> Pose:
        """Source SDK 2013 mstudio_rle_anim_t chain (L4D2 uses it for reference poses and viewmodels)."""
        out: Pose = [(_ZERO, _IDENTITY) if delta else (bn.pos, bn.quat) for bn in self.bones]
        nb = len(self.bones)
        while True:
            bone, fl, nxt = struct.unpack_from("<BBh", buf, o)
            if bone >= nb:
                break
            bn, d = self.bones[bone], o + 4
            raw = fl & 0x10                # (values are deltas, not added to the bone's rest)
            if fl & 0x02:
                q = _quat48(buf, d)
            elif fl & 0x20:
                q = _quat64(buf, d)
            elif fl & 0x08:
                ang = []
                for j in range(3):
                    off = struct.unpack_from("<h", buf, d + 2 * j)[0]
                    v = _extract(buf, d + off, lf) * self._rotscale[bone][j] if off else 0.0
                    ang.append(v if raw else v + self._rot[bone][j])
                q = _angle_quat(ang)
            else:
                q = _IDENTITY if raw else bn.quat
            if fl & 0x01:
                p = struct.unpack_from("<3e", buf, d + (6 if fl & 0x02 else 0) + (8 if fl & 0x20 else 0))
            elif fl & 0x04:
                pd = d + (6 if fl & 0x08 else 0)
                p = []
                for j in range(3):
                    off = struct.unpack_from("<h", buf, pd + 2 * j)[0]
                    v = _extract(buf, pd + off, lf) * self._posscale[bone][j] if off else 0.0
                    p.append(v if raw else v + bn.pos[j])
            else:
                p = _ZERO if raw else bn.pos
            out[bone] = (tuple(p), tuple(q))
            if nxt == 0:
                break
            o += nxt
        return out


# ---- packed values ----
def _extract(buf: bytes, o: int, k: int) -> float:
    """ExtractAnimValue: runs of {valid, total} headers, each followed by `valid` shorts."""
    while True:
        valid, total = buf[o], buf[o + 1]
        if total == 0:
            return 0.0
        if total > k:
            break
        k -= total
        o += 2 * (valid + 1)
    # (past the stored values the run repeats its last one)
    return struct.unpack_from("<h", buf, o + 2 * (k + 1) if valid > k else o + 2 * valid)[0]


def _quat48(b: bytes, o: int) -> Quat:
    x, y, zw = struct.unpack_from("<HHH", b, o)
    qx, qy, qz = (x - 32768) / 32768.0, (y - 32768) / 32768.0, ((zw & 0x7FFF) - 16384) / 16384.0
    w = math.sqrt(max(0.0, 1 - qx * qx - qy * qy - qz * qz))
    return (qx, qy, qz, -w if zw >> 15 else w)


def _quat48s(b: bytes, o: int) -> Quat:
    """Quaternion48S ('smallest three'): three 15-bit values, a 2-bit index of the largest
    component (top bits of the first two) and its sign (top bit of the third)."""
    a, bb, c = struct.unpack_from("<HHH", b, o)
    # Measured on anim_gambler: the index counts in (w, x, y, z) order, so the largest is
    # (x,y,z,w)[(index + 3) % 4] and the stored three follow it cyclically.
    big = (((a >> 15) << 1 | (bb >> 15)) + 3) % 4
    t = [0.0] * 4
    for j, v in enumerate((a, bb, c)):
        t[(big + 1 + j) % 4] = ((v & 0x7FFF) - 16384) / 23168.0
    t[big] = math.sqrt(max(0.0, 1 - sum(v * v for v in t)))
    if c >> 15:
        t[big] = -t[big]
    return tuple(t)


def _quat64(b: bytes, o: int) -> Quat:
    v = int.from_bytes(b[o:o + 8], "little")
    qx, qy, qz = ((((v >> s) & 0x1FFFFF) - 1048576) / 1048576.5 for s in (0, 21, 42))
    w = math.sqrt(max(0.0, 1 - qx * qx - qy * qy - qz * qz))
    return (qx, qy, qz, -w if v >> 63 else w)


def _angle_quat(a) -> Quat:
    """mathlib AngleQuaternion(RadianEuler): (x roll, y pitch, z yaw) radians."""
    sr, cr = math.sin(a[0] * 0.5), math.cos(a[0] * 0.5)
    sp, cp = math.sin(a[1] * 0.5), math.cos(a[1] * 0.5)
    sy, cy = math.sin(a[2] * 0.5), math.cos(a[2] * 0.5)
    return (sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy)


def _qmul(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _qrot(q: Quat, v: Vec) -> Vec:
    x, y, z, w = q
    vx, vy, vz = v
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz), vz + w * tz + (x * ty - y * tx))


# ---- loading through the game's files ----
def load_game_model(content, path: str) -> GameModel | None:
    """A model from the game (a vpk.GameContent), with its .ani when the header names one; None if missing."""
    path = path.replace("\\", "/").lower()
    data = content.read(path)
    if data is None:
        return None
    try:
        model = GameModel(data, path=path)
    except (ValueError, struct.error, IndexError):
        return None
    if model.ani_name:
        ani = content.read(model.ani_name)
        if ani is None and not model.ani_name.startswith("models/"):
            ani = content.read("models/" + model.ani_name)
        model._ani = ani
    return model


def animation_sources(content, model: GameModel) -> list[GameModel]:
    """The model and every model it $includemodel's (recursively, each once), in the order the game
    merges their sequences."""
    out, seen = [model], {model.path}
    k = 0
    while k < len(out):
        for inc in out[k].includes:
            if inc in seen:
                continue
            seen.add(inc)
            m = load_game_model(content, inc)
            if m is not None:
                out.append(m)
        k += 1
    return out


def all_sequence_names(content, model: GameModel) -> list[str]:
    """Sequence names as the game lists them: the model's own, then its includes'; a name already
    seen (case-insensitive, like LookupSequence) is skipped."""
    names, seen = [], set()
    for m in animation_sources(content, model):
        for s in m.sequences:
            if s.name.lower() not in seen:
                seen.add(s.name.lower())
                names.append(s.name)
    return names


def retarget(src: GameModel, dst: GameModel, local_pose: Pose) -> Pose:
    """A local pose of src's bones put on dst's bones by name (how the game shares animations between
    a model and its includes); dst bones src lacks keep dst's bind pose."""
    by_name = {bn.name.lower(): k for k, bn in enumerate(src.bones)}
    out: Pose = []
    for bn in dst.bones:
        k = by_name.get(bn.name.lower())
        out.append(local_pose[k] if k is not None else (bn.pos, bn.quat))
    return out
