"""Ready-made logic graphs to learn from: Add > Examples (and Examples 2, logic_examples_2) in the logic editor.

Each example becomes a new graph (with notes on what it does and how), plus any objects it needs
(a button, a gate, a room) in its own collection at the 3D cursor. They are ordinary graphs: they
work in the map as they are, and can be changed or deleted like any other.
"""
from __future__ import annotations

import textwrap

import bmesh
import bpy
from bpy.props import IntProperty

from .logic import TREE, _show_tree, new_tree

COL, ROW = 280.0, -190.0          # grid spacing for node positions


def N(nid, idname, col, row, label="", sockets=None, **props):
    """A node: its name, type, grid position, then its settings (set in order: some add sockets) and
    the values typed on its input sockets (by identifier; '@name' is one of the example's objects)."""
    return {"id": nid, "type": idname, "pos": (col, row), "label": label, "props": props,
            "sockets": sockets or {}}


def fn(nid, function, col, row, sockets=None, label=""):
    return N(nid, "HL_NodeScriptCall", col, row, label, sockets, fn=function)


def ev(nid, event, col, row, label=""):
    return N(nid, "HL_NodeScriptEvent", col, row, label, None, event=event)


def text(nid, value, col, row):
    """A fixed piece of text (a Format Text with nothing to fill in)."""
    return N(nid, "HL_NodeFormatText", col, row, f'Text: "{value}"', None, template=value)


