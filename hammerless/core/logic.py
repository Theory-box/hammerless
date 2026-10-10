"""Logic graphs -> the game's entities, I/O connections and a small map script.

The Blender node editor (blender/logic.py) hands over plain data: nodes (a kind, settings,
maybe a scene object and where it is) and wires (from node/socket to node/socket). Each node
becomes entities; each wire becomes one output row on the entity that fires it ("when X
happens, tell Y to do Z"), exactly what Hammer mappers wire by hand. Nodes that act on a plain
mesh turn it into the entity they need (a button, a mover, a toggleable wall). The few things
entity I/O can't do (game events such as "survivors left the safe room", teleporting the team,
changing Director settings) become functions in one generated map script, called through the
same wires.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import vscript as vs
from .entities import CRESCENDO, default_keyvalues
from .gamefiles import sq_text
from .ir import Brush, Entity, MapIR, Output

DIRECTOR = "director"
LOGIC_SCRIPT = "hl_logic"
TRIGGER_MATERIAL = "tools/toolstrigger"
WHO_FLAGS = {"SURVIVORS": "1", "INFECTED": "3", "EVERYONE": "3"}   # trigger spawnflags: 1 clients, 2 NPCs

# Game Event node: event id -> (label, game event, extra Squirrel condition on `p` = the player, or "")
GAME_EVENTS = {
    "LEFT_SAFE_ROOM": ("Survivors Leave the Safe Room", "player_left_start_area", ""),
    "FINALE_START": ("Finale Starts", "finale_start", ""),
    "SURVIVOR_DIES": ("A Survivor Dies", "player_death", "p && p.IsSurvivor()"),
    "SURVIVOR_DOWN": ("A Survivor Is Incapacitated", "player_incapacitated", "p && p.IsSurvivor()"),
    "SURVIVOR_REVIVED": ("A Survivor Is Revived", "revive_success", ""),
    "SPECIAL_KILLED": ("A Special Infected Is Killed", "player_death", "p && !p.IsSurvivor() && p.GetZombieType() != 8"),
    "COMMON_KILLED": ("A Common Infected Is Killed", "infected_death", ""),
    "TANK_SPAWNS": ("A Tank Appears", "tank_spawn", ""),
    "TANK_KILLED": ("A Tank Is Killed", "tank_killed", ""),
    "WITCH_KILLED": ("A Witch Is Killed", "witch_killed", ""),
    "WITCH_STARTLED": ("A Witch Is Startled", "witch_harasser_set", ""),
}
ZOMBIE_TYPES = ("tank", "witch", "hunter", "smoker", "boomer", "charger", "jockey", "spitter", "common")
SPAWN_TYPES = {"smoker": 1, "boomer": 2, "hunter": 3, "spitter": 4, "jockey": 5, "charger": 6, "witch": 7, "tank": 8}
# infected counting for Spawn Zombie's "Only If Fewer Than": every Tank, Witch and special that appears
COUNT_CODE = {
    "tank_spawn": "    HL_Count.tank++;\n",
    "witch_spawn": "    HL_Count.witch++;\n",
    "player_spawn": ("    if (p && !p.IsSurvivor()) { local t = p.GetZombieType(); "
                     "if (t in HL_CountNames) HL_Count[HL_CountNames[t]]++; }\n"),
}
# Director Settings node fields: (setting, DirectorOptions key)
DIRECTOR_FIELDS = (("common_limit", "CommonLimit"), ("mob_min", "MobMinSize"), ("mob_max", "MobMaxSize"),
                   ("mob_interval_min", "MobSpawnMinTime"), ("mob_interval_max", "MobSpawnMaxTime"),
                   ("max_specials", "MaxSpecials"), ("special_interval", "SpecialRespawnInterval"),
                   ("tank_limit", "TankLimit"), ("witch_limit", "WitchLimit"))


@dataclass
class LNode:
    id: str                          # unique within the graph (the Blender node name)
    kind: str
    settings: dict = field(default_factory=dict)
    obj: str = ""                    # scene object the node acts on
    brushes: list[Brush] = field(default_factory=list)   # VOLUME: the object's shape
    params: dict = field(default_factory=dict)            # input socket -> value typed on the node
    pos: tuple | None = None         # where the object is (Hammer units), for spawn/sound/teleport
    consts: dict = field(default_factory=dict)            # value input socket -> value typed on the node


@dataclass
class LLink:
    from_node: str
    from_socket: str
    to_node: str
    to_socket: str
    data: bool = False               # a value wire (number / true-false), not an event


@dataclass
class _Fire:        # an output socket: which entity fires which output (and any delay)
    entity: Entity
    output: str
    delay: float = 0.0
    times: int = -1


@dataclass
class _Take:        # an input socket: which named entity receives which input
    target: str
    input: str
    param: str = ""


# what a scene object became, in the words of the node that made it (for messages)
BECAME = {"func_movelinear": "Mover (Move Over Time)", "func_button": "Button", "func_button_timed": "Button",
          "func_brush": "solid brush (Collision or Show / Hide)", "func_illusionary": "walk-through brush (Collision)"}


RELAY = "2"            # logic_relay: allow fast retrigger (else it ignores Trigger until its last delay ran out)
RELAY_ONCE = "3"       # ...and only trigger once


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "node"


def _base(source: str) -> str:
    return re.sub(r" \(part \d+\)$", "", source)


BLOCK_ACTIONS = ("FOR_EACH", "SET_VAR", "SCRIPT_CODE", "DIRECTOR_OPTION", "HUD_TEXT", "HUD_HIDE", "OVERRIDE_ANSWER")
BLOCK_VALUES = ("GET_VAR", "MAKE_TABLE", "GET_FIELD", "FORMAT_TEXT", "MAKE_VECTOR", "BREAK_VECTOR", "VECTOR_MATH",
                "CHECK", "SCRIPT_VALUE")
VALUE_KINDS = ("num", "int", "bool", "text", "vec", "thing", "any")


def parse_fields(text: str) -> list[tuple[str, str]]:
    """Make Table's fields: 'type: num, pos: vec' -> [(name, kind)] (kind defaults to any)."""
    out = []
    for part in (text or "").split(","):
        if not part.strip():
            continue
        name, _, kind = part.partition(":")
        name = re.sub(r"[^A-Za-z0-9_]", "", name.strip())
        kind = kind.strip().lower() or "any"
        if name:
            out.append((name, kind if kind in VALUE_KINDS else "any"))
    return out


# shared script helpers (written once into the map script when a node needs them)
HELPERS = {
    "HL_Depth": ("::HL_Depth <- 0;   // script nodes in a loop: how deep the calls are\n"
                 "::HL_MAX_DEPTH <- 32;"),
    "HL_Players": (
        "::HL_Players <- function(which) {   // 0 everyone, 1 survivors, 2 infected players (alive)\n"
        "    local out = [], p = null;\n"
        "    while (p = Entities.FindByClassname(p, \"player\")) {\n"
        "        if (which == 1 && !p.IsSurvivor()) continue;\n"
        "        if (which == 2 && (p.IsSurvivor() || p.IsDead())) continue;\n"
        "        out.append(p);\n    }\n    return out;\n}"),
    "HL_Find": (
        "::HL_Find <- function(what, byClass) {\n"
        "    local out = [], e = null;\n"
        "    if (byClass) while (e = Entities.FindByClassname(e, what)) out.append(e);\n"
        "    else while (e = Entities.FindByName(e, what)) out.append(e);\n    return out;\n}"),
    "HL_Items": (
        "::HL_Items <- function(list) {   // a list or a table's values\n"
        "    local out = [];\n    if (list == null) return out;\n"
        "    if (typeof list == \"array\") return list;\n"
        "    if (typeof list == \"table\") foreach (k, v in list) out.append(v);\n    return out;\n}"),
    "HL_Vars": (
        "::HL_Var <- {};\n::HL_PVar <- {};\n::HL_VarKeep <- {};   // the variables kept across map changes\n"
        "if (\"RestoreTable\" in getroottable()) {\n"
        "    local t = {}; RestoreTable(\"hl_kept\", t);\n"
        "    foreach (k, x in t) { ::HL_Var[k] <- x; ::HL_VarKeep[k] <- true; }\n}\n"
        "::HL_VarGet <- function(name, d) { return (name in ::HL_Var && ::HL_Var[name] != null) ? ::HL_Var[name] : d; }\n"
        "::HL_VarSet <- function(name, v, keep) {\n    ::HL_Var[name] <- v;\n"
        "    if (!keep) return;\n"
        "    ::HL_VarKeep[name] <- true;     // only these, and only plain values: entities don't outlive the map\n"
        "    local t = {};\n"
        "    foreach (k, _ in ::HL_VarKeep) {\n"
        "        local x = (k in ::HL_Var) ? ::HL_Var[k] : null, ty = typeof x;\n"
        "        if (ty == \"integer\" || ty == \"float\" || ty == \"bool\" || ty == \"string\") t[k] <- x;\n"
        "    }\n"
        "    SaveTable(\"hl_kept\", t);\n}\n"
        "::HL_PKey <- function(p) { return (p == null) ? \"\" : (\"GetPlayerUserId\" in p ? p.GetPlayerUserId() : p.GetEntityIndex()).tostring(); }\n"
        "::HL_PVarGet <- function(p, name, d) {\n    local k = ::HL_PKey(p);\n"
        "    return (k in ::HL_PVar && name in ::HL_PVar[k] && ::HL_PVar[k][name] != null) ? ::HL_PVar[k][name] : d;\n}\n"
        "::HL_PVarSet <- function(p, name, v) {\n    local k = ::HL_PKey(p);\n"
        "    if (!(k in ::HL_PVar)) ::HL_PVar[k] <- {};\n    ::HL_PVar[k][name] <- v;\n}"),
    "HL_Field": (
        "::HL_Field <- function(t, key) {   // a table's field, or a list's item (0, 1, ...)\n"
        "    if (t == null) return null;\n"
        "    if (typeof t == \"array\") { local i = key.tointeger(); return (i >= 0 && i < t.len()) ? t[i] : null; }\n"
        "    try { return (key in t) ? t[key] : null; } catch (e) { return null; }\n}\n"
        "::HL_Count_ <- function(t) { return (t == null) ? 0.0 : t.len().tofloat(); }"),
    "HL_Str": (
        "::HL_Str <- function(v) {   // any value as readable text\n"
        "    if (v == null) return \"\";\n"
        "    if (typeof v == \"float\") return (v == v.tointeger()) ? v.tointeger().tostring() : v.tostring();\n"
        "    if (typeof v == \"instance\") {\n"
        "        try { if (v.IsPlayer()) return v.GetPlayerName(); } catch (e) {}\n"
        "        try { local n = v.GetName(); return n != \"\" ? n : v.GetClassname(); } catch (e) {}\n"
        "        try { return v.x + \" \" + v.y + \" \" + v.z; } catch (e) {}\n    }\n"
        "    return v.tostring();\n}"),
    "HL_DirOpts": (
        "::HL_DirOpts <- {};   // Director settings changed by the graph (kept on top of every Director script)\n"
        "::HL_TopOptions <- function() {   // the table the Director reads first\n"
        "    local m = ::DirectorScript.MapScript;\n"
        "    if (m.ChallengeScript.rawin(\"DirectorOptions\")) return m.ChallengeScript.DirectorOptions;\n"
        "    if (m.LocalScript.rawin(\"DirectorOptions\")) return m.LocalScript.DirectorOptions;\n"
        "    if (!m.rawin(\"DirectorOptions\")) m.DirectorOptions <- {};\n"
        "    return m.DirectorOptions;\n}\n"
        "::HL_ApplyOpts <- function() { local t = ::HL_TopOptions(); foreach (k, v in ::HL_DirOpts) t[k] <- v; }\n"
        "::HL_SetOpt <- function(k, v) { ::HL_DirOpts[k] <- v; ::HL_ApplyOpts(); }\n"
        "::HL_ResetOpt <- function(k) {\n    if (k in ::HL_DirOpts) delete ::HL_DirOpts[k];\n"
        "    local t = ::HL_TopOptions(); if (t.rawin(k)) delete t[k];\n}"),
    "HL_Hud": (
        "::HL_Hud <- { Fields = {} };   // the custom HUD (scripted mode)\n"
        "::HL_HudShow <- function(slot, func, flags) {\n"
        "    ::HL_Hud.Fields[\"s\" + slot] <- { slot = slot, name = \"s\" + slot, datafunc = func, dataval = func(), flags = flags };\n"
        "    HUDSetLayout(::HL_Hud);\n}\n"
        "::HL_HudHide <- function(slot) {\n"
        "    if ((\"s\" + slot) in ::HL_Hud.Fields) delete ::HL_Hud.Fields[\"s\" + slot];\n    HUDSetLayout(::HL_Hud);\n}"),
    "HL_IsSet": (
        "::HL_IsSet <- function(v) {   // there, and still in the game if it's an entity\n"
        "    if (v == null) return false;\n"
        "    if (typeof v == \"instance\") { try { return v.IsValid(); } catch (e) { return false; } }\n"
        "    return true;\n}"),
}


