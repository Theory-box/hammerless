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
