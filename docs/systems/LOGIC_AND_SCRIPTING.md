# Logic and Scripting

How Hammerless turns a node graph in Blender into behaviour in Left 4 Dead 2: what the nodes compile to, how we mapped the game's script interface, why some maps need Hammerless's own game mode, how node groups work, and how every example is checked in the running game.

For the list of nodes and how to use each one, see [Logic Nodes](../LOGIC_NODES.md). For the Director, hordes and crescendos, see [Hordes & Events](../HORDE_EVENTS.md). This page is about how the system works underneath.

## The idea in one paragraph

You wire nodes in the **L4D2 Logic** editor: events (a button pressed, a survivor hurt), flow (Delay, If, Counter, For Each), values (Math, Compare, variables) and actions (open a gate, spawn a Tank, change a Director setting). When the map builds, each graph becomes two things the game already understands: ordinary **entities with outputs** ("when X happens, tell Y to do Z", the same I/O Hammer mappers wire by hand), and one generated **VScript** (Squirrel) file for everything entity I/O can't do. Nothing runs in Blender at play time; the game runs what was built.

## Design principle: generic building blocks

Nodes are small and general. A specific behaviour ("drop a pickup on a random kill, then wait 30 s") is built by wiring existing nodes (Delay, Random Value, Math, Set / Get Variable, Compare, If), not by adding a setting to a node.

The reason is control. Every behaviour stays visible and rewirable in the graph. A whole behaviour that is reused becomes a **node group**: a graph of plain nodes that looks like one node from outside, and that you can Tab into and change. The Swelling Pod is one node on the canvas, but inside it is made of ordinary nodes.

The same rule applies to code. The **Script** and **Script Value** nodes (your own Squirrel) exist, but none of the examples use them. Examples that once hid their logic in a script block (Flashlight Battery, Third Person Muzzle Flash, Power-Up: Nuke and others) were rebuilt from nodes. Where a piece was missing, it became a new generic node: Pick Value, Fire Input, Text Check, Split Text, and bit operations on Math.

## From graph to game

### Experimental bot action graphs

In the L4D2 Logic editor, **Add → Bot Actions → Freeze Commons When Watched** adds a complete wired example. It finds common infected, checks whether a living human survivor sees each one, and requests an editable freeze action. The common action pauses native AI updates and animation with managed property overrides. In-game tests verified stationary holds, looking-away release, repeat cycles, damage while frozen, and cleanup after lethal damage. Special infected have a separate experimental action; changing the Find Bots selection does not change which action is connected.

**Bot Action Loop** owns the repeated updates. Wire a Find Bots query into Bots, an event into Start, and its Each Bot output into Apply Bot Action. Wire Current Bot explicitly into Apply's Bot input. **Apply Bot Action** takes an Action socket, not an action dropdown. Enabled false, a bot leaving the selection, or stopping the loop releases that action's managed overrides. Next means the request was collected; After Update runs after requests have been applied.

Click **Edit Action** to change an action's Start, Update and Stop graphs. Use ordinary **Group Input** sockets for parameters, and Get/Set Action State for values that belong to that bot's particular action instance. Override Bot Property temporarily owns a property or selected flag bits and restores its saved value when released, provided another system has not changed it. Bot Command issues Move, Attack, Retreat or Reset commands to supported player bots; commands are not automatically undone. Actions that claim conflicting channels or properties cannot run together.

Use **New Editable Action** to create another asset, or pick an existing action in the Bot Actions menu. These graphs are synchronous: Delay, timers and arbitrary script/event actions inside the loop's per-bot path or lifecycle are rejected at build time. Bot sockets reject props and generic entities. An action's declared Supports setting also limits which bots may run it.

Disjoint bot selections and action types now stop the build with a descriptive error. On file load, an untouched original special-infected preset connected to a Commons loop is replaced with the common action; edited assets and other consumers of shared actions are preserved. The common preset opts its scheduling and animation overrides into **Restore on Death**, allowing death processing to resume without running authored Stop actions on dead bots.

The compiler, lifecycle runtime, typed group boundaries, and Blender save/reload are tested automatically. Source's in-game save/load continuation, multiplayer behavior and the special-infected preset remain unverified.

### The node editor

