"""Bounded, synchronous compiler for owned bot action loops and editable groups.

These paths deliberately do not use the ordinary event compiler: an engine I/O
delay must never retain a transient Current Bot or lifecycle context.
"""
from contextlib import contextmanager
import math
from pathlib import Path

from . import vscript as vs
from . import worldqueries, listtools, actionutilities, coordination, candidates, routes, diagnostics, workbudgets


class BotActionProblem(str):
    """A fatal bot-action graph diagnostic, distinct from legacy logic warnings."""


KINDS = {"COMMONS", "SPECIALS", "SURVIVORS", "ALL"}
PURE = {"WB_POOL", "ROUTE_DATA", "VALUE", "EXPOSED_VALUE", "MATH", "COMPARE", "BOOL_MATH", "MAKE_VECTOR",
        "BREAK_VECTOR", "VECTOR_MATH", "CHECK", "PICK", "TEXT_CHECK", "FORMAT_TEXT",
        "CAND_AMBIENT", "GET_FIELD", "GET_PROP", "SEEN", "VIEW", "SELF", "TIME", "BA_FIND"}
BA = {"BA_LOOP", "BA_FIND", "BA_APPLY", "BA_ACTION", "BA_ENTRY", "BA_PARAM",
      "BA_OVERRIDE", "BA_STATE_GET", "BA_STATE_SET", "BA_COMMAND", "BA_RESULT", "BA_CHILD", "BA_CHOOSE", "BA_PRIORITY", "BA_SEQUENCE"} | set(actionutilities.KINDS) | set(coordination.CLAIMS) | set(diagnostics.ACTION) | set(workbudgets.ACTION)

HELPERS = r'''
HLBA.Supports <- function(bot, kind) {
    if (!IsBot(bot)) return false;
    local c=bot.GetClassname();
    if (kind == "ALL") return true;
    if (kind == "COMMONS") return c == "infected";
    if (c != "player") return false;
    return kind == "SURVIVORS" ? bot.IsSurvivor() : !bot.IsSurvivor();
};
HLBA.Find <- function(kind, origin, radius) {
    local out=[];
    if (radius < 0 || radius != radius) throw "Find Bots radius must be nonnegative";
    foreach (classname in ["player", "infected", "witch"]) {
        local e=null;
        while ((e=Entities.FindByClassname(e,classname)) != null) {
            if (!Supports(e,kind)) continue;
            if (radius > 0 && (e.GetOrigin()-origin).LengthSqr() > radius*radius) continue;
            out.append(e);
        }
    }
    return out;
};
HLBA.Command <- function(ctx, command, target, destination) {
    if (!IsBot(ctx.bot)) throw "Bot commands require a bot";
    local classname=ctx.bot.GetClassname();
    if (classname != "player" && !(classname == "infected" && (command == "MOVE" || command == "RESET")))
        throw "Common infected support Move and Reset; Attack and Retreat require player bots";
    local ids={ ATTACK=0, MOVE=1, RETREAT=2, RESET=3 };
    if (!(command in ids)) throw "Unknown bot command";
    if ((command == "ATTACK" || command == "RETREAT") && (target == null || !target.IsValid()))
        throw "Attack and Retreat require a valid target";
    local args={bot=ctx.bot,cmd=ids[command]};
    if (command == "MOVE") args.pos <- destination;
    if (command == "ATTACK" || command == "RETREAT") args.target <- target;
    if (!CommandABot(args)) throw "The game rejected the bot command";
};
'''


def quote(value):
    return vs.literal(vs.TEXT, str(value))


def find_expression(c, n):
    kind = n.settings.get("kind", "SPECIALS")
    if kind not in KINDS:
        raise ValueError("Find Bots has an unsupported bot kind")
    c.helper("HLBA_Runtime", Path(__file__).with_name("bot_actions_runtime.nut").read_text(encoding="utf-8") + HELPERS)
    return f"HLBA.Find({quote(kind)}, {c.typed_in(n, 'origin', 'vec')}, {c.typed_in(n, 'radius', 'num')})"


