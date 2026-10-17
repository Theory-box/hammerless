# Known issues (open)

Behaviour problems found by audits or testing that are **not fixed yet**, each with what happens, where it lives in
the code, and the fix suggested so far. Investigated 2026-10-13 against commit 30de660 (read in the code; no fixes
made). When one is fixed, move it to the "Fixed" table at the bottom with the commit.

Sources: the October 2026 audits in `docs/dev/audits/` (audit 2: build pipeline, audit 4: nav, audit 5: silent
traps), re-checked in the current code.

## Nav

### 1. "Made by the game" nav never gets the unreachable marks (audit 4 item 1, HIGH)

- **What happens:** with *Nav Mesh: Made in Blender* (the default), areas the survivors can never reach (sealed
  spots, roofs too high to drop from) are marked EMPTY, so the Director doesn't waste wandering-common picks on them.
  With *Made by the game*, nothing marks them: the Director can place commons where no survivor can go.
- **Where:** the marks are made only in Blender's generator, `navpredict.py` (the reachability walk from the
  PLAYER_START areas, `m.spawn_attributes |= EMPTY`, around line 311-327). The game-made path runs
  `nav_generate` and then `navmark_<map>.nut` (`core/nav.py: navmark_script`, called from `compile.py: nav_steps`),
  which marks only the safe-room regions and the zombie climbs.
- **Suggested fix:** after the game saves its nav, load the .nav in Python, run the same walk, mark and write it back
  (like `clear_nav_analysis` does for its edits); or do the walk in the navmark Squirrel script.
- **How much it matters:** Valve's own maps mark about half of their unreachable areas EMPTY (audit 4 table:
  c1m1 47 of 99, c2m1 69 of 124). *Made by the game* is the fallback nav source, so fewer maps hit this.

### 2. No start room: nothing is marked, silently (audit 4 item 4, MEDIUM)

- **What happens:** the reachability walk starts from the PLAYER_START areas (the Start Safe Room). With no start room,
  or a start-room box that catches no nav areas, the walk is skipped (`if start:` in `navpredict.py`) and no warning
  says so.
- **Suggested fix:** seed the walk from the areas under `info_player_start` / `info_survivor_position` when there are
  no PLAYER_START areas; warn when a PLAYER_START region marks 0 areas.

### 3. Control Volumes do nothing for a game-made nav (audit 4 item 6, LOW)

- **What happens:** a Control Volume ticked *Nav Mesh* limits Blender's generator; with *Made by the game* it has no
  effect, and the UI doesn't say so.
- **Suggested fix:** a note in the volume's panel when the nav source is the game.

## Build & Play

### 4. Two .blend files in one folder with the same Map Name (audit 2 item 2, audit 5 item 2)

- **What happens:** the build goes to `//hammerless_build/<map>.vmf` next to the .blend, so two .blend files in the
  same folder using the same Map Name share one build folder: the compiled map, the smart-build records
  (`.built.vmf`, `.built.opts`), build times and leak files. Each build replaces the other's map in the game, with
  no warning.
- **Where:** `blender/ops.py` (around line 459) warns only when the map's owner record
  (`scripts/vscripts/hammerless/owner_<map>.txt`, `compile.py: map_owner / set_map_owner`) names a *different* VMF
  path. Two files in one folder write the same VMF path, so the check can't fire. When it does fire it's only a
  warning.
- **Related:** a new file's Map Name defaults to `my_map`, so every new file starts out sharing one name. An imported
  map keeps the imported map's name (e.g. Valve's `styleguide_urban`), and builds over anything filed under it
  (`maps/styleguide_urban.bsp`, its nav, its scripts).
- **Suggested fix:** own the name by (.blend path, scene) and ask before taking it over; a build folder per .blend;
  default the Map Name from the file name, or warn while it's `my_map`.
- **Already safe:** names the game itself owns (its campaign maps) are refused (`compile.py: foreign_map`).

### 5. A failed compile after the game was started early (audit 2 item 5)

- **What happens:** Build & Play starts the game before the export (`ops.py`, `cc.prestart_game`), so it boots while
  the map compiles. If the compile fails or is cancelled: a game this build started sits at its main menu; a game
  that was already running stays on the previous version of the map. The failure is reported only in Blender
  (`self.report` and the Problems list), which is behind the game window.
