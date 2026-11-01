"""Editable pure scoring rules and typed ranked snapshots."""
import bpy
from bpy.props import EnumProperty, PointerProperty, StringProperty
from .logic import _Block, _rebuild, TREE
from .botactions import _Wire

TYPES = [("bots", "Bots", "Living bots"), ("navareas", "Nav Areas", "Navigation areas")]


def changed(self, context):
    _rebuild(self)


class HL_CandidateRuleSocket(_Wire, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_CandidateRuleSocket", "Candidate Rule"


class HL_CandidateResultsSocket(_Wire, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_CandidateResultsSocket", "Ranked Candidates"


class HL_CandidateRule(_Block, bpy.types.Node):
    """Editable stateless group. Candidate, Position and Index change for each item. Extra inputs are captured once. Keep filters; larger Score wins. Open the group to change its logic."""
    bl_idname, bl_label, kind = "HL_CandidateRule", "Candidate Rule Group", "CAND_RULE"
    group: PointerProperty(type=bpy.types.NodeTree, name="Rule", update=changed,
                           poll=lambda self,t: t.bl_idname == TREE and bool(t.get("hl_candidate_rule")))
    list_type: EnumProperty(name="Items", items=TYPES, default="navareas", update=changed)

    def make_sockets(self):
        if self.group:
            for it in self.group.group_items:
                if not it.is_output and it.uid not in {"candidate", "position", "index"}:
                    self.data_in(it.kind, it.uid, it.name)
        self.data_out("candidate_rule", "rule", "Rule")

    def draw_buttons(self, context, layout):
        layout.prop(self,"group",text="")
        if self.group:
            op=layout.operator("hammerless.logic_group_edit",text="Edit Rule",icon="NODETREE")
            op.node_name=self.name
        else:
            layout.prop(self,"list_type",text="")
            layout.operator("hammerless.candidate_rule_new",text="New Rule").node_name=self.name

    def to_lnode(self, context):
        from types import SimpleNamespace
        from .logicgroups import flatten
        from ..core.logic import LNode, LLink
        if not self.group:
            raise ValueError("Choose or create a Candidate Rule group")
        todo,seen=[self.group],set()
        while todo:
            tree=todo.pop()
            if tree.name in seen: continue
            seen.add(tree.name)
            for n in tree.nodes:
                if getattr(n,"kind","") in {"CAND_RULE","BA_ACTION"}:
                    raise ValueError("Candidate rules cannot contain callable rule or action assets; use ordinary groups")
                if getattr(n,"kind","")=="GROUP" and n.group: todo.append(n.group)
        report=SimpleNamespace(warnings=[])
        flat=flatten(context,self.group,report)
        if report.warnings:
            raise ValueError("; ".join(report.warnings))
        kind=self.group.get("hl_candidate_rule")
        required={"candidate": "bot" if kind=="bots" else "navarea", "position":"vec", "index":"num"}
        ins={it.uid:it.kind for it in self.group.group_items if not it.is_output}
        outs={it.uid:it.kind for it in self.group.group_items if it.is_output}
        if any(ins.get(uid)!=typ for uid,typ in required.items()) or outs!={"keep":"bool","score":"num"}:
            raise ValueError("Rule interface requires Candidate, Position, Index inputs and Keep/Score outputs")
        if any(typ not in {"num","bool","vec","text","bot","navarea","thing"} for typ in ins.values()):
            raise ValueError("Rule parameters must be scalar values, positions or handles")
        outputs={}
        for uid,typ in ins.items():
            nid="__candidate_parameter_"+uid
            if any(n.id==nid for n in flat.nodes):
                raise ValueError("Reserved candidate parameter node name")
            flat.nodes.append(LNode(nid,"CAND_PARAM",{"uid":uid,"type":typ}))
            for target,socket in flat.ins.get(uid,[]):
                if target=="OUT":
                    outputs[socket]=(nid,"value")
                else:
                    flat.links.append(LLink(nid,"value",target,socket,data=True))
        for uid,sources in flat.outs.items():
            real=[s for s in sources if s[0]!="IN"]
            if real: outputs[uid]=real[0]
        result=super().to_lnode(context)
        result.settings["definition"]={"list_type":kind,"nodes":flat.nodes,"links":flat.links,
                                       "inputs":list(ins.items()),"outputs":outputs}
        return result


class _Typed(_Block):
    list_type: EnumProperty(name="Items", items=TYPES, default="navareas", update=changed)
    def settings(self): return {"list_type":self.list_type}
    def draw_buttons(self,context,layout): layout.prop(self,"list_type",text="")
    def result_sockets(self):
        self.data_out("bool","found","Found")
        self.data_out("bot" if self.list_type=="bots" else "navarea","item","Item")
        self.data_out("num","score","Score")
        self.data_out("num","index","Original Index")
        self.data_out("vec","position","Position")


class HL_EvaluateCandidates(_Typed,bpy.types.Node):
    """Run your rule on the first 256 entries, skipping invalid handles. Keep=true candidates sort by highest finite Score; ties preserve original order. Returns up to 128. Errors counts failed rule evaluations, Rejected counts invalid or filtered entries. Results remain a snapshot until Run fires again."""
    bl_idname,bl_label,kind="HL_EvaluateCandidates","Evaluate Candidates","CAND_EVALUATE"
    def make_sockets(self):
        self.ev_in("run","Run"); self.ev_out("next","Next")
        self.data_in(self.list_type,"items","Items")
        self.data_in("candidate_rule","rule","Rule")
        self.data_in("num","limit","Max Results").value=32
        self.data_out("candidate_results","results","Ranked Results")
        self.data_out(self.list_type,"items","Ranked Items")
        for key,label in (("count","Count"),("examined","Examined"),("rejected","Rejected"),("errors","Errors")):
            self.data_out("num",key,label)
        self.data_out("text","error","First Error")
        self.data_out("bool","truncated","Truncated")
        self.result_sockets()


class HL_RankedCandidate(_Typed,bpy.types.Node):
    """Inspect one ranked result, including its original input index and score. Rank starts at zero. Found=false for missing entries, expired handles or mismatched Bot/Nav Area type. Position is the position when evaluated."""
    bl_idname,bl_label,kind="HL_RankedCandidate","Ranked Candidate at Index","CAND_ITEM"
    def make_sockets(self):
        self.ev_in("run","Run"); self.ev_out("next","Next")
        self.data_in("candidate_results","results","Ranked Results")
        self.data_in("num","index","Rank (starts at 0)")
        self.result_sockets()


class HL_FindRankedCandidate(_Typed,bpy.types.Node):
    """Find a handle in an evaluated snapshot. Found means present and still valid, not that an absent item is invalid. Truncated warns that omitted items may still exist. The first/highest-ranked duplicate wins. Scores and positions are sampled, not recomputed."""
    bl_idname,bl_label,kind="HL_FindRankedCandidate","Find Ranked Candidate","CAND_FIND"
    def make_sockets(self):
        self.ev_in("run","Run"); self.ev_out("next","Next")
        self.data_in("candidate_results","results","Ranked Results")
        self.data_in("bot" if self.list_type=="bots" else "navarea","item","Item")
        self.result_sockets()
        self.data_out("num","rank","Rank")
        self.data_out("bool","truncated","Snapshot Truncated")


class HL_CandidateAmbient(_Block,bpy.types.Node):
    """Read baked ambient probes at a position. Pure value query for candidate rules; Available must be checked before treating zero as darkness. Excludes flashlights, dynamic lights and exposure. Multiple outputs share one query per candidate."""
    bl_idname,bl_label,kind="HL_CandidateAmbient","Sample Baked Ambient Light","CAND_AMBIENT"
    def make_sockets(self):
        self.data_in("vec","position","Position")
        for key in ("available","fallback"): self.data_out("bool",key,key.title())
        self.data_out("num","brightness","Brightness")
        self.data_out("vec","color","Color")


class HL_OT_candidate_rule_new(bpy.types.Operator):
    bl_idname,bl_label,bl_options="hammerless.candidate_rule_new","New Candidate Rule",{"REGISTER","UNDO"}
    node_name: StringProperty()
    def execute(self,context):
        from .logicgroups import new_item,sync
        tree=context.space_data.edit_tree
        node=tree.nodes.get(self.node_name)
        if node is None: return {"CANCELLED"}
        group=bpy.data.node_groups.new("Candidate Rule",TREE)
        group.is_group=True; group.use_fake_user=True; group["hl_candidate_rule"]=node.list_type
        for uid,label,typ,out in (("candidate","Candidate","bot" if node.list_type=="bots" else "navarea",False),
            ("position","Position","vec",False),("index","Index","num",False),
            ("keep","Keep","bool",True),("score","Score","num",True)):
            new_item(group,label,typ,out,uid)
        gi=group.nodes.new("HL_NodeGroupInput"); go=group.nodes.new("HL_NodeGroupOutput")
        gi.location=(-300,0); go.location=(220,0)
        from .logicgroups import sync
        sync(group)
        # Default rule explicitly accepts every candidate with equal priority.
        value=group.nodes.new("HL_NodeValue")
        value.inputs[0].value=0.0
        keep=group.nodes.new("HL_NodeCompare")
        keep.inputs[0].value=0.0; keep.inputs[1].value=0.0
        value.location=(-50,-140); keep.location=(-50,70)
        group.links.new(keep.outputs[0],next(s for s in go.inputs if s.identifier=="keep"))
        group.links.new(value.outputs[0],next(s for s in go.inputs if s.identifier=="score"))
        node.group=group
        return {"FINISHED"}


NODES=(HL_CandidateRule,HL_EvaluateCandidates,HL_RankedCandidate,HL_FindRankedCandidate,HL_CandidateAmbient)
class HL_MT_candidates(bpy.types.Menu):
    bl_idname,bl_label="HL_MT_candidates","Candidate Rules"
    def draw(self,context):
        for cls in NODES:
            op=self.layout.operator("node.add_node",text=cls.bl_label); op.type=cls.bl_idname; op.use_transform=True

CLASSES=(HL_CandidateRuleSocket,HL_CandidateResultsSocket)+NODES+(HL_OT_candidate_rule_new,HL_MT_candidates)
def register():
    from . import logic
    logic.SOCKET_OF_KIND.update(candidate_rule="HL_CandidateRuleSocket",candidate_results="HL_CandidateResultsSocket")
    logic.DATA_SOCKETS+=("HL_CandidateRuleSocket","HL_CandidateResultsSocket")
    for cls in CLASSES: bpy.utils.register_class(cls)
def unregister():
    from . import logic
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
    for key in ("candidate_rule","candidate_results"): logic.SOCKET_OF_KIND.pop(key,None)
    logic.DATA_SOCKETS=tuple(s for s in logic.DATA_SOCKETS if s not in {"HL_CandidateRuleSocket","HL_CandidateResultsSocket"})
