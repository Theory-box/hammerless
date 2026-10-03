"""Bot walkthrough test: a map script that plays the level with the survivor bots.

Enabled with Debug > Bot Walkthrough Test. It needs no cheats: it's ordinary map
scripting (CommandABot for movement, EntFire for button presses / the end door).
Progress is printed as HAMMERLESS_AUTOTEST lines; combine with the Debug Log.

Route (derived from the map):
  1. every horde trigger (trigger_once/multiple with a ForcePanicEvent output), nearest first
  2. every button (func_button) -> pressed when the bots arrive
  3. the end safe room (info_changelevel volume) -> door closed once everyone is inside
The human player (nobody is playing it during the test) is moved along with the
bots. Survivors can't go down while the test runs: it checks that the level works,
not how hard it is.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import geometry as g
from .ir import MapIR


@dataclass
class Waypoint:
    kind: str                  # TRIGGER, BUTTON, END
    pos: tuple[float, float, float]
    label: str
    target: str = ""           # entity targetname to fire (buttons, end door)


from .gamefiles import sq_text  # noqa: E402


def _centre(entity):
    pts = [v for b in entity.brushes for f in b.faces for v in f.verts]
    (x0, y0, z0), (x1, y1, z1) = g.bounds(pts)
    return ((x0 + x1) / 2, (y0 + y1) / 2, z0 + 8)


def start_door(ir: MapIR) -> tuple[str, str]:
    """(start door name, name of an entity inside the start room to open the door away from)."""
    doors = [e for e in ir.entities if e.classname == "prop_door_rotating_checkpoint" and e.origin
             and e.keyvalues.get("targetname")]
    start = _start_spot(ir)
    door = min(doors, key=lambda e: g.length(g.sub(e.origin, start)), default=None)
    if door is None:
        return "", ""
    inside = [e for e in ir.entities if e.classname == "info_landmark" and e.origin and e.keyvalues.get("targetname")]
    inside.sort(key=lambda e: g.length(g.sub(e.origin, door.origin)))
    return door.keyvalues["targetname"], (inside[0].keyvalues["targetname"] if inside else "")


def _start_spot(ir: MapIR):
    return next((e.origin for e in ir.entities if e.classname in ("info_survivor_position", "info_player_start")
                 and e.origin is not None), (0.0, 0.0, 0.0))


def plan_route(ir: MapIR) -> tuple[list[Waypoint], list[str]]:
    """Returns (waypoints, entities that need a targetname) - buttons get names assigned."""
    start = next((e.origin for e in ir.entities if e.classname in ("info_survivor_position", "info_player_start")
                  and e.origin is not None), (0.0, 0.0, 0.0))
    points: list[Waypoint] = []
    for i, e in enumerate(ir.entities):
        if not e.brushes:
            continue
        if e.classname in ("trigger_once", "trigger_multiple") and any(
                o.input.lower() in ("forcepanicevent", "scriptedpanicevent") for o in e.outputs):
            points.append(Waypoint("TRIGGER", _centre(e), e.source or e.classname))
        elif e.classname == "func_button":
            name = e.keyvalues.get("targetname") or f"hammerless_autotest_button_{i}"
            e.keyvalues["targetname"] = name
            points.append(Waypoint("BUTTON", _centre(e), e.source or "button", name))
    points.sort(key=lambda w: g.length(g.sub(w.pos, start)))
    end = next((e for e in ir.entities if e.classname == "info_changelevel" and e.brushes), None)
    if end is not None:
        doors = [e for e in ir.entities if e.classname == "prop_door_rotating_checkpoint" and e.origin
                 and e.keyvalues.get("targetname")]
        start_name = start_door(ir)[0]
        doors = [e for e in doors if e.keyvalues["targetname"] != start_name] or doors
        centre = _centre(end)
        door = min(doors, key=lambda e: g.length(g.sub(e.origin, centre)), default=None)
        points.append(Waypoint("END", centre, "end safe room", door.keyvalues["targetname"] if door else ""))
    return points, []


def autotest_script(waypoints: list[Waypoint], map_name: str, start_door_name: tuple[str, str] = ("", "")) -> str:
    rows = ",\n".join(
        f'    {{ kind = "{w.kind}", pos = Vector({w.pos[0]:.0f}, {w.pos[1]:.0f}, {w.pos[2]:.0f}), '
        f'label = "{sq_text(w.label)}", target = "{sq_text(w.target)}" }}' for w in waypoints)
    return (AUTOTEST_TEMPLATE.replace("%MAP%", map_name).replace("%WAYPOINTS%", rows)
            .replace("%START_DOOR%", start_door_name[0]).replace("%DOOR_AWAY_FROM%", start_door_name[1]))


AUTOTEST_TEMPLATE = r'''// Hammerless bot walkthrough test for %MAP%. No cheats: map scripting only.
// Lines start with HAMMERLESS_AUTOTEST.

::HLT <- {
    route = [
%WAYPOINTS%
    ],
    start_door = "%START_DOOR%",
    door_away_from = "%DOOR_AWAY_FROM%",   // entity inside the start room
    step = -1,
    next_order = 0.0,
    step_started = 0.0,
    arrived_at = -1.0,
    start_delay = 10.0,        // seconds after survivors first appear
    first_seen = -1.0,
    human_active = false,      // someone took control of the human survivor
    human_last = null,         // where the human was after our last check / teleport
    panic_active = false,
    panic_time = -100.0,
    panics = 0,
    door_tries = 0,
};

function HLT_Log(msg) { printl("HAMMERLESS_AUTOTEST " + Time().tointeger() + "s " + msg); }

function HLT_Survivors() {
    local list = []; local p = null;
    while ((p = Entities.FindByClassname(p, "player")) != null)
        if (p.IsSurvivor() && !p.IsDead()) list.append(p);
    return list;
}

function HLT_Ground(pos) {
    // a button sits in a wall: stand on the walkable nav nearest to it instead
    local a = NavMesh.GetNearestNavArea(pos, 256.0, false, false);
    return a != null ? a.GetCenter() : pos;
}

function HLT_Order(wp) {
    local i = 0;
    local base = wp.kind == "BUTTON" ? HLT_Ground(wp.pos) : wp.pos;
    foreach (p in HLT_Survivors()) {
        local spot = base + Vector((i % 2) * 48 - 24, (i / 2) * 48 - 24, 0);
        if (IsPlayerABot(p)) {
            CommandABot({ cmd = 1, pos = spot, bot = p });   // BOT_CMD_MOVE
        } else if (!::HLT.human_active && (p.GetOrigin() - spot).Length() > 400) {
            // nobody is playing the human survivor: bring it along so "all survivors"
            // conditions (like the end door) can be met
            p.SetOrigin(spot + Vector(0, 0, 16));
            ::HLT.human_last = p.GetOrigin();
        }
        i++;
    }
}

function HLT_AllNear(pos, radius) {
    if (HLT_Survivors().len() == 0) return false;
    foreach (p in HLT_Survivors())
        if ((p.GetOrigin() - pos).Length2D() > radius) return false;
    return true;
}

function HLT_Begin(i) {
    ::HLT.step = i;
    ::HLT.step_started = Time();
    ::HLT.arrived_at = -1.0;
    if (i >= ::HLT.route.len()) { HLT_Log("route finished"); return; }
    local wp = ::HLT.route[i];
    HLT_Log("step " + (i + 1) + "/" + ::HLT.route.len() + " " + wp.kind + " -> " + wp.label);
    HLT_Order(wp);
}

function HLT_KeepSurvivorsAlive() {
    // The test checks that the level WORKS (triggers, buttons, crescendos, transition),
    // not how hard it is, so survivors can't go down while it runs. Test mode only.
    foreach (p in HLT_Survivors()) {
        if (p.IsIncapacitated()) p.ReviveFromIncap();
        if (p.GetHealth() < 60) p.SetHealth(100);
    }
}

function HLT_WatchHuman() {
    // If the human survivor moves by itself, a person is playing: stop teleporting it.
    if (::HLT.human_active) return;
    foreach (p in HLT_Survivors()) {
        if (IsPlayerABot(p)) continue;
        local o = p.GetOrigin();
        if (::HLT.human_last != null && (o - ::HLT.human_last).Length2D() > 48 && p.GetVelocity().Length2D() > 20) {
            ::HLT.human_active = true;
            HLT_Log("a human is playing: no longer moving them (walk along, or finish the map yourself)");
        }
        ::HLT.human_last = o;
    }
}

function HLT_Think() {
    local now = Time();
    HLT_KeepSurvivorsAlive();
    HLT_WatchHuman();
    if (::HLT.step == -1) {
        if (HLT_Survivors().len() == 0) return;
        if (::HLT.first_seen < 0) ::HLT.first_seen = now;
        if (now < ::HLT.first_seen + ::HLT.start_delay) return;
        HLT_Log("start: " + ::HLT.route.len() + " waypoint(s), " + HLT_Survivors().len() + " survivor(s)");
        if (::HLT.start_door != "") {
            // bots on a move order don't open the start safe room door themselves
            // swing it outward, as when a survivor inside opens it (a plain Open can
            // swing it into the room and pin bots behind it)
            HLT_Log("opening " + ::HLT.start_door + " (stands in for a player's Use key)");
            if (::HLT.door_away_from != "") EntFire(::HLT.start_door, "OpenAwayFrom", ::HLT.door_away_from, 0.0);
            else EntFire(::HLT.start_door, "Open", "", 0.0);
        }
        HLT_Begin(0);
        return;
    }
    if (::HLT.step >= ::HLT.route.len()) return;
    local wp = ::HLT.route[::HLT.step];
    local waited = now - ::HLT.step_started;

    if (now >= ::HLT.next_order) {          // re-issue orders: bots stop to fight
        ::HLT.next_order = now + 4.0;
        if (::HLT.arrived_at < 0) HLT_Order(wp);
    }
    if (::HLT.arrived_at < 0) {
        if (HLT_AllNear(wp.pos, wp.kind == "END" ? 150 : 260)) {
            ::HLT.arrived_at = now;
            HLT_Log("arrived at " + wp.label + " after " + waited.tointeger() + "s");
            if (wp.kind == "BUTTON" && wp.target != "") {
                HLT_Log("pressing " + wp.target + " (stands in for a player's Use key)");
                EntFire(wp.target, "Press", "", 0.5);
            }
            if (wp.kind == "END" && wp.target != "") {
                HLT_Log("closing " + wp.target + " (stands in for a player's Use key)");
                EntFire(wp.target, "Close", "", 1.0);
            }
        } else if (waited > 120 && wp.kind == "END") {
            // finish the test anyway, but report who couldn't walk there (a nav problem to fix)
            local i = 0;
            foreach (p in HLT_Survivors()) {
                local o = p.GetOrigin();
                if ((o - wp.pos).Length2D() > 150) {
                    HLT_Log("WARNING: " + p.GetPlayerName() + " couldn't reach the end safe room on foot (stuck at "
                            + o.x.tointeger() + "," + o.y.tointeger() + "," + o.z.tointeger() + "); moving them in");
                    p.SetOrigin(wp.pos + Vector((i % 2) * 48 - 24, (i / 2) * 48 - 24, 16));
                }
                i++;
            }
            ::HLT.step_started = now;
        } else if (waited > 120) {
            HLT_Log("TIMEOUT: survivors didn't reach " + wp.label + " in 120s; skipping");
            HLT_Begin(::HLT.step + 1);
        }
        return;
    }
    local hold = now - ::HLT.arrived_at;
    if (wp.kind == "END") {
        // no transition yet? someone may have wandered out: regroup and close again
        if (hold > 25 && ::HLT.door_tries < 3) {
            ::HLT.door_tries++;
            HLT_Log("no transition yet; regrouping and closing the door again (try " + (::HLT.door_tries + 1) + ")");
            EntFire(wp.target, "Open", "", 0.0);
            ::HLT.arrived_at = -1.0;
            ::HLT.step_started = now;
        }
        return;
    }
    // wait for any horde to be dealt with before moving on
    local panic_over = !::HLT.panic_active || now - ::HLT.panic_time > 45;
    local calm = panic_over && Director.GetCommonInfectedCount() < 5;
    if ((hold > 8 && calm) || hold > 150) HLT_Begin(::HLT.step + 1);
}

function OnGameEvent_create_panic_event(params) { ::HLT.panic_active = true; ::HLT.panic_time = Time(); ::HLT.panics++; HLT_Log("panic started (#" + ::HLT.panics + ")"); }
function OnGameEvent_panic_event_finished(params) { ::HLT.panic_active = false; HLT_Log("panic finished"); }
function OnGameEvent_map_transition(params) { HLT_Log("PASS: level transition started"); }
function OnGameEvent_mission_lost(params) { HLT_Log("FAIL: all survivors died"); }

__CollectEventCallbacks(this, "OnGameEvent_", "GameEventCallbacks", RegisterScriptGameEventListener);
HLT_Log("loaded (map=" + Director.GetMapName() + ")");
'''
