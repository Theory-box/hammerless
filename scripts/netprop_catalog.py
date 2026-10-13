"""Build hammerless/core/data/netprops.json: every entity property (NetProp) L4D2's scripts can read and write, with
its type and the entity classes that have it, as the game itself reports them.

    python scripts/netprop_catalog.py dump      # records a short demo in the game (hl_netprops.dem in left4dead2/)
    python scripts/netprop_catalog.py tables    # the demo's property tables -> scripts/netprop_tables.json
    python scripts/netprop_catalog.py harvest   # in the game: every .fgd class's names -> scripts/netprop_harvest.json
    python scripts/netprop_catalog.py build     # tables + harvest + scripts/netprop_descriptions.txt -> the catalogue
    python scripts/netprop_catalog.py check     # in the game: NetProps.HasProp on live entities for their names

There's no console command that lists them, but every demo starts with the server's full property tables (the
"SendTables" a client needs to read entity updates) and the list of server classes. L4D2's layout, measured from its
own demo (demo protocol 4, network protocol 2100): 4 split-screen command infos per packet (304 bytes); per property
5 bits type, name, 19 bits flags, 8 bits priority, then a table name (tables), an excluded table name (exclusions),
10 bits element count (arrays) or two floats and 6 bits bit count (numbers).

Names are flattened as NetProps takes them: a class's base classes' properties directly ("m_iHealth"), properties
of other nested tables with the table's property name in front ("m_Local.m_iHideHUD"; tables flagged collapsible,
and containers whose name isn't a member's, like "localdata", add no name), arrays by their own name
with a size (read and written with the ...Array functions and an index).
"""
from __future__ import annotations

import json
import os
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "hammerless", "core", "data", "netprops.json")
TABLES = os.path.join(ROOT, "scripts", "netprop_tables.json")          # the demo's tables (not committed)
DESCRIPTIONS = os.path.join(ROOT, "scripts", "netprop_descriptions.txt")
DEMO = "hl_netprops"
CMDINFO = 304                         # 4 x 76-byte split-screen command infos
TYPES = {0: "int", 1: "float", 2: "vector", 3: "vectorxy", 4: "string", 5: "array", 6: "table", 7: "int64"}
SPROP_EXCLUDE, SPROP_INSIDEARRAY, SPROP_COLLAPSIBLE = 1 << 6, 1 << 8, 1 << 11


class Bits:
    def __init__(self, data: bytes):
        self.n, self.p = int.from_bytes(data, "little"), 0

    def bits(self, k: int) -> int:
        v = (self.n >> self.p) & ((1 << k) - 1)
        self.p += k
        return v

    def string(self) -> str:
        out = bytearray()
        while (c := self.bits(8)) != 0:
            out.append(c)
        return out.decode("latin-1")

    def float32(self) -> float:
        return struct.unpack("<f", struct.pack("<I", self.bits(32)))[0]


def datatables(path: str) -> bytes:
    d = open(path, "rb").read()
    if d[:8] != b"HL2DEMO\0":
        raise ValueError(f"{path} isn't a demo")
    p = 1072
    while p < len(d):
        cmd = d[p]
        p += 6                                       # command, tick, player slot
        if cmd in (1, 2):                            # signon / packet
            p += CMDINFO + 8
            p += 4 + struct.unpack_from("<i", d, p)[0]
        elif cmd == 3:                               # sync tick
            pass
        elif cmd in (4, 9):                          # console command / string tables
            p += 4 + struct.unpack_from("<i", d, p)[0]
        elif cmd in (5, 8):                          # user command / custom data
            p += 8 + struct.unpack_from("<i", d, p + 4)[0]
        elif cmd == 6:                               # data tables
            n = struct.unpack_from("<i", d, p)[0]
            return d[p + 4:p + 4 + n]
        else:
            raise ValueError(f"unknown demo command {cmd} at {p}")
    raise ValueError("the demo has no data tables")


