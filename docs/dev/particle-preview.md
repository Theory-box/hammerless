# Independent particle preview

Experimental implementation on `feature/particle-preview`. It reads the user's
installed game's PCF definitions and VMT/VTF assets at runtime. No game assets,
Valve simulator binaries, or third-party simulator implementation are bundled.
The existing static particle browser remains available.

The selected effect's Preview Animation button starts a transient viewport
overlay. Stop, changing selection, loading a file, or unregistering the addon
cleans it up. It does not create map objects or save the blend file. Preview is
bounded to 16 systems and 256 live particles per system.

## Implemented approximations

- Continuous and burst emission; box/sphere/offset positioning.
- Random lifetime, radius, colour, alpha, rotation and animation sequence.
- Animated atlas frames, camera-facing cards, horizontal yaw flips, tint and
  alpha/additive blending.
- Velocity-aligned animated sprite trails, length limits and fade-in, yaw
  orientation and secondary opacity. Multiple supported renderers on a system
  each produce their own geometry.
- Gravity, drag and random forces consumed by Movement Basic.
- Random initial velocity and coherent velocity noise driven by birth time and
  position. Noise honours per-axis bounds, absolute/inverted-absolute masks,
  coordinate offsets and scales; control-point transforms remain unsupported.
- Roll spin with a linear slowdown to minimum speed, measured in seconds.
- Random rotation speed and basic rotation integration. Random property
  exponents, sphere directional bias/local velocity and radius bias/easing.
- Shared field access for initial/runtime scalar remapping, initial scalar-to-
  vector remapping, and noise-to-scalar remapping. Range gates, emitter windows,
  reversed/equal ranges and initial-value multipliers are handled. Unsupported
  attribute IDs remain reported, including secondary sequence attributes.
- Lifetime From Sequence uses the selected atlas sequence's frame count and
  authored FPS. Missing timing/sequence data produces a runtime warning.
- Radius/colour changes, random alpha fades, lifespan decay and combined
  alpha fade/decay. Random fade-out duration is measured backward from death.
- Scalar oscillation of radius, roll, yaw and both alpha fields; vector oscillation of position
  and RGB. Per-particle frequency/rate and start/end windows are stable across
  frame partitions and particle removal. Lifespan-relative timing is supported.

