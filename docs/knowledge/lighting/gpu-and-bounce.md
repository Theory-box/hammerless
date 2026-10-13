# The GPU path, ray-traced bounce, and lighting alongside vis

Code: `native/hlvrad/gpu.c` (Vulkan, buffers, dispatch), `native/hlvrad/shaders/` (`gather.comp`, `ambient.comp`, `propind.comp`, `vis.comp`, `gi.comp`, `walk.glsl`, `skymap.glsl`; compiled to `.spv.h` headers by `native/build.py`, which needs glslang), `native/hlvrad/direct.c` (the per-face driver, `-gi`, `-layers`), `native/hlvrad/leafambient.c` (`-gpu` export of the walk's data). Branch history: `hlvrad-embree` (2026-10-08), merged.

## Why

The user wanted big speed with visually identical results, keeping Exact mode for bit-identity (research note, 2026-10-08). Embree was tried first (`-embree`, commit 59528ce): only about 15% faster, because hlvrad's kd tracer already packets 4 rays; shelved and never shipped (the release zip has no `embree4.dll`/`tbb12.dll`; `compile.py` stopped passing `-embree` on 2026-10-13, commit e1e0d1d, because every install logged "can't load embree4.dll"). The GPU path (`-gpu`, commit 72a0441) gave Normal 2.2 s -> 1.2 s, Final 7.3 s -> 1.5 s on a large user map.

## How the GPU path works

Status: `done`, default unless Exact Lighting is on. Vulkan comes with the graphics driver (`vulkan-1.dll`), loaded only when asked; without a capable card (needs ray queries: NVIDIA RTX, AMD RX 6000+, Intel Arc) hlvrad warns and lights on the CPU. A driver that refuses the GPU programs also falls back to the CPU (0.11.x). GPU init runs on a background thread while the CPU does its setup.

**Direct light = record/replay rounds.** The CPU lays out every sample point exactly as vrad does (`BuildSamplesAndLuxels`, windings, balance points), records the shadow rays each light needs, and `gather.comp` answers them on the RT cores. Supersampling needs one round per pass (each pass's points depend on the last pass's result), so a Normal bake is 1 + 4 rounds. vrad's stale supersampling mask (`PointsInWinding`'s uninitialised slot, see compilers/what-ours-do.md) is reproduced from the **sign of the last light's falloff**, which needs no rays, so the same points get supersampled as on the CPU.

**The shadow scene**: the ray tracer's triangles (the world, the sky's, the static props' kept apart so a prop can skip its own shadow). Since 41dc861 `-textureshadows`' see-through triangles live in a separate instance (gather.comp bindings 8-11): cover is summed up to the first solid hit.

**Leaf ambient and prop bounce do not use the RT cores.** Ray-traced surfaces did not match vrad: vrad's tree walk hits a face's **lightmap rectangle**, not the face polygon, and tests displacements against the whole ray from any leaf their box touches. So vrad's BSP walk was ported to a shader (`walk.glsl`) and `ambient.comp` / `propind.comp` use it: the same surfaces, quirks included. `leafambient.c` exports the map as the CPU walk sees it (bindings 1-14, the last the lightmaps).

**Bounce links on the GPU** (`vis.comp`, commit 1c161be, 2026-10-09): vrad's patch-to-patch visibility tests (`MakeScales`' transfers, about 10 million on a large user map) went from 0.26 s to 0.11 s. The transfers' form factors stay on the CPU (0.27 s).

**Chunks**: work is dispatched in chunks, each waited for, so no single GPU job runs over Windows' 2-second TDR limit (a stuck GPU is reset by Windows). A memory leak when bouncing on the GPU (several GB on a mid-size map, which then fell back to Valve's vrad) was fixed in 0.12.x.

**Host memory lesson** (2026-10-12, layers work): the GPU's outputs are written to device memory and copied back once (`RunOut`); zero-filling host-visible memory first cost 4x the GPU time, and per-slot dispatches about 0.6 s per layer. Write each value once to host memory, never read or `+=` it there.

### How close to Exact

| Part, large user map | Identical to Exact |
|---|---|
| Lightmap bytes, Normal | 99.991% |
| Lightmap bytes, Final | 99.917% |
| Static prop vertices | 99.996% |
| Leaf ambient | ~98.8% within 1% (the same spread as two vrad runs) |

Differing pixels: near-black ones and single pixels on shadow edges. With `-textureshadows` the GPU is further off (1.7% of luxels differ, 0.19% by over 1%, 2026-10-13) because vrad counts see-through triangles slightly past a ray's end, in whatever kd cell it visited last, which a ray-query traversal can't reproduce.

### Debugging it

