"""Differential lifecycle coverage against the pre-index conflict algorithm.

The legacy fixture is the conflict block from c83724d. Only that block is
substituted in memory; all request, ownership and lifecycle code remains actual
production code. No Git executable or repository history is needed at runtime.
"""
import os
from pathlib import Path
import sys
import unittest

if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
try: import squirrel
except ImportError: squirrel=None

ROOT=Path(__file__).resolve().parents[2]
START='        // Build a global desired set, including still-active non-due loops.'
END='        // Release every removed action before any newly requested action starts.'
LEGACY='''        // Build a global desired set, including still-active non-due loops.
        local desired=[];
        foreach (loop in snapshot) {
            if (loop.stopped) continue;
            local isDue=due.find(loop) != null;
            foreach (r in (isDue ? loop.requests : loop.active)) desired.append({loop=loop,r=r,due=isDue});
        }
        for (local i=0;i<desired.len();++i) for(local j=i+1;j<desired.len();++j) {
            local a=desired[i],b=desired[j];
            if (Clashes(a.r,b.r)) {
                if (a.due) a.r.rejected=true;
                if (b.due) b.r.rejected=true;
                Report("Conflicting bot actions: " + a.r.def.id + " / " + b.r.def.id);
            }
        }
'''


@unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'optional squirrel-lang interpreter unavailable')
class ActionConflictIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current=(ROOT/'hammerless/core/bot_actions_runtime.nut').read_text(encoding='utf-8')
        if cls.current.count(START)!=1 or cls.current.count(END)!=1:
            raise AssertionError('Conflict block markers changed; review the differential fixture boundary')
        start,end=cls.current.index(START),cls.current.index(END)
        cls.legacy=cls.current[:start]+LEGACY+cls.current[end:]
        cls.cases=Path(__file__).with_suffix('.nut').read_text(encoding='utf-8')

    def test_mixed_lifecycle_matches_legacy(self):
        current=squirrel.SQVM();legacy=squirrel.SQVM()
        current.execute(self.current+'\n'+self.cases)
        legacy.execute(self.legacy+'\n'+self.cases)
        for seed in range(24):
            with self.subTest(seed=seed):
                actual=current.execute(f'return MixedCase({seed});')
                expected=legacy.execute(f'return MixedCase({seed});')
                self.assertEqual(actual,expected)
                self.assertIn('Conflicting bot actions:',actual)
                self.assertIn('start:',actual)

    def test_non_due_owner_retains_effect_until_due_conflict(self):
        for name,runtime in [('indexed',self.current),('legacy',self.legacy)]:
            with self.subTest(runtime=name):
                squirrel.SQVM().execute(runtime+'\n'+self.cases+'\nNonDueOwnerCase();')

    def test_distinct_bot_actions_skip_conflict_comparisons(self):
        squirrel.SQVM().execute(self.current+'\n'+self.cases+'\nDistinctBotsCase();')

if __name__=='__main__': unittest.main()
