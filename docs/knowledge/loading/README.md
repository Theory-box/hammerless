# Loading: Build & Play, the parallel build, smart builds, and the game's load time

Everything measured about getting from the Build & Play button to standing in the map: the game prestart, the parallel build and its critical path, smart builds (including `hlvbsp -onlyents` and the kept nav), Fast Map Loading (`sv_lan 1`), the stringtable dictionary, and the timings. Compiler-level facts (which tool runs, fallbacks, options) are in [../compilers/](../compilers/README.md); lighting alongside vis in [../lighting/gpu-and-bounce.md](../lighting/gpu-and-bounce.md); the kept nav in [../nav/](../nav/README.md). User-facing: `docs/systems/BUILD_AND_PLAY.md`, `docs/TROUBLESHOOTING.md`.

Code: `hammerless/core/compile.py` (`CompileJob`, `prestart_game`, `launch_game`, `send_commands`, `parse_flow`), `hammerless/core/buildplan.py` (smart build plans, `strip_stale`), `hammerless/core/stringtables.py` (the dictionary), `hammerless/core/timeline.py` (the build chart; steps collected in `blender/ops.py` `_timeline`), `hammerless/core/gamefiles.py` (the ready script), `hammerless/core/navworker.py`. Tests: `tests/unit/test_core.py`, `tests/ingame/bench.py` (`build`).

Timeline (2026): smart builds 09-30 (c9a5577); audit round 1 of compile & Build & Play 10-03 (ad08c65); prestart and LAN loading 10-08 (612da78); stringtable dictionary 10-09 (351c6b5); lighting alongside vis and steps not slowing each other 10-09 (5a08f9f); build timeline and the kept nav 10-11 (a4d77c8, 87a2bc4); `lastbuild.log` 10-11 (1eb6acb); `hlvbsp -onlyents` 10-13 (42f40b9); Build & Play fixes (scripts with the new map only, the nav kept after Bake Lighting / Compute Visibility, nav processes stopped on every end, Cycles after the nav) and build caches 10-13 (d06144f, d82f2ad, c29bd02, 10ea8d0; 0.17.0).

## The steps and what runs where

