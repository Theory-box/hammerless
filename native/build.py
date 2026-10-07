"""Build the native parts with zig (pip install ziglang):

- hammerless/core/_hlnav.dll from native/hlnav.c (nav sampling, flood fill, nav visibility)
- hammerless/core/hlvvis.exe from native/hlvvis.c (the visibility compiler, a drop-in for vvis)

Floating point must behave exactly like the reference: no fused multiply-add (-ffp-contract=off), no
fast-math, and a baseline CPU (SSE2) so the binaries run anywhere.

    python native/build.py            (both)
    python native/build.py hlvvis     (just one)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "..", "hammerless", "core")
FLAGS = ["-O2", "-ffp-contract=off", "-fno-fast-math", "-target", "x86_64-windows-gnu", "-mcpu=baseline"]
TARGETS = {
    "hlnav": (["-shared"], "_hlnav.dll", "hlnav.c"),
    "hlvvis": ([], "hlvvis.exe", "hlvvis.c"),
}

for name in sys.argv[1:] or list(TARGETS):
    extra, out_name, src = TARGETS[name]
    out = os.path.abspath(os.path.join(CORE, out_name))
    cmd = [sys.executable, "-m", "ziglang", "cc", *extra, *FLAGS, "-o", out, os.path.join(HERE, src)]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    stem = out[:-4]
    for leftover in (stem + ".lib", stem + ".pdb", os.path.join(CORE, name + ".lib"), os.path.join(CORE, name + ".pdb")):
        if os.path.exists(leftover):
            os.remove(leftover)
    print("built", out, os.path.getsize(out), "bytes")
