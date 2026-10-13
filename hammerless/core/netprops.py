"""The game's entity properties ("NetProps"): every property name scripts can read and write on an entity, with its
type and the entity classes that have it. data/netprops.json is built from the game itself by
scripts/netprop_catalog.py (the networked properties from a recorded demo's tables, the server-only ones and each
class's list by asking the running game)."""
from __future__ import annotations

import json
import os

_DATA = os.path.join(os.path.dirname(__file__), "data", "netprops.json")
_cache: dict | None = None

TYPES = ("int", "float", "vector", "string", "entity")
# a property's type -> the logic socket kind it reads/writes as
KINDS = {"int": "num", "float": "num", "vector": "vec", "string": "text", "entity": "thing"}
# ... and the letter the HL_GetProp / HL_SetProp script helpers take
LETTERS = {"int": "i", "float": "f", "vector": "v", "string": "s", "entity": "e"}


def props() -> dict[str, dict]:
    """name -> {"type", "size" (0: not a list), "classes", "networked", "desc"}."""
    global _cache
    if _cache is None:
        try:
            with open(_DATA, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        _cache = data.get("props", {})
        every = data.get("every_class", [])
        classes = data.get("classes", {})
        for p in _cache.values():
            p["classes"] = []
        for cls in sorted(classes):                     # (stored as: the names every class has, then each one's own)
            for name in every if cls in classes and not cls[:1].isupper() else ():
                _cache[name]["classes"].append(cls)
            for name in classes[cls]:
                _cache[name]["classes"].append(cls)
    return _cache


def info(name: str) -> dict | None:
    return props().get(name)


def type_of(name: str, custom_type: str = "int", custom_list: bool = False) -> tuple[str, bool]:
    """(type, is a list) for a property: the catalogue's, or what the node says for a name it doesn't know."""
    p = info(name)
    if p is not None:
        return p["type"], bool(p.get("size"))
    return (custom_type if custom_type in TYPES else "int"), custom_list
