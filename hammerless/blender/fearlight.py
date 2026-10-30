"""Editable, bounded common-infected shade-seeking example built from ordinary nodes."""
import bpy
from .botactions import new_action
from .logicgroups import new_item, sync

PARAMETERS = (("enter", "Avoid Above Brightness", .8),
              ("exit", "Safe Below Brightness", .4),
              ("improvement", "Minimum Improvement", .1),
              ("radius", "Shade Search Radius", 768.0),
              ("candidates", "Max Candidate Areas", 8.0),
              ("retry", "Search Cooldown (seconds)", 3.0),
              ("path", "Max Nav Path Distance", 2048.0))

HELP = {
    "enter": "Start seeking shade above this brightness score. Try 0.8 first; lower it if zombies never react. Score = baked ambient luminance x 100,000, not screen brightness or a percentage. No flashlight response.",
    "exit": "Release the movement command below this score. Try 0.4. Keep this LOWER than Avoid Above Brightness to prevent constant switching. Lower values require deeper shade.",
    "improvement": "How much darker a new spot must be before the zombie moves there. At 0.1, a zombie standing at brightness 0.8 needs a spot below 0.7. Lower this to accept smaller improvements; raise it to demand deeper shade.",
    "radius": "How far around each zombie to look for shade, in Source units. Start at 768 (about 64 feet). Larger searches can find more shade but cost more. Maximum 4096.",
    "candidates": "Maximum nearby nav areas to consider per search. Start at 8; try 16 if nearby shade is missed. More candidates cost more, especially for a horde. Maximum 128.",
    "retry": "Seconds between destination searches, even when none succeeds. Start at 3. Brightness is still checked every 0.25 seconds. Lower values react to new destinations faster but cost more.",
    "path": "Maximum allowed nav route length to a candidate, in Source units. Start at 2048. This is walking distance around obstacles, not the straight-line search radius. Maximum 32768.",
}


