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


class _ListSetQuery(_ListQuery):
    def make_sockets(self):
        self.ev_in("run", "Run"); self.ev_out("next", "Next")
        self.data_in(self.list_type, "items", "Items" if self.kind == "LIST_UNIQUE" else "List A")
        if self.kind != "LIST_UNIQUE":
            self.data_in(self.list_type, "other", "List B")
        self.data_in("num", "limit", "Max Results").value = 128
        self.data_out(self.list_type, "items", "Items")
        self.data_out("num", "count", "Count")
        self.data_out("bool", "found", "Found")
        self.data_out("bool", "truncated", "Truncated")

    def draw_buttons(self, context, layout):
        layout.prop(self, "list_type", text="")
        layout.label(text="First 256 per list; up to 128 results")


class HL_ListUnion(_ListSetQuery, bpy.types.Node):
    """Combine valid items from A then B, keeping each handle once in first-seen order. Examines at most 256 entries per list and returns at most 128. Run captures a snapshot; Truncated means input or matching results were omitted."""
    bl_idname, bl_label, kind = "HL_ListUnion", "Combine Lists", "LIST_UNION"


class HL_ListIntersection(_ListSetQuery, bpy.types.Node):
    """Keep valid items present in both lists, once each in A's order. Only the first 256 entries of each list are examined; matches later in B are unknown when Truncated is true. At most 128 results."""
    bl_idname, bl_label, kind = "HL_ListIntersection", "List Intersection", "LIST_INTERSECTION"


class HL_ListDifference(_ListSetQuery, bpy.types.Node):
    """Keep valid items in A that are absent from the examined part of B, once each in A's order. Truncated means the answer may be incomplete: entries beyond B's first 256 are not checked. At most 128 results."""
    bl_idname, bl_label, kind = "HL_ListDifference", "Subtract List", "LIST_DIFFERENCE"


class HL_ListUnique(_ListSetQuery, bpy.types.Node):
    """Remove duplicate, dead or invalid handles, preserving first-seen order. Examines the first 256 entries and returns up to Max Results (capped at 128). Run refreshes the snapshot."""
    bl_idname, bl_label, kind = "HL_ListUnique", "Unique Valid Items", "LIST_UNIQUE"


NODES = (HL_ListCount, HL_ListItem, HL_ListClosest, HL_ListRandom, HL_ListFilter,
         HL_ListUnion, HL_ListIntersection, HL_ListDifference, HL_ListUnique)


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
