"""Vis Only brushes (func_hl_visblock): hlvbsp puts them in the tree like world brushes (the same leaves,
portals and so the same vis) but leaves them out of the brush list (no collision, none of the 8,192).

The same map twice: a room split by a wall with a doorway, the wall once a world brush, once Vis Only.
Every lump must be the same except the brush lists, leaf brushes, physics and the entities.

    python tests/compile/vis_only_suite.py

Needs Left 4 Dead 2 (materials, vphysics.dll). Output in %TEMP%/hammerless_mapcompiler_suite/visonly_*.
"""
from __future__ import annotations

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bspdump  # noqa: E402
import mapcompiler_suite as suite  # noqa: E402
from hammerless.core.compile import find_game_root  # noqa: E402

LUMP_LEAFBRUSHES, LUMP_BRUSHES, LUMP_BRUSHSIDES, LUMP_PHYS, LUMP_ENTITIES = 17, 18, 19, 29, 0
LUMP_LEAFS = 10
# (texdata, texinfo and the texture names come in another order: the wall's material is met later)
MAY_DIFFER = {LUMP_ENTITIES, LUMP_LEAFBRUSHES, LUMP_BRUSHES, LUMP_BRUSHSIDES, LUMP_PHYS, LUMP_LEAFS, 2, 6, 43, 44,
              28, 35, 40}
NODRAW = "TOOLS/TOOLSNODRAW"


def make(vis_only: bool) -> str:
    m = suite.Map()
    m.solids = m.room((0, 0, 0), (1024, 512, 256))
    wall = [m.box((504, 0, 0), (520, 200, 256), NODRAW, "wa."), m.box((504, 312, 0), (520, 512, 256), NODRAW, "wb."),
            m.box((504, 200, 128), (520, 312, 256), NODRAW, "wc.")]
    if vis_only:
        m.ent("func_hl_visblock", solids=wall)
    else:
        m.solids += wall
    m.player_and_light("256 256 64", "256 256 200")
    m.ent("light", "768 256 200", '"_light" "255 255 255 300"')
    return m.text()


def main() -> int:
    root = find_game_root()
    world, _ = suite.compile_map("visonly_world", make(False), root)
    vis, _ = suite.compile_map("visonly_vis", make(True), root)
    bad = []
    for b in (world, vis):
        if not os.path.exists(b + ".bsp"):
            print(open(b + ".log").read()[-2000:])
            return 1
    a, b = bspdump.lumps(open(world + ".bsp", "rb").read()), bspdump.lumps(open(vis + ".bsp", "rb").read())
    for i, (la, lb) in enumerate(zip(a, b)):
        if i not in MAY_DIFFER and la[3] != lb[3]:
            bad.append(f"lump {i} differs")
    la, lb = a[LUMP_LEAFS][3], b[LUMP_LEAFS][3]
    strip = lambda d: b"".join(d[k:k + 24] + d[k + 28:k + 32] for k in range(0, len(d), 32))   # (no leaf brushes)
    if strip(la) != strip(lb):
        bad.append("the leaves differ (beyond their brush lists)")
    nb_a, nb_b = len(a[LUMP_BRUSHES][3]) // 12, len(b[LUMP_BRUSHES][3]) // 12
    if nb_b != nb_a - 3:
        bad.append(f"brushes: {nb_a} with the world wall, {nb_b} with it Vis Only (expected 3 fewer)")
    leaves = len(a[10][3]) // 32
    # the wall's leaves are solid and hold no brush when Vis Only
    leafb = b[10][3]
    solid_empty = 0
    for k in range(len(leafb) // 32):
        contents, = struct.unpack_from("<i", leafb, 32 * k)
        nlb, = struct.unpack_from("<H", leafb, 32 * k + 26)
        if contents & 1 and nlb == 0 and k:
            solid_empty += 1
    if not solid_empty:
        bad.append("no solid leaf without brushes: the wall's leaves still list it")
    with open(world + ".prt") as f1, open(vis + ".prt") as f2:
        if f1.read() != f2.read():
            bad.append("portal files differ")
    if "func_hl_visblock" in b[LUMP_ENTITIES][3].decode("latin-1"):
        bad.append("func_hl_visblock left in the entities")
    print(f"{leaves} leaves; brushes {nb_a} -> {nb_b}; {solid_empty} solid leaves with no brush")
    for x in bad:
        print("FAIL", x)
    print("PASS" if not bad else f"{len(bad)} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
