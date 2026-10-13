# Nav Mesh and the AI Director

Bots, common infected, specials and the AI Director all run on the **nav mesh**: the game's map of walkable areas and how they connect. In L4D2 the nav also carries what the Director needs to place zombies: which areas can see which, where the hiding spots are, and which areas belong to the safe rooms.

Hammerless makes the whole nav itself. It reads and writes the game's `.nav` format, generates the mesh with a port of the game's own generator, runs the game's nav analysis, and marks the safe rooms. The game loads the finished file once, with nothing left to generate or analyze.

This page explains how that works, how it was checked against the game, and what is still different. For the settings, buttons and colour views, see the user guide: [Nav Mesh & Zombie Spawns](../NAV_AND_ZOMBIES.md). For hordes and events, see [Hordes & Events](../HORDE_EVENTS.md).

The generator and analysis follow the algorithms in Valve's published Source SDK 2013 nav code (`nav_generate.cpp`, `nav_area.cpp`, `nav_file.cpp`). They are reimplemented, not copied, and every L4D2 difference was measured against the game's own output.

## The .nav file

Code: `hammerless/core/navfile.py`.

L4D2 saves nav files as **version 16, sub-version 14**. The layout is the SDK 2013 version 16 layout with a few L4D2 additions:

| Where | What L4D2 adds |
|---|---|
| After the place directory and its "has unnamed areas" byte | A null-terminated string (`default`) and a u16 |
| Per area, between the four light intensities and the visibility list | A u32 of **spawn attributes** and a u16 (always 0) |
| After the ladders | Four trailing bytes |

Every version 16 nav that ships with the game reads and writes back **byte for byte**. Runtime-only flags (such as `0x2000000`) are not saved by the game, so we don't save them either.

Details that the SDK source doesn't settle, measured on the game's files:

- **Visibility list entries are area positions**, counted from 0 in file order. The "inherit from" field is an area **ID**. The lists are delta-compressed with the SDK's `PostCustomAnalysis` method and `nav_max_vis_delta_list_length` 64.
- **Hiding spot IDs** run from 0 in area order.
- **Area centre** is `(nw + se) / 2`, using the z of those two corners only, in 32-bit floats.
- Coordinates are Hammer units. Source's "north" is -Y, so `nw` is the (min x, min y) corner.

### Spawn attributes

These are the per-area bits the Director reads.

| Bit | Name | What it does (as measured) |
|---|---|---|
| `0x2` | EMPTY | No wandering commons. Read **only** by the wanderer placement. |
| `0x80` | PLAYER_START | Start safe room. |
| `0x800` | CHECKPOINT | Safe room (start and end). |
| `0x1000` | OBSCURED | Treated as hidden even when survivors can see it. |
| `0x2000` | NO_MOBS | No hordes. Doesn't affect wanderers. |
| `0x4000` | THREAT | The Director picks two per round itself and ignores marks in the file, so we write none. |

Two more fields are written but don't matter: the area attribute `0x20000000` is recomputed by the game when the nav loads, and the light intensity per corner is left at 1.0. The game's own light values depend on the client (flashlight, camera) and many corners are never measured, so they can't be reproduced.

## The generator

Code: `hammerless/core/collision.py`, `navgen.py`, `navareas.py`, `navpredict.py`; native code in `native/hlnav.c`, `hlareas.c`, `hlbrush.c`.

### How it works

The generator follows the game's `nav_generate`:

1. **Seeds.** The flood starts from `info_landmark` and `weapon_*_spawn` entities, as the game's does. The game won't seed from survivor spawns (a map with only those fails with "No valid walkable seed positions"), so Build adds a landmark when a map has none.
2. **Flood fill.** From each seed it steps outward on a 25-unit grid. Each step sweeps a thin column (0.9 x 0.9 x 55 units) to the next grid point, drops to the floor, and if blocked tries climbing up to 200 units. The result is a graph of walkable nodes.
3. **Areas.** Nodes become rectangles (`CreateNavAreasFromNodes`, `TestArea`, `BuildArea`), get connected, jump-down links are searched, areas are merged, squared up, split under overhangs, stairs are marked, connections fixed.
4. **Ladders.** A port of the game's ladder building (`BuildLadders`, `CreateLadder`, the ladder-end search, `ConnectGeneratedLadder`). It matches the game's ladder exactly on a test map.

