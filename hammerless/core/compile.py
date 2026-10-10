"""Locate L4D2, run vbsp/vvis/vrad, parse logs, launch the game."""
from __future__ import annotations

import os
import sys
import queue
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field, replace

DEFAULT_GAME_ROOTS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Left 4 Dead 2",
    r"D:\SteamLibrary\steamapps\common\Left 4 Dead 2",
    r"C:\Program Files\Steam\steamapps\common\Left 4 Dead 2",
]

@dataclass
class CompileOptions:
    vis: str = "FULL"           # SKIP / FAST / FULL
    rad: str = "NORMAL"         # SKIP / FAST (vrad -fast) / NORMAL (with the lighting settings below) / FINAL (older
                                # builds: -final and prop lighting with full-model shadows)
    hdr: str = "HDR"            # LDR / HDR / BOTH (L4D2 only uses HDR: Valve's maps have no LDR lighting)
    static_prop_lighting: bool = False
    # lighting settings (the Lighting panel's; its Quality presets fill them in)
    sky_rays: float = 1.0          # times vrad's sky rays (16: Final, -extrasky)
    supersample: bool = True       # re-light luxels where the light changes sharply (off: -noextra)
    bounces: int = 100             # -bounce
    prop_polys: bool = False       # props cast shadows with their full model, not their collision (-StaticPropPolys)
    patch_size: float = 4.0        # bounce patches, in luxels (vrad's 4; smaller: finer bounced light, -chop/-maxchop)
    # (the Hammerless light compiler's own, beyond vrad: Valve's ignores them)
    ss_points: int = 4             # supersampling points across a luxel (vrad's 4 x 4)
    ss_passes: int = 4
    ss_threshold: float = 0.0625   # the brightness step between neighbours that triggers it
    fix_quirks: bool = False       # leave out vrad's oddities (see hlvrad -fixquirks)
    gi: bool = False               # bounced light by final gathering on the GPU (hlvrad -gi), not vrad's patches
    gi_rays: int = 1024            # its rays per luxel
    extra_vbsp: str = ""
    extra_vvis: str = ""
    extra_vrad: str = ""
    vis_tool: str = "VALVE"     # VALVE: L4D2's vvis.exe / HAMMERLESS: hlvvis.exe (same results, faster)
    map_tool: str = "HAMMERLESS"  # HAMMERLESS: hlvbsp.exe (same map as vbsp) / VALVE: L4D2's vbsp.exe
    light_tool: str = "VALVE"   # VALVE: vrad's lightmaps / HAMMERLESS: hlvrad.exe (vrad's lighting, faster) /
                                # CYCLES: Blender bakes them after vrad (bake_handler)
    light_exact: bool = False   # hlvrad on the CPU, byte for byte vrad's (else -gpu: much faster, looks the same)
    cycles_samples: int = 1024
    cycles_stitch: bool = True     # make neighbouring faces' lightmaps agree along shared edges
    cycles_denoise: bool = False   # measured: OpenImageDenoise smears the packed bake (7.7% off vs 1.3% raw)
    sky_key: str = ""              # sky light from a picture of the sky (<map>.hlsky_src.npy): its fingerprint

    def vbsp_args(self) -> list[str]:
        return self.extra_vbsp.split()

    def vvis_args(self) -> list[str] | None:
        if self.vis == "SKIP":
            return None
        return (["-fast"] if self.vis == "FAST" else []) + self.extra_vvis.split()

    def vrad_args(self) -> list[str] | None:
        if self.rad == "SKIP":
            return None
        args = {"LDR": ["-ldr"], "HDR": ["-hdr"], "BOTH": ["-both"]}[self.hdr]
        if self.rad == "FAST":
            args.append("-fast")
        elif self.rad == "FINAL":
            args += ["-final"]
        else:
            if self.sky_rays != 1:
                args += ["-extrasky", _num(self.sky_rays)]
            if not self.supersample:
                args.append("-noextra")
            if self.bounces != 100:
                args += ["-bounce", str(self.bounces)]
            if self.patch_size != 4:
                args += ["-chop", _num(self.patch_size), "-maxchop", _num(self.patch_size)]
        if self.static_prop_lighting or self.rad == "FINAL":
            args.append("-StaticPropLighting")
        if self.prop_polys or self.rad == "FINAL":
            args.append("-StaticPropPolys")
        return args + self.extra_vrad.split()

    def hlvrad_args(self) -> list[str]:
        """The Hammerless light compiler's own options (after vrad_args)."""
        # (no capable GPU: hlvrad warns and uses the CPU. On the GPU its few CPU rays go through Embree, whose tree
        # builds far quicker than vrad's: the same map, measured)
        args = [] if self.light_exact else ["-gpu", "-embree"]
        if self.ss_points != 4:
            args += ["-sspoints", str(self.ss_points)]
        if self.ss_passes != 4:
            args += ["-sspasses", str(self.ss_passes)]
        if self.ss_threshold != 0.0625:
            args += ["-ssthreshold", _num(self.ss_threshold)]
        if self.fix_quirks:
            args.append("-fixquirks")
        if self.gi and not self.light_exact and self.bounces > 0:
            # (a bounce a pass: each passes on about a twentieth of the light, so 8 is as good as vrad's 100)
            args += ["-gi", str(min(self.bounces, GI_MAX_PASSES)), "-girays", str(self.gi_rays)]
        return args


GI_MAX_PASSES = 8


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


VIS_RANK = {"SKIP": 0, "FAST": 1, "FULL": 2}


# what lighting is baked with: a change only relights. Settings count only where they apply: the normal-lighting ones
# not with vrad -fast, the Hammerless light compiler's own only when it's used (the sky picture too), Cycles' with it
_LIGHT_FIELDS = ("hdr", "static_prop_lighting", "extra_vrad", "prop_polys")
_NORMAL_FIELDS = ("sky_rays", "supersample", "bounces", "patch_size")
_HLVRAD_FIELDS = ("light_exact", "ss_points", "ss_passes", "ss_threshold", "fix_quirks", "sky_key", "gi", "gi_rays")
# (settings added later: a build from before them was baked as their default)
_FIELD_DEFAULTS = {"gi": "False", "gi_rays": "1024"}
_CYCLES_FIELDS = ("cycles_samples", "cycles_denoise", "cycles_stitch")
_ALL_LIGHT_FIELDS = _LIGHT_FIELDS + _NORMAL_FIELDS + _HLVRAD_FIELDS + _CYCLES_FIELDS


def _field(text: str, name: str) -> str | None:
    m = re.search(r"\b%s=('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|[^,)]+)" % name, text)
    return m.group(1) if m else None


def _opts_rest(text: str) -> str:
    """Compile options without the visibility and lighting: those are tracked apart (a more complete vis
    serves a lesser one; lighting can be added or redone on a map compiled without it). Which map, vis or
    light compiler ran (Valve's or ours) doesn't count: they give the same map (Cycles is part of the lighting)."""
    for name in _ALL_LIGHT_FIELDS:
        v = _field(text, name)
        if v is not None:
            text = text.replace(f", {name}={v}", "", 1)
    text = re.sub(r", (vis|map|light)_tool='\w+'", "", text)
    return re.sub(r"(vis|rad)='\w+'", r"\1='*'", text)


HLVVIS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hlvvis.exe")


HLVVIS_OPTIONS = {"-fast": 0, "-threads": 1, "-radius_override": 1}   # vvis options ours knows: arguments each


def _hlvvis_knows(args: list[str]) -> bool:
    i = 0
    while i < len(args):
        n = HLVVIS_OPTIONS.get(args[i].lower())
        if n is None or i + n >= len(args):
            return False
        i += 1 + n
    return True


def use_hlvvis(opts: "CompileOptions") -> bool:
    """Our vis compiler runs when chosen, present, and not given vvis options it doesn't know."""
    return opts.vis_tool == "HAMMERLESS" and os.path.exists(HLVVIS) and _hlvvis_knows(opts.extra_vvis.split())


HLVRAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hlvrad.exe")
HLPHYS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hlphys.exe")  # (hlvrad's prop collision helper)


def hlvrad_unsupported(opts: "CompileOptions") -> list[str]:
    """What our light compiler doesn't do yet (then Valve's vrad lights the map)."""
    why = []
    if opts.hdr != "HDR":
        why.append("LDR lighting")
    if opts.rad == "FAST":
        why.append("fast lighting")
    if opts.extra_vrad.split():
        why.append("extra vrad options")
    if not os.path.exists(HLVRAD):
        why.append("(hlvrad.exe is missing)")
    if not os.path.exists(HLPHYS):
        why.append("(hlphys.exe is missing)")
    return why


