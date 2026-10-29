"""Typed bot loops and editable, owned action assets."""
import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty, PointerProperty, BoolProperty
from .logic import _Block, _rebuild, TREE

KINDS = [(k, label, "") for k, label in (("SPECIALS", "Special Infected Bots"),
         ("SURVIVORS", "Survivor Bots"), ("COMMONS", "Common Infected"), ("ALL", "All Bots"))]
TYPES = [(k, label, "") for k, label in (("num", "Number"), ("bool", "Boolean"),
         ("text", "Text"), ("vec", "Vector"), ("thing", "Entity"))]


class _Wire:
    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return self.draw_color_simple()

    @classmethod
    def draw_color_simple(cls):
        return (0.25, 0.75, 0.65, 1.0)


class HL_BotSocket(_Wire, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_BotSocket", "Bot"


class HL_BotListSocket(_Wire, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_BotListSocket", "Bot List"


class HL_BotActionSocket(_Wire, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_BotActionSocket", "Bot Action"


def changed(self, context):
    _rebuild(self)


class HL_BAFind(_Block, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BAFind", "Find Bots", "BA_FIND"
    selection: EnumProperty(name="Find", items=KINDS, default="SPECIALS")

    def make_sockets(self):
        self.data_in("vec", "origin", "Around Position")
        self.data_in("num", "radius", "Radius (0 = All)")
        self.data_out("bots", "bots", "Bots")

    def settings(self):
        return {"kind": self.selection}

    def draw_buttons(self, context, layout):
        layout.prop(self, "selection", text="")


class HL_BALoop(_Block, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BALoop", "Bot Action Loop", "BA_LOOP"

    def make_sockets(self):
        self.ev_in("start", "Start")
        self.ev_in("stop", "Stop")
        self.data_in("bots", "bots", "Bots")
        self.data_in("num", "interval", "Interval (seconds)").value = 0.1
        self.ev_out("each", "Each Bot")
        self.data_out("bot", "current", "Current Bot")
        self.ev_out("after", "After Update")


class HL_BAApply(_Block, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BAApply", "Apply Bot Action", "BA_APPLY"

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("bot", "bot", "Bot")
        self.data_in("action", "action", "Action")
        self.data_in("bool", "enabled", "Enabled").value = True
        self.ev_out("next", "Next")


def action_poll(self, tree):
    return tree.bl_idname == TREE and getattr(tree, "bot_action", False) and tree != self.id_data


class HL_BAAction(_Block, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BAAction", "Bot Action Group", "BA_ACTION"
    group: PointerProperty(type=bpy.types.NodeTree, name="Action", poll=action_poll, update=changed)

    def make_sockets(self):
        if self.group:
            for it in self.group.group_items:
                if not it.is_output and it.kind in {x[0] for x in TYPES}:
                    self.data_in(it.kind, it.uid, it.name)
        self.data_out("action", "action", "Action")

    def draw_buttons(self, context, layout):
        layout.prop(self, "group", text="")
        if self.group:
            op = layout.operator("hammerless.logic_group_edit", text="Edit Action", icon="NODETREE")
            op.node_name = self.name
        else:
            op = layout.operator("hammerless.bot_action_new", text="New Action", icon="ADD")
            op.node_name = self.name

    def to_lnode(self, context):
        from types import SimpleNamespace
        from .logicgroups import flatten
        from ..core.logic import LNode, LLink
        if not self.group or not self.group.bot_action:
            raise ValueError(f"{self.name}: choose a Bot Action Group")
        todo, seen = [self.group], set()
        while todo:
            tree = todo.pop()
            if tree.name in seen:
                continue
            seen.add(tree.name)
            for n in tree.nodes:
                if getattr(n, "kind", "") == "BA_ACTION":
                    raise ValueError("Actions cannot contain other action assets; use ordinary groups for shared logic")
                if getattr(n, "kind", "") == "GROUP" and n.group:
                    todo.append(n.group)
        report = SimpleNamespace(warnings=[])
        flat = flatten(context, self.group, report)
        if report.warnings:
            raise ValueError("; ".join(report.warnings))
        for it in self.group.group_items:
            if it.is_output or it.kind not in {x[0] for x in TYPES}:
                raise ValueError("Action groups expose value inputs only; use Action Lifecycle for Start, Update and Stop")
            targets = flat.ins.get(it.uid, [])
            if targets:
                nid = "__action_parameter_" + it.uid
                while any(n.id == nid for n in flat.nodes):
                    nid += "_"
                flat.nodes.append(LNode(nid, "BA_PARAM", {"uid": it.uid, "type": it.kind}))
                for name, socket in targets:
                    flat.links.append(LLink(nid, "value", name, socket, data=True))
        result = super().to_lnode(context)
        defaults = {"num": 0.0, "bool": False, "text": "", "vec": (0, 0, 0), "thing": ""}
        result.settings["definition"] = {
            "nodes": flat.nodes, "links": flat.links,
            "inputs": [(it.uid, it.kind, defaults[it.kind]) for it in self.group.group_items
                       if not it.is_output and it.kind in defaults],
            "supports": self.group.action_supports,
            "channels": [s.strip() for s in self.group.action_channels.split(",") if s.strip()]}
        return result


class _Inside(_Block):
    @classmethod
    def poll(cls, tree):
        return tree.bl_idname == TREE and getattr(tree, "is_group", False)


class HL_BAEntry(_Inside, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BAEntry", "Action Lifecycle", "BA_ENTRY"

    @classmethod
    def poll(cls, tree):
        return tree.bl_idname == TREE and getattr(tree, "bot_action", False)

    def make_sockets(self):
        for ident, label in (("start", "Start"), ("update", "Update"), ("stop", "Stop")):
            self.ev_out(ident, label)
        self.data_out("bot", "bot", "Bot")

    def draw_buttons(self, context, layout):
        layout.prop(self.id_data, "action_supports")
        layout.prop(self.id_data, "action_channels")
        layout.label(text="Managed overrides restore on Stop")


class HL_BAOverride(_Inside, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BAOverride", "Override Bot Property", "BA_OVERRIDE"
    property_name: StringProperty(name="Net Property")
    value_type: EnumProperty(name="Type", items=[(s, s.title(), "") for s in ("float", "int", "vector", "flags")], update=changed)
    mask: IntProperty(name="Owned Flag Bits", default=32, min=1)
    restore_on_death: BoolProperty(name="Restore on Death", description="Release this owned property on death too, allowing paused AI and animation to finish death processing")

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("vec" if self.value_type == "vector" else "int" if self.value_type in {"int", "flags"} else "num", "value", "Value")
        self.ev_out("next", "Next")

    def settings(self):
        return {"property": self.property_name, "type": self.value_type, "mask": self.mask,
                "restore_on_death": self.restore_on_death}

    def draw_buttons(self, context, layout):
        layout.prop(self, "property_name")
        layout.prop(self, "value_type")
        if self.value_type == "flags":
            layout.prop(self, "mask")
        layout.prop(self, "restore_on_death")


class HL_BAParam(_Inside, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BAParam", "Action Parameter", "BA_PARAM"
    uid: StringProperty(name="Parameter", update=changed)
    value_type: EnumProperty(name="Type", items=TYPES, update=changed)

    def make_sockets(self):
        item = self.parameter()
        self.data_out(item.kind if item else self.value_type, "value", item.name if item else "Value")

    def parameter(self):
        return next((it for it in self.id_data.group_items if not it.is_output and it.kind in {x[0] for x in TYPES}
                     and self.uid in (it.uid, it.name)), None)

    def settings(self):
        item = self.parameter()
        return {"uid": item.uid if item else self.uid, "type": item.kind if item else self.value_type}

    def draw_buttons(self, context, layout):
        layout.prop_search(self, "uid", self.id_data, "group_items", text="Input")
        if not self.parameter():
            layout.label(text="Add an input in the Group sidebar")


class _State(_Inside):
    state_name: StringProperty(name="State Name", default="state")
    value_type: EnumProperty(name="Type", items=TYPES, update=changed)

    def settings(self):
        return {"name": self.state_name, "type": self.value_type}

    def draw_buttons(self, context, layout):
        layout.prop(self, "state_name")
        layout.prop(self, "value_type")


class HL_BAStateGet(_State, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BAStateGet", "Get Action State", "BA_STATE_GET"

    def make_sockets(self):
        self.data_in(self.value_type, "default", "Default")
        self.data_out(self.value_type, "value", "Value")


class HL_BAStateSet(_State, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BAStateSet", "Set Action State", "BA_STATE_SET"

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in(self.value_type, "value", "Value")
        self.ev_out("next", "Next")


class HL_BACommand(_Inside, bpy.types.Node):
    bl_idname, bl_label, kind = "HL_BACommand", "Bot Command", "BA_COMMAND"
    command: EnumProperty(name="Command", items=[(s, s.title(), "") for s in ("MOVE", "ATTACK", "RETREAT", "RESET")], update=changed)

    def make_sockets(self):
        self.ev_in("run", "Run")
        if self.command == "MOVE":
            self.data_in("vec", "destination", "Destination")
        if self.command in {"ATTACK", "RETREAT"}:
            self.data_in("thing", "target", "Target")
        self.ev_out("next", "Next")

    def settings(self):
        return {"command": self.command}

    def draw_buttons(self, context, layout):
        layout.prop(self, "command")
        layout.label(text="Player bots only; commands aren't undone")


def new_action(name="Bot Action", preset=False, family="SPECIALS"):
    from .logicgroups import new_group_tree
    tree = new_group_tree(name)
    tree.bot_action = True
    entry = tree.nodes.new("HL_BAEntry")
    inputs = tree.nodes.new("HL_NodeGroupInput")
    inputs.location = (0, -240)
    if preset:
        tree.action_supports, tree.action_channels = family, "Movement"
        move = tree.nodes.new("HL_BAOverride")
        move.property_name, move.value_type = ("m_nNextThinkTick" if family == "COMMONS" else "movetype"), "int"
        move.location = (250, 0)
        flags = tree.nodes.new("HL_BAOverride")
        flags.property_name, flags.value_type, flags.mask = ("m_flPlaybackRate" if family == "COMMONS" else "m_fFlags"), ("float" if family == "COMMONS" else "flags"), 32
        flags.inputs["Value"].value = 0 if family == "COMMONS" else 32
        flags.location = (500, 0)
        tree.links.new(entry.outputs["Start"], move.inputs["Run"])
        tree.links.new(move.outputs["Next"], flags.inputs["Run"])
        if family == "COMMONS":
            move.inputs["Value"].value = -1
            move.restore_on_death = flags.restore_on_death = True
            tree.links.new(entry.outputs["Update"], move.inputs["Run"])
    return tree


def _stock_special_freeze(tree):
    """Recognize only our untouched old preset, never rewrite user-authored actions."""
    if not tree or not tree.name.startswith("Freeze Special Infected (experimental)"):
        return False
    if tree.action_supports != "SPECIALS" or tree.action_channels != "Movement" or tree.group_items:
        return False
    if len(tree.nodes) != 4 or len(tree.links) != 2:
        return False
    entries = [n for n in tree.nodes if n.bl_idname == "HL_BAEntry"]
    inputs = [n for n in tree.nodes if n.bl_idname == "HL_NodeGroupInput"]
    props = {n.property_name: n for n in tree.nodes if n.bl_idname == "HL_BAOverride"}
    if len(entries) != 1 or len(inputs) != 1 or set(props) != {"movetype", "m_fFlags"}:
        return False
    move, flags = props["movetype"], props["m_fFlags"]
    if move.value_type != "int" or move.inputs["Value"].value != 0 or flags.value_type != "flags" or flags.mask != 32 or flags.inputs["Value"].value != 32:
        return False
    if any(n.mute for n in tree.nodes) or move.restore_on_death or flags.restore_on_death:
        return False
    links = {(l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier) for l in tree.links}
    return links == {(entries[0].name, "start", move.name, "run"), (move.name, "next", flags.name, "run")}


@bpy.app.handlers.persistent
def repair_common_examples(*_args):
    """Replace the untouched special preset only where a Commons loop requests it."""
    replacements = {}
    for tree in list(bpy.data.node_groups):
        if tree.bl_idname != TREE:
            continue
        def source(node, socket):
            return next((l.from_node for l in tree.links if l.to_node == node and l.to_socket.identifier == socket), None)
        for apply in list(tree.nodes):
            if apply.bl_idname != "HL_BAApply":
                continue
            loop, action = source(apply, "bot"), source(apply, "action")
            if not loop or loop.bl_idname != "HL_BALoop" or not action or action.bl_idname != "HL_BAAction":
                continue
            query = source(loop, "bots")
            if not query or query.bl_idname != "HL_BAFind" or query.selection != "COMMONS" or not _stock_special_freeze(action.group):
                continue
            old_name = action.group.name
            if old_name not in replacements:
                replacements[old_name] = new_action("Freeze Common Infected", True, "COMMONS")
            # A shared Action node could also feed a Specials loop. Give only
            # this Apply a separate reference rather than changing those users.
            reference = tree.nodes.new("HL_BAAction")
            reference.group = replacements[old_name]
            reference.location = action.location.copy()
            reference.location.y -= 200
            socket = next(s for s in apply.inputs if s.identifier == "action")
            tree.links.new(reference.outputs["Action"], socket)


class HL_OT_bot_action_new(bpy.types.Operator):
    bl_idname, bl_label = "hammerless.bot_action_new", "New Bot Action"
    bl_options = {"REGISTER", "UNDO"}
    node_name: StringProperty()
    preset: BoolProperty(default=False)
    family: EnumProperty(items=KINDS, default="COMMONS")
    group_name: StringProperty()

    def execute(self, context):
        tree = context.space_data.edit_tree
        node = tree.nodes.get(self.node_name) if self.node_name else tree.nodes.new("HL_BAAction")
        if node is None or node.bl_idname != "HL_BAAction":
            return {"CANCELLED"}
        asset = bpy.data.node_groups.get(self.group_name) if self.group_name else None
        if self.group_name and (asset is None or not getattr(asset, "bot_action", False)):
            return {"CANCELLED"}
        title = "Freeze Common Infected" if self.family == "COMMONS" else "Freeze Special Infected (experimental)"
        node.group = asset or new_action(title if self.preset else "Bot Action", self.preset, self.family)
        node.select = True
        tree.nodes.active = node
        return {"FINISHED"}


def watched_example(tree):
    """Editable example; adds nodes only to the explicitly requested tree."""
    offset = max((n.location.x + n.width for n in tree.nodes), default=-920) + 920
    for n in tree.nodes:
        n.select = False
    specs = [("HL_NodeMapStart", (-680, 200)), ("HL_BAFind", (-680, -30)),
             ("HL_BALoop", (-380, 200)), ("HL_NodeSeenBy", (-80, -100)),
             ("HL_BAAction", (-80, -430)), ("HL_BAApply", (240, 200))]
    start, find, loop, seen, action, apply = [tree.nodes.new(kind) for kind, _ in specs]
    for node, (_, position) in zip((start, find, loop, seen, action, apply), specs):
        node.location = position
        node.location.x += offset
        node.width = 220
        node.select = True
    seen.humans_only = True
    find.selection = "COMMONS"
    action.group = new_action("Freeze Common Infected", preset=True, family="COMMONS")
    for src, out, dst, inp in ((start, "On Map Start", loop, "Start"),
                               (find, "Bots", loop, "Bots"),
                               (loop, "Each Bot", apply, "Run"),
                               (loop, "Current Bot", apply, "Bot"),
                               (loop, "Current Bot", seen, "Target"),
                               (seen, "Seen", apply, "Enabled"),
                               (action, "Action", apply, "Action")):
        tree.links.new(src.outputs[out], dst.inputs[inp])
    return loop


class HL_OT_bot_action_example(bpy.types.Operator):
    bl_idname, bl_label = "hammerless.bot_action_example", "Freeze Commons When Watched"
    bl_description = "Add an editable common-infected freeze action that resumes when humans look away"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = context.space_data.edit_tree
        if tree is None or getattr(tree, "is_group", False):
            return {"CANCELLED"}
        watched_example(tree)
        tree.nodes.active = next(n for n in reversed(list(tree.nodes)) if n.select)
        if context.region and context.region.type == "WINDOW":
            bpy.ops.node.view_selected()
        return {"FINISHED"}


class HL_MT_bot_actions(bpy.types.Menu):
    bl_idname, bl_label = "HL_MT_bot_actions", "Bot Actions"

    def draw(self, context):
        inside = getattr(context.space_data.edit_tree, "is_group", False)
        for cls in (INSIDE if inside else OUTSIDE):
            if cls == HL_BAParam:
                continue  # Normal Group Input supplies editable parameters.
            if cls == HL_BAEntry and not getattr(context.space_data.edit_tree, "bot_action", False):
                continue
            op = self.layout.operator("node.add_node", text=cls.bl_label)
            op.type, op.use_transform = cls.bl_idname, True
        if not inside:
            self.layout.separator()
            self.layout.operator("hammerless.bot_action_example", icon="HELP")
            self.layout.operator("hammerless.bot_action_new", text="New Editable Action")
            self.layout.operator("hammerless.bot_action_new", text="Freeze Common Infected").preset = True
            op = self.layout.operator("hammerless.bot_action_new", text="Freeze Special Infected (experimental)")
            op.preset, op.family = True, "SPECIALS"
            for tree in sorted(bpy.data.node_groups, key=lambda t: t.name):
                if getattr(tree, "bot_action", False):
                    self.layout.operator("hammerless.bot_action_new", text=tree.name, icon="NODETREE").group_name = tree.name


OUTSIDE = (HL_BAFind, HL_BALoop, HL_BAApply, HL_BAAction)
INSIDE = (HL_BAEntry, HL_BAParam, HL_BAOverride, HL_BAStateGet, HL_BAStateSet, HL_BACommand)
CLASSES = (HL_BotSocket, HL_BotListSocket, HL_BotActionSocket) + OUTSIDE + INSIDE + (HL_OT_bot_action_new, HL_OT_bot_action_example, HL_MT_bot_actions)


def register():
    from . import logic
    logic.SOCKET_OF_KIND.update(bot="HL_BotSocket", bots="HL_BotListSocket", action="HL_BotActionSocket")
    logic.DATA_SOCKETS += ("HL_BotSocket", "HL_BotListSocket", "HL_BotActionSocket")
    logic.HL_LogicTree.bot_action = BoolProperty(default=False)
    logic.HL_LogicTree.action_supports = EnumProperty(name="Supports", items=KINDS, default="ALL")
    logic.HL_LogicTree.action_channels = StringProperty(name="Owned Channels", description="Comma-separated channels, such as Movement or Appearance")
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_post.append(repair_common_examples)
    # Blender restricts bpy.data while enabling addons. Migrate only after
    # registration finishes (or after a file loads), never inside register().
    if not bpy.app.timers.is_registered(repair_common_examples):
        bpy.app.timers.register(repair_common_examples, first_interval=0.1)


def unregister():
    from . import logic
    if bpy.app.timers.is_registered(repair_common_examples):
        bpy.app.timers.unregister(repair_common_examples)
    if repair_common_examples in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(repair_common_examples)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    for name in ("bot_action", "action_supports", "action_channels"):
        delattr(logic.HL_LogicTree, name)
    for key in ("bot", "bots", "action"):
        logic.SOCKET_OF_KIND.pop(key, None)
    logic.DATA_SOCKETS = tuple(s for s in logic.DATA_SOCKETS if s not in {"HL_BotSocket", "HL_BotListSocket", "HL_BotActionSocket"})
