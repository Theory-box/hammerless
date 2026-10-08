# Logic Nodes

Map logic (events, timers, buttons, gates, spawns, Director changes) is built as a node graph, like Blender's shader editor. Hammerless turns the graph into game entities and a VScript when the map builds.

## Getting started

1. *World > Logic Graphs > **New Logic Graph*** (or **Graph from Outputs** to turn existing entity outputs into a graph).
2. Switch any editor to **L4D2 Logic**.
3. **Shift+A** adds nodes. Connect outputs to inputs.

## Wires

| Colour | Carries |
|---|---|
| **Orange** | Events: "when this happens, do that" |
| **Blue** | Objects (a mesh or entity in your scene) |
| **Grey** | Numbers, worked out live in the game |
| **Pink** | True / false |

## Nodes (Shift+A)

The Add menu has one place per purpose:

| Menu | Nodes |
|---|---|
| **Events** | Map Start, Volume (a trigger box), Button, Timer, Game Event. **All Game Events**: any of the game's 381 events (player hurt, item picked up, tank killed...), with its details as values (user ids already turned into players, entity ids into entities). |
| **Flow** | When (fires when a condition becomes true), If, Sequence, Delay, Once, Gate, Branch, Counter, Random, For Each (survivors, infected, players, commons, entities by class or name, a list) |
| **Values** | Value, Math, Compare, Boolean Math, Random Value, Path Progress (furthest / average / last survivor, 0 to 1), Infected Count; Set / Get Variable (for the map or per player, kept across maps if you like), Format Text, Compare Values; Make / Break Vector, Vector Math; Make Table, Get Field |
| **Scene** | Object Info (picks a scene object to hand to other nodes), Entity Events (an object's own events, e.g. a door opening), Collision (solid for players and/or the nav) |
| **Actions** | Move Over Time (Mover), Show / Hide Object, Horde, Crescendo, Spawn Zombie (a spot, or the game picks; *Only If Fewer Than*), Play Sound, Teleport Survivors. **All Game Functions**: all 419 of the game's script functions, grouped (Player, Director, Nav Mesh, Entity, Find Entities, Spawning, Sound...) or found with *Search...* |
| **Director** | Director, Director Settings, Director Setting (any of the ~140 Director settings, changed while playing), Director Mood (the Director's intensity, 0 calm to 1 furious, and warnings 60 s and 20 s before a mob) |
| **HUD & Messages** | Show Message, Objective, HUD Text, HUD Hide |
| **Overrides** | Override, Answer |
| **Script** | Script and Script Value (your own Squirrel code) |
| **Examples**, **Examples 2** | 41 ready-made graphs to learn from (below) |

Game functions that only work something out (Get Health, Find By Name...) just give a Result; actions (Give Item, Stagger...) run when an event wire arrives and then fire *Then*. Wire colours for script values: teal **text**, purple **vector**, pink **entity / player / nav area**, dark grey **any value**.

Script nodes (game functions, If, For Each, Set Variable, Script...) chain straight into each other: an event's player flows into the next node, and mix with the other nodes both ways. Inside a For Each, use script nodes for each item: an entity-wiring node fires a moment later, after the loop has moved on.

**Several wires into one input** all work (three buttons into one Counter). **Loops.** Blender draws a wire red when it goes back to a node earlier in the chain (a Timer whose own tick ends up stopping it). For event wires that is fine and works. A circle of value wires (a value worked out from itself) can't be, so it is left out and the build warns.

### Overrides

The game asks the map before it does some things: allow this damage? turn this weapon spawn into something else? An **Override** node picks the question; *Asked* runs your nodes when the game asks, and its other outputs are what was asked (the attacker, the weapon's class...). A fixed answer can be typed on the Override itself. To work the answer out from what was asked, end the Asked wires with an **Answer** node (set to the same question): its Allow / Answer (and New Damage, for damage) goes back to the game. With If nodes, different Answers can answer different cases.

Overrides about the map's own entities (weapon spawns...) are asked as the map loads; Hammerless creates its logic first so they are answered too. Most questions are "yes" unless an answer says no; **Can Pickup Object** (normally survivors can't carry physics props) and **Should Avoid Item** are "no" unless an answer says yes. **Convert Zombie Class** is only asked about the Director's own specials.

### Overrides and the HUD need Hammerless's game mode

The game only asks the map before doing things (Override nodes) and only shows a custom HUD in *scripted mode*, which plain co-op never turns on (tested). A map that uses these nodes gets **Co-op (Hammerless)**: co-op plus an empty mode script, installed as `addons/hammerless_mode.vpk`. Build & Play starts the map in it. The first time, close the game and Build & Play again: the game learns new modes when it starts.

## Examples

*Shift+A > Examples* adds a ready-made graph, from simple to hard, to learn from. Each comes with notes on what it does and how, numbered frames in the order things happen, and any objects it needs (a button, a gate, a room) in its own collection at the 3D cursor. They are ordinary graphs: they work in your map as they are; delete them when you don't want them.

| | Example | Shows |
|---|---|---|
| 1 | Hello Message | Map Start, Delay, Show Message |
| 2 | Horde After the Safe Room | Game Event, Horde's On Finished |
| 3 | Button Opens a Gate | Button and Move turning plain meshes into map parts |
| 4 | Tank in a Room | Volume, Spawn Zombie |
| 5 | A Random Special Every Minute | Timer, Random |
| 6 | A Tank Somewhere Along the Way | Path Progress, Random Value, Compare, When |
| 7 | Pills for a Gas Can | Game event details, If, Compare Values, Give Item, Client Print |
| 8 | Low Health Warning | Boolean Math, a value function (Is Survivor) |
| 9 | Headshot Rewards | Per-player variables |
| 10 | Alarm Button Crescendo | Objective, a hold-to-use Button, Crescendo |
| 11 | Kill Counter on the HUD | HUD Text that updates itself |
| 12 | Countdown to a Horde | A stopped Timer, a countdown variable, When |
| 13 | The Director Eases Off | Director Mood, Director Setting set / reset |
| 14 | No Friendly Fire | Override, Script Value, Answer |
| 15 | Tanks Hit Twice as Hard | Answer's New Damage |
| 16 | Pistols Become Magnums | Override on weapon spawns |
| 17 | !heal Chat Command | Chat event, per-player flags, game functions in a chain |
| 18 | Wave Arena | For Each survivor, HUD, a Timer stopped by When |
| 19 | Score Kept Across Chapters | Keep Across Maps |
| 20 | Ambush Ahead, Out of Sight | Nav areas around the leader, flow, visibility, Make Table, ZSpawn |

*Shift+A > Examples 2* has 21 more, chosen so that between the two sets every node is used somewhere:

| | Example | Shows |
|---|---|---|
| 21 | Two-Step Gate | Sequence, Play Sound, Move's On Arrived |
| 22 | Lever Lift | Branch (Toggle, Then Test), Move Go Back / On Back |
| 23 | Three Fuses | Counter, Button Lock / Unlock |
| 24 | Power First | Gate, Objective's On Started / On Completed, a hold-to-use Button |
| 25 | Alarm While Inside | Volume On Leave / On Everyone Left, Delay Cancel, a sound from an object |
| 26 | Secret Wall | Show / Hide Object, Collision |
| 27 | Teleporter | Volume On All Survivors Inside, Teleport Survivors |
| 28 | Witch Guards the Gate | Spawn Zombie at a spot, On Killed |
| 29 | Never More Than Two Tanks | Infected Count, Value, Spawn's Only If Fewer Than, a random Timer |
| 30 | Quiet Until You Leave | Director Settings, the Director node's own inputs |
| 31 | Door Triggers a Horde | Entity Events (a door's On Open), Once |
| 32 | Mob Warning | Director Mood's mob warnings, Show Message until Hide, HUD Text blinking at its own position |
| 33 | Bring Back Stragglers | For Each survivor, Vector Math, Break / Make Vector, Set Origin |
| 34 | Special Kill Feed | Get Field (a name by number from a list), Script |
| 35 | Clear the Commons | For Each common infected, Kill |
| 36 | Gas Can Counter | For Each entity of a class, a Timer with a random time |
| 37 | !tank Chat Command | Override Intercept Chat, For Each player |
| 38 | Everyone Is a Charger | Override Convert Zombie Class, Should Play Boss Music (a typed answer) |
| 39 | Shotgun Start, No Snipers | Override Get Default Item, Allow Weapon Spawn |
| 40 | Carry the Crates | Override Can Pickup Object (survivors carry chosen props), Should Avoid Item |
| 41 | Console Command: boost | Override User Console Command, Apply Abs Velocity Impulse |

Examples 2 tested in game: 21-28, 30, 31, 34-37, 39-41 on a map, 29 and 33 on c2m1; 32 and 38 depend on the Director's own mobs and specials and weren't seen in a short test.

Tested in game: 7, 14, 16, 17 on a map; 20 on c2m1 (it needs the map's flow, a path from the start to the end safe room).

## Movers (gates, lifts, sliding doors)

Settings: direction, distance (*auto* = the object's own size in that direction), seconds. **Block Nav While Closed** is off by default. Turning it on hides the area behind the closed gate from the Director's wandering zombies; see [Nav Mesh & Zombie Spawns](NAV_AND_ZOMBIES.md#gates-and-nav-blocking).

## Hordes and crescendos

- **Horde** and **Crescendo** *On Finished* only fire for their own horde or crescendo. A Crescendo's *On Finished* fires once, after its **last** stage.
- When a crescendo ends, its Director settings stay in force (the game keeps them). They repeat your map-wide settings, so nothing changes there. But settings a **Director Settings** node applied before the crescendo are replaced; apply them again after the crescendo if you need them. Measured in game: loading Director settings at the very moment a crescendo ends makes the game start another panic event, so Hammerless doesn't do that for you.
- With the map-wide Director settings on, **Map Start** fires 1.1 s into the map (after those settings load), so a Director Settings node applied at map start isn't overwritten by them.

## Director control

*World > AI Director > Director Spawns* (with the AI Director box ticked) switches a type (Tank, Witch, each special) off for the Director, so only your graph spawns it.

## Debugging

*Settings > Debug > Debug Log* prints every wire as it fires (`HAMMERLESS_EVENT`) and each random roll to `left4dead2/console.log`.
