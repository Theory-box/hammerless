"""Locate L4D2, run vbsp/vvis/vrad, parse logs, launch the game."""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

DEFAULT_GAME_ROOTS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Left 4 Dead 2",
    r"D:\SteamLibrary\steamapps\common\Left 4 Dead 2",
    r"C:\Program Files\Steam\steamapps\common\Left 4 Dead 2",
]

@dataclass
class CompileOptions:
    vis: str = "FULL"           # SKIP / FAST / FULL
    rad: str = "NORMAL"         # SKIP / FAST / NORMAL / FINAL
    hdr: str = "BOTH"           # LDR / HDR / BOTH
    static_prop_lighting: bool = False
    extra_vbsp: str = ""
    extra_vvis: str = ""
    extra_vrad: str = ""

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
        if self.static_prop_lighting or self.rad == "FINAL":
            args += ["-StaticPropLighting", "-StaticPropPolys"]
        return args + self.extra_vrad.split()


PRESETS = {
    "QUICK": CompileOptions(vis="SKIP", rad="SKIP"),   # geometry only; map is fullbright
    "FAST": CompileOptions(vis="FAST", rad="FAST", hdr="LDR"),   # one lighting pass instead of two
    "NORMAL": CompileOptions(vis="FULL", rad="NORMAL"),
    "FINAL": CompileOptions(vis="FULL", rad="FINAL"),
}


def find_game_root(extra: list[str] | None = None) -> str | None:
    candidates = list(extra or []) + DEFAULT_GAME_ROOTS
    # Also read Steam library folders
    vdf = r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"
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

