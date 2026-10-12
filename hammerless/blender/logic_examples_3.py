"""The third set of example graphs (42 on): Add > Examples 3, special game modes and tricks."""
from __future__ import annotations

from .logic_examples import N, ev, fn, text

PICKUP_MODEL = "models/w_models/weapons/w_eq_pipebomb.mdl"

# blow up the next few infected of the queue (a wave outward from where the nuke was taken, a few per
# tick so the game isn't asked for every explosion in one frame); a = who took it
NUKE_WAVE = """local n = 0;
while (::HL_NukeQueue.len() > 0 && n < 5) {
    local z = ::HL_NukeQueue.remove(0);
    if (!z.IsValid() || z.GetHealth() <= 0) continue;
    local boom = SpawnEntityFromTable("env_explosion", { targetname = "hl_nuke_boom", origin = z.GetOrigin() + Vector(0, 0, 32), iMagnitude = 0, spawnflags = (n == 0 ? 1 : 65) });
    z.TakeDamage(10000, 64, a);
    n++;
}
EntFire("hl_nuke_boom", "Explode", "", 0.0, null);
EntFire("hl_nuke_boom", "Kill", "", 1.0, null);
result = ::HL_NukeQueue.len();"""

# every common infected alive within b units of a (a position), nearest first
NUKE_QUEUE = """local list = [], e = null;
while (e = Entities.FindByClassname(e, "infected")) {
    if (e.GetHealth() <= 0) continue;
    local d = (e.GetOrigin() - a).Length();
    if (d <= b) list.append([d, e]);
}
list.sort(function(x, y) { return x[0] > y[0] ? 1 : (x[0] < y[0] ? -1 : 0); });
::HL_NukeQueue <- [];
foreach (p in list) ::HL_NukeQueue.append(p[1]);
result = ::HL_NukeQueue.len();"""

# ---------------------------------------------------------------- node groups (plain nodes inside; Tab goes in)

def _var(nid, name, col, row, get=True, kind="num", op="SET", label="", sockets=None):
    return N(nid, "HL_NodeGetVariable" if get else "HL_NodeSetVariable", col, row, label=label, var_name=name,
             scope="PLAYER", value_kind=kind, sockets=sockets, **({} if get else {"op": op}))


# the flashlight is bit 4 (EF_DIMLIGHT) of a player's m_fEffects, which scripts can read and change (no cheats)
FLASHLIGHT_IS_ON = dict(
    about="Is a player's flashlight on? It's bit 4 of the player's m_fEffects (Bits: A And B keeps only that bit).",
    inputs=[("player", "Player", "thing")], outputs=[("on", "On", "bool")],
    nodes=[fn("fx", "NetProps.GetPropInt", 0, 0, {"p1": "m_fEffects"}),
           N("bit", "HL_NodeMath", 1, 0, label="Flashlight bit", op="BIT_AND", sockets={"b": 4.0}),
           N("set", "HL_NodeCompare", 2, 0, label="Set?", op="NOT_EQUAL", sockets={"b": 0.0})],
    links=[("in.player", "fx.p0"), ("fx.result", "bit.a"), ("bit.value", "set.a"), ("set.result", "out.on")])

FLASHLIGHT_OFF = dict(
    about="Switches a player's flashlight off: bit 4 of the player's m_fEffects cleared (Bits: A Without B).",
    inputs=[("run", "Run", "event"), ("player", "Player", "thing")], outputs=[("then", "Then", "event")],
    nodes=[fn("fx", "NetProps.GetPropInt", 0, 1, {"p1": "m_fEffects"}),
           N("bit", "HL_NodeMath", 1, 1, label="Without the flashlight bit", op="BIT_CLEAR", sockets={"b": 4.0}),
           fn("set", "NetProps.SetPropInt", 2, 0, {"p1": "m_fEffects"})],
    links=[("in.run", "set.run"), ("in.player", "fx.p0"), ("in.player", "set.p0"), ("fx.result", "bit.a"),
           ("bit.value", "set.p2"), ("set.then", "out.then")])

