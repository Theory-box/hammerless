"""Node groups for L4D2 Logic graphs, like Blender's shader / Geometry Nodes groups: a group is a logic graph of
plain nodes with a Group Input and a Group Output; a Group node uses it like one node. Ctrl+G makes one from the
selected nodes, Tab goes in and out.

A group graph has its own list of sockets (its items: name, kind, input or output) rather than Blender's
tree.interface, which doesn't take our custom socket types well. Sockets are only made again from operators (adding,
removing, renaming an item; picking a group), never while undoing or loading.

At build every Group node is flattened: the nodes inside come in (named '<group node>/<node>', so each copy is its
own), wires into a group input go straight to what the Group Input feeds inside, wires out of a group output come
straight from what feeds the Group Output. A number typed on the Group node's input is given to the nodes inside.
Set / Get Variable 'This Group Copy' names are made per copy.
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty

from ..core.logic import LLink


def node_title(node) -> str:
    from .logic import node_title as title
    return title(node)

ITEM_KINDS = [("event", "Event", "An event wire (orange)"), ("num", "Number", ""), ("bool", "True/False", ""),
              ("text", "Text", ""), ("vec", "Vector", ""), ("thing", "Entity / Player", ""),
              ("any", "Anything", "Any value")]
KIND_OF_SOCKET = {"HL_EventSocket": "event", "HL_FloatSocket": "num", "HL_BoolSocket": "bool",
                  "HL_TextSocket": "text", "HL_VectorSocket": "vec", "HL_ThingSocket": "thing", "HL_AnySocket": "any"}
GROUP_COLOR = (0.20, 0.30, 0.22)


def _rename_sockets(self, context):
    """An item renamed: the sockets made for it show the new name (only names change: safe in an update)."""
    tree = self.id_data
    for node in _users_and_io(tree):
        for sock in (*node.inputs, *node.outputs):
            if sock.identifier == self.uid:
                sock.name = self.name


class HL_GroupItem(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", default="Value", update=_rename_sockets)
    kind: EnumProperty(name="Kind", items=ITEM_KINDS, default="event")
    is_output: BoolProperty(name="Output", default=False)
    uid: StringProperty()           # the sockets' identifier: never reused, so old wires can't land on a new item


def is_group(tree) -> bool:
    return tree is not None and tree.bl_idname == "HL_LogicTree" and getattr(tree, "is_group", False)


def new_item(tree, name: str, kind: str, is_output: bool, uid: str = "") -> HL_GroupItem:
    if not uid:
        tree.hl_next_uid += 1
        uid = f"g{tree.hl_next_uid}"
    it = tree.group_items.add()
    it.name, it.kind, it.is_output, it.uid = name, kind, is_output, uid
    return it


def items(tree, outputs: bool):
    return [it for it in tree.group_items if it.is_output == outputs] if tree is not None else []


def _users_and_io(tree):
    """Nodes whose sockets come from this group's items: its Group Input / Output, and every Group node using it."""
    out = [n for n in tree.nodes if n.bl_idname in ("HL_NodeGroupInput", "HL_NodeGroupOutput")]
    for t in bpy.data.node_groups:
        if t.bl_idname == "HL_LogicTree":
            out += [n for n in t.nodes if n.bl_idname == "HL_NodeGroup" and n.group == tree]
    return out


def sync(tree) -> None:
    """Make the sockets of the group's Group Input / Output and Group nodes match its items (wires and typed values
    on sockets that stay are kept)."""
    from .logic import _rebuild
    for node in _users_and_io(tree):
        _rebuild(node)


def _add_socket(node, it, as_output: bool):
    from .logic import EVENT_LINK_LIMIT, _data_socket
    sockets = node.outputs if as_output else node.inputs
    if it.kind == "event":
        s = sockets.new("HL_EventSocket", it.name, identifier=it.uid)
        if not as_output:
            s.link_limit = EVENT_LINK_LIMIT
        return s
    return _data_socket(sockets, it.kind, it.uid, it.name)


