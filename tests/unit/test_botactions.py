import unittest
import os
import sys

if os.environ.get("SQUIRREL_PYTHONPATH"):
    sys.path.insert(0, os.environ["SQUIRREL_PYTHONPATH"])
try:
    import squirrel
except ImportError:
    squirrel = None

from hammerless.core.ir import MapIR
from hammerless.core.logic import LNode as N, LLink as L, compile_graph


def fixture():
    definition = {
        "nodes": [N("entry", "BA_ENTRY"),
                  N("amount", "BA_PARAM", {"uid": "amount", "type": "num"}),
                  N("override", "BA_OVERRIDE", {"property": "m_flFrozen", "type": "float"})],
        "links": [L("entry", "start", "override", "run"),
                  L("amount", "value", "override", "value", True)],
        "inputs": [("amount", "num", 1)], "supports": "COMMONS", "channels": ["Movement"]}
    nodes = [N("start", "MAP_START"), N("find", "BA_FIND", {"kind": "COMMONS"}),
             N("loop", "BA_LOOP"), N("apply", "BA_APPLY"),
             N("freeze", "BA_ACTION", {"definition": definition}),
             N("seen", "SEEN", consts={"angle": 90})]
    links = [L("start", "start", "loop", "start"),
             L("find", "bots", "loop", "bots", True),
             L("loop", "each", "apply", "run"),
             L("loop", "current", "apply", "bot", True),
             L("freeze", "action", "apply", "action", True),
             L("loop", "current", "seen", "target", True),
             L("seen", "seen", "apply", "enabled", True)]
    return nodes, links


def compile_fixture(nodes=None, links=None, ir=None, graph=""):
    if nodes is None:
        nodes, links = fixture()
    ir = ir or MapIR()
    errors = compile_graph(nodes, links, ir, graph)
    script = next((s for p, s in ir.extra_scripts.items() if "/logic_" in p), "")
    return errors, script, ir