FLASHLIGHT_BATTERY = dict(
    about="One survivor's battery, worked out each time Run fires (every 'Every' seconds). The player variable "
          "'flashlight_used' is how much is used up (0 full, 1 empty), 'flashlight_dead_until' the game time an "
          "empty one comes back. On: used goes up by Every / Lasts; at 1 the flashlight is switched off and stays "
          "dead for Empty For seconds, then comes back at Comes Back At. Off: used goes down just as slowly.",
    inputs=[("run", "Run", "event"), ("player", "Player", "thing"), ("every", "Every (seconds)", "num"),
            ("lasts", "Lasts (seconds)", "num"), ("empty_for", "Empty For (seconds)", "num"),
            ("back_at", "Comes Back At", "num")],
    outputs=[("then", "Then", "event"), ("charge", "Charge", "num"), ("empty", "Empty", "bool")],
    nodes=[fn("now", "Time", 0, 4),
           _var("until", "flashlight_dead_until", 0, 5, label="Dead until"),
           _var("used", "flashlight_used", 0, 6, label="Used up"),
           N("dead", "HL_NodeCompare", 1, 4, label="Still dead?", op="LESS"),
           N("if_dead", "HL_NodeIf", 1, 0),
           N("off_dead", "HL_NodeGroup", 2, 0, label="Keep it off", group="Flashlight Off"),
           N("waited", "HL_NodeCompare", 2, 5, label="Was dead?", op="GREATER", sockets={"b": 0.0}),
           N("if_back", "HL_NodeIf", 2, 1),
           _var("clear", "flashlight_dead_until", 3, 1, get=False, label="Not dead any more", sockets={"value": 0.0}),
           N("back_used", "HL_NodeMath", 3, 5, label="1 - Comes Back At", op="SUBTRACT", sockets={"a": 1.0}),
           _var("set_back", "flashlight_used", 4, 1, get=False, label="Back with a little charge"),
           N("is_on", "HL_NodeGroup", 3, 6, label="Flashlight on?", group="Flashlight Is On"),
           N("if_on", "HL_NodeIf", 3, 2),
           N("step", "HL_NodeMath", 4, 6, label="Every / Lasts", op="DIVIDE"),
           _var("drain", "flashlight_used", 4, 2, get=False, op="ADD", label="Drain"),
           N("full", "HL_NodeCompare", 5, 5, label="All used up?", op="GREATER_EQUAL", sockets={"b": 1.0}),
           N("if_full", "HL_NodeIf", 5, 2),
           _var("cap", "flashlight_used", 6, 2, get=False, label="Empty", sockets={"value": 1.0}),
           N("until_at", "HL_NodeMath", 6, 5, label="Now + Empty For", op="ADD"),
           _var("set_until", "flashlight_dead_until", 7, 2, get=False, label="Dead until"),
           N("off_empty", "HL_NodeGroup", 8, 2, label="Switch it off", group="Flashlight Off"),
           N("less", "HL_NodeMath", 5, 6, label="Used - step", op="SUBTRACT"),
           N("not_below", "HL_NodeMath", 6, 6, label="Not below 0", op="MAXIMUM", sockets={"b": 0.0}),
           _var("recharge", "flashlight_used", 4, 3, get=False, label="Recharge"),
           N("charge", "HL_NodeMath", 7, 5, label="Charge = 1 - used", op="SUBTRACT", sockets={"a": 1.0})],
    links=[("in.run", "if_dead.in"), ("now.result", "dead.a"), ("until.result", "dead.b"),
           ("dead.result", "if_dead.condition"),
           ("in.player", "until.player"), ("in.player", "used.player"),
           # dead: keep it off
           ("if_dead.true", "off_dead.run"), ("in.player", "off_dead.player"), ("off_dead.then", "out.then"),
           # the wait is over: back with a little charge
           ("if_dead.false", "if_back.in"), ("until.result", "waited.a"), ("waited.result", "if_back.condition"),
           ("if_back.true", "clear.run"), ("in.player", "clear.player"), ("clear.then", "set_back.run"),
           ("in.back_at", "back_used.b"), ("back_used.value", "set_back.value"), ("in.player", "set_back.player"),
           ("set_back.then", "out.then"),
           # on: drain; all used up: dead for a while, switched off
           ("if_back.false", "if_on.in"), ("in.player", "is_on.player"), ("is_on.on", "if_on.condition"),
           ("in.every", "step.a"), ("in.lasts", "step.b"),
           ("if_on.true", "drain.run"), ("in.player", "drain.player"), ("step.value", "drain.value"),
           ("drain.then", "if_full.in"), ("used.result", "full.a"), ("full.result", "if_full.condition"),
           ("if_full.true", "cap.run"), ("in.player", "cap.player"), ("cap.then", "set_until.run"),
           ("now.result", "until_at.a"), ("in.empty_for", "until_at.b"), ("until_at.value", "set_until.value"),
           ("in.player", "set_until.player"), ("set_until.then", "off_empty.run"), ("in.player", "off_empty.player"),
           ("off_empty.then", "out.then"), ("if_full.false", "out.then"),
           # off: recharge just as slowly
           ("if_on.false", "recharge.run"), ("in.player", "recharge.player"), ("used.result", "less.a"),
           ("step.value", "less.b"), ("less.value", "not_below.a"), ("not_below.value", "recharge.value"),
           ("recharge.then", "out.then"),
           # what the rest of the graph reads
           ("used.result", "charge.b"), ("charge.value", "out.charge"), ("dead.result", "out.empty")],
    steps=[("Dead (empty, waiting)? Keep it off", ["now", "until", "used", "dead", "if_dead", "off_dead"]),
           ("The wait is over: back with a little charge", ["waited", "if_back", "clear", "back_used", "set_back"]),
           ("On: drain", ["is_on", "if_on", "step", "drain"]),
           ("All used up: dead for a while, switched off",
            ["full", "if_full", "cap", "until_at", "set_until", "off_empty"]),
           ("Off: recharge", ["less", "not_below", "recharge"]),
           ("Out: the charge", ["charge"])])


