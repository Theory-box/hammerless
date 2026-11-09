"""Exercise test-process cleanup without starting Blender."""
import ctypes
from ctypes import wintypes
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

RUNNER = Path(__file__).with_name('run_isolated.py')
spec = importlib.util.spec_from_file_location('isolated_runner', RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

@unittest.skipUnless(os.name == 'nt', 'Windows job ownership')
class CleanupTests(unittest.TestCase):
    def test_normal_timeout_and_unrelated(self):
        with tempfile.TemporaryDirectory() as folder:
            other = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                normal = runner.run_process([sys.executable, '-c', 'pass'], folder, Path(folder)/'normal.log', 5)
                hung = runner.run_process([sys.executable, '-c', 'import time;time.sleep(30)'], folder, Path(folder)/'hung.log', .3)
                self.assertEqual(normal['returncode'], 0)
                self.assertTrue(normal['exited'])
                self.assertTrue(hung['timed_out'] and hung['exited'])
                self.assertLess(hung['elapsed_seconds'], 5)
                self.assertIsNone(other.poll())
            finally:
                other.kill(); other.wait()

    def test_descendant_exits_after_timeout(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'child.pid'
            code = ('import subprocess,sys,pathlib,time; '
                    'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]); '
                    f'pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)')
            result = runner.run_process([sys.executable, '-c', code], folder, Path(folder)/'tree.log', 1)
            self.assertTrue(result['timed_out'])
            self.assertTrue(marker.exists())
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = api.OpenProcess(0x100000, False, int(marker.read_text()))
            if handle:
                try:
                    self.assertEqual(api.WaitForSingleObject(handle, 3000), 0)
                finally:
                    api.CloseHandle(handle)

if __name__ == '__main__':
    unittest.main()
