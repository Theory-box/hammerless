"""L4D2 Logic: a node editor for map events, objectives and director control (like the shader
editor, for game logic).

Two kinds of wire: orange event wires ("when this happens, do that") and blue object wires
(which scene object a node acts on; an Object Info node supplies one, or pick it on the node).
core/logic.py turns a graph into entities, I/O connections and a small map script at export.
"""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, StringProperty

from ..core import fgd
from ..core.logic import DIRECTOR_FIELDS, GAME_EVENTS, SPAWN_TYPES, ZOMBIE_TYPES, LLink, LNode, compile_graph

SPAWN_KINDS = ("tank", "witch", "smoker", "boomer", "hunter", "charger", "jockey", "spitter")

TREE = "HL_LogicTree"
EVENT_COLOR = (1.0, 0.62, 0.15, 1.0)
OBJECT_COLOR = (0.35, 0.65, 1.0, 1.0)
FLOAT_COLOR = (0.63, 0.63, 0.63, 1.0)       # Blender's colours: grey numbers, pink true/false
BOOL_COLOR = (0.80, 0.65, 0.84, 1.0)
DATA_SOCKETS = ("HL_FloatSocket", "HL_BoolSocket")
CATEGORY_COLORS = {"Events": (0.45, 0.18, 0.16), "Scene": (0.16, 0.33, 0.40), "Flow": (0.25, 0.25, 0.30),
                   "Values": (0.22, 0.22, 0.40),
                   "Actions": (0.18, 0.36, 0.20), "Director": (0.33, 0.20, 0.42), "Objectives": (0.45, 0.38, 0.12)}


class HL_LogicTree(bpy.types.NodeTree):
    """Map logic: events, objectives and director control"""
    bl_idname = TREE
    bl_label = "L4D2 Logic"
    bl_icon = "NODETREE"

    scene_name: StringProperty(name="Scene", description="The scene (map) this graph belongs to: only that map "
                                                         "gets its logic")


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


class HL_ObjectSocket(bpy.types.NodeSocket):
    """A scene object: wire one in from Object Info, or pick it here"""
    bl_idname = "HL_ObjectSocket"
    bl_label = "Object"

    value: PointerProperty(type=bpy.types.Object, name="Object")

    def draw(self, context, layout, node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text)
        else:
            layout.prop(self, "value", text="")

    def draw_color(self, context, node):
        return OBJECT_COLOR

    @classmethod
    def draw_color_simple(cls):
        return OBJECT_COLOR


class HL_FloatSocket(bpy.types.NodeSocket):
    """A number, worked out live in the game (wire it in, or type it here)"""
    bl_idname = "HL_FloatSocket"
    bl_label = "Number"
    value: FloatProperty(name="Value", default=0.0)

    def draw(self, context, layout, node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text)
        else:
            layout.prop(self, "value", text=text)

    def draw_color(self, context, node):
        return FLOAT_COLOR

    @classmethod
    def draw_color_simple(cls):
        return FLOAT_COLOR


class HL_BoolSocket(bpy.types.NodeSocket):
    """True or false, worked out live in the game"""
    bl_idname = "HL_BoolSocket"
    bl_label = "True/False"
    value: BoolProperty(name="Value", default=False)

    def draw(self, context, layout, node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text)
        else:
            layout.prop(self, "value", text=text)

    def draw_color(self, context, node):
        return BOOL_COLOR

    @classmethod
    def draw_color_simple(cls):
        return BOOL_COLOR


class _Node:
    kind = ""
    category = "Flow"

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE

    def init(self, context):
        self.use_custom_color = True
        self.color = CATEGORY_COLORS.get(self.category, (0.25, 0.25, 0.25))
        self.make_sockets()

    def make_sockets(self):
        pass

    def ev_in(self, ident, label, takes_value=False, tip=""):
        s = self.inputs.new("HL_EventSocket", label, identifier=ident)
        s.takes_value, s.tip = takes_value, tip
        return s

    def ev_out(self, ident, label, tip=""):
        s = self.outputs.new("HL_EventSocket", label, identifier=ident)
        s.tip = tip
        return s

    def obj_in(self, ident, label):
        return self.inputs.new("HL_ObjectSocket", label, identifier=ident)

    def num_in(self, ident, label, default=0.0):
        s = self.inputs.new("HL_FloatSocket", label, identifier=ident)
        s.value = default
        return s

    def num_out(self, ident, label):
        return self.outputs.new("HL_FloatSocket", label, identifier=ident)

    def bool_in(self, ident, label, default=False):
        s = self.inputs.new("HL_BoolSocket", label, identifier=ident)
        s.value = default
        return s

    def bool_out(self, ident, label):
        return self.outputs.new("HL_BoolSocket", label, identifier=ident)

    def object_from(self, ident):
        """The object wired into (or picked on) an object socket."""
        sock = next((s for s in self.inputs if s.identifier == ident), None)
        if sock is None:
            return None
        if sock.is_linked:
            found = _sources(sock)
            return getattr(found[0][0], "target", None) if found else None
        return sock.value

    def settings(self) -> dict:
        return {}

    def to_lnode(self, context) -> LNode:
        params = {s.identifier: s.value for s in self.inputs if getattr(s, "takes_value", False) and s.value}
        consts = {s.identifier: s.value for s in self.inputs if s.bl_idname in DATA_SOCKETS}
        return LNode(self.name, self.kind, self.settings(), params=params, consts=consts)

    def with_object(self, context, n: LNode, ident="object", position=False) -> LNode:
        obj = self.object_from(ident)
        if obj is not None:
            n.obj = obj.name
            if position:
                n.pos = tuple(c * context.scene.hammerless.units_per_meter for c in obj.matrix_world.translation)
        return n


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


def _label_from_target(self, context):
    self.label = self.target.name if self.target else ""


# ---------------------------------------------------------------- Events

class HL_NodeMapStart(_Node, bpy.types.Node):
    """Fires once when the map starts"""
    bl_idname, bl_label, bl_icon = "HL_NodeMapStart", "Map Start", "PLAY"
    kind, category = "MAP_START", "Events"

    def make_sockets(self):
        self.ev_out("start", "On Map Start")


