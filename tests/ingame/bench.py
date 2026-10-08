"""Hammerless test bench: drive the running game from Python, fast and two-way.

The game side (bench/hl_bench.nut) is installed as scripts/vscripts/mapspawn.nut while a Bench is open,
so it starts on every map load. Requests and answers go through files in left4dead2/ems/ (the only
folder the game's scripts may read and write): a round trip takes about 0.1 s.

    from tests.ingame.bench import Bench
    with Bench() as b:                       # starts the game if needed, installs the bench
        b.load("c2m1_highway")               # loads a map and waits until the survivors are in
        print(b.value("Director.GetMapName()"))
        b.console("god 1")
        b.screenshot("look", pos=(0, 0, 100), ang=(10, 90))

Building a test map from a .blend: build() (Blender, no lighting, about 35 s).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from hammerless.core import compile as cc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe"


class BenchError(Exception):
    pass


class Bench:
    def __init__(self, game_root: str | None = None):
        self.tools = cc.Tools(game_root or cc.find_game_root())
        self.game = self.tools.gamedir
        self.ems = os.path.join(self.game, "ems")
        self.log = os.path.join(self.game, "console.log")
        self.mapspawn = os.path.join(self.game, "scripts", "vscripts", "mapspawn.nut")
        self._id = int(time.time() * 1000) % 1_000_000_000
        self._installed = False

    # ------------------------------------------------------------ setup

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self):
        """Install the game side. A mapspawn.nut that isn't ours is never overwritten."""
        if os.path.exists(self.mapspawn) and "Hammerless test bench" not in open(self.mapspawn, errors="replace").read():
            raise BenchError(f"{self.mapspawn} exists and isn't the bench's: not touching it")
        os.makedirs(self.ems, exist_ok=True)
        shutil.copy(os.path.join(HERE, "bench", "hl_bench.nut"), self.mapspawn)
        self._installed = True

    def close(self):
        """Remove the game side (the game keeps running)."""
        if self._installed and os.path.exists(self.mapspawn):
            os.remove(self.mapspawn)
        for f in ("hl_bench_in", "hl_bench_out", "hl_bench_state"):
            try:
                os.remove(os.path.join(self.ems, f))
            except OSError:
                pass
        self._installed = False

    # ------------------------------------------------------------ the game

    def state(self) -> tuple[str, int]:
        """(map, how many times the bench has started) as the game last wrote it."""
        try:
            name, starts = open(os.path.join(self.ems, "hl_bench_state")).read().strip("\x00 \n").split()
            return name, int(starts)
        except (OSError, ValueError):
            return "", 0

    def load(self, map_name: str, mode: str = "", timeout: float = 240.0, settle: float = 3.0):
        """Load a map (starting the game if it isn't running) and wait until the survivors are in it.
        mode: a game mode ('hammerless' for maps with Override nodes), or '' for the map's own."""
        before = self.state()[1]
        if not cc.game_running():
            cc.launch_game(self.tools, map_name)
        else:
            cc.send_commands(self.tools, [f"map {map_name} {mode}".strip()])
        self._wait_loaded(map_name, before, timeout, settle)

    def run(self, code: str, timeout: float = 10.0):
        """Run Squirrel in the game (root scope) and return what it returns, as Python values
        (entities as {'ent', 'class', 'name'}, vectors as [x, y, z])."""
        self._id += 1
        rid = str(self._id)
        tmp = os.path.join(self.ems, "hl_bench_in.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(rid + "\n" + code)
        for attempt in range(50):           # the game may be reading the file at that moment (Windows)
            try:
                os.replace(tmp, os.path.join(self.ems, "hl_bench_in"))
                break
            except PermissionError:
                time.sleep(0.02)
        else:
            raise BenchError("couldn't write the request file (the game keeps it open?)")
        out = os.path.join(self.ems, "hl_bench_out")
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                text = open(out, encoding="utf-8", errors="replace").read().rstrip("\x00")
            except OSError:
                text = ""
            head, _, rest = text.partition("\n")
            if head == rid:
                status, _, body = rest.partition("\n")
                if status == "error":
                    raise BenchError(body)
                return json.loads(body) if body else None
            time.sleep(0.02)
        raise BenchError(f"no answer within {timeout:.0f} s (is the map loaded? is the bench installed?)")

    def value(self, expr: str):
        """The value of one Squirrel expression."""
        return self.run(f"return ({expr});")

    def console(self, *commands: str, wait: float = 0.2):
        """Console commands, run by the host (cheat commands need sv_cheats 1)."""
        self.run("".join(f"SendToConsole({json.dumps(c)});" for c in commands))
        time.sleep(wait)

    def wait_until(self, expr: str, timeout: float = 30.0, every: float = 0.2):
        """Wait until a Squirrel expression is true; returns the seconds it took."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.value(expr):
                    return time.time() - t0
            except BenchError:
                pass
            time.sleep(every)
        raise BenchError(f"still false after {timeout:.0f} s: {expr}")

    def look_at(self, target, distance: float = 36.0, noclip: bool = True):
        """Put the host distance units from target (x, y, z, or an entity name: its centre), looking straight
        at it (for using buttons, picking things up, screenshots). Turns noclip on so the host stays put."""
        import math
        if isinstance(target, str):
            target = self.value(f'Entities.FindByName(null, {json.dumps(target)}).GetCenter()')
        if noclip and self.value('NetProps.GetPropInt(GetListenServerHost(), "movetype")') != 8:   # 8: noclip
            self.console("noclip", wait=0.3)
        x, y, z = target
        self.run(f"local h = GetListenServerHost(); h.SetOrigin(Vector({x - distance}, {y}, {z - 64})); "
                 "h.SetVelocity(Vector(0, 0, 0));")
        time.sleep(0.2)
        ex, ey, ez = self.value("GetListenServerHost().EyePosition()")
        dx, dy, dz = x - ex, y - ey, z - ez
        yaw = math.degrees(math.atan2(dy, dx))
        pitch = -math.degrees(math.atan2(dz, math.hypot(dx, dy)))
        self.console(f"setang {pitch:.2f} {yaw:.2f} 0", wait=0.3)

    def walk_to(self, fraction: float, step: float = 400.0, pause: float = 0.2):
        """Move every survivor along the map's path (its flow) to fraction of the way (0 start, 1 end), in
        steps of about step units (one big teleport doesn't move the Director's furthest-survivor value the
        same way). About 15 s for a short map. Needs the map's flow."""
        self.run('''
            ::HLB_Path <- [];
            local t = {}; NavMesh.GetAllAreas(t);
            foreach (a in t) { if (a.GetSizeX() < 24 || a.GetSizeY() < 24) continue;
                ::HLB_Path.append([GetFlowDistanceForPosition(a.GetCenter()), a]); }
            ::HLB_Path.sort(function(x, y) { return x[0] > y[0] ? 1 : (x[0] < y[0] ? -1 : 0); });
            return true;''')
        # Where a position is on the path (GetFlowDistanceForPosition) and where the Director counts a player
        # standing there (GetCurrentFlowDistanceForPlayer) disagree for some areas off the route (measured):
        # try the areas nearest each step, and keep one only when the Director agrees
        goal = fraction * self.value("GetMaxFlowDistance()")
        f = self.value("Director.GetFurthestSurvivorFlow()")
        while f < goal - 1:
            f = min(goal, f + step)
            near = self.run(f'''
                local d = [];
                foreach (i, p in ::HLB_Path) d.append([fabs(p[0] - {f}), i]);
                d.sort(function(x, y) {{ return x[0] > y[0] ? 1 : (x[0] < y[0] ? -1 : 0); }});
                local out = []; for (local k = 0; k < 8 && k < d.len(); k++) out.append(d[k][1]);
                return out;''')
            for i in near:
                self.run(f'''
                    local a = ::HLB_Path[{i}][1], n = 0, e = null;
                    while (e = Entities.FindByClassname(e, "player")) if (e.IsSurvivor()) {{
                        e.SetOrigin(a.GetCenter() + Vector((n % 2) * 24, (n / 2) * 24, 8)); e.SetVelocity(Vector(0, 0, 0)); n++; }}
                    return true;''')
                time.sleep(0.15)
                if abs(self.value("GetCurrentFlowDistanceForPlayer(GetListenServerHost())") - f) < step * 1.5:
                    break
            time.sleep(pause)

    def use(self, seconds: float = 0.2):
        """Hold the use key (pressing buttons, picking things up)."""
        self.console("+use", wait=seconds)
        self.console("-use", wait=0.2)

    # ------------------------------------------------------------ the console log

    def mark(self) -> int:
        return os.path.getsize(self.log) if os.path.exists(self.log) else 0

    def lines_since(self, mark: int, *contains: str) -> list[str]:
        with open(self.log, "rb") as f:
            f.seek(mark)
            lines = f.read().decode("latin-1", "replace").splitlines()
        return [l for l in lines if not contains or any(c in l for c in contains)]

    def events_since(self, mark: int) -> list[str]:
        """Logic wires that fired since mark (maps built with the debug log on): 'node.out -> node.in'."""
        return [l.split("HAMMERLESS_EVENT ", 1)[1].strip() for l in self.lines_since(mark, "HAMMERLESS_EVENT ")]

    # ------------------------------------------------------------ pictures

    def screenshot(self, name: str, pos=None, ang=None, hud: bool = False, out_dir: str | None = None) -> str:
        """A screenshot (JPEG) from the host's eyes, or from pos (x, y, z) looking at ang (pitch, yaw).
        Returns the file's path (copied to out_dir when given). Needs sv_cheats 1 to move the camera."""
        cmds = ["hideconsole", "gameui_hide"]      # the console or a menu / dialog would be in the picture
        cmds += ["cl_drawhud 0", "r_drawviewmodel 0"] if not hud else []
        if pos is not None:
            cmds.append("setpos {} {} {}".format(*pos))
        if ang is not None:
            cmds.append("setang {} {} 0".format(*ang))
        self.console(*cmds, wait=0.5)
        shots = os.path.join(self.game, "screenshots")
        path = os.path.join(shots, f"{name}.jpg")
        if os.path.exists(path):
            os.remove(path)
        self.console(f"jpeg {name} 90", wait=0.1)
        t0 = time.time()
        while not os.path.exists(path) and time.time() - t0 < 10:
            time.sleep(0.1)
        if not hud:
            self.console("cl_drawhud 1", "r_drawviewmodel 1", wait=0.0)
        if not os.path.exists(path):
            raise BenchError("no screenshot appeared (is the game window minimised?)")
        time.sleep(0.3)                 # let the game finish writing it
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            dest = os.path.join(out_dir, f"{name}.jpg")
            shutil.copy(path, dest)
            return dest
        return path

    # ------------------------------------------------------------ building test maps

    def build(self, blend: str, map_name: str, prep: str = "", out_dir: str | None = None,
              debug_log: bool = True, mode: str = "", timeout: float = 600.0) -> str:
        """Build blend as map_name without lighting (with the debug log of logic wires on), play it, and
        wait until the survivors are in, leaving the .blend untouched. prep: Python run in Blender first
        (bpy and hammerless imported, s = the scene's Hammerless settings). Returns the build folder."""
        import textwrap
        out_dir = out_dir or os.path.join(os.path.dirname(self.game), "hammerless_bench_build")
        os.makedirs(out_dir, exist_ok=True)
        script = os.path.join(out_dir, "bench_build.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(BUILD_SCRIPT.format(root=ROOT, out=out_dir, map=map_name, debug=debug_log,
                                        prep=textwrap.indent(prep, " " * 8) or "        pass"))
        result = os.path.join(out_dir, "bench_result.txt")
        if os.path.exists(result):
            os.remove(result)
        before = self.state()[1]
        subprocess.run([BLENDER, "--factory-startup", blend, "--python", script], timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        text = open(result).read() if os.path.exists(result) else "no result"
        if not text.startswith("ok"):
            raise BenchError(f"build failed: {text}")
        self._wait_loaded(map_name, before, 240)
        return out_dir

    def _wait_loaded(self, map_name, before, timeout, settle=3.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            name, starts = self.state()
            if name == map_name and starts != before:
                break
            time.sleep(0.25)
        else:
            raise BenchError(f"{map_name} didn't load within {timeout:.0f} s")
        # the map runs scripts before players exist: wait for the host survivor, then a moment more
        self.wait_until("GetListenServerHost() != null && GetListenServerHost().IsSurvivor()", timeout=60)
        time.sleep(settle)


# runs in a Blender with a window (Build is a modal operator); never saves the .blend
BUILD_SCRIPT = '''
import os, sys, time, traceback, bpy
sys.path.insert(0, r"{root}")
import hammerless
try:
    hammerless.register()
except Exception:
    pass
OUT = r"{out}"
from hammerless.core import compile as cc
LOG = os.path.join(cc.Tools(cc.find_game_root()).gamedir, "console.log")
MARK = os.path.getsize(LOG) if os.path.exists(LOG) else 0
def done(text):
    open(os.path.join(OUT, "bench_result.txt"), "w").write(text)
    os._exit(0)
def start():
    global bpy                  # (prep code may import bpy again)
    try:
        s = bpy.context.scene.hammerless
        s.map_name, s.output_dir, s.sound_mode = "{map}", OUT, "OFF"
        s.compile_preset, s.vis_tool, s.light_tool = "QUICK", "VALVE", "VALVE"
        s.debug_log = {debug}
{prep}
        win = bpy.context.window_manager.windows[0]
        with bpy.context.temp_override(window=win, area=win.screen.areas[0]):
            r = bpy.ops.hammerless.build(play=True)
        if "CANCELLED" in r:
            done("error\\nBuild was cancelled: " + bpy.data.texts["hammerless_log"].as_string()[-2000:]
                 if "hammerless_log" in bpy.data.texts else "error\\nBuild was cancelled")
        bpy.app.timers.register(wait, first_interval=1.0)
    except Exception:
        done("error\\n" + traceback.format_exc())
T0 = time.time()
def wait():
    # quit once the game has started loading the map (the bench waits for the survivors)
    with open(LOG, "rb") as f:
        f.seek(MARK)
        if b"Map: {map}" in f.read():
            done("ok")
    if time.time() - T0 > 500:
        done("error\\ntimed out")
    return 1.0
bpy.app.timers.register(start, first_interval=1.0)
'''