def prepare_skymap(base: str, key: str = "") -> None:
    """The sky picture the build exported (<map>.hlsky_src.npy) blurred and scaled for hlvrad (<map>.hlsky)."""
    import numpy as np
    from .skylight import make_skymap
    make_skymap(base + ".hlsky", np.load(base + ".hlsky_src.npy"), key)


def use_hlvrad(opts: "CompileOptions") -> bool:
    """Our light compiler runs when chosen and it does what's asked."""
    return opts.light_tool == "HAMMERLESS" and opts.rad != "SKIP" and not hlvrad_unsupported(opts)


def prepare_hlvrad(tools: "Tools", base: str) -> str:
    """Copy the map's prop models out of the game for hlvrad (prop shadows and detail props); returns the folder."""
    from .radprep import export_prop_models
    from .vpk import GameContent
    if tools.root not in _CONTENT:
        _CONTENT[tools.root] = GameContent(tools.root)
    folder = base + ".hlvrad_models"
    export_prop_models(base + ".bsp", _CONTENT[tools.root], folder)
    return folder


# lighting alongside vis: threads while vis runs (0: one per core). Measured on a real map: fewer threads slow
# the lighting more than they speed vis (all 10.0 s, 8: 10.3 s, 4: 10.8 s, 2: 12.4 s)
LIGHT_THREADS_DURING_VIS = 0

HLVBSP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hlvbsp.exe")
HLVBSP_TABLES = (".hlvbsp_materials.txt", ".hlvbsp_surfaceprops.txt", ".hlvbsp_props.txt", ".hlvbsp_detail.txt",
                 ".hlvbsp_cubemaps.txt", ".hlvbsp_fgd.txt")


def use_hlvbsp(opts: "CompileOptions") -> bool:
    """Our map compiler runs when chosen, present, and not given vbsp options it doesn't know."""
    return opts.map_tool == "HAMMERLESS" and os.path.exists(HLVBSP) and not opts.extra_vbsp.split()


def hlvbsp_command(tools: "Tools", base: str) -> list[str]:
    mat, surf, props, detail, cubemaps, fgd = (base + ext for ext in HLVBSP_TABLES)
    return [HLVBSP, "-game", tools.gamedir, "-materials", mat, "-surfaceprops", surf, "-props", props,
            "-detail", detail, "-cubemaps", cubemaps, "-fgd", fgd, base]


_CONTENT: dict = {}


def prepare_hlvbsp(tools: "Tools", vmf_path: str, base: str) -> list[str]:
    """Write what hlvbsp reads from the game's files (materials, surface properties, prop models).
    Returns the reasons it can't compile this map yet (then Valve's vbsp does)."""
    from .mapcompiler import (unsupported, write_cubemap_materials, write_detail_file, write_fgd_table,
                              write_material_table, write_prop_table, write_surfaceprops)
    from .vpk import GameContent
    if tools.root not in _CONTENT:
        _CONTENT[tools.root] = GameContent(tools.root)
    content = _CONTENT[tools.root]
    mat, surf, props, detail, cubemaps, fgd = (base + ext for ext in HLVBSP_TABLES)
    if "func_instance" in open(vmf_path, encoding="latin-1").read():
        write_fgd_table(fgd, tools.root)          # (instances rename their entities' keys by the .fgd's types)
    else:
        open(fgd, "w").close()
    write_material_table(mat, vmf_path, content, tools.gamedir)
    write_cubemap_materials(cubemaps, vmf_path, content, tools.gamedir)
    write_surfaceprops(surf, content)
    detail_text = write_detail_file(detail, vmf_path, content, tools.gamedir)
    write_prop_table(props, vmf_path, content, tools.gamedir, detail_text)
    with open(vmf_path, encoding="utf-8", errors="replace") as f:
        return unsupported(f.read())


def _opts_vis(text: str) -> str | None:
    m = re.search(r"vis='(\w+)'", text)
    return m.group(1) if m else None


def _opts_rad(text: str) -> str | None:
    """The lighting a build bakes: its level and every lighting setting (SKIP: none)."""
    m = re.search(r"rad='(\w+)'", text)
    if not m or m.group(1) == "SKIP":
        return m.group(1) if m else None
    names = _LIGHT_FIELDS + (_NORMAL_FIELDS if m.group(1) == "NORMAL" else ())
    names += _HLVRAD_FIELDS if "light_tool='HAMMERLESS'" in text else ()
    cycles = "light_tool='CYCLES'" in text
    names += _CYCLES_FIELDS if cycles else ()
    values = {n: _field(text, n) or _FIELD_DEFAULTS.get(n) for n in names}
    if values.get("gi") == "False":
        values.pop("gi_rays", None)                     # (only counts with GI on)
    return "|".join([m.group(1), "cycles" if cycles else "vrad"] + [f"{n}={v}" for n, v in values.items()])


def _with_light(text: str, source: str) -> str:
    """Options text with the lighting settings `source` was baked with (lighting kept from an earlier build)."""
    for name in _ALL_LIGHT_FIELDS + ("light_tool",):
        old, new = _field(text, name), _field(source, name)
        if old is not None and new is not None:
            text = text.replace(f", {name}={old}", f", {name}={new}", 1)
    return text


def _serves(built: str, opts: "CompileOptions") -> bool:
    """A map built with `built` options does for `opts`: visibility at least as complete, and the
    same lighting (rad SKIP asks for none: a compile for the nav analysis doesn't need it)."""
    vis, rad = _opts_vis(built), _opts_rad(built)
    return (vis in VIS_RANK and VIS_RANK[vis] >= VIS_RANK[opts.vis]
            and (opts.rad == "SKIP" or rad == _opts_rad(repr(opts))))


PRESETS = {
    "QUICK": CompileOptions(vis="SKIP", rad="SKIP"),   # geometry only; map is fullbright
    "FAST": CompileOptions(vis="FAST", rad="FAST"),
    # (props lit per vertex in the build: the game would otherwise do it at every map load)
    "NORMAL": CompileOptions(vis="FULL", rad="NORMAL", static_prop_lighting=True, prop_polys=True),
    "FINAL": CompileOptions(vis="FULL", rad="NORMAL", static_prop_lighting=True, prop_polys=True, sky_rays=16),
    "ULTRA": CompileOptions(vis="FULL", rad="NORMAL", static_prop_lighting=True, prop_polys=True, sky_rays=16,
                            ss_points=8, ss_passes=8, ss_threshold=0.03, fix_quirks=True),
}


def _steam_roots() -> list[str]:
    """Where Steam is installed: its own registry entry, then the usual folder."""
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(os.path.normpath(winreg.QueryValueEx(k, name)[0]))
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
    roots.append(r"C:\Program Files (x86)\Steam")
    return list({os.path.normcase(r): r for r in roots}.values())


def _steam_process() -> tuple[int, int]:
    """(Steam's process id, signed-in user) from Steam's own registry entry; (0, 0) when unknown."""
    if sys.platform != "win32":
        return 0, 0
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as k:
            values = []
            for name in ("pid", "ActiveUser"):
                try:
                    values.append(int(winreg.QueryValueEx(k, name)[0]))
                except (OSError, ValueError):
                    values.append(0)
            return values[0], values[1]
    except (ImportError, OSError):
        return 0, 0


def _process_running(name: str) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return name.lower() in out.lower()
    except OSError:
        return False


def steam_ready() -> bool:
    """Steam is running with someone signed in. Started before that, the game stops with
    'Steam is not running'. (Steam clears ActiveUser when it quits; the process check covers a
    crash, which leaves the old value behind.)"""
    _pid, user = _steam_process()
    return user != 0 and _process_running("steam.exe")


def steam_exe() -> str | None:
    for root in _steam_roots():
        exe = os.path.join(root, "steam.exe")
        if os.path.exists(exe):
            return exe
    return None


def find_game_root(extra: list[str] | None = None) -> str | None:
    candidates = []
    for c in extra or []:          # a folder the user set: also its parent (they picked 'left4dead2' inside)
        candidates += [c, os.path.dirname(c.rstrip("\\/"))]
    candidates += DEFAULT_GAME_ROOTS
    for steam in _steam_roots():    # every Steam library (games can be on any drive)
        candidates.append(os.path.join(steam, "steamapps", "common", "Left 4 Dead 2"))
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            with open(vdf, encoding="utf-8", errors="replace") as f:
                for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                    candidates.append(os.path.join(m.group(1).replace("\\\\", "\\"),
                                                   "steamapps", "common", "Left 4 Dead 2"))
    for c in candidates:
        if c and os.path.exists(os.path.join(c, "left4dead2.exe")):
            return c
    return None


