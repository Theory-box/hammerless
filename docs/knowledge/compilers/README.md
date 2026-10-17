# Compilers: hlvbsp, hlvvis, hlvrad against Valve's vbsp, vvis, vrad

A Source map is built by three tools in a row. Valve ships them with the L4D2 Authoring Tools (`bin/vbsp.exe`, `bin/vvis.exe` + `vvis_dll.dll`, `bin/vrad.exe` + `vrad_dll.dll`). Hammerless has its own three, written in C, each a drop-in for Valve's (same arguments, same log lines, same output files), and each the default since 2026-10-10.

| Stage | Valve's tool | Ours | Source | Built to | Default since |
|---|---|---|---|---|---|
| Map compile: .vmf to .bsp (BSP tree, faces, collision, portals, props, displacements, overlays, water, cubemaps) | vbsp | hlvbsp | `native/hlvbsp/*.c` | `hammerless/core/hlvbsp.exe` (32-bit) | 2026-10-08 (commit 7a1f7f9) |
| Visibility: which clusters see which (PVS/PAS) | vvis | hlvvis | `native/hlvvis.c` | `hammerless/core/hlvvis.exe` | 2026-10-10 (commit 95d3125) |
| Lighting: lightmaps, world lights, leaf ambient, prop and detail-prop light | vrad | hlvrad | `native/hlvrad/*.c` (+ `shaders/`) | `hammerless/core/hlvrad.exe` (64-bit) + `hlphys.exe` (32-bit helper for prop collision) | 2026-10-10 (commit 95d3125) |

All three are built with `python native/build.py <tool>` (zig cc). The build uses `-ffp-contract=off` so the C compiler can't fuse float operations: byte-identity to Valve's output depends on repeating Valve's arithmetic in Valve's order.

Why each exists, in one line each:

- **hlvbsp**: to own the whole pipeline (no feature of the map should need Valve's tool), to make smart builds exact (`-onlyents`), and as the base for speed work later. It is not yet faster than vbsp.
- **hlvvis**: speed. Vis is the critical path of a full build. Exact mode is about 3x faster than vvis; merged windows is many times faster again (c1m1_hotel 18.4 s exact to 2.3 s merged, 2026-10-11); since 2026-10-16 the default runs that flow on the graphics card (the five-house map 290 s on the CPU, 109 s Tight / 46 s Fast on an RTX 4090; [../vis/gpu-vis.md](../vis/gpu-vis.md)).
- **hlvrad**: speed and features vrad can't do: GPU ray tracing, sky light from the skybox or an HDRI, ray-traced bounce, partial bakes with kept light, per-light layers for live mixing and remixes.

The user's standing rule (2026-10-10): **any time a Valve tool runs instead of ours is a bug to fix**, not an accepted outcome. Valve's tools remain installed as a safety net only. The full list of cases is in [valve-fallbacks.md](valve-fallbacks.md).

## Pages in this folder

| Page | Contents |
|---|---|
| [valve-options.md](valve-options.md) | Every command-line option of vbsp, vvis and vrad, what it does in Valve's tool, how many values it takes, and what ours does with it |
| [valve-fallbacks.md](valve-fallbacks.md) | Every remaining case where Valve's tool runs, the decision taken on 2026-10-13 for each, and its status |
| [what-ours-do.md](what-ours-do.md) | What each of ours does byte-identically (with evidence), what it does differently or better, what isn't done, known issues |

Related system docs (user-facing, fact-checked): `docs/systems/MAP_COMPILER.md`, `docs/systems/VISIBILITY.md`, `docs/systems/LIGHTING.md`, `docs/systems/BUILD_AND_PLAY.md`.

## The tool flow during a build

A full build runs, in order: export (Blender scene to `<map>.vmf`) -> map compiler -> vis -> light -> copy to the game. The nav mesh and its analysis run in their own process alongside. `docs/systems/BUILD_AND_PLAY.md` has the user's view and the measured timeline; this section is the code's view.

### Where the steps are decided: `hammerless/core/compile.py`

`CompileOptions` (dataclass) holds everything a build is configured with: `vis` (SKIP / FAST / FULL), `rad` (SKIP / FAST / NORMAL / FINAL), `hdr` (always "HDR" since 2026-10-13), lighting settings, `extra_vbsp` / `extra_vvis` / `extra_vrad` (free text typed in the Settings panel), and which tool to use per stage: `map_tool`, `vis_tool`, `light_tool` (each "HAMMERLESS" or "VALVE"; `light_tool` also "CYCLES"), plus `vis_exact` and `light_exact`.

Three methods turn the options into Valve-style argument lists, which both Valve's tools and ours accept:

| Method | Produces |
|---|---|
| `CompileOptions.extra(tool, ours)` | The Extra Options typed for that tool: all of them for Valve's tool; for ours only the ones it does (`toolopts.sort_options(tool, text)[0]`). `extra_notes()` returns the build-log lines about the options left out (since 2026-10-13, commit 1d2d8fd). For Valve's tool the words go through `toolopts.valve_accepts`, which drops the options L4D2's binary refuses with their values (`VALVE_REFUSES`: vrad `-scale 1`, `-ambient 3`, `-sky 1`), so Valve's vrad run as a fallback doesn't exit on usage (c29bd02). The notes are kept apart from the fallback notes (`CompileJob._option_notes`) and put in the log as plain notes when that tool runs, not through `_fallback` (c29bd02: they were counted as fallbacks before). |
| `CompileOptions.vbsp_args()` | just `extra_vbsp.split()` (Valve's vbsp; `hlvbsp_command` builds ours and takes `extra("vbsp", True)`) |
| `CompileOptions.vvis_args(ours)` | `None` when vis is SKIP; `["-fast"]` for FAST; plus `extra("vvis", ours)` |
| `CompileOptions.vrad_args(ours)` | `None` when rad is SKIP; always `-hdr`; for FAST: `-fast` for Valve's vrad but `-noextra -bounce 2` for ours (commit 31a33e5; vrad's `-fast` is not ported); `-final` for FINAL; else `-extrasky N`, `-noextra`, `-bounce N`, `-chop N -maxchop N` when they differ from vrad's defaults; `-StaticPropLighting`, `-StaticPropPolys`; plus `extra("vrad", ours)` |
| `CompileOptions.hlvrad_args()` | Hammerless-only options appended after the above: `-gpu` (unless `light_exact`), `-sspoints`, `-sspasses`, `-ssthreshold`, `-fixquirks`, `-gi N -girays N` |

### Which tool runs: the `use_*` functions

| Function | Returns true when | Notes |
|---|---|---|
| `use_hlvbsp(opts)` | `map_tool == "HAMMERLESS"` and `hlvbsp.exe` exists | Extra vbsp options no longer matter here (before 1d2d8fd any extra option sent the compile to Valve's vbsp) |
| `use_hlvvis(opts)` | `vis_tool == "HAMMERLESS"` and `hlvvis.exe` exists | The old `HLVVIS_OPTIONS` / `_hlvvis_knows` check is gone (1d2d8fd) |
| `hlvrad_unsupported(opts)` | returns the list of reasons ours can't run: `hlvrad.exe` or `hlphys.exe` missing (nothing else since 31a33e5; "fast lighting" and "extra vrad options" were reasons until 2026-10-13) | `use_hlvrad(opts)` is `light_tool == "HAMMERLESS" and rad != "SKIP" and not hlvrad_unsupported(opts)` |

`hammerless/core/toolopts.py` (added 2026-10-13, commit f7c0d44; wired into the build in commit 1d2d8fd the same day) replaced the "any extra option means Valve's tool" rule. It holds one table per tool of every option Valve's binary knows, with how many values it takes and one of three kinds: `ours` (pass it to our compiler), `no effect` (leave it out; it doesn't change the map), `not done` (leave it out; it would change the map but ours doesn't do it yet). `sort_options(tool, text)` returns the options to keep and the build-log notes for the rest; `CompileOptions.extra()` / `extra_notes()` call it. The full option tables are in [valve-options.md](valve-options.md).

### `CompileJob`: the steps and the fallbacks

`CompileJob.__init__` builds `self.steps`, a list of `(name, command)` pairs, from the options:

1. `("vbsp", ...)`: `hlvbsp_command(tools, base)` when `use_hlvbsp`, else Valve's `vbsp`. When ours is used, Valve's command is kept in `self._valve_vbsp` as the fallback.
2. `("vvis", ...)` if `vvis_args()` is not None: `[hlvvis.exe] + (["-exact"] if vis_exact) + vvis_args + ["-game", gamedir, base]` when `use_hlvvis`, else Valve's vvis. Valve's kept in `self._valve_vvis`.
3. `("vrad", ...)` if `vrad_args()` is not None: `[hlvrad.exe] + vrad_args + hlvrad_args + ["-game", gamedir] + sky/nobake/keeplight/layers options + ["-modeldir", base + ".hlvrad_models", base]` when `use_hlvrad`, else Valve's vrad. Valve's kept in `self._valve_vrad`; the bake command and a `-remix` variant are kept in `self._hlvrad_bake` / `self._hlvrad_remix`.

`hlvbsp_command` passes the tables Python prepares from the game's VPKs (hlvbsp doesn't read VPKs): `-materials`, `-surfaceprops`, `-props`, `-detail`, `-cubemaps`, `-fgd` (`HLVBSP_TABLES`), written by `prepare_hlvbsp` (`core/mapcompiler.py`, `core/cubemappatch.py`). `prepare_hlvrad` copies the map's prop models out of the game into `<map>.hlvrad_models` (`core/radprep.py`).

`CompileJob._run_steps` runs them. It calls `_choose_steps()` first when `skip_if_unchanged` (Build / Build & Play), which is the smart build: `core/buildplan.plan()` compares the new .vmf with `<map>.built.vmf` and returns `same` / `entities` / `remix` / `lighting` / `full`. For anything but `full` the first step is replaced by `_onlyents_step()`: `hlvbsp -onlyents` (since 2026-10-13, commit 42f40b9), with Valve's `vbsp -onlyents` kept in `self._valve_onlyents` as the crash fallback only, and the vis and/or light steps appended as needed.

`CompileJob._fallback(msg, failure)` is the one place a switch to Valve's tool is recorded: the message goes to the log and to `self.fallbacks` (shown in the Problems list after the build); with `failure=True` the build is marked `_shaky`, which makes the next build compile everything instead of building on this result. Every call site is listed in [valve-fallbacks.md](valve-fallbacks.md).

### Lighting alongside vis

When ours lights on the GPU with ray-traced bounce (`-gi`) and the map has no radial vis, `_start_light_alongside` copies the compiled map before vis starts and runs hlvrad on the copy with `-visfrom <map>.bsp -visthreads N`; hlvrad waits for vis only for prop light and sky leaves. `_finish_light_alongside` swaps the lit copy in. Measured byte-identical to lighting after vis (`docs/systems/VISIBILITY.md`, "Lighting alongside vis"). `LIGHT_THREADS_DURING_VIS = 0` (one per core): fewer threads were measured slower (all cores 10.0 s, 8: 10.3 s, 4: 10.8 s, 2: 12.4 s on a real map).

### The build record

After a successful build the job writes `<map>.built.vmf`, `<map>.built.opts` (the `CompileOptions` repr, used by `_opts_rest` / `_opts_vis` / `_opts_rad` to decide what a later smart build may keep), `<map>.built.prt` (the portals, so a later vis upgrade needn't recompile) and `<map>.built.inst` (hash of instance files). A build that fell back after a failure writes none of these.

## Contradictions found

None open as of 2026-10-13 (commit 31a33e5). Resolved the same day: the `rad="FAST"` preset path that still sent lighting to Valve's vrad (31a33e5: ours gets `-noextra -bounce 2`); the system docs' "extra options run Valve's tool" sentences (updated with 1d2d8fd's rule).

Last checked: 2026-10-13 against branch light-layers, commit 10ea8d0 (0.17.0).
