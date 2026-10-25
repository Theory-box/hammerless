"""L4D2 Logic: a node editor for map events, objectives and director control (like the shader
editor, for game logic).

Two kinds of wire: orange event wires ("when this happens, do that") and blue object wires
(which scene object a node acts on; an Object Info node supplies one, or pick it on the node).
core/logic.py turns a graph into entities, I/O connections and a small map script at export.
"""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, StringProperty

from ..core import fgd
from ..core.logic import DIRECTOR_FIELDS, GAME_EVENTS, ZOMBIE_TYPES, LLink, LNode, compile_graph

SPAWN_KINDS = ("tank", "witch", "smoker", "boomer", "hunter", "charger", "jockey", "spitter")

TREE = "HL_LogicTree"
EVENT_COLOR = (1.0, 0.62, 0.15, 1.0)
OBJECT_COLOR = (0.35, 0.65, 1.0, 1.0)
FLOAT_COLOR = (0.63, 0.63, 0.63, 1.0)       # Blender's colours: grey numbers, pink true/false
BOOL_COLOR = (0.80, 0.65, 0.84, 1.0)
DATA_SOCKETS = ("HL_FloatSocket", "HL_BoolSocket", "HL_TextSocket", "HL_VectorSocket", "HL_ThingSocket", "HL_AnySocket",
                "HL_IntSocket")
CATEGORY_COLORS = {"Events": (0.45, 0.18, 0.16), "Scene": (0.16, 0.33, 0.40), "Flow": (0.25, 0.25, 0.30),
                   "Values": (0.22, 0.22, 0.40),
                   "Actions": (0.18, 0.36, 0.20), "Director": (0.33, 0.20, 0.42), "Objectives": (0.45, 0.38, 0.12),
                   "Script": (0.16, 0.26, 0.42)}


class HL_LogicTree(bpy.types.NodeTree):
    """Map logic: events, objectives and director control"""
    bl_idname = TREE
    bl_label = "L4D2 Logic"
    bl_icon = "NODETREE"

    scene_name: StringProperty(name="Scene", description="The scene (map) this graph belongs to: only that map "
                                                         "gets its logic")
    brain: PointerProperty(type=bpy.types.Object, name="Brain",
                           description="The empty in the map this logic lives on: select it to find the graph; "
                                       "outside the map (or deleted), the graph isn't built. Self is this object")


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
        if self.is_output or self.is_linked or self.hide_value:
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
        if self.is_output or self.is_linked or self.hide_value:
            layout.label(text=text)
        else:
            layout.prop(self, "value", text=text)

    def draw_color(self, context, node):
        return FLOAT_COLOR

    @classmethod
    def draw_color_simple(cls):
        return FLOAT_COLOR


class HL_IntSocket(bpy.types.NodeSocket):
    """A whole number (wire a number in, or type it here). Kept exactly, every digit: a colour with its see-through
    amount is a number in the billions, which a Number input would round (it keeps about 7 digits)"""
    bl_idname = "HL_IntSocket"
    bl_label = "Whole Number"
    value: IntProperty(name="Value", default=0)

    def draw(self, context, layout, node, text):
        if self.is_output or self.is_linked or self.hide_value:
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
        if self.is_output or self.is_linked or self.hide_value:
            layout.label(text=text)
        else:
            layout.prop(self, "value", text=text)

    def draw_color(self, context, node):
        return BOOL_COLOR

    @classmethod
    def draw_color_simple(cls):
        return BOOL_COLOR


EVENT_LINK_LIMIT = 4095      # Blender's default for an input is one wire: a second one replaced the first


def _event_inputs_take_many(*_args):
    """Graphs saved before event inputs took several wires."""
    for tree in bpy.data.node_groups:
        if tree.bl_idname == TREE:
            for node in tree.nodes:
                for sock in node.inputs:
                    if sock.bl_idname == "HL_EventSocket" and sock.link_limit != EVENT_LINK_LIMIT:
                        sock.link_limit = EVENT_LINK_LIMIT


class _Node:
    """A logic node's header always says what kind of node it is: its type, then a note (an example's, or the
    user's) or what it's set to (its object, event, function...): "Text Check: A pod?", "Script Call: Get Model
    Name". Blender's own Label (Node panel) still overrides it for whoever sets one."""
    kind = ""
    category = "Flow"
    note: StringProperty(name="Note", description="What this node is for, after its type in its header")
    auto_title: StringProperty(options={"HIDDEN"})       # (what it's set to, kept up to date by the node itself)

    def draw_label(self):
        extra = self.note or self.auto_title
        return f"{self.bl_label}: {extra}" if extra else self.bl_label

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE

    def init(self, context):
        self.use_custom_color = True
        self.color = CATEGORY_COLORS.get(self.category, (0.25, 0.25, 0.25))
        self.make_sockets()
        _record_sockets(self)

    def make_sockets(self):
        pass

    def ev_in(self, ident, label, takes_value=False, tip=""):
        s = self.inputs.new("HL_EventSocket", label, identifier=ident)
        s.takes_value, s.tip = takes_value, tip
        s.link_limit = EVENT_LINK_LIMIT        # any number of events can fire one input
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
            if found and found[0][0].bl_idname == "HL_NodeSelf":
                return self_owner()              # (the object the Behaviour is being built for)
            if found and found[0][0].bl_idname == "HL_NodeExposedSetting" and found[0][0].value_type == 'OBJECT':
                return exposed.resolve(found[0][0], _EXPOSED_SLOT[0])
            return getattr(found[0][0], "target", None) if found else None
        return sock.value

    def settings(self) -> dict:
        return {}

    def to_lnode(self, context) -> LNode:
        params = {s.identifier: s.value for s in self.inputs if getattr(s, "takes_value", False) and s.value}
        consts = {}
        for s in self.inputs:
            if s.bl_idname not in DATA_SOCKETS or not hasattr(s, "value"):
                continue
            v = s.value
            if s.bl_idname == "HL_ThingSocket":
                v = v.name if v is not None else ""
            elif s.bl_idname == "HL_VectorSocket":
                v = tuple(v)
            consts[s.identifier] = v
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
    if node.bl_idname == 'HL_NodeObject':
        node.obj_in('object', 'Object')
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


def _record_sockets(node) -> None:
    """What the node's sockets were made for (see _keeps_links)."""
    node["hl_sockets_for"] = repr(tuple(getattr(node, a, None) for a in ("fn", "event", "key", "hook")))


def _keeps_links(rebuild):
    """For a function that clears a node's sockets and makes them again: the wires and typed values on sockets
    that are still there (same identifier and type) come back."""
    import functools

    @functools.wraps(rebuild)
    def wrapper(self, context=None):
        # what decides the node's sockets (its function, event, Director key, hook): when that changed, the
        # sockets are new ones even with the same names, so their values are the new defaults; only wires stay
        what = repr(tuple(getattr(self, a, None) for a in ("fn", "event", "key", "hook")))
        before = self.get("hl_sockets_for")
        same_thing = before is None or before == what     # (none recorded yet, e.g. a node from an older file)
        self["hl_sockets_for"] = what
        tree = self.id_data
        keep = [(l.from_node.name, l.from_socket.identifier, l.from_socket.bl_idname, l.to_node.name,
                 l.to_socket.identifier, l.to_socket.bl_idname)
                for l in tree.links if l.from_node == self or l.to_node == self]
        values = {}
        for sock in self.inputs:
            for attr in ("value", "default_value"):
                if hasattr(sock, attr):
                    try:
                        v = getattr(sock, attr)
                        values[(sock.identifier, sock.bl_idname, attr)] = tuple(v) if hasattr(v, "__len__") and not isinstance(v, str) else v
                    except (AttributeError, TypeError):
                        pass
        out = rebuild(self, context)
        for sock in self.inputs if same_thing else ():
            for attr in ("value", "default_value"):
                key = (sock.identifier, sock.bl_idname, attr)
                if key in values:
                    try:
                        setattr(sock, attr, values[key])
                    except (AttributeError, TypeError, ValueError):
                        pass
        for fn, fs, ft, tn, ts, tt in keep:
            a, b = tree.nodes.get(fn), tree.nodes.get(tn)
            o = next((x for x in a.outputs if x.identifier == fs and x.bl_idname == ft), None) if a else None
            i = next((x for x in b.inputs if x.identifier == ts and x.bl_idname == tt), None) if b else None
            if o and i and not any(l.from_socket == o and l.to_socket == i for l in tree.links):
                tree.links.new(o, i)
        return out
    return wrapper


def _label_from_target(self, context):
    self.auto_title = self.target.name if self.target else ""


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
                        update=lambda self, c: setattr(self, "auto_title", GAME_EVENTS[self.event][0]))
    once: BoolProperty(name="Only Once", description="Fires the first time only")

    def make_sockets(self):
        self.ev_out("happened", "On Event")
        self.auto_title = GAME_EVENTS[self.event][0]

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
    every_tick: BoolProperty(name="Every Tick", default=False,
                             description="Fire on every server tick (30 a second) instead of every N seconds: for "
                                         "things that must follow the player closely, like movement or a camera")

    def make_sockets(self):
        self.ev_in("start", "Start")
        self.ev_in("stop", "Stop")
        self.ev_in("now", "Fire Now")
        self.ev_out("tick", "On Tick")

    def draw_buttons(self, context, layout):
        layout.prop(self, "every_tick", toggle=True)
        if not self.every_tick:
            row = layout.row(align=True)
            row.prop(self, "seconds")
            row.prop(self, "max_seconds")
        layout.prop(self, "running", toggle=True)

    def settings(self):
        return {"seconds": self.seconds, "max_seconds": self.max_seconds, "running": self.running,
                "every_tick": self.every_tick}


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


_SELF_OWNER: list = [None]        # the object a graph is being built for (compile_logic sets it around flatten)
_SELF_ISOLATE: list = [False]     # ...and whether it's a Behaviour (per-player variables kept apart per object)


def self_owner():
    return _SELF_OWNER[0]


def self_isolated() -> bool:
    return _SELF_ISOLATE[0]


class HL_NodeSelf(_Node, bpy.types.Node):
    """The object this logic runs on (a Behaviour's object; a world graph's brain): wire Object into nodes that act
    on an object or a place (Move, Show / Hide, Spawn at...), Entity into ones that take an entity (functions...)"""
    bl_idname, bl_label, bl_icon = "HL_NodeSelf", "Self", "OBJECT_ORIGIN"
    kind, category = "SELF", "Scene"

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE        # (a Behaviour's object, or a world graph's brain)

    def make_sockets(self):
        self.outputs.new("HL_ObjectSocket", "Object", identifier="object")
        _data_socket(self.outputs, "thing", "entity", "Entity")

    def to_lnode(self, context) -> LNode:
        owner = self_owner()
        return LNode(self.name, self.kind, {}, obj=owner.name if owner is not None else "")


def _object_class(obj) -> str:
    """The entity class a scene object becomes ("" for a plain mesh): a Blender light is a light entity (a sun can't
    be switched: none)."""
    from .extract import LIGHT_CLASS, effective_role
    if obj is None:
        return ""
    if obj.type == "LIGHT" and effective_role(obj) == "LIGHT":
        cls = LIGHT_CLASS.get(obj.data.type, "light")
        return "" if cls == "light_environment" else cls
    return obj.hammerless.classname if obj.hammerless.role in ("ENTITY", "BRUSH_ENTITY") else ""


def _object_update(self, context):
    from .entitycontext import entity_object
    if self.target is not None:
        owner = entity_object(context, self.target)
        if owner != self.target:
            self.target = owner
            return
    cls = _object_class(self.target)
    self.classname = cls
    _rebuild_entity_sockets(self, cls)
    _label_from_target(self, context)


def _owners_of(tree) -> list:
    """The objects carrying this Behaviour graph."""
    return [o for o in bpy.data.objects for s in o.hammerless.behaviours if s.graph == tree]


def _self_update(self, context):
    """Entity Events on Self: its events are those of the objects carrying the Behaviour (the first one's class)."""
    if self.use_self:
        owners = _owners_of(self.id_data)
        cls = _object_class(owners[0]) if owners else self.classname
        self.classname = cls
        _rebuild_entity_sockets(self, cls)
        self.auto_title = "Self" + (f" ({cls})" if cls else "")
    else:
        _object_update(self, context)


