import os
import sys
import json
from pathlib import Path
import unittest
if os.environ.get('SQUIRREL_PYTHONPATH'): sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
try: import squirrel
except ImportError: squirrel=None
from tests.unit.test_candidates import rule
from tests.unit.test_worldqueries import compile_nodes,N,L

ROOT=Path(__file__).resolve().parents[2]

def compile_step(event='update'):
    definition={'nodes':[N('entry','BA_ENTRY'),rule(),N('step','CAND_STEP',{'list_type':'bots'},
                consts={'enabled':True,'reset':False,'budget':8,'limit':32})],
                'links':[L('entry',event,'step','run'),L('rule','rule','step','rule',True)],
                'inputs':[],'supports':'COMMONS','channels':[]}
    return compile_nodes([N('find','BA_FIND',{'kind':'COMMONS'}),N('loop','BA_LOOP'),N('apply','BA_APPLY'),N('action','BA_ACTION',{'definition':definition})],
        [L('find','bots','loop','bots',True),L('loop','each','apply','run'),L('loop','current','apply','bot',True),L('action','action','apply','action',True)])

class CandidateStepsCompilerTests(unittest.TestCase):
    def test_lazy_factory_and_lifecycle_scope(self):
        errors,script=compile_step()
        self.assertFalse(errors,errors)
        self.assertIn('HLCE.Step(ctx,',script)
        self.assertIn('function() { return {items=',script)
        if squirrel is not None and hasattr(squirrel,'SQVM'):
            squirrel.SQVM().execute('compilestring('+json.dumps(script)+');')
        errors,_=compile_step('stop')
        self.assertTrue(any('Stop' in str(e) for e in errors),errors)
        errors,_=compile_nodes([N('start','MAP_START'),rule(),N('step','CAND_STEP',{'list_type':'bots'})],
            [L('start','start','step','run'),L('rule','rule','step','rule',True)])
        self.assertTrue(errors)
        errors,_=compile_nodes([N('find','BA_FIND'),N('loop','BA_LOOP'),rule(),N('step','CAND_STEP',{'list_type':'bots'})],
            [L('find','bots','loop','bots',True),L('loop','each','step','run'),L('rule','rule','step','rule',True)])
        self.assertTrue(any('lifecycle' in str(e) for e in errors),errors)

@unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'optional squirrel VM unavailable')
class CandidateStepsRuntimeTests(unittest.TestCase):
    def test_actual_runtime(self):
        prelude=Path(__file__).with_name('test_listtools.nut').read_text().split('function RunTests()')[0]
        prelude=prelude.replace('class Vec {','class Vec {\n    function _typeof(){return "Vector";}')
        runtime='\n'.join((ROOT/'hammerless/core'/name).read_text() for name in
            ('list_tools_runtime.nut','candidate_runtime.nut','bot_actions_runtime.nut'))
        squirrel.SQVM().execute(prelude+'\n::now<-0.0; function Time(){return now;} function printl(s){}\n'+runtime+'\n'+Path(__file__).with_suffix('.nut').read_text())

if __name__=='__main__': unittest.main()
