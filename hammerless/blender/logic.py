"""L4D2 Logic: a node editor for map events (like the shader editor, for game logic).

Event wires carry "when this happens, do that". Nodes stand for scene entities (sockets come
from the game's own entity definitions) or for small logic pieces (delay, counter, volume,
message...). core/logic.py turns a graph into entities and I/O connections at export.
"""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

from ..core import fgd
from ..core.logic import LLink, LNode, compile_graph

TREE = "HL_LogicTree"
EVENT_COLOR = (1.0, 0.62, 0.15, 1.0)


class HL_LogicTree(bpy.types.NodeTree):
    """Map logic: events, objectives and director control"""
    bl_idname = TREE
    bl_label = "L4D2 Logic"
    bl_icon = "NODETREE"


class HL_EventSocket(bpy.types.NodeSocket):
    """An event: an output fires it, an input does something when it arrives"""
    bl_idname = "HL_EventSocket"
    bl_label = "Event"

    value: StringProperty(name="Value", description="Value sent with this input (e.g. 0.5 for SetPosition)")
    takes_value: BoolProperty(default=False)
    tip: StringProperty(default="")

    def draw(self, context, layout, node, text):
        if self.is_output or not self.takes_value:
            layout.label(text=text)
        else:
            row = layout.row(align=True)
            row.label(text=text)
            row.prop(self, "value", text="")

    def draw_color(self, context, node):
        return EVENT_COLOR

    @classmethod
    def draw_color_simple(cls):
        return EVENT_COLOR


class _Node:
    kind = ""

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE

    def ev_in(self, ident, label, takes_value=False, tip=""):
        s = self.inputs.new("HL_EventSocket", label, identifier=ident)
        s.takes_value, s.tip = takes_value, tip
        return s

    def ev_out(self, ident, label, tip=""):
        s = self.outputs.new("HL_EventSocket", label, identifier=ident)
        s.tip = tip
        return s

    def settings(self) -> dict:
        return {}

    def to_lnode(self, context) -> LNode:
        params = {s.identifier: s.value for s in self.inputs if getattr(s, "takes_value", False) and s.value}
        return LNode(self.name, self.kind, self.settings(), params=params)


def _game_root():
    try:
        from .ops import game_root
        return game_root(bpy.context)
    except Exception:
        return None


def _entity_sockets(classname: str, advanced: bool):
    classes = fgd.load(_game_root())
    ios = [io for io in fgd.resolve(classes, classname) if advanced or io.name not in fgd.PLUMBING]
    return [io for io in ios if io.kind == "input"], [io for io in ios if io.kind == "output"]


