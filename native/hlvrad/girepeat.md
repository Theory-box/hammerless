# Persistent GI replay diagnostic

`hlvrad_girepeat.exe -gpu -gi 3 -girays 64 -noextra -girepeat 3 OUTPUT_PREFIX -game GAME_DIR INPUT.bsp`

Without `-girelight`, this is **GI replay only**, not an interactive renderer or a light-edit benchmark. It performs the normal initial BSP/material/shadow/direct-light preparation once, then retains the actual VRAD GI acceleration structure, face/luxel receiver groups, and direct sample snapshot across repeated solves. Each repeat restores the direct packed HDR lightdata and direct sample arrays, then executes the existing GI gather, radial reconstruction, RGBExp32 storage, and convergence loop without changing their arithmetic or seed.

Diagnostic mode returns before leaf/prop ambient work and before writing the input BSP. It requires a working GPU and an explicit different output prefix. Repeat count is 2..8, passes 1..64, rays 1..8192. Layers, remix, visfrom, onlylights, skymap, nobake, keeplight, and inherited RTTEST/TLTEST hooks are rejected. It needs no new visibility compile; the normal existing compiled-map visibility behavior is retained.

Outputs:

- `PREFIX.jsonl`: prepare, individual pass, and replay records; timing, work counts, packed-lightdata FNV1a32, and byte equality against the first replay. Initial scene/direct preparation time is also printed as `[girepeat] initial_setup_and_direct_seconds`.
- `PREFIX.N.bin`: little-endian uint32 header `(0x48474931, face_count, face_record_bytes, lightdata_bytes)`, then the current `g_pFaces` records, then raw HDR RGBExp32 lightdata. Face records include post-allocation light offsets and dimensions, so the artifact does not depend on the input BSP already having baked light offsets.

The normal compiler path remains one solve. Diagnostic snapshots and temporary replay arrays are freed after the bounded loop. GPU process lifetime remains the existing compiler lifetime. This diagnostic does not support changing geometry, prove realtime performance, or install anything into Blender. Compare all replay payloads against a separate normal GPU bake, and verify the input file hash remains unchanged.

## Bounded real intensity edit

Add `-girelight 0.5` to the command above. It requires exactly one style-0 point or spot light, no displacements, and a finite factor from 0 through 16. Other unchanged light kinds may remain. Replay zero uses the original light; before replay one, the selected actual direct-light intensity is changed and the normal GPU direct gather and final-light reconstruction run again. Subsequent replays use that edited state.

The GI acceleration structure and receiver groups remain resident. Before reuse, receiver counts/positions/normals and complete face/lightmap layout are checked against the original; a change terminates the diagnostic. This is a real direct-light recomputation, not multiplication of a previously baked lightmap. It is deliberately not yet arbitrary light motion, geometry edits, or a production incremental-update API.

Replay records include `state`, `direct_recompute_seconds`, and `equal_previous`; `equal_first` remains the literal comparison with the first completed artifact and is normally false after the predetermined edit. Compare edited artifacts to a fresh normal compiler bake with the same source-light intensity change. Preparation records include `relight_factor` (`-1` means disabled).

## Checked checkpoint (2026-11-09)

`tests/compile/check_persistent_gi.py` runs disposable copies, verifies source hashes,
requires complete replay records, and invokes `inspect_gi_replay.py` to compare
actual face lightmaps against a normal bake. `--reference-exe` permits comparison
with the unchanged production compiler; `--relight-factor` creates a separate
one-light fixture with edited source brightness for an independent reference.

On the RTX 4090, using 256 rays, a 12-pass ceiling, and one CPU thread:

| Fixture | Compared luxels | Actual intensity edit | Direct rebuild + GI | Result |
| --- | ---: | --- | ---: | --- |
| Closed room | 26,400 | Brightness halved | 20.1 + 48.6 ms | Exact reference equality; 6 GI passes |
| Shadow room | 73,690 | Brightness doubled | 53.8 + 164.1 ms | Exact reference equality; 7 GI passes |

These are individual warm-session diagnostic measurements, not statistical
performance claims or full viewport timings. They exclude cold setup, artifact
writing, Blender display, props/ambient finalization, and live edit extraction.
The edited state and subsequent replay both match separate normal GPU bakes in
all compared channels. An unedited convergence run also matches exactly.

