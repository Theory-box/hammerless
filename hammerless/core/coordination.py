"""Captured latest-value signals and action-owned destination leases."""
from pathlib import Path
KINDS = ("CO_SIGNAL_PUBLISH", "CO_SIGNAL_READ", "CO_SIGNAL_CLEAR")
CLAIMS = ("BA_CLAIM", "BA_RELEASE_CLAIM")
ALL = KINDS + CLAIMS
HELPERS = Path(__file__).with_name("coordination_runtime.nut").read_text(encoding="utf-8")
FIELDS = {
    "CO_SIGNAL_PUBLISH": {"success": "false"},
    "CO_SIGNAL_READ": {"available":"false", "position":"Vector(0,0,0)", "age":"0.0", "remaining":"0.0", "revision":"0"},
    "CO_SIGNAL_CLEAR": {"success":"false"},
    "BA_CLAIM": {"acquired":"false", "remaining":"0.0"},
    "BA_RELEASE_CLAIM": {"released":"false"},
}
def call(c,n):
    c.helper("HLCO", HELPERS)
    v=lambda key,typ:c.typed_in(n,key,typ)
    if n.kind == "CO_SIGNAL_PUBLISH": return f"HLCO.Publish({v('channel','text')}, {v('position','vec')}, {v('ttl','num')})"
    if n.kind == "CO_SIGNAL_READ": return f"HLCO.Read({v('channel','text')})"
    if n.kind == "CO_SIGNAL_CLEAR": return f"HLCO.Clear({v('channel','text')})"
    if n.kind == "BA_CLAIM": return f"HLCO.Claim(ctx, {v('key','text')}, {v('ttl','num')})"
    if n.kind == "BA_RELEASE_CLAIM": return f"HLCO.Release(ctx, {v('key','text')})"
    raise ValueError("Unknown coordination node")
def output(n,socket,table):
    if socket not in FIELDS[n.kind]: raise ValueError("Unknown coordination output")
    return f"{table}.{socket}"
def stored_output(n,socket,key):
    value=output(n,socket,'::HL_R["'+key+'"]')
    return f'(("{key}" in ::HL_R) ? {value} : {FIELDS[n.kind][socket]})'
