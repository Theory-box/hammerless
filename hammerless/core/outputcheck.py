"""Conservative I/O target checks: static evidence, runtime names, and import provenance."""
import re


def matches_any(pattern, names):
    pattern = pattern.lower()
    if "*" not in pattern:
        return pattern in names
    # Source uses *, not Python glob's ? or bracket patterns.
    regex = re.compile(re.escape(pattern).replace(r"\*", ".*"))
    return any(regex.fullmatch(name) for name in names)


def names_for(entities):
    names = {e.keyvalues.get("targetname", "").lower() for e in entities} - {""}
    classes = {e.classname.lower() for e in entities} | {"info_director"}
    names.add("director")
    runtime = set()
    for e in entities:
        # Explicit runtime renames, only when the AddOutput recipient exists.
        for o in e.outputs:
            if o.input.lower() == "addoutput" and matches_any(o.target, names | classes):
                parts = o.parameter.split(None, 1)
                if len(parts) == 2 and parts[0].lower() == "targetname":
                    runtime.add(parts[1].strip().lower())
    return names | classes, runtime


def fingerprint(e, o):
    # IDs prevent an unrelated new entity from inheriting an original entity's exemption.
    return (e.imported_id, e.classname, o.output, o.target, o.input, o.parameter, o.delay, o.times)


def unresolved(entities):
    names, runtime = names_for(entities)
    for e in entities:
        for o in e.outputs:
            if o.target.startswith("!") or matches_any(o.target, names | runtime):
                continue
            yield e, o


def check_outputs(entities, original=()):
    baseline = {fingerprint(e, o) for e, o in unresolved(original) if e.imported_id}
    inherited = []
    for e, o in unresolved(entities):
        if e.imported_id and fingerprint(e, o) in baseline:
            inherited.append(o.target)
            continue
        yield ("WARNING", f"'{e.source or e.classname}' output {o.output} targets '{o.target}', "
               "but no matching entity or declared runtime name was found in the exported map. "
               "Check the target's name and Map Collection membership, restore a removed entity, or verify "
               "that a script creates it before this event fires. Static validation cannot prove script-created targets are missing.",
               e.source)
    if inherited:
        targets = sorted(set(inherited), key=str.lower)
        yield ("INFO", f"Original map: {len(inherited)} output references to {len(targets)} targets were already "
               "unresolved in the imported source. They are unchanged references, not newly missing entities. "
               "They may be unused or created at runtime; this does not confirm they execute successfully. "
               "Targets: " + ", ".join(targets), "")
