"""Light Pattern presets as Behaviours: picking a Pattern on a light puts a shared preset Behaviour graph on it
(Map Start -> Fire Input SetPattern on Self), which the user can open and change (or Make Unique). Lights using one
preset graph share one name, so the map compiler gives them ONE light style between them (it stops at 32 switched
styles, native/hlvbsp/write.c); a light made unique gets its own.

The patterns are the game's own default light styles (Source SDK 2013 game/server/world.cpp, g_DefaultLightstyles):
the same flicker the old 'style' key gave, now as a graph's value."""
from __future__ import annotations

import bpy

PATTERNS = {
    "1": "mmnmmommommnonmmonqnmmo",
    "2": "abcdefghijklmnopqrstuvwxyzyxwvutsrqponmlkjihgfedcba",
    "3": "mmmmmaaaaammmmmaaaaaabcdefgabcdefg",
    "4": "mamamamamama",
    "5": "jklmnopqrstuvwxyzyxwvutsrqponmlkj",
    "6": "nmonqnmomnmomomno",
    "7": "mmmaaaabcdefgmmmmaaaammmaamm",
    "8": "mmmaaammmaaammmabcdefaaaammmmabcdefmmmaaaa",
    "9": "aaaaaaaazzzzzzzz",
    "10": "mmamammmmammamamaaamammma",
    "11": "abcdefghijklmnopqrrqponmlkjihgfedcba",
    "12": "mmnnmmnnnmmnn",
}
TAG = "hl_light_preset"          # on a preset graph: which pattern it is


def _label(pid: str) -> str:
    from .props import LIGHT_PATTERNS
    return next((label for v, label, _d in LIGHT_PATTERNS if v == pid), pid)


def preset_graph(pid: str):
    """The shared Behaviour graph for this pattern (made the first time)."""
    for g in bpy.data.node_groups:
        if g.get(TAG) == pid and getattr(g, "is_behaviour", False) and not g.get("hl_unique"):
            return g
    from .behaviours import new_behaviour_tree
    tree = new_behaviour_tree(f"Light Pattern: {_label(pid)}")
    tree[TAG] = pid
    me = next(n for n in tree.nodes if n.bl_idname == "HL_NodeSelf")
    me.location = (-200.0, -180.0)
    start = tree.nodes.new("HL_NodeMapStart")
    start.location = (-200.0, 60.0)
    fire = tree.nodes.new("HL_NodeFireInput")
    fire.location = (120.0, 0.0)
    fire.note = "the light's pattern"
    fire.inputs["Input"].value = "SetPattern"
    fire.inputs["Value"].value = PATTERNS[pid]
    tree.links.new(start.outputs["On Map Start"], fire.inputs["Run"])
    tree.links.new(me.outputs["Entity"], fire.inputs["Entity"])
    return tree


def preset_slot(obj):
    """The light's Light Pattern slot (a preset graph, or a copy of one made unique), or None."""
    return next((s for s in obj.hammerless.behaviours if s.graph is not None and s.graph.get(TAG)), None)


def apply_pattern(obj) -> None:
    """The light's Pattern dropdown -> its Light Pattern Behaviour: replaced (Steady and Custom: none)."""
    hs = obj.hammerless
    pid = hs.light_pattern
    slot = preset_slot(obj)
    if pid in PATTERNS:
        graph = preset_graph(pid)
        if slot is None:
            slot = hs.behaviours.add()
            hs.behaviours.move(len(hs.behaviours) - 1, 0)       # (first in the list)
            slot = hs.behaviours[0]
        slot.graph = graph
    elif slot is not None:
        i = next(i for i, s in enumerate(hs.behaviours) if s == slot)
        hs.behaviours.remove(i)
        hs.behaviours_index = max(0, min(hs.behaviours_index, len(hs.behaviours) - 1))


def shared_name(obj) -> str | None:
    """The name a light with a Light Pattern Behaviour shares with the others using that graph. None when the light
    must answer to its own name: Switchable, Starts Off, or wired into logic by itself (switching it would otherwise
    switch every light of the preset)."""
    slot = preset_slot(obj)
    hs = obj.hammerless
    if slot is None or hs.light_switchable or hs.light_start_off:
        return None
    from .extract import in_world_logic
    if in_world_logic(obj):
        return None
    import re
    return "hl_lightpattern_" + (re.sub(r"[^a-z0-9_]+", "_", slot.graph.name.lower()).strip("_") or "light")
