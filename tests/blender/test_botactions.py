"""Real Blender action sockets, editable asset compilation, and save/reload."""
import sys
import tempfile
import re
from pathlib import Path
from types import SimpleNamespace
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import botactions
from hammerless.blender.logicgroups import flatten, new_item, new_group_tree, sync
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR

tree = bpy.data.node_groups.new("Bot runtime integration", "HL_LogicTree")
tree.use_fake_user = True
tree.scene_name = bpy.context.scene.name
start = tree.nodes.new("HL_NodeMapStart")
find = tree.nodes.new("HL_BAFind")
loop = tree.nodes.new("HL_BALoop")
apply = tree.nodes.new("HL_BAApply")
action = tree.nodes.new("HL_BAAction")
action.group = botactions.new_action("Editable test freeze", preset=True)
tree.links.new(start.outputs["On Map Start"], loop.inputs["Start"])
tree.links.new(find.outputs["Bots"], loop.inputs["Bots"])
tree.links.new(loop.outputs["Each Bot"], apply.inputs["Run"])
tree.links.new(loop.outputs["Current Bot"], apply.inputs["Bot"])
tree.links.new(action.outputs["Action"], apply.inputs["Action"])
assert apply.inputs["Action"].bl_idname == "HL_BotActionSocket"
assert apply.inputs["Bot"].bl_idname == "HL_BotSocket"
assert loop.inputs["Bots"].bl_idname == "HL_BotListSocket"
assert action.group.bot_action and action.group.is_group
assert len([n for n in action.group.nodes if n.bl_idname == "HL_BAOverride"]) == 2
assert not any(n.bl_idname in {"HL_NodeScriptCode", "HL_NodeScriptValue"} for n in action.group.nodes)

def compile_tree(t):
    report = SimpleNamespace(warnings=[])
    flat = flatten(bpy.context, t, report)
    assert not report.warnings, report.warnings
    ir = MapIR()
    problems = compile_graph(flat.nodes, flat.links, ir)
    return problems, "\n".join(ir.extra_scripts.values())

problems, script = compile_tree(tree)
assert not problems, problems
assert "HLBA.Request" in script and "HLBA.Start" in script
assert "property:movetype" in script and "property:m_fFlags" in script
assert "HLBA.Override" in script

# Normal Group Input exposes action settings without hidden parameter nodes.
new_item(action.group, "Amount", "num", False, "amount")
sync(action.group)
group_input = next(n for n in action.group.nodes if n.bl_idname == "HL_NodeGroupInput")
entry = next(n for n in action.group.nodes if n.bl_idname == "HL_BAEntry")
extra = action.group.nodes.new("HL_BAOverride")
extra.property_name = "m_flFrozen"
extra.value_type = "float"
action.group.links.new(group_input.outputs["Amount"], extra.inputs["Value"])
action.group.links.new(entry.outputs["Start"], extra.inputs["Run"])
action.inputs["Amount"].value = 0.375
problems, script = compile_tree(tree)
assert not problems, problems
assert 'ctx.params["amount"]' in script and '0.375' in script

# Ordinary groups must preserve specialized sockets and flattened ownership.
passthrough = new_group_tree("Typed bot transport")
for label, kind in (("Bot", "bot"), ("Bots", "bots"), ("Action", "action")):
    new_item(passthrough, label, kind, False, label.lower() + "_in")
    new_item(passthrough, label, kind, True, label.lower() + "_out")
group_in = passthrough.nodes.new("HL_NodeGroupInput")
group_out = passthrough.nodes.new("HL_NodeGroupOutput")
for label in ("Bot", "Bots", "Action"):
    passthrough.links.new(group_in.outputs[label], group_out.inputs[label])
transport = tree.nodes.new("HL_NodeGroup")
transport.group = passthrough
for label, socket_id in (("Bot", "HL_BotSocket"), ("Bots", "HL_BotListSocket"), ("Action", "HL_BotActionSocket")):
    assert transport.inputs[label].bl_idname == socket_id
    assert transport.outputs[label].bl_idname == socket_id
