"""Extra files a map needs in the game folder (VScripts), generated from the MapIR.

All paths are relative to the game dir (left4dead2/) and live under
scripts/vscripts/hammerless/ so they never collide with Valve's files.
Scripts are run by entities in the map (logic_script, info_director inputs), so
they work without sv_cheats.
"""
from __future__ import annotations

from .ir import MapIR
from .nav import collect_climbs, collect_regions, navmark_script

SCRIPT_DIR = "scripts/vscripts/hammerless"


def script_path(name: str) -> str:
    """Path as used by entities / inputs (relative to scripts/vscripts, no extension)."""
    return f"hammerless/{name}"


# The Director's script inputs (BeginScript, ScriptedPanicEvent) only look in the top
# scripts/vscripts folder - a sub-folder path silently does nothing (verified in-game).
# These scripts get map-specific names there instead.
def director_input_script(map_name: str, name: str) -> str:
    # (with a short hash of both: map "a" + "b_director" and map "a_b" + "director" would share a file)
    import hashlib
    tag = hashlib.sha1(f"{map_name}\0{name}".encode("utf-8")).hexdigest()[:6]
    return f"hammerless_{map_name}_{name}_{tag}"


# ---------------------------------------------------------------- debug log

DEBUG_SCRIPT = r'''// Hammerless debug log: prints Director events and stats to the console.
// Added to maps built with "Debug Log" enabled. Lines start with HAMMERLESS_DEBUG.
// Runs from a logic_script entity, so it needs no cheats.

::HL_DebugNext <- 0.0;
::HL_DebugInterval <- %INTERVAL%;

function HL_Log(msg) { printl("HAMMERLESS_DEBUG " + Time().tointeger() + "s " + msg); }

function HL_Think() {
    if (Time() < ::HL_DebugNext) return;
    ::HL_DebugNext = Time() + ::HL_DebugInterval;
    local parts = "stats commons=" + Director.GetCommonInfectedCount()
        + " furthest_flow=" + Director.GetFurthestSurvivorFlow().tointeger()
        + " left_safe_area=" + Director.HasAnySurvivorLeftSafeArea();
    local p = null;
    while ((p = Entities.FindByClassname(p, "player")) != null) {
        if (!p.IsSurvivor()) {
            if (p.GetZombieType && p.GetZombieType() > 0 && !p.IsDead()) parts += " SI:" + p.GetPlayerName();
            continue;
        }
        local o = p.GetOrigin();
        parts += " " + p.GetPlayerName() + "@" + o.x.tointeger() + "," + o.y.tointeger() + "," + o.z.tointeger()
              + "/hp" + p.GetHealth() + (p.IsIncapacitated() ? "/incap" : "") + (p.IsDead() ? "/dead" : "");
    }
    HL_Log(parts);
}

function OnGameEvent_player_left_start_area(params) { HL_Log("event player_left_start_area"); }
function OnGameEvent_player_left_checkpoint(params) { HL_Log("event player_left_checkpoint"); }
function OnGameEvent_player_entered_checkpoint(params) { HL_Log("event player_entered_checkpoint"); }
function OnGameEvent_create_panic_event(params) { HL_Log("event create_panic_event"); }
function OnGameEvent_panic_event_finished(params) { HL_Log("event panic_event_finished"); }
function OnGameEvent_door_open(params) { HL_Log("event door_open checkpoint=" + ("checkpoint" in params ? params.checkpoint : "?")); }
function OnGameEvent_door_close(params) { HL_Log("event door_close checkpoint=" + ("checkpoint" in params ? params.checkpoint : "?")); }
function OnGameEvent_tank_spawn(params) { HL_Log("event tank_spawn"); }
function OnGameEvent_witch_spawn(params) { HL_Log("event witch_spawn"); }
function OnGameEvent_witch_harasser_set(params) { HL_Log("event witch_startled"); }
function OnGameEvent_map_transition(params) { HL_Log("event map_transition"); }
function OnGameEvent_mission_lost(params) { HL_Log("event mission_lost"); }
function OnGameEvent_finale_start(params) { HL_Log("event finale_start"); }
function OnGameEvent_player_death(params) {
    if ("userid" in params) {
        local p = GetPlayerFromUserID(params.userid);
        if (p && p.IsSurvivor()) HL_Log("event survivor_death " + p.GetPlayerName());
    }
}

__CollectEventCallbacks(this, "OnGameEvent_", "GameEventCallbacks", RegisterScriptGameEventListener);
HL_Log("loaded map=" + Director.GetMapName());
'''


