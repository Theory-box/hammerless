"""L4D2's script API as logic nodes: the catalogue (data/vscript_api.json, made by
scripts/vscript_catalog.py from the game's own `script_help` and event files) and how a call or an
event field becomes Squirrel in the map script.

A function node either works something out (a value node: no event wires, e.g. Get Health) or does
something (an action: an event comes in, the call runs, Then fires; what it returns is kept for
later nodes). Event nodes run their wires with the event's fields in HL_Ctx (players and entities
already turned into handles).
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vscript_api.json")

# value kinds a socket carries
NUM, BOOL, TEXT, VEC, THING, ANY = "num", "bool", "text", "vec", "thing", "any"
KIND_OF_TYPE = {"int": NUM, "float": NUM, "bool": BOOL, "string": TEXT, "Vector": VEC, "QAngle": VEC,
                "handle": THING, "any": ANY}
TYPE_LABEL = {"int": "Whole Number", "float": "Number", "bool": "True/False", "string": "Text", "Vector": "Vector",
              "QAngle": "Angles", "handle": "Entity", "any": "Value"}
ON_LABEL = {"player": "Player", "entity": "Entity", "area": "Nav Area"}
# names of functions that only work something out (value nodes); everything else is an action
PURE = re.compile(r"^(Get|Is|Has|Are|Can|Find|Lookup|Eye|Contains|Compute|Count|Should|Was|First|Next|Length|"
                  r"Dot|Cross|Norm|To[A-Z]|Rand|Time|Frame|Tick|Trace|Any|Num|Did)")


@lru_cache(maxsize=1)
def catalogue() -> dict:
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    data["by_id"] = {f["id"]: f for f in data["functions"]}
    data["events_by_name"] = {e["name"]: e for e in data["events"]}
    return data


def function(fid: str) -> dict | None:
    return catalogue()["by_id"].get(fid)


def event(name: str) -> dict | None:
    return catalogue()["events_by_name"].get(name)


def is_pure(f: dict) -> bool:
    """A value node (no event wires): it only works something out. Functions that fill a table in
    (GetAllAreas...) are values too: the node gives the table."""
    if f.get("fills") is not None:
        return True
    return f["returns"] != "void" and PURE.match(f["name"]) is not None


def label(f: dict) -> str:
    """'GetHealth' -> 'Get Health'."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", f["name"].lstrip("_"))
    return words.replace("_", " ").strip()


def event_label(e: dict) -> str:
    return e["name"].replace("_", " ").capitalize()


def field_kind(fld: dict) -> str:
    if fld.get("player") or fld.get("entity"):
        return THING
    return {"string": TEXT, "bool": BOOL}.get(fld["type"], NUM)


def param_sockets(f: dict) -> list[tuple[str, str, str]]:
    """(identifier, label, kind) for a function's inputs: the thing it acts on, then its parameters."""
    out = []
    if f.get("on"):
        out.append(("target", ON_LABEL[f["on"]], THING))
    names = f.get("names") or []
    counts: dict[str, int] = {}
    for t in f["params"]:
        counts[t] = counts.get(t, 0) + 1
    seen: dict[str, int] = {}
    for i, t in enumerate(f["params"]):
        if i == f.get("fills"):
            continue                       # filled in by the function: the node's Result
        seen[t] = seen.get(t, 0) + 1
        if i < len(names):
            name = names[i]
        else:
            name = TYPE_LABEL.get(t, "Value") + (f" {seen[t]}" if counts[t] > 1 else "")
        kind = ANY if i in (f.get("tables") or []) else KIND_OF_TYPE.get(t, ANY)
        out.append((f"p{i}", name, kind))
    return out


def result_kind(f: dict) -> str | None:
    if f.get("fills") is not None:
        return ANY
    return None if f["returns"] == "void" else KIND_OF_TYPE.get(f["returns"], ANY)


def cast(expr: str, t: str) -> str:
    """An argument as the type the game expects (numbers on value wires are floats)."""
    if t == "int":
        return f"({expr}).tointeger()"
    if t == "float":
        return f"({expr}).tofloat()"
    return expr


def call_expr(f: dict, target: str | None, args: list[str]) -> str:
    """The call as an expression. args are the shown inputs in order; a filled-in table is made here
    and the expression gives it."""
    fills = f.get("fills")
    it = iter(args)
    parts = []
    for i, t in enumerate(f["params"]):
        parts.append("t" if i == fills else cast(next(it, "null"), t))
    a = ", ".join(parts)
    call = f"{target}.{f['name']}({a})" if f.get("on") else f"{f['call']}({a})"
    if fills is not None:
        return f"(function() {{ local t = {{}}; {call}; return t; }})()"
    return call


def literal(kind: str, value) -> str:
    """A value typed on a socket, as Squirrel."""
    if kind == BOOL:
        return "true" if value else "false"
    if kind == NUM:
        v = float(value or 0.0)
        text = f"{v:g}"
        return text if any(c in text for c in ".e") else text + ".0"
    if kind == TEXT:
        s = str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{s}"'
    if kind == VEC:
        x, y, z = (tuple(value) + (0.0, 0.0, 0.0))[:3] if value else (0.0, 0.0, 0.0)
        return f"Vector({x:g}, {y:g}, {z:g})"
    return "null"


def context_code(e: dict) -> str:
    """Squirrel that fills HL_Ctx from an event's params: user ids become players, entity ids entities
    (the raw number stays as <field>_id)."""
    lines = ["    ::HL_Ctx <- clone params;"]   # (root table: every caller sees the same one)
    for fld in e["fields"]:
        n = fld["name"]
        if fld.get("player"):
            lines.append(f'    if ("{n}" in params) {{ ::HL_Ctx["{n}_id"] <- params["{n}"]; '
                         f'::HL_Ctx["{n}"] <- GetPlayerFromUserID(params["{n}"]); }}')
        elif fld.get("entity"):
            lines.append(f'    if ("{n}" in params) {{ ::HL_Ctx["{n}_id"] <- params["{n}"]; '
                         f'::HL_Ctx["{n}"] <- EntIndexToHScript(params["{n}"]); }}')
    return "\n".join(lines)


def field_expr(name: str, kind: str) -> str:
    default = {NUM: "0.0", BOOL: "false", TEXT: '""'}.get(kind, "null")
    value = f'::HL_Ctx["{name}"]'      # (by key: a field may be named like a keyword, e.g. "class")
    if kind == NUM:
        value = f'::HL_Ctx["{name}"].tofloat()'
    return f'(("{name}" in ::HL_Ctx && ::HL_Ctx["{name}"] != null) ? {value} : {default})'


def groups() -> list[tuple[str, list[dict]]]:
    """Functions by menu group, in a stable order."""
    by: dict[str, list[dict]] = {}
    for f in catalogue()["functions"]:
        by.setdefault(f["group"], []).append(f)
    return sorted(by.items())


def event_groups() -> list[tuple[str, list[dict]]]:
    """Events by their first word (player_*, tank_*, ...); small groups go under Other."""
    by: dict[str, list[dict]] = {}
    for e in catalogue()["events"]:
        by.setdefault(e["name"].split("_")[0], []).append(e)
    out, other = [], []
    for k, v in sorted(by.items()):
        (out.append((k.capitalize(), v)) if len(v) >= 4 else other.extend(v))
    return out + ([("Other", other)] if other else [])
