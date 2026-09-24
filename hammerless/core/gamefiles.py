"""Extra files a map needs in the game folder (VScripts), generated from the MapIR.

All paths are relative to the game dir (left4dead2/) and live under
scripts/vscripts/hammerless/ so they never collide with Valve's files.
Scripts are run by entities in the map (logic_script, info_director inputs), so
they work without sv_cheats.
"""
from __future__ import annotations

from .ir import MapIR
from .nav import collect_regions, navmark_script

SCRIPT_DIR = "scripts/vscripts/hammerless"


def script_path(name: str) -> str:
    """Path as used by entities / inputs (relative to scripts/vscripts, no extension)."""
    return f"hammerless/{name}"


# The Director's script inputs (BeginScript, ScriptedPanicEvent) only look in the top
# scripts/vscripts folder - a sub-folder path silently does nothing (verified in-game).
# These scripts get map-specific names there instead.
def director_input_script(map_name: str, name: str) -> str:
    return f"hammerless_{map_name}_{name}"


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


def director_script(ir: MapIR) -> str:
    s = ir.settings
    lines = ["// Hammerless map-wide Director settings. Started by a logic_auto at map spawn",
             "// (director > BeginScript). Values come from the Director panel in Blender.",
             "DirectorOptions <-", "{"]
    lines += director_option_lines(ir)
    lines += ["}", 'printl("HAMMERLESS_DIRECTOR map options active");']
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- crescendo scripts

STAGE_TYPES = {"PANIC": 0, "TANK": 1, "DELAY": 2}


def director_option_lines(ir: MapIR) -> list[str]:
    s = ir.settings
    if not s.director_enabled:
        return []
    lines = [f"    {key} = {getattr(s, attr)}" for key, attr in DIRECTOR_OPTION_KEYS]
    if s.dir_no_mobs:
        lines.append("    NoMobSpawns = true")
    if s.dir_no_wanderers:
        lines.append("    WanderingZombieDensityModifier = 0")
    return lines


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
        # a scripted event's options replace the map's while it runs, so repeat the
        # map-wide Director settings here (e.g. the common infected limit)
        lines.append("    // map-wide Director settings")
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
        name = "".join(c if c.isalnum() or c == "_" else "_" for c in e.keyvalues.get("name", "crescendo").lower())
        stages, probs = parse_stages(e.keyvalues.get("stages", ""))
        problems += [f"Crescendo '{name}' {p}" for p in probs]
        if name in ir.crescendos:
            problems.append(f"Two crescendos are named '{name}'")
        ir.crescendos[name] = stages
    return problems


# ---------------------------------------------------------------- all files

def game_files(ir: MapIR) -> dict[str, str]:
    """relative path (under left4dead2/) -> file contents."""
    s = ir.settings
    files: dict[str, str] = {}
    regions, _ = collect_regions(ir)
    files[f"{SCRIPT_DIR}/navmark_{s.name}.nut"] = navmark_script(regions, s.name)
    if s.debug_log:
        files[f"{SCRIPT_DIR}/debug_{s.name}.nut"] = DEBUG_SCRIPT.replace("%INTERVAL%", f"{s.debug_interval:.1f}")
    if s.director_enabled:
        files[f"scripts/vscripts/{director_input_script(s.name, 'director')}.nut"] = director_script(ir)
    if s.autotest:
        from .autotest import autotest_script, plan_route, start_door
        route, _ = plan_route(ir)
        files[f"{SCRIPT_DIR}/autotest_{s.name}.nut"] = autotest_script(route, s.name, start_door(ir))
    for name, stages in ir.crescendos.items():
        files[f"scripts/vscripts/{director_input_script(s.name, name)}.nut"] = crescendo_script(
            name, stages, director_option_lines(ir))
    return files