def parse(blob: bytes):
    b = Bits(blob)
    tables = {}
    while b.bits(1):
        b.bits(1)                                    # needs decoder
        name = b.string()
        props = []
        for _ in range(b.bits(10)):
            t = TYPES[b.bits(5)]
            prop = {"name": b.string(), "type": t}
            flags = b.bits(19)
            b.bits(8)                                # priority
            prop["flags"] = flags
            if t == "table":
                prop["table"] = b.string()
            elif flags & SPROP_EXCLUDE:
                prop["exclude"] = b.string()
            elif t == "array":
                prop["elements"] = b.bits(10)
            else:
                b.float32(), b.float32()
                prop["bits"] = b.bits(6)
            props.append(prop)
        tables[name] = props
    classes = []
    for _ in range(b.bits(16)):
        b.bits(16)                                   # class id
        classes.append((b.string(), b.string()))
    if len(blob) * 8 - b.p >= 8:
        raise ValueError(f"{len(blob) * 8 - b.p} bits left over: the layout is off")
    return tables, classes


def _excluded(tables, table, seen=None) -> set[tuple[str, str]]:
    """(table, property) pairs a table and its bases leave out."""
    out = set()
    for p in tables.get(table, []):
        if "exclude" in p:
            out.add((p["exclude"], p["name"]))
        elif p["type"] == "table" and p["name"] == "baseclass":
            out |= _excluded(tables, p["table"])
    return out


def flatten(tables, table, excluded, prefix="") -> dict[str, dict]:
    out = {}
    props = tables.get(table, [])
    for i, p in enumerate(props):
        if "exclude" in p or p["flags"] & SPROP_INSIDEARRAY or (table, p["name"]) in excluded:
            continue
        if p["type"] == "table":
            sub = tables.get(p["table"], [])
            if p["name"] == "baseclass" or p["flags"] & SPROP_COLLAPSIBLE or not p["name"].startswith("m_"):
                # (merged into this level: base classes, and containers like "localdata" NetProps looks inside)
                out.update(flatten(tables, p["table"], excluded, prefix))
            elif sub and all(s["name"].isdigit() for s in sub):         # an array of one kind ("000", "001"...)
                first = sub[0]
                kind = "entity" if first["type"] == "int" and first.get("bits") == 21 else first["type"]
                out[prefix + p["name"]] = {"type": kind, "size": len(sub)}
            else:
                out.update(flatten(tables, p["table"], excluded, prefix + p["name"] + "."))
        elif p["type"] == "array":
            elem = props[i - 1] if i and props[i - 1]["flags"] & SPROP_INSIDEARRAY else {"type": "int"}
            kind = "entity" if elem["type"] == "int" and elem.get("bits") == 21 else elem["type"]
            out[prefix + p["name"]] = {"type": kind, "size": p["elements"]}
        else:
            entry = {"type": p["type"]}
            if p["type"] == "int" and p.get("bits") == 21:
                entry["type"] = "entity"                              # entity handles: 11 index + 10 serial bits
            out[prefix + p["name"]] = entry
    return out


def server_classes(demo: str) -> dict:
    """Each server class's networked properties, from the demo's tables."""
    tables, classes = parse(datatables(demo))
    out = {"source": "the game's own property tables (a recorded demo)", "classes": {}}
    for cls, table in sorted(classes):
        props = flatten(tables, table, _excluded(tables, table))
        bases, t = [], table
        while True:
            base = next((p["table"] for p in tables.get(t, []) if p["type"] == "table" and p["name"] == "baseclass"),
                        None)
            if not base:
                break
            bases.append(base)
            t = base
        out["classes"][cls] = {"table": table, "bases": bases, "props": props}
    return out


GAME_TYPES = {"integer": "int", "float": "float", "Vector": "vector", "string": "string"}


