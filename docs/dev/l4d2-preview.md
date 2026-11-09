# Experimental L4D2 viewport renderer

Status: work in progress on `feature/l4d2-preview-renderer`. Not a finished
replacement for VRAD or the game's material shaders. No release authorized.

## Implemented

- Evaluated object-local geometry cache; transforms reuse extracted geometry
  and mesh BVHs. Instance transforms have a separate top-level accelerator.
- Point, spot, sun and exporter-sampled area direct lighting; flat sky visibility; opaque GPU shadow
  rays using a software BVH (not hardware ray tracing).
- Progressive diffuse paths, configurable rays and one to eight bounces.
  Flat sky also refines when bounce rays are zero: nine directions per frame,
  18 frames per complete 162-direction cycle. Large area emitters use eight
  batches; 72 frames complete both cycles. Partial cycles are estimates.
  Area sampling on secondary bounce hits selects one weighted emitter sample.
- Linear accumulation, fixed sRGB display, exposure without retracing, viewport
  depth restoration. Camera/scene/material changes reset accumulation.
- Source texture precedence, custom image selection, UnlitGeneric base output,
  source-root-separated image caches, byte validation during snapshot rebuild.
- Model material provenance and verified reversible legacy cache-name lookup.
- Refresh Game Materials rereads game content and restarts accumulation without
  re-extracting scene geometry. It does not reload user-edited Blender images.

## Evidence and limits (2026-11-09)

Artifacts live under the workspace `outputs/l4d2-preview` directory, outside
this repository. Test code is under `tests/blender`, `tests/compile` and
`tests/ingame`. Metrics below are fixture-specific, not universal fidelity claims.

- Direct light compared with CPU/GPU VRAD; controlled one/two/four-bounce tests.
- Actual game direct-lit floor capture: mean absolute display difference about
  0.0083 on the stationary shadow fixture. JPEG and game postprocessing remain.
- VRAD planar radial-filter reconstruction: 525 distinct positions, maximum
  normalized linear red error 0.00194. Interpolated profile RMS 0.000586 versus
  0.03964 for hard rays. Saved-image floor ROI error improved 0.00801 -> 0.00539.
- Nine GPU filter taps match the independent sixteen-term CPU sum within
  1.9e-7; nine-phase mean matches full evaluation within 1.5e-7.
  This filter is NOT connected to live rendering.
- VMF-derived affine lightmap vectors match compiled slanted brush fixtures,
  including shifted/rotated texture axes and two scales. World-space steps
  need not be orthogonal. Live imported mapping/overrides and boundary
  filtering still require implementation and validation.
- Actual viewport callback tests cover geometry reuse, light/transform/camera
  resets, exposure, material refresh, sky-only refinement and depth restoration.
- RTX 4090, 1280x720, representative imported map (256 instances, 68,207
  triangles), synthetic camera light: sky-only progressive frames about
  7.2-7.4 ms; settled frames about 2 ms after resolving all 23 legacy model
  materials. Synchronous draw/readback timing excludes scene preparation.
  Paired full-sky runs with resolved textures measured 34.4/36.2 ms for
  overview/spawn. No missing-material warnings in either corrected run.

## Remaining work

- Alpha-tested/translucent geometry and shadows; material emission; normal maps, advanced Source shaders and model lighting parity.
- Integrate bake-like filtering only with correct grid provenance and boundary
  treatment. Do not replace it with a screen-space blur or guessed square grid.
  Avoid correlating its nine-phase schedule with sky-direction subsets.
- Broader in-game scenes and bounce comparisons; real interaction timings,
  first-frame/stall/memory measurements, complex instance/export equivalence.
- Vulkan investigation found separate-device interoperability/readback costs;
  no production Vulkan/hardware-RT path has been implemented.
- External texture changes require explicit Refresh or another material
  snapshot invalidation; there is no filesystem watcher.

## Test discipline