class _Compiler:
    def __init__(self, ir: MapIR, problems: list[str], graph: str = "", log: bool = False):
        self.log = log
        self.log_names: list[str] = []
        self.ir = ir
        self.problems = problems
        self.graph = self._graph_slug(graph) if graph else ""
        self.graph_tag = f"_{self.graph}" if self.graph else ""
        self.graph_title = graph            # (the debug log says which graph a wire is in, when there are several)
        self.fires: dict[tuple[str, str], _Fire] = {}
        self.takes: dict[tuple[str, str], list[_Take]] = {}
        self.filters: dict[str, str] = {}
        self.functions: list[str] = []                  # Squirrel functions for the map script
        self.events: dict[str, list[tuple[str, str]]] = {}   # game event -> [(condition, relay)]
        self.progress: list[tuple[str, float, float]] = []  # Path Progress: (relay, from, to)
        self.nodes: dict[str, LNode] = {}
        self.data_links: dict[tuple[str, str], tuple[str, str]] = {}
        self.expr_cache: dict[tuple[str, str], str] = {}
        self.defined: set[str] = set()
        self.random_fns: dict[str, str] = {}               # Random Value node -> its function
        self.whens: list[tuple[str, str, str, bool]] = []
        # object -> (class it became, node that did it): shared by every graph (two graphs can't make one
        # object two different things either)
        self.converted: dict[str, tuple[str, str]] = ir.__dict__.setdefault("_logic_converted", {})
        # script nodes (game functions and events): their event outputs run Squirrel directly
        self.script_fns: dict[str, tuple[str, str]] = {}     # node -> (function, body before Then)
        self.script_takes: dict[tuple[str, str], str] = {}  # (node, input) -> function to call
        self.script_conts: dict[tuple[str, str], list[str]] = {}   # (node, output) -> code it runs
        self.script_events: dict[str, list[str]] = {}       # game event -> event nodes
        self.script_sources: set[tuple[str, str]] = set()   # (node, output) whose wires run as script
        self.hook_nodes: dict[str, list[str]] = {}           # override hook -> Override nodes

    # -- names and entities
    def fn_name(self, base: str) -> str:
        """A script function name no other node (in any graph) uses."""
        taken = self.ir.__dict__.setdefault("_logic_fn_names", set())
        name, i = base, 2
        while name in taken:
            name, i = f"{base}_{i}", i + 1
        taken.add(name)
        return name

    def _graph_slug(self, graph: str) -> str:
        taken = self.ir.__dict__.setdefault("_logic_graph_slugs", set())
        base = _slug(graph)
        name, i = base, 2
        while name in taken:
            name, i = f"{base}_{i}", i + 1
        taken.add(name)
        return name

    def key_of(self, nid: str) -> str:
        """The node's key in the shared tables (::HL_R, ::HL_L, ::HL_I): its own, even when two node names slug
        the same ("Code-1", "Code_1")."""
        keys = self.ir.__dict__.setdefault("_logic_keys", {})
        mine = (self.graph, nid)
        if mine not in keys:
            base = _slug(f"{self.graph}_{nid}" if self.graph else nid)
            used = set(keys.values())
            name, i = base, 2
            while name in used:
                name, i = f"{base}_{i}", i + 1
            keys[mine] = name
        return keys[mine]

    def unique(self, base: str) -> str:
        # (Source names don't care about case: "HL_Delay" is "hl_delay")
        taken = {(e.keyvalues.get("targetname") or "").lower() for e in self.ir.entities}
        name, i = base, 2
        while name.lower() in taken:
            name, i = f"{base}_{i}", i + 1
        return name

    def name_of(self, e: Entity, hint: str) -> str:
        """The entity's targetname, giving it one if it has none."""
        if not e.keyvalues.get("targetname"):
            e.keyvalues = {**e.keyvalues, "targetname": self.unique(f"hl_{_slug(hint)}")}
        return e.keyvalues["targetname"]

    def add(self, classname: str, name: str, kv: dict, brushes=None, source: str = "", origin=None) -> Entity:
        e = Entity(classname, None if brushes else tuple(origin or (0.0, 0.0, 0.0)), (0, 0, 0),
                   {"targetname": self.unique(name), **kv}, brushes or [], source or name)
        self.ir.entities.append(e)
        return e

    def entity_of(self, obj: str) -> Entity | None:
        return next((e for e in self.ir.entities if _base(e.source) == obj), None)

    def make_entity(self, nid: str, obj: str, classname: str, kv: dict) -> Entity | None:
        """The scene object as an entity of this class: an existing entity is reused (its class
        changed if needed), a plain mesh's brushes are taken out of the world."""
        if not obj:
            self.problems.append(f"Logic node '{nid}': pick an object")
            return None
        if obj in self.converted and self.converted[obj][0] != classname:
            # two nodes want this object as different things: the first one wins, never silently
            first_class, first_node = self.converted[obj]
            self.problems.append(f"Logic node '{nid}': '{obj}' is already a {BECAME.get(first_class, first_class)} (node '{first_node}'), "
                                 f"so it can't also be a {BECAME.get(classname, classname)}; this node is skipped")
            return None
        self.converted.setdefault(obj, (classname, nid))
        e = self.entity_of(obj)
        if e is not None and e.brushes:
            e.classname = classname
            e.keyvalues = {**kv, **{k: v for k, v in e.keyvalues.items() if k == "targetname"}}
            self.name_of(e, obj)     # a func_detail (or an empty catalog name) has none to keep
            return e
        brushes = [b for b in self.ir.brushes if _base(b.source) == obj]
        if not brushes:
            self.problems.append(f"Logic node '{nid}': '{obj}' has no solid shape to turn into a {classname} "
                                 "(pick a mesh that is part of the map)")
            return None
        self.ir.brushes = [b for b in self.ir.brushes if _base(b.source) != obj]
        e = Entity(classname, None, (0, 0, 0), {"targetname": self.unique(f"hl_{_slug(obj)}"), **kv}, brushes, obj)
        self.ir.entities.append(e)
        return e

    def director(self) -> Entity:
        for e in self.ir.entities:
            if e.classname == "info_director":
                if not e.keyvalues.get("targetname") and self.unique("director") == "director":
                    e.keyvalues = {**e.keyvalues, "targetname": "director"}   # what hordes and crescendos target
                self.name_of(e, DIRECTOR)
                return e
        return self.add("info_director", DIRECTOR, {k: v for k, v in default_keyvalues("info_director").items()
                                                     if k != "targetname"})

    def team_filter(self, team: str) -> str:
        if team not in self.filters:
            e = self.add("filter_activator_team", f"hl_filter_team{team}", {"filterteam": team, "Negated": "0"})
            self.filters[team] = e.keyvalues["targetname"]
        return self.filters[team]

    # -- value wires: each value socket becomes a Squirrel expression, worked out live in the map script
    def expr_in(self, n: LNode, sock: str, default=0.0) -> str:
        src = self.data_links.get((n.id, sock))
        if src is not None:
            return self.expr_out(*src)
        v = n.consts.get(sock, default)
        if isinstance(v, bool):
            return "true" if v else "false"
        return f"{float(v):g}" if "." in f"{float(v):g}" or "e" in f"{float(v):g}" else f"{float(v):g}.0"

    def expr_out(self, nid: str, sock: str) -> str:
        key = (nid, sock)
        if key in self.expr_cache:
            return self.expr_cache[key]
        self.expr_cache[key] = "0.0"          # a loop of value wires reads 0 rather than hanging
        n = self.nodes.get(nid)
        e = self.value_expr(n, sock) if n is not None else "0.0"
        self.expr_cache[key] = e
        return e

    def typed_in(self, n: LNode, sock: str, kind: str) -> str:
        """A typed input (text, vector, entity...): its wire, or the value typed / object picked on it."""
        src = self.data_links.get((n.id, sock))
        if src is not None:
            return self.expr_out(*src)
        if kind in (vs.NUM, vs.BOOL):
            return self.expr_in(n, sock, False if kind == vs.BOOL else 0.0)
        v = n.consts.get(sock)
        if kind == vs.THING:
            if not v:
                return "null"
            e = self.entity_of(v)
            if e is None:
                self.problems.append(f"Logic node '{n.id}': '{v}' isn't an entity in the map (set its Role, or "
                                     "wire in what the node should act on)")
                return "null"
            return f'Entities.FindByName(null, "{self.name_of(e, v)}")'
        return vs.literal(kind, v)

    def script_args(self, n: LNode, f: dict) -> tuple:
        socks = vs.param_sockets(f)
        target = self.typed_in(n, "target", vs.THING) if f.get("on") else None
        args = [self.typed_in(n, ident, kind) for ident, _label, kind in socks if ident != "target"]
        return target, args

    def value_expr(self, n: LNode, sock: str) -> str:
        s, k = n.settings, n.kind
        slug = self.key_of(n.id)
        if k == "SCRIPT_CALL":
            f = vs.function(s.get("fn", ""))
            if f is None or sock != "result":
                return "null"
            if vs.is_pure(f):
                target, args = self.script_args(n, f)
                if not f.get("on"):
                    return vs.call_expr(f, target, args)
                # nothing to ask (no player yet, a deleted entity): null rather than a script error
                return (f"(function(hl_t) {{ return (typeof hl_t == \"instance\" && hl_t.IsValid()) ? "
                        f"{vs.call_expr(f, 'hl_t', args)} : null; }})({target})")
            return f'(("{slug}" in ::HL_R) ? ::HL_R["{slug}"] : null)'
        if k == "SCRIPT_EVENT":
            e = vs.event(s.get("event", ""))
            fld = next((x for x in (e or {}).get("fields", []) if x["name"] == sock), None)
            return vs.field_expr(sock, vs.field_kind(fld)) if fld else "null"
        if k in BLOCK_VALUES or k in ("FOR_EACH", "SCRIPT_CODE"):
            return self.block_value(n, sock, slug)
        if k == "DIRECTOR_MOOD":
            self.director_query()
            return '(("HL_Anger" in getroottable()) ? ::HL_Anger : 0.0)'
        if k == "OVERRIDE":
            from .director_options import HOOKS_BY_NAME
            hook = HOOKS_BY_NAME.get(s.get("hook", ""))
            kind = dict(hook[2]).get(sock) if hook else None
            return vs.field_expr(sock, kind) if kind else "null"
        if k == "VALUE":
            return self.expr_in(n, "value") if sock == "value" else "0.0"
        if k == "PROGRESS":
            self.ir.logic_path = True
            return {"furthest": "HL_PathFurthest()", "average": "HL_PathAverage()",
                    "last": "HL_PathLast()"}.get(sock, "HL_PathFurthest()")
        if k == "RANDOM_VALUE":
            lo, hi = self.expr_in(n, "min", 0.0), self.expr_in(n, "max", 1.0)
            if s.get("each_time"):
                return f"RandomFloat({lo}, {hi})"
            fn = self.random_fns.get(n.id)
            if fn is None:
                fn = self.random_fns[n.id] = self.fn_name(f"HL_Random_{slug}")
                say = (f'printl("HAMMERLESS_VALUE {sq_text(n.id)} rolled " + {fn}_v);\n        ' if self.log else "")
                self.functions.append(f"{fn}_v <- null;\nfunction {fn}() {{\n    if ({fn}_v == null) {{\n"
                                      f"        {fn}_v = RandomFloat({lo}, {hi});\n        {say}}}\n"
                                      f"    return {fn}_v;\n}}")
            return f"{fn}()"
        if k == "MATH":
            a, b = self.expr_in(n, "a", 0.0), self.expr_in(n, "b", 0.0)
            op = s.get("op", "ADD")
            return {"ADD": f"({a} + {b})", "SUBTRACT": f"({a} - {b})", "MULTIPLY": f"({a} * {b})",
                    "DIVIDE": f"(({b}) != 0 ? ({a}) / ({b}) : 0.0)", "MINIMUM": f"(({a}) < ({b}) ? ({a}) : ({b}))",
                    "MAXIMUM": f"(({a}) > ({b}) ? ({a}) : ({b}))", "POWER": f"pow({a}, {b})",
                    "ABSOLUTE": f"fabs({a})", "ROUND": f"floor(({a}) + 0.5)", "FLOOR": f"floor({a})",
                    "CEIL": f"ceil({a})", "MODULO": f"(({b}) != 0 ? ({a}) % ({b}) : 0.0)"}.get(op, f"({a} + {b})")
        if k == "COMPARE":
            a, b = self.expr_in(n, "a", 0.0), self.expr_in(n, "b", 0.0)
            op = s.get("op", "GREATER_EQUAL")
            return {"GREATER_EQUAL": f"({a} >= {b})", "GREATER": f"({a} > {b})", "LESS": f"({a} < {b})",
                    "LESS_EQUAL": f"({a} <= {b})", "EQUAL": f"(fabs(({a}) - ({b})) < 0.0001)",
                    "NOT_EQUAL": f"(fabs(({a}) - ({b})) >= 0.0001)"}.get(op, f"({a} >= {b})")
        if k == "BOOL_MATH":
            a, b = self.expr_in(n, "a", False), self.expr_in(n, "b", False)
            op = s.get("op", "AND")
            return {"AND": f"({a} && {b})", "OR": f"({a} || {b})", "NOT": f"(!{a})",
                    "XOR": f"(({a}) != ({b}))"}.get(op, f"({a} && {b})")
        if k == "INFECTED_COUNT":
            what = s.get("what", "tank")
            if s.get("mode", "APPEARED") == "APPEARED":
                self.ir.logic_counts = True
                return f"HL_Count.{what}.tofloat()" if what in SPAWN_TYPES else "0.0"
            fn = "HL_Alive"
            if fn not in self.defined:
                self.defined.add(fn)
                self.functions.append(
                    "function HL_Alive(type) {\n    local n = 0, e = null;\n"
                    "    if (type == 7) { while (e = Entities.FindByClassname(e, \"witch\")) n++; return n.tofloat(); }\n"
                    "    while (e = Entities.FindByClassname(e, \"player\"))\n"
                    "        if (!e.IsSurvivor() && e.GetZombieType() == type && e.GetHealth() > 0) n++;\n"
                    "    return n.tofloat();\n}")
            return f"HL_Alive({SPAWN_TYPES.get(what, 8)})"
        return "0.0"

    def script_call(self, fn: str) -> tuple[str, str, str]:
        return (LOGIC_SCRIPT, "RunScriptCode", f"{fn}()")

    # -- nodes
    def node(self, n: LNode):
        s, nid = n.settings, n.id
        slug = self.key_of(nid)

        def fire(sock, ent, output, delay=0.0, times=-1):
            self.fires[(nid, sock)] = _Fire(ent, output, delay, times)

        def take(sock, target, inp, param=""):
            self.takes[(nid, sock)] = [_Take(target, inp, n.params.get(sock, param))]

        def also(sock, target, inp, param=""):     # one input socket, a second receiver
            self.takes.setdefault((nid, sock), []).append(_Take(target, inp, param))

        k = n.kind
        if k == "SCRIPT_CALL":
            f = vs.function(s.get("fn", ""))
            if f is None:
                self.problems.append(f"Logic node '{nid}': pick a game function")
                return
            if vs.is_pure(f):
                return                      # a value: turned into an expression where it's used
            fn = self.fn_name(f"HL_S_{slug}")
            target, args = self.script_args(n, f)
            guard = ""
            if f.get("on"):            # what it acts on, worked out once
                guard = (f"    local t = {target};\n    if (typeof t != \"instance\" || !t.IsValid()) {{ printl(\"HAMMERLESS_SCRIPT "
                         f"'{sq_text(nid)}': nothing to act on\"); return; }}\n")
                target = "t"
            call = vs.call_expr(f, target, args)
            body = guard + (f'    ::HL_R["{slug}"] <- {call};\n' if f["returns"] != "void" else f"    {call};\n")
            self.script_action(nid, fn, body + "@@then@@", ["then"], take)
            return
        if k in BLOCK_ACTIONS:
            self.block_action(n, slug, take)
            return
        if k in BLOCK_VALUES:
            return                          # values: expressions where they're used
        if k == "SCRIPT_EVENT":
            e = vs.event(s.get("event", ""))
            if e is None:
                self.problems.append(f"Logic node '{nid}': pick a game event")
                return
            self.script_events.setdefault(e["name"], []).append(nid)
            return
        if k == "DIRECTOR_MOOD":
            query = self.director_query()
            fire("mob60", query, "On60SecondsToMob")
            fire("mob20", query, "On20SecondsToMob")
            return
        if k == "OVERRIDE":
            from .director_options import HOOKS_BY_NAME
            hook = HOOKS_BY_NAME.get(s.get("hook", ""))
            if hook is None:
                self.problems.append(f"Logic node '{nid}': pick what to override")
                return
            self.ir.scripted_mode = True
            self.script_sources.add((nid, "asked"))
            self.hook_nodes.setdefault(hook[0], []).append(nid)
            return
        if k in ("OBJECT", "DIRECTOR"):
            e = self.director() if k == "DIRECTOR" else self.entity_of(n.obj)
            if e is None:
                self.problems.append(f"Logic node '{nid}': " + (
                    "pick an object" if not n.obj else
                    f"'{n.obj}' is a plain mesh. Use a Button, Move Over Time or Show/Hide node on it, "
                    "or set its Role to an entity"))
                return
            name = self.name_of(e, n.obj or DIRECTOR)
            for sock in s.get("outputs", []):
                fire(sock, e, sock)
            for sock in s.get("inputs", []):
                take(sock, name, sock)
        elif k == "VOLUME":
            if not n.brushes:
                self.problems.append(f"Logic node '{nid}': pick a mesh object for the volume")
                return
            for b in n.brushes:
                for f in b.faces:
                    f.material = TRIGGER_MATERIAL
            who = s.get("who", "SURVIVORS")
            kv = {"spawnflags": WHO_FLAGS.get(who, "1"), "wait": "0", "entireteam": "2",
                  "StartDisabled": "1" if s.get("start_disabled") else "0"}
            if who in ("SURVIVORS", "INFECTED"):
                kv["filtername"] = self.team_filter("2" if who == "SURVIVORS" else "3")
            e = self.add("trigger_multiple", f"hl_{slug}", kv, n.brushes, n.obj)
            times = 1 if s.get("once") else -1
            for sock, output in (("enter", "OnStartTouch"), ("leave", "OnEndTouch"), ("first", "OnStartTouchAll"),
                                 ("empty", "OnEndTouchAll"), ("all_inside", "OnEntireTeamStartTouch")):
                fire(sock, e, output, times=times)
            take("enable", e.keyvalues["targetname"], "Enable")
            take("disable", e.keyvalues["targetname"], "Disable")
        elif k == "BUTTON":
            hold = float(s.get("hold", 0) or 0)
            if hold > 0:
                e = self.make_entity(nid, n.obj, "func_button_timed", {
                    "use_time": str(max(1, int(round(hold)))), "use_string": s.get("text", "Using..."),
                    "auto_disable": "1" if s.get("once", True) else "0", "spawnflags": "0"})
                press = "OnTimeUp"
            else:
                wait = "-1" if s.get("once", True) else f"{float(s.get('reset', 1)):g}"
                e = self.make_entity(nid, n.obj, "func_button", {"spawnflags": "1025", "wait": wait})
                press = "OnPressed"
            if e is None:
                return
            name = e.keyvalues["targetname"]
            fire("pressed", e, press)
            fire("started", e, "OnPressed")
            take("lock", name, "Lock")
            take("unlock", name, "Unlock")
        elif k == "MOVE":
            e = self.make_entity(nid, n.obj, "func_movelinear", {
                "direction": s.get("direction", "down"), "movedistance": s.get("distance", "auto") or "auto",
                "move_time": f"{float(s.get('seconds', 4)):g}", "startposition": "0"})
            if e is None:
                return
            name = e.keyvalues["targetname"]
            take("go", name, "Open")
            take("back", name, "Close")
            fire("arrived", e, "OnFullyOpen")
            fire("returned", e, "OnFullyClosed")
            if s.get("block_nav", False):      # off by default: a blocked gate hides what's behind it
                                               # from the Director's wandering population
                # the nav mesh runs through a closed gate (nav generation ignores moving brushes):
                # block the nav areas it covers while closed, like Valve's gates and barricades
                pts = [v for b in e.brushes for f in b.faces for v in f.verts]
                lo = [min(p[i] for p in pts) for i in range(3)]
                hi = [max(p[i] for p in pts) for i in range(3)]
                from . import geometry as g
                box = g.box_brush((lo[0] - 8, lo[1] - 8, lo[2] - 16), (hi[0] + 8, hi[1] + 8, hi[2]), TRIGGER_MATERIAL,
                                  f"{n.obj} nav blocker")
                blocker = self.add("func_nav_blocker", f"{name}_navblock", {"teamToBlock": "-1", "affectsFlow": "0"},
                                   [box], f"{n.obj} nav blocker")
                bname = blocker.keyvalues["targetname"]
                self.add("logic_auto", f"{name}_navblock_start", {"spawnflags": "1"}).outputs.append(
                    Output("OnMapSpawn", bname, "BlockNav", "", 1.0, 1))
                also("go", bname, "UnblockNav")
                also("back", bname, "BlockNav")
        elif k == "COLLISION":
            players, nav = bool(s.get("players", True)), bool(s.get("nav", False))
            if players and nav:
                return                      # ordinary world geometry already does both
            if n.obj in self.converted:     # e.g. a Mover: already solid, and the nav already runs through it
                first_class, first_node = self.converted[n.obj]
                self.problems.append(f"Logic node '{nid}': '{n.obj}' is already a {BECAME.get(first_class, first_class)} (node "
                                     f"'{first_node}'), which sets its own collision; this Collision node is skipped")
                return
            if players:
                # solid for players and zombies, but nav generation ignores brush entities: the nav
                # mesh runs through it (a fence zombies climb, a barricade they bash)
                self.make_entity(nid, n.obj, "func_brush", {"Solidity": "2", "StartDisabled": "0", "spawnflags": "2"})
                return
            e = self.make_entity(nid, n.obj, "func_illusionary", {})     # seen, but walked through
            if e is not None and nav:        # ...and zombies and bots path around it
                self.nav_blocker(e, n.obj, always=True)
        elif k == "SHOW_HIDE":
            e = self.make_entity(nid, n.obj, "func_brush", {
                "Solidity": "0", "StartDisabled": "1" if s.get("start_hidden") else "0", "spawnflags": "2"})
            if e is None:
                return
            name = e.keyvalues["targetname"]
            take("show", name, "Enable")
            take("hide", name, "Disable")
            take("remove", name, "Kill")
        elif k == "PATH_PROGRESS":
            lo, hi = float(s.get("from", 0.5)), float(s.get("to", 0.5))
            e = self.add("logic_relay", f"hl_{slug}", {"spawnflags": "1"})
            self.progress.append((e.keyvalues["targetname"], min(lo, hi), max(lo, hi)))
            fire("reached", e, "OnTrigger")
        elif k in ("VALUE", "PROGRESS", "RANDOM_VALUE", "MATH", "COMPARE", "BOOL_MATH", "INFECTED_COUNT"):
            pass                            # value nodes turn into expressions where they're used
        elif k == "WHEN":
            cond = self.expr_in(n, "condition", False)
            fn = self.fn_name(f"HL_When_{slug}")
            self.functions.append(f"function {fn}() {{ return {cond}; }}")
            on_true = self.add("logic_relay", f"hl_{slug}_true", {"spawnflags": RELAY})
            on_false = self.add("logic_relay", f"hl_{slug}_false", {"spawnflags": RELAY})
            self.whens.append((fn, on_true.keyvalues["targetname"], on_false.keyvalues["targetname"],
                               bool(s.get("once", True))))
            fire("true", on_true, "OnTrigger")
            fire("false", on_false, "OnTrigger")
        elif k == "IF":
            # a script function: an event's chain runs straight through it (its values still current);
            # True / False run the next script nodes directly, or fire entity inputs
            cond = self.expr_in(n, "condition", False)
            fn = self.fn_name(f"HL_S_{slug}")
            self.script_action(nid, fn, f"    if ({cond}) {{\n@@true@@\n    }} else {{\n@@false@@\n    }}",
                               ["true", "false"], take, inp="in")
        elif k == "MAP_START":
            e = self.add("logic_auto", f"hl_{slug}", {"spawnflags": "1"})
            # after the map-wide Director settings (they load at 1 s), or settings a graph applies at
            # map start would be replaced by them
            fire("start", e, "OnMapSpawn", 1.1 if self.ir.settings.director_enabled else 0.0)
        elif k == "GAME_EVENT":
            ev = s.get("event", "LEFT_SAFE_ROOM")
            label, game_event, cond = GAME_EVENTS.get(ev, GAME_EVENTS["LEFT_SAFE_ROOM"])
            e = self.add("logic_relay", f"hl_{slug}", {"spawnflags": RELAY_ONCE if s.get("once") else RELAY})
            self.events.setdefault(game_event, []).append((cond, e.keyvalues["targetname"]))
            fire("happened", e, "OnTrigger")
        elif k == "TIMER":
            lo, hi = float(s.get("seconds", 10)), float(s.get("max_seconds", 0) or 0)
            kv = {"StartDisabled": "0" if s.get("running", True) else "1", "spawnflags": "0"}
            if hi > lo:
                kv.update({"UseRandomTime": "1", "LowerRandomBound": f"{lo:g}", "UpperRandomBound": f"{hi:g}"})
            else:
                kv.update({"UseRandomTime": "0", "RefireTime": f"{max(lo, 0.1):g}"})
            e = self.add("logic_timer", f"hl_{slug}", kv)
            name = e.keyvalues["targetname"]
            take("start", name, "Enable")
            take("stop", name, "Disable")
            take("now", name, "FireTimer")
            fire("tick", e, "OnTimer")
        elif k in ("DELAY", "ONCE"):
            e = self.add("logic_relay", f"hl_{slug}", {"spawnflags": RELAY_ONCE if k == "ONCE" else RELAY})
            take("in", e.keyvalues["targetname"], "Trigger")
            take("cancel", e.keyvalues["targetname"], "CancelPending")
            fire("out", e, "OnTrigger", float(s.get("seconds", 0)) if k == "DELAY" else 0.0)
        elif k == "SEQUENCE":
            step = float(s.get("seconds", 0) or 0)
            e = self.add("logic_relay", f"hl_{slug}", {"spawnflags": RELAY})
            take("in", e.keyvalues["targetname"], "Trigger")
            for i in range(1, 7):     # a hundredth of a second apart keeps the order
                fire(f"then{i}", e, "OnTrigger", step * (i - 1) + 0.01 * (i - 1))
        elif k == "GATE":
            e = self.add("logic_relay", f"hl_{slug}", {"spawnflags": RELAY, "StartDisabled": "0" if s.get("open", True)
                                                        else "1"})
            name = e.keyvalues["targetname"]
            take("in", name, "Trigger")
            take("open", name, "Enable")
            take("close", name, "Disable")
            fire("out", e, "OnTrigger")
        elif k == "BRANCH":
            e = self.add("logic_branch", f"hl_{slug}", {"InitialValue": "1" if s.get("initial") else "0"})
            name = e.keyvalues["targetname"]
            take("test", name, "Test")
            take("set_true", name, "SetValue", "1")
            take("set_false", name, "SetValue", "0")
            take("toggle", name, "Toggle")
            take("toggle_test", name, "ToggleTest")
            fire("true", e, "OnTrue")
            fire("false", e, "OnFalse")
        elif k == "COUNTER":
            target = max(1, int(s.get("count", 3)))
            e = self.add("math_counter", f"hl_{slug}", {"min": "0", "max": str(target), "startvalue": "0"})
            take("add", e.keyvalues["targetname"], "Add", "1")
            take("reset", e.keyvalues["targetname"], "SetValueNoFire", "0")
            fire("reached", e, "OnHitMax")
        elif k == "RANDOM":
            e = self.add("logic_case", f"hl_{slug}", {})
            take("pick", e.keyvalues["targetname"], "PickRandom")
            for i in range(1, 5):
                fire(f"case{i}", e, f"OnCase{i:02d}")
        elif k == "HORDE":
            # measured: OnPanicEventFinished fires after a ForcePanicEvent horde (any horde): only this
            # node's "On Finished" listens, from its Start until the next finish
            d = self.director()
            dname = self.name_of(d, DIRECTOR)
            done = self.add("logic_relay", f"hl_{slug}_done", {"spawnflags": RELAY, "StartDisabled": "1"})
            dn = done.keyvalues["targetname"]
            d.outputs = list(d.outputs) + [Output("OnPanicEventFinished", dn, "Trigger", "", 0.0, -1)]
            done.outputs = list(done.outputs) + [Output("OnTrigger", dn, "Disable", "", 0.0, -1)]
            self.takes[(nid, "start")] = [_Take(dn, "Enable"), _Take(dname, "ForcePanicEvent")]
            fire("finished", done, "OnTrigger")
        elif k == "CRESCENDO":
            name = _slug(s.get("name") or slug)
            if self.graph and not name.startswith(self.graph):
                name = f"{self.graph}_{name}"       # two graphs' crescendos can share a node name
            self.ir.entities.append(Entity(CRESCENDO, (0, 0, 0), (0, 0, 0),
                                           {"name": name, "stages": s.get("stages", "PANIC 1")}, [], nid))
            from .gamefiles import parse_stages
            stages = max(1, len(parse_stages(s.get("stages", "PANIC 1"))[0]))
            d = self.director()
            dname = self.name_of(d, DIRECTOR)
            count = self.add("math_counter", f"hl_{slug}_stages", {"min": "0", "max": str(stages), "startvalue": "0",
                                                                   "StartDisabled": "1"})
            cn = count.keyvalues["targetname"]
            d.outputs = list(d.outputs) + [Output("OnCustomPanicStageFinished", cn, "Add", "1", 0.0, -1)]
            # (no Director script is loaded again afterwards: measured, a BeginScript as a crescendo ends
            # starts another panic event, and when that ends the Director drops its settings altogether)
            count.outputs = list(count.outputs) + [Output("OnHitMax", cn, "Disable", "", 0.0, -1),
                                                   Output("OnHitMax", cn, "SetValueNoFire", "0", 0.0, -1)]
            self.takes[(nid, "start")] = [_Take(cn, "Enable"), _Take(dname, "ScriptedPanicEvent", name)]
            fire("finished", count, "OnHitMax")
        elif k == "SPAWN":
            what = s.get("what", "tank")
            limit = int(s.get("fewer_than", 0) or 0)
            if n.pos is None and what == "common":
                self.problems.append(f"Logic node '{nid}': common infected need a Where (an object or empty)")
                return
            spawner = None
            if n.pos is not None:
                spawner = self.add("commentary_zombie_spawner", f"hl_{slug}", {}, origin=n.pos)
                fire("killed", spawner, "OnSpawnedZombieDeath")
            if spawner is not None and not limit:
                take("spawn", spawner.keyvalues["targetname"], "SpawnZombie", what)
            else:
                # the game picks the spot (ZSpawn without a position, like its own spawns) and/or
                # only if fewer than `limit` of this infected have appeared so far
                fn = self.fn_name(f"HL_Spawn_{slug}")
                check = f"if (HL_Count.{what} >= {limit}) return;\n    " if limit and what in SPAWN_TYPES else ""
                if limit and what in SPAWN_TYPES:
                    self.ir.logic_counts = True
                if spawner is not None:
                    act = f"EntFire(\"{spawner.keyvalues['targetname']}\", \"SpawnZombie\", \"{what}\");"
                else:       # no spot right now (e.g. just after a teleport): keep trying for 30 s
                    self.ir.logic_retry = True
                    act = (f"HL_TrySpawn({SPAWN_TYPES.get(what, 8)}, \"{what}\", "
                           f"{limit if what in SPAWN_TYPES else 0});")
                self.functions.append(f"function {fn}() {{\n    {check}{act}\n}}")
                take("spawn", *self.script_call(fn))
        elif k == "SOUND":
            everywhere = n.pos is None or s.get("everywhere", True)
            e = self.add("ambient_generic", f"hl_{slug}", {
                "message": s.get("sound", "ambient/alarms/klaxon1.wav"), "health": str(int(s.get("volume", 10))),
                "radius": str(int(s.get("radius", 1250))), "pitch": "100", "pitchstart": "100",
                "spawnflags": str(16 | 32 | (1 if everywhere else 0))}, origin=n.pos)
            take("play", e.keyvalues["targetname"], "PlaySound")
            take("stop", e.keyvalues["targetname"], "StopSound")
        elif k == "TELEPORT":
            if n.pos is None:
                self.problems.append(f"Logic node '{nid}': pick where to teleport to (an object or empty)")
                return
            fn = self.fn_name(f"HL_Teleport_{slug}")
            x, y, z = n.pos
            self.functions.append(
                f"function {fn}() {{\n    local p = null, i = 0;\n"
                f"    while (p = Entities.FindByClassname(p, \"player\")) {{\n"
                f"        if (!p.IsSurvivor()) continue;\n"
                f"        p.SetOrigin(Vector({x:.1f} + (i % 2) * 40, {y:.1f} + (i / 2) * 40, {z + 8:.1f}));\n"
                f"        p.SetVelocity(Vector(0, 0, 0));\n        i++;\n    }}\n}}")
            take("go", *self.script_call(fn))
        elif k == "MESSAGE":
            e = self.hint(slug, s.get("text", "Objective"), float(s.get("seconds", 6)), s.get("color", "255 255 255"),
                          s.get("icon", "icon_tip"))
            take("show", e.keyvalues["targetname"], "ShowHint")
            take("hide", e.keyvalues["targetname"], "EndHint")
        elif k == "OBJECTIVE":
            hint = self.hint(slug, s.get("text", "Objective"), 0, s.get("color", "255 255 200"), "icon_info")
            done_kv = {"spawnflags": "1"}
            done = self.add("logic_relay", f"hl_{slug}_done", done_kv)
            start = self.add("logic_relay", f"hl_{slug}_start", {"spawnflags": "1"})
            start.outputs.append(Output("OnTrigger", hint.keyvalues["targetname"], "ShowHint"))
            done.outputs.append(Output("OnTrigger", hint.keyvalues["targetname"], "EndHint"))
            if s.get("done_text"):
                ok = self.hint(slug + "_ok", s["done_text"], 4, "150 255 150", "icon_tip")
                done.outputs.append(Output("OnTrigger", ok.keyvalues["targetname"], "ShowHint", "", 0.1))
            take("start", start.keyvalues["targetname"], "Trigger")
            take("complete", done.keyvalues["targetname"], "Trigger")
            fire("started", start, "OnTrigger")
            fire("completed", done, "OnTrigger")
        elif k == "DIRECTOR_SETTINGS":
            from .gamefiles import crescendo_key, director_input_script, director_option_lines
            # never the map-wide Director script's name (which a node called 'Director' would take), nor a
            # crescendo's: its script file has the same form, and one would replace the other
            key = slug if slug != "director" else "director_settings"
            crescendos = {crescendo_key(e.keyvalues.get("name", "crescendo")) for e in self.ir.entities
                          if e.classname == CRESCENDO}
            while (key in crescendos or f"scripts/vscripts/{director_input_script(self.ir.settings.name, key)}.nut"
                   in self.ir.extra_scripts):
                key += "_settings"
            name = director_input_script(self.ir.settings.name, key)
            base = {line.split("=")[0].strip(): line for line in director_option_lines(self.ir)}
            for key, opt in DIRECTOR_FIELDS:
                v = s.get(key, -1)
                if v is not None and int(v) >= 0:
                    base[opt] = f"    {opt} = {int(v)}"
            for what in SPAWN_TYPES:        # Director spawns this type: Off = limit 0, On = back to the map's
                v = s.get(f"spawn_{what}", "SAME")
                key = f"{what.title()}Limit"
                if v == "OFF":
                    base[key] = f"    {key} = 0"
                elif v == "ON" and base.get(key, "").strip().endswith("= 0"):
                    base.pop(key, None)
            for key, opt, on in (("no_mobs", "NoMobSpawns", "true"), ("no_wanderers", "WanderingZombieDensityModifier", "0")):
                if s.get(key) == "ON":
                    base[opt] = f"    {opt} = {on}"
                elif s.get(key) == "OFF":
                    base.pop(opt, None)
            self.ir.extra_scripts[f"scripts/vscripts/{name}.nut"] = (
                f"// Hammerless Director Settings node '{nid}'\nDirectorOptions <-\n{{\n"
                + "\n".join(base.values()) + f"\n}}\nprintl(\"HAMMERLESS_DIRECTOR settings '{sq_text(nid)}' active\");\n")
            d = self.director()
            dname = self.name_of(d, DIRECTOR)
            take("apply", dname, "BeginScript", name)
            take("reset", dname, "EndScript")      # (back to the map-wide settings: they're the map's own)
        else:
            self.problems.append(f"Logic node '{nid}': unknown kind {k}")

    def director_query(self) -> Entity:
        """The Director's anger (0-1) for scripts: a logic_director_query asked twice a second; its
        OutAnger value (0-15) goes through a logic_case, whose matching case stores it (one per map)."""
        found = next((e for e in self.ir.entities if e.keyvalues.get("targetname") == "hl_director_query"), None)
        if found is not None:
            return found
        query = self.add("logic_director_query", "hl_director_query", {"minAngerRange": "0", "maxAngerRange": "15",
                                                                       "noise": "0"})
        case = self.add("logic_case", "hl_director_anger", {f"Case{i + 1:02d}": str(i) for i in range(16)})
        timer = self.add("logic_timer", "hl_director_ask", {"RefireTime": "0.5", "StartDisabled": "0",
                                                            "UseRandomTime": "0", "spawnflags": "0"})
        timer.outputs.append(Output("OnTimer", "hl_director_query", "HowAngry", "", 0.0, -1))
        query.outputs.append(Output("OutAnger", "hl_director_anger", "InValue", "", 0.0, -1))
        for i in range(16):
            case.outputs.append(Output(f"OnCase{i + 1:02d}", LOGIC_SCRIPT, "RunScriptCode",
                                       f"::HL_Anger <- {i / 15:.3f}", 0.0, -1))
        return query

    def nav_blocker(self, e: Entity, obj: str, always: bool = False) -> Entity:
        """func_nav_blocker over an entity's bounds; always = blocking from map start for good."""
        from . import geometry as g
        pts = [v for b in e.brushes for f in b.faces for v in f.verts]
        lo = [min(p[i] for p in pts) for i in range(3)]
        hi = [max(p[i] for p in pts) for i in range(3)]
        box = g.box_brush((lo[0] - 8, lo[1] - 8, lo[2] - 16), (hi[0] + 8, hi[1] + 8, hi[2]), TRIGGER_MATERIAL,
                          f"{obj} nav blocker")
        name = e.keyvalues.get("targetname") or _slug(obj)
        blocker = self.add("func_nav_blocker", f"{name}_navblock", {"teamToBlock": "-1", "affectsFlow": "0"},
                           [box], f"{obj} nav blocker")
        if always:
            self.add("logic_auto", f"{name}_navblock_start", {"spawnflags": "1"}).outputs.append(
                Output("OnMapSpawn", blocker.keyvalues["targetname"], "BlockNav", "", 1.0, 1))
        return blocker

    def hint(self, slug: str, text: str, seconds: float, color: str, icon: str) -> Entity:
        return self.add("env_instructor_hint", f"hl_{slug}", {
            "hint_caption": text, "hint_timeout": str(max(1, round(seconds)) if seconds > 0 else 0),
            "hint_color": color, "hint_static": "1",
            "hint_icon_onscreen": icon, "hint_forcecaption": "1", "hint_range": "0", "hint_auto_start": "0",
            "hint_instance_type": "2"})

    def link(self, l: LLink):
        src = (l.from_node, l.from_socket)
        if self._script_source(*src):
            # a script node's output: run the next node's function, or fire its entity input
            cont = self.script_conts.setdefault(src, [])
            fn = self.script_takes.get((l.to_node, l.to_socket))
            if fn is not None:
                cont.append(f"    {fn}();")
            else:
                for t in self.takes.get((l.to_node, l.to_socket), []):
                    cont.append(f'    EntFire("{t.target}", "{t.input}", "{sq_text(t.param or "")}");')
            if self.log:
                self.log_names.append(f"{l.from_node}.{l.from_socket} -> {l.to_node}.{l.to_socket}")
                cont.append(f"    HL_Log{self.graph_tag}({len(self.log_names) - 1});")
            return
        f = self.fires.get((l.from_node, l.from_socket))
        takes = self.takes.get((l.to_node, l.to_socket))
        if f is None or not takes:
            return            # the node itself already reported what's wrong
        for t in takes:
            if "," in (t.param or ""):
                self.problems.append(f"Logic node '{l.to_node}': the value '{t.param}' has a comma, which the game "
                                     "can't pass in an output (it splits the output's fields): leave it out")
                continue
            f.entity.outputs.append(Output(f.output, t.target, t.input, t.param, f.delay, f.times))
        if self.log:        # Debug Log: print each wire as it fires
            self.log_names.append(f"{l.from_node}.{l.from_socket} -> {l.to_node}.{l.to_socket}")
            f.entity.outputs.append(Output(f.output, LOGIC_SCRIPT, "RunScriptCode",
                                           f"HL_Log{self.graph_tag}({len(self.log_names) - 1})", f.delay, f.times))

    def _script_source(self, nid: str, sock: str) -> bool:
        n = self.nodes.get(nid)
        return (nid, sock) in self.script_sources or (n is not None and n.kind == "SCRIPT_EVENT" and sock == "happened")

    def script_action(self, nid: str, fn: str, body: str, outputs: list[str], take, inp: str = "run") -> None:
        """A node that runs as a script function: its event input calls it, and @@output@@ in the body is
        where each event output's wires go."""
        self.script_fns[nid] = (fn, body)
        self.script_takes[(nid, inp)] = fn
        self.script_sources |= {(nid, o) for o in outputs}
        take(inp, *self.script_call(fn))

    def helper(self, name: str, code: str) -> None:
        """A shared script function, written once."""
        if name not in self.defined and name not in self.ir.__dict__.setdefault("_logic_helpers", set()):
            self.defined.add(name)
            self.ir._logic_helpers.add(name)
            self.functions.insert(0, code)

    def block_action(self, n: LNode, slug: str, take) -> None:
        """Building blocks that do something (For Each, Set Variable, Script)."""
        s, k, nid = n.settings, n.kind, n.id
        fn = self.fn_name(f"HL_S_{slug}")
        if k == "FOR_EACH":
            self.helper("HL_Players", HELPERS["HL_Players"])
            self.helper("HL_Find", HELPERS["HL_Find"])
            what = s.get("what", "SURVIVORS")
            match = self.typed_in(n, "match", vs.TEXT)
            gather = {"SURVIVORS": "HL_Players(1)", "SPECIALS": "HL_Players(2)", "PLAYERS": "HL_Players(0)",
                      "COMMONS": 'HL_Find("infected", true)', "CLASS": f"HL_Find({match}, true)",
                      "NAME": f"HL_Find({match}, false)"}.get(what)
            if gather is None:                      # a list or table wired in
                gather = f"HL_Items({self.typed_in(n, 'list', vs.ANY)})"
                self.helper("HL_Items", HELPERS["HL_Items"])
            body = (f"    local i = 0;\n    foreach (v in {gather}) {{\n"
                    f'        ::HL_L["{slug}"] <- v; ::HL_I["{slug}"] <- i;\n@@each@@\n        i++;\n    }}\n@@done@@')
            self.script_action(nid, fn, body, ["each", "done"], take)
        elif k == "SET_VAR":
            self.helper("HL_Vars", HELPERS["HL_Vars"])
            name = vs.literal(vs.TEXT, s.get("name") or "value")
            value = self.typed_in(n, "value", s.get("kind", vs.ANY))
            if s.get("op") == "ADD":
                old = (f"HL_PVarGet({self.typed_in(n, 'player', vs.THING)}, {name}, 0.0)" if s.get("scope") == "PLAYER"
                       else f"HL_VarGet({name}, 0.0)")
                value = f"({old} + ({value}))"
            if s.get("scope") == "PLAYER":
                line = f"    HL_PVarSet({self.typed_in(n, 'player', vs.THING)}, {name}, {value});\n"
            else:
                line = f"    HL_VarSet({name}, {value}, {'true' if s.get('keep') else 'false'});\n"
            self.script_action(nid, fn, line + "@@then@@", ["then"], take)
        elif k == "DIRECTOR_OPTION":
            from .director_options import BY_KEY
            self.helper("HL_DirOpts", HELPERS["HL_DirOpts"])
            self.ir.logic_diropts = True
            key = s.get("key", "CommonLimit")
            opt = BY_KEY.get(key)
            if opt is None:
                self.problems.append(f"Logic node '{nid}': '{key}' isn't a Director setting")
                return
            if s.get("op") == "RESET":
                line = f'    ::HL_ResetOpt("{key}");\n'
            else:
                kind = opt[1]
                v = self.typed_in(n, "value", vs.NUM if kind in ("int", "num") else kind)
                if kind == "int":
                    v = f"({v}).tointeger()"
                line = f'    ::HL_SetOpt("{key}", {v});\n'
            self.script_action(nid, fn, line + "@@then@@", ["then"], take)
        elif k in ("HUD_TEXT", "HUD_HIDE"):
            self.helper("HL_Hud", HELPERS["HL_Hud"])
            self.ir.scripted_mode = True
            slot = int(s.get("slot", 2))
            if k == "HUD_HIDE":
                line = f"    ::HL_HudHide({slot});\n"
            else:
                from .director_options import HUD_ALIGN, HUD_FLAG_BLINK, HUD_FLAG_NOBG, HUD_TEAM
                flags = HUD_ALIGN.get(s.get("align", "CENTER"), 512) | HUD_TEAM.get(s.get("team", "ALL"), 0)
                flags |= (HUD_FLAG_NOBG if s.get("no_background") else 0) | (HUD_FLAG_BLINK if s.get("blink") else 0)
                text = self.typed_in(n, "text", vs.TEXT)
                self.helper("HL_Str", HELPERS["HL_Str"])
                line = (f"    ::HL_HudShow({slot}, (function() {{ return HL_Str({text}); }}).bindenv(::HL_Scope), "
                        f"{flags});\n")
                if s.get("place"):
                    x, y, w, h = (float(s.get(c, d)) for c, d in (("x", 0.25), ("y", 0.1), ("w", 0.5), ("h", 0.08)))
                    line += f"    HUDPlace({slot}, {x:g}, {y:g}, {w:g}, {h:g});\n"
            self.script_action(nid, fn, line + "@@then@@", ["then"], take)
        elif k == "SCRIPT_CODE":
            args = "".join(f"    local {x} = {self.typed_in(n, x, vs.ANY)};\n" for x in "abcd")
            code = "\n".join("    " + line for line in (s.get("code") or "").splitlines())
            body = (f"{args}    local result = null;\n{code}\n"
                    f'    ::HL_R["{slug}"] <- result;\n@@then@@')
            self.script_action(nid, fn, body, ["then"], take)
        elif k == "OVERRIDE_ANSWER":
            # what an Override's Asked chain answers (a wire can't loop back into the Override itself)
            from .director_options import HOOKS_BY_NAME
            hook = HOOKS_BY_NAME.get(s.get("hook", ""))
            if hook is None or not (hook[3] or hook[0] == "AllowTakeDamage"):
                self.problems.append(f"Logic node '{nid}': pick an override that takes an answer")
                return
            line = '    if (!("HL_Ans" in getroottable())) ::HL_Ans <- {};\n'      # (reached outside a question)
            line += f"    ::HL_Ans.answer <- {self.typed_in(n, 'answer', hook[3])};\n" if hook[3] else ""
            if hook[0] == "AllowTakeDamage" and (nid, "damage") in self.data_links:
                line += f"    ::HL_Ans.damage <- {self.typed_in(n, 'damage', vs.NUM)};\n"
            self.script_action(nid, fn, line + "@@then@@", ["then"], take)

    def block_value(self, n: LNode, sock: str, slug: str) -> str:
        """Building blocks that work something out."""
        s, k = n.settings, n.kind
        if k == "FOR_EACH":
            if sock == "index":
                return f'(("{slug}" in ::HL_I) ? ::HL_I["{slug}"].tofloat() : 0.0)'
            return f'(("{slug}" in ::HL_L) ? ::HL_L["{slug}"] : null)'
        if k == "SCRIPT_CODE":                # what its code left in result, the last time it ran
            return f'(("{slug}" in ::HL_R) ? ::HL_R["{slug}"] : null)'
        if k == "GET_VAR":
            self.helper("HL_Vars", HELPERS["HL_Vars"])
            kind = s.get("kind", vs.NUM)
            default = {vs.NUM: "0.0", vs.BOOL: "false", vs.TEXT: '""'}.get(kind, "null")
            name = vs.literal(vs.TEXT, s.get("name") or "value")
            if s.get("scope") == "PLAYER":
                return f"HL_PVarGet({self.typed_in(n, 'player', vs.THING)}, {name}, {default})"
            return f"HL_VarGet({name}, {default})"
        if k == "MAKE_TABLE":
            rows = [(name, kind) for name, kind in parse_fields(s.get("fields", ""))]
            def field(name, kind):
                if kind == "int":                  # a whole number (the game wants one for e.g. ZSpawn's type)
                    return f'({self.typed_in(n, "f_" + name, vs.NUM)}).tointeger()'
                return self.typed_in(n, "f_" + name, kind)
            body = ", ".join(f'["{name}"] = {field(name, kind)}' for name, kind in rows)
            return f"{{ {body} }}"
        if k == "GET_FIELD":
            self.helper("HL_Field", HELPERS["HL_Field"])
            table = self.typed_in(n, "table", vs.ANY)
            if sock == "count":
                return f"HL_Count_({table})"
            return f"HL_Field({table}, {self.typed_in(n, 'key', vs.TEXT)})"
        if k == "FORMAT_TEXT":
            self.helper("HL_Str", HELPERS["HL_Str"])
            import re as _re
            parts = []
            for piece in _re.split(r"(\{[abcd]\})", s.get("template") or ""):
                if _re.fullmatch(r"\{[abcd]\}", piece):
                    parts.append(f"HL_Str({self.typed_in(n, piece[1], vs.ANY)})")
                elif piece:
                    parts.append(vs.literal(vs.TEXT, piece))
            return "(" + (" + ".join(parts) if parts else '""') + ")"
        if k == "MAKE_VECTOR":
            return (f"Vector({self.typed_in(n, 'x', vs.NUM)}, {self.typed_in(n, 'y', vs.NUM)}, "
                    f"{self.typed_in(n, 'z', vs.NUM)})")
        if k == "BREAK_VECTOR":
            v = self.typed_in(n, "vector", vs.VEC)
            return f"(({v}).{sock if sock in ('x', 'y', 'z') else 'x'}).tofloat()"
        if k == "VECTOR_MATH":
            a, b = self.typed_in(n, "a", vs.VEC), self.typed_in(n, "b", vs.VEC)
            f = self.typed_in(n, "scale", vs.NUM)
            op = s.get("op", "ADD")
            if sock == "value":
                return {"DISTANCE": f"(({a}) - ({b})).Length()", "LENGTH": f"({a}).Length()",
                        "DOT": f"({a}).Dot({b})"}.get(op, "0.0")
            return {"ADD": f"(({a}) + ({b}))", "SUBTRACT": f"(({a}) - ({b}))", "SCALE": f"(({a}) * ({f}))",
                    "NORMALIZE": f"(function(v) {{ local l = v.Length(); return l > 0 ? v * (1.0 / l) : v; }})({a})",
                    "CROSS": f"({a}).Cross({b})"}.get(op, a)
        if k == "CHECK":
            self.helper("HL_IsSet", HELPERS["HL_IsSet"])
            a, b = self.typed_in(n, "a", vs.ANY), self.typed_in(n, "b", vs.ANY)
            op = s.get("op", "IS_SET")
            return {"IS_SET": f"HL_IsSet({a})", "NOT_SET": f"!HL_IsSet({a})", "EQUAL": f"(({a}) == ({b}))",
                    "NOT_EQUAL": f"(({a}) != ({b}))"}.get(op, f"HL_IsSet({a})")
        if k == "SCRIPT_VALUE":
            args = ", ".join(self.typed_in(n, x, vs.ANY) for x in "abcd")
            expr = (s.get("expr") or "null").strip().rstrip(";")
            return f"(function(a, b, c, d) {{ return {expr}; }})({args})"
        return "null"

    def render_script_nodes(self):
        """Script nodes' functions, now that their wires (what each event output runs) are known."""
        import re as _re
        # script nodes wired in a loop (If A -> If B -> If A) call each other directly, as every script wire does
        # (in order, with the event and For Each item they were started with); a loop that doesn't end stops
        # after HL_MAX_DEPTH calls with a message, instead of running Squirrel out of stack
        node_of = {fn: nid for nid, (fn, _b) in self.script_fns.items()}
        node_of.update({fn: nid for (nid, _inp), fn in self.script_takes.items()})     # (its other inputs too)
        calls = {nid: {node_of[c] for (n, _o), lines in self.script_conts.items() if n == nid for line in lines
                       for c in _re.findall(r"^\s*(\w+)\(\);$", line) if c in node_of} for nid in self.script_fns}

        def reaches(a, b):
            seen, todo = set(), [a]
            while todo:
                for x in calls.get(todo.pop(), ()):
                    if x == b:
                        return True
                    if x not in seen:
                        seen.add(x)
                        todo.append(x)
            return False
        for nid, (fn, body) in self.script_fns.items():
            body = _re.sub(r"@@(\w+)@@", lambda m: "\n".join(self.script_conts.get((nid, m.group(1)), [])), body)
            if reaches(nid, nid):
                self.helper("HL_Depth", HELPERS["HL_Depth"])
                where = sq_text(nid)
                # (bound to the logic script's scope, where its helpers are: calling it through .call() would be
                # a native call a level, and the game allows only a few of those nested)
                self.functions.append(f"::{fn}_body <- (function() {{\n{body}\n}}).bindenv(::HL_Scope)")
                self.functions.append(
                    f"::{fn} <- function() {{\n"
                    f"    if (::HL_Depth >= ::HL_MAX_DEPTH) {{ printl(\"HAMMERLESS_SCRIPT '{where}': a loop that doesn't "
                    f"end, stopped\"); return; }}\n"
                    f"    ::HL_Depth++;\n"
                    f"    try {{ ::{fn}_body(); }} catch (e) {{ ::HL_Depth--; throw e; }}\n"
                    f"    ::HL_Depth--;\n}}")
            else:
                self.functions.append(f"::{fn} <- function() {{\n{body}\n}}")   # root: callable from anywhere
        for hook, nids in self.hook_nodes.items():
            from .director_options import HOOKS_BY_NAME
            answer_kind = HOOKS_BY_NAME[hook][3]
            for nid in nids:
                n = self.nodes[nid]
                code = "\n".join(self.script_conts.get((nid, "asked"), []))
                answer = None
                typed = n.consts.get("answer")
                from .director_options import NO_BY_DEFAULT
                # typed values that change anything (the game's own answer needs no code)
                changes = (typed is (hook in NO_BY_DEFAULT)) if answer_kind == "bool" else bool(typed)
                if answer_kind and ((nid, "answer") in self.data_links or changes):
                    answer = self.typed_in(n, "answer", answer_kind)
                damage = self.typed_in(n, "damage", vs.NUM) if (nid, "damage") in self.data_links else None
                self.ir.logic_hooks.setdefault(hook, []).append((code, answer, damage))
        for ev, nids in self.script_events.items():
            code = "\n".join(line for nid in nids for line in self.script_conts.get((nid, "happened"), []))
            if code:
                self.ir.logic_script_events.setdefault(ev, []).append(code)

    def finish_script(self):
        """(Re)write the one map script shared by all graphs: their functions, and one handler per
        game event registered on the script's own scope (the pattern that works in L4D2)."""
        if self.log_names:
            where = f"[{self.graph_title}] " if self.graph_title else ""
            names = ", ".join('"' + (where + n).replace('"', "'").replace("\\", "/") + '"' for n in self.log_names)
            self.functions.append(f"HL_LogNames{self.graph_tag} <- [{names}];\n"
                                  f"function HL_Log{self.graph_tag}(i) {{ printl(\"HAMMERLESS_EVENT \" + "
                                  f"HL_LogNames{self.graph_tag}[i]); }}")
        self.ir.logic_functions += self.functions
        for ev, targets in self.events.items():
            self.ir.logic_events.setdefault(ev, []).extend(targets)
        self.ir.logic_progress += self.progress
        self.ir.logic_whens += self.whens
        ir = self.ir
        if not (ir.logic_functions or ir.logic_events or ir.logic_progress or ir.logic_counts or ir.logic_whens
                or ir.logic_script_events or ir.logic_hooks):
            return
        thinks = bool(ir.logic_progress or ir.logic_retry or ir.logic_whens or ir.logic_diropts)
        parts = [f"// Hammerless logic graphs for {ir.settings.name}\n",
                 "::HL_Ctx <- {};   // the event being handled, its fields\n::HL_R <- {};     // what action nodes returned\n"
                 "::HL_L <- {};     // For Each: the current item\n::HL_I <- {};     // and its number\n"
                 "::HL_Scope <- this;   // this script's scope: Override questions are answered in it\n"
                 f'::HL_Map <- "{ir.settings.name}";   // and only on this map (globals outlive the map)\n']
        if ir.logic_counts:
            parts.append("HL_Count <- { tank = 0, witch = 0, smoker = 0, boomer = 0, hunter = 0, spitter = 0, "
                         "jockey = 0, charger = 0 };\n"
                         "HL_CountNames <- { [1] = \"smoker\", [2] = \"boomer\", [3] = \"hunter\", "
                         "[4] = \"spitter\", [5] = \"jockey\", [6] = \"charger\" };\n")
        if ir.logic_retry:
            check = "(limit > 0 && HL_Count[what] >= limit)" if ir.logic_counts else "false"
            # The game's spawn call obeys the Director's limits (one Tank at a time unless a script
            # says otherwise; the Director Spawns switches set a type's limit to 0): lift them just for
            # our spawn, on the table the Director reads first (measured: a second Tank only spawns
            # with TankLimit there), then put them back
            parts.append("HL_LimitKeys <- { [1] = \"SmokerLimit\", [2] = \"BoomerLimit\", [3] = \"HunterLimit\", "
                         "[4] = \"SpitterLimit\", [5] = \"JockeyLimit\", [6] = \"ChargerLimit\", [7] = \"WitchLimit\", "
                         "[8] = \"TankLimit\" };\n"
                         "function HL_DirectorOptions() {   // the table the Director reads first\n"
                         "    try {\n"
                         "        local m = ::DirectorScript.MapScript;\n"
                         "        if (m.ChallengeScript.rawin(\"DirectorOptions\")) return m.ChallengeScript.DirectorOptions;\n"
                         "        if (m.LocalScript.rawin(\"DirectorOptions\")) return m.LocalScript.DirectorOptions;\n"
                         "        if (!m.rawin(\"DirectorOptions\")) m.DirectorOptions <- {};\n"
                         "        return m.DirectorOptions;\n"
                         "    } catch (e) { return null; }\n"
                         "}\n"
                         "function HL_ZSpawn(type) {\n"
                         "    local o = HL_DirectorOptions(), keys = [HL_LimitKeys[type]], saved = {};\n"
                         "    if (type <= 6) keys.append(\"MaxSpecials\");\n"
                         "    if (o) foreach (k in keys) { saved[k] <- o.rawin(k) ? o[k] : null; o[k] <- 32; }\n"
                         "    local ok = ZSpawn({ type = type });\n"
                         "    if (!ok) { local at = HL_HiddenSpot(); if (at) ok = ZSpawn({ type = type, pos = at }); }\n"
                         "    foreach (k, v in saved) { if (v == null) delete o[k]; else o[k] = v; }\n"
                         "    return ok;\n"
                         "}\n"
                         # the game only places a Tank by itself while none is alive (measured): for a second
                         # one, a spot like the Director's own, near the survivors and out of their sight
                         "function HL_HiddenSpot() {\n"
                         "    local p = Director.GetHighestFlowSurvivor();\n"
                         "    if (!p) { local e = null; while (e = Entities.FindByClassname(e, \"player\")) "
                         "if (e.IsSurvivor() && !e.IsDead()) { p = e; break; } }\n"
                         "    if (!p) return null;\n"
                         "    local t = {}, spots = [], at = p.GetOrigin();\n"
                         "    NavMesh.GetNavAreasInRadius(at, 1500.0, t);\n"
                         "    foreach (a in t) {\n"
                         "        local d = (a.GetCenter() - at).Length();\n"
                         "        if (d > 600 && !a.IsPotentiallyVisibleToTeam(2) && !a.IsUnderwater()) spots.append(a);\n"
                         "    }\n"
                         "    return spots.len() ? spots[RandomInt(0, spots.len() - 1)].FindRandomSpot() : null;\n"
                         "}\n"
                         "HL_Pending <- [];\n"
                         "function HL_TrySpawn(type, what, limit) {\n"
                         f"    if ({check}) return;\n"
                         "    if (!HL_ZSpawn(type)) HL_Pending.append({ type = type, what = what, "
                         "limit = limit, until = Time() + 30.0 });\n"
                         "}\n"
                         "HL_PendingNext <- 0.0;\n"
                         "function HL_Retry() {\n"
                         "    if (HL_Pending.len() == 0 || Time() < HL_PendingNext) return;\n"
                         "    HL_PendingNext = Time() + 1.0;\n"
                         "    local keep = [];\n"
                         "    foreach (s in HL_Pending) {\n"
                         "        local limit = s.limit, what = s.what;\n"
                         f"        if ({check}) continue;\n"
                         "        if (!HL_ZSpawn(s.type) && Time() < s.until) keep.append(s);\n"
                         "    }\n"
                         "    HL_Pending = keep;\n"
                         "}\n")
        if ir.logic_path:
            # how far along the path to the end safe room (0..1): the furthest survivor, the average
            # of the living survivors, the last one
            parts.append("function HL_PathMax() { local m = GetMaxFlowDistance(); return m > 0 ? m : 1.0; }\n"
                         "function HL_PathFurthest() { return Director.GetFurthestSurvivorFlow() / HL_PathMax(); }\n"
                         "function HL_PathSurvivors() {\n    local out = [], p = null;\n"
                         "    while (p = Entities.FindByClassname(p, \"player\"))\n"
                         "        if (p.IsSurvivor() && p.GetHealth() > 0) out.append(GetFlowDistanceForPosition(p.GetOrigin()) / HL_PathMax());\n"
                         "    return out;\n}\n"
                         "function HL_PathAverage() { local a = HL_PathSurvivors(), t = 0.0; foreach (v in a) t += v; "
                         "return a.len() ? t / a.len() : 0.0; }\n"
                         "function HL_PathLast() { local a = HL_PathSurvivors(), m = 2.0; foreach (v in a) if (v < m) m = v; "
                         "return a.len() ? m : 0.0; }\n")
        parts += [f + "\n" for f in ir.logic_functions]
        for hook, rows in ir.logic_hooks.items():
            parts.append(hook_function(hook, rows))
        if ir.logic_whens:
            rows = ", ".join(f'{{ fn = {fn}, t = "{rt}", f = "{rf}", once = {"true" if once else "false"}, last = false, '
                             f'done = false }}' for fn, rt, rf, once in ir.logic_whens)
            parts.append(f"HL_Whens <- [ {rows} ];\nHL_WhenNext <- 0.0;\n"
                         "function HL_When_Think() {\n"
                         "    if (Time() < HL_WhenNext) return;\n"
                         "    HL_WhenNext = Time() + 0.5;\n"
                         "    foreach (w in HL_Whens) {\n"
                         "        local v = w.fn.call(this);\n"      # in the script's scope, not the row's
                         "        if (v && !w.last) { if (!w.done) EntFire(w.t, \"Trigger\"); if (w.once) w.done = true; }\n"
                         "        else if (!v && w.last) EntFire(w.f, \"Trigger\");\n"
                         "        w.last = v;\n"
                         "    }\n}\n")
        if ir.logic_progress:
            # Path Progress: each node picks its random point once per game, fires when the furthest
            # survivor gets there
            rows = ", ".join(f'{{ relay = "{r}", lo = {lo:.3f}, hi = {hi:.3f}, at = -1.0 }}' for r, lo, hi in ir.logic_progress)
            parts.append(f"HL_Progress <- [ {rows} ];\nHL_ProgressNext <- 0.0;\n"
                         "function HL_Progress_Think() {\n"
                         "    if (Time() < HL_ProgressNext) return;\n"
                         "    HL_ProgressNext = Time() + 0.5;\n"
                         "    local max = GetMaxFlowDistance();\n"
                         "    if (max <= 0) return;\n"
                         "    local flow = Director.GetFurthestSurvivorFlow() / max;\n"
                         "    foreach (r in HL_Progress) {\n"
                         "        if (r.at < 0) { r.at = RandomFloat(r.lo, r.hi); "
                         "printl(\"HAMMERLESS_PROGRESS \" + r.relay + \" at \" + (r.at * 100).tointeger() + \" percent\"); }\n"
                         "        if (r.at <= 1.0 && flow >= r.at) { r.at = 2.0; EntFire(r.relay, \"Trigger\"); }\n"
                         "    }\n}\n")
        if thinks:
            parts.append("function HL_Think() {\n"
                         + ("    HL_Progress_Think();\n" if ir.logic_progress else "")
                         + ("    HL_When_Think();\n" if ir.logic_whens else "")
                         + ("    HL_Retry();\n" if ir.logic_retry else "")
                         + ("    if (Time() >= HL_OptsNext) { HL_OptsNext = Time() + 0.5; ::HL_ApplyOpts(); }\n"
                            if ir.logic_diropts else "") + "}\n")
            if ir.logic_diropts:
                parts.insert(-1, "HL_OptsNext <- 0.0;\n")
        events = {ev: list(t) for ev, t in ir.logic_events.items()}
        for ev in ir.logic_script_events:
            events.setdefault(ev, [])
        if ir.logic_counts:
            for ev in COUNT_CODE:
                events.setdefault(ev, [])
        for game_event, targets in events.items():
            body = "    local p = (\"userid\" in params) ? GetPlayerFromUserID(params.userid) : null;\n"
            if ir.logic_counts:
                body += COUNT_CODE.get(game_event, "")
            for cond, relay in targets:
                fire = f"EntFire(\"{relay}\", \"Trigger\");"
                body += f"    if ({cond}) {fire}\n" if cond else f"    {fire}\n"
            if ir.logic_script_events.get(game_event):
                info = vs.event(game_event)
                body += (vs.context_code(info) if info else "    ::HL_Ctx <- clone params;") + "\n"
                body += "\n".join(ir.logic_script_events[game_event]) + "\n"
            parts.append(f"function OnGameEvent_{game_event}(params) {{\n{body}}}\n")
        if events:
            # only the copy the map's logic entity runs (it has "self") listens: the mode script loads an
            # early copy too, for the Override questions asked before the map's entities exist
            parts.append("if (\"self\" in this) __CollectEventCallbacks(this, \"OnGameEvent_\", \"GameEventCallbacks\", "
                         "RegisterScriptGameEventListener);\n")
        self.ir.extra_scripts[f"scripts/vscripts/hammerless/logic_{self.ir.settings.name}.nut"] = "".join(parts)
        ours = f"hammerless/logic_{self.ir.settings.name}"
        script = next((e for e in self.ir.entities if e.classname == "logic_script"
                       and (e.keyvalues.get("targetname") or "").lower() == LOGIC_SCRIPT
                       and (e.keyvalues.get("vscripts") or "").startswith("hammerless/logic_")), None)
        if script is not None:            # (Hammerless's own, maybe from a build under another map name)
            script.keyvalues = {**script.keyvalues, "vscripts": ours}
        if script is None:
            for e in self.ir.entities:
                if (e.keyvalues.get("targetname") or "").lower() == LOGIC_SCRIPT:
                    self.problems.append(f"'{e.source or e.classname}' is named {LOGIC_SCRIPT}: rename it (the logic "
                                         "graphs' script entity has that name, so it would get their outputs)")
                    break
            script = Entity("logic_script", (0.0, 0.0, 0.0), (0, 0, 0), {
                "targetname": LOGIC_SCRIPT, "vscripts": f"hammerless/logic_{self.ir.settings.name}"}, [], LOGIC_SCRIPT)
            # first in the map: entities run their scripts as they're created, in order, and the game asks
            # Override questions (weapon spawns...) as the other entities are made
            self.ir.entities.insert(0, script)
        if thinks:
            script.keyvalues = {**script.keyvalues, "thinkfunction": "HL_Think"}