Operator semantics were checked against the public
[particle operator documentation](https://www.dota2.com.cn/wiki/Dota_2_Workshop_Tools/Particles/Particle_System_Operators.htm)
and parameter names against installed L4D2 definitions. Maths, easing, omitted
parameter defaults and random distributions remain independent approximations;
these checks do not establish Source parity.

`particlenoise.py` is an independently written four-dimensional value-noise
implementation, using hashed lattice values and quintic interpolation. It does
not reproduce Valve's noise function or permutation tables. The same position,
birth time and seed yield the same velocity regardless of draw-frame timing or
how particles are batched. Defaults and coordinate-space behaviour remain
approximate; fields for unsupported CP transforms stay in the coverage report.

Oscillation integrates a sine rate analytically. Radius/colour receive cumulative
offsets because they reset to initial values each tick; position/rotation receive
only that step's change. This is a preview approximation, especially when other
operators also affect the same field. Lifetime oscillation remains unsupported
and is reported by value, not merely by parameter name.
Attribute IDs were checked against Valve's public
[particle attribute declarations](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/public/particles/particles.h);
the SDK implementation is not included or linked.

Trail length is approximated as speed times the particle's trail-duration field,
clamped by renderer length settings. If the particle has initial velocity but
no movement operator, that velocity can still stretch its trail. This is not a
history ribbon and does not itself implement rope subdivision; the separate rope renderer handles connected ribbons. Sample comparisons with
the game are still required for exact shape, defaults and material blending.

Remap outputs for angular attributes are interpreted as degrees and stored in
radians; multiplier mode is dimensionless. RGB outputs can exceed one for
authored bright effects. The field abstraction also manages creation time and
initial-value snapshots so changing one particle does not corrupt another.

## Coverage and validation

Run `python tools/audit_particles.py --game GAME_ROOT --output REPORT.json
--seconds 3 --textures` with the flags on one line. It scans manifest definitions,
runs each system with a bounded particle count, and optionally decodes referenced
atlases. Reports include parse/simulation errors, missing rules ranked by number
of affected definitions, unmapped operator and system parameters, child gaps,
and missing materials. Every effect remains labelled approximate, even if it
has no recorded gaps. Zero smoke-test failures does not mean zero visual errors.

Unit tests: `python -m unittest tests.unit.test_particlepreview
tests.unit.test_particlesheet tests.unit.test_particlenoise tests.unit.test_particleoscillation
tests.unit.test_particlefields tests.unit.test_particlegeometry`
(one line). Blender lifecycle test:
`blender --background --factory-startup --python tests/blender/test_particle_preview.py`.
GPU test requires a separate window:
`blender --factory-startup --python tests/blender/test_particle_gpu.py`.
It uses synthetic textures and asserts upload, UV orientation, tint and actual
production trail geometry on the GPU; no game
installation is needed. GPU readback uses `Buffer.to_list()` because direct
NumPy buffer conversion produced corrupt images in Blender 4.4 on the test host.
Vertex attribute arrays are explicitly contiguous float32; float64 arrays also
produced missing geometry through Blender's buffer upload path.

For automated visual samples, run a separate Blender with `--factory-startup
--python tools/render_particle_samples.py -- --game GAME_ROOT --output DIRECTORY
--effects gas_smoke small_sparks --times 0.05 0.1 0.25 2` (one line). It saves
PNGs and a report with particle counts, visible-pixel counts and coverage gaps.
Frames are independently fitted to their bounds for inspection, not a
scale-consistent animation. Empty frames are expected before emission or after
short bursts die. These local diagnostics read installed game assets; they are
not bundled with the addon. The tool never opens or saves the user's blend file.

## Next gaps

Remaining attribute targets, operator strength/time
envelopes, bone attachments and control-point hierarchies, secondary material refinements,
collision and non-sprite material shaders remain missing; rope rendering is now an approximation.
Unsupported fields stay visible in reports. Missing atlas sequences are skipped
and reported rather than displaying the entire sheet as one particle.

Use the generated priority list to choose shared operators that unlock multiple
effects. Compare representative effects visually with the game before claiming
fidelity; keep numerical smoke checks separate from that comparison.

## Control points and child systems

`EffectSimulation` advances a bounded parent-first effect tree on one fixed clock.
Children inherit parent control points and can emit from live parent particles;
child delays are cumulative. Cycles, missing children and system limits produce
warnings. Parent-dependent births without a live parent are suppressed.

The viewport resolves cpoint1..63 by object name or targetname. Missing/ambiguous
targets are reported. Moving emitters affect new particles; old particles stay
in their original world frame unless a lock operator follows a control point.
No objects, keyvalues, cursor or viewport preferences are written by preview.
The starting frame and unit scale stay fixed for a preview session.

Supported approximations include translation/rotation locks with lifetime fades,
control-point velocity inheritance, relative authored point positions, particle
centers, child control points, straight sequential paths and inverse-power pull.
Rotation interpolation, parent selection and force defaults are independent
approximations. Lock rotation moves positions but does not rotate particle roll
or velocity. World-space authored CP positions, distance fades, bone attachments,
parent CP hierarchies, path bulges and child group IDs remain unsupported.

Add `--trees` to the audit to validate whole synchronized trees. Run
`python -m unittest tests.unit.test_particlecontrol` for deterministic motion,
birth, delay and isolation checks. Blender integration uses a disposable factory
scene: `blender --background --factory-startup --python-exit-code 1 --python
 tests/blender/test_particle_controlpoints.py` (one line). It checks named targets,
ambiguity, moving emitters, free/locked particles and non-mutating cleanup.

Validation for the control-point batch: 50 unit tests passed; Blender 4.4.3
factory-scene integration, lifecycle and GPU tests passed. All 1,785 installed
L4D2 effect trees ran for three seconds with no parse or simulation errors.
343 roots reported runtime warnings (including absent external CPs, child-only
systems tested as roots, bounded tree limits and missing sheet data). Six sample
frames rendered; smoke and sparks were visually inspected. These are simulator
checks, not a comparison against game captures. Missing-rule occurrences fell
from 2,345 to 1,645; that count is not a visual-completeness percentage.

## Ribbon renderer and secondary sequence storage

`render_rope` connects particles in birth-ID order, never camera depth order.
It generates a camera-facing ribbon with shared edge vertices, interpolated
radius/colour/secondary opacity, independently implemented cubic Hermite
subdivision (capped at 16), and scrolling repeated UVs. Coincident points are
collapsed and view-aligned tangents have fallback axes. Texture density uses
texel_size times decoded texture height; downsampling, material UV transforms
and Source's exact spline/default behaviour remain approximations. The shader
wraps only the rope's longitudinal coordinate; sprite atlas UVs stay unwrapped.

`Sequence Two Random` and the authored `sequence_number 1` value now have a
separate integer array preserved across births and deaths. Dual-sequence material
blending now has a separate approximate material profile (see below); storing
sequence choices alone does not establish correct material rendering.

Validation includes deterministic ribbon topology, particle removal, coincident
points, camera-aligned paths, bounded subdivision, scrolling, texture density,
secondary sequence lifecycle and actual GPU ribbon pixels. Installed-game sample
renders include drainage streams, hydrant water and rain. Missing control points
and water distortion shaders visibly limit fidelity; these samples are not
claimed to match the game. The implementation uses independent geometry maths,
not third-party simulator source.

## Material profiles and dual-sequence blending

The viewport and sample tool now load resolved VMT settings separately from the
texture atlas. Independent UVs animate each sequence. Preview modes 0/1/2 use,
respectively, an RGBA average, secondary RGB with primary alpha, and secondary
over primary. These are independent approximate compositing rules, not a claim
that every SpriteCard variant matches Source. Unsupported modes are reported.
The shader also applies overbrightfactor, alpha and alphatest/reference. Ropes
keep their base-texture path and report dual-sequence materials as unsupported.

Refraction is explicitly reported as a base-texture fallback: it needs captured
scene colour/depth and distortion textures, which this overlay does not yet
provide. Depth blending, addoverblend and distance-alpha shaders remain reported
material gaps. Frame interpolation, maximum-luminance blending, zoom and addself
now have independent preview approximations described below. Ground fog samples produce visible dual-sequence smoke; effects
requiring a parent still need that parent, rather than inventing root emissions.

`tools/audit_particles.py --geometry` now checks bounded finite vertex/UV/colour
arrays and resolved material profiles, alongside the existing simulation audit.
GPU tests exercise distinct atlas regions and unequal alpha values for all three
preview blend modes, brightness/opacity and alpha rejection. None of these checks
replace a visual comparison against the game. No game materials are bundled.

Material-batch validation: 58 unit tests passed, Blender lifecycle passed, and
floating-point GPU tests passed (including brightness above one). A .25-second
whole-library audit covered 1,785 roots, 3,275 geometry batches and 235 material
profile cache entries with no parse/simulation/geometry errors. Eight sample PNGs
were generated; fog was visually inspected. Empty parent-dependent standalone
samples are reported, not counted as successful visual reproduction.

## Animation smoothing and glow

Sheet sampling returns current/next frames and a fractional weight, including
nonuniform durations, FPS timing, loop wrap, clamped terminal frames and lifetime
fit. `$blendframes` enables separate GPU interpolation for both sequences. Each
frame uses its own atlas rectangle; coordinates never interpolate through adjacent
sprites. Maximum-luminance preview uses a componentwise maximum of weighted
samples. Secondary zoom crops each rectangle around its own center, progressing
from 1 to the authored zoom over particle lifetime. These latter rules are
independent approximations awaiting game comparisons, not established engine
semantics. Shrinking below 1 is not implemented.

Self-additive preview uses alpha-masked premultiplied colour, boosted by
`1 + addself`, with unchanged coverage alpha. This keeps transparent sprite
backgrounds from glowing. Additive/addself combinations fall back to additive
and report the ignored setting. Exact HDR appearance, bloom and Source's
material-specific response remain outside this approximation.

Tests cover nonuniform timing, loop/clamp/FPS/fit boundaries, per-frame UVs,
secondary zoom staying inside its atlas rectangle, GPU linear and maximum frame
blending, HDR brightness and premultiplied glow over a coloured background.

Smoothing-batch validation: 62 unit tests, the GPU suite and Blender lifecycle
checks passed. The .25-second audit again covered 1,785 effect trees and 3,275
geometry batches without errors. Eight sample images rendered. Visual inspection
caught unmasked glow in an intermediate implementation; it was fixed and a GPU
regression now asserts that transparent texture pixels preserve the background.
Final smoke samples were inspected after that correction.

## Orientation fixes and standalone visual testing

Sprite mode 0 faces the camera, mode 1 stays upright around the control point's
vertical axis, and mode 2 lies in its XY plane. The authored orientation control
point rotates that basis. Unknown modes and missing points remain reported.
The real PCF key `second sequence animation rate` now overrides the primary
animation speed; a zero rate holds the secondary frame. Geometry tests cover
flat/vertical planes, camera independence, CP rotation and independent UV timing.

Users can run the branch without replacing their installed addon:
`blender --factory-startup --python tools/particle_preview_lab.py -- --game GAME`
(one line). This opens a disposable scene with one emitter. In the sidebar's
**Preview Lab** tab, search all installed effects, pick presets, Restart short
bursts or Stop playback. Orbit/zoom normally. The branch is imported directly;
no map is opened or saved. `--smoke-test` automatically switches five presets,
checks the simulation timer and stops cleanly in a separate Blender process.

`render_particle_samples.py --camera tests/fixtures/particle_reference_camera.json`
provides fixed perspective instead of per-frame fitting. `--reference-report`
uses an L4D2 capture report's camera, names and measured times. Reports contain
PCF fingerprints. `compare_particle_samples.py` makes a local side-by-side HTML
review and flags metadata mismatches; it never declares visual parity.

The first live game captures were exploratory, not golden references: lingering
fog and HUD overlays contaminated later frames, and a short spark capture was
late. The capture harness now reloads between effects and shortens spark timing;
those capture changes await the user's visual testing. Do not use the original
exploratory images as automated pass/fail baselines. Further game visual testing
is user-led; automated checks remain local and separate from the user's map.


## In-addon comparison workflow and batching

Open Hammerless > Particles, choose an effect at the 3D cursor, and keep
Preview when placing enabled. The selected emitter animates immediately;
Restart Preview replays short bursts and Stop ends the transient animation.
Placed entities retain their normal `info_particle_system` export and default
`start_active=1`. Build & Play loads that same effect through the game. Preview
remains selected-object only; it does not simulate every emitter in the map.
Restart Blender after installing changed Python modules; no live reload or
user scene edits are performed by the installer.

Sprite geometry and atlas sampling now operate on NumPy batches grouped by
sequence. CP orientation is computed once per renderer, not once per card.
A deterministic 40-particle comparison covers all three orientations, secondary
sheets, flips, yaw, nonuniform durations, looping/clamping, lifetime fit and FPS
sampling against the scalar path. 69 unit tests and background Blender placement,
replacement and lifecycle checks pass. All 1,785 installed definitions pass the
bounded tree/geometry audit. No new game or visual fidelity claim is made.

`tools/profile_particles.py --game GAME` profiles 30 fog frames after five
seconds warmup with the existing particle limits. On this machine, profiled CPU
simulation+geometry time fell from 4.101 seconds to .094 seconds total (about
137 to 3.1 ms/frame). This excludes GPU upload, viewport drawing and scene cost;
it is not a measured viewport FPS. The viewport timer now targets 60 Hz rather
than 30 Hz, subtracting simulation work from its scheduling delay.