The editor lives in `hammerless/blender/logic.py`. It is a custom Blender node tree with its own socket types, coloured by what they carry (events, objects, numbers, true / false, text, vectors, entities; see [Logic Nodes](../LOGIC_NODES.md#wires)). When you build, the editor hands the compiler plain data: nodes (a kind, its settings, maybe a scene object and its position) and wires (from node and socket, to node and socket).

**Node headers show the real type.** A node's header always starts with what kind of node it is, followed by a note or what it is set to: "Text Check: A pod?", "Script Call: Get Model Name". Example titles are a note after the type, so you can always tell what a node does without opening it. Files from before this change convert when opened.

### The compiler

The compiler is `hammerless/core/logic.py`. For each graph it:

1. **Turns nodes into entities.** A Volume becomes a trigger, a Button turns a plain mesh into a `func_button`, Move makes a `func_movelinear`, Show / Hide and Collision make toggleable brushes. Timers, Delays and relays become the game's logic entities.
2. **Turns event wires into outputs.** Each wire is one output row on the entity that fires it.
3. **Writes one map script** for the rest: game events, For Each loops, variables, maths, Director settings, HUD text, overrides, and the game's script functions. All graphs on a map share one script, `scripts/vscripts/hammerless/logic_<map>.nut`, run by a `logic_script` entity named `hl_logic`.

Script nodes (game functions, If, For Each, Set Variable...) chain straight into each other inside the script; entity nodes and script nodes mix freely in both directions.

Some details that came from testing in the game:

- **The logic script is the first entity in the map.** Entities run their scripts as they are created, in order, and the game asks some override questions (weapon spawns) while it creates the others. With `hl_logic` later in the list, early spawns got the game's default answer.
- **Map Start waits 1.1 s** when the map-wide Director settings are on, so a Director Settings node at map start isn't overwritten by them.
- **Game events** are registered on the script's own scope, the pattern that works in L4D2. Event details come through as values, with user ids already turned into players and entity ids into entities.
- **Loops.** Event wires may loop back (a Timer whose tick ends up stopping it). A circle of value wires (a value worked out from itself) can't be computed, so it is dropped with a warning. Script nodes wired in an endless loop stop after 32 calls with a console message.
- **Cheats.** If a graph uses a cheat-only command or console setting, Build gives one "Needs cheats" warning naming its nodes. The list of cheat-only names comes from the game itself.

### Variables: map, per player, per entity, per group copy

Set / Get Variable has a scope:

| Scope | One value for |
|---|---|
| Map | The whole map |
| Per Player | Each player, or each entity wired into Player (a prop, an infected) |
| This Group Copy | Each Group node using the group |

Per-entity values are how the Swelling Pod keeps a charge for each pod and how a Husk remembers it already fell. Their keys are kept apart from players' keys. A map variable can also be **kept across maps**; only the variables marked to be kept, and only plain values, are saved.

## Mapping the game's script interface

To offer "all of VScript" as nodes, we needed the game's own list of what exists. We took it from the game rather than from wikis.

### Functions: dumped with developer mode

The console command `script_help` prints every documented native function with a typed signature, but only when the game was started with developer mode (`-dev +developer 1`; the developer setting is hidden in the retail game). `scripts/vscript_catalog.py dump` starts the game that way and saves the output; `build` turns it into `hammerless/core/data/vscript_api.json`.

The catalogue currently holds **419 functions** and **381 game events**. Functions are grouped for the Add menu by the class they belong to (Director, Nav Mesh, Entity, Player, NetProps, Convars...) or by name (Spawning, Sound, HUD, Debug Drawing...). They appear under **All Game Functions**, searchable.

Game events and their field types come from the game's own `resource/*events.res` files inside its VPKs. They appear under **All Game Events**.

The catalogue keeps only facts about the interface: names, signatures, field types. The descriptions and input names are our own, written in `scripts/vscript_descriptions.txt`, not Valve's help text.

### DirectorOptions and the decision hooks

The Director's settings and the game's "ask the map first" hooks are not in `script_help`. Their names were found as strings in the game's `server.dll`, which reads each one by name. `hammerless/core/director_options.py` lists them with our own descriptions: 124 Director settings (pacing, spawning, limits...) for the Director Setting node, and the hooks for the Override node:

| Hook | The game asks |
|---|---|
| AllowTakeDamage | Allow this damage? (and how much) |
| InterceptChat | Someone typed in chat |
| UserConsoleCommand | A player ran a console command |
| AllowWeaponSpawn | May this weapon or item spawn? |
| ConvertWeaponSpawn | Turn this weapon spawn into another? |
| ConvertZombieClass | Change this special's class? |
| ShouldAvoidItem | Should bots avoid this item? |
| GetDefaultItem | What does a survivor start with? |
| CanPickupObject | May a survivor carry this prop? |
| ShouldPlayBossMusic | Play the Tank or Witch music? |

Several Override nodes can answer the same question. Most questions are "yes unless any answer says no". **Can Pickup Object** and **Should Avoid Item** are the reverse: "no unless any answer says yes". Measured: **Convert Zombie Class** is only asked about the Director's own specials, not ones a script spawns.

Director settings changed while playing go to the top-most DirectorOptions table that exists; the game chains its tables and re-reads them each think. Map-wide settings are written at spawn into the map's own table. A crescendo replaces the local table and leaves it empty when it ends; the map-wide ones stay in force. See [Hordes & Events](../HORDE_EVENTS.md) for what that means in practice.

### Entity properties (NetProps)

Scripts can read and write an entity's internal properties by name with the game's `NetProps` functions, but the game has no list of the names. We built one from the game itself (`scripts/netprop_catalog.py`):

- **The networked properties** come from a recorded demo. Every demo starts with the server's full property tables, the ones a client needs to read entity updates: 407 tables across 278 server classes. Their bit-packed layout had to be measured. L4D2 stores a numeric property's bit count in 6 bits where other Source games use 7, and its demos carry 4 split-screen command blocks per packet. The names are flattened the way NetProps takes them: base classes merged, containers like `localdata` looked inside, nested tables as `m_Local.m_iHideHUD`, lists by name with a size.
- **The server-only properties** (NetProps reads those too, e.g. a player's `m_flGravity` or any entity's `m_iName`) and **which class has what** come from asking the running game. One entity of each of the 382 classes the game's entity definitions name is spawned out of sight. Every candidate name (the networked ones plus every `m_...` string in the game's server code) is tried on it with `HasProp`, and `GetPropType` and `GetPropArraySize` are kept. 335 classes could be made this way. 3 crash or hang the game (the area portals and `trigger_finale`) and are skipped. The result: 2,631 properties with their type, list size and classes. The catalogue records the game build it came from (PatchVersion 2.2.4.3, demo protocol 4, network protocol 2100); after a game update, run `scripts/netprop_catalog.py` again.
- **Checked:** on a live map, the game accepted 1,617 of 1,629 flattened names on 14 entity classes. The 12 misses were a misidentified class, not wrong names. Each of the 48 described properties was set to a test value and read back a second later. All stuck except `m_Local.m_iHideHUD`, which the game rewrites itself. The `Set...Array` functions take the value before the index (checked).

**Get Entity Property** and **Set Entity Property** use the catalogue: a searchable picker (the described ones first), sockets typed from the property, an Index for lists. They compile to two helpers that do nothing (or give 0) when the entity is gone or doesn't have the property. Examples 55 to 61 (Speed Boost, Sonar Pulse, Bottomless Clip, Progress Bar Gate, No Guns in Here, Ghost Survivors, Tunnel Vision) use them and are checked in game, as is 62 (Top-Down Camera: each survivor looks through a camera of their own; m_takedamage puts back what the camera's Enable changes).