class BotActionCompilerTests(unittest.TestCase):
    @unittest.skipUnless(squirrel is not None and hasattr(squirrel, "SQVM"), "optional Squirrel interpreter")
    def test_commands_use_target_for_retreat_and_position_for_move(self):
        from hammerless.core.botactions import HELPERS
        vm = squirrel.SQVM()
        vm.execute(r'''
::HLBA <- {IsBot=function(bot){return true;}};
::sent <- null;
::CommandABot <- function(args){sent=args;return true;};
local bot={GetClassname=function(){return "player";}};
local target={IsValid=function(){return true;}};
''' + HELPERS + r'''
HLBA.Command({bot=bot}, "RETREAT", target, null);
if(sent.cmd!=2 || sent.target!=target || "pos" in sent)throw "Retreat must reference its target";
HLBA.Command({bot=bot}, "MOVE", null, "position");
if(sent.cmd!=1 || sent.pos!="position" || "target" in sent)throw "Move must reference its position";
''')

    @unittest.skipUnless(squirrel is not None and hasattr(squirrel, "SQVM"), "optional Squirrel interpreter")
    def test_generated_script_runs_selection_freeze_and_release(self):
        errors, script, _ = compile_fixture()
        self.assertEqual(errors, [])
        vm = squirrel.SQVM()
        vm.execute(script + r'''
::printl <- function(s) {};
::Time <- function() { return 0.0; };
::Vector <- function(x,y,z) { return {x=x,y=y,z=z}; };
class TestBot {
    props=null;
    constructor() { props={m_flFrozen=0.25}; }
    function IsValid() { return true; }
    function GetHealth() { return 100; }
    function GetClassname() { return "infected"; }
}
::testbot <- TestBot();
::Entities <- { FindByClassname=function(previous, classname) {
    return classname == "infected" && previous == null ? testbot : null;
}};
::NetProps <- {
    HasProp=function(bot,key) { return key in bot.props; },
    GetPropFloat=function(bot,key) { return bot.props[key]; },
    SetPropFloat=function(bot,key,value) { bot.props[key]=value; }
};
::visible <- true;
::HL_SeenBy <- function(a,b,c,d,e,f) { return visible ? testbot : null; };
HL_BA_Start_loop();
HLBA.TickAll(0);
if (HLBA.errors.len()) throw HLBA.errors[0];
if (testbot.props.m_flFrozen != 1) throw "Generated Start did not apply the parameter";
visible=false;
HLBA.TickAll(0.2);
if (testbot.props.m_flFrozen != 0.25) throw "Generated false condition did not restore";
visible=true;
HLBA.TickAll(0.4);
OnGameEvent_round_end({});
if (testbot.props.m_flFrozen != 0.25 || HLBA.loops.len()) throw "Generated round cleanup failed";
if (HL_Think() != 0.01) throw "Generated thinker did not schedule the fast tick";
''')

    def test_scoped_values_snapshots_cleanup_and_engine_start(self):
        errors, script, ir = compile_fixture()
        self.assertEqual(errors, [])
        self.assertIn('HLBA.Request(ctx, "apply", ctx.bot, HL_Action_freeze', script)
        self.assertIn('HL_SeenBy(ctx.bot', script)
        self.assertIn('["amount"] = 1.0', script)
        self.assertIn('ctx.params["amount"]', script)
        self.assertIn('"property:m_flFrozen"', script)
        self.assertIn('HLBA.TickAll();', script)
        self.assertIn('function HL_Think() {\n    HLBA.TickAll();', script)
        self.assertIn('HL_OtherThinkNext = Time() + 0.1;\n    return 0.01;', script)
        self.assertIn('OnGameEvent_round_end', script)
        self.assertIn('HLBA.StopAll();', script)
        self.assertTrue(any(o.parameter == 'HL_BA_Start_loop()' for e in ir.entities for o in e.outputs))

    def test_box_and_other_loop_are_not_bot_sources(self):
        for kind in ("SELF", "BA_LOOP"):
            nodes, links = fixture()
            nodes.append(N("box", kind))
            links = [l for l in links if not (l.to_node == "apply" and l.to_socket == "bot")]
            links.append(L("box", "current", "apply", "bot", True))
            errors, script, _ = compile_fixture(nodes, links)
            self.assertTrue(any("Current Bot" in e for e in errors), errors)
            self.assertNotIn('::HL_BA_Start_loop <-', script)

    def test_delay_cannot_capture_current_bot(self):
        nodes, links = fixture()
        nodes.append(N("later", "DELAY"))
        links.append(L("apply", "next", "later", "in"))
        errors, script, _ = compile_fixture(nodes, links)
        self.assertTrue(errors)
        self.assertNotIn('::HL_BA_Start_loop <-', script)

    def test_cyclic_value_and_event_paths_are_rejected(self):
        for data in (True, False):
            nodes, links = fixture()
            if data:
                nodes.append(N("cycle", "BOOL_MATH"))
                links += [L("cycle", "value", "cycle", "a", True),
                          L("cycle", "value", "apply", "enabled", True)]
            else:
                links.append(L("apply", "next", "apply", "run"))
            errors, _, _ = compile_fixture(nodes, links)
            self.assertTrue(any("cycle" in e or "acyclic" in e for e in errors), errors)

    def test_lifecycle_requires_declared_parameters(self):
        nodes, links = fixture()
        nodes[4].settings["definition"]["inputs"] = []
        errors, _, _ = compile_fixture(nodes, links)
        self.assertTrue(any("parameter" in e for e in errors), errors)

    def test_dynamic_action_parameters_are_read_inside_collection(self):
        nodes, links = fixture()
        nodes.append(N("value", "VALUE", consts={"value": 0.75}))
        links.append(L("value", "value", "freeze", "amount", True))
        errors, script, _ = compile_fixture(nodes, links)
        self.assertEqual(errors, [])
        self.assertIn('["amount"] = 0.75', script)

    def test_two_graphs_get_one_runtime_and_distinct_definitions(self):
        nodes, links = fixture()
        ir = MapIR()
        self.assertEqual(compile_graph(nodes, links, ir, "a"), [])
        self.assertEqual(compile_graph(nodes, links, ir, "b"), [])
        script = next(iter(ir.extra_scripts.values()))
        self.assertEqual(script.count('::HLBA <-'), 1)
        self.assertIn('HL_Action_a_freeze', script)
        self.assertIn('HL_Action_b_freeze', script)
        self.assertEqual(script.count('    HLBA.StopAll();'), 3)

    def test_nonfinite_loop_interval_rejected(self):
        nodes, links = fixture()
        nodes[2].settings["interval"] = float("nan")
        errors, _, _ = compile_fixture(nodes, links)
        self.assertTrue(any("finite" in e for e in errors), errors)

    def test_interval_socket_default_and_wire_are_honored(self):
        nodes, links = fixture()
        nodes[2].consts["interval"] = 0.4
        errors, script, _ = compile_fixture(nodes, links)
        self.assertEqual(errors, [])
        self.assertIn('HLBA.Start("loop", 0.4,', script)
        nodes.append(N("interval", "VALUE", consts={"value": 0.75}))
        links.append(L("interval", "value", "loop", "interval", True))
        errors, script, _ = compile_fixture(nodes, links)
        self.assertEqual(errors, [])
        self.assertIn('HLBA.Start("loop", 0.75,', script)

    def test_flag_and_integer_overrides_coerce_numeric_inputs(self):
        for kind in ("int", "flags"):
            nodes, links = fixture()
            override = nodes[4].settings["definition"]["nodes"][2]
            override.settings.update(type=kind, mask=32)
            errors, script, _ = compile_fixture(nodes, links)
            self.assertEqual(errors, [])
            self.assertIn('(ctx.params["amount"]).tointeger()', script)

    def test_current_bot_cannot_escape_to_ordinary_events(self):
        nodes, links = fixture()
        nodes.append(N("ordinary", "IF"))
        links.append(L("seen", "seen", "ordinary", "condition", True))
        errors, _, _ = compile_fixture(nodes, links)
        self.assertTrue(any("owning loop" in e for e in errors), errors)
        from hammerless.core.botactions import BotActionProblem
        self.assertTrue(all(isinstance(e, BotActionProblem) for e in errors))

    def test_fatal_bot_diagnostics_do_not_promote_ordinary_warnings(self):
        from hammerless.core.botactions import BotActionProblem
        nodes, links = fixture()
        nodes[2].consts["interval"] = -1
        nodes.append(N("missing_self", "SELF"))
        nodes.append(N("ordinary_seen", "SEEN"))
        nodes.append(N("ordinary_if", "IF"))
        links += [L("missing_self", "entity", "ordinary_seen", "target", True),
                  L("ordinary_seen", "seen", "ordinary_if", "condition", True)]
        errors, _, _ = compile_fixture(nodes, links)
        fatal = [e for e in errors if isinstance(e, BotActionProblem)]
        ordinary = [e for e in errors if not isinstance(e, BotActionProblem)]
        self.assertTrue(any("interval" in e for e in fatal), errors)
        self.assertTrue(any("Self has no object" in e for e in ordinary), errors)

    @unittest.skipUnless(squirrel is not None and hasattr(squirrel, "SQVM"), "optional Squirrel interpreter")
    def test_seen_by_human_filter_excludes_bots_and_infected(self):
        from hammerless.core.logic import HELPERS
        vm = squirrel.SQVM()
        vm.execute(r'''
class V {
    function _sub(other) { return this; }
    function Length() { return 0.0; }
}
class Viewer {
    bot=false; survivor=true;
    constructor(b,s) { bot=b; survivor=s; }
    function IsValid() { return true; }
    function IsDead() { return false; }
    function GetClassname() { return "player"; }
    function IsSurvivor() { return survivor; }
}
::IsPlayerABot <- function(p) { return p.bot; };
::HL_Eye <- function(p) { return V(); };
''' + HELPERS["HL_SeenBy"] + r'''
local bot=Viewer(true,true), human=Viewer(false,true), infected=Viewer(false,false);
if (HL_SeenBy(null,V(),bot,90,false,true)!=null) throw "Bot watcher counted as human";
if (HL_SeenBy(null,V(),infected,90,false,true)!=null) throw "Infected watcher counted as survivor";
if (HL_SeenBy(null,V(),human,90,false,true)!=human) throw "Human watcher rejected";
if (HL_SeenBy(null,V(),bot,90,false)!=bot) throw "Default existing behavior changed";
''')

    @unittest.skipUnless(squirrel is not None and hasattr(squirrel, "SQVM"), "optional Squirrel interpreter")
    def test_seen_by_obstruction_tolerance_is_world_space(self):
        from hammerless.core.logic import HELPERS
        vm = squirrel.SQVM()
        vm.execute(r'''
class V {
    distance=1000.0;
    function _sub(other) { return this; }
    function Length() { return distance; }
    function Dot(other) { return other.distance; }
}
class E {
    function IsValid() { return true; }
    function GetClassname() { return "player"; }
    function IsDead() { return false; }
    function GetCenter() { return V(); }
}
::HL_Eye <- function(p) { return V(); };
::HL_Look <- function(p) { return null; };
::HL_Fwd <- function(p) { return V(); };
::fraction <- 0.99;
::hit_entity <- null;
::TraceLine <- function(t) { t.hit <- true; t.fraction <- fraction; t.enthit <- hit_entity; };
''' + HELPERS["HL_SeenBy"] + r'''
local viewer=E(), target=E();
if(HL_SeenBy(target,null,viewer,90,true)!=null)throw "Wall ten units in front of distant target ignored";
fraction=0.9995;
if(HL_SeenBy(target,null,viewer,90,true)!=viewer)throw "Sub-unit endpoint tolerance lost";
fraction=0.5;hit_entity=target;
if(HL_SeenBy(target,null,viewer,90,true)!=viewer)throw "Hitting the target itself counts as obstruction";
''')


if __name__ == "__main__":
    unittest.main()
