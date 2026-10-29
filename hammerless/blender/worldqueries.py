"""World information as explicit Run/Next snapshots, reusable in normal and bot graphs."""
import bpy
from bpy.props import BoolProperty, EnumProperty
from .logic import _Block


class _NavSocket:
    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return (0.30, 0.70, 0.42, 1.0)

    @classmethod
    def draw_color_simple(cls):
        return (0.30, 0.70, 0.42, 1.0)


class HL_NavAreaSocket(_NavSocket, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_NavAreaSocket", "Nav Area"


class HL_NavAreasSocket(_NavSocket, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_NavAreasSocket", "Nav Area List"


class _Query(_Block):
    def begin(self):
        self.ev_in("run", "Run")
        self.ev_out("next", "Next")
        self.data_out("bool", "available", "Available")

    def found(self):
        self.data_out("bool", "found", "Found")

    def area_out(self):
        self.data_out("navarea", "area", "Area")
        self.data_out("vec", "position", "Center")


TEAM = [("2", "Survivors", "Survivor nav blockers"), ("3", "Infected", "Infected nav blockers")]


class HL_WQNearest(_Query, bpy.types.Node):
    """Find the nearest nav area at or below Position. Center is its center, not a precise landing point.
    Available means a navmesh is loaded; Found means an area was located. Run before reading outputs."""
    bl_idname, bl_label, kind = "HL_WQNearest", "Nearest Nav Area", "WQ_NEAREST"
    check_los: BoolProperty(name="Require Line of Sight", default=True)
    check_ground: BoolProperty(name="Require Ground", default=True)

    def make_sockets(self):
        self.begin(); self.found()
        self.data_in("vec", "position", "Position")
        self.data_in("num", "radius", "Search Distance").value = 256
        self.area_out()

    def draw_buttons(self, context, layout):
        layout.prop(self, "check_los"); layout.prop(self, "check_ground")
        layout.label(text="Search capped at 4096 units")

    def settings(self):
        return {"check_los": self.check_los, "check_ground": self.check_ground}


class HL_WQNearby(_Query, bpy.types.Node):
    """Nearby areas sorted by distance, then ID. May include other floors and unreachable areas.
    Test Nav Path Exists separately. Radius capped at 4096 and results at 128."""
    bl_idname, bl_label, kind = "HL_WQNearby", "Nearby Nav Areas", "WQ_NEARBY"

    def make_sockets(self):
        self.begin(); self.found()
        self.data_in("vec", "position", "Position")
        self.data_in("num", "radius", "Radius").value = 512
        self.data_in("num", "limit", "Max Results").value = 16
        self.data_out("navareas", "areas", "Areas")
        self.data_out("num", "count", "Count")
        self.area_out()


class HL_WQInfo(_Query, bpy.types.Node):
    """Read one nav area's center, ID and state. Found is false for an absent or stale area."""
    bl_idname, bl_label, kind = "HL_WQInfo", "Nav Area Info", "WQ_INFO"
    team: EnumProperty(name="Team", items=TEAM, default="3")

    def make_sockets(self):
        self.begin(); self.found()
        self.data_in("navarea", "area", "Area")
        self.data_out("vec", "position", "Center")
        self.data_out("num", "id", "ID")
        self.data_out("bool", "blocked", "Blocked")
        self.data_out("bool", "underwater", "Underwater")

    def draw_buttons(self, context, layout):
        layout.prop(self, "team")

    def settings(self):
        return {"team": self.team}


class HL_WQItem(_Query, bpy.types.Node):
    """Get one area from Nearby Nav Areas. Index starts at zero; out-of-range returns Found=false."""
    bl_idname, bl_label, kind = "HL_WQItem", "Nav Area at Index", "WQ_ITEM"

    def make_sockets(self):
        self.begin(); self.found()
        self.data_in("navareas", "areas", "Areas")
        self.data_in("num", "index", "Index")
        self.area_out()


class HL_WQPath(_Query, bpy.types.Node):
    """Check nav connectivity for a team, not a guarantee an actor can follow the route.
    Found means both endpoints are valid. Exists means a path fits Max Distance (capped 32768).
    Run before reading outputs; respects current nav blockers by default."""
    bl_idname, bl_label, kind = "HL_WQPath", "Nav Path Exists", "WQ_PATH"
    team: EnumProperty(name="Team", items=TEAM, default="3")
    ignore_blockers: BoolProperty(name="Ignore Nav Blockers", default=False)

    def make_sockets(self):
        self.begin(); self.found()
        self.data_in("navarea", "start", "Start Area")
        self.data_in("navarea", "goal", "Goal Area")
        self.data_in("num", "length", "Max Distance").value = 8192
        self.data_out("bool", "exists", "Exists")

    def draw_buttons(self, context, layout):
        layout.prop(self, "team"); layout.prop(self, "ignore_blockers")

    def settings(self):
        return {"team": self.team, "ignore_blockers": self.ignore_blockers}


class HL_WQAmbient(_Query, bpy.types.Node):
    """Estimate the baked ambient field at Position, not a surface luxel. Excludes dynamic lights and
    runtime exposure. Available=false is missing data, not darkness. Run before reading outputs."""
    bl_idname, bl_label, kind = "HL_WQAmbient", "Baked Ambient Light", "WQ_AMBIENT"

    def make_sockets(self):
        self.begin()
        self.data_in("vec", "position", "Position")
        self.data_out("num", "brightness", "Brightness")
        self.data_out("vec", "color", "Color")
        self.data_out("bool", "fallback", "Neighbor Sample")


NODES = (HL_WQNearest, HL_WQNearby, HL_WQInfo, HL_WQItem, HL_WQPath, HL_WQAmbient)


class HL_MT_world_queries(bpy.types.Menu):
    bl_idname, bl_label = "HL_MT_world_queries", "World Queries"

    def draw(self, context):
        for cls in NODES:
            op = self.layout.operator("node.add_node", text=cls.bl_label)
            op.type, op.use_transform = cls.bl_idname, True


CLASSES = (HL_NavAreaSocket, HL_NavAreasSocket) + NODES + (HL_MT_world_queries,)


def register():
    from . import logic
    logic.SOCKET_OF_KIND.update(navarea="HL_NavAreaSocket", navareas="HL_NavAreasSocket")
    logic.DATA_SOCKETS += ("HL_NavAreaSocket", "HL_NavAreasSocket")
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    from . import logic
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    for key in ("navarea", "navareas"):
        logic.SOCKET_OF_KIND.pop(key, None)
    logic.DATA_SOCKETS = tuple(s for s in logic.DATA_SOCKETS if s not in {"HL_NavAreaSocket", "HL_NavAreasSocket"})
