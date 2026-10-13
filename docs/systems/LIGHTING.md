# Lighting

Left 4 Dead 2 lights a map ahead of time. A compiler traces light from every light entity, the sun and the sky, and stores the result in the map: lightmaps on brush faces, colours at static prop vertexes, and small ambient samples for characters and moving models. Valve's compiler for this is `vrad`.

Hammerless has its own: **hlvrad** (`native/hlvrad/*.c`). It is the default light compiler. It can reproduce vrad's output byte for byte, or run on the graphics card and give the same look several times faster. On top of that it does things vrad can't: sky light taken from the skybox or an HDRI, ray-traced bounce, partial bakes that keep the rest of the map, and per-light layers that let you change a light's colour and brightness without baking again.

This page explains how it works, what was measured, and what each setting in the **Lighting** panel does.

## What a light compiler writes

vrad (and hlvrad) fill these parts of the compiled map:

| Part | What it is |
|---|---|
| Lightmaps (lump 53, HDR) | Per brush face, a small grid of colours ("luxels"). Bumped materials get 4 pages: flat plus 3 bump directions. Switchable lights get pages of their own (light styles). |
| World lights (lump 54) | The light entities, which the game uses to light models at run time. |
| Leaf ambient (lumps 51/55) | Per room of the BSP tree (leaf), sampled colour cubes. Characters, weapons and other moving models take their light from these. |
| Detail prop lighting (game lump) | Light for grass and other detail props. |
| Static prop lighting (`.vhv` files in the map's pak) | Per-vertex colour for each static prop, with `-StaticPropLighting`. |

Hammerless bakes **HDR only**, which is what L4D2 uses: Valve's own maps have no LDR lighting, so there's no LDR setting.

## hlvrad: how it was built

hlvrad was written stage by stage, each stage compared against vrad's output before moving on. The Source SDK 2013 vrad source was read for behaviour. Where L4D2's vrad differs from that source, the 32-bit `vrad_dll.dll` that ships with the game was disassembled, and a small launcher ran a copy of it in-process with hooks that dump its internal state (patches, transfers, ray results, leaf ambient rays) for direct comparison.

### Making it byte-identical

Matching vrad to the byte meant reproducing its floating-point arithmetic, not just its algorithm. Some of what had to be copied:

- **x87 rounding.** vrad's vector normalize returns an unrounded length; some lengths sum squares in a particular order, (x² + z²) + y² rather than x² + y² + z². Form factors are computed in double, thresholds in float.
- **Its ray tracer.** vrad builds its own kd-tree over triangles. hlvrad builds the same tree, including the split-cost arithmetic, and uses the same triangle test thresholds. The tracer was confirmed exact by feeding the same rays to vrad's own `Trace4Rays` through the hook and comparing answers.
- **Sampling patterns.** Sky light uses 162 directions from a Halton (2, 3) sequence starting at element 2. The sun's spread uses 30 rays.
- **Its bugs.** vrad's sky test for leaves starts its ray from an uninitialised point, so every non-solid leaf in a radial-vis map gets sky when the map has a sky. Its supersampling ORs a mask into an uninitialised stack slot. Static prop bounce has no distance falloff. hlvrad keeps these, because removing them changes the output.
- **Prop shadows.** Static props block light with their `.phy` collision, read through the game's own `vphysics.dll`. When a model has no `.phy`, L4D2's vrad builds a convex hull per mesh from the `.vvd` vertexes; hlvrad does the same.

vrad itself is not fully deterministic. Two runs on the same large user map differed in 0.34% of luxels, and its leaf ambient changes slightly from run to run. With bounce on and supersampling off (`-noextra`), it is deterministic, so that is the configuration used for byte comparisons.

### Result

| Check | Result |
|---|---|
| All 16 test maps (sun, sky, spot and point lights, water, displacements, grass and detail props, static props) | Whole output file byte-identical to vrad, bounce included |
| Large user map, supersampling on (when supersampling was added) | 94% of luxels identical (the rest: vrad's uninitialised supersampling mask, only partly emulated) |
| Large user map, all 80 static prop `.vhv` files | Identical |
| Light-styles test map and large user map, with layer support added | Normal bakes still byte-identical |

### Speed

hlvrad started single-threaded and slow (81 s on the large user map, against vrad's 5.9 s). Threading the per-face work, leaf ambient and transfers, plus per-displacement bounding volumes, brought it under vrad. Output is identical with 1 thread or 16.

Measured on the large user map:

| Mode | vrad (16 threads) | hlvrad CPU (Exact) | hlvrad GPU (RTX 4090) |
|---|---|---|---|
| Normal | 6.0 s | 2.1-2.2 s | 1.2 s, later 1.0 s |
| Final | 16 s | 7.3-8.2 s | 1.5 s, later 1.4 s |
| Ultra | (not possible) | 15 s | 2.6 s, later 2.2 s |

The "later" GPU figures are after the bounce links (patch-to-patch visibility) also moved to the GPU: that step went from 0.26 s to 0.11 s for about 10 million tests.

A separate fix removed an old limit of 262,144 bounce patches. Large maps had been quietly running Valve's vrad instead: on a 2.6 million square foot map that was 27 s of lighting, now 4.7 s.

## Exact mode and the GPU

**Settings: Lighting > Advanced > Exact Lighting (match vrad).** Off by default.

- **On:** hlvrad runs on the CPU and reproduces vrad bit for bit.
- **Off:** hlvrad uses the graphics card through Vulkan ray queries (NVIDIA RTX, AMD RX 6000 and newer, Intel Arc). Without a capable card it lights on the CPU.

On the GPU, direct light uses the ray tracing cores (`gather.comp`). The CPU still lays out every sample point as vrad does; the GPU answers the shadow rays in rounds, one per supersampling pass. vrad's stale supersampling mask is reproduced from the sign of the last light's falloff, which needs no rays, so the same points get supersampled.

Leaf ambient and prop bounce do not use the ray tracing cores. Ray-traced surfaces did not match vrad: vrad's tree walk hits a face's lightmap rectangle, not the face, and tests displacements against the whole ray from any leaf their box touches. So vrad's own BSP walk was ported to a shader (`walk.glsl`) and finds the same surfaces, quirks included.

Work goes to the GPU in chunks, so Windows never resets a busy card.

GPU mode is not bit-exact. On the large user map, against Exact:

| Part | Identical |
|---|---|
| Lightmap bytes, Normal | 99.991% |
| Lightmap bytes, Final | 99.917% |
| Static prop vertexes | 99.996% |
| Leaf ambient | about 98.8% within 1%, the same spread as two vrad runs |

The differing lightmap pixels are near-black pixels and single pixels on shadow edges.

## Quality settings

**Lighting > Quality** fills in the settings under **Quality Details**. Changing any of them makes the Quality *Custom*. Changing only lighting settings relights the map without recompiling its geometry.

| Quality | What it sets |
|---|---|
| Off | No lighting: the map is fullbright. |
| Fast | Quick lighting for testing: Normal's lighting with cheaper settings (no supersampling, 2 bounces, no prop lighting). Not vrad's `-fast`, a rough preview mode Hammerless doesn't copy. |
| Normal | vrad's normal lighting. On the GPU, 4 times vrad's sky rays; with Exact or Valve's vrad, 1 time. |
| Final | 16 times the sky rays. |
| Ultra | Final, plus 8 x 8 supersampling points, 8 passes, edge threshold 0.03, and *Fix vrad's Quirks*. Hammerless only. |

Normal's 4x sky rays on the GPU was measured against a 64x reference on the large user map: at 1x, 54% of luxels were more than 1% off (mean 4.8%); 4x halves that, for 0.07 s more.

**Quality Details:**

| Setting | What it does |
|---|---|
| Sky Rays | Times vrad's sky rays per luxel (`-extrasky`). More is smoother sky light and sun spread. Nearly free on the GPU; on the CPU each doubling about doubles its time. |
| Supersampling | Re-lights luxels where light changes sharply (shadow edges) at several points. Off is vrad's `-noextra`. |
| Points | Points across a luxel when supersampling: 4 is vrad's 4 x 4, Ultra uses 8 x 8. Hammerless only. |
| Passes | How many times supersampling spreads to neighbours of luxels it changed (vrad: 4). Hammerless only. |
| Edge Threshold | The brightness step between neighbours that triggers supersampling (vrad: 0.0625). Lower catches softer edges. Hammerless only. |
| Bounces | Light bounces between surfaces. 0 is direct light only. |
| Bounce Rays | With ray-traced bounce: rays per luxel (default 1024). 256 shows a little grain; 4096 is about 3 times slower. |
| Bounce Patch Size | With vrad's patches: patch size in luxels (vrad: 4). Smaller is finer bounce in corners and much slower. |
| Prop Shadows from Full Model | Props cast shadows with their full model (LOD0 triangles), not their collision shape (`-StaticPropPolys`). |

**Lighting panel** (main settings):

| Setting | What it does |
|---|---|
| Lightmap Scale | Shadow detail on brush faces: 8 sharp, 16 Valve's default, 32+ soft and faster. A material can set its own. *Imported Brushes Too* applies it to an imported map's brushes, which otherwise keep their own scale. |
| Prop Lighting | Bakes static prop vertex lighting (`-StaticPropLighting`), so the game doesn't light props at every map load. |
| Bounce | *vrad's Patches* or *Ray Traced (GPU)*. See below. |
| Live Light Mixing | Bakes each light into a layer of its own. See below. |

**Advanced:** Light Compiler (Hammerless, Valve vrad, Cycles), Exact Lighting, Fix vrad's Quirks, and Extra vrad Options.

### Fix vrad's Quirks

Off by default, on in Ultra. It removes four vrad behaviours: edge luxels left dark by the stale supersampling mask, bounced light picked up from a face's lightmap rectangle instead of the face, far displacements hiding nearer faces, and props' bounced light missing far surfaces. The result deliberately goes beyond vrad's look and is not vrad-identical.

## Bounced light: patches or ray traced

vrad bounces light with **patches**: surfaces are cut into pieces (4 luxels by default), each patch works out which others it sees (the "transfers"), and light is passed between them up to 100 times. hlvrad reproduces this exactly, and on the GPU it traces the transfer visibility on the card.

**Ray Traced (GPU)** (`-gi`) is a different method, final gathering. Each lightmap sample sends cosine-weighted rays over its hemisphere (a stratified set, turned by a random amount per point). Each ray takes the light of the surface it hits, from the current lightmaps, times that surface's reflectivity. The mean is the bounced light; bumped faces weight the same rays toward each bump direction. Each pass gathers from the previous pass's result, so n passes give n bounces. It stops when a pass adds under 0.2%, and at most 8 passes are run: each pass carries on roughly a twentieth of the light, so 8 is as good as vrad's 100.

What it gives: bounced light at luxel detail instead of patch-sized blotches, no patch light leaks, and bounce even with Visibility off (patches need vis). On the large user map, 4 bounces at 1024 rays took 0.15 s. On a 2.6 million square foot map it took 1.3 s against 2.6 s for patches. Overall brightness is about 1.04 to 1.08 times vrad's. It is GPU only, not available with Exact, and marked experimental.

## Sky light from the sky

vrad lights a map with one sky colour from every direction. **Environment > Sky Light** offers:

| Option | What it does |
|---|---|
| One Colour | vrad's way: the Sky Light Color from every direction. |
| From the Skybox | Each part of the sky lights the map with its own colour, from the map's skybox: warm from a sunset horizon, blue from above. |
| From an Image (HDRI) | The same from a panorama image: the one picked, or the World's Environment Texture, with a Sky Rotation. The game still shows the skybox. |

How it works: `core/skylight.py` reads the skybox's six HDR faces (compressed HDR is colour x 16 x alpha; the side faces are stretched to double height) and turns them into a panorama. The face orientation was measured on L4D2's skies so the sides join seamlessly. The panorama is blurred to the spacing of the sky rays (9 degrees, so a bright spot doesn't make blotches) and scaled so that a floor under open sky gets the same light as the flat colour at the same Sky Light Brightness. hlvrad (`-skymap`, `skymap.c`) then gives each sky ray the colour where it looks: on faces, supersampling, bounce, static and detail props, and the leaf ambient.

Without a sky picture, output is unchanged and still byte-identical to vrad. If the picture can't be prepared, the build says so and lights with one colour.

Sky Light Brightness can be used with no sun (Add Sun if Missing off): the build adds the sky light alone.

## Flickering and switchable lights

**Selected Object > Light** on Blender point and spot lights:

| Setting | What it does |
|---|---|
| Pattern | How the baked light changes in the game: one of the game's 12 built-in styles (Flicker, Fluorescent Flicker, Candle, Gentle Pulse, Fast Strobe, Underwater and others) or *Custom*. |
| Custom (Steps) | Your own brightness steps, ten a second: a is dark, m normal, z double. Makes the light switchable. |
| Switchable | Logic can turn it on and off (TurnOn, TurnOff, Toggle, SetPattern), for example from an Entity Events node. |
| Starts Off | Dark at map start until logic turns it on. |

The panel shows the light's logic name. A light that logic uses is made switchable even without the box ticked.

Such a light is baked into a light style of its own: its own lightmap pages that the game scales at run time. Hammerless's map and light compilers both do this, with no fallback to Valve's tools. The game limits how many can overlap: at most 3 switchable or patterned lights may reach the same surface, and a map can have 32 switchable lights.

Tested in game with the in-game test bench: on, off, back on, a pattern change, a style 10 flicker, Starts Off; characters in the room dim with the light. Examples 53 (Power Goes Out) and 54 (Lights Flicker While a Tank Lives) use them.

## Live Light Mixing

**Lighting > Live Light Mixing.** Bake once, then change any light's colour or brightness (or the sun's and the sky's), and the Baked Lighting view follows at once.

### How it works

Light adds up. A bake is the sum of what each light gives each luxel, bounces included. So with mixing on, the build tags every light entity with a layer number (`_hllayer`, `_hllayersky` for a light_environment's sky, and `_hlobject` for the Blender object it came from), and `hlvrad -layers` bakes the map as usual while also writing each layer's own light per luxel to `<map>.hllayers` (half floats, plus the keys each layer was baked with).

Blender then shows:

    rest + sum(layer x weight)

where *rest* is the bake minus the layers, and a layer's *weight* is, per colour, its light's intensity now over its intensity when baked. Only the luxels a layer lights are touched; a remix takes about 5 ms (`blender/lightmix.py`).

Separate bakes per light were tried first and cost 3 to 4 s per light, which was rejected. Instead, layers come out of the one bake:

- the GPU direct gather does every light style and layer in one dispatch, with lights sorted by style, then layer;
- the ray-traced bounce reads each layer's light at the same ray hits;
- layers resolve through the bake's own luxel filter, four layers at a time as channels.

Gathering bounce from the layers' luxels rather than their samples mattered: from samples, faces lit by bounce were 5-25% off. The GPU writes its output to device memory and copies it back once; zero-filling host memory first had cost 4 times the GPU time.

Mixing forces ray-traced bounce on the GPU (the panel says so). Layers carry the bump pages; switchable lights' layers don't bounce, as in the game. Normal bakes stay byte-identical with layer support in the compiler.

Cost on the large user map: about 0.3 s per light in the first version (direct 0.09 s, bounce 0.03 s per pass, filtering 0.09 s), about 0.6 s once bump pages and styles were added.

### Build keeps the mix

When only lights' colour or brightness changed, the smart build doesn't bake. It runs `hlvrad -remix` (`remix.c`): starting from the bake's own lightmaps (kept in `<map>.hllayers.base`, so remixing a remix doesn't round twice), it adds (weight - 1) x layer into every lightmap page of the layer's style, bump pages included. It then remakes the world lights, leaf ambient, prop and detail prop light from the result, as a bake does. The weights now applied are recorded only after the map is written.

| Measurement (large user map) | Result |
|---|---|
| Remix vs a real bake, lightmaps | median 0.5%, every luxel within 2% (also two remixes in a row) |
| Remix vs a real bake, leaf ambient (model lighting) | median 0.2% |
| Remix vs a real bake, world lights | exact |
| Per-light layers summed vs the whole bake (`-onlylights`) | median 0.1%, 99.89% of luxels within 1% |
| Remix time vs bake time | 1.3 s vs 4.4 s |
| In game | the remixed map looks the same as the bake; switchable lamps still switch |

Tests: `tests/compile/light_mixing.py`, `tests/compile/light_layers_sum.py`, `tests/ingame/test_light_mixing.py`.

### When it bakes instead

- A light moved, was added or removed, or anything other than `_light` / `_ambient` (and their HDR keys) changed.
- A colour channel that was baked at 0 now has light: the layer has nothing to scale up.
- Visibility is being upgraded, or the layers file or its base is missing.
- A light the view can't find (renamed or deleted) keeps its weight, and the view asks for a bake.

If a remix fails, the build bakes with hlvrad. Stale layers are deleted whenever a bake runs without them.

## Partial bakes, Control Volumes and keep light

The **Bake Lighting** button has a dropdown of what to bake:

| Choice | What it bakes |
|---|---|
| Whole Map | Everything. |
| What the View Sees | What the 3D viewport sees, out to *View Distance* (default 3000 units). |
| Selected Objects | Around the selection's bounds. |
| A Control Volume | Each Control Volume set to *Only Inside* with *Light Baking* ticked, by name. |

**Control Volumes** (Add > Volumes; they replaced the earlier No Bake Volume) with *Light Baking* ticked tell hlvrad (`-nobake`, `nobake.c`) where not to work out light. *Not Inside* skips the inside; *Only Inside* bakes only the inside. Points that aren't baked aren't traced: they cost next to nothing. Surfaces there still cast shadows. Partly covered faces are cut at the volume's edge, and prop vertexes and leaf ambient samples inside are skipped too. With bounce off, everything outside is byte-identical to a full bake.

Build and Build & Play bake inside every Only Inside light volume, or the whole map if there are none.

**Keep light** (`-keeplight`, `keeplight.c`): what isn't baked takes the previous build's lighting instead of a flat ambient colour, wherever the previous build has exactly the same thing: a face with the same corners, lightmap size, axes, styles and bump mapping (and displaced vertexes for terrain); a leaf with the same bounds; a static prop with the same record, model and model checksum. Anything else, and anything inside a Not Inside volume, gets the map's ambient colour. The old map is read as untrusted input, every read bounds checked. Bounce inside the baked area comes from the real lighting around it.

Measured on a real map: 6,410 faces outside the baked area kept exactly, lighting in 1.7 s. A view holding a quarter of the map's surfaces lit in 1.6 s instead of 3.2 s.

## The smart build plan

`core/buildplan.py` compares the new VMF with the last build's and picks the least work:

| Plan | When | What runs |
|---|---|---|
| same | nothing that reaches the map changed | nothing |
| entities | only entity keys or outputs | entity lump rewritten, lighting kept |
| remix | only lights' colour or brightness, every light has a layer | entity lump, then `hlvrad -remix` |
| lighting | lights or static props changed, geometry didn't | entity lump, then a bake |
| full | geometry changed | map compiler, vis, light |

Anything the planner doesn't recognise counts as geometry, so a doubt always means a full compile. Lighting settings form their own fingerprint: changing one relights without recompiling geometry. A bake made by Bake Lighting records its area, so a following Build & Play with nothing changed reuses it.

**Lighting alongside vis.** With hlvrad on the GPU and ray-traced bounce, lighting starts as soon as the map is compiled, not after vis (`-visfrom`). Faces' light and bounce don't need vis, which only spares rays a light can't reach; hlvrad waits for vis only for prop light and sky leaves. The result is byte-identical to lighting after vis. With this and related scheduling changes, a full Build & Play of a large user map went from 29.0 s to 26.6 s (35.3 s that morning, before the stringtable dictionary fix as well). Maps with fog distance (radial vis) and vrad's patch bounce still light after vis.

## Viewing the lighting in Blender

**Lighting > Baked Lighting** draws the last build's lighting over the viewport, read straight from the compiled map (`blender/lightview.py`). Nothing is added to the .blend.

| Setting | What it does |
|---|---|
| Mode: Lighting Only | The baked light alone: best for leaks, harsh shadows, dark corners. |
| Mode: Lit | The baked light multiplied over the viewport (best in Solid view with Flat lighting and Texture colour). |
| Mode: Textured | The map as the game draws it: each surface's game texture times its light, props with their textures and vertex light, see-through parts cut out. |
| Exposure | Brighter or darker, in stops. |
| Props | Draws static props with their baked vertex light. |
| X-Ray | Draws the lighting through walls. |

It shows the compiled map's own faces: after moving a wall, the light stays where the compiled wall was until the next build, and the panel says the scene has changed. With Live Light Mixing it follows light changes.

## Cycles bake (optional)

**Advanced > Light Compiler: Cycles.** Valve's vrad lays out every face's lightmap and writes the world lights, ambient samples and prop lighting. Then Blender's Cycles bakes the lightmaps with the same lights and units, on the GPU if there is one, and `core/lightbake.py` writes them back in vrad's layout. It runs in a background Blender, so you can keep working, and Cancel stops it.

- **Units:** a diffuse bake times 100 x pi equals vrad's units. No fitting. Against vrad on a real level: correlation 0.976, overall brightness within 1-3%.
- **Stitch Seams** (on): where faces meet within 45 degrees, luxels along the shared edge are adjusted (least squares). Mean difference across edges: 1.6% for vrad, 0.02% stitched.
- **Samples** (1024): 256 is already within 0.3% of 4096.
- **Denoise** (off): Blender's denoiser smears the packed bake; measured 7.7% off against 1.3% raw.

Prop lighting and switchable lights stay vrad's. It predates hlvrad and still needs Valve's vrad for the layout.

## Valve's vrad is not a silent fallback

Any case where Valve's vrad lights a map instead of hlvrad is treated as a bug to fix. When it happens, the build log says why and the build's Problems list it. Today vrad still runs for:

- Extra vrad Options;
- hlvrad failing, or not being able to read the game's models.

## Limits and what's not done

- `-textureshadows` (alpha-textured foliage shadows from `lights.rad`) is not supported.
- Exact mode with supersampling on matched 94% of luxels on the large user map, not 100%: fully emulating vrad's uninitialised stack value would mean copying its tracer's stack layout.
- A ray that passes exactly through a kd-tree split corner can still differ (a handful of luxels).
- Ray-traced bounce is GPU only and experimental.
- Live Light Mixing: no layers for vrad's patch bounce; static props' vertex light isn't mixed live in the Blender view (the build's remix does remake it); each light costs a channel in a 4-channel filter, and a wider one would cut the per-light cost.
- Sky light from an image doesn't change the visible sky: the game still shows the skybox.
- The absolute orientation of sky light from the skybox was measured from the sky textures, not yet checked in game.
- Very big maps stop with a clear message instead of overflowing hlvrad's tables.
