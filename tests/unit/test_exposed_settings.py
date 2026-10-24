import unittest
from hammerless.core.logic import LNode,LLink,compile_graph
from hammerless.core.ir import MapIR,Entity

class ExposedSettingsTests(unittest.TestCase):
    def test_values_feed_general_logic(self):
        for kind,value,expected in [('num',42,'42.0'),('bool',False,'false'),('text','hello "there"','"hello \\"there\\""'),('vec',(1,2,3),'Vector(1, 2, 3)')]:
            with self.subTest(kind=kind):
                ir=MapIR()
                problems=compile_graph([LNode('setting','EXPOSED_VALUE',{'type':kind,'value':value}),
                    LNode('store','SET_VAR',{'kind':kind,'name':'setting_value'})],
                    [LLink('setting','value','store','value',True)],ir)
                self.assertEqual(problems,[])
                script='\n'.join(ir.extra_scripts.values())
                self.assertIn('HL_VarSet("setting_value", '+expected+', false)',script)
    def test_wrong_purchase_setting_type_is_reported(self):
        ir=MapIR(entities=[Entity('prop_dynamic_override',(0,0,0),keyvalues={'model':'models/w_models/weapons/w_eq_medkit.mdl'},source='Station')])
        problems=compile_graph([LNode('setting','EXPOSED_VALUE',{'type':'text','value':'not a price'}),
             LNode('buy','WALL_WEAPON',obj='Station')],[LLink('setting','value','buy','price',True)],ir)
        self.assertTrue(any('wrong setting type' in p for p in problems))