- **Suggested fix:** tell the game (a console message or HUD text through the two-way link) that the build failed,
  or bring Blender to the front on a failure.
- **Partly addressed (0.17.0, d06144f):** the old map no longer runs the new build's logic: the map's scripts are
  written only once the compiled map is in the game, and the nav processes stop on every ending (see
  [loading/](loading/README.md)). The missing note in the game remains open.

### 6. Warnings are hidden behind the game (audit 5 item 3)

- **What happens:** a successful Build & Play ends with the status-bar line "Compiled. Launching L4D2 on <map>"
  (`ops.py` around line 1554), which replaces earlier warnings; nav warnings arrive after the launch; the Problems
  list colours only errors and shows few rows. A play-affecting warning (much of the map marked no-spawn, the nav
  limited by a volume, no End Safe Room, the Map Name owned by another file, Auto Test on) is easy to miss while the
  game is in front.
- **Suggested fix:** confirm before launching when a play-affecting warning exists, or print those warnings in the
  game when the map loads (the map's ready script); tier and colour the warnings.

### 7. Switching the vis / map / light compiler doesn't rebuild (audit 2 item 4)

- **What happens:** the smart build's options stamp leaves out which compiler is chosen (`compile.py: _opts_rest`
  strips `*_tool`), so switching e.g. to Valve's vvis to compare says "Map unchanged: skipped".
- **Status:** the vis knowledge page records this as by design (the outputs are meant to be equivalent); audit 2
  calls it a defect. Undecided. *Start Fresh* forces a full build in the meantime.

### 12. Build & Play's first press in a fresh Blender still freezes about 5 s on a big map (2026-10-14)

- **What happens:** the part of the button press that runs on Blender's main thread (`ops.export_vmf`: scene read,
  Custom Model compiles, VMF write) freezes the window. On the user's imported map with 32 Swelling Pods, measured
  in a Blender window: 4.6 s on the first build after starting Blender, 1.2 s on a rebuild, no hitch over 0.3 s
  during the compile (5d40ae4; before it 9.2 s / 4.9 s, and 20-30 s before the pose cache; see the fixed table).
- **Where:** the first press pays the imported map's parse (1.5 s, `vmfimport._document`, kept afterwards), the
  shared pod model's compile (studiomdl on the main thread, `ops.py`), and cold caches.
- **Suggested fix:** compile Custom Models in the background compile job instead of in `execute`; parse the
  imported map in the background or keep the parsed Document with the file. Target: under 1 s on every press.
  Before calling it fixed, profile the whole button press on the user's map (cProfile of `ops.export_vmf` plus a
  0.1 s Blender timer measuring gaps over a whole windowed build); and read `%TEMP%\hammerless_freeze.log` (the
  freeze recorder, `blender/watchdog.py`) first for any freeze report. Details in
  [loading/README.md](loading/README.md).

### 14. Our map compiler is slower than vbsp on the user's map (2026-10-14)

- **What happens:** in the whole-build benchmark ([loading/README.md](loading/README.md)) hlvbsp took 5.4 s (median
  of 3) against vbsp's 4.5 s on the imported 4.6 MB map; the other stages are many times faster than Valve's.
- **Where:** `native/hlvbsp/`; not profiled yet.
- **Suggested fix:** profile a full compile of this map (which phase is slower: the CSG, portals, the collision
  writer, the Python-prepared tables), then match or beat vbsp.

## Logic

### 15. Top-down camera: effects on the player don't render through a camera entity (2026-10-14)

- **What happens:** through a `point_viewcontrol` the game draws neither the laser sight nor the flashlight (both are
  drawn from the player's own eyes), nor glow outlines (`m_Glow.m_iGlowType` 3 is set and reads back but isn't
  drawn), nor an `m_clrRender` tint on commons, nor an `env_sprite` near the player. An `env_beam` between two
  `info_target`s draws where the targets were when it switched on and never follows them (targets are server-only
  unless spawnflag 2, always transmit); `env_laser` moves its end only on its own ~0.1 s think. Also, a camera
  parented to the player inherits their pitch, and `cl_pitchup` / `cl_pitchdown` can't be set by a map.