@dataclass
class Tools:
    root: str

    @property
    def bin(self) -> str:
        return os.path.join(self.root, "bin")

    @property
    def gamedir(self) -> str:
        return os.path.join(self.root, "left4dead2")

    @property
    def maps_dir(self) -> str:
        return os.path.join(self.gamedir, "maps")

    def exe(self, name: str) -> str:
        return os.path.join(self.bin, name + ".exe")

    def missing(self) -> list[str]:
        return [n for n in ("vbsp", "vvis", "vrad") if not os.path.exists(self.exe(n))]


# ---------------------------------------------------------------- log parsing

@dataclass
class LogSummary:
    leaked: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


ERROR_PATTERNS = [
    (re.compile(r"\*+\s*leaked\s*\*+", re.I), "Map leaks: there's a hole to the void. Import the leak line to find it."),
    (re.compile(r"MAX_MAP_\w+", re.I), None),
    (re.compile(r"^\s*Error[: ]", re.I), None),
    (re.compile(r"plane with no normal", re.I), None),
    (re.compile(r"Brush \d+: .*", re.I), None),
    (re.compile(r"couldn't open|could not open|can't find", re.I), None),
]
WARNING_PATTERNS = [
    re.compile(r"^\s*warning", re.I),
    re.compile(r"material .* not found", re.I),
]


def sources_path(vmf_path: str) -> str:
    return os.path.splitext(vmf_path)[0] + ".brushes.json"


def name_brushes(lines: list[str], vmf_path: str) -> list[str]:
    """'Brush 177: ...' -> "Brush 177 ('Plane.006'): ..." using the ids written at export."""
    try:
        import json
        with open(sources_path(vmf_path), encoding="utf-8") as f:
            sources = json.load(f)
    except (OSError, ValueError):
        return lines

    def name(m):
        src = sources.get(m.group(1))
        return f"Brush {m.group(1)} ('{src}')" if src else m.group(0)
    return [re.sub(r"Brush (\d+)", name, line) for line in lines]


def failure_reason(log: list[str]) -> str:
    """When no known error pattern matched: the failure line, plus what the failing tool
    printed last (usually the real reason)."""
    fail = next((i for i in range(len(log) - 1, -1, -1) if log[i].startswith("!! ")), None)
    if fail is None:
        return "Compile failed (no error message). See the hammerless_log text block"
    reason = log[fail][3:].strip()
    if "failed (exit code" not in reason:
        return reason
    tail = []
    for line in reversed(log[:fail]):
        if line.startswith("==== "):
            break
        line = line.strip()
        if line and not line.endswith("elapsed") and not re.match(r"^[\d.]+\.\.\.", line):
            tail.append(line)
        if len(tail) == 3:
            break
    return reason + (": " + " | ".join(reversed(tail)) if tail else "")


def parse_log(text: str) -> LogSummary:
    s = LogSummary()
    for line in text.splitlines():
        for pat, msg in ERROR_PATTERNS:
            if pat.search(line):
                if "leaked" in pat.pattern:
                    s.leaked = True
                s.errors.append(msg or line.strip())
                break
        else:
            if any(p.search(line) for p in WARNING_PATTERNS):
                s.warnings.append(line.strip())
    s.errors = list(dict.fromkeys(s.errors))
    s.warnings = list(dict.fromkeys(s.warnings))
    return s


def read_pointfile(path: str) -> list[tuple[float, float, float]]:
    """Leak trace (.lin): one 'x y z' per line."""
    pts = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 3:
                try:
                    pts.append((float(parts[0]), float(parts[1]), float(parts[2])))
                except ValueError:
                    pass
    return pts


# ---------------------------------------------------------------- compile job

_SIGNATURES: dict[str, tuple] = {}     # path -> ((size, mtime), signature)


def bsp_signature(path: str) -> str:
    """Fingerprint of a compiled map without its pakfile (lump 40): the game saves its stringtable
    dictionary there whenever it loads the map (measured: the only lump that changes)."""
    import hashlib
    import struct
    st = os.stat(path)
    key = (st.st_size, st.st_mtime_ns)
    if _SIGNATURES.get(path, (None,))[0] == key:
        return _SIGNATURES[path][1]
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"VBSP":
        raise ValueError(f"{path} is not a BSP")
    h = hashlib.sha1(data[:8])
    for i in range(64):
        if i == 40:
            continue
        ver, off, length, cc = struct.unpack_from("<iiii", data, 8 + 16 * i)
        h.update(struct.pack("<iiii", ver, off, length, cc))
        h.update(data[off:off + length])
    _SIGNATURES[path] = (key, h.hexdigest())
    return _SIGNATURES[path][1]