The collision it traces against is built the way the engine builds it: the VMF's plane points snapped like vbsp's `FindFloatPlane`, plus vbsp's bevel planes, swept with the classic box-trace algorithm and `DIST_EPSILON`. Traces hit what `MASK_NPCSOLID_BRUSHONLY` hits: not triggers, not player clips. Ladder brushes are solid for generation (measured: the game puts a small area on top of each ladder brush).

Static props are solid, like in the game. Without them our nav ran under 74% of the big props (cars, dumpsters) on a large user map; the game's own nav ran under 9%. With each prop's collision model (its `.phy`, or its hull box for `solid 2`) our nav runs under 8%, with 2322 areas against the game's 2199.

### The L4D2 constants

The SDK code has `TERROR` branches for Left 4 Dead. These are the values used:

| Constant | Value |
|---|---|
| Generation step | 25 |
| Step height | 18 |
| Jump-crouch height | 64 |
| Death drop | 400 (zombies take no fall damage) |
| Climb-up height | 200 |
| Player hull | 32 x 71 |
| Crouch height | 55 |
| Slope limit | 0.7 |

### Where L4D2 differs from the SDK code

Every SDK pass is ported except `HandleObstacleTopAreas`: L4D2 makes no areas on top of fences (measured). Other measured differences:

- Areas more than 18 units apart in height are never linked both ways. Drops over 64 units are one-way drop-downs. Ledges between 19 and 64 units are not linked in either direction.
- Seeds that are already covered are skipped.
- The cliff check is on.
- Square-up doesn't skip after a split.
- **Merging is capped.** L4D2's areas are much smaller than the SDK code would make. Allowing a merge only while the result has at most 32 cells (25 x 25 units each) gave the most identical areas, fitted on four test maps.

### How close it is

Walkable space and where the path breaks match the game. Exact rectangles match on only about half the areas, because the merge order differs. That doesn't change play: the Director uses the walkable space, the connections and the area count, not exact rectangle edges.

