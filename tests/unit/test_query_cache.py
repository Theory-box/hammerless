import os
from pathlib import Path
import sys
import unittest
if os.environ.get('SQUIRREL_PYTHONPATH'):sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
try:import squirrel
except ImportError:squirrel=None
from hammerless.core import querycache
from hammerless.core.logic import _Compiler,LNode
from hammerless.core.ir import MapIR
from tests.unit.test_worldqueries import compile_nodes,N,L
ROOT=Path(__file__).resolve().parents[2]

class QueryCacheCompilerTests(unittest.TestCase):
    def test_helpers_and_round_resets_once(self):
        c=_Compiler(MapIR(),[])
        node=LNode('cache','QC_AMBIENT',consts={'max_age':0.25,'refresh':False})
        self.assertIn('HLQC.ReadAmbient',querycache.call(c,node))
        querycache.call(c,node)
        self.assertTrue(c.ir._world_query_ambient)
        for event in ('round_start','round_end','mission_lost','map_transition'):
            callbacks=c.ir.logic_script_events[event]
            self.assertEqual(sum('HLQC.Clear()' in body for body in callbacks),1)
        descriptor=querycache.definition(c,LNode('pool','QC_POOL',{'capacity':64}))
        self.assertIn('capacity=64',descriptor)
        with self.assertRaises(ValueError):querycache.definition(c,LNode('bad','QC_POOL',{'capacity':0}))

    def test_reset_callbacks_survive_generated_script(self):
        errors,script=compile_nodes([N('start','MAP_START'),N('pool','QC_POOL',{'capacity':64}),
            N('query','QC_AMBIENT',consts={'max_age':0.25,'refresh':False})],
            [L('start','start','query','run'),L('pool','pool','query','pool',True)])
        self.assertFalse(errors,errors)
        self.assertEqual(script.count('HLQC.Clear();'),4)
        for event in ('round_start','round_end','mission_lost','map_transition'):
            self.assertIn('function OnGameEvent_'+event+'(',script)
        if squirrel is not None and hasattr(squirrel,'SQVM'):
            vm=squirrel.SQVM()
            vm.execute('function Vector(x,y,z){return {x=x,y=y,z=z};}\n'+script)
            for event in ('round_start','round_end','mission_lost','map_transition'):
                vm.execute('HLQC.pools.test <- {entries=[]}; HLQC.count=7; OnGameEvent_'+event+'({}); assert(HLQC.count==0 && HLQC.pools.len()==0);')

@unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'optional squirrel VM unavailable')
class QueryCacheRuntimeTests(unittest.TestCase):
    def test_cache_with_actual_queries(self):
        runtime='\n'.join((ROOT/'hammerless/core'/name).read_text(encoding='utf-8') for name in
            ('world_queries_runtime.nut','ambient_query_runtime.nut','query_cache_runtime.nut'))
        squirrel.SQVM().execute(runtime+'\n'+Path(__file__).with_suffix('.nut').read_text(encoding='utf-8'))

if __name__=='__main__':unittest.main()
