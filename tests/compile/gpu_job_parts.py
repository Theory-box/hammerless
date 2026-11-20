"""hlvrad -gpu sends each GPU job in parts sized to the card (so a slow card isn't reset by Windows): the lighting
must not depend on the parts.

    python tests/compile/gpu_job_parts.py <map.bsp> [--preset NORMAL] [--before <older hlvrad.exe>] [--parts 0.01,0.3]
        [--args "-gi 3"] [--threads 4]

The map (compiled, with vis) is copied, never written. Bakes: the tool as it paces itself, then with the part fixed
(HL_GPU_JOBPART) at each value, and with --before an older build. Every lump of every result must equal the first's
to the byte. Prints the times and a line per comparison; exit 1 on a difference.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from hammerless.core import compile as cc

ap = argparse.ArgumentParser()
ap.add_argument("bsp")
ap.add_argument("--preset", default="NORMAL")
ap.add_argument("--before", default="")
ap.add_argument("--parts", default="0.01,0.3")
ap.add_argument("--args", default="", help="more hlvrad options, e.g. \"-gi 3\"")
ap.add_argument("--threads", type=int, default=4, help="CPU threads (kept low: a test, not a build)")
a = ap.parse_args()

root = cc.find_game_root()
tools = cc.Tools(root)
opts = cc.COMPILE_PRESETS[a.preset] if hasattr(cc, "COMPILE_PRESETS") else None
if opts is None:
    opts = next(v for k, v in vars(cc).items() if isinstance(v, dict) and a.preset in v
                and isinstance(v[a.preset], cc.CompileOptions))[a.preset]
args = (opts.vrad_args(True) + opts.hlvrad_args() + a.args.split() + ["-threads", str(a.threads)]
        + ["-game", tools.gamedir])
work = tempfile.mkdtemp(prefix="hl_test_gpuparts_")
name = "hl_test_gpuparts"
src = os.path.join(work, name + ".bsp")
shutil.copyfile(a.bsp, src)
models = cc.prepare_hlvrad(tools, os.path.join(work, name))
print("PARTS map", os.path.basename(a.bsp), f"{os.path.getsize(src) / 1e6:.1f} MB, options:", " ".join(args[:-2]))


def bake(label, exe, env_part=None):
    folder = os.path.join(work, label)
    os.makedirs(folder)
    base = os.path.join(folder, name)
    shutil.copyfile(src, base + ".bsp")
    env = dict(os.environ)
    env.pop("HL_GPU_JOBPART", None)
    if env_part:
        env["HL_GPU_JOBPART"] = env_part
    t = time.time()
    r = subprocess.run([exe] + args + ["-modeldir", models, base], capture_output=True, env=env, cwd=folder,
                       creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    out = r.stdout.decode("latin-1", "replace")
    sec = time.time() - t
    gpu = "on the GPU" if "GPU" in out and "using the CPU" not in out and "can't" not in out.lower() else "CHECK: maybe not on the GPU"
    print(f"PARTS {label}: exit {r.returncode}, {sec:.1f} s, {gpu}")
    if r.returncode:
        print(out[-1500:])
        sys.exit(1)
    return open(base + ".bsp", "rb").read()


def lumps(data):
    out = {}
    for i in range(64):
        _ver, ofs, ln, _four = struct.unpack_from("<iiii", data, 8 + 16 * i)
        out[i] = data[ofs:ofs + ln]
    return out


exe = cc.HLVRAD
runs = [("paced", bake("paced", exe)), ("paced again", bake("paced2", exe))]
for part in [p for p in a.parts.split(",") if p]:
    runs.append((f"part fixed at {part}", bake("part_" + part.replace(".", "_"), exe, part)))
if a.before:
    runs.append(("the build before", bake("before", a.before)))
ref = lumps(runs[0][1])
bad = 0
for label, data in runs[1:]:
    got = lumps(data)
    diff = [i for i in range(64) if got[i] != ref[i] and i != 40]          # (40: the pak file, its zip dates)
    lit = sum(len(ref[i]) for i in (8, 53))
    print(f"PARTS {label} against the first: " + ("identical" if not diff else f"DIFFERENT lumps {diff}")
          + f" ({lit / 1e6:.1f} MB of lightmaps compared)")
    bad += bool(diff)
shutil.rmtree(work, ignore_errors=True)
print("PARTS", "FAILED" if bad else "OK")
sys.exit(1 if bad else 0)
