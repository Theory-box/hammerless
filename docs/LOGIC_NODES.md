# Logic Nodes

Map logic (events, timers, buttons, gates, spawns, Director changes) is built as a node graph, like Blender's shader editor. Hammerless turns the graph into game entities and a VScript when the map builds.

## Getting started

1. *Logic Graphs > **New Logic Graph*** (or **Graph from Outputs** to turn existing entity outputs into a graph).
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
| **Values** | Value, Math (with bit operations for flags), Compare, Boolean Math, Random Value, Path Progress (furthest / average / last survivor, 0 to 1), Infected Count; Set / Get Variable (for the map, per player, or per copy of a node group; kept across maps if you like; Add adds on to a number or the end of a text), Format Text, Compare Values, Pick Value (one of two values by a condition), Text Check (contains / starts with / ends with / equals), Split Text (text into a list); Make / Break Vector, Vector Math; Make Table, Get Field |
| **Scene** | Object Info (picks a scene object to hand to other nodes), Entity Events (an object's own events, e.g. a door opening), Collision (solid for players and/or the nav), Get Entity Property (reads any property the game keeps on an entity, see below) |
| **Actions** | Move Over Time (Mover), Show / Hide Object, Horde, Crescendo, Spawn Zombie (a spot, or the game picks; *Only If Fewer Than*), Play Sound, Teleport Survivors, Camera (first person / the game's over-the-shoulder third person; Player empty: whoever set it off, or everyone), Fire Input (sends an input to an entity wired in, e.g. one just spawned), Set Entity Property (changes any property the game keeps on an entity, see below). **All Game Functions**: all 419 of the game's script functions, grouped (Player, Director, Nav Mesh, Entity, Find Entities, Spawning, Sound...) or found with *Search...* |
| **Director** | Director, Director Settings, Director Setting (any of the 124 Director settings, changed while playing), Director Mood (the Director's intensity, 0 calm to 1 furious, and warnings 60 s and 20 s before a mob) |
| **HUD & Messages** | Show Message, Objective, HUD Text, HUD Hide |
| **Overrides** | Override, Answer |
| **Groups** | Make Group (Ctrl+G), Ungroup (Ctrl+Alt+G), Group Input / Output (inside a group), and every node group in the file |
| **Script** | Script and Script Value (your own Squirrel code; the examples don't use them: everything they do is built from nodes) |
| **Examples**, **Examples 2**, **Examples 3** | 61 ready-made graphs to learn from (below) |

### Entity properties

**Get Entity Property** and **Set Entity Property** read and change what the game keeps on an entity: a player's health, speed (`m_flLaggedMovementValue`), gravity, field of view, revive count; any entity's outline glow, colour and see-through amount, model scale, skin, animation frame, collision; a weapon's clip and upgrades; and much more. The search button lists all 2,631 properties the game has (taken from the game itself), the ones worth knowing first as *Common*, with a description; set **For** to a class (player, prop_dynamic, infected...) to list only what it has. The node's sockets follow the property: a number, text, a vector or an entity, with an **Index** for the ones that hold a list (a player's `m_iAmmo`, one slot per ammo type). An entity without the property is left alone (Get gives 0 or nothing).

A few properties are put back by the game itself every moment, so a Set doesn't last: `m_Local.m_iHideHUD` (hiding HUD parts) is one. Set those again on a Timer. Example 55 (Speed Boost) uses both nodes; examples 56 to 61 each do something the game has no other way to do (glowing commons, a bottomless clip, the progress bar, no firing in a room, see-through survivors, tunnel vision).

Game functions that only work something out (Get Health, Find By Name...) just give a Result; actions (Give Item, Stagger...) run when an event wire arrives and then fire *Then*. Wire colours for script values: teal **text**, purple **vector**, pink **entity / player / nav area**, dark grey **any value**.

Script nodes (game functions, If, For Each, Set Variable, Script...) chain straight into each other: an event's player flows into the next node, and mix with the other nodes both ways. Inside a For Each, use script nodes for each item: an entity-wiring node fires a moment later, after the loop has moved on.

**Several wires into one input** all work (three buttons into one Counter). **Loops.** Blender draws a wire red when it goes back to a node earlier in the chain (a Timer whose own tick ends up stopping it). For event wires that is fine and works. A circle of value wires (a value worked out from itself) can't be, so it is left out and the build warns.

### Node groups

A node group is a logic graph of its own used like one node, like Blender's shader or Geometry Nodes groups. Select nodes and press **Ctrl+G**: they move into a new group and one **Group** node takes their place, with an input for each wire that came in and an output for each wire that went out. **Tab** goes into the selected group and back out; **Ctrl+Alt+G** puts its nodes back. Inside, **Group Input** gives what comes in and **Group Output** takes what goes out; the sidebar's *Group* tab adds, renames, reorders and removes them. Pick a group on any Group node (or Shift+A > Groups) to use it again: every copy is built separately, and values typed on a Group node's inputs reach the nodes inside. Set / Get Variable *For: This Group Copy* gives each copy its own variable. Several examples come with groups (Flashlight Battery, Muzzle Flash, Nuke...): Tab into them to see how they work.

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
| 14 | No Friendly Fire | Override, the 'Is a Survivor' group, Compare Values, Answer |
| 15 | Tanks Hit Twice as Hard | Answer's New Damage, Pick Value |
| 16 | Pistols Become Magnums | Override on weapon spawns, Text Check, Pick Value |
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
| 34 | Special Kill Feed | Split Text and Get Field (a name by number from a list), groups, Client Print |
| 35 | Clear the Commons | For Each common infected, Kill |
| 36 | Gas Can Counter | For Each entity of a class, a Timer with a random time |
| 37 | !tank Chat Command | Override Intercept Chat, Text Check, For Each player |
| 38 | Everyone Is a Charger | Override Convert Zombie Class, Should Play Boss Music (a typed answer) |
| 39 | Shotgun Start, No Snipers | Override Get Default Item, Allow Weapon Spawn |
| 40 | Carry the Crates | Override Can Pickup Object (survivors carry chosen props), Should Avoid Item |
| 41 | Console Command: boost | Override User Console Command, Apply Abs Velocity Impulse |

*Shift+A > Examples 3* (special modes and tricks):

| | Example | Shows |
|---|---|---|
| 42 | Power-Up: Nuke | A kill may drop a glowing pickup (spawned with Spawn Entity From Table); walking over it blows up the common infected within a Radius in a growing ring (the 'Explode Common' and 'Explosion' groups, Fire Input, Pick Value); a Delay keeps new ones from dropping for 30 s |
| 43 | Slow Motion Headshots | A headshot on a common (infected_death) or a special (player_death) slows the game to 30% for about 2 s with a Time Scale entity (func_timescale, no cheats), then back |
| 44 | Third Person Zone | Volume On Enter / On Leave into Camera nodes with Player empty: only the player who walked in or out switches |
| 45 | Third Person Everywhere | Game event player_spawn, its Userid wired into a Camera's Player |
| 46 | Random Zombie Sizes | For Each common, Random Value, Set Model Scale, hitboxes that follow the size |
| 47 | Third Person Muzzle Flash | The 'Muzzle Light' group used twice (a light for everything, an extra one for models), Ent Fire (inputs sent by name), Make Table |
| 49 | Swelling Pod | One node, Swelling Pods, with its settings on it (Burst When Near, Radius, Swell Time (Near), Shrink Time, Shooting Sets It Off, Shot: Swell First, Swell Time (Shot), Grow To, Burst Radius, Damage). Inside: a per-entity charge that rises while a pod is provoked and falls when it's left alone; the swell animation held at the charge (Set Playback Rate 0, NetProps m_flCycle) and the size to match (groups: Swelling Pods, Swelling Pod, Show Swelling, Explosion) |
| 50 | Reskinned Commons | A mannequin on the game's common-infected skeleton (Add > Game Skeleton); every common infected wears its model (For Each common, Get Model Name, Compare Values, Set Model) and keeps behaving as a common |
| 51 | Sleepers | Placed characters woken by a survivor close, a gunshot within hearing or a shot; a stand-up animation chosen from their pose; Replace With Common, Director.GetClosestSurvivor, RushVictim (groups: Sleeper, Wake Sleeper, Stand Up Animation, Replace With Common) |
| 52 | Husks | Placed characters that fall like a killed common when hit (Replace With Common, Take Damage) |
| 53 | Power Goes Out | Switchable baked lights (Light panel) driven from Entity Events: SetPattern to stutter, TurnOff with a horde, SetPattern m to bring the power back; lights sharing a name switch together |
| 54 | Lights Flicker While a Tank Lives | Game events tank_spawn / tank_killed, SetPattern on lights sharing a name, Director.IsTankInPlay with Not and If (steady only when no Tank is left) |
| 55 | Speed Boost | Set Entity Property on every survivor (speed, outline glow and its colour) for 10 s; Get Entity Property reads their health to top up anyone under 40 (For Each, Compare, If) |
| 56 | Sonar Pulse | Commons near a spot glow through walls for 5 s (they have no glow input): For Each common, Get Origin, Vector Math distance, Set Entity Property m_Glow.m_iGlowType / m_Glow.m_glowColorOverride |
| 57 | Bottomless Clip | For 10 s each shot puts its round back: game event weapon_fire, Get Entity Property m_hActiveWeapon then m_iClip1, Math, Set Entity Property; a true/false variable for the power-up |
| 58 | Progress Bar Gate | The game's own progress bar on every survivor for 5 s without holding Use (m_flProgressBarStartTime from Time, m_flProgressBarDuration), then a gate opens |
| 59 | No Guns in Here | Survivors near the room's middle can't fire (m_afButtonDisabled, the attack bit), set four times a second with Pick Value; a Volume shows a message on entering |
| 60 | Ghost Survivors | Every survivor see-through for 10 s (players have no colour input): m_nRenderMode 1 and m_clrRender with a low alpha, then back to normal |
| 61 | Tunnel Vision | Each survivor's field of view narrows as their health drops: m_iHealth through Math to m_iFOV (0 = their own setting at full health) |
| 48 | Flashlight Battery | The 'Flashlight Battery' group (with 'Flashlight Is On' / 'Flashlight Off' inside it), per-player variables, Math bit operations, text built up in a For Each, HUD Text |

`demo/hl_test_nuke.blend` (made by `tests/fixtures/nuke_test.py`) is the demo level with it, set so the first kill drops it, and 40 commons on the field.

Note: the game's `infected_death` event doesn't say which infected died (measured); use `infected_hurt` (a hit at least as big as its health left is a kill).

Examples 2 tested in game: 21-28, 30, 31, 34-37, 39-41 on a map, 29 and 33 on c2m1; 32 and 38 depend on the Director's own mobs and specials and weren't seen in a short test.

Tested in game: 7, 14, 16, 17 on a map; 20 on c2m1 (it needs the map's flow, a path from the start to the end safe room).

## Movers (gates, lifts, sliding doors)

Settings: direction, distance (*auto* = the object's own size in that direction), seconds. **Block Nav While Closed** is off by default. Turning it on hides the area behind the closed gate from the Director's wandering zombies; see [Nav Mesh & Zombie Spawns](NAV_AND_ZOMBIES.md#gates-and-nav-blocking).

## Hordes and crescendos

- **Horde** and **Crescendo** *On Finished* only fire for their own horde or crescendo. A Crescendo's *On Finished* fires once, after its **last** stage.
- When a crescendo ends, its Director settings stay in force (the game keeps them). They repeat your map-wide settings, so nothing changes there. But settings a **Director Settings** node applied before the crescendo are replaced; apply them again after the crescendo if you need them. Measured in game: loading Director settings at the very moment a crescendo ends makes the game start another panic event, so Hammerless doesn't do that for you.
- With the map-wide Director settings on, **Map Start** fires 1.1 s into the map (after those settings load), so a Director Settings node applied at map start isn't overwritten by them.

## Director control

*AI Director > Director Spawns* (with the AI Director box ticked) switches a type (Tank, Witch, each special) off for the Director, so only your graph spawns it.

## Debugging

*Settings > Debug > Debug Log* prints every wire as it fires (`HAMMERLESS_EVENT`) and each random roll to `left4dead2/console.log`.

## Cheats

When a graph uses something that only works with cheats on (a cheat console setting such as host_timescale, a cheat command such as z_spawn or give, or turning sv_cheats on), Build lists one warning for the graph naming its nodes: "Needs cheats: ...". In a normal game those do nothing; a map that turns cheats on itself makes the game announce it, turns achievements off for that game and lets players use any cheat meanwhile, and public servers usually don't allow it. The list of names is the game's own (hammerless/core/data/cheat_commands.txt, made by scripts/cheat_commands.py).
