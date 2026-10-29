"""Shared compiler support for captured world queries, including bot lifecycle paths."""
from pathlib import Path

KINDS = ("WQ_NEAREST", "WQ_NEARBY", "WQ_INFO", "WQ_ITEM", "WQ_PATH", "WQ_AMBIENT")
FIELDS = {"available": "false", "found": "false", "area": "null", "areas": "[]",
          "count": "0", "position": "Vector(0,0,0)", "id": "-1", "blocked": "false",
          "underwater": "false", "exists": "false", "brightness": "0.0", "color": "Vector(0,0,0)", "fallback": "false"}


def call(c, n):
    """Emit a query once; downstream outputs read its snapshot rather than repeat it."""
    if n.kind == "WQ_AMBIENT":
        c.ir._world_query_ambient = True
        c.helper("HLWQ_Ambient", Path(__file__).with_name("ambient_query_runtime.nut").read_text(encoding="utf-8"))
        return f"HLWQ_Ambient({c.typed_in(n, 'position', 'vec')})"
    c.helper("HLWQ", Path(__file__).with_name("world_queries_runtime.nut").read_text(encoding="utf-8"))
    val = lambda name, kind: c.typed_in(n, name, kind)
    boolean = lambda name, default: "true" if n.settings.get(name, default) else "false"
    team = int(n.settings.get("team", 3))
    if team not in (2, 3):
        raise ValueError("Nav query Team must be Survivors or Infected")
    if n.kind == "WQ_NEAREST":
        return f"HLWQ.Nearest({val('position','vec')}, {val('radius','num')}, {boolean('check_los',True)}, {boolean('check_ground',True)})"
    if n.kind == "WQ_NEARBY":
        return f"HLWQ.Nearby({val('position','vec')}, {val('radius','num')}, {val('limit','num')})"
    if n.kind == "WQ_INFO":
        return f"HLWQ.Info({val('area','any')}, {team})"
    if n.kind == "WQ_ITEM":
        return f"HLWQ.Item({val('areas','any')}, {val('index','num')})"
    if n.kind == "WQ_PATH":
        return f"HLWQ.Path({val('start','any')}, {val('goal','any')}, {val('length','num')}, {team}, {boolean('ignore_blockers',False)})"
    raise ValueError(f"Unknown world query {n.kind}")


def output(n, socket, table):
    if socket not in FIELDS:
        raise ValueError(f"Unknown world query output {socket}")
    return f"{table}.{socket}"


def stored_output(n, socket, key):
    value = output(n, socket, '::HL_R["' + key + '"]')
    return f'(("{key}" in ::HL_R) ? {value} : {FIELDS[socket]})'