def bsp_signature(path: str) -> str:
    """Fingerprint of a compiled map without its pakfile (lump 40): the game saves its stringtable
    dictionary there whenever it loads the map (measured: the only lump that changes)."""
    import hashlib
    import struct
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
    return h.hexdigest()


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
        self.steps: list[tuple[str, list[str]]] = [
            ("vbsp", [tools.exe("vbsp")] + opts.vbsp_args() + game + [self.base])]
        if vvis is not None:
            self.steps.append(("vvis", [tools.exe("vvis")] + vvis + game + [self.base]))
        if vrad is not None:
            self.steps.append(("vrad", [tools.exe("vrad")] + vrad + game + [self.base]))
        self.plan = "full"            # what a smart build decided (buildplan.plan); see _choose_steps
        self.log: list[str] = []
        self.done = False
        self.failed = False
        self.summary: LogSummary | None = None
        self.lighting: list = []     # bspcheck.lighting_problems after vrad: (message, location, object)
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "CompileJob":
        lin = self.base + ".lin"
        if os.path.exists(lin):
            os.remove(lin)
        self._thread.start()
        return self

    def _stamp(self) -> str:
        """Fingerprint of what gets compiled: the VMF text and the compile options."""
        import hashlib
        with open(self.vmf, "rb") as f:
            return hashlib.sha1(f.read() + repr(self._opts).encode()).hexdigest()

    def up_to_date(self) -> bool:
        """The last successful compile used the same VMF and options, and the game has its BSP
        (compared without the pakfile, which the game rewrites on every load)."""
        try:
            with open(self.base + ".stamp", encoding="utf-8") as f:
                same = f.read() == self._stamp()
            game_bsp = os.path.join(self.tools.maps_dir, self.name + ".bsp")
            return same and bsp_signature(game_bsp) == bsp_signature(self.base + ".bsp")
        except (OSError, ValueError):
            return False

    def _run(self):
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
            for name, cmd in steps:
                self._q.put(f"==== {name} ====")
                t0 = time.time()
                proc = subprocess.Popen(
                    cmd, cwd=os.path.dirname(self.vmf), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, errors="replace",
                    # below-normal priority: the PC stays responsive while vvis/vrad use every core
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
                out = []
                for line in proc.stdout:
                    self._q.put(line.rstrip("\n"))
                    out.append(line)
                code = proc.wait()
                self.timings.append((name, time.time() - t0))
                if code != 0 or (name == "vbsp" and os.path.exists(self.base + ".lin")):
                    self._q.put(f"!! {name} failed (exit code {code})")
                    self._q.put(("FAILED",))
                    return
                if name == "vrad":
                    try:
                        from .bspcheck import lighting_problems
                        self.lighting = lighting_problems(self.base + ".bsp", self.vmf, "".join(out))
                    except Exception as ex:      # a checker bug must never fail the build
                        self._q.put(f"(lighting check skipped: {ex})")
                    for msg, _loc, _obj in self.lighting:
                        self._q.put(f"!! {msg}")
            if self.plan != "full":
                from .buildplan import strip_stale
                strip_stale(self.base + ".bsp")
            if self.copy_to_game:
                os.makedirs(self.tools.maps_dir, exist_ok=True)
                self._copy_bsp(os.path.join(self.tools.maps_dir, self.name + ".bsp"))
                self._q.put(f"Copied {self.name}.bsp to {self.tools.maps_dir}")
            with open(self.base + ".stamp", "w", encoding="utf-8") as f:
                f.write(self._stamp())
            shutil.copy2(self.vmf, self.base + ".built.vmf")      # what this BSP was made from
            with open(self.base + ".built.opts", "w", encoding="utf-8") as f:
                f.write(repr(self._opts))
            self._q.put("Timing: " + ", ".join(f"{n} {t:.1f}s" for n, t in self.timings))
            self._q.put(("OK",))
        except Exception as ex:  # surfaced to the user in the log
            self._q.put(f"!! {ex}")
            self._q.put(("FAILED",))

    def _choose_steps(self) -> list[tuple[str, list[str]]]:
        """Smart build: the least work that gives the same map as a full compile, judged against
        the VMF the current BSP was compiled from (buildplan.plan). A doubt means a full compile."""
        from .buildplan import plan
        old = None
        try:
            with open(self.base + ".built.opts", encoding="utf-8") as f:
                same_opts = f.read() == repr(self._opts)
            if same_opts and os.path.exists(self.base + ".bsp"):
                with open(self.base + ".built.vmf", encoding="utf-8") as f:
                    old = f.read()
        except OSError:
            pass
        with open(self.vmf, encoding="utf-8") as f:
            kind, why = plan(old, f.read())
        if kind == "full":
            self.plan = "full"
            return self.steps
        self.plan = "entities" if kind == "same" else kind
        game = ["-game", self.tools.gamedir]
        steps = [("vbsp (entities only)", [self.tools.exe("vbsp"), "-onlyents"] + game + [self.base])]
        if self.plan == "lighting":
            steps += [st for st in self.steps if st[0] == "vrad"]
            self._q.put(f"Smart build: {why}: updating entities and relighting, keeping geometry and visibility")
        else:
            self._q.put(f"Smart build: {why}: updating entities only, keeping geometry, visibility and lighting")
        return steps

    def _copy_bsp(self, dest: str):
        """Copy the BSP into the game. While the game has this map loaded it keeps the file
        open and Windows refuses the copy: unload the map (disconnect) and try again."""
        import time
        for attempt in range(12):
            try:
                shutil.copy2(self.base + ".bsp", dest)
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


def game_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq left4dead2.exe", "/NH"],
                             capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return "left4dead2.exe" in out.lower()
    except OSError:
        return False


def send_commands(tools: Tools, commands: list[str]):
    """Run console commands in the already-running game (via -hijack).

    Commands go through a cfg file: passing them as `+cmd args` breaks on
    arguments that start with '-' (e.g. `setpos 10 -200 0`), which the launcher
    reads as command-line switches.
    """
    cfg_dir = os.path.join(tools.gamedir, "cfg")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "hammerless_cmd.cfg"), "w", encoding="utf-8") as f:
        f.write("\n".join(commands) + "\n")
    return subprocess.Popen([os.path.join(tools.root, "left4dead2.exe"), "-game", "left4dead2", "-hijack",
                             "+exec", "hammerless_cmd"], cwd=tools.root)


