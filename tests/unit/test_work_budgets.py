"""Real Squirrel fairness, receipt safety and shared candidate-cap regressions."""
import os
from pathlib import Path
import sys
import unittest
if os.environ.get('SQUIRREL_PYTHONPATH'):sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
try:import squirrel
except ImportError:squirrel=None
ROOT=Path(__file__).resolve().parents[2]

@unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'optional squirrel VM unavailable')
class WorkBudgetTests(unittest.TestCase):
    def test_runtime_and_candidate_cap(self):
        runtime='\n'.join((ROOT/'hammerless/core'/name).read_text(encoding='utf-8') for name in
            ('list_tools_runtime.nut','candidate_runtime.nut','bot_actions_runtime.nut','work_budget_runtime.nut'))
        squirrel.SQVM().execute(runtime+'\n'+Path(__file__).with_suffix('.nut').read_text(encoding='utf-8'))

if __name__=='__main__':unittest.main()
