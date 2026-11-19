"""compile._process_running on a Windows whose tasklist answers in bytes Python can't decode (subprocess hands back
None for text then: a non-English Windows stopped Build & Play with "'NoneType' object has no attribute 'lower'")."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hammerless.core import compile as cc


class ProcessRunningTests(unittest.TestCase):
    def run_with(self, stdout):
        with mock.patch.object(cc.subprocess, "run", return_value=SimpleNamespace(stdout=stdout)) as run:
            result = cc._process_running("left4dead2.exe")
        self.assertNotIn("text", run.call_args.kwargs)      # (bytes: no decoding left to subprocess)
        return result

    def test_no_output(self):
        self.assertFalse(self.run_with(None))
        self.assertFalse(self.run_with(b""))

    def test_other_language(self):
        self.assertFalse(self.run_with(b"INFORMACJA: brak uruchomionych zada\x81 spe\x88niaj\xa5cych kryteria.\r\n"))

    def test_running(self):
        self.assertTrue(self.run_with(b"\x81 Left4Dead2.exe   1234 Console  1   900\xff000 K\r\n"))

    def test_tasklist_missing(self):
        with mock.patch.object(cc.subprocess, "run", side_effect=OSError("no tasklist")):
            self.assertFalse(cc._process_running("steam.exe"))


if __name__ == "__main__":
    unittest.main()
