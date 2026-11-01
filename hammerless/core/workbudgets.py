"""Public shared work pools and action-owned, expiring grants."""
from pathlib import Path
import hashlib
from . import vscript as vs

ACTION = ("WB_REQUEST",)
FIELDS = {"grant":"null", "granted":"0", "remaining":"0", "waiting":"false",
          "queued":"false", "error":'""', "cycle":"0"}


def definition(c, n):
    limit = n.settings.get("limit", 128)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 4096:
        from .botactions import BotActionProblem
        c.problems.append(BotActionProblem("Work Budget Pool requires 1–4096 entries per scheduler cycle"))
        return "null"
    identity = "work_" + hashlib.sha256(c.key_of(n.id).encode("utf-8")).hexdigest()
    return "{id=" + vs.literal("text", identity) + ",limit=" + str(limit) + "}"


def call(c, n):
    c.helper("HLWB", Path(__file__).with_name("work_budget_runtime.nut").read_text(encoding="utf-8"))
    return (f"HLWB.Request(ctx, {vs.literal('text',n.id)}, {c.typed_in(n,'pool','any')}, "
            f"{c.typed_in(n,'wanted','num')}, {c.typed_in(n,'enabled','bool')})")


def output(n, socket, table):
    if socket not in FIELDS:
        raise ValueError("Unknown work grant output")
    return table + "." + socket