Game capture requires explicit `--run --user-away`, a closed game, a right-side
secondary monitor and no existing mapspawn script. Launches can capture the
mouse even without simulated input. Do not use old away authorization for new
captures. Preserve configuration and clean up only the process/files created
by the test. Short map names are required by the capture harness after long
names caused observed signon failures. Never scan the entire multi-GB console
log; read its tail.

The old rotated `game-shadow-reference-v2` capture is invalid for pixel
comparison. Use `game-shadow-still`, with its recorded camera and BSP hash.
`mat_fullbright 1` is not an UnlitGeneric parity reference.

## Area-light milestone

- Export sample positions/power verified for square, rectangle, disk and ellipse,
  with rotation, mirrored scale, zero energy and ignored emitters.
- GPU radiometry agrees with an independent two-cosine/inverse-square sum.
  Half-emitter occlusion returns half the illumination; moving the blocker
  preserves its mesh BVH. Complete eight-phase means match full evaluation,
  including uneven 17-sample sets.
- Actual viewport callbacks verified eight-frame accumulation and light,
  transform, camera, refresh resets; exposure retains the converged history.
- Compiled 17-position area shadow profile: unfiltered maximum linear error
  0.00899, planar four-cell-filtered maximum 0.000238. Filtering remains test-only.
- Representative map at 720p, one 256-sample area: full direct sampling
  96-139 ms/frame, progressive 13.7-14.3 ms without sky and 15.4-15.5 ms with
  sky. Settled frames about 2 ms. Synthetic light placement; no GI in this
  benchmark. Multiple area emitters and bounced-area performance unverified.

## Controlled rerun after process cleanup (2026-11-09)

Earlier timings above were collected while leftover Blender test processes
existed. They are historical measurements, not reliable paired speedup evidence.
Use `tests/blender/run_isolated.py` for subsequent GUI tests: it holds a single-run
lock, assigns the process to a Windows kill-on-close job, waits for exit and
kills its owned job on timeout. Python-only tests cover normal exit, timeout,
descendant cleanup and preservation of an unrelated process. A zero process
exit does NOT establish test success; inspect the fresh test JSON as well.

Fresh area GPU checks and bounce checks passed. The area secondary-ray estimator
now chooses one emitter sample per hit, weighted by the emitter sample count;
primary rays retain the eight-batch cycle. In the controlled 8192-path fixture,
full area evaluation returned 0.03140916 red, sampled evaluation 0.03095248
(1.45% difference). This is a finite-sample check, not broad convergence proof.

Fresh representative-map 1280x720 timings, one 256-sample area, eight paths per
pixel, one bounce, sampled sky/area and progressive primary lighting:
with sky, overview 28.94 ms and spawn 49.90 ms median refining frame; settled
cached drawing 1.92/2.32 ms. Includes synchronous pixel readback, excludes scene
preparation. Only eight refinement frames were measured: this establishes cost,
NOT completed sky convergence or final image quality. RTX 4090. Reports:
`outputs/l4d2-preview/map-clean-sampled-area.json` and corresponding process log.
No test Blender processes remained after the rerun (only original PID 7676).

Convergence rerun at 640x360: compared 72 accumulated frames with 288
(same sequence, not an independent ground truth). Spawn-view mean absolute
linear RGB difference 0.00023261, 95th percentile 0.00065941; overview below
0.0000005 mean. Inspected the saved spawn image: visible room/materials and
soft illumination, but dark under synthetic benchmark lighting. This checks
stability, not matching an actual map bake. Sky/no-sky spawn results coincide
in this enclosed view; this fixture is insufficient for outdoor GI validation.
Report: `outputs/l4d2-preview/map-convergence-clean.json`. Test exited in 38.8s.