def _ancestors_ok(tree, group) -> bool:
    """False when group is tree itself or (through its own Group nodes) contains tree: a group in itself."""
    todo, seen = [group], set()
    while todo:
        g = todo.pop()
        if g is None or g.name in seen:
            continue
        if g == tree:
            return False
        seen.add(g.name)
        todo += [n.group for n in g.nodes if n.bl_idname == "HL_NodeGroup"]
    return True


def _poll_group(self, tree):
    return is_group(tree) and _ancestors_ok(self.id_data, tree)


def _group_changed(self, context):
    from .logic import _rebuild
    if self.group is not None:
        self.group.use_fake_user = True          # (kept on save even while no Group node uses it)
    _rebuild(self)


def _make_node_classes():
    from .logic import _Node

    class HL_NodeGroup(_Node, bpy.types.Node):
        """A node group: a logic graph of its own, used like one node (Tab goes in, Ctrl+G makes one from selected
        nodes)"""
        bl_idname, bl_label, bl_icon = "HL_NodeGroup", "Group", "NODETREE"
        kind, category = "GROUP", "Group"
        group: PointerProperty(type=bpy.types.NodeTree, name="Group", poll=_poll_group, update=_group_changed)

        def init(self, context):
            super().init(context)
            self.color = GROUP_COLOR
            self.width = 180

        def make_sockets(self):
            for it in items(self.group, False):
                _add_socket(self, it, False)
            for it in items(self.group, True):
                _add_socket(self, it, True)

        def draw_label(self):
            return f"Group: {self.note or (self.group.name if self.group else '(none)')}"

        def draw_buttons(self, context, layout):
            row = layout.row(align=True)
            row.template_ID(self, "group", new="hammerless.logic_group_new")
            if self.group is not None:
                row.operator("hammerless.logic_group_edit", text="", icon="FILE_PARENT").node_name = self.name

    class HL_NodeGroupInput(_Node, bpy.types.Node):
        """What comes into the group: one output per input of the Group node (edit them in the sidebar, Group tab)"""
        bl_idname, bl_label, bl_icon = "HL_NodeGroupInput", "Group Input", "IMPORT"
        kind, category = "GROUP_IN", "Group"

        @classmethod
        def poll(cls, ntree):
            return is_group(ntree)

        def init(self, context):
            super().init(context)
            self.color = GROUP_COLOR

        def make_sockets(self):
            for it in items(self.id_data, False):
                _add_socket(self, it, True)

    class HL_NodeGroupOutput(_Node, bpy.types.Node):
        """What goes out of the group: one input per output of the Group node"""
        bl_idname, bl_label, bl_icon = "HL_NodeGroupOutput", "Group Output", "EXPORT"
        kind, category = "GROUP_OUT", "Group"

        @classmethod
        def poll(cls, ntree):
            return is_group(ntree)

        def init(self, context):
            super().init(context)
            self.color = GROUP_COLOR

        def make_sockets(self):
            for it in items(self.id_data, True):
                _add_socket(self, it, False)

    return HL_NodeGroup, HL_NodeGroupInput, HL_NodeGroupOutput


HL_NodeGroup, HL_NodeGroupInput, HL_NodeGroupOutput = _make_node_classes()


# ---------------------------------------------------------------- flattening (build)

class _Flat:
    def __init__(self):
        self.nodes = []          # LNodes, named with the copy's prefix
        self.links = []          # LLinks between them
        self.ins = {}            # input uid -> [(node id, socket) | ("OUT", uid)]: what the Group Input feeds
        self.outs = {}           # output uid -> [(node id, socket) | ("IN", uid)]: what feeds the Group Output


def _const_of(sock):
    v = sock.value
    if sock.bl_idname == "HL_ThingSocket":
        return v.name if v is not None else ""
    if sock.bl_idname == "HL_VectorSocket":
        return tuple(v)
    return v


