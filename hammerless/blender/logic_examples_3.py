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

# every common infected alive, nearest to a (a position) first
NUKE_QUEUE = """local list = [], e = null;
while (e = Entities.FindByClassname(e, "infected")) if (e.GetHealth() > 0) list.append([(e.GetOrigin() - a).Length(), e]);
list.sort(function(x, y) { return x[0] > y[0] ? 1 : (x[0] < y[0] ? -1 : 0); });
::HL_NukeQueue <- [];
foreach (p in list) ::HL_NukeQueue.append(p[1]);
result = ::HL_NukeQueue.len();"""

EXAMPLES_3 = [
    dict(title="Power-Up: Nuke", level="Power-Ups",
         about="Like the power-ups in Call of Duty's zombies: each common infected killed has a "
               "chance (the Value node, 0.03 = 3%) to drop a glowing pickup where it fell, one at a "
               "time. A survivor who walks over it sets off a nuke: a flash, a shake, and every "
               "common infected explodes, nearest first, five every tenth of a second so the game "
               "isn't asked for every explosion at once. The kills count for whoever took it. (The "
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
                ("all_ok.result", "if_drop.condition"), ("hurt.entityid", "fell.target"),
                ("fell.result", "lift.a"), ("lift.vector", "what.f_origin"), ("what.table", "spawn.p1"),
                ("if_drop.true", "spawn.run"), ("spawn.then", "keep.run"), ("spawn.result", "keep.value"),
                ("keep.then", "glow.run"),
                ("timer.tick", "each.run"), ("each.each", "if_take.in"), ("nuke.result", "is_down.a"),
                ("nuke.result", "nuke_at.target"), ("each.item", "me_at.target"), ("me_at.result", "dist.a"),
                ("nuke_at.result", "dist.b"), ("dist.value", "close.a"), ("is_down.result", "take_ok.a"),
                ("close.result", "take_ok.b"), ("take_ok.result", "if_take.condition"),
                ("if_take.true", "by.run"), ("each.item", "by.value"), ("by.then", "remove.run"),
                ("nuke.result", "remove.target"), ("remove.then", "gone.run"),
                ("gone.then", "queue.run"), ("me_at.result", "queue.a"), ("queue.then", "wave_timer.start"),
                ("gone.then", "shout.show"), ("gone.then", "boom.play"), ("gone.then", "shake.run"),
                ("me_at.result", "shake.p0"), ("gone.then", "flash_each.run"), ("flash_each.each", "flash.run"),
                ("flash_each.item", "flash.p0"),
                ("wave_timer.tick", "wave.run"), ("taker.result", "wave.a"), ("wave.then", "if_empty.in"),
                ("wave.result", "empty.a"), ("empty.result", "if_empty.condition"), ("if_empty.true", "wave_timer.stop")],
         steps=[("Load the pickup's model", ["start", "precache"]),
                ("A kill may drop it (one at a time)",
                 ["hurt", "hp", "lethal", "all_ok", "chance", "roll", "lucky", "down", "none_down", "drop_ok",
                  "if_drop", "fell", "lift",
                  "what", "spawn", "keep", "glow"]),
                ("A survivor walks over it", ["timer", "each", "nuke", "is_down", "nuke_at", "me_at", "dist", "close",
                                              "take_ok", "if_take", "by", "remove", "gone"]),
                ("NUKE: line them up, flash and shake",
                 ["queue", "wave_timer", "shout", "boom", "shake", "flash_each", "flash"]),
                ("Five explode every tenth of a second; none left: stop (the red wire is a loop back to the "
                 "Timer, which is fine for events)", ["taker", "wave", "empty", "if_empty"])]),
]
