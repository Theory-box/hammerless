# What our compilers do: identical, different, not yet, known issues

For each of hlvbsp, hlvvis and hlvrad: what it does exactly like Valve's tool (with the evidence for every byte-identical claim), what it does differently or better, what it doesn't do yet, and the known issues. Facts come from the system docs (`docs/systems/MAP_COMPILER.md`, `VISIBILITY.md`, `LIGHTING.md`, `BUILD_AND_PLAY.md`), `CHANGELOG.md`, the git log, the source comments, and the research notes kept during the work (Oct 2026). "A large user map" is the same real map throughout (about 1191 clusters, 3466 portals; its bigger version 2.6 million square feet).

## How byte-identity was reached (all three)

The method was the same for each tool and is worth recording because it is what makes the claims checkable:

1. Read Valve's Source SDK 2013 / Alien Swarm source for behaviour only (the SDK licence isn't GPL-compatible, so the code is ours; hlvvis follows id Software's GPL Quake vis method).
2. Build stage by stage, comparing each stage's output lumps against Valve's tool with a dump/diff tool (`tests/compile/bspdump.py --diff`, one record per line, every lump named, masking the bytes Valve's tools leave uninitialised).
3. Where L4D2's binary differs from the SDK source, disassemble the binary (`vbsp.exe`, `vvis_dll.dll`, `vrad_dll.dll`, 32-bit SSE builds) with capstone and read the one function responsible. Lesson recorded 2026-10-08: disassemble early instead of guessing float-order variants.
4. For vrad, a launcher ran a copy of `vrad_dll.dll` in-process with hooks that dump its internal state (patches, transfers, ray results, leaf ambient rays) for direct comparison, and hardware watchpoints found who writes an uninitialised stack slot.
5. Match the arithmetic, not just the algorithm: vbsp and vrad compute in 32-bit floats, parts in the x87 unit at higher precision; sums grouped in a particular order; `-0` vs `+0`; old-MSVC text rounding (ties away from zero); Microsoft's quicksort order for equal keys; the random number streams.

Byte-identity in these tools means: the whole output file identical, except bytes Valve's tool itself leaves uninitialised or varies between its own runs (listed per tool below), which the comparison tools mask.

---

## hlvbsp (map compiler)

Source `native/hlvbsp/*.c` (main, map, brush, csg, portals, faces, detail, detail_props, disp, staticprop, overlay, water, cubemap, occluder, instance, onlyents, pak, phys, poly, write). Built 32-bit because, like vbsp, it loads the game's `bin/vphysics.dll` to build collision models; the 32-bit build's output is identical to a 64-bit build's. Default map compiler since 2026-10-08 (commit 7a1f7f9).

### Identical to vbsp

| Part | Evidence | Date |
|---|---|---|
| The whole compile: brushes/CSG, BSP tree, portal file (`.prt`), leak file (`.lin`), faces (merge, subdivide, weld, t-junctions), detail brushes, physics (brush models and displacements through `vphysics.dll`), area portals with clip outlines, displacements (neighbours, LOD verts, lightmap samples, physics), detail props, static props (`sprp` v9), overlays and water overlays, cubemaps (patched materials + placeholder), water (volumes, fog, per-depth materials, fluid physics, automatic `water_lod_control`), 3D skybox areas, `func_occluder`, `func_viscluster`, `info_no_dynamic_shadow`, blend (WVT) materials on brush faces, ladders (`func_simpleladder` brush models as L4D2 makes them), instances (nested too), the game's limits with vbsp's messages | `tests/compile/mapcompiler_suite.py`: 23 generated maps, one per feature: 23/23 identical to vbsp lump by lump (displacement physics lump skipped, see below), portal files identical | 2026-10-08 to 10-10 |
| Valve's `sdk_content` sample maps | `tests/compile/compare_vbsp.py --dir`: 132 of 134 byte-identical, 0 handed to Valve's vbsp (124 of 125 with 9 instance fallbacks before instances landed) | 2026-10-10 (commit ea41ab0) |
| Detail props (grass) | byte-identical on a 29,348-prop grass map (vbsp's and the game's random streams, Gaussian, Microsoft quicksort order reproduced) | 2026-10-08 (commit 8d4f339) |
| Demo level in game | `tests/ingame/test_mapcompiler.py`: same lumps as vbsp except displacement physics; plays | 2026-10-08 |
| `-onlyents` | `tests/compile/onlyents_suite.py`: whole files identical to `vbsp -onlyents` over a series of entity edits, about 1,340 comparisons: the 21 suite maps unlit and lit, Valve's sample maps, the instance folder including leaked builds, 32 point-entity instance files, lit `styleguide_swamp` and `tutorial03`, two real add-on builds. Only vbsp's uninitialised static-prop bytes differ (lighting origin with no `info_lighting`, 3 bytes after `disableX360`), zeroed by the suite. Unit tests `TestOnlyEnts`. In game: a light edit's smart build ran it in 0.2 s, no Valve tool | 2026-10-13 (commit 42f40b9) |
| Lump order on lit maps | the full compile's write order fixed where lit maps showed it (42 before 41 and 15; 58 after 7; ambient 56, 52, 51, 55) | 2026-10-13 (commit 42f40b9) |

Numeric details that had to be copied (each found by a diff, confirmed in the binary): vbsp's normalize is `sqrtf` then `* 1/(r + FLT_EPSILON)` in the SSE build, while the x87 `VectorNormalize` returns the length **unrounded** and some code divides by it (plane normals, windings, bevels, t-junction edge direction, face subdivision, displacement vectors); sums grouped `(y*y + z*z) + x*x` (regrouping matters, swapping a pair doesn't); snapped plane distance `(p0 + p1 + p2) * (1/3.0f)` in float; `-0` kept where vbsp does `VectorSubtract(vec3_origin, v)`; `%f` text rounds ties away from zero (old MSVC); `%g` needs a 3-digit exponent; `RoundInt = floorf(x + .5f)`; L4D2's world bounds include SKY faces (only NODRAW skipped); L4D2 `MASK_SOLID` = SOLID|WINDOW|MOVEABLE|MONSTER (no GRATE); `AngleMatrix` products grouped `(sr*sp)*cy`; `TransformAABB` extents in x87 double; instance merge per the SDK's `MergeInstance` with the FGD's key types (`-fgd` table: bases first, own after, a redefined class replaces in place; a missing instance file is fatal as in vbsp). L4D2-vs-SDK differences measured by disassembly: the Water shader is lit; materials with no base texture take `%tooltexture` size and reflectivity 0.2; `%compileNoShadows`, `%compileTeam`, `%hotSurface` (= NOPORTAL 0x20); `ChopBrushes` also fragments when the union of contents has SOLID|WATER; no detail props under water (WATER|SLIME leaves when the map has water) or inside `func_detail_blocker`.

