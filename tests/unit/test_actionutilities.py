"""Per-bot utility behavior in a real Squirrel VM, without a game process."""
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

from hammerless.core.actionutilities import HELPERS


@unittest.skipUnless(squirrel is not None and hasattr(squirrel, "SQVM"), "optional squirrel-lang interpreter not installed")
class ActionUtilitiesTests(unittest.TestCase):
    def test_runtime(self):
        squirrel.SQVM().execute(HELPERS + Path(__file__).with_suffix(".nut").read_text())


if __name__ == "__main__":
    unittest.main()
