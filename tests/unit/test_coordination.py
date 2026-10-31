import os
import json
from pathlib import Path
import sys
import unittest
if os.environ.get('SQUIRREL_PYTHONPATH'): sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
try: import squirrel
except ImportError: squirrel=None
from hammerless.core.coordination import HELPERS
from tests.unit.test_worldqueries import compile_nodes,N,L

class CoordinationCompilerTests(unittest.TestCase):
    def test_signal_capture(self):
        errors,script=compile_nodes([N('start','MAP_START'),N('pub','CO_SIGNAL_PUBLISH',consts={'channel':'bell','ttl':10}),N('read','CO_SIGNAL_READ',consts={'channel':'bell'})],[L('start','start','pub','run'),L('pub','next','read','run')])
        self.assertFalse(errors,errors)
        self.assertEqual(script.count('HLCO.Publish('),1)
        self.assertIn('::HL_R["read"] <- HLCO.Read',script)
    def test_synchronous_signal_snapshot_order(self):
        nodes=[N('find','BA_FIND'),N('loop','BA_LOOP'),N('read','CO_SIGNAL_READ',consts={'channel':'alarm'}),N('publish','CO_SIGNAL_PUBLISH',consts={'channel':'copy','ttl':2})]
        links=[L('find','bots','loop','bots',True),L('loop','each','read','run'),L('read','next','publish','run'),L('read','position','publish','position',True)]
        errors,script=compile_nodes(nodes,links);self.assertFalse(errors,errors)
        self.assertIn('local HL_Q_read = HLCO.Read',script)
        links[1]=L('loop','each','publish','run')
        errors,_=compile_nodes(nodes,links);self.assertTrue(any('earlier' in str(e) for e in errors),errors)
    def test_claim_lifecycle_and_stop_prohibition(self):
        def compile_claim(event):
            definition={'nodes':[N('entry','BA_ENTRY'),N('claim','BA_CLAIM',consts={'key':'spot','ttl':5})], 'links':[L('entry',event,'claim','run')], 'inputs':[],'supports':'COMMONS','channels':[]}
            return compile_nodes([N('find','BA_FIND',{'kind':'COMMONS'}),N('loop','BA_LOOP'),N('apply','BA_APPLY'),N('action','BA_ACTION',{'definition':definition})],[L('find','bots','loop','bots',True),L('loop','each','apply','run'),L('loop','current','apply','bot',True),L('action','action','apply','action',True)])
        errors,script=compile_claim('update');self.assertFalse(errors,errors);self.assertIn('HLCO.Claim(ctx,',script)
        errors,_=compile_claim('stop');self.assertTrue(any('Stop cleanup' in str(e) for e in errors),errors)
        errors,_=compile_nodes([N('claim','BA_CLAIM')],[]);self.assertTrue(errors)

@unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'optional squirrel VM unavailable')
class CoordinationRuntimeTests(unittest.TestCase):
    def test_runtime(self):
        squirrel.SQVM().execute('::now <- 0.0; function Time(){return now;} function Vector(x,y,z){return {x=x,y=y,z=z};}\n'+HELPERS+Path(__file__).with_suffix('.nut').read_text())
    def test_map_reset_and_scope(self):
        squirrel.SQVM().execute('::now <- 0; function Time(){return now;} function Vector(x,y,z){return {x=x,y=y,z=z};}'+HELPERS+
            'HLCO.Publish("old",Vector(0,0,0),10); HLCO.Claim({},"old",10);'+
            'local entityScope={}; compilestring('+json.dumps(HELPERS)+').bindenv(entityScope)();'+
            'assert(HLCO.signals.len()==0 && HLCO.claims.len()==0 && HLCO.revision==0);')
    def test_release_invalid_and_composite(self):
        runtime=Path('hammerless/core/bot_actions_runtime.nut').read_text()
        squirrel.SQVM().execute('::now <- 0.0; function Time(){return now;}\n'+HELPERS+runtime+'''
        local parent={bot=null,overrides=[]}; local child={bot=null,overrides=[]};
        parent.child <- {ctx=child,def={},released=false};
        assert(HLCO.Claim(parent,"parent",10).acquired);
        assert(HLCO.Claim(child,"child",10).acquired);
        HLBA.Release({ctx=parent,def={},released=false});
        assert(HLCO.claims.len()==0);
        local bot={IsValid=function(){return true;},GetHealth=function(){return 100;}};
        local active={bot=bot,overrides=[]};
        HLCO.Claim(active,"before",10);
        HLBA.Release({ctx=active,def={stopFn=function(ctx){HLCO.Claim(ctx,"during",10);}},released=false});
        assert(HLCO.claims.len()==0);
        ''')
if __name__=='__main__': unittest.main()
