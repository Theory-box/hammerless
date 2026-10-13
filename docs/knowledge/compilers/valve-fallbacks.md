# Where Valve's tools still run instead of ours

The rule (user, 2026-10-10): any time a Hammerless build hands work to Valve's vbsp, vvis or vrad is a bug to fix. Valve's tools stay installed only as a safety net. This page lists every such case as the code has it, the decision taken for each (as of 2026-10-13), and its status. It is updated as fixes land.

How the code records a fallback: `CompileJob._fallback(msg, failure)` in `hammerless/core/compile.py` writes `msg` to the build log and to `self.fallbacks` (shown as warnings in the Problems list and written to `<map>.build.log`); `failure=True` marks the build "shaky", so no `.built.*` record is written and the next build compiles everything from scratch instead of building on the result. Messages starting with `!!` are failures of our tool; the others are "doesn't do X yet" cases.

Status values: `done` / `partial` / `not done` / `known issue` / `removed` / `in progress`.

## Summary table

| # | Case | Trigger in the code | Valve's tool that runs | Decision (2026-10-13) | Status |
|---|---|---|---|---|---|
| a | Entity-only smart builds (`entities`, `remix`, `lighting` plans) | `CompileJob._choose_steps` -> `_onlyents_step()` | `vbsp -onlyents` | Replaced by `hlvbsp -onlyents`; byte-identical to `vbsp -onlyents` is the bar | `in progress`: code, tests and docs in the working tree (uncommitted on 2026-10-13); committed branch still runs Valve's |
| b | LDR lighting (`hdr` = LDR or BOTH) | was `hlvrad_unsupported`: `opts.hdr != "HDR"` | vrad | Removed: L4D2 uses only HDR; Valve's own maps have no LDR lighting | `removed` 2026-10-13 (commit dbf5f0f) |
| c | Fast lighting quality (`rad == "FAST"`, vrad's `-fast`) | `hlvrad_unsupported`: "fast lighting" | vrad | Don't port vrad's `-fast`; Fast quality is now Normal's lighting with cheaper settings through hlvrad | `done` on the Blender side 2026-10-13 (commit 6a17d74); the `rad="FAST"` code path in `compile.py` still exists and still falls back if reached |
| d | Extra vbsp / vvis / vrad options typed in Settings | `use_hlvbsp` (any extra vbsp option), `use_hlvvis` (any option not in `HLVVIS_OPTIONS`), `hlvrad_unsupported` (any extra vrad option) | vbsp / vvis / vrad | Ours takes the options it does; the rest are left out with a build-log note (`core/toolopts.py`); never switch to Valve's tool. `-textureshadows`, `-softsun`, `-sky`, `-ambient`, `-scale` to be implemented in hlvrad | `in progress`: `toolopts.py` written and unit-tested (commit f7c0d44), not yet wired into `compile.py` |
| e | Our tool exits with an error | the `code != 0` checks after `_exec` | the same stage's Valve tool, logged with `!!` | Kept as the safety net; every occurrence is a bug to fix | `done` (the net); each crash a `known issue` when it happens |
| f | Cycles light tool (`light_tool == "CYCLES"`) | `use_hlvrad` is false for CYCLES; the vrad step is Valve's | vrad (for the lightmap layout, world lights, leaf ambient, prop light), then Cycles bakes the lightmaps | By design: Cycles predates hlvrad and needs vrad's layout | `done` as designed; replacing vrad's layout with hlvrad's is possible but not planned |
| g | Our tool not installed | `use_hlvbsp` / `use_hlvvis` (`os.path.exists`), `hlvrad_unsupported` ("hlvrad.exe is missing", "hlphys.exe is missing") | the stage's Valve tool, with a note "isn't installed" | A packaging check, not a feature gap | `done` (release zips carry all four executables) |
| h | hlvbsp's tables can't be prepared, or a map feature is unsupported | `prepare_hlvbsp` raises, or `mapcompiler.unsupported()` returns reasons | vbsp ("Hammerless map compiler doesn't do X yet" / "couldn't read the game's files") | The unsupported list is empty (`UNSUPPORTED_CLASSES = {}` since instances and water overlays landed 2026-10-10); the read-failure branch is a safety net | `done` (feature side); the table-read failure path is a `known issue` only if it fires |
| i | hlvrad can't read the game's models | `prepare_hlvrad` raises | vrad ("couldn't read the game's models") | Safety net | `done` (net); a `known issue` if it fires |
| j | The user chooses Valve's tool in Settings | `map_tool` / `vis_tool` / `light_tool` == "VALVE" | the chosen tool | Allowed on purpose (comparison, debugging); not a fallback | n/a |

Cases a to f are the ones named in the 2026-10-13 decisions; g to j are the remaining code paths, listed so the table is complete.

## The cases in detail

### a. `vbsp -onlyents` in smart builds

**What happened.** The smart build (`core/buildplan.plan`) returns `entities` when only entity keyvalues, outputs or point entities changed, `remix` when only lights' colour or brightness changed (Live Light Mixing), and `lighting` when lights or static props changed but geometry didn't. All three start with an entities-only step that rewrites the entity lump into the existing .bsp. Until 2026-10-13 that step was always Valve's `vbsp -onlyents` (`[tools.exe("vbsp"), "-onlyents"] + vbsp_args + ["-game", gamedir, base]`), even with Hammerless's map compiler chosen, so every smart build ran a Valve tool. `docs/systems/BUILD_AND_PLAY.md` listed it as a known case.

**The replacement.** `hlvbsp -onlyents` (`native/hlvbsp/onlyents.c`, `OnlyEnts()`), reached through `CompileJob._onlyents_step()`, which returns `("vbsp (entities only)", hlvbsp_command(tools, base, onlyents=True))` when ours is in use and keeps Valve's `-onlyents` command in `self._valve_vbsp` as the crash fallback (case e). With Valve's map compiler chosen (`_valve_vbsp is None`), Valve's `-onlyents` runs as before (case j).

What `OnlyEnts` does, matching vbsp's `-onlyents` branch in `vbsp.cpp`: reads the compiled .bsp (version 21 only; refuses compressed lumps), reads the edited .vmf, and remakes only what depends on entities: the entity lump (brush entities' model numbers, named lights' styles, worldspawn `world_mins`/`world_maxs` measured with the 3D skybox included as vbsp's `ComputeBoundsNoSkybox` does without the tree, the `water_lod_control` vbsp adds, `func_occluder` numbers), the static props (`sprp` game lump, moved to the end of the game lumps as vbsp does), and the cubemap list. Every other lump is copied and written in vbsp's lump order. Like vbsp it leaves overlays, detail props and lighting as they were; writes an empty leaf ambient lump (one dark sample per leaf) into a never-lit map; rewrites the pakfile its own way (stored entries, no dates, vbsp's comment) and adds `stale.txt` (which Hammerless then strips with `buildplan.strip_stale`, as it did for vbsp's); deletes the old `.prt` and `.lin` (so `_run_steps` restores `<map>.built.prt` before a vis step, as before).

**The bar: byte-identical to `vbsp -onlyents`.** `tests/compile/onlyents_suite.py` compiles each map with Valve's vbsp (optionally lit with `vvis -fast` + vrad, `--lit`), then applies a series of entity edits as a mapper would (a light moved, a light named, a point entity added and one removed, a static prop moved and its model changed, an `env_cubemap` and an `info_overlay` added, a brush entity's key changed, a new named light), running Valve's `-onlyents` on one copy and ours on another after each edit and comparing the whole files (every lump, the pakfile, the header). Each step starts from the previous result with `stale.txt` stripped, so later steps read a pakfile Python wrote. Per the CHANGELOG entry in the working tree: identical on the generated test maps and Valve's sample maps, lit and unlit; the only differing bytes are the ones vbsp leaves uninitialised in static prop records (the same 3 padding bytes as in a full compile, masked by `bspdump.py`).

