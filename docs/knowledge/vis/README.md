# Visibility: hlvvis in depth

Compiler-level facts (byte-identity evidence, speed table, option handling) are in [../compilers/what-ours-do.md](../compilers/what-ours-do.md) and [../compilers/valve-options.md](../compilers/valve-options.md). This folder holds the method, the measurements and the proof work behind hlvvis. User-facing text: `docs/systems/VISIBILITY.md`.

| Page | Contents |
|---|---|
| this README | What vis is and writes, the exact-mode details that decide bits, vvis's nondeterminism, radial vis as decoded, speed history and the ideas tried, views and the Vis Cost file, limits |
| [merged-windows.md](merged-windows.md) | The merged-window method, why it is safe, the audit that found two bugs, the exact claim, the certifying tools and the release gate |

Code: `native/hlvvis.c` (one file; `hammerless/core/hlvvis.exe`), `hammerless/blender/visview.py` (views), `hammerless/core/bsppvs.py` (PVS reader used by the nav analysis), tests in `tests/compile/` (`compare_vis.py`, `vis_merged_suite.py`, `vis_check.py`, `vis_rays.c`, `vis_certify.c`, `vis_inflate_test.c`) and `tests/unit/test_core.py` (fixtures `tests/fixtures/vis/rooms_portal.zip`, `rooms_fog.zip`).

Timeline (2026): research and first bit-identical prototype 10-07; shipping tool and compare_vis 10-07 (`-threads 1` repeatable); final exact corpus 10-10 (commit 0ecc9dd); radial vis 10-10 (1b1f066); default compiler 10-10 (95d3125); merged windows default 10-11 (ac1ae02); audit fixes and the claim reworded 10-11 (2ae86d8, f5e96d1).

## What vis is, and what vvis writes

The map compiler splits open space into convex leaves and writes the openings between them (`<map>.prt`); neighbouring leaves form **clusters**. vis answers, per cluster pair, whether any straight line from one reaches the other through portals only: the **PVS** (one bit row per cluster). The **PAS** is the union of the PVS rows of the clusters a cluster sees. Both are run-length compressed (Quake's scheme, rows `(n+7)>>3` bytes) into lump 4 as header, all PVS rows, all PAS rows.

Other lumps vvis touches (measured 2026-10-07 by diffing before/after; hlvvis writes them the same way):

| Lump | What vvis writes |
|---|---|
| 46 leaf min distance to water | 65535 for every leaf when the map has no water; computed otherwise (`WaterDist.cpp`); `TESTFOGVOLUME` leaf contents with water |
| 51/52 leaf ambient index, 55/56 leaf ambient lighting | placeholders: `{1 sample, first = leaf index}` and one all-zero 28-byte sample per leaf, only if all four lumps are empty |
| 35 game lump | only its absolute file offsets, shifted when lumps before it change size |
| 10 leaf flags | every leaf's `area_flags |= 0x400` (dleaf byte 7 |= 4) on radial maps |

File layout per L4D2's writer: lumps keep their input order (sorted by input offset; empty lumps before the non-empty one at the same offset; 56, 52, 51, 55 tie order), each lump and the file end 4-byte aligned, never-written lumps keep offset 0. Note on the unit fixture: vbsp packs a default cubemap copied from the game into the pakfile and vvis re-serialises the pakfile, so a hand-made empty zip doesn't round-trip; the fixture uses vvis's own 54-byte empty pak.

## The exact search (id's portal flow) and what decides the bits

The method is the portal-flow visibility id Software published with Quake's vis (GPL): a rough pass (`BasePortalVis`: every portal at least partly in front, then a flood: the might-see bound) and the flow (`PortalFlow`: every chain of portals from each base portal, source and pass windows cut by separating planes; a dead window ends the chain). hlvvis is our code; the Source SDK 2013 `vvis.cpp`/`flow.cpp` were read for behaviour only (licence). Everything below was found by comparing with vvis's output on a large user map (330,735 PVS cells) until 0 differed (2026-10-07):

- `ON_VIS_EPSILON` 0.01, compared in **double** (`mathlib.h`); coordinates are floats (`vec_t`).
- `MAX_POINTS_ON_FIXED_WINDING` 12: a chop that would need more points returns its input unchanged (this is why merged windows can be *tighter* than vvis in places).
- The source-side check uses the **base** portal's radius.
- Plane = `(p0 - p1) x (p2 - p1)` normalised; the forward portal's plane negated; one-way portals numbered 2i (leaves the portal line's first cluster) and 2i+1.
- Portals processed in order of might-see count (`SortPortals`); a later portal uses a **done** portal's final `portalvis` as its filter, else that portal's rough flood. This shortcut is part of the result: with it off ("nodone") the output is very different.
- `ClusterMerge`, then `CompressAndCrosscheckClusterVis` = AND-symmetrise (the PVS is fully symmetric); PAS = OR of the PVS rows of visible clusters.
- Exploration order *inside* a portal is result-neutral (verified both orders identical single-threaded).
- The rough pass alone contains 100% of vvis's PVS (0.5 s single-threaded); its bound was 552,884 pairs against vvis's final 164,772 on the large user map: 70% of the bound is truly hidden.