def flatten(context, tree, report, prefix: str = "", stack: tuple = (), checked: set | None = None) -> _Flat:
    """The tree's nodes and wires with every Group node replaced by what's inside it (recursively)."""
    from .logic import _Node, _check_nodes, _compiled_links
    flat = _Flat()
    if tree.name in stack:
        report.warnings.append(f"{tree.name}: the group is inside itself, so that copy is left out")
        return flat
    checked = set() if checked is None else checked
    if tree.name not in checked:              # (a group used several times: its problems reported once)
        checked.add(tree.name)
        _check_nodes(context, tree, report)
    nodes = {n.name: n for n in tree.nodes if isinstance(n, _Node) and not n.mute and n.kind != "OBJECT_INFO"}
    links = _compiled_links(tree, report)
    sub: dict[str, _Flat] = {}
    by_id = {}
    for n in nodes.values():
        if n.kind == "GROUP":
            if n.group is None:
                report.warnings.append(f"{tree.name}: Group node '{node_title(n)}' has no group picked")
                continue
            sub[n.name] = f = flatten(context, n.group, report, prefix + n.name + "/", stack + (tree.name,), checked)
            flat.nodes += f.nodes
            flat.links += f.links
        elif n.kind not in ("GROUP_IN", "GROUP_OUT"):
            ln = n.to_lnode(context)
            ln.id = prefix + ln.id
            if ln.kind in ("SET_VAR", "GET_VAR") and ln.settings.get("scope") == "GROUP":
                ln.settings = dict(ln.settings, name=prefix + (ln.settings.get("name") or "value"), scope="MAP")
            flat.nodes.append(ln)
    for ln in flat.nodes:
        by_id[ln.id] = ln

    def sources(name, sock, seen=()):
        n = nodes.get(name)
        if n is None:
            return []
        if n.kind == "GROUP_IN":
            return [("IN", sock)]
        if n.kind == "GROUP":
            out = []
            for s in sub.get(name, _Flat()).outs.get(sock, []):
                if s[0] == "IN":                 # (straight through that group: what feeds its input)
                    out += [x for l in links if l.to_node == name and l.to_socket == s[1]
                            for x in sources(l.from_node, l.from_socket)]
                else:
                    out.append(s)
            return out
        return [(prefix + name, sock)]

    def targets(name, sock):
        n = nodes.get(name)
        if n is None:
            return []
        if n.kind == "GROUP_OUT":
            return [("OUT", sock)]
        if n.kind == "GROUP":
            out = []
            for t in sub.get(name, _Flat()).ins.get(sock, []):
                if t[0] == "OUT":
                    out += [x for l in links if l.from_node == name and l.from_socket == t[1]
                            for x in targets(l.to_node, l.to_socket)]
                else:
                    out.append(t)
            return out
        return [(prefix + name, sock)]

    for l in links:
        for s in sources(l.from_node, l.from_socket):
            for t in targets(l.to_node, l.to_socket):
                if s[0] == "IN" and t[0] == "OUT":
                    flat.ins.setdefault(s[1], []).append(t)
                    flat.outs.setdefault(t[1], []).append(s)
                elif s[0] == "IN":
                    flat.ins.setdefault(s[1], []).append(t)
                elif t[0] == "OUT":
                    flat.outs.setdefault(t[1], []).append(s)
                else:
                    flat.links.append(LLink(s[0], s[1], t[0], t[1], data=l.data))
    seen, unique = set(), []
    for l in flat.links:          # (a wire through a group is found from its input's side and its output's: once)
        key = (l.from_node, l.from_socket, l.to_node, l.to_socket, l.data)
        if key not in seen:
            seen.add(key)
            unique.append(l)
    flat.links = unique
    if getattr(tree, "is_group", False):
        for it in tree.group_items:
            if it.is_output and it.kind != "event" and len({tuple(x) for x in flat.outs.get(it.uid, [])}) > 1:
                report.warnings.append(f"{tree.name}: the output '{it.name}' is fed by more than one Group Output "
                                       "node, so only one of them gives the value: wire it from one place")
    # a value typed on a Group node's (unwired) input goes to the nodes inside it
    wired = {(l.to_node, l.to_socket) for l in flat.links if l.data}
    for name, n in nodes.items():
        if n.kind != "GROUP" or name not in sub:
            continue
        for sock in n.inputs:
            if sock.is_linked or sock.bl_idname == "HL_EventSocket" or not hasattr(sock, "value"):
                continue
            for t in targets(name, sock.identifier):
                if t[0] != "OUT" and t[0] in by_id and (t[0], t[1]) not in wired:
                    by_id[t[0]].consts[t[1]] = _const_of(sock)
    return flat