Bytes vbsp itself doesn't determine (masked by `bspdump.py`): padding and unused fields left uninitialised (dispinfo padding and invalid sub-neighbour bytes, 3 bytes per static prop record, the lighting origin when there's no `info_lighting`), the 4th word of IVP points in the physics lump, and the displacement physics hull bytes (lump 28), which differ between vbsp's own runs. Block order / plane numbering depends on thread order in vbsp, so references are made with `vbsp -threads 1`.

### Different or better than vbsp

| What | Detail |
|---|---|
| No VPK reading | Python writes tables next to the map before each compile (`-materials`, `-surfaceprops`, `-props`, `-detail`, `-cubemaps`, `-fgd`), following vbsp's `FindMiptex` rules; patch material templates are rendered by Python with vbsp's KeyValues spelling rules (empty blocks omitted, names as first interned, numbers retyped `%d`/`%f`). |
| Stops at the game's limits with vbsp's message | brushes, brush sides, materials, areas, area portals, leaf faces, primitives and more, instead of writing a map the game rejects. |
| Export-side checks | a brush side with more than 64 corners (crashes vbsp without a message) is caught before the compile; near-coplanar faces snapped with a note. |
| `-onlyents` is exact and auditable | the same file as vbsp's; the smart build then strips `stale.txt`. |

### Not done yet

- **Speed.** hlvbsp was written to match, not to beat vbsp; it is not faster yet. On a large user map the map compile is 3.9 s of a 30 s Build & Play, not the critical path (vis is).
- The vbsp switches listed `not done` in [valve-options.md](valve-options.md) (`-nodetail`, `-nocsg`, `-luxelscale`, `-bumpall`, `-block`...): hlvbsp does what vbsp does without them.
- `-onlyprops`.

### Known issues