Speed-ups that change no decision (each verified bit-identical at the portal level against a single-thread reference): 64-bit bitsets limited to their non-empty word range; 12-point stack windings; separator planes built once per step (cached when the source is unclipped) and four at a time with SSE2 using the same float operations in the same order; SIMD chop (four points at a time); no descent into steps that can't reveal anything new; parallel rough pass. Float-vs-double epsilon equivalence for SIMD: `d > 0.01` (double) is `d >= first float above 0.01`; `len < 0.01` is `len <= last float below 0.01`. The build uses `-ffp-contract=off`.

Progression on the large user map, 16 threads (2026-10-07): baseline 11.4 s -> bitsets + stack windings 8.5 s -> cached separators 7.4 s -> SIMD separators 5.3 s -> SIMD chop 5.0 s -> (narrowest-first 4.35 s -> work sharing 4.2 s, both later removed) -> shipping exact mode 5.2 s. Profile at the time: separator build 34%, chop 28%, bitset loop 14%, overhead 23%; 80% of built planes used (lazy build would save about 7%); chop outcomes 75% unchanged, 6% removed, 19% split. Tried with no gain: lazy plane building, bounding-sphere skip of no-op chops (-9% chops, 0 time), AVX2 compile flags alone.

**Two speed-ups removed on purpose** (cost about 15%): exploring each cluster's portals narrowest-might-see-first and work sharing (idle threads taking branches of running portals; utilisation 85% -> 97%). Neither changes a portal's own result, but both change *when* portals finish, and with threads that decides borderline pairs differently from vvis's usual timing: the heavy no-detail map came out 26 cells off vvis (16 extra, 10 missing) instead of 0 to 4 with vvis-like scheduling.

## vvis is not deterministic

Measured 2026-10-07 on the large user map: two vvis runs differed by 4 PVS bits of about 330,000 (3 rows); 8 runs gave 6 identical results and only 4 cells ever varied; the PAS was identical. Cause: whether a portal meets a finished neighbour (tighter filter) or one still working (rough bound) depends on thread timing. hlvvis's threaded runs vary 0 to 8 cells for the same reason; `-threads 1` is deterministic. So "identical to vvis" means byte-identical to one of vvis's runs, or differing only in cells vvis itself varies. `tests/compile/compare_vis.py` encodes exactly that: several runs of each, full and `-fast`, a match being byte-identity with any vvis run, otherwise the count of cells vvis never produced. vvis's own profile: 99% in the flow, 0.1 s rough pass, 14 to 15 of 16 cores busy for three quarters of the run then a 3 s one-core tail (load imbalance).

## Radial (fog-distance) vis, decoded from `vvis_dll.dll`

