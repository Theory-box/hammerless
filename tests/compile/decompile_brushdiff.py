"""Which brushes a decompile round trip didn't bring back, and the nearest one it made instead.

    python tests/compile/decompile_brushdiff.py c2m1_highway      (after decompile_roundtrip.py ran on it)
"""
import collections
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import decompile_roundtrip as rt  # noqa: E402
from hammerless.core.bspdecompile import Bsp  # noqa: E402
from hammerless.core.compile import find_game_root  # noqa: E402
from hammerless.core.vpk import GameContent  # noqa: E402

name = sys.argv[1]
orig = Bsp(GameContent(find_game_root()).read(f"maps/{name}.bsp"))
ours = Bsp(open(os.path.join(tempfile.gettempdir(), "hammerless_decompile", f"dc_{name}", f"hl_test_dc_{name}.bsp"), "rb").read())
pa, pc = rt.brush_planes(orig), rt.brush_planes(ours)
miss, extra = list((pa - pc).elements()), list((pc - pa).elements())
print(f"missing {len(miss)} by contents {collections.Counter(hex(k[0]) for k in miss).most_common(6)}")
print(f"extra {len(extra)} by contents {collections.Counter(hex(k[0]) for k in extra).most_common(6)}")
for m in miss[:8]:
    best = min(extra, key=lambda e: len(set(m[1]) ^ set(e[1]))) if extra else None
    if best:
        print(f"  {hex(m[0])} {len(m[1])} sides vs {hex(best[0])}: only original {sorted(set(m[1]) - set(best[1]))[:2]}, "
              f"only ours {sorted(set(best[1]) - set(m[1]))[:2]}")