EXAMPLES = [
    # ------------------------------------------------------------------ simple
    dict(title="Hello Message", level="Simple",
         about="The smallest graph: when the map starts, wait a moment (players are still loading), "
               "then show a message to everyone. Events (red sockets) are wires that say 'now': "
               "On Map Start fires once, the Delay passes it on 5 seconds later, and the Message "
               "shows when its Show input is fired.",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("wait", "HL_NodeDelay", 1, 0, seconds=5.0),
                N("hello", "HL_NodeMessage", 2, 0, text="Welcome! This message comes from a logic graph",
                  seconds=6.0)],
         links=[("start.start", "wait.in"), ("wait.out", "hello.show")],
         steps=[("Map starts, wait 5 s, show a message", ["start", "wait", "hello"])]),

    dict(title="Horde After the Safe Room", level="Simple",
         about="A horde 10 seconds after the survivors leave the safe room, and a message when they "
               "have fought it off. Game Event listens for things the game does (Only Once: the "
               "first time). Horde's On Finished fires when the panic event is over.",
         nodes=[N("left", "HL_NodeGameEvent", 0, 0, event="LEFT_SAFE_ROOM", once=True),
                N("wait", "HL_NodeDelay", 1, 0, seconds=10.0),
                N("horde", "HL_NodeHorde", 2, 0),
                N("done", "HL_NodeMessage", 3, 0, text="You held them off", seconds=4.0)],
         links=[("left.happened", "wait.in"), ("wait.out", "horde.start"), ("horde.finished", "done.show")],
         steps=[("Leave the safe room, 10 s later a horde", ["left", "wait", "horde"]),
                ("When it's over", ["done"])]),

    dict(title="Button Opens a Gate", level="Simple",
         objects={"button": ("BUTTON", (0.0, -1.5, 1.0)), "gate": ("GATE", (0.0, 1.5, 1.5))},
         about="Two plain meshes become working map parts: the Button node turns its mesh into a "
               "button players press (E), the Move node turns its mesh into something that slides "
               "(here up, over 4 seconds). One output can be wired to many inputs: pressing also "
               "shows a message. Move the objects to where you need them.",
         nodes=[N("button", "HL_NodeButton", 0, 0, sockets={"object": "@button"}, once=True),
                N("open", "HL_NodeMove", 1, 0, sockets={"object": "@gate"}, direction="up", seconds=4.0),
                N("msg", "HL_NodeMessage", 1, 1, text="The gate is opening", seconds=4.0)],
         links=[("button.pressed", "open.go"), ("button.pressed", "msg.show")],
         steps=[("Press the button: the gate slides up", ["button", "open", "msg"])]),

    dict(title="Tank in a Room", level="Simple",
         objects={"room": ("ROOM", (0.0, 0.0, 1.5))},
         about="A Volume is a mesh that notices players walking in and out (it is invisible in the "
               "game). The first survivor to walk in spawns a Tank (Spawn Zombie with no Where: the "
               "game picks a spot out of sight). Only Once on the Volume makes it happen once.",
         nodes=[N("room", "HL_NodeVolume", 0, 0, sockets={"object": "@room"}, once=True),
                N("tank", "HL_NodeSpawn", 1, 0, what="tank"),
                N("warn", "HL_NodeMessage", 1, 1, text="Something big is coming", seconds=4.0)],
         links=[("room.first", "tank.spawn"), ("room.first", "warn.show")],
         steps=[("First one in: a Tank", ["room", "tank", "warn"])]),

    dict(title="A Random Special Every Minute", level="Simple",
         about="A Timer fires On Tick every 60 seconds. Random picks one of its wired outputs each "
               "time, so a different special infected arrives: a Hunter, Smoker, Jockey or Charger.",
         nodes=[N("timer", "HL_NodeTimer", 0, 0, seconds=60.0),
                N("pick", "HL_NodeRandom", 1, 0),
                N("hunter", "HL_NodeSpawn", 2, 0, what="hunter"),
                N("smoker", "HL_NodeSpawn", 2, 1, what="smoker"),
                N("jockey", "HL_NodeSpawn", 2, 2, what="jockey"),
                N("charger", "HL_NodeSpawn", 2, 3, what="charger")],
         links=[("timer.tick", "pick.pick"), ("pick.case1", "hunter.spawn"), ("pick.case2", "smoker.spawn"),
                ("pick.case3", "jockey.spawn"), ("pick.case4", "charger.spawn")],
         steps=[("Every 60 s, pick one", ["timer", "pick"]), ("What can come", ["hunter", "smoker", "jockey", "charger"])]),

    # ------------------------------------------------------------------ values
    dict(title="A Tank Somewhere Along the Way", level="Values",
         about="Values (grey, green, blue sockets) are worked out live in the game. Path Progress is "
               "how far the leading survivor is along the map (0 at the start, 1 at the end). Random "
               "Value picks a point between 20% and 100% once per game. When watches its Condition "
               "and fires On True when it becomes true: when the survivors pass that point, a Tank.",
         nodes=[N("progress", "HL_NodeProgress", 0, 0),
                N("where", "HL_NodeRandomValue", 0, 1, sockets={"min": 0.2, "max": 1.0}),
                N("past", "HL_NodeCompare", 1, 0, op="GREATER_EQUAL"),
                N("when", "HL_NodeWhen", 2, 0, once=True),
                N("tank", "HL_NodeSpawn", 3, 0, what="tank")],
         links=[("progress.furthest", "past.a"), ("where.value", "past.b"), ("past.result", "when.condition"),
                ("when.true", "tank.spawn")],
         steps=[("Has the lead survivor passed the random point?", ["progress", "where", "past"]),
                ("Then a Tank", ["when", "tank"])]),

    dict(title="Pills for a Gas Can", level="Values",
         about="Game Event (any) gives every event the game has, with its details as values: "
               "'item_pickup' says who picked up what. If checks a condition at that moment: "
               "Compare Values asks whether the item is a gas can (a Format Text with no blanks is "
               "just a fixed text). Then the game's own GiveItem function gives that player pills, "
               "and ClientPrint tells them why (Where 3 = their chat).",
         nodes=[ev("pickup", "item_pickup", 0, 0),
                text("gascan", "gascan", 0, 1),
                N("is_can", "HL_NodeCheck", 1, 1, op="EQUAL"),
                N("if", "HL_NodeIf", 2, 0),
                fn("give", "player:GiveItem", 3, 0, {"p0": "pain_pills"}),
                fn("tell", "ClientPrint", 4, 0, {"p1": 3.0, "p2": "Pills, for carrying the gas can"})],
         links=[("pickup.happened", "if.in"), ("pickup.item", "is_can.a"), ("gascan.result", "is_can.b"),
                ("is_can.result", "if.condition"), ("if.true", "give.run"), ("pickup.userid", "give.target"),
                ("give.then", "tell.run"), ("pickup.userid", "tell.p0")],
         steps=[("Someone picks something up: is it a gas can?", ["pickup", "gascan", "is_can", "if"]),
                ("Give them pills and say why", ["give", "tell"])]),

    dict(title="Low Health Warning", level="Values",
         about="'player_hurt' fires every time someone takes damage, with their health left. Boolean "
               "Math joins two checks: health under 25 AND the player is a survivor (infected "
               "players get hurt too). Is Survivor is one of the game's functions that just gives a "
               "value, so it has no Run socket. ClientPrint Where 4 prints in the middle of the screen.",
         nodes=[ev("hurt", "player_hurt", 0, 0),
                N("low", "HL_NodeCompare", 1, 1, op="LESS", sockets={"b": 25.0}),
                fn("survivor", "player:IsSurvivor", 1, 2),
                N("both", "HL_NodeBoolMath", 2, 1, op="AND"),
                N("if", "HL_NodeIf", 3, 0),
                fn("tell", "ClientPrint", 4, 0, {"p1": 4.0, "p2": "You're badly hurt: find a medkit"})],
         links=[("hurt.happened", "if.in"), ("hurt.health", "low.a"), ("hurt.userid", "survivor.target"),
                ("low.result", "both.a"), ("survivor.result", "both.b"), ("both.result", "if.condition"),
                ("if.true", "tell.run"), ("hurt.userid", "tell.p0")],
         steps=[("Hurt, under 25 health, and a survivor?", ["hurt", "low", "survivor", "both", "if"]),
                ("Warn that player", ["tell"])]),

    dict(title="Headshot Rewards", level="Values",
         about="Variables remember values. This one is Per Player: every survivor has their own "
               "'headshots' count. Each common infected killed with a headshot adds 1 for the "
               "attacker; at 10 they get adrenaline and their count goes back to 0.",
         nodes=[ev("death", "infected_death", 0, 0),
                N("if_head", "HL_NodeIf", 1, 0),
                N("add", "HL_NodeSetVariable", 2, 0, var_name="headshots", scope="PLAYER", op="ADD",
                  sockets={"value": 1.0}),
                N("count", "HL_NodeGetVariable", 3, 1, var_name="headshots", scope="PLAYER"),
                N("ten", "HL_NodeCompare", 4, 1, op="GREATER_EQUAL", sockets={"b": 10.0}),
                N("if_ten", "HL_NodeIf", 5, 0),
                fn("give", "player:GiveItem", 6, 0, {"p0": "adrenaline"}),
                N("reset", "HL_NodeSetVariable", 7, 0, var_name="headshots", scope="PLAYER", sockets={"value": 0.0})],
         links=[("death.happened", "if_head.in"), ("death.headshot", "if_head.condition"),
                ("if_head.true", "add.run"), ("death.attacker", "add.player"),
                ("death.attacker", "count.player"), ("count.result", "ten.a"),
                ("add.then", "if_ten.in"), ("ten.result", "if_ten.condition"),
                ("if_ten.true", "give.run"), ("death.attacker", "give.target"),
                ("give.then", "reset.run"), ("death.attacker", "reset.player")],
         steps=[("A headshot kill: +1 for the attacker", ["death", "if_head", "add"]),
                ("At 10: adrenaline, and start again", ["count", "ten", "if_ten", "give", "reset"])]),

    dict(title="Alarm Button Crescendo", level="Values",
         objects={"button": ("BUTTON", (0.0, 0.0, 1.0))},
         about="An objective tells the players what to do. Pressing the button starts a crescendo "
               "(hordes in waves, like the campaign's lifts and radios: edit its Stages), and "
               "surviving it completes the objective. The button must be held for 3 seconds.",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("goal", "HL_NodeObjective", 1, 0, text="Sound the alarm", done_text="You survived the alarm"),
                N("button", "HL_NodeButton", 0, 1, sockets={"object": "@button"}, once=True, hold=3.0,
                  text="Sounding the alarm..."),
                N("waves", "HL_NodeCrescendo", 1, 1, stages="PANIC 1, DELAY 10, PANIC 1, DELAY 10, PANIC 2"),
                N("hold", "HL_NodeMessage", 1, 2, text="Here they come: hold out!", seconds=5.0)],
         links=[("start.start", "goal.start"), ("button.pressed", "waves.start"), ("button.pressed", "hold.show"),
                ("waves.finished", "goal.complete")],
         steps=[("Tell them the goal", ["start", "goal"]), ("Press, survive the waves", ["button", "waves", "hold"])]),

    # ------------------------------------------------------------------ HUD & Director
    dict(title="Kill Counter on the HUD", level="HUD & Director",
         about="HUD Text puts text on the screen and keeps it up to date by itself: whatever is "
               "wired into Text is worked out again every moment. So it's shown once at map start, "
               "and each common killed only adds 1 to the 'kills' variable. (The HUD needs "
               "Hammerless's own game mode: Build & Play starts the map in it.)",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("count", "HL_NodeGetVariable", 0, 1, var_name="kills"),
                N("line", "HL_NodeFormatText", 1, 1, template="Zombies killed: {a}"),
                N("hud", "HL_NodeHudText", 2, 0, slot="2"),
                N("killed", "HL_NodeGameEvent", 0, 3, event="COMMON_KILLED"),
                N("add", "HL_NodeSetVariable", 1, 3, var_name="kills", op="ADD", sockets={"value": 1.0})],
         links=[("start.start", "hud.run"), ("count.result", "line.a"), ("line.result", "hud.text"),
                ("killed.happened", "add.run")],
         steps=[("Show the count (it updates itself)", ["start", "count", "line", "hud"]),
                ("Count each kill", ["killed", "add"])]),

    dict(title="Countdown to a Horde", level="HUD & Director",
         about="Leaving the safe room sets 'countdown' to 60 and starts a 1-second Timer (it starts "
               "stopped, Running off, until something fires its Start). Each tick takes 1 off. A When "
               "watches the countdown: at 0 it stops the timer, starts the horde and hides the HUD text.",
         nodes=[N("left", "HL_NodeGameEvent", 0, 0, event="LEFT_SAFE_ROOM", once=True),
                N("set", "HL_NodeSetVariable", 1, 0, var_name="countdown", sockets={"value": 60.0}),
                N("hud", "HL_NodeHudText", 2, 0, slot="2"),
                N("timer", "HL_NodeTimer", 3, 0, seconds=1.0, running=False),
                N("count", "HL_NodeGetVariable", 1, 1, var_name="countdown"),
                N("line", "HL_NodeFormatText", 2, 1, template="Horde in {a}"),
                N("tick", "HL_NodeSetVariable", 4, 0, var_name="countdown", op="ADD", sockets={"value": -1.0}),
                N("zero", "HL_NodeCompare", 1, 3, op="LESS_EQUAL", sockets={"b": 0.0}),
                N("when", "HL_NodeWhen", 2, 3, once=True),
                N("horde", "HL_NodeHorde", 3, 3),
                N("hide", "HL_NodeHudHide", 3, 4, slot="2")],
         links=[("left.happened", "set.run"), ("set.then", "hud.run"), ("count.result", "line.a"),
                ("line.result", "hud.text"), ("hud.then", "timer.start"), ("timer.tick", "tick.run"),
                ("count.result", "zero.a"), ("zero.result", "when.condition"),
                ("when.true", "horde.start"), ("when.true", "timer.stop"), ("when.true", "hide.run")],
         steps=[("Start the countdown", ["left", "set", "hud", "timer", "count", "line"]),
                ("Every second: one less", ["tick"]),
                ("At 0: stop, and the horde", ["zero", "when", "horde", "hide"])]),

    dict(title="The Director Eases Off", level="HUD & Director",
         about="Director Mood's Anger is how intense the AI Director thinks the game is (0 calm, 1 "
               "furious). While it's above 0.9 the map allows only 10 common infected at once; when "
               "it calms down, Reset puts the setting back to what the map had. When with Only Once "
               "off fires On True / On False every time the condition changes.",
         nodes=[N("mood", "HL_NodeDirectorMood", 0, 0),
                N("furious", "HL_NodeCompare", 1, 0, op="GREATER_EQUAL", sockets={"b": 0.9}),
                N("when", "HL_NodeWhen", 2, 0, once=False),
                N("fewer", "HL_NodeDirectorOption", 3, 0, key="CommonLimit", sockets={"value": 10.0}),
                N("back", "HL_NodeDirectorOption", 3, 1, key="CommonLimit", op="RESET")],
         links=[("mood.anger", "furious.a"), ("furious.result", "when.condition"),
                ("when.true", "fewer.run"), ("when.false", "back.run")],
         steps=[("Furious?", ["mood", "furious", "when"]), ("Fewer commons, then back to normal", ["fewer", "back"])]),

    # ------------------------------------------------------------------ overrides
    dict(title="No Friendly Fire", level="Overrides",
         about="Overrides answer the game's questions. Before any damage, the game asks 'allow "
               "this?'. Asked runs the nodes wired to it, and the Answer node at the end sends the "
               "answer back. A Script Value works out 'a survivor is hurting another survivor' as a "
               "line of the game's script language (Squirrel), and Not turns that into Allow. "
               "(Overrides need Hammerless's own game mode: Build & Play starts it.)",
         nodes=[N("ask", "HL_NodeOverride", 0, 0, hook="AllowTakeDamage"),
                N("friendly", "HL_NodeScriptValue", 1, 0, label="Survivor hurts survivor",
                  expr="a != null && b != null && a != b && a.IsPlayer() && b.IsPlayer() "
                       "&& a.IsSurvivor() && b.IsSurvivor()"),
                N("not", "HL_NodeBoolMath", 2, 1, op="NOT"),
                N("answer", "HL_NodeOverrideAnswer", 3, 0, hook="AllowTakeDamage")],
         links=[("ask.asked", "answer.run"), ("ask.attacker", "friendly.a"), ("ask.victim", "friendly.b"),
                ("friendly.result", "not.a"), ("not.result", "answer.answer")],
         steps=[("Allow the damage unless it's friendly fire", ["ask", "friendly", "not", "answer"])]),

    dict(title="Tanks Hit Twice as Hard", level="Overrides",
         about="The same damage question can also change how much damage is done: the Answer's "
               "New Damage. A Script Value gives double the damage when the attacker is a Tank "
               "(zombie type 8), and the damage as it was otherwise.",
         nodes=[N("ask", "HL_NodeOverride", 0, 0, hook="AllowTakeDamage"),
                N("double", "HL_NodeScriptValue", 1, 0, label="Double for Tanks",
                  expr="(a != null && a.IsPlayer() && a.GetZombieType() == 8) ? b * 2 : b"),
                N("answer", "HL_NodeOverrideAnswer", 2, 0, hook="AllowTakeDamage")],
         links=[("ask.asked", "answer.run"), ("ask.attacker", "double.a"), ("ask.damage", "double.b"),
                ("double.result", "answer.damage")],
         steps=[("Change the damage", ["ask", "double", "answer"])]),

    dict(title="Pistols Become Magnums", level="Overrides",
         about="When the map loads, the game asks about every weapon spawn: 'turn this into "
               "something else?'. The answer is the new weapon spawn's class, or nothing to keep it. "
               "Here every pistol spawn becomes a Magnum.",
         nodes=[N("ask", "HL_NodeOverride", 0, 0, hook="ConvertWeaponSpawn"),
                N("magnum", "HL_NodeScriptValue", 1, 0, label="Pistol to Magnum",
                  expr='a == "weapon_pistol" ? "weapon_pistol_magnum_spawn" : ""'),
                N("answer", "HL_NodeOverrideAnswer", 2, 0, hook="ConvertWeaponSpawn")],
         links=[("ask.asked", "answer.run"), ("ask.classname", "magnum.a"), ("magnum.result", "answer.answer")],
         steps=[("Swap pistols", ["ask", "magnum", "answer"])]),

    # ------------------------------------------------------------------ harder
    dict(title="!heal Chat Command", level="Hard",
         about="'player_say' fires when someone types in chat. Typing !heal heals that survivor to "
               "100 once per map: a Per Player variable remembers who has used it. Check order: "
               "the text is !heal AND they haven't used it (Not of the variable).",
         nodes=[ev("say", "player_say", 0, 0),
                text("cmd", "!heal", 0, 1),
                N("is_cmd", "HL_NodeCheck", 1, 1, op="EQUAL"),
                N("used", "HL_NodeGetVariable", 1, 2, var_name="healed", scope="PLAYER", value_kind="bool"),
                N("unused", "HL_NodeBoolMath", 2, 2, op="NOT"),
                N("both", "HL_NodeBoolMath", 3, 1, op="AND"),
                N("if", "HL_NodeIf", 4, 0),
                fn("heal", "entity:SetHealth", 5, 0, {"p0": 100.0}),
                N("mark", "HL_NodeSetVariable", 6, 0, var_name="healed", scope="PLAYER", value_kind="bool",
                  sockets={"value": True}),
                fn("tell", "ClientPrint", 7, 0, {"p1": 3.0, "p2": "Healed (once per map)"}),
                fn("no", "ClientPrint", 6, 2, {"p1": 3.0, "p2": "You already used !heal on this map"}),
                N("is_cmd2", "HL_NodeIf", 5, 2)],
         links=[("say.happened", "if.in"), ("say.text", "is_cmd.a"), ("cmd.result", "is_cmd.b"),
                ("say.userid", "used.player"), ("used.result", "unused.a"), ("is_cmd.result", "both.a"),
                ("unused.result", "both.b"), ("both.result", "if.condition"),
                ("if.true", "heal.run"), ("say.userid", "heal.target"), ("heal.then", "mark.run"),
                ("say.userid", "mark.player"), ("mark.then", "tell.run"), ("say.userid", "tell.p0"),
                ("if.false", "is_cmd2.in"), ("is_cmd.result", "is_cmd2.condition"),
                ("is_cmd2.true", "no.run"), ("say.userid", "no.p0")],
         steps=[("Typed !heal, and not used yet?", ["say", "cmd", "is_cmd", "used", "unused", "both", "if"]),
                ("Heal, remember, tell them", ["heal", "mark", "tell"]),
                ("Already used", ["is_cmd2", "no"])]),

    dict(title="Wave Arena", level="Hard",
         objects={"button": ("BUTTON", (0.0, 0.0, 1.0))},
         about="Press the button to start three waves, 20 seconds apart. Each wave, For Each runs "
               "once per survivor and spawns a Hunter for each one. The HUD shows the wave number "
               "(it updates itself), and a When stops the timer at the third wave.",
         nodes=[N("button", "HL_NodeButton", 0, 0, sockets={"object": "@button"}, once=True),
                N("reset", "HL_NodeSetVariable", 1, 0, var_name="wave", sockets={"value": 0.0}),
                N("hud", "HL_NodeHudText", 2, 0, slot="2"),
                N("wave_no", "HL_NodeGetVariable", 1, 1, var_name="wave"),
                N("line", "HL_NodeFormatText", 2, 1, template="Wave {a} of 3"),
                N("timer", "HL_NodeTimer", 3, 0, seconds=20.0, running=False),
                N("next", "HL_NodeSetVariable", 0, 3, var_name="wave", op="ADD", sockets={"value": 1.0}),
                N("each", "HL_NodeForEach", 1, 3, what="SURVIVORS"),
                N("hunter", "HL_NodeSpawn", 2, 3, what="hunter"),
                N("last", "HL_NodeCompare", 1, 5, op="GREATER_EQUAL", sockets={"b": 3.0}),
                N("when", "HL_NodeWhen", 2, 5, once=True),
                N("msg", "HL_NodeMessage", 3, 5, text="Last wave!", seconds=4.0)],
         links=[("button.pressed", "reset.run"), ("reset.then", "hud.run"), ("wave_no.result", "line.a"),
                ("line.result", "hud.text"), ("hud.then", "timer.start"), ("hud.then", "timer.now"),
                ("timer.tick", "next.run"), ("next.then", "each.run"), ("each.each", "hunter.spawn"),
                ("wave_no.result", "last.a"), ("last.result", "when.condition"),
                ("when.true", "timer.stop"), ("when.true", "msg.show")],
         steps=[("Start: wave 0, HUD, timer", ["button", "reset", "hud", "wave_no", "line", "timer"]),
                ("Each wave: a Hunter per survivor", ["next", "each", "hunter"]),
                ("At the third: stop", ["last", "when", "msg"])]),

    dict(title="Score Kept Across Chapters", level="Hard",
         about="A score on the HUD: 1 point per common, 10 per special. Keep Across Maps on Set "
               "Variable carries the score into the campaign's next chapter (the next map needs "
               "the same variable name).",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("score", "HL_NodeGetVariable", 0, 1, var_name="score"),
                N("line", "HL_NodeFormatText", 1, 1, template="Score: {a}"),
                N("hud", "HL_NodeHudText", 2, 0, slot="4"),
                N("common", "HL_NodeGameEvent", 0, 3, event="COMMON_KILLED"),
                N("add1", "HL_NodeSetVariable", 1, 3, var_name="score", op="ADD", keep=True,
                  sockets={"value": 1.0}),
                N("special", "HL_NodeGameEvent", 0, 4, event="SPECIAL_KILLED"),
                N("add10", "HL_NodeSetVariable", 1, 4, var_name="score", op="ADD", keep=True,
                  sockets={"value": 10.0})],
         links=[("start.start", "hud.run"), ("score.result", "line.a"), ("line.result", "hud.text"),
                ("common.happened", "add1.run"), ("special.happened", "add10.run")],
         steps=[("Show the score", ["start", "score", "line", "hud"]),
                ("Points, kept for the next map", ["common", "add1", "special", "add10"])]),

    dict(title="Ambush Ahead, Out of Sight", level="Hard",
         about="Every 30 seconds, look at the nav mesh within 1200 units of the leading survivor "
               "and spawn one Hunter on an area that is further along the map (400+ units of flow "
               "ahead) and that no survivor can see. NavMesh Areas in Radius gives a table of "
               "areas; For Each goes through it; the 'ambush' variable stops it after one spawn. "
               "This is how the Director itself picks spawn spots, built from the game's functions. "
               "It needs the map's flow (a path from the start to the end safe room): without it "
               "there is no leading survivor, and the first If stops it.",
         nodes=[N("timer", "HL_NodeTimer", 0, 0, seconds=30.0),
                N("none", "HL_NodeSetVariable", 1, 0, var_name="ambush", sockets={"value": 0.0}),
                fn("leader", "Director.GetHighestFlowSurvivor", 0, 2),
                N("has_lead", "HL_NodeCheck", 1, 1, op="IS_SET"),
                N("if_lead", "HL_NodeIf", 2, 0),
                fn("origin", "entity:GetOrigin", 1, 2),
                fn("areas", "NavMesh.GetNavAreasInRadius", 2, 2, {"p1": 1200.0}),
                N("each", "HL_NodeForEach", 3, 0, what="LIST"),
                fn("center", "area:GetCenter", 4, 3),
                fn("flow", "GetFlowDistanceForPosition", 5, 3),
                fn("lead_flow", "Director.GetFurthestSurvivorFlow", 4, 4),
                N("ahead_by", "HL_NodeMath", 5, 4, op="ADD", sockets={"b": 400.0}),
                N("ahead", "HL_NodeCompare", 6, 3, op="GREATER"),
                fn("seen", "area:IsPotentiallyVisibleToTeam", 5, 2, {"p0": 2.0}),
                N("hidden", "HL_NodeBoolMath", 6, 2, op="NOT"),
                N("spawned", "HL_NodeGetVariable", 5, 5, var_name="ambush"),
                N("first", "HL_NodeCompare", 6, 5, op="LESS", sockets={"b": 1.0}),
                N("ok1", "HL_NodeBoolMath", 7, 2, op="AND"),
                N("ok", "HL_NodeBoolMath", 7, 3, op="AND"),
                N("if", "HL_NodeIf", 8, 0),
                fn("spot", "area:FindRandomSpot", 8, 2),
                N("what", "HL_NodeMakeTable", 9, 2, fields="type: int, pos: vec", sockets={"f_type": 3.0}),
                fn("spawn", "ZSpawn", 9, 0),
                N("count", "HL_NodeSetVariable", 10, 0, var_name="ambush", op="ADD", sockets={"value": 1.0})],
         links=[("timer.tick", "none.run"), ("none.then", "if_lead.in"), ("leader.result", "has_lead.a"),
                ("has_lead.result", "if_lead.condition"), ("if_lead.true", "each.run"),
                ("leader.result", "origin.target"), ("origin.result", "areas.p0"), ("areas.result", "each.list"),
                ("each.item", "center.target"), ("center.result", "flow.p0"), ("flow.result", "ahead.a"),
                ("lead_flow.result", "ahead_by.a"), ("ahead_by.value", "ahead.b"),
                ("each.item", "seen.target"), ("seen.result", "hidden.a"),
                ("spawned.result", "first.a"),
                ("hidden.result", "ok1.a"), ("ahead.result", "ok1.b"), ("ok1.result", "ok.a"), ("first.result", "ok.b"),
                ("each.each", "if.in"), ("ok.result", "if.condition"),
                ("each.item", "spot.target"), ("spot.result", "what.f_pos"), ("what.table", "spawn.p0"),
                ("if.true", "spawn.run"), ("spawn.then", "count.run")],
         steps=[("Every 30 s, if there's a leader: the nav areas around them",
                 ["timer", "none", "leader", "has_lead", "if_lead", "origin", "areas", "each"]),
                ("Ahead, unseen, and none spawned yet?",
                 ["center", "flow", "lead_flow", "ahead_by", "ahead", "seen", "hidden", "spawned", "first", "ok1", "ok"]),
                ("Then spawn a Hunter there", ["if", "spot", "what", "spawn", "count"])]),
]