# ---------------------------------------------------------------- ready signal

READY_SCRIPT = r'''// Hammerless: prints HAMMERLESS_READY once the survivors have spawned.
// Build & Play waits for this before generating the nav mesh: nav_generate grows the
// mesh from where the players stand, so run too early (on a map with no nav mesh
// yet, survivors take ~15 s to appear) it fails with "No valid walkable seed positions".
::HLR_Done <- false;
function HLR_Think() {
    if (::HLR_Done) return;
    local p = null;
    while ((p = Entities.FindByClassname(p, "player")) != null) {
        if (p.IsSurvivor()) {
            ::HLR_Done = true;
            printl("HAMMERLESS_READY survivors spawned");
            HLR_FlowReport();
            return;
        }
    }
}

// Tells Build & Play whether the Director found the path from the start to the end safe
// room (the "flow"; without it no zombies wander). If not, walks the nav mesh from the
// start room and prints the reached spot closest to the end room: where the path breaks.
function HLR_FlowReport() {
    local areas = {};
    NavMesh.GetAllAreas(areas);
    if (areas.len() == 0) { printl("HAMMERLESS_FLOW nonav"); return; }
    local max = GetMaxFlowDistance();
    if (max > 0) { printl("HAMMERLESS_FLOW ok " + max); return; }
    local queue = [], seen = {}, goal = Vector(0, 0, 0), ends = 0;
    foreach (id, a in areas) {
        local f = a.GetSpawnAttributes();
        if (f & 128) { queue.append(a); seen[a.GetID()] <- true; }
        else if (f & 2048) { goal += a.GetCenter(); ends++; }
    }
    if (ends == 0 || queue.len() == 0) { printl("HAMMERLESS_FLOW noend"); return; }
    goal = goal * (1.0 / ends);
    local best = queue[0], bestD = 1e12, reachedEnd = false;
    while (queue.len() > 0) {
        local a = queue.pop();
        local f = a.GetSpawnAttributes();
        if ((f & 2048) && !(f & 128)) reachedEnd = true;
        local d = (a.GetCenter() - goal).Length();
        if (d < bestD) { bestD = d; best = a; }
        for (local dir = 0; dir < 4; dir++) {
            local adj = {};
            a.GetAdjacentAreas(dir, adj);
            foreach (k, b in adj) if (!(b.GetID() in seen)) { seen[b.GetID()] <- true; queue.append(b); }
        }
    }
    local c = best.GetCenter();
    printl("HAMMERLESS_FLOW broken " + (reachedEnd ? "connected" : "at") + " " + c.x + " " + c.y + " " + c.z);
}
'''


# ---------------------------------------------------------------- director options

DIRECTOR_OPTION_KEYS = [
    # (DirectorOptions key, MapSettings attribute)
    ("CommonLimit", "dir_common_limit"),
    ("MobMinSize", "dir_mob_min"),
    ("MobMaxSize", "dir_mob_max"),
    ("MobSpawnMinTime", "dir_mob_interval_min"),
    ("MobSpawnMaxTime", "dir_mob_interval_max"),
    ("MaxSpecials", "dir_max_specials"),
    ("SpecialRespawnInterval", "dir_special_interval"),
    ("TankLimit", "dir_tank_limit"),
    ("WitchLimit", "dir_witch_limit"),
]


DIRECTOR_ENTITY = "hammerless_director"