- **Where:** the Top-Down Camera node group (`hammerless/blender/logicgroups.py`, Example 62). The Laser Sight
  (56592cf, 4ccb44a) and Aim Highlight (587d0d3) were removed in 04d5abc; the mode keeps the fixed camera, Screen
  Movement and Level Aim. Details in [logic/README.md](logic/README.md), "Top-down and camera modes".
- **Status:** decided, not fixable from a map: the user judged the mode too limited by what renders through a camera
  entity. Anything an aim indicator needs must be a world model or a beam between always-transmitted points
  spawned in place.

## Defaults and settings

### 8. Control Volume: Nav Mesh and Light Baking ticked by default (audit 5 item 1)

- **What happens:** a new Control Volume has *Nav Mesh* and *Light Baking* ticked (`core/entities.py`, the
  `KeyDef("nav", "1", ...)` and `KeyDef("light", "1", ...)` defaults). Nothing
  reports how much floor a volume cut out of the nav. Both of the user's audited .blend files hold an Only-Inside
  volume with every tick on.
- **Partly addressed (6f38061, 2026-10-16):** *Only Inside* now means the same for every tick: every enabled volume
  applies to every build and every Bake Lighting (the Bake dropdown no longer lists volumes). Each volume has an
  *Enabled* switch and a panel lists them, so a volume that cuts too much can be switched off without deleting it
  ([lighting/bakes-sky-cycles-view.md](lighting/bakes-sky-cycles-view.md)).
- **Also:** under Only Inside, the *Zombie Spawns* toggle is drawn pressed while it's ignored, and its stored value
  comes back as a map-sized no-spawn box if the mode is switched (audit 5, `ui.py` around line 359).
- **Suggested fix (what's left):** every tick off by default ("pick what this volume controls"); a
  Problems row per volume with the share of floor it leaves out, alerting above 20%.

### 9. Launch forces the difficulty (audit 5 item 5)

- **What happens:** *Difficulty* defaults to Normal (`blender/props.py`, `difficulty` default `"Normal"`), so every
  Build & Play sets the game's difficulty; *Keep Current* exists but isn't the default. *Fast Map Loading* is on by
  default (`sv_lan 1`: friends can't join; its tooltip says so).
- **Suggested fix:** default to *Keep Current*; name the forced difficulty and "LAN only" in the launch line.

### 10. Director panel off (audit 5 item 4, partly addressed)

- **Now:** with the Director panel off, its header says "game default".
- **Still open:** the panel's numbers are greyed but still shown (e.g. Max Specials 4, while the co-op default may
  differ); the DirectorOptions written when it's on aren't logged.

### 11. Misleading Visibility message on imported maps (audit 5 item 6)

- **What happens:** "Control Volumes ticked Visibility do nothing here: they need Auto Seal (the map's own walls must
  keep sealing it)" (`core/build.py` around line 499) also shows on imported maps, where the real reason is that
  imported maps keep their own walls (Auto Seal off by design).
- **Suggested fix:** a separate message for imported maps.

## Fixed since the audits