Status: `done`, byte-identical (2026-10-10, commit 1b1f066), full and `-fast`. Before it, hlvvis refused such maps (exit 3) and Valve's slow vvis ran: the trigger for the "fallbacks are bugs" rule.

- Radius squared = `farz * farz` in double (the max over every `env_fog_controller` with a far Z), or `-radius_override R` (`atof`, prints "Vis Radius = %4.2f", skips the fog check). Log line: "max farz ... used for radial vis"; farz -1 means off.
- Every leaf gets `area_flags |= 0x400`.
- Only `BasePortalVis` changes: after the two front tests, `md` = min over the target portal's winding points of `(dx² + dy²) + dz²` to the source portal's origin (double, starting at 1024000000.0); if `md > r2`, `d` = a sampled fan distance (the target's winding fan i = 2..n-1 against the source's fan j = 2..n+1 wrapping, triangles sampled at barycentric 0.25 steps, SSE floats); cull if `min(md, d) > r2`. A bounding-sphere gap shortcut skips the sampling safely.
- Valve's `-fast` on radial maps is about 10x slower than ours (styleguide_swamp 23 s vs 2 s).

## Speeds

16-core PC, 16 threads unless noted.

| Map | vvis | hlvvis exact | hlvvis merged | Date |
|---|---|---|---|---|
| Large user map (1191 clusters, 3466 portals) | 14.1 s (15.2 s first measured) | 5.2 s | | 2026-10-10 |
| Same, Auto Detail off ("no-detail", heavy) | 118 s (112 s earlier) | 38 s (35 s) | | 2026-10-10 |
| styleguide_urban (radial; 1262 clusters, 4129 portals) | 2 min 19 s | 46 s (630 CPU-s) | 4.8 s | 2026-10-10/11 |
| c1m1_hotel (1813 clusters), 6 threads | | 18.4 s | 2.3 s | 2026-10-11 |
| c5m1_waterfront (1782 clusters), 6 threads / 16 | | 32.8 s / 18 s | 5.0 s / 3 s | 2026-10-11 |
| styleguide_swamp radial `-fast` / full | 23 s / 475 s | 2 s / 120 s | | 2026-10-10 |
| VVIS++ (third party, closed) on the large user map | 3.3 s | | | 2026-10-07 |

VVIS++ (ficool2) claims the same results as vvis; measured: hides 82 pairs vvis shows (rays proved none of them visible), shows 236 extra, PAS differs, rewrites lumps 0 and 40, nondeterministic too. Not L4D2-exact; it was the target to beat in exact mode (not beaten: 5.2 s) and is beaten by merged windows.

## Ideas tried and dropped (so they aren't retried)

| Idea | Result | Date |
|---|---|---|
| Ray-proven visible pairs as a head start for the early-out | only ~3% fewer chains: the early-out needs *all* remaining might-see bits proven, and 70% are truly hidden; CPU rays through the BSP (leaf cluster -1 = blocking): 0 false proofs, 13.6 M rays in 3.5 s prove 86% of visible pairs, but rays can't prove hidden | 2026-10-07 |
| Dominance pruning (skip a step into portal X when an earlier step into X in the same flow had source, pass and might-see supersets) | urban 47 -> 25 s (534 M -> 219 M steps) but hid one cluster pair vs vvis, also without the 12-point quirk: float epsilons aren't monotonic (tiny windows skip planes). Not exact | 2026-10-10 |
| Uniform point sampling of portals with 3D-dilated portals (conservative) | urban would need 14 M samples at eps 8; a prototype ran over 10 minutes and was killed | 2026-10-10 |
| Adaptive portal sampling (split patches where dilated/undilated disagree) | under 500 of 6932 base portals in 5 minutes: dilated walks barely narrow | 2026-10-10 |
| Published faster methods: VVIS OptiX GPU path (~1% misses), Trim Regions 2023 (1.3-5% false negatives), Disocclusion Buffer 2025 (no guarantees), NeuralPVS, COS 2019 (conservative but view cells <= 30 cm) | all allow misses or are slower; a miss shows as a missing wall in game | 2026-10-07/10 |
| Exact from-region methods (Teller, Nirenstein, Bittner), Plücker, GPU core | slower than vvis's search; GPU kept only as an idea for a validation ray oracle | 2026-10-10 |
| Merged-window fixpoint per base flow (Fable consult, 2026-10-10) | **adopted**: see merged-windows.md | 2026-10-11 |