class HL_NodeGameEvent(_Node, bpy.types.Node):
    """Something that happens in the game: survivors leave the safe room, a Tank appears, a witch dies..."""
    bl_idname, bl_label, bl_icon = "HL_NodeGameEvent", "Game Event", "LIGHT_SUN"
    kind, category = "GAME_EVENT", "Events"
    event: EnumProperty(name="Event", items=[(k, v[0], v[0]) for k, v in GAME_EVENTS.items()],
                        update=lambda self, c: setattr(self, "label", GAME_EVENTS[self.event][0]))
    once: BoolProperty(name="Only Once", description="Fires the first time only")

    def make_sockets(self):
        self.ev_out("happened", "On Event")
        self.label = GAME_EVENTS[self.event][0]

    def draw_buttons(self, context, layout):
        layout.prop(self, "event", text="")
        layout.prop(self, "once", toggle=True)

    def settings(self):
        return {"event": self.event, "once": self.once}


class HL_NodeVolume(_Node, bpy.types.Node):
    """A mesh in the scene used as a trigger volume: fires when survivors (or zombies) enter or leave"""
    bl_idname, bl_label, bl_icon = "HL_NodeVolume", "Volume", "MESH_CUBE"
    kind, category = "VOLUME", "Events"
    who: EnumProperty(name="Who", default="SURVIVORS", items=[
        ("SURVIVORS", "Survivors", "Survivor players and bots"),
        ("INFECTED", "Infected", "Zombies and infected players"),
        ("EVERYONE", "Everyone", "Anyone")])
    once: BoolProperty(name="Only Once", description="Each event fires only the first time")
    start_disabled: BoolProperty(name="Starts Off", description="Does nothing until it gets Enable")

    def make_sockets(self):
        self.obj_in("object", "Mesh")
        self.ev_in("enable", "Enable")
        self.ev_in("disable", "Disable")
        self.ev_out("enter", "On Enter", "Someone walks in")
        self.ev_out("leave", "On Leave", "Someone walks out")
        self.ev_out("first", "On First Enter", "The first one walks in (nobody was inside)")
        self.ev_out("empty", "On Everyone Left", "The last one walks out")
        self.ev_out("all_inside", "On All Survivors Inside", "The whole survivor team is inside")

    def draw_buttons(self, context, layout):
        layout.prop(self, "who", text="")
        row = layout.row(align=True)
        row.prop(self, "once", toggle=True)
        row.prop(self, "start_disabled", toggle=True)

    def settings(self):
        return {"who": self.who, "once": self.once, "start_disabled": self.start_disabled}

    def to_lnode(self, context):
        n = self.with_object(context, super().to_lnode(context))
        obj = self.object_from("object")
        if obj is not None and obj.type == "MESH":
            from .extract import MaterialResolver, mesh_to_brushes
            s = context.scene.hammerless
            n.brushes = mesh_to_brushes(obj, context.evaluated_depsgraph_get(), s.units_per_meter,
                                        MaterialResolver(s, None, _Quiet()))
        return n


class _Quiet:
    """A report sink for material lookups while turning a volume into brushes."""
    def __init__(self):
        self.errors, self.warnings, self.info = [], [], []


class HL_NodeButton(_Node, bpy.types.Node):
    """Turns a mesh into a button survivors press (or hold) with the use key"""
    bl_idname, bl_label, bl_icon = "HL_NodeButton", "Button", "RADIOBUT_ON"
    kind, category = "BUTTON", "Events"
    once: BoolProperty(name="Only Once", default=True, description="Can be used once, then stays pressed")
    reset: FloatProperty(name="Reset After", default=1.0, min=0.0, description="Seconds until it can be pressed again")
    hold: FloatProperty(name="Hold For", default=0.0, min=0.0,
                        description="Seconds survivors hold the use key, with a progress bar (0 = a quick press)")
    text: StringProperty(name="Hold Text", default="Opening...", description="Shown while holding")

    def make_sockets(self):
        self.obj_in("object", "Mesh")
        self.ev_in("lock", "Lock")
        self.ev_in("unlock", "Unlock")
        self.ev_out("pressed", "On Press", "Pressed (for Hold For: held until the bar is full)")
        self.ev_out("started", "On Start Holding", "Hold For buttons: someone starts holding")

    def draw_buttons(self, context, layout):
        layout.prop(self, "once", toggle=True)
        if not self.once:
            layout.prop(self, "reset")
        layout.prop(self, "hold")
        if self.hold > 0:
            layout.prop(self, "text", text="")

    def settings(self):
        return {"once": self.once, "reset": self.reset, "hold": self.hold, "text": self.text}

    def to_lnode(self, context):
        return self.with_object(context, super().to_lnode(context))


class HL_NodePathProgress(_Node, bpy.types.Node):
    """Fires when the survivors get this far along the path to the end safe room: at a random point
    between From and To, picked each game (From = To for a fixed point)"""
    bl_idname, bl_label, bl_icon = "HL_NodePathProgress", "Reach Random Point (old)", "TRACKING_FORWARDS"
    kind, category = "PATH_PROGRESS", "Events"
    lo: FloatProperty(name="From", default=0.2, min=0.0, max=1.0, subtype="FACTOR",
                      description="Earliest point (0 = start safe room, 1 = end safe room)")
    hi: FloatProperty(name="To", default=0.8, min=0.0, max=1.0, subtype="FACTOR",
                      description="Latest point; a random point between From and To is picked each game")

    def make_sockets(self):
        self.ev_out("reached", "On Reached")

    def draw_buttons(self, context, layout):
        col = layout.column(align=True)
        col.prop(self, "lo", slider=True)
        col.prop(self, "hi", slider=True)

    def settings(self):
        return {"from": self.lo, "to": self.hi}


class HL_NodeTimer(_Node, bpy.types.Node):
    """Fires again and again: every N seconds, or at random times between two values"""
    bl_idname, bl_label, bl_icon = "HL_NodeTimer", "Timer", "TIME"
    kind, category = "TIMER", "Events"
    seconds: FloatProperty(name="Every", default=10.0, min=0.1)
    max_seconds: FloatProperty(name="Up To", default=0.0, min=0.0, description="Random time up to this (0 = exact)")
    running: BoolProperty(name="Running", default=True, description="Starts running with the map")

    def make_sockets(self):
        self.ev_in("start", "Start")
        self.ev_in("stop", "Stop")
        self.ev_in("now", "Fire Now")
        self.ev_out("tick", "On Tick")

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "seconds")
        row.prop(self, "max_seconds")
        layout.prop(self, "running", toggle=True)

    def settings(self):
        return {"seconds": self.seconds, "max_seconds": self.max_seconds, "running": self.running}


# ---------------------------------------------------------------- Scene