| Item | Fixed | How |
|---|---|---|
| Audit 2 item 1: no build log on disk | 2026-10-11 | `<map>.lastbuild.log` in the build folder (commands, smart-build reasons, timings) |
| Audit 2 item 3: fallbacks leave no trace | 2026-10-11 / 10-13 | in `.lastbuild.log` and Problems; designed fallbacks removed, see [compilers/valve-fallbacks.md](compilers/valve-fallbacks.md) |
| Audit 4 item 2: wall climbs undid the unreachable marks | a267cc1 | reachability worked out before the climb links |
| Audit 5 item 7: unsaved .blend builds into a temp folder silently | earlier | warning: "The .blend isn't saved: the build goes to a temporary folder..." (`ops.py`) |
| Audit 5 item 7: hlvrad's "can't load embree4.dll" fallback | e1e0d1d | the build no longer asks for Embree (never shipped) |
| A failed, leaking or cancelled build left the old map running the new scripts, with the nav processes still going (part of audit 2 item 5) | d06144f, c29bd02 (0.17.0) | scripts written only with the new map; `_stop_nav` on every ending; the cancel message says what's true |
| Bake Lighting / Compute Visibility then Play made the game regenerate the nav (two extra loads) | d06144f, c29bd02 | the nav follows the new map; only its analysis is redone when the visibility changed |
| Imported maps with accented characters failed to build until Start Fresh (`UnicodeDecodeError` on the built `.vmf`) | d06144f | every VMF read uses `errors="replace"` |
| Cycles lighting froze Blender while the nav mesh was being made | d06144f | the bake starts after the nav thread ends |
| Extra vrad options left out were logged as fallbacks | c29bd02 | plain notes when the tool runs; `-extra` known; Valve's vrad never gets `-scale` / `-ambient` / `-sky` |
| Socket numbers lost digits (12345.67 -> 12345.7); `inf` / `nan` crashed the script | c29bd02 | `num_text`: shortest text that round-trips the single-precision value; 3.4e38 / 0.0 |
| Build & Play froze Blender 20-30 s once 32 Swelling Pods were in the user's map (from 2026-10-12 19:02; `_sample_actions`: `frame_set` per frame of each pod's actions on a 3,134-object scene, ~150 ms depsgraph rebuild on the first frame of each action) | f57eadf, c29bd02 (0.17.0) | the pose cache: one sampling shared by all 32 pods (one rig fingerprint); traced with the freeze recorder (`watchdog.py`, 0df63c3); last user freeze in the log 10-12 20:29, before the fix was installed. Ruled out on the way: netprops catalogue, Example 62, Live Light Mixing's handler, other add-ons' handlers, quality migration, panel drawing, the Play path's waits |
| studiomdl runs one after another (32 pods: 10 s of waiting) | 5865e70 | up to 8 at once |
| Build & Play froze 9.2 s on a first build and 4.9 s on a rebuild of the user's map (32 pods) with 0.17.0 as released | 5d40ae4 (2026-10-14) | 4.6 s / 1.2 s: copies of a rigged model share one model by content (32 pods = 1 model), the imported file's parsed Document and each brush's faces are kept between builds, instance lookups from a memo, the prestart after the export; VMF text identical to before (see [loading/](loading/README.md)) |
| A build folder past Windows' 260-character path limit made the light compiler fall back to Valve's vrad silently (its model copies couldn't be written) | 283c44a (2026-10-14) | the export warns when the path would pass the limit (the fall back itself stays the net; [compilers/valve-fallbacks.md](compilers/valve-fallbacks.md) case i) |
| Map names of 32+ characters: the game loaded the map but never joined its own listen server (`console.log`: "Netchannel: failed reading message net_SignonState from loopback", no HAMMERLESS_READY; was item 13). Measured 2026-10-14 on `demo_level`: 31 characters loads and the survivors spawn, 32 and 33 don't | 78892b4 (2026-10-14) | `props.MAP_NAME_MAX = 31`: `clean_map_name` trims a typed name to 31 characters, and `build_map_text` refuses a longer stored Map Name with a message |
| Build Nav Mesh was preview-only (its compile has `copy_to_game=False`): Play then ran the game's own `nav_generate`, or an older nav | fc1d655 (2026-10-16) | when the game's map has the same `bsp_signature` as the compile the button analysed, the nav goes into the game with the inputs key a Build records; otherwise the button says Build puts it there ([nav/README.md](nav/README.md)) |
| A build that made the nav in Blender never showed it (the nav preview said "Build Nav Mesh to see it"); the lighting preview reloaded only at the next redraw; "What can be seen from the 3D cursor" with the cursor off the nav drew everything near-invisible grey; the lighting preview hid the nav overlay at a distance | 196457a, 7238ac9, dae428a (2026-10-16) | builds show the nav they make (analysis and problems) and switch the overlay on; the lighting view reloads when the build ends; usual colours plus a legend hint; the nav draws with its own shaders and a 4e-5 depth bias |
| "Zombies Climb Walls" made a route that only works through climb links read "Start to end: OK" (the user's map: a start area 35-88 units below everything around it) | 00723c8 (2026-10-16) | renamed *Auto Climb Links* and described as what it is: ordinary nav connections the flow and the bots also walk ([nav/README.md](nav/README.md)) |
| Light entities (`light`, `light_spot`, `light_environment` point entities) and Blender lights were two systems with two panels | f8e16b2 (2026-10-16) | one system: the Add panel adds Blender lights and old files' light entities convert on load ([lighting/layers-and-styles.md](lighting/layers-and-styles.md)) |

Last checked: 2026-10-16 against branch behaviours, commit 18c2ce4 (item 8 and the Fixed table; the rest 2026-10-14 against 04d5abc).
