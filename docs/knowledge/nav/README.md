# Nav mesh: the .nav format, the generator port, the analysis, the plan for real-time nav

Hammerless reads and writes L4D2's `.nav` files, generates the mesh with a reimplementation of the game's generator, runs the game's analysis itself (visibility lists, hiding spots), marks safe rooms and spawn attributes, and checks the Director's flow in game. The game loads the finished file once. User-facing: `docs/systems/NAV_MESH.md` (system), `docs/NAV_AND_ZOMBIES.md` (settings and views). Director rules the nav feeds are in [../director/](../director/README.md).

| Page | Contents |
|---|---|
| this README | The file format as decoded, the generator port (constants, L4D2 differences, native code, marks, extras), how close it is |
| [analysis.md](analysis.md) | The game's `nav_analyze` reproduced: what blocks sight, the rules, how it was matched, speed work, the in-game flow check and the test tips |
| [realtime-plan.md](realtime-plan.md) | Real-time incremental nav (not built): the bar, what already works, the order-dependence analysis, the design |

Code: `hammerless/core/navfile.py` (format), `collision.py`, `navgen.py`, `navareas.py`, `navpredict.py` (generator), `navanalyze.py`, `navvis.py`, `navanalysis.py` (analysis and facts), `navworker.py` (own process), `fastnav.py` + `native/hlnav.c`, `hlareas.c`, `hlbrush.c`, `hlvis.c` -> `hammerless/core/_hlnav.dll` (native), `bsppvs.py` (PVS reader), `spawnsim.py` (spawn preview). Tests: `tests/compile/nav_native_exact.py`, `tests/ingame/test_spawn_preview.py`, `HAMMERLESS_NAV_CHECK=1` (whole-mesh native-vs-Python compare on every build).

Timeline: format decoded 2026-09-26; generator validated and game loads our nav 2026-09-26; own analysis matched 2026-10-01; prop sight blocking 2026-10-02; analysis audit fix 2026-10-03; native speed round (branch spawn-fix) 2026-10-11; Nav Culling, Nav Area volume, drop links 2026-10-11 (0.13.3/0.13.4).

## The .nav file (decoded 2026-09-26)

Method: read the SDK 2013 `nav_file.cpp` version-16 layout, then diff against the game's own files until every shipped v16 nav round-trips byte for byte (`navfile.py`). L4D2 writes **version 16, sub-version 14**, magic `0xFEEDFACE`. Additions over the SDK layout:

| Where | L4D2 addition |
|---|---|
| After the place directory and its "has unnamed areas" byte | a null-terminated string (`default`) and a u16 |
| Per area, between the four light intensity floats and the visibility list | u32 **spawn attributes** + u16 (always 0) |
| After the ladders | 4 trailing bytes |

Facts the SDK source doesn't settle, measured on the game's files:
- Visibility list entries are 0-based area **positions** in file order (an old reader took them for ids: wrong); "inherit from" is an area **ID**. Delta compression per the SDK's `PostCustomAnalysis`, `nav_max_vis_delta_list_length` 64.
- Hiding spot ids run from 0 in area order.
- Area centre = `(nw + se) / 2` using the z of those two corners only, float32 (`GetCenter`).
- Coordinates are Hammer units; Source "north" is -Y, so `nw` is the (min x, min y) corner.
- Runtime-only flags (e.g. `0x2000000`) aren't saved. Attribute `0x20000000` is recomputed by the game on load (file value irrelevant; our writer sets it). `analyzed` = 1 when we analysed; `bsp_size` = the BSP file size (the game checks it, so a smart build updates the header when it keeps the nav).
- Per-corner light intensity is client-dependent in the game (flashlight, camera) and many corners are never measured (1.0); not reproducible; written as 1.0. It affects bot flashlights (`sb_flashlight 0` lets bots decide).