class HL_NodeObjectInfo(_Node, bpy.types.Node):
    """Picks a scene object to hand to other nodes (Move Over Time, Spawn at, Teleport to...)"""
    bl_idname, bl_label, bl_icon = "HL_NodeObjectInfo", "Object Info", "OBJECT_DATA"
    kind, category = "OBJECT_INFO", "Scene"
    target: PointerProperty(type=bpy.types.Object, name="Object", update=_label_from_target)

    def make_sockets(self):
        self.outputs.new("HL_ObjectSocket", "Object", identifier="object")

    def draw_buttons(self, context, layout):
        layout.prop(self, "target", text="")


def _object_update(self, context):
    cls = self.target.hammerless.classname if self.target and self.target.hammerless.role in (
        "ENTITY", "BRUSH_ENTITY") else ""
    self.classname = cls
    _rebuild_entity_sockets(self, cls)
    _label_from_target(self, context)


class HL_NodeObject(_Node, bpy.types.Node):
    """An entity in the scene with all its events from the game (for anything the other nodes don't cover)"""
    bl_idname, bl_label, bl_icon = "HL_NodeObject", "Entity Events", "OUTLINER_DATA_EMPTY"
    kind, category = "OBJECT", "Scene"
    target: PointerProperty(type=bpy.types.Object, name="Object", update=_object_update)
    classname: StringProperty()
    show_all: BoolProperty(name="All Events", description="Also show the rarely used inputs and outputs",
                           update=lambda self, c: _rebuild_entity_sockets(self, self.classname))

    def draw_buttons(self, context, layout):
        layout.prop(self, "target", text="")
        if self.target and not self.classname:
            col = layout.column(align=True)
            col.label(text="Plain mesh, no entity events.", icon="INFO")
            col.label(text="Use Button / Move / Show-Hide on it")
        elif self.classname:
            row = layout.row()
            row.label(text=self.classname)
            row.prop(self, "show_all", text="", icon="PLUS")
            if not self.inputs and not self.outputs:
                layout.operator("hammerless.logic_refresh_node", icon="FILE_REFRESH").node = self.name

    def settings(self):
        return {"outputs": [s.identifier for s in self.outputs], "inputs": [s.identifier for s in self.inputs]}

    def to_lnode(self, context):
        n = super().to_lnode(context)
        n.obj = self.target.name if self.target else ""
        return n


# ---------------------------------------------------------------- Flow

class HL_NodeSequence(_Node, bpy.types.Node):
    """Does several things in order, optionally a few seconds apart"""
    bl_idname, bl_label, bl_icon = "HL_NodeSequence", "Sequence", "LINENUMBERS_ON"
    kind, category = "SEQUENCE", "Flow"
    seconds: FloatProperty(name="Seconds Between", default=0.0, min=0.0)

    def make_sockets(self):
        self.ev_in("in", "Run")
        for i in range(1, 5):
            self.ev_out(f"then{i}", f"Then {i}")

    def draw_buttons(self, context, layout):
        layout.prop(self, "seconds")

    def settings(self):
        return {"seconds": self.seconds}


class HL_NodeDelay(_Node, bpy.types.Node):
    """Waits, then passes the event on"""
    bl_idname, bl_label, bl_icon = "HL_NodeDelay", "Delay", "TIME"
    kind, category = "DELAY", "Flow"
    seconds: FloatProperty(name="Seconds", default=2.0, min=0.0)

    def make_sockets(self):
        self.ev_in("in", "In")
        self.ev_in("cancel", "Cancel")
        self.ev_out("out", "Out")

    def draw_buttons(self, context, layout):
        layout.prop(self, "seconds")

    def settings(self):
        return {"seconds": self.seconds}


class HL_NodeOnce(_Node, bpy.types.Node):
    """Passes the event on the first time only"""
    bl_idname, bl_label, bl_icon = "HL_NodeOnce", "Once", "FORWARD"
    kind, category = "ONCE", "Flow"

    def make_sockets(self):
        self.ev_in("in", "In")
        self.ev_out("out", "Out")


class HL_NodeGate(_Node, bpy.types.Node):
    """Lets events through only while it's open"""
    bl_idname, bl_label, bl_icon = "HL_NodeGate", "Gate", "UNLOCKED"
    kind, category = "GATE", "Flow"
    open: BoolProperty(name="Starts Open", default=True)

    def make_sockets(self):
        self.ev_in("in", "In")
        self.ev_in("open", "Open")
        self.ev_in("close", "Close")
        self.ev_out("out", "Out")

    def draw_buttons(self, context, layout):
        layout.prop(self, "open", toggle=True)

    def settings(self):
        return {"open": self.open}


class HL_NodeBranch(_Node, bpy.types.Node):
    """Remembers true or false; Test sends the event one way or the other"""
    bl_idname, bl_label, bl_icon = "HL_NodeBranch", "Branch", "DECORATE_KEYFRAME"
    kind, category = "BRANCH", "Flow"
    initial: BoolProperty(name="Starts True", default=False)

    def make_sockets(self):
        self.ev_in("test", "Test")
        self.ev_in("set_true", "Set True")
        self.ev_in("set_false", "Set False")
        self.ev_in("toggle", "Toggle")
        self.ev_out("true", "If True")
        self.ev_out("false", "If False")

    def draw_buttons(self, context, layout):
        layout.prop(self, "initial", toggle=True)

    def settings(self):
        return {"initial": self.initial}


class HL_NodeCounter(_Node, bpy.types.Node):
    """Counts events and fires when the count is reached (e.g. after 3 buttons)"""
    bl_idname, bl_label, bl_icon = "HL_NodeCounter", "Counter", "LINENUMBERS_ON"
    kind, category = "COUNTER", "Flow"
    count: IntProperty(name="Count", default=3, min=1)

    def make_sockets(self):
        self.ev_in("add", "Add One")
        self.ev_in("reset", "Reset")
        self.ev_out("reached", "On Reached")

    def draw_buttons(self, context, layout):
        layout.prop(self, "count")

    def settings(self):
        return {"count": self.count}


class HL_NodeRandom(_Node, bpy.types.Node):
    """Picks one of the connected outputs at random"""
    bl_idname, bl_label, bl_icon = "HL_NodeRandom", "Random", "MOD_NOISE"
    kind, category = "RANDOM", "Flow"

    def make_sockets(self):
        self.ev_in("pick", "Pick")
        for i in range(1, 5):
            self.ev_out(f"case{i}", f"Option {i}")


# ---------------------------------------------------------------- Actions

DIRECTIONS = [("down", "Down", ""), ("up", "Up", ""), ("+x", "+X", ""), ("-x", "-X", ""), ("+y", "+Y", ""),
              ("-y", "-Y", "")]


