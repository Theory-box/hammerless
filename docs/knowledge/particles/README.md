# Particle preview system

Last checked: 2026-10-27, release 0.20.20.

## Use and export

In Hammerless > Particles, browse the installed game's effects and click an effect
to place an info_particle_system at the 3D cursor. Auto-preview selection defaults
to on. Selecting a particle plays it; changing selection stops its preview. Restart
Preview restarts bursts, and Stop remains stopped until selection changes. Finished
bursts repeat after .75 seconds; continuous emitters and pending delayed emitters
are not restarted. This replay behavior exists only in Blender.

New markers are empties. Generated legacy crossed cards are converted with entity
properties, parenting and object references retained. Legacy objects with animation,
constraints or modifiers keep their identity and receive a wire marker. Files are
not saved automatically. Normal export handles the empty as a point entity, retains
its effect_name/control-point keys and defaults start_active to 1. Build & Play uses
the game's particle renderer, not our preview code. No converted particle simulation
or preview mesh is baked into the map.

Preview simulation, raycasts, drawing and marker migration pause while Hammerless
build/export/nav/background-bake jobs or Blender render/object-bake jobs are active.
Particle entities have no brush geometry; Cycles baking reads the compiled BSP in
a separate bake scene. Preview playback does not contribute lightmap faces or shadows.
A face-vectors-parallel-to-face-normal warning concerns exported face texture axes,
not particle point entities.

Only the selected effect tree plays. Global playback, shared multi-instance budgets
and distance-based update scheduling are future work, not released features.

## Architecture and distribution

The implementation is independent Python/NumPy plus Blender GPU shaders. Installed
PCF/DMX definitions and VMT/VTF assets are read from the user's game content. Valve
particle runtime binaries, source implementations and particle assets are not added
to the release. The package contains our readers and simulator; the user supplies
the installed game. Do not turn research examples into redistributed game assets.

| Module in hammerless/ | Responsibility |
|---|---|
| core/particlepreview.py | Rule registration, definition loading, audit and fixed-step simulation |
| core/particleeffect.py | Parent-first effect trees, child delays, completion detection |
| core/particlecontrol.py | Rigid CP frames, inheritance, authored CP placement |
| core/particlefields.py, particlenoise.py, particleoscillation.py | Fields, deterministic noise and oscillation |
| core/particlesheet.py | VTF sheet sequences, duration sampling and atlas loading |
| core/particlematerial.py | Material profiles, cropping, blending and unsupported-key diagnostics |
| core/particlegeometry.py, particlerope.py | Batched cards, trails, ribbons and UVs |
| blender/particlepreview.py | Selection watcher, transient simulation, GPU draw and viewport trace |
| blender/particlebrowser.py | Placement, browser, marker migration and preview controls |

Simulation advances in fixed 1/60-second steps. The viewport timer targets 60 Hz;
GPU drawing and scene cost can lower delivered FPS. Trees are bounded to 16 systems
and 256 particles per system, further limited by authored max_particles. Atlas
textures are capped to 1024 pixels in preview. Control-point bindings use cpoint1
through cpoint63 names. Bone attachments and full game collision are not reproduced.

## Rendering facts and important corrections

Sprite modes 0/1/2 preview camera-facing/upright/flat cards. Atlas UVs use separate
current/next rectangles; interpolation never slides UVs across atlas cells. Secondary
sequences use the authored `second sequence animation rate` key. Geometry uploaded
to Blender must be contiguous float32; float64 raw buffers previously produced
incorrect rendering. Sprite geometry and sheet sampling operate in batches rather
than Python loops over every particle.

Fire: env_fire_tiny_smoke includes a fire_molotov_crop material with `$cropfactor
[0.5 1]`. Crop both the card and frame UVs around the center. Ignoring this samples
padded/neighboring atlas regions; changing all textures' UV rotation is not a fix.

Water: fire_hydrant has a rope, an impact CP and multiple spray children. Connecting
random birth positions before its path exists produces giant zigzags. Missing path
endpoints now suppress that rope. A quadratic path approximation and throttled
viewport raycast supply the curve and impact point. Coincident endpoints are removed
before spline subdivision to avoid artificial folds. These are preview approximations;
viewport geometry/collision groups differ from compiled game collision.

The first rope UV assumption stretched one texture row into long lines. The next
density interpretation compressed hundreds of copies. The accepted plain-water
preview instead preserves source texture proportions using nominal ribbon width:
repeat length = 2 * initial radius * texture height / texture width. Thus a 128x256
texture on a 48-unit-wide ribbon occupies 96 units along the path. U crosses the
ribbon and V follows arc length. Animated width does not continually retile it.
This is explicitly a base-texture preview substitute, not verified native warp
material semantics. User reported it working on 2026-10-27. No refraction is used.
Other ribbon materials retain the separate density approximation and need comparison.

Material approximations include dual-sequence blending, frame interpolation,
self-additive glow, maximum-luminance blending and secondary zoom. Full depth blend,
lighting/fog, visibility proxies, distance alpha/outlines and scene refraction are
not implemented. The panel lists the first five limitations; the console and audit
retain fuller diagnostics. Unsupported keys must not be silently treated as supported.

## Evidence and testing

- 76 focused particle unit tests passed for the final water-aspect change.
- Background Blender checks passed for registration/cleanup, placement/replacement,
  default game activation, marker migration, retained references and entity export.
- An installed-library bounded audit covered 1,785 roots with no parse/simulation/
  geometry failures before the final aspect substitution. Audit success does not
  mean every effect looks correct; unsupported rules remain reported.
- A 30-frame groundfog_street CPU profile dropped from 4.101 seconds to .094 seconds
  after batching (about 137 to 3.1 ms/frame). It excludes GPU upload and scene draw.
- User tested the addon in Blender and confirmed the final water texture scaling.
  No full-library visual parity or whole-map performance claim is justified.
- Early game comparison images had lingering fog/HUD contamination and late spark
  captures. They are exploratory evidence, not golden baselines.

Run `python -m unittest discover -s tests/unit -p "test_particle*.py"` for focused
checks. `tools/audit_particles.py --game GAME --output REPORT --trees --geometry`
reads the user's library. `tools/profile_particles.py --game GAME` profiles fog CPU
work. `tools/particle_preview_lab.py` opens a disposable interactive Blender lab only
when requested. `tools/render_particle_samples.py` and `tools/compare_particle_samples.py`
provide fixed-camera reports with definition fingerprints; neither declares parity.
`tests/ingame/capture_particle_reference.py --run` explicitly launches a temporary
game probe and must not run while the user is working or has forbidden screen use.

For detailed development history, see [particle-preview.md](../../dev/particle-preview.md).