def build() -> dict:
    """The catalogue: every name the game accepted on some class (the harvest), with its type (an integer that is
    an entity handle in the networked tables, or named m_hName, is an entity), whether it's a list (more than one
    slot; text only when the networked tables say so: a server-only text field reports its length), whether it's
    networked, and our description; plus networked m_ names on classes the harvest couldn't make (listed with their
    server class). Records the game version it came from."""
    import re
    import datetime
    tables = json.load(open(TABLES, encoding="utf-8"))["classes"]
    harvest = json.load(open(HARVEST, encoding="utf-8"))["classes"]
    networked = {}
    for cls, c in tables.items():
        for name, p in c["props"].items():
            networked.setdefault(name, p)
    desc = {}
    for line in open(DESCRIPTIONS, encoding="utf-8"):
        if line.strip() and not line.startswith("#") and ":" in line:
            k, v = line.split(":", 1)
            desc[k.strip()] = v.strip()
    props = {}
    for cls, found in sorted(harvest.items()):
        if not isinstance(found, list):
            continue
        for name, gtype, size in found:
            t = GAME_TYPES.get(gtype)
            if t is None:
                continue
            if t == "string":                      # (a server-only text field reports its length as its size)
                size = networked.get(name, {}).get("size", 0)
            p = props.setdefault(name, {"type": t, "size": size if size > 1 else 0, "classes": []})
            p["classes"].append(cls)
    for name, p in props.items():
        net = networked.get(name)
        p["networked"] = net is not None
        if p["type"] == "int" and ((net and net["type"] == "entity") or (net is None and re.match(r"m_h[A-Z]", name))):
            p["type"] = "entity"
    tried = {cls for cls, v in harvest.items() if isinstance(v, list)}
    for cls, c in tables.items():                       # (classes the harvest couldn't make: from the tables)
        for name, net in c["props"].items():
            if name in props or not name.startswith("m_"):         # (nested names merged without a prefix:
                continue                                           # not usable as written, and never checked)
            t = {"vectorxy": "vector", "int64": "int"}.get(net["type"], net["type"])
            if t not in GAME_TYPES.values() and t != "entity":
                continue
            p = props.setdefault(name, {"type": t, "size": net.get("size", 0), "classes": [], "networked": True})
            p["classes"].append(cls)
    for name, text in desc.items():
        if name in props:
            props[name]["desc"] = text
        else:
            print(f"  (described but not in the game's list: {name})")
    # stored compactly: the names every class has once, then each class's other names (props' class lists come back
    # from these when loaded, see core/netprops.py)
    by_class = {}
    for name, p in props.items():
        for cls in p.pop("classes"):
            by_class.setdefault(cls, []).append(name)
    every = set.intersection(*(set(v) for cls, v in by_class.items() if cls in tried)) if tried else set()
    return {"source": "the game itself: a recorded demo's property tables, and NetProps asked on every class",
            "game": game_version(), "built": datetime.date.today().isoformat(),
            "classes_checked": len(tried), "props": props,
            "every_class": sorted(every), "every_class_in": sorted(tried),
            "classes": {cls: sorted(set(v) - (every if cls in tried else set())) for cls, v in sorted(by_class.items())}}


def game_version() -> dict:
    """Which build of the game the catalogue came from (steam.inf; the demo's protocols): an update may change it."""
    out = {}
    try:
        for line in open(os.path.join(game_dir(), "steam.inf"), encoding="latin-1"):
            k, _, v = line.strip().partition("=")
            if k in ("PatchVersion", "NetworkVersion"):
                out[k] = v
    except OSError:
        pass
    try:
        with open(os.path.join(game_dir(), DEMO + ".dem"), "rb") as f:
            out["demo_protocol"], out["network_protocol"] = struct.unpack("<ii", f.read(16)[8:16])
    except (OSError, struct.error):
        pass
    return out


def game_dir() -> str:
    from hammerless.core.compile import Tools, find_game_root
    return Tools(find_game_root()).gamedir


def main(argv):
    what = argv[1] if len(argv) > 1 else "build"
    if what == "dump":
        from tests.ingame.bench import Bench
        with Bench() as b:
            b.load(argv[2] if len(argv) > 2 else "c1m1_hotel")
            b.console(f"record {DEMO}", wait=3.0)
            b.console("stop", wait=2.0)
            b.console("disconnect")
        print("recorded", os.path.join(game_dir(), DEMO + ".dem"))
    elif what == "tables":
        cat = server_classes(os.path.join(game_dir(), DEMO + ".dem"))
        with open(TABLES, "w", encoding="utf-8") as f:
            json.dump(cat, f, indent=0, sort_keys=True)
        n = sum(len(c["props"]) for c in cat["classes"].values())
        print(f"{len(cat['classes'])} server classes, {n} networked properties -> {TABLES}")
    elif what == "build":
        cat = build()
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(cat, f, separators=(",", ":"), sort_keys=True)
        print(f"{len(cat['props'])} properties, {cat['classes_checked']} classes checked in the game -> {OUT}")
    elif what == "harvest":
        harvest(argv[2] if len(argv) > 2 else "c1m1_hotel")
    elif what == "check":
        check(argv[2] if len(argv) > 2 else "c1m1_hotel")
    else:
        raise SystemExit(__doc__)