class HL_NodeMove(_Node, bpy.types.Node):
    """Slides an object: gates, garage doors, lifts, drawbridges"""
    bl_idname, bl_label, bl_icon = "HL_NodeMove", "Move Over Time", "ORIENTATION_LOCAL"
    kind, category = "MOVE", "Actions"
    direction: EnumProperty(name="Direction", items=DIRECTIONS, default="down")
    distance: StringProperty(name="Distance", default="auto",
                             description="Units to move (auto = the object's own size in that direction)")
    seconds: FloatProperty(name="Seconds", default=4.0, min=0.0,
                           description="How long the whole move takes (the speed is worked out from this)")
    block_nav: BoolProperty(name="Blocks Nav While Closed", default=False,
                            description="Zombies and bots treat the way through as blocked until it has moved. "
                                        "Off by default: L4D2 blocks the nav from the moment the map loads, and "
                                        "the Director then places no wandering zombies behind a closed gate "
                                        "(measured: the room behind the first gate stayed empty)")

    def make_sockets(self):
        self.obj_in("object", "Object")
        self.ev_in("go", "Go")
        self.ev_in("back", "Go Back")
        self.ev_out("arrived", "On Arrived")
        self.ev_out("returned", "On Back")

    def draw_buttons(self, context, layout):
        col = layout.column(align=True)
        col.prop(self, "direction", text="")
        col.prop(self, "distance", text="Distance")
        col.prop(self, "seconds", text="Seconds")
        layout.prop(self, "block_nav", text="Block Nav While Closed")

    def settings(self):
        return {"direction": self.direction, "distance": self.distance, "seconds": self.seconds,
                "block_nav": self.block_nav}

    def to_lnode(self, context):
        return self.with_object(context, super().to_lnode(context))


class HL_NodeCollision(_Node, bpy.types.Node):
    """How an object collides: solid for players and zombies, and whether the nav mesh goes through it"""
    bl_idname, bl_label, bl_icon = "HL_NodeCollision", "Collision", "MOD_PHYSICS"
    kind, category = "COLLISION", "Scene"
    players: BoolProperty(name="Blocks Players", default=True,
                          description="Survivors and zombies bump into it (off: they walk through)")
    nav: BoolProperty(name="Blocks Nav Mesh", default=False,
                      description="Zombies and bots path around it (off: the nav mesh runs through it, e.g. a "
                                  "fence or barricade they can get past)")

    def make_sockets(self):
        self.obj_in("object", "Object")

    def draw_buttons(self, context, layout):
        col = layout.column(align=True)
        col.prop(self, "players", toggle=True)
        col.prop(self, "nav", toggle=True)

    def settings(self):
        return {"players": self.players, "nav": self.nav}

    def to_lnode(self, context):
        return self.with_object(context, super().to_lnode(context))


class HL_NodeShowHide(_Node, bpy.types.Node):
    """Makes an object appear or disappear (solid while shown): walls, barricades, debris"""
    bl_idname, bl_label, bl_icon = "HL_NodeShowHide", "Show / Hide Object", "HIDE_OFF"
    kind, category = "SHOW_HIDE", "Actions"
    start_hidden: BoolProperty(name="Starts Hidden")

    def make_sockets(self):
        self.obj_in("object", "Object")
        self.ev_in("show", "Show")
        self.ev_in("hide", "Hide")
        self.ev_in("remove", "Remove")

    def draw_buttons(self, context, layout):
        layout.prop(self, "start_hidden", toggle=True)

    def settings(self):
        return {"start_hidden": self.start_hidden}

    def to_lnode(self, context):
        return self.with_object(context, super().to_lnode(context))


class HL_NodeHorde(_Node, bpy.types.Node):
    """Starts a zombie horde (panic event)"""
    bl_idname, bl_label, bl_icon = "HL_NodeHorde", "Horde", "COMMUNITY"
    kind, category = "HORDE", "Actions"

    def make_sockets(self):
        self.ev_in("start", "Start")
        self.ev_out("finished", "On Finished")


class HL_NodeCrescendo(_Node, bpy.types.Node):
    """Hordes in waves, like the lift and radio events in the campaigns"""
    bl_idname, bl_label, bl_icon = "HL_NodeCrescendo", "Crescendo", "SEQ_HISTOGRAM"
    kind, category = "CRESCENDO", "Actions"
    stages: StringProperty(name="Stages", default="PANIC 1, DELAY 10, PANIC 1, DELAY 10, PANIC 2",
                           description="PANIC n (hordes), DELAY n (seconds), TANK n")

    def make_sockets(self):
        self.ev_in("start", "Start")
        self.ev_out("finished", "On Finished")

    def draw_buttons(self, context, layout):
        layout.prop(self, "stages", text="")

    def settings(self):
        return {"stages": self.stages, "name": self.name}


class HL_NodeSpawn(_Node, bpy.types.Node):
    """Spawns a Tank, Witch or special infected: at an object's position, or (Where empty) where the
    game would put one. Only If Fewer Than makes it 'at least N' instead of 'always'"""
    bl_idname, bl_label, bl_icon = "HL_NodeSpawn", "Spawn Zombie", "GHOST_ENABLED"
    kind, category = "SPAWN", "Actions"
    what: EnumProperty(name="What", items=[(z, z.title(), "") for z in ZOMBIE_TYPES], default="tank")
    fewer_than: IntProperty(name="Only If Fewer Than", default=0, min=0, max=16,
                            description="Only spawn if fewer than this many have appeared so far, counting the "
                                        "Director's own (0 = always spawn)")

    def make_sockets(self):
        self.obj_in("object", "Where (empty = game picks)")
        self.ev_in("spawn", "Spawn")
        self.ev_out("killed", "On Killed (with a Where)")

    def draw_buttons(self, context, layout):
        layout.prop(self, "what", text="")
        layout.prop(self, "fewer_than")

    def settings(self):
        return {"what": self.what, "fewer_than": self.fewer_than}

    def to_lnode(self, context):
        return self.with_object(context, super().to_lnode(context), position=True)