# ---------------------------------------------------------------- building

def all_examples():
    from .logic_examples_2 import EXAMPLES_2
    from .logic_examples_3 import EXAMPLES_3
    return EXAMPLES + EXAMPLES_2 + EXAMPLES_3


def _first_of(n):   # where each set starts in all_examples()
    from .logic_examples_2 import EXAMPLES_2
    return [0, len(EXAMPLES), len(EXAMPLES) + len(EXAMPLES_2)][n]


def _box(name, size, location, collection):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x, v.co.y, v.co.z = v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    collection.objects.link(obj)
    return obj


# plain meshes (metres); "SPOT" is an empty (a position), "ENTITY:<class>" a game entity
OBJECT_SHAPES = {"BUTTON": (0.3, 0.3, 0.3), "GATE": (3.0, 0.25, 3.0), "ROOM": (4.0, 4.0, 3.0),
                 "PAD": (3.0, 3.0, 1.0), "PLATFORM": (2.5, 2.5, 0.3), "WALL": (3.0, 0.3, 3.0),
                 "HEDGE": (3.0, 0.6, 2.5)}
SEE_THROUGH = ("ROOM", "PAD")      # volumes


def _objects(context, number, example):
    wanted = example.get("objects") or {}
    if not wanted:
        return {}
    coll = bpy.data.collections.new(f"Example {number:02d} objects")
    context.scene.collection.children.link(coll)
    base = context.scene.cursor.location.copy()
    made = {}
    for key, (shape, offset, *extra) in wanted.items():
        name, where = f"Example {number:02d} {key}", base + type(base)(offset)
        if shape.startswith("ENTITY:"):             # (shape, offset, keyvalues)
            from .ops import make_entity_object
            obj = make_entity_object(context, shape.split(":", 1)[1], where, collection=coll, name=name,
                                     keyvalues=extra[0] if extra else None)
        elif shape == "SPOT":
            obj = bpy.data.objects.new(name, None)
            obj.empty_display_type, obj.empty_display_size = "SINGLE_ARROW", 0.5
            obj.location = where
            coll.objects.link(obj)
        else:
            obj = _box(name, OBJECT_SHAPES[shape], where, coll)
            if shape in SEE_THROUGH:
                obj.display_type = "WIRE"
        made[key] = obj
    return made


