"""A compiler error that says where it is becomes a problem with that place (the Problems list goes to it)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hammerless.core.compile import parse_log

LOG = """==== hlvbsp
Processing areas...done (0)
Error: Overlay touching too many faces (touching 65, max 64)
Overlay decals/debris_concrete001a at 1234.0 -512.5 96.0
!! hlvbsp failed (exit code 1)
"""


class ErrorPlace(unittest.TestCase):
    def test_place_on_the_next_line(self):
        s = parse_log(LOG)
        self.assertEqual(len(s.problems), 1)
        p = s.problems[0]
        self.assertEqual(p.location, (1234.0, -512.5, 96.0))
        self.assertIn("touching too many faces", p.message)
        self.assertIn("decals/debris_concrete001a", p.message)
        self.assertIn(p.message, s.errors)            # (the build matches a problem to its error by text)

    def test_place_on_the_error_line(self):
        s = parse_log("Error: entity prop_static at origin (10, 20, -30.5) is broken\n")
        self.assertEqual(s.problems[0].location, (10.0, 20.0, -30.5))
        s = parse_log("Error: bad thing near [1 2 3]\n")
        self.assertEqual(s.problems[0].location, (1.0, 2.0, 3.0))

    def test_no_place_no_problem(self):
        s = parse_log("Error: something else (touching 65, max 64)\nnext line with no place\n")
        self.assertEqual(s.problems, [])
        self.assertEqual(s.errors, ["Error: something else (touching 65, max 64)"])
        s = parse_log("MAX_MAP_BRUSHSIDES exceeded\nError: in file 12 34 56 of things\n")
        self.assertEqual(s.problems, [])

    def test_next_error_is_not_a_detail_line(self):
        s = parse_log("Error: first\nError: second at 1 2 3\n")
        self.assertEqual([p.message for p in s.problems], ["Error: second at 1 2 3"])
        self.assertEqual(s.errors[0], "Error: first")

    def test_leak_stays_as_it_was(self):
        s = parse_log("**** leaked ****\nEntity info_player_start (0.00 0.00 64.00) leaked!\n")
        self.assertTrue(s.leaked)
        self.assertEqual(s.problems, [])


    def test_patch_name_too_long_says_what_to_do(self):
        s = parse_log("Error: Generated env_cubemap patch name : maps/nh1_hoods_survival_hammerless_0/maps/"
                      "nh1_hoods_survival_hammerless_0/street/ma_floor_concrete_sidewalk_a_blend_a_wet_wvt_patch"
                      "_-784_-101_44 too long! (max = 128)")
        self.assertEqual(len(s.errors), 1)
        self.assertIn("Map Name is too long", s.errors[0])
        self.assertIn("'street/ma_floor_concrete_sidewalk_a_blend_a_wet'", s.errors[0])
        self.assertIn("at least 9 character", s.errors[0])


    def test_gpu_failure_says_what_to_do(self):
        s = parse_log("Error: GPU: vkQueueWaitIdle(queue) failed (-4)\n")
        self.assertEqual(len(s.errors), 1)
        self.assertIn("stopped answering", s.errors[0])
        self.assertIn("vkQueueWaitIdle(queue) failed (-4)", s.errors[0])
        self.assertIn("Exact Lighting", s.errors[0])
        s = parse_log("Error: GPU: vkAllocateMemory(dev, &ai, NULL, &b.mem) failed (-2): out of memory\n")
        self.assertIn("couldn't finish", s.errors[0])


if __name__ == "__main__":
    unittest.main()
