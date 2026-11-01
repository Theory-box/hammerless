"""Public opt-in observations usable inside editable behavior groups."""
import bpy
from bpy.props import EnumProperty
from .logic import _Block, _rebuild

def changed(self, context):
    _rebuild(self)

class _Diagnostic(_Block):
    def log_sockets(self):
        self.data_in("bool", "enabled", "Enable Logging").value = False
        self.data_in("text", "label", "Label").value = "Watch"
        self.data_in("num", "interval", "Minimum Interval (s)").value = 1.0
        self.data_out("bool", "logged", "Logged")
        self.data_out("bool", "suppressed", "Suppressed")
        self.data_out("text", "reason", "Log Result")

class HL_DebugWatch(_Diagnostic, bpy.types.Node):
    """Print one labeled value to the game console when enabled. Disabled by default; its incoming value expression is not evaluated while disabled (earlier Run nodes still execute). Interval clamps to 0.1–300 seconds; all watches share a 20-lines/second cap. Suppressed and Log Result explain skipped logs. State is separate for each action instance; ordinary graphs share it per node. Bot loops throttle per loop, bot index and node using a shared 256-entry cache that expires after 301 seconds."""
    bl_idname, bl_label, kind = "HL_DebugWatch", "Debug Watch", "DBG_WATCH"
    value_type: EnumProperty(name="Value Type", items=[(k,l,"") for k,l in
        (("num","Number"),("bool","Boolean"),("text","Text"),("vec","Position"),
         ("thing","Entity"),("bot","Bot"),("navarea","Nav Area"))], default="num", update=changed)
    def make_sockets(self):
        self.ev_in("run", "Run"); self.ev_out("next", "Next")
        self.data_in(self.value_type,"value","Value")
        self.log_sockets()
    def settings(self): return {"type":self.value_type}
    def draw_buttons(self,context,layout): layout.prop(self,"value_type",text="")

class HL_DebugCheck(_Diagnostic, bpy.types.Node):
    """Branch on Condition every time Run fires. Optional logging prints failed conditions only, without throwing or stopping the action. Logging is disabled by default; branches work regardless. Logs are throttled per node/action and globally; Log Result explains suppression."""
    bl_idname, bl_label, kind = "HL_DebugCheck", "Check Condition", "DBG_CHECK"
    def make_sockets(self):
        self.ev_in("run","Run"); self.ev_out("true","Passed"); self.ev_out("false","Failed")
        self.data_in("bool","condition","Condition")
        self.data_out("bool","passed","Passed")
        self.log_sockets()

class HL_InspectAction(_Block,bpy.types.Node):
    """Capture this action instance's status and cleanup flag. Only valid inside an action lifecycle; does not inspect other bots or child actions. Step Count includes Start and subsequent Update calls, not elapsed seconds. Status during Stop can still be RUNNING when interrupted; Stopping identifies cleanup. Inspect child outcomes using Step Child Action's outputs."""
    bl_idname, bl_label, kind = "HL_InspectAction", "Inspect Own Action", "BA_INSPECT"
    def make_sockets(self):
        self.ev_in("run","Run"); self.ev_out("next","Next")
        self.data_out("text","status","Status")
        for key,label in (("running","Running"),("success","Succeeded"),("failed","Failed"),("stopping","Stopping")):
            self.data_out("bool",key,label)
        self.data_out("num","updates","Step Count")

NODES=(HL_DebugWatch,HL_DebugCheck,HL_InspectAction)
class HL_MT_diagnostics(bpy.types.Menu):
    bl_idname, bl_label = "HL_MT_diagnostics", "Diagnostics"
    def draw(self,context):
        for cls in NODES:
            op=self.layout.operator("node.add_node",text=cls.bl_label)
            op.type,op.use_transform=cls.bl_idname,True
CLASSES=NODES+(HL_MT_diagnostics,)
def register():
    for cls in CLASSES: bpy.utils.register_class(cls)
def unregister():
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
