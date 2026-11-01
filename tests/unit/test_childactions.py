import os
from pathlib import Path
import sys
import unittest
if os.environ.get('SQUIRREL_PYTHONPATH'): sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
try: import squirrel
except ImportError: squirrel=None
from hammerless.core.ir import MapIR
from hammerless.core.logic import LNode as N,LLink as L,compile_graph

def fixture():
    child={'nodes':[N('entry','BA_ENTRY'),N('done','BA_RESULT',{'result':'FAILED'})],
           'links':[L('entry','start','done','run')],'inputs':[],'channels':['Movement'],'supports':'COMMONS'}
    parent={'nodes':[N('entry','BA_ENTRY'),N('arg','BA_PARAM',{'uid':'task','type':'action'}),N('step','BA_CHILD')],
            'links':[L('entry','start','step','run'),L('entry','update','step','run'),L('arg','value','step','action',True)],
            'inputs':[('task','action',None)],'channels':[],'supports':'ALL'}
    nodes=[N('start','MAP_START'),N('find','BA_FIND',{'kind':'COMMONS'}),N('loop','BA_LOOP'),N('apply','BA_APPLY'),
           N('task','BA_ACTION',{'definition':child}),N('wrapper','BA_ACTION',{'definition':parent})]
    links=[L('start','start','loop','start'),L('find','bots','loop','bots',True),L('loop','each','apply','run'),
           L('loop','current','apply','bot',True),L('wrapper','action','apply','action',True),L('task','action','wrapper','task',True)]
    return nodes,links

class ChildActionTests(unittest.TestCase):
    def test_static_binding_and_channels(self):
        nodes,links=fixture();ir=MapIR();errors=compile_graph(nodes,links,ir)
        self.assertFalse(errors,errors)
        script='\n'.join(ir.extra_scripts.values())
        self.assertIn('HLBA.InheritChannels(HL_Action_wrapper, [HL_Action_task]',script)
        self.assertIn('HLBA.ChildStep(ctx, "wrapper/step", HL_Action_task, ctx.params["task"]',script)
    def test_missing_and_cyclic_bindings(self):
        nodes,links=fixture();ir=MapIR()
        self.assertTrue(compile_graph(nodes,links[:-1],ir))
        links[-1]=L('wrapper','action','wrapper','task',True)
        self.assertTrue(compile_graph(nodes,links,MapIR()))
    def test_cannot_step_in_stop(self):
        nodes,links=fixture();nodes[-1].settings['definition']['links'][0]=L('entry','stop','step','run')
        errors=compile_graph(nodes,links,MapIR());self.assertTrue(any('Stop cleanup' in str(e) for e in errors),errors)
    def test_direct_parent_movement_conflict(self):
        nodes,links=fixture();d=nodes[-1].settings['definition'];d['nodes'].append(N('move','BA_COMMAND',{'command':'RESET'}))
        d['links'].append(L('entry','stop','move','run'))
        errors=compile_graph(nodes,links,MapIR());self.assertTrue(any('own bot commands' in str(e) for e in errors),errors)
    def test_declared_parent_channel_without_commands_is_allowed(self):
        nodes,links=fixture();nodes[-1].settings['definition']['channels']=['Movement']
        self.assertFalse(compile_graph(nodes,links,MapIR()))
    def test_action_parameter_can_feed_composition(self):
        nodes,links=fixture();d=nodes[-1].settings['definition'];d['nodes'].append(N('choose','BA_CHOOSE',consts={'condition':True}))
        d['links'][-1]=L('arg','value','choose','true_action',True)
        d['links'].append(L('choose','action','step','action',True))
        self.assertFalse(compile_graph(nodes,links,MapIR()))
    def test_combined_bound_and_internal_depth_is_rejected(self):
        nodes,links=fixture()
        # Fourteen external wrappers + the leaf reaches depth 15.
        previous='task'
        for i in range(14):
            name='sequence'+str(i);nodes.append(N(name,'BA_SEQUENCE'));links.append(L(previous,'action',name,'action_1',True));previous=name
        links[5]=L(previous,'action','wrapper','task',True)
        d=nodes[5].settings['definition'];d['nodes'].append(N('choose','BA_CHOOSE',consts={'condition':True}))
        d['links'][-1]=L('arg','value','choose','true_action',True)
        d['links'].append(L('choose','action','step','action',True))
        errors=compile_graph(nodes,links,MapIR())
        self.assertTrue(any('nesting exceeds 16' in str(e) for e in errors),errors)
    @unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'optional squirrel-lang missing')
    def test_runtime_children(self):
        root=Path(__file__).resolve().parents[2]
        squirrel.SQVM().execute((root/'hammerless/core/bot_actions_runtime.nut').read_text()+'\n'+Path(__file__).with_suffix('.nut').read_text())
if __name__=='__main__':unittest.main()
