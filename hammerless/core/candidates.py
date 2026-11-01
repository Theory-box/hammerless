"""Bounded candidate evaluation with user-authored, stateless value groups."""
from pathlib import Path
from . import vscript as vs, worldqueries, querycache

KINDS = ("CAND_EVALUATE", "CAND_ITEM", "CAND_FIND", "CAND_STEP")
PURE = {"VALUE", "EXPOSED_VALUE", "MATH", "COMPARE", "BOOL_MATH", "MAKE_VECTOR",
        "BREAK_VECTOR", "VECTOR_MATH", "CHECK", "PICK", "TEXT_CHECK", "FORMAT_TEXT",
        "GET_FIELD", "GET_PROP", "CAND_PARAM", "CAND_AMBIENT", "CAND_CACHED_AMBIENT"}
FIELDS = {"found": "false", "count": "0", "examined": "0", "rejected": "0", "errors": "0",
          "truncated": "false", "item": "null", "items": "[]", "score": "0.0", "index": "-1",
          "rank": "-1", "position": "Vector(0,0,0)", "results": "null", "error": '""'}
STEP_FIELDS = {"status":'"IDLE"', "busy":"false", "complete":"false", "cancelled":"false",
               "failed":"false", "processed":"0", "total":"0", "progress":"0.0", "age":"0.0", "waiting":"false"}


def compile_rule(c, node, expected):
    """Compile a pure DAG into lazy memoized locals, preserving short-circuit nodes."""
    d = node.settings.get("definition", {})
    if d.get("list_type") != expected:
        raise ValueError("Candidate rule type must match the evaluator's Bot/Nav Area list type")
    nodes, links = d.get("nodes", []), d.get("links", [])
    if len(nodes) > 128 or len(links) > 512:
        raise ValueError("Candidate rules allow at most 128 nodes and 512 links")
    if any(n.kind not in PURE for n in nodes) or any(not l.data for l in links):
        raise ValueError("Candidate rules support stateless value nodes only; no events, state, commands or delays")
    from .logic import _Compiler
    sub = _Compiler(c.ir, c.problems)
    sub.nodes = {n.id: n for n in nodes}
    if len(sub.nodes) != len(nodes):
        raise ValueError("Candidate rule contains duplicate node identifiers")
    sub.data_links = {(l.to_node, l.to_socket): (l.from_node, l.from_socket) for l in links}
    if len(sub.data_links) != len(links):
        raise ValueError("Candidate rule inputs must have only one incoming wire")
    if any(l.from_node not in sub.nodes or l.to_node not in sub.nodes for l in links):
        raise ValueError("Candidate rule wire references a missing node")
    dependencies = {}
    for link in links:
        dependencies.setdefault(link.to_node, []).append(link.from_node)
    depths = {}
    def check(nid, path=()):
        if nid in path:
            raise ValueError("Candidate rule value wires contain a cycle")
        if len(path) >= 64:
            raise ValueError("Candidate rule exceeds 64 dependency levels")
        if nid in depths:
            return depths[nid]
        depth = 1 + max((check(dependency, path + (nid,))
                         for dependency in dependencies.get(nid, [])), default=0)
        if depth > 64:
            raise ValueError("Candidate rule exceeds 64 dependency levels")
        depths[nid] = depth
        return depth
    for nid in sub.nodes:
        check(nid)
    sub.helper = c.helper
    body, cache, active, ambient = ["local memo={}, values={}, samples={}, queries={};"], {}, set(), {}
    inputs = dict(d.get("inputs", []))

    def expression(nid, socket):
        key = nid, socket
        if key in active:
            raise ValueError("Candidate rule value wires contain a cycle")
        if key in cache:
            return cache[key]
        if nid not in sub.nodes or len(active) >= 64:
            raise ValueError("Candidate rule has a missing node or exceeds 64 dependency levels")
        active.add(key)
        n = sub.nodes[nid]
        if n.kind == "CAND_PARAM":
            uid = n.settings.get("uid")
            if uid not in inputs:
                raise ValueError("Unknown candidate rule parameter")
            value = {"candidate": "candidate", "position": "position", "index": "index"}.get(uid)
            if value is None:
                value = "params[" + vs.literal("text", uid) + "]"
        elif n.kind in {"CAND_AMBIENT", "CAND_CACHED_AMBIENT"}:
            cached = n.kind == "CAND_CACHED_AMBIENT"
            if nid not in ambient:
                old = n.kind
                try:
                    n.kind = "QC_AMBIENT" if cached else "WQ_AMBIENT"
                    value = querycache.call(sub, n) if cached else worldqueries.call(sub, n)
                finally:
                    n.kind = old
                index = len(ambient)
                local = f"queries[{index}]"
                body.append(f"{local} <- function() {{ if (!({index} in samples)) "
                            f"samples[{index}] <- {value}; return samples[{index}]; }};")
                ambient[nid] = local
            allowed = {"available", "brightness", "color", "fallback"}
            if cached: allowed |= {"cache_hit", "refreshed", "age", "error"}
            if socket not in allowed:
                raise ValueError("Unknown ambient-light output")
            value = ambient[nid] + "()." + socket
        else:
            value = sub.value_expr(n, socket)
        index = len(cache)
        local = f"values[{index}]"
        body.append(f"{local} <- function() {{ if (!({index} in memo)) "
                    f"memo[{index}] <- {value}; return memo[{index}]; }};")
        cache[key] = local + "()"
        active.remove(key)
        return cache[key]

    sub.expr_out = expression
    outputs = d.get("outputs", {})
    if set(outputs) != {"keep", "score"}:
        raise ValueError("Candidate rule requires wired Keep and Score outputs")
    keep, score = (expression(*outputs[name]) for name in ("keep", "score"))
    fn = c.fn_name("HL_CandidateRule")
    c.functions.append(f"function {fn}(candidate, position, index, params) {{\n" +
                       "\n".join(body) + f"\nreturn {{keep={keep}, score={score}}};\n}}")
    # Quoted Squirrel table keys require bracket syntax.
    params = ["[" + vs.literal("text", uid) + "]=" + c.typed_in(node, uid, typ)
              for uid, typ in inputs.items() if uid not in {"candidate", "position", "index"}]
    return fn, "{" + ",".join(params) + "}"