HARVEST = os.path.join(ROOT, "scripts", "netprop_harvest.json")      # harvest output (not committed)
# made by hand when a map loads, not by a script: making them mid-game can crash the game (the host player is sampled
# live instead)
SKIP = {"worldspawn", "player", "info_player_start", "info_survivor_position", "terror_gamerules", "terror_player_manager",
        "info_director", "info_changelevel", "env_fog_controller", "env_tonemap_controller", "sky_camera", "light_environment",
        "shadow_control", "env_sun", "info_game_event_proxy", "cs_team_manager",
        "func_areaportal", "func_areaportalwindow", "trigger_finale"}   # (measured: crash or hang the game)


def _candidates() -> list[str]:
    """Every name to try: the catalogue's networked names, and every "m_..." string in the game's server code
    (server-only fields NetProps also reads, like m_flLaggedMovementValue)."""
    import re
    names = set()
    for c in json.load(open(TABLES, encoding="utf-8"))["classes"].values():
        names |= set(c["props"])
    data = open(os.path.join(game_dir(), "bin", "server.dll"), "rb").read()
    names |= {m.group(1).decode() for m in re.finditer(rb"\x00(m_[A-Za-z0-9_.\[\]]{2,60})(?=\x00)", data)}
    return sorted(names)


def harvest(map_name: str) -> None:
    """In the game: one entity of every class the game's .fgd names, spawned out of the way and removed again,
    and NetProps.HasProp / GetPropType / GetPropArraySize for every candidate name.
    The host player stands in for "player". Writes scripts/netprop_harvest.json after every class (a crash loses
    one class; run again to carry on from there)."""
    from hammerless.core import fgd
    from hammerless.core.compile import find_game_root
    from tests.ingame.bench import Bench
    classes = sorted(n for n in fgd.load(find_game_root()) if n == n.lower() and n not in SKIP)
    cands = _candidates()
    try:
        result = json.load(open(HARVEST, encoding="utf-8"))["classes"]
    except (OSError, ValueError, KeyError):
        result = {}

    def save():
        with open(HARVEST, "w", encoding="utf-8") as f:
            json.dump({"candidates": len(cands), "classes": result}, f)

    probe = ("local out = []; foreach (n in ::HL_CAND) { if (NetProps.HasProp(e, n)) "
             "out.append([n, NetProps.GetPropType(e, n), NetProps.GetPropArraySize(e, n)]); } ")
    from hammerless.core import compile as cc

    def start(b):
        """(Re)start the game on the map and give it the names to try."""
        if cc.game_running():
            subprocess.run(["taskkill", "/F", "/IM", "left4dead2.exe"], capture_output=True)
            time.sleep(5)
        b.load(map_name)
        b.run("::HL_CAND <- []; return null;")
        for i in range(0, len(cands), 400):                  # (one request can't hold them all)
            b.run("::HL_CAND.extend(" + json.dumps(cands[i:i + 400]) + "); return null;")

    import subprocess
    with Bench() as b:
        start(b)
        if "player" not in result:
            result["player"] = b.run("local e = GetListenServerHost(); " + probe + "return out;", timeout=60)
            save()
        todo = [c for c in classes if c not in result]
        for i, cls in enumerate(todo):
            result[cls] = {"error": "crashed the game"}         # (stays if this one takes the game down)
            save()
            try:
                # (L4D2's scripts can't make an entity without spawning it: spawn one far below the map, with a
                # model for those that want one, read its properties and remove it)
                kv = '{origin = Vector(0, 0, -16000)}'
                if cls.startswith(("func_", "trigger_")):
                    kv = '{origin = Vector(0, 0, -16000), model = "*1"}'
                elif cls.startswith(("prop_", "weapon_", "item_", "physics_", "simple_", "point_prop")):
                    kv = '{origin = Vector(0, 0, -16000), model = "models/props_junk/gascan001a.mdl"}'
                result[cls] = b.run(f"local e = SpawnEntityFromTable({json.dumps(cls)}, {kv}); "
                                    "if (e == null) return null; " + probe + "e.Kill(); return out;", timeout=60)
            except Exception as ex:                          # (a class the game can't make, or a script error)
                result[cls] = {"error": str(ex)[:200]}
                if "no answer" in str(ex):                   # (it hung or crashed the game: start it again)
                    result[cls] = {"error": "hung or crashed the game"}
                    save()
                    start(b)
            save()
            if i % 25 == 0:
                print(f"{i}/{len(todo)} {cls}: {len(result[cls]) if isinstance(result[cls], list) else result[cls]}",
                      flush=True)
        b.console("disconnect")
    made = sum(1 for v in result.values() if isinstance(v, list))
    print(f"{made} of {len(result)} classes made, {len(cands)} names tried -> {HARVEST}")