class HL_NodeObject(_Node, bpy.types.Node):
    """An entity in the scene with all its events from the game (for anything the other nodes don't cover)"""
    bl_idname, bl_label, bl_icon = "HL_NodeObject", "Entity Events", "OUTLINER_DATA_EMPTY"
    kind, category = "OBJECT", "Scene"
    target: PointerProperty(type=bpy.types.Object, name="Object", update=_object_update)
    classname: StringProperty()
    show_all: BoolProperty(name="All Events", description="Also show the rarely used inputs and outputs",
                           update=lambda self, c: _rebuild_entity_sockets(self, self.classname))
    use_self: BoolProperty(name="Self", update=_self_update,
                           description="The object running this Behaviour, whichever it is (its events: those of the "
                                       "objects carrying the Behaviour)")

    def draw_buttons(self, context, layout):
        tree = self.id_data
        row = layout.row(align=True)
        if getattr(tree, "is_behaviour", False) or getattr(tree, "is_group", False):
            row.prop(self, "use_self", text="", icon="OBJECT_ORIGIN")
        if self.use_self:
            row.label(text="Self")
            if self.classname:
                row = layout.row()
                row.label(text=self.classname)
                row.prop(self, "show_all", text="", icon="PLUS")
            else:
                layout.label(text="Put this Behaviour on an entity first", icon="INFO")
            return
        row.prop(self, "target", text="")
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
        return {"outputs": [s.identifier for s in self.outputs],
                "inputs": [s.identifier for s in self.inputs if s.bl_idname == 'HL_EventSocket']}

    def to_lnode(self, context):
        n = super().to_lnode(context)
        if self.use_self:
            owner = self_owner()
            n.obj = owner.name if owner is not None else ""
        else:
            sock = self.inputs.get('Object')
            obj = self.object_from('object') if sock is not None and sock.is_linked else self.target
            n.obj = obj.name if obj else ""
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
        self.ev_in("toggle_test", "Toggle, Then Test")
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
    block_nav: BoolProperty(name="Block Navigation While Shown", default=False,
                            description="Block bots and zombies while visible; release navigation when hidden or removed")

    def make_sockets(self):
        self.obj_in("object", "Object")
        self.ev_in("show", "Show")
        self.ev_in("hide", "Hide")
        self.ev_in("remove", "Remove")

    def draw_buttons(self, context, layout):
        layout.prop(self, "start_hidden", toggle=True)
        layout.prop(self, "block_nav")

    def settings(self):
        return {"start_hidden": self.start_hidden, "block_nav": self.block_nav}

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


class HL_NodeCamera(_Node, bpy.types.Node):
    """Switches players between first person and the over-the-shoulder third person camera (the game's own
    thirdpersonshoulder: no cheats needed). Player empty: whoever set it off (walked into a Volume, pressed a
    Button), or everyone when nobody did (a Timer, Map Start)"""
    bl_idname, bl_label, bl_icon = "HL_NodeCamera", "Camera", "CAMERA_DATA"
    kind, category = "CAMERA", "Actions"

    def make_sockets(self):
        self.ev_in("run", "Set")
        _data_socket(self.inputs, "bool", "third", "Third Person").value = True
        _data_socket(self.inputs, "thing", "player", "Player")
        self.ev_out("then", "Then")


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

from . import exposed
_EXPOSED_SLOT = [None]


class HL_NodeExposedSetting(_Node, bpy.types.Node):
    """A graph-defined control shown on each object's Behaviour panel; wires carry that object's value"""
    bl_idname, bl_label, bl_icon = "HL_NodeExposedSetting", "Exposed Setting", "PREFERENCES"
    kind, category = "EXPOSED_VALUE", "Values"
    setting_id: StringProperty(options={'HIDDEN'})
    ui_name: StringProperty(name="Label", default="Setting", update=exposed.schema_changed)
    value_type: EnumProperty(name="Type", default="FLOAT", update=exposed.type_changed, items=[
        ("FLOAT","Number",""), ("INT","Integer",""), ("BOOL","Checkbox",""), ("TEXT","Text",""),
        ("ENUM","Dropdown",""), ("VECTOR","Vector",""), ("COLOR","Color",""), ("OBJECT","Object","")])
    default_object: PointerProperty(type=bpy.types.Object, name="Default", update=exposed.schema_changed)
    choices: StringProperty(name="Choices", default="one=One; two=Two",
                            description="Stable ID=Label pairs separated by semicolons. Keep IDs when renaming or reordering choices",
                            update=exposed.schema_changed)
    default_number: FloatProperty(name="Default", update=exposed.schema_changed)
    default_integer: IntProperty(name="Default", update=exposed.schema_changed)
    default_boolean: BoolProperty(name="Default", update=exposed.schema_changed)
    default_text: StringProperty(name="Default / Choice ID", default="one", update=exposed.schema_changed)
    default_vector: FloatVectorProperty(name="Default", size=3, update=exposed.schema_changed)
    default_color: FloatVectorProperty(name="Default", size=3, subtype='COLOR', default=(1,1,1), min=0,max=1,update=exposed.schema_changed)

    def init(self,context):
        self.setting_id=exposed.uuid.uuid4().hex
        super().init(context)
        exposed.sync_all()

    def copy(self,node):
        # A new node in one graph is a new setting; a copied graph retains its interface IDs.
        self.setting_id=exposed.uuid.uuid4().hex if self.id_data==node.id_data else node.setting_id
        exposed.sync_all()

    def make_sockets(self):
        if self.value_type == 'OBJECT':
            self.outputs.new('HL_ObjectSocket', 'Object', identifier='object')
            _data_socket(self.outputs, 'thing', 'value', 'Entity')
            return
        kind={'FLOAT':'num','INT':'num','BOOL':'bool','TEXT':'text','ENUM':'text','VECTOR':'vec','COLOR':'vec'}[self.value_type]
        _data_socket(self.outputs,kind,'value','Value')

    def draw_buttons(self,context,layout):
        layout.prop(self,'ui_name');layout.prop(self,'value_type')
        if self.value_type=='ENUM':layout.prop(self,'choices')
        layout.prop(self,'default_'+('text' if self.value_type=='ENUM' else exposed.FIELDS[self.value_type]))

    def to_lnode(self,context):
        if self.value_type == 'OBJECT':
            obj = exposed.resolve(self, _EXPOSED_SLOT[0])
            return LNode(self.name, 'SELF', {}, obj=obj.name if obj else '')
        kind={'FLOAT':'num','INT':'num','BOOL':'bool','TEXT':'text','ENUM':'text','VECTOR':'vec','COLOR':'vec'}[self.value_type]
        return LNode(self.name,'EXPOSED_VALUE',{'type':kind,'value':exposed.resolve(self,_EXPOSED_SLOT[0])})


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
            ("CEIL", "Ceil (A)", ""), ("BIT_AND", "Bits: A And B", "The bits set in both (whole numbers): flags A has of B"),
            ("BIT_OR", "Bits: A Or B", "A with B's bits set too (whole numbers): turns flags on"),
            ("BIT_CLEAR", "Bits: A Without B", "A with B's bits cleared (whole numbers): turns flags off")]


class HL_NodeMath(_Node, bpy.types.Node):
    """Maths on numbers, like Blender's Math node"""
    bl_idname, bl_label, bl_icon = "HL_NodeMath", "Math", "LINENUMBERS_ON"
    kind, category = "MATH", "Values"
    op: EnumProperty(name="Operation", items=MATH_OPS, default="MULTIPLY")
    integer: BoolProperty(name="Whole Numbers", default=False,
                          description="Convert operands to integers before arithmetic; useful for exact scores and counters")

    def make_sockets(self):
        self.num_in("a", "A", 0.0)
        self.num_in("b", "B", 0.5)
        self.num_out("value", "Value")

    def draw_buttons(self, context, layout):
        layout.prop(self, "op", text="")
        layout.prop(self, "integer")

    def settings(self):
        return {"op": self.op, "integer": self.integer}


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


# ---------------------------------------------------------------- Script: the game's own functions and events

TEXT_COLOR = (0.44, 0.84, 0.70, 1.0)
VECTOR_COLOR = (0.39, 0.39, 0.78, 1.0)
THING_COLOR = (0.93, 0.45, 0.58, 1.0)
ANY_COLOR = (0.45, 0.45, 0.45, 1.0)


class HL_TextSocket(bpy.types.NodeSocket):
    """Text, worked out live in the game (wire it in, or type it here)"""
    bl_idname = "HL_TextSocket"
    bl_label = "Text"
    value: StringProperty(name="Value", default="")

    def draw(self, context, layout, node, text):
        if self.is_output or self.is_linked or self.hide_value:
            layout.label(text=text)
        else:
            layout.prop(self, "value", text=text)

    def draw_color(self, context, node):
        return TEXT_COLOR

    @classmethod
    def draw_color_simple(cls):
        return TEXT_COLOR


class HL_VectorSocket(bpy.types.NodeSocket):
    """A position, direction or angles (x y z), worked out live in the game"""
    bl_idname = "HL_VectorSocket"
    bl_label = "Vector"
    value: FloatVectorProperty(name="Value", size=3, default=(0.0, 0.0, 0.0))

    def draw(self, context, layout, node, text):
        if self.is_output or self.is_linked or self.hide_value:
            layout.label(text=text)
        else:
            col = layout.column(align=True)
            col.label(text=text)
            col.prop(self, "value", text="")

    def draw_color(self, context, node):
        return VECTOR_COLOR

    @classmethod
    def draw_color_simple(cls):
        return VECTOR_COLOR


class HL_ThingSocket(bpy.types.NodeSocket):
    """A player, entity or nav area in the running game: wire one in, or pick a scene object here"""
    bl_idname = "HL_ThingSocket"
    bl_label = "Entity"
    value: PointerProperty(type=bpy.types.Object, name="Object")

    def draw(self, context, layout, node, text):
        if self.is_output or self.is_linked or self.hide_value:
            layout.label(text=text)
        else:
            layout.prop(self, "value", text=text)

    def draw_color(self, context, node):
        return THING_COLOR

    @classmethod
    def draw_color_simple(cls):
        return THING_COLOR


class HL_AnySocket(bpy.types.NodeSocket):
    """Any value (a table, a list, ...): connects to every kind of value wire"""
    bl_idname = "HL_AnySocket"
    bl_label = "Value"

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return ANY_COLOR

    @classmethod
    def draw_color_simple(cls):
        return ANY_COLOR


SOCKET_OF_KIND = {"num": "HL_FloatSocket", "int": "HL_IntSocket", "bool": "HL_BoolSocket", "text": "HL_TextSocket",
                  "vec": "HL_VectorSocket",
                  "thing": "HL_ThingSocket", "any": "HL_AnySocket"}


def _data_socket(sockets, kind, ident, label):
    return sockets.new(SOCKET_OF_KIND.get(kind, "HL_AnySocket"), label, identifier=ident)


@_keeps_links
def _rebuild_script_call(self, context=None):
    from ..core import vscript as vs
    self.inputs.clear()
    self.outputs.clear()
    f = vs.function(self.fn)
    if f is None:
        self.auto_title = ""
        return
    self.auto_title = vs.label(f)
    if not vs.is_pure(f):
        self.ev_in("run", "Run")
        self.ev_out("then", "Then")
    for ident, text, kind in vs.param_sockets(f):
        _data_socket(self.inputs, kind, ident, text)
    rk = vs.result_kind(f)
    if rk:
        _data_socket(self.outputs, rk, "result", "Result")


class HL_NodeScriptCall(_Node, bpy.types.Node):
    """One of the game's script functions: value functions just give a result, the others run when an
    event comes in (Run) and fire Then afterwards"""
    bl_idname, bl_label, bl_icon = "HL_NodeScriptCall", "Game Function", "SCRIPT"
    kind, category = "SCRIPT_CALL", "Script"
    fn: StringProperty(name="Function", default="", update=_rebuild_script_call)

    def make_sockets(self):
        _rebuild_script_call(self)

    def draw_buttons(self, context, layout):
        from ..core import vscript as vs
        f = vs.function(self.fn)
        op = layout.operator("hammerless.logic_pick_function", text=f"{f['group']}: {vs.label(f)}" if f else "Pick Function",
                             icon="VIEWZOOM")
        op.node_name = self.name

    def draw_buttons_ext(self, context, layout):
        from ..core import vscript as vs
        self.draw_buttons(context, layout)
        f = vs.function(self.fn)
        if f:
            box = layout.box()
            box.label(text=f"{f['returns']} {f['call'] if not f.get('on') else f['on'] + '.' + f['name']}({', '.join(f['params'])})")
            for line in _wrap(f.get("desc") or "", 40):
                box.label(text=line)

    def settings(self):
        return {"fn": self.fn}


@_keeps_links
def _rebuild_script_event(self, context=None):
    from ..core import vscript as vs
    self.inputs.clear()
    self.outputs.clear()
    e = vs.event(self.event)
    if e is None:
        self.auto_title = ""
        return
    self.auto_title = vs.event_label(e)
    self.ev_out("happened", "Happened")
    for fld in e["fields"]:
        _data_socket(self.outputs, vs.field_kind(fld), fld["name"], fld["name"].replace("_", " ").title())


class HL_NodeScriptEvent(_Node, bpy.types.Node):
    """Any of the game's events (player hurt, item picked up, tank killed...): fires Happened, with the
    event's details (who, what, how much) as values for the nodes it runs"""
    bl_idname, bl_label, bl_icon = "HL_NodeScriptEvent", "Game Event (any)", "LIGHT"
    kind, category = "SCRIPT_EVENT", "Script"
    event: StringProperty(name="Event", default="", update=_rebuild_script_event)

    def make_sockets(self):
        _rebuild_script_event(self)

    def draw_buttons(self, context, layout):
        from ..core import vscript as vs
        e = vs.event(self.event)
        op = layout.operator("hammerless.logic_pick_event", text=vs.event_label(e) if e else "Pick Event",
                             icon="VIEWZOOM")
        op.node_name = self.name

    def draw_buttons_ext(self, context, layout):
        from ..core import vscript as vs
        self.draw_buttons(context, layout)
        e = vs.event(self.event)
        if e:
            box = layout.box()
            for fld in e["fields"]:
                kind = "player" if fld.get("player") else "entity" if fld.get("entity") else fld["type"]
                box.label(text=f"{fld['name']} ({kind})")

    def settings(self):
        return {"event": self.event}


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return lines + ([line] if line else [])