### Why a game mode, and why an addon VPK

Measured in the game: the hooks and the custom HUD only work in **scripted mode**, and plain co-op never turns it on. Scripted mode needs a game mode with its own script. The hooks must also exist when the mode starts, not later.

So a map that uses Override or HUD nodes gets **Co-op (Hammerless)**: base co-op plus a nearly empty mode script. The map gets `scripts/vscripts/<map>_hammerless.nut`, which scripted mode loads as it starts. That file defines the hook functions and loads the map's logic early, because the game asks about weapon spawns and starting items before any entity's script has run.

The mode itself has to be **packed**. The game ignores a loose `modes/*.txt` file, and it ignores a mode given on the command line. It only learns modes from its packed files and addon VPKs, read when it starts. Hammerless therefore installs a small addon, `addons/hammerless_mode.vpk`. Build & Play starts the map in that mode. The first time, the game has to be closed and started again to learn it.

Two more traps found on the way:

- **Script globals outlive the map.** Hooks left from the previous map answered before the new map's logic ran. Each hook now checks that the logic belongs to this map (`::HL_Map`) and runs in the logic script's scope.
- **Nested damage.** Damage dealt from inside a damage override asks the same question again. The inner call puts the outer call's details and answer back when it ends.

## Node groups

Node groups work like Blender's shader and Geometry Nodes groups (`hammerless/blender/logicgroups.py`):