Environment variables in hlvrad: `HLGPUDBG` (rounds, leaf timings), `HLGPUCHK` (CPU vs GPU per answer; single thread only, it flips a global phase), `HLGPUTYPES` (light-type mask), `HLWALKDBG` (trace the CPU walk for one ray), `TLTEST` / `TLLOG` (shadow tests vs the vrad hook programs, 41dc861). Lightmap/ambient/prop diff tooling (two-bsp diff with PNG output) exists as development scripts outside the repo.

## Ray-traced bounce: `-gi N -girays M` (final gathering)

Status: `done`, GPU only, marked experimental in the UI. Commit ce87b21 (2026-10-09). Code: `direct.c` (`-gi` section), `gi.comp`.

vrad's method is radiosity with **patches** (`bounce.c`): every lit face is cut into patches at most a few luxels across (finer at edges, `-chop`/`-maxchop`), each patch's direct light is averaged from its samples, each patch gets the list of patches it can see (same PVS, in front, unobstructed) weighted by form factors, and light is passed along the lists up to 100 times (`-bounce`); luxels then add nearby patches' bounced light in `FinalLightFace`. hlvrad reproduces this exactly (patches and transfers identical to vrad's via the hook dumps).

`-gi` is a different method, **final gathering at luxel detail**: each face's lightmap samples (the points their direct light was gathered at) send cosine-weighted rays over their hemisphere (a stratified set, turned by a random amount per point); each ray takes the light of the surface it hits from the **current lightmaps**, times that surface's reflectivity; the mean is the bounced light. Bumped faces weight the same rays toward each bump direction. Each pass gathers from the lightmaps the last pass made (direct + bounce so far), so N passes = N bounces. It stops when a pass adds under 0.2%, and at most `GI_MAX_PASSES = 8` are run: each pass carries on roughly a twentieth of the light, so 8 passes are as good as vrad's 100. `compile.py` passes `-gi min(bounces, 8)`.

What it gives: bounced light at luxel detail instead of patch-sized blotches; no patch light leaks; bounce even with Visibility off (patches need the PVS). Measured 2026-10-09: 4 bounces at 1024 rays 0.15 s on a large user map; a 2.6 million square foot map 1.3 s against 2.6 s for patches; overall brightness 1.04 to 1.08x vrad's. 256 rays shows a little grain; 4096 is about 3x slower. The GI ambient on characters was found to have no sky (56% of the right brightness) and fixed to within 0.4% (0.11.x).

Live Light Mixing forces `-gi` on (patch bounce has no per-layer version; see layers-and-styles.md). With layers, `gi.comp` reads each layer's grid at the same ray hits (`GI_LAYER_BATCH = 4` layers gathered together).

`-fixquirks` (Ultra) removes four vrad behaviours in the CPU/GPU paths: edge luxels left dark by the stale supersampling mask; bounced light picked up from a face's lightmap rectangle instead of the face; far displacements hiding nearer faces; props' bounced light missing far surfaces. Deliberately not vrad-identical.

## Lighting alongside vis

Status: `done` (0.12.x, 2026-10-10). Code: `CompileJob._start_light_alongside` / `_finish_light_alongside` in `hammerless/core/compile.py`; hlvrad `-visfrom <map>.bsp -visthreads N`; `.visdone` / `.visfailed` marker files written next to the map by the build when vis ends.

Vis is the critical path of a full build. A face's direct and bounced light don't need the PVS: vis only spares rays a light can't reach. So with hlvrad on the GPU and `-gi` bounce, the build copies the compiled map to `<map>.light.bsp` before vis starts and lights the copy; hlvrad waits for vis (polling the marker) only for what needs it: static prop light and which leaves see the sky. When both are done the lit copy replaces the map. Measured byte-identical to lighting after vis.

It does not start early (lights after vis as before) when: vrad's patch bounce is used (transfers need the PVS); Valve's vrad is the light compiler; the map has fog far Z or `-radius_override` (radial vis changes which leaves see the sky). The light step also takes vis's changes to leaves (fog volumes and water, 0.12.x fix).

Threads during vis: `LIGHT_THREADS_DURING_VIS = 0` (one per core) because fewer threads made things worse (all cores 10.0 s, 8: 10.3 s, 4: 10.8 s, 2: 12.4 s on a real map). Total CPU work is fixed; contention is the real limit. Build & Play on a large user map: 29.0 s -> 26.6 s with this and the scheduling fixes (35.3 s earlier that day before the stringtable dictionary fix).

## Not done / known issues

| Item | Status |
|---|---|
| Transfers' form factors, kd build (0.6 s), detail props still on the CPU | not done |
| Caching light-independent work (kd tree, transfers) by lump hash; per-light contribution cache for previews | not done (Fable's suggestions 2026-10-11) |
| `-gi` is GPU only; no Exact/CPU version | by design |
| GPU `-textureshadows` not exact (last-kd-cell behaviour) | known issue, 2026-10-13 |
| Embree tracer | shelved, never shipped |

Last checked: 2026-10-13 against branch light-layers, commit 41dc861.
