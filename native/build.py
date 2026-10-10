"""Build the native parts with zig (pip install ziglang):

- hammerless/core/_hlnav.dll from native/hlnav.c (nav sampling, flood fill, nav visibility)
- hammerless/core/hlvvis.exe from native/hlvvis.c (the visibility compiler, a drop-in for vvis)
- hammerless/core/hlvbsp.exe from native/hlvbsp/*.c (the map compiler, a drop-in for vbsp)
- hammerless/core/hlvrad.exe from native/hlvrad/*.c (the lighting compiler, a drop-in for vrad, 64-bit)
- hammerless/core/hlphys.exe (32-bit: hlvrad's helper that runs the game's vphysics.dll for prop collision)
- native/hlvrad32.exe (32-bit hlvrad for checking the 64-bit one gives the same; not shipped)

Floating point must behave exactly like the reference: no fused multiply-add (-ffp-contract=off), no
fast-math, and a baseline CPU (SSE2) so the binaries run anywhere.

    python native/build.py            (both)
    python native/build.py hlvvis     (just one)
"""
import glob
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "..", "hammerless", "core")
FLAGS = ["-O2", "-ffp-contract=off", "-fno-fast-math", "-target", "x86_64-windows-gnu", "-mcpu=baseline"]
FLAGS32 = ["-O2", "-ffp-contract=off", "-fno-fast-math", "-target", "x86-windows-gnu", "-mcpu=pentium4", "-mfpmath=sse"]
TARGETS = {
    "hlnav": (["-shared"], "_hlnav.dll", "hlnav.c"),
    "hlvvis": ([], "hlvvis.exe", "hlvvis.c"),
    "hlvbsp": (["32bit"], "hlvbsp.exe", ["hlvbsp/main.c", "hlvbsp/poly.c", "hlvbsp/map.c", "hlvbsp/instance.c", "hlvbsp/brush.c", "hlvbsp/csg.c",
                                  "hlvbsp/portals.c", "hlvbsp/faces.c", "hlvbsp/detail.c", "hlvbsp/write.c",
                                  "hlvbsp/phys.c", "hlvbsp/disp.c", "hlvbsp/pak.c", "hlvbsp/staticprop.c", "hlvbsp/detail_props.c", "hlvbsp/overlay.c", "hlvbsp/water.c", "hlvbsp/cubemap.c", "hlvbsp/occluder.c"]),
    # 64-bit (room for Embree); prop collision comes from hlphys.exe, 32-bit like the game's vphysics.dll
    "hlvrad": (["-I" + os.path.join(HERE, "third_party", "embree4"), "-I" + os.path.join(HERE, "third_party", "vulkan")], "hlvrad.exe", ["hlvrad/main.c", "hlvrad/bspio.c", "hlvrad/normals.c", "hlvrad/layout.c", "hlvrad/vis.c", "hlvrad/entities.c", "hlvrad/lights.c", "hlvrad/winding.c", "hlvrad/raytrace.c", "hlvrad/anorms.c", "hlvrad/direct.c", "hlvrad/disp.c", "hlvrad/staticprops.c", "hlvrad/bounce.c", "hlvrad/texlights.c", "hlvrad/leafambient.c", "hlvrad/detailprops.c", "hlvrad/spropvlight.c", "hlvrad/threads.c", "hlvrad/skymap.c", "hlvrad/modelfile.c", "hlvrad/embree.c", "hlvrad/gpu.c"]),
    "hlphys": (["32bit"], "hlphys.exe", ["hlvrad/hlphys.c", "hlvrad/physcollide.c", "hlvrad/modelfile.c"]),
    # (the 32-bit build, vphysics inside: for checking the 64-bit one; not shipped)
    "hlvrad32": (["32bit", "-I" + os.path.join(HERE, "third_party", "embree4"), "-I" + os.path.join(HERE, "third_party", "vulkan")], os.path.join("..", "..", "native", "hlvrad32.exe"), ["hlvrad/main.c", "hlvrad/bspio.c", "hlvrad/normals.c", "hlvrad/layout.c", "hlvrad/vis.c", "hlvrad/entities.c", "hlvrad/lights.c", "hlvrad/winding.c", "hlvrad/raytrace.c", "hlvrad/anorms.c", "hlvrad/direct.c", "hlvrad/disp.c", "hlvrad/staticprops.c", "hlvrad/bounce.c", "hlvrad/texlights.c", "hlvrad/leafambient.c", "hlvrad/detailprops.c", "hlvrad/spropvlight.c", "hlvrad/threads.c", "hlvrad/skymap.c", "hlvrad/physcollide.c", "hlvrad/modelfile.c", "hlvrad/embree.c", "hlvrad/gpu.c"]),
}


def build_shaders():
    """hlvrad's GPU programs (hlvrad/shaders/*.comp) into C headers (*.spv.h), with Khronos's glslang (set GLSLANG to
    its glslang.exe, or have it on the PATH). The headers are committed: without glslang they're used as they are."""
    sdir = os.path.join(HERE, "hlvrad", "shaders")
    tool = os.environ.get("GLSLANG") or shutil.which("glslang") or shutil.which("glslangValidator")
    for comp in sorted(glob.glob(os.path.join(sdir, "*.comp"))):
        name = os.path.splitext(os.path.basename(comp))[0]
        header = os.path.join(sdir, name + ".spv.h")
        newest = max(os.path.getmtime(f) for f in glob.glob(os.path.join(sdir, "*.comp")) + glob.glob(os.path.join(sdir, "*.glsl")))
        if os.path.exists(header) and os.path.getmtime(header) >= newest:
            continue
        if not tool:
            if os.path.exists(header):
                print("warning: glslang not found (set GLSLANG): using the committed", os.path.basename(header))
                continue
            sys.exit("glslang is needed to build " + os.path.basename(comp) + " (set GLSLANG to glslang.exe)")
        spv = header[:-2]
        subprocess.run([tool, "-V", "--target-env", "vulkan1.2", "-o", spv, comp], check=True, stdout=subprocess.DEVNULL)
        words = struct.unpack("<%dI" % (os.path.getsize(spv) // 4), open(spv, "rb").read())
        os.remove(spv)
        lines = ["/* %s.comp compiled by build.py (glslang): don't edit */" % name, "static const unsigned int spv_%s[] = {" % name]
        lines += ["    " + ", ".join("0x%08x" % w for w in words[i:i + 8]) + "," for i in range(0, len(words), 8)]
        lines.append("};")
        with open(header, "w") as f:
            f.write(chr(10).join(lines) + chr(10))
        print("built", header)


for name in sys.argv[1:] or list(TARGETS):
    if name.startswith("hlvrad"):
        build_shaders()
    extra, out_name, src = TARGETS[name]
    out = os.path.abspath(os.path.join(CORE, out_name))
    srcs = src if isinstance(src, list) else [src]
    flags = FLAGS32 if "32bit" in extra else FLAGS      # 32-bit: hlvbsp loads the game's (32-bit) vphysics.dll
    extra = [e for e in extra if e != "32bit"]
    cmd = [sys.executable, "-m", "ziglang", "cc", *extra, *flags, "-o", out, *(os.path.join(HERE, s) for s in srcs)]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    stem = out[:-4]
    for leftover in (stem + ".lib", stem + ".pdb", os.path.join(CORE, name + ".lib"), os.path.join(CORE, name + ".pdb")):
        if os.path.exists(leftover):
            os.remove(leftover)
    print("built", out, os.path.getsize(out), "bytes")