# ---------------------------------------------------------------- operators

def _edit_tree(context):
    space = context.space_data
    return getattr(space, "edit_tree", None) if space is not None and space.type == "NODE_EDITOR" else None


class _InLogic:
    @classmethod
    def poll(cls, context):
        t = _edit_tree(context)
        return t is not None and t.bl_idname == "HL_LogicTree"


def new_group_tree(name: str):
    tree = bpy.data.node_groups.new(name, "HL_LogicTree")
    tree.is_group = True
    tree.use_fake_user = True
    return tree


class HL_OT_logic_group_new(bpy.types.Operator):
    bl_idname = "hammerless.logic_group_new"
    bl_label = "New Group"
    bl_description = "Make a new, empty node group (with a Group Input and Group Output)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = new_group_tree("Group")
        tree.nodes.new("HL_NodeGroupInput").location = (-300, 0)
        tree.nodes.new("HL_NodeGroupOutput").location = (300, 0)
        node = getattr(context, "node", None)
        if node is not None and node.bl_idname == "HL_NodeGroup":
            node.group = tree
        return {"FINISHED"}


class HL_OT_logic_group_edit(_InLogic, bpy.types.Operator):
    bl_idname = "hammerless.logic_group_edit"
    bl_label = "Enter / Exit Group"
    bl_description = "Go into the selected Group node's group, or back out of the group you're in (Tab)"
    bl_options = {"REGISTER"}

    node_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        space = context.space_data
        tree = space.edit_tree
        node = tree.nodes.get(self.node_name) if self.node_name else tree.nodes.active
        if node is not None and node.bl_idname == "HL_NodeGroup" and node.group is not None and (
                self.node_name or node.select):
            space.path.append(node.group, node=node)
            return {"FINISHED"}
        if len(space.path) > 1:
            space.path.pop()
            return {"FINISHED"}
        return {"CANCELLED"}


def _selected_logic_nodes(tree):
    return [n for n in tree.nodes if n.select and n.bl_idname not in ("HL_NodeGroupInput", "HL_NodeGroupOutput")
            and n.bl_idname != "NodeFrame"]