**Status 2026-10-13:** `in progress`. The C code, `compile.py` changes, the suite, unit tests (`tests/unit/test_core.py`) and doc updates are in the working tree but not committed at the time of writing. When committed: change this row to `done` and record the suite's numbers (maps, steps) here.

### b. LDR lighting

**What happened.** `CompileOptions.hdr` could be LDR, HDR or BOTH, from a Lighting setting. `hlvrad_unsupported` listed "LDR lighting" for anything but HDR, so choosing LDR made Valve's vrad light the map (hlvrad parses `-ldr` and `-both` but only writes HDR lumps).

**Why removed.** L4D2 only uses HDR lighting: Valve's own maps have no LDR lighting (measured when decoding what vrad writes, 2026-10-08: `vrad -hdr` changes lumps 30/31, 35, 51/55, 53, 54, 58 and leaves the LDR lumps 8, 15, 52/56 untouched; the game reads the HDR set). An LDR setting could only ever produce a map the game doesn't use, through a Valve tool.

**Done 2026-10-13** (commit dbf5f0f): the setting is gone from `blender/props.py` and `ui.py`, `vrad_args()` always emits `-hdr`, and `hlvrad_unsupported` no longer checks it. `toolopts.VRAD` classes `-ldr` and `-both` as `no effect`. Status: `removed`.