class HL_NodeSound(_Node, bpy.types.Node):
    """Plays a sound: everywhere, or from an object's position"""
    bl_idname, bl_label, bl_icon = "HL_NodeSound", "Play Sound", "SPEAKER"
    kind, category = "SOUND", "Actions"
    sound: StringProperty(name="Sound", default="ambient/alarms/klaxon1.wav",
                          description="A game sound file (sound/...) or sound name")
    volume: IntProperty(name="Volume", default=10, min=0, max=10)
    everywhere: BoolProperty(name="Everywhere", default=True, description="Heard everywhere, not just nearby")

    def make_sockets(self):
        self.obj_in("object", "From (optional)")
        self.ev_in("play", "Play")
        self.ev_in("stop", "Stop")

    def draw_buttons(self, context, layout):
        layout.prop(self, "sound", text="")
        row = layout.row(align=True)
        row.prop(self, "volume")
        row.prop(self, "everywhere", toggle=True)

    def settings(self):
        return {"sound": self.sound, "volume": self.volume, "everywhere": self.everywhere}

    def to_lnode(self, context):
        return self.with_object(context, super().to_lnode(context), position=True)


class HL_NodeTeleport(_Node, bpy.types.Node):
    """Moves every survivor to an object's position"""
    bl_idname, bl_label, bl_icon = "HL_NodeTeleport", "Teleport Survivors", "CON_TRACKTO"
    kind, category = "TELEPORT", "Actions"

    def make_sockets(self):
        self.obj_in("object", "To")
        self.ev_in("go", "Teleport")

    def to_lnode(self, context):
        return self.with_object(context, super().to_lnode(context), position=True)


class HL_NodeMessage(_Node, bpy.types.Node):
    """Shows text on the survivors' screens: hints, warnings"""
    bl_idname, bl_label, bl_icon = "HL_NodeMessage", "Show Message", "INFO"
    kind, category = "MESSAGE", "Actions"
    text: StringProperty(name="Text", default="Find a way through")
    seconds: FloatProperty(name="Seconds", default=6.0, min=0.0, description="0 = until Hide")
    color: FloatVectorProperty(name="Colour", subtype="COLOR_GAMMA", size=3, min=0, max=1, default=(1, 1, 1))

    def make_sockets(self):
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


# ---------------------------------------------------------------- Director

class HL_NodeDirector(_Node, bpy.types.Node):
    """The AI Director's own inputs and outputs"""
    bl_idname, bl_label, bl_icon = "HL_NodeDirector", "Director", "GHOST_ENABLED"
    kind, category = "DIRECTOR", "Director"
    show_all: BoolProperty(name="All Events", update=lambda self, c: _rebuild_entity_sockets(self, "info_director"))

    def make_sockets(self):
        _rebuild_entity_sockets(self, "info_director")

    def draw_buttons(self, context, layout):
        layout.prop(self, "show_all", text="All Events", icon="PLUS")
        if not self.inputs and not self.outputs:
            layout.operator("hammerless.logic_refresh_node", icon="FILE_REFRESH").node = self.name

    def settings(self):
        return {"outputs": [s.identifier for s in self.outputs], "inputs": [s.identifier for s in self.inputs]}


TRI = [("SAME", "Unchanged", ""), ("ON", "On", ""), ("OFF", "Off", "")]


class HL_NodeDirectorSettings(_Node, bpy.types.Node):
    """Changes how the Director behaves from this point on (e.g. no hordes until the gate opens,
    more zombies in the finale). -1 = leave as the map's setting"""
    bl_idname, bl_label, bl_icon = "HL_NodeDirectorSettings", "Director Settings", "PREFERENCES"
    kind, category = "DIRECTOR_SETTINGS", "Director"
    common_limit: IntProperty(name="Max Commons", default=-1, min=-1, max=300)
    mob_min: IntProperty(name="Horde Min", default=-1, min=-1, max=300)
    mob_max: IntProperty(name="Horde Max", default=-1, min=-1, max=300)
    mob_interval_min: IntProperty(name="Horde Every (min s)", default=-1, min=-1, max=3600)
    mob_interval_max: IntProperty(name="Horde Every (max s)", default=-1, min=-1, max=3600)
    max_specials: IntProperty(name="Max Specials", default=-1, min=-1, max=32)
    special_interval: IntProperty(name="Special Every (s)", default=-1, min=-1, max=600)
    tank_limit: IntProperty(name="Max Tanks", default=-1, min=-1, max=8)
    witch_limit: IntProperty(name="Max Witches", default=-1, min=-1, max=16)
    no_mobs: EnumProperty(name="No Random Hordes", items=TRI, default="SAME")
    no_wanderers: EnumProperty(name="No Wandering Zombies", items=TRI, default="SAME")
    spawn_tank: EnumProperty(name="Director Spawns Tanks", items=TRI, default="SAME")
    spawn_witch: EnumProperty(name="Director Spawns Witches", items=TRI, default="SAME")
    spawn_smoker: EnumProperty(name="Smokers", items=TRI, default="SAME")
    spawn_boomer: EnumProperty(name="Boomers", items=TRI, default="SAME")
    spawn_hunter: EnumProperty(name="Hunters", items=TRI, default="SAME")
    spawn_charger: EnumProperty(name="Chargers", items=TRI, default="SAME")
    spawn_jockey: EnumProperty(name="Jockeys", items=TRI, default="SAME")
    spawn_spitter: EnumProperty(name="Spitters", items=TRI, default="SAME")

    def make_sockets(self):
        self.ev_in("apply", "Apply")
        self.ev_in("reset", "Back to Map Settings")

    def draw_buttons(self, context, layout):
        col = layout.column(align=True)
        for key, _opt in DIRECTOR_FIELDS:
            col.prop(self, key)
        layout.prop(self, "no_mobs")
        layout.prop(self, "no_wanderers")
        box = layout.box()
        box.label(text="Director spawns (Off = your graph decides)")
        col = box.column(align=True)
        for what in SPAWN_KINDS:
            col.prop(self, f"spawn_{what}", text=what.title() + "s" if what != "witch" else "Witches")

    def settings(self):
        d = {key: getattr(self, key) for key, _opt in DIRECTOR_FIELDS}
        d.update(no_mobs=self.no_mobs, no_wanderers=self.no_wanderers)
        d.update({f"spawn_{w}": getattr(self, f"spawn_{w}") for w in SPAWN_KINDS})
        return d


# ---------------------------------------------------------------- Values

class HL_NodeValue(_Node, bpy.types.Node):
    """A number to share between nodes"""
    bl_idname, bl_label, bl_icon = "HL_NodeValue", "Value", "DRIVER_TRANSFORM"
    kind, category = "VALUE", "Values"

    def make_sockets(self):
        self.num_in("value", "Value", 0.5)
        self.num_out("value", "Value")


