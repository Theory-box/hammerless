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

PRESETS = {
    # name: (vvis args or None to skip, vrad args or None to skip)
    "QUICK": (None, None),                     # geometry only, fullbright-ish; fastest
    "FAST": (["-fast"], ["-fast", "-both"]),
    "NORMAL": ([], ["-both"]),
    "FINAL": ([], ["-both", "-final", "-StaticPropLighting", "-StaticPropPolys"]),
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

class CompileJob:
    """Runs the compile steps one after another without blocking the caller.

    Call poll() regularly (e.g. from a Blender timer); it returns new log lines.
    `done`, `failed` and `summary` are set when finished.
    """

    def __init__(self, tools: Tools, vmf_path: str, preset: str = "FAST", copy_to_game: bool = True):
        self.tools = tools
        self.vmf = os.path.abspath(vmf_path)
        self.base = os.path.splitext(self.vmf)[0]
        self.name = os.path.basename(self.base)
        self.copy_to_game = copy_to_game
        vvis, vrad = PRESETS[preset]
        game = ["-game", tools.gamedir]
        self.steps: list[tuple[str, list[str]]] = [("vbsp", [tools.exe("vbsp")] + game + [self.base])]
        if vvis is not None:
            self.steps.append(("vvis", [tools.exe("vvis")] + vvis + game + [self.base]))
        if vrad is not None:
            self.steps.append(("vrad", [tools.exe("vrad")] + vrad + game + [self.base]))
        self.log: list[str] = []
        self.done = False
        self.failed = False
        self.summary: LogSummary | None = None
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "CompileJob":
        lin = self.base + ".lin"
        if os.path.exists(lin):
            os.remove(lin)
        self._thread.start()
        return self

    def _run(self):
        try:
            for name, cmd in self.steps:
                self._q.put(f"==== {name} ====")
                proc = subprocess.Popen(
                    cmd, cwd=os.path.dirname(self.vmf), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for line in proc.stdout:
                    self._q.put(line.rstrip("\n"))
                code = proc.wait()
                if code != 0 or (name == "vbsp" and os.path.exists(self.base + ".lin")):
                    self._q.put(f"!! {name} failed (exit code {code})")
                    self._q.put(("FAILED",))
                    return
            if self.copy_to_game:
                os.makedirs(self.tools.maps_dir, exist_ok=True)
                shutil.copy2(self.base + ".bsp", os.path.join(self.tools.maps_dir, self.name + ".bsp"))
                self._q.put(f"Copied {self.name}.bsp to {self.tools.maps_dir}")
            self._q.put(("OK",))
        except Exception as ex:  # surfaced to the user in the log
            self._q.put(f"!! {ex}")
            self._q.put(("FAILED",))

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


LOADED = "receiving uncompressed update from server"   # end of every map load


def nav_steps(map_name: str, mark: bool = True) -> list[tuple[str, list[str]]]:
    """Generate nav, mark safe-room attributes (see nav.py), save, reload fresh."""
    steps = [(f"host_newgame on map {map_name.lower()}", []),
             (LOADED, ["sv_cheats 1", "nav_generate"]),
             (".nav' saved.", []),
             (LOADED, [f"script_execute hammerless/navmark_{map_name}"] if mark
              else ["sv_cheats 0", f"map {map_name}"])]
    if mark:
        steps.append(("hammerless_navmark done", []))
        steps.append((".nav' saved.", ["sv_cheats 0", f"map {map_name}"]))
    return steps


def launch_game(tools: Tools, map_name: str, generate_nav: bool = False, extra: list[str] | None = None):
    """Load the map. Reuses a running game if there is one.

    Nav generation must run after the map has loaded (commands after +map on the
    command line run too early), so it's sent separately once console.log shows
    the map started. nav_generate saves maps/<name>.nav and reloads the map.
    """
    log = os.path.join(tools.gamedir, "console.log")
    log_start = os.path.getsize(log) if os.path.exists(log) else 0
    if game_running():
        # sv_cheats 0 resets every cheat cvar (nb_stop, z_common_limit, ...) left over
        # from earlier testing or play, so each build starts from a clean game.
        proc = send_commands(tools, ["sv_cheats 0", f"map {map_name}"])
    else:
        cmd = [os.path.join(tools.root, "left4dead2.exe"), "-game", "left4dead2",
               "-novid", "-console", "-condebug", "-windowed"]
        cmd += extra or []
        cmd += ["+map", map_name]
        proc = subprocess.Popen(cmd, cwd=tools.root)
    if generate_nav:
        mark = os.path.exists(os.path.join(tools.gamedir, "scripts", "vscripts", "hammerless",
                                           f"navmark_{map_name}.nut"))
        threading.Thread(target=_run_console_script, args=(tools, nav_steps(map_name, mark), log_start),
                         daemon=True).start()
    return proc