def _value(v, objects):
    return objects[v[1:]] if isinstance(v, str) and v.startswith("@") else v


def _socket(node, ident, outputs):
    return next((s for s in (node.outputs if outputs else node.inputs) if s.identifier == ident), None)


def _place(node, x, y):
    if hasattr(node, "location_absolute"):      # Blender 4.4+: inside a frame, location is relative
        node.location_absolute = (x, y)
    else:
        node.location = (x, y)


def _note(tree, name, lines, x, y, width):
    """A frame with text in it (the Text is named after the example, so it can be read in full)."""
    t = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    t.clear()
    t.write("\n".join(lines))
    frame = tree.nodes.new("NodeFrame")
    frame.name = frame.label = name
    frame.text = t
    frame.shrink = False
    frame.width, frame.height = width, 60 + 22 * len(lines)
    frame.label_size = 16
    frame.location = (x, y)
    return frame


def build_example(context, index: int, problems: list | None = None):
    """Make example number index (0-based): its graph, notes and objects. Returns the tree. Sockets
    that aren't there (an entity's events need the game's definitions) go into problems."""
    problems = problems if problems is not None else []
    ex = all_examples()[index]
    number = index + 1
    title = f"Example {number:02d}: {ex['title']}"
    objects = _objects(context, number, ex)
    tree = new_tree(context)
    tree.name = title
    nodes = {}
    for spec in ex["nodes"]:
        node = tree.nodes.new(spec["type"])
        node.name = spec["id"]
        for k, v in spec["props"].items():
            setattr(node, k, _value(v, objects))
        if spec["label"]:
            node.label = spec["label"]
        for ident, v in spec["sockets"].items():
            sock = _socket(node, ident, False)
            if sock is None:
                problems.append(f"'{node.name}' has no input '{ident}'")
                continue
            sock.value = _value(v, objects)
        nodes[spec["id"]] = node
    for a, b in ex["links"]:
        (an, ao), (bn, bi) = a.split("."), b.split(".")
        out, inp = _socket(nodes[an], ao, True), _socket(nodes[bn], bi, False)
        if out is None or inp is None:
            problems.append(f"no wire from '{an}' {ao} to '{bn}' {bi}: its node has no such socket (entity "
                            f"events need the game's definitions: set the L4D2 folder, then connect it)")
            continue
        tree.links.new(out, inp)
    # steps: a frame around each group of nodes, numbered in the order things happen
    for i, (label, ids) in enumerate(ex.get("steps", []), 1):
        frame = tree.nodes.new("NodeFrame")
        frame.name = frame.label = f"{i}. {label}"
        frame.label_size = 14
        for nid in ids:
            nodes[nid].parent = frame
    for spec in ex["nodes"]:
        c, r = spec["pos"]
        _place(nodes[spec["id"]], c * COL, r * ROW)
    # what it does, above the graph
    cols = max(s["pos"][0] for s in ex["nodes"]) + 1
    width = max(600.0, min(cols * COL, 1200.0))
    lines = textwrap.wrap(ex["about"], int(width / 8.5))
    if objects:
        lines += [""] + textwrap.wrap("Its objects are in the collection " + f"'Example {number:02d} objects', "
                                      "at the 3D cursor: move them where you need them.", int(width / 8.5))
    lines += [""] + textwrap.wrap("Delete this graph (and its objects) when you don't want it in your map.",
                                  int(width / 8.5))
    _note(tree, title, lines, 0.0, 120.0 + 22 * len(lines) + 60, width)
    return tree