MUZZLE_FLASH = """local p = a;      // (the event's Userid wire gives the player)
local w = p != null ? p.GetActiveWeapon() : null;
if (w != null && w.LookupAttachment("muzzle_flash") > 0) {
    // a point light for everything (Brightness), and an extra one for zombies and survivors (Model Brightness)
    foreach (part in [["light", b, 0], ["models", d, 1]]) {      // (spawnflags 1: not the world)
        local name = "hl_muzzle_" + part[0] + "_" + p.GetEntityIndex();
        local l = Entities.FindByName(null, name);
        if (l == null && part[1] > 0) {      // made once
            l = SpawnEntityFromTable("light_dynamic", {targetname = name, origin = w.GetOrigin(),
                _light = "255 190 110 255", brightness = part[1].tostring(), distance = c.tostring(),
                spotlight_radius = "0", _inner_cone = "0", _cone = "0", spawnflags = part[2]});
            DoEntFire("!self", "TurnOff", "", 0.0, null, l);
            l.ValidateScriptScope();
        }
        if (l != null) {
            local s = l.GetScriptScope();
            if (!("gun" in s) || s.gun != w) {      // a new gun in hand: on its muzzle
                s.gun <- w;
                DoEntFire("!self", "SetParent", "!activator", 0.0, w, l);
                DoEntFire("!self", "SetParentAttachment", "muzzle_flash", 0.0, w, l);
            }
            DoEntFire("!self", "TurnOn", "", 0.0, null, l);
            DoEntFire("!self", "TurnOff", "", 0.03, null, l);      // (one server tick: full-auto flickers)
        }
    }
}"""