class HL_NodeProgress(_Node, bpy.types.Node):
    """How far along the path to the end safe room the survivors are, 0 (start) to 1 (end)"""
    bl_idname, bl_label, bl_icon = "HL_NodeProgress", "Path Progress", "TRACKING_FORWARDS"
    kind, category = "PROGRESS", "Values"

    def make_sockets(self):
        self.num_out("furthest", "Furthest Survivor")
        self.num_out("average", "Average")
        self.num_out("last", "Last Survivor")


class HL_NodeRandomValue(_Node, bpy.types.Node):
    """A random number between Min and Max"""
    bl_idname, bl_label, bl_icon = "HL_NodeRandomValue", "Random Value", "MOD_NOISE"
    kind, category = "RANDOM_VALUE", "Values"
    each_time: BoolProperty(name="New Each Time", default=False,
                            description="Roll again every time it's read (off: rolled once per game and kept)")

    def make_sockets(self):
        self.num_in("min", "Min", 0.0)
        self.num_in("max", "Max", 1.0)
        self.num_out("value", "Value")

    def draw_buttons(self, context, layout):
        layout.prop(self, "each_time", toggle=True)

    def settings(self):
        return {"each_time": self.each_time}


MATH_OPS = [("ADD", "Add", ""), ("SUBTRACT", "Subtract", ""), ("MULTIPLY", "Multiply", ""), ("DIVIDE", "Divide", ""),
            ("MINIMUM", "Minimum", ""), ("MAXIMUM", "Maximum", ""), ("POWER", "Power", ""), ("MODULO", "Modulo", ""),
            ("ABSOLUTE", "Absolute (A)", ""), ("ROUND", "Round (A)", ""), ("FLOOR", "Floor (A)", ""),
            ("CEIL", "Ceil (A)", "")]


class HL_NodeMath(_Node, bpy.types.Node):
    """Maths on numbers, like Blender's Math node"""
    bl_idname, bl_label, bl_icon = "HL_NodeMath", "Math", "LINENUMBERS_ON"
    kind, category = "MATH", "Values"
    op: EnumProperty(name="Operation", items=MATH_OPS, default="MULTIPLY")

    def make_sockets(self):
        self.num_in("a", "A", 0.0)
        self.num_in("b", "B", 0.5)
        self.num_out("value", "Value")

    def draw_buttons(self, context, layout):
        layout.prop(self, "op", text="")

    def settings(self):
        return {"op": self.op}


COMPARE_OPS = [("GREATER_EQUAL", "A ≥ B", ""), ("GREATER", "A > B", ""), ("LESS", "A < B", ""),
               ("LESS_EQUAL", "A ≤ B", ""), ("EQUAL", "A = B", ""), ("NOT_EQUAL", "A ≠ B", "")]


class HL_NodeCompare(_Node, bpy.types.Node):
    """Compares two numbers: true or false"""
    bl_idname, bl_label, bl_icon = "HL_NodeCompare", "Compare", "ARROW_LEFTRIGHT"
    kind, category = "COMPARE", "Values"
    op: EnumProperty(name="Operation", items=COMPARE_OPS, default="GREATER_EQUAL")

    def make_sockets(self):
        self.num_in("a", "A", 0.0)
        self.num_in("b", "B", 0.5)
        self.bool_out("result", "Result")

    def draw_buttons(self, context, layout):
        layout.prop(self, "op", text="")

    def settings(self):
        return {"op": self.op}


class HL_NodeBoolMath(_Node, bpy.types.Node):
    """And, Or, Not on true/false values"""
    bl_idname, bl_label, bl_icon = "HL_NodeBoolMath", "Boolean Math", "SELECT_INTERSECT"
    kind, category = "BOOL_MATH", "Values"
    op: EnumProperty(name="Operation", default="AND", items=[
        ("AND", "And", ""), ("OR", "Or", ""), ("NOT", "Not (A)", ""), ("XOR", "Either but not both", "")])

    def make_sockets(self):
        self.bool_in("a", "A")
        self.bool_in("b", "B")
        self.bool_out("result", "Result")

    def draw_buttons(self, context, layout):
        layout.prop(self, "op", text="")

    def settings(self):
        return {"op": self.op}


class HL_NodeInfectedCount(_Node, bpy.types.Node):
    """How many Tanks, Witches or specials have appeared so far (the Director's and your own), or are alive now"""
    bl_idname, bl_label, bl_icon = "HL_NodeInfectedCount", "Infected Count", "COMMUNITY"
    kind, category = "INFECTED_COUNT", "Values"
    what: EnumProperty(name="What", items=[(z, z.title(), "") for z in ZOMBIE_TYPES if z != "common"], default="tank")
    mode: EnumProperty(name="Count", default="APPEARED", items=[
        ("APPEARED", "Appeared So Far", "Every one that has spawned this game"),
        ("ALIVE", "Alive Now", "Only the ones alive right now")])

    def make_sockets(self):
        self.num_out("count", "Count")

    def draw_buttons(self, context, layout):
        layout.prop(self, "what", text="")
        layout.prop(self, "mode", text="")

    def settings(self):
        return {"what": self.what, "mode": self.mode}


class HL_NodeWhen(_Node, bpy.types.Node):
    """Fires the moment its condition becomes true (checked twice a second); On False when it turns false again"""
    bl_idname, bl_label, bl_icon = "HL_NodeWhen", "When", "PLAY"
    kind, category = "WHEN", "Flow"
    once: BoolProperty(name="Only Once", default=True, description="Fire On True the first time only")

    def make_sockets(self):
        self.bool_in("condition", "Condition")
        self.ev_out("true", "On True")
        self.ev_out("false", "On False")

    def draw_buttons(self, context, layout):
        layout.prop(self, "once", toggle=True)

    def settings(self):
        return {"once": self.once}


class HL_NodeIf(_Node, bpy.types.Node):
    """An event comes in and goes out True or False, depending on the condition right then"""
    bl_idname, bl_label, bl_icon = "HL_NodeIf", "If", "DECORATE_KEYFRAME"
    kind, category = "IF", "Flow"

    def make_sockets(self):
        self.ev_in("in", "In")
        self.bool_in("condition", "Condition")
        self.ev_out("true", "True")
        self.ev_out("false", "False")


# ---------------------------------------------------------------- Objectives

