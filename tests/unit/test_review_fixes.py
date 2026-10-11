"""Logic and collision fixes: run with `python -m unittest discover tests/unit` (no Blender needed)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hammerless.core import geometry as g
from hammerless.core.ir import Entity, MapIR


class TestLogicNames(unittest.TestCase):
    def test_reused_entity_without_a_name_gets_one(self):
        # a func_detail (no targetname) or an empty catalog name, turned into a button / mover
        from hammerless.core.logic import LNode, compile_graph
        ir = MapIR()
        lever = Entity("func_detail", None, (0, 0, 0), {}, [g.box_brush((0, 0, 0), (8, 8, 8), "x")], "lever")
        gate = Entity("func_brush", None, (0, 0, 0), {"targetname": ""}, [g.box_brush((50, 0, 0), (58, 64, 128), "x")],
                      "gate")
        ir.entities += [lever, gate]
        problems = compile_graph([LNode("Press", "BUTTON", {}, "lever"), LNode("Open", "MOVE", {}, "gate")], [], ir)
        self.assertEqual(problems, [])
        self.assertEqual((lever.classname, lever.keyvalues["targetname"]), ("func_button", "hl_lever"))
        self.assertEqual((gate.classname, gate.keyvalues["targetname"]), ("func_movelinear", "hl_gate"))

    def test_script_functions_of_same_slug_nodes_dont_clash(self):
        from hammerless.core.logic import LNode, compile_graph
        ir = MapIR()
        nodes = [LNode("Go here", "TELEPORT", pos=(0, 0, 0)), LNode("Go-here", "TELEPORT", pos=(64, 0, 0)),
                 LNode("Tank 1", "SPAWN", {"what": "tank"}), LNode("Tank-1", "SPAWN", {"what": "tank"})]
        compile_graph(nodes, [], ir)
        names = re.findall(r"function (HL_\w+)\(", "\n".join(ir.logic_functions))
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(sum(n.startswith("HL_Teleport_") for n in names), 2)
        self.assertEqual(sum(n.startswith("HL_Spawn_") for n in names), 2)

    def test_director_settings_never_take_a_crescendo_script(self):
        from hammerless.core.entities import CRESCENDO
        from hammerless.core.gamefiles import collect_crescendos, director_input_script, game_files
        from hammerless.core.logic import LNode, compile_graph
        ir = MapIR()
        ir.settings.name = "hl_test_clash"
        ir.entities.append(Entity(CRESCENDO, (0, 0, 0), (0, 0, 0), {"name": "boss", "stages": "PANIC 1"}, [], "boss"))
        compile_graph([LNode("Boss", "DIRECTOR_SETTINGS", {})], [], ir)
        path = f"scripts/vscripts/{director_input_script('hl_test_clash', 'boss')}.nut"
        self.assertNotIn(path, ir.extra_scripts)
        self.assertEqual(collect_crescendos(ir), [])
        self.assertIn("HAMMERLESS_CRESCENDO boss", game_files(ir)[path])


class TestCollisionLimits(unittest.TestCase):
    def test_too_many_triangles_is_refused_not_wrapped(self):
        from hammerless.core.phywrite import MAX_PIECE_TRIANGLES, _ledge
        pts = [(float(i), 0.0, 0.0) for i in range(3)]
        with self.assertRaises(ValueError):
            _ledge(pts, [(0, 1, 2)] * (MAX_PIECE_TRIANGLES + 1))


if __name__ == "__main__":
    unittest.main()


class TestCompileCancel(unittest.TestCase):
    """Cancel stops the running compiler at once, runs nothing after it and copies nothing to the game."""

    def _job(self, folder, steps):
        from hammerless.core import compile as cc
        vmf = os.path.join(folder, "hl_test_cancel.vmf")
        with open(vmf, "w") as f:
            f.write("world\n{\n}\n")
        job = cc.CompileJob(cc.Tools(os.path.join(folder, "game")), vmf, "FAST", copy_to_game=True)
        job.steps = steps
        return job

    def test_cancel_kills_the_compiler_and_skips_the_rest(self):
        import tempfile
        import time
        slow = [sys.executable, "-c", "import time; time.sleep(30)"]
        marker = None
        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, "second_step_ran")
            job = self._job(folder, [("vbsp", slow),
                                     ("vvis", [sys.executable, "-c", f"open({marker!r}, 'w')"])])
            job.start()
            t0 = time.time()
            while job._proc is None and time.time() - t0 < 10:
                time.sleep(0.05)
            proc = job._proc
            job.cancel()
            job._thread.join(10)
            self.assertFalse(job._thread.is_alive())
            self.assertIsNotNone(proc.poll())                  # the compiler was killed, not waited for
            self.assertLess(time.time() - t0, 10)
            job.poll()
            self.assertTrue(job.done and job.failed and job.cancelled)
            self.assertFalse(os.path.exists(marker))
            self.assertFalse(os.path.exists(os.path.join(folder, "game", "left4dead2", "maps")))
            self.assertFalse(os.path.exists(os.path.join(folder, "hl_test_cancel.stamp")))

    def test_a_job_that_isnt_cancelled_isnt_marked_cancelled(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            job = self._job(folder, [("vbsp", [sys.executable, "-c", "raise SystemExit(3)"])])
            job.start().wait()
            self.assertTrue(job.failed)
            self.assertFalse(job.cancelled)


class TestCheatWarning(unittest.TestCase):
    """Logic that only works with cheats on is reported once per graph, naming its nodes."""

    def setUp(self):
        from hammerless.core import cheats
        self._saved = cheats._NAMES
        cheats._NAMES = {"sv_cheats", "host_timescale", "god"}

    def tearDown(self):
        from hammerless.core import cheats
        cheats._NAMES = self._saved

    def test_graph_with_cheats(self):
        from hammerless.core.logic import LNode, compile_graph
        nodes = [LNode("on", "SCRIPT_CALL", {"fn": "Convars.SetValue"}, consts={"p0": "sv_cheats"}),
                 LNode("slow", "SCRIPT_CALL", {"fn": "Convars.SetValue"}, consts={"p0": "host_timescale"}),
                 LNode("fine", "SCRIPT_CALL", {"fn": "Convars.SetValue"}, consts={"p0": "z_common_limit"}),
                 LNode("cmd", "SCRIPT_CALL", {"fn": "SendToServerConsole"}, consts={"p0": "say hi; god 1"}),
                 LNode("code", "SCRIPT_CODE", {"code": 'Convars.SetValue("host_timescale", 0.5);'})]
        warnings = [p for p in compile_graph(nodes, [], MapIR()) if p.startswith("Needs cheats")]
        self.assertEqual(len(warnings), 1, warnings)
        w = warnings[0]
        for nid in ("'on'", "'slow'", "'cmd'", "'code'"):
            self.assertIn(nid, w)
        self.assertNotIn("'fine'", w)
        self.assertIn("turns cheats on", w)
        self.assertIn("host_timescale", w)
        self.assertIn("god", w)

    def test_graph_without_cheats(self):
        from hammerless.core.logic import LNode, compile_graph
        nodes = [LNode("fine", "SCRIPT_CALL", {"fn": "Convars.SetValue"}, consts={"p0": "z_common_limit"}),
                 LNode("say", "SCRIPT_CALL", {"fn": "SendToServerConsole"}, consts={"p0": "say god mode"})]
        self.assertFalse([p for p in compile_graph(nodes, [], MapIR()) if p.startswith("Needs cheats")])

    def test_only_cheats_used_is_a_setting(self):
        from hammerless.core import cheats
        w = cheats.warning("x", ["host_timescale"])
        self.assertIn("only work with cheats on", w)


class TestBuildTimeline(unittest.TestCase):
    def test_chart_and_waited_on(self):
        from hammerless.core.timeline import Timeline
        t = Timeline(100.0)
        t.add("export", 100.0, 102.0)
        t.add("map compiler", 102.0, 103.0)
        t.add("nav mesh", 102.1, 102.5)              # (alongside: not waited on)
        t.add("vis", 103.0, 107.0)
        t.add("game load", 107.2, 115.0)
        lines = t.chart()
        self.assertIn("15.0 s from pressing Build", lines[0])
        self.assertEqual(t.critical_path(), ["export", "map compiler", "vis", "game load"])
        self.assertEqual(Timeline().chart(), [])


@unittest.skipUnless(sys.platform == "win32", "Windows job objects")
class TestChildKill(unittest.TestCase):
    """core/childkill.py: a process tied to Blender dies with it (closed or crashed); one not tied (the game) lives."""

    def test_tied_child_dies_with_parent(self):
        import subprocess
        import time
        dn = subprocess.DEVNULL
        parent = ("import os, subprocess, sys, time; sys.path.insert(0, sys.argv[1]);"
                  "from hammerless.core.childkill import adopt;"
                  "c = ['ping', '-n', '60', '127.0.0.1'];"
                  "t = subprocess.Popen(c, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL);"
                  "f = subprocess.Popen(c, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL);"
                  "print(t.pid, f.pid, adopt(t), flush=True); time.sleep(0.5); os._exit(1)")
        root = os.path.join(os.path.dirname(__file__), "..", "..")
        out = subprocess.run([sys.executable, "-c", parent, root], stdout=subprocess.PIPE, stdin=dn, text=True).stdout
        tied, free, ok = out.split()
        time.sleep(1.5)

        def alive(pid):
            return f" {pid} " in subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                                                text=True).stdout
        try:
            self.assertEqual(ok, "True")
            self.assertFalse(alive(tied))
            self.assertTrue(alive(free))
        finally:
            subprocess.run(["taskkill", "/PID", free, "/F"], capture_output=True)