def call(c, n, context=None):
    c.helper("HLLT", Path(__file__).with_name("list_tools_runtime.nut").read_text())
    c.helper("HLCE", Path(__file__).with_name("candidate_runtime.nut").read_text())
    kind = n.settings.get("list_type", "bots")
    if kind not in {"bots", "navareas"}:
        raise ValueError("Candidate list type must be Bots or Nav Areas")
    if n.kind == "CAND_FIND":
        return f"HLCE.Find({c.typed_in(n,'results','any')}, {c.typed_in(n,'item','any')}, {vs.literal('text',kind)})"
    if n.kind == "CAND_ITEM":
        return f"HLCE.Item({c.typed_in(n,'results','any')}, {c.typed_in(n,'index','num')}, {vs.literal('text',kind)})"
    src = c.data_links.get((n.id, "rule"))
    rule = c.nodes.get(src[0]) if src else None
    if rule is None or rule.kind != "CAND_RULE" or src[1] != "rule":
        raise ValueError("Evaluate Candidates needs a Candidate Rule group connected to Rule")
    fn, params = compile_rule(c, rule, kind)
    if n.kind == "CAND_STEP":
        if context is None:
            raise ValueError("Evaluate Candidates in Steps belongs inside an action lifecycle")
        # Keep input evaluation inside the factory: resumed/disabled jobs do not
        # re-read their input list, captured rule parameters or result limit.
        factory = f"function() {{ return {{items={c.typed_in(n,'items','any')},kind={vs.literal('text',kind)},rule={fn},params={params},limit={c.typed_in(n,'limit','num')}}}; }}"
        shared = (n.id, 'grant') in c.data_links
        if shared:
            c.helper("HLWB", Path(__file__).with_name("work_budget_runtime.nut").read_text(encoding="utf-8"))
        return f"HLCE.Step({context}, {vs.literal('text',n.id)}, {factory}, {c.typed_in(n,'budget','num')}, {c.typed_in(n,'reset','bool')}, {c.typed_in(n,'enabled','bool')}, {c.typed_in(n,'grant','any') if shared else 'null'}, {'true' if shared else 'false'})"
    return f"HLCE.Evaluate({c.typed_in(n,'items','any')}, {vs.literal('text',kind)}, {fn}, {params}, {c.typed_in(n,'limit','num')})"


def output(n, socket, table):
    if socket not in FIELDS and not (n.kind == "CAND_STEP" and socket in STEP_FIELDS):
        raise ValueError("Unknown candidate result output")
    if n.kind == "CAND_STEP": return table + "." + socket
    return table if socket == "results" else table + "." + socket


def stored_output(n, socket, key):
    defaults = dict(FIELDS, **STEP_FIELDS) if n.kind == "CAND_STEP" else FIELDS
    return f'(("{key}" in ::HL_R) ? {output(n,socket,"::HL_R[" + chr(34) + key + chr(34) + "]")} : {defaults[socket]})'


def ambient_expression(c, n, socket):
    from .logic import LNode
    query = LNode(n.id, "WQ_AMBIENT", n.settings, consts=n.consts)
    if socket not in {"available", "brightness", "color", "fallback"}:
        raise ValueError("Unknown ambient-light output")
    return "(" + worldqueries.call(c, query) + ")." + socket
