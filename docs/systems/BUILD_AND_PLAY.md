# Build & Play and Map Loading

**Build & Play** is the one button that turns a Blender scene into a running Left 4 Dead 2 game on your map. This page explains what happens after you press it, what runs in parallel, how smart builds decide what to skip, and what was done to make the game load the map faster.

The short version: the game starts booting the moment you press the button. The map is exported, compiled, lit and given a nav mesh, with several of those steps running side by side. Only what changed is redone. The new map is copied into the game with a few things carried over from the last build, and the running game is told to load it. On a large user map, a fresh Build & Play from the button to standing in the map went from 35.3 s to 26.6 s.

## The steps

| # | Step | What it does | Runs |
|---|---|---|---|
| 1 | Game prestart | Starts Left 4 Dead 2 to its main menu | At once, in the background |
| 2 | Export | Blender scene to a `.vmf` in the build folder | In Blender |
| 3 | Map compiler | `.vmf` to `.bsp`: brushes, displacements, props, overlays | Own process |
| 4 | Vis | Works out what can be seen from where | Own process, all cores |
| 5 | Light | Bakes the lighting | Alongside vis, when it can |
| 6 | Nav mesh | Generates the nav mesh | Own process, during the compile |
| 7 | Nav analysis | Visibility and hiding spots for the nav | Own process, once vis is final |
| 8 | Copy to game | `.bsp` and `.nav` into the game's `maps` folder | After the compile |
| 9 | Launch | Sends the map to the running game, waits for survivors | Game |

**Build** does steps 2 to 8 without starting the game. **Play** does step 9 on the last build.

### 1. Game prestart

If the game isn't running and Steam is ready (running, with someone signed in), Build & Play starts the game straight away, so it boots while the map exports and compiles. The game reaches its main menu in about 4.5 s. Hammerless watches the game's `console.log` for the menu's background movie starting (`BinkOpen(`); from then on, commands sent to the game are taken in about 0.1 s.

When the build is done, the map is sent to that game once it reaches its menu. If you close the game while it's still booting, Hammerless starts it afresh with the map instead. It waits up to 90 s for the menu, then sends the map anyway.

If Steam isn't running (or is still signing in), there is no prestart. The launch at the end starts Steam and waits up to two minutes for it to be ready, because a game started before that quits with "Steam is not running".

Measured from the button to the map loaded, with a 4-second build: **18.1 s before prestart, 8.1 s after** (prestart together with Fast Map Loading, below).

### 2. Export

The scene is written to `<map>.vmf` in the build folder (`hammerless_build` next to your saved `.blend`). Problems found here go to the Problems list. If the game or the L4D2 Authoring Tools aren't found, the build stops here with a message saying where to set or get them; the `.vmf` is still written.

### 3. Map compiler

Hammerless's own map compiler (`hlvbsp`) turns the `.vmf` into a `.bsp`. It first prepares tables from the game's files in Python; when that's done, the build signals that other Python work (the nav mesh) can start (see "Parallel build").

### 4. Vis

Hammerless's vis compiler works out which parts of the map can see which. It uses all cores and is the critical path of a full build: everything else is arranged around it.

### 5. Light alongside vis

