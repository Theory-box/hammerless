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
- Gravity, drag and random forces consumed by Movement Basic.
- Random initial velocity and coherent velocity noise driven by birth time and
  position. Noise honours per-axis bounds, absolute/inverted-absolute masks,
  coordinate offsets and scales; control-point transforms remain unsupported.
- Roll spin with a linear slowdown to minimum speed, measured in seconds.
- Radius/colour changes, random alpha fades, lifespan decay and combined
  alpha fade/decay. Random fade-out duration is measured backward from death.

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

## Coverage and validation

Run `python tools/audit_particles.py --game GAME_ROOT --output REPORT.json
--seconds 3 --textures` with the flags on one line. It scans manifest definitions,
runs each system with a bounded particle count, and optionally decodes referenced
atlases. Reports include parse/simulation errors, missing rules ranked by number
of affected definitions, unmapped operator and system parameters, child gaps,
and missing materials. Every effect remains labelled approximate, even if it
has no recorded gaps. Zero smoke-test failures does not mean zero visual errors.

Unit tests: `python -m unittest tests.unit.test_particlepreview
tests.unit.test_particlesheet tests.unit.test_particlenoise` (one line). Blender lifecycle test:
`blender --background --factory-startup --python tests/blender/test_particle_preview.py`.
GPU test requires a separate window:
`blender --factory-startup --python tests/blender/test_particle_gpu.py`.
It uses synthetic textures and asserts upload, UV orientation and tint; no game
installation is needed. GPU readback uses `Buffer.to_list()` because direct
NumPy buffer conversion produced corrupt images in Blender 4.4 on the test host.

## Next gaps

Scalar/vector remapping and oscillation, operator strength/time
envelopes, live control points and parent inheritance, secondary sequences,
trail/rope renderers, collision, and non-sprite material shaders remain missing.
Unsupported fields stay visible in reports. Missing atlas sequences are skipped
and reported rather than displaying the entire sheet as one particle.

Use the generated priority list to choose shared operators that unlock multiple
effects. Compare representative effects visually with the game before claiming
fidelity; keep numerical smoke checks separate from that comparison.
