import os
import json
import sys
import unittest
from pathlib import Path
if os.environ.get('SQUIRREL_PYTHONPATH'): sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
try: import squirrel
except ImportError: squirrel=None
from hammerless.core.diagnostics import HELPERS, call
from tests.unit.test_worldqueries import compile_nodes,N,L

class DiagnosticsCompilerTests(unittest.TestCase):
    def test_ordinary_capture_and_check_branches(self):
        nodes=[N('start','MAP_START'),N('check','DBG_CHECK',consts={'condition':True,'enabled':False,'label':'test','interval':1}),
               N('watch','DBG_WATCH',{'type':'bool'},consts={'enabled':True,'label':'pass','interval':1})]
        links=[L('start','start','check','run'),L('check','true','watch','run'),L('check','passed','watch','value',True)]
        errors,script=compile_nodes(nodes,links)
        self.assertFalse(errors,errors)
        self.assertIn('if (::HL_R["check"].passed)',script)
        self.assertIn('HLDBG.Watch(null,',script)
        self.assertIn(': HLDBG.Disabled()',script)
        if squirrel is not None and hasattr(squirrel,'SQVM'):
            squirrel.SQVM().execute('compilestring('+json.dumps(script)+');')

    def test_action_snapshot_and_scope(self):
        definition={'nodes':[N('entry','BA_ENTRY'),N('inspect','BA_INSPECT'),N('watch','DBG_WATCH',{'type':'text'},consts={'enabled':True,'label':'state','interval':1})],
                    'links':[L('entry','update','inspect','run'),L('inspect','next','watch','run'),L('inspect','status','watch','value',True)],
                    'inputs':[],'supports':'COMMONS','channels':[]}
        errors,script=compile_nodes([N('find','BA_FIND',{'kind':'COMMONS'}),N('loop','BA_LOOP'),N('apply','BA_APPLY'),N('action','BA_ACTION',{'definition':definition})],
            [L('find','bots','loop','bots',True),L('loop','each','apply','run'),L('loop','current','apply','bot',True),L('action','action','apply','action',True)])
        self.assertFalse(errors,errors)
        self.assertIn('HLDBG.Inspect(ctx)',script)
        self.assertIn('HLDBG.Watch(ctx,',script)
        if squirrel is not None and hasattr(squirrel,'SQVM'):
            squirrel.SQVM().execute('compilestring('+json.dumps(script)+');')
        errors,_=compile_nodes([N('inspect','BA_INSPECT')],[])
        self.assertTrue(errors)

    def test_bot_loop_check_and_snapshot_order(self):
        nodes=[N('find','BA_FIND'),N('loop','BA_LOOP'),N('check','DBG_CHECK',consts={'condition':True}),
               N('watch','DBG_WATCH',{'type':'bool'})]
        links=[L('find','bots','loop','bots',True),L('loop','each','check','run'),
               L('check','true','watch','run'),L('check','passed','watch','value',True)]
        errors,script=compile_nodes(nodes,links)
        self.assertFalse(errors,errors)
        self.assertIn('if (HL_Q_check.passed)',script)
        self.assertIn('HLDBG.Watch(ctx,',script)
        links[1]=L('loop','each','watch','run')
        errors,_=compile_nodes(nodes,links)
        self.assertTrue(any('earlier' in str(e) for e in errors),errors)

@unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'optional squirrel VM unavailable')
class DiagnosticsRuntimeTests(unittest.TestCase):
    def test_runtime(self):
        squirrel.SQVM().execute('::now<-0.0; ::logs<-[]; function Time(){return now;} function printl(s){logs.append(s);}\n'+HELPERS+Path(__file__).with_suffix('.nut').read_text())

    def test_disabled_expression_is_lazy(self):
        class Compiler:
            def helper(self,*a): pass
            def typed_in(self,n,k,t): return {'enabled':'false','value':'explode()','label':'explode()','interval':'explode()'}[k]
        expression=call(Compiler(),N('watch','DBG_WATCH',{'type':'num'}))
        squirrel.SQVM().execute(HELPERS+'function explode(){throw "evaluated";} local result='+expression+'; assert(result.reason=="disabled"); assert(HLDBG.nodes.len()==0);')

    def test_check_only_evaluates_diagnostics_for_enabled_failure(self):
        class Compiler:
            condition='false'
            def helper(self,*a): pass
            def typed_in(self,n,k,t): return {'condition':self.condition,'enabled':'false','label':'explode()','interval':'explode()'}[k]
        compiler=Compiler()
        for condition in ('false','true'):
            compiler.condition=condition
            expression=call(compiler,N('check','DBG_CHECK'))
            squirrel.SQVM().execute(HELPERS+'function explode(){throw "evaluated";} local result='+expression+'; assert(result.passed=='+condition+'); assert(HLDBG.nodes.len()==0);')

    def test_release_cleans_state_even_invalid_bot(self):
        runtime=Path('hammerless/core/bot_actions_runtime.nut').read_text()
        squirrel.SQVM().execute(runtime+'local ctx={bot=null,_diagnostics={old=1},overrides=[]}; HLBA.Release({ctx=ctx,def={},released=false}); assert(!("_diagnostics" in ctx));')

if __name__=='__main__': unittest.main()
