import os
from pathlib import Path
import sys
import unittest
from hammerless.core import candidates
from hammerless.core.logic import LNode as N, LLink as L, _Compiler, compile_graph
from hammerless.core.ir import MapIR

if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
try:
    import squirrel
except ImportError:
    squirrel=None


def rule():
    return N('rule','CAND_RULE',{'definition':{
        'list_type':'bots','nodes':[N('yes','COMPARE',consts={'a':1,'b':0}),
          N('index','CAND_PARAM',{'uid':'index'}),N('score','MATH',{'op':'MULTIPLY'},consts={'b':-1})],
        'links':[L('index','value','score','a',True)],
        'inputs':[('candidate','bot'),('position','vec'),('index','num')],
        'outputs':{'keep':('yes','result'),'score':('score','value')}}})


class CandidateTests(unittest.TestCase):
    def test_find_compiles_in_both_execution_contexts(self):
        from hammerless.core.botactions import ActionCompiler
        for typ in ('bots', 'navareas'):
            nodes = [N('start', 'MAP_START'), N('find', 'CAND_FIND', {'list_type':typ}),
                     N('save', 'SET_VAR', {'name':'rank','kind':'num'})]
            links = [L('start','start','find','run'), L('find','next','save','run'),
                     L('find','rank','save','value',True)]
            ir=MapIR(); self.assertEqual(compile_graph(nodes,links,ir),[])
            self.assertIn('HLCE.Find', '\n'.join(ir.extra_scripts.values()))
            nodes[-1]=N('save','BA_STATE_SET',{'name':'rank','type':'num'})
            c=_Compiler(MapIR(),[]); ac=ActionCompiler(c,nodes,links)
            with ac.scope({n.id:n for n in nodes},links,lifecycle=True):
                body=ac.chain('start','start')
            self.assertIn('HLCE.Find',body)
            self.assertIn('HL_Q_find.rank',body)

    def test_rule_bounds_cycles_side_effects_and_types(self):
        for change,match in [
            (lambda d:d.update(list_type='navareas'),'type must match'),
            (lambda d:d['nodes'].append(N('bad','BA_COMMAND')),'stateless'),
            (lambda d:d['links'].append(L('score','value','score','b',True)),'cycle'),
            (lambda d:d.update(outputs={}),'Keep and Score'),
            (lambda d:d['nodes'].extend(N(str(i),'VALUE') for i in range(130)),'128 nodes')]:
            n=rule(); change(n.settings['definition'])
            with self.assertRaisesRegex(ValueError,match): candidates.compile_rule(_Compiler(MapIR(),[]),n,'bots')

    def test_ordinary_and_action_compilers(self):
        from hammerless.core.botactions import ActionCompiler
        n=rule(); evaluate=N('evaluate','CAND_EVALUATE',{'list_type':'bots'},consts={'limit':12})
        nodes=[N('start','MAP_START'),n,evaluate,N('item','CAND_ITEM',{'list_type':'bots'})]
        links=[L('start','start','evaluate','run'),L('rule','rule','evaluate','rule',True),
               L('evaluate','next','item','run'),L('evaluate','results','item','results',True)]
        ir=MapIR(); errors=compile_graph(nodes,links,ir)
        self.assertEqual(errors,[])
        script='\n'.join(ir.extra_scripts.values())
        self.assertIn('HLCE.Evaluate',script); self.assertIn('function HL_CandidateRule',script)
        c=_Compiler(MapIR(),[]); ac=ActionCompiler(c,nodes,links)
        with ac.scope({n.id:n for n in nodes},links,loop='start'):
            body=ac.chain('start','start')
        self.assertIn('local HL_Q_evaluate',body)
        self.assertIn('HLCE.Item(HL_Q_evaluate',body)
        with ac.scope({n.id:n for n in nodes},links,loop='start'):
            with self.assertRaisesRegex(ValueError,'earlier'):
                c.expr_out('evaluate','score')

    def test_ambient_is_once_per_candidate_and_marks_export(self):
        n=rule(); d=n.settings['definition']
        d['nodes']=[N('light','CAND_AMBIENT')]
        d['links']=[]; d['outputs']={'keep':('light','available'),'score':('light','brightness')}
        c=_Compiler(MapIR(),[]); candidates.compile_rule(c,n,'bots')
        text=c.functions[-1]
        self.assertEqual(text.count('HLWQ_Ambient('),1)
        self.assertTrue(c.ir._world_query_ambient)

    def test_action_parameters_capture_outer_context_only(self):
        from hammerless.core.botactions import ActionCompiler
        n=rule(); d=n.settings['definition']
        d['inputs'].append(('bias','num'))
        d['nodes'].append(N('bias','CAND_PARAM',{'uid':'bias'}))
        d['links'].append(L('bias','value','score','b',True))
        state=N('state','BA_STATE_GET',{'name':'bias','type':'num'})
        c=_Compiler(MapIR(),[])
        links=[L('state','value','rule','bias',True)]
        ac=ActionCompiler(c,[n,state],links)
        with ac.scope({'rule':n,'state':state},links,lifecycle=True):
            _,params=candidates.compile_rule(c,n,'bots')
        self.assertIn('ctx.state',params)
        self.assertNotIn('ctx.',c.functions[-1])
        self.assertIn('params["bias"]',c.functions[-1])

    @unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'squirrel-lang unavailable')
    def test_lazy_branches_and_full_node_budget(self):
        for op in ('PICK','AND','OR','NOT'):
            n=rule(); d=n.settings['definition']
            d['inputs'].append(('bad','vec'))
            d['nodes']=[N('bad','CAND_PARAM',{'uid':'bad'}),N('break','BREAK_VECTOR'),
                N('compare','COMPARE'),N('yes','COMPARE',consts={'a':1,'b':0}),
                N('safe','VALUE',consts={'value':7})]
            d['links']=[L('bad','value','break','vector',True),L('break','x','compare','a',True)]
            if op=='PICK':
                d['nodes'].append(N('choice','PICK',{'kind':'num'},consts={'condition':True}))
                d['links'] += [L('safe','value','choice','a',True),L('break','x','choice','b',True)]
                d['outputs']={'keep':('yes','result'),'score':('choice','value')}
            else:
                d['nodes'].append(N('choice','BOOL_MATH',{'op':op},consts={'a':op=='OR'}))
                d['links'].append(L('compare','result','choice','b',True))
                d['outputs']={'keep':('choice','result'),'score':('safe','value')}
            c=_Compiler(MapIR(),[]); fn,_=candidates.compile_rule(c,n,'bots')
            vm=squirrel.SQVM()
            vm.execute('\n'.join(c.functions)+f'\nlocal r={fn}(null,null,0,{{bad=null}}); if(r.score!=7) throw "bad safe branch";')
        n=rule();d=n.settings['definition'];d['nodes']=[N('yes','COMPARE',consts={'a':1,'b':0})]
        d['nodes'] += [N('leaf'+str(i),'VALUE',consts={'value':1}) for i in range(64)]
        d['links']=[]; layer=['leaf'+str(i) for i in range(64)];i=0
        while len(layer)>1:
            following=[]
            for a,b in zip(layer[::2],layer[1::2]):
                name='sum'+str(i);i+=1
                d['nodes'].append(N(name,'MATH',{'op':'ADD'}))
                d['links'] += [L(a,'value',name,'a',True),L(b,'value',name,'b',True)]
                following.append(name)
            layer=following
        d['outputs']={'keep':('yes','result'),'score':(layer[0],'value')}
        c=_Compiler(MapIR(),[]);fn,_=candidates.compile_rule(c,n,'bots')
        vm=squirrel.SQVM();vm.execute('\n'.join(c.functions)+f'\nif({fn}(null,null,0,{{}}).score!=64) throw "node budget";')

    @unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'squirrel-lang unavailable')
    def test_runtime_and_compiled_rule(self):
        root=Path(__file__).resolve().parents[2]
        prelude=Path(__file__).with_name('test_listtools.nut').read_text().split('function RunTests()')[0]
        c=_Compiler(MapIR(),[]); fn,_=candidates.compile_rule(c,rule(),'bots')
        vm=squirrel.SQVM()
        vm.execute(prelude+'\n'+(root/'hammerless/core/list_tools_runtime.nut').read_text()+'\n'+
                   (root/'hammerless/core/candidate_runtime.nut').read_text()+'\n'+'\n'.join(c.functions)+f'\n::compiledRule <- {fn};\n'+
                   Path(__file__).with_suffix('.nut').read_text())
