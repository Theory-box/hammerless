"""Typed list snapshots for ordinary logic and bot action callbacks."""
import bpy
from bpy.props import EnumProperty
from .logic import _Block, _rebuild


def changed(self, context):
    _rebuild(self)


class _ListQuery(_Block):
    list_type: EnumProperty(name="List Type", items=[("bots", "Bots", "Living common infected, witches and bot players"),
                            ("navareas", "Nav Areas", "Valid navigation areas")], default="bots", update=changed)

    def make_sockets(self):
        self.ev_in("run", "Run"); self.ev_out("next", "Next")
        self.data_in(self.list_type, "items", "Items")
        self.data_out("bool", "found", "Found")
        self.data_out("bool", "truncated", "Truncated")
        if self.kind in ("LIST_COUNT", "LIST_FILTER"):
            self.data_out("num", "count", "Count")
        if self.kind in ("LIST_CLOSEST", "LIST_FILTER"):
            self.data_in("vec", "origin", "Around Position")
        if self.kind == "LIST_ITEM":
            self.data_in("num", "index", "Index (starts at 0)")
        if self.kind == "LIST_FILTER":
            self.data_in("num", "radius", "Radius (0 = All)").value = 512
            self.data_in("num", "limit", "Max Results").value = 32
            self.data_out(self.list_type, "items", "Items")
        elif self.kind != "LIST_COUNT":
            self.data_out("bot" if self.list_type == "bots" else "navarea", "item", "Item")
            self.data_out("vec", "position", "Position")

    def settings(self):
        return {"list_type": self.list_type}

    def draw_buttons(self, context, layout):
        layout.prop(self, "list_type", text="")
        layout.label(text="Snapshot; examines first 256 items")


class HL_ListCount(_ListQuery, bpy.types.Node):
    """Count living bots or valid nav areas among the first 256 entries. Truncated means later entries were not examined. Run refreshes the snapshot; outputs do not update by themselves."""
    bl_idname, bl_label, kind = "HL_ListCount", "Count Valid Items", "LIST_COUNT"


class HL_ListItem(_ListQuery, bpy.types.Node):
    """Read an item at its original zero-based index. Missing, stale, dead, fractional or out-of-range entries return Found=false; invalid earlier entries do not shift the index. Only first 256 entries are accessible."""
    bl_idname, bl_label, kind = "HL_ListItem", "Item at Index", "LIST_ITEM"


class HL_ListClosest(_ListQuery, bpy.types.Node):
    """Choose the nearest valid item by straight-line distance among the first 256 entries. Ties keep the first item. Does not check sight or reachability; use a separate query for that."""
    bl_idname, bl_label, kind = "HL_ListClosest", "Closest Item", "LIST_CLOSEST"


class HL_ListRandom(_ListQuery, bpy.types.Node):
    """Choose one random valid item among the first 256 entries each time Run fires. The choice stays unchanged until the next Run. Duplicate entries count as separate choices."""
    bl_idname, bl_label, kind = "HL_ListRandom", "Random Item", "LIST_RANDOM"


class HL_ListFilter(_ListQuery, bpy.types.Node):
    """Keep living bots or valid nav areas within Radius, preserving input order. Zero radius means no distance filter. Radius caps at 32768 units; examines first 256 entries and returns at most 128. Truncated reports omitted input or matching results. Does not check reachability."""
    bl_idname, bl_label, kind = "HL_ListFilter", "Filter Valid Items in Radius", "LIST_FILTER"


NODES = (HL_ListCount, HL_ListItem, HL_ListClosest, HL_ListRandom, HL_ListFilter)


class HL_MT_list_tools(bpy.types.Menu):
    bl_idname, bl_label = "HL_MT_list_tools", "List Tools"

    def draw(self, context):
        for cls in NODES:
            op = self.layout.operator("node.add_node", text=cls.bl_label)
            op.type, op.use_transform = cls.bl_idname, True


CLASSES = NODES + (HL_MT_list_tools,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
