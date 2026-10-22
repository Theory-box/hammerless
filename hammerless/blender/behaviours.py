"""Behaviours: logic graphs objects carry (Selected Object > Behaviours). Each runs once for every object using it,
its Self node being that object (blender/logic.py compile_logic). Several objects can share one graph, as materials
are shared: duplicating an object keeps its behaviours, Make Unique gives one object a copy of its own."""
from __future__ import annotations

import bpy
from bpy.props import IntProperty

from .logic import TREE
from .entitycontext import entity_object, selected_entities


def new_behaviour_tree(name: str = "Behaviour"):
    """A new Behaviour graph with a Self node in it, ready to wire."""
    tree = bpy.data.node_groups.new(name, TREE)
    tree.is_behaviour = True
    node = tree.nodes.new("HL_NodeSelf")
    node.location = (-300.0, 0.0)
    return tree


def show_tree(context, tree) -> bool:
    """The graph in a Node Editor: one already open, else a new window with one."""
    from .logic import _show_tree
    if context.screen and any(a.type == "NODE_EDITOR" for a in context.screen.areas):
        _show_tree(context, tree)
        return True
    try:
        bpy.ops.wm.window_new()
    except RuntimeError:
        return False
    win = context.window_manager.windows[-1]
    area = max(win.screen.areas, key=lambda a: a.width * a.height)
    area.ui_type = TREE
    area.spaces.active.node_tree = tree
    return True


def _slot(context, index=None):
    obj = entity_object(context)
    if obj is None:
        return None, None
    hs = obj.hammerless
    i = hs.behaviours_index if index is None or index < 0 else index
    return obj, (hs.behaviours[i] if 0 <= i < len(hs.behaviours) else None)


class HL_OT_behaviour_new(bpy.types.Operator):
    bl_idname = "hammerless.behaviour_new"
    bl_label = "New Behaviour"
    bl_description = ("A new Behaviour graph for the selected objects (they all share it), opened in the node "
                      "editor: its Self node is whichever object runs it")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objs = selected_entities(context) or ([entity_object(context)] if context.object else [])
        if not objs:
            return {"CANCELLED"}
        tree = new_behaviour_tree(f"{(entity_object(context) or objs[0]).name} Behaviour")
        for o in objs:
            o.hammerless.behaviours.add().graph = tree
            o.hammerless.behaviours_index = len(o.hammerless.behaviours) - 1
        show_tree(context, tree)
        return {"FINISHED"}


class HL_OT_behaviour_add(bpy.types.Operator):
    bl_idname = "hammerless.behaviour_add"
    bl_label = "Add Behaviour Slot"
    bl_description = "An empty Behaviour slot on this object: pick an existing Behaviour in it"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = entity_object(context)
        if obj is None:
            return {"CANCELLED"}
        obj.hammerless.behaviours.add()
        obj.hammerless.behaviours_index = len(obj.hammerless.behaviours) - 1
        return {"FINISHED"}