- **Ctrl+G** moves the selected nodes into a new group, with an input for each wire that came in and an output for each wire that went out.
- **Tab** goes in and out; **Ctrl+Alt+G** ungroups.
- Inside, **Group Input** and **Group Output** carry what crosses the edge. The sidebar's *Group* tab adds, renames, reorders and removes them.
- **Exposed inputs**: a value typed on a Group node's input reaches the nodes inside. The Swelling Pod's settings (Radius, Swell Time, Burst Radius, Damage...) are exactly that.

**At build, every Group node is flattened.** The nodes inside are copied in under their own names (`<group node>/<node>`), so each copy is separate. Wires into a group input go straight to what the Group Input feeds; wires out come straight from what feeds the Group Output. The game never sees a group.

**Cleanup.** Re-adding an example reuses its groups instead of making `.001` copies, unless you edited them. Stale example groups that nothing uses are removed. **Remove Unused Groups** (Add > Groups) deletes every logic group no graph uses, repeating until none is left, and gives `.001` copies their plain names back.

## World queries

The Logic editor's **Add > World Queries** menu provides reusable map information for normal graphs,
Bot Action Loops, and editable action groups:

| Node | Result |
|---|---|
| Nearest Nav Area | Area and its center near a position; optional ground and line-of-sight checks |
| Nearby Nav Areas | Areas sorted by distance, plus count and nearest center |
| Nav Area at Index | One area from that list (zero-based) |
| Nav Area Info | Center, ID, team blocking, and underwater state |
| Nav Path Exists | Whether two areas have a nav route within the distance budget, respecting blockers by default |
| Baked Ambient Light | Linear ambient RGB and luminance at a position, from the compiled BSP's ambient probes |

These are **Run → Next** queries. Run each query before consuming its outputs: the outputs read a
captured result, rather than repeating the search. Inside bot loops/actions, query results are local
to that execution and the compiler rejects reading a query before it runs. **Available** distinguishes
missing map data from a valid negative result; nav **Found** distinguishes a missing area from a valid
area with no route. Nav handles have their own sockets, so they cannot be passed to entity operations.
**For Each**, set to **Nav Areas**, iterates the typed list; its Item belongs on the Each branch.

Example: Nearby Nav Areas → For Each (Nav Areas) → Nav Area Info → Baked Ambient Light, using the
area center plus an eye-height offset as Position. Check Available before comparing Brightness, and
check Nav Path Exists before choosing a candidate destination. Existing Trace Line and Seen By nodes
provide actual visibility tests; VVIS is not a replacement for a line trace. A nav path does not
guarantee an individual actor can or will follow it.

Queries cap nav search radius at 4096 units, returned lists at 128 areas, and path distance at 32768
units. These limits do not make engine searches constant-time. Avoid searching a large region for
every zombie every frame; query shared destinations less frequently and use the loop interval.

### Baked ambient data

`core/ambientquery.py` exports existing BSP ambient cubes only when a graph uses the light query.
It adds no ray tracing or bake pass. After a successful Build, `blender/ops.py` injects the current
compiled data into the map script; export-only, missing probes, and unsupported/corrupt data leave
Available false. Removing the graph removes its script through the normal file manifest. Failed
builds do not replace the old map's script/data.

The runtime selects the containing BSP leaf and interpolates that leaf's probes, averaging the six
cube directions. It does not choose the nearest probe through a wall. **Neighbor Sample** reports
when VRAD itself supplied a fallback leaf. Brightness is linear luminance, not an exposure-adjusted
0–1 screen value. The result estimates baked ambient illumination; it excludes dynamic lights,
flashlights, runtime exposure, and separate model direct-light contributions. It is a useful basis
for light-sensitive behavior, not a complete renderer or a finished light-avoidance behavior.