Fresh matched GPU VRAD two-bounce room comparison (2026-11-09): 36 floor samples
(nine per compiled face), compiled geometry/reflectivity, 4096 rays. Indirect mean absolute
linear RGB error 0.00043256, max 0.00141554, mean relative error 5.07%.
Both bakes were rebuilt during this run; compiler log confirms two GI passes.
This is bounded transport agreement, not arbitrary-material/game-image parity.
Report: `outputs/l4d2-preview/two-bounce-reference/results.json`.

## Trying this experimental build

Install the local preview ZIP using Blender Preferences > Install from Disk.
Select **L4D2 Preview (Experimental)** as the render engine, then use Rendered
viewport shading. Render Properties contains exposure, Bounce Rays, Max Bounces
and Refinement Frames. Start with 0 bounce rays for direct light; try 8 rays,
1 bounce and 72 refinement frames for indirect light. These settings do not
change the map bake. F12 final rendering is not implemented.

Connected Blender emission now contributes sampled direct lighting using the
existing exporter's mesh-light conversion. Snapshot tests verify positions,
power changes, transforms and removal; actual viewport tests verify resulting
illumination and accumulation resets. Emission samples are cached per geometry,
transform and material revision. Native VMT selfillum/texlights and visible
self-emission are NOT covered by this integration. Alpha-cutout/translucent
shadows remain opaque, and game exposure/advanced shader matching is pending.
This is an experimental test package, not completion of the renderer goal.

## Large-map limitation confirmed

`c1m1_hotel_with_transition.blend`, 2,716,156 triangles / 4,784 instances,
RTX 4090 at 1280x720, one bounce / eight paths, sampled sky and 256-sample
synthetic area: refining medians 570-785 ms; settled drawing 192-204 ms.
First overview frame 2.27 seconds. Complete isolated job exited in 72.4 seconds.
Report: `outputs/l4d2-preview/hotel-gpu.json`. This is NOT usable realtime on
this scene. The smaller map's 29-50 ms result cannot be generalized. Both
results exclude preparation and use synthetic benchmark illumination.
Large-scene drawing/submission and traversal require profiling before further
performance promises; no hardware-ray-tracing acceleration is implemented.


Cached-depth display optimization: Blender 4.4 runtime confirms CreateInfo has
no depth_write method (4.5 docs do). On OpenGL, a legacy GLSL display shader
copies the sample depth while displaying accumulated color, avoiding a second
geometry submission. Other backends retain the geometry-depth pass. Spatial
8x8 depth equality, settled reuse, resize, exposure and real viewport reset tests
pass. Hotel rerun: settled 0.08-0.17 ms versus previous 192-204 ms; refining
387-841 ms remains too slow. These synchronous measurements are not end-to-end
UI FPS. See hotel-cached-depth.json and history-check.json. Documentation:
https://docs.blender.org/api/4.5/gpu.types.html#gpu.types.GPUShaderCreateInfo.depth_write

Camera-only raster culling: conservative transformed bounds tested against
clip planes, straddling bounds, invalid inputs and randomized perspective
interior points. Bounds cache retains owner references to avoid recycled-ID
collisions. Full geometry remains in TraceCache. Controlled bounce tests and
actual emission viewport tests pass with culling enabled. Hotel spawn view
submits 64 / 4,784 instances: sampled-sky refining median 103.49 ms versus
397.01 ms before camera culling. Overview retains all 4,784 and costs 820 ms.
No VVIS dependency. See hotel-cull.json. This remains too slow for a smooth
large-map interactive preview; settled cached drawing stays below 1 ms here.


Depth prepass benchmark: hotel interior sky refining 77.93 ms versus 103.49 ms
without prepass; overview 765.43 ms versus 820.03 ms. Both saved sky comparison
PNGs were byte-identical before/after. Enabled for progressive previews above
256 instances; this threshold is a heuristic, not a measured universal optimum.
The prepass uses the same raster culling and leaves ray geometry unchanged.
Report: hotel-prepass.json. Large-scene refinement still needs substantial work.

## Surface lighting cache experiment

