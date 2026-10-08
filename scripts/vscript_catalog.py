"""Build hammerless/core/data/vscript_api.json: L4D2's script API as the game itself reports it.

    python scripts/vscript_catalog.py dump      # starts L4D2 in developer mode on a test map, saves script_help.txt
    python scripts/vscript_catalog.py build     # script_help.txt + the game's event files -> the catalogue

`script_help` lists every documented native function with its signature, but only when the game
was started with developer mode on (the developer cvar is hidden in the retail game). Game events
and their fields come from resource/*events.res in the game's VPKs. The catalogue keeps names,
signatures and field types (facts about the game's interface); descriptions are our own, from
scripts/vscript_descriptions.txt ("Name: text" lines), not Valve's help text.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "hammerless", "core", "data", "vscript_api.json")
HELP = os.path.join(ROOT, "scripts", "vscript_help.txt")              # dump output (not committed)
DESCRIPTIONS = os.path.join(ROOT, "scripts", "vscript_descriptions.txt")

# script class -> (how it's called, what it acts on, menu group)
OWNERS = {
    "CDirector": ("Director", None, "Director"),
    "CNavMesh": ("NavMesh", None, "Nav Mesh"),
    "TerrorNavArea": (None, "area", "Nav Area"),
    "CEntities": ("Entities", None, "Find Entities"),
    "CNetPropManager": ("NetProps", None, "Entity Properties"),
    "Convars": ("Convars", None, "Console Variables"),
    "CTerrorPlayer": (None, "player", "Player"),
    "CBaseEntity": (None, "entity", "Entity"),
    "CBaseAnimating": (None, "entity", "Entity"),
    "CBaseFlex": (None, "entity", "Entity"),
    "CScriptEntityOutputs": ("EntityOutputs", None, "Entity Outputs"),
    "CScriptResponseCriteria": ("ResponseCriteria", None, "Speech"),
}
SKIP_OWNERS = {"Decider"}
SKIP = re.compile(r"^(__|ScriptDebug|RegisterFunctionDocumentation|Document$|PrintHelp$|AddToScriptHelp$|"
                  r"RetrieveNativeSignature$|GetFunctionSignature$|DumpObject$)")
# global functions -> menu group, by name
GLOBAL_GROUPS = [
    (r"^Debug", "Debug Drawing"),
    (r"^HUD", "HUD"),
    (r"^Screen", "Screen Effects"),
    (r"Scavenge|Survival|Versus|Difficulty|Mission|Dedicated|MOTD", "Game Mode"),
    (r"^(Save|Restore|Clear)\w*Table|InvTable", "Saving Data"),
    (r"^rr_|Speak", "Speech"),
    (r"Rush|Assault", "Infected"),
    (r"^Drop|Spawn|ZSpawn|CreateProp|CreateSceneEntity|Precache", "Spawning"),
    (r"ListenServerHost", "Players & Survivors"),
    (r"Phys|Friction|ModelIndex|Pickup|PickupObject|Rotate", "Entities"),
    (r"Rescue", "Players & Survivors"),
    (r"IncludeScript|FireGameEvent|RegisterScriptGameEventListener|ShowMessage", "Scripts & Events"),
    (r"Flow|Survivor|Player|Bot|Infected|Zombie|Character", "Players & Survivors"),
    (r"Nav|Path", "Nav Mesh"),
    (r"Ent(Fire|ity)|DoEntFire|EntIndex|Entities", "Entities"),
    (r"Trace|Line|Raycast", "Tracing"),
    (r"Time|Frame|Tick|Think", "Timing"),
    (r"Random|Rand", "Random"),
    (r"Print|Msg|Say|Chat|Hint|Console|Send", "Text & Console"),
    (r"Sound|Music|Emit", "Sound"),
    (r"File|String|Format", "Files & Text"),
    (r"Vector|Angle|Quat|Vec", "Vectors"),
    (r"Debug", "Debug Drawing"),
]
TYPE_ALIASES = {"<unknown>": "any", "function": "any", "variant": "any", "HSCRIPT": "handle"}
# functions whose help lists parameter names instead of types (the game rejects a null delay)
PARAM_TYPES = {"EntFire": ["string", "string", "string", "float", "handle"]}


def dump():
    """Start the game with developer mode on a map and capture `script_help` from console.log."""
    from hammerless.core import compile as cc
    tools = cc.Tools(cc.find_game_root())
    if cc.game_running():
        sys.exit("Close Left 4 Dead 2 first: developer mode only takes effect when the game starts")
    maps = [n[:-4] for n in os.listdir(tools.maps_dir) if n.startswith("hl_test") and n.endswith(".bsp")]
    if not maps:
        sys.exit("No hl_test_* map in the game's maps folder: build one first")
    log = os.path.join(tools.gamedir, "console.log")
    start = os.path.getsize(log) if os.path.exists(log) else 0
    cc.launch_game(tools, maps[0], extra=["-dev", "+developer", "1"])

    def text(since):
        with open(log, "rb") as f:
            f.seek(since)
            return f.read().decode("latin-1", "replace")
    t0 = time.time()
    while time.time() - t0 < 300 and not (os.path.exists(log) and "Redownloading all lightmaps" in text(start)):
        time.sleep(2)
    time.sleep(10)
    mark = os.path.getsize(log)
    cc.send_commands(tools, ["echo HL_DUMP_BEGIN", "script_help", "echo HL_DUMP_END"])
    for _ in range(90):
        time.sleep(2)
        if "HL_DUMP_END" in text(mark):
            break
    time.sleep(3)
    out = text(mark)
    with open(HELP, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"{out.count('Function:')} functions -> {HELP} (close the game when done)")


def _type(t: str) -> str:
    t = t.strip()
    return TYPE_ALIASES.get(t, t)


def _group(name: str) -> str:
    for pattern, group in GLOBAL_GROUPS:
        if re.search(pattern, name):
            return group
    return "Other"


def _events() -> list[dict]:
    """Game events and their fields (with the comment the game's file gives each)."""
    from hammerless.core import compile as cc
    from hammerless.core.vpk import GameContent
    game = GameContent(cc.find_game_root())
    found: dict[str, dict] = {}
    for path in ("resource/serverevents.res", "resource/gameevents.res", "resource/modevents.res"):
        data = game.read(path)
        if not data:
            continue
        text = data.decode("latin-1")
        for m in re.finditer(r'"([A-Za-z0-9_]+)"\s*(//[^\n]*)?\s*\{([^{}]*)\}', text):
            fields = []
            for fm in re.finditer(r'"([A-Za-z0-9_]+)"\s+"([a-z]+)"[ \t]*(//[^\n]*)?', m.group(3)):
                name, ftype, note = fm.group(1), fm.group(2), (fm.group(3) or "").lstrip("/ ").strip()
                entity = ftype in ("short", "long") and (re.search(r"(entid|entindex|entityid|infected_id)$", name) is not None
                                                         or re.match(r"entity (id|index)", note, re.I) is not None)
                player = ftype in ("short", "long") and not entity and (
                    name in ("userid", "attacker", "victim", "subject", "rescuer", "healer")
                    or re.search(r"user ?id", note, re.I) is not None)
                # (the file's comments only help tell players and entities apart: they aren't kept)
                fields.append({"name": name, "type": ftype, "player": bool(player), "entity": bool(entity)})
            found[m.group(1)] = {"name": m.group(1), "fields": fields}      # later files win, like the game
    return sorted(found.values(), key=lambda e: e["name"])


def build():
    with open(HELP, encoding="utf-8") as f:
        text = f.read()
    desc = {}       # id -> (input names, description)
    if os.path.exists(DESCRIPTIONS):
        with open(DESCRIPTIONS, encoding="utf-8") as f:
            for line in f:
                if line.count("|") >= 2 and not line.startswith("#"):
                    fid, names, about = (x.strip() for x in line.split("|", 2))
                    desc[fid] = ([n.strip() for n in names.split(",")] if names else [], about)
    functions = []
    for m in re.finditer(r"Function:\s+(\S+)\s*\nSignature:\s+(.*?)\s*\n", text):
        full, sig = m.groups()
        sm = re.match(r"^\s*(\S+)\s+([\w:]+)\((.*)\)\s*$", sig)
        if not sm:
            continue
        ret, _n, args = sm.groups()
        owner, name = full.split("::") if "::" in full else ("", full)
        if owner in SKIP_OWNERS or SKIP.match(name):
            continue
        params = PARAM_TYPES.get(full) or [_type(a) for a in args.split(",") if a.strip()]
        if owner:
            if owner not in OWNERS:
                continue
            obj, on, group = OWNERS[owner]
            call = f"{obj}.{name}" if obj else name
        else:
            on, group, call = None, _group(name), name
        fid = f"{on}:{name}" if on else call
        if any(f["id"] == fid for f in functions):          # the same method on two script classes
            continue
        entry = {"id": fid, "name": name, "call": call, "on": on, "returns": _type(ret), "params": params,
                 "group": group, "desc": "", "names": [], "tables": [], "fills": None}
        if fid in desc:
            names, entry["desc"] = desc[fid]
            if len(names) != len(params):
                print(f"!! {fid}: {len(names)} input names for {len(params)} inputs ({', '.join(params)})")
            else:
                for i, n in enumerate(names):
                    if n == "=fills":
                        entry["fills"] = i
                        n = "Result"
                    elif n.endswith("=table"):
                        entry["tables"].append(i)
                        n = n[:-len("=table")]
                    entry["names"].append(n)
        else:
            print(f"-- no description: {fid}")
        functions.append(entry)
    functions.sort(key=lambda f: (f["group"], f["name"]))
    events = _events()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"source": "L4D2 script_help + resource/*events.res", "functions": functions, "events": events},
                  f, indent=0, separators=(",", ":"))
    print(f"{len(functions)} functions, {len(events)} events -> {OUT}")


if __name__ == "__main__":
    {"dump": dump, "build": build}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
