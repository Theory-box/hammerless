"""Logic graphs -> the game's entities and I/O connections.

The Blender node editor (blender/logic.py) hands over plain data: nodes (a kind, settings,
maybe a scene object or volume brushes) and wires (from node/socket to node/socket). Each
node becomes zero or more entities; each wire becomes one output row on the entity that
fires it ("when X happens, tell Y to do Z"), exactly what Hammer mappers wire by hand.
Nothing here needs scripting, so it is as robust as hand-made Hammer logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .entities import default_keyvalues
from .ir import Brush, Entity, MapIR, Output

DIRECTOR = "director"
TRIGGER_MATERIAL = "tools/toolstrigger"
WHO_FLAGS = {"SURVIVORS": "1", "INFECTED": "3", "EVERYONE": "3"}   # trigger spawnflags: 1 clients, 2 NPCs


@dataclass
class LNode:
    id: str                          # unique within the graph (the Blender node name)
    kind: str                        # OBJECT, DIRECTOR, VOLUME, MAP_START, DELAY, ONCE, COUNTER, RANDOM, HORDE, MESSAGE
    settings: dict = field(default_factory=dict)
    obj: str = ""                    # scene object the node stands for (OBJECT, VOLUME)
    brushes: list[Brush] = field(default_factory=list)   # VOLUME: the object's shape
    params: dict = field(default_factory=dict)            # input socket -> value typed on the node


@dataclass
class LLink:
    from_node: str
    from_socket: str
    to_node: str
    to_socket: str


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


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "node"


class _Compiler:
    def __init__(self, ir: MapIR, problems: list[str]):
        self.ir = ir
        self.problems = problems
        self.by_source = {}
        for e in ir.entities:
            self.by_source.setdefault(re.sub(r" \(part \d+\)$", "", e.source), e)
        self.fires: dict[tuple[str, str], _Fire] = {}
        self.takes: dict[tuple[str, str], _Take] = {}
        self.filters: dict[str, str] = {}

    # -- helpers
    def name_of(self, e: Entity, hint: str) -> str:
        """The entity's targetname, giving it one if it has none."""
        if not e.keyvalues.get("targetname"):
            e.keyvalues = {**e.keyvalues, "targetname": f"hl_{_slug(hint)}"}
        return e.keyvalues["targetname"]

    def add(self, classname: str, name: str, kv: dict, brushes=None, source: str = "") -> Entity:
        e = Entity(classname, None if brushes else (0.0, 0.0, 16.0), (0, 0, 0),
                   {"targetname": name, **kv}, brushes or [], source or name)
        self.ir.entities.append(e)
        return e

    def director(self) -> Entity:
        for e in self.ir.entities:
            if e.classname == "info_director":
                self.name_of(e, DIRECTOR)
                return e
        return self.add("info_director", DIRECTOR, {k: v for k, v in default_keyvalues("info_director").items()
                                                     if k != "targetname"})

    def team_filter(self, team: str) -> str:
        if team not in self.filters:
            name = f"hl_filter_team{team}"
            self.add("filter_activator_team", name, {"filterteam": team, "Negated": "0"})
            self.filters[team] = name
        return self.filters[team]

    # -- nodes
    def node(self, n: LNode):
        s, nid = n.settings, n.id
        slug = _slug(nid)

        def fire(sock, ent, output, delay=0.0, times=-1):
            self.fires[(nid, sock)] = _Fire(ent, output, delay, times)

        def take(sock, target, inp, param=""):
            self.takes[(nid, sock)] = _Take(target, inp, n.params.get(sock, param))

        if n.kind in ("OBJECT", "DIRECTOR"):
            e = self.director() if n.kind == "DIRECTOR" else self.by_source.get(n.obj)
            if e is None:
                self.problems.append(f"Logic node '{nid}': "
                                     + ("pick an object" if not n.obj else
                                        f"'{n.obj}' isn't an entity (set its Role to Point or Brush Entity)"))
                return
            name = self.name_of(e, n.obj or DIRECTOR)
            for sock in s.get("outputs", []):
                fire(sock, e, sock)
            for sock in s.get("inputs", []):
                take(sock, name, sock)
        elif n.kind == "VOLUME":
            if not n.brushes:
                self.problems.append(f"Logic node '{nid}': pick a mesh object for the volume")
                return
            for b in n.brushes:
                for f in b.faces:
                    f.material = TRIGGER_MATERIAL
            who = s.get("who", "SURVIVORS")
            kv = {"spawnflags": WHO_FLAGS.get(who, "1"), "wait": "0", "entireteam": "2", "StartDisabled":
                  "1" if s.get("start_disabled") else "0"}
            if who in ("SURVIVORS", "INFECTED"):
                kv["filtername"] = self.team_filter("2" if who == "SURVIVORS" else "3")
            e = self.add("trigger_multiple", f"hl_{slug}", kv, n.brushes, n.obj)
            times = 1 if s.get("once") else -1
            for sock, output in (("enter", "OnStartTouch"), ("leave", "OnEndTouch"), ("first", "OnStartTouchAll"),
                                 ("empty", "OnEndTouchAll"), ("all_inside", "OnEntireTeamStartTouch")):
                fire(sock, e, output, times=times)
            take("enable", e.keyvalues["targetname"], "Enable")
            take("disable", e.keyvalues["targetname"], "Disable")
        elif n.kind == "MAP_START":
            e = self.add("logic_auto", f"hl_{slug}", {"spawnflags": "1"})
            fire("start", e, "OnMapSpawn")
        elif n.kind in ("DELAY", "ONCE"):
            e = self.add("logic_relay", f"hl_{slug}", {"spawnflags": "1" if n.kind == "ONCE" else "0"})
            take("in", e.keyvalues["targetname"], "Trigger")
            take("cancel", e.keyvalues["targetname"], "CancelPending")
            fire("out", e, "OnTrigger", float(s.get("seconds", 0)) if n.kind == "DELAY" else 0.0)
        elif n.kind == "COUNTER":
            target = max(1, int(s.get("count", 3)))
            e = self.add("math_counter", f"hl_{slug}", {"min": "0", "max": str(target), "startvalue": "0"})
            take("add", e.keyvalues["targetname"], "Add", "1")
            take("reset", e.keyvalues["targetname"], "SetValueNoFire", "0")
            fire("reached", e, "OnHitMax")
        elif n.kind == "RANDOM":
            e = self.add("logic_case", f"hl_{slug}", {})
            take("pick", e.keyvalues["targetname"], "PickRandom")
            for i in range(1, 5):
                fire(f"case{i}", e, f"OnCase{i:02d}")
        elif n.kind == "HORDE":
            d = self.director()
            take("start", self.name_of(d, DIRECTOR), "ForcePanicEvent")
            fire("finished", d, "OnPanicEventFinished")
        elif n.kind == "MESSAGE":
            e = self.add("env_instructor_hint", f"hl_{slug}", {
                "hint_caption": s.get("text", "Objective"), "hint_timeout": str(int(float(s.get("seconds", 6)))),
                "hint_color": s.get("color", "255 255 255"), "hint_static": "1", "hint_icon_onscreen":
                s.get("icon", "icon_tip"), "hint_forcecaption": "1", "hint_range": "0", "hint_auto_start": "0",
                "hint_instance_type": "2"})
            take("show", e.keyvalues["targetname"], "ShowHint")
            take("hide", e.keyvalues["targetname"], "EndHint")
        else:
            self.problems.append(f"Logic node '{nid}': unknown kind {n.kind}")

    def link(self, l: LLink):
        f = self.fires.get((l.from_node, l.from_socket))
        t = self.takes.get((l.to_node, l.to_socket))
        if f is None or t is None:
            return            # the node itself already reported what's wrong
        f.entity.outputs.append(Output(f.output, t.target, t.input, t.param, f.delay, f.times))


def compile_graph(nodes: list[LNode], links: list[LLink], ir: MapIR) -> list[str]:
    """Add the graph's entities and connections to the map. Returns problems to report."""
    problems: list[str] = []
    c = _Compiler(ir, problems)
    for n in nodes:
        c.node(n)
    for l in links:
        c.link(l)
    return problems
