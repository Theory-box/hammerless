"""Typed shared work budgets, with consumable action-owned grants."""
import bpy
from bpy.props import IntProperty
from .logic import _Block
from .botactions import _Wire


class HL_WorkBudgetSocket(_Wire, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_WorkBudgetSocket", "Work Budget Pool"


class HL_WorkGrantSocket(_Wire, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_WorkGrantSocket", "Work Grant"


class HL_WorkBudgetPool(_Block, bpy.types.Node):
    """Wire this same pool to actions that should share a candidate-entry budget. The limit applies per bot-action scheduler cycle, not per rendered frame or millisecond. Separate pool nodes have independent limits. Requests take turns; newly queued work waits until a later eligible update."""
    bl_idname, bl_label, kind = "HL_WorkBudgetPool", "Work Budget Pool", "WB_POOL"
    limit: IntProperty(name="Entries Per Cycle", default=128, min=1, max=4096,
                       description="Maximum entry credits reserved by this pool in one scheduler cycle; includes every action wired to this pool")

    def make_sockets(self): self.data_out("work_budget", "pool", "Pool")
    def settings(self): return {"limit": self.limit}
    def draw_buttons(self, context, layout): layout.prop(self, "limit")


class HL_RequestWorkGrant(_Block, bpy.types.Node):
    """Inside an action, request candidate-entry credits for the next eligible update. Connect Grant to Evaluate Candidates in Steps. Credits are shared and consumed centrally, expire this scheduler cycle, and belong to this action. New requests wait; zero Requested Entries or Enabled=false cancels. Call each update to remain queued. Granted/Remaining are observations, not spendable numeric permissions."""
    bl_idname, bl_label, kind = "HL_RequestWorkGrant", "Request Work Grant", "WB_REQUEST"

    def make_sockets(self):
        self.ev_in("run", "Run"); self.ev_out("next", "Next")
        self.data_in("work_budget", "pool", "Pool")
        self.data_in("num", "wanted", "Requested Entries").value = 8
        self.data_in("bool", "enabled", "Enabled").value = True
        self.data_out("work_grant", "grant", "Grant")
        for key, label in (("granted", "Granted Entries"), ("remaining", "Remaining at Request"), ("cycle", "Scheduler Cycle")):
            self.data_out("num", key, label)
        self.data_out("bool", "waiting", "Waiting")
        self.data_out("bool", "queued", "Queued")
        self.data_out("text", "error", "Error")


NODES = (HL_WorkBudgetPool, HL_RequestWorkGrant)
class HL_MT_workbudgets(bpy.types.Menu):
    bl_idname, bl_label = "HL_MT_workbudgets", "Work Budgets"
    def draw(self, context):
        for cls in NODES:
            op = self.layout.operator("node.add_node", text=cls.bl_label)
            op.type = cls.bl_idname; op.use_transform = True


CLASSES = (HL_WorkBudgetSocket, HL_WorkGrantSocket) + NODES + (HL_MT_workbudgets,)


@bpy.app.handlers.persistent
def repair_candidate_sockets(*_args):
    """Add optional sockets to saved nodes without rebuilding existing links."""
    for tree in bpy.data.node_groups:
        if tree.bl_idname != "HL_LogicTree" or tree.library is not None:
            continue
        for node in tree.nodes:
            if node.bl_idname != "HL_StepCandidates":
                continue
            if not any(s.identifier == "grant" for s in node.inputs):
                node.data_in("work_grant", "grant", "Shared Work Grant (optional)")
            if not any(s.identifier == "waiting" for s in node.outputs):
                node.data_out("bool", "waiting", "Waiting for Grant")
    return None


def register():
    from . import logic
    logic.SOCKET_OF_KIND.update(work_budget="HL_WorkBudgetSocket", work_grant="HL_WorkGrantSocket")
    logic.DATA_SOCKETS += ("HL_WorkBudgetSocket", "HL_WorkGrantSocket")
    for cls in CLASSES: bpy.utils.register_class(cls)
    bpy.app.handlers.load_post.append(repair_candidate_sockets)
    bpy.app.timers.register(repair_candidate_sockets, first_interval=0.1)

def unregister():
    from . import logic
    if bpy.app.timers.is_registered(repair_candidate_sockets):
        bpy.app.timers.unregister(repair_candidate_sockets)
    if repair_candidate_sockets in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(repair_candidate_sockets)
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
    for key in ("work_budget", "work_grant"): logic.SOCKET_OF_KIND.pop(key, None)
    logic.DATA_SOCKETS = tuple(s for s in logic.DATA_SOCKETS if s not in {"HL_WorkBudgetSocket", "HL_WorkGrantSocket"})