EXAMPLES_3 = [
    dict(title="Power-Up: Nuke", level="Power-Ups",
         about="Like the power-ups in Call of Duty's zombies: each common infected killed has a "
               "chance (the Value node, 0.03 = 3%) to drop a glowing pickup where it fell, one at a "
               "time. A survivor who walks over it sets off a nuke: a flash, a shake, and every "
               "common infected within the Radius (a Value node, in units) explodes, nearest first, "
               "five every tenth of a second so the game isn't asked for every explosion at once. The "
               "kills count for whoever took it. After a nuke, a Delay keeps new ones from dropping "
               "for 30 seconds (the 'cooling' variable). (The "
               "game's 'infected_death' event doesn't say which infected died, so the drop uses "
               "'infected_hurt': a hit at least as big as its health left is a kill.)",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                fn("precache", "PrecacheModel", 1, 0, {"p0": PICKUP_MODEL}),
                # drop
                ev("hurt", "infected_hurt", 0, 2),
                fn("hp", "entity:GetHealth", 2, 6),
                N("lethal", "HL_NodeCompare", 3, 6, op="GREATER_EQUAL"),
                N("all_ok", "HL_NodeBoolMath", 3, 5, op="AND"),
                N("chance", "HL_NodeValue", 0, 4, label="Drop chance", sockets={"value": 0.03}),
                N("roll", "HL_NodeRandomValue", 0, 5, each_time=True, sockets={"min": 0.0, "max": 1.0}),
                N("lucky", "HL_NodeCompare", 1, 4, op="LESS"),
                N("down", "HL_NodeGetVariable", 1, 5, var_name="nuke", value_kind="thing"),
                N("none_down", "HL_NodeCheck", 2, 5, op="NOT_SET"),
                N("drop_ok", "HL_NodeBoolMath", 2, 4, op="AND"),
                N("if_drop", "HL_NodeIf", 3, 2),
                N("cooling", "HL_NodeGetVariable", 4, 6, var_name="cooling", value_kind="bool"),
                N("not_cool", "HL_NodeBoolMath", 5, 6, op="NOT"),
                N("ready", "HL_NodeBoolMath", 4, 5, op="AND"),
                fn("fell", "entity:GetOrigin", 1, 3),
                N("lift", "HL_NodeVectorMath", 2, 3, op="ADD", sockets={"b": (0.0, 0.0, 16.0)}),
                N("what", "HL_NodeMakeTable", 3, 3, fields="targetname: text, model: text, origin: vec, solid: int",
                  sockets={"f_targetname": "hl_nuke", "f_model": PICKUP_MODEL, "f_solid": 0.0}),
                fn("spawn", "SpawnEntityFromTable", 4, 2, {"p0": "prop_dynamic"}),
                N("keep", "HL_NodeSetVariable", 5, 2, var_name="nuke", value_kind="thing"),
                fn("glow", "EntFire", 6, 2, {"p0": "hl_nuke", "p1": "StartGlowing"}),
                # pick up
                N("timer", "HL_NodeTimer", 0, 7, seconds=0.2),
                N("each", "HL_NodeForEach", 1, 7, what="SURVIVORS"),
                N("nuke", "HL_NodeGetVariable", 1, 9, var_name="nuke", value_kind="thing"),
                N("is_down", "HL_NodeCheck", 2, 9, op="IS_SET"),
                fn("nuke_at", "entity:GetOrigin", 2, 10),
                fn("me_at", "entity:GetOrigin", 2, 8),
                N("dist", "HL_NodeVectorMath", 3, 8, op="DISTANCE"),
                N("close", "HL_NodeCompare", 4, 8, op="LESS", sockets={"b": 64.0}),
                N("take_ok", "HL_NodeBoolMath", 4, 9, op="AND"),
                N("if_take", "HL_NodeIf", 5, 7),
                N("by", "HL_NodeSetVariable", 6, 7, var_name="nuke_by", value_kind="thing"),
                fn("remove", "entity:Kill", 7, 7),
                N("gone", "HL_NodeSetVariable", 8, 7, var_name="nuke", value_kind="thing"),
                # the nuke
                N("queue", "HL_NodeScriptCode", 0, 12, label="Every common, nearest first", code=NUKE_QUEUE),
                N("wave_timer", "HL_NodeTimer", 1, 12, seconds=0.1, running=False),
                N("radius", "HL_NodeValue", 0, 13, label="Radius", sockets={"value": 1000.0}),
                N("cool_on", "HL_NodeSetVariable", 0, 17, var_name="cooling", value_kind="bool", sockets={"value": True}),
                N("cool_wait", "HL_NodeDelay", 1, 17, label="Cooldown", seconds=30.0),
                N("cool_off", "HL_NodeSetVariable", 2, 17, var_name="cooling", value_kind="bool", sockets={"value": False}),
                N("shout", "HL_NodeMessage", 1, 13, text="NUKE!", seconds=2.0),
                N("boom", "HL_NodeSound", 2, 13, sound="explode_3", everywhere=True),
                fn("shake", "ScreenShake", 3, 13, {"p1": 16.0, "p2": 40.0, "p3": 1.5, "p4": 5000.0}),
                N("flash_each", "HL_NodeForEach", 2, 15, what="SURVIVORS"),
                fn("flash", "ScreenFade", 3, 15, {"p1": 255.0, "p2": 255.0, "p3": 255.0, "p4": 200.0,
                                                   "p5": 0.8, "p6": 0.1, "p7": 1.0}),
                # the wave
                N("taker", "HL_NodeGetVariable", 4, 12, var_name="nuke_by", value_kind="thing"),
                N("wave", "HL_NodeScriptCode", 5, 12, label="Blow up the next five", code=NUKE_WAVE),
                N("empty", "HL_NodeCompare", 5, 13, op="LESS_EQUAL", sockets={"b": 0.0}),
                N("if_empty", "HL_NodeIf", 6, 12)],
         links=[("start.start", "precache.run"),
                ("hurt.happened", "if_drop.in"), ("hurt.entityid", "hp.target"), ("hurt.amount", "lethal.a"),
                ("hp.result", "lethal.b"), ("drop_ok.result", "all_ok.a"), ("lethal.result", "all_ok.b"), ("roll.value", "lucky.a"), ("chance.value", "lucky.b"),
                ("down.result", "none_down.a"), ("lucky.result", "drop_ok.a"), ("none_down.result", "drop_ok.b"),
                ("all_ok.result", "ready.a"), ("cooling.result", "not_cool.a"), ("not_cool.result", "ready.b"),
                ("ready.result", "if_drop.condition"), ("hurt.entityid", "fell.target"),
                ("fell.result", "lift.a"), ("lift.vector", "what.f_origin"), ("what.table", "spawn.p1"),
                ("if_drop.true", "spawn.run"), ("spawn.then", "keep.run"), ("spawn.result", "keep.value"),
                ("keep.then", "glow.run"),
                ("timer.tick", "each.run"), ("each.each", "if_take.in"), ("nuke.result", "is_down.a"),
                ("nuke.result", "nuke_at.target"), ("each.item", "me_at.target"), ("me_at.result", "dist.a"),
                ("nuke_at.result", "dist.b"), ("dist.value", "close.a"), ("is_down.result", "take_ok.a"),
                ("close.result", "take_ok.b"), ("take_ok.result", "if_take.condition"),
                ("if_take.true", "by.run"), ("each.item", "by.value"), ("by.then", "remove.run"),
                ("nuke.result", "remove.target"), ("remove.then", "gone.run"),
                ("gone.then", "queue.run"), ("me_at.result", "queue.a"), ("radius.value", "queue.b"),
                ("gone.then", "cool_on.run"), ("cool_on.then", "cool_wait.in"), ("cool_wait.out", "cool_off.run"), ("queue.then", "wave_timer.start"),
                ("gone.then", "shout.show"), ("gone.then", "boom.play"), ("gone.then", "shake.run"),
                ("me_at.result", "shake.p0"), ("gone.then", "flash_each.run"), ("flash_each.each", "flash.run"),
                ("flash_each.item", "flash.p0"),
                ("wave_timer.tick", "wave.run"), ("taker.result", "wave.a"), ("wave.then", "if_empty.in"),
                ("wave.result", "empty.a"), ("empty.result", "if_empty.condition"), ("if_empty.true", "wave_timer.stop")],
         steps=[("Load the pickup's model", ["start", "precache"]),
                ("A kill may drop it (one at a time)",
                 ["hurt", "hp", "lethal", "all_ok", "chance", "roll", "lucky", "down", "none_down", "drop_ok",
                  "if_drop", "cooling", "not_cool", "ready", "fell", "lift",
                  "what", "spawn", "keep", "glow"]),
                ("A survivor walks over it", ["timer", "each", "nuke", "is_down", "nuke_at", "me_at", "dist", "close",
                                              "take_ok", "if_take", "by", "remove", "gone"]),
                ("NUKE: line them up, flash and shake",
                 ["queue", "radius", "wave_timer", "shout", "boom", "shake", "flash_each", "flash"]),
                ("Five explode every tenth of a second; none left: stop (the red wire is a loop back to the "
                 "Timer, which is fine for events)", ["taker", "wave", "empty", "if_empty"]),
                ("No new drops for 30 seconds", ["cool_on", "cool_wait", "cool_off"])]),
    dict(title="Slow Motion Headshots", level="Scripting",
         objects={"slowmo": ("ENTITY:func_timescale", (0.0, 0.0, 0.5),
                             {"desiredTimescale": "0.3", "acceleration": "1.0", "minBlendRate": "1.0",
                              "blendDeltaMultiplier": "3.0"})},
         about="Every headshot slows the whole game to 30% speed for about two seconds, then brings it "
               "back, no cheats needed. Kills of common infected ('infected_death') and of specials "
               "('player_death') both say whether it was a headshot. The slow motion is a Time Scale entity "
               "(func_timescale, in the collection 'Example 43 objects': put it anywhere in the map): Start "
               "blends the game to its Desired Timescale, Stop blends back to normal (Acceleration and the "
               "blend rates set how quickly). The Delay counts game time, which runs slowly too: 0.6 s of it "
               "is about 2 s for the players. Change the entity's Desired Timescale for more or less.",
         nodes=[ev("kill", "infected_death", 0, 0),
                N("if_kill", "HL_NodeIf", 1, 0),
                ev("special", "player_death", 0, 2),
                N("if_special", "HL_NodeIf", 1, 2),
                N("slow", "HL_NodeObject", 2, 0, label="Slow motion", target="@slowmo"),
                N("wait", "HL_NodeDelay", 2, 2, label="Slow motion (game time)", seconds=0.6),
                N("normal", "HL_NodeObject", 3, 2, label="Back to normal", target="@slowmo")],
         links=[("kill.happened", "if_kill.in"), ("kill.headshot", "if_kill.condition"),
                ("special.happened", "if_special.in"), ("special.headshot", "if_special.condition"),
                ("if_kill.true", "slow.Start"), ("if_special.true", "slow.Start"),
                ("if_kill.true", "wait.in"), ("if_special.true", "wait.in"), ("wait.out", "normal.Stop")],
         steps=[("A headshot, on a common or a special", ["kill", "if_kill", "special", "if_special"]),
                ("Slow the game down (the Time Scale entity's Start)", ["slow"]),
                ("After the slow motion: back to normal (its Stop)", ["wait", "normal"])]),
    dict(title="Third Person Zone", level="Simple",
         objects={"room": ("ROOM", (0.0, 0.0, 1.5))},
         about="The Camera node switches players to the game's over-the-shoulder third person view and back "
               "(no cheats needed). Its Player is left empty, so it acts on whoever set it off: walking into "
               "the room switches only that player to third person, walking out switches them back. "
               "Untick Third Person on a Camera node for first person.",
         nodes=[N("room", "HL_NodeVolume", 0, 0, sockets={"object": "@room"}, once=False),
                N("third", "HL_NodeCamera", 1, 0),
                N("first", "HL_NodeCamera", 1, 2, sockets={"third": False})],
         links=[("room.enter", "third.run"), ("room.leave", "first.run")],
         steps=[("In: third person; out: first person, for the player who walked in or out",
                 ["room", "third", "first"])]),
    dict(title="Third Person Everywhere", level="Simple",
         about="The whole map in third person: each player is switched when they spawn (at map start players "
               "aren't in yet). The game event's Userid is the player, wired into the Camera's Player.",
         nodes=[ev("spawn", "player_spawn", 0, 0),
                N("third", "HL_NodeCamera", 1, 0)],
         links=[("spawn.happened", "third.run"), ("spawn.userid", "third.player")],
         steps=[("Every player who spawns: third person", ["spawn", "third"])]),
    dict(title="Random Zombie Sizes", level="Zombies & Director",
         about="Every common infected gets a random size between Min and Max on the Size node: 0.5 is half "
               "size, 2 is twice as big (set both the same for all giants or all tiny ones). Every half second "
               "For Each looks at the commons, and the ones still at normal size (1) are new: they're scaled "
               "(Set Model Scale), and their hitboxes are made to follow the new size (the collision 'surround "
               "type' set to hitboxes, else a giant can only be shot at normal height). No cheats. Tested in "
               "the game: shots hit the whole body of a giant and of a tiny one.",
         nodes=[N("timer", "HL_NodeTimer", 0, 0, seconds=0.5),
                N("each", "HL_NodeForEach", 1, 0, what="COMMONS"),
                fn("now", "entity:GetModelScale", 1, 2),
                N("new", "HL_NodeCompare", 2, 2, op="EQUAL", sockets={"b": 1.0}),
                N("if_new", "HL_NodeIf", 2, 0),
                N("size", "HL_NodeRandomValue", 3, 2, label="Size (Min / Max)", each_time=True,
                  sockets={"min": 0.5, "max": 2.0}),
                fn("scale", "entity:SetModelScale", 3, 0, {"p1": 0.0}),
                fn("hitboxes", "NetProps.SetPropInt", 4, 0, {"p1": "m_Collision.m_nSurroundType", "p2": 2})],
         links=[("timer.tick", "each.run"), ("each.each", "if_new.in"), ("each.item", "now.target"),
                ("now.result", "new.a"), ("new.result", "if_new.condition"),
                ("if_new.true", "scale.run"), ("each.item", "scale.target"), ("size.value", "scale.p0"),
                ("scale.then", "hitboxes.run"), ("each.item", "hitboxes.p0")],
         steps=[("Every half second, each common still at normal size", ["timer", "each", "now", "new", "if_new"]),
                ("A random size, and hitboxes that follow it", ["size", "scale", "hitboxes"])]),
    dict(title="Third Person Muzzle Flash", level="Scripting",
         about="In third person the game's muzzle flashes light only models, not the walls and floor. This gives "
               "every player a point light on their gun's muzzle (Brightness), plus an extra light for zombies "
               "and survivors only (Model Brightness: models need more light to look lit; 0 for none), made at their "
               "first shot and moved to each new gun, and flashes them on each shot for one server tick (0.03 s), so full-auto fire flickers. Brightness and Reach (units) set how strong and how far. "
               "Use it with the third person examples. No cheats; tested in the game (lights per shooter, parented to the gun).",
         nodes=[ev("fire", "weapon_fire", 0, 0),
                N("bright", "HL_NodeValue", 0, 2, label="Brightness", sockets={"value": 0.3}),
                N("reach", "HL_NodeValue", 0, 3, label="Reach", sockets={"value": 700.0}),
                N("model_bright", "HL_NodeValue", 0, 4, label="Model Brightness", sockets={"value": 2.0}),
                N("flash", "HL_NodeScriptCode", 1, 0, label="Flash the shooter's muzzle light", code=MUZZLE_FLASH)],
         links=[("fire.happened", "flash.run"), ("fire.userid", "flash.a"), ("bright.value", "flash.b"),
                ("reach.value", "flash.c"), ("model_bright.value", "flash.d")],
         steps=[("Every shot: the shooter's own muzzle light, on for a moment", ["fire", "bright", "reach", "model_bright", "flash"])]),
    dict(title="Flashlight Battery", level="Scripting",
         about="Flashlights run on batteries, like Half-Life: on, a survivor's battery drains (a full one lasts "
               "'Lasts' seconds); off, it recharges just as slowly. Empty, the flashlight is switched off and stays "
               "dead for 'Empty For' seconds, then comes back at 'Comes Back At' (0.05 = 5%) and recharges from there. "
               "Ten times a second, For Each survivor runs the 'Flashlight Battery' group (a node group: select it "
               "and press Tab to see the plain nodes inside, Tab again to come back; it uses two smaller groups, "
               "'Flashlight Is On' and 'Flashlight Off'), then adds that survivor to a line of text. The HUD shows "
               "the line: 'Flashlights:  Nick 80%  Ellis EMPTY ...'. No cheats. (The HUD needs Hammerless's own "
               "game mode: Build & Play starts the map in it.)",
         groups={"Flashlight Is On": FLASHLIGHT_IS_ON, "Flashlight Off": FLASHLIGHT_OFF,
                 "Flashlight Battery": FLASHLIGHT_BATTERY},
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("line", "HL_NodeGetVariable", 0, 1, label="The finished line", var_name="flashlight_hud",
                  value_kind="text"),
                N("hud", "HL_NodeHudText", 1, 0, slot="3", team="SURVIVORS", place=True, x=0.15, y=0.02, w=0.7,
                  h=0.04),
                N("timer", "HL_NodeTimer", 0, 3, seconds=0.1),
                N("begin", "HL_NodeSetVariable", 1, 3, label="Start the line", var_name="flashlight_text",
                  value_kind="text", sockets={"value": "Flashlights: "}),
                N("each", "HL_NodeForEach", 2, 3, what="SURVIVORS"),
                N("lasts", "HL_NodeValue", 2, 5, label="Lasts (seconds)", sockets={"value": 60.0}),
                N("empty_for", "HL_NodeValue", 2, 6, label="Empty For (seconds)", sockets={"value": 10.0}),
                N("back_at", "HL_NodeValue", 2, 7, label="Comes Back At", sockets={"value": 0.05}),
                N("battery", "HL_NodeGroup", 3, 3, group="Flashlight Battery", sockets={"every": 0.1}),
                N("if_empty", "HL_NodeIf", 4, 3),
                N("say_empty", "HL_NodeFormatText", 4, 5, template="   {a} EMPTY"),
                N("percent", "HL_NodeMath", 4, 6, label="Charge x 100", op="MULTIPLY", sockets={"b": 100.0}),
                N("whole", "HL_NodeMath", 4, 7, label="Whole number", op="ROUND"),
                N("say_pct", "HL_NodeFormatText", 5, 6, template="   {a} {b}%"),
                N("add_empty", "HL_NodeSetVariable", 5, 3, label="Add to the line", var_name="flashlight_text",
                  value_kind="text", op="ADD"),
                N("add_pct", "HL_NodeSetVariable", 5, 4, label="Add to the line", var_name="flashlight_text",
                  value_kind="text", op="ADD"),
                N("text", "HL_NodeGetVariable", 3, 1, label="The line so far", var_name="flashlight_text",
                  value_kind="text"),
                N("publish", "HL_NodeSetVariable", 4, 1, label="Show it", var_name="flashlight_hud",
                  value_kind="text")],
         links=[("start.start", "hud.run"), ("line.result", "hud.text"),
                ("timer.tick", "begin.run"), ("begin.then", "each.run"),
                ("each.each", "battery.run"), ("each.item", "battery.player"), ("lasts.value", "battery.lasts"),
                ("empty_for.value", "battery.empty_for"), ("back_at.value", "battery.back_at"),
                ("battery.then", "if_empty.in"), ("battery.empty", "if_empty.condition"),
                ("if_empty.true", "add_empty.run"), ("each.item", "say_empty.a"), ("say_empty.result", "add_empty.value"),
                ("if_empty.false", "add_pct.run"), ("battery.charge", "percent.a"), ("percent.value", "whole.a"),
                ("each.item", "say_pct.a"), ("whole.value", "say_pct.b"), ("say_pct.result", "add_pct.value"),
                ("each.done", "publish.run"), ("text.result", "publish.value")],
         steps=[("The HUD shows the finished line", ["start", "line", "hud"]),
                ("Ten times a second, every survivor's battery (Tab into the group)",
                 ["timer", "begin", "each", "lasts", "empty_for", "back_at", "battery"]),
                ("Add them to the line: EMPTY or the charge in %",
                 ["if_empty", "say_empty", "percent", "whole", "say_pct", "add_empty", "add_pct"]),
                ("All done: show it", ["text", "publish"])]),
]