def director_script(ir: MapIR) -> str:
    """The map-wide Director settings, set as the map's own (DirectorScript.MapScript): a crescendo or a Director
    Settings node puts its own over them while it runs (LocalScript) and they're back when it ends. (Started with
    BeginScript they were the LocalScript, which a crescendo replaces and leaves empty: measured in game, the
    map ran on the Director's defaults after any crescendo.)"""
    lines = ["// Hammerless map-wide Director settings, from the Director panel in Blender. A logic_auto calls",
             "// HL_ApplyDirector() at map spawn (hammerless_director > RunScriptCode).",
             "::HL_ApplyDirector <- function() {",
             "    ::DirectorScript.MapScript.DirectorOptions <- {"]
    lines += ["    " + ln for ln in director_option_lines(ir)]
    lines += ["    };", '    printl("HAMMERLESS_DIRECTOR map options active");', "}"]
    return "\n".join(lines) + "\n"


def sq_text(text: str) -> str:
    """Text for inside a Squirrel "..." string literal (object names can hold quotes, backslashes...)."""
    return (str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " "))


def crescendo_key(name: str) -> str:
    """A crescendo's name as used for its script file (and to match the outputs that start it)."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name.strip().lower())


# ---------------------------------------------------------------- crescendo scripts

STAGE_TYPES = {"PANIC": 0, "TANK": 1, "DELAY": 2}


def director_option_lines(ir: MapIR) -> list[str]:
    s = ir.settings
    if not s.director_enabled:
        return []
    lines = [f"    {key} = {getattr(s, attr)}" for key, attr in DIRECTOR_OPTION_KEYS]
    for what, on in s.dir_spawns.items():       # Director Spawns switches: off = the map's logic decides
        if not on:
            key = f"{what.title()}Limit"
            lines = [l for l in lines if l.split("=")[0].strip() != key] + [f"    {key} = 0"]
    if s.dir_no_mobs:
        lines.append("    NoMobSpawns = true")
    if s.dir_no_wanderers:
        lines.append("    WanderingZombieDensityModifier = 0")
    return lines


def crescendo_options(ir: MapIR, stages) -> list[str]:
    """What a crescendo changes of the map-wide Director settings while it runs: the rest come through from the
    map's (DirectorScript.MapScript, director_script). No Random Hordes off (its PANIC waves are hordes), and the
    Tank limit raised to its TANK stage."""
    tanks = max((int(v) for kind, v in stages if kind == "TANK"), default=0)
    out = []
    for line in director_option_lines(ir):
        key, _, value = (p.strip() for p in line.partition("="))
        if key == "NoMobSpawns":
            out.append("    NoMobSpawns = false")
        elif key == "TankLimit" and tanks:
            try:
                if int(value) < tanks:
                    out.append(f"    TankLimit = {tanks}")
            except ValueError:
                pass
    return out


def crescendo_script(name: str, stages: list[tuple[str, float]], extra_options: list[str] | None = None) -> str:
    """ScriptedPanicEvent script: stages like [("PANIC", 1), ("DELAY", 10), ("PANIC", 2)].
    PANIC value = number of hordes, DELAY value = seconds, TANK value = number of tanks."""
    lines = [f"// Hammerless crescendo '{name}'. Started by an output: director > ScriptedPanicEvent > {name}",
             "PANIC <- 0", "TANK <- 1", "DELAY <- 2", "", "DirectorOptions <-", "{",
             f"    A_CustomFinale_StageCount = {len(stages)}"]
    for i, (kind, value) in enumerate(stages, start=1):
        lines.append(f"    A_CustomFinale{i} = {kind}")
        lines.append(f"    A_CustomFinaleValue{i} = {int(value) if float(value).is_integer() else value}")
    if extra_options:
        # (over the map-wide settings, which come through for everything not set here)
        lines.append("    // changed from the map-wide Director settings while it runs")
        lines += extra_options
    lines += ["}", f'printl("HAMMERLESS_CRESCENDO {name} started");']
    return "\n".join(lines) + "\n"


def parse_stages(text: str) -> tuple[list[tuple[str, float]], list[str]]:
    stages, problems = [], []
    for chunk in text.replace(";", ",").split(","):
        parts = chunk.split()
        if not parts:
            continue
        kind = parts[0].upper()
        if kind not in STAGE_TYPES:
            problems.append(f"unknown stage '{parts[0]}' (use PANIC, DELAY or TANK)")
            continue
        try:
            value = float(parts[1]) if len(parts) > 1 else 1.0
        except ValueError:
            problems.append(f"stage '{chunk.strip()}' needs a number")
            continue
        import math
        if not math.isfinite(value) or value < 0 or value > 3600:
            problems.append(f"stage '{chunk.strip()}' needs a number from 0 to 3600")
            continue
        if kind in ("PANIC", "TANK") and not value.is_integer():
            problems.append(f"stage '{chunk.strip()}' needs a whole number")
            continue
        stages.append((kind, value))
    if not stages:
        problems.append("has no stages")
    return stages, problems


def collect_crescendos(ir: MapIR) -> list[str]:
    """Fill ir.crescendos from crescendo pseudo-entities. Returns problems."""
    from .entities import CRESCENDO
    problems = []
    for e in ir.entities:
        if e.classname != CRESCENDO:
            continue
        name = crescendo_key(e.keyvalues.get("name", "crescendo"))
        if name == "director":
            problems.append("A crescendo can't be named 'director' (that name is the map's Director settings): "
                            "rename it")
            continue
        stages, probs = parse_stages(e.keyvalues.get("stages", ""))
        problems += [f"Crescendo '{name}' {p}" for p in probs]
        if name in ir.crescendos:
            problems.append(f"Two crescendos are named '{name}'")
        ir.crescendos[name] = stages
    return problems


# ---------------------------------------------------------------- Hammerless's game mode (scripted mode)

def mode_files(ir: MapIR) -> dict[str, str]:
    """Override hooks and the custom HUD only work in scripted mode, which the game turns on for a mode
    with its own script (measured: plain co-op never calls the hooks or shows the HUD). Maps that use
    them get Hammerless's mode: co-op plus an empty mode script, and the map's <map>_hammerless.nut,
    which scripted mode loads as it starts and where the game looks for the hooks. maps/<map>.hlmode
    tells Build & Play which mode to start the map in."""
    from .director_options import GAME_MODE, HOOKS_BY_NAME
    name = ir.settings.name
    if not ir.scripted_mode:
        return {f"maps/{name}.hlmode": "coop\n"}
    files = {f"maps/{name}.hlmode": GAME_MODE + "\n"}
    mode, options = [], []
    for hook in sorted(ir.logic_hooks):
        _n, where, args, answer, _about = HOOKS_BY_NAME[hook]
        params = "dt" if hook == "AllowTakeDamage" else ", ".join(a for a, _k in args)
        fallback = {"AllowTakeDamage": "true", "ShouldAvoidItem": "false", "CanPickupObject": "false", "ConvertWeaponSpawn": "0",
                    "GetDefaultItem": "0", "ConvertZombieClass": args[0][0] if args else "0"}.get(hook, "true")
        # only this map's logic answers (the game keeps scripts' globals from map to map), and in the
        # logic script's own scope, where the nodes' functions and helpers live
        ready = f'("HL_Map" in getroottable()) && ::HL_Map == "{name}" && ("HL_Hook_{hook}" in getroottable())'
        call = f'::HL_Hook_{hook}.call(::HL_Scope{", " + params if params else ""})'
        body = f'return ({ready}) ? {call} : {fallback};' if answer else f'if ({ready}) {call};'
        (mode if where == "mode" else options).append(f"function {hook}({params}) {{ {body} }}")
    text = f"// Hammerless: {name}'s rules for its scripted mode (the logic graph's Override nodes)\n"
    # the game asks some questions (weapon spawns, starting items) while it creates the map's entities,
    # before any entity's script runs (measured): load the map's logic now, into its own table
    text += ("::HL_EarlyScope <- {};\n"
             f'IncludeScript("hammerless/logic_{name}", ::HL_EarlyScope);\n')
    text += "".join(m + "\n" for m in mode)
    if options:
        text += "MapOptions <- {\n" + "".join("    " + o + "\n" for o in options) + "}\n"
    files[f"scripts/vscripts/{name}_{GAME_MODE}.nut"] = text
    return files


MODE_ADDON = "addons/hammerless_mode.vpk"


def mode_addon_files() -> dict[str, bytes]:
    """Hammerless's game mode as the files of a small addon (MODE_ADDON under left4dead2/): the game
    only learns modes from its packed files and addon VPKs, when it starts (measured: a loose
    modes/*.txt is ignored, and so is the mode on the command line)."""
    from .director_options import GAME_MODE
    mode = (f'"{GAME_MODE}"\n{{\n\t"base"\t\t"coop"\n\t"maxplayers"\t"4"\n\t"hasdifficulty"\t"1"\n'
            '\t"DisplayTitle"\t"Co-op (Hammerless)"\n'
            "\t\"ShortDescription\"\t\"Co-op with the map's own rules and HUD.\"\n"
            "\t\"Description\"\t\"Co-op, with the custom rules and HUD the map's Hammerless logic sets up.\"\n"
            '\t"Image"\t\t"maps/any"\n\t"Author"\t"Hammerless"\n}\n')
    script = ("// Hammerless's game mode: co-op in scripted mode, so maps can override the game's decisions\n"
              "// (damage, weapon spawns, ...) and show a HUD. The map's rules are in <map>_hammerless.nut.\n"
              'MutationOptions <- {}\nprintl("HAMMERLESS_MODE scripted mode on");\n')
    info = ('"AddonInfo"\n{\n\t"addontitle"\t"Hammerless game mode"\n\t"addonversion"\t"1"\n'
            '\t"addonauthor"\t"Hammerless"\n'
            '\t"addonDescription"\t"Co-op in scripted mode, for maps made with Hammerless that use overrides or a HUD."\n'
            '\t"addonContent_Script"\t"1"\n}\n')
    return {f"modes/{GAME_MODE}.txt": mode.encode(), f"scripts/vscripts/{GAME_MODE}.nut": script.encode(),
            "addoninfo.txt": info.encode()}


# ---------------------------------------------------------------- all files

def game_files(ir: MapIR) -> dict[str, str]:
    """relative path (under left4dead2/) -> file contents."""
    s = ir.settings
    files: dict[str, str] = {}
    regions, _ = collect_regions(ir)
    climbs, _ = collect_climbs(ir)
    files[f"{SCRIPT_DIR}/navmark_{s.name}.nut"] = navmark_script(regions, s.name, climbs, s.wall_climbs)
    files[f"{SCRIPT_DIR}/ready.nut"] = READY_SCRIPT
    if s.debug_log:
        files[f"{SCRIPT_DIR}/debug_{s.name}.nut"] = DEBUG_SCRIPT.replace("%INTERVAL%", f"{s.debug_interval:.1f}")
    if s.director_enabled:
        files[f"scripts/vscripts/{director_input_script(s.name, 'director')}.nut"] = director_script(ir)
    if s.autotest:
        from .autotest import autotest_script, plan_route, start_door
        route, _ = plan_route(ir)
        files[f"{SCRIPT_DIR}/autotest_{s.name}.nut"] = autotest_script(route, s.name, start_door(ir))
    files.update(ir.extra_scripts)
    files.update(mode_files(ir))
    for name, stages in ir.crescendos.items():
        if f"scripts/vscripts/{director_input_script(s.name, name)}.nut" in ir.extra_scripts:
            continue          # a safety net: Director Settings nodes steer clear of crescendo names (core/logic.py)
        files[f"scripts/vscripts/{director_input_script(s.name, name)}.nut"] = crescendo_script(
            name, stages, crescendo_options(ir, stages))
    return files
