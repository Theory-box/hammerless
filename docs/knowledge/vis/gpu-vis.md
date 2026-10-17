# Vis on the graphics card: the round-based flow (`-jacobi`), the GPU port, the two passes, Fast and Tight

Status: `done`, the default since 2026-10-16 (set with e784cf2, pulled with 666ced5 the same day, restored with 10ce1bf after the user's in-game test, Fast/Tight added with 19c70d6). Code: `native/hlvvis.c` (sections "round-based merged flow (-jacobi)" and the `-gpu` pipeline glue; `g_auto`, `g_two`, `g_onepass`, `g_jacobi`, `jacobi_flow`), `native/hlvvis_gpu.c`, `native/hlvvis_shaders/` (`basevis.comp`, `expand.comp`, `pipe_*.comp`, `pipe_pt.comp`, `merge_core.glsl`), `native/build.py` (`float_controls()`). Settings: Visibility panel, *Vis on the GPU* (`vis_gpu`, default on) and *Vis: Fast / Tight* under it (`vis_culling`, default Fast); `CompileJob` passes `-onepass` for Fast, nothing for Tight, `-cpu` when the GPU is off, `-exact` for Match vvis Exactly (`hammerless/core/compile.py`).

Everything on this page was done on 2026-10-16 (the commit messages are the record; the day's commits run from 109207e to 19c70d6 on branch light-layers). The CPU merged-window method it builds on is in [merged-windows.md](merged-windows.md).

## The maps the numbers refer to

- **1-house**: the user's house as Vis Only blockers (the collision pieces of one house; see [README.md](README.md), "Vis Only brushes"): 392 to 396 clusters visible on average, exact 386.
- **5-house**: the user's map with five such houses; 290 s with the CPU merged windows (measured 2026-10-16; on 2026-10-15 only hlvvis's progress estimate, "about 635 s left", had been seen for it).
- **The user's map copy, 8,984 clusters and 31,654 portals** (the Fast/Tight measurement). Whether it is the 5-house map is *inferred*, not recorded: the commits name it only "the user's map".
- Valve's c1m1, c2m1, c3m1, c5m1 and the test suite for safety.

## Step 0: the CPU reference, `-jacobi` (109207e)

The merged-window flow (one state per portal per base, re-expanded when it grows) runs through a work queue whose order depends on thread timing. A GPU needs rounds instead: `-jacobi` is the **round-based** merged flow, the CPU reference the GPU port had to match bit for bit:

| Change from the merged windows | Why |
|---|---|
| Rounds: every dirty portal of a base's flow is expanded once per round, results merged after the round (deferred, sorted merge) | the same work in the same order on any thread count: **deterministic** |
| No might-see sets in the flow | the GPU holds windows, not per-chain bitsets |
| Saturation instead of the growth thresholds (`g_grow_eps`) | a state is done when it stops changing |
| Separators must keep the **whole source** window on one side (`g_robust_sep`) | merged hulls' hair-length edges made planes that cut real sight lines |
| Windows capped at **16 corners** (over that, the 16-gon of supporting lines in fixed directions from a table: no sin/cos on the GPU); at most **64 windows** reaching one portal per round (more: it opens fully, a superset) (875e4ff) | the GPU's fixed memory layout, put into the CPU reference first |

Validated: 5-house 290 s -> 170 s (172 s with the caps), 0 real misses (every flagged sight line judged by portal semantics: `vis_judge.py`, `vis_chain_walk.py`, `vis_rays` with `VIS_RAYS_THICK`); 1-house against `-exact`: 0 of 80 pairs it hides are visible, 62 proven hidden; Valve's c1m1/c2m1/c3m1/c5m1 as safe as the default. The default (merged windows) was unchanged, its single-thread output byte-identical.

## The GPU port, step by step

Vulkan compute, any Vulkan 1.2 card (no ray tracing needed). The rule throughout: the CPU's float operations in the CPU's order, fused multiply-adds forbidden, float controls pinned, so the GPU's bits are the CPU's.

| Step | Commit | What runs on the GPU | Checked |
|---|---|---|---|
| 1. The rough pass's plane tests (`basevis.comp`) | 79e6ac1 | `BasePortalVis`'s front tests | byte-identical output on 1-house and 5-house; 5-house plane tests 0.5 s on an RTX 4090 including start-up |
| Self-test (`-gpuselftest`) | 9140264 | double `a / b`, `sqrt`, `(float)(1 / sqrt(len))` against the CPU | RTX 4090: 0 of 4,000,000 differ, so the clipping runs in doubles on the GPU as the CPU reference does |
| 2. The expansion steps (`expand.comp`: chop, separators with the corner check, clipping; float division through double; the chop limit from the CPU) | 32f7a8e | one dispatch per round per base | 1-house 68 M steps, 0 differ (`HL_GPU_CHECK=1`); 5-house 2.08 billion steps, final vis byte-identical; the merge library against 600,000 recorded CPU merges (caps and opened portals included): 0 differ (`HL_MERGE_TEST=n` with `-threads 1`) |
| 3. The whole flow, many bases at once (`pipe_*.comp`) | ec8faf3 | one slot per base portal; each round per live slot: compact (dirty -> frontier in portal order), scans, snap (expansion count / open fully, snapshot), expand (results linked per slot and portal), merge in item order (the CPU's order; over 64 arrivals opens fully, counted across passes). The CPU does each base's first openings when it fills a slot | byte-identical to CPU `-jacobi` on 1-house (2, 64, 216 slots) and 5-house; 1-house flow 2.5 s (CPU 5.8 s), 5-house 82 s with only 49 slots |
| Flows sized by their own flood | 285314a | each flow takes a power-of-two block of entries from a pool (8.4 M entries; window buffers split so none passes 2 GB), up to 4,096 flows at once (5-house: about 370 at once instead of 49); `scan1` in parallel | byte-identical (1-house with 2 and 4,096 slots, 5-house); 1-house flow 1.3 s; 5-house flow 40.8 s, 46 s in all (CPU `-jacobi` 170 s, merged windows 290 s): merge 27.9 s, expand 10.1 s, rest 1.5 s |
| Fable's review fixes | aca6e58 | float controls really pinned (see lessons); a round's expansions in passes of at most 4 M items (Windows resets a GPU job over 2 s), a round over 2^32 items detected (the CPU does it); device limits checked; a failure on the way keeps the bases it finished (`HL_GPU_FAIL_AT=n`); refills at most 1,024 per round | byte-identical on 1-house (default, 20,000-item passes, failing at round 60) and 5-house (41.0 s flow, 46.5 s in all) |

`merge_core.glsl` replaced `merge_lib.glsl` in step 3: the same arithmetic, but windows in private arrays and the hull at one call site. GLSL functions with array parameters are inlined with their own copies: the merge needed 37 KB of scratch per invocation and merely creating that program lost the device on later dispatches; 7 KB after.

## The second pass (39a022c) and the two-pass default

Pass 1 is the GPU `-jacobi` flow. **Pass 2** runs `-jacobi` again with the merged flow's might-see sets pruned by pass 1's result: over clusters in each base's own first-pass cluster ranks, a step x -> q needs q's cluster in x's set, each window brings `SM(x) & PT(q)` where PT is q's first-pass vis (built by `pipe_pt.comp`); sets sized per base from a word pool. The "more" rule was dropped (it changed no result, only work). Byte-identical to the CPU's two-pass reference (`HL_TWO=2 HL_JM_CL=1 HL_JM_NOMORE=1`) on 1-house and 5-house.

| Map | Exact | Merged windows (CPU) | GPU pass 1 only | GPU two passes |
|---|---|---|---|---|
| 1-house, avg clusters visible / time | 386 | 396 in 4.0-4.1 s | | 392 in 2.9-3.0 s |
| 5-house | | 1,503 in 290 s | 1,650 in 46 s | 1,507 in 109 s (95 s later in the day) |

Safety of the two passes (233e9da; `HL_DUMP_PVIS` dumps every portal's vis row, `HL_V1_FILE` feeds a second pass tests from a file): with vvis's exact rows as pass-1 input, pass 2 hides the same zero-width pairs on c3m1 (sight lines 0.0000 units inside a portal), so those come from the pruning itself, not from pass 1, and the judge rejects all of them as proof; c5m1 0 of 111 hidden pairs seen with 20 M thick rays each; 5-house 0 of 30,089 pairs the default shows, 200k thick rays each; Valve maps and the test suite 0 real misses.

**The default's history on 2026-10-16:**

| Commit | Default | Why |
|---|---|---|
| e784cf2 | the two GPU passes; `-cpu` (or no usable card, fog-radius maps, `-fast`) = the merged windows on the CPU; a GPU failure falls back to them | 1-house 392 in 3.0 s (old 396 in 4.0 s); 5-house 1,507 in 109 s (old 1,503 in 290 s) |
| 3afc2bb | also on fog-radius (radial) maps: the radius is only in the rough pass (on the CPU there), the flows read its floods | byte-identical to the CPU reference with `-radius_override 2000` on c3m1; 120 avg visible (old 121); judge: 0 real misses |
| 08becaf | the two passes as one progress bar (first about 38%, then the rest; time left counts the second pass) | |
| 666ced5 | **back to the merged windows on the CPU**; `-gpu2` asks for the GPU passes | the second pass failed on the user's map, and hordes behaved differently in the user's game test. Also: the merge lists sorted by length (byte-identical) |
| e4d0f03 | GPU passes sized by time (about 250 ms each, so the second pass's costlier items stay far under Windows' 2 s GPU reset); the second pass's set buffers 1 GB each, inside 5/8 of the card (room for Blender's); a second-pass GPU failure redoes the vis with the merged windows on the CPU, with progress (`HL_GPU_FAIL_PASS2` tests it) | 1-house byte-identical; both fallbacks give the CPU default's 396 |
| 10ce1bf | **the two GPU passes again**; `-cpu` for the merged windows; any GPU failure falls back to them | the user tested in game: hordes and nav fine; 138 s vis with lighting alongside against about 290 s |
| 19c70d6 | **Fast** (`-onepass`: pass 1 only) the default; **Tight** = both passes, byte-identical to the two-pass default before it | below |

The hordes that "behaved differently" in 666ced5 were not pinned to a vis difference; the restored default passed the user's test. Recorded as it happened, not as a measurement.

## Fast and Tight (19c70d6)

`-onepass` keeps the default's GPU setup and fallbacks and skips the second pass. Measured on the user's map copy (8,984 clusters, 31,654 portals):

| | Tight (two passes) | Fast (one pass) |
|---|---|---|
| vis | 68 s | 30 s |
| whole compile without lighting | 79 s | 41 s |
| clusters drawn on average | 1,531 | 1,675 (+9%) |
| volume-weighted share of the map seen | 73.3% | 74.3% |
| pairs Tight shows that Fast hides | | **0 of 12.6 M** (`merge_safe.py`, a development script: Fast is a strict superset) |

A Tight build isn't skipped as up to date over a Fast one: `_serves` in `compile.py` refuses a built record with `vis_tight=False` when Tight is asked for. The options in `toolopts.py`: `-gpu2`, `-cpu`, `-onepass` (ours). hlvvis's usage line: `[-fast] [-exact | -cpu | -onepass | -jacobi [-gpu]]`.

## Fallbacks (all to our own code, never to vvis)

| Case | What happens |
|---|---|
| No Vulkan, no usable card, the card lacks RTE / signed-zero-inf-NaN preserve for 32- and 64-bit floats, the doubles self-test fails, a device limit (storage buffers, workgroup size or count, shared memory, push constants) | the merged windows on the CPU, printed "(no GPU flow: the merged windows on the CPU)" |
| A GPU failure during the two-pass flow | everything redone with the merged windows on the CPU (vis rows cleared first: an earlier version ORed into rows the GPU had written, 285314a) |
| A GPU failure in explicit `-jacobi -gpu` | the bases it finished are kept, the CPU does the rest |
| `-fast` | the rough pass only, on the CPU as before |

## Development environment variables

`HL_GPU_CHECK=1` (each expansion step checked on the CPU), `HL_GPU_STEPS` (the old per-round expansion path), `HL_MERGE_TEST=n` / `HL_MERGE_TEST_EVERY`, `HL_GPU_SLOTS`, `HL_PIPE_DEBUG`, `HL_PIPE_ROUNDS`, `HL_GPU_STATS` (the driver's registers / memory per program), `HL_GPU_PROFILE=1` (GPU time per kernel), `HL_GPU_ROBUST`, `HL_GPU_FAIL_AT=n`, `HL_GPU_FAIL_PASS2`, `HL_TWO` (1/2/3/4: the second-pass variants), `HL_JM_CL`, `HL_JM_NOMORE`, `HL_JM_NOFILTER`, `HL_JMIGHT`, `HL_JACOBI_X`, `HL_UNBUF`, `HL_ROUND_STATS`, `HL_DUMP_PVIS`, `HL_V1_FILE`, `HL_DEBUG_PAIR "A B"` (only the flows between two clusters), `HL_REPLAY` (exact chain replay against stored windows).

## Lessons (so they aren't paid for twice)

- glslang keeps only the **last** `spirv_execution_mode` per mode and declares no capability: each float-control mode must be written for 32- and 64-bit floats with its capability (`build.py` `float_controls()`), or the controls are not really pinned (aca6e58).
- GLSL array parameters inline with their own arrays: 37 KB of scratch per invocation lost the device (ec8faf3; `HL_GPU_STATS` shows a program's registers and memory).
- Binding arrays must hold `PIPE_NBIND` (a crash at 28 bindings with room for 24).
- An empty environment variable reads as 0 through `atoi`.
- Windows resets a GPU job that runs over about 2 s: dispatches are sized by item count (4 M) and then by time (250 ms).

## Rejected on the way: cluster merging at weak portals (2026-10-16)

An hlvbsp-side idea (`-mergeclusters` / `HL_MERGE_CLUSTERS`, with a size cap): merge neighbouring clusters across "weak" portals so vis has fewer clusters. Rejected, code kept in a git stash on branch light-layers:

- merged clusters are non-convex, and the flow never revisits a cluster on a chain, so it hides real sight lines: **396 of 1,000** sampled hidden pairs were ray-visible (`merge_rays.py` with `tests/compile/vis_rays`);
- uncapped merging made one cluster with 5,144 portals (hlvvis estimated 229,000 s);
- capped, vis went only 68 -> 64 s (about 5%): vis time follows the **portal** count, not the cluster count.

## Reuse between compiles: the zoned-vis review (2026-10-16, Fable)

Not built. hlvbsp already cuts the map into 1024-unit XY blocks (`ProcessBlock` / `BlockTree`), but `MarkVisibleSides`, `FloodEntities` / `FillOutside` and the sequential cluster numbering are global, so a block's leaves and clusters are not independent of the rest, and resuming flows at zone edges can only be conservative. Recommendation: a **per-portal vis cache keyed by geometry hashes** instead of zones; step 1 is to measure how much of the portal graph changes per edit.

## What is left

| Item | Status |
|---|---|
| Speed ideas (Fable): a float pre-filter before the double outside test; a separator-plane cache per pair; list buckets | list buckets done (per the work notes of 2026-10-16); the other two not started |
| The two-pass quality against `-exact` on more maps than the four Valve maps and the two house maps | not measured |
| Why hordes behaved differently in the user's first two-pass test (666ced5) | not explained; the restored default passed the user's second test |
| Per-portal vis cache between compiles | not started (above) |

Last checked: 2026-10-16 against branch behaviours, commit 18c2ce4 (the code), with the measurements from the commits of 2026-10-16 on light-layers.
