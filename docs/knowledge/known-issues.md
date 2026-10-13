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

## Defaults and settings

### 8. Control Volume: Nav Mesh and Light Baking ticked by default (audit 5 item 1)

- **What happens:** a new Control Volume has *Nav Mesh* and *Light Baking* ticked (`core/entities.py`, the
  `KeyDef("nav", "1", ...)` and `KeyDef("light", "1", ...)` defaults); *Only Inside* means different things per tick
  (for nav it limits every build; for light it's a Bake-dropdown choice and full builds bake everything). Nothing
  reports how much floor a volume cut out of the nav. Both of the user's audited .blend files hold an Only-Inside
  volume with every tick on.
- **Also:** under Only Inside, the *Zombie Spawns* toggle is drawn pressed while it's ignored, and its stored value
  comes back as a map-sized no-spawn box if the mode is switched (audit 5, `ui.py` around line 359).
- **Suggested fix:** every tick off by default ("pick what this volume controls"); label Only Inside per tick; a
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

Last checked: 2026-10-13 against branch light-layers, commit 30de660.