Spawn attribute bits (what each does is measured; the Director's use is in director/):

| Bit | Name | Measured meaning |
|---|---|---|
| `0x2` | EMPTY | no wandering commons; read only by wanderer placement |
| `0x80` | PLAYER_START | start safe room |
| `0x800` | CHECKPOINT | safe room (start and end) |
| `0x1000` | OBSCURED | treated as hidden even when visible |
| `0x2000` | NO_MOBS | no hordes; wanderers unaffected |
| `0x4000` | THREAT | the Director picks 2 per round itself and ignores file marks: never written |

## The generator

A reimplementation of the SDK 2013 `nav_generate.cpp` / `nav_node.cpp` / `nav_area.cpp` (the SDK licence isn't GPL-compatible: reimplemented, not copied), with the `TERROR` (Left 4 Dead) branches, checked against the game's own `.nav` output.

**Pipeline** (`navgen.py` stage A, `navareas.py` stage B, `navpredict.py` marks): seeds from `info_landmark` and `weapon_*_spawn` (the game won't seed from survivor spawns: a map with only those fails with "No valid walkable seed positions", verified in game; Build adds a landmark when a map has none) -> flood fill on a 25-unit grid, each step sweeping a 0.9 x 0.9 x 55 column to the next point, dropping to the floor, climbing up to 200 units if blocked -> nodes to rectangles (`CreateNavAreasFromNodes`, `TestArea`, `BuildArea`), connections, jump-down search (`findJumpDownArea`, `testJumpDown`), merge, square-up, split under overhangs, stairs, connection fix-up -> ladders (`BuildLadders`, `CreateLadder`, ladder-end search, `ConnectGeneratedLadder`; exact match on a test map).

**Collision** is built the way the engine builds it: VMF plane points snapped like vbsp's `FindFloatPlane`, plus vbsp's bevel planes, swept with the classic box trace and `DIST_EPSILON`; traces hit what `MASK_NPCSOLID_BRUSHONLY` hits (no triggers, no player clips); ladder brushes are solid for generation (measured: the game puts a small area on top of each ladder brush). Static props are solid: without them our nav ran under 74% of the big props on a large user map against the game's 9%; with each prop's collision (its `.phy`, or its hull box for `solid 2`) 8%, 2322 areas against the game's 2199.

**Constants** (`nav.h` TERROR): GenerationStepSize 25, StepHeight 18, JumpCrouchHeight 64, DeathDrop 400 (zombies take no fall damage), ClimbUpHeight 200, hull 32 x 71, crouch 55, slope limit 0.7, `nav_coplanar_slope_limit` 0.99 (0.7 on displacements).

**Measured L4D2 differences from the SDK code** (fitted on four test maps and the game's navs):

| Difference | Detail |
|---|---|
| No `HandleObstacleTopAreas` | L4D2 makes no areas on fence tops (measured) |
| Height linking | no two-way link across more than 18 units of height; drops over 64 are one-way; ledges of 19 to 64 units link neither way |
| Seeds | already-covered seeds skipped |
| Cliff check | on (traces 4000 units down) |
| Square-up | doesn't skip after a split |
| Merge cap | a merge is allowed only while the result has at most 32 cells (25 x 25 units); L4D2's areas are much smaller than the SDK code makes; the cap gave the most identical areas |
| Visibility radius | 1500 with no distance cut-off; reverse always computed (analysis.md) |

**How close**: walkable space and where the path breaks match the game; exact rectangles match on only about half the areas (merge order differs); skip links 0 like the game. Verified 2026-09-26: the game loads our nav (a rooms test map: our 247 areas vs the game's 274; flow 4964 vs 5033; 30 commons at 20 s; no warnings). A perfect match isn't required: the Director uses walkable space, connections and area count.

**Native code** (`_hlnav.dll`, `python native/build.py`, zig cc, `-ffp-contract=off`): `hlnav.c` traces and flood fill; `hlareas.c` the whole area pipeline (nodes never become Python objects); `hlbrush.c` every brush's planes and bevels; `hlvis.c` the analysis visibility. Must be bit-identical to the Python reference (same double arithmetic and order, same candidate order, same quirks), checked stage by stage; `nav_native_exact.py` checks the written `.nav` byte-identical (run with Blender's Python 3.11). Python 3.12's `sum()` (Neumaier) differs, so the reference sums plainly. The Python is the fallback when the DLL can't load (other OS), much slower. Generation and analysis run in their own process (`navworker.py`) so they don't share Python's lock with Blender (measured: the map compiler's preparation went 0.3 -> 3.8 s next to nav generation in-process); meshes identical to in-process (c5m1: 2101 areas).

Speed (styleguide_urban, 2026-10-11): collision in C took generation 12.7 -> 2.8 s and analysis 11.4 -> 3.4 s; one shared VMF parse brought generation to 1.7 s; earlier, fixing the native position hash took the flood fill 4.4 -> 0.6 s with the same nodes.

**Marks the generator adds** (`navpredict.py`, as a mapper would paint in Hammer): safe rooms (centre inside the Start Safe Room: PLAYER_START + CHECKPOINT; End: CHECKPOINT; boxes padded down and sideways, **never up**: roof areas with CHECKPOINT break the flow); Zombie Spawn Area boxes add OBSCURED, no-spawn boxes EMPTY and/or NO_MOBS; unreachable areas (no walk/drop/ladder path to where survivors can go, computed before zombie-only climb links) get EMPTY; **drop links off islands** (0.13.3): the game's generator leaves car tops, crates, balconies with no way down (33 such islands on a real map); a one-way drop link is added wherever a crouching player-sized box can move off the edge and fall clear (400 units at most): on a large user map 164 links, EMPTY prop tops 61 -> 4, lost wanderer picks 32 -> 13. Commons-only mark only where infected can't get out (0.13.3).

**Extras**: Zombies Climb Walls (every edge with walkable ground 19 to 160 units higher past a wall gets a one-way climb link up plus a way back down; large user map +680 links, unlinked ledges 56 -> 5; in game commons climbed a 120-unit tower, 14 on top after 75 s); Zombie Climb preset (commons climbed 120 and 160 units in game, not 172; taller warns); Nav Culling (on by default like the game: nav grows only from seeds; off: every flat top inside the map gets nav, 0.13.2's behaviour; the Director spreads commons over every area, so roof areas cost street commons: ~30 starting commons with culling on vs ~17 off on a real map); Nav Area volume (one area exactly where placed, linked both ways to level nav beside it and one way to nav up to 400 units below); Control Volumes (none inside / only inside; no area straddles the boundary). The nav is kept across builds when its inputs' hash (`scripts/vscripts/hammerless/navinputs_<map>.txt`) is unchanged; since 0.17.0 (d06144f, c29bd02) the inputs count only the map's own models, not its textures (`asset_fingerprint(..., ("models",))`: a repainted texture doesn't change where zombies walk; the key takes 0.12 s on the user's 3.4 MB VMF), and Bake Lighting / Compute Visibility keep the nav too: only its analysis is dropped when the visibility lump changed (`bsp_lump_hash(bsp, 4)`), and `nav_follow_bsp` writes the maker marker before touching the nav because `nav_maker` treats a game-made nav that is newer than its marker as unknown (details in [../loading/](../loading/README.md)).

## Limits

| Item | Status |
|---|---|
| Exact rectangles ~50% match (merge order) | known, by design: gameplay-equivalent |
| Light intensity always 1.0; THREAT never written | by design (not reproducible / ignored by the game) |
| Very large maps hit the node limit (500,000 nav points); part of the map gets no nav with a warning | known |
| A full-map nav with roofs marked measured 0-1 commons where the preview predicted ~10 | unexplained (NAV_MESH.md) |
| Native code is Windows-only | known |

Last checked: 2026-10-13 against branch light-layers, commit 10ea8d0 (0.17.0).