| Issue | Detail | Status |
|---|---|---|
| `styleguide_urban` differs | one arc vertex off by 0.002 units after a long chain of brush clips; the responsible step wasn't found in the time spent (SplitBrush, offset clipping and BrushVolume all match) | open since 2026-10-08 |
| `styleguide_semiurban` differs | one detail prop's angle (float noise) and two bytes of the brush physics data; not instance-related | open since 2026-10-10 |
| Shattered-glass brush + water with envmap | vbsp gives `militiaWaterbeneath` texdata 128 / reflectivity 0 from material-system state (a missing fallback material); ours doesn't reproduce that state | known difference, no sample map hits it |
| Shell heredocs | writing C through a shell heredoc turned `\n` in strings into real newlines (a tooling trap, recorded so it isn't repeated) | n/a |

---

## hlvvis (visibility compiler)

Source `native/hlvvis.c`, built to `hammerless/core/hlvvis.exe`. Default since 2026-10-10 (commit 95d3125); merged windows the default mode since 2026-10-11 (commit ac1ae02).

Two modes:

- **`-exact`** ("Match vvis Exactly" in the Visibility panel): vvis's own path search; its output byte for byte; about 3x faster than vvis.
- **Merged windows** (default): the same search with one merged state per portal per base flow (convex hulls of the source and pass windows, union of might-see bits), re-expanded only when a state grows; a sweep with exact hulls when the queue runs dry; a portal that keeps growing is opened fully (a superset). About 1% looser than vvis, several times faster again.

### Identical to vvis (exact mode)

| Claim | Evidence | Date |
|---|---|---|
| PVS + PAS byte-identical to L4D2's vvis | first bit-identical result on a large user map: 0 of 330,735 PVS cells differ; raw 334,646 and crosscheck 3,911 match vvis's own log | 2026-10-07 |
| Final corpus | 8 maps (a large user map; the two demo maps; a water level; the user map with Auto Detail off and with everything detail; rooms with an area portal; outdoor terrain) x 3 runs, full and `-fast`: every file byte-identical to vvis's, except the heaviest map (Auto Detail off): 0, 2 and 0 cells outside vvis's own 3-run variation | 2026-10-10 (commit 0ecc9dd) |
| Radial (fog far Z / `-radius_override`) vis | decoded from `vvis_dll.dll` and reproduced with the same float operations; byte-identical on the fixtures, full and `-fast` (`tests/fixtures/vis/rooms_fog.zip`) | 2026-10-10 (commit 1b1f066) |
| Unit tests | `tests/unit/test_core.py`: hlvvis on `rooms_portal.zip` and `rooms_fog.zip` must write the same file as vvis | ongoing |

What "identical" means here: **vvis is not deterministic with threads.** Whether a portal meets a finished neighbour (and uses its tighter result) or one still working (the rough bound) depends on timing and can flip a borderline pair. Measured on a large user map: 8 vvis runs gave 6 identical results; only 4 cells of about 330,000 ever varied; PAS identical. So "matches vvis" = byte-identical to one of vvis's runs, or differing only in cells vvis itself varies. hlvvis has the same behaviour with threads; `-threads 1` is fully repeatable.

Details that decide the bits (from the SDK's `flow.cpp` behaviour, confirmed against output): `ON_VIS_EPSILON` 0.01 compared in double; `MAX_POINTS_ON_FIXED_WINDING` 12 (a chop that would overflow returns its input unchanged); the source-side check uses the base portal's radius; plane = `(p0-p1) x (p2-p1)` normalised, forward portal plane negated, portals numbered 2i / 2i+1; portals sorted by might-see count; a later portal uses a done portal's final vis, else its rough flood (turning this off gives very different output); `ClusterMerge` then the symmetric crosscheck (AND); PAS = OR of the PVS rows of visible clusters; `vec_t` float. Radial: radius squared = farz^2 (double; max over fog controllers) or `-radius_override R`; every leaf's area_flags |= 0x400; in `BasePortalVis` after the two front tests, the nearest winding point distance (double, start 1024000000.0) and then a sampled fan distance (triangles at barycentric 0.25 steps, SSE floats) decide the cull.

Other lumps vvis writes, and hlvvis the same way: lump 46 (leaf distance to water: 65535 everywhere without water, `WaterDist.cpp` otherwise), 51/52/55/56 ambient placeholders (one empty sample per leaf, only if all four are empty), game lump 35 offsets, `TESTFOGVOLUME` leaf contents with water, every leaf flagged on radial maps. File layout per L4D2's writer: lumps keep input order, each lump and the file end 4-byte aligned, never-written lumps keep offset 0.

Speed-ups in exact mode that change no decision: 64-bit visibility words limited to the non-empty range; separating planes built once per step and four at a time with SSE2 (same float operations in the same order; the float-vs-double epsilon equivalence: `d > 0.01` (double) is `d >= first float above 0.01`); chops four points at a time; no descent into steps that can't reveal anything new. Two measured speed-ups were **left out** because they change *when* portals finish: narrowest-first exploration and work sharing between threads (heavy map: 26 cells off vvis instead of 0 to 4); they cost about 15%.

### Merged windows (default): not vvis's bits, proven safe

| Map | Clusters | Avg visible exact / merged | Looser | Time exact -> merged | Date |
|---|---|---|---|---|---|
| Large user map | 1194 | 285 / 288 | 1.02% | | 2026-10-11 |
| c1m1_hotel | 1813 | 215 / 218 | 0.33% | 18.4 s -> 2.3 s | 2026-10-11 |
| c5m1_waterfront | 1782 | 415 / 425 | 1.37% | 32.8 s -> 5.0 s | 2026-10-11 |
| styleguide_urban (radial) | 1262 | | | 46 s -> 4.8 s | 2026-10-11 |
| Small suite maps | | | identical | | 2026-10-11 |

6 threads for the audit table; 16 for the urban figure. For scale, vvis's `-fast` was 164% to 186% looser on the same maps.

**The claim, made exact on 2026-10-11 (commit f5e96d1):** merged windows never hides an opening wider than vvis's own 0.01-unit tolerance. The one case found (audit 3, c1m1_hotel pair 493-1711) has a real sight line with 0.0003 units of clearance, far below vvis's tolerance (vvis *keeps* such pairs, so it shows things that aren't visible). `HL_INFLATE=0.005` in the environment makes it strict at some cost in speed. Every other pair merged hides and exact shows was proven hidden by `tests/compile/vis_certify.c` (vvis's path search in doubles from the `.prt`, every tolerance on the keeping side, no done-portal shortcut; `-all` enumerates every surviving chain: a single chain proves nothing). One c5m1 pair (743-1766) needed `-all` plus a stabbing-line optimiser: no line through its 66 chains, while a control pair on the same map found one.

Two real bugs were found by Fable's audit of the first merged version and fixed before it shipped (2026-10-11): new might-see bits not re-pushed after a portal was opened fully (c1m1 hid 2 pairs); separators built from the smaller hull while points up to 0.01 outside were accepted (fixed first by inflating the hull, then replaced by exact-union hulls plus dirty sweeps at queue-empty, commit 2ae86d8; 0 portals opened fully on the test maps).

### Measured speeds (16-core PC, 16 threads unless noted)

| Map | vvis | hlvvis exact | hlvvis merged |
|---|---|---|---|
| Large user map (1191 clusters, 3466 portals) | 14.1 s (15.2 s earlier) | 5.2 s | |
| Same, Auto Detail off | 118 s | 38 s | |
| styleguide_urban (radial; 1262 clusters, 4129 portals) | 2 min 19 s | 46 s | 4.8 s |
| c5m1_waterfront | | 18 s | 3 s |
| styleguide_swamp, radial, `-fast` | 23 s | 2 s | |
| styleguide_swamp, radial, full | 475 s | 120 s | |

Profile facts: vvis spends 99% in the flow, 0.1 s in the rough pass; keeps 14 to 15 of 16 cores busy for three quarters of the run then trails to one core for about 3 s. VVIS++ (third party, closed) does the large user map in 3.3 s but hides 82 pairs vvis shows and rewrites lumps 0 and 40; not L4D2-exact.

### Different or better than vvis

- Merged windows (above). Progress lines per percent, weighted by an estimate of each portal's cost; `<map>.viscost` for the Vis Cost view (portals coloured by time, mapped back to Blender objects).
- Lighting alongside vis (`hlvrad -visfrom`): see hlvrad below.

### Not done yet

- **Reuse between compiles** (incremental vis for portals a change didn't touch): planned, no prior art found.
- Beating VVIS++'s 3.3 s in exact mode (ideas: lazy planes, AVX2 dispatch, less overhead); merged windows is the fast path instead.
- Ideas tried and dropped, so they aren't retried: ray-proven visible pairs as a head start (only about 3% fewer chains); dominance pruning (fast, but hid a pair vvis shows: float epsilon isn't monotonic); uniform and adaptive portal point sampling (exploded / too slow); published sampling, GPU and neural methods (all allow misses).

### Known issues

| Issue | Status |
|---|---|
| Exact mode with threads varies like vvis (a borderline pair or two); `-threads 1` repeatable | by design |
| Merged windows is about 1% looser and may hide an opening thinner than 0.01 units | by design, documented claim |
| Switching between Valve's and Hammerless's vis compiler doesn't trigger a rebuild (they count as the same map) | by design |

---

## hlvrad (light compiler)

Source `native/hlvrad/*.c` (main, lights, direct, raytrace, bounce, disp, leafambient, detailprops, staticprops, spropvlight, texlights, normals, layout, bspio, vis, winding, threads, physcollide/hlphys, modelfile, gpu + `shaders/`, skymap, nobake, keeplight, remix, embree (shelved)). 64-bit since 2026-10-08 (commit f6eb5ee), with prop collision through the 32-bit helper `hlphys.exe` (the game's `vphysics.dll` is 32-bit). Default since 2026-10-10 (commit 95d3125). Written 2026-10-08 in one day of staged commits (0230858 to 779980b), then GPU, GI, sky light, partial bakes and layers over 10-09 to 10-12.

### Identical to vrad (Exact mode: CPU, `light_exact`)

| Claim | Evidence | Date |
|---|---|---|
| Whole output files byte-identical to `vrad -hdr`, bounce included: all 16 test maps (sun, sky, spot and point lights, water, displacements, grass and detail props, static props, light styles) | `tests/compile/compare_vrad.py` (`--dir sdk_content` for Valve's samples; the test maps with `-noextra`, see determinism) | 2026-10-08 (commit a797c13 "whole output files now identical"), re-checked after each later feature (GPU 10-08, layers 10-12) |
| Static prop vertex lighting (`-StaticPropLighting`) | all 80 `.vhv` files of a large user map identical | 2026-10-08 (commit 779980b) |
| Leaf ambient (lumps 51/55), detail prop lighting (game lump), world lights (54), faces HDR (58), vertex normals (30/31), leaf sky flags (10) | per-lump comparisons on Valve's samples during the build-up | 2026-10-08 |
| The ray tracer | the same rays fed to vrad's own `Trace4Rays` through the in-process hook and compared: exact (`hlvrad RTTEST=file`) | 2026-10-08 (commit a1ac1bd) |
| Patches and transfers (bounce) | `g_Patches` and `MakeScales` transfers dumped from vrad through the hook, compared with hlvrad's `HLPATCHES` / `HLTRANSFERS` dumps: all patches identical | 2026-10-08 (commit b1caac9 / a5725a2) |
| Large user map with supersampling on | 94% of luxels identical (the rest: vrad's uninitialised supersampling mask, only partly emulated, below) | 2026-10-08 (commit 2d8d7a9) |
| Normal bakes unchanged by later features | byte-identical after layer support (user map and light-styles map), after sky light support (without a sky picture) | 2026-10-12, 2026-10-08 |

**vrad's determinism** (measured 2026-10-08, L4D2's vrad): two runs on a large user map differ in 0.34% of luxels (median 0.6%, 38 luxels over 5%); leaf ambient (lump 55) differs slightly run to run and almost completely with `-threads 1`. With bounce on and supersampling off (`-noextra`) vrad is deterministic, so that is the configuration for byte comparisons. `-extra` itself is deterministic too, except the uninitialised slot below.

Quirks and arithmetic copied (each found by a diff, confirmed in the disassembly or the hook; kept because removing them changes the output):

- x87 `VectorNormalize` returning the unrounded length; some lengths summed `(x² + z²) + y²` or `(z² + y²) + x²` depending on the function; form factors in double, thresholds in float; `asinf` from Intel's SSE2 table version (1-ulp); rsqrt Newton step `((3 - (g*g)*a)*g)*0.5`.
- Light directions `deg * (float)(1/180) * pi` then x87 `fsin`/`fcos`; `SolveInverseQuadraticMonotonic` times `1/det`, c-term order, blend in double; `_castentityshadow` (default 1, flag 2, cleared for suns), `_shadoworiginoffset`, `light_directional` as sun type 3 with its own `SunSpreadAngle`; `light_environment` sets the global extent for every env before the first-env check; named lights need the `style` key vbsp assigns.
- Sky: 162 Halton(2,3) directions starting at element 2; sun spread 30 rays (7 under `-fast`), Halton elements 2 onward; sky colour for leaf ambient = the world-light lump's intensity (not /255); `LinearToScreenGamma` truncates; remap `* 0.05`.
- The kd-tree: the same split-cost arithmetic (x87, unrounded compared against a float best), triangle test thresholds `isect > 1e-10`, `b0, b1 >= 1e-10`; brush sides built around an inside point pushed onto the planes it's behind (4 passes), then `+ 0.0f`.
- `CanLeafTraceToSky` starts its ray from an uninitialised point, so every non-solid leaf in a radial-vis map gets sky when the map has a sky. Kept.
- `PointsInWinding` ORs into an uninitialised stack slot (`[ebp-0x50]`), whose writers were found with DR0-3 hardware watchpoints: PIW's own mask, `Resample` `fxdot[0]`, sky gather temporaries, `Trace4Rays` mailbox fills, `TestLine_DoesHitSky` pushes. Emulated: PIW's mask and the resample `fxdot` (small maps 99.3 to 100%, the large user map 94% with `-extra`); the rest would mean copying the tracer's stack layout. `g_staleSlot` is reset per face so the output is the same with 1 or 16 threads.
- Leaf ambient: `MakeParents` only from node 0 (brush-entity leaves keep parent 0, so every sample fails and the leaf centre is used); ray length `(z² + y²) + x²`; displacement boxes ±1.
- Static props: indirect light has **no distance falloff**; full 162 rays (bad vertices forced fast); the `CLightSurface` is kept across a vertex's rays (stale hitfrac); `PointLeafnum` is a plain walk; Halton inverse `*= 1/base`; `lineartovertex = min(1, (i/1024)^(1/2.2) * 0.5)`; map flags lump 59 bit 2. Prop collision: the `.phy`'s first solid through `vphysics` (`UnserializeCollide`, `CreateQueryModel`); with no `.phy`, L4D2 builds a convex hull per mesh from the `.vvd` vertices as stored (`ConvexFromVerts`, `ConvertConvexToCollide`); a box only if the model is unreadable. Matrix per `VMatrix::SetupMatrixOrgAngles` (`(sr*sp)*cy`).
- Radial filter: `SampleRadial` weight 0 gives **black** in L4D2 (red only for out-of-range u/v). Displacements: `CalcNormalFromEdges` + `BlendTJuncs/Corners/Edges`, 64-unit sample hash cells; scalar `GetPhongNormal` (first fan triangle wins, 0.99999988 normals); sub-edge order `{p1-p0, p2-p0, p2-p1}`; `AddBouncedToRadial * 1/distt`.
- `-StaticPropPolys` = LOD0 vtx strip triangles; `lights.rad` `noshadow` honoured; `forcetextureshadow` only matters with `-textureshadows` (unsupported).

### GPU mode (default: `-gpu`, Vulkan ray queries; NVIDIA RTX, AMD RX 6000+, Intel Arc)

Not bit-exact by design; "visually identical, several times faster". Direct light is record/replay rounds: the CPU lays out the sample points as vrad does, `gather.comp` answers the shadow rays on the RT cores, one round per supersampling pass; vrad's stale mask is reproduced from the sign of the last light's falloff (no rays needed). Leaf ambient and prop bounce don't use the RT cores: ray-traced surfaces didn't match vrad (vrad's tree walk hits a face's **lightmap rectangle**, not the face, and tests displacements against the whole ray from any leaf their box touches), so vrad's BSP walk was ported to a shader (`walk.glsl`). Work is dispatched in chunks so Windows never resets the card. GPU init on a background thread. Bounce links (transfer visibility) on the GPU since 2026-10-09 (commit 1c161be): 0.26 s -> 0.11 s for about 10 million tests.

| Measured against Exact, large user map (RTX 4090) | Identical |
|---|---|
| Lightmap bytes, Normal | 99.991% |
| Lightmap bytes, Final | 99.917% |
| Static prop vertices | 99.996% |
| Leaf ambient | about 98.8% within 1% (the same spread as two vrad runs) |

The differing pixels are near-black and single pixels on shadow edges.

### Speed

| Mode, large user map | vrad (16 threads) | hlvrad CPU Exact | hlvrad GPU (RTX 4090) |
|---|---|---|---|
| Normal | 6.0 s | 2.1 to 2.2 s | 1.2 s, later 1.0 s |
| Final | 16 s | 7.3 to 8.2 s | 1.5 s, later 1.4 s |
| Ultra | (not possible) | 15 s | 2.6 s, later 2.2 s |

History: the first wired-in version was single-threaded, 81 s (2026-10-08, commit ceed756); threading per-face work, leaf ambient (68 s of that: fixed with displacement box tests and 16-triangle band boxes), `MakeAllScales`, plus per-displacement BVHs brought it to 4.0 s then 2.1 s (commits 927a04b, f9d2e58). `ldexp` in the UCRT was 25% of the time at one point. Output identical with 1 or 16 threads. A 2.6 million square foot map: 27 s (when it silently fell back to Valve's vrad over the old 262,144-patch limit) -> 4.7 s (2026-10-09, commit f21a18f). Remaining serial parts: kd build 0.6 s, transfers 0.27 s (CPU), detail props.

### Different or better than vrad

| Feature | What | Measured |
|---|---|---|
| Sky light from the skybox or an HDRI (`-skymap`, `skymap.c`, `core/skylight.py`) | each sky ray takes the colour where it looks (faces, supersampling, bounce, static and detail props, leaf ambient); the panorama is blurred to the ray spacing (9 degrees) and scaled so a floor under open sky gets the same light as the flat colour | L4D2 sky face orientation measured from its skies (rt +X, ft -Y, lf -X, bk +Y, up; compressed HDR = rgb x 16 x alpha; side faces are the upper half); absolute orientation not yet checked in game |
| Ray-traced bounce on the GPU (`-gi`, final gathering) | cosine-weighted hemisphere rays per sample from the current lightmaps times reflectivity; a pass per bounce, stops under 0.2% added, 8 at most | large user map, 4 bounces at 1024 rays: 0.15 s; the 2.6 M sq ft map 1.3 s vs 2.6 s for patches; brightness 1.04 to 1.08x vrad's; no patch light leaks; works with vis off |
| Lighting alongside vis (`-visfrom`, `-visthreads`) | faces' direct and bounced light don't need vis; waits for vis only for prop light and sky leaves | byte-identical to lighting after vis; Build & Play 29.0 s -> 26.6 s |
| Partial bakes (`-nobake`, Control Volumes) and keep light (`-keeplight`) | points not baked aren't traced; what isn't baked takes the previous build's lighting where the face/leaf/prop is exactly the same | 6,410 faces outside the area kept exactly, 1.7 s; a quarter of the map in 1.6 s instead of 3.2 s; with bounce off everything outside is byte-identical to a full bake |
| Supersampling beyond vrad's (`-sspoints` up to 16, `-sspasses`, `-ssthreshold`) and `-fixquirks` (Ultra quality) | | not vrad-identical by design |
| Live Light Mixing (`-layers`, `-remix`, `-onlylights`, `remix.c`) | one bake writes each tagged light's own contribution (half floats, `<map>.hllayers`); Blender remixes in about 5 ms; a build with only colour/brightness changes runs `-remix` instead of baking | remix vs real bake: lightmaps median 0.5%, every luxel within 2% (bump pages and styles too); leaf ambient median 0.2%; world lights exact; per-light layers summed vs the whole bake median 0.1%, 99.89% within 1%; 1.3 s vs 4.4 s; about 0.3 s per light at first, 0.6 s with bump pages and styles; separate bakes per light (3 to 4 s each) were rejected. Gathering bounce from layer luxels not samples mattered (5 to 25% off otherwise); writing GPU output to device memory then one copy back (zero-filling host memory cost 4x the GPU time) |
| Switchable and patterned lights in their own styles | the game's 12 patterns or custom steps; both compilers, no fallback | in-game test `tests/ingame/test_lightstyles.py` (2026-10-12) |
| Threads with identical output | | 1 vs 16 threads identical in Exact mode |

### Not done yet

- `-textureshadows` (alpha-tested foliage shadows, `forcetextureshadow` in `lights.rad`): hard error in hlvrad. Planned 2026-10-13.
- vrad options listed `not done` in [valve-options.md](valve-options.md): `-softsun` (command-line form), `-scale`, `-ambient`, `-sky` (planned), `-fastambient`, `-centersamples`, `-luxeldensity`, `-dlightmap`, `-disppatchradius`, `-maxdispsamplesize`, `-largedispsampleradius`, `-noskyboxrecurse`, `-nodetaillight`, `-onlydetail`, `-onlystaticprops`, `-staticpropnormals`, `-nossprops`, `-dlight`, `-notexscale`, `-coring`, `-finitefalloff`, `-staticproplighting3`.
- vrad's `-fast` path: decided not to port (2026-10-13); the build never passes it to ours (Fast quality = `-noextra -bounce 2`, commit 31a33e5); hlvrad's own `-fast` flag only cuts ray counts.
- Live Light Mixing gaps: no layers for vrad's patch bounce (mixing forces GPU ray-traced bounce); static props' vertex light not mixed live in the Blender view (the build's remix does remake it); a wider radial than 4 channels would cut the per-light cost.
- Sky light from an image doesn't change the visible sky (no skybox texture writer); its absolute orientation not checked in game.
- Embree tracer: shelved (about 15% gain; our kd tracer already packets 4 rays), never shipped (no DLLs in the release); `compile.py` no longer asks for it (2026-10-13, commit e1e0d1d).
- Caching light-independent work (kd tree, transfers) by lump hash; a per-light contribution cache for previews (Fable's suggestions, 2026-10-11, not started).

### Known issues

| Issue | Detail | Status |
|---|---|---|
| Exact mode with supersampling: 94% of luxels on a large user map | vrad's uninitialised stack value only partly emulated | open since 2026-10-08; full emulation parked |
| Rays exactly through a kd split corner | a handful of luxels can differ (vrad's x87-reordered `CalculateCostsOfSplit` ported, remaining ties) | open, time-boxed |
| Ray-traced bounce is GPU only and marked experimental | 1.04 to 1.08x vrad's brightness | by design |
| Very big maps | stop with a clear message rather than overflowing hlvrad's tables | by design |
| `vrad -dump` / `-dumptrace` write into the game folder | a vrad trap (not ours), recorded so test runs move the files out | n/a |

---

## Test tools (all in `tests/compile/`; they need the game and the Authoring Tools)

| Tool | Tool under test | What it does |
|---|---|---|
| `bspdump.py` | all | Dumps an L4D2 .bsp (version 21) as text, one record per line, every lump named; `--diff a b` prints the first differing record per lump; masks vbsp's uninitialised bytes |
| `compare_vbsp.py` | hlvbsp | Compiles .vmf files with both compilers and compares; `--dir <sdk_content>` runs every .vmf in a folder |
| `mapcompiler_suite.py` | hlvbsp | 23 generated feature maps, lump by lump and by portal file; Valve's reference reused while a map's .vmf is unchanged |
| `onlyents_suite.py` | hlvbsp `-onlyents` | whole files vs `vbsp -onlyents` over a series of entity edits (`--lit`, `--vmf`, `--dir`, `--built`) |
| `compare_vis.py` | hlvvis | one VMF, vvis and hlvvis several times each, full and `-fast`; a match is byte-identity with one of vvis's runs, else the count of cells vvis never produced |
| `vis_merged_suite.py` | hlvvis | `-exact` vs merged on many compiled maps: time, looseness, and every pair merged hides checked |
| `vis_check.py` + `vis_rays.c` | hlvvis | random sight lines at every pair a result hides; one line through open space proves the result wrong; pairs no ray reaches go to `vis_certify` |
| `vis_certify.c` | hlvvis | proves a pair hidden (vvis's path search in doubles, tolerances on the keeping side, no done-portal shortcut); `-all` enumerates every surviving chain |
| `vis_inflate_test.c` | hlvvis | the retired 2D hull inflation (8M points, worst 0.0019 out at 16k) |
| `compare_vrad.py` | hlvrad | hlvrad vs vrad on .vmf files or `--dir sdk_content`, optionally after vis (`--vis`) |
| `light_mixing.py`, `light_layers_sum.py` | hlvrad layers | live mix vs a real rebake; `-onlylights` layer sums |
| `tests/unit/test_core.py` | hlvvis, toolopts, buildplan | fixtures `tests/fixtures/vis/rooms_portal.zip`, `rooms_fog.zip` |
| `tests/ingame/test_mapcompiler.py`, `test_lightstyles.py`, `test_light_mixing.py` | all | in-game checks through the test bench |

The release gate for the vis compiler is the suite plus `vis_certify -all` on every pair merged hides and exact shows, plus a sight-line search over each of those chains.

Last checked: 2026-10-13 against branch light-layers, commit 31a33e5.
