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


class _Compiler:
    def __init__(self, ir: MapIR, problems: list[str], graph: str = "", log: bool = False):
        self.log = log
        self.log_names: list[str] = []
        self.ir = ir
        self.problems = problems
        self.graph = _slug(graph) if graph else ""
        self.graph_tag = f"_{self.graph}" if self.graph else ""
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
        self.whens: list[tuple[str, str, str, bool]] = []
        self.converted: dict[str, tuple[str, str]] = {}   # object -> (class it became, node that did it)
        # script nodes (game functions and events): their event outputs run Squirrel directly
        self.script_fns: dict[str, tuple[str, str]] = {}     # node -> (function, body before Then)
        self.script_takes: dict[tuple[str, str], str] = {}  # (node, input) -> function to call
        self.script_conts: dict[tuple[str, str], list[str]] = {}   # (node, output) -> code it runs
        self.script_events: dict[str, list[str]] = {}       # game event -> event nodes

    # -- names and entities
    def fn_name(self, base: str) -> str:
        """A script function name no other node (in any graph) uses."""
        taken = self.ir.__dict__.setdefault("_logic_fn_names", set())
        name, i = base, 2
        while name in taken:
            name, i = f"{base}_{i}", i + 1
        taken.add(name)
        return name

    def unique(self, base: str) -> str:
        taken = {e.keyvalues.get("targetname") for e in self.ir.entities}
        name, i = base, 2
        while name in taken:
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
        slug = _slug(f"{self.graph}_{n.id}" if self.graph else n.id)
        if k == "SCRIPT_CALL":
            f = vs.function(s.get("fn", ""))
            if f is None or sock != "result":
                return "null"
            if vs.is_pure(f):
                return vs.call_expr(f, *self.script_args(n, f))
            return f'(("{slug}" in ::HL_R) ? ::HL_R["{slug}"] : null)'
        if k == "SCRIPT_EVENT":
            e = vs.event(s.get("event", ""))
            fld = next((x for x in (e or {}).get("fields", []) if x["name"] == sock), None)
            return vs.field_expr(sock, vs.field_kind(fld)) if fld else "null"
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
            fn = f"HL_Random_{slug}"
            if fn not in self.defined:
                self.defined.add(fn)
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
        slug = _slug(f"{self.graph}_{nid}" if self.graph else nid)

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
                guard = (f"    local t = {target};\n    if (t == null || !t.IsValid()) {{ printl(\"HAMMERLESS_SCRIPT "
                         f"'{sq_text(nid)}': nothing to act on\"); return; }}\n")
                target = "t"
            call = vs.call_expr(f, target, args)
            body = guard + (f'    ::HL_R["{slug}"] <- {call};\n' if f["returns"] != "void" else f"    {call};\n")
            self.script_fns[nid] = (fn, body)
            self.script_takes[(nid, "run")] = fn
            take("run", *self.script_call(fn))
            return
        if k == "SCRIPT_EVENT":
            e = vs.event(s.get("event", ""))
            if e is None:
                self.problems.append(f"Logic node '{nid}': pick a game event")
                return
            self.script_events.setdefault(e["name"], []).append(nid)
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
            cond = self.expr_in(n, "condition", False)
            on_true = self.add("logic_relay", f"hl_{slug}_true", {"spawnflags": RELAY})
            on_false = self.add("logic_relay", f"hl_{slug}_false", {"spawnflags": RELAY})
            fn = self.fn_name(f"HL_If_{slug}")
            self.functions.append(f"function {fn}() {{\n    if ({cond}) EntFire(\"{on_true.keyvalues['targetname']}\", "
                                  f"\"Trigger\");\n    else EntFire(\"{on_false.keyvalues['targetname']}\", \"Trigger\");\n}}")
            take("in", *self.script_call(fn))
            fire("true", on_true, "OnTrigger")
            fire("false", on_false, "OnTrigger")
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
                fn = f"HL_Spawn_{slug}"
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
            fn = f"HL_Teleport_{slug}"
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
            from .gamefiles import director_input_script, director_option_lines
            # (never the map-wide Director script's name, which a node called 'Director' would take)
            name = director_input_script(self.ir.settings.name, slug if slug != "director" else "director_settings")
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
            main = director_input_script(self.ir.settings.name, "director")
            take("apply", dname, "BeginScript", name)
            if self.ir.settings.director_enabled:
                take("reset", dname, "BeginScript", main)
            else:
                take("reset", dname, "EndScript")
        else:
            self.problems.append(f"Logic node '{nid}': unknown kind {k}")

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
        return n is not None and ((n.kind == "SCRIPT_CALL" and sock == "then" and nid in self.script_fns)
                                  or (n.kind == "SCRIPT_EVENT" and sock == "happened"))

    def render_script_nodes(self):
        """Script nodes' functions, now that their wires (what Then runs) are known."""
        for nid, (fn, body) in self.script_fns.items():
            then = "\n".join(self.script_conts.get((nid, "then"), []))
            self.functions.append(f"::{fn} <- function() {{\n{body}{then}\n}}")   # root: callable from anywhere
        for ev, nids in self.script_events.items():
            code = "\n".join(line for nid in nids for line in self.script_conts.get((nid, "happened"), []))
            if code:
                self.ir.logic_script_events.setdefault(ev, []).append(code)

    def finish_script(self):
        """(Re)write the one map script shared by all graphs: their functions, and one handler per
        game event registered on the script's own scope (the pattern that works in L4D2)."""
        if self.log_names:
            names = ", ".join('"' + n.replace('"', "'") + '"' for n in self.log_names)
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
                or ir.logic_script_events):
            return
        thinks = bool(ir.logic_progress or ir.logic_retry or ir.logic_whens)
        parts = [f"// Hammerless logic graphs for {ir.settings.name}\n",
                 "::HL_Ctx <- {};   // the event being handled, its fields\n::HL_R <- {};     // what action nodes returned\n"]
        if ir.logic_counts:
            parts.append("HL_Count <- { tank = 0, witch = 0, smoker = 0, boomer = 0, hunter = 0, spitter = 0, "
                         "jockey = 0, charger = 0 };\n"
                         "HL_CountNames <- { [1] = \"smoker\", [2] = \"boomer\", [3] = \"hunter\", "
                         "[4] = \"spitter\", [5] = \"jockey\", [6] = \"charger\" };\n")
        if ir.logic_retry:
            check = "(limit > 0 && HL_Count[what] >= limit)" if ir.logic_counts else "false"
            # The Director Spawns switches set the Director's own limit for a type to 0, and the
            # game's spawn call obeys that too: lift the limit just for our spawn, then put it back
            parts.append("HL_LimitKeys <- { [1] = \"SmokerLimit\", [2] = \"BoomerLimit\", [3] = \"HunterLimit\", "
                         "[4] = \"SpitterLimit\", [5] = \"JockeyLimit\", [6] = \"ChargerLimit\", [7] = \"WitchLimit\", "
                         "[8] = \"TankLimit\" };\n"
                         "function HL_DirectorOptions() {\n"
                         "    try { return ::DirectorScript.MapScript.LocalScript.DirectorOptions; } catch (e) { return null; }\n"
                         "}\n"
                         "function HL_ZSpawn(type) {\n"
                         "    local o = HL_DirectorOptions(), keys = [HL_LimitKeys[type]], saved = {};\n"
                         "    if (type <= 6) keys.append(\"MaxSpecials\");\n"
                         "    if (o) foreach (k in keys) if (k in o) { saved[k] <- o[k]; o[k] = 32; }\n"
                         "    local ok = ZSpawn({ type = type });\n"
                         "    foreach (k, v in saved) o[k] = v;\n"
                         "    return ok;\n"
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
                         + ("    HL_Retry();\n" if ir.logic_retry else "") + "}\n")
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
            parts.append("__CollectEventCallbacks(this, \"OnGameEvent_\", \"GameEventCallbacks\", "
                         "RegisterScriptGameEventListener);\n")
        self.ir.extra_scripts[f"scripts/vscripts/hammerless/logic_{self.ir.settings.name}.nut"] = "".join(parts)
        script = next((e for e in self.ir.entities if e.keyvalues.get("targetname") == LOGIC_SCRIPT), None)
        if script is None:
            script = Entity("logic_script", (0.0, 0.0, 0.0), (0, 0, 0), {
                "targetname": LOGIC_SCRIPT, "vscripts": f"hammerless/logic_{self.ir.settings.name}"}, [], LOGIC_SCRIPT)
            self.ir.entities.append(script)
        if thinks:
            script.keyvalues = {**script.keyvalues, "thinkfunction": "HL_Think"}


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