class HL_OT_logic_group_make(_InLogic, bpy.types.Operator):
    bl_idname = "hammerless.logic_group_make"
    bl_label = "Make Group"
    bl_description = ("Put the selected nodes into a new node group, used by one Group node in their place: wires "
                      "into and out of the selection become the group's inputs and outputs (Ctrl+G)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        space = context.space_data
        parent = space.edit_tree
        sel = _selected_logic_nodes(parent)
        if not sel:
            self.report({"WARNING"}, "Select the nodes to put in a group first")
            return {"CANCELLED"}
        names = {n.name for n in sel}
        crossing_in = [l for l in parent.links if l.to_node.name in names and l.from_node.name not in names]
        crossing_out = [l for l in parent.links if l.from_node.name in names and l.to_node.name not in names]
        for l in crossing_in + crossing_out:
            if l.from_socket.bl_idname == "HL_ObjectSocket":
                self.report({"WARNING"}, f"The object wire into '{node_title(l.to_node)}' would cross "
                                         "into the group: select its Object Info node too")
                return {"CANCELLED"}
        group = new_group_tree("Group")
        for n in parent.nodes:            # (a selected Group Input / Output or frame isn't copied in)
            n.select = n.name in names
        # (the nodes are copied with Blender's own clipboard, which keeps their settings and the wires between them)
        bpy.ops.node.clipboard_copy()
        center = [sum(n.location[i] for n in sel) / len(sel) for i in (0, 1)]
        space.path.append(group)
        try:
            bpy.ops.node.clipboard_paste()
        finally:
            space.path.pop()
        inner = {n.name: n for n in group.nodes}
        xs = [n.location[0] for n in group.nodes] or [0.0]
        ys = [n.location[1] for n in group.nodes] or [0.0]
        gin = group.nodes.new("HL_NodeGroupInput")
        gout = group.nodes.new("HL_NodeGroupOutput")
        gin.location = (min(xs) - 300, sum(ys) / len(ys))
        gout.location = (max(xs) + 300, sum(ys) / len(ys))
        # one group input per outer socket wired in, one group output per inner socket wired out
        in_items, out_items = {}, {}
        for l in crossing_in:
            key = (l.from_node.name, l.from_socket.identifier)
            if key not in in_items:
                in_items[key] = new_item(group, l.to_socket.name, KIND_OF_SOCKET.get(l.from_socket.bl_idname, "any"),
                                         False).uid
        for l in crossing_out:
            key = (l.from_node.name, l.from_socket.identifier)
            if key not in out_items:
                out_items[key] = new_item(group, l.from_socket.name,
                                          KIND_OF_SOCKET.get(l.from_socket.bl_idname, "any"), True).uid
        from .logic import _rebuild
        _rebuild(gin)
        _rebuild(gout)

        def sock(node, ident, outputs):
            return next((s for s in (node.outputs if outputs else node.inputs) if s.identifier == ident), None)
        plan_in = [(in_items[(l.from_node.name, l.from_socket.identifier)], l.to_node.name, l.to_socket.identifier,
                    l.from_node.name, l.from_socket.identifier) for l in crossing_in]
        plan_out = [(out_items[(l.from_node.name, l.from_socket.identifier)], l.from_node.name,
                     l.from_socket.identifier, l.to_node.name, l.to_socket.identifier) for l in crossing_out]
        for uid, tn, ts, _fn, _fs in plan_in:
            a, b = sock(gin, uid, True), sock(inner[tn], ts, False) if tn in inner else None
            if a and b:
                group.links.new(a, b)
        for uid, fn, fs, _tn, _ts in plan_out:
            a, b = sock(inner[fn], fs, True) if fn in inner else None, sock(gout, uid, False)
            if a and b and not b.is_linked:
                group.links.new(a, b)
        # the Group node in their place
        node = parent.nodes.new("HL_NodeGroup")
        node.group = group
        node.location = center
        for uid, _tn, _ts, fn, fs in plan_in:
            a, b = sock(parent.nodes[fn], fs, True), sock(node, uid, False)
            if a and b and not any(l.from_socket == a and l.to_socket == b for l in parent.links):
                parent.links.new(a, b)
        for uid, _fn, _fs, tn, ts in plan_out:
            a, b = sock(node, uid, True), sock(parent.nodes[tn], ts, False)
            if a and b:
                parent.links.new(a, b)
        for n in sel:
            parent.nodes.remove(n)
        for n in parent.nodes:
            n.select = False
        node.select = True
        parent.nodes.active = node
        return {"FINISHED"}


class HL_OT_logic_group_ungroup(_InLogic, bpy.types.Operator):
    bl_idname = "hammerless.logic_group_ungroup"
    bl_label = "Ungroup"
    bl_description = "Put the selected Group node's nodes back in this graph, in its place (Ctrl+Alt+G)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        space = context.space_data
        parent = space.edit_tree
        node = parent.nodes.active
        if node is None or node.bl_idname != "HL_NodeGroup" or node.group is None:
            self.report({"WARNING"}, "Select a Group node first")
            return {"CANCELLED"}
        group = node.group
        before = set(parent.nodes)
        # (pasting renames a node whose name is taken here: the copies get marked names first, to be found again)
        marks = {}
        space.path.append(group, node=node)
        try:
            for n in group.nodes:
                n.select = n.bl_idname not in ("HL_NodeGroupInput", "HL_NodeGroupOutput")
                if n.select:
                    old = n.name
                    n.name = f"hl_ungroup {len(marks)}"
                    marks[n.name] = old
            bpy.ops.node.clipboard_copy()
            for mark, old in marks.items():
                group.nodes[mark].name = old
        finally:
            space.path.pop()
        bpy.ops.node.clipboard_paste()
        pasted = {}
        for n in parent.nodes:
            if n not in before and n.name in marks:
                pasted[marks[n.name]] = n
        for old, n in pasted.items():
            n.name = old                   # (Blender adds .001 when the name is taken)
        gin = [n for n in group.nodes if n.bl_idname == "HL_NodeGroupInput"]
        gout = [n for n in group.nodes if n.bl_idname == "HL_NodeGroupOutput"]

        def sock(nd, ident, outputs):
            return next((s for s in (nd.outputs if outputs else nd.inputs) if s.identifier == ident), None)
        outer_in = {}
        for l in parent.links:
            if l.to_node == node:
                outer_in.setdefault(l.to_socket.identifier, []).append(l.from_socket)
        outer_out = {}
        for l in parent.links:
            if l.from_node == node:
                outer_out.setdefault(l.from_socket.identifier, []).append(l.to_socket)
        for l in group.links:
            if l.from_node in gin and l.to_node.name in pasted:
                for s in outer_in.get(l.from_socket.identifier, []):
                    t = sock(pasted[l.to_node.name], l.to_socket.identifier, False)
                    if t:
                        parent.links.new(s, t)
                if not outer_in.get(l.from_socket.identifier):
                    gs = sock(node, l.from_socket.identifier, False)
                    t = sock(pasted[l.to_node.name], l.to_socket.identifier, False)
                    if gs is not None and t is not None and hasattr(gs, "value") and hasattr(t, "value"):
                        try:
                            t.value = gs.value
                        except (TypeError, AttributeError):
                            pass
            if l.to_node in gout and l.from_node.name in pasted:
                f = sock(pasted[l.from_node.name], l.from_socket.identifier, True)
                for t in outer_out.get(l.to_socket.identifier, []):
                    if f:
                        parent.links.new(f, t)
        parent.nodes.remove(node)
        return {"FINISHED"}


class HL_OT_logic_group_item(_InLogic, bpy.types.Operator):
    bl_idname = "hammerless.logic_group_item"
    bl_label = "Group Socket"
    bl_description = "Add, remove or move one of the group's inputs / outputs"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=[("ADD_IN", "Add Input", ""), ("ADD_OUT", "Add Output", ""), ("REMOVE", "Remove", ""),
                                ("UP", "Up", ""), ("DOWN", "Down", ""), ("KIND", "Kind", "")])
    index: IntProperty(default=-1)
    kind: EnumProperty(items=ITEM_KINDS, default="event")

    def execute(self, context):
        tree = _edit_tree(context)
        if not is_group(tree):
            return {"CANCELLED"}
        coll = tree.group_items
        if self.action in ("ADD_IN", "ADD_OUT"):
            new_item(tree, "Event" if self.kind == "event" else "Value", self.kind, self.action == "ADD_OUT")
        elif 0 <= self.index < len(coll):
            if self.action == "REMOVE":
                coll.remove(self.index)
            elif self.action == "KIND":
                coll[self.index].kind = self.kind
            else:
                same = [i for i, it in enumerate(coll) if it.is_output == coll[self.index].is_output]
                k = same.index(self.index) + (-1 if self.action == "UP" else 1)
                if 0 <= k < len(same):
                    coll.move(self.index, same[k])
        sync(tree)
        return {"FINISHED"}


