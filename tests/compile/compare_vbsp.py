"""Compile .vmf files with Valve's vbsp and Hammerless's hlvbsp and compare the maps (as the suite does).

    python tests/compile/compare_vbsp.py path/to/map.vmf [more.vmf ...]
    python tests/compile/compare_vbsp.py --dir "C:/.../sdk_content" [--skip-unsupported]

Maps hlvbsp hands to Valve's vbsp (instances, water overlays) are reported as such. Needs the
Authoring Tools; output in %TEMP%/hammerless_mapcompiler_suite/real_<name>.
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mapcompiler_suite as suite  # noqa: E402
from hammerless.core.compile import find_game_root  # noqa: E402
from hammerless.core.mapcompiler import unsupported  # noqa: E402


def main(argv: list[str]) -> int:
    paths = [a for a in argv if not a.startswith("--")]
    if "--dir" in argv:
        d = argv[argv.index("--dir") + 1]
        paths = [p for p in paths if p != d]
        for dirpath, _, files in os.walk(d):
            paths += [os.path.join(dirpath, f) for f in sorted(files) if f.lower().endswith(".vmf")]
    root = find_game_root()
    results = {"same": 0, "differ": 0, "fallback": 0}
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        parent = os.path.basename(os.path.dirname(os.path.abspath(path))).lower()
        name = "real_" + parent + "_" + os.path.splitext(os.path.basename(path))[0].lower()
        why = unsupported(text)
        if why:
            results["fallback"] += 1
            print(f"FALLBACK {name}: {', '.join(why)}", flush=True)
            continue
        t = time.time()
        base, vbase = suite.compile_map(name, text, root)
        problems = suite.compare(base, vbase)
        results["differ" if problems else "same"] += 1
        print(f"{'SAME' if not problems else 'DIFF'} {name} ({time.time() - t:.0f} s)"
              + (": " + ", ".join(problems) if problems else ""), flush=True)
    print(f"\n{results['same']} same, {results['differ']} different, {results['fallback']} handed to Valve's vbsp")
    return 1 if results["differ"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