## Vis Only brushes and the cost of a house as a vis blocker (2026-10-15)

A brush with Detail *Vis Only* (`func_hl_visblock`; exported by `core/build.py`, handled by hlvbsp) shapes the tree, leaves, portals and vis exactly as a World brush would, but collides with nothing and uses none of the 8,192 brushes (compiler details in [../compilers/what-ours-do.md](../compilers/what-ours-do.md); the reason it exists, Make Collision's props, in [../models/collision.md](../models/collision.md)). For vis it is simply more structural geometry, and a house's collision pieces are a lot of it:

| Measurement | Value | How |
|---|---|---|
| One house, collision as props with *Blocks Vis* (its ~1,000 pieces as Vis Only brushes) | map compile 2.8 s, hlvvis 4.7 s, 4,242 leaves, 2,073 clusters | build log, 2026-10-15 |
| The same map without the vis blockers | 29 leaves, 4 clusters, vis 0.03 s | build log, 2026-10-15 |
| The house as World collision brushes, earlier in the day | about 20 s of vis | user report (tool and settings not recorded) |
| Five houses | hlvvis's progress line read "vis 6%, about 635 s left" | user screenshot, 2026-10-15; the estimate, not a finished run |
| The five-house map in game | "extremely smooth, everything worked" | user test, 2026-10-15 |

So Blocks Vis on a whole house trades vis time for the game drawing less; the per-wall cost grows with the number of walls that become portals. Not yet measured: the frame-rate gain in game from the blockers against the same map without them, and whether only the outer walls as Vis Only (the interior pieces as plain collision) gives most of the gain for a fraction of the leaves.

## Views and the Vis Cost file

`blender/visview.py` draws over the viewport from the last build; nothing is added to the scene. **Portals**: the openings (slivers under 16 square units red: usually two brushes that almost line up). **Rendering Load**: faces coloured by how many faces the game draws from there, a button puts the 3D cursor on the heaviest spot (works whichever compiler built the map). **Vis Cost**: portals coloured by the time hlvvis spent on them, mapped back to the Blender objects whose brushes those portals were split along, with their share and a Select button; needs hlvvis (it writes `<map>.viscost`). hlvvis also prints one progress line per percent, weighting each portal by an estimated cost, which Blender shows as "Vis 42%, about 3 s left".

Detail facts measured: in a test room, 36 overlapping boxes made `func_detail` cut the map's vis pieces from 190 to 48; the Auto Detail 256-unit size limit cut vvis time by about 25% on a real map while the game culled the same (24% vs 23% drawn).

## Limits and known issues

| Item | Status |
|---|---|
| No reuse between compiles (every build runs vis from scratch) | not done; incremental reuse by a changed-brush hull test was Fable's suggestion; no prior art found |
| Exact mode with threads varies like vvis | by design; `-threads 1` repeatable |
| Merged windows about 1% looser; may hide an opening thinner than 0.01 units | by design; see merged-windows.md |
| Switching vis compiler doesn't trigger a rebuild (same map) | by design |
| Per-thread memory NPD x PW x 8 bytes (~50 MB/thread on a 20k-portal map), never freed | fine (audit 3) |
| hlvvis options: `-fast`, `-exact`, `-threads`, `-radius_override`, `-game`, `-v` | done |
| A house's every collision piece as a Vis Only blocker: 4,242 leaves / 2,073 clusters / 4.7 s for one, ~635 s estimated for five | known (2026-10-15); outer-walls-only not tried |

Last checked: 2026-10-15 against branch light-layers, commit a2d00a3 (the Vis Only section; the rest re-read 2026-10-13 against 41dc861).
