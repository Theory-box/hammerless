"""Per-action, per-bot synchronous observations (never issue AI commands)."""
from pathlib import Path
from . import vscript as vs

HELPERS = Path(__file__).with_name("action_utilities_runtime.nut").read_text(encoding="utf-8")
KINDS = {"BA_COOLDOWN", "BA_CHANGED", "BA_MOVEMENT"}
FIELDS = {"BA_COOLDOWN": {"passed", "blocked", "remaining"},
          "BA_CHANGED": {"changed", "unchanged"},
          "BA_MOVEMENT": {"distance", "arrived", "progress", "stalled", "timed_out", "elapsed"}}


def call(c, n):
    c.helper("HLBA_Utilities", HELPERS)
    val = lambda name, kind: c.typed_in(n, name, kind)
    key = vs.literal(vs.TEXT, n.kind + ":" + n.id)
    reset = val("reset", "bool")
    if n.kind == "BA_COOLDOWN":
        return f"HLBAU.Cooldown(ctx, {key}, {val('seconds','num')}, {reset})"
    if n.kind == "BA_CHANGED":
        kind = n.settings.get("type", "num")
        if kind not in {"num", "bool", "text", "vec", "thing"}:
            raise ValueError("Changed supports Number, Boolean, Text, Vector and Entity values")
        initial = "true" if n.settings.get("initial", False) else "false"
        return f"HLBAU.Changed(ctx, {key}, {val('value',kind)}, {vs.literal(vs.TEXT,kind)}, {initial}, {reset})"
    if n.kind == "BA_MOVEMENT":
        args = [val("goal", "vec")] + [val(s, "num") for s in ("radius", "min_progress", "stall_seconds", "timeout")]
        return f"HLBAU.Movement(ctx, {key}, {', '.join(args)}, {reset})"
    raise ValueError(f"Unknown action utility {n.kind}")


def output(n, socket, table):
    if socket not in FIELDS[n.kind]:
        raise ValueError(f"Unknown action utility output {socket}")
    return f"{table}.{socket}"