def fear_light_action():
    tree = new_action("Fear Baked Light (experimental)")
    tree["hl_fear_light_score_version"] = 1
    tree.action_supports, tree.action_channels = "COMMONS", "Movement"
    entry = next(n for n in tree.nodes if n.kind == "BA_ENTRY")
    params = next(n for n in tree.nodes if n.bl_idname == "HL_NodeGroupInput")
    for uid, name, _ in PARAMETERS:
        new_item(tree, name, "num", False, uid)
    sync(tree)
    sequence = [0]
    current_frame = [None]

    def stage(label, x):
        frame = tree.nodes.new("NodeFrame")
        frame.label = label
        frame.location = (x, 0)
        frame.use_custom_color = True
        frame.color = (.12, .20, .23)
        current_frame[0] = frame
        sequence[0] = 0

    stage("1. Check light, maintain hysteresis, and release our command", 0)

    def node(kind, label, **properties):
        n = tree.nodes.new(kind)
        n.label = label
        n.width = 215
        n.parent = current_frame[0]
        n.location = (sequence[0] % 4 * 280, -(sequence[0] // 4) * 300)
        sequence[0] += 1
        for key, value in properties.items():
            setattr(n, key, value)
        return n

    def sock(n, key, out=False):
        return next(s for s in (n.outputs if out else n.inputs) if s.identifier == key)

    def wire(a, out, b, inp):
        tree.links.new(sock(a, out, True), sock(b, inp))

    def value(n, key, val):
        sock(n, key).value = val

    def get(name, kind="bool", default=False):
        n = node("HL_BAStateGet", name, state_name=name, value_type=kind)
        value(n, "default", default)
        return n

    def set_(name, kind="bool", val=False):
        n = node("HL_BAStateSet", "Store " + name, state_name=name, value_type=kind)
        value(n, "value", val)
        return n

    def condition(label, source, port):
        n = node("HL_NodeIf", label)
        wire(source, port, n, "condition")
        return n

    def compare(label, op, a, ao, b=None, bo=None, constant=None):
        n = node("HL_NodeCompare", label, op=op)
        wire(a, ao, n, "a")
        if b is not None:
            wire(b, bo, n, "b")
        else:
            value(n, "b", constant)
        return n

    def both(label, a, ao, b, bo, op="AND"):
        n = node("HL_NodeBoolMath", label, op=op)
        wire(a, ao, n, "a"); wire(b, bo, n, "b")
        return n

    owned = get("movement_owned")
    next_search = get("next_search", "num", 0)
    clock = node("HL_NodeTime", "Game time")
    origin = node("HL_NodeGetProperty", "Bot feet position", prop_type="vector", key="m_vecOrigin")
    wire(entry, "bot", origin, "entity")
    body = node("HL_NodeVectorMath", "Sample at body height", op="ADD")
    wire(origin, "value", body, "a"); value(body, "b", (0, 0, 32))
    ambient = node("HL_WQAmbient", "Current baked ambient")
    current_score = node("HL_NodeMath", "Readable brightness score (ambient x 100,000)", op="MULTIPLY")
    wire(ambient, "brightness", current_score, "a"); value(current_score, "b", 100000)
    wire(body, "vector", ambient, "position")
    available = condition("Only use real lighting data", ambient, "available")
    wire(entry, "start", ambient, "run"); wire(entry, "update", ambient, "run")
    wire(ambient, "next", available, "in")
    bright = compare("Begin avoiding light", "GREATER", current_score, "value", params, "enter")
    above_safe = compare("Still outside shade", "GREATER", current_score, "value", params, "exit")
    continuing = both("Keep existing escape until safe", owned, "value", above_safe, "result")
    needs_shade = both("Hysteresis", bright, "result", continuing, "result", op="OR")
    flee = condition("Needs shade?", needs_shade, "result")
    ordered = compare("Enter threshold exceeds exit", "GREATER", params, "enter", params, "exit")
    nonnegative = compare("Exit threshold is nonnegative", "GREATER_EQUAL", params, "exit", constant=0)
    thresholds = both("Valid hysteresis thresholds", ordered, "result", nonnegative, "result")
    valid_settings = condition("Enter > Exit >= 0 required", thresholds, "result")
    wire(available, "true", valid_settings, "in")
    wire(valid_settings, "true", flee, "in")

    release = condition("Only release our own command", owned, "value")
    reset = node("HL_BACommand", "Return to normal AI", command="RESET")
    clear_owned = set_("movement_owned")
    wire(available, "false", release, "in")
    wire(valid_settings, "false", release, "in")
    wire(flee, "false", release, "in")
    wire(entry, "stop", release, "in")
    wire(release, "true", reset, "run"); wire(reset, "next", clear_owned, "run")

    stage("2. Cooldown and bounded nearby-area search", 1300)
    due_value = compare("Search deadline reached", "GREATER_EQUAL", clock, "value", next_search, "value")
    due = condition("Search only after cooldown", due_value, "result")
    wire(flee, "true", due, "in")
    deadline = node("HL_NodeMath", "Next search time", op="ADD")
    retry = node("HL_NodeMath", "At least a quarter second between searches", op="MAXIMUM")
    wire(params, "retry", retry, "a"); value(retry, "b", .25)
    wire(clock, "value", deadline, "a"); wire(retry, "value", deadline, "b")
    save_deadline = set_("next_search", "num", 0)
    wire(deadline, "value", save_deadline, "value"); wire(due, "true", save_deadline, "run")
    nearest = node("HL_WQNearest", "Find bot's nav area")
    wire(origin, "value", nearest, "position"); wire(save_deadline, "next", nearest, "run")
    valid_start = condition("Bot has a nav area", nearest, "found")
    wire(nearest, "next", valid_start, "in")
    nearby = node("HL_WQNearby", "Bounded candidate search")
    wire(origin, "value", nearby, "position")
    wire(params, "radius", nearby, "radius"); wire(params, "candidates", nearby, "limit")
    wire(valid_start, "true", nearby, "run")
    initial_best = node("HL_NodeMath", "Require a meaningful improvement", op="SUBTRACT")
    improvement = node("HL_NodeMath", "Require a strictly darker destination", op="MAXIMUM")
    wire(params, "improvement", improvement, "a"); value(improvement, "b", 0)
    wire(current_score, "value", initial_best, "a"); wire(improvement, "value", initial_best, "b")
    save_best = set_("best_brightness", "num", 0)
    wire(initial_best, "value", save_best, "value"); wire(nearby, "next", save_best, "run")
    clear_found = set_("found_destination")
    wire(save_best, "next", clear_found, "run")
    each = node("HL_NodeForEach", "Consider capped nearby areas", what="NAV_AREAS")
    wire(nearby, "areas", each, "list"); wire(clear_found, "next", each, "run")
    stage("3. Score darker, dry, reachable candidates", 2600)
    info = node("HL_WQInfo", "Candidate center and blocker state")
    wire(each, "each", info, "run"); wire(each, "item", info, "area")
    unblocked = node("HL_NodeBoolMath", "Not blocked", op="NOT")
    wire(info, "blocked", unblocked, "a")
    dry = node("HL_NodeBoolMath", "Not underwater", op="NOT")
    wire(info, "underwater", dry, "a")
    walkable = both("Unblocked dry area", unblocked, "result", dry, "result")
    valid = both("Valid usable candidate", info, "found", walkable, "result")
    candidate_ok = condition("Candidate available", valid, "result")
    wire(info, "next", candidate_ok, "in")
    sample_pos = node("HL_NodeVectorMath", "Candidate body height", op="ADD")
    wire(info, "position", sample_pos, "a"); value(sample_pos, "b", (0, 0, 32))
    sample = node("HL_WQAmbient", "Candidate baked ambient")
    candidate_score = node("HL_NodeMath", "Candidate brightness score (ambient x 100,000)", op="MULTIPLY")
    wire(sample, "brightness", candidate_score, "a"); value(candidate_score, "b", 100000)
    wire(sample_pos, "vector", sample, "position"); wire(candidate_ok, "true", sample, "run")
    sample_valid = condition("Missing sample is not darkness", sample, "available")
    wire(sample, "next", sample_valid, "in")
    best = get("best_brightness", "num", 0)
    darker_value = compare("Darker than best so far", "LESS", candidate_score, "value", best, "value")
    darker = condition("Worth checking a path", darker_value, "result")
    wire(sample_valid, "true", darker, "in")
    path = node("HL_WQPath", "Reachable for infected")
    wire(nearest, "area", path, "start"); wire(each, "item", path, "goal")
    wire(params, "path", path, "length"); wire(darker, "true", path, "run")
    reachable = condition("Path exists", path, "exists")
    wire(path, "next", reachable, "in")
    update_best = set_("best_brightness", "num", 0)
    wire(candidate_score, "value", update_best, "value"); wire(reachable, "true", update_best, "run")
    destination = set_("destination", "vec", (0, 0, 0))
    wire(info, "position", destination, "value"); wire(update_best, "next", destination, "run")
    found = set_("found_destination", val=True)
    wire(destination, "next", found, "run")
    stage("4. Request movement (experimental: engine AI may override)", 3900)
    found_value = get("found_destination")
    move_if = condition("Found a darker reachable destination", found_value, "value")
    wire(each, "done", move_if, "in")
    wire(move_if, "false", release, "in")
    wire(valid_start, "false", release, "in")
    target = get("destination", "vec", (0, 0, 0))
    move = node("HL_BACommand", "Move toward shade", command="MOVE")
    wire(target, "value", move, "destination"); wire(move_if, "true", move, "run")
    mark_owned = set_("movement_owned", val=True)
    wire(move, "next", mark_owned, "run")
    entry.location = (-580, 200); params.location = (-580, -120)
    return tree


def fear_light_example(tree):
    offset = max((n.location.x + n.width for n in tree.nodes), default=-500) + 500
    for n in tree.nodes:
        n.select = False
    start, find, loop, action, apply = [tree.nodes.new(k) for k in
        ("HL_NodeMapStart", "HL_BAFind", "HL_BALoop", "HL_BAAction", "HL_BAApply")]
    for n, pos in zip((start, find, loop, action, apply), ((0, 200), (0, -80), (300, 200), (300, -180), (650, 200))):
        n.location = (offset + pos[0], pos[1]); n.width = 240; n.select = True
    find.selection = "COMMONS"
    loop.inputs["Interval (seconds)"].value = .25
    action.group = fear_light_action()
    for _, name, default in PARAMETERS:
        action.inputs[name].value = default
    for a, out, b, inp in ((start, "On Map Start", loop, "Start"), (find, "Bots", loop, "Bots"),
                            (loop, "Each Bot", apply, "Run"), (loop, "Current Bot", apply, "Bot"),
                            (action, "Action", apply, "Action")):
        tree.links.new(a.outputs[out], b.inputs[inp])
    return action


class HL_OT_fear_light_help(bpy.types.Operator):
    bl_idname, bl_label = "hammerless.fear_light_help", "Fear Light setting"
    field: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        return HELP.get(properties.field, "Fear Baked Light settings")

    def execute(self, context):
        self.report({"INFO"}, HELP.get(self.field, ""))
        return {"FINISHED"}


def _setting_help_operator(uid, label):
    # Static RNA descriptions also work when Blender does not resolve dynamic
    # operator descriptions for an icon-only button in a custom node socket.
    def execute(self, context):
        import textwrap
        lines = textwrap.wrap(HELP[uid], width=58)
        def draw(menu, context):
            for line in lines:
                menu.layout.label(text=line)
        context.window_manager.popup_menu(draw, title=label, icon='INFO')
        return {'FINISHED'}
    return type('HL_OT_fear_help_' + uid, (bpy.types.Operator,), {
        '__module__': __name__,
        'bl_idname': 'hammerless.fear_help_' + uid,
        'bl_label': label,
        'bl_description': HELP[uid],
        'bl_options': {'INTERNAL'},
        'execute': execute,
    })


HELP_OPERATORS = tuple(_setting_help_operator(uid, name) for uid, name, _ in PARAMETERS)


class HL_OT_fear_light_example(bpy.types.Operator):
    bl_idname, bl_label = "hammerless.fear_light_example", "Fear Baked Light (experimental)"
    bl_description = "Add editable common-infected shade seeking; uses baked ambient data, not flashlights. Movement requests are experimental and game AI may override them"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = context.space_data.edit_tree
        if tree is None or getattr(tree, "is_group", False):
            return {"CANCELLED"}
        node = fear_light_example(tree)
        tree.nodes.active = node
        if context.region and context.region.type == "WINDOW":
            bpy.ops.node.view_selected()
        return {"FINISHED"}