### Common infected movement test

Stock-game testing found that `info_goal_infected_chase` can attract commons around obstacles, but
it is a global lure and nearby survivors can take priority. In one five-common test, two reached
the first target and one followed the retarget while survivors were nearby; moving survivors away
allowed all five to reach the new target. Disabling the lure left them idling during the observation
window, so reliable return to normal pursuit was not established. It is not exposed as a selected-bot
Move action. Direct common-infected MOVE and RESET were subsequently verified in-game and are supported. ATTACK and RETREAT remain restricted to player bots pending validation.

### Experimental Fear Baked Light example

Bot Actions > Fear Baked Light creates an editable action with four labeled stages:
sample current light, throttle candidate searches, score reachable darker areas, and request movement.
It updates at 0.25 seconds, searches at most eight candidates by default every three seconds, uses
separate enter/exit brightness thresholds, and releases its own command on shade, missing data,
failed searches, or Stop. Thresholds are linear ambient values and must be tuned for the map.
Game Time is a reusable value node for deadlines in any synchronous graph.

**Verified on a common infected.** A direct MOVE covered about 318 units and stopped within 14 units;
retargeting another 230 units stopped within 8 units. RESET followed by provocation resumed survivor
pursuit. The full generated shade graph then selected a reachable lower-ambient area on a stock map,
moved the common there, and cleared its movement-owned state after crossing the safe threshold,
with no runtime errors. Test thresholds were calibrated to that map's actual ambient values.
Close-range survivor melee can still interrupt movement. This is a one-common proof, not a horde
performance benchmark. Specials did not reliably obey earlier movement tests and are not selected
by this example. The generated callback tests cover hysteresis, cooldowns, missing data,
unreachable candidates, and once-only RESET cleanup.

## Worked examples

*Shift+A > Examples*, *Examples 2* and *Examples 3* add **62** ready-made graphs (`hammerless/blender/logic_examples.py`, `_2.py`, `_3.py`), from a hello message to Director tricks. Each comes with notes, numbered frames in the order things happen, and any objects it needs. Between them every node and every Override is used somewhere. The full list is in [Logic Nodes](../LOGIC_NODES.md#examples). A few of the more involved ones:

**Swelling Pod (49).** An animated Custom Model (a pod with `idle` and `swell` actions) and one Swelling Pods node. A survivor close by, or a shot, provokes a pod: a per-entity charge rises while it is provoked and falls when it is left alone. The swell animation is held at that charge (playback rate 0, its cycle set through NetProps) and the size follows. Full, it bursts: an explosion, damage and a green flash for survivors in range. A shot pod always bursts. Inside are the groups Swelling Pods, Swelling Pod, Show Swelling and Explosion. Tested in game: walk-up (swell, burst, 20 damage) and shooting.

**Sleepers (51).** Placed characters lying or sitting, named `sleeper...`. A survivor walking close, a gunshot within hearing, or a shot at one wakes it: it stands up with an animation that suits its pose, becomes a real common in the same clothes, and rushes the closest survivor. Built from the groups Sleeper, Wake Sleeper, Stand Up Animation and Replace With Common. Tested in game: walk-up and gunshot wakes, the woken commons attacked.

**Power Goes Out (53).** Lights set *Switchable* in the Light panel get their own layer in the baked lighting, which the game can turn on, off or flicker. Lights sharing a name switch together. A button makes the lights stutter (SetPattern), die as a horde comes, and return 30 seconds later. **Lights Flicker While a Tank Lives (54)** does the same from the `tank_spawn` and `tank_killed` events, steady again only when no Tank is left.

**Car Alarm.** Not an example but a preset (*Add > L4D2 > Car Alarm*): Valve's own alarm-car instance, copied from the game's sdk_content next to the map at build. Shoot or bump it and a horde comes. It has its own in-game test (`tests/ingame/test_car_alarm.py`): a shot sets off that car and calls a mob; a second car stays quiet.

## Presets

Presets are pre-wired map parts, each one object (a parent Empty with its pieces) with its settings on the parent: **Start / End Safe Room** (fully wired), **Horde Trigger**, **Horde Button**, **Crescendo Button** (with editable Stages such as `PANIC 1, DELAY 10, PANIC 2`), **Tank Ambush**, **Gate + Button**, **Ladder**, **Zombie Ladder**, **Zombie Climb**, **Zombie Spawn Area** and **Car Alarm**. They use entity outputs directly; [Hordes & Events](../HORDE_EVENTS.md) shows the wiring and which were tested in game. **Graph from Outputs** turns existing entity outputs into a logic graph.

## Debugging and verification

### The debug log

*Settings > Debug > Debug Log* makes every wire print a line to the game's `console.log` when it fires, with the graph it is in:

```
HAMMERLESS_EVENT [Example 21: Two-Step Gate] button.pressed -> steps.in
```

Random rolls are printed too. This is what you read when a graph doesn't do what you expect, and what the automated tests read.

### The in-game test bench

`tests/ingame/bench.py` drives a running game from Python in about 0.1 s per request. While a bench is open, it installs a small script as the game's `mapspawn.nut` (run on every map load). That script polls a request file in `left4dead2/ems/`, the one folder game scripts may read and write, runs the Squirrel it finds and writes back the result as JSON. It can also build a .blend without lighting (about 30 s), press and hold Use, walk the survivors along the map's path, take screenshots and read which logic wires fired. Details are in [tests/ingame/README.md](../../tests/ingame/README.md).

### Every example, checked in the game

`tests/ingame/test_examples.py` builds the demo level with every example on its field as one test map, with the debug log on. Then for each example it acts as a player would: pressing buttons, holding Use, moving players into volumes, killing infected, typing chat and console commands. It checks the wires that fired and the game's state, and prints PASS / FAIL / SKIP per example along with every script error of the run. A few need real play and are skipped (headshot kills, the Director getting furious, the Director's own mob timer); a few camera examples are checked by screenshot. Examples 53 to 62 were each checked this way when added (October 2026); the last full run recorded in the changelog (0.14.1) had all 42 automated examples of the time passing.

