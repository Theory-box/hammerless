# Audit 5 (2026-10-11): user-facing defaults and silent traps

By a Fable auditor; evidence from copies of the user's two Downloads .blend files (both the imported
styleguide_urban, both building "styleguide_urban" into the same folder). Ranked by hours likely lost.

1. **Control Volume ticks Nav Mesh and Light Baking by default, and "Only inside" means different things per
   tick.** entities.py:311-318 (nav, light default "1"); for light, Only inside is just a Bake-dropdown choice
   (builds bake everything); for nav it limits every build (nav.py:85-87). Nothing reports how much floor a volume
   cut. Both user files hold an Only-inside volume with all five ticks on. Fix: default every tick off ("Pick what
   this volume controls"); label Only inside per tick; build-log/Problems row per volume with the share of floor
   left out; alert over 20%. Related: under Only inside the Zombie Spawns toggle is drawn pressed while ignored, and
   its stored spawns=1 comes back as a map-sized no-spawn box if the mode is switched (ui.py:359).
2. **Two .blend files building one map name: the guard can't fire.** ops.py:445-448 compares VMF paths; two files in
   one folder share `//hammerless_build/<map>.vmf`, the stamp, build times and leak files. When it does fire it's
   only a warning. map_name defaults to my_map for every file. Fix: own the name by (.blend path, scene) and ask
   before taking it over; build folder per file; default the name from the file name or alert while it's my_map.
3. **Build & Play hides every warning behind the game.** Warnings are status-bar reports overwritten by "Compiled.
   Launching L4D2" (ops.py:1473); nav warnings arrive after launch; Problems rows only colour ERROR, truncate text,
   show 6 rows. Fix: confirm before launching when a play-affecting warning exists (no-spawn share, nav limited by a
   volume, no end room, name owned by another file, autotest on), or print them in game from ready.nut; tier and
   colour warnings.
4. **Director panel off shows numbers the game doesn't use** (director_options.py:4-6; MaxSpecials starts at 4,
   the co-op default may be 2). Fix: when off say "the game's own Director settings apply"; log the DirectorOptions
   written when on.
5. **Launch silently changes game settings:** difficulty forced to Normal (props.py:822, compile.py:1675),
   fast_loading = sv_lan 1 (friends can't join), autotest/debug_log left on without notice. Fix: KEEP difficulty by
   default; say "LAN only" and the forced difficulty in the launch line; alert when autotest is on.
6. **Misleading "Control Volumes ticked Visibility do nothing: need Auto Seal"** on imported maps (build.py:479),
   where the cause is that imported maps keep their own walls. Fix: a separate message.
7. Smaller: unsaved .blend builds into temp (say "Save the file first" in the Map panel); auto sun added silently;
   hlvrad "can't load embree4.dll: using vrad's ray tracer" fallback only in the log; the Slow Motion example turns
   cheats on (warned, but see 3).

Checked and fine: compile/vis/light/nav/sky/fog defaults are explained in tooltips and panel headers; Organize Scene
warns about objects outside the collection; foreign (game-owned) map names are refused.
