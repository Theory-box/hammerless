import unittest
from tests.unit.test_candidates import rule
from tests.unit.test_worldqueries import compile_nodes, N, L


def fixture(event="update", shared=True):
    nodes=[N('entry','BA_ENTRY'), rule(), N('pool','WB_POOL',{'limit':16}),
           N('grant','WB_REQUEST',consts={'wanted':8,'enabled':True}),
           N('step','CAND_STEP',{'list_type':'bots'},consts={'budget':8,'enabled':True,'limit':32})]
    links=[L('entry',event,'grant','run'),L('grant','next','step','run'),
           L('pool','pool','grant','pool',True),L('rule','rule','step','rule',True)]
    if shared: links.append(L('grant','grant','step','grant',True))
    definition={'nodes':nodes,'links':links,'inputs':[],'supports':'COMMONS','channels':[]}
    return compile_nodes([N('find','BA_FIND',{'kind':'COMMONS'}),N('loop','BA_LOOP'),
                          N('apply','BA_APPLY'),N('action','BA_ACTION',{'definition':definition})],
                         [L('find','bots','loop','bots',True),L('loop','each','apply','run'),
                          L('loop','current','apply','bot',True),L('action','action','apply','action',True)])


class WorkBudgetCompilerTests(unittest.TestCase):
    def test_shared_grant_is_explicit_and_runtime_is_included(self):
        errors, script=fixture()
        self.assertFalse(errors,errors)
        self.assertIn('HLWB.Request(ctx,',script)
        self.assertIn('HLWB.Begin(due,now)',script)
        self.assertIn('.grant, true)',script)
        errors, script=fixture(shared=False)
        self.assertFalse(errors,errors)
        self.assertIn(', null, false)',script)

    def test_lifecycle_and_pool_validation(self):
        errors,_=fixture('stop')
        self.assertTrue(any('Stop' in str(e) for e in errors),errors)
        errors,_=compile_nodes([N('start','MAP_START'),N('grant','WB_REQUEST')],
                               [L('start','start','grant','run')])
        self.assertTrue(errors)
        from hammerless.core import workbudgets
        from hammerless.core.logic import _Compiler
        from hammerless.core.ir import MapIR
        c=_Compiler(MapIR(),[])
        self.assertEqual(workbudgets.definition(c,N('pool','WB_POOL',{'limit':0})),'null')
        self.assertTrue(c.problems)

    def test_pool_identity_is_graph_scoped_and_bounded(self):
        from hammerless.core import workbudgets
        from hammerless.core.logic import _Compiler
        from hammerless.core.ir import MapIR
        ir=MapIR(); a=_Compiler(ir,[],"A"); b=_Compiler(ir,[],"B")
        node=N('nested/'*100,'WB_POOL',{'limit':16})
        first=workbudgets.definition(a,node)
        self.assertEqual(first,workbudgets.definition(a,node))
        self.assertNotEqual(first,workbudgets.definition(b,node))
        self.assertLess(len(first),128)


if __name__=='__main__': unittest.main()
