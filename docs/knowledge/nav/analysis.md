# Nav analysis: the game's `nav_analyze` reproduced, and the in-game checks

Status: `done` (hiding spots identical, visibility 99.92% of pairs identical to the game's own output, the game loads the result without analysing or reloading). Code: `hammerless/core/navanalyze.py` (the pass, the entity and prop rules, `compress_visibility`), `hammerless/core/navvis.py` (the Python reference of the visibility rules), `native/hlvis.c` (the visibility traces, included from `hlnav.c`), `hammerless/core/bsppvs.py` (PVS rows and brushes read from the compiled BSP), `hammerless/core/vision.py` (which materials and classes block sight), `hammerless/core/navworker.py` (own process). User-facing: `docs/systems/NAV_MESH.md`, "The nav analysis".

## What the analysis produces

After generation the game's `nav_analyze` computes two things the Director and the bots need; the file's `analyzed` byte tells the game whether they are present (format in [README.md](README.md)):

| Output | Used by | Ours |
|---|---|---|
| Visibility lists: per area, every other area that is completely or potentially visible (two bits), stored delta-compressed against a neighbour | the Director (commons appear where no survivor can see; the "in view" rule of wanderer placement, see [../director/](../director/README.md)) | `done`, 99.923% of pairs identical |
| Hiding spots: positions in cover at area corners, with "in cover" / "exposed" flags | bots, specials, the Director's ambush picks | `done`, identical |
| Per-corner light intensity | bot flashlights | not reproducible (client-dependent), written as 1.0 |

The reference is the game's own output: the game was made to `nav_analyze` the same mesh (console commands, `nav_edit 1` needed) and the saved `.nav` was read with `navfile.py` and diffed pair by pair against ours.

## What blocks sight (measured)

The SDK 2013 source (`nav_area.cpp`, `ComputeVisibilityToMesh`, `IsPartiallyVisible`) says sight is tested with `MASK_NAV_VISION` traces; what the engine actually hits with that mask in L4D2 was measured by comparing lists (2026-10-01 to 2026-10-03):

| Blocker | Rule | How known |
|---|---|---|
| World brushes | brushes with `SOLID`, `MOVEABLE` or `BLOCKLOS` contents, read from the **compiled BSP** (planes and contents exactly vbsp's), not from the VMF: so window, grate and `$alphatest` materials don't block | measured 2026-10-01; `bsppvs.bsp_brushes`, `vision.blocks_sight` |
| Displacements | block, like the game | measured; `collision.solid_displacements` |
| Solid brush entities | each traced on its own, the closest hit wins (the engine's rule); `SOLID_VPHYSICS` ones (movers, doors, `func_brush`, `func_movelinear`...) are "stuck" when a trace starts inside them | measured; `SOLID_BRUSH_CLASSES`, `PHYSICS_BRUSH_CLASSES` in `vision.py`, "physics" slots in `hlvis.c` |
| Doors (`prop_door_rotating`, `_checkpoint`) | block **sight lines and boxes** with their collision model at the position they spawn in; the end checkpoint door spawns open (`spawnpos 1`, yaw - 90) | measured 2026-10-01 (commit f91a99d): `DOOR_MODE` "none" and "boxes" left every doorway area different from the game, "all" none |
| Props | `prop_static`, `prop_dynamic` (even `StartDisabled`), `prop_physics` with a `.phy` collision model, at spawn. `solid 2` on an entity = its unturned hull box; on a `prop_static` = an upright box around the turned hull. Collision planes come from the `.phy` triangles (`phy.hull_planes`) | measured 2026-10-02 (commit 08e2aa8) with a container, a trailer and a van; props didn't change hiding spots |
| Props the game or vbsp deletes | block nothing: a `prop_physics` without `prop_data` (gone in game, e.g. a fire barrel model), a `prop_dynamic` whose `prop_data` isn't `allowstatic`, a `prop_static` vbsp drops ("Deleted.") | measured 2026-10-02; `_prop_exists` |
| Player clips, triggers, the nav generator's own collision world (glass, fences, ladders) | don't block | measured; the generator's world blocking was the 2026-10-03 audit bug (below) |

A VScript `TraceLine` with the same mask **passes through doors** (its filter differs from the analysis's), so VScript probes were not usable as the reference: only the game's saved lists are.

Prop cost: a detailed collision model made the analysis take minutes before the per-piece handling (a train tank: 466 s, now 0.07 s; CHANGELOG, audit round 4). A damaged model or odd prop keyvalues no longer abort the analysis.

## The visibility rules (measured L4D2 differences from the SDK code)

| Rule | L4D2 (measured) | SDK code |
|---|---|---|
| Pair culling | PVS first: the current area's sample points against the other area's eye box, using the BSP's vis rows (`bsppvs.py`, `SetupPVS`) | same |
| Radius | 1500 units, `distSq <= r*r` in float32, no further cut-off: L4D2's `nav_max_view_distance` is 0 | `nav_max_view_distance` cut-off |
| Reverse direction | computed whenever the pair is inside the PVS | conditional |
| Arithmetic | positions, areas and the PVS walk in the game's 32-bit floats (`hlvis.c`) | float |
| Storage | `PostCustomAnalysis`: each list stored as a delta against the best neighbour when shorter, `nav_max_vis_delta_list_length` 64; entries are 0-based area **positions**, "inherit from" an area **ID** | same algorithm |
| Hiding spots | `ComputeHidingSpots` exactly, traced with `MASK_NPCSOLID_BRUSHONLY`, displacements included (same result with or without them), ids from 0 in area order | same |

The native pass runs on every core and the lists are assembled in one-thread order, so the output equals a single-threaded run; the DLL is built with `-ffp-contract=off` and every speed change is checked byte-identical against the run before it.

## How close it is

Matching method: a 2330-area test map, the game's `nav_analyze` output versus ours, pair by pair (376,000 pairs within radius).

| Date | What | Result |
|---|---|---|
| 2026-10-01 | first match (commits 8c261ef, f91a99d) | hiding spots identical; visibility 99.923% of 376k pairs; the game loads the file with no analysis and no reload |
| 2026-10-01 | the remaining 0.077% | hairline grazes along brush edges; leave-epsilon, tree-walk and float32 variants all tested, the current rule is the best of them |
| 2026-10-03 | audit (commit 25b0317): the 99.92% had been measured **standalone**; in real use (generate, then analyse in the same process) the generator's collision world sat in DLL slot 1023 and blocked sight too | 96.16% in real use; fixed: the stash slot is not used or counted; **both modes are measured after any native change** |
| 2026-10-11 | native hardening (0df63c3), speed round | 99.922% (unchanged within the rounding), navs byte-identical |

Why 100% isn't pursued: the misses are single pairs on brush edges and the Director's spawn rule works on the set of hidden areas; no gameplay difference was found. The in-game spawn preview check (director/) passed on a large user map with these lists.

## Speed

The visibility pass is the heavy part: the pairs that are **not** visible (63% on c2m1) must prove every sample blocked. Profile of c2m1 by Fable (2026-10-11): 56% line rays, 30% hull sweeps, the candidate gather's `qsort` 120 thread-seconds; a GPU batch was estimated at most 2.2x by Amdahl's law, so the CPU work came first (the GPU batch, in the style of hlvrad's `vis.comp`, is not started).

| Map | Before | After | Change | Date / commit |
|---|---|---|---|---|
| 2330-area test map | 92 s (Python) | 3.9 s | all cores, faster traces in C | 2026-10-01, 4ea9ecc |
| styleguide_urban | 11.4 s | 3.4 s (3.5 s in the worker) | collision built in C (`hlbrush.c`) | 2026-10-11 |
| c2m1_highway (24 MB nav) | 53 s | 38 s | unsorted gather for rays and hulls without start contact; last-blocker cache per start point | 70c2256 |
| c2m1 | 38 s | 28 s | a brush BVH per collision slot, built per vis run (the gather was 62% of the run) | 570e623 |
| c2m1 | 28 s | 26 s | `compress_visibility` counts a neighbour's delta only as far as it can matter | 4331654 |
| c3m1 | 61 s | about 30 s | the same three changes | 2026-10-11 |
| c1m1_hotel | 7 s | 3.3 s | the same | 2026-10-11 |

c2m1's 26 s splits into about 19 s of C visibility, 2 s hiding spots, 1 s loading the collision slots, 1.3 s compression and about 2 s converting the lists. Every step was checked byte-identical with an A/B tool on the written `.nav` (the same files are the `nav_native_exact.py` corpus: urban, a large user map, c1m1, c2m1, c3m1).

Scheduling in a build: the analysis starts as soon as the map's visibility is compiled, alongside the lighting step (it doesn't use the lighting): about 0.5 to 2 s saved per build. Analyze Navmesh compiles only the walls and visibility when the walls changed (not the lighting; about 7 s less on a medium map), and after a Bake Lighting it adds the full visibility first so it never analyses on a bake's fast vis, which can differ from the game's.

## The game's path versus ours

| Path | What happens | Reloads |
|---|---|---|
| Nav Mesh: Made by the game | map loads, `nav_generate` runs after the survivors spawn (it seeds from landmarks and item spawns only), map reloads, `nav_analyze` runs, map reloads | 2 extra |
| Made in Blender, Nav Analysis: By the game | our nav is written with `analyzed = 0`, the game analyses and reloads once; the analysis path's reload keeps `sv_cheats 1` / `nav_edit 1` on but populates exactly like a clean load (audit 6, 2026-10-11) | 1 extra |
| Made in Blender, In Blender (default) | generator during the compile in its own process, analysis alongside lighting against the fresh BSP, finished `.nav` written | none |

Measured whole-build figures (CHANGELOG, a real level from scratch until playable): all-Valve (nav made and analysed by the game) 99 s against 32 to 45 s with the Hammerless path, before the 2026-10-11 speed round.

A nav whose analysis never got saved is analysed on the next launch (08e2aa8).

## The in-game flow check

The Director computes the flow (the escape route from the start to the end safe room) at map load; without it no commons wander, and no Tanks or Witches are placed. Because the game's own `nav_generate` can leave a drop onto a ramp unconnected while connecting others of similar height (a 125-unit drop onto a 19-degree ramp on a user map, 2026-09-24), a pre-compile grid check cannot be the final word: the map's ready script asks the game.

`HLR_FlowReport` in the ready script (`hammerless/core/gamefiles.py`), run when `HAMMERLESS_READY survivors spawned` is printed:

| Printed | Meaning |
|---|---|
| `HAMMERLESS_FLOW ok <length>` | `GetMaxFlowDistance()` is positive |
| `HAMMERLESS_FLOW broken at x y z` | no flow; a breadth-first walk over `GetAdjacentAreas` from the PLAYER_START areas (`0x80`) never reached a CHECKPOINT-only area; the printed point is the reached area centre closest to the mean of the end areas: where the path breaks |
| `HAMMERLESS_FLOW broken connected x y z` | the walk reached the end areas but the Director still found no flow (typically the next-map rule below) |
| `HAMMERLESS_FLOW nonav` / `noend` | no nav areas / no CHECKPOINT areas or no PLAYER_START areas |

`compile.py` parses the line (`parse_flow`, unit-tested in `tests/unit/test_core.py`) and Build & Play shows it in Blender (the Problems row). Rules this check depends on, verified in game 2026-09-24: the flow exists only when the end room's `info_changelevel` names a **different, real** map (empty or self-referencing gives flow -9999; Build fills in `c1m2_streets`, `build.FALLBACK_NEXT_MAP`); roof areas marked CHECKPOINT break the flow (so safe-room marks are never padded upward); `nav_generate` must run after the survivors spawn.

## Audit 4 (2026-10-11) items still open

| Item | Status |
|---|---|
| Nav Mesh "Made by the game" never gets the unreachable (EMPTY) marks: they are made in `navpredict._finish` only | open at the audit; the `navmark_<map>.nut` script marks safe rooms and climbs, not reachability |
| Auto marks look like the user's own EMPTY boxes in the nav view | open (MEDIUM) |
| No start room, or a Start Safe Room box catching no areas: nothing is marked, silently | open (MEDIUM) |
| Climb links were added before the reachability walk, undoing the marks | fixed (a267cc1) |
| Full-map nav with roofs marked: 0-1 commons where the preview said ~10 | unexplained, needs the game |

## Test tips (what cost time)

- Steam must be running or the game exits with "Steam is not running".
- Probe scripts: write a `.nut` into `left4dead2/scripts/vscripts`, send `script_execute` (needs `sv_cheats 1`; `setpos`, `nav_*` are cheats too; `nav_analyze` needs `nav_edit 1`); semicolons split console commands; read `console.log`.
- `compile.send_commands` (`-hijack`) **starts** the game without `-condebug` if it isn't running, so there is no `console.log`: use `compile.launch_game`.
- Counting commons: a fresh `map X`, wait about 5 s, count with a probe; don't freeze bots or teleport; a player moving confounds the count; keep tests to about 30 s.
- The game keeps `maps/<map>.nav01..10` backups on every nav save (73 found on the test machine): clean them when measuring sizes.
- Run `tests/compile/nav_native_exact.py` with Blender's Python 3.11 (3.12's `sum()` differs).
- Set `HAMMERLESS_NAV_CHECK=1` to compare the native and Python meshes on every build.

Last checked: 2026-10-13 against branch light-layers, commit 46f75ac.