class HL_OT_behaviour_copy(bpy.types.Operator):
    bl_idname = "hammerless.behaviour_copy"
    bl_label = "Copy to Selected"
    bl_description = ("Give the other selected objects this Behaviour too (they share it; ones that already carry it "
                      "are left as they are)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj, slot = _slot(context)
        if slot is None or slot.graph is None:
            return {"CANCELLED"}
        n = 0
        for o in selected_entities(context):
            if o is obj or o.hammerless.preset or any(s.graph == slot.graph for s in o.hammerless.behaviours):
                continue
            o.hammerless.behaviours.add().graph = slot.graph
            n += 1
        self.report({"INFO"}, f"'{slot.graph.name}' on {n} more object{'s' if n != 1 else ''}")
        return {"FINISHED"}


class HL_OT_behaviour_remove(bpy.types.Operator):
    bl_idname = "hammerless.behaviour_remove"
    bl_label = "Remove Behaviour"
    bl_description = ("Take this Behaviour off the object (the graph stays while other objects use it; with none, "
                      "it goes when the file is saved)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj, slot = _slot(context)
        if slot is None:
            return {"CANCELLED"}
        hs = obj.hammerless
        hs.behaviours.remove(hs.behaviours_index)
        hs.behaviours_index = max(0, min(hs.behaviours_index, len(hs.behaviours) - 1))
        return {"FINISHED"}


class HL_OT_behaviour_move(bpy.types.Operator):
    bl_idname = "hammerless.behaviour_move"
    bl_label = "Move Behaviour"
    bl_description = "Move this Behaviour up or down the list"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    step: IntProperty(default=-1)

    def execute(self, context):
        obj, slot = _slot(context)
        if slot is None:
            return {"CANCELLED"}
        hs = obj.hammerless
        to = hs.behaviours_index + self.step
        if not 0 <= to < len(hs.behaviours):
            return {"CANCELLED"}
        hs.behaviours.move(hs.behaviours_index, to)
        hs.behaviours_index = to
        return {"FINISHED"}


class HL_OT_behaviour_open(bpy.types.Operator):
    bl_idname = "hammerless.behaviour_open"
    bl_label = "Open Behaviour"
    bl_description = "Show this Behaviour's graph in the node editor"
    bl_options = {"INTERNAL"}
    index: IntProperty(default=-1)

    def execute(self, context):
        _obj, slot = _slot(context, self.index)
        if slot is None or slot.graph is None:
            return {"CANCELLED"}
        if not show_tree(context, slot.graph):
            self.report({"WARNING"}, "Open a Node Editor (L4D2 Logic) to see it")
            return {"CANCELLED"}
        return {"FINISHED"}


class HL_OT_behaviour_unique(bpy.types.Operator):
    bl_idname = "hammerless.behaviour_unique"
    bl_label = "Make Unique"
    bl_description = "Give this object its own copy of the Behaviour, to change it without changing the others"
    bl_options = {"REGISTER", "UNDO"}
    index: IntProperty(default=-1)

    def execute(self, context):
        obj, slot = _slot(context, self.index)
        if slot is None or slot.graph is None:
            return {"CANCELLED"}
        copy = slot.graph.copy()
        copy.name = f"{obj.name} {slot.graph.name}"
        copy["hl_unique"] = True                  # (a preset's copy is this object's own: never handed out again)
        slot.graph = copy
        return {"FINISHED"}


class HL_OT_logic_open_graph(bpy.types.Operator):
    bl_idname = "hammerless.logic_open_graph"
    bl_label = "Open Logic Graph"
    bl_description = "Show this logic graph in the node editor"
    bl_options = {"INTERNAL"}
    graph: bpy.props.StringProperty()

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.graph)
        if tree is None or not show_tree(context, tree):
            return {"CANCELLED"}
        return {"FINISHED"}


class HL_OT_logic_give_brain(bpy.types.Operator):
    bl_idname = "hammerless.logic_give_brain"
    bl_label = "Give It a Brain"
    bl_description = "A brain (an empty at the 3D cursor) for this logic graph: it's built again"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    graph: bpy.props.StringProperty()

    def execute(self, context):
        from .logic import make_brain
        tree = bpy.data.node_groups.get(self.graph)
        if tree is None:
            return {"CANCELLED"}
        make_brain(context.scene, tree, context.scene.cursor.location)
        return {"FINISHED"}


class HL_UL_behaviours(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row(align=True)
        row.prop(item, "graph", text="", icon="NODETREE")
        if item.graph is not None:
            users = sum(1 for o in bpy.data.objects for s in o.hammerless.behaviours if s.graph == item.graph)
            if users > 1:
                op = row.operator("hammerless.behaviour_unique", text=str(users))
                op.index = index
            op = row.operator("hammerless.behaviour_open", text="", icon="WINDOW")
            op.index = index


class HL_PT_obj_behaviours(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Behaviours"
    bl_parent_id = "HL_PT_object"

    @classmethod
    def poll(cls, context):
        obj = entity_object(context)
        return obj is not None and not obj.hammerless.preset

    def draw_header_preset(self, context):
        n = len(entity_object(context).hammerless.behaviours)
        if n:
            self.layout.label(text=str(n))

    def draw(self, context):
        layout = self.layout
        from .logic import brain_trees
        for t in brain_trees(entity_object(context)):       # (a brain: the world graphs living on it)
            row = layout.row(align=True)
            row.label(text=f"Brain of '{t.name}'", icon="NODETREE")
            row.operator("hammerless.logic_open_graph", text="", icon="WINDOW").graph = t.name
        hs = entity_object(context).hammerless
        row = layout.row()
        row.template_list("HL_UL_behaviours", "", hs, "behaviours", hs, "behaviours_index", rows=2)
        side = row.column(align=True)
        side.operator("hammerless.behaviour_new", text="", icon="ADD")
        side.operator("hammerless.behaviour_add", text="", icon="LINKED")
        side.operator("hammerless.behaviour_remove", text="", icon="REMOVE")
        side.operator("hammerless.behaviour_copy", text="", icon="COPYDOWN")
        side.separator()
        side.operator("hammerless.behaviour_move", text="", icon="TRIA_UP").step = -1
        side.operator("hammerless.behaviour_move", text="", icon="TRIA_DOWN").step = 1
        if not hs.behaviours:
            col = layout.column(align=True)
            col.scale_y = 0.8
            col.label(text="Logic this object carries: + makes one,", icon="INFO")
            col.label(text="its Self node is this object", icon="BLANK1")


CLASSES = (HL_OT_behaviour_new, HL_OT_behaviour_add, HL_OT_behaviour_copy, HL_OT_behaviour_remove, HL_OT_behaviour_move,
           HL_OT_behaviour_open, HL_OT_behaviour_unique, HL_OT_logic_open_graph, HL_OT_logic_give_brain,
           HL_UL_behaviours, HL_PT_obj_behaviours)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