class HL_NodeObjective(_Node, bpy.types.Node):
    """A goal shown on screen until it's completed; chain them: On Completed -> the next one's Start"""
    bl_idname, bl_label, bl_icon = "HL_NodeObjective", "Objective", "CHECKMARK"
    kind, category = "OBJECTIVE", "Objectives"
    text: StringProperty(name="Goal", default="Open the gate")
    done_text: StringProperty(name="When Done", default="", description="Shown briefly when completed (optional)")

    def make_sockets(self):
        self.ev_in("start", "Start")
        self.ev_in("complete", "Complete")
        self.ev_out("started", "On Started")
        self.ev_out("completed", "On Completed")

    def draw_buttons(self, context, layout):
        layout.prop(self, "text", text="")
        layout.prop(self, "done_text", text="Done")

    def settings(self):
        return {"text": self.text, "done_text": self.done_text}


CATEGORIES = [
    ("Events", [HL_NodeMapStart, HL_NodeGameEvent, HL_NodeVolume, HL_NodeButton, HL_NodeTimer]),
    ("Values", [HL_NodeProgress, HL_NodeRandomValue, HL_NodeMath, HL_NodeCompare, HL_NodeBoolMath,
                HL_NodeInfectedCount, HL_NodeValue]),
    ("Scene", [HL_NodeObjectInfo, HL_NodeObject, HL_NodeCollision]),
    ("Flow", [HL_NodeWhen, HL_NodeIf, HL_NodeSequence, HL_NodeDelay, HL_NodeOnce, HL_NodeGate, HL_NodeBranch,
              HL_NodeCounter, HL_NodeRandom]),
    ("Actions", [HL_NodeMove, HL_NodeShowHide, HL_NodeHorde, HL_NodeCrescendo, HL_NodeSpawn, HL_NodeSound,
                 HL_NodeTeleport, HL_NodeMessage]),
    ("Director", [HL_NodeDirector, HL_NodeDirectorSettings]),
    ("Objectives", [HL_NodeObjective]),
]
NODE_CLASSES = tuple(c for _t, cs in CATEGORIES for c in cs) + (HL_NodePathProgress,)   # (old graphs)


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

def logic_trees(scene=None):
    """The L4D2 Logic graphs (of one scene: its own, plus unassigned ones when the file has one scene)."""
    trees = [t for t in bpy.data.node_groups if t.bl_idname == TREE]
    if scene is None:
        return trees
    return [t for t in trees if t.scene_name == scene.name
            or (not bpy.data.scenes.get(t.scene_name) and len(bpy.data.scenes) == 1)]


def _sources(sock, seen=None):
    """The real (node, output socket) pairs feeding an input socket, through reroutes and muted nodes
    (a muted node passes its input straight through, as Blender draws it)."""
    seen = seen if seen is not None else set()
    out = []
    for link in sock.links:
        if link.is_muted or not link.is_valid:
            continue
        node, from_sock = link.from_node, link.from_socket
        key = (node.name, from_sock.identifier)
        if key in seen:
            continue
        seen.add(key)
        if node.type == "REROUTE":
            out += _sources(node.inputs[0], seen)
        elif node.mute:
            for il in node.internal_links:
                if il.to_socket == from_sock:
                    out += _sources(il.from_socket, seen)
        else:
            out.append((node, from_sock))
    return out


def _compiled_links(tree, report):
    """Wires between real (unmuted) nodes, with reroutes and muted nodes resolved. A wire joining
    sockets of different kinds (an event into a number...) does nothing: reported."""
    links = []
    for node in tree.nodes:
        if not isinstance(node, _Node) or node.mute:
            continue
        for inp in node.inputs:
            for src, out in _sources(inp):
                if not isinstance(src, _Node):
                    continue
                if out.bl_idname != inp.bl_idname:
                    report.warnings.append(f"{tree.name}: the wire from '{src.label or src.name}' to "
                                           f"'{node.label or node.name}' joins a {out.bl_label} with a "
                                           f"{inp.bl_label}, so it does nothing")
                    continue
                if out.bl_idname not in ("HL_EventSocket",) + DATA_SOCKETS:
                    continue
                links.append(LLink(src.name, out.identifier, node.name, inp.identifier,
                                   data=out.bl_idname in DATA_SOCKETS))
    return links


def _check_nodes(context, tree, report) -> None:
    """Problems Blender can't show: objects deleted from the scene that nodes still point at, entity
    nodes made for another class, and event nodes whose sockets need the game's definitions."""
    scene_objects = context.scene.objects
    for node in tree.nodes:
        if not isinstance(node, _Node) or node.mute:
            continue
        objs = [getattr(node, "target", None)] + [getattr(s, "value", None) for s in node.inputs
                                                    if s.bl_idname == "HL_ObjectSocket"]
        for obj in objs:
            if isinstance(obj, bpy.types.Object) and obj.name not in scene_objects:
                report.warnings.append(f"{tree.name}: node '{node.label or node.name}' points at '{obj.name}', "
                                       f"which isn't in this scene (deleted?): pick another object")
        target = getattr(node, "target", None)
        cls = getattr(node, "classname", "")
        if target is not None and cls and target.hammerless.classname and target.hammerless.classname != cls:
            report.warnings.append(f"{tree.name}: node '{node.label or node.name}' shows the events of a {cls}, "
                                   f"but '{target.name}' is now a {target.hammerless.classname}: pick it again")
        if node.bl_idname in ("HL_NodeDirector", "HL_NodeObject") and not node.inputs and not node.outputs:
            refresh_entity_node(node)


def refresh_entity_node(node) -> None:
    """Fill an event node's sockets from the game's definitions (when they were missing before)."""
    if node.bl_idname == "HL_NodeDirector":
        _rebuild_entity_sockets(node, "info_director")
    elif getattr(node, "target", None) is not None and getattr(node, "classname", ""):
        _rebuild_entity_sockets(node, node.classname)


def compile_logic(context, ir, report) -> None:
    """Add the scene's L4D2 Logic graphs' entities, connections and script to the map."""
    for t in logic_trees():
        if not bpy.data.scenes.get(t.scene_name) and len(bpy.data.scenes) == 1:
            t.scene_name = context.scene.name     # graphs from before graphs had a scene, or appended
        if not t.use_fake_user:
            t.use_fake_user = True                # never dropped on save for having no user
    unassigned = [t.name for t in logic_trees() if not bpy.data.scenes.get(t.scene_name)]
    if unassigned:
        report.warnings.append(f"Logic graph(s) without a scene are left out: {', '.join(unassigned)} "
                               "(open one in the node editor and set its Scene in the sidebar)")
    trees = logic_trees(context.scene)
    for tree in trees:
        _check_nodes(context, tree, report)
        nodes = [n.to_lnode(context) for n in tree.nodes if isinstance(n, _Node) and n.kind != "OBJECT_INFO"
                 and not n.mute]
        volumes = {n.obj for n in nodes if n.kind == "VOLUME" and n.obj}
        if volumes:     # a volume's mesh becomes the trigger, not a solid wall
            ir.brushes = [b for b in ir.brushes if b.source.split(" (part ")[0] not in volumes]
            ir.entities = [e for e in ir.entities if not (e.source.split(" (part ")[0] in volumes
                                                          and e.classname == "func_detail")]
        links = _compiled_links(tree, report)
        for p in compile_graph(nodes, links, ir, tree.name if len(trees) > 1 else "",
                               log=context.scene.hammerless.debug_log):
            report.warnings.append(f"{tree.name}: {p}")