| # | Step | Runs |
|---|---|---|
| 1 | Game prestart (only if Steam is running with someone signed in) | at once, background |
| 2 | Export `.vmf` into `hammerless_build` next to the `.blend` | Blender |
| 3 | `hlvbsp` (after a Python preparation of material/model tables from the game's files; then the build signals other Python work may start) | own process |
| 4 | `hlvvis`: all cores, the critical path of a full build | own process |
| 5 | `hlvrad` on a copy `<map>.light.bsp` with `-visfrom`, alongside vis (GPU + ray-traced bounce only; not with vrad's patch bounce, Valve's vrad, or radial vis); byte-identical to lighting after vis | own process |
| 6 | Nav generation during the compile | own process |
| 7 | Nav analysis on a copy `<map>.analysis.bsp` once the last vis step is past, while lighting may still run | own process |
| 8 | Copy `.bsp` and `.nav` into `left4dead2/maps` (the dictionary taken from the old copy first; if the game has the map loaded Windows refuses the copy, so `disconnect` is sent and the copy retried); then the map's scripts (`write_map_scripts`: since 0.17.0 they are only made during the export and written once the compiled map is in the game, see below) | |
| 9 | Launch: `-hijack` into the running game or a fresh one; before `map`: `sv_cheats 0` (resets cheat cvars left from testing), `sv_lan` per Fast Map Loading, the difficulty if set, `mat_fullbright 0` for a lit map | game |

Facts behind step 9 (measured): the engine turns `mat_fullbright 1` on after loading an unlit map and leaves it on for the session (so a lit map then looks unbaked); a fresh game ignores the game mode on its command line, so a map in another mode is loaded first and switched from the console; a game started before Steam is ready quits with "Steam is not running" (the launch waits up to two minutes for Steam); `send_commands` (`-hijack`) starts the game **without** `-condebug` if it isn't running, so there is no `console.log`: use `launch_game`.

## Game boot and map load (measured 2026-10-08, a props test map, 16-core PC)

| What | Measured |
|---|---|
| Game boot to the main menu | about 4.5 s; the menu marker in `console.log` is `BinkOpen(` (the background movie); `-hijack` commands sent after it are taken in 0.1 s, with no second game copy started |
| Map load into a running game | about 11.5 s, of which about 6 s is a silent wait right after "VAC secure mode disabled" (stock maps too): the listen server registering with Steam's servers |
| The same with `sv_lan 1` | 5.1 s. *Fast Map Loading* (on by default) = `sv_lan 1`; cost: friends can't join online |
| The game rewrites the `.bsp` on map change | "Updated stringtable dictionary saved to maps/<map>.bsp" |
| Button to map loaded, 4-second build | 18.1 s before prestart, 8.1 s with prestart + Fast Map Loading |
| Prestart timing | waits up to 90 s for the menu, then sends the map anyway; a game closed while booting is started afresh with the map; prestart only when Steam is ready |
| `con_timestamp` | doesn't exist in L4D2 |
| WPR CPU profiling | needs admin on the test machine (the in-game 6-7 s remains unprofiled) |

## The stringtable dictionary (measured 2026-10-09)

L4D2 compresses its networked string tables (precached models, sounds, scenes) with a dictionary stored in the map's pakfile as `stringtable_dictionary.dct`: plain null-separated strings, no header or CRC, about 36,000 of them, mostly the game-wide sound precache and Scenes. A fresh compile has none, so the game builds one while connecting ("Creating dictionary") and writes it back into the `.bsp` when the map unloads: about 10 s per load after every fresh build (map start 12-13 s against 2.9 s).

| Dictionary in the map | Result |
|---|---|
| None | built during the load, about 10 s |
| Near-complete, even stale (a 90% match from an older build) | as fast as an exact one |
| About half the strings (Valve's c1m1 as an example) | as slow as none |
| Nearly empty | `Host_Error "Overflow error writing string table baseline Scenes"`: the load fails |

`stringtables.py` carries the game-saved dictionary into each build: read from the game's copy of the old map before it is replaced; used if it has at least 10,000 strings and saved as the last good one (`scripts/vscripts/hammerless/stringtable_dictionary.dct` in the game folder), else the saved one is used; the map's own file names in it are renamed; written into the new pakfile as a stored entry. The first build on a computer pays the 10 s once; a failure never fails the build. Measured on a large user map: map start 9.1 s -> 2.6 s, Build & Play 35 s -> 30 s.

## The parallel build (2026-10-09, 5a08f9f)

- **Contention is the real limit.** Total CPU work is fixed: fewer hlvrad threads during vis made the build slower (all cores 10.0 s, 8: 10.3 s, 4: 10.8 s, 2: 12.4 s on a real map; `LIGHT_THREADS_DURING_VIS = 0`). Vis (~6.5 s on all cores on a large user map) is the critical path.
- **Python steps in Blender share the GIL.** `prepare_hlvbsp` went 0.3 s -> 3.8 s next to nav generation in-process; nav now waits on `job.prepared`, and the nav mesh and its analysis run in their own process (`navworker.py`).
- The build log ends with a timeline chart and the critical path ("waited on, in order: export -> map compiler -> vis -> ... -> game load"), found by walking back from the step that ended last through the step that ended just before each one started.

Fresh Build & Play of a large user map (Final, full vis, from nothing to survivors): 35.3 s that morning (before the dictionary fix) -> 29.0 s -> 26.6 s. Left at the time: ~0.9 s idle before vis, a ~4 s tail (prop lighting against nav analysis), the game load 9.6 s (2.8 s to "Map:", then ~6.8 s inside the game to survivors: game-internal, unprofiled). Step times after the dictionary fix and before the parallel build: export 2.8 s, map compiler 3.9 s, vis 5.9 s (full), light 5.9 s, game 9.0 s (map start 2.6 s + ~6 s to survivors). Next candidates noted: the lightmap upload ("Redownloading all lightmaps" ~1.5 s), the sound cache, hot-swap.

## Smart builds (`buildplan.py`)

Each build compares the new `.vmf` with `<map>.built.vmf` and picks the least work that gives the same map as a full compile; anything it can't classify counts as geometry (a doubt means a full compile).

| Plan | When | What runs |
|---|---|---|
| same | nothing reaching the map changed, same options, the game has this build's map | nothing |
| entities | only entity keyvalues, outputs or point entities changed | `hlvbsp -onlyents` rewrites the entity lump; geometry, vis, lighting kept |
| remix | only colour/brightness of lights baked into their own layers changed | `-onlyents`, then `hlvrad -remix` (no bake; lighting/) |
| lighting | lights or static props changed | `-onlyents`, then the light compiler |
| full | geometry changed (world and brush-entity brushes, origins, classes, displacements, overlays, cubemaps, detail props, area portals, ladders, instances, the fog far Z, an area portal window's target) | map compiler, vis, light |

Full compile also when: an instance file changed; a new or moved entity sits where the compiled map is solid (the compiler fills rooms no entity reaches; logic entities at the origin don't count, 1eb6acb); the last build was Valve's tool after a failure of ours (no record to build on); a remix can't be used. A lower-quality vis is upgraded without recompiling geometry (after Bake Lighting's fast vis, the next build adds only the full vis).

`hlvbsp -onlyents` (2026-10-13, 42f40b9; `native/hlvbsp/onlyents.c`) writes the same file as `vbsp -onlyents`: byte-identical in about 1,340 comparisons; Valve's runs only as the crash fallback or when Valve's map compiler is chosen; both leave `stale.txt` in the map (the engine treats it as not final), which Hammerless strips (`buildplan.strip_stale`). Details and the fixed lit-map lump order in [../compilers/valve-fallbacks.md](../compilers/valve-fallbacks.md).

The nav is kept when its inputs' hash (`navinputs_<map>.txt`) is unchanged, the nav on disk was made in Blender and analysed, and no safe-room or spawn marks changed; only its header's `bsp_size` is updated (the game checks it). Before 87a2bc4, entity-only smart builds regenerated the nav every time (audit 2: slow, not wrong). Each build writes `<map>.lastbuild.log` (commands, the smart-build reason, timings, "nav mesh kept").

Audit 2 (2026-10-11) items: no build log on disk (fixed: `lastbuild.log`); two `.blend` files with the same map name share one build folder and owner record silently (open; audit 5 ranked it second); fallbacks leave no trace (addressed by the log and the build record, compilers/); switching the vis/map/light compiler doesn't rebuild (by design, vis/); Build & Play prestarts before export, so a failed or cancelled compile leaves the game on the old map without a note (open for the note; since 0.17.0 the old map at least keeps its own scripts, see above); a Valve `<map>.log` from an earlier `vvis -fast` run was mistaken for our vis's numbers (our tools never write it; the loaded map had ~510 clusters visible per cluster, hlvvis 519, vvis 507).

## Build & Play endings: what the game is left with (2026-10-13, 0.17.0)

Before d06144f a build wrote the map's scripts (`scripts/vscripts/hammerless_<map>*.nut`, the mode file) during the export, so a build that then failed, leaked or was cancelled left the **old** map in the game running the **new** logic, and its nav generator / analysis processes kept running. Fixed in `hammerless/blender/ops.py`:

| Fact | Detail | Status |
|---|---|---|
| Scripts go in with the map | `export_vmf(defer_scripts=True)` only makes the files (`rep.pending_scripts`); `HL_OT_build._write_scripts` writes them (`write_map_scripts`: write, remove the ones this build no longer makes, record) once the compiled map is copied into the game, and on a cancel that came after the compile had finished (the map is in the game; its nav isn't). The one exception is `navmark_<map>.nut`, written early because `needs_nav` compares it with the nav's own copy of the marks, and the old map never reads it. A write failure ends the build with an error naming the game folder. | `done` (d06144f) |
| Nav processes stop on every ending | `_stop_nav` runs from `_finish` (failed or leaking builds), `_cancel_build`, and `cancel` (Blender ending the operator: File > Open / New, window closed; that path also cancels the compile job, since its scripts would be lost with the operator). The watcher threads end the `navworker` processes. | `done` (d06144f, c29bd02) |
| Cancel message | "Build cancelled: the map in the game and its scripts are unchanged (the next build compiles the map again)", or, when the compile had finished, "...the map was already compiled and is in the game; its nav mesh wasn't finished". `_cancel_build` polls the job first, so a job that ended just before the cancel counts as compiled. | `done` |
| The nav after Bake Lighting / Compute Visibility | Both used to leave the nav to "the next Build / Build & Play", which made the game regenerate it (two extra map loads). Now `_nav_fit` (a nav and a map in the game, a known maker, not outdated) keeps it: after the compile `nav_follow_bsp` gives the nav the new map's size, and if the visibility lump changed (`compile.bsp_lump_hash(bsp, 4)` before and after) `clear_nav_analysis` + `set_nav_inputs(None)` drop only the analysis, so the game re-analyses but doesn't regenerate. `nav_follow_bsp` writes the marker **before** touching the nav and then `os.utime`s it: `nav_maker` treats a game-made nav newer than its marker as unknown, so the old order (nav first, marker after) could make a game-made nav look unowned. | `done` (d06144f, c29bd02) |
| Cycles bake after the nav | `_eye_points` used to `join()` the nav thread, freezing Blender until the nav mesh was done; `_step` now starts the Cycles bake only once the nav thread has ended (the bake skips faces nobody sees from the nav). Settings are read from the built scene (`_settings`), not whichever scene is shown. | `done` (d06144f) |
| Imported VMFs | Hammer's VMFs are latin-1 (accented characters); every read of the built `.vmf` (`compile.py` smart-build comparison, `navworker` analysis and generation, `bspcheck.objects_near`, `visdata.portal_owners`) uses `errors="replace"`, so they no longer fail with `UnicodeDecodeError` until Start Fresh. | `done` (d06144f) |
| A new `console.log` | `_watch_ready` restarts reading from 0 when the log got smaller (the game started a fresh `console.log`). | `done` (c29bd02) |
| The analysis's own map copy | `<map>.analysis.bsp` is removed when the analysis ends. | `done` (d06144f) |

In-game results (2026-10-13, `tests/ingame/bench.py`): Build & Play passes; Bake Lighting then Play loads once with no `nav_generate`; cancel during the nav leaves the new scripts in place (the map was already in the game) and no nav process running.

## Build caches (2026-10-13, d82f2ad / c29bd02)

What a rebuild no longer redoes when its inputs are the same, each keyed by what it depends on:

| Cache | Key | Where | Note |
|---|---|---|---|
| Sound trace (`_TRACE_CACHE`) | the solid geometry arrays (float32 vertices, corners, sizes), the starts (rounded to 3 places) and the Sound volumes | `blender/sound.py` | `scene_geometry` must reproduce mathutils' `matrix_world @ co * scale` exactly: single-precision products summed in double, then scaled in single. The listening-spot grid starts at the geometry's bounds, so a last-bit difference moved every spot (650 -> 616 spots on the test map). See [../sound/](../sound/README.md). |
| Packed images (`_image_signature`) | a packed image's bytes (SHA-1), size and colour space; a file image's path, size and mtime | `blender/extract.py` | packed images were converted on every build before |
| Exported assets (`asset_fingerprint`) | each file's name, size and mtime under `materials/hammerless/<map>` and `models/hammerless/<map>`; the files are read and hashed only when that listing changed (`_ASSET_PRINTS`) | `core/compile.py` | |
| Sky picture (`_SKY_CACHE`) | the skybox faces' paths, sizes and mtimes, or the sky image's signature plus its rotation; the saved `<map>.hlsky_src.npy` must exist | `blender/ops.py` `export_sky` | |
| Nav inputs key | models only (`asset_fingerprint(..., ("models",))`): a repainted texture no longer remakes the nav | `blender/ops.py` `_nav_key` | |
| Sampled rig poses (`_POSE_CACHE`) | see [../models/](../models/README.md) | `blender/extract.py` | |
| Scene sorting (`mapcollection._on_depsgraph`) | every object is listed only when the object or collection counts changed or an unknown object was updated; objects of instanced collections evaluated but not in the scene are remembered (`_outside`) so they don't trigger a relisting; `forget_bake_volumes` only when a Control Volume changed | `blender/mapcollection.py` | was a full pass on each move and on each of a build's scene updates |
| Collision thickness (`_hull_thickness`) | numpy over all faces and points at once | `blender/extract.py` | 30x faster (CHANGELOG 0.17.0) |

Measured small, not worth caching (the user's 3.4 MB VMF, 2026-10-13): the nav inputs key 0.12 s, `bsp_signature` 0.02 s, `prepare_hlvbsp` 0.35 s.

## The two-way link and cancel

Commands go in through `-hijack` (or the bench's file channel, logic/); results come back through `console.log` (`-condebug`): `HAMMERLESS_READY survivors spawned` (the load's end marker and the gate for game-side nav steps, which fail before survivors spawn), `HAMMERLESS_FLOW ...` (nav/), `HAMMERLESS_DIRECTOR map options active`, `HAMMERLESS_EVENT` (debug log), `HAMMERLESS_CRESCENDO`. Build & Play with a game-analysed nav loads an analysis map first (a second `HAMMERLESS_FLOW` follows); with a Blender-made nav there is one load. Cancel (Esc or the red button) stops the compilers at once, the nav and its analysis at their next stage, copies nothing (the map in the game and its scripts stay as they were, since 0.17.0), and the next build compiles again; closing Blender stops the build (the game keeps running); a second Build can't start while the first is making the nav, analysing or baking.

## Limits and known issues

| Item | Status |
|---|---|
| The in-game 6-7 s between map start and survivors | not profiled |
| First build on a computer pays the dictionary once | by design |
| Lighting alongside vis needs GPU + ray-traced bounce and no radial vis | by design |
| Smart builds need a successful last build (cancelled / failed / rescued builds leave no record) | by design |
| Two `.blend` files sharing a map name | known issue (audits 2, 5) |
| Launch silently forces the difficulty and LAN mode; Build & Play hides warnings behind the game | known issue (audit 5) |
| A running game that won't change map: quit and press Build & Play again | known (TROUBLESHOOTING.md) |
| Game-side files pile up (`.navNN` backups, old `hl_*` maps, 39 MB `console.log`) | known issue (audit 6) |
| A failed or cancelled build isn't announced in the game (only in Blender, behind the game window) | known issue (audit 2 item 5); the old map keeps its scripts since 0.17.0 |

Last checked: 2026-10-13 against branch light-layers, commit 10ea8d0 (0.17.0).