### c. Fast lighting quality

**What vrad's `-fast` is.** A separate rough preview path (`do_fast` in `vrad.cpp`, `lightmap.cpp`, `radial.cpp`): one sample per luxel (`BuildSamplesAndLuxels_DoFast`), sun-area rays divided by 4 and sky rays 162/4, no patch subdivision, no radial filter (luxels take their sample directly), and no static prop lighting even when asked. Details per option in [valve-options.md](valve-options.md).

**What happened.** The Fast quality preset set `rad="FAST"`, `vrad_args()` emitted `-fast`, and `hlvrad_unsupported` listed "fast lighting", so Valve's vrad ran. hlvrad has a `g_bFast` flag that only reduces ray counts (7 sun rays, 162/4 sky rays, no prop lighting), not the rest.

**Decision 2026-10-13: don't port `-fast`.** It is a different, rougher algorithm whose only purpose is a quick preview; Hammerless's own compiler on the GPU is already fast at Normal settings (1.0 to 1.2 s on a large user map), so Fast quality now means **Normal's lighting with cheaper settings**: no supersampling, 2 bounces, no prop lighting, through hlvrad (`blender/quality.py`, `"FAST": dict(_NORMAL, light_supersample=False, light_bounces=2, static_prop_lighting=False, light_prop_polys=False)`). The "Fast Lighting" checkbox (`light_fast`) was removed; `quality.py` never produces `rad="FAST"` any more (commit 6a17d74).

**Left over (2026-10-13):** `compile.py` still has `PRESETS["FAST"] = CompileOptions(vis="FAST", rad="FAST")`, the `-fast` emission in `vrad_args()`, and the "fast lighting" check in `hlvrad_unsupported`. Reachable only by code that uses the preset directly. Should be removed with the `-fast` handling, or `rad="FAST"` mapped to the cheap-Normal settings, so no path can send lighting to Valve's vrad. Status: `done` for users; `partial` in the code.

### d. Extra vbsp / vvis / vrad options

**What happened.** The three "Extra Options" fields in Settings pass free text to the tools. The old rule: `use_hlvbsp` is false if `extra_vbsp` has any word; `use_hlvvis` is false if any word of `extra_vvis` isn't in `HLVVIS_OPTIONS` (`-fast`, `-threads`, `-radius_override`, `-exact`); `hlvrad_unsupported` lists "extra vrad options" if `extra_vrad` has any word. So typing `-v` in the vrad field sent the whole lighting to Valve's vrad; `-threads 4` in the vbsp field sent the map compile to vbsp. `CompileJob.__init__` adds the notes "doesn't take extra vbsp options" / "doesn't know some extra vvis options" and `_run_steps` reports them through `_fallback` when the stage runs.

**Decision 2026-10-13.** Ours takes the options it does and the rest are left out with a build-log note; the build never switches to Valve's tool because of an option. `hammerless/core/toolopts.py` (commit f7c0d44) holds the complete option tables (every option L4D2's binaries know, with value counts) in three kinds: `ours` (passed on), `no effect` (left out: "they make no difference to the map"), `not done` (left out: "Hammerless's compiler doesn't do them yet"); `sort_options(tool, text)` also reports options with a missing value and words that aren't options the tool has. The tables, with what each option does in Valve's tool, are in [valve-options.md](valve-options.md).

Options to implement in hlvrad so they move from `not done` to `ours` (planned 2026-10-13): `-textureshadows` (alpha-tested shadows; today a hard error in hlvrad), `-softsun` (the command-line form; the `SunSpreadAngle` map key already works), `-sky`, `-ambient`, `-scale`. Note from reading the SDK source: `-sky` sets `indirect_sun`, which nothing in the SDK source reads; check L4D2's `vrad_dll.dll` before spending time on it.