# ---------------------------------------------------------------- operators

class HL_OT_logic_new(bpy.types.Operator):
    bl_idname = "hammerless.logic_new"
    bl_label = "New Logic Graph"
    bl_description = "Create a logic graph and open it in a node editor"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = new_tree(context)
        _show_tree(context, tree)
        return {"FINISHED"}


def new_tree(context):
    tree = bpy.data.node_groups.new("Map Logic", TREE)
    tree.use_fake_user = True          # kept when saved, even if no editor shows it
    tree.scene_name = context.scene.name
    return tree


class HL_OT_logic_from_outputs(bpy.types.Operator):
    bl_idname = "hammerless.logic_from_outputs"
    bl_label = "Graph from Outputs"
    bl_description = ("Move the outputs set on objects (the Outputs lists) into a logic graph as nodes and "
                      "wires, so all your map logic is in one place")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = next(iter(logic_trees(context.scene)), None) or new_tree(context)
        by_name = {}
        for o in context.scene.objects:
            for kv in o.hammerless.keyvalues:
                if kv.key == "targetname" and kv.value:
                    by_name[kv.value] = o
        nodes = {n.target.name: n for n in tree.nodes if isinstance(n, HL_NodeObject) and n.target}
        director = next((n for n in tree.nodes if isinstance(n, HL_NodeDirector)), None)

        def node_for(obj):
            if obj.name not in nodes:
                n = tree.nodes.new("HL_NodeObject")
                n.target = obj
                n.location = (0.0, -220.0 * (len(nodes) % 6))
                nodes[obj.name] = n
            return nodes[obj.name]
        from ..core import fgd as _fgd

        def has_socket(obj, ident, outputs):
            ins, outs = _entity_sockets(obj.hammerless.classname, True) if obj.hammerless.classname else ([], [])
            return any(io.name == ident for io in (outs if outputs else ins))
        moved = kept = 0
        params: dict = {}          # (node, input) -> the parameter its socket holds
        for o in list(context.scene.objects):
            if not o.hammerless.outputs:
                continue
            keep = []
            for i, out in enumerate(o.hammerless.outputs):
                if not has_socket(o, out.output, True):
                    keep.append(i)
                    continue
                if out.target == "director":
                    if director is None:
                        director = tree.nodes.new("HL_NodeDirector")
                        director.location = (500.0, 200.0)
                    dst = director
                elif out.target in by_name and has_socket(by_name[out.target], out.input, False):
                    dst = node_for(by_name[out.target])
                else:
                    keep.append(i)
                    continue
                src = node_for(o)
                if dst is not director:
                    dst.location.x = max(dst.location.x, src.location.x + 350)
                a = next((s for s in src.outputs if s.identifier == out.output), None)
                b = next((s for s in dst.inputs if s.identifier == out.input), None)
                # one input socket holds one parameter: a second output with a different one stays put
                if not (a and b) or params.get((dst.name, out.input), out.parameter) != out.parameter:
                    keep.append(i)
                    continue
                params[(dst.name, out.input)] = out.parameter
                # the output's delay and "only once" become Delay / Once nodes on the wire
                chain = a
                x = src.location.x + 175
                if out.only_once:
                    once = tree.nodes.new("HL_NodeOnce")
                    once.location = (x, src.location.y - 120)
                    tree.links.new(chain, once.inputs["In"])
                    chain = once.outputs["Out"]
                if out.delay > 0:
                    delay = tree.nodes.new("HL_NodeDelay")
                    delay.seconds = out.delay
                    delay.location = (x + 90, src.location.y - 160)
                    tree.links.new(chain, delay.inputs["In"])
                    chain = delay.outputs["Out"]
                tree.links.new(chain, b)
                if out.parameter:
                    b.value = out.parameter
                moved += 1
            kept += len(keep)
            for i in reversed(range(len(o.hammerless.outputs))):
                if i not in keep:
                    o.hammerless.outputs.remove(i)
        _show_tree(context, tree)
        self.report({"INFO"}, f"Moved {moved} output(s) into '{tree.name}'"
                    + (f"; {kept} stayed on their objects (no matching node socket, or a second "
                       f"parameter on the same input)" if kept else ""))
        return {"FINISHED"}


def _show_tree(context, tree):
    if not bpy.data.scenes.get(tree.scene_name):
        tree.scene_name = context.scene.name
    area = context.area if context.area and context.area.type == "NODE_EDITOR" else None
    if area is None and context.screen:
        area = next((a for a in context.screen.areas if a.type == "NODE_EDITOR"), None)
    if area is not None:
        area.ui_type = TREE
        area.spaces.active.node_tree = tree


class HL_OT_logic_refresh_node(bpy.types.Operator):
    bl_idname = "hammerless.logic_refresh_node"
    bl_label = "Load Events"
    bl_description = "Load this node's events from the game's entity definitions (needs the L4D2 folder)"
    bl_options = {"UNDO", "INTERNAL"}

    node: bpy.props.StringProperty()

    def execute(self, context):
        tree = getattr(context.space_data, "edit_tree", None)
        node = tree.nodes.get(self.node) if tree else None
        if node is None:
            return {"CANCELLED"}
        refresh_entity_node(node)
        if not node.inputs and not node.outputs:
            self.report({"WARNING"}, "The game's entity definitions weren't found: set the L4D2 Folder "
                                     "(Advanced) and make sure the Authoring Tools are installed")
            return {"CANCELLED"}
        return {"FINISHED"}


CLASSES = (HL_LogicTree, HL_EventSocket, HL_ObjectSocket, HL_FloatSocket, HL_BoolSocket) + NODE_CLASSES + tuple(CATEGORY_MENUS) + (
    HL_OT_logic_new, HL_OT_logic_from_outputs, HL_OT_logic_refresh_node)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.NODE_MT_add.append(_add_menu)


def unregister():
    bpy.types.NODE_MT_add.remove(_add_menu)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
