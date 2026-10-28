import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from hammerless.core import compile as cc


class DeploymentCancellation(unittest.TestCase):
    def job(self):
        job = cc.CompileJob.__new__(cc.CompileJob)
        job.tools = SimpleNamespace(gamedir='unused')
        job.base = 'unused'
        job.name = 'audit'
        job._q = queue.Queue()
        job._stopping = False
        job._deployed = False
        job._cancel_event = threading.Event()
        job._light = job._proc = None
        return job

    def run_copy(self, job, copy):
        with patch('hammerless.core.stringtables.keep_for_next_build', return_value=None), \
             patch.object(cc.shutil, 'copy2', side_effect=copy), \
             patch.object(cc, 'game_running', return_value=True), \
             patch.object(cc, 'send_commands'), \
             patch.object(cc, 'verify_deployed_bsp'):
            job._copy_bsp('unused')

    def test_cancel_during_locked_destination_wait_does_not_retry(self):
        job = self.job()
        job._cancel_event = Mock()
        job._cancel_event.wait.side_effect = lambda _: job.cancel()
        copy = Mock(side_effect=PermissionError('locked'))
        with self.assertRaises(cc._Cancelled):
            self.run_copy(job, copy)
        self.assertEqual(copy.call_count, 1)
        self.assertFalse(job._deployed)
        job._cancel_event.set.assert_called_once()

    def test_cancel_before_copy_does_not_touch_destination(self):
        job = self.job()
        job.cancel()
        copy = Mock()
        with self.assertRaises(cc._Cancelled):
            self.run_copy(job, copy)
        copy.assert_not_called()

    def test_cancel_inside_copy_records_that_deployment_occurred(self):
        job = self.job()
        with self.assertRaises(cc._Cancelled):
            self.run_copy(job, lambda *_: job.cancel())
        self.assertTrue(job._deployed)

    def test_successful_copy_still_works(self):
        job = self.job()
        self.run_copy(job, lambda *_: None)
        self.assertTrue(job._deployed)


if __name__ == '__main__':
    unittest.main()
