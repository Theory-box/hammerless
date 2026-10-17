# Partial bakes and keep light, sky light from the sky, the Cycles bake, and the Baked Lighting view

## Partial bakes: `-nobake` (Control Volumes)

Status: `done` (0.12.x, 2026-10-09; Control Volumes replaced No Bake Volumes in 0.13.x). Code: `hammerless/core/lightvolumes.py` (`no_bake_text`), `native/hlvrad/nobake.c`.

A Control Volume with *Light Baking* ticked tells hlvrad where not to work out light. *Not Inside* skips the inside; *Only Inside* bakes only the inside. The volumes go to hlvrad as text (`<map>.hlnobake`), one convex brush a line: `<invert> <planes> nx ny nz d ...` (inside where `n.p <= d` for every plane; a volume without a closed brush acts as its box). A point isn't baked when it's inside a non-inverted volume, or when inverted volumes exist and it's inside none.

What happens to unbaked points: they get the map's ambient colour (the `light_environment`'s ambient) and aren't traced, so they cost next to nothing; surfaces there still cast shadows; partly covered faces are cut at the volume's edge; prop vertices and leaf ambient samples inside are skipped (rooms wholly inside skip their ambient); bounced light from skipped surfaces is their ambient colour. With bounce off, everything outside is byte-identical to a full bake. The volumes count as a lighting setting (changing them relights without recompiling geometry) and aren't written into the map.

The **Bake Lighting** dropdown: Whole Map; What the View Sees (the viewport's frustum out to *View Distance*, default 3000 units, as an Only Inside volume); Selected Objects (around the selection's bounds). Until 6f38061 (2026-10-16) it also listed each Only Inside volume by name and Bake Lighting ignored those volumes unless one was picked; now **every enabled Control Volume applies to every build and every bake** (Build, Build & Play and Bake Lighting bake only inside the Only Inside volumes, or the whole map if there are none; 0.13.x fix before that: for a while builds baked the whole map). A bake made by Bake Lighting records its area (`CompileJob.light_as`), so a following Build & Play with nothing changed reuses it (0.13.x fix: it used to bake again because the area counted as a changed setting).

