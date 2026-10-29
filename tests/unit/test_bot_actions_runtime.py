"""Execute ownership/lifecycle cases in a real Squirrel VM.

Requires optional squirrel-lang (or SQUIRREL_PYTHONPATH pointing at its install).
No game process or game files are touched.
"""
import os
from pathlib import Path
import sys
import unittest

if os.environ.get("SQUIRREL_PYTHONPATH"):
    sys.path.insert(0, os.environ["SQUIRREL_PYTHONPATH"])
try:
    import squirrel
except ImportError:
    squirrel = None

ROOT = Path(__file__).resolve().parents[2]

@unittest.skipUnless(squirrel is not None and hasattr(squirrel, "SQVM"), "optional squirrel-lang interpreter not installed")
class BotActionsRuntimeTests(unittest.TestCase):
    def test_lifecycle_and_ownership(self):
        vm = squirrel.SQVM()
        runtime = (ROOT / "hammerless/core/bot_actions_runtime.nut").read_text()
        cases = Path(__file__).with_suffix(".nut").read_text()
        vm.execute(runtime + "\n" + cases)

if __name__ == "__main__":
    unittest.main()
