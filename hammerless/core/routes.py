"""Bounded authored routes and public, stateless cursor operations."""
from pathlib import Path
import math
from . import vscript as vs

HELPERS = Path(__file__).with_name("routes_runtime.nut").read_text(encoding="utf-8")
KINDS = ("ROUTE_POINT", "ROUTE_NEAREST", "ROUTE_ADVANCE")
FIELDS = {"found": "false", "position": "Vector(0,0,0)", "index": "-1", "count": "0",
          "distance": "0", "direction": "1", "completed": "false", "error": '"Not run"'}


def definition(c, n):
    points = n.settings.get("points", [])
    error = n.settings.get("error", "")
    if not error and (not points or len(points) > 256):
        error = "Route needs 1 to 256 ordered Empty points"
    if not error and any(len(p) != 3 or any(not math.isfinite(float(x)) for x in p) for p in points):
        error = "Route contains a non-finite position"
    if error:
        from .botactions import BotActionProblem
        c.problems.append(BotActionProblem(f"Route '{n.id}': {error}"))
        return "null"
    key = "HL_Route_" + c.key_of(n.id)
    c.helper(key, "::" + key + " <- [" + ", ".join(vs.literal(vs.VEC, p) for p in points) + "];\n")
    return "::" + key


def call(c, n):
    c.helper("HLRoute", HELPERS)
    val = lambda k, t: c.typed_in(n, k, t)
    route = val("route", "any")
    if n.kind == "ROUTE_POINT":
        return f"HLRoute.Point({route}, {val('index','num')})"
    if n.kind == "ROUTE_NEAREST":
        return f"HLRoute.Nearest({route}, {val('origin','vec')})"
    mode = n.settings.get("mode", "ONCE")
    if mode not in {"ONCE", "LOOP", "PINGPONG"}:
        raise ValueError("Unknown route traversal mode")
    return f"HLRoute.Advance({route}, {val('index','num')}, {val('direction','num')}, {vs.literal(vs.TEXT,mode)})"


def output(n, socket, table):
    if socket not in FIELDS:
        raise ValueError("Unknown route output " + socket)
    return f"{table}.{socket}"


def stored_output(n, socket, key):
    value = output(n, socket, '::HL_R["' + key + '"]')
    return f'(("{key}" in ::HL_R) ? {value} : {FIELDS[socket]})'