class ActionCompiler:
    def __init__(self, compiler, nodes, links):
        self.c = compiler
        self.nodes = {n.id: n for n in nodes}
        self.links = links
        self.owned = set()
        self.definitions = {}
        self.loop = None
        self.lifecycle = False
        self.visiting = set()
        self.query_path = ()
        self.iteration_path = ()
        self.action_bindings = {}
        self.definition_stack = []
        self.definition_channels = {}
        self.definition_depths = {}
        self.child_descriptors = {}

    def fail(self, message):
        raise ValueError(message)

    @contextmanager
    def scope(self, nodes, links, loop=None, lifecycle=False):
        c = self.c
        saved = (c.nodes, c.data_links, c.expr_cache, c.value_expr, c.expr_out,
                 self.nodes, self.links, self.loop, self.lifecycle, self.visiting, self.query_path, self.iteration_path)
        self.nodes, self.links = nodes, links
        self.loop, self.lifecycle, self.visiting = loop, lifecycle, set()
        self.query_path = ()
        self.iteration_path = ()
        original = c.value_expr
        # Strip any outer scope wrapper when compiling a nested action definition.
        original = getattr(original, "_ba_original", original)
        c.nodes, c.data_links, c.expr_cache = nodes, {
            (l.to_node, l.to_socket): (l.from_node, l.from_socket) for l in links if l.data}, {}
        original_out = getattr(c.expr_out, "_ba_original", c.expr_out)
        active = set()

        def expr_out(nid, socket):
            key = (nid, socket)
            if nid not in nodes:
                self.fail("a value wire references a missing node")
            if key in active:
                self.fail("bot action value wires contain a cycle")
            active.add(key)
            try:
                return original_out(nid, socket)
            finally:
                active.remove(key)
        expr_out._ba_original = original_out
        c.expr_out = expr_out

        def expression(n, socket):
            if n is None:
                self.fail("a value wire references a missing node")
            if n.kind == "BA_LOOP":
                if n.id != self.loop or socket != "current":
                    self.fail("Current Bot is only valid inside its own Bot Action Loop")
                return "ctx.bot"
            if n.kind == "BA_ENTRY":
                if not self.lifecycle or socket != "bot":
                    self.fail("Action Bot is only valid inside an action lifecycle")
                return "ctx.bot"
            if n.kind == "BA_PARAM":
                if not self.lifecycle:
                    self.fail("Action Parameter is only valid inside an action group")
                if n.settings.get("type") == "action":
                    self.fail("Action inputs can only connect to child Action sockets")
                return "ctx.params[" + quote(n.settings.get("uid", "")) + "]"
            if n.kind == "BA_CHILD":
                if not self.lifecycle or n.id not in self.query_path:
                    self.fail("Step Child Action must run earlier on the same lifecycle path")
                if socket not in {"idle", "running", "success", "failed", "error", "attempt"}:
                    self.fail("Unknown child action output")
                return "HL_Q_" + c.key_of(n.id) + "." + socket
            if n.kind == "BA_STATE_GET":
                if not self.lifecycle:
                    self.fail("Action State is only valid inside an action group")
                key = quote(n.settings.get("name", "state"))
                fallback = c.typed_in(n, "default", n.settings.get("type", "num"))
                return f"(({key} in ctx.state) ? ctx.state[{key}] : {fallback})"
            if n.kind in workbudgets.ACTION:
                if not self.lifecycle:
                    self.fail("Work grants belong inside an action lifecycle")
                if n.id not in self.query_path:
                    self.fail("Request a work grant earlier on this same synchronous path before reading it")
                return workbudgets.output(n, socket, "HL_Q_" + c.key_of(n.id))
            if n.kind in routes.KINDS or n.kind in diagnostics.ALL:
                if n.kind in diagnostics.ACTION and not self.lifecycle:
                    self.fail("Action inspection belongs inside an action lifecycle")
                if n.id not in self.query_path:
                    self.fail("Run this node earlier on the same synchronous path before reading it")
                module = routes if n.kind in routes.KINDS else diagnostics
                return module.output(n, socket, "HL_Q_" + c.key_of(n.id))
            if n.kind in coordination.ALL:
                if n.kind in coordination.CLAIMS and not self.lifecycle:
                    self.fail("Destination claims belong inside an action lifecycle")
                if n.id not in self.query_path:
                    self.fail("Run the coordination node earlier on this same synchronous path before reading it")
                return coordination.output(n, socket, "HL_Q_" + c.key_of(n.id))
            if n.kind in candidates.KINDS or n.kind in listtools.KINDS or n.kind in actionutilities.KINDS:
                if n.kind in actionutilities.KINDS and not self.lifecycle:
                    self.fail("Action utilities belong inside an action lifecycle")
                if n.id not in self.query_path:
                    self.fail(f"'{n.id}': run the node earlier on this same synchronous path before reading it")
                module = candidates if n.kind in candidates.KINDS else (listtools if n.kind in listtools.KINDS else actionutilities)
                return module.output(n, socket, "HL_Q_" + c.key_of(n.id))
            if n.kind in worldqueries.KINDS:
                if n.id not in self.query_path:
                    self.fail(f"'{n.id}': run the world query earlier on this same synchronous path before reading it")
                return worldqueries.output(n, socket, "HL_WQ_" + c.key_of(n.id))
            if n.kind == "FOR_EACH" and n.settings.get("what") == "NAV_AREAS":
                if n.id not in self.iteration_path:
                    self.fail("Nav Area iteration outputs belong inside their Each path")
                return ("HL_WQI_" if socket == "index" else "HL_WQA_") + c.key_of(n.id)
            if n.kind not in PURE:
                self.fail(f"'{n.id}' ({n.kind}) cannot be evaluated inside a bot action; use synchronous value nodes")
            return original(n, socket)
        expression._ba_original = original
        c.value_expr = expression
        try:
            yield
        finally:
            (c.nodes, c.data_links, c.expr_cache, c.value_expr, c.expr_out,
             self.nodes, self.links, self.loop, self.lifecycle, self.visiting, self.query_path, self.iteration_path) = saved

    def source(self, n, socket):
        return self.c.data_links.get((n.id, socket))

    def action_source(self, node, socket):
        source = self.source(node, socket)
        target = self.nodes.get(source[0]) if source else None
        expected = "value" if target is not None and target.kind == "BA_PARAM" else "action"
        if target is None or source[1] != expected:
            self.fail(f"'{node.id}': connect a valid Action output to '{socket}'")
        return target

    def chain(self, nid, socket, path=()):
        out = []
        for link in self.links:
            if link.data or (link.from_node, link.from_socket) != (nid, socket):
                continue
            if link.to_node in path or len(path) >= 64:
                self.fail("bot action event paths must be acyclic and at most 64 nodes long")
            n = self.nodes.get(link.to_node)
            if n is None:
                self.fail("an event wire references a missing node")
            if not self.lifecycle:
                self.owned.add(n.id)
            s, k = n.settings, n.kind
            self.query_path = path
            self.c.expr_cache = {}
            tail = path + (n.id,)
            if k == "IF":
                if link.to_socket != "in":
                    self.fail("If must be connected through its event input")
                cond = self.c.expr_in(n, "condition", False)
                out.append(f"if ({cond}) {{\n{self.chain(n.id, 'true', tail)}\n}} else {{\n{self.chain(n.id, 'false', tail)}\n}}")
                continue
            if link.to_socket != "run":
                self.fail(f"'{n.id}' must be connected through Run")
            if k == "FOR_EACH" and s.get("what") == "NAV_AREAS":
                values = self.c.typed_in(n, "list", "any")
                key = self.c.key_of(n.id)
                previous = self.iteration_path
                self.iteration_path += (n.id,)
                try:
                    each = self.chain(n.id, 'each', tail)
                finally:
                    self.iteration_path = previous
                # Inputs are captured once; these lists come from the capped Nearby query.
                out.append(f"local HL_WQL_{key} = {values};\nif (typeof HL_WQL_{key} == \"array\") {{\n"
                           f"for (local HL_WQI_{key}=0; HL_WQI_{key}<HL_WQL_{key}.len() && HL_WQI_{key}<128; ++HL_WQI_{key}) {{\n"
                           f"local HL_WQA_{key}=HL_WQL_{key}[HL_WQI_{key}];\n{each}\n}}\n}}")
                # The loop item is no longer in scope on Done.
                out.append(self.chain(n.id, "done", tail))
                continue
            if k == "BA_APPLY" and not self.lifecycle:
                if self.source(n, "bot") != (self.loop, "current"):
                    self.fail(f"'{n.id}': Bot must come from this loop's Current Bot output")
                src = self.source(n, "action")
                action = self.nodes.get(src[0]) if src else None
                if action is None or action.kind not in {"BA_ACTION", "BA_CHOOSE", "BA_PRIORITY", "BA_SEQUENCE"}:
                    self.fail(f"'{n.id}': plug an editable Bot Action group into Action")
                if src[1] != "action":
                    self.fail(f"'{n.id}': Action needs the group's Action output")
                # The typed Bot socket excludes props, but its selection can still
                # be incompatible with this action's narrower supported bot family.
                # ALL deliberately remains a mixed selection: supportsFn filters
                # individual bots at runtime, so any intersecting family is useful.
                loop_node = self.nodes[self.loop]
                selector = self.nodes[self.source(loop_node, "bots")[0]]
                selected = selector.settings.get("kind", "SPECIALS")
                definition_data = action.settings.get("definition")
                supported = definition_data.get("supports", "ALL") if isinstance(definition_data, dict) else "ALL"
                if selected in KINDS and supported in KINDS and "ALL" not in (selected, supported) and selected != supported:
                    self.fail(f"'{n.id}': Find Bots selects {selected.title()}, but action '{action.id}' supports "
                              f"only {supported.title()}; choose a compatible action or bot selection")
                definition, params = self.descriptor(action)
                enabled = self.c.expr_in(n, "enabled", True)
                out.append(f"HLBA.Request(ctx, {quote(self.c.key_of(n.id))}, ctx.bot, {definition}, {enabled}, {params});")
            elif k == "BA_CHILD" and self.lifecycle:
                if getattr(self, "lifecycle_event", None) == "stop":
                    self.fail("Cannot step child actions during Stop cleanup")
                definition, parameters = self.descriptor(self.action_source(n, "action"))
                self.child_descriptors.setdefault(self.definition_stack[-1], set()).add(definition)
                reset = self.c.typed_in(n, "reset", "bool")
                enabled = self.c.expr_in(n, "enabled", True)
                out.append(f"local HL_Q_{self.c.key_of(n.id)} = HLBA.ChildStep(ctx, {quote(n.id)}, {definition}, {parameters}, {reset}, {enabled});")
            elif k == "BA_RESULT" and self.lifecycle:
                if getattr(self, "lifecycle_event", None) == "stop":
                    self.fail("Action Result cannot run during Stop cleanup")
                result = s.get("result", "SUCCESS")
                if result not in {"SUCCESS", "FAILED"}:
                    self.fail("Action Result must be Success or Failed")
                out.append(f"ctx.status = {quote(result)}; return;")
                continue
            elif k in workbudgets.ACTION:
                if not self.lifecycle:
                    self.fail("Work grants belong inside an action lifecycle")
                if getattr(self, "lifecycle_event", None) == "stop":
                    self.fail("Cannot request work grants during Stop cleanup")
                out.append(f"local HL_Q_{self.c.key_of(n.id)} = {workbudgets.call(self.c, n)};")
            elif k in routes.KINDS:
                out.append(f"local HL_Q_{self.c.key_of(n.id)} = {routes.call(self.c, n)};")
            elif k in diagnostics.ALL:
                if k in diagnostics.ACTION and not self.lifecycle:
                    self.fail("Action inspection belongs inside an action lifecycle")
                table = "HL_Q_" + self.c.key_of(n.id)
                out.append(f"local {table} = {diagnostics.call(self.c, n, context='ctx')};")
                if k == "DBG_CHECK":
                    out.append(f"if ({table}.passed) {{\n{self.chain(n.id, 'true', tail)}\n}} else {{\n{self.chain(n.id, 'false', tail)}\n}}")
                    continue
            elif k in coordination.ALL:
                if k in coordination.CLAIMS and not self.lifecycle:
                    self.fail("Destination claims belong inside an action lifecycle")
                if k == "BA_CLAIM" and getattr(self, "lifecycle_event", None) == "stop":
                    self.fail("Cannot acquire a destination claim during Stop cleanup")
                out.append(f"local HL_Q_{self.c.key_of(n.id)} = {coordination.call(self.c, n)};")
            elif k in candidates.KINDS:
                if k == "CAND_STEP":
                    if not self.lifecycle:
                        self.fail("Evaluate Candidates in Steps belongs inside an action lifecycle")
                    if getattr(self, "lifecycle_event", None) == "stop":
                        self.fail("Cannot step candidate evaluation during Stop cleanup")
                out.append(f"local HL_Q_{self.c.key_of(n.id)} = {candidates.call(self.c, n, context='ctx' if self.lifecycle else None)};")
            elif k in listtools.KINDS:
                out.append(f"local HL_Q_{self.c.key_of(n.id)} = {listtools.call(self.c, n)};")
            elif k in actionutilities.KINDS and self.lifecycle:
                self.c.helper("HLBA_Runtime", Path(__file__).with_name("bot_actions_runtime.nut").read_text(encoding="utf-8") + HELPERS)
                table = "HL_Q_" + self.c.key_of(n.id)
                out.append(f"local {table} = {actionutilities.call(self.c, n)};")
                if k in {"BA_COOLDOWN", "BA_CHANGED"}:
                    yes, no = ("passed", "blocked") if k == "BA_COOLDOWN" else ("changed", "unchanged")
                    out.append(f"if ({table}.{yes}) {{\n{self.chain(n.id, yes, tail)}\n}} else {{\n{self.chain(n.id, no, tail)}\n}}")
                    continue
            elif k in worldqueries.KINDS:
                out.append(f"local HL_WQ_{self.c.key_of(n.id)} = {worldqueries.call(self.c, n)};")
            elif k == "BA_OVERRIDE" and self.lifecycle:
                kind, key = s.get("type", "float"), s.get("property", "")
                if kind not in {"float", "int", "vector", "flags"} or not key:
                    self.fail(f"'{n.id}': choose a property and supported override type")
                value = self.c.typed_in(n, "value", "vec" if kind == "vector" else "num")
                if kind in {"int", "flags"}:
                    value = f"({value}).tointeger()"
                mask = str(int(s.get("mask", 0))) if kind == "flags" else "null"
                if kind == "flags" and int(s.get("mask", 0)) <= 0:
                    self.fail(f"'{n.id}': flag mask must be positive")
                restore_on_death = "true" if s.get("restore_on_death", False) else "false"
                out.append(f"HLBA.Override(ctx, {quote(key)}, {quote(kind)}, {value}, {mask}, {restore_on_death});")
            elif k == "BA_STATE_SET" and self.lifecycle:
                value = self.c.typed_in(n, "value", s.get("type", "num"))
                out.append(f"ctx.state[{quote(s.get('name', 'state'))}] <- {value};")
            elif k == "BA_COMMAND" and self.lifecycle:
                command = s.get("command", "RESET")
                if command not in {"MOVE", "ATTACK", "RETREAT", "RESET"}:
                    self.fail(f"'{n.id}': unsupported bot command")
                target = self.c.typed_in(n, "target", "thing")
                destination = self.c.typed_in(n, "destination", "vec")
                out.append(f"HLBA.Command(ctx, {quote(command)}, {target}, {destination});")
            else:
                self.fail(f"'{n.id}' ({k}) is not supported in this synchronous bot action path (no delays or ordinary event actions)")
            out.append(self.chain(n.id, "next", tail))
        return "\n".join(out)

    def descriptor(self, node, path=()):
        if node.id in path or len(path) >= 16:
            self.fail("Action composition must be acyclic and at most 16 levels deep")
        if node.kind == "BA_PARAM" and node.settings.get("type") == "action":
            uid = node.settings.get("uid")
            if uid not in self.action_bindings:
                self.fail("Action input has no compile-time binding")
            return self.action_bindings[uid], "ctx.params[" + quote(uid) + "]"
        if node.kind == "BA_ACTION":
            params, bindings = [], {}
            for uid, kind, default in node.settings["definition"].get("inputs", []):
                if kind == "action":
                    child, value = self.descriptor(self.action_source(node, uid), path + (node.id,))
                    bindings[uid] = child
                    params.append(f"[{quote(uid)}] = {value}")
                    continue
                value = (vs.literal(kind, default) if (node.id, uid) not in self.c.data_links and uid not in node.consts
                         else self.c.typed_in(node, uid, kind))
                params.append(f"[{quote(uid)}] = {value}")
            definition = self.definition(node, bindings)
            return definition, "{ " + ", ".join(params) + " }"
        if node.kind not in {"BA_CHOOSE", "BA_PRIORITY", "BA_SEQUENCE"}:
            self.fail("Action sockets require an action group or action composition")
        sockets = ("true_action", "false_action") if node.kind == "BA_CHOOSE" else ("action_1", "action_2", "action_3")
        definitions, parameters = [], []
        for socket in sockets:
            src = self.source(node, socket)
            if src is None:
                definitions.append("null"); parameters.append("{}"); continue
            definition, params = self.descriptor(self.action_source(node, socket), path + (node.id,))
            definitions.append(definition); parameters.append(params)
        depth = 1 + max((self.definition_depths.get(d, 0) for d in definitions), default=0)
        if depth > 16:
            self.fail("Action composition nesting exceeds 16")
        if node.id not in self.definitions:
            name = self.c.fn_name("HL_Compose_" + self.c.key_of(node.id))
            self.c.functions.append(f"::{name} <- HLBA.Compose({quote(name)}, {quote(node.kind)}, [{', '.join(definitions)}]);")
            self.definitions[node.id] = name
        name = self.definitions[node.id]
        self.definition_depths[name] = depth
        self.definition_channels[name] = set().union(*(self.definition_channels.get(d, set()) for d in definitions))
        enabled = [self.c.expr_in(node, "enabled_" + str(i), True) for i in range(1, 4)] if node.kind == "BA_PRIORITY" else []
        choice = self.c.expr_in(node, "condition", False) if node.kind == "BA_CHOOSE" else "false"
        return name, "{children=[" + ", ".join(parameters) + "], enabled=[" + ", ".join(enabled) + "], choice=" + choice + "}"

    def definition(self, node, bindings=None):
        if node.id in self.definitions:
            return self.definitions[node.id]
        if node.id in self.definition_stack or len(self.definition_stack) >= 16:
            self.fail("Nested action definitions must be acyclic and at most 16 levels deep")
        from .logic import LNode, LLink
        d = node.settings.get("definition")
        if not isinstance(d, dict):
            self.fail(f"'{node.id}': missing action group definition")
        nodes = [x if isinstance(x, LNode) else LNode(**x) for x in d.get("nodes", [])]
        links = [x if isinstance(x, LLink) else LLink(**x) for x in d.get("links", [])]
        entries = [n for n in nodes if n.kind == "BA_ENTRY"]
        if len(entries) != 1:
            self.fail(f"'{node.id}': an action group needs exactly one lifecycle entry")
        if len({n.id for n in nodes}) != len(nodes):
            self.fail(f"'{node.id}': action node identifiers must be unique")
        params = d.get("inputs", [])
        if len({p[0] for p in params}) != len(params):
            self.fail(f"'{node.id}': action parameter identifiers must be unique")
        types = {p[0]: p[1] for p in params}
        for p in nodes:
            if p.kind == "BA_PARAM" and (p.settings.get("uid") not in types or
                    types[p.settings["uid"]] != p.settings.get("type", "num")):
                self.fail(f"'{p.id}': action parameter does not match the group's input interface")
        if d.get("supports", "ALL") not in KINDS:
            self.fail(f"'{node.id}': invalid supported bot type")
        name = self.c.fn_name("HL_Action_" + self.c.key_of(node.id))
        # Definition-local IDs are namespaced per caller, preventing nested copies
        # from sharing function definitions or state slots.
        prefix = node.id + "/"
        from dataclasses import replace
        nodes = [replace(n, id=prefix + n.id) for n in nodes]
        links = [LLink(prefix + l.from_node, l.from_socket, prefix + l.to_node, l.to_socket, l.data) for l in links]
        previous_bindings, previous_event = self.action_bindings, getattr(self, "lifecycle_event", None)
        self.action_bindings = bindings or {}
        self.definition_stack.append(node.id)
        try:
            with self.scope({n.id: n for n in nodes}, links, lifecycle=True):
                bodies = {}
                for event in ("start", "update", "stop"):
                    self.lifecycle_event = event
                    bodies[event] = self.chain(prefix + entries[0].id, event)
        finally:
            self.definition_stack.pop()
            self.action_bindings, self.lifecycle_event = previous_bindings, previous_event
        # Property ownership is never optional: even custom groups must not race
        # restoration against another group that changes the same property.
        owned_channels = list(d.get("channels", []))
        owned_channels += ["property:" + str(n.settings.get("property", "")) for n in nodes if n.kind == "BA_OVERRIDE"]
        if any(n.kind == "BA_COMMAND" for n in nodes):
            owned_channels.append("Movement")
        child_definitions = set((bindings or {}).values()) | self.child_descriptors.get(node.id, set())
        depth = 1 + max((self.definition_depths.get(d, 1) for d in child_definitions), default=0)
        if depth > 16:
            self.fail("Action composition nesting exceeds 16")
        self.definition_depths[name] = depth
        inherited = set().union(*(self.definition_channels.get(d, set()) for d in child_definitions))
        if any(n.kind == "BA_COMMAND" for n in nodes) and "Movement" in inherited:
            self.fail("An action cannot issue its own bot commands while owning a child Movement action; put commands in separate child actions")
        if any("property:" + str(n.settings.get("property", "")) in inherited for n in nodes if n.kind == "BA_OVERRIDE"):
            self.fail("An action cannot override a property also owned by a child action; move the override into a separate child action")
        self.definition_channels[name] = set(owned_channels) | inherited
        channels = ", ".join(quote(x) for x in dict.fromkeys(owned_channels))
        code = f"::{name} <- {{ id = {quote(name)}, channels = [{channels}], supportsFn = function(bot) {{ return HLBA.Supports(bot, {quote(d.get('supports', 'ALL'))}); }},\n"
        code += ",\n".join(event + "Fn = function(ctx) {\n" + body + "\n}" for event, body in bodies.items()) + "\n};"
        if child_definitions:
            code += "\nHLBA.InheritChannels(" + name + ", [" + ", ".join(sorted(child_definitions)) + "] );"
        self.c.functions.append(code)
        self.definitions[node.id] = name
        return name

    def loop_code(self, n):
        from .logic import _Take
        c = self.c
        src = self.source(n, "bots")
        query = self.nodes.get(src[0]) if src else None
        if query is None or query.kind != "BA_FIND" or src[1] != "bots":
            self.fail(f"'{n.id}': Bots needs a Find Bots query")
        kind = query.settings.get("kind", "SPECIALS")
        if kind not in KINDS:
            self.fail("Find Bots has an unsupported bot kind")
        if (query.id, "radius") not in self.c.data_links:
            radius_value = float(query.consts.get("radius", 0))
            if not math.isfinite(radius_value) or radius_value < 0:
                self.fail("Find Bots radius must be finite and nonnegative")
        interval = None
        if (n.id, "interval") not in self.c.data_links:
            interval = float(n.consts.get("interval", n.settings.get("interval", 0.1)))
            if not math.isfinite(interval) or interval < 0.05:
                self.fail("Bot Action Loop interval must be finite and at least 0.05 seconds")
        with self.scope(self.nodes, self.links):
            origin = c.typed_in(query, "origin", "vec")
            radius = c.typed_in(query, "radius", "num")
            if interval is None:
                interval = c.typed_in(n, "interval", "num")
        with self.scope(self.nodes, self.links, loop=n.id):
            body = self.chain(n.id, "each")
        slug = c.key_of(n.id)
        start, stop, after = (c.fn_name("HL_BA_" + x + "_" + slug) for x in ("Start", "Stop", "After"))
        c.functions.append(f"::{start} <- function() {{ HLBA.Start({quote(slug)}, {interval}, function() {{ return HLBA.Find({quote(kind)}, {origin}, {radius}); }}, function(ctx) {{\n{body}\n}}, function() {{ {after}(); }}); }}")
        c.functions.append(f"::{stop} <- function() {{ HLBA.Stop({quote(slug)}); }}")
        c.script_fns[n.id] = (after, "@@after@@")
        c.script_sources.add((n.id, "after"))
        for socket, fn in (("start", start), ("stop", stop)):
            c.script_takes[(n.id, socket)] = fn
            c.takes[(n.id, socket)] = [_Take(*c.script_call(fn))]

    def compile(self):
        c = self.c
        for n in list(self.nodes.values()):
            if n.kind != "BA_LOOP":
                continue
            try:
                self.loop_code(n)
            except (ValueError, TypeError, KeyError) as error:
                c.problems.append(f"Bot Action Loop '{n.id}': {error}")
        for n in self.nodes.values():
            if n.kind == "BA_APPLY" and n.id not in self.owned:
                c.problems.append(f"'{n.id}': Apply Bot Action must run inside a Bot Action Loop")
            if n.kind in {"BA_ENTRY", "BA_PARAM", "BA_OVERRIDE", "BA_STATE_GET", "BA_STATE_SET", "BA_COMMAND", "BA_RESULT", "BA_CHILD"} | set(actionutilities.KINDS) | set(coordination.CLAIMS) | set(diagnostics.ACTION) | set(workbudgets.ACTION):
                c.problems.append(f"'{n.id}': this action primitive belongs inside an editable Bot Action group")
        if any(n.kind == "BA_LOOP" for n in self.nodes.values()):
            c.helper("HLBA_Runtime", Path(__file__).with_name("bot_actions_runtime.nut").read_text(encoding="utf-8") + HELPERS)
            if not c.ir.__dict__.get("_bot_actions"):
                c.ir._bot_actions = True
                for event in ("round_end", "mission_lost", "map_transition"):
                    c.ir.logic_script_events.setdefault(event, []).append("    HLBA.StopAll();")
        # Paths owned by loops cannot also be entered from ordinary engine events.
        for l in self.links:
            if not l.data and l.to_node in self.owned and not (
                    l.from_node in self.owned or (self.nodes.get(l.from_node) is not None and
                    self.nodes[l.from_node].kind == "BA_LOOP" and l.from_socket == "each")):
                c.problems.append(f"'{l.to_node}': a bot loop path cannot also be entered from ordinary events")
        return self.owned | {n.id for n in self.nodes.values() if n.kind in BA}


def prepare(compiler, nodes, links):
    if not any(n.kind in BA for n in nodes):
        return set(), set()
    first_problem = len(compiler.problems)
    worker = ActionCompiler(compiler, nodes, links)
    skipped = worker.compile()
    # Only this bounded compiler's diagnostics become fatal. Existing ordinary
    # graph warnings must keep their established severity.
    compiler.problems[first_problem:] = [BotActionProblem(p) for p in compiler.problems[first_problem:]]
    # Loop Start/Stop and After Update still connect to the ordinary event graph.
    consumed_sources = worker.owned | {n.id for n in nodes if n.kind in BA and n.kind != "BA_LOOP"}
    return skipped, consumed_sources
