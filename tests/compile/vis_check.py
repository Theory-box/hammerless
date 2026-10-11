"""Is a visibility result safe? Compares a map's PVS with a reference (vvis's) and fires random sight lines at
every cluster pair the result hides that the reference shows: one line through open space proves the pair
visible, so the result would hide something real (a missing wall in game).

    python tests/compile/vis_check.py reference.bsp result.bsp map.prt [--rays 20000000]

Both maps must be the same compile (same clusters). Prints how much looser the result is and, for each hidden
pair, whether any ray got through. A result is safe when no ray gets through for any of them; pairs no ray
reaches are the reference's own tolerance artefacts (vvis keeps a pair through openings under 0.01 units).
The rays: tests/compile/vis_rays.c (built with zig on first use), between random points on the two clusters'
portals; a ray is open when every BSP leaf it crosses belongs to a cluster.
"""
import argparse
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "vis_rays.exe")


def pvs(path: str) -> set:
    d = open(path, "rb").read()
    _v, o, ln, _c = struct.unpack_from("<4i", d, 8 + 16 * 4)
    lump = d[o:o + ln]
    n = struct.unpack_from("<i", lump, 0)[0]
    out, nbytes = set(), (n + 7) // 8
    for c in range(n):
        i = struct.unpack_from("<i", lump, 4 + 8 * c)[0]
        row = bytearray()
        while len(row) < nbytes:
            b = lump[i]
            i += 1
            if b:
                row.append(b)
            else:
                row += bytes(lump[i])
                i += 1
        for k in range(n):
            if row[k >> 3] & (1 << (k & 7)):
                out.add((c, k))
    return out


def build() -> None:
    src = os.path.join(HERE, "vis_rays.c")
    if os.path.exists(EXE) and os.path.getmtime(EXE) >= os.path.getmtime(src):
        return
    subprocess.run([sys.executable, "-m", "ziglang", "cc", "-O2", "-target", "x86_64-windows-gnu", src, "-o", EXE],
                   check=True)


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("result")
    ap.add_argument("prt")
    ap.add_argument("--rays", type=int, default=20_000_000)
    a = ap.parse_args(argv)
    ref, res = pvs(a.reference), pvs(a.result)
    hidden = sorted({tuple(sorted(p)) for p in ref - res})
    extra = len(res - ref)
    print(f"reference {len(ref)} visible pairs, result {len(res)}: {extra} more ({100 * extra / len(ref):.2f}% looser), "
          f"hides {len(hidden)} cluster pairs the reference shows", flush=True)
    if not hidden:
        return 0
    build()
    args = [EXE, a.reference, a.prt, str(a.rays)] + [str(x) for p in hidden for x in p]
    out = subprocess.run(args, capture_output=True, text=True).stdout
    seen = [ln for ln in out.splitlines() if "VISIBLE" in ln]
    print(out.strip())
    print(f"\n{'UNSAFE' if seen else 'SAFE'}: {len(seen)} of {len(hidden)} hidden pairs proven visible by a ray")
    return 1 if seen else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
