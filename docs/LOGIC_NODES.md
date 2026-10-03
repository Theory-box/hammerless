# Logic Nodes

Map logic (events, timers, buttons, gates, spawns, Director changes) is built as a node graph, like Blender's shader editor. Hammerless turns the graph into game entities and a VScript when the map builds.

## Getting started

1. *Logic (nodes) > **New Logic Graph*** (or **Graph from Outputs** to turn existing entity outputs into a graph).
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
| **Actions** | Move Over Time (Mover), Show/Hide Object, Horde, Crescendo, Spawn Zombie (a spot, or the game picks; *Only If Fewer Than*), Play Sound, Teleport Survivors, Show Message |
| **Director** | Director, Director Settings |
| **Objectives** | Objective |

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

*AI Director > Director Spawns* switches a type (Tank, Witch, each special) off for the Director, so only your graph spawns it.

## Debugging

*Debug > Debug Log* prints every wire as it fires (`HAMMERLESS_EVENT`) and each random roll to `left4dead2/console.log`.