class HL_OT_logic_group_add(_InLogic, bpy.types.Operator):
    bl_idname = "hammerless.logic_group_add"
    bl_label = "Add Group"
    bl_description = "Add a Group node using this group"
    bl_options = {"REGISTER", "UNDO"}

    group_name: StringProperty()

    def invoke(self, context, event):
        tree = _edit_tree(context)
        group = bpy.data.node_groups.get(self.group_name)
        if not is_group(group):
            return {"CANCELLED"}
        if not _ancestors_ok(tree, group):
            self.report({"WARNING"}, "A group can't be put inside itself")
            return {"CANCELLED"}
        for n in tree.nodes:
            n.select = False
        node = tree.nodes.new("HL_NodeGroup")
        node.group = group
        node.location = context.space_data.cursor_location
        node.select = True
        tree.nodes.active = node
        bpy.ops.node.translate_attach_remove_on_cancel("INVOKE_DEFAULT")
        return {"FINISHED"}


class HL_PT_logic_group(bpy.types.Panel):
    bl_space_type, bl_region_type, bl_category = "NODE_EDITOR", "UI", "Group"
    bl_label = "Group Sockets"

    @classmethod
    def poll(cls, context):
        return is_group(_edit_tree(context))

    def draw(self, context):
        tree = _edit_tree(context)
        layout = self.layout
        for outputs, title in ((False, "Inputs"), (True, "Outputs")):
            layout.label(text=title)
            col = layout.column(align=True)
            for i, it in enumerate(tree.group_items):
                if it.is_output != outputs:
                    continue
                row = col.row(align=True)
                row.prop(it, "name", text="")
                op = row.operator_menu_enum("hammerless.logic_group_item", "kind", text=dict(
                    (k, l) for k, l, _d in ITEM_KINDS)[it.kind])
                op.action, op.index = "KIND", i
                for action, icon in (("UP", "TRIA_UP"), ("DOWN", "TRIA_DOWN"), ("REMOVE", "X")):
                    op = row.operator("hammerless.logic_group_item", text="", icon=icon)
                    op.action, op.index = action, i
            row = layout.row(align=True)
            op = row.operator_menu_enum("hammerless.logic_group_item", "kind",
                                        text="Add Output" if outputs else "Add Input", icon="ADD")
            op.action = "ADD_OUT" if outputs else "ADD_IN"


