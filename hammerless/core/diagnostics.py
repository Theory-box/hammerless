"""Bounded, opt-in public graph observations; never a profiler."""
from pathlib import Path
from . import vscript as vs
KINDS = ("DBG_WATCH", "DBG_CHECK")
ACTION = ("BA_INSPECT",)
ALL = KINDS + ACTION
HELPERS = Path(__file__).with_name("diagnostics_runtime.nut").read_text(encoding="utf-8")
FIELDS = {"DBG_WATCH": {"logged":"false", "suppressed":"false", "reason":'"disabled"'},
          "DBG_CHECK": {"logged":"false", "suppressed":"false", "reason":'"disabled"', "passed":"false"},
          "BA_INSPECT": {"status":'"IDLE"', "running":"false", "success":"false", "failed":"false", "stopping":"false", "updates":"0"}}

def call(c, n, context="null"):
    c.helper("HLDBG", HELPERS)
    v=lambda key,typ:c.typed_in(n,key,typ)
    if n.kind == "BA_INSPECT": return "HLDBG.Inspect(ctx)"
    key=vs.literal(vs.TEXT,n.id)
    enabled=v("enabled","bool")
    label=v("label","text")
    interval=v("interval","num")
    if n.kind == "DBG_CHECK":
        return f"({v('condition','bool')} ? HLDBG.Passed() : ({enabled} ? HLDBG.Check({context}, {key}, false, true, {label}, {interval}) : HLDBG.Check(null, {key}, false, false, \"\", 1)))"
    typ=n.settings.get("type","num")
    if typ not in {"num","bool","text","vec","thing","bot","navarea"}:
        raise ValueError("Debug Watch requires a scalar, position, entity, bot or nav area")
    # Runtime ternary: disabled watch does not evaluate value/label/interval.
    return f"({enabled} ? HLDBG.Watch({context}, {key}, {label}, {v('value',typ)}, {interval}) : HLDBG.Disabled())"

def output(n,socket,table):
    if socket not in FIELDS[n.kind]: raise ValueError("Unknown diagnostic output")
    return f"{table}.{socket}"

def stored_output(n,socket,key):
    value=output(n,socket,'::HL_R["'+key+'"]')
    return f'(("{key}" in ::HL_R) ? {value} : {FIELDS[n.kind][socket]})'