class CompileJob:
    """Runs the compile steps one after another without blocking the caller.

    Call poll() regularly (e.g. from a Blender timer); it returns new log lines.
    `done`, `failed` and `summary` are set when finished.
    """

    def __init__(self, tools: Tools, vmf_path: str, preset: "str | CompileOptions" = "NORMAL",
                 copy_to_game: bool = True, skip_if_unchanged: bool = False):
        self.tools = tools
        self.skip_if_unchanged = skip_if_unchanged
        self.timings: list[tuple[str, float]] = []
        self.skipped = False
        self.vmf = os.path.abspath(vmf_path)
        self.base = os.path.splitext(self.vmf)[0]
        self.name = os.path.basename(self.base)
        self.copy_to_game = copy_to_game
        opts = PRESETS[preset] if isinstance(preset, str) else preset
        self._opts = opts
        vvis, vrad = opts.vvis_args(), opts.vrad_args()
        game = ["-game", tools.gamedir]
        valve_vbsp = [tools.exe("vbsp")] + opts.vbsp_args() + game + [self.base]
        self._valve_vbsp = None             # Valve's vbsp, run instead when ours can't (or fails)
        if use_hlvbsp(opts):
            self.steps: list[tuple[str, list[str]]] = [("vbsp", hlvbsp_command(tools, self.base))]
            self._valve_vbsp = valve_vbsp
        else:
            self.steps = [("vbsp", valve_vbsp)]
        self._valve_vvis = None             # Valve's vvis, run instead if ours fails
        self._stopping = False
        if vvis is not None:
            valve = [tools.exe("vvis")] + vvis + game + [self.base]
            if use_hlvvis(opts):
                self.steps.append(("vvis", [HLVVIS] + vvis + game + [self.base]))
                self._valve_vvis = valve
            else:
                self.steps.append(("vvis", valve))
        self._valve_vrad = None             # Valve's vrad, run instead if ours can't (or fails)
        if vrad is not None:
            valve = [tools.exe("vrad")] + vrad + game + [self.base]
            if use_hlvrad(opts):
                sky = ["-skymap", self.base + ".hlsky"] if opts.sky_key else []
                self.steps.append(("vrad", [HLVRAD] + vrad + opts.hlvrad_args() + game + sky
                                   + ["-modeldir", self.base + ".hlvrad_models", self.base]))
                self._valve_vrad = valve
            else:
                self.steps.append(("vrad", valve))
        self.plan = "full"            # what a smart build decided (buildplan.plan); see _choose_steps
        self.log: list[str] = []
        self.done = False
        self.failed = False
        self.summary: LogSummary | None = None
        self.lighting: list = []     # bspcheck.lighting_problems after vrad: (message, location, object)
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.prepared = threading.Event()      # (the map compiler's tables written: Python work after it can start)
        self._proc = None
        self._vmf_bytes = b""
        self._built_vis = opts.vis            # the visibility the BSP ends up with
        self._built_rad = opts.rad            # and its lighting (SKIP: none, or out of date)
        self._built_light: str | None = None  # (lighting kept from the last build: its options, for its settings)
        self.vis_bsp: str | None = None        # a copy of the BSP once geometry and visibility are final:
                                               # the nav analysis can start on it while vrad still runs
        self.snapshot_vis = False              # make that copy (only when an analysis will use it)
        # Light Compiler Cycles: after vrad the job asks for the bake (bake_request = the BSP) and waits;
        # Blender's main thread bakes, then calls bake_finished. Nobody baking (bake_abandoned): vrad's stays
        self.bake_request: str | None = None
        self.bake_abandoned = False
        self._bake_done = threading.Event()
        self._bake_lines: list[str] = []
        self._bake_ok = False

    def start(self) -> "CompileJob":
        other = _ACTIVE_JOBS.get(self.base)
        if other is not None and other._thread.is_alive():
            raise RuntimeError(f"{self.name} is already compiling: wait for it to finish (see the hammerless_log "
                               "text) and build again")
        with open(self.vmf, "rb") as f:
            self._vmf_bytes = f.read()       # what this job compiles, whatever happens to the file later
        lin = self.base + ".lin"
        if os.path.exists(lin):
            os.remove(lin)
        _ACTIVE_JOBS[self.base] = self
        self._thread.start()
        return self

    def _stamp(self, vmf_bytes: bytes | None = None) -> str:
        """Fingerprint of what gets compiled: the VMF text and the compile options."""
        import hashlib
        if vmf_bytes is None:
            with open(self.vmf, "rb") as f:
                vmf_bytes = f.read()
        return hashlib.sha1(vmf_bytes + _opts_rest(repr(self._opts)).encode()).hexdigest()

    def _built_opts(self) -> str:
        with open(self.base + ".built.opts", encoding="utf-8") as f:
            return f.read()

    def up_to_date(self) -> bool:
        """The last successful compile used the same VMF and options, with visibility at least as
        complete, and the game has its BSP (compared without the pakfile, which the game rewrites
        on every load)."""
        try:
            with open(self.base + ".stamp", encoding="utf-8") as f:
                same = f.read() == self._stamp()
            enough = _serves(self._built_opts(), self._opts)
            if not self.copy_to_game:
                return same and enough and os.path.exists(self.base + ".bsp")
            game_bsp = os.path.join(self.tools.maps_dir, self.name + ".bsp")
            return same and enough and bsp_signature(game_bsp) == bsp_signature(self.base + ".bsp")
        except (OSError, ValueError, KeyError):
            return False

    # ---- lighting alongside vis: our light compiler on the GPU with ray-traced bounce lights the faces without
    # vis (vis only spares it rays: the same lightmaps to the byte), then waits for vis to finish the rest
    # (hlvrad -visfrom). It lights a copy of the map (<base>.light.bsp) that replaces the map when both are done.
    _light = None
    _light_t0 = 0.0

    def _start_light_alongside(self, step) -> None:
        """Before vis starts: copy the map for the lighting, then (on a thread, while vis already runs) prepare
        what the light compiler reads and start it. Nothing started: vrad runs after vis as usual."""
        import threading
        import time
        name, cmd = step
        if name != "vrad" or cmd[0] != HLVRAD or "-gpu" not in cmd or "-gi" not in cmd:
            return                               # (vrad's own bounce needs vis: one after the other)
        light_bsp = self.base + ".light.bsp"
        try:
            for ext in (".visdone", ".visfailed"):
                if os.path.exists(self.base + ".bsp" + ext):
                    os.remove(self.base + ".bsp" + ext)
            shutil.copy2(self.base + ".bsp", light_bsp)      # (before vis rewrites the map)
        except OSError:
            return
        light_cmd = cmd[:-1] + ["-visfrom", self.base + ".bsp", "-visthreads", str(LIGHT_THREADS_DURING_VIS),
                                cmd[-1] + ".light"]
        box = {"proc": None, "lines": [], "reader": None}

        def launch():
            try:
                from .radprep import export_prop_models
                from .vpk import GameContent
                if self._opts.sky_key:
                    prepare_skymap(self.base, self._opts.sky_key)
                if self.tools.root not in _CONTENT:
                    _CONTENT[self.tools.root] = GameContent(self.tools.root)
                export_prop_models(light_bsp, _CONTENT[self.tools.root], self.base + ".hlvrad_models")
                if self._stopping:
                    return
                proc = subprocess.Popen(light_cmd, cwd=os.path.dirname(self.vmf), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace",
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                        | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
                box["reader"] = threading.Thread(target=lambda: box["lines"].extend(proc.stdout), daemon=True)
                box["reader"].start()
                box["proc"] = proc
            except Exception:
                box["proc"] = None                # (the usual way: vrad after vis prepares and reports it)

        box["starter"] = threading.Thread(target=launch, daemon=True)
        box["starter"].start()
        self._light, self._light_t0 = box, time.time()

    def _finish_light_alongside(self) -> tuple[int, list[str]] | None:
        """The lighting started alongside vis: its exit code and output (the map moved into place). None: it
        never started (vrad runs now instead)."""
        box = self._light
        box["starter"].join()
        proc = box["proc"]
        if proc is None:
            self._light = None
            return None
        code = proc.wait()
        box["reader"].join()
        self._q.put("(lit alongside vis)")
        for line in box["lines"]:
            self._q.put(line.rstrip("\n"))
        if code == 0:
            os.replace(self.base + ".light.bsp", self.base + ".bsp")
        return code, box["lines"]

    def _light_proc(self):
        if self._light is None:
            return None
        return self._light.get("proc")

    def _end_light_alongside(self) -> None:
        if self._light is not None:
            self._light["starter"].join(5)
            proc = self._light.get("proc")
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
        for p in (self.base + ".light.bsp", self.base + ".bsp.visdone", self.base + ".bsp.visfailed"):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    def _run(self):
        try:
            self._run_steps()
        finally:
            self.prepared.set()
            self._end_light_alongside()
            self._proc = None
            if _ACTIVE_JOBS.get(self.base) is self:
                del _ACTIVE_JOBS[self.base]

    def _exec(self, cmd: list[str]) -> tuple[int, list[str]]:
        proc = self._proc = subprocess.Popen(
            cmd, cwd=os.path.dirname(self.vmf), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace",
            # below-normal priority: the PC stays responsive while vvis/vrad use every core
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        out = []
        for line in proc.stdout:
            self._q.put(line.rstrip("\n"))
            out.append(line)
        return proc.wait(), out

    def _run_steps(self):
        import time
        try:
            if self.skip_if_unchanged and self.up_to_date():
                self.skipped = True
                self._q.put("Map unchanged since the last build: skipped compiling")
                self._q.put(("OK",))
                return
            steps = self._choose_steps() if self.skip_if_unchanged else self.steps
            for stale in (self.base + ".stamp", self.base + ".built.vmf"):   # a failed compile mustn't look
                if os.path.exists(stale):                                     # up to date or be built on
                    os.remove(stale)
            prt, kept_prt = self.base + ".prt", self.base + ".built.prt"
            for k, (name, cmd) in enumerate(steps):
                self._q.put(f"==== {name} ====")
                if name == "vvis" and not os.path.exists(prt) and os.path.exists(kept_prt):
                    shutil.copy2(kept_prt, prt)      # vbsp -onlyents deletes the portal file; geometry is the same
                if name == "vvis" and os.path.exists(self.base + ".viscost"):
                    os.remove(self.base + ".viscost")    # Hammerless vis writes a new one; Valve's vvis none
                if name == "vvis" and k + 1 < len(steps):
                    self._start_light_alongside(steps[k + 1])
                t0 = time.time()
                done = self._finish_light_alongside() if name == "vrad" and self._light is not None else None
                if done is not None:
                    code, out = done
                    cmd = [HLVRAD]                   # (ours ran: a failure falls back to Valve's vrad below)
                    if code != 0 and self._valve_vrad and not self._stopping:
                        self._q.put(f"!! Hammerless light compiler failed (exit code {code}): running Valve's vrad instead")
                        code, out = self._exec(self._valve_vrad)
                    self.timings.append(("vrad (alongside vis)", time.time() - self._light_t0))
                    if code != 0:
                        self._q.put(f"!! {name} failed (exit code {code})")
                        self._q.put(("FAILED",))
                        return
                    try:
                        from .bspcheck import lighting_problems
                        self.lighting = lighting_problems(self.base + ".bsp", self.vmf, "".join(out))
                    except Exception as ex:      # a checker bug must never fail the build
                        self._q.put(f"(lighting check skipped: {ex})")
                    for msg, _loc, _obj in self.lighting:
                        self._q.put(f"!! {msg}")
                    continue
                if name == "vbsp" and cmd[0] == HLVBSP:
                    why = []
                    try:
                        why = prepare_hlvbsp(self.tools, self.vmf, self.base)
                    except Exception as ex:          # never let the tables stop a build
                        why = [f"couldn't read the game's files ({ex})"]
                    if why:
                        self._q.put("Hammerless map compiler doesn't do " + ", ".join(why) + " yet: running Valve's vbsp")
                        cmd = self._valve_vbsp
                self.prepared.set()
                if name == "vrad" and cmd[0] == HLVRAD and self._opts.sky_key:
                    try:
                        prepare_skymap(self.base, self._opts.sky_key)
                    except Exception as ex:          # (the sky picture: one colour instead, ours still lights it)
                        self._q.put(f"Sky Light: couldn't prepare the sky picture ({ex}): the sky lights the map "
                                    "with one colour")
                        cmd = [c for i, c in enumerate(cmd) if c != "-skymap" and (i == 0 or cmd[i - 1] != "-skymap")]
                if name == "vrad" and cmd[0] == HLVRAD:
                    try:
                        prepare_hlvrad(self.tools, self.base)
                    except Exception as ex:          # never let the model copy stop a build
                        self._q.put(f"Hammerless light compiler couldn't read the game's models ({ex}): running Valve's vrad")
                        cmd = self._valve_vrad
                elif name == "vrad" and self._opts.light_tool == "HAMMERLESS":
                    self._q.put("Hammerless light compiler doesn't do " + ", ".join(hlvrad_unsupported(self._opts))
                                + " yet: running Valve's vrad")
                if name == "vrad" and cmd[0] != HLVRAD and self._opts.sky_key:
                    self._q.put("Sky Light from the sky needs the Hammerless light compiler: Valve's vrad lights it "
                                "with one colour")
                code, out = self._exec(cmd)
                if code != 0 and name == "vrad" and cmd[0] == HLVRAD and self._valve_vrad and not self._stopping:
                    # ours writes the map only when it has finished, so it's untouched
                    self._q.put(f"!! Hammerless light compiler failed (exit code {code}): running Valve's vrad instead")
                    code, out = self._exec(self._valve_vrad)
                if code != 0 and name == "vbsp" and cmd[0] == HLVBSP and self._valve_vbsp and not self._stopping:
                    self._q.put(f"!! Hammerless map compiler failed (exit code {code}): running Valve's vbsp instead")
                    code, out = self._exec(self._valve_vbsp)
                if code != 0 and name == "vvis" and cmd[0] == HLVVIS and self._valve_vvis and not self._stopping:
                    # ours only replaces the map at the very end, so the map and portals are untouched
                    self._q.put(f"!! Hammerless vis failed (exit code {code}): running Valve's vvis instead")
                    code, out = self._exec(self._valve_vvis)
                self.timings.append((name, time.time() - t0))
                if name == "vvis" and self._light is not None:
                    open(self.base + ".bsp" + (".visdone" if code == 0 else ".visfailed"), "w").close()
                if code != 0 or (name == "vbsp" and os.path.exists(self.base + ".lin")):
                    self._q.put(f"!! {name} failed (exit code {code})")
                    self._q.put(("FAILED",))
                    return
                if name == "vbsp" and os.path.exists(prt):
                    shutil.copy2(prt, kept_prt)          # the portals, for adding the full vis to a bake later
                rest = [n for n, _c in steps[k + 1:]]
                if self.snapshot_vis and self.vis_bsp is None and rest and "vvis" not in rest and not any(n.startswith("vbsp") for n in rest):
                    snap = self.base + ".analysis.bsp"   # geometry and visibility won't change any more
                    shutil.copy2(self.base + ".bsp", snap)
                    self.vis_bsp = snap
                if name == "vrad":
                    try:
                        from .bspcheck import lighting_problems
                        self.lighting = lighting_problems(self.base + ".bsp", self.vmf, "".join(out))
                    except Exception as ex:      # a checker bug must never fail the build
                        self._q.put(f"(lighting check skipped: {ex})")
                    for msg, _loc, _obj in self.lighting:
                        self._q.put(f"!! {msg}")
                    if self._opts.light_tool == "CYCLES" and not self._cycles_bake():
                        self._built_rad = "SKIP"     # (vrad's lighting only: the next build bakes again)
            if self.plan != "full":
                from .buildplan import strip_stale
                strip_stale(self.base + ".bsp")
            if self.copy_to_game:
                os.makedirs(self.tools.maps_dir, exist_ok=True)
                self._copy_bsp(os.path.join(self.tools.maps_dir, self.name + ".bsp"))
                self._q.put(f"Copied {self.name}.bsp to {self.tools.maps_dir}")
            with open(self.base + ".stamp", "w", encoding="utf-8") as f:
                f.write(self._stamp(self._vmf_bytes))
            with open(self.base + ".built.vmf", "wb") as f:       # what this BSP was made from (the VMF
                f.write(self._vmf_bytes)                         # as it was when the job started)
            with open(self.base + ".built.opts", "w", encoding="utf-8") as f:
                built = repr(replace(self._opts, vis=self._built_vis, rad=self._built_rad))
                f.write(_with_light(built, self._built_light) if self._built_light else built)
            self._q.put("Timing: " + ", ".join(f"{n} {t:.1f}s" for n, t in self.timings))
            self._q.put(("OK",))
        except Exception as ex:  # surfaced to the user in the log
            self._q.put(f"!! {ex}")
            self._q.put(("FAILED",))

    def _cycles_bake(self) -> bool:
        """Whether Blender baked the map with Cycles (else it keeps vrad's lighting)."""
        import time
        self._q.put("==== Cycles lighting ====")
        t0 = time.time()
        self._bake_done.clear()
        self.bake_request = self.base + ".bsp"
        while not self._bake_done.wait(0.25):
            if self._stopping or self.bake_abandoned:
                self.bake_request = None
                self._q.put("Cycles bake skipped (not watching the build): the map keeps vrad's lighting")
                return False
        for line in self._bake_lines:
            self._q.put(line)
        self.timings.append(("cycles", time.time() - t0))
        return self._bake_ok

    def bake_finished(self, lines: list[str], ok: bool = True):
        """Main thread: the bake is written (or failed: lines say so, vrad's lighting stays)."""
        self._bake_lines = lines
        self._bake_ok = ok
        self.bake_request = None
        self._bake_done.set()

    def _choose_steps(self) -> list[tuple[str, list[str]]]:
        """Smart build: the least work that gives the same map as a full compile, judged against
        the VMF the current BSP was compiled from (buildplan.plan). A doubt means a full compile."""
        from .buildplan import plan
        old, built_vis, built_rad = None, None, None
        try:
            built = self._built_opts()
            built_vis, built_rad = _opts_vis(built), _opts_rad(built)
            same_opts = (_opts_rest(built) == _opts_rest(repr(self._opts)) and built_vis in VIS_RANK
                         and built_rad is not None)
            if same_opts and os.path.exists(self.base + ".bsp"):
                with open(self.base + ".built.vmf", encoding="utf-8") as f:
                    old = f.read()
        except OSError:
            pass
        kind, why = plan(old, self._vmf_bytes.decode("utf-8", "replace"))
        if kind == "full":
            self.plan = "full"
            return self.steps
        add_vis = VIS_RANK[built_vis] < VIS_RANK[self._opts.vis]
        if add_vis and not os.path.exists(self.base + ".built.prt"):
            self.plan = "full"                   # no portal file kept (an older build): compile it all
            return self.steps
        lights_changed = kind == "lighting"      # lights or static props moved: the bake is out of date
        want = self._opts.rad != "SKIP"
        add_rad = want and (lights_changed or built_rad != _opts_rad(repr(self._opts)))
        self._built_vis = self._opts.vis if add_vis else built_vis
        if add_rad:
            self._built_rad = self._opts.rad
        elif lights_changed:
            self._built_rad = "SKIP"
        else:                                    # (the lighting stays as it was baked)
            self._built_rad = built_rad.split("|")[0]
            self._built_light = built
        self.plan = "lighting" if add_rad else "entities"
        game = ["-game", self.tools.gamedir]
        steps = [("vbsp (entities only)", [self.tools.exe("vbsp"), "-onlyents"] + game + [self.base])]
        keep = "keeping geometry" + ("" if add_rad or lights_changed else " and the baked lighting")
        self._q.put(f"Smart build: {why}: {keep}")
        if add_vis:          # e.g. after a lighting bake (fast vis): the full vis leaves the lighting alone
            steps += [st for st in self.steps if st[0] == "vvis"]
            self._q.put("Smart build: adding the full visibility")
        if add_rad:          # last, so it uses the final visibility
            steps += [st for st in self.steps if st[0] == "vrad"]
            self._q.put("Smart build: baking the lighting" + (" again (lights changed)" if lights_changed else ""))
        return steps

    def _copy_bsp(self, dest: str):
        """Copy the BSP into the game. While the game has this map loaded it keeps the file
        open and Windows refuses the copy: unload the map (disconnect) and try again."""
        import time
        from . import stringtables
        dictionary = stringtables.keep_for_next_build(dest, self.tools.gamedir)
        for attempt in range(12):
            try:
                shutil.copy2(self.base + ".bsp", dest)
                if dictionary:          # (the game's dictionary: it doesn't spend ~10 s building one at load)
                    try:
                        stringtables.put_dictionary(dest, stringtables.for_map(dictionary, self.name))
                    except Exception as ex:          # never fail a build over it: the game makes its own
                        self._q.put(f"(stringtable dictionary not kept: {ex})")
                return
            except PermissionError:
                if attempt == 0:
                    if not game_running():
                        break
                    self._q.put("The game has the map open: unloading it to copy the new one in")
                    send_commands(self.tools, ["disconnect"])
                time.sleep(1.0)
        raise RuntimeError(f"Couldn't copy {self.name}.bsp into the game's maps folder: the file is in use. "
                           "Close Left 4 Dead 2 (or load another map) and build again")

    def poll(self) -> list[str]:
        new = []
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple):
                self.done = True
                self.failed = item[0] == "FAILED"
                self.summary = parse_log("\n".join(self.log))
                if self.failed and not self.summary.errors and not self.summary.leaked:
                    self.summary.errors = [failure_reason(self.log)]
                self.summary.errors = name_brushes(self.summary.errors, self.vmf)
                self.summary.warnings = name_brushes(self.summary.warnings, self.vmf)
            else:
                self.log.append(item)
                new.append(item)
        return new

    def wait(self) -> "CompileJob":
        self._thread.join()
        self.poll()
        return self


_ACTIVE_JOBS: dict[str, "CompileJob"] = {}       # map base path -> its running compile


def compile_running(vmf_path: str) -> bool:
    """A compile of this map (same VMF) is still running."""
    job = _ACTIVE_JOBS.get(os.path.splitext(os.path.abspath(vmf_path))[0])
    return job is not None and job._thread.is_alive()


def _stop_compilers() -> None:
    """Blender is closing: don't leave vbsp / vvis / vrad running hidden."""
    for job in list(_ACTIVE_JOBS.values()):
        job._stopping = True                              # (no fallback compiler after this)
        for proc in (getattr(job, "_proc", None), job._light_proc()):
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass


import atexit                                             # noqa: E402
atexit.register(_stop_compilers)


def game_running() -> bool:
    return _process_running("left4dead2.exe")


def send_commands(tools: Tools, commands: list[str]):
    """Run console commands in the already-running game (via -hijack).

    Commands go through a cfg file: passing them as `+cmd args` breaks on
    arguments that start with '-' (e.g. `setpos 10 -200 0`), which the launcher
    reads as command-line switches.
    """
    cfg_dir = os.path.join(tools.gamedir, "cfg")
    os.makedirs(cfg_dir, exist_ok=True)
    _SENT[0] = (_SENT[0] + 1) % 4
    name = f"hammerless_cmd{_SENT[0]}"
    with open(os.path.join(cfg_dir, name + ".cfg"), "w", encoding="utf-8") as f:
        f.write("\n".join(commands) + "\n")
    return subprocess.Popen([os.path.join(tools.root, "left4dead2.exe"), "-game", "left4dead2", "-hijack",
                             "+exec", name], cwd=tools.root)


_SENT = [0]


def _run_console_script(tools: Tools, steps: list[tuple[str, list[str]]], log_start: int,
                        timeout: float = 600.0, launch_id: int | None = None, on_done=None):
    """Tail console.log; for each (marker, commands) step, wait until `marker`
    appears (after the previous step's match), then send `commands`. Stops when a newer launch
    starts; if it gives up, turns cheats (and nav editing) back off. on_done runs after the last
    step."""
    import time
    log = os.path.join(tools.gamedir, "console.log")
    pos = log_start
    deadline = time.time() + timeout
    cheats = False
    seen, checked = False, 0.0          # the game process (it may still be starting when this begins)
    for marker, commands in steps:
        needle = marker.lower().encode("utf-8")
        while True:
            if launch_id is not None and LOAD_STATUS["launch_id"] != launch_id:
                return                          # a newer Build & Play took over
            if time.time() - checked > 5.0:
                checked = time.time()
                if game_running():
                    seen = True
                elif seen:                      # closed: sending more would start the game again
                    LOAD_STATUS["error"] = ("The game was closed before its nav mesh step finished. Launch "
                                            "again to finish it")
                    return
            if time.time() > deadline:
                if cheats and game_running():
                    send_commands(tools, ["nav_edit 0", "sv_cheats 0"])
                LOAD_STATUS["error"] = ("The game didn't finish its nav mesh step in time; the nav may be "
                                        "missing its marks. Build & Play again (tick Rebuild Nav Mesh)")
                return
            time.sleep(1.0)
            try:
                if os.path.getsize(log) < pos:
                    pos = 0  # log was truncated/restarted
                with open(log, "rb") as f:          # bytes: console.log is CRLF, text mode would drift
                    f.seek(pos)
                    chunk = f.read()
            except OSError:
                continue
            i = chunk.lower().find(needle)
            if i >= 0:
                pos += i + len(needle)
                break
        time.sleep(3.0)  # let the client finish connecting / the map settle
        if launch_id is not None and LOAD_STATUS["launch_id"] != launch_id:
            return                              # (a newer Build & Play started meanwhile)
        if commands:
            cheats = cheats or any(c.startswith("sv_cheats 1") for c in commands)
            if any(c.startswith("sv_cheats 0") for c in commands):
                cheats = False
            send_commands(tools, commands)
    if on_done is not None:
        on_done()


# Printed in the status block at the end of every map load, both on a fresh game
# start and when loading into a running game ("Receiving uncompressed update" is
# only printed in the second case).
LOADED = "server number:"


# Printed by the map's hammerless_ready script once survivors exist (see gamefiles.py).
# nav_generate needs players standing in the map; sent earlier, it fails.
READY = "hammerless_ready survivors spawned"   # not just the name: the game also prints "hammerless_ready executing script"


def nav_steps(map_name: str, mark: bool = True, map_cmd: str | None = None) -> list[tuple[str, list[str]]]:
    """Generate nav, mark safe-room attributes (see nav.py), save, reload fresh (map_cmd: the map and
    its game mode, see map_mode)."""
    map_cmd = map_cmd or map_name
    steps = [(f"host_newgame on map {map_name.lower()}", []),
             (READY, ["sv_cheats 1", "nav_generate"]),
             (".nav' saved.", []),
             (READY, [f"script_execute hammerless/navmark_{map_name}"] if mark
              else ["sv_cheats 0", f"map {map_cmd}"])]
    if mark:
        steps.append(("hammerless_navmark done", []))
        steps.append((".nav' saved.", ["sv_cheats 0", f"map {map_cmd}"]))
    return steps


def analyze_steps() -> list[tuple[str, list[str]]]:
    """For a nav mesh Hammerless wrote itself: the game adds its visibility data and hiding spots
    (nav_analyze, a second or so), saves and reloads the map once."""
    return [(READY, ["sv_cheats 1", "nav_edit 1", "nav_analyze"]),
            (".nav' saved.", []),
            (READY, ["nav_edit 0", "sv_cheats 0"])]


def write_generated_nav(tools: Tools, map_name: str, mesh, analyzed: bool = False) -> str:
    """Write a nav mesh from our generator (navpredict) as maps/<map>.nav: ready for the game's
    nav_analyze, or (analyzed=True, after navanalyze.analyze) for loading as it is. Records the
    marks it was made with, like an in-game generation does."""
    from .navfile import write_nav
    for a in mesh.areas:
        a.flags |= 0x20000000        # set on every area of L4D2's own generated navs
    mesh.analyzed = analyzed
    bsp = os.path.join(tools.maps_dir, map_name + ".bsp")
    mesh.bsp_size = os.path.getsize(bsp) if os.path.exists(bsp) else 0
    path = os.path.join(tools.maps_dir, map_name + ".nav")
    with open(path, "wb") as f:
        f.write(write_nav(mesh))
    set_nav_maker(tools, map_name, "blender")
    script, used = _navmark_paths(tools, map_name)
    if os.path.exists(script):
        shutil.copyfile(script, used)
    return path


def _navmark_paths(tools: Tools, map_name: str) -> tuple[str, str]:
    d = os.path.join(tools.gamedir, "scripts", "vscripts", "hammerless")
    return os.path.join(d, f"navmark_{map_name}.nut"), os.path.join(d, f"navmark_{map_name}.used")


def _nav_maker_path(tools: Tools, map_name: str) -> str:
    return os.path.join(tools.gamedir, "scripts", "vscripts", "hammerless", f"navmaker_{map_name}.txt")


def _owner_path(tools: Tools, map_name: str) -> str:
    return os.path.join(tools.gamedir, "scripts", "vscripts", "hammerless", f"owner_{map_name}.txt")


def map_owner(tools: Tools, map_name: str) -> str | None:
    """The VMF (in its .blend's build folder) that last wrote maps/<map>: two .blend files with
    the same Map Name replace each other's map in the game."""
    try:
        with open(_owner_path(tools, map_name), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def set_map_owner(tools: Tools, map_name: str, vmf_path: str) -> None:
    path = _owner_path(tools, map_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(os.path.abspath(vmf_path))


def set_nav_maker(tools: Tools, map_name: str, maker: str) -> None:
    """Record who made maps/<map>.nav: "blender" (our generator) or "game" (nav_generate), and for
    which compiled map (its signature), so a later compile without a new nav is noticed."""
    path = _nav_maker_path(tools, map_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        sig = bsp_signature(os.path.join(tools.maps_dir, map_name + ".bsp"))
    except (OSError, ValueError):
        sig = ""
    with open(path, "w", encoding="utf-8") as f:
        f.write(maker + ("\n" + sig if sig else ""))


def clear_nav(tools: Tools, map_name: str) -> list[str]:
    """Delete the map's nav mesh (and what records how it was made): the next build makes a new
    one. Returns the files that couldn't be deleted (the game has the map loaded)."""
    script, used = _navmark_paths(tools, map_name)
    return _remove([os.path.join(tools.maps_dir, map_name + ".nav"), _nav_maker_path(tools, map_name), used])


def clear_nav_analysis(tools: Tools, map_name: str) -> bool:
    """Remove the visibility data and hiding spots from the map's nav mesh (the areas stay): the
    next build analyzes it again. False when there's no readable nav."""
    from .navfile import load_nav, write_nav
    path = os.path.join(tools.maps_dir, map_name + ".nav")
    try:
        mesh = load_nav(path)
    except (OSError, ValueError):
        return False
    for a in mesh.areas:
        a.visible, a.inherit_visibility, a.hiding_spots = [], 0, []
    mesh.analyzed = False
    with open(path, "wb") as f:
        f.write(write_nav(mesh))
    return True


def clear_build(tools: Tools | None, work_base: str, map_name: str) -> list[str]:
    """Delete the compiled map (which holds the baked lighting) and what records how it was built:
    the next build compiles and bakes from scratch. Returns files that couldn't be deleted."""
    paths = [work_base + ext for ext in (".bsp", ".stamp", ".built.vmf", ".built.opts", ".built.prt", ".prt", ".lin",
                                         ".analysis.bsp", ".viscost") + HLVBSP_TABLES]
    if tools is not None:
        paths.append(os.path.join(tools.maps_dir, map_name + ".bsp"))
    shutil.rmtree(work_base + ".hlvrad_models", ignore_errors=True)    # (models copied for hlvrad)
    paths += [work_base + ".hlsky", work_base + ".hlsky.key", work_base + ".hlsky_src.npy"]
    return _remove(paths)


def _remove(paths: list[str]) -> list[str]:
    locked = []
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            locked.append(os.path.basename(p))
    return locked


def nav_outdated(tools: Tools, map_name: str) -> bool:
    """The map was compiled again since the nav mesh was made (e.g. a build with another nav setting, or one stopped
    with Esc): the nav no longer fits its walls. False when it isn't known (older markers)."""
    try:
        with open(_nav_maker_path(tools, map_name), encoding="utf-8") as f:
            lines = f.read().split()
        if len(lines) < 2:
            return False
        return bsp_signature(os.path.join(tools.maps_dir, map_name + ".bsp")) != lines[1]
    except (OSError, ValueError):
        return False


def nav_maker(tools: Tools, map_name: str) -> str | None:
    """Who made the current nav mesh ("blender" / "game"), or None when unknown (older builds, or
    a game generation that never saved: the marker is written when it starts, so the nav has to
    be newer than the marker)."""
    marker = _nav_maker_path(tools, map_name)
    try:
        with open(marker, encoding="utf-8") as f:
            maker = (f.read().split() or [None])[0]
        if maker == "game":
            nav = os.path.join(tools.maps_dir, map_name + ".nav")
            if not os.path.exists(nav) or os.path.getmtime(nav) < os.path.getmtime(marker):
                return None
        return maker
    except OSError:
        return None


def nav_analyzed(tools: Tools, map_name: str) -> bool | None:
    """Whether maps/<map>.nav holds the analysis (visibility, hiding spots): the header's
    'analyzed' byte. None when there's no readable nav."""
    try:
        with open(os.path.join(tools.maps_dir, map_name + ".nav"), "rb") as f:
            head = f.read(17)
    except OSError:
        return None
    return bool(head[16]) if len(head) == 17 and head[:4] == bytes((0xCE, 0xFA, 0xED, 0xFE)) else None


def nav_marks_changed(tools: Tools, map_name: str) -> bool:
    """True when the safe room / spawn area marks differ from the ones the current nav
    mesh was built with. Marks only add flags, so a change needs a fresh nav mesh."""
    script, used = _navmark_paths(tools, map_name)
    if not os.path.exists(script):
        return False
    try:
        with open(script, encoding="utf-8") as a, open(used, encoding="utf-8") as b:
            return a.read() != b.read()
    except OSError:
        return True


@dataclass
class LaunchOptions:
    width: int = 1600
    height: int = 900
    borderless: bool = False
    monitor_index: int = -1        # -1 = let the game decide
    extra: str = ""                # extra command-line options, e.g. "-novid -high"
    difficulty: str = ""           # Easy / Normal / Hard / Impossible ("" = leave as is)
    lan: bool = True               # sv_lan 1: the map loads ~6 s faster (measured: the listen server no
                                   # longer registers with Steam's servers), but friends can't join online


# How long the last launch took until survivors were in the map, and the latest in-game
# flow report from the map's ready script (both set by a watcher thread).
LOAD_STATUS: dict = {"launch_id": 0, "seconds": None, "flow": None, "flow_seq": 0, "error": None}
FLOW = "hammerless_flow "


def parse_flow(line: str) -> dict | None:
    """'HAMMERLESS_FLOW ok 2380.3' / 'HAMMERLESS_FLOW broken at x y z' / '... broken connected x y z'."""
    parts = line.strip().split()
    if len(parts) < 2 or parts[0].upper() != "HAMMERLESS_FLOW":
        return None
    state = parts[1].lower()
    if state == "ok":
        return {"state": "ok", "length": float(parts[2]) if len(parts) > 2 else 0.0}
    if state == "broken" and len(parts) >= 6:
        return {"state": "broken", "connected": parts[2] == "connected",
                "location": tuple(float(v) for v in parts[3:6])}
    return {"state": state}


def _watch_ready(log: str, start: int, launch_id: int, timeout: float = 660.0):
    """Record the load time (first HAMMERLESS_READY), then keep collecting flow reports
    (the map reloads after nav generation, each load prints one)."""
    import time
    t0 = time.time()
    pos = start
    while time.time() - t0 < timeout and LOAD_STATUS["launch_id"] == launch_id:
        time.sleep(0.5)
        try:
            with open(log, "rb") as f:
                f.seek(pos)
                chunk = f.read()
        except OSError:
            continue
        cut = chunk.rfind(b"\n") + 1           # only complete lines
        text, pos = chunk[:cut].decode("utf-8", "replace"), pos + cut
        for line in text.splitlines():
            low = line.lower()
            if READY in low and LOAD_STATUS["seconds"] is None:
                LOAD_STATUS["seconds"] = time.time() - t0
            elif low.startswith(FLOW):
                report = parse_flow(line)
                if report and report["state"] in ("ok", "broken"):
                    LOAD_STATUS["flow"] = report
                    LOAD_STATUS["flow_seq"] += 1


def bsp_has_lighting(path: str) -> bool:
    """Whether a compiled map has baked lighting (LDR or HDR lighting lump)."""
    import struct
    try:
        with open(path, "rb") as f:
            head = f.read(8 + 16 * 64)
    except OSError:
        return False
    if len(head) < 8 + 16 * 64 or head[:4] != b"VBSP":
        return False
    # L4D2's lump_t is (version, offset, length, fourCC): the length says whether there's lighting
    return any(struct.unpack_from("<iiii", head, 8 + 16 * lump)[2] > 0 for lump in (8, 53))


STEAM_WAIT = 120.0       # seconds to wait for Steam to start and sign in


def _start_with_steam(cmd: list[str], cwd: str, launch_id: int, then) -> None:
    """Steam isn't running (or is still signing in): start it, wait until it's ready, then start
    the game. Started before that, the game quits with 'Steam is not running'."""
    import time
    if not _process_running("steam.exe"):
        exe = steam_exe()
        if exe is None:
            LOAD_STATUS["error"] = "Steam isn't running and wasn't found: start Steam, then press Play"
            return
        subprocess.Popen([exe, "-silent"], cwd=os.path.dirname(exe))
    deadline = time.time() + STEAM_WAIT
    while not steam_ready():
        if time.time() > deadline:
            LOAD_STATUS["error"] = ("Steam didn't finish starting (is it waiting for you to sign in?). "
                                    "When it's ready, press Play")
            return
        if LOAD_STATUS["launch_id"] != launch_id:
            return                          # another launch took over
        time.sleep(1.0)
    time.sleep(2.0)                         # signed in a moment ago: let Steam settle
    if LOAD_STATUS["launch_id"] == launch_id:
        subprocess.Popen(cmd, cwd=cwd)
        then()


def map_mode(tools: Tools, map_name: str) -> str:
    """The game mode a map is started in: co-op, or Hammerless's scripted mode when its logic uses
    overrides or the HUD (gamefiles.mode_files writes maps/<map>.hlmode)."""
    try:
        with open(os.path.join(tools.maps_dir, map_name + ".hlmode"), encoding="utf-8") as f:
            mode = f.read().strip()
        return mode if re.fullmatch(r"[a-z0-9_]+", mode or "") else "coop"
    except OSError:
        return "coop"


def _game_command(tools: Tools, window: LaunchOptions, extra: list[str] | None = None) -> list[str]:
    cmd = [os.path.join(tools.root, "left4dead2.exe"), "-game", "left4dead2",
           "-novid", "-console", "-condebug", "-windowed",
           "-w", str(window.width), "-h", str(window.height)]
    if window.borderless:
        cmd.append("-noborder")
    cmd += window.extra.split() + (extra or [])
    if window.difficulty:
        cmd += ["+z_difficulty", window.difficulty]
    if window.lan:
        cmd += ["+sv_lan", "1"]
    return cmd


def _move_window(window: LaunchOptions):
    if window.monitor_index >= 0:
        from .window import monitors, move_game_window
        mons = monitors()
        if window.monitor_index < len(mons):
            move_game_window(mons[window.monitor_index])


# Build & Play starts the game as the build begins (prestart_game), so it boots while the map compiles;
# launch_game then sends the map to it, once it has reached its main menu.
PRESTART: dict = {"time": None, "log_start": 0}
MENU_MARKER = b"binkopen("          # the menu's background movie starting: commands are taken from then on
                                    # (measured: sent then, taken 0.1 s later; no second copy of the game)
PRESTART_WAIT = 90.0                # (then the map is sent anyway)
PRESTART_GRACE = 15.0               # (a pre-started game not running after this was closed, or crashed)


def prestart_game(tools: Tools, window: LaunchOptions | None = None) -> bool:
    """Start the game to its main menu now, if it isn't running. Returns whether it started one."""
    import time
    if game_running() or not steam_ready():     # (Steam still starting: the launch after the build handles it)
        return False
    window = window or LaunchOptions()
    log = os.path.join(tools.gamedir, "console.log")
    PRESTART["time"] = time.time()
    PRESTART["log_start"] = os.path.getsize(log) if os.path.exists(log) else 0
    subprocess.Popen(_game_command(tools, window), cwd=tools.root)
    _move_window(window)
    return True


def _prestarted_booting(tools: Tools) -> bool:
    """A game this session started ahead is still booting (it hasn't reached its menu yet)."""
    import time
    if PRESTART["time"] is None or time.time() - PRESTART["time"] > PRESTART_WAIT:
        PRESTART["time"] = None
        return False
    if _menu_reached(tools):
        PRESTART["time"] = None
        return False
    if time.time() - PRESTART["time"] > PRESTART_GRACE and not game_running():
        PRESTART["time"] = None              # (closed or crashed while booting: start afresh)
        return False
    return True


def _menu_reached(tools: Tools) -> bool:
    log = os.path.join(tools.gamedir, "console.log")
    try:
        with open(log, "rb") as f:
            f.seek(PRESTART["log_start"])
            return MENU_MARKER in f.read().lower()
    except OSError:
        return False


def _send_when_ready(tools: Tools, commands: list[str], launch_id: int, fresh_cmd: list[str], move_window):
    """Send commands to the pre-started game once it has reached its menu (or given up waiting). If it was
    closed meanwhile, start the game afresh with the map (fresh_cmd): -hijack would start a bare game."""
    import time
    started = PRESTART["time"] or time.time()
    deadline = started + PRESTART_WAIT
    while time.time() < deadline:
        if LOAD_STATUS["launch_id"] != launch_id:
            return                                  # a newer launch took over
        running = game_running()
        if running and _menu_reached(tools):
            break
        if not running and time.time() - started > PRESTART_GRACE:
            PRESTART["time"] = None
            subprocess.Popen(fresh_cmd, cwd=tools.root)
            move_window()
            return
        time.sleep(0.25)
    PRESTART["time"] = None
    if LOAD_STATUS["launch_id"] == launch_id:
        send_commands(tools, commands)


def launch_game(tools: Tools, map_name: str, generate_nav: bool = False, extra: list[str] | None = None,
                window: LaunchOptions | None = None, analyze_nav: bool = False):
    """Load the map. Reuses a running game if there is one.

    Nav generation must run after the map has loaded (commands after +map on the
    command line run too early), so it's sent separately once console.log shows
    the map started. nav_generate saves maps/<name>.nav and reloads the map.
    """
    log = os.path.join(tools.gamedir, "console.log")
    log_start = os.path.getsize(log) if os.path.exists(log) else 0
    window = window or LaunchOptions()
    mode = map_mode(tools, map_name)
    map_cmd = f"{map_name} {mode}" if mode != "coop" else map_name
    booting = _prestarted_booting(tools)
    fresh = not game_running() and not booting
    if not fresh:
        # sv_cheats 0 resets every cheat cvar (nb_stop, z_common_limit, ...) left over
        # from earlier testing or play, so each build starts from a clean game.
        pre = [f"z_difficulty {window.difficulty}"] if window.difficulty else []
        pre.append(f"sv_lan {1 if window.lan else 0}")
        if bsp_has_lighting(os.path.join(tools.maps_dir, map_name + ".bsp")):
            # the engine turns mat_fullbright on for a map without lighting (a Quick build) and leaves
            # it on for the rest of the session: a lit map loaded after one would look unlit
            pre = ["sv_cheats 1", "mat_fullbright 0"] + pre
        commands = ["con_logfile console.log"] + pre + ["sv_cheats 0", f"map {map_cmd}"]
    cmd = _game_command(tools, window, extra)
    cmd += ["+map", map_name] + ([mode] if mode != "coop" else [])
    # (a new launch before anything is sent: an older one's scripts see it and stop)
    LOAD_STATUS["launch_id"] += 1
    LOAD_STATUS["seconds"] = None
    LOAD_STATUS["flow"] = None
    LOAD_STATUS["error"] = None
    launch_id = LOAD_STATUS["launch_id"]
    proc = send_commands(tools, commands) if not fresh and not booting else None

    def move_window():
        _move_window(window)
    if booting:          # started ahead by Build & Play: the map goes to it once it reaches its menu
        threading.Thread(target=_send_when_ready, args=(tools, commands, launch_id, cmd, move_window),
                         daemon=True).start()
    elif proc is None:
        if steam_ready():
            proc = subprocess.Popen(cmd, cwd=tools.root)
            move_window()
        else:
            threading.Thread(target=_start_with_steam, args=(cmd, tools.root, launch_id, move_window),
                             daemon=True).start()
    else:
        move_window()
    threading.Thread(target=_watch_ready, args=(log, log_start, launch_id), daemon=True).start()
    # a fresh game ignores the mode on its command line (measured): load the map, then switch it into
    # its mode from the console (the game's own reloads, like nav_analyze's, keep the mode it's in)
    switch = [(READY, [f"map {map_cmd}"])] if fresh and mode != "coop" and not generate_nav else []
    if analyze_nav and not generate_nav:
        steps = analyze_steps()
        if switch:
            steps[-1] = (steps[-1][0], steps[-1][1] + [f"map {map_cmd}"])
        threading.Thread(target=_run_console_script, args=(tools, steps, log_start),
                         kwargs={"launch_id": launch_id}, daemon=True).start()
    elif switch:
        threading.Thread(target=_run_console_script, args=(tools, switch, log_start),
                         kwargs={"launch_id": launch_id}, daemon=True).start()
    if generate_nav:
        set_nav_maker(tools, map_name, "game")
        script, used = _navmark_paths(tools, map_name)
        mark = os.path.exists(script)

        def marked():                       # the marks count as applied only once they are
            if mark:
                shutil.copyfile(script, used)
        threading.Thread(target=_run_console_script, args=(tools, nav_steps(map_name, mark, map_cmd), log_start),
                         kwargs={"launch_id": launch_id, "on_done": marked}, daemon=True).start()
    return proc