def _run_console_script(tools: Tools, steps: list[tuple[str, list[str]]], log_start: int,
                        timeout: float = 240.0):
    """Tail console.log; for each (marker, commands) step, wait until `marker`
    appears (after the previous step's match), then send `commands`."""
    import time
    log = os.path.join(tools.gamedir, "console.log")
    pos = log_start
    deadline = time.time() + timeout
    for marker, commands in steps:
        marker = marker.lower()
        while True:
            if time.time() > deadline:
                return
            time.sleep(1.0)
            try:
                if os.path.getsize(log) < pos:
                    pos = 0  # log was truncated/restarted
                with open(log, encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
            except OSError:
                continue
            i = chunk.lower().find(marker)
            if i >= 0:
                pos += len(chunk[:i + len(marker)].encode("utf-8", "replace"))
                break
        time.sleep(3.0)  # let the client finish connecting / the map settle
        if commands:
            send_commands(tools, commands)


# Printed in the status block at the end of every map load, both on a fresh game
# start and when loading into a running game ("Receiving uncompressed update" is
# only printed in the second case).
LOADED = "server number:"


# Printed by the map's hammerless_ready script once survivors exist (see gamefiles.py).
# nav_generate needs players standing in the map; sent earlier, it fails.
READY = "hammerless_ready survivors spawned"   # not just the name: the game also prints "hammerless_ready executing script"


def nav_steps(map_name: str, mark: bool = True) -> list[tuple[str, list[str]]]:
    """Generate nav, mark safe-room attributes (see nav.py), save, reload fresh."""
    steps = [(f"host_newgame on map {map_name.lower()}", []),
             (READY, ["sv_cheats 1", "nav_generate"]),
             (".nav' saved.", []),
             (READY, [f"script_execute hammerless/navmark_{map_name}"] if mark
              else ["sv_cheats 0", f"map {map_name}"])]
    if mark:
        steps.append(("hammerless_navmark done", []))
        steps.append((".nav' saved.", ["sv_cheats 0", f"map {map_name}"]))
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


def set_nav_maker(tools: Tools, map_name: str, maker: str) -> None:
    """Record who made maps/<map>.nav: "blender" (our generator) or "game" (nav_generate)."""
    path = _nav_maker_path(tools, map_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(maker)


def nav_maker(tools: Tools, map_name: str) -> str | None:
    """Who made the current nav mesh ("blender" / "game"), or None when unknown (older builds, or
    a game generation that never saved: the marker is written when it starts, so the nav has to
    be newer than the marker)."""
    marker = _nav_maker_path(tools, map_name)
    try:
        with open(marker, encoding="utf-8") as f:
            maker = f.read().strip() or None
        if maker == "game":
            nav = os.path.join(tools.maps_dir, map_name + ".nav")
            if not os.path.exists(nav) or os.path.getmtime(nav) < os.path.getmtime(marker):
                return None
        return maker
    except OSError:
        return None


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


# How long the last launch took until survivors were in the map, and the latest in-game
# flow report from the map's ready script (both set by a watcher thread).
LOAD_STATUS: dict = {"launch_id": 0, "seconds": None, "flow": None, "flow_seq": 0}
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


def _watch_ready(log: str, start: int, launch_id: int, timeout: float = 300.0):
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
    if game_running():
        # sv_cheats 0 resets every cheat cvar (nb_stop, z_common_limit, ...) left over
        # from earlier testing or play, so each build starts from a clean game.
        pre = [f"z_difficulty {window.difficulty}"] if window.difficulty else []
        proc = send_commands(tools, ["sv_cheats 0"] + pre + [f"map {map_name}"])
    else:
        cmd = [os.path.join(tools.root, "left4dead2.exe"), "-game", "left4dead2",
               "-novid", "-console", "-condebug", "-windowed",
               "-w", str(window.width), "-h", str(window.height)]
        if window.borderless:
            cmd.append("-noborder")
        cmd += window.extra.split() + (extra or [])
        if window.difficulty:
            cmd += ["+z_difficulty", window.difficulty]
        cmd += ["+map", map_name]
        proc = subprocess.Popen(cmd, cwd=tools.root)
    if window.monitor_index >= 0:
        from .window import monitors, move_game_window
        mons = monitors()
        if window.monitor_index < len(mons):
            move_game_window(mons[window.monitor_index])
    LOAD_STATUS["launch_id"] += 1
    LOAD_STATUS["seconds"] = None
    LOAD_STATUS["flow"] = None
    threading.Thread(target=_watch_ready, args=(log, log_start, LOAD_STATUS["launch_id"]), daemon=True).start()
    if analyze_nav and not generate_nav:
        threading.Thread(target=_run_console_script, args=(tools, analyze_steps(), log_start),
                         daemon=True).start()
    if generate_nav:
        set_nav_maker(tools, map_name, "game")
        script, used = _navmark_paths(tools, map_name)
        mark = os.path.exists(script)
        if mark:
            shutil.copyfile(script, used)
        threading.Thread(target=_run_console_script, args=(tools, nav_steps(map_name, mark), log_start),
                         daemon=True).start()
    return proc