**Enabled** (6f38061): a Control Volume has a keyvalue `enabled` (on by default; older files without it read as on). Off, `control.settings_of` returns None, so the nav, spawns, the nav analysis, visibility, light baking and the sound trace all ignore it (they all read `settings_of`); the volume stays in the map. A *Control Volumes* panel lists every volume with its checkbox, mode and aspect icons, selects one on click, and adds new ones. Tests: `tests/unit/test_core.py` `TestControlVolumeEnabled` (a disabled volume does nothing for each aspect in both modes), `tests/blender/run_tests.py` `test_control_volumes` (add, list, toggle; the Bake dropdown's choices).

Measured: a view holding a quarter of a real map's surfaces lit in 1.6 s instead of 3.2 s.

## Keep light: `-keeplight`

Status: `done` (0.13.x). Code: `native/hlvrad/keeplight.c`; `CompileJob._keep_last_light` copies the last build's `.bsp` to `<map>.keep.bsp` when the build starts (only if it has lighting; otherwise the option is dropped with "No earlier lighting to keep").

What isn't baked takes the previous build's lighting instead of the flat ambient colour, wherever the previous build has **exactly** the same thing:
- a face with the same corners (in order), lightmap size, place and axes, styles, bump mapping, and for a displacement the same displaced vertices: its luxels and averages;
- a leaf with the same bounds, when the old map has the same leaves: its ambient samples;
- a static prop with the same record (place, angles, flags...), model name and model checksum: its `.vhv` colours.

Anything else, and anything inside a Not Inside volume, gets the ambient colour (Not Inside wins over kept light). The old map is untrusted input: every read is bounds-checked. Bounce inside the baked area comes from the real lighting around it. Fable's audit (0.13.x) made the matching stricter (full face key, model checksum, bounds checks).

Measured on a real map: 6,410 faces outside the baked area kept exactly, lighting in 1.7 s.

## Sky light from the sky: `-skymap`

Status: `done` (commit f8ced24, 2026-10-08; HDRI orientation fix 0.11.x). Code: `hammerless/core/skylight.py` (`make_skymap`), `native/hlvrad/skymap.c`, `shaders/skymap.glsl`. The first non-vrad feature of hlvrad.

vrad lights a map from the sky with one colour (`_ambient`). *Environment > Sky Light* offers One Colour (vrad's way), From the Skybox (the map's own sky), From an Image (an HDRI: the picked one or the World's Environment Texture, with a Sky Rotation).

How: Blender exports `<map>.hlsky_src.npy`; `skylight.py` turns it into a small equirectangular panorama (`MAP_W, MAP_H = 128, 64`) in the game's axes: column `u` looks along `atan2(y, x) = 2 pi u - pi`, row `v` looks up at `pi/2 - pi v` (row 0 straight up); blurred by `BLUR_DEGREES = 9` (about the spacing of vrad's 162 sky rays, 4 pi / 162 sr each, so a bright spot doesn't make blotches); normalised so a floor under open sky gets brightness 1 (cosine-weighted over the upper hemisphere). hlvrad multiplies by the luminance of the `_ambient` intensity, so the overall level stays the Sky Light Brightness with the sky's colours. The file for hlvrad: `"HLSK"`, width, height, then float RGB. Each sky ray then takes the colour where it looks (bilinear, wrapping round the horizon): on faces, supersampling, bounce, static and detail props, and the leaf ambient. Without a sky picture the output is unchanged and byte-identical to vrad. If the picture can't be prepared, the build says so and lights with one colour (`_shaky` set, so it isn't recorded as final).

**Skybox facts measured on L4D2's skies** (2026-10-08): faces `rt` looks +X (its right is -Y), `ft` -Y, `lf` -X, `bk` +Y, `up` (right -Y, top -X); the sides join seamlessly going `rt -> ft -> lf -> bk`, and the top's edges meet each side's top row. Compressed HDR skies are `rgb x 16 x alpha`; side faces are "scale 1 2" = the upper half, stretched to double height. The HDRI was once turned half a turn relative to Blender's view (fixed 0.11.x).

Sky light can be used with no sun (*Add Sun if Missing* off): the build adds the sky light alone, the entity's sun part at 0 (0.13.x); imported maps with no sun of their own get it too.

Not done: the absolute orientation from the skybox was measured from textures, not yet checked in game (expected: rural02's orange glow toward +X / yaw 0); Sky Light from an Image doesn't change the visible sky (no skybox texture writer).

## The Cycles bake (Light Compiler: Cycles)

Status: `done`, optional, predates hlvrad (branch `cycles-bake`, 2026-10-07; 0.10.x). Code: `hammerless/blender/cyclesbake.py`, `hammerless/core/lightbake.py`. Valve's vrad still runs first by design (it lays out every face's lightmap, writes the world lights, leaf ambient, detail and static prop light, and switchable styles); then a background Blender (half a second to start; Cancel stops it; 0.12.x, before that it froze Blender) builds a temporary scene from the compiled faces, lit like vrad lights the map, bakes, and `lightbake.py` writes the values over vrad's static lightmaps in vrad's own layout.

**Units, no fitting** (calibrated 2026-10-07): a diffuse bake (light arriving on a white surface) x 100 pi = vrad's units. A sun of strength 1 gives E = 1 W/m² -> bake 1/pi; vrad gives brightness 100 for it. So sun strength = brightness / 100; sky radiance = ambient / (100 pi); a point light of P watts gives P / (4 pi d²) at d metres -> P = brightness x 400 pi / upm² (vrad: brightness at 100 units, inverse square). vrad's light value: `(c/255)^2.2 x 255 x brightness/255`.

**Pipeline matched to vrad's** so seams agree: samples are one-luxel cells `[s, s+1)` clipped to the face, lit at the balance point; a luxel = `BuildLuxelRadial`'s weighted mean over own and `PairEdges`-neighbour samples; luxels with no nearby sample are black in vrad too. The bake is Combined limited to diffuse (Cycles denoises only Combined), on a white target surface invisible to rays, with an occluder copy 1 unit behind carrying the real colours (keep `visible_camera` on or the bake is 0). Overlapping coplanar faces handled the same way.

| Measurement, a real level vs vrad | Result |
|---|---|
| Correlation / brightness | 0.976 (0.956 in the first prototype), within 1-3% (prototype: 1.01-1.03x, mean diff 7.5%) |
| Stitch Seams (on; least squares along shared edges within 45 degrees) | mean difference across edges 1.6% vrad, 0.02% stitched |
| Samples | 256 already within 0.3% of 4096; default 1024 |
| Denoise (off by default) | OpenImageDenoise smears the packed atlas: 7.7% off vs 1.3% raw at 256 spp |

Pitfalls recorded: `dface.planenum` already faces the face's way, do **not** negate it for the side byte (that flipped 38% of faces to black; an earlier "48% buried faces" conclusion was this bug). Lights exported without attenuation keys mean vrad constant attenuation (infinite reach); the branch set `_quadratic_attn 1` by default and Blender W -> brightness `P x upm² / (400 pi)`. Displacement lightmaps were laid out transposed in the view and the bake (fixed 0.11.x). A skipped or failed Cycles bake was once recorded as done (fixed 0.11.x).

## The Baked Lighting view

Status: `done`. Code: `hammerless/blender/lightview.py`, `hammerless/core/proplight.py`, `lightmix.py`. Draws the last build's lighting over the viewport from the compiled map; nothing is added to the scene or saved; turning it off frees it. It shows the compiled map's own faces (after moving a wall the light stays where the compiled wall was until the next build; the panel says the scene changed).

| Mode / option | What |
|---|---|
| Lighting Only | the baked light alone (leaks, harsh shadows, dark corners) |
| Lit | baked light multiplied over the viewport (Solid view, Flat lighting, Texture colour) |
| Textured | the game's look: each surface's game texture x its light, props with textures and vertex light, see-through parts cut out; textures go straight to the GPU |
| Exposure | stops |
| Props | static props with their baked vertex light (`.vhv`): per body part, model, LOD, mesh, strip group, the `.vtx` vertices' colours, 8-bit BGRA in the game's vertex gamma `(light)^(1/2.2) / 2`, put back on LOD 0 as linear light in lightmap units; works for vrad's and hlvrad's bakes |
| X-Ray | through walls |
| Live Light Mixing | follows light colour/brightness changes (layers-and-styles.md) |

Fixes recorded: it kept showing after opening another file, could show one map's lighting over another, re-read the map every second after an unlit build (0.11.x). A shown lighting preview reloads when a build ends instead of at the viewport's next redraw (196457a, 2026-10-16; checked in a Blender window: a second Build with the sun changed shows the new bake). The view pulls its faces toward the camera by a fixed depth bias of 2e-5 of clip depth (`lightview.py`), which far away is more than the nav overlay's 2-unit lift, so the lit floor hid the nav beyond some distance; the nav overlay now draws with its own shaders and a bias of 4e-5 (`navview.py` `DEPTH_BIAS`; dae428a, checked by screenshots zoomed far out over the demo level).

## Not done

- Lit mode in the viewport as a sub-second light iteration loop (Fable's ranking 2026-10-11, item 2) beyond the current view; per-light prop vertex light in the view (layers page).
- Sky from an image visible in game; in-game check of skybox light orientation.
- hlvrad providing the layout for Cycles instead of Valve's vrad (possible, not asked for).

Last checked: 2026-10-16 against branch behaviours, commit 18c2ce4 (the Control Volume and view rows; the rest 2026-10-13 against 41dc861).