The game was tested on our nav directly. On a test map with rooms, our 247 areas (the game made 274) gave a working flow of 4964 (the game's own nav: 5033), 30 commons within 20 seconds, and no warnings.

### Native code

The hot loops run in a DLL (`core/_hlnav.dll`, built by `python native/build.py` with `zig cc`):

- `hlnav.c`: brush traces and the flood fill.
- `hlareas.c`: the whole area pipeline, from building areas through fixing connections, so the nodes never become Python objects.
- `hlbrush.c`: building every brush's collision planes and bevels.

The native code must give **bit-identical** results to the Python. It uses the same double arithmetic in the same order (built with `-ffp-contract=off`), the same candidate order and the same quirks. It was checked stage by stage against the Python, and `HAMMERLESS_NAV_CHECK=1` compares the whole mesh on every build. `tests/compile/nav_native_exact.py` checks that the written `.nav` is byte-identical. The Python stays the reference, and the fallback when the DLL can't load.

One detail this forced: Python 3.12 changed how `sum()` rounds, so the Python reference now sums plainly and gives the same brushes on every Python version.

Generation and analysis run in their own process (`core/navworker.py`), so Blender stays responsive while vis and lighting run. The meshes are identical to running in-process (c5m1: 2101 areas, same hiding spots and visibility).

### Marks the generator adds

After generation, `navpredict.py` marks areas the way a mapper would paint them by hand in Hammer:

- **Safe rooms.** Every area whose centre is inside the Start Safe Room gets PLAYER_START + CHECKPOINT; inside the End Safe Room, CHECKPOINT. Without these, survivors don't spawn in the room and closing the end door doesn't change level. The room boxes are padded down and sideways, **never up**: if roof areas get CHECKPOINT, the flow breaks.
- **Zombie Spawn Area** boxes add OBSCURED; no-spawn boxes add EMPTY and/or NO_MOBS.
- **Unreachable areas.** An area from which there is no way (walking, drops, ladders) to where the survivors can go gets EMPTY. A common placed there could never reach the survivors, so it would be a wasted pick. Specials and hordes still use those areas: EMPTY only affects wanderers. Reachability is worked out before the zombie-only climb links are added, so a climb doesn't make a sealed roof count as reachable.
- **Drops off islands.** The game's generator (and ours) leaves car tops, crates and balconies with no way down. Hammerless adds a drop link where a player could step off and fall clear. On a large user map: 164 links, EMPTY prop tops 61 down to 4, lost wanderer picks 32 down to 13.

### Optional extras

- **Zombies Climb Walls.** Every area edge with walkable ground 19 to 160 units higher just past it, and a wall in between, gets a one-way climb link up, plus a way back down where the game makes none. On a large user map: +680 links, unlinked ledges 56 down to 5. In game, commons climbed a 120-unit tower with no markers (14 on top after 75 seconds).
- **Zombie Climb** preset: one climb link from a bottom point to a top point. Commons climbed 120 and 160 units in game, not 172; taller climbs warn.
- **Nav Culling** (on by default, like the game): nav only grows from seeds across what can be walked. Off: every flat top inside the map big enough to stand on gets nav too.
- **Nav Area** volume: one nav area exactly where you put it, linked to the nav beside and below it.
- **Control Volumes** limit the nav (none inside, or only inside). No area straddles the boundary.

## The nav analysis

Code: `hammerless/core/navanalyze.py`, `navvis.py`; native code in `native/hlvis.c`.

After generation, the game's `nav_analyze` works out two things the Director needs:

- **Visibility lists**: for each area, which other areas are completely or partly visible. The Director spawns commons where no survivor can see.
- **Hiding spots**: spots in cover, used by bots and specials.

Hammerless does this itself, against the compiled BSP, so the game doesn't have to.

### What blocks sight

Measured against the game's lists:

- The compiled map's brushes with solid, moveable or block-LOS contents, read from the BSP (so window, grate and `$alphatest` materials don't block), and its displacements.
- Solid brush entities, each traced on its own; the closest hit wins. Physics-solid ones (movers, doors, `func_brush`) stop a trace that starts inside them.
- **Doors, at the position they spawn in.** Checkpoint doors block both sight lines and boxes. The end safe room door spawns open. A VScript `TraceLine` with the same mask passes through doors (its filter differs), so the game's lists, not VScript, were the reference.
- **Solid props**: static, dynamic (even when start-disabled) and physics props with collision. Measured with a container, a trailer and a van. Props the game or vbsp deletes block nothing: a `prop_physics` without `prop_data` (it's gone in game), a `prop_dynamic` with `prop_data` that isn't allowed static, a `prop_static` vbsp drops. Props didn't change hiding spots.

### The visibility rules

- Pairs are first culled with the BSP's PVS (the current area's sample points against the other area's eye box), as the game does.
- Areas are gathered within 1500 units, using the game's 32-bit `distSq <= r^2`. L4D2's `nav_max_view_distance` is 0, so there is no further distance cut-off.
- The reverse direction is computed whenever the pair is inside the PVS.
- Hiding spots follow the SDK's `ComputeHidingSpots` exactly, traced with `MASK_NPCSOLID_BRUSHONLY` (displacements included).

### How close it is

On a 2330-area map, compared with the game's own `nav_analyze`:

| | Result |
|---|---|
| Hiding spots | Identical |
| Visibility | 99.923% of 376,000 area pairs identical |
| Game loading our analyzed nav | No analysis, no reload |

The remaining 0.077% are hairline grazes along brush edges. Other epsilon, tree-walk and float32 variants were all tested; the current rule was the closest.

The 99.92% was measured with the analysis run on its own. An audit found that in real use, where generation runs first, the generator's world was left loaded in the DLL and blocked sight too: 96.16%. That was fixed, and both ways are now measured after any native change.

### Speed

The visibility pass is the heavy part. It runs on every core in C and is assembled in one-thread order, so the lists are the same as a single-threaded run. Every change below was checked byte-identical against the run before it.

| Map | Before | After |
|---|---|---|
| 2330-area test map (first native version) | 92 s | 3.9 s |
| c2m1 (large outdoor, 24 MB nav) | 53 s | 26 s |
| c3m1 (large outdoor, 24 MB nav) | 61 s | about 30 s |
| c1m1 | 7 s | 3.3 s |

The c2m1 gains came from profiling, step by step: no sorting where the order doesn't matter, trying the last blocker first (52 to 38 s), a brush tree per collision slot for candidate lookup (38 to 28 s), and counting visibility deltas only as far as they can matter. On c2m1 the 26 s now splits into about 19 s of C visibility, 2 s hiding spots, 1 s loading the collision slots, 1.3 s compression and about 2 s converting lists.

## Speed: Hammerless vs the game's own path

The game's path with **Nav Mesh: Made by the game**: the map loads, `nav_generate` runs in the game after the survivors spawn, the map reloads, `nav_analyze` runs, and the map reloads again. That is two extra reloads on every build that needs a new nav.

The Hammerless path: the generator runs during the compile, in its own process, starting at once. The analysis runs alongside the lighting step, against the freshly compiled BSP. The `.nav` is written finished, and the game loads the map once.

Collision building moved to C on Valve's `styleguide_urban` sample map took generation from 12.7 s to 2.8 s and the analysis from 11.4 s to 3.4 s; reading the map once for generation brought generation to 1.7 s. Earlier, fixing the native position hash took the flood fill from 4.4 s to 0.6 s with the same nodes.

## Real-time nav (in progress)

The goal is a nav that updates in about a second after an edit, and that **is** the nav shipped to the game, not a preview. The bar is strict:

- An incrementally updated nav must be **byte-identical** to a full regeneration by our generator on the same scene (after canonical renumbering), proven by a harness that makes random edits on real maps. A mismatch is a bug, never a silent fallback.
- Against Valve's generator, gameplay-identical is enough. Any deliberate improvement is its own measured decision, never a side effect of speed work.

What already works:

- **Sweep memory.** Each sweep is kept with the box of space its traces looked at. When brushes change, only sweeps whose box touches a changed brush are redone; the flood replays the rest in its usual order, so the nodes match a fresh run exactly. Brushes are compared by their exact planes, and if their order changes the memory starts afresh.
- **Bevel planes** are remembered between runs (loading 2.0 s down to 0.1 s on a repeat).
- **Smart builds** keep the nav when only logic changed: logic entities aren't part of what the nav is made from.

What's still to do is the area stage. The design found where generation depends on order: the order nodes are created in the flood, newest-first area creation, chains of merges, and passes that restart or append to lists. The plan is position-keyed decisions plus fixed chunks of about 1024 units that areas never cross (exact by construction), with cheap global passes (reachability, renumbering) after. The analysis stays a build step, since it needs the compiled PVS. Keeping the merge local matters: the Director places wanderers by area **count**, so the number of areas changes how many commons a map gets.

## What we learned about the Director

These were measured in game or read from `server.dll`. They are the rules the nav views and the Problems checks are built on.

### Flow

- The **flow** (escape route) is computed at map load only if the end room's `info_changelevel` names a **different, real** map. Empty or self-referencing gives flow -9999: no wanderers, no Tanks, no Witches. Build uses `c1m2_streets` when none is set.
- Without safe rooms there are no wanderers at all: only timed mobs and specials.
- `nav_generate` in the game seeds only from landmarks and item spawns, and must run after the survivors spawn.
- The game's `nav_generate` can leave a drop onto a ramp unconnected while connecting others of similar height. Drop height alone doesn't predict it. So the map's ready script checks the flow in game (below).

### Nav blockers

`func_nav_blocker` blocks the nav **at spawn** in L4D2. It ignores StartDisabled, and team -1, 2 and 3 all behave the same. The Director's start-of-map wanderers skip everything behind a blocked gate. A vote restart doesn't re-run that blocking, so the room fills, which makes the bug look random. With Movers' *Block Nav While Closed* off by default, a room behind the first gate went from 0 to 42-51 commons.

### Wandering commons (from server.dll)

- **Budget** = round(`z_wandering_density` 0.03 x escape route length). `DirectorOptions.WanderingZombieDensityModifier` scales it.
- Areas are picked **uniformly at random by index**: a big street area is no likelier than a small roof area. A pick is lost if the area can't hold wanderers: crouch or jump areas, blocked areas, EMPTY, PLAYER_START, CHECKPOINT, RESCUE_CLOSET, no flow, or attribute bits `0x30000`. NO_MOBS doesn't matter for wanderers.
- A wanderer appears only more than 550 units from every survivor (`z_spawn_safety_range`) and out of their sight. At the start, only the share in the ring out to 1500 units appears; the rest wait until survivors come near. The live cap is `z_common_limit` 30.
- On a large user map the live wanderer budget measured only 12-15. Tripling `z_wandering_density` (a cheat cvar) gave 27-28 on the street, so hidden spots weren't short; the budget was. Valve's c1m1 has 287 valid areas within 1400 units of the start, against 65 on that map: props chop Valve's floors into small areas, while a clean street is one big area.
- `WanderingZombieDensityModifier` is read too early for every map hook tried (logic_auto, logic_script at load, a `<map>.nut`, `mapspawn.nut`). It can't be set per map yet.
- `director_report` shows the live population; `director_debug_threat_placement` printed nothing.

### Specials and hordes

The special and mob spawn filter rejects crouch and jump areas, attribute bits `0x30000`, PLAYER_START, rescue vehicle and closet areas, and NO_MOBS (except for some special rules). It **never tests EMPTY**. So EMPTY is a commons-only mark: marking an unreachable roof EMPTY keeps wanderers off it while smokers (whose default rule is to spawn above the survivors) still use it.

### Items

The Director places items from `info_map_parameters` densities, in items per square 100 yards (3600 units) of walkable area. Defaults are 6.48 for pills, adrenaline, throwables, ammo, melee and pistols, 3.0 for defibrillators and 1.0 for upgrade packs and chainsaws. Same-kind spots within `ItemClusterRange` (50) count as one cluster. Measured over 55 Valve campaign maps, Valve's median densities are much lower: pills 2.5, pipe bombs 3, most others 2, defibrillators and ammo 0.8. Hammerless doesn't write `info_map_parameters` yet, so maps get the defaults. The full research is in [docs/dev/research/ITEM_PLACEMENT.md](../dev/research/ITEM_PLACEMENT.md).

## The Blender views

Every view is built from the same facts the Director uses. The user guide explains each one: [Nav Mesh & Zombie Spawns](../NAV_AND_ZOMBIES.md).

| View | What it shows | Built from |
|---|---|---|
| Can survivors reach it? | Reachable from the start room or not, and where the path to the end breaks | Walks, drops and ladders from PLAYER_START |
| Distance from the start | Walking distance along the path | The same walk |
| Zombie spawn marks | EMPTY, NO_MOBS, OBSCURED per area | The written spawn attributes |
| Where zombies can spawn | For survivors leaving the start room (or at the 3D cursor): can spawn, no-spawn mark, too close (walking distance under 550), in view | The wanderer rules above and the visibility lists |
| What can be seen from the 3D cursor | Completely, partly or not visible | The visibility lists |
| Zombies can appear ahead? | For each route area, how many hidden spots lie 550-1500 units ahead (red under 10: a dead zone) | Visibility lists and the route |
| Hiding spots, drops and jumps | Hiding spots in cover or exposed; one-way drop and jump arrows | The analysis and the links |

### The spawn preview

**Zombie Spawns** (`core/spawnsim.py`) simulates the wanderer placement from a seed: the budget, random picks by area count, picks lost on areas that can't hold wanderers, and which ones appear now. Red boxes appear now, grey ones wait, purple ones are lost (with the reason).

It was checked in game by `tests/ingame/test_spawn_preview.py`, which loads a map many times, records every common 5 seconds in, and compares with 200 preview rolls on the same `.nav`:

| Map | Count (game / preview) | Placed within 300 units of a predicted spot | Result |
|---|---|---|---|
| A large user map, as built | 30 / 30 | 99% | Pass |
| The same, roofs marked no-spawn | 12.7 / 13.9 | 92% | Pass |
| c1m1_hotel | 30 / 20 | 46% | Fail |

c1m1 fails because its route goes through an elevator that the nav connections don't model: the game's route length is 15994, ours 4106. The preview is a guide, not the game.

### Problems checks

The zombie report adds Problems rows for: more than 40% of the wanderer budget lost to areas that can't hold wanderers (Valve's maps lose 30-35%), a short route, no horde in a run-through, and dead zones where nearly every spawn spot ahead is in view. On a large user map it predicted dead zones of 56-66%; the game measured 50-70%. It finds none on c1m1 or c5m1. Hammerless also warns when a no-spawn box covers much of the nav.

### The in-game flow check

When the survivors spawn, the map's ready script prints whether the Director found the flow. If `GetMaxFlowDistance()` is positive it prints `HAMMERLESS_FLOW ok` with the length. If not, it walks the nav from the PLAYER_START areas and prints the reached spot closest to the end room: where the path breaks. Build & Play reads this from the console and shows it in Blender.

## Limits

- **Exact rectangles** match the game's on only about half the areas. Walkable space, path breaks and ladders match.
- **Visibility** is 99.92%, not 100%. Hiding spots are identical.
- **Light intensity** is always 1.0, and THREAT marks are never written (the game ignores them).
- Routes the nav doesn't model (c1m1's elevator) make the spawn preview wrong for those maps.
- A full-map nav with roofs marked was measured at 0-1 commons, where the preview predicted about 10. That case is unexplained.
- The analysis needs the compiled map, so it can't be real-time.
- Very large maps hit a node limit; Hammerless warns that part of the map has no nav.
- The native code is a Windows DLL. Elsewhere the Python runs, with identical results, much slower.
- `WanderingZombieDensityModifier` can't be set per map yet, and item densities aren't written yet.
