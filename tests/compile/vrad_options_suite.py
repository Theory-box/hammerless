"""hlvrad vs Valve's vrad with the vrad options hlvrad now does itself: -softsun, -scale, -ambient, -sky, -textureshadows.

    python tests/compile/vrad_options_suite.py [--fresh] [--gpu] [map names...]

Builds small test maps (Valve's vbsp + vvis -fast, written under %TEMP%/hammerless_vrad_options), lights each with
Valve's vrad and with hlvrad (Exact: CPU, -noextra, so vrad is deterministic) and compares the whole .bsp.

L4D2's vrad.exe has -softsun and -textureshadows, but not -scale, -ambient or -sky (they are debug options in
Valve's source and L4D2's build refuses them: usage, exit 1). Those are checked another way:
  -scale s      == vrad on the map with every light's brightness times s (all lumps but the entities)
  -ambient      the same code as vrad's own ambient term (0.1 on a map without vis): the no-vis map must match
  -sky          no effect (Valve's source parses it into a variable nothing reads): identical to without it
-textureshadows: hlvrad reads the materials and alpha textures from the game's VPKs itself (as in the add-on's builds).
With supersampling on (no -noextra) hlvrad isn't byte-exact even without these options (vrad reads stack memory it
never set, see direct.c): those cases pass when the option adds no luxels off by over 1% beyond that ("close"; with
-textureshadows' partly see-through values more luxels come out one step apart).
--gpu: also how far hlvrad -gpu (same options) is from the Exact result, per case (reported, not checked).
Exit code 1 when anything differs.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from mapcompiler_suite import Map  # noqa: E402
from hammerless.core.compile import find_game_root  # noqa: E402
from hammerless.core.radprep import export_prop_models  # noqa: E402
from hammerless.core.vpk import GameContent  # noqa: E402

HLVRAD = os.path.join(ROOT, "hammerless", "core", "hlvrad.exe")
OUT = os.path.join(tempfile.gettempdir(), "hammerless_vrad_options")
SKY = "TOOLS/TOOLSSKYBOX"


# ------------------------------------------------------------------ test maps
def _prop(m, model, o, a="0 0 0"):
    m.ents.append('entity { "id" "%d" "classname" "prop_static" "model" "%s" "origin" "%s" "angles" "%s" }'
                  % (m.nid(), model, o, a))


def _courtyard(m, roof=SKY, height=512):
    m.solids = m.room((0, 0, 0), (1024, 1024, height))
    m.solids[1] = m.box((-16, -16, height), (1040, 1040, height + 16), roof)
    m.solids += [m.box((300, 300, 0), (400, 400, 300)), m.box((600, 500, 0), (700, 800, 120))]
    m.ent("info_player_start", "100 100 64")


def _sun(extra=""):
    return ('"angles" "-60 30 0" "pitch" "-60" "_light" "255 240 220 300" "_ambient" "120 140 200 40" ' + extra)


def m_sun(m):
    _courtyard(m)
    m.ent("light_environment", "512 512 400", _sun())


def m_sunspread(m):                 # (the map's own SunSpreadAngle beats -softsun)
    _courtyard(m)
    m.ent("light_environment", "512 512 400", _sun('"SunSpreadAngle" "5"'))


def m_lights(m):
    _courtyard(m, roof="DEV/DEV_MEASUREGENERIC01B")
    m.ent("light_spot", "512 512 450", '"angles" "-90 0 0" "pitch" "-90" "_light" "255 255 255 800" '
          '"_cone" "45" "_inner_cone" "20" "_exponent" "1"')
    m.ent("light", "800 200 100", '"_light" "100 255 100 200" "_fifty_percent_distance" "200" '
          '"_zero_percent_distance" "500"')


def m_foliage(m):                   # (lights.rad "forcetextureshadow" props)
    _courtyard(m, height=768)
    m.ent("light_environment", "512 512 600", '"angles" "-50 30 0" "pitch" "-50" "_light" "255 240 220 400" '
          '"_ambient" "120 140 200 60"')
    _prop(m, "models/props_foliage/tree_deciduous_01a.mdl", "300 300 0")
    _prop(m, "models/props_foliage/flower_barrel.mdl", "700 300 0")
    _prop(m, "models/props_foliage/Tree_base_bushes01.mdl", "700 700 0", "0 40 0")


def m_fence(m):                     # (grate brushes, "%compilepassbullets" alpha-tested materials)
    _courtyard(m)
    m.ent("light_environment", "512 512 400", _sun())
    m.solids.append(m.box((100, 600, 0), (900, 604, 300), "METAL/FENCE_BARBED"))
    m.solids.append(m.box((450, 100, 200), (850, 450, 204), "METAL/METALGRATE004A"))


MAPS = {"sun": m_sun, "sunspread": m_sunspread, "lights": m_lights, "foliage": m_foliage, "fence": m_fence}


def build_map(name: str, root: str, vis: bool = True) -> str:
    """the map compiled (Valve's vbsp, vvis -fast), unlit: <OUT>/<name>/hl_test_<name>.prevrad.bsp"""
    d = os.path.join(OUT, name)
    pre = os.path.join(d, f"hl_test_{name}.prevrad.bsp")
    if os.path.exists(pre):
        return pre
    os.makedirs(d, exist_ok=True)
    m = Map()
    MAPS[name.removesuffix("_novis")](m)
    base = os.path.join(d, "hl_test_" + name)
    with open(base + ".vmf", "w") as f:
        f.write(m.text())
    game = os.path.join(root, "left4dead2")
    subprocess.run([os.path.join(root, "bin", "vbsp.exe"), "-game", game, base], capture_output=True)
    if vis:
        subprocess.run([os.path.join(root, "bin", "vvis.exe"), "-fast", "-game", game, base], capture_output=True)
    shutil.copy(base + ".bsp", pre)
    return pre


# ------------------------------------------------------------------ reading maps
def _lump(bsp: bytes, i: int) -> bytes:
    ofs, n = struct.unpack_from("<ii", bsp, 12 + 16 * i)
    return bsp[ofs:ofs + n]


# ------------------------------------------------------------------ lighting and comparing
def lightmap_stats(a: bytes, b: bytes) -> tuple[float, float]:
    """(% of luxels that differ at all, % differing by over 1% in brightness)"""
    import numpy as np

    def dec(bsp):
        x = np.frombuffer(_lump(bsp, 53), np.uint8).reshape(-1, 4)
        return x[:, :3].astype(np.float64) * np.exp2(x[:, 3].view(np.int8).astype(np.float64))[:, None]
    la, lb = dec(a), dec(b)
    if len(la) != len(lb):
        return 100.0, 100.0
    sa, sb = la.sum(1), lb.sum(1)
    rel = np.abs(sa - sb) / np.maximum(np.maximum(sa, sb), 1.0)
    return 100.0 * float((np.abs(la - lb).max(1) > 0).mean()), 100.0 * float((rel > 0.01).mean())


def _lumps(b: bytes) -> list[tuple]:
    return [struct.unpack_from("<iiii", b, 8 + 16 * i) for i in range(64)]


def lumps_differing(a: bytes, b: bytes, skip=()) -> list[int]:
    la, lb = _lumps(a), _lumps(b)
    out = []
    for i in range(64):
        if i in skip:
            continue
        va, oa, na, _ = la[i]
        vb, ob, nb, _ = lb[i]
        da, db = a[oa:oa + na], b[ob:ob + nb]
        if i == 35 and na >= 4 and nb >= 4:       # (game lump: its offsets are absolute)
            def rel(x, o):
                x = bytearray(x)
                for g in range(struct.unpack_from("<i", x, 0)[0]):
                    q = 4 + 16 * g + 8
                    struct.pack_into("<i", x, q, struct.unpack_from("<i", x, q)[0] - o)
                return bytes(x)
            da, db = rel(da, oa), rel(db, ob)
        if da != db or va != vb:
            out.append(i)
    return out


def scale_lights(src: str, dst: str, factor: float) -> None:
    """the map with every light's brightness (4th number of _light/_ambient and their HDR keys) times factor"""
    d = bytearray(open(src, "rb").read())
    ofs, n = struct.unpack_from("<ii", d, 12)
    ents = d[ofs:ofs + n].decode("latin-1")

    def rep(m):
        parts = m.group(2).split()
        if len(parts) == 4:
            parts[3] = repr(float(parts[3]) * factor)
        return f'"{m.group(1)}" "{" ".join(parts)}"'

    new = re.sub(r'"(_light|_ambient|_lightHDR|_ambientHDR)" "([^"]*)"', rep, ents).encode("latin-1")
    pos = len(d) + (-len(d)) % 4
    d += b"\0" * (pos - len(d)) + new
    struct.pack_into("<ii", d, 12, pos, len(new))
    open(dst, "wb").write(bytes(d))


class Runner:
    def __init__(self, root: str):
        self.root = root
        self.game = os.path.join(root, "left4dead2")
        self.content = None

    def modeldir(self, pre: str) -> str:
        d = os.path.join(os.path.dirname(pre), "models_out")
        if not os.path.isdir(d):
            if self.content is None:
                self.content = GameContent(self.root)
            export_prop_models(pre, self.content, d)
        return d

    def vrad(self, src: str, out: str, opts: list[str]) -> int:
        shutil.copy(src, out + ".bsp")
        r = subprocess.run([os.path.join(self.root, "bin", "vrad.exe"), "-hdr", *opts, "-game", self.game, out],
                           capture_output=True, text=True)
        open(out + ".log", "w").write(r.stdout + r.stderr)
        return r.returncode

    def hlvrad(self, src: str, out: str, opts: list[str], modeldir: str) -> int:
        shutil.copy(src, out + ".bsp")
        r = subprocess.run([HLVRAD, "-hdr", *opts, "-game", self.game, "-modeldir", modeldir, out],
                           capture_output=True, text=True)
        open(out + ".log", "w").write(r.stdout + r.stderr)
        return r.returncode


def case(run: Runner, name: str, opts: list[str], vis=True, check="same", gpu=False) -> tuple[str | None, str]:
    """(None when it matches, else what differs; a note)"""
    pre = build_map(name, run.root, vis)
    md = run.modeldir(pre)
    d = os.path.dirname(pre)
    tag = re.sub(r"[^a-z0-9]+", "_", " ".join(opts).lower()).strip("_") or "plain"
    ours = os.path.join(d, "hl_" + tag)
    if run.hlvrad(pre, ours, opts, md):
        return "hlvrad failed: " + open(ours + ".log").read()[-300:], ""
    b = open(ours + ".bsp", "rb").read()
    note = ""
    if gpu:
        g = os.path.join(d, "gpu_" + tag)
        if run.hlvrad(pre, g, ["-gpu", *opts], md) == 0:
            same, big = lightmap_stats(b, open(g + ".bsp", "rb").read())
            note = f" [gpu vs exact: {same:.2f}% luxels differ, {big:.3f}% by over 1%]"
    if check == "scale":                       # vrad on the brighter map
        factor = float(opts[opts.index("-scale") + 1])
        scaled = os.path.join(d, "scaled_" + tag + ".bsp")
        scale_lights(pre, scaled, factor)
        rest = [o for i, o in enumerate(opts) if o != "-scale" and (i == 0 or opts[i - 1] != "-scale")]
        valve = os.path.join(d, "vr_" + tag)
        run.vrad(scaled, valve, rest)
        diff = lumps_differing(open(valve + ".bsp", "rb").read(), b, skip=(0,))
    elif check == "noeffect":                  # the same as without the option
        k = opts.index("-sky")
        rest = opts[:k] + opts[k + 2:]
        plain = os.path.join(d, "hl_plain_" + tag)
        run.hlvrad(pre, plain, rest, md)
        a = open(plain + ".bsp", "rb").read()
        diff = [] if a == b else lumps_differing(a, b)
    else:
        valve = os.path.join(d, "vr_" + tag)
        if run.vrad(pre, valve, opts):
            return "vrad failed", note
        a = open(valve + ".bsp", "rb").read()
        diff = [] if a == b else lumps_differing(a, b)
        if diff and check == "close":          # (supersampling: no worse than without the option)
            tested = {"-softsun": 2, "-textureshadows": 1}
            rest, i = [], 0
            while i < len(opts):
                if opts[i] in tested:
                    i += tested[opts[i]]
                    continue
                rest.append(opts[i])
                i += 1
            btag = re.sub(r"[^a-z0-9]+", "_", " ".join(rest).lower()).strip("_") or "plain"
            bo, bv = os.path.join(d, "hl_" + btag), os.path.join(d, "vr_" + btag)
            run.hlvrad(pre, bo, rest, md)
            run.vrad(pre, bv, rest)
            with_opt = lightmap_stats(a, b)
            without = lightmap_stats(open(bv + ".bsp", "rb").read(), open(bo + ".bsp", "rb").read())
            note = (f" (close: {with_opt[0]:.2f}% luxels differ, {with_opt[1]:.3f}% by over 1%; without the option "
                    f"{without[0]:.2f}% / {without[1]:.3f}%)") + note
            if with_opt[1] <= without[1] + 0.1:          # (no more luxels off by over 1%)
                diff = []
    return (f"lumps {diff} differ" if diff else None), note


CASES = [
    ("sun", ["-noextra"], True, "same"),
    ("sun", ["-noextra", "-softsun", "5"], True, "same"),
    ("sun", ["-noextra", "-softsun", "13.7"], True, "same"),
    ("sun", ["-softsun", "5"], True, "close"),                      # (supersampling on)
    ("sunspread", ["-noextra", "-softsun", "5"], True, "same"),
    ("lights", ["-noextra", "-softsun", "5"], True, "same"),
    ("sun", ["-noextra", "-scale", "2"], True, "scale"),
    ("lights", ["-noextra", "-scale", "2"], True, "scale"),
    ("lights", ["-noextra", "-scale", "0.5"], True, "scale"),
    ("sun_novis", ["-noextra"], False, "same"),                      # (vrad's ambient term, 0.1)
    ("sun", ["-noextra", "-sky", "4"], True, "noeffect"),
    ("foliage", ["-noextra", "-textureshadows"], True, "same"),      # (no effect without -StaticPropPolys)
    ("foliage", ["-noextra", "-StaticPropPolys"], True, "same"),
    ("foliage", ["-noextra", "-StaticPropPolys", "-textureshadows"], True, "same"),
    ("foliage", ["-noextra", "-StaticPropPolys", "-textureshadows", "-StaticPropLighting"], True, "same"),
    ("foliage", ["-StaticPropPolys", "-textureshadows"], True, "close"),
    ("foliage", ["-final", "-StaticPropPolys", "-textureshadows"], True, "close"),
    ("fence", ["-noextra"], True, "same"),
    ("fence", ["-noextra", "-textureshadows"], True, "same"),
]


def main(argv: list[str]) -> int:
    names = [a for a in argv if not a.startswith("--")]
    if "--fresh" in argv:
        shutil.rmtree(OUT, ignore_errors=True)
    run = Runner(find_game_root())
    bad = 0
    for name, opts, vis, check in CASES:
        if names and name not in names:
            continue
        t = time.time()
        why, note = case(run, name, opts, vis, check, "--gpu" in argv)
        bad += why is not None
        how = {"scale": " (vs vrad on the map with brighter lights)", "noeffect": " (vs hlvrad without it)"}.get(check, "")
        word = "DIFF" if why else ("CLOSE" if note.startswith(" (close") else "SAME")
        print(f"{word} {name} {' '.join(opts)}{how} ({time.time() - t:.0f} s)" + (": " + why if why else "") + note,
              flush=True)
    print()
    print("all match" if not bad else f"{bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