class HL_MT_logic_groups(bpy.types.Menu):
    bl_idname = "HL_MT_logic_groups"
    bl_label = "Groups"

    def draw(self, context):
        layout = self.layout
        tree = _edit_tree(context)
        layout.operator("hammerless.logic_group_make", icon="NODETREE")
        layout.operator("hammerless.logic_group_ungroup")
        if is_group(tree):
            layout.separator()
            for idname in ("HL_NodeGroupInput", "HL_NodeGroupOutput"):
                cls = getattr(bpy.types, idname)
                op = layout.operator("node.add_node", text=cls.bl_label, icon=cls.bl_icon)
                op.type, op.use_transform = idname, True
        groups = [g for g in bpy.data.node_groups if is_group(g) and g != tree]
        if groups:
            layout.separator()
        for g in sorted(groups, key=lambda g: g.name):
            layout.operator("hammerless.logic_group_add", text=g.name, icon="NODETREE").group_name = g.name


CLASSES = (HL_GroupItem, HL_NodeGroup, HL_NodeGroupInput, HL_NodeGroupOutput, HL_OT_logic_group_new,
           HL_OT_logic_group_edit, HL_OT_logic_group_make, HL_OT_logic_group_ungroup, HL_OT_logic_group_item,
           HL_OT_logic_group_add, HL_PT_logic_group, HL_MT_logic_groups)
_keymaps = []


def register():
    from .logic import HL_LogicTree
    bpy.utils.register_class(HL_GroupItem)
    HL_LogicTree.is_group = BoolProperty(name="Is a Group", default=False)
    HL_LogicTree.group_items = bpy.props.CollectionProperty(type=HL_GroupItem)
    HL_LogicTree.hl_next_uid = IntProperty(default=0)
    for c in CLASSES[1:]:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name="Node Editor", space_type="NODE_EDITOR")
        for idname, key, ctrl, alt in (("hammerless.logic_group_edit", "TAB", False, False),
                                       ("hammerless.logic_group_make", "G", True, False),
                                       ("hammerless.logic_group_ungroup", "G", True, True)):
            _keymaps.append((km, km.keymap_items.new(idname, key, "PRESS", ctrl=ctrl, alt=alt)))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError):
            pass
    _keymaps.clear()
    for c in reversed(CLASSES[1:]):
        bpy.utils.unregister_class(c)
    from .logic import HL_LogicTree
    for p in ("is_group", "group_items", "hl_next_uid"):
        try:
            delattr(HL_LogicTree, p)
        except AttributeError:
            pass
    bpy.utils.unregister_class(HL_GroupItem)
