"""Real Blender sockets, editable rule flattening and generated compiler output."""
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import candidates, logicgroups
from hammerless.core.candidates import compile_rule
from hammerless.core.logic import _Compiler
from hammerless.core.ir import MapIR

tree=bpy.data.node_groups.new('Candidates test','HL_LogicTree')
for kind in ('bots','navareas'):
    node=tree.nodes.new('HL_CandidateRule'); node.list_type=kind
    context=SimpleNamespace(space_data=SimpleNamespace(edit_tree=tree))
    result=candidates.HL_OT_candidate_rule_new.execute(SimpleNamespace(node_name=node.name),context)
    assert result=={'FINISHED'}
    exported=node.to_lnode(bpy.context)
    c=_Compiler(MapIR(),[])
    compile_rule(c,exported,kind)
    assert not c.problems,c.problems
    group=node.group
    logicgroups.new_item(group,'Bias','num',False,'bias'); logicgroups.sync(group)
    assert any(s.identifier=='bias' for s in node.inputs)
    group_in=next(n for n in group.nodes if n.kind=='GROUP_IN')
    group_out=next(n for n in group.nodes if n.kind=='GROUP_OUT')
    score=next(s for s in group_out.inputs if s.identifier=='score')
    for link in list(score.links): group.links.remove(link)
    group.links.new(next(s for s in group_in.outputs if s.identifier=='bias'),score)
    exported=node.to_lnode(bpy.context)
    c=_Compiler(MapIR(),[]); _,params=compile_rule(c,exported,kind)
    assert 'bias' in params
    # Ordinary nested groups flatten into the rule rather than becoming opaque callbacks.
    nested=bpy.data.node_groups.new('Nested score','HL_LogicTree'); nested.is_group=True
    logicgroups.new_item(nested,'Value','num',False,'in')
    logicgroups.new_item(nested,'Value','num',True,'out')
    ni=nested.nodes.new('HL_NodeGroupInput'); no=nested.nodes.new('HL_NodeGroupOutput')
    nested.links.new(ni.outputs[0],no.inputs[0])
    use=group.nodes.new('HL_NodeGroup'); use.group=nested
    for link in list(score.links): group.links.remove(link)
    group.links.new(next(s for s in group_in.outputs if s.identifier=='bias'),use.inputs[0])
    group.links.new(use.outputs[0],score)
    c=_Compiler(MapIR(),[]); compile_rule(c,node.to_lnode(bpy.context),kind)
    assert not c.problems,c.problems
    for cls in (candidates.HL_EvaluateCandidates,candidates.HL_RankedCandidate):
        n=tree.nodes.new(cls.bl_idname); n.list_type=kind
        assert next(s for s in n.outputs if s.identifier=='item').bl_idname==('HL_BotSocket' if kind=='bots' else 'HL_NavAreaSocket')
print('CANDIDATE_BLENDER_OK')
