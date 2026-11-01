"""Public child lifecycle primitive and an editable retry/recovery action."""
import bpy
from .botactions import _Inside
from .actiontemplates import _Builder


class HL_BAChild(_Inside, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BAChild", "Step Child Action", "BA_CHILD"
    bl_description = "Start or update the connected action once per parent callback. Completion stays latched until Reset. Child state is isolated per bot and node; parent cleanup releases it automatically"

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("action", "action", "Action")
        self.data_in("bool", "reset", "Reset Before Step")
        self.data_in("bool", "enabled", "Enabled").value = True
        self.ev_out("next", "Next")
        for key, label in (("idle", "Idle"), ("running", "Running"), ("success", "Succeeded"), ("failed", "Failed")):
            self.data_out("bool", key, label)
        self.data_out("text", "error", "Runtime Error")
        self.data_out("num", "attempt", "Attempt")


RETRY_PARAMETERS = (("task", "Task", "action", None), ("recovery", "Recovery", "action", None),
                    ("attempts", "Maximum Attempts", "num", 3.0))


def retry_action():
    b = _Builder("Retry with Recovery", RETRY_PARAMETERS)
    def state(name):
        return b.node("HL_BAStateGet", name, state_name=name, value_type="bool")
    def save(name, value):
        n=b.node("HL_BAStateSet", name, state_name=name, value_type="bool")
        b.value(n,"value",value)
        return n
    def test(source, output, label):
        n=b.node("HL_NodeIf",label);b.wire(source,output,n,"condition");return n
    phase=test(state("recovering"),"value","Run recovery or task?")
    b.wire(b.entry,"start",phase,"in");b.wire(b.entry,"update",phase,"in")
    task=b.node("HL_BAChild","Run the task")
    recovery=b.node("HL_BAChild","Run recovery before retrying")
    for child,uid,branch in ((task,"task","false"),(recovery,"recovery","true")):
        b.wire(b.params,uid,child,"action");b.wire(phase,branch,child,"run")
        b.wire(state("reset_"+uid),"value",child,"reset")
    clear_task=save("reset_task",False);b.wire(task,"next",clear_task,"run")
    succeeded=test(task,"success","Task succeeded?");b.wire(clear_task,"next",succeeded,"in")
    success=b.node("HL_BAResult","Task completed",result="SUCCESS");b.wire(succeeded,"true",success,"run")
    failed=test(task,"failed","Task failed?");b.wire(succeeded,"false",failed,"in")
    minimum=b.node("HL_NodeMath","At least one attempt",op="MAXIMUM");b.wire(b.params,"attempts",minimum,"a");b.value(minimum,"b",1)
    maximum=b.node("HL_NodeMath","Bound attempts to 1000",op="MINIMUM");b.wire(minimum,"value",maximum,"a");b.value(maximum,"b",1000)
    whole=b.node("HL_NodeMath","Whole attempts",op="FLOOR");b.wire(maximum,"value",whole,"a")
    exhausted=b.node("HL_NodeCompare","Attempts exhausted?",op="GREATER_EQUAL");b.wire(task,"attempt",exhausted,"a");b.wire(whole,"value",exhausted,"b")
    limit=test(exhausted,"result","Retry budget exhausted?");b.wire(failed,"true",limit,"in")
    failure=b.node("HL_BAResult","Task or recovery failed",result="FAILED");b.wire(limit,"true",failure,"run")
    previous=limit;port="false"
    for name in ("recovering","reset_task","reset_recovery"):
        n=save(name,True);b.wire(previous,port,n,"run");previous=n;port="next"
    clear_recovery=save("reset_recovery",False);b.wire(recovery,"next",clear_recovery,"run")
    recovery_failed=test(recovery,"failed","Recovery failed?");b.wire(clear_recovery,"next",recovery_failed,"in");b.wire(recovery_failed,"true",failure,"run")
    recovered=test(recovery,"success","Recovery succeeded?");b.wire(recovery_failed,"false",recovered,"in")
    resume=save("recovering",False);b.wire(recovered,"true",resume,"run")
    return b.tree


class HL_OT_retry_action(bpy.types.Operator):
    bl_idname,bl_label="hammerless.retry_action","Add Retry with Recovery"
    bl_description="Create an editable action group: run Task, run Recovery after failure, then retry up to Maximum Attempts. Recovery failure ends the group. Both Action inputs must be connected"
    bl_options={"REGISTER","UNDO"}
    @classmethod
    def poll(cls,context):
        tree=getattr(getattr(context,"space_data",None),"edit_tree",None)
        return tree is not None and tree.bl_idname=="HL_LogicTree" and not tree.is_group
    def execute(self,context):
        tree=context.space_data.edit_tree
        asset=retry_action()
        node=tree.nodes.new("HL_BAAction");node.group=asset
        _Builder.socket(node,"attempts").value=3
        node.location=context.space_data.cursor_location
        tree.nodes.active=node
        return {"FINISHED"}


def register():
    for cls in (HL_BAChild,HL_OT_retry_action):bpy.utils.register_class(cls)


def unregister():
    for cls in (HL_OT_retry_action,HL_BAChild):bpy.utils.unregister_class(cls)
