"""An editable observation-only example of shared caches and work grants."""
import bpy
from types import SimpleNamespace
from .actiontemplates import _Builder
from .logicgroups import new_item, sync

PARAMETERS=(("anchor","Shared Search Center","vec",(0,0,0)),
            ("radius","Search Radius","num",512.0),
            ("age","Maximum Sample Age (s)","num",1.0),
            ("cache","Shared Query Cache","query_cache",None),
            ("budget","Shared Work Budget","work_budget",None))


def shade_scan_action():
    from .candidates import HL_OT_candidate_rule_new
    b=_Builder("Shared Shade Scan (diagnostic)",PARAMETERS,supports="COMMONS")
    nearby=b.node("HL_CachedNearby","Search around the shared anchor")
    for source,target in (("anchor","position"),("radius","radius"),("age","max_age"),("cache","pool")):
        b.wire(b.params,source,nearby,target)
    b.value(nearby,"limit",32)
    grant=b.node("HL_RequestWorkGrant","Take turns using the shared budget")
    b.wire(b.params,"budget",grant,"pool")
    scan=b.node("HL_StepCandidates","Score a few areas per update",list_type="navareas")
    b.wire(nearby,"areas",scan,"items")
    b.wire(grant,"grant",scan,"grant");b.wire(grant,"queued",scan,"enabled")
    rule=b.node("HL_CandidateRule","Prefer lower available baked light",list_type="navareas")
    context=SimpleNamespace(space_data=SimpleNamespace(edit_tree=b.tree))
    HL_OT_candidate_rule_new.execute(SimpleNamespace(node_name=rule.name),context)
    group=rule.group
    for node in list(group.nodes):
        if node.bl_idname not in {"HL_NodeGroupInput","HL_NodeGroupOutput"}:group.nodes.remove(node)
    new_item(group,"Shared Query Cache","query_cache",False,"cache")
    new_item(group,"Maximum Sample Age (s)","num",False,"age")
    sync(group)
    gi=next(n for n in group.nodes if n.bl_idname=="HL_NodeGroupInput")
    go=next(n for n in group.nodes if n.bl_idname=="HL_NodeGroupOutput")
    sample=group.nodes.new("HL_CandidateCachedAmbient");sample.location=(-40,40);sample.width=240
    negate=group.nodes.new("HL_NodeMath");negate.op="SUBTRACT";negate.location=(240,-140)
    def wire(a,out,dst,inp):group.links.new(b.socket(a,out,True),b.socket(dst,inp))
    wire(gi,"position",sample,"position");wire(gi,"cache",sample,"pool");wire(gi,"age",sample,"max_age")
    wire(sample,"available",go,"keep");wire(sample,"brightness",negate,"b");wire(negate,"value",go,"score")
    rule.group=None;rule.group=group
    b.wire(b.params,"cache",rule,"cache");b.wire(b.params,"age",rule,"age")
    b.wire(rule,"rule",scan,"rule")
    complete=b.node("HL_NodeIf","Wait until the scan is complete")
    b.wire(scan,"complete",complete,"condition")
    found=b.node("HL_NodeIf","Did any candidate have baked light data?")
    b.wire(scan,"found",found,"condition")
    watch=b.node("HL_DebugWatch","Report selected center",value_type="vec")
    b.value(watch,"enabled",True);b.value(watch,"label","Shared shade scan: selected nav center")
    b.wire(scan,"position",watch,"value")
    missing=b.node("HL_DebugWatch","Report missing candidates or lighting",value_type="text")
    b.value(missing,"enabled",True);b.value(missing,"label","Shared shade scan")
    b.value(missing,"value","No candidate with available baked lighting was found")
    done=b.node("HL_BAResult","Observation complete",result="SUCCESS")
    failed=b.node("HL_BAResult","No usable observation",result="FAILED")
    def checked_next(source,destination,destination_socket):
        clear=b.node("HL_NodeTextCheck","No query error",op="EQUAL")
        b.wire(source,"error",clear,"a");b.value(clear,"b","")
        branch=b.node("HL_NodeIf","Continue only when the query succeeded")
        b.wire(clear,"result",branch,"condition")
        report=b.node("HL_DebugWatch","Report query failure",value_type="text")
        b.value(report,"enabled",True);b.value(report,"label","Shared shade scan failed")
        b.wire(source,"error",report,"value")
        b.wire(source,"next",branch,"in")
        b.wire(branch,"true",destination,destination_socket)
        b.wire(branch,"false",report,"run");b.wire(report,"next",failed,"run")
    checked_next(nearby,grant,"run")
    checked_next(grant,scan,"run")
    scan_failed=b.node("HL_NodeIf","Did candidate evaluation fail?")
    b.wire(scan,"failed",scan_failed,"condition")
    scan_error=b.node("HL_DebugWatch","Report candidate failure",value_type="text")
    b.value(scan_error,"enabled",True);b.value(scan_error,"label","Shared shade scan failed")
    b.wire(scan,"error",scan_error,"value")
    b.wire(scan_failed,"true",scan_error,"run");b.wire(scan_error,"next",failed,"run")
    for a,out,dst,inp in ((b.entry,"start",nearby,"run"),(b.entry,"update",nearby,"run"),
        (scan,"next",scan_failed,"in"),(scan_failed,"false",complete,"in"),
        (complete,"true",found,"in"),(found,"true",watch,"run"),(found,"false",missing,"run"),
        (watch,"next",done,"run"),(missing,"next",failed,"run")):
        b.wire(a,out,dst,inp)
    return b.tree


class HL_OT_shared_shade_scan(bpy.types.Operator):
    bl_idname,bl_label,bl_options="hammerless.shared_shade_scan","Shared Shade Scan Example",{"REGISTER","UNDO"}
    bl_description="Create an editable diagnostic graph: commons share a cached search around the 3D cursor, rank baked lighting within a shared work budget, and log a selected nav center. Does not move bots or prove reachability"
    def execute(self,context):
        from .logic import new_tree
        tree=new_tree(context,"Shared Shade Scan (diagnostic)")
        classes=("HL_NodeMapStart","HL_BAFind","HL_BALoop","HL_BAApply","HL_BAAction","HL_QueryCachePool","HL_WorkBudgetPool")
        start,find,loop,apply,action,cache,budget=[tree.nodes.new(cls) for cls in classes]
        find.selection="COMMONS";action.group=shade_scan_action()
        for key,label,kind,default in PARAMETERS:
            if default is not None:_Builder.socket(action,key).value=default
        _Builder.socket(action,"anchor").value=tuple(context.scene.cursor.location*context.scene.hammerless.units_per_meter)
        for i,node in enumerate((start,find,loop,apply,action,cache,budget)):
            node.location=((i%4)*300,-(i//4)*340);node.width=240
        for a,out,dst,inp in ((start,"start",loop,"start"),(find,"bots",loop,"bots"),
            (loop,"each",apply,"run"),(loop,"current",apply,"bot"),(action,"action",apply,"action"),
            (cache,"pool",action,"cache"),(budget,"pool",action,"budget")):
            tree.links.new(_Builder.socket(a,out,True),_Builder.socket(dst,inp))
        if context.area and context.area.type=="NODE_EDITOR":
            context.space_data.node_tree=tree
        return {"FINISHED"}


def register():bpy.utils.register_class(HL_OT_shared_shade_scan)
def unregister():bpy.utils.unregister_class(HL_OT_shared_shade_scan)