The `previewlightcache.py` prototype evaluates irradiance at supplied surface
samples into a GPU texture, independently of the camera. The initial standalone
checks below preceded live integration (see the next section). Its readback produces numeric lighting
and sample coordinates for later bake/export work; no BSP-writing claim.

Compiled two-bounce fixture: four floor charts, 4,356 samples (66x66 storage),
using compiled lightmap vectors and extents. Independent point-light maximum
linear RGB error 1.62e-7 on interior samples. 72 refinement passes with eight
paths / two bounces took 54.6 ms including final readback in this small test.
Direct cold setup/readback varied 3.7-17.7 ms across runs; not a stable throughput
measurement. Full-resolution 1280x720 chart display median 0.924 ms across 30
camera moves, with exactly zero additional lighting evaluations. View inspected:
smooth point-light gradient, no obvious chart seam. No detailed textures in this
fixture. Real map memory, throughput, scheduling and image quality remain unproven.

Artifacts: surface-cache-check.json, surface-cache-charts.json,
surface-cache-samples.npz, surface-cache-display.png. The prototype invalidates
on caller-provided scene signatures; identifying affected regions is not solved.

Next integration must preserve export policy: material scale or scene default,
plus original imported per-face scales unless Imported Brushes Too is enabled.
Adaptive lightmaps can further alter final scales. Do not replace these policies
with a separate preview density. Props require a separate lighting strategy;
the compiled brush chart experiment does not establish model lightmap support.

Surface chart construction now consumes VMF side axes and lightmapscale before
BSP compilation. Planar/slanted and shifted-axis tests pass, rectangular margin
samples clamp to the convex surface boundary, and oversized charts fail rather
than silently coarsening. New Blender brush export tests confirm material
scales 4/8/16 and inherited scene scale 32 propagate into chart density exactly.
A 128-unit cube produces 6,534 / 1,734 / 486 / 150 samples respectively.
This pre-BSP path is not enabled in the normal renderer yet. Imported per-face
scale handling, adaptive scale changes, non-brush models and cache invalidation
still require integration. Reports: live-surface-scales.json.

Imported chart extraction reuses solid_block/_faces and the same opt-in scale
policy as VMF rebuild, using a copied original block to avoid mutating shared
source state. Tests cover preserved per-face scales, material and scene override,
restoration when toggled off, moved originals, duplicates and unchanged source
text. Per-object layout cache tests cover no-change reuse, scale/transform/
geometry invalidation and deleted-object eviction. Geometry and imported-text
revision tokens are caller obligations; these are not yet wired into view_update.
Displacements/models reject chart extraction rather than pretending they are
planar brush faces. These checks preceded the live integration below.

## Live surface cache integration (opt-in, not installed yet)

`hl_preview_surface_cache` enables the first live brush-cache path. Default is
false while broad validation remains incomplete. `view_update` builds/reuses
export-policy charts and material bindings; `view_draw` refines GPU irradiance
and displays full-resolution textures multiplied by the cached light. Surface
samples use scene/material/imported-face lightmap scales. Source texture axes
and Source-to-Blender unit conversion are independent of this lighting density.

Successful brush charts skip per-pixel lighting, but all geometry remains in
the ray scene. Props, instanced objects, terrain, displacements, hull-converted
objects and rejected charts retain the prior renderer. A one-million-sample
budget falls back with a warning rather than silently reducing density.
Edits conservatively restart irradiance; camera and exposure changes do not.
This is not incremental regional relighting or final BSP lightmap export.

Actual RenderEngine callback tests passed light changes, object transforms,
camera changes, exposure, explicit material refresh and cached/uncached depth
composition. A prop moved from in front of a cached brush to behind it. Camera
and exposure changes made no additional cached-light evaluations. Baseline
renderer tests with the switch disabled also passed. Independent static review
caught and verified fixes for hull/terrain eligibility, accumulated material
binding dictionaries, eager snapshot construction and disappearing budget
warnings. Blender test processes exited normally under the owned job runner.

