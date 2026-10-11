# Audit 6 (2026-10-11): what Hammerless puts in the game and how it launches it

By a Fable auditor, with in-game runs (bench, hl_test_examples).

In game:
- tests/ingame/test_examples.py: 40 passed, 1 failed, 4 skipped. The failure (33 "Bring Back Stragglers") passes
  alone: flaky check (the bot lifted 2500 u up falls back under 1500 u before the timer tick). Fix the test: move
  the bot sideways to a fixed spot, or teleport and FireTimer in one run().
- Director options as DirectorScript.MapScript.DirectorOptions: active ~1 s after map spawn, ~3 s before survivors
  and the first common (3/3 loads); nothing leaks into the next map.
- Build & Play reloads: the nav path ends in a fresh `map` load; the analysis path's reload keeps sv_cheats 1 /
  nav_edit 1 but populates exactly like a clean load. No population difference.

Findings:
1. MEDIUM: game-side files pile up forever: 48 hammerless_*.nut, 153 files in vscripts/hammerless (dead test maps),
   73 maps/*.nav01..10 backups, 200 maps/hl_* files, 1.1 GB of .bsp, console.log 39.6 MB. clear_nav() leaves the
   .navNN backups; clear_build() leaves scripts/.hlmode/navmark/owner; export never deletes scripts of removed
   crescendos, Director settings, debug, logic, mode or soundscapes. Fix: remove this map's known file patterns at
   export before writing; clear .navNN too; a "Remove map from game" operator; maybe truncate console.log at a
   fresh start past ~20 MB.
2. LOW, verified safe: a stale file can't change a newer build (every script name is gated or hashed); the one file
   the game loads by name alone is scripts/soundscapes_<map>.txt (stale after Sound OFF).
3. LOW: the flaky example-33 check (above).
4. LOW: bench leftovers in the Steam folder (Left 4 Dead 2/hammerless_bench_build, 14 MB): use %TEMP%.
5. INFO: addonlist.txt only gets "hammerless_mode.vpk" (added by the game); worth a docs line / uninstall operator.
6. INFO: the analysis path leaves sv_cheats 1 / nav_edit 1 across the game's own reload (cosmetic).