def _rebuild_entity_sockets(node, classname: str):
    """Sockets from the game's definition of the class, keeping wires on sockets that remain."""
    ins, outs = _entity_sockets(classname, node.show_all)
    tree = node.id_data
    keep = {(l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
            for l in tree.links if l.from_node == node or l.to_node == node}
    values = {s.identifier: s.value for s in node.inputs if getattr(s, "value", "")}
    node.inputs.clear()
    node.outputs.clear()
    for io in ins:
        s = node.ev_in(io.name, fgd.pretty(io.name), io.takes_value, io.description)
        s.value = values.get(io.name, "")
    for io in outs:
        node.ev_out(io.name, fgd.pretty(io.name), io.description)
    for fn, fs, tn, ts in keep:
        a, b = tree.nodes.get(fn), tree.nodes.get(tn)
        out = next((s for s in a.outputs if s.identifier == fs), None) if a else None
        inp = next((s for s in b.inputs if s.identifier == ts), None) if b else None
        if out and inp:
            tree.links.new(out, inp)


def _object_update(self, context):
    cls = self.target.hammerless.classname if self.target else ""
    self.classname = cls
    _rebuild_entity_sockets(self, cls)
    if self.target:
        self.label = self.target.name


class HL_NodeObject(_Node, bpy.types.Node):
    """An entity in the scene, with all its events from the game (like Object Info)"""
    bl_idname = "HL_NodeObject"
    bl_label = "Object"
    bl_icon = "OBJECT_DATA"
    kind = "OBJECT"

    target: PointerProperty(type=bpy.types.Object, name="Object", update=_object_update)
    classname: StringProperty()
    show_all: BoolProperty(name="All Events", description="Also show the rarely used inputs and outputs",
                           update=lambda self, c: _rebuild_entity_sockets(self, self.classname))

    def draw_buttons(self, context, layout):
        layout.prop(self, "target", text="")
        if self.target and not self.classname:
            layout.label(text="Not an entity: set its Role", icon="ERROR")
        elif self.classname:
            row = layout.row()
            row.label(text=self.classname)
            row.prop(self, "show_all", text="", icon="PLUS")

    def settings(self):
        return {"outputs": [s.identifier for s in self.outputs], "inputs": [s.identifier for s in self.inputs]}

    def to_lnode(self, context):
        n = super().to_lnode(context)
        n.obj = self.target.name if self.target else ""
        return n


class HL_NodeDirector(_Node, bpy.types.Node):
    """The AI Director: hordes, scripted events and its own events"""
    bl_idname = "HL_NodeDirector"
    bl_label = "Director"
    bl_icon = "GHOST_ENABLED"
    kind = "DIRECTOR"
    show_all: BoolProperty(name="All Events", update=lambda self, c: _rebuild_entity_sockets(self, "info_director"))

    def init(self, context):
        _rebuild_entity_sockets(self, "info_director")

    def draw_buttons(self, context, layout):
        layout.prop(self, "show_all", text="All Events", icon="PLUS")

    def settings(self):
        return {"outputs": [s.identifier for s in self.outputs], "inputs": [s.identifier for s in self.inputs]}


class HL_NodeVolume(_Node, bpy.types.Node):
    """A mesh in the scene used as a trigger volume: fires when survivors (or zombies) enter or leave"""
    bl_idname = "HL_NodeVolume"
    bl_label = "Volume"
    bl_icon = "MESH_CUBE"
    kind = "VOLUME"

    target: PointerProperty(type=bpy.types.Object, name="Mesh", poll=lambda self, o: o.type == "MESH",
                            update=lambda self, c: setattr(self, "label", self.target.name if self.target else ""))
    who: EnumProperty(name="Who", default="SURVIVORS", items=[
        ("SURVIVORS", "Survivors", "Survivor players and bots"),
        ("INFECTED", "Infected", "Zombies and infected players"),
        ("EVERYONE", "Everyone", "Anyone")])
    once: BoolProperty(name="Only Once", description="Each event fires only the first time")
    start_disabled: BoolProperty(name="Starts Off", description="Does nothing until it gets Enable")

    def init(self, context):
        self.ev_in("enable", "Enable")
        self.ev_in("disable", "Disable")
        self.ev_out("enter", "On Enter", "Someone walks in")
        self.ev_out("leave", "On Leave", "Someone walks out")
        self.ev_out("first", "On First Enter", "The first one walks in (nobody was inside)")
        self.ev_out("empty", "On Everyone Left", "The last one walks out")
        self.ev_out("all_inside", "On All Survivors Inside", "The whole survivor team is inside")

    def draw_buttons(self, context, layout):
        layout.prop(self, "target", text="")
        layout.prop(self, "who", text="")
        row = layout.row(align=True)
        row.prop(self, "once", toggle=True)
        row.prop(self, "start_disabled", toggle=True)

    def settings(self):
        return {"who": self.who, "once": self.once, "start_disabled": self.start_disabled}

    def to_lnode(self, context):
        n = super().to_lnode(context)
        if self.target:
            from .extract import MaterialResolver, mesh_to_brushes
            s = context.scene.hammerless
            n.obj = self.target.name
            n.brushes = mesh_to_brushes(self.target, context.evaluated_depsgraph_get(), s.units_per_meter,
                                        MaterialResolver(s, None, _Quiet()))
        return n


class _Quiet:
    """A report sink for material lookups while turning a volume into brushes."""
    def __init__(self):
        self.errors, self.warnings, self.info = [], [], []


class HL_NodeMapStart(_Node, bpy.types.Node):
    """Fires once when the map starts"""
    bl_idname = "HL_NodeMapStart"
    bl_label = "Map Start"
    bl_icon = "PLAY"
    kind = "MAP_START"

    def init(self, context):
        self.ev_out("start", "On Map Start")


class HL_NodeDelay(_Node, bpy.types.Node):
    """Waits, then passes the event on"""
    bl_idname = "HL_NodeDelay"
    bl_label = "Delay"
    bl_icon = "TIME"
    kind = "DELAY"
    seconds: FloatProperty(name="Seconds", default=2.0, min=0.0)

    def init(self, context):
        self.ev_in("in", "In")
        self.ev_in("cancel", "Cancel")
        self.ev_out("out", "Out")

    def draw_buttons(self, context, layout):
        layout.prop(self, "seconds")

    def settings(self):
        return {"seconds": self.seconds}


class HL_NodeOnce(_Node, bpy.types.Node):
    """Passes the event on the first time only"""
    bl_idname = "HL_NodeOnce"
    bl_label = "Once"
    bl_icon = "FORWARD"
    kind = "ONCE"

    def init(self, context):
        self.ev_in("in", "In")
        self.ev_out("out", "Out")


class HL_NodeCounter(_Node, bpy.types.Node):
    """Counts events and fires when the count is reached (e.g. after 3 buttons)"""
    bl_idname = "HL_NodeCounter"
    bl_label = "Counter"
    bl_icon = "LINENUMBERS_ON"
    kind = "COUNTER"
    count: IntProperty(name="Count", default=3, min=1)

    def init(self, context):
        self.ev_in("add", "Add One")
        self.ev_in("reset", "Reset")
        self.ev_out("reached", "On Reached")

    def draw_buttons(self, context, layout):
        layout.prop(self, "count")

    def settings(self):
        return {"count": self.count}


class HL_NodeRandom(_Node, bpy.types.Node):
    """Picks one of the connected outputs at random"""
    bl_idname = "HL_NodeRandom"
    bl_label = "Random"
    bl_icon = "MOD_NOISE"
    kind = "RANDOM"

    def init(self, context):
        self.ev_in("pick", "Pick")
        for i in range(1, 5):
            self.ev_out(f"case{i}", f"Option {i}")


class HL_NodeHorde(_Node, bpy.types.Node):
    """Starts a zombie horde (panic event)"""
    bl_idname = "HL_NodeHorde"
    bl_label = "Horde"
    bl_icon = "COMMUNITY"
    kind = "HORDE"

    def init(self, context):
        self.ev_in("start", "Start")
        self.ev_out("finished", "On Finished")


class HL_NodeMessage(_Node, bpy.types.Node):
    """Shows text on the survivors' screens: objectives, hints, warnings"""
    bl_idname = "HL_NodeMessage"
    bl_label = "Show Message"
    bl_icon = "INFO"
    kind = "MESSAGE"
    text: StringProperty(name="Text", default="Find a way through")
    seconds: FloatProperty(name="Seconds", default=6.0, min=0.0, description="0 = until Hide")
    color: bpy.props.FloatVectorProperty(name="Colour", subtype="COLOR", size=3, min=0, max=1, default=(1, 1, 1))

    def init(self, context):
        self.ev_in("show", "Show")
        self.ev_in("hide", "Hide")

    def draw_buttons(self, context, layout):
        layout.prop(self, "text", text="")
        row = layout.row(align=True)
        row.prop(self, "seconds")
        row.prop(self, "color", text="")

    def settings(self):
        return {"text": self.text, "seconds": self.seconds,
                "color": " ".join(str(int(c * 255)) for c in self.color)}


NODE_CLASSES = (HL_NodeObject, HL_NodeDirector, HL_NodeVolume, HL_NodeMapStart, HL_NodeDelay, HL_NodeOnce,
                HL_NodeCounter, HL_NodeRandom, HL_NodeHorde, HL_NodeMessage)
CATEGORIES = [
    ("Events", [HL_NodeMapStart, HL_NodeVolume]),
    ("Scene", [HL_NodeObject, HL_NodeDirector]),
    ("Flow", [HL_NodeDelay, HL_NodeOnce, HL_NodeCounter, HL_NodeRandom]),
    ("Actions", [HL_NodeHorde, HL_NodeMessage]),
]


def _category_menu(title, classes):
    def draw(self, context):
        for c in classes:
            op = self.layout.operator("node.add_node", text=c.bl_label, icon=c.bl_icon)
            op.type, op.use_transform = c.bl_idname, True
    return type(f"HL_MT_logic_{title.lower()}", (bpy.types.Menu,), {
        "bl_idname": f"HL_MT_logic_{title.lower()}", "bl_label": title, "draw": draw})


CATEGORY_MENUS = [_category_menu(t, c) for t, c in CATEGORIES]


def _add_menu(self, context):
    if getattr(context.space_data, "tree_type", "") != TREE:
        return
    for m in CATEGORY_MENUS:
        self.layout.menu(m.bl_idname)


# ---------------------------------------------------------------- export

def logic_trees():
    return [t for t in bpy.data.node_groups if t.bl_idname == TREE]


def compile_logic(context, ir, report) -> None:
    """Add every L4D2 Logic graph's entities and connections to the map."""
    for tree in logic_trees():
        nodes = [n.to_lnode(context) for n in tree.nodes if isinstance(n, _Node)]
        volumes = {n.obj for n in nodes if n.kind == "VOLUME" and n.obj}
        if volumes:     # a volume's mesh becomes the trigger, not a solid wall
            ir.brushes = [b for b in ir.brushes if b.source.split(" (part ")[0] not in volumes]
            ir.entities = [e for e in ir.entities if not (e.source.split(" (part ")[0] in volumes
                                                          and e.classname == "func_detail")]
        links = [LLink(l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                 for l in tree.links if l.is_valid and not l.is_muted]
        for p in compile_graph(nodes, links, ir):
            report.warnings.append(f"{tree.name}: {p}")


# ---------------------------------------------------------------- operators

class HL_OT_logic_new(bpy.types.Operator):
    bl_idname = "hammerless.logic_new"
    bl_label = "New Logic Graph"
    bl_description = "Create a logic graph and open it in this editor (or the largest area)"

    def execute(self, context):
        tree = bpy.data.node_groups.new("Map Logic", TREE)
        _show_tree(context, tree)
        return {"FINISHED"}


class HL_OT_logic_from_outputs(bpy.types.Operator):
    bl_idname = "hammerless.logic_from_outputs"
    bl_label = "Graph from Outputs"
    bl_description = ("Move the outputs set on objects (the Outputs lists) into a logic graph as nodes and "
                      "wires, so all your map logic is in one place")

    def execute(self, context):
        tree = next(iter(logic_trees()), None) or bpy.data.node_groups.new("Map Logic", TREE)
        by_name = {}
        for o in context.scene.objects:
            for kv in o.hammerless.keyvalues:
                if kv.key == "targetname" and kv.value:
                    by_name[kv.value] = o
        nodes = {n.target.name: n for n in tree.nodes if isinstance(n, HL_NodeObject) and n.target}
        director = next((n for n in tree.nodes if isinstance(n, HL_NodeDirector)), None)
        col = {"x": 0.0}

        def node_for(obj):
            if obj.name not in nodes:
                n = tree.nodes.new("HL_NodeObject")
                n.target = obj
                n.location = (col["x"], -200.0 * (len(nodes) % 6))
                nodes[obj.name] = n
            return nodes[obj.name]
        moved = 0
        for o in list(context.scene.objects):
            if not o.hammerless.outputs:
                continue
            src = node_for(o)
            keep = []
            for i, out in enumerate(o.hammerless.outputs):
                if out.target == "director":
                    if director is None:
                        director = tree.nodes.new("HL_NodeDirector")
                        director.location = (500.0, 200.0)
                    dst = director
                elif out.target in by_name:
                    dst = node_for(by_name[out.target])
                    dst.location.x = max(dst.location.x, src.location.x + 350)
                else:
                    keep.append(i)
                    continue
                a = next((s for s in src.outputs if s.identifier == out.output), None)
                b = next((s for s in dst.inputs if s.identifier == out.input), None)
                if not (a and b):
                    keep.append(i)
                    continue
                tree.links.new(a, b)
                if out.parameter:
                    b.value = out.parameter
                moved += 1
            for i in reversed(range(len(o.hammerless.outputs))):
                if i not in keep:
                    o.hammerless.outputs.remove(i)
        _show_tree(context, tree)
        self.report({"INFO"}, f"Moved {moved} output(s) into '{tree.name}'")
        return {"FINISHED"}


def _show_tree(context, tree):
    area = context.area if context.area and context.area.type == "NODE_EDITOR" else None
    if area is None:
        area = next((a for a in context.screen.areas if a.type == "NODE_EDITOR"), None)
    if area is not None:
        area.ui_type = TREE
        area.spaces.active.node_tree = tree


CLASSES = (HL_LogicTree, HL_EventSocket) + NODE_CLASSES + tuple(CATEGORY_MENUS) + (
    HL_OT_logic_new, HL_OT_logic_from_outputs)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.NODE_MT_add.append(_add_menu)


def unregister():
    bpy.types.NODE_MT_add.remove(_add_menu)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