The run has found real bugs: overrides answered before the logic existed, Keep Across Maps saving junk, and an example that fired at map load.

## What we measured about the game

The nodes rely on behaviour measured in the running game, not assumed:

| Finding | Used by |
|---|---|
| Hooks and HUD only in scripted mode; modes only from packed files | The game mode addon |
| Entities run scripts in creation order; weapon spawns asked as created | `hl_logic` first in the map |
| `point_clientcommand` runs a console command on one player (its activator) | Camera node (third / first person per player) |
| Shots on a `prop_dynamic` reach AllowTakeDamage, with the hit point | Swelling Pod and Sleepers shot detection |
| `infected_death` carries no infected id; `infected_hurt` does | Power-Up: Nuke (42), kill-based logic |
| ConvertWeaponSpawn gets e.g. `weapon_pistol`; answering `weapon_pistol_magnum_spawn` converts it | Pistols Become Magnums |
| A script ZSpawn with no position places a Tank only while none is alive | Spawn Zombie falls back to a hidden spot |
| Flow values are empty on maps with broken flow | Flow-based examples must guard |

Some measurements are not in nodes yet. A map's `maps/<map>_level_sounds.txt` can override any game sound for that map and add new ones. `point_clientcommand` can set a per-player DSP effect (`dsp_player`). The retail game has no `snd_setmixlayer`, but a `sound_mix_layer` entity exists. We also tested script-driven enemies on L4D1-survivor bot bodies: they move and fire under script control, but bullets between them and survivors are dropped by the engine, so damage has to be done by script.

## Limits and what's left

- **First run of the mode.** A map with Overrides or a HUD needs the game restarted once after the addon is installed.
- **Not every example is automated.** Some depend on the Director's own timing or real aiming.
- **Overrides depend on the game's choices.** Convert Zombie Class only sees Director specials; Can Pickup Object and Should Avoid Item default to "no"; bots ask Should Avoid Item constantly.
- **Planned utility groups** (not started): All Of, Any Of, Toggle, Cooldown, Repeat, Random Interval, Countdown, While True, Ramp, Survivors Inside, Nearest Survivor, For Each Survivor Delayed.
- **Maybe later**: map-setup groups such as Elevator, Holdout, Gauntlet, Alarm Door, Scavenge Fill, Radio Finale, Breakable Wall and One-Way Drop.
- **Not yet in nodes**: per-map sound overrides, mix layers, per-player DSP, and custom enemies on bot bodies.
