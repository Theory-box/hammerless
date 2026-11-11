# Live Lighting (realtime vrad)

Status: first working version on `feature/l4d2-preview-renderer` (2026-11-10). Light edits only; the map's geometry
is the last build's.

## What it is

The Hammerless light compiler kept running on the compiled map, so a light edit costs only what changed instead of
a bake. The GPU bake stays as it is for Build; this reuses its code for the preview.

- `hlvrad -live <name>` (`native/hlvrad/live_session.h`, part of `direct.c`; `live.c` for stdin and shared memory).
  The command protocol and the shared memory layout are documented at the top of `live_session.h`.
- `hammerless/core/livelight.py`: the client (start, `set` / `add` / `delete` / `go` / `cold`, `snapshot`).
- `hammerless/blender/livelighting.py`: the Lighting view's **Live Lighting** button. It matches the scene's lights
  to the map's light entities (each carries its object's name, `_hlobject`, written by every build now; older builds
  are matched by class and place), sends what changed and shows each result in the Baked Lighting view.

## In the L4D2 Preview render engine

With **Built-Map Lighting** on (Render properties, the default) the engine's Rendered viewport shows the last
build's map: its faces with the game's textures times the session's lightmaps, its props with their baked vertex
light, the sky behind (`l4d2preview.py`: `_vrad_ready` starts the session and loads the map, `_vrad_draw` draws it
with `lighttextures`). The engine starts the session itself and it ends 20 s after the last such viewport closes.
The scene isn't extracted at all in this mode, so big scenes cost nothing per edit. Without a build, or with the
option off, the engine's own experimental lighting draws the scene as before.

A render engine's viewport gives `POST_VIEW` overlays no scene depth (measured in 4.4: the sky overlay covered
everything whatever the engine wrote or left in the depth buffer), so the engine draws the sky itself
(`skyview.draw_sky`) and the Sky and Lighting overlays stay out of such viewports (`l4d2preview.vrad_view`).
Other overlays that test depth (nav, problems) draw over the map there.

Test: `tests/blender/check_engine_vrad.py` (a window; saves before/after screenshots).

## How an edit is lit

1. The light entities are parsed again and the old and new light lists compared. The changed lights are gathered
   alone at every sample, without supersampling, in one GPU job (old lights as one style slot, new as another); new
   minus old goes onto the samples. Bounced light is kept.
2. The faces those lights reach get their exact supersampled direct light (`BuildFacelightsGPUFaces`).
3. Bounce passes go on from the lightmaps as they are. The passes use the same rays every time, so they settle where
   a bake's passes settle, within the passes' tolerance. Only faces whose samples changed (and their smoothing
   neighbours) are filtered into luxels again.

`COLD` lights the edits as a bake from nothing; its lightmaps are a fresh bake's to the byte.

Speed-ups since the first version (all in `live_session.h`):

- Step 1 only asks the sample groups in clusters the changed lights can reach.
- Bounce passes are **change passes**: what the lightmaps changed by since the bounced light was gathered (a signed
  grid through `gi.comp`'s layer channel) gathered with 128 rays (`HLLIVEDELTA`, 0 = full passes only) and added to
  the bounced light. Their jobs are bigger than a bake's (a point's turn of the ray set needn't be a bake's there).
  On a map with vis, only groups in clusters that see a changed face are gathered (when that is under 3/4 of them).
- When change passes have nothing to add, a **full pass** (a bake's rays) replaces the bounced light of the groups
  change passes touched; each such group carries its place in the job a bake gathers it in (`pad0`), so its rays are
  the bake's. Change passes that add under two tolerances a face after a full pass need no full pass after them.
- A face's luxels are made again only when its samples moved a quarter of a tolerance since they were last made
  (all that waited are made when the lighting settles). Displacements no longer make every pass's luxels again:
  their luxels blend their own and their neighbours' samples only.
- `HLLIVEDBG=1` prints each step's time and how many groups a pass gathered.

## Measured (RTX 4090)

After the speed-ups: user's map copy (8 threads), a lamp moved and recoloured: first result 0.04 s, exact direct
0.23 s, settled 1.3 s (4 of 2.7M luxels over 2% from a fresh bake, worst 2.5%); a sun edit: first result 0.23 s,
settled about 4.5 s (the exact direct light of every face is 1.1 s of it). Hotel: a lamp edit: first result 0.05 s,
settled 3.4 s (was 21 s; 58 of 1.8M luxels over 2%, worst 5.4%). `COLD` is still a fresh bake's to the byte on both.
The session's start on the hotel takes 25 s to settle (a bake: 28 s): slow-converging maps spend it in change passes.

The first version's numbers:

User's map copy, 2.4M samples, 3 light entities, 1024 bounce rays, 4 threads, whole map every time:
bake 8.5 s; session start to first lightmaps 3.5 s; a lamp moved and recoloured: first result 0.22 s, exact direct
and a bounce pass 0.8 s, settled 2.3 s, every luxel within 0.6% of a fresh bake. In Blender (8 threads) a sun edit:
first result 0.35 s, settled 4.2 s.

Hotel map (1.8M luxels, 167 lights): bake 22 s; a lamp edit: first result 0.8 s, exact direct 2.7 s, settled 21 s
(21 passes). Slow-converging maps need the bounce of the change alone (below).

## Tests

- `tests/compile/live_session.py <bsp> --game <dir>`: a session against fresh bakes (settled: close; `COLD`: to the
  byte, before and after an edit).
- `tests/blender/check_live_lighting.py` (background) and `check_live_lighting_window.py` (a window: timer,
  depsgraph handler, texture upload). Neither saves the .blend nor writes the map.

## Not done

- Geometry edits (a session restart on a quickly recompiled map; the old lighting shown meanwhile).
- The bounce of the change alone: gather the lightmaps' difference with few rays on top of the kept bounce.
- Visible surfaces first; fewer sky rays while a sun is dragged.
- The exact direct pass can't be interrupted (it rewrites the facelights in place).
- A face a changed light reaches only between its main samples isn't supersampled again until `COLD` or a Build.
- Area lights, emissive surfaces, prop vertex light and leaf ambient aren't followed.
- A light with a style of its own (switchable, flickering) makes all the direct light again.
- In the engine, walls and props changed since the build don't show until a Build; props keep the build's light.
- Build could take a running session's lightmaps instead of baking again.
