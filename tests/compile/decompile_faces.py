"""Faces after a decompile round trip: total drawn area per material, original vs recompiled (a material whose area
changed means surfaces were lost or added; the same area in a different number of faces is only splitting).

    python tests/compile/decompile_faces.py c1m1_hotel      (after decompile_roundtrip.py ran on it)
"""
import collections
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from hammerless.core.bspdecompile import Bsp, original_material  # noqa: E402
from hammerless.core.compile import find_game_root  # noqa: E402
from hammerless.core.vpk import GameContent  # noqa: E402


def area_by_material(b: Bsp, world_only=True) -> collections.Counter:
    names, texinfos = b.texture_names(), b.texinfos()
    models = b.records(14, "9f3i")
    first, num = models[0][10], models[0][11]
    out = collections.Counter()
    for i, (_plane, w, ti, disp) in enumerate(b.face_windings()):
        if world_only and not (first <= i < first + num):
            continue
        if disp >= 0 or ti < 0:
            continue
        a = [0.0, 0.0, 0.0]
        for k in range(1, len(w) - 1):
            u = [w[k][j] - w[0][j] for j in range(3)]
            v = [w[k + 1][j] - w[0][j] for j in range(3)]
            a = [a[0] + u[1] * v[2] - u[2] * v[1], a[1] + u[2] * v[0] - u[0] * v[2], a[2] + u[0] * v[1] - u[1] * v[0]]
        td = texinfos[ti][3]
        out[original_material(names[td].upper())] += math.sqrt(sum(x * x for x in a)) / 2
    return out


name = sys.argv[1]
orig = Bsp(GameContent(find_game_root()).read(f"maps/{name}.bsp"))
ours = Bsp(open(os.path.join(tempfile.gettempdir(), "hammerless_decompile", f"dc_{name}", f"hl_test_dc_{name}.bsp"), "rb").read())
a, b = area_by_material(orig), area_by_material(ours)
ta, tb = sum(a.values()), sum(b.values())
print(f"world face area: original {ta:.0f}, ours {tb:.0f} ({100 * (tb - ta) / ta:+.2f}%)")
diffs = sorted(((b[m] - a[m], m) for m in set(a) | set(b) if abs(b[m] - a[m]) > max(1.0, 0.001 * a[m])), key=lambda x: x[0])
for d, m in diffs[:8] + diffs[-8:]:
    print(f"  {m}: original {a[m]:.0f}, ours {b[m]:.0f} ({d:+.0f})")
