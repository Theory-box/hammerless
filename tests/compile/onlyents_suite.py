"""hlvbsp -onlyents against Valve's vbsp -onlyents (the smart builds' entity-only step), byte for byte.

Each map is compiled once with Valve's vbsp (optionally lit: vvis -fast and vrad), then edited step by step as
a mapper would: a light moved, a light named, a point entity added and one removed, a static prop moved and its
model changed, an env_cubemap and an info_overlay added, a brush entity's key changed, a new named light. Each step
runs Valve's vbsp -onlyents on one copy of the map and hlvbsp -onlyents on another and compares the two files
whole (every lump, the pakfile and the header). The next step starts from that result with stale.txt taken out
(buildplan.strip_stale, as the add-on does after each smart build), so later steps also read a pakfile Python
wrote.

    python tests/compile/onlyents_suite.py                    # the generated test maps (mapcompiler_suite)
    python tests/compile/onlyents_suite.py props water        # the ones whose name contains these
    python tests/compile/onlyents_suite.py --lit              # lit first (lumps vrad writes)
    python tests/compile/onlyents_suite.py --vmf path/to/a.vmf [--dir folder_of_vmfs]
    python tests/compile/onlyents_suite.py --built path/to/map.bsp      # a map the add-on built (its .vmf beside it)

Needs Left 4 Dead 2 and its Authoring Tools. Output in %TEMP%/hammerless_onlyents_suite.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import bspdump  # noqa: E402
import mapcompiler_suite as suite  # noqa: E402
from hammerless.core.buildplan import strip_stale  # noqa: E402
from hammerless.core.compile import HLVBSP, find_game_root  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "hammerless_onlyents_suite")
THREADS = ["-threads", "2"]        # (vvis / vrad for --lit: leave the machine some room)


# ---------------------------------------------------------------- .vmf text
def _blocks(text: str, keyword: str = "entity") -> list[tuple[int, int]]:
    """(start, end) of each top-level `keyword { ... }` block."""
    out, depth, i, n, quoted, start = [], 0, 0, len(text), False, None
    word = re.compile(r"\s*" + keyword + r"\s*\{", re.I)
    while i < n:
        c = text[i]
        if quoted:
            quoted = c != '"'
        elif c == '"':
            quoted = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                out.append((start, i + 1))
                start = None
        elif depth == 0 and start is None:
            m = word.match(text, i)
            if m and (i == 0 or not text[i - 1].isalnum()):
                start = i
                depth = 1
                i = m.end()
                continue
        i += 1
    return out


def _keys(block: str) -> dict[str, tuple[int, int, str]]:
    """The block's own keys (not its solids' or connections'): key -> (value start, value end, value)."""
    out, depth, i = {}, 0, 0
    pair = re.compile(r'"([^"]*)"\s+"([^"]*)"')
    while i < len(block):
        c = block[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == '"':
            m = pair.match(block, i)
            if m:
                if depth == 1:
                    out.setdefault(m.group(1).lower(), (m.start(2), m.end(2), m.group(2)))
                i = m.end()
                continue
            i = block.index('"', i + 1)
        i += 1
    return out


def _set(block: str, key: str, value: str) -> str:
    k = _keys(block)
    if key.lower() in k:
        a, b, _v = k[key.lower()]
        return block[:a] + value + block[b:]
    at = block.index("{") + 1
    return block[:at] + f' "{key}" "{value}"' + block[at:]


def _ents(text: str) -> list[tuple[int, int, dict]]:
    return [(a, b, {k: v[2] for k, v in _keys(text[a:b]).items()}) for a, b in _blocks(text)]


def _first(text: str, test) -> tuple[int, int] | None:
    for a, b, k in _ents(text):
        if test(k, text[a:b]):
            return a, b
    return None


def _point(text: str) -> str:
    """Somewhere inside the map: the first point entity's origin (players, lights)."""
    for _a, _b, k in _ents(text):
        if k.get("classname", "").startswith(("info_player", "light")) and "origin" in k:
            return k["origin"]
    for _a, _b, k in _ents(text):
        if "origin" in k:
            return k["origin"]
    return "0 0 64"


def _shift(origin: str, dx: float, dy: float = 0, dz: float = 0) -> str:
    x, y, z = (float(v) for v in origin.split()[:3])
    return f"{x + dx:g} {y + dy:g} {z + dz:g}"


def _edit_key(text: str, test, key: str, value) -> str | None:
    span = _first(text, test)
    if span is None:
        return None
    a, b = span
    block = text[a:b]
    v = value(_keys(block)) if callable(value) else value
    return text[:a] + _set(block, key, v) + text[b:]


def _add(text: str, body: str) -> str:
    return text.rstrip() + "\nentity\n{\n" + body + "\n}\n"


_ID = [990000]


def _nid() -> int:
    _ID[0] += 1
    return _ID[0]


def _is_light(k, _b):
    return k.get("classname", "") in ("light", "light_spot") and "origin" in k


def e_move_light(t):
    return _edit_key(t, _is_light, "origin", lambda k: _shift(k["origin"][2], 16, 8))


def e_name_light(t):
    return _edit_key(t, lambda k, b: _is_light(k, b) and not k.get("targetname"), "targetname", "hl_oe_lamp")


def e_add_point(t):
    return _add(t, f'"id" "{_nid()}" "classname" "info_target" "targetname" "hl_oe_target" '
                   f'"origin" "{_shift(_point(t), 8, 8, 8)}"')


def e_remove_point(t):
    keep = ("worldspawn", "info_player_start", "light", "light_spot", "light_environment", "prop_static")
    spans = [(a, b) for a, b, k in _ents(t) if "origin" in k and "solid" not in t[a:b]
             and k.get("classname") not in keep]
    if not spans:
        return None
    a, b = spans[-1]
    return t[:a] + t[b:]


def _is_prop(k, _b):
    return k.get("classname") == "prop_static"


def e_prop(t):
    out = _edit_key(t, _is_prop, "origin", lambda k: _shift(k["origin"][2], 24, -8))
    if out is None:        # (no static prop yet: add one)
        return _add(t, f'"id" "{_nid()}" "classname" "prop_static" "model" "models/props_junk/wood_crate001a.mdl" '
                       f'"solid" "6" "skin" "0" "angles" "0 30 0" "origin" "{_shift(_point(t), 32, 0, -32)}"')
    return _edit_key(out, _is_prop, "angles", "0 45 0")


def e_prop_model(t):
    return _edit_key(t, _is_prop, "model", "models/props_junk/wood_crate002a.mdl")


def e_add_cubemap(t):
    return _add(t, f'"id" "{_nid()}" "classname" "env_cubemap" "cubemapsize" "0" "sides" "" '
                   f'"origin" "{_shift(_point(t), 0, 16, 16)}"')


def e_add_overlay(t):
    """An info_overlay on a world side: -onlyents doesn't make overlays (the lump stays), the entity just goes."""
    m = re.search(r'side\s*\{\s*"id"\s*"(\d+)"', t)
    if not m:
        return None
    return _add(t, f'"id" "{_nid()}" "classname" "info_overlay" "material" "decals/asphalt/asphalt1" '
                   f'"sides" "{m.group(1)}" "RenderOrder" "0" "StartU" "0" "EndU" "1" "StartV" "0" "EndV" "1" '
                   f'"BasisOrigin" "{_point(t)}" "BasisU" "1 0 0" "BasisV" "0 1 0" "BasisNormal" "0 0 1" '
                   f'"uv0" "-16 -16 0" "uv1" "-16 16 0" "uv2" "16 16 0" "uv3" "16 -16 0" '
                   f'"origin" "{_point(t)}"')


def e_brush_entity(t):
    return _edit_key(t, lambda k, b: k.get("classname", "").startswith("func_") and "solid" in b
                     and k.get("classname") not in ("func_detail", "func_instance"), "rendercolor", "255 128 0")


def e_add_light(t):
    return _add(t, f'"id" "{_nid()}" "classname" "light" "targetname" "hl_oe_switch" "_light" "255 200 150 300" '
                   f'"style" "0" "origin" "{_shift(_point(t), -16, 0, 32)}"')


EDITS = [("unchanged", lambda t: t), ("move light", e_move_light), ("name a light", e_name_light),
         ("add a point entity", e_add_point), ("remove a point entity", e_remove_point),
         ("move a static prop", e_prop), ("change a prop's model", e_prop_model), ("add an env_cubemap", e_add_cubemap),
         ("add an info_overlay", e_add_overlay), ("brush entity key", e_brush_entity),
         ("add a named light", e_add_light)]


# ---------------------------------------------------------------- running
def _tables(base: str, root: str, content) -> list[str]:
    from hammerless.core.mapcompiler import (write_cubemap_materials, write_detail_file, write_fgd_table,
                                             write_material_table, write_prop_table, write_surfaceprops)
    game = os.path.join(root, "left4dead2")
    vmf = base + ".vmf"
    write_material_table(base + ".hlvbsp_materials.txt", vmf, content, game)
    write_surfaceprops(base + ".hlvbsp_surfaceprops.txt", content)
    detail = write_detail_file(base + ".hlvbsp_detail.txt", vmf, content, game)
    write_prop_table(base + ".hlvbsp_props.txt", vmf, content, game, detail)
    write_cubemap_materials(base + ".hlvbsp_cubemaps.txt", vmf, content, game)
    write_fgd_table(base + ".hlvbsp_fgd.txt", root)
    return ["-materials", base + ".hlvbsp_materials.txt", "-surfaceprops", base + ".hlvbsp_surfaceprops.txt",
            "-props", base + ".hlvbsp_props.txt", "-detail", base + ".hlvbsp_detail.txt",
            "-cubemaps", base + ".hlvbsp_cubemaps.txt", "-fgd", base + ".hlvbsp_fgd.txt"]


def _run(cmd: list[str], log: str) -> int:
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    with open(log, "w", encoding="utf-8") as f:
        f.write(r.stdout + r.stderr)
    return r.returncode


def compile_base(name: str, text: str, root: str, lit: bool, instances=()) -> str | None:
    """The map compiled by Valve's vbsp (and lit), cached while the .vmf is unchanged. Returns the .bsp."""
    d = os.path.join(OUT, name + ("_lit" if lit else ""), "base")
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, "hl_test_oe_" + name[:40])
    stamp = hashlib.sha1((text + repr(lit)).encode("utf-8", "replace")).hexdigest()
    if os.path.exists(base + ".bsp") and os.path.exists(base + ".stamp") and open(base + ".stamp").read() == stamp:
        return base + ".bsp"
    with open(base + ".vmf", "w", encoding="utf-8") as f:
        f.write(text)
    for key, src in instances:
        rel = os.path.splitext(key.replace("\\", "/"))[0] + ".vmf"
        dst = os.path.join(d, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    for ext in (".bsp", ".prt", ".lin"):
        if os.path.exists(base + ext):
            os.remove(base + ext)
    game = ["-game", os.path.join(root, "left4dead2")]
    bin_ = os.path.join(root, "bin")
    _run([os.path.join(bin_, "vbsp.exe")] + game + [base], base + ".vbsp.log")
    if not os.path.exists(base + ".bsp"):
        return None
    # (a leaked map is still written: unlit, but its entities are as good a test - an instance file on its own)
    if lit and not os.path.exists(base + ".lin"):
        _run([os.path.join(bin_, "vvis.exe"), "-fast"] + THREADS + game + [base], base + ".vvis.log")
        _run([os.path.join(bin_, "vrad.exe"), "-hdr", "-fast"] + THREADS + game + [base], base + ".vrad.log")
    with open(base + ".stamp", "w") as f:
        f.write(stamp)
    return base + ".bsp"


def mask(data: bytes) -> bytes:
    """vbsp leaves bytes of a static prop record uninitialised (the lighting origin unless the prop has one, the 3
    after disableX360: different every run, as bspdump notes): zeroed, so the rest can be compared exactly."""
    out = bytearray(data)
    _v, off, length, _cc = struct.unpack_from("<4i", out, 8 + 16 * 35)
    if length < 4:
        return bytes(out)
    for k in range(struct.unpack_from("<i", out, off)[0]):
        gid, _fl, gver, gofs, glen = struct.unpack_from("<4sHHii", out, off + 4 + 16 * k)
        if gid != b"prps" or gver != 9:
            continue
        at = gofs + 4 + 128 * struct.unpack_from("<i", out, gofs)[0]
        at += 4 + 2 * struct.unpack_from("<i", out, at)[0]
        for i in range(struct.unpack_from("<i", out, at)[0]):
            p = at + 4 + 72 * i
            if not out[p + 31] & 0x2:
                out[p + 44:p + 56] = bytes(12)
            out[p + 69:p + 72] = bytes(3)
    return bytes(out)


def diff(a: bytes, b: bytes) -> list[str]:
    """What differs between two maps (empty: the same bytes, but for vbsp's uninitialised ones)."""
    a, b = mask(a), mask(b)
    if a == b:
        return []
    out = []
    for i in range(64):
        ha, hb = struct.unpack_from("<4i", a, 8 + 16 * i), struct.unpack_from("<4i", b, 8 + 16 * i)
        da, db = a[ha[1]:ha[1] + ha[2]], b[hb[1]:hb[1] + hb[2]]
        if da != db:
            out.append(f"lump {i} {bspdump.NAMES[i]}")
        elif ha != hb:
            out.append(f"lump {i} {bspdump.NAMES[i]} header")
    if not out:
        out.append("header / padding" if len(a) == len(b) else f"size {len(a)} vs {len(b)}")
    return out


def run_map(name: str, text: str, root: str, lit: bool, content, instances=(), built: str | None = None) -> tuple[int, int]:
    """Every edit in turn on one map (built: start from this compiled map). Returns (same, different)."""
    bsp = built or compile_base(name, text, root, lit, instances)
    if bsp is None and _blocks(text):
        # no map from it alone (an instance file of only point entities): its entities in a closed room instead
        m = suite.Map()
        suite.m_box(m)
        text = m.text().rstrip() + "\n" + "\n".join(text[a:b] for a, b in _blocks(text)) + "\n"
        name += "_in_a_room"
        bsp = compile_base(name, text, root, lit, instances)
    if bsp is None:
        print(f"SKIP {name}: Valve's vbsp made no map", flush=True)
        return 0, 0
    game = ["-game", os.path.join(root, "left4dead2")]
    d = os.path.join(OUT, name + ("_lit" if lit else ""))
    current, data = text, open(bsp, "rb").read()
    same = differ = 0
    for step, (label, edit) in enumerate(EDITS):
        edited = edit(current)
        if edited is None:
            continue
        results = {}
        for who in ("valve", "ours"):
            sd = os.path.join(d, f"{step:02d}_{who}")
            os.makedirs(sd, exist_ok=True)
            base = os.path.join(sd, os.path.splitext(os.path.basename(bsp))[0])
            with open(base + ".vmf", "w", encoding="utf-8") as f:
                f.write(edited)
            for key, src in instances:
                dst = os.path.join(sd, os.path.splitext(key.replace("\\", "/"))[0] + ".vmf")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
            with open(base + ".bsp", "wb") as f:
                f.write(data)
            if who == "valve":
                code = _run([os.path.join(root, "bin", "vbsp.exe"), "-onlyents"] + game + [base], base + ".oe.log")
            else:
                code = _run([HLVBSP, "-onlyents"] + game + _tables(base, root, content) + [base], base + ".oe.log")
            results[who] = (code, open(base + ".bsp", "rb").read(), base + ".bsp")
        (vc, vdata, vpath), (oc, odata, _opath) = results["valve"], results["ours"]
        if vc != 0:
            print(f"  {name}: {label}: Valve's vbsp -onlyents failed ({vc}): skipped", flush=True)
            continue
        problems = diff(vdata, odata) if oc == 0 else [f"hlvbsp failed ({oc})"]
        same += not problems
        differ += bool(problems)
        print(f"  {'SAME' if not problems else 'DIFF'} {name}: {label}" + (": " + ", ".join(problems) if problems else ""),
              flush=True)
        # the next edit starts from this result, stale.txt taken out as the add-on does
        nxt = os.path.join(d, "next.bsp")
        with open(nxt, "wb") as f:
            f.write(vdata)
        strip_stale(nxt)
        current, data = edited, open(nxt, "rb").read()
    return same, differ


def main(argv: list[str]) -> int:
    root = find_game_root()
    if not root:
        print("Left 4 Dead 2 not found")
        return 2
    from hammerless.core.mapcompiler import vmf_instances
    from hammerless.core.vpk import GameContent
    content = GameContent(root)
    lit = "--lit" in argv
    maps: list[tuple[str, str, list, str | None]] = []
    paths, builts = [], []
    for flag in ("--vmf", "--dir", "--built"):
        while flag in argv:
            i = argv.index(flag)
            p = argv[i + 1]
            del argv[i:i + 2]
            if flag == "--vmf":
                paths.append(p)
            elif flag == "--built":
                builts.append(p)
            else:
                for dirpath, _, files in os.walk(p):
                    paths += [os.path.join(dirpath, f) for f in sorted(files) if f.lower().endswith(".vmf")]
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as f:
            maps.append(("vmf_" + os.path.splitext(os.path.basename(p))[0].lower(), f.read(), vmf_instances(p), None))
    for p in builts:
        vmf = os.path.splitext(p)[0] + ".vmf"
        with open(vmf, encoding="utf-8", errors="replace") as f:
            maps.append(("built_" + os.path.splitext(os.path.basename(p))[0].lower(), f.read(), vmf_instances(vmf), p))
    if not paths and not builts:
        wanted = [a for a in argv if not a.startswith("--")]
        for n, make in suite.MAPS.items():
            if wanted and not any(w in n for w in wanted):
                continue
            m = suite.Map()
            make(m)
            maps.append((n, m.text(), [], None))
    same = differ = 0
    for name, text, inst, built in maps:
        s, d = run_map(name, text, root, lit, content, inst, built)
        same += s
        differ += d
    print(f"\n{same} edits the same as Valve's vbsp -onlyents, {differ} different (output in {OUT})")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
