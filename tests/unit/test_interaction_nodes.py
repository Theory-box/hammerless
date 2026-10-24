import unittest
from hammerless.core.ir import Entity, MapIR
from hammerless.core.logic import LNode, LLink, compile_graph


class InteractionNodeTests(unittest.TestCase):
    def test_inventory_definition_is_reusable_without_a_shop(self):
        ir=MapIR()
        problems=compile_graph([
            LNode('item','INVENTORY_ITEM',{'item':'katana'}),
            LNode('give','GIVE_INVENTORY')],
            [LLink('item','value','give','item',True)],ir)
        self.assertEqual(problems,[])
        script=next(iter(ir.extra_scripts.values()))
        self.assertIn('melee_script_name = "katana"',script)
        self.assertIn('HL_GiveInventory',script)
        self.assertNotIn('HL_Points',script)
        self.assertNotIn('HL_Wall',script)
        self.assertTrue(any(e.keyvalues.get('thinkfunction')=='HL_Think' for e in ir.entities))

    def test_exclusive_controller_reports_duplicate_across_graphs(self):
        ir=MapIR()
        n=LNode('reset','RESET_KEY_VAR',{'name':'scores','exclusive':True})
        self.assertEqual(compile_graph([n],[],ir,'one'),[])
        self.assertTrue(any('Single Controller' in p for p in compile_graph([n],[],ir,'two')))

    def test_nonexclusive_resets_can_be_used_in_several_places(self):
        ir=MapIR()
        for name in ('one','two'):
            self.assertEqual(compile_graph([LNode(name,'RESET_KEY_VAR',{'name':'scores'})],[],ir,name),[])

    def test_interaction_registration_follows_function_definition(self):
        ir=MapIR(entities=[Entity('prop_dynamic_override',source='Display')])
        errors=compile_graph([LNode('self','SELF',obj='Display'),LNode('use','INTERACTION',consts={'range':96})],
            [LLink('self','entity','use','entity',True)],ir)
        self.assertEqual(errors,[])
        script=next(iter(ir.extra_scripts.values()))
        self.assertLess(script.index('::HL_Interact_use <- function'),script.index('HL_Interactions.append('))

    def test_key_variable_initial_value_is_used(self):
        ir=MapIR()
        errors=compile_graph([
            LNode('get','GET_VAR',{'name':'score','scope':'KEY','kind':'num'},consts={'key':'human-a','default':100}),
            LNode('set','SET_VAR',{'name':'copy','scope':'MAP','kind':'num'})],
            [LLink('get','result','set','value',True)],ir)
        self.assertEqual(errors,[])
        self.assertIn('HL_KeyGet("human-a", "score", 100.0)',next(iter(ir.extra_scripts.values())))


if __name__=='__main__':
    unittest.main()
