import os
from pathlib import Path
import sys
import unittest

if os.environ.get("SQUIRREL_PYTHONPATH"):
    sys.path.insert(0, os.environ["SQUIRREL_PYTHONPATH"])
try:
    import squirrel
except ImportError:
    squirrel = None

from hammerless.core.ir import MapIR
from hammerless.core.logic import LNode as N, LLink as L, compile_graph


def compile_nodes(nodes, links):
    ir=MapIR()
    errors=compile_graph(nodes,links,ir)
    script=next((s for p,s in ir.extra_scripts.items() if "/logic_" in p), "")
    return errors,script


class WorldQueryCompilerTests(unittest.TestCase):
    def test_ordinary_query_has_single_capture(self):
        nodes=[N("start","MAP_START"),N("near","WQ_NEAREST",consts={"radius":256}),N("info","WQ_INFO")]
        links=[L("start","start","near","run"),L("near","next","info","run"),L("near","area","info","area",True)]
        errors,script=compile_nodes(nodes,links)
        self.assertFalse(errors,errors)
        self.assertEqual(script.count("HLWQ.Nearest("),1)
        self.assertIn('::HL_R["near"] <- HLWQ.Nearest',script)
        self.assertIn('::HL_R["near"].area',script)

    def test_synchronous_loop_queries_are_local_and_ordered(self):
        nodes=[N("find","BA_FIND"),N("loop","BA_LOOP"),N("near","WQ_NEAREST"),N("info","WQ_INFO")]
        links=[L("find","bots","loop","bots",True),L("loop","each","near","run"),L("near","next","info","run"),L("near","area","info","area",True)]
        errors,script=compile_nodes(nodes,links)
        self.assertFalse(errors,errors)
        self.assertIn("local HL_WQ_near = HLWQ.Nearest",script)
        self.assertIn("HLWQ.Info(HL_WQ_near.area",script)
        self.assertNotIn('::HL_R["near"]',script)
        links[1]=L("loop","each","info","run")
        errors,_=compile_nodes(nodes,links)
        self.assertTrue(any("run the world query earlier" in e for e in errors),errors)

    def test_typed_iteration_is_bounded_and_item_cannot_escape(self):
        nodes=[N("find","BA_FIND"),N("loop","BA_LOOP"),N("near","WQ_NEARBY"),N("each","FOR_EACH",{"what":"NAV_AREAS"}),N("info","WQ_INFO")]
        links=[L("find","bots","loop","bots",True),L("loop","each","near","run"),L("near","next","each","run"),L("near","areas","each","list",True),L("each","each","info","run"),L("each","item","info","area",True)]
        errors,script=compile_nodes(nodes,links)
        self.assertFalse(errors,errors)
        self.assertIn("HL_WQI_each<128",script)
        self.assertIn("HLWQ.Info(HL_WQA_each",script)
        links[4]=L("each","done","info","run")
        errors,_=compile_nodes(nodes,links)
        self.assertTrue(any("inside their Each path" in e for e in errors),errors)

    def test_action_lifecycle_query(self):
        definition={"nodes":[N("entry","BA_ENTRY"),N("near","WQ_NEAREST"),N("info","WQ_INFO")],
                    "links":[L("entry","update","near","run"),L("near","next","info","run"),L("near","area","info","area",True)],
                    "inputs":[],"supports":"SPECIALS","channels":[]}
        nodes=[N("find","BA_FIND"),N("loop","BA_LOOP"),N("apply","BA_APPLY"),N("action","BA_ACTION",{"definition":definition})]
        links=[L("find","bots","loop","bots",True),L("loop","each","apply","run"),L("loop","current","apply","bot",True),L("action","action","apply","action",True)]
        errors,script=compile_nodes(nodes,links)
        self.assertFalse(errors,errors)
        self.assertIn("local HL_WQ_",script)

    def test_ambient_is_safe_before_data_deployment(self):
        errors,script=compile_nodes([N("ambient","WQ_AMBIENT")],[])
        self.assertFalse(errors,errors)
        self.assertIn("HLWQ_REQUIRES_AMBIENT",script)
        self.assertIn("HLWQ_Ambient(Vector",script)

    def test_nav_handles_and_lists_pass_through_action_parameters(self):
        definition={"nodes":[N("entry","BA_ENTRY"),N("input","BA_PARAM",{"uid":"area","type":"navarea"}),
                             N("areas","BA_PARAM",{"uid":"areas","type":"navareas"}),N("info","WQ_INFO"),
                             N("item","WQ_ITEM")],
                    "links":[L("entry","start","info","run"),L("input","value","info","area",True),
                             L("info","next","item","run"),L("areas","value","item","areas",True)],
                    "inputs":[("area","navarea",None),("areas","navareas",None)],"supports":"SPECIALS","channels":[]}
        nodes=[N("find","BA_FIND"),N("loop","BA_LOOP"),N("near","WQ_NEARBY"),N("apply","BA_APPLY"),N("action","BA_ACTION",{"definition":definition})]
        links=[L("find","bots","loop","bots",True),L("loop","each","near","run"),L("near","next","apply","run"),
               L("loop","current","apply","bot",True),L("action","action","apply","action",True),
               L("near","area","action","area",True),L("near","areas","action","areas",True)]
        errors,script=compile_nodes(nodes,links)
        self.assertFalse(errors,errors)
        self.assertIn('HLWQ.Info(ctx.params["area"]',script)
        self.assertIn('HLWQ.Item(ctx.params["areas"]',script)
        self.assertIn('["areas"] = HL_WQ_near.areas',script)


@unittest.skipUnless(squirrel is not None and hasattr(squirrel,"SQVM"),"squirrel-lang unavailable")
class WorldQueryRuntimeTests(unittest.TestCase):
    def test_runtime(self):
        root=Path(__file__).resolve().parents[2]
        vm=squirrel.SQVM()
        vm.execute((Path(__file__).with_suffix(".nut")).read_text() + "\n" +
                   (root/"hammerless/core/world_queries_runtime.nut").read_text()+"\nRunTests();")


if __name__ == "__main__":
    unittest.main()
