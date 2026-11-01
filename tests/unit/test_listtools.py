import os
from pathlib import Path
import sys
import unittest
from hammerless.core import listtools
from hammerless.core.logic import LNode
from tests.unit.test_worldqueries import compile_nodes, N, L

if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0, os.environ['SQUIRREL_PYTHONPATH'])
try:
    import squirrel
except ImportError:
    squirrel = None


class ListCompilerTests(unittest.TestCase):
    def test_set_operations_in_both_compilers(self):
        for kind in listtools.SET_KINDS:
            for action in (False, True):
                nodes = [N('set', kind, {'list_type': 'bots'}, consts={'limit': 128}),
                         N('count', 'LIST_COUNT', {'list_type': 'bots'})]
                links = [L('set', 'next', 'count', 'run'), L('set', 'items', 'count', 'items', True)]
                if action:
                    nodes += [N('find', 'BA_FIND'), N('loop', 'BA_LOOP')]
                    links += [L('find', 'bots', 'loop', 'bots', True),
                              L('loop', 'each', 'set', 'run'), L('find', 'bots', 'set', 'items', True)]
                else:
                    nodes += [N('start', 'MAP_START')]
                    links += [L('start', 'start', 'set', 'run')]
                errors, script = compile_nodes(nodes, links)
                self.assertFalse(errors, errors)
                self.assertEqual(script.count('HLLT.SetQuery('), 1)
                self.assertIn('HL_Q_set' if action else '::HL_R["set"]', script)

    def test_calls_and_defaults(self):
        class Compiler:
            def helper(self, name, body):
                self.name, self.body = name, body
            def typed_in(self, node, name, kind):
                return name
        compiler = Compiler()
        for kind in listtools.KINDS:
            text = listtools.call(compiler, LNode('list', kind, {'list_type': 'navareas'}))
            if kind in listtools.SET_KINDS:
                self.assertIn('HLLT.SetQuery(items, ', text)
                self.assertIn('"navareas"', text)
                self.assertIn('[]' if kind == 'LIST_UNIQUE' else 'other', text)
            else:
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
