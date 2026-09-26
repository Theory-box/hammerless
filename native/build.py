"""Build hammerless/core/_hlnav.dll from native/hlnav.c with zig (pip install ziglang).

Floating point must behave exactly like Python's doubles: no fused multiply-add
(-ffp-contract=off), no fast-math, and a baseline CPU so the DLL runs anywhere.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "hammerless", "core", "_hlnav.dll")

cmd = [sys.executable, "-m", "ziglang", "cc", "-shared", "-O2", "-ffp-contract=off", "-fno-fast-math",
       "-target", "x86_64-windows-gnu", "-mcpu=baseline", "-o", os.path.abspath(OUT),
       os.path.join(HERE, "hlnav.c")]
print(" ".join(cmd))
subprocess.run(cmd, check=True)
for extra in (OUT[:-4] + ".lib", OUT[:-4] + ".pdb", os.path.join(os.path.dirname(OUT), "hlnav.lib")):
    if os.path.exists(extra):
        os.remove(extra)
print("built", os.path.abspath(OUT), os.path.getsize(OUT), "bytes")
