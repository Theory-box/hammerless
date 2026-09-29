"""Read the game's entity definitions (.fgd): each entity class's inputs and outputs, with
their descriptions, following base classes. The logic node editor builds its sockets from
this, so every entity gets exactly the events the game supports.

The files ship with the game (bin/base.fgd, bin/left4dead2.fgd); nothing is copied into
the add-on.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

_HEADER = re.compile(r'@(\w+)([^=\[]*?)=\s*([\w]+)[^\[]*\[', re.S)
_BASES = re.compile(r'base\(([^)]*)\)')
_IO = re.compile(r'^\s*(input|output)\s+(\w+)\s*\(\s*(\w*)\s*\)\s*(?::\s*"((?:[^"]|"\s*\+\s*")*)")?', re.M)

# inputs/outputs every entity inherits that are rarely what a mapper wants in a graph
PLUMBING = {
    "AddOutput", "FireUser1", "FireUser2", "FireUser3", "FireUser4", "OnUser1", "OnUser2", "OnUser3",
    "OnUser4", "KillHierarchy", "SetParent", "SetParentAttachment", "SetParentAttachmentMaintainOffset",
    "ClearParent", "RunScriptFile", "RunScriptCode", "CallScriptFunction", "DispatchEffect",
    "DispatchResponse", "AddContext", "RemoveContext", "ClearContext", "SetDamageFilter", "Use",
    "SetLocalOrigin", "SetLocalAngles", "EnableDraw", "DisableDraw", "EnableReceivingFlashlight",
    "DisableReceivingFlashlight", "EnableDamageForces", "DisableDamageForces", "DisableShadow",
    "EnableShadow", "AlternativeSorting", "SetTeam", "OnKilled", "Alpha", "Color", "SetBodyGroup",
}


@dataclass
class IO:
    name: str
    kind: str            # "input" / "output"
    type: str            # void, string, float, integer, bool, target_destination...
    description: str = ""

    @property
    def takes_value(self) -> bool:
        return self.kind == "input" and self.type not in ("", "void")


@dataclass
class EntityClass:
    name: str
    bases: list[str] = field(default_factory=list)
    ios: list[IO] = field(default_factory=list)


def _bodies(text: str):
    """(header match, body text) for every @Class ... [ body ] block (bodies nest [ ] for choices)."""
    pos = 0
    while True:
        m = _HEADER.search(text, pos)
        if not m:
            return
        depth, i, in_str = 1, m.end(), False
        while i < len(text) and depth:
            c = text[i]
            if c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
            i += 1
        yield m, text[m.end():i - 1]
        pos = i


def parse(text: str, classes: dict[str, EntityClass] | None = None) -> dict[str, EntityClass]:
    classes = {} if classes is None else classes
    text = re.sub(r"//[^\n]*", "", text)
    for m, body in _bodies(text):
        name = m.group(3)
        bases = []
        b = _BASES.search(m.group(2))
        if b:
            bases = [x.strip() for x in b.group(1).split(",") if x.strip()]
        ec = EntityClass(name, bases)
        for io in _IO.finditer(body):
            desc = re.sub(r'"\s*\+\s*"', "", io.group(4) or "")
            ec.ios.append(IO(io.group(2), io.group(1), io.group(3).lower(), desc))
        classes[name] = ec
    return classes


def resolve(classes: dict[str, EntityClass], name: str, seen=None) -> list[IO]:
    """All inputs and outputs of a class, its own first, then inherited (no duplicates)."""
    seen = set() if seen is None else seen
    ec = classes.get(name)
    if ec is None or name in seen:
        return []
    seen.add(name)
    out, names = [], set()
    for io in ec.ios + [x for base in ec.bases for x in resolve(classes, base, seen)]:
        key = (io.kind, io.name)
        if key not in names:
            names.add(key)
            out.append(io)
    return out


_cache: dict = {}


def load(game_root: str | None) -> dict[str, EntityClass]:
    """Parse the game's base.fgd + left4dead2.fgd (cached). Empty when the game isn't found."""
    if not game_root:
        return {}
    if game_root in _cache:
        return _cache[game_root]
    classes: dict[str, EntityClass] = {}
    for f in ("base.fgd", "left4dead2.fgd"):
        path = os.path.join(game_root, "bin", f)
        try:
            with open(path, encoding="latin-1") as fh:
                parse(fh.read(), classes)
        except OSError:
            pass
    _cache[game_root] = classes
    return classes


def pretty(name: str) -> str:
    """OnFullyOpen -> On Fully Open, ForcePanicEvent -> Force Panic Event."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
