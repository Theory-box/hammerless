"""Captured, bounded operations on typed bot and nav-area lists."""
from pathlib import Path

KINDS = ("LIST_COUNT", "LIST_ITEM", "LIST_CLOSEST", "LIST_RANDOM", "LIST_FILTER")
FIELDS = {"found": "false", "count": "0", "item": "null", "items": "[]",
          "position": "Vector(0,0,0)", "truncated": "false"}


def call(c, n):
    kind = n.settings.get("list_type", "bots")
    if kind not in ("bots", "navareas"):
        raise ValueError("List type must be Bots or Nav Areas")
    c.helper("HLLT", Path(__file__).with_name("list_tools_runtime.nut").read_text(encoding="utf-8"))
    val = lambda key, typ: c.typed_in(n, key, typ)
    args = [val("items", "any"), '"' + kind + '"', '"' + n.kind.removeprefix("LIST_") + '"']
    args += [val("origin", "vec") if n.kind in ("LIST_CLOSEST", "LIST_FILTER") else "Vector(0,0,0)",
             val("radius", "num") if n.kind == "LIST_FILTER" else "0",
             val("limit", "num") if n.kind == "LIST_FILTER" else "128",
             val("index", "num") if n.kind == "LIST_ITEM" else "0"]
    return "HLLT.Query(" + ", ".join(args) + ")"


def output(n, socket, table):
    if socket not in FIELDS:
        raise ValueError(f"Unknown list output {socket}")
    return f"{table}.{socket}"


def stored_output(n, socket, key):
    value = output(n, socket, '::HL_R["' + key + '"]')
    return f'(("{key}" in ::HL_R) ? {value} : {FIELDS[socket]})'