def hook_function(hook: str, rows: list) -> str:
    """::HL_Hook_<hook>: what the map's mode script calls when the game asks (see gamefiles.mode_files).
    rows: one per Override node: (the code its Asked wire runs, its answer or None, new damage or None)."""
    from .director_options import HOOKS_BY_NAME
    _name, _where, args, answer_kind, _about = HOOKS_BY_NAME[hook]
    if hook == "AllowTakeDamage":
        params = "dt"
        ctx = ("    ::HL_Ctx <- { attacker = dt.Attacker, victim = dt.Victim, inflictor = dt.Inflictor, "
               "damage = dt.DamageDone, damage_type = dt.DamageType, weapon = dt.Weapon, position = dt.Location };\n")
    else:
        params = ", ".join(a for a, _k in args)
        ctx = "    ::HL_Ctx <- { " + ", ".join(f"{a} = {a}" for a, _k in args) + " };\n"
    from .director_options import NO_BY_DEFAULT
    no = hook in NO_BY_DEFAULT          # normally no: any yes wins; otherwise any no wins
    default = {"bool": "false" if no else "true", "text": '""', "num": args[0][0] if args else "0"}.get(answer_kind, "null")
    join = "||" if no else "&&"
    body = ctx + f"    local answer = {default};\n"
    for code, answer, damage in rows:
        if code:        # its Asked chain; an Answer node in it leaves the answer in ::HL_Ans
            body += "    ::HL_Ans <- {};\n" + code + "\n"
            if hook == "AllowTakeDamage":
                body += '    if ("damage" in ::HL_Ans) dt.DamageDone = ::HL_Ans.damage;\n'
            if answer_kind == "bool":
                body += f'    if ("answer" in ::HL_Ans) answer = answer {join} ::HL_Ans.answer;\n'
            elif answer_kind:
                body += '    if ("answer" in ::HL_Ans) { local a = ::HL_Ans.answer; if (a != null && a != "") answer = a; }\n'
        if damage is not None:
            body += f"    dt.DamageDone = {damage};\n"
        if answer is not None:
            body += (f"    answer = answer {join} ({answer});\n" if answer_kind == "bool"
                     else f"    {{ local a = {answer}; if (a != null && a != \"\") answer = a; }}\n")
    if answer_kind == "num":
        body += "    return answer.tointeger();\n"
    elif answer_kind == "text":
        body += "    return answer == \"\" ? 0 : answer;\n"
    elif answer_kind:
        body += "    return answer;\n"
    return f"::HL_Hook_{hook} <- function({params}) {{\n{body}}}\n"


def compile_graph(nodes: list[LNode], links: list[LLink], ir: MapIR, graph: str = "", log: bool = False) -> list[str]:
    """Add the graph's entities, connections and script to the map. Returns problems to report.
    log: every wire also prints 'HAMMERLESS_EVENT from -> to' to the console when it fires."""
    problems: list[str] = []
    c = _Compiler(ir, problems, graph, log)
    c.nodes = {n.id: n for n in nodes}
    c.data_links = {(l.to_node, l.to_socket): (l.from_node, l.from_socket) for l in links if l.data}
    makers = ("BUTTON", "MOVE", "SHOW_HIDE", "VOLUME")
    for n in sorted(nodes, key=lambda n: (n.kind == "COLLISION", n.kind not in makers)):
        c.node(n)
    for l in links:
        if not l.data:
            c.link(l)
    c.render_script_nodes()
    c.finish_script()
    return problems