With the Hammerless light compiler on the GPU and ray-traced bounce, lighting doesn't wait for vis. Before vis starts, the compiled map is copied to `<map>.light.bsp`, and the light compiler runs on that copy with `-visfrom <map>.bsp`. The faces' direct and bounced light don't need vis (vis only spares rays a light can't reach); the light compiler waits for vis only for what does need it (props' light, which leaves see the sky). When both are done the lit copy replaces the map. The result is byte-identical to lighting after vis.

It does not run alongside vis when:

- vrad's patch bounce is used (it needs vis), or Valve's vrad is the light compiler;
- the map has a fog far Z or a vis radius override (radial vis changes which leaves see the sky, so lighting runs after vis).

### 6. Nav mesh during the compile

With *Nav Mesh: Made in Blender*, the nav generator (a port of the game's own `nav_generate`) runs in its own process while the map compiles. Without it, the game makes the nav itself: load the map, generate, then two reloads. If generation fails in Blender, the build says so and the game makes it instead.

### 7. Nav analysis

The analysis (visibility between nav areas, hiding spots) needs the compiled walls and the final visibility, not the lighting. As soon as the nav mesh exists and the compile is past its last vis step, the build copies the map to `<map>.analysis.bsp` and starts the analysis on that copy while the light compiler may still be running. If the analysis fails, the game analyzes the nav instead (one reload).

### 8. Copy to game

The `.bsp` is copied into `left4dead2/maps`. If the game has that map loaded, Windows refuses the copy; Hammerless sends `disconnect` and tries again. Before the copy, the stringtable dictionary is taken from the old map (see below). The nav mesh is written next to it.

### 9. Launch

The map is loaded in the running game (via `-hijack`) or in a fresh one. Before `map`, the game is sent `sv_cheats 0` (which resets any cheat settings left from testing), `sv_lan` per Fast Map Loading, and the difficulty if one is set. For a lit map, `mat_fullbright 0` goes first: the engine turns fullbright on after loading an unlit map and leaves it on for the session.

A fresh game ignores the game mode on its command line, so a map in another mode is loaded first and then switched into its mode from the console.

## Parallel build and the timeline

Each step records when it started and ended. The build log ends with a chart of them on a shared time axis (`core/timeline.py`; the steps are collected in `blender/ops.py`, `_timeline`). Build & Play adds the game load once survivors are in the map. It looks like this (layout only, not a real measurement):

```
Build timeline (T s from pressing Build; each mark is t s):
  export                   0.0 -    ...  ###
  map compiler             ...  -   ...     ######
  nav mesh                 ...  -   ...       ######
  vis                      ...  -   ...           ###########
  light (alongside vis)    ...  -   ...           ##########
  nav analysis             ...  -   ...                      #####
  game load (to survivors) ...  -   ...                           ###########
  waited on, in order: export -> map compiler -> vis -> ... -> game load (to survivors)
```

The last line is the critical path. It starts from the step that ended last and walks back through the step that ended just before each one started (what it waited on). Shortening any step not on that line doesn't make the build finish sooner.

What was learned making the build parallel:

- **Contention is the real limit.** Total CPU work is fixed. Giving the light compiler fewer threads during vis made the build slower, not faster. Vis (about 6.5 s on all cores on a large user map) is the critical path.
- **Python steps in Blender share one lock.** The map compiler's preparation went from 0.3 s to 3.8 s when it ran next to nav generation in the same process. Nav generation now waits for that preparation, and the nav mesh and its analysis now run in their own process.

On a large user map (Final quality, full vis, from nothing to survivors in game): **29.0 s before these changes, 26.6 s after** (35.3 s that morning, before the stringtable dictionary fix too). What was left at the time: about 0.9 s idle before vis, about a 4 s tail (prop lighting against nav analysis), and the game load, 9.6 s (2.8 s to the map starting, then about 6.8 s inside the game until survivors appear).

## Smart builds

Every build compares the new `.vmf` with the one the current `.bsp` was compiled from (`<map>.built.vmf`) and picks the least work that gives the same map as a full compile (`core/buildplan.py`). Anything it can't classify counts as geometry, so a doubt always means a full compile.

| Plan | When | What runs |
|---|---|---|
| **same** | Nothing that reaches the map changed, same options, and the game has this build's map | Nothing: "Map unchanged, skipped compiling" |
| **entities** | Only entity keyvalues, outputs or point entities changed | The map compiler's `-onlyents` rewrites the entity lump; geometry, vis and lighting are kept |
| **remix** | Only the colour or brightness of lights baked into layers of their own changed (Live Light Mixing) | `-onlyents`, then the light compiler mixes the layers at their new weights (`-remix`): no bake |
| **lighting** | Lights or static props changed, geometry didn't | `-onlyents`, then the light compiler |
| **full** | Geometry changed | Map compiler, vis, light |

What counts as geometry: the world's brushes, brush entities' brushes, origin and class, displacements, overlays, cubemaps, detail props, area portals, ladders, instances, the fog's far Z (it makes vis radial), and an area portal window's target brush. Lights and static props count as lighting, along with a light's aim target and the brush entity keys the light compiler reads (`_minlight`, shadow settings).

A smart build also falls back to a full compile when:

- an instance file changed;
- a new or moved entity sits where the compiled map is solid (the map compiler fills rooms no entity reaches, so an entities-only build would keep that room filled). Logic entities at the map origin don't count;
- the last build was made by Valve's tool after one of ours failed (it left no record to build on);
- a remix can't be used (no layers, a different lighting setup, a light now has a colour channel it was baked without, or the vis is being upgraded at the same time): then it bakes.

A lower-quality vis is upgraded without recompiling geometry: after **Bake Lighting** (fast vis), the next build adds only the full vis, and leaves the lighting alone unless lights changed or the vis is radial.

`-onlyents` is Hammerless's own (`hlvbsp -onlyents`), and writes the same file as Valve's `vbsp -onlyents`; Valve's runs only if ours fails, or when Valve's map compiler is chosen. Like Valve's, it leaves a `stale.txt` in the map, which makes the engine treat the map as not final. A smart build's map is equivalent to a full compile, so Hammerless removes it.

### The nav mesh is kept when its inputs are unchanged

The nav is made from the map without its logic entities, outputs and names, plus the nav settings, the vis settings (the analysis reads the vis), the instance files, the map's own models and the nav code itself. That is hashed into a key and stored next to the map's scripts in the game (`scripts/vscripts/hammerless/navinputs_<map>.txt`). If the key is the same, the nav on disk was made in Blender, is analyzed, and no safe-room or spawn marks changed, the nav is kept: only its header is updated to the new map's size (the game checks it). The build log says "nav mesh kept (nothing it's made from changed)". In practice, adding a logic node or changing a delay no longer rebuilds and re-analyzes the nav.

### Why did a build redo work?

Each build writes `<map>.lastbuild.log` in the build folder: the compile messages, the smart build's reason ("Smart build: only entities changed: keeping geometry and the baked lighting", "geometry changed"...), each step's timing, and whether the nav was kept. Read it when a build took longer than expected.

## Fast Map Loading

*Settings > Game Window > Fast Map Loading*, on by default, starts test games as LAN-only (`sv_lan 1`).

Measured: a map load into a running game took about 11.5 s, of which about 6 s was a silent wait right after "VAC secure mode disabled", on stock maps too. That is the listen server registering with Steam's servers. With `sv_lan 1` the load took 5.1 s.

The cost: friends can't join your game online while it's on. Untick it for that.

## The stringtable dictionary

L4D2 compresses its networked string tables (precached models, sounds, scenes) with a dictionary stored in the map's pakfile, `stringtable_dictionary.dct`: plain strings, each ending in a zero byte, about 36,000 of them, mostly the game's own sounds and scenes. A freshly compiled map has none, so the game builds one while it loads ("Creating dictionary") and writes it back into `maps/<map>.bsp` when the map unloads.

That cost about 10 s of every load after a fresh build. Measured on a real map: map start 9.1 s before the fix, 2.6 s after; Build & Play 35 s to 30 s.

What was found about the dictionary:

| Dictionary | Result |
|---|---|
| None | Built during the load (about 10 s) |
| Near-complete, even from an older build (strings added and missing) | Loads as fast as an exact one |
| About half the strings | As slow as none |
| Nearly empty | The load fails with "Overflow error writing string table baseline Scenes" |

How it is carried into each build (`core/stringtables.py`):

1. Before the new map replaces the game's copy, the dictionary the game saved into that copy is read.
2. If it has at least 10,000 strings, it is used, and also saved as the last good one (`scripts/vscripts/hammerless/stringtable_dictionary.dct` in the game folder). Otherwise that saved one is used: a new map gets the dictionary from the last Hammerless map the game saved.
3. The map's own file names in it (`maps/<old>.bsp`, `.nav`) are renamed to the new map, and it is written into the new map's pakfile as a stored (uncompressed) entry.

The very first build on a computer still pays the 10 s once. A failure here never fails the build: the game just makes its own.

## The two-way game link

Hammerless sends commands into the game through `-hijack` and reads back through the game's `console.log`. Each built map carries a small script that prints two lines:

- `HAMMERLESS_READY survivors spawned`, once a survivor exists. Build & Play times the load to this line and adds it to the timeline. It also gates game-side nav steps: `nav_generate` grows the mesh from where players stand and fails if run before they spawn.
- `HAMMERLESS_FLOW`, the flow check. The AI Director computes the path from the start to the end safe room (the "flow") at map load; without it no zombies wander. The script asks the game for the flow distance and prints `HAMMERLESS_FLOW ok <length>`. If there is none, it walks the nav mesh from the start safe room and prints `HAMMERLESS_FLOW broken at x y z`, the reached spot closest to the end room: where the path breaks (`broken connected` when the end areas were reached but the Director still found no flow). `nonav` and `noend` cover a map with no nav or no end room.

Blender picks the report up and shows it ("In game: path from start to end works (2380 units)" or "no path from start to end") in the Problems list, then reads the nav the game saved for the viewport. Every map load sends one report.

The flow is only computed when the end room's changelevel names a different, real map; the build supplies one if your map doesn't.

## Valve's tools are never a silent fallback

Hammerless's map, vis and light compilers are the defaults. Valve's `vbsp`, `vvis` and `vrad` stay as a safety net: if one of ours fails or can't do something yet, Valve's runs instead. When that happens, the build says so in the log and lists it as a warning in Problems. After a fallback caused by a failure, the next build compiles everything again rather than building on that result. Any fallback is treated as a bug to fix, not an accepted outcome. Valve's tools still run in a few known cases, each noted in the log and Problems: Fast lighting, and extra options only Valve's vrad or vvis understand.

## Measured timings

All from the add-on's own notes and changelog; "a large user map" is the same real map throughout.

| What | Before | After | Change |
|---|---|---|---|
| Button to map loaded (small map, 4 s build) | 18.1 s | 8.1 s | Game prestart + Fast Map Loading |
| Map load into a running game | 11.5 s | 5.1 s | `sv_lan 1` |
| Map start after a fresh compile | 9.1 s | 2.6 s | Stringtable dictionary kept |
| Build & Play, large user map | 35 s | 30 s | Stringtable dictionary kept |
| Build & Play, large user map, Final, from nothing | 29.0 s | 26.6 s | Light alongside vis, steps no longer slowing each other |

Step times on the large user map after the dictionary fix, before the parallel build: export 2.8 s, map compiler 3.9 s, vis 5.9 s (full), light 5.9 s, game 9.0 s (2.6 s to map start, about 6 s to survivors).

## Limits

- **Prestart needs Steam ready.** If Steam is closed or signing in, the game starts only after the build.
- **Fast Map Loading is LAN-only.** Friends can't join online while it's on.
- **The game load is the largest single step left.** About 6 to 7 s of it, between the map starting and survivors appearing, is inside the game and hasn't been profiled.
- **The first build on a computer** has no dictionary to carry over and pays about 10 s at load once.
- **Light alongside vis** needs the Hammerless light compiler on the GPU with ray-traced bounce, and no radial vis.
- **Smart builds compare against the last successful build.** A cancelled build, a failed one, or one rescued by Valve's tool leaves no record, so the next build compiles everything.
- **A running game may not change map.** If it doesn't, quit L4D2 and press Build & Play again: an unchanged map isn't recompiled (see [Troubleshooting](../TROUBLESHOOTING.md)).
