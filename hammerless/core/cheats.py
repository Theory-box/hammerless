"""What in a map's logic only works with cheats on (sv_cheats 1): found when the map is built, so the user knows.

A cheat-protected console setting or command does nothing in a normal game, unless the map turns cheats on
itself (a script can): then the game announces it, achievements are off for that game, and players can use
every cheat meanwhile. Public servers usually don't allow either. The names come from the game's own list
(data/cheat_commands.txt: the settings and commands its 'cvarlist' marks "cheat").
"""
from __future__ import annotations

import os
import re

_NAMES: set[str] | None = None
CONSOLE_FUNCTIONS = ("SendToConsole", "SendToServerConsole")


def names() -> set[str]:
    global _NAMES
    if _NAMES is None:
        path = os.path.join(os.path.dirname(__file__), "data", "cheat_commands.txt")
        try:
            with open(path, encoding="utf-8") as f:
                _NAMES = {ln.strip().lower() for ln in f if ln.strip() and not ln.startswith("#")}
        except OSError:
            _NAMES = set()
        _NAMES.add("sv_cheats")
    return _NAMES


def in_commands(text: str) -> list[str]:
    """The cheat settings / commands a console line uses ("a 1; b" -> the first word of each command)."""
    out = []
    for part in re.split(r"[;\n]", text or ""):
        words = part.split()
        if words and words[0].lower() in names() and words[0].lower() not in out:
            out.append(words[0].lower())
    return out


def in_code(code: str) -> list[str]:
    """The cheat names that appear as text in script code (in a SetValue or a console command)."""
    out = []
    for s in re.findall(r'"((?:[^"\\]|\\.)*)"', code or ""):
        for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", s):
            if w.lower() in names() and w.lower() not in out:
                out.append(w.lower())
    return out


def warning(where: str, used: list[str]) -> str:
    turns_on = "sv_cheats" in used
    rest = [u for u in used if u != "sv_cheats"]
    what = (f"turns cheats on (sv_cheats){' for ' + ', '.join(rest) if rest else ''}: the game says so to "
            "everyone, achievements are off for that game, and players can use any cheat meanwhile"
            if turns_on else
            f"uses {', '.join(rest)}, which only work with cheats on: in a normal game they do nothing")
    return f"{where} {what}. Public servers usually don't allow cheats"