class HL_OT_logic_example(bpy.types.Operator):
    bl_idname = "hammerless.logic_example"
    bl_label = "Add Example"
    bl_description = "Add a ready-made logic graph to learn from (and any objects it needs, at the 3D cursor)"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, context, properties):
        examples = all_examples()
        if 0 <= properties.index < len(examples):
            return examples[properties.index]["about"]
        return cls.bl_description

    def execute(self, context):
        if not 0 <= self.index < len(all_examples()):
            return {"CANCELLED"}
        problems = []
        tree = build_example(context, self.index, problems)
        _show_tree(context, tree)
        if problems:
            self.report({"WARNING"}, f"Added '{tree.name}', but: " + "; ".join(problems))
        else:
            self.report({"INFO"}, f"Added '{tree.name}'")
        return {"FINISHED"}


def _examples_menu(idname, label, which):
    def draw(self, context):
        level = None
        last = _first_of(which + 1) if which < 2 else None
        for i, ex in list(enumerate(all_examples()))[_first_of(which):last]:
            if ex["level"] != level:
                if level is not None:
                    self.layout.separator()
                level = ex["level"]
                self.layout.label(text=level)
            op = self.layout.operator(HL_OT_logic_example.bl_idname, text=f"{i + 1:2d}. {ex['title']}")
            op.index = i
    return type(idname, (bpy.types.Menu,), {"bl_idname": idname, "bl_label": label, "draw": draw})


HL_MT_logic_examples = _examples_menu("HL_MT_logic_examples", "Examples", 0)
HL_MT_logic_examples_2 = _examples_menu("HL_MT_logic_examples_2", "Examples 2", 1)
HL_MT_logic_examples_3 = _examples_menu("HL_MT_logic_examples_3", "Examples 3", 2)
CLASSES = (HL_OT_logic_example, HL_MT_logic_examples, HL_MT_logic_examples_2, HL_MT_logic_examples_3)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
