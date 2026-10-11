"""The decompiler's test: a game map (.bsp) decompiled to .vmf, compiled again with Hammerless's map compiler, and
the result's structure compared with the original (what the map compiler writes: brushes and their planes,
models, entities, static props, cubemaps, displacements, overlays, faces). Lighting and visibility aren't compared
(only the map compiler runs).

    python tests/compile/decompile_roundtrip.py c1m1_hotel [c2m1_highway ...]
    python tests/compile/decompile_roundtrip.py --all          # every map in the game

Output in %TEMP%/hammerless_decompile/<map>/ (the .vmf, the compiled .bsp, the compiler's log).
"""
from __future__ import annotations

import collections
import os
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import mapcompiler_suite as suite  # noqa: E402
from hammerless.core.bspdecompile import Bsp, decompile  # noqa: E402
from hammerless.core.compile import find_game_root  # noqa: E402
from hammerless.core.vpk import GameContent  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "hammerless_decompile")


def brush_planes(b: Bsp) -> collections.Counter:
    """Every brush as its set of (non-bevel) planes, rounded: the same brushes give the same counter."""
    planes = b.planes()
    sides = b.brushsides()
    out = collections.Counter()
    for first, count, contents in b.brushes():
        key = []
        for s in sides[first:first + count]:
            if s[3]:
                continue
            n, d = planes[s[0]]
            key.append((round(n[0], 3), round(n[1], 3), round(n[2], 3), round(d, 1)))
        out[(contents, tuple(sorted(key)))] += 1
    return out


def near_matches(missing: collections.Counter, extra: collections.Counter, n_tol=1e-3, d_tol=0.05) -> int:
    """Brushes the rounded keys missed only because a value sat on a rounding boundary: the same contents and
    planes, each normal within n_tol and distance within d_tol."""
    pool = list(extra.elements())
    found = 0
    for contents, planes in missing.elements():
        for i, (c2, p2) in enumerate(pool):
            if c2 != contents or len(p2) != len(planes):
                continue
            left = list(p2)
            for p in planes:
                j = next((j for j, q in enumerate(left) if all(abs(p[k] - q[k]) <= n_tol + 1e-3 for k in range(3))
                          and abs(p[3] - q[3]) <= d_tol + 0.1), None)
                if j is None:
                    break
                left.pop(j)
            else:
                found += 1
                pool.pop(i)
                break
    return found


def counts(b: Bsp) -> dict:
    def n(lump, size):
        return len(b.lump(lump)) // size
    ents = b.entities()
    classes = collections.Counter(dict(e).get("classname", "") for e in ents)
    return {"brushes": len(b.brushes()), "models": len(b.models()), "entities": len(ents),
            "static props": len(b.static_props()), "cubemaps": len(b.cubemaps()), "dispinfo": n(26, 176),
            "overlays": n(45, 352), "faces": n(7, 56), "areaportals": n(21, 12), "occluders": _occluders(b),
            "classes": classes}


def _occluders(b: Bsp) -> int:
    lump = b.lump(9)
    return struct.unpack_from("<i", lump, 0)[0] if len(lump) >= 4 else 0


def run(name: str, content, root: str) -> bool:
    data = content.read(f"maps/{name}.bsp")
    if not data:
        print(f"SKIP {name}: not in the game", flush=True)
        return True
    t0 = time.time()
    text, notes = decompile(data)
    t1 = time.time()
    base, _vbase = suite.compile_map("dc_" + name, text, root)
    t2 = time.time()
    if not os.path.exists(base + ".bsp"):
        log = open(base + ".log", errors="replace").read()
        print(f"FAIL {name}: the map compiler made no map ({log.strip().splitlines()[-1] if log.strip() else ''})")
        return False
    orig, ours = Bsp(data), Bsp(open(base + ".bsp", "rb").read())
    a, c = counts(orig), counts(ours)
    diffs = [f"{k} {a[k]} -> {c[k]}" for k in a if k != "classes" and a[k] != c[k]]
    lost = {k: v - c["classes"].get(k, 0) for k, v in a["classes"].items() if v != c["classes"].get(k, 0)}
    pa, pc = brush_planes(orig), brush_planes(ours)
    same = sum((pa & pc).values()) + near_matches(pa - pc, pc - pa)
    ok = not diffs and not lost and same == sum(pa.values())
    print(f"{'SAME' if ok else 'DIFF'} {name}: decompile {t1 - t0:.1f}s, compile {t2 - t1:.0f}s; "
          f"brushes matching {same}/{sum(pa.values())}" + (f"; {', '.join(diffs)}" if diffs else "")
          + (f"; entity classes (original - ours): {lost}" if lost else "")
          + (f"; notes: {notes}" if notes else ""), flush=True)
    return ok


def main(argv):
    root = find_game_root()
    content = GameContent(root)
    names = [a for a in argv if not a.startswith("--")]
    if "--all" in argv:
        names = {p[5:-4] for p in content.files if p.startswith("maps/") and p.endswith(".bsp") and "/" not in p[5:]}
        for sub in ("left4dead2", "update", "left4dead2_dlc1", "left4dead2_dlc2", "left4dead2_dlc3"):
            folder = os.path.join(root, sub, "maps")      # (the campaigns' maps are loose files)
            if os.path.isdir(folder):
                names |= {f[:-4] for f in os.listdir(folder) if f.lower().endswith(".bsp") and not f.startswith("hl_")}
        names = sorted(names)
    suite.OUT = OUT
    results = [run(n, content, root) for n in names]
    print(f"\n{sum(results)} of {len(results)} the same")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