**Status 2026-10-13:** `in progress`. `toolopts.py` and its unit test exist; `compile.py`'s `use_hlvbsp`, `use_hlvvis`, `hlvrad_unsupported`, `vbsp_args`/`vvis_args`/`vrad_args` and the two `_notes` messages still implement the old rule. `docs/systems/VISIBILITY.md` and `LIGHTING.md` still describe the old rule. When wired: update this row, the two system docs, and the `use_*` descriptions in `README.md` here.

### e. Crash fallbacks (`!!`)

Kept as the safety net. Each call site in `CompileJob._run_steps`:

| Stage | Condition | Message | Then |
|---|---|---|---|
| vbsp (full or `-onlyents`) | `code != 0 and cmd[0] == HLVBSP and self._valve_vbsp` | `!! Hammerless map compiler failed (exit code N): running Valve's vbsp instead` | Valve's command from `_valve_vbsp` (the `-onlyents` variant in a smart build). Safe because hlvbsp writes the .bsp only once it has finished, so the input map is untouched. |
| vvis | `code != 0 and cmd[0] == HLVVIS and self._valve_vvis` | `!! Hammerless vis failed (exit code N): running Valve's vvis instead` | Valve's vvis. hlvvis replaces the map only at the very end, so the map and `.prt` are untouched. |
| vrad (after vis) | `code != 0 and cmd[0] == HLVRAD and self._valve_vrad` | `!! Hammerless light compiler failed (exit code N): running Valve's vrad instead` | `_drop_layers()` (stale `.hllayers`), then Valve's vrad. hlvrad writes the map only when finished. |
| vrad (alongside vis) | same, in the `_finish_light_alongside` branch | same message | same |
| vrad remix | `code != 0 and "-remix" in cmd and self._hlvrad_bake` | `Live Light Mixing: the remix failed (exit code N): baking the lighting instead` | **Not a Valve fallback**: our bake runs; only if that fails too does the row above apply. |

All of them pass `failure=True`: the build is shaky, no `.built.*` record, next build compiles everything. Every occurrence is a bug: the fix is in the tool that exited non-zero, not in the fallback.

Known crash classes seen so far (fixed): hlvrad's old limit of 262,144 bounce patches made big maps fall back silently (fixed 2026-10-09, commit f21a18f: a 2.6 million square foot map went from 27 s with Valve's vrad to 4.7 s); hlvvis refusing radial-vis maps (exit 3) before radial vis was done (fixed 2026-10-10, commit 1b1f066). The Oct 2026 audits (`docs/dev/audits/2026-10-11-audit2-build-pipeline.md`) added the `.build.log` record so a fallback never goes unnoticed.

### f. The Cycles light tool

With `light_tool == "CYCLES"`, `use_hlvrad` is false and the vrad step is Valve's vrad. vrad lays out every face's lightmap, writes the world lights, the leaf ambient samples, detail prop and static prop light; then a background Blender bakes the lightmaps with Cycles and `core/lightbake.py` writes them back in vrad's layout. This predates hlvrad (branch `cycles-bake`) and is kept as an option by design; it is not counted as a fallback. hlvrad could provide the same layout, but no one has asked for it. Status: as designed.

### g to j

See the summary table. None of these is a feature gap: g is packaging, h and i are "couldn't read the game's files" nets (with `mapcompiler.UNSUPPORTED_CLASSES` empty since 2026-10-10 there is no map feature that sends a map to vbsp), j is the user's explicit choice in *Settings > Map & Model Compilers*, *Visibility*, *Lighting > Advanced*.

## What a smart build does after a fallback

`_choose_steps` returns the full step list ("full") when the last build has no `.built.*` record, which is the case after any `failure=True` fallback (`_shaky`). So a fallback never becomes the base of a later entities-only or lighting-only build: the next build recompiles everything with ours and leaves a clean record.

## Checklist when a fallback shows up

1. Read `<map>.build.log` (commands, fallbacks, timings) and the Problems list: which message, which stage.
2. If it is a `!!` crash: reproduce with the same command line from the log on the build folder's files; fix the tool; add the map to the matching suite (`mapcompiler_suite.py`, `compare_vis.py` / `vis_merged_suite.py`, `compare_vrad.py`, `onlyents_suite.py`).
3. If it is a "doesn't do X yet" message: it is one of the rows above; update the row's status when fixed.
4. Update this page in the same commit.

Last checked: 2026-10-13 against branch light-layers (commit 6a17d74 plus the uncommitted hlvbsp -onlyents work in the tree).