tree.links.new(loop.outputs["Current Bot"], transport.inputs["Bot"])
tree.links.new(transport.outputs["Bot"], apply.inputs["Bot"])
tree.links.new(action.outputs["Action"], transport.inputs["Action"])
tree.links.new(transport.outputs["Action"], apply.inputs["Action"])
tree.links.new(find.outputs["Bots"], transport.inputs["Bots"])
tree.links.new(transport.outputs["Bots"], loop.inputs["Bots"])
seen = tree.nodes.new("HL_NodeSeenBy")
tree.links.new(transport.outputs["Bot"], seen.inputs["Target"])
tree.links.new(seen.outputs["Seen"], apply.inputs["Enabled"])
problems, script = compile_tree(tree)
assert not problems, problems
assert "HL_SeenBy(ctx.bot" in script, "Bot-to-Entity query connection must compile"

# Generic entities cannot masquerade as bots, even through an ordinary group.
self_node = tree.nodes.new("HL_NodeSelf")
tree.links.new(self_node.outputs["Entity"], transport.inputs["Bot"])
try:
    problems, script = compile_tree(tree)
except ValueError as error:
    assert "Entity to Bot" in str(error), error
else:
    assert problems, "Generic entity accepted as a Bot"
tree.links.new(loop.outputs["Current Bot"], transport.inputs["Bot"])
tree.nodes.remove(self_node)

# Editable settings survive actual datablock serialization, then compile again.
flags = next(n for n in action.group.nodes if n.bl_idname == "HL_BAOverride" and n.value_type == "flags")
flags.mask = 64
flags.inputs["Value"].value = 64
with tempfile.TemporaryDirectory() as tmp:
    path = str(Path(tmp) / "bot_actions.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    tree = bpy.data.node_groups["Bot runtime integration"]
    action = next(n for n in tree.nodes if n.bl_idname == "HL_BAAction")
    flags = next(n for n in action.group.nodes if n.bl_idname == "HL_BAOverride" and n.value_type == "flags")
    assert flags.mask == 64 and flags.inputs["Value"].value == 64
    assert action.inputs["Amount"].value == 0.375
    problems, script = compile_tree(tree)
    assert not problems, problems

# Bypassing the owning loop must be rejected, rather than silently accepting a bot action.
apply = next(n for n in tree.nodes if n.bl_idname == "HL_BAApply")
start = next(n for n in tree.nodes if n.bl_idname == "HL_NodeMapStart")
for link in list(tree.links):
    if link.to_node == apply and link.to_socket.name == "Run":
        tree.links.remove(link)
tree.links.new(start.outputs["On Map Start"], apply.inputs["Run"])
problems, script = compile_tree(tree)
assert problems, "Apply outside the owning loop was accepted"
assert "HLBA.Request(ctx" not in script
# The shipped example is a complete graph, with deliberate human-only watchers.
example = bpy.data.node_groups.new("Watched example test", "HL_LogicTree")
example.use_fake_user = True
example.scene_name = bpy.context.scene.name
example_loop = botactions.watched_example(example)
assert abs(example_loop.inputs["Interval (seconds)"].value - 0.1) < 1e-6
example_seen = next(n for n in example.nodes if n.bl_idname == "HL_NodeSeenBy")
assert example_seen.humans_only
problems, script = compile_tree(example)
assert not problems, problems
assert re.search(r"HL_SeenBy\(ctx.bot,[^\n]+true\)", script), "Human-only watcher flag missing"
cadence = re.search(r'HLBA.Start\("[^"\n]+", ([0-9.]+),', script)
assert cadence and abs(float(cadence.group(1)) - 0.1) < 1e-6, [line for line in script.splitlines() if "HLBA.Start" in line]
for channel in ("Movement", "property:movetype", "property:m_fFlags"):
    assert channel in script, channel

# Registration can be cycled without duplicated socket entries or lost classes.
hammerless.unregister()
hammerless.register()
from hammerless.blender import logic
for socket_id in ("HL_BotSocket", "HL_BotListSocket", "HL_BotActionSocket"):
    assert logic.DATA_SOCKETS.count(socket_id) == 1
check_tree = bpy.data.node_groups.new("Reregister bot nodes", "HL_LogicTree")
botactions.watched_example(check_tree)
problems, script = compile_tree(check_tree)
assert not problems, problems
print("PASS bot sockets, Group Input parameters, typed groups, editable preset, compile, save/reload, invalid types/scope, watched example, reregister")
hammerless.unregister()