def _function_items(self, context):
    from ..core import vscript as vs
    if not _function_items.cache:
        _function_items.cache = [(f["id"], f"{f['group']}: {vs.label(f)}", f.get("desc") or f["id"])
                                 for f in vs.catalogue()["functions"]]
    return _function_items.cache


_function_items.cache = []


def _event_items(self, context):
    from ..core import vscript as vs
    if not _event_items.cache:
        _event_items.cache = [(e["name"], vs.event_label(e), e["name"]) for e in vs.catalogue()["events"]]
    return _event_items.cache


_event_items.cache = []


def _node_in_editor(context, name):
    tree = getattr(context.space_data, "edit_tree", None)
    return tree.nodes.get(name) if tree else None


class HL_OT_logic_pick_function(bpy.types.Operator):
    bl_idname = "hammerless.logic_pick_function"
    bl_label = "Pick Game Function"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Search the game's script functions"
    bl_property = "choice"
    node_name: StringProperty(options={"HIDDEN"})
    choice: EnumProperty(items=_function_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        node = _node_in_editor(context, self.node_name)
        if node is not None:
            node.fn = self.choice
        return {"FINISHED"}


class HL_OT_logic_pick_event(bpy.types.Operator):
    bl_idname = "hammerless.logic_pick_event"
    bl_label = "Pick Game Event"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Search the game's events"
    bl_property = "choice"
    node_name: StringProperty(options={"HIDDEN"})
    choice: EnumProperty(items=_event_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        node = _node_in_editor(context, self.node_name)
        if node is not None:
            node.event = self.choice
        return {"FINISHED"}


def _script_menus():
    """Add > Script > Functions / Game Events: one submenu per group, each item a node already set up."""
    from ..core import vscript as vs
    menus = []

    def leaf(idname, title, entries, node_type, prop):
        def draw(self, context):
            for value, text in entries:
                op = self.layout.operator("node.add_node", text=text)
                op.type, op.use_transform = node_type, True
                s = op.settings.add()
                s.name, s.value = prop, repr(value)
        return type(idname, (bpy.types.Menu,), {"bl_idname": idname, "bl_label": title, "draw": draw})

    fn_menus = [leaf(f"HL_MT_logic_fn_{i}", group, [(f["id"], vs.label(f)) for f in fs], "HL_NodeScriptCall", "fn")
                for i, (group, fs) in enumerate(vs.groups())]
    ev_menus = [leaf(f"HL_MT_logic_ev_{i}", group, [(e["name"], vs.event_label(e)) for e in es], "HL_NodeScriptEvent",
                     "event") for i, (group, es) in enumerate(vs.event_groups())]

    def parent(idname, title, children, extra=None):
        def draw(self, context):
            if extra:
                op = self.layout.operator("node.add_node", text=extra[1], icon="VIEWZOOM")
                op.type, op.use_transform = extra[0], True
                self.layout.separator()
            for m in children:
                self.layout.menu(m.bl_idname)
        return type(idname, (bpy.types.Menu,), {"bl_idname": idname, "bl_label": title, "draw": draw})

    menus += fn_menus + ev_menus
    menus.append(parent("HL_MT_logic_game_functions", "All Game Functions", fn_menus, ("HL_NodeScriptCall", "Pick by Search...")))
    menus.append(parent("HL_MT_logic_game_events", "All Game Events", ev_menus, ("HL_NodeScriptEvent", "Pick by Search...")))
    return menus


SCRIPT_MENUS = _script_menus()


# ---------------------------------------------------------------- Script blocks: loops, variables, tables, text, vectors

VALUE_KIND_ITEMS = [("num", "Number", ""), ("bool", "True/False", ""), ("text", "Text", ""), ("vec", "Vector", ""),
                    ("thing", "Entity / Player", ""), ("any", "Anything", "")]


@_keeps_links
def _rebuild(self, context=None):
    self.inputs.clear()
    self.outputs.clear()
    self.make_sockets()


def _ensure(self, context=None):
    """Rebuild sockets when a setting changes which sockets the node has (wires to sockets that stay are kept:
    _keeps_links)."""
    _rebuild(self)


class _Block(_Node):
    category = "Script"

    def init(self, context):
        self.use_custom_color = True
        self.color = CATEGORY_COLORS["Script"]
        self.make_sockets()
        _record_sockets(self)

    def data_in(self, kind, ident, label):
        return _data_socket(self.inputs, kind, ident, label)

    def data_out(self, kind, ident, label):
        return _data_socket(self.outputs, kind, ident, label)


class HL_NodeForEach(_Block, bpy.types.Node):
    """Runs Each once for every survivor, infected, entity or list item (Item is the current one), then Done"""
    bl_idname, bl_label, bl_icon = "HL_NodeForEach", "For Each", "LOOP_FORWARDS"
    kind = "FOR_EACH"
    what: EnumProperty(name="For Each", default="SURVIVORS", update=_ensure, items=[
        ("SURVIVORS", "Survivor", "Every survivor (bots too)"),
        ("SPECIALS", "Infected Player", "Every living special infected / Tank"),
        ("PLAYERS", "Player", "Everyone"),
        ("COMMONS", "Common Infected", "Every common infected"),
        ("CLASS", "Entity of Class", "Every entity of a class (prop_physics, weapon_*...)"),
        ("NAME", "Entity Named", "Every entity with a name (* is a wildcard)"),
        ("LIST", "Item in a List", "Every item of a list or table wired in")])

    def make_sockets(self):
        self.ev_in("run", "Run")
        if self.what in ("CLASS", "NAME"):
            self.data_in("text", "match", "Class" if self.what == "CLASS" else "Name")
        if self.what == "LIST":
            self.data_in("any", "list", "List")
        self.ev_out("each", "Each")
        self.ev_out("done", "Done")
        self.data_out("any" if self.what == "LIST" else "thing", "item", "Item")
        self.data_out("num", "index", "Number")

    def draw_buttons(self, context, layout):
        layout.prop(self, "what", text="")

    def settings(self):
        return {"what": self.what}


SCOPE_ITEMS = [("MAP", "Map", "One value for the whole map"), ("PLAYER", "Per Player", "A value for each player (or each entity wired into Player: a prop, an infected)"),
               ("GROUP", "This Group Copy", "Inside a node group: a value for each Group node using it (outside a "
                                            "group: the same as Map)"), ("KEY", "Per Key", "Separate values by a text key, such as Human Identity"),
               ("OBJECT", "This Object", "A value belonging to the object running this behaviour, shared with its nested groups")]


from ..core.wall_weapon import ITEMS as PURCHASE_ITEMS
_PURCHASE_CHOICES = [("AUTO", "Auto from Display Model", "Infer the purchase from the display's world model", 0),
                     ("CUSTOM", "Custom Item", "GiveItem ID and inventory confirmation for custom items", 1)] + [
    (key, value["title"], key, value["enum_id"]) for key, value in sorted(PURCHASE_ITEMS.items())]


class HL_NodeWallWeapon(_Block, bpy.types.Node):
    """Sell a carried weapon or item for personal points using any display prop"""
    bl_idname, bl_label, bl_icon = "HL_NodeWallWeapon", "Purchase", "PROPERTIES"
    item: EnumProperty(name="Item to Give", items=_PURCHASE_CHOICES, default="AUTO", update=exposed.preview_node_changed)
    custom_item: StringProperty(name="Item ID", description="GiveItem name, e.g. rifle_ak47; inventory items only")
    inventory_class: StringProperty(name="Inventory Class", description="Optional confirmation classname; normally weapon_ plus Item ID")
    inventory_model: StringProperty(name="Inventory Model", description="Optional model to distinguish custom melee weapons in the same inventory class")
    title: StringProperty(name="Display Name", description="Optional name shown in the purchase prompt")
    kind = "WALL_WEAPON"
    preview_label = "Match Model to Item"

    def update_owner_preview(self,owner,slot,setting_id=None):
        # Never follow a reference and rewrite another object's display.
        display=next((s for s in self.inputs if s.identifier=='object'),None)
        if display is None:return
        if display.is_linked:
            sources=_sources(display)
            if len(sources)!=1 or sources[0][0].bl_idname!='HL_NodeSelf':return
        elif display.value!=owner:return
        if owner.type!='MESH' or owner.mode!='OBJECT':return
        item=self.item
        sock=next((s for s in self.inputs if s.identifier=='item'),None)
        if sock is not None and sock.is_linked:
            sources=_sources(sock)
            if len(sources)!=1 or sources[0][0].bl_idname!='HL_NodeExposedSetting':return
            if setting_id is not None and sources[0][0].setting_id!=setting_id:return
            item=exposed.resolve(sources[0][0],slot)
        elif setting_id is not None:return
        if item in ('AUTO','CUSTOM'):return
        data=PURCHASE_ITEMS.get(item)
        if data is None:return
        model=data['model']
        kv=next((k for k in owner.hammerless.keyvalues if k.key=='model'),None)
        if kv is not None and kv.value.replace('\\','/').lower()==model:return
        from .ops import preview_mesh,style_entity_object
        scene=next((s for s in owner.users_scene if s==bpy.context.scene),None)
        if scene is None:return
        mesh=preview_mesh('prop_dynamic_override',scene.hammerless.units_per_meter,model)
        # Assign a mesh pointer only to this object, never edit the shared mesh in place.
        owner.data=mesh
        if kv is None:kv=owner.hammerless.keyvalues.add();kv.key='model'
        kv.value=model
        style_entity_object(owner)

    price: IntProperty(name="Price", default=500, min=0, max=1000000)
    reach: IntProperty(name="Use Range", default=96, min=32, max=256,
                       description="Maximum distance in game units")

    def make_sockets(self):
        self.inputs.new("HL_ObjectSocket", "Display Object", identifier="object")
        self.data_in('text','item','Item').hide_value=True
        self.data_in('num','price','Price').hide_value=True
        self.data_in('text','title','Display Name').hide_value=True
        self.data_in('num','range','Use Range').hide_value=True

    def draw_buttons(self, context, layout):
        row=layout.row()
        sock=next((s for s in self.inputs if s.identifier=='item'),None)
        row.enabled=sock is None or not sock.is_linked
        row.prop(self,"item")
        if self.item == "CUSTOM":
            layout.prop(self, "custom_item")
            layout.prop(self, "inventory_class")
            layout.prop(self, "inventory_model")
        for prop,ident in (("title","title"),("price","price"),("reach","range")):
            row=layout.row()
            sock=next((s for s in self.inputs if s.identifier==ident),None)
            row.enabled=sock is None or not sock.is_linked
            row.prop(self,prop)
        layout.label(text="Requires Personal Points")

    def settings(self):
        return {"price": self.price, "range": self.reach, "item": self.item, "custom_item": self.custom_item,
                "inventory_class": self.inventory_class, "inventory_model": self.inventory_model, "title": self.title}

    def to_lnode(self, context):
        n = self.with_object(context, super().to_lnode(context))
        from .preview import purchase_display_transform
        transform = purchase_display_transform(self.object_from("object"), context)
        if transform is not None:
            n.settings["display_transform"] = transform
        return n


class HL_NodeInventoryItem(_Block, bpy.types.Node):
    """Reusable carried-item definition for inventory checks, grants and display models"""
    bl_idname, bl_label, bl_icon = "HL_NodeInventoryItem", "Inventory Item", "PROPERTIES"
    kind = "INVENTORY_ITEM"
    item: EnumProperty(name="Item", items=_PURCHASE_CHOICES, default="AUTO", update=exposed.preview_node_changed)
    custom_item: StringProperty(name="Item ID")
    inventory_class: StringProperty(name="Inventory Class")
    inventory_model: StringProperty(name="Inventory Model")
    preview_label = "Match Model to Item"
    update_owner_preview = HL_NodeWallWeapon.update_owner_preview
    def to_lnode(self, context):
        n = self.with_object(context, super().to_lnode(context))
        from .preview import purchase_display_transform
        transform = purchase_display_transform(self.object_from("object"), context)
        if transform is not None:
            n.settings["display_transform"] = transform
        return n

    def make_sockets(self):
        self.inputs.new("HL_ObjectSocket", "Display Object", identifier="object")
        self.data_in("text", "item", "Item").hide_value = True
        self.data_out("any", "value", "Item")
        self.data_out("text", "title", "Name")
        self.data_out("text", "model", "Model")
        self.data_out("thing", "entity", "Display Entity")

    def settings(self):
        return {"item": self.item, "custom_item": self.custom_item,
                "inventory_class": self.inventory_class, "inventory_model": self.inventory_model}

    def draw_buttons(self, context, layout):
        layout.prop(self, "item")
        if self.item == "CUSTOM":
            for key in ("custom_item", "inventory_class", "inventory_model"):
                layout.prop(self, key)


class HL_NodeHumanIdentity(_Block, bpy.types.Node):
    """Stable human survivor identity; empty for bots and non-survivors"""
    bl_idname, bl_label, bl_icon = "HL_NodeHumanIdentity", "Human Identity", "USER"
    kind = "HUMAN_ID"

    def make_sockets(self):
        self.data_in("thing", "player", "Player")
        self.data_out("text", "key", "Key")


class HL_NodeInteraction(_Block, bpy.types.Node):
    """Look at the nearest reachable target; emits Focused periodically or Used on a new Use press"""
    bl_idname, bl_label, bl_icon = "HL_NodeInteraction", "Look / Use Target", "RESTRICT_SELECT_OFF"
    kind = "INTERACTION"

    def make_sockets(self):
        self.data_in("thing", "entity", "Target")
        self.data_in("num", "range", "Range").value = 96
        self.ev_out("focused", "Focused")
        self.ev_out("used", "Used")
        self.ev_out("lost", "Lost Focus")
        self.data_out("thing", "player", "Player")
        self.data_out("text", "key", "Human Key")


class HL_NodeHasInventory(_Block, bpy.types.Node):
    """Check a player's carried inventory against an Inventory Item definition"""
    bl_idname, bl_label, bl_icon = "HL_NodeHasInventory", "Has Inventory Item", "QUESTION"
    kind = "HAS_INVENTORY"

    def make_sockets(self):
        self.data_in("thing", "player", "Player")
        self.data_in("any", "item", "Item")
        self.data_out("bool", "result", "Has Item")


class HL_NodeGiveInventory(_Block, bpy.types.Node):
    """Give a carried item; confirm inventory within 0.5 seconds, or fire Failed (including disconnect)"""
    bl_idname, bl_label, bl_icon = "HL_NodeGiveInventory", "Give Inventory Item", "ADD"
    kind = "GIVE_INVENTORY"

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("thing", "player", "Player")
        self.data_in("any", "item", "Item")
        self.ev_out("success", "Delivered")
        self.ev_out("failure", "Failed")


class HL_NodeEmergencyKnife(_Block, bpy.types.Node):
    """Give one emergency knife for the enabled enemies, at most two per human. Hold Reload for one second while pinned to escape and stagger it. Resets on death and round end."""
    bl_idname, bl_label, bl_icon = "HL_NodeEmergencyKnife", "Emergency Knife Stock", "ADD"
    kind = "EMERGENCY_KNIFE"

    def make_sockets(self):
        self.ev_in("run", "Give One")
        self.data_in("thing", "player", "Player")
        for key, default in (("smoker", True), ("hunter", True), ("jockey", False), ("charger", False)):
            self.data_in("bool", key, "Charger (Pummel Only)" if key == "charger" else key.title()).value = default
        self.data_out("num", "count", "Carried")
        self.ev_out("success", "Given")
        self.ev_out("failure", "Full / Unavailable")


class HL_NodeResetKeyVariable(_Block, bpy.types.Node):
    """Clear one named variable for every key; other named variables are untouched"""
    bl_idname, bl_label, bl_icon = "HL_NodeResetKeyVariable", "Reset Key Variable", "LOOP_BACK"
    kind = "RESET_KEY_VAR"
    var_name: StringProperty(name="Name", default="score")
    exclusive: BoolProperty(name="Single Controller", default=False,
                            description="Report an export error if another exclusive reset controls this variable")

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.ev_out("then", "Then")

    def settings(self):
        return {"name": self.var_name, "exclusive": self.exclusive}

    def draw_buttons(self, context, layout):
        layout.prop(self, "var_name", text="")
        layout.prop(self, "exclusive")


class HL_NodePersonalPoints(_Block, bpy.types.Node):
    """Personal kill points for human survivors; one controller per map. Type !points in chat"""
    bl_idname, bl_label, bl_icon = "HL_NodePersonalPoints", "Personal Points", "PROPERTIES"
    kind = "PERSONAL_POINTS"
    starting: IntProperty(name="Starting Points", default=0, min=0, max=1000000)
    common: IntProperty(name="Common Kill", default=10, min=0, max=1000000)
    special: IntProperty(name="Special Kill", default=50, min=0, max=1000000)
    tank: IntProperty(name="Tank Kill", default=500, min=0, max=1000000)
    witch: IntProperty(name="Witch Kill", default=250, min=0, max=1000000)
    feedback: BoolProperty(name="Show Earned Points", default=True,
                           description="Show a private centre-screen message to the killer")

    def make_sockets(self):
        pass

    def draw_buttons(self, context, layout):
        for key in ("starting", "common", "special", "tank", "witch", "feedback"):
            layout.prop(self, key)
        layout.label(text="Chat: !points")
        layout.label(text="Resets each round")

    def settings(self):
        return {key: getattr(self, key) for key in
                ("starting", "common", "special", "tank", "witch", "feedback")}


class HL_NodeSetVariable(_Block, bpy.types.Node):
    """Stores a value under a name (for the map, or for one player), for Get Variable to read later"""
    bl_idname, bl_label, bl_icon = "HL_NodeSetVariable", "Set Variable", "PROPERTIES"
    kind = "SET_VAR"
    var_name: StringProperty(name="Name", default="score")
    scope: EnumProperty(name="For", items=SCOPE_ITEMS, default="MAP", update=_ensure)
    value_kind: EnumProperty(name="Value", items=VALUE_KIND_ITEMS, default="num", update=_ensure)
    op: EnumProperty(name="Do", default="SET", items=[("SET", "Set", "Replace the value"),
                                                     ("ADD", "Add", "Add to the number already stored (text: add on to the end)")])
    keep: BoolProperty(name="Keep Across Maps", default=False,
                       description="Remember the value in the next chapter of the campaign (map variables)")

    def make_sockets(self):
        self.ev_in("run", "Run")
        if self.scope == "KEY":
            self.data_in("text", "key", "Key")
        if self.scope == "PLAYER":
            self.data_in("thing", "player", "Player")
        self.data_in(self.value_kind, "value", "Value")
        self.ev_out("then", "Then")

    def draw_buttons(self, context, layout):
        layout.prop(self, "var_name", text="")
        row = layout.row(align=True)
        row.prop(self, "op", text="")
        row.prop(self, "scope", text="")
        layout.prop(self, "value_kind", text="")
        if self.scope == "MAP":
            layout.prop(self, "keep")

    def settings(self):
        return {"name": self.var_name, "scope": self.scope, "op": self.op, "keep": self.keep, "kind": self.value_kind}


class HL_NodeGetVariable(_Block, bpy.types.Node):
    """Reads a value Set Variable stored (Number 0 / empty until something is stored)"""
    bl_idname, bl_label, bl_icon = "HL_NodeGetVariable", "Get Variable", "PROPERTIES"
    kind = "GET_VAR"
    var_name: StringProperty(name="Name", default="score")
    scope: EnumProperty(name="For", items=SCOPE_ITEMS, default="MAP", update=_ensure)
    value_kind: EnumProperty(name="Value", items=VALUE_KIND_ITEMS, default="num", update=_ensure)

    def make_sockets(self):
        if self.scope == "KEY":
            self.data_in("text", "key", "Key")
        if self.scope == "PLAYER":
            self.data_in("thing", "player", "Player")
        if self.scope == "KEY":
            self.data_in(self.value_kind, "default", "Initial Value")
        self.data_out(self.value_kind, "result", "Value")

    def draw_buttons(self, context, layout):
        layout.prop(self, "var_name", text="")
        row = layout.row(align=True)
        row.prop(self, "scope", text="")
        row.prop(self, "value_kind", text="")

    def settings(self):
        return {"name": self.var_name, "scope": self.scope, "kind": self.value_kind}


class HL_NodeMakeTable(_Block, bpy.types.Node):
    """Builds a table of named values (what Spawn Zombie, Trace Line, Spawn Entity... take).
    Fields: name: kind, ... (kinds: num, int, bool, text, vec, thing, any)"""
    bl_idname, bl_label, bl_icon = "HL_NodeMakeTable", "Make Table", "PRESET"
    kind = "MAKE_TABLE"
    fields: StringProperty(name="Fields", default="type: int, pos: vec", update=_ensure,
                           description="name: kind, ... (kinds: num, int, bool, text, vec, thing, any)")

    def make_sockets(self):
        from ..core.logic import parse_fields
        for name, kind in parse_fields(self.fields):
            self.data_in(kind, "f_" + name, name)       # (int: a whole-number input, every digit kept: masks, flags)
        self.data_out("any", "table", "Table")

    def draw_buttons(self, context, layout):
        layout.prop(self, "fields", text="")

    def settings(self):
        return {"fields": self.fields}


class HL_NodeGetField(_Block, bpy.types.Node):
    """A table's field by name, or a list's item by number (0 = the first); Count = how many"""
    bl_idname, bl_label, bl_icon = "HL_NodeGetField", "Get Field", "VIEWZOOM"
    kind = "GET_FIELD"

    def make_sockets(self):
        self.data_in("any", "table", "Table / List")
        self.data_in("text", "key", "Field / Number")
        self.data_out("any", "value", "Value")
        self.data_out("num", "count", "Count")


class HL_NodeFormatText(_Block, bpy.types.Node):
    """Text with values put in: {a} {b} {c} {d} are replaced by the inputs (players by their names)"""
    bl_idname, bl_label, bl_icon = "HL_NodeFormatText", "Format Text", "FONT_DATA"
    kind = "FORMAT_TEXT"
    template: StringProperty(name="Text", default="{a} has {b}")

    def make_sockets(self):
        for x in "abcd":
            self.data_in("any", x, x.upper())
        self.data_out("text", "result", "Text")

    def draw_buttons(self, context, layout):
        layout.prop(self, "template", text="")

    def settings(self):
        return {"template": self.template}


class HL_NodeMakeVector(_Block, bpy.types.Node):
    """A vector from X, Y, Z (Hammer units)"""
    bl_idname, bl_label, bl_icon = "HL_NodeMakeVector", "Make Vector", "EMPTY_ARROWS"
    kind = "MAKE_VECTOR"

    def make_sockets(self):
        for x in "xyz":
            self.data_in("num", x, x.upper())
        self.data_out("vec", "result", "Vector")


class HL_NodeBreakVector(_Block, bpy.types.Node):
    """A vector's X, Y and Z"""
    bl_idname, bl_label, bl_icon = "HL_NodeBreakVector", "Break Vector", "EMPTY_ARROWS"
    kind = "BREAK_VECTOR"

    def make_sockets(self):
        self.data_in("vec", "vector", "Vector")
        for x in "xyz":
            self.data_out("num", x, x.upper())


class HL_NodeVectorMath(_Block, bpy.types.Node):
    """Maths on vectors: add, subtract, scale, distance, length, normalize, dot, cross"""
    bl_idname, bl_label, bl_icon = "HL_NodeVectorMath", "Vector Math", "ORIENTATION_GLOBAL"
    kind = "VECTOR_MATH"
    op: EnumProperty(name="Operation", default="ADD", items=[
        ("ADD", "Add", ""), ("SUBTRACT", "Subtract", ""), ("SCALE", "Scale (A x Scale)", ""),
        ("DISTANCE", "Distance (A to B)", ""), ("LENGTH", "Length (A)", ""), ("NORMALIZE", "Normalize (A)", ""),
        ("DOT", "Dot", ""), ("CROSS", "Cross", "")])

    def make_sockets(self):
        self.data_in("vec", "a", "A")
        self.data_in("vec", "b", "B")
        self.data_in("num", "scale", "Scale")
        self.data_out("vec", "vector", "Vector")
        self.data_out("num", "value", "Value")

    def draw_buttons(self, context, layout):
        layout.prop(self, "op", text="")

    def settings(self):
        return {"op": self.op}


class HL_NodeCheck(_Block, bpy.types.Node):
    """True/false about any value: is it set (an entity still in the game), equal, not equal"""
    bl_idname, bl_label, bl_icon = "HL_NodeCheck", "Compare Values", "QUESTION"
    kind = "CHECK"
    op: EnumProperty(name="Check", default="IS_SET", items=[
        ("IS_SET", "A Is Set", "A has a value (an entity: still in the game)"),
        ("NOT_SET", "A Is Not Set", ""), ("EQUAL", "A = B", ""), ("NOT_EQUAL", "A ≠ B", "")])

    def make_sockets(self):
        self.data_in("any", "a", "A")
        self.data_in("any", "b", "B")
        self.bool_out("result", "Result")

    def draw_buttons(self, context, layout):
        layout.prop(self, "op", text="")

    def settings(self):
        return {"op": self.op}


class HL_NodeTextCheck(_Block, bpy.types.Node):
    """True/false about text: does it contain, start with, end with or equal another text (an empty or missing text
    is false)"""
    bl_idname, bl_label, bl_icon = "HL_NodeTextCheck", "Text Check", "SORTALPHA"
    kind = "TEXT_CHECK"
    ignore_case: BoolProperty(name="Ignore Case", default=False)
    op: EnumProperty(name="Check", default="CONTAINS", items=[
        ("CONTAINS", "Contains", "B is somewhere in A"), ("STARTS", "Starts With", "A starts with B"),
        ("ENDS", "Ends With", "A ends with B"), ("EQUAL", "Equals", "A and B are the same text")])

    def make_sockets(self):
        self.data_in("text", "a", "Text")
        self.data_in("text", "b", "Look For")
        self.bool_out("result", "Result")

    def draw_buttons(self, context, layout):
        layout.prop(self, "op", text="")
        layout.prop(self, "ignore_case")

    def settings(self):
        return {"op": self.op, "ignore_case": self.ignore_case}


class HL_NodeSplitText(_Block, bpy.types.Node):
    """Cuts text into a list at each Separator (spaces around each piece trimmed, empty pieces left out): 'a, b, c'
    gives a list of 3 (Get Field reads one by number, 0 = the first; For Each goes through them)"""
    bl_idname, bl_label, bl_icon = "HL_NodeSplitText", "Split Text", "ALIGN_JUSTIFY"
    kind = "SPLIT_TEXT"

    def make_sockets(self):
        self.data_in("text", "text", "Text")
        sep = self.data_in("text", "separator", "Separator")
        sep.value = ","
        self.data_out("any", "list", "List")
        self.data_out("num", "count", "Count")


class HL_NodeFireInput(_Block, bpy.types.Node):
    """Sends an input (Kill, TurnOn, Explode, SetParent...) to an entity wired in, e.g. one a Spawn Entity just made,
    after Delay seconds"""
    bl_idname, bl_label, bl_icon = "HL_NodeFireInput", "Fire Input", "FORWARD"
    kind = "FIRE_INPUT"

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("thing", "entity", "Entity")
        self.data_in("text", "input", "Input")
        self.data_in("text", "value", "Value")
        self.data_in("num", "delay", "Delay")
        self.data_in("thing", "activator", "Activator")
        self.ev_out("then", "Then")


class HL_NodePickValue(_Block, bpy.types.Node):
    """One of two values: If True when the condition is true, else If False"""
    bl_idname, bl_label, bl_icon = "HL_NodePickValue", "Pick Value", "ARROW_LEFTRIGHT"
    kind = "PICK"
    value_kind: EnumProperty(name="Value", items=VALUE_KIND_ITEMS, default="num", update=_ensure)

    def make_sockets(self):
        self.bool_in("condition", "Condition")
        self.data_in(self.value_kind, "a", "If True")
        self.data_in(self.value_kind, "b", "If False")
        self.data_out(self.value_kind, "value", "Value")

    def draw_buttons(self, context, layout):
        layout.prop(self, "value_kind", text="")

    def settings(self):
        return {"kind": self.value_kind}


def _code_text(node) -> str:
    return node.text.as_string() if node.text is not None else node.code


class HL_NodeScriptCode(_Block, bpy.types.Node):
    """Your own Squirrel (L4D2 VScript): inputs arrive as a, b, c, d; set result for the Result output"""
    bl_idname, bl_label, bl_icon = "HL_NodeScriptCode", "Script", "CONSOLE"
    kind = "SCRIPT_CODE"
    code: StringProperty(name="Code", default='result = "hello " + a;')
    text: PointerProperty(type=bpy.types.Text, name="Text",
                          description="Use a text block (Blender's Text Editor) for longer code")

    def make_sockets(self):
        self.ev_in("run", "Run")
        for x in "abcd":
            self.data_in("any", x, x)
        self.ev_out("then", "Then")
        self.data_out("any", "result", "Result")

    def draw_buttons(self, context, layout):
        layout.prop(self, "text", text="")
        if self.text is None:
            layout.prop(self, "code", text="")

    def settings(self):
        return {"code": _code_text(self)}


class HL_NodeScriptValue(_Block, bpy.types.Node):
    """A Squirrel expression worked out from a, b, c, d (e.g. a * 2 + b)"""
    bl_idname, bl_label, bl_icon = "HL_NodeScriptValue", "Script Value", "CONSOLE"
    kind = "SCRIPT_VALUE"
    expr: StringProperty(name="Expression", default="a + b")

    def make_sockets(self):
        for x in "abcd":
            self.data_in("any", x, x)
        self.data_out("any", "result", "Result")

    def draw_buttons(self, context, layout):
        layout.prop(self, "expr", text="")

    def settings(self):
        return {"expr": self.expr}


# ---------------------------------------------------------------- Combat & Physics: lines traced through the world,
# damage, pushes, aiming, explosions and effects, who sees what (generic pieces for turrets, mines, grapples, ...)

TRACE_HITS = [("SHOTS", "Shots", "Stops at what bullets hit: the world, props, players and infected"),
              ("SIGHT", "Sight", "Stops at what blocks seeing: the world and solid props (not glass, players or "
                                 "infected)"),
              ("WORLD", "World Only", "Stops only at the map's walls, floors and brush entities")]


class HL_NodeTrace(_Block, bpy.types.Node):
    """Traces a straight line from Start to End and tells what it hits first (Hit), or that nothing is in the way
    (Missed). Hit Position, Hit Entity and Distance hold the last trace's answer"""
    bl_idname, bl_label, bl_icon = "HL_NodeTrace", "Trace Line", "IPO_LINEAR"
    kind = "TRACE"
    hits: EnumProperty(name="Hits", default="SHOTS", items=TRACE_HITS)

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("vec", "start", "Start")
        self.data_in("vec", "end", "End")
        self.data_in("thing", "ignore", "Ignore")
        self.ev_out("hit", "Hit")
        self.ev_out("missed", "Missed")
        self.data_out("vec", "pos", "Hit Position")
        self.data_out("thing", "entity", "Hit Entity")
        self.data_out("num", "distance", "Distance")

    def draw_buttons(self, context, layout):
        layout.prop(self, "hits", text="")

    def settings(self):
        return {"hits": self.hits}


DAMAGE_TYPES = [("0", "Generic", ""), ("2", "Bullet", ""), ("64", "Blast", ""), ("8", "Burn", ""),
                ("4", "Slash", ""), ("128", "Club", "")]


class HL_NodeDamage(_Block, bpy.types.Node):
    """Hurts an entity (a player, an infected, a breakable prop) by Amount, as if Attacker did it (empty: the
    world). The game keeps survivors from being hurt this way until someone has left the safe room"""
    bl_idname, bl_label, bl_icon = "HL_NodeDamage", "Damage", "MOD_PHYSICS"
    kind = "DAMAGE"
    damage_type: EnumProperty(name="Type", default="2", items=DAMAGE_TYPES)

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("thing", "entity", "Entity")
        self.data_in("num", "amount", "Amount").value = 10.0
        self.data_in("thing", "attacker", "Attacker")
        self.ev_out("then", "Then")

    def draw_buttons(self, context, layout):
        layout.prop(self, "damage_type", text="")

    def settings(self):
        return {"type": self.damage_type}


class HL_NodePush(_Block, bpy.types.Node):
    """Pushes an entity: adds Velocity to how it moves (Add), or makes it move at exactly Velocity (Set). Hammer
    units per second; up is +Z (700 up lifts a survivor about 170 units). Survivors can't be pushed until someone
    has left the safe room"""
    bl_idname, bl_label, bl_icon = "HL_NodePush", "Push", "FORCE_FORCE"
    kind = "PUSH"
    mode: EnumProperty(name="Mode", default="ADD", items=[
        ("ADD", "Add", "Adds to how it moves now (a push, a jump pad, a blast)"),
        ("SET", "Set", "Moves it at exactly this velocity (not physics props: use Add)")])

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("thing", "entity", "Entity")
        self.data_in("vec", "velocity", "Velocity")
        self.ev_out("then", "Then")

    def draw_buttons(self, context, layout):
        layout.prop(self, "mode", text="")

    def settings(self):
        return {"mode": self.mode}


class HL_NodeAimAt(_Block, bpy.types.Node):
    """Turns an entity to face a position (a player: their view). Max Turn limits how many degrees it turns each
    time it runs (0 = at once), so run it from a Timer for a smooth turn"""
    bl_idname, bl_label, bl_icon = "HL_NodeAimAt", "Aim At", "ORIENTATION_VIEW"
    kind = "AIM"
    yaw_only: BoolProperty(name="Turn Sideways Only", default=False,
                           description="Only turns left and right (keeps its up and down tilt)")

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("thing", "entity", "Entity")
        self.data_in("vec", "target", "Target")
        self.data_in("num", "max_turn", "Max Turn")
        self.ev_out("then", "Then")

    def draw_buttons(self, context, layout):
        layout.prop(self, "yaw_only")

    def settings(self):
        return {"yaw_only": self.yaw_only}


class HL_NodeExplosion(_Block, bpy.types.Node):
    """An explosion at a position: Damage to everything within Radius, with the game's blast effect and sound"""
    bl_idname, bl_label, bl_icon = "HL_NodeExplosion", "Explosion", "OUTLINER_OB_FORCE_FIELD"
    kind = "EXPLOSION"
    push: BoolProperty(name="Push Props", default=True, description="Physics props nearby are thrown away")
    shake: BoolProperty(name="Shake Screens", default=True, description="Screens near it shake")

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("vec", "position", "Position")
        self.data_in("num", "damage", "Damage").value = 100.0
        self.data_in("num", "radius", "Radius").value = 256.0
        self.ev_out("then", "Then")

    def draw_buttons(self, context, layout):
        layout.prop(self, "push")
        layout.prop(self, "shake")

    def settings(self):
        return {"push": self.push, "shake": self.shake}


class HL_NodeBeam(_Block, bpy.types.Node):
    """A straight beam of light from Start to End (a laser, a tracer, a tripwire, a cable) for Seconds
    (0 = stays)"""
    bl_idname, bl_label, bl_icon = "HL_NodeBeam", "Beam", "LIGHT_SUN"
    kind = "BEAM"

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("vec", "start", "Start")
        self.data_in("vec", "end", "End")
        self.data_in("text", "colour", "Colour").value = "255 40 40"
        self.data_in("num", "width", "Width").value = 2.0
        self.data_in("num", "seconds", "Seconds").value = 0.1
        self.ev_out("then", "Then")
        self.data_out("thing", "beam", "Beam")


class HL_NodeParticle(_Block, bpy.types.Node):
    """Plays one of the game's particle effects at Position, or riding on Attach To, for Seconds (0 = stays). The
    effect is the game's name for it (e.g. fire_small_01)"""
    bl_idname, bl_label, bl_icon = "HL_NodeParticle", "Particle Effect", "PARTICLES"
    kind = "PARTICLE"

    def make_sockets(self):
        self.ev_in("run", "Run")
        self.data_in("text", "effect", "Effect")
        self.data_in("vec", "position", "Position")
        self.data_in("thing", "attach", "Attach To")
        self.data_in("num", "seconds", "Seconds").value = 2.0
        self.ev_out("then", "Then")
        self.data_out("thing", "particle", "Particle")


class HL_NodeSeenBy(_Block, bpy.types.Node):
    """Whether someone is looking at a target: it is within View Angle of the centre of their view and (Clear Sight)
    nothing blocks the line. Viewer empty: any living survivor. Seen By is who sees it"""
    bl_idname, bl_label, bl_icon = "HL_NodeSeenBy", "Seen By", "HIDE_OFF"
    kind = "SEEN"
    clear_sight: BoolProperty(name="Clear Sight", default=True,
                              description="Walls and solid props in between hide it (off: only where they look)")

    def make_sockets(self):
        self.data_in("thing", "target", "Target")
        self.data_in("vec", "position", "Or Position")
        self.data_in("thing", "viewer", "Viewer")
        self.data_in("num", "angle", "View Angle").value = 45.0
        self.bool_out("seen", "Seen")
        self.data_out("thing", "who", "Seen By")

    def draw_buttons(self, context, layout):
        layout.prop(self, "clear_sight")

    def settings(self):
        return {"clear_sight": self.clear_sight}


class HL_NodeFindNearby(_Block, bpy.types.Node):
    """What is within Radius of a position: the Nearest one, all of them as a List (for For Each) and how many"""
    bl_idname, bl_label, bl_icon = "HL_NodeFindNearby", "Find Nearby", "VIEWZOOM"
    kind = "FIND_NEAR"
    what: EnumProperty(name="Find", default="INFECTED", update=_ensure, items=[
        ("SURVIVORS", "Survivors", "Living survivors (bots too)"),
        ("SPECIALS", "Infected Players", "Living special infected and Tanks"),
        ("COMMONS", "Common Infected", ""),
        ("INFECTED", "Any Infected", "Common infected, special infected and Tanks"),
        ("PLAYERS", "Players", "Living survivors and infected players"),
        ("CLASS", "Entity of Class", "Entities of a class (prop_physics, weapon_*...)"),
        ("NAME", "Entity Named", "Entities with a name (* is a wildcard)")])

    def make_sockets(self):
        self.data_in("vec", "position", "Position")
        self.data_in("num", "radius", "Radius").value = 500.0
        if self.what in ("CLASS", "NAME"):
            self.data_in("text", "match", "Class" if self.what == "CLASS" else "Name")
        self.data_in("thing", "ignore", "Ignore")
        self.data_out("thing", "nearest", "Nearest")
        self.data_out("any", "list", "List")
        self.data_out("num", "count", "Count")

    def draw_buttons(self, context, layout):
        layout.prop(self, "what", text="")

    def settings(self):
        return {"what": self.what}


class HL_NodePlayerView(_Block, bpy.types.Node):
    """Where a player's eyes are and which way they look (Look Direction: one unit long; scale it with Vector
    Math for a point ahead of them). Other entities: their position and facing"""
    bl_idname, bl_label, bl_icon = "HL_NodePlayerView", "Player View", "CAMERA_DATA"
    kind = "VIEW"

    def make_sockets(self):
        self.data_in("thing", "player", "Player")
        self.data_out("vec", "eye", "Eye Position")
        self.data_out("vec", "forward", "Look Direction")
        self.data_out("vec", "angles", "Look Angles")


COMBAT_CLASSES = (HL_NodeTrace, HL_NodeDamage, HL_NodePush, HL_NodeAimAt, HL_NodeExplosion, HL_NodeBeam,
                  HL_NodeParticle, HL_NodeSeenBy, HL_NodeFindNearby, HL_NodePlayerView)


BLOCK_CLASSES = (HL_NodeEmergencyKnife, HL_NodeInventoryItem, HL_NodeHumanIdentity, HL_NodeInteraction, HL_NodeHasInventory, HL_NodeGiveInventory, HL_NodeResetKeyVariable, HL_NodeForEach, HL_NodeWallWeapon, HL_NodePersonalPoints, HL_NodeSetVariable, HL_NodeGetVariable, HL_NodeMakeTable, HL_NodeGetField,
                 HL_NodeFormatText, HL_NodeMakeVector, HL_NodeBreakVector, HL_NodeVectorMath, HL_NodeCheck,
                 HL_NodePickValue, HL_NodeTextCheck, HL_NodeSplitText, HL_NodeFireInput, HL_NodeScriptCode, HL_NodeScriptValue) + COMBAT_CLASSES


# ---------------------------------------------------------------- Director settings, HUD, overrides

def _option_items(self, context):
    from ..core.director_options import OPTIONS
    if not _option_items.cache:
        _option_items.cache = [(k, f"{group}: {k}", desc) for k, _kind, _v, group, desc in OPTIONS]
    return _option_items.cache


_option_items.cache = []


@_keeps_links
def _rebuild_option(self, context=None):
    from ..core.director_options import BY_KEY
    self.inputs.clear()
    self.outputs.clear()
    self.ev_in("run", "Run")
    opt = BY_KEY.get(self.key)
    if opt is not None and self.op == "SET":
        kind = {"int": "num"}.get(opt[1], opt[1])
        s = _data_socket(self.inputs, kind, "value", "Value")
        if opt[2] is not None and hasattr(s, "value"):
            s.value = opt[2] if kind != "num" else float(opt[2])
    self.ev_out("then", "Then")
    self.auto_title = f"{'Reset' if self.op == 'RESET' else 'Set'} {self.key}" if opt else ""


class HL_NodeDirectorOption(_Block, bpy.types.Node):
    """Changes one of the AI Director's settings while the map runs (stays on until changed or reset)"""
    bl_idname, bl_label, bl_icon = "HL_NodeDirectorOption", "Director Setting", "MODIFIER"
    kind = "DIRECTOR_OPTION"
    category = "Director"
    key: StringProperty(name="Setting", default="CommonLimit", update=_rebuild_option)
    op: EnumProperty(name="Do", default="SET", update=_rebuild_option, items=[
        ("SET", "Set", "Change the setting"), ("RESET", "Reset", "Back to what the map's Director scripts say")])

    def init(self, context):
        self.use_custom_color = True
        self.color = CATEGORY_COLORS["Director"]
        _rebuild_option(self)

    def make_sockets(self):
        _rebuild_option(self)

    def draw_buttons(self, context, layout):
        op = layout.operator("hammerless.logic_pick_option", text=self.key or "Pick Setting", icon="VIEWZOOM")
        op.node_name = self.name
        layout.prop(self, "op", expand=True)

    def draw_buttons_ext(self, context, layout):
        from ..core.director_options import BY_KEY
        self.draw_buttons(context, layout)
        opt = BY_KEY.get(self.key)
        if opt:
            box = layout.box()
            for line in _wrap(opt[4], 40):
                box.label(text=line)

    def settings(self):
        return {"key": self.key, "op": self.op}


class HL_OT_logic_pick_option(bpy.types.Operator):
    bl_idname = "hammerless.logic_pick_option"
    bl_label = "Pick Director Setting"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Search the AI Director's settings"
    bl_property = "choice"
    node_name: StringProperty(options={"HIDDEN"})
    choice: EnumProperty(items=_option_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        node = _node_in_editor(context, self.node_name)
        if node is not None:
            node.key = self.choice
        return {"FINISHED"}


# ---------------------------------------------------------------- entity properties (NetProps)

def _prop_items(self, context):
    """The picker's list: properties with a description first ("Common"), then every other; only those the
    node's For class has when one is set."""
    from ..core import netprops
    node = _node_in_editor(context, getattr(self, "node_name", ""))
    cls = (node.for_class.strip().lower() if node is not None else "")       # (server classes are CamelCase)
    if _prop_items.cache.get("for") != cls or not _prop_items.cache.get("items"):
        items = []
        for name, p in sorted(netprops.props().items(), key=lambda kv: (not kv[1].get("desc"), kv[0].lower())):
            if cls and cls not in (c.lower() for c in p.get("classes", ())):
                continue
            what = p["type"] + (f" list of {p['size']}" if p.get("size") else "")
            label = f"{'Common: ' if p.get('desc') else ''}{name} ({what})"
            tip = p.get("desc") or ("Has it: " + ", ".join(p.get("classes", [])[:8])
                                    + (" ..." if len(p.get("classes", [])) > 8 else ""))
            items.append((name, label, tip))
        _prop_items.cache = {"for": cls, "items": items or [("_NONE_", "Nothing for that class", "")]}
    return _prop_items.cache["items"]


_prop_items.cache = {}

PROP_TYPES = [("int", "Whole Number", ""), ("float", "Number", ""), ("vector", "Vector", ""),
              ("string", "Text", ""), ("entity", "Entity", "")]


@_keeps_links
def _rebuild_prop(self, context=None):
    from ..core import netprops
    self.inputs.clear()
    self.outputs.clear()
    setter = self.kind == "SET_PROP"
    t, is_list = netprops.type_of(self.key, self.prop_type, self.list)
    kind = netprops.KINDS[t]
    if setter:
        self.ev_in("run", "Run")
    _data_socket(self.inputs, "thing", "entity", "Entity")
    if is_list:
        _data_socket(self.inputs, "num", "index", "Index")
    if setter:
        if t == "int":                       # (exact: colours with their alpha, flags; see HL_IntSocket)
            self.inputs.new("HL_IntSocket", "Value", identifier="value")
        else:
            _data_socket(self.inputs, kind, "value", "Value")
        self.ev_out("then", "Then")
    else:
        _data_socket(self.outputs, kind, "value", "Value")
    self.auto_title = self.key


class _PropNode(_Block):
    key: StringProperty(name="Property", default="", update=_rebuild_prop,
                        description="The property's name, e.g. m_iHealth (Pick searches the game's list)")
    for_class: StringProperty(name="For", default="",
                              description="Only list the properties this entity class has (player, prop_dynamic, "
                                          "infected...). Empty: every property")
    prop_type: EnumProperty(name="Type", items=PROP_TYPES, default="int", update=_rebuild_prop,   # (Node has .type)
                       description="For a name the game's list doesn't have: what kind of value it holds")
    list: BoolProperty(name="List", default=False, update=_rebuild_prop,
                       description="For a name the game's list doesn't have: it holds several values (an Index picks one)")

    def make_sockets(self):
        _rebuild_prop(self)

    def draw_buttons(self, context, layout):
        from ..core import netprops
        row = layout.row(align=True)
        row.prop(self, "key", text="")
        op = row.operator("hammerless.logic_pick_property", text="", icon="VIEWZOOM")
        op.node_name = self.name
        layout.prop(self, "for_class")
        if self.key and netprops.info(self.key) is None:
            row = layout.row(align=True)
            row.prop(self, "prop_type", text="")
            row.prop(self, "list")

    def draw_buttons_ext(self, context, layout):
        from ..core import netprops
        self.draw_buttons(context, layout)
        p = netprops.info(self.key)
        if p:
            box = layout.box()
            for line in _wrap(p.get("desc") or "No description: see the classes that have it below.", 40):
                box.label(text=line)
            if not p.get("networked", True):
                box.label(text="Server only (players' screens don't get it)")
            for line in _wrap("Has it: " + ", ".join(p.get("classes", [])[:30]), 40):
                box.label(text=line)

    def settings(self):
        return {"key": self.key, "type": self.prop_type, "list": self.list}


class HL_NodeGetProperty(_PropNode, bpy.types.Node):
    """Reads one of an entity's properties: anything the game keeps on it (health, speed, glow, colour, model scale,
    ammo...). The game's whole list is searchable; nothing (0, empty) when the entity doesn't have it"""
    bl_idname, bl_label, bl_icon = "HL_NodeGetProperty", "Get Entity Property", "PROPERTIES"
    kind = "GET_PROP"


class HL_NodeSetProperty(_PropNode, bpy.types.Node):
    """Changes one of an entity's properties (health, speed, glow, colour, model scale, ammo...). Some are put back
    by the game every moment (it decides them itself): set those again on a timer, or use another way.
    Nothing happens when the entity doesn't have it"""
    bl_idname, bl_label, bl_icon = "HL_NodeSetProperty", "Set Entity Property", "PROPERTIES"
    kind = "SET_PROP"


class HL_OT_logic_pick_property(bpy.types.Operator):
    bl_idname = "hammerless.logic_pick_property"
    bl_label = "Pick Entity Property"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Search the game's entity properties (the node's For limits it to one class)"
    bl_property = "choice"
    node_name: StringProperty(options={"HIDDEN"})
    choice: EnumProperty(items=_prop_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        node = _node_in_editor(context, self.node_name)
        if node is not None and self.choice and self.choice != "_NONE_":
            node.key = self.choice
        return {"FINISHED"}


def _hud_slots(self, context):
    from ..core.director_options import HUD_SLOTS
    return HUD_SLOTS


class HL_NodeHudText(_Block, bpy.types.Node):
    """Shows text in one of the HUD's slots and keeps it up to date (a score, a timer, a count...).
    Needs scripted mode: Build & Play starts the map in Hammerless's own co-op mode for it"""
    bl_idname, bl_label, bl_icon = "HL_NodeHudText", "HUD Text", "FONT_DATA"
    kind = "HUD_TEXT"
    slot: EnumProperty(name="Where", items=_hud_slots)
    align: EnumProperty(name="Align", default="CENTER", items=[("LEFT", "Left", ""), ("CENTER", "Center", ""),
                                                               ("RIGHT", "Right", "")])
    team: EnumProperty(name="Seen By", default="ALL", items=[("ALL", "Everyone", ""), ("SURVIVORS", "Survivors", ""),
                                                             ("INFECTED", "Infected", "")])
    no_background: BoolProperty(name="No Background", default=False)
    blink: BoolProperty(name="Blink", default=False)
    place: BoolProperty(name="Own Position", default=False, description="Place it yourself (0 to 1 across / down the screen)")
    x: FloatProperty(name="X", default=0.25, min=0, max=1)
    y: FloatProperty(name="Y", default=0.1, min=0, max=1)
    w: FloatProperty(name="Width", default=0.5, min=0, max=1)
    h: FloatProperty(name="Height", default=0.08, min=0, max=1)

    def make_sockets(self):
        self.ev_in("run", "Show")
        self.data_in("text", "text", "Text")
        self.ev_out("then", "Then")

    def draw_buttons(self, context, layout):
        layout.prop(self, "slot", text="")
        row = layout.row(align=True)
        row.prop(self, "align", text="")
        row.prop(self, "team", text="")
        row = layout.row(align=True)
        row.prop(self, "no_background", toggle=True)
        row.prop(self, "blink", toggle=True)
        layout.prop(self, "place")
        if self.place:
            col = layout.column(align=True)
            for p in ("x", "y", "w", "h"):
                col.prop(self, p)

    def settings(self):
        return {"slot": self.slot, "align": self.align, "team": self.team, "no_background": self.no_background,
                "blink": self.blink, "place": self.place, "x": self.x, "y": self.y, "w": self.w, "h": self.h}


class HL_NodeHudHide(_Block, bpy.types.Node):
    """Clears a HUD slot"""
    bl_idname, bl_label, bl_icon = "HL_NodeHudHide", "HUD Hide", "HIDE_ON"
    kind = "HUD_HIDE"
    slot: EnumProperty(name="Where", items=_hud_slots)

    def make_sockets(self):
        self.ev_in("run", "Hide")
        self.ev_out("then", "Then")

    def draw_buttons(self, context, layout):
        layout.prop(self, "slot", text="")

    def settings(self):
        return {"slot": self.slot}


def _hook_items(self, context):
    from ..core.director_options import HOOKS
    return [(h[0], h[0], h[4]) for h in HOOKS]


@_keeps_links
def _rebuild_override(self, context=None):
    from ..core.director_options import HOOKS_BY_NAME, NO_BY_DEFAULT
    self.inputs.clear()
    self.outputs.clear()
    hook = HOOKS_BY_NAME.get(self.hook)
    if hook is None:
        return
    _n, _where, args, answer, _about = hook
    self.ev_out("asked", "Asked")
    for name, kind in args:
        _data_socket(self.outputs, kind, name, name.replace("_", " ").title())
    if answer:
        s = _data_socket(self.inputs, answer, "answer", "Allow" if answer == "bool" else "Answer")
        if answer == "bool":
            s.value = self.hook not in NO_BY_DEFAULT
    if self.hook == "AllowTakeDamage":
        _data_socket(self.inputs, "num", "damage", "New Damage")
    self.auto_title = self.hook


class HL_NodeOverride(_Block, bpy.types.Node):
    """The game asks the map before it does something (allow this damage? turn this weapon spawn into
    something else?): Asked runs your nodes, then the answer goes back. Type a fixed answer here, or work
    it out from what was asked with an Answer node at the end of Asked. Needs scripted mode: Build & Play
    starts the map in Hammerless's own co-op mode for it"""
    bl_idname, bl_label, bl_icon = "HL_NodeOverride", "Override", "QUESTION"
    kind = "OVERRIDE"
    hook: EnumProperty(name="Override", items=_hook_items, update=_rebuild_override)

    def make_sockets(self):
        _rebuild_override(self)

    def draw_buttons(self, context, layout):
        layout.prop(self, "hook", text="")

    def draw_buttons_ext(self, context, layout):
        from ..core.director_options import HOOKS_BY_NAME
        self.draw_buttons(context, layout)
        hook = HOOKS_BY_NAME.get(self.hook)
        if hook:
            box = layout.box()
            for line in _wrap(hook[4], 40):
                box.label(text=line)

    def settings(self):
        return {"hook": self.hook}


@_keeps_links
def _rebuild_answer(self, context=None):
    from ..core.director_options import HOOKS_BY_NAME, NO_BY_DEFAULT
    self.inputs.clear()
    self.outputs.clear()
    self.ev_in("run", "Run")
    hook = HOOKS_BY_NAME.get(self.hook)
    if hook is not None and hook[3]:
        s = _data_socket(self.inputs, hook[3], "answer", "Allow" if hook[3] == "bool" else "Answer")
        if hook[3] == "bool":
            s.value = self.hook not in NO_BY_DEFAULT
    if self.hook == "AllowTakeDamage":
        _data_socket(self.inputs, "num", "damage", "New Damage")
    self.ev_out("then", "Then")
    self.auto_title = self.hook


class HL_NodeOverrideAnswer(_Block, bpy.types.Node):
    """The answer to an Override's question, worked out from what the game asked: put it at the end of
    the Override's Asked wires (with If nodes, different answers in different cases)"""
    bl_idname, bl_label, bl_icon = "HL_NodeOverrideAnswer", "Answer", "CHECKMARK"
    kind = "OVERRIDE_ANSWER"
    hook: EnumProperty(name="Override", items=_hook_items, update=_rebuild_answer)

    def make_sockets(self):
        _rebuild_answer(self)

    def draw_buttons(self, context, layout):
        layout.prop(self, "hook", text="")

    def settings(self):
        return {"hook": self.hook}


class HL_NodeDirectorMood(_Block, bpy.types.Node):
    """How intense the AI Director thinks the game is right now (Anger, 0 calm to 1 furious, updated
    twice a second), and warnings before its next mob"""
    bl_idname, bl_label, bl_icon = "HL_NodeDirectorMood", "Director Mood", "FUND"
    kind = "DIRECTOR_MOOD"
    category = "Director"

    def init(self, context):
        self.use_custom_color = True
        self.color = CATEGORY_COLORS["Director"]
        self.make_sockets()
        _record_sockets(self)

    def make_sockets(self):
        self.data_out("num", "anger", "Anger")
        self.ev_out("mob60", "60 s Before a Mob")
        self.ev_out("mob20", "20 s Before a Mob")


DIRECTOR_CLASSES = (HL_NodeDirectorOption, HL_NodeDirectorMood, HL_NodeHudText, HL_NodeHudHide, HL_NodeOverride,
                    HL_NodeOverrideAnswer, HL_NodeGetProperty, HL_NodeSetProperty)


CATEGORIES = [
    ("Events", [HL_NodeMapStart, HL_NodeGameEvent, HL_NodeVolume, HL_NodeButton, HL_NodeTimer]),
    ("Values", [HL_NodeProgress, HL_NodeRandomValue, HL_NodeMath, HL_NodeCompare, HL_NodeBoolMath,
                HL_NodeInfectedCount, HL_NodeValue, HL_NodeExposedSetting]),
    ("Scene", [HL_NodeObjectInfo, HL_NodeSelf, HL_NodeObject, HL_NodeCollision]),
    ("Flow", [HL_NodeWhen, HL_NodeIf, HL_NodeSequence, HL_NodeDelay, HL_NodeOnce, HL_NodeGate, HL_NodeBranch,
              HL_NodeCounter, HL_NodeRandom]),
    ("Actions", [HL_NodeMove, HL_NodeShowHide, HL_NodeHorde, HL_NodeCrescendo, HL_NodeSpawn, HL_NodeSound,
                 HL_NodeTeleport, HL_NodeMessage, HL_NodeCamera]),
    ("Director", [HL_NodeDirector, HL_NodeDirectorSettings]),
    ("Objectives", [HL_NodeObjective]),
]
NODE_CLASSES = tuple(c for _t, cs in CATEGORIES for c in cs) + (HL_NodePathProgress,)   # (old graphs)


# The Add menu: one place per purpose. The game's full API sits one submenu deeper where it belongs
# (all its events under Events, all its functions under Actions); Blender's Add search finds them too.
SUB = "SUB"          # ("SUB", menu idname, label, icon) entries open a submenu
MENU_LAYOUT = [
    ("Events", [HL_NodeInteraction, HL_NodeMapStart, HL_NodeVolume, HL_NodeButton, HL_NodeTimer, HL_NodeGameEvent, None,
                (SUB, "HL_MT_logic_game_events", "All Game Events", "LIGHT")]),
    ("Flow", [HL_NodeWhen, HL_NodeIf, HL_NodeSequence, HL_NodeDelay, HL_NodeOnce, HL_NodeGate, HL_NodeBranch,
              HL_NodeCounter, HL_NodeRandom, None, HL_NodeForEach]),
    ("Values", [HL_NodeHumanIdentity, HL_NodeInventoryItem, HL_NodeHasInventory, HL_NodeResetKeyVariable, HL_NodeExposedSetting, HL_NodeValue, HL_NodeMath, HL_NodeCompare, HL_NodeBoolMath, HL_NodeRandomValue, HL_NodeProgress,
                HL_NodeInfectedCount, None, HL_NodeSetVariable, HL_NodeGetVariable, HL_NodeFormatText, HL_NodeTextCheck, HL_NodeSplitText,
                HL_NodeCheck, HL_NodePickValue, None, HL_NodeMakeVector, HL_NodeBreakVector, HL_NodeVectorMath, None,
                HL_NodeMakeTable, HL_NodeGetField]),
    ("Scene", [HL_NodeObjectInfo, HL_NodeSelf, HL_NodeObject, HL_NodeCollision, None, HL_NodeGetProperty]),
    ("Actions", [HL_NodeEmergencyKnife, HL_NodeGiveInventory, HL_NodeMove, HL_NodeShowHide, HL_NodeHorde, HL_NodeCrescendo, HL_NodeSpawn, HL_NodeSound,
                 HL_NodeTeleport, HL_NodeFireInput, HL_NodeSetProperty, None, (SUB, "HL_MT_logic_game_functions", "All Game Functions", "SCRIPT")]),
    ("Combat & Physics", [HL_NodeTrace, HL_NodeSeenBy, HL_NodeFindNearby, HL_NodePlayerView, None, HL_NodeDamage,
                          HL_NodePush, HL_NodeAimAt, None, HL_NodeExplosion, HL_NodeBeam, HL_NodeParticle]),
    ("Director", [HL_NodeDirector, HL_NodeDirectorSettings, HL_NodeDirectorOption, HL_NodeDirectorMood]),
    ("HUD & Messages", [HL_NodeMessage, HL_NodeObjective, None, HL_NodeHudText, HL_NodeHudHide]),
    ("Overrides", [HL_NodeOverride, HL_NodeOverrideAnswer]),
    ("Script", [HL_NodeScriptCode, HL_NodeScriptValue]),
]


def _layout_menu(index, title, entries):
    def draw(self, context):
        for e in entries:
            if e is None:
                self.layout.separator()
            elif isinstance(e, tuple):
                self.layout.menu(e[1], text=e[2], icon=e[3])
            else:
                op = self.layout.operator("node.add_node", text=e.bl_label, icon=e.bl_icon)
                op.type, op.use_transform = e.bl_idname, True
    idname = f"HL_MT_logic_menu_{index}"
    return type(idname, (bpy.types.Menu,), {"bl_idname": idname, "bl_label": title, "draw": draw})


CATEGORY_MENUS = [_layout_menu(i, t, e) for i, (t, e) in enumerate(MENU_LAYOUT)]


def _add_menu(self, context):
    if getattr(context.space_data, "tree_type", "") != TREE:
        return
    for m in CATEGORY_MENUS:
        self.layout.menu(m.bl_idname)
    self.layout.menu("HL_MT_logic_groups", icon="NODETREE")
    if "HL_MT_logic_examples" in dir(bpy.types):
        self.layout.separator()
        self.layout.menu("HL_MT_logic_examples", icon="HELP")
        self.layout.menu("HL_MT_logic_examples_2", icon="HELP")
        self.layout.menu("HL_MT_logic_examples_3", icon="HELP")


# ---------------------------------------------------------------- export

def logic_trees(scene=None):
    """The L4D2 Logic graphs (of one scene: its own, plus unassigned ones when the file has one scene). Node groups
    aren't graphs of their own: they're built where Group nodes use them."""
    trees = [t for t in bpy.data.node_groups if t.bl_idname == TREE and not getattr(t, "is_group", False)
             and not getattr(t, "is_behaviour", False)]
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
        # (Blender draws a wire that loops back to an earlier node red, "invalid": loops of event wires
        # are normal map logic, like a timer that stops itself, and work; _compiled_links drops value loops)
        if link.is_muted:
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
                numbers = {out.bl_idname, inp.bl_idname} <= {"HL_FloatSocket", "HL_IntSocket"}   # (a number into a
                if out.bl_idname != inp.bl_idname and not numbers and not (    # whole number: the game rounds it)
                        out.bl_idname in DATA_SOCKETS and inp.bl_idname in DATA_SOCKETS
                        and "HL_AnySocket" in (out.bl_idname, inp.bl_idname)):
                    report.warnings.append(f"{tree.name}: the wire from '{node_title(src)}' to "
                                           f"'{node_title(node)}' joins a {out.bl_label} with a "
                                           f"{inp.bl_label}, so it does nothing")
                    continue
                if out.bl_idname not in ("HL_EventSocket",) + DATA_SOCKETS:
                    continue
                links.append(LLink(src.name, out.identifier, node.name, inp.identifier,
                                   data=out.bl_idname in DATA_SOCKETS))
    return _without_value_loops(tree, links, report)


def _without_value_loops(tree, links, report):
    """A value worked out from itself (value wires in a circle) can't be: those wires are left out.
    Only nodes that are pure values (no event sockets) work their outputs out from their inputs: an
    Override's or an action's outputs are what the game asked or what it did, so wires back into them
    are no circle."""
    def pure(name):
        n = tree.nodes.get(name)
        return n is not None and not any(s.bl_idname == "HL_EventSocket" for s in (*n.inputs, *n.outputs))
    feeds: dict[str, set] = {}
    for l in links:
        if l.data and pure(l.from_node) and pure(l.to_node):
            feeds.setdefault(l.from_node, set()).add(l.to_node)

    def reaches(a, b):
        todo, seen = [a], set()
        while todo:
            n = todo.pop()
            if n == b:
                return True
            if n not in seen:
                seen.add(n)
                todo += feeds.get(n, ())
        return False
    keep = []
    for l in links:
        if l.data and l.from_node in feeds and reaches(l.to_node, l.from_node):
            a, b = tree.nodes.get(l.from_node), tree.nodes.get(l.to_node)
            report.warnings.append(f"{tree.name}: the value wire from '{node_title(a)}' to '{node_title(b)}' "
                                   f"is part of a circle of value wires (a value worked out from itself), so it "
                                   f"does nothing")
            continue
        keep.append(l)
    return keep


def _check_nodes(context, tree, report) -> None:
    """Problems Blender can't show: objects deleted from the scene that nodes still point at, entity
    nodes made for another class, and event nodes whose sockets need the game's definitions."""
    scene_objects = context.scene.objects
    for node in tree.nodes:
        if not isinstance(node, _Node) or node.mute:
            continue
        objs = [getattr(node, "target", None)] + [getattr(s, "value", None) for s in node.inputs
                                                    if s.bl_idname == "HL_ObjectSocket"]
        if node.bl_idname == 'HL_NodeExposedSetting' and node.value_type == 'OBJECT':
            objs.append(exposed.resolve(node, _EXPOSED_SLOT[0]))
        for obj in objs:
            if isinstance(obj, bpy.types.Object) and obj.name not in scene_objects:
                report.warnings.append(f"{tree.name}: node '{node_title(node)}' points at '{obj.name}', "
                                       f"which isn't in this scene (deleted?): pick another object")
        target = getattr(node, "target", None) if not getattr(node, "use_self", False) else None
        cls = getattr(node, "classname", "")
        if target is not None and cls and target.hammerless.classname and target.hammerless.classname != cls:
            report.warnings.append(f"{tree.name}: node '{node_title(node)}' shows the events of a {cls}, "
                                   f"but '{target.name}' is now a {target.hammerless.classname}: pick it again")
        if node.bl_idname in ("HL_NodeDirector", "HL_NodeObject") and not node.outputs and not any(s.bl_idname == 'HL_EventSocket' for s in node.inputs):
            refresh_entity_node(node)


def refresh_entity_node(node) -> None:
    """Fill an event node's sockets from the game's definitions (when they were missing before)."""
    if node.bl_idname == "HL_NodeDirector":
        _rebuild_entity_sockets(node, "info_director")
    elif getattr(node, "classname", ""):
        _rebuild_entity_sockets(node, node.classname)


# nodes that act on the whole map: in a Behaviour they'd run once per object using it (a warning says so)
MAP_WIDE_KINDS = ("DIRECTOR_OPTION", "DIRECTOR_SETTINGS", "HUD_TEXT", "HUD_HIDE", "CRESCENDO", "OBJECTIVE", "PERSONAL_POINTS")


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
    from .logicgroups import flatten
    from .extract import collection_role
    from .mapcollection import map_objects as _map_objects
    in_map = _map_objects(context.scene)
    jobs = []
    for tree in trees:
        b = tree.brain
        if b is None:
            if tree.get("hl_brain_migrated"):
                report.warnings.append(f"Logic graph '{tree.name}' has no brain (its empty was deleted), so it isn't "
                                       "built: give it one in Logic Graphs (or delete the graph)")
                continue
        elif b.name not in in_map or collection_role(b) == "IGNORE":
            report.info.append(f"Logic graph '{tree.name}': its brain '{b.name}' isn't in the map, so it's off")
            continue
        jobs.append((tree, b, None))
    # Behaviours: each object's graphs, built once for that object (Self = it), always under their own names
    from .extract import effective_role
    from .mapcollection import map_objects
    inside = map_objects(context.scene)
    for obj in sorted(context.scene.objects, key=lambda o: o.name):
        if obj.name not in inside or effective_role(obj) == "IGNORE":
            continue                                # (not part of the map: nor is its logic)
        for slot in obj.hammerless.behaviours:
            if slot.graph is not None:
                jobs.append((slot.graph, obj, slot))
    done_shared = set()
    for tree, owner, slot in jobs:
        behaviour = slot is not None
        if behaviour and tree.get("hl_light_preset"):
            from .lightpresets import shared_name
            if shared_name(owner):
                if tree.name in done_shared:     # (its lights share one name: one copy acts on them all)
                    continue
                done_shared.add(tree.name)
        _SELF_OWNER[0], _SELF_ISOLATE[0] = owner, behaviour
        _EXPOSED_SLOT[0] = slot
        try:   # (Group nodes inlined; a world graph keeps the names it always had)
            flat = flatten(context, tree, report, prefix=f"{owner.name}/" if behaviour else "")
        except ValueError as error:
            report.errors.append(f"Behaviour settings in '{tree.name}': {error}")
            continue
        finally:
            _EXPOSED_SLOT[0] = None
            _SELF_OWNER[0], _SELF_ISOLATE[0] = None, False
        nodes = flat.nodes
        if behaviour:
            wide = sorted({n.id.split("/")[-1] for n in nodes if n.kind in MAP_WIDE_KINDS})
            if wide:
                report.warnings.append(f"Behaviour '{tree.name}' on '{owner.name}': {', '.join(wide)} act on the whole "
                                       "map, and run once for every object using this behaviour")
        volumes = {n.obj for n in nodes if n.kind == "VOLUME" and n.obj}
        if volumes:     # a volume's mesh becomes the trigger, not a solid wall
            ir.brushes = [b for b in ir.brushes if b.source.split(" (part ")[0] not in volumes]
            for e in ir.entities:
                # one the user made a brush entity of their own class (keyvalues, outputs) stays: say so
                if e.brushes and e.classname != "func_detail" and e.source.split(" (part ")[0] in volumes:
                    report.warnings.append(f"{tree.name}: '{e.source}' is a {e.classname} and a Volume node makes "
                                           "a trigger of it too, so the map gets both: clear its class (Role: Brush) "
                                           "or use another object for the volume")
            ir.entities = [e for e in ir.entities if not (e.source.split(" (part ")[0] in volumes
                                                          and e.classname == "func_detail")]
        links = flat.links
        name = f"{owner.name} {tree.name}" if behaviour else (tree.name if len(trees) > 1 else "")
        for p in compile_graph(nodes, links, ir, name, log=context.scene.hammerless.debug_log):
            report.warnings.append(f"{tree.name} on '{owner.name}': {p}" if behaviour else f"{tree.name}: {p}")


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


def node_title(node) -> str:
    """A node as its header shows it."""
    if node.label:
        return node.label
    try:
        return node.draw_label()
    except Exception:
        return node.name


def migrate_labels(tree) -> None:
    """Logic made before headers showed node types kept its title in Blender's Label (which hides the type): moved
    after the type (a title the node worked out itself becomes that again, an example's or the user's a note)."""
    for n in tree.nodes:
        if not hasattr(n, "note") or not n.label:
            continue
        old = n.label
        n.label = ""
        for prefix in ("On ", "Override: ", "Answer: "):
            if old.startswith(prefix) and n.bl_idname in ("HL_NodeScriptEvent", "HL_NodeOverride", "HL_NodeOverrideAnswer"):
                old = old[len(prefix):]
        if not n.auto_title and n.bl_idname in AUTO_TITLED:
            n.auto_title = old
        elif old != n.auto_title:
            n.note = old


AUTO_TITLED = {"HL_NodeObject", "HL_NodeObjectInfo", "HL_NodeGameEvent", "HL_NodeScriptCall", "HL_NodeScriptEvent",
               "HL_NodeDirectorOption", "HL_NodeOverride", "HL_NodeOverrideAnswer", "HL_NodeGetProperty",
               "HL_NodeSetProperty"}


@bpy.app.handlers.persistent
def _migrate_on_load(*_args):
    for tree in bpy.data.node_groups:
        if tree.bl_idname == TREE:
            try:
                migrate_labels(tree)
            except Exception:
                pass


def new_tree(context, name: str = "Map Logic"):
    """A new world graph, with a brain at the 3D cursor."""
    tree = bpy.data.node_groups.new(name, TREE)
    tree.use_fake_user = True          # kept when saved, even if no editor shows it
    tree.scene_name = context.scene.name
    make_brain(context.scene, tree, context.scene.cursor.location)
    return tree


def make_brain(scene, tree, location=(0.0, 0.0, 0.0)):
    """An empty in the scene's map that the graph lives on (its brain)."""
    from .mapcollection import map_collection
    obj = bpy.data.objects.new(tree.name, None)
    obj.empty_display_type, obj.empty_display_size = "SPHERE", 0.4
    obj.location = location
    obj["hl_brain"] = True
    (map_collection(scene) or scene.collection).objects.link(obj)
    tree.brain = obj
    tree["hl_brain_migrated"] = True
    return obj


def migrate_brains() -> int:
    """World graphs from before brains get one: an empty named after the graph at the map's origin (nothing else
    changes: they build as they did). Returns how many."""
    n = 0
    for t in logic_trees():
        if t.brain is not None or t.get("hl_brain_migrated") or t.library is not None:
            continue
        scene = bpy.data.scenes.get(t.scene_name) or (bpy.data.scenes[0] if bpy.data.scenes else None)
        if scene is None:
            continue
        make_brain(scene, t)
        n += 1
    return n


def brain_trees(obj) -> list:
    """The world graphs living on this object."""
    return [t for t in logic_trees() if t.brain == obj]


class HL_OT_logic_from_outputs(bpy.types.Operator):
    bl_idname = "hammerless.logic_from_outputs"
    bl_label = "Behaviours from Outputs"
    bl_description = ("Move entity outputs into separate Behaviour graphs on their source objects; "
                      "unresolved connections stay in Outputs")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .behaviours import new_behaviour_tree, show_tree
        from .mapcollection import map_objects
        inside = map_objects(context.scene)
        objects = [o for o in context.scene.objects if o.name in inside]
        by_name = {}
        for obj in objects:
            for kv in obj.hammerless.keyvalues:
                if kv.key == "targetname" and kv.value:
                    by_name[kv.value] = obj
        moved = kept = graphs = 0
        first = preferred = None
        for obj in objects:
            if not obj.hammerless.outputs:
                continue
            tree = src = None
            remove = []
            for index, out in enumerate(obj.hammerless.outputs):
                target = by_name.get(out.target)
                ins, _ = _entity_sockets(_object_class(target), True) if target else ([], [])
                _, outs = _entity_sockets(_object_class(obj), True)
                if (target is None or not any(s.name == out.output for s in outs)
                        or not any(s.name == out.input for s in ins)):
                    kept += 1
                    continue
                if tree is None:
                    tree = new_behaviour_tree(f"{obj.name} - Outputs")
                    tree.nodes.clear()
                    obj.hammerless.behaviours.add().graph = tree
                    obj.hammerless.behaviours_index = len(obj.hammerless.behaviours) - 1
                    src = tree.nodes.new("HL_NodeObject")
                    src.target = obj
                    src.show_all = True
                    src.use_self = True
                    src.location = (0, 0)
                    first = first or tree
                    if obj == context.object:
                        preferred = tree
                    graphs += 1
                row = len(remove)
                # One destination per output preserves different parameters on the same input.
                dst = tree.nodes.new("HL_NodeObject")
                dst.target = target
                dst.show_all = True
                dst.location = (650, -row * 230)
                dst.label = f"{out.target}: {out.input}"
                next(s for s in dst.inputs if s.identifier == out.input).value = out.parameter
                chain = next(s for s in src.outputs if s.identifier == out.output)
                if out.only_once:
                    once = tree.nodes.new("HL_NodeOnce")
                    once.location = (230, -row * 230)
                    tree.links.new(chain, once.inputs["In"])
                    chain = once.outputs["Out"]
                if out.delay > 0:
                    delay = tree.nodes.new("HL_NodeDelay")
                    delay.seconds = out.delay
                    delay.location = (430, -row * 230)
                    tree.links.new(chain, delay.inputs["In"])
                    chain = delay.outputs["Out"]
                tree.links.new(chain, next(s for s in dst.inputs if s.identifier == out.input))
                remove.append(index)
                moved += 1
            if tree is not None:
                for node in tree.nodes:
                    for socket in (*node.inputs, *node.outputs):
                        socket.hide = not socket.is_linked
            for index in reversed(remove):
                obj.hammerless.outputs.remove(index)
        if preferred or first:
            show_tree(context, preferred or first)
        self.report({"INFO"}, f"Moved {moved} outputs into {graphs} object Behaviours; "
                    f"{kept} unresolved outputs stayed on their objects")
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


CLASSES = (HL_LogicTree, HL_EventSocket, HL_ObjectSocket, HL_FloatSocket, HL_IntSocket, HL_BoolSocket, HL_TextSocket, HL_VectorSocket,
           HL_ThingSocket, HL_AnySocket) + NODE_CLASSES + tuple(CATEGORY_MENUS) + tuple(SCRIPT_MENUS) + (
    HL_NodeScriptCall, HL_NodeScriptEvent, HL_OT_logic_pick_function, HL_OT_logic_pick_event) + BLOCK_CLASSES + (
    ) + DIRECTOR_CLASSES + (HL_OT_logic_pick_option, HL_OT_logic_pick_property,
    HL_OT_logic_new, HL_OT_logic_from_outputs, HL_OT_logic_refresh_node)


@bpy.app.handlers.persistent
def _on_load(*_args):
    _event_inputs_take_many()


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    from . import logicgroups
    logicgroups.register()
    bpy.types.NODE_MT_add.append(_add_menu)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.handlers.load_post.append(_migrate_on_load)
    from . import economy_migration
    bpy.app.handlers.load_post.append(economy_migration.on_load)


def unregister():
    from . import economy_migration
    if economy_migration.on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(economy_migration.on_load)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if _migrate_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_migrate_on_load)
    bpy.types.NODE_MT_add.remove(_add_menu)
    from . import logicgroups
    logicgroups.unregister()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