def check(map_name: str) -> None:
    """Every live entity on a map: which catalogue class it is (the most derived class whose own properties it all
    has), then NetProps.HasProp for each of that class's names. Prints what the game didn't accept."""
    from tests.ingame.bench import Bench
    cat = json.load(open(TABLES, encoding="utf-8"))["classes"]
    by_table = {c["table"]: c for c in cat.values()}
    sig = {}
    for cls, c in cat.items():
        base = by_table.get(c["bases"][0]) if c["bases"] else None
        base_props = set(base["props"]) if base else set()
        own = [k for k in sorted(c["props"]) if k not in base_props and "[" not in k][:3]
        if own:
            sig[cls] = own
    depth = {cls: len(c["bases"]) for cls, c in cat.items()}
    with Bench() as b:
        b.load(map_name)
        b.run("::HL_SIG <- " + json.dumps(sig).replace('":', '" :') + "; return null;")
        ents = b.run("local out = []; local e = null; while (e = Entities.Next(e)) out.append(e.GetEntityIndex()); "
                     "return out;")
        seen, fails, tried = {}, {}, 0
        for idx in ents:
            found = b.run(f"local e = EntIndexToHScript({idx}); if (e == null) return null; local m = []; "
                          "foreach (cls, names in ::HL_SIG) { local ok = true; "
                          "foreach (n in names) { if (!NetProps.HasProp(e, n)) { ok = false; break; } } "
                          "if (ok) m.append(cls); } return [e.GetClassname(), m];")
            if not found or not found[1]:
                continue
            classname, cands = found
            cls = max(cands, key=lambda c: (depth[c], len(cat[c]["props"])))
            if cls in seen:
                continue
            seen[cls] = classname
            names = sorted(cat[cls]["props"])
            bad = b.run(f"local e = EntIndexToHScript({idx}); local bad = []; "
                        f"foreach (n in {json.dumps(names)}) {{ if (!NetProps.HasProp(e, n)) bad.append(n); }} "
                        "return bad;", timeout=30)
            tried += len(names)
            if bad:
                fails[cls] = bad
        dm = b.value('[NetProps.HasProp(GetListenServerHost(), "m_flLaggedMovementValue"), '
                     'NetProps.HasProp(GetListenServerHost(), "m_iName"), '
                     'NetProps.HasProp(GetListenServerHost(), "m_iMaxHealth")]')
        b.console("disconnect")
    nbad = sum(len(v) for v in fails.values())
    print(f"{len(seen)} classes on {map_name}: " + ", ".join(f"{c}={n}" for c, n in sorted(seen.items())))
    print(f"{tried - nbad} of {tried} names accepted by NetProps.HasProp")
    for cls, bad in sorted(fails.items()):
        print(f"  {cls}: {len(bad)} not accepted, e.g. {bad[:6]}")
    print("datamap fields (m_flLaggedMovementValue, m_iName) / networked (m_iMaxHealth):", dm)


if __name__ == "__main__":
    main(sys.argv)