Saved `deadline_roundtrip/roundtrip.blend` benchmark: RTX 4090, 1280x720,
256 instances / 68,207 triangles, eight bounce rays, one bounce, eighteen
refinement frames, synthetic point illumination with/without flat sky.
164 objects / 725 charts / 40,472 samples cached. Initial CPU layout preparation
was approximately 0.44 seconds in the first run. First GPU setup can still stall
for seconds (shader/resource preparation); this is not hidden by steady-state
numbers. Camera-navigation timings include GPU draw and synchronous pixel
readback, exclude scene update work, and are not whole-application FPS:

| View | Sky | Per-pixel baseline | Surface cache + depth pass |
|---|---|---:|---:|
| Overview | Off | 10.44 ms | 4.17 ms |
| Overview | On | 13.98 ms | 4.20 ms |
| Spawn | Off | 13.60 ms | 4.23 ms |
| Spawn | On | 21.42 ms | 5.26 ms |

Cache refinement medians were 9.1–12.6 ms in that run. A cached-geometry depth
pass is necessary to avoid shading hidden props before walls cover them. Sky
comparison PNGs were byte-identical with/without this pass. Raw reports:
`map-baseline-navigation.json`, `map-cache-navigation.json`, and
`map-cache-depth-navigation.json` under the workspace's outputs/l4d2-preview.

Remaining validation: large hotel map, interactive editing cost, boundary
sampling/seams, image comparison against VRAD and game, and package deployment.
Adaptive compile-time scale changes, BSP face splitting, exact boundary filtering
and advanced material parity are not implemented by this cache.

## Large-map rejection audit and partial brush caching

Hotel baseline with surface caching: 639 objects, 2,376 charts, 438,428 samples;
CPU layout 5.15 seconds. Interior navigation was 45.3–52.8 ms, while refinement
was 125–150 ms. Many whole brushes were rejected by a single singular face.
Read-only diagnostics captured vertical faces whose U/V directions both lie
in the horizontal plane. These directions genuinely cannot parameterize that
vertical face; this is not fixed by inventing alternate preview axes. Valve's
published `TexinfoForBrushTexture` uses the supplied VMF directions directly:
https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/utils/vbsp/textures.cpp
The published code confirms the conversion formula, not all L4D2 compiler
handling of invalid faces. No broader engine-parity claim follows.

Partial caching keeps valid charts and matches original raster triangles using
material identity, inverse-transpose normals, coplanarity and full containment
in normalized edge half-spaces. Ambiguous or crossing triangles stay per-pixel.
Cached display uses those original triangles rather than reconstructed polygon
coverage. One current fallback Geometry owns its color/depth batches; full ray
geometry is untouched. Custom materials use original-ID provenance because
evaluated material IDs differ in real viewport callbacks.

Tests passed: five valid faces plus one singular face, all twelve original cube
triangles accounted for, mirrored/nonuniform transform, ambiguous matches,
unchanged-update reuse, persistent budget warning and source VMF preservation.
Core tests cover crossing, off-plane and opposite-facing triangles. Actual
mixed cached/uncached RenderEngine callbacks pass after the original-ID fix.
Independent static review verified drawing, matching, lifetime and identity
handling; no reviewer-run Blender claim.

Hotel rerun: 967 cached objects (954 partial), 3,066 charts, 999,998 samples,
CPU layout 8.30 seconds. Interior navigation improved to 28.8–31.9 ms;
overview to 231–274 ms. However refinement worsened to 192–241 ms indoors,
396–488 ms overview because every cached sample is evaluated. This is NOT a
general realtime result. Next bounded investigation: visibility-driven primary
chart refinement while preserving all ray occluders and bounce contributors.
Reports: hotel-surface-cache.json, hotel-partial-cache.json,
chart-rejections.json, partial-surface-cache.json in outputs/l4d2-preview.
