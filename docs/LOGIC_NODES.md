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

## Nodes

| Group | Nodes |
|---|---|
| **Events** | Map Start, Game Event, Volume (a trigger box), Button, Timer |
| **Values** | Path Progress (furthest / average / last survivor, 0 to 1), Random Value, Math, Compare, Boolean Math, Infected Count, Value |
| **Scene** | Object Info (picks a scene object to hand to other nodes), Entity Events (an object's own events, e.g. a door opening), Collision (solid for players and/or the nav) |
| **Flow** | When (fires when a condition becomes true), If, Sequence, Delay, Once, Gate, Branch, Counter, Random |
| **Actions** | Move Over Time (Mover), Show / Hide Object, Horde, Crescendo, Spawn Zombie (a spot, or the game picks; *Only If Fewer Than*), Play Sound, Teleport Survivors, Show Message |
| **Director** | Director, Director Settings |
| **Objectives** | Objective |

## The game's own scripting (Add menu, bottom)

Everything L4D2's script language (VScript) can do is available as nodes.

| Menu | What's in it |
|---|---|
| **Game Functions** | All 419 of the game's script functions, grouped (Player, Director, Nav Mesh, Entity, Find Entities, Entity Properties, Spawning, Sound, HUD...) or found with *Search...*. Value functions (Get Health, Find By Name...) just give a Result; actions (Give Item, Stagger...) run when an event wire arrives and then fire *Then*. |
| **Game Events** | Any of the game's 381 events (player hurt, item picked up, tank killed...). Its details come out as values: user ids already turned into players, entity ids into entities. |
| **Script Blocks** | For Each (survivors, infected, players, commons, entities by class or name, a list), Set / Get Variable (for the map or per player, kept across maps if you like), Make Table / Get Field, Format Text, Make / Break Vector, Vector Math, Compare Values, Script and Script Value (your own Squirrel code). |
| **Director, HUD & Overrides** | Director Setting (any of the ~140 Director settings, changed while playing), Director Mood (the Director's intensity, 0 calm to 1 furious, and warnings 60 s and 20 s before a mob), HUD Text / HUD Hide, Override. |

Wire colours added for these: teal **text**, purple **vector**, pink **entity / player / nav area**, dark grey **any value**.

Script nodes chain straight into each other (an event's player flows into the next node), and mix with the other nodes both ways. Inside a For Each, use script nodes for each item: an entity-wiring node fires a moment later, after the loop has moved on.

### Overrides and the HUD need Hammerless's game mode

The game only asks the map before doing things (Override nodes) and only shows a custom HUD in *scripted mode*, which plain co-op never turns on (tested). A map that uses these nodes gets **Co-op (Hammerless)**: co-op plus an empty mode script, installed as `addons/hammerless_mode.vpk`. Build & Play starts the map in it. The first time, close the game and Build & Play again: the game learns new modes when it starts.

## Examples

**A Tank somewhere between 20% and 100% of the way:**
Path Progress ≥ Random Value (0.2 to 1.0) → **When** → **Spawn Zombie** (Tank).

**A gate that opens when a button is pressed:** *Add > Events > Gate + Button* is a ready-made example. Or: Button → *On Press* → Mover (*Go*).

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
