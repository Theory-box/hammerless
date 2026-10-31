import os
from pathlib import Path
import sys
import unittest
from hammerless.core import listtools
from hammerless.core.logic import LNode

if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0, os.environ['SQUIRREL_PYTHONPATH'])
try:
    import squirrel
except ImportError:
    squirrel = None


class ListCompilerTests(unittest.TestCase):
    def test_calls_and_defaults(self):
        class Compiler:
            def helper(self, name, body):
                self.name, self.body = name, body
            def typed_in(self, node, name, kind):
                return name
        compiler = Compiler()
        for kind in listtools.KINDS:
            text = listtools.call(compiler, LNode('list', kind, {'list_type': 'navareas'}))
            self.assertIn('HLLT.Query(items, "navareas"', text)
            self.assertEqual(compiler.name, 'HLLT')
        with self.assertRaises(ValueError):
            listtools.call(compiler, LNode('list', 'LIST_COUNT', {'list_type': 'things'}))
        self.assertIn('false', listtools.stored_output(None, 'found', 'list'))
        with self.assertRaises(ValueError):
            listtools.output(None, 'nonsense', 'r')


@unittest.skipUnless(squirrel is not None and hasattr(squirrel, 'SQVM'), 'squirrel-lang unavailable')
class ListRuntimeTests(unittest.TestCase):
    def test_runtime(self):
        vm = squirrel.SQVM()
        root = Path(__file__).resolve().parents[2]
        vm.execute(Path(__file__).with_suffix('.nut').read_text() + '\n' +
                   (root / 'hammerless/core/list_tools_runtime.nut').read_text() + '\nRunTests();')
