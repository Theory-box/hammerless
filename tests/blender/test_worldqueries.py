"""Register, wire typed navigation groups, compile, and save/reload in real Blender."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logicgroups import flatten,new_group_tree,new_item,sync
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR

t=bpy.data.node_groups.new("World query test","HL_LogicTree")
t.use_fake_user=True
near=t.nodes.new("HL_WQNearby")
each=t.nodes.new("HL_NodeForEach"); each.what="NAV_AREAS"
info=t.nodes.new("HL_WQInfo")
t.links.new(near.outputs["Next"],each.inputs["Run"])
t.links.new(near.outputs["Areas"],each.inputs["Areas"])
t.links.new(each.outputs["Each"],info.inputs["Run"])
t.links.new(each.outputs["Item"],info.inputs["Area"])
assert each.outputs["Item"].bl_idname=="HL_NavAreaSocket"
report=SimpleNamespace(warnings=[])
flat=flatten(bpy.context,t,report)
assert not report.warnings,report.warnings
errors=compile_graph(flat.nodes,flat.links,MapIR())
assert not errors,errors
g=new_group_tree("Nav interface")
new_item(g,"Area","navarea",False)
new_item(g,"Areas","navareas",True)
sync(g)
from hammerless.blender.botactions import new_action
action_group=new_action("Nav action parameters")
new_item(action_group,"Area","navarea",False,"area")
new_item(action_group,"Areas","navareas",False,"areas")
action=t.nodes.new("HL_BAAction")
action.group=action_group
assert action.inputs["Area"].bl_idname=="HL_NavAreaSocket"
assert action.inputs["Areas"].bl_idname=="HL_NavAreasSocket"
serialized=action.to_lnode(bpy.context)
assert ("area","navarea",None) in serialized.settings["definition"]["inputs"]
assert ("areas","navareas",None) in serialized.settings["definition"]["inputs"]
parameter=action_group.nodes.new("HL_BAParam")
parameter.uid="area"
assert parameter.outputs[0].bl_idname=="HL_NavAreaSocket"
for name in ("HL_WQNearest","HL_WQItem","HL_WQPath","HL_WQAmbient"):
    t.nodes.new(name)
# Entity sockets must never receive a nav handle.
trace=t.nodes.new("HL_NodeTrace")
bad=t.links.new(each.outputs["Item"],trace.inputs["Ignore"])
try:
    flatten(bpy.context,t,SimpleNamespace(warnings=[]))
    raise AssertionError("nav area accepted as entity")
except ValueError as error:
    assert "cannot connect" in str(error),error
t.links.remove(bad)
with tempfile.TemporaryDirectory() as d:
    path=str(Path(d)/"worldqueries.blend")
    near_name=near.name
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    assert bpy.data.node_groups["World query test"].nodes[near_name].outputs["Areas"].bl_idname=="HL_NavAreasSocket"
print("PASS typed nav queries, groups, compiler, save/reload")
