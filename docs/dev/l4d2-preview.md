# Experimental L4D2 viewport renderer

Status: work in progress on `feature/l4d2-preview-renderer`. Not a finished
replacement for VRAD or the game's material shaders. No release authorized.

## Implemented

- Evaluated object-local geometry cache; transforms reuse extracted geometry
  and mesh BVHs. Instance transforms have a separate top-level accelerator.
- Point, spot and sun direct lighting; flat sky visibility; opaque GPU shadow
  rays using a software BVH (not hardware ray tracing).
- Progressive diffuse paths, configurable rays and one to eight bounces.
  Flat sky also refines when bounce rays are zero: nine directions per frame,
  18 frames per complete 162-direction cycle. Partial cycles are estimates.
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

- Alpha-tested/translucent geometry and shadows; area lights and material
  emission; normal maps, advanced Source shaders and model lighting parity.
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
