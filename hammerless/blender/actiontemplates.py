"""Editable action assets composed exclusively from ordinary logic nodes."""
import bpy
from .botactions import new_action
from .logicgroups import new_item, sync


class _Builder:
    def __init__(self, name, parameters, channels="", supports="ALL"):
        self.tree = new_action(name)
        self.tree.action_channels = channels
        self.tree.action_supports = supports
        self.entry = next(n for n in self.tree.nodes if n.kind == "BA_ENTRY")
        self.params = next(n for n in self.tree.nodes if n.bl_idname == "HL_NodeGroupInput")
        for uid, label, kind, default in parameters:
            new_item(self.tree, label, kind, False, uid)
        sync(self.tree)
        self.parameters = parameters
        self.index = 0

    def node(self, cls, label, **properties):
        n = self.tree.nodes.new(cls)
        n.label, n.width = label, 220
        n.location = (300 + self.index % 4 * 280, -(self.index // 4) * 300)
        self.index += 1
        for key, value in properties.items():
            setattr(n, key, value)
        return n

    @staticmethod
    def socket(n, key, output=False):
        return next(s for s in (n.outputs if output else n.inputs) if s.identifier == key)

    def wire(self, a, out, b, inp):
        self.tree.links.new(self.socket(a, out, True), self.socket(b, inp))

    def value(self, n, key, value):
        self.socket(n, key).value = value


WAIT_PARAMETERS = (("duration", "Duration (seconds)", "num", 1.0),)
MOVE_PARAMETERS = (("goal", "Destination", "vec", (0, 0, 0)),
                   ("radius", "Arrival Radius", "num", 32.0),
                   ("stall", "Stall After (seconds)", "num", 5.0),
                   ("timeout", "Timeout (seconds)", "num", 30.0))


def wait_action():
    b = _Builder("Wait", WAIT_PARAMETERS)
    clock = b.node("HL_NodeTime", "Current game time")
    duration = b.node("HL_NodeMath", "Clamp negative durations to zero", op="MAXIMUM")
    b.value(duration, "b", 0)
    b.wire(b.params, "duration", duration, "a")
    deadline = b.node("HL_NodeMath", "Completion time", op="ADD")
    b.wire(clock, "value", deadline, "a")
    b.wire(duration, "value", deadline, "b")
    save = b.node("HL_BAStateSet", "Remember deadline", state_name="deadline", value_type="num")
    b.wire(deadline, "value", save, "value")
    b.wire(b.entry, "start", save, "run")
    read = b.node("HL_BAStateGet", "Saved deadline", state_name="deadline", value_type="num")
    reached = b.node("HL_NodeCompare", "Deadline reached", op="GREATER_EQUAL")
    b.wire(clock, "value", reached, "a")
    b.wire(read, "value", reached, "b")
    check = b.node("HL_NodeIf", "Finished waiting?")
    b.wire(reached, "result", check, "condition")
    b.wire(save, "next", check, "in")
    b.wire(b.entry, "update", check, "in")
    done = b.node("HL_BAResult", "Wait completed", result="SUCCESS")
    b.wire(check, "true", done, "run")
    return b.tree


def move_action():
    b = _Builder("Move to Position (experimental)", MOVE_PARAMETERS, "Movement", "COMMONS")
    observe = b.node("HL_BAMovement", "Measure arrival and movement progress")
    for source, target in (("goal", "goal"), ("radius", "radius"), ("stall", "stall_seconds"), ("timeout", "timeout")):
        b.wire(b.params, source, observe, target)
    b.wire(b.entry, "start", observe, "run")
    b.wire(b.entry, "update", observe, "run")
    arrival = b.node("HL_NodeIf", "Already at destination?")
    b.wire(observe, "arrived", arrival, "condition")
    b.wire(observe, "next", arrival, "in")
    success = b.node("HL_BAResult", "Destination reached", result="SUCCESS")
    b.wire(arrival, "true", success, "run")
    stopped = b.node("HL_NodeBoolMath", "Stalled or timed out", op="OR")
    b.wire(observe, "stalled", stopped, "a")
    b.wire(observe, "timed_out", stopped, "b")
    failure = b.node("HL_NodeIf", "Unable to reach destination?")
    b.wire(stopped, "result", failure, "condition")
    b.wire(arrival, "false", failure, "in")
    failed = b.node("HL_BAResult", "Movement failed", result="FAILED")
    b.wire(failure, "true", failed, "run")
    cooldown = b.node("HL_BACooldown", "Refresh command at most twice a second")
    b.value(cooldown, "seconds", .5)
    b.wire(failure, "false", cooldown, "run")
    command = b.node("HL_BACommand", "Ask common infected to move", command="MOVE")
    b.wire(b.params, "goal", command, "destination")
    b.wire(cooldown, "passed", command, "run")
    owned = b.node("HL_BAStateSet", "Remember our movement command", state_name="movement_owned", value_type="bool")
    b.value(owned, "value", True)
    b.wire(command, "next", owned, "run")
    was_owned = b.node("HL_BAStateGet", "Did we issue a movement command?", state_name="movement_owned", value_type="bool")
    cleanup = b.node("HL_NodeIf", "Release only our own command")
    b.wire(was_owned, "value", cleanup, "condition")
    b.wire(b.entry, "stop", cleanup, "in")
    reset = b.node("HL_BACommand", "Return control to native AI", command="RESET")
    b.wire(cleanup, "true", reset, "run")
    return b.tree


class HL_OT_action_template(bpy.types.Operator):
    bl_idname, bl_label = "hammerless.action_template", "Add Editable Action"
    bl_description = "Create an editable action group and plug its Action output into an Apply, Priority, or Sequence node"
    bl_options = {"REGISTER", "UNDO"}
    template: bpy.props.EnumProperty(items=[("WAIT", "Wait", "Finish after a duration"),
        ("MOVE", "Move to Position (experimental)", "Commons only; native AI may interrupt movement. Straight-line progress can mark detours as stalled")])

    @classmethod
    def poll(cls, context):
        tree = getattr(getattr(context, "space_data", None), "edit_tree", None)
        return tree is not None and tree.bl_idname == "HL_LogicTree" and not tree.is_group

    def execute(self, context):
        tree = context.space_data.edit_tree
        asset = wait_action() if self.template == "WAIT" else move_action()
        for n in tree.nodes:
            n.select = False
        node = tree.nodes.new("HL_BAAction")
        node.group = asset
        node.location = context.space_data.cursor_location
        for uid, label, kind, default in (WAIT_PARAMETERS if self.template == "WAIT" else MOVE_PARAMETERS):
            _Builder.socket(node, uid).value = default
        node.select = True
        tree.nodes.active = node
        return {"FINISHED"}


def draw_menu(layout):
    layout.operator(HL_OT_action_template.bl_idname, text="Wait Action").template = "WAIT"
    layout.operator(HL_OT_action_template.bl_idname, text="Move to Position (experimental)").template = "MOVE"


def register():
    bpy.utils.register_class(HL_OT_action_template)


def unregister():
    bpy.utils.unregister_class(HL_OT_action_template)