Artifacts are under workspace `outputs/l4d2-preview/persistent-gi-relight-01`
and `persistent-gi-relight-shadow-01`. The comparison includes all styles/bump
pages of renderable faces containing static style 0, not every BSP subsystem.
Bad repeat counts, missing GPU mode, input/prefix collision, inherited tracing
debug hooks, and nonfinite edit factors were rejected without changing the input.

Still unproven: large-map update time, arbitrary light movement, dynamic chart
layouts, viewport integration, adaptive resolution, and performance against the
current preview at matched image quality. No addon installation was performed.

## Required viewport interaction policy

Orbiting, zooming, and editing pause heavy lighting calculation, not presentation
of existing lighting. Draw current geometry with its last available surface
lighting; never reuse an old screen image or depth buffer as if the camera had
not moved. Changed/new surfaces may need provisional lighting, while unchanged
surfaces retain their results. Resume refinement after interaction stops. This
policy is a requirement for the next viewport integration, not functionality
provided by this command-line diagnostic.

## Bounded controlled session

Use `-giserver 4 OUTPUT_PREFIX` instead of `-girepeat`; do not combine it with `-girelight`. `SOLVE` supports the existing fixed light set, including multiple lights and displacements. Only `SCALE` requires the single steady point/spot and no-displacement restrictions. The process prepares the original direct lighting, GI geometry and receiver groups once, then prints `HLGI_CAPABILITIES scale 0` or `HLGI_CAPABILITIES scale 1`, followed by the flushed `HLGI_READY`. A zero capability means only SOLVE/QUIT are supported; attempting SCALE fails before changing lighting.

Send one uppercase command per line on stdin:

- `SOLVE`: solve the current direct-light state.
- `SCALE factor`: set an **absolute multiplier of the original light intensity** and solve. A change runs actual direct recomputation; an unchanged factor reuses current direct data. Zero followed by one restores the original intensity without division or cumulative multiplication drift.
- `QUIT`: end without another solve. EOF also ends cleanly.

After each complete artifact and JSON record has been flushed, stdout contains `HLGI_RESULT index factor`. On normal session termination it contains `HLGI_DONE completed_count`. Other existing compiler log lines may appear; a controller must parse these exact marker prefixes. `SOLVE` and `SCALE` count toward the configured budget (2..8); the worker exits when it exhausts that budget. Invalid, overlong, nonfinite, out-of-range, or extra-token commands fail the process. Waiting for a command is excluded from solve timing. Artifact records carry the current `factor`; state is `original` when factor equals one, otherwise `edited`.

The controlling process must own a kill-on-close job and enforce timeouts/cancellation; this CLI intentionally creates no background service and does not implement its own idle timeout. No geometry rebuild, BSP write, or visibility recompile is performed between requests.

A controlled session may begin with `SCALE`; in that case its first artifact is already edited, and `equal_first` refers to that first artifact, not an uncomputed original-state result.

## Session and surface-display checks

`tests/compile/check_persistent_gi_session.py` owns one hidden worker and tests
SOLVE, SCALE, repeated SOLVE, return to original intensity, and QUIT. Each artifact
is compared with a fresh independent normal bake. Shadow-room requests matched
all 221,070 decoded channels; the half-intensity update took about 202 ms including
the command/artifact roundtrip, after about 1.20 s initial readiness. A separate
room test verified zero intensity and return to original, also exactly. These
are individual small-fixture timings, not large-map or viewport performance.

`tests/blender/check_persistent_gi_viewport.py` displays fixed compiled surfaces
using the existing lightmap shader. Two camera matrices produce different images
and depth while retaining identical GPU lighting data and one upload. A lighting
replacement is the only event that uploads a new atlas.

`tests/blender/check_persistent_gi_live.py` connects that display to one bounded
worker. GPU drawing and completed-result installation occur on Blender's main
thread; a pipe reader only queues text. Pending-result captures retain the old
atlas through two camera views; completed SOLVE/SCALE results are then installed.
The worker is owned, has phase deadlines, and exits after QUIT. Independent review
covered state restoration, protocol, process cleanup, and retained display.

Checked artifacts: workspace `outputs/l4d2-preview/persistent-session-01`,
`persistent-session-zero-01`, `persistent-viewport`, and `persistent-live-01`.
This connection fixture does not measure continuous navigation or prove in-flight
pause/cancellation: QUIT is processed between solves. It does not implement live
mesh edits, full materials/props, or changes to the installed addon.
