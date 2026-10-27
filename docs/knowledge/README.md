# Hammerless knowledge base

This folder is the living record of what Hammerless knows about Left 4 Dead 2, its tools, and its own reimplementations of them: what each system does, what works, what doesn't and exactly where and why, what was measured (with numbers, maps and dates), and what was reverse-engineered and how. It is written for people and for AI assistants alike, so that a question can be answered by reading here instead of being researched again.

It differs from the other docs in the repo:

| Folder | Purpose |
|---|---|
| `docs/*.md`, `docs/systems/` | User and system documentation: how to use a feature, how a system works, fact-checked against the code. |
| `docs/dev/` | Design notes, audits and research write-ups from particular dates. |
| `docs/knowledge/` (this folder) | The accumulated facts: option by option, lump by lump, test by test, with dates and sources. Kept current as fixes land. |

## Organisation

One folder per area. Each folder has a `README.md` giving the overview and an index of its pages.

| Folder | Area | Status |
|---|---|---|
| `compilers/` | The map, vis and light compilers (hlvbsp, hlvvis, hlvrad) against Valve's vbsp, vvis, vrad: options, fallbacks, what ours do | written |
| `lighting/` | hlvrad beyond vrad: GPU path and ray-traced bounce, light styles and Live Light Mixing, partial bakes and keep light, sky light, Cycles, the lighting view | written |
| `vis/` | hlvvis in depth: what decides vvis's bits, nondeterminism, radial vis decoded, speed history and dropped ideas, merged windows with its safety proof and tools, vis on the graphics card (the round-based flow, the two passes, Fast / Tight) | written |
| `nav/` | The .nav format decoded, the generator port and its L4D2 constants, the analysis matched to the game, speed work, the in-game flow check, the real-time nav plan | written |
| `director/` | AI Director: the flow, wanderer placement read from `server.dll` and fitted in game, the special/mob filter, the budget limit, the spawn preview check, options/scopes/hooks, item densities, quirks | written |
| `logic/` | Graph -> VScript compiler and the facts it is built on, the script API dump method, scripted mode and hooks, the NetProps catalogue method and what sticks, the test bench, examples and what each measured, enemies on bot bodies | written |
| `models/` | The own model writer matched to studiomdl, animated models through SMD/QC and what studiomdl was measured to do, the game's `.mdl`/`.ani` format decoded (frame-anims), game skeletons and placed characters, VTF/VMT, alpha, materials, parallax, collision from meshes (Convert to Brushes: Extend-or-Fill, Organic, Slabs; invisible materials, Vis Only brushes) | written |
| [particles/](particles/README.md) | Independent particle preview, selection playback, atlas/material rules, control points, ribbon fixes, performance evidence and limits | written |
| `sound/` | What a map can control of the sound engine (measured), new sounds through an addon VPK with its own `sound.cache` (format decoded), DSP presets, the automatic soundscapes, the shelved impulse-response work | written |
| `loading/` | Build & Play step by step, the game prestart, the parallel build and its critical path, smart builds and `hlvbsp -onlyents`, the kept nav, Fast Map Loading (`sv_lan 1`), the stringtable dictionary, every load timing | written |
| `game-facts/` | The BSP as L4D2 writes it (lump order, game lump, pakfile, `stale.txt`), the game's physics DLL from our tools, reading the game's content, Valve's maps decompiled, the VMF importer's exact round trip (and its shape-after-modifiers rule), engine behaviours met in testing, limits hit, reference maps, testing rules; weather (precipitation as Valve uses it, measured in game) | written |
| `editing/` | The brush tools: the CSG core (`native/hlcsg.c`: plane sets in double, the compiler's snapping, the coplanar and degenerate-face tolerances and the bugs behind them, two cut orders, merge), Carve / Intersect / Clip / Hollow / Merge, Draw Brush / Draw Carve, what the 2,500-case stress test proves, what is untested | written |

## Conventions

Every page follows these rules so the facts stay traceable and checkable:

1. **Each fact says how it is known.** One of: "measured" (a test, with the map, the tool and the number), "read from source" (Valve's Source SDK 2013 / Alien Swarm source, with the file and function), "decoded from the binary" (which DLL or EXE, which function), "inferred / unverified" (an educated guess, marked as such). Never state an inference as a measurement.
2. **Each measurement carries a date and a map.** Write "2026-10-11, c1m1_hotel, 6 threads: 2.3 s". Users' personal maps are called "a large user map" (the same map throughout, about 1191 clusters and 3466 portals, 2.6 million square feet in its bigger version); never their file names.
3. **Each feature or option has a status** from this fixed set: `done` (works, verified), `partial` (works with listed gaps), `not done`, `known issue` (a defect with its symptom), `removed` (was there, taken out, with the date and reason), `in progress` (being worked on at the page's "Last checked" date).
4. **"Last checked" lines.** Every page ends with `Last checked: <date> against <commit or branch>`. When a page's facts are re-verified, the line is updated even if nothing else changed. A page whose date is far behind the code should be read with care.
5. **Link to code by path**, for example `native/hlvrad/direct.c`, `hammerless/core/compile.py` (`CompileJob._choose_steps`). Function names in backticks. Line numbers are avoided (they rot); function names are preferred.
6. **Cite Valve's sources as** "Valve's Source SDK 2013 / Alien Swarm source" with the file (`vrad.cpp`, `lightmap.cpp`). No local scratch or session paths in these pages.
7. **Tables for facts, prose for reasoning.** No marketing language. Numbers must be traceable to a test tool in `tests/` or to a named measurement.
8. **Stale statements are fixed or removed, not left.** When a fix lands, the page that mentioned the gap is updated in the same change set (or the next one), and the "Status" cell is changed. Removed features keep a one-line entry marked `removed` so the history isn't lost.
9. **Contradictions are listed, not silently resolved.** If the code and a doc disagree, the page says so under a "Contradictions found" heading until one of them is fixed.

## Keeping it current

- Code changes that touch a compiler, a fallback, an option table, a measured number or a game fact should update the matching page here. The commit that lands the fix is the right place.
- Each release: re-read the `Status` columns of `compilers/valve-fallbacks.md` and the "Known issues" sections, and bump "Last checked" on the pages that were verified.
- New measurements go in the page's table with the date; old numbers stay if they're still meaningful (before/after pairs), otherwise they move to a "History" note.
- When a new area is written, add it to the index above and change its status from "to write".

## Index of pages

### compilers/

| Page | What it covers |
|---|---|
| [compilers/README.md](compilers/README.md) | Overview of hlvbsp / hlvvis / hlvrad versus Valve's vbsp / vvis / vrad, the tool flow during a build, where each tool is invoked in the code |
| [compilers/valve-options.md](compilers/valve-options.md) | Every command-line option of Valve's vbsp, vvis and vrad: what it does (from Valve's source), how many values it takes, what Hammerless's compiler does with it; the L4D2-only options |
| [compilers/valve-fallbacks.md](compilers/valve-fallbacks.md) | Every case where Valve's tools still run instead of ours, with the decision and plan for each, and a Status column kept current |
| [compilers/what-ours-do.md](compilers/what-ours-do.md) | For each of our three compilers: what is byte-identical to Valve's (with the evidence), what differs or is better, what isn't done, known issues |

### lighting/

| Page | What it covers |
|---|---|
| [lighting/README.md](lighting/README.md) | What a light compiler writes (lumps), quality presets and what they set, settings-to-options map, every measured number in one table |
| [lighting/gpu-and-bounce.md](lighting/gpu-and-bounce.md) | The GPU path (Vulkan ray queries, record/replay rounds, vrad's walk in a shader), ray-traced bounce `-gi`, bounce links on the GPU, lighting alongside vis |
| [lighting/layers-and-styles.md](lighting/layers-and-styles.md) | Light styles and switchable/flickering lights, a light's Pattern as a preset Behaviour (one switched style per preset, measured in game), Live Light Mixing (`-layers`, the `.hllayers` file), the remix (`-remix`), accuracy and cost |
| [lighting/bakes-sky-cycles-view.md](lighting/bakes-sky-cycles-view.md) | Partial bakes (`-nobake`), keep light (`-keeplight`), sky light from the skybox/HDRI (`-skymap`), the Cycles bake and its units, the Baked Lighting view |

### vis/

| Page | What it covers |
|---|---|
| [vis/README.md](vis/README.md) | What vvis writes, the exact search's bit-deciding details, vvis nondeterminism, radial vis as decoded from `vvis_dll.dll`, speeds, ideas tried and dropped, views, limits |
| [vis/merged-windows.md](vis/merged-windows.md) | The merged-window method, the corrected safety argument, the audit's two bugs, the exact 0.01-unit claim, the proof tools and release gate |
| [vis/gpu-vis.md](vis/gpu-vis.md) | Vis on the graphics card (the default since 2026-10-16): the round-based `-jacobi` reference, the GPU port step by step with its bit-identity checks, the second pass, the day's default history, Fast / Tight with the measured difference, fallbacks, development variables and lessons, the rejected cluster merging, the zoned-vis review |

### nav/

| Page | What it covers |
|---|---|
| [nav/README.md](nav/README.md) | The .nav v16/14 format as decoded, the generator port (constants, measured L4D2 differences, native code, marks, extras), how close it is |
| [nav/analysis.md](nav/analysis.md) | The game's nav_analyze reproduced: sight-blocking rules, matching method and results, speed work, the in-game flow check, test tips |
| [nav/realtime-plan.md](nav/realtime-plan.md) | Real-time incremental nav (not built): the bar, what exists, the order-dependence analysis, the chunked design |

### director/

| Page | What it covers |
|---|---|
| [director/README.md](director/README.md) | The flow rules, nav blockers, wanderer placement as read from `server.dll` and fitted in game, the "few zombies" investigation and the budget limit, the special/mob filter (EMPTY is commons-only), the spawn preview check, Director option scopes and hooks, item densities and Valve's measured choices, quirks and test method |

### logic/

| Page | What it covers |
|---|---|
| [logic/README.md](logic/README.md) | The compiler and the measured game facts it depends on (entity order, root table, Gate timing, precision), how the script API, Director options and hooks were dumped, scripted mode and the mode add-on, the NetProps catalogue method (demo SendTables layout, harvest) and what sticks, the test bench, the examples and what each measured, Behaviours (logic an object carries: Self, Light Pattern presets sharing one style, Entity Events on Self, the Swelling Pod as a Behaviour, brains for world graphs, the in-game test), custom enemies on bot bodies |

### models/

| Page | What it covers |
|---|---|
| [models/README.md](models/README.md) | The own `.mdl`/`.vvd`/`.vtx`/`.phy` writer and its verification against studiomdl, the SMD/QC path and studiomdl's measured behaviour (the 90-degree turn, events, hitboxes, limits), the game's model files decoded (frame-anims, skeletons, libraries), game skeletons and placed characters proven in game, the pose cache and the 63-character name limit, textures, alpha, material resolution, the loose-material fix, parallax |
| [models/collision.md](models/collision.md) | Collision from meshes: Convert to Brushes and its Algorithm dropdown (Auto, Extend-or-Fill, Organic, Slabs), the slab method stage by stage, General's (Organic's) cut-and-check method (exact within-Fit certify, worst-air search, judging in the workers, CutScorer and its compiled C version with an exact hull (0.20.6), prescoring and the next piece scored ahead, pyramid triangle queries, peels, halving, the Max Sides cap and the chest-cube fix, Time Limit, no scipy) with its benchmark, the statue timings, the Match Seams research (shelved), the speed ideas measured and not shipped (stand-ins, exact pruning, hull prefilters, GPU booleans) and the ideas dropped on the way, Brushes or Props (<= 20 pieces) with the measured frame rates behind that number, the ideas tried and dropped for houses (voxels, a space BSP, fill-the-unreachable, one big prop), invisible model materials, Vis Only brushes (`func_hl_visblock`) and their test, why Source worlds are brushes |

### sound/

| Page | What it covers |
|---|---|
| [sound/README.md](sound/README.md) | The automatic soundscapes and their trace, per-map `level_sounds`, per-player DSP through `point_clientcommand`, mix layers, the retail DSP cvars and the measured distance dulling, why new sounds failed and the addon `sound.cache` that fixes it (format), addon `dsp_presets.txt`, the shelved gunfire acoustics v1/v2, limits |

### loading/

| Page | What it covers |
|---|---|
| [loading/README.md](loading/README.md) | The nine steps and where each runs, boot and load timings (`BinkOpen(`, the 6 s Steam wait), the stringtable dictionary findings and how it is carried over, the parallel build's lessons (contention, the GIL), smart build plans and when they fall back, the kept nav (also after Bake Lighting / Compute Visibility), what a failed or cancelled build leaves in the game, the build caches, audit items, the two-way link and cancel, limits |

### game-facts/

| Page | What it covers |
|---|---|
| [game-facts/README.md](game-facts/README.md) | BSP version 21 facts, `vphysics.dll` calling conventions and vtable slots, VPK and binary sources, the decompiler's findings and round-trip results, the Hammer-map importer, a table of engine behaviours with the area each belongs to, the limits met, the reference maps, the testing rules that cost data |
| [game-facts/weather.md](game-facts/weather.md) | L4D2's precipitation as Hard Rain uses it (one map-sized `func_precipitation`, a handful of blockers, `env_wind` values), why buildings keep rain out by themselves, the in-game rain tests, the Weather Volume's keys |

### editing/

| Page | What it covers |
|---|---|
| [editing/README.md](editing/README.md) | The brush tools of 0.18.0: the CSG core (`native/hlcsg.c`) fact by fact with its tolerances (`c_snap`, `c_same`, `CSG_MIN_AREA` 1e-6 and the 0.16-unit bug, two cut orders, merge's checks), the random stress test and what it proves, Carve / Intersect / Clip / Hollow / Merge and their Blender tests, Draw Brush / Draw Carve step by step, what has not been tested (the modal mouse interaction by events; the user's hands-on test pending) |

### Known issues

| Page | What it covers |
|---|---|
| [known-issues.md](known-issues.md) | Open behaviour problems from the October 2026 audits, re-checked in the code: what happens, where, the suggested fix; and what's been fixed since |

Last checked: 2026-10-17 against branch brush-tools, commit 55c63a0 (release 0.18.0 is being made from it: the behaviours slice 2, brains, the brush tools and the importer's modifiers fix).

Particle section added 2026-10-27 for release 0.20.20; other sections retain their individual verification dates.
