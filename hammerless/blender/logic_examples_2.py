"""The second set of example graphs (21 on): Add > Examples 2. Between them and the first set, every
logic node is used somewhere, so building them in the game tests the nodes too."""
from __future__ import annotations

from .logic_examples import N, ev, fn, text

EXAMPLES_2 = [
    # ------------------------------------------------------------------ map parts
    dict(title="Two-Step Gate", level="Map Parts",
         objects={"button": ("BUTTON", (0.0, -1.5, 1.0)), "gate": ("GATE", (0.0, 1.5, 1.5))},
         about="Sequence does things in order, here 3 seconds apart: first an alarm and a warning, "
               "then the gate starts to open. The Move node's On Arrived fires when the gate is "
               "fully open, which stops the alarm.",
         nodes=[N("button", "HL_NodeButton", 0, 0, sockets={"object": "@button"}, once=True),
                N("steps", "HL_NodeSequence", 1, 0, seconds=3.0),
                N("alarm", "HL_NodeSound", 2, 0, sound="ambient/alarms/klaxon1.wav", everywhere=True),
                N("warn", "HL_NodeMessage", 2, 1, text="The gate is about to open", seconds=3.0),
                N("open", "HL_NodeMove", 2, 2, sockets={"object": "@gate"}, direction="up", seconds=5.0),
                N("go", "HL_NodeMessage", 3, 2, text="Go!", seconds=3.0)],
         links=[("button.pressed", "steps.in"), ("steps.then1", "alarm.play"), ("steps.then1", "warn.show"),
                ("steps.then2", "open.go"), ("open.arrived", "alarm.stop"), ("open.arrived", "go.show")],
         steps=[("Press: alarm, then 3 s later the gate", ["button", "steps", "alarm", "warn", "open"]),
                ("Fully open", ["go"])]),

    dict(title="Lever Lift", level="Map Parts",
         objects={"lever": ("BUTTON", (0.0, -2.0, 1.0)), "lift": ("PLATFORM", (0.0, 0.0, 0.15))},
         about="One lever, two directions. Branch remembers true or false ('the lift is up'): "
               "each press flips it and then tests it (Toggle, Then Test), sending the lift up (Go) "
               "or back down (Go Back). Move's On Arrived and On Back fire when the lift gets to "
               "the top or the bottom. The lever can be used again 5 seconds after each press.",
         nodes=[N("lever", "HL_NodeButton", 0, 0, sockets={"object": "@lever"}, once=False, reset=5.0),
                N("is_up", "HL_NodeBranch", 1, 0, initial=False),
                N("lift", "HL_NodeMove", 2, 0, sockets={"object": "@lift"}, direction="up", distance="160",
                  seconds=4.0),
                N("top", "HL_NodeMessage", 3, 0, text="The lift is at the top", seconds=3.0),
                N("bottom", "HL_NodeMessage", 3, 1, text="The lift is at the bottom", seconds=3.0)],
         links=[("lever.pressed", "is_up.toggle_test"), ("is_up.true", "lift.go"), ("is_up.false", "lift.back"),
                ("lift.arrived", "top.show"), ("lift.returned", "bottom.show")],
         steps=[("Each press: up if it's down, down if it's up", ["lever", "is_up", "lift"]),
                ("Where it got to", ["top", "bottom"])]),

    dict(title="Three Fuses", level="Map Parts",
         objects={"fuse1": ("BUTTON", (-2.0, -2.0, 1.0)), "fuse2": ("BUTTON", (0.0, -2.0, 1.0)),
                  "fuse3": ("BUTTON", (2.0, -2.0, 1.0)), "exit": ("BUTTON", (3.0, 1.0, 1.0)),
                  "gate": ("GATE", (0.0, 1.5, 1.5))},
         about="Three fuse buttons, each usable once. Counter counts the presses and fires On "
               "Reached at 3. The exit button is locked when the map starts (pressing it does "
               "nothing) and Unlock makes it work, so the gate only opens once all three are in.",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("fuse1", "HL_NodeButton", 0, 1, sockets={"object": "@fuse1"}, once=True),
                N("fuse2", "HL_NodeButton", 0, 2, sockets={"object": "@fuse2"}, once=True),
                N("fuse3", "HL_NodeButton", 0, 3, sockets={"object": "@fuse3"}, once=True),
                N("count", "HL_NodeCounter", 1, 2, count=3),
                N("ready", "HL_NodeMessage", 2, 3, text="All fuses in: use the exit", seconds=4.0),
                N("exit", "HL_NodeButton", 2, 0, sockets={"object": "@exit"}, once=True),
                N("gate", "HL_NodeMove", 3, 0, sockets={"object": "@gate"}, direction="up", seconds=4.0)],
         links=[("start.start", "exit.lock"), ("fuse1.pressed", "count.add"), ("fuse2.pressed", "count.add"),
                ("fuse3.pressed", "count.add"), ("count.reached", "exit.unlock"), ("count.reached", "ready.show"),
                ("exit.pressed", "gate.go")],
         steps=[("Three fuses", ["fuse1", "fuse2", "fuse3", "count", "ready"]),
                ("The exit, locked until then", ["start", "exit", "gate"])]),

    dict(title="Power First", level="Map Parts",
         objects={"generator": ("BUTTON", (-2.0, -2.0, 1.0)), "button": ("BUTTON", (2.0, -2.0, 1.0)),
                  "gate": ("GATE", (0.0, 1.5, 1.5))},
         about="The gate button only works once the generator is on. A Gate node lets events "
               "through only while it's open: it starts closed, and the generator opens it. The "
               "Objective tells the players what to do; its On Started and On Completed fire when "
               "it's shown and done.",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("goal", "HL_NodeObjective", 1, 0, text="Turn on the generator", done_text="Power is on"),
                N("hint", "HL_NodeMessage", 2, 0, text="The gate has no power", seconds=4.0),
                N("generator", "HL_NodeButton", 0, 1, sockets={"object": "@generator"}, once=True, hold=2.0,
                  text="Starting the generator..."),
                N("power", "HL_NodeGate", 1, 2, open=False),
                N("button", "HL_NodeButton", 0, 2, sockets={"object": "@button"}, once=False, reset=2.0),
                N("open", "HL_NodeMove", 2, 2, sockets={"object": "@gate"}, direction="up", seconds=4.0),
                N("done", "HL_NodeMessage", 2, 1, text="Now open the gate", seconds=4.0)],
         links=[("start.start", "goal.start"), ("goal.started", "hint.show"), ("generator.pressed", "power.open"),
                ("generator.pressed", "goal.complete"), ("goal.completed", "done.show"),
                ("button.pressed", "power.in"), ("power.out", "open.go")],
         steps=[("The goal", ["start", "goal", "hint"]), ("Generator on: power flows", ["generator", "done"]),
                ("The gate button, through the Gate", ["button", "power", "open"])]),

    dict(title="Alarm While Inside", level="Map Parts",
         objects={"room": ("ROOM", (0.0, 0.0, 1.5))},
         about="A Volume's other events: On First Enter (nobody was inside), On Leave (anyone walks "
               "out) and On Everyone Left. Two seconds after the first one walks in, an alarm plays "
               "from the room; if everyone leaves before then, Cancel stops the Delay, and the "
               "alarm stops when the room is empty.",
         nodes=[N("room", "HL_NodeVolume", 0, 0, sockets={"object": "@room"}, once=False),
                N("wait", "HL_NodeDelay", 1, 0, seconds=2.0),
                N("alarm", "HL_NodeSound", 2, 0, sockets={"object": "@room"}, sound="ambient/alarms/klaxon1.wav",
                  everywhere=False),
                N("left", "HL_NodeMessage", 1, 2, text="Someone left the alarm room", seconds=2.0)],
         links=[("room.first", "wait.in"), ("wait.out", "alarm.play"), ("room.empty", "wait.cancel"),
                ("room.empty", "alarm.stop"), ("room.leave", "left.show")],
         steps=[("In: alarm after 2 s; all out: stop", ["room", "wait", "alarm"]), ("Anyone leaving", ["left"])]),

    dict(title="Secret Wall", level="Map Parts",
         objects={"button": ("BUTTON", (-2.0, -2.0, 1.0)), "wall": ("WALL", (0.0, 1.5, 1.5)),
                  "hedge": ("HEDGE", (0.0, -1.0, 1.25))},
         about="Show / Hide Object makes a mesh appear or disappear, solid only while shown: the "
               "button hides the wall for good. Collision sets how an object collides all the time: "
               "the hedge is solid-looking but players and zombies walk straight through it, and "
               "the nav mesh runs through it (a hidden way in).",
         nodes=[N("button", "HL_NodeButton", 0, 0, sockets={"object": "@button"}, once=True),
                N("wall", "HL_NodeShowHide", 1, 0, sockets={"object": "@wall"}),
                N("msg", "HL_NodeMessage", 1, 1, text="Something moved...", seconds=3.0),
                N("hedge", "HL_NodeCollision", 0, 2, sockets={"object": "@hedge"}, players=False, nav=False)],
         links=[("button.pressed", "wall.hide"), ("button.pressed", "msg.show")],
         steps=[("The button removes the wall", ["button", "wall", "msg"]), ("A hedge you can walk through", ["hedge"])]),

    dict(title="Teleporter", level="Map Parts",
         objects={"pad": ("PAD", (0.0, 0.0, 0.5)), "there": ("SPOT", (8.0, 0.0, 0.1))},
         about="When the whole survivor team stands on the pad (a Volume's On All Survivors "
               "Inside), Teleport Survivors moves everyone to the 'there' spot. Move the spot "
               "(an empty) to where they should arrive.",
         nodes=[N("pad", "HL_NodeVolume", 0, 0, sockets={"object": "@pad"}, once=False),
                N("go", "HL_NodeTeleport", 1, 0, sockets={"object": "@there"}),
                N("msg", "HL_NodeMessage", 1, 1, text="Everyone on the pad: here we go", seconds=3.0)],
         links=[("pad.all_inside", "go.go"), ("pad.all_inside", "msg.show")],
         steps=[("All on the pad: send them there", ["pad", "go", "msg"])]),

    # ------------------------------------------------------------------ zombies & Director
    dict(title="Witch Guards the Gate", level="Zombies & Director",
         objects={"spot": ("SPOT", (0.0, 0.0, 0.1)), "gate": ("GATE", (0.0, 3.0, 1.5))},
         about="Spawn Zombie with a Where puts the zombie on that spot (an empty), and then its On "
               "Killed fires when that very one dies: killing the Witch opens the gate.",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("witch", "HL_NodeSpawn", 1, 0, sockets={"object": "@spot"}, what="witch"),
                N("open", "HL_NodeMove", 2, 0, sockets={"object": "@gate"}, direction="up", seconds=4.0),
                N("msg", "HL_NodeMessage", 2, 1, text="The Witch is dead: the gate opens", seconds=4.0)],
         links=[("start.start", "witch.spawn"), ("witch.killed", "open.go"), ("witch.killed", "msg.show")],
         steps=[("A Witch on the spot", ["start", "witch"]), ("Kill her: the gate opens", ["open", "msg"])]),

    dict(title="Never More Than Two Tanks", level="Zombies & Director",
         about="Every 45 to 90 seconds (a Timer with Up To: a random time each time), a Tank, but "
               "only while fewer than 2 are alive (Infected Count, Alive Now, against a Value). "
               "Spawn Zombie's Only If Fewer Than caps the whole game: no more than 4 Tanks ever, "
               "counting the Director's own.",
         nodes=[N("timer", "HL_NodeTimer", 0, 0, seconds=45.0, max_seconds=90.0),
                N("alive", "HL_NodeInfectedCount", 0, 1, what="tank", mode="ALIVE"),
                N("most", "HL_NodeValue", 0, 2, label="Most alive at once", sockets={"value": 2.0}),
                N("room", "HL_NodeCompare", 1, 1, op="LESS"),
                N("if", "HL_NodeIf", 2, 0),
                N("tank", "HL_NodeSpawn", 3, 0, what="tank", fewer_than=4)],
         links=[("timer.tick", "if.in"), ("alive.count", "room.a"), ("most.value", "room.b"),
                ("room.result", "if.condition"), ("if.true", "tank.spawn")],
         steps=[("Every 45-90 s: fewer than 2 alive?", ["timer", "alive", "most", "room", "if"]),
                ("A Tank (at most 4 this game)", ["tank"])]),

    dict(title="Quiet Until You Leave", level="Zombies & Director",
         about="Director Settings changes several of the AI Director's settings at once: at map "
               "start, no hordes, no specials and at most 5 commons. Leaving the safe room puts the "
               "map's own settings back, and 5 seconds later the Director node (the AI Director's "
               "own inputs, from the game) forces a horde.",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("quiet", "HL_NodeDirectorSettings", 1, 0, common_limit=5, max_specials=0, no_mobs="ON"),
                N("left", "HL_NodeGameEvent", 0, 2, event="LEFT_SAFE_ROOM", once=True),
                N("wait", "HL_NodeDelay", 1, 2, seconds=5.0),
                N("director", "HL_NodeDirector", 2, 2),
                N("msg", "HL_NodeMessage", 2, 3, text="Here they come", seconds=3.0)],
         links=[("start.start", "quiet.apply"), ("left.happened", "quiet.reset"), ("left.happened", "wait.in"),
                ("wait.out", "director.ForcePanicEvent"), ("wait.out", "msg.show")],
         steps=[("Quiet at the start", ["start", "quiet"]), ("Leave: back to normal, then a horde",
                                                              ["left", "wait", "director", "msg"])]),

    dict(title="Door Triggers a Horde", level="Zombies & Director",
         objects={"door": ("ENTITY:prop_door_rotating", (0.0, 0.0, 0.0))},
         about="Entity Events shows an entity's own events and inputs, straight from the game's "
               "definitions: here a door's On Open. Once lets only the first opening through, so "
               "the horde comes once. Put the door in a doorway.",
         nodes=[N("door", "HL_NodeObject", 0, 0, target="@door"),
                N("once", "HL_NodeOnce", 1, 0),
                N("horde", "HL_NodeHorde", 2, 0),
                N("msg", "HL_NodeMessage", 2, 1, text="That door was loud...", seconds=3.0)],
         links=[("door.OnOpen", "once.in"), ("once.out", "horde.start"), ("once.out", "msg.show")],
         steps=[("The first time the door opens: a horde", ["door", "once", "horde", "msg"])]),

    dict(title="Mob Warning", level="Zombies & Director",
         about="Director Mood warns before each of the Director's mobs. 60 seconds before: a "
               "message that stays up (Seconds 0) until Hide. 20 seconds before: the message goes "
               "and a blinking HUD text takes over, placed by hand (Own Position: X / Y / Width / "
               "Height as parts of the screen), hidden again 20 seconds later.",
         nodes=[N("mood", "HL_NodeDirectorMood", 0, 0),
                N("soon", "HL_NodeMessage", 1, 0, text="A mob is on its way", seconds=0.0),
                N("hud", "HL_NodeHudText", 1, 1, slot="9", blink=True, place=True, x=0.35, y=0.3, w=0.3, h=0.06,
                  sockets={"text": "MOB IN 20 SECONDS"}),
                N("wait", "HL_NodeDelay", 2, 1, seconds=20.0),
                N("hide", "HL_NodeHudHide", 3, 1, slot="9")],
         links=[("mood.mob60", "soon.show"), ("mood.mob20", "soon.hide"), ("mood.mob20", "hud.run"),
                ("hud.then", "wait.in"), ("wait.out", "hide.run")],
         steps=[("60 s before", ["mood", "soon"]), ("20 s before: blink until it comes", ["hud", "wait", "hide"])]),

    # ------------------------------------------------------------------ scripting
    dict(title="Bring Back Stragglers", level="Scripting",
         about="Every 5 seconds, each survivor more than 1500 units from the leading survivor is "
               "moved next to them. Vector Math gives the distance between two positions; Break "
               "Vector and Make Vector take the leader's position apart and put it back together "
               "16 units higher, and Set Origin moves the straggler there. Needs the map's flow to "
               "know who leads (otherwise the first If stops it).",
         nodes=[N("timer", "HL_NodeTimer", 0, 0, seconds=5.0),
                fn("leader", "Director.GetHighestFlowSurvivor", 0, 1),
                N("has_lead", "HL_NodeCheck", 1, 1, op="IS_SET"),
                N("if_lead", "HL_NodeIf", 1, 0),
                N("each", "HL_NodeForEach", 2, 0, what="SURVIVORS"),
                fn("lead_pos", "entity:GetOrigin", 3, 3),
                fn("my_pos", "entity:GetOrigin", 3, 2),
                N("dist", "HL_NodeVectorMath", 4, 2, op="DISTANCE"),
                N("far", "HL_NodeCompare", 5, 2, op="GREATER", sockets={"b": 1500.0}),
                N("if_far", "HL_NodeIf", 6, 0),
                N("parts", "HL_NodeBreakVector", 3, 5),
                N("up", "HL_NodeMath", 4, 5, op="ADD", sockets={"b": 16.0}),
                N("spot", "HL_NodeMakeVector", 5, 5),
                fn("move", "entity:SetOrigin", 7, 0),
                fn("tell", "ClientPrint", 8, 0, {"p1": 3.0, "p2": "You fell behind: brought back to the group"})],
         links=[("timer.tick", "if_lead.in"), ("leader.result", "has_lead.a"), ("has_lead.result", "if_lead.condition"),
                ("if_lead.true", "each.run"), ("leader.result", "lead_pos.target"), ("each.item", "my_pos.target"),
                ("my_pos.result", "dist.a"), ("lead_pos.result", "dist.b"), ("dist.value", "far.a"),
                ("each.each", "if_far.in"), ("far.result", "if_far.condition"),
                ("lead_pos.result", "parts.vector"), ("parts.x", "spot.x"), ("parts.y", "spot.y"),
                ("parts.z", "up.a"), ("up.value", "spot.z"),
                ("if_far.true", "move.run"), ("each.item", "move.target"), ("spot.result", "move.p0"),
                ("move.then", "tell.run"), ("each.item", "tell.p0")],
         steps=[("Every 5 s, each survivor (if there's a leader)", ["timer", "leader", "has_lead", "if_lead", "each"]),
                ("Too far from the leader?", ["lead_pos", "my_pos", "dist", "far", "if_far"]),
                ("Just above the leader", ["parts", "up", "spot"]),
                ("Move them, tell them", ["move", "tell"])]),

    dict(title="Special Kill Feed", level="Scripting",
         about="'player_death' gives who died (userid) and who killed them (attacker). When a "
               "survivor kills a special infected, Get Field looks up its name in a list (a Script "
               "Value makes the list; item 0 is unused, 1 = Smoker ... 8 = Tank) by its zombie "
               "type, Format Text makes the line and a Script node prints it to everyone.",
         nodes=[ev("death", "player_death", 0, 0),
                N("special", "HL_NodeScriptValue", 0, 2, label="A survivor killed a special",
                  expr="a != null && b != null && a.IsPlayer() && b.IsPlayer() && !a.IsSurvivor() && b.IsSurvivor()"),
                N("if", "HL_NodeIf", 1, 0),
                fn("type", "player:GetZombieType", 2, 3),
                N("names", "HL_NodeScriptValue", 2, 4, label="Names by zombie type",
                  expr='["", "Smoker", "Boomer", "Hunter", "Spitter", "Jockey", "Charger", "Witch", "Tank"]'),
                N("name", "HL_NodeGetField", 3, 3),
                N("line", "HL_NodeFormatText", 4, 2, template="{a} killed a {b}"),
                N("say", "HL_NodeScriptCode", 5, 0, label="Print to everyone", code="ClientPrint(null, 3, a);")],
         links=[("death.happened", "if.in"), ("death.userid", "special.a"), ("death.attacker", "special.b"),
                ("special.result", "if.condition"), ("death.userid", "type.target"), ("names.result", "name.table"),
                ("type.result", "name.key"), ("death.attacker", "line.a"), ("name.value", "line.b"),
                ("if.true", "say.run"), ("line.result", "say.a")],
         steps=[("A survivor killed a special?", ["death", "special", "if"]),
                ("Its name, the line, print it", ["type", "names", "name", "line", "say"])]),

    dict(title="Clear the Commons", level="Scripting",
         objects={"button": ("BUTTON", (0.0, 0.0, 1.0))},
         about="For Each can go through every common infected alive. The game's Kill function "
               "removes each one; Done fires after the last.",
         nodes=[N("button", "HL_NodeButton", 0, 0, sockets={"object": "@button"}, once=False, reset=10.0),
                N("each", "HL_NodeForEach", 1, 0, what="COMMONS"),
                fn("kill", "entity:Kill", 2, 0),
                N("msg", "HL_NodeMessage", 2, 1, text="Commons cleared", seconds=3.0)],
         links=[("button.pressed", "each.run"), ("each.each", "kill.run"), ("each.item", "kill.target"),
                ("each.done", "msg.show")],
         steps=[("Every common: removed", ["button", "each", "kill", "msg"])]),

    dict(title="Gas Can Counter", level="Scripting",
         objects={"can1": ("ENTITY:weapon_gascan", (-1.0, 0.0, 0.1)), "can2": ("ENTITY:weapon_gascan", (1.0, 0.0, 0.1))},
         about="For Each Entity of Class goes through every entity of a class: here every gas can. "
               "Every 1 to 2 seconds the 'cans' count is worked out again, and the HUD shows it "
               "(it updates itself).",
         nodes=[N("start", "HL_NodeMapStart", 0, 0),
                N("cans", "HL_NodeGetVariable", 0, 1, var_name="cans"),
                N("line", "HL_NodeFormatText", 1, 1, template="Gas cans: {a}"),
                N("hud", "HL_NodeHudText", 2, 0, slot="0"),
                N("timer", "HL_NodeTimer", 0, 3, seconds=1.0, max_seconds=2.0),
                N("zero", "HL_NodeSetVariable", 1, 3, var_name="cans", sockets={"value": 0.0}),
                N("each", "HL_NodeForEach", 2, 3, what="CLASS", sockets={"match": "weapon_gascan"}),
                N("add", "HL_NodeSetVariable", 3, 3, var_name="cans", op="ADD", sockets={"value": 1.0})],
         links=[("start.start", "hud.run"), ("cans.result", "line.a"), ("line.result", "hud.text"),
                ("timer.tick", "zero.run"), ("zero.then", "each.run"), ("each.each", "add.run")],
         steps=[("Show the count", ["start", "cans", "line", "hud"]),
                ("Count them again", ["timer", "zero", "each", "add"])]),

    # ------------------------------------------------------------------ overrides
    dict(title="!tank Chat Command", level="Overrides",
         about="Override Intercept Chat sees every chat line before it's shown (the text includes "
               "the speaker's name). A line with !tank spawns a Tank, and For Each Player gives "
               "every survivor adrenaline to face it.",
         nodes=[N("chat", "HL_NodeOverride", 0, 0, hook="InterceptChat"),
                N("is_cmd", "HL_NodeScriptValue", 1, 1, label="Has !tank", expr='a.find("!tank") != null'),
                N("if", "HL_NodeIf", 2, 0),
                N("tank", "HL_NodeSpawn", 3, 0, what="tank"),
                N("each", "HL_NodeForEach", 3, 1, what="PLAYERS"),
                fn("survivor", "player:IsSurvivor", 4, 2),
                N("if_s", "HL_NodeIf", 5, 1),
                fn("give", "player:GiveItem", 6, 1, {"p0": "adrenaline"})],
         links=[("chat.asked", "if.in"), ("chat.text", "is_cmd.a"), ("is_cmd.result", "if.condition"),
                ("if.true", "tank.spawn"), ("if.true", "each.run"), ("each.each", "if_s.in"),
                ("each.item", "survivor.target"), ("survivor.result", "if_s.condition"),
                ("if_s.true", "give.run"), ("each.item", "give.target")],
         steps=[("A chat line with !tank?", ["chat", "is_cmd", "if"]),
                ("A Tank, and adrenaline for every survivor", ["tank", "each", "survivor", "if_s", "give"])]),

    dict(title="Everyone Is a Charger", level="Overrides",
         about="Override Convert Zombie Class is asked what each special infected about to spawn "
               "should be (1 Smoker, 2 Boomer, 3 Hunter, 4 Spitter, 5 Jockey, 6 Charger): every "
               "special the Director sends becomes a Charger (Spawn Zombie nodes and script spawns "
               "aren't asked: they spawn what they say). Should Play Boss Music gets a fixed answer typed on the "
               "Override itself: Allow off, so no Tank or Witch music.",
         nodes=[N("ask", "HL_NodeOverride", 0, 0, hook="ConvertZombieClass"),
                N("charger", "HL_NodeScriptValue", 1, 1, label="Specials become Chargers",
                  expr="(a >= 1 && a <= 6) ? 6 : a"),
                N("answer", "HL_NodeOverrideAnswer", 2, 0, hook="ConvertZombieClass"),
                N("music", "HL_NodeOverride", 0, 3, hook="ShouldPlayBossMusic", sockets={"answer": False})],
         links=[("ask.asked", "answer.run"), ("ask.zombie_type", "charger.a"), ("charger.result", "answer.answer")],
         steps=[("Every special: a Charger", ["ask", "charger", "answer"]), ("No boss music", ["music"])]),

    dict(title="Shotgun Start, No Snipers", level="Overrides",
         about="Get Default Item is asked for the survivors' starting items one by one (0, 1, ...) "
               "until it gets no answer: a pistol and a pump shotgun. Allow Weapon Spawn is asked "
               "about every weapon spawn in the map: sniper rifles are removed.",
         nodes=[N("items", "HL_NodeOverride", 0, 0, hook="GetDefaultItem"),
                N("pick", "HL_NodeScriptValue", 1, 1, label="Pistol, then shotgun",
                  expr='a == 0 ? "weapon_pistol" : (a == 1 ? "weapon_pumpshotgun" : "")'),
                N("answer", "HL_NodeOverrideAnswer", 2, 0, hook="GetDefaultItem"),
                N("spawns", "HL_NodeOverride", 0, 3, hook="AllowWeaponSpawn"),
                N("no_sniper", "HL_NodeScriptValue", 1, 4, label="Not a sniper rifle",
                  expr='a.find("sniper") == null && a != "weapon_hunting_rifle"'),
                N("allow", "HL_NodeOverrideAnswer", 2, 3, hook="AllowWeaponSpawn")],
         links=[("items.asked", "answer.run"), ("items.slot", "pick.a"), ("pick.result", "answer.answer"),
                ("spawns.asked", "allow.run"), ("spawns.classname", "no_sniper.a"), ("no_sniper.result", "allow.answer")],
         steps=[("Starting items", ["items", "pick", "answer"]), ("No sniper spawns", ["spawns", "no_sniper", "allow"])]),

    dict(title="Carry the Crates", level="Overrides",
         objects={"crate": ("ENTITY:prop_physics", (0.0, 0.0, 0.3), {"model": "models/props_junk/wood_crate001a.mdl"})},
         about="Survivors normally can't pick up physics objects. Can Pickup Object is asked when one "
               "tries (Use on it): answering yes lets them carry wooden crates. Should Avoid Item is "
               "asked by the survivor bots about items: they leave propane tanks alone. For these "
               "two questions the game's own answer is no, and any yes wins.",
         nodes=[N("pickup", "HL_NodeOverride", 0, 0, hook="CanPickupObject"),
                N("is_crate", "HL_NodeScriptValue", 1, 1, label="A wooden crate",
                  expr='a != null && a.GetModelName().find("wood_crate") != null'),
                N("allow", "HL_NodeOverrideAnswer", 2, 0, hook="CanPickupObject"),
                N("bots", "HL_NodeOverride", 0, 3, hook="ShouldAvoidItem"),
                N("is_propane", "HL_NodeScriptValue", 1, 4, label="A propane tank", expr='a == "weapon_propanetank"'),
                N("avoid", "HL_NodeOverrideAnswer", 2, 3, hook="ShouldAvoidItem")],
         links=[("pickup.asked", "allow.run"), ("pickup.object", "is_crate.a"), ("is_crate.result", "allow.answer"),
                ("bots.asked", "avoid.run"), ("bots.classname", "is_propane.a"), ("is_propane.result", "avoid.answer")],
         steps=[("Crates can be carried", ["pickup", "is_crate", "allow"]),
                ("Bots leave propane alone", ["bots", "is_propane", "avoid"])]),

    dict(title="Console Command: boost", level="Overrides",
         about="Override User Console Command runs when a player types scripted_user_func in the "
               "console (bind it to a key: bind b \"scripted_user_func boost\"). 'boost' throws "
               "that player into the air with the game's Apply Abs Velocity Impulse.",
         nodes=[N("cmd", "HL_NodeOverride", 0, 0, hook="UserConsoleCommand"),
                text("boost", "boost", 0, 2),
                N("is_boost", "HL_NodeCheck", 1, 2, op="EQUAL"),
                N("if", "HL_NodeIf", 2, 0),
                fn("push", "entity:ApplyAbsVelocityImpulse", 3, 0, {"p0": (0.0, 0.0, 600.0)})],
         links=[("cmd.asked", "if.in"), ("cmd.command", "is_boost.a"), ("boost.result", "is_boost.b"),
                ("is_boost.result", "if.condition"), ("if.true", "push.run"), ("cmd.player", "push.target")],
         steps=[("scripted_user_func boost: up you go", ["cmd", "boost", "is_boost", "if", "push"])]),
]
