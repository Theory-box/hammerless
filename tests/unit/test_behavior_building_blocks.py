import unittest
from hammerless.core.logic import LNode as N,LLink as L,compile_graph
from hammerless.core.ir import MapIR


def compile_nodes(nodes,links):
 ir=MapIR(); errors=compile_graph(nodes,links,ir)
 return errors,'\n'.join(ir.extra_scripts.values())


def action(nodes,links):
 definition={'nodes':[N('entry','BA_ENTRY')]+nodes,'links':links,'inputs':[],'supports':'COMMONS','channels':[]}
 return compile_nodes([N('find','BA_FIND',{'kind':'COMMONS'}),N('loop','BA_LOOP'),N('apply','BA_APPLY'),N('action','BA_ACTION',{'definition':definition})],
 [L('find','bots','loop','bots',True),L('loop','each','apply','run'),L('loop','current','apply','bot',True),L('action','action','apply','action',True)])

class BuildingBlockIntegration(unittest.TestCase):
 def test_cooldown_branches_capture_once(self):
  e,s=action([N('cool','BA_COOLDOWN',consts={'seconds':2}),N('save','BA_STATE_SET',{'name':'remaining','type':'num'})],
   [L('entry','update','cool','run'),L('cool','passed','save','run'),L('cool','remaining','save','value',True)])
  self.assertFalse(e,e);self.assertEqual(s.count('HLBAU.Cooldown(ctx,'),1)
  self.assertIn('.passed)',s);self.assertIn('HL_Q_action_cool.remaining',s)
 def test_utilities_require_action_context(self):
  e,s=compile_nodes([N('cool','BA_COOLDOWN')],[])
  self.assertTrue(any('belongs inside' in x for x in e),e)
 def test_feedback_cannot_be_read_before_execution(self):
  e,s=action([N('motion','BA_MOVEMENT'),N('save','BA_STATE_SET',{'type':'num'})],
   [L('entry','update','save','run'),L('motion','distance','save','value',True)])
  self.assertTrue(any('run the node earlier' in x for x in e),e)
 def test_changed_and_movement_capture_locally(self):
  e,s=action([N('changed','BA_CHANGED',{'type':'bool'}),N('motion','BA_MOVEMENT')],
   [L('entry','update','changed','run'),L('changed','changed','motion','run')])
  self.assertFalse(e,e);self.assertIn('HLBAU.Changed(ctx,',s);self.assertIn('HLBAU.Movement(ctx,',s)
 def test_bot_lists_work_in_normal_and_action_graphs(self):
  nodes=[N('bots','BA_FIND',{'kind':'COMMONS'}),N('count','LIST_COUNT',{'list_type':'bots'})]
  data=L('bots','bots','count','items',True)
  e,s=compile_nodes([N('start','MAP_START')]+nodes,[L('start','start','count','run'),data])
  self.assertFalse(e,e);self.assertIn('HLLT.Query(HLBA.Find(',s)
  e,s=action(nodes,[L('entry','update','count','run'),data])
  self.assertFalse(e,e);self.assertIn('local HL_Q_action_count',s)

if __name__=='__main__':unittest.main()
