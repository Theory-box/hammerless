# Light styles, switchable and flickering lights, Live Light Mixing and the remix

Branch `light-layers` (from main at 0.14.1, started 2026-10-12). Commits: 5ec24d0 (switchable and flickering lights), 7f9e3fc (`-onlylights`), 99c3528 (Live Light Mixing), 2d94d14 and f0a63da (Build keeps the mix, Fable's audit fixes). Released as 0.15.0 on 2026-10-12. Rule throughout: never fall back to Valve's vrad.

## Light styles in the engine (facts)

- A face's lightmap has one page per **light style**; style 0 is the static light. A named light (or one with a `pattern`/`style` key) gets a style of its own; the game scales that page at run time by the style's current brightness letter (a..z, `m` = normal, `a` = dark, `z` = double) ten steps a second.
- The style number is assigned by the **map compiler** (`SetLightStyles` in vbsp / hlvbsp), not vrad: a named light needs the `style` key vbsp writes, which is why `hlvbsp -onlyents` remakes "named lights' styles".
- L4D2 has the game's 12 built-in patterns (Flicker, Fluorescent Flicker, Candle, Gentle Pulse, Fast Strobe, Underwater and others) plus custom step strings.
- Limits measured/known: at most **3** switchable or patterned lights may reach the same surface (4 style slots per face, one is style 0); a map can have **32** switchable lights (styles 32..63 are the named ones): hlvbsp stops with "Too many switched lights (error at light <name>), max = 32" (`native/hlvbsp/write.c`, the style assignment; read from the code 2026-10-16).
- What Valve's own maps do (read from the 25 loose `.bsp` files of c1-c5, 2026-10-16): only switchable (named) lights, styles 32 and up assigned to named lights; c2m5 has 23, c1m1 17, the others 2 to 4; **no** baked flicker patterns (styles 1-12) at all. The DLC maps (inside VPKs) weren't scanned.
- Switchable lights' styled layers **don't bounce** in the game's model (their pages hold direct light only), so hlvrad's styled layers don't bounce either.

## Switchable and flickering lights in Hammerless

Status: `done` (commit 5ec24d0, 2026-10-12). UI: *Selected Object > Light* on Blender point and spot lights: **Pattern** (one of the 12 or Custom), **Custom (Steps)**, **Switchable**, **Starts Off**. A light that logic uses (TurnOn, TurnOff, Toggle, SetPattern on an Entity Events node, which accepts Blender lights) is made switchable even without the box. Both compilers (hlvbsp, hlvrad) handle the styles; no Valve tool. `layout.c` does vrad's `MakePatches` face test, style assignment per face and `PrecompLightmapOffsets` (lighting lump layout: per face, one average colour per style in reverse order, then per style the lightmap and 3 bump pages for bumped materials).

Game-tested with `tests/ingame/test_lightstyles.py` (lit build with our compilers): on, off, back on, a pattern change, a style 10 flicker, Starts Off; characters in the room dim with the light. 7/7 again on 2026-10-13 after the vrad-options work. Examples 53 (Power Goes Out) and 54 (Lights Flicker While a Tank Lives) use them.

Screenshot-brightness testing facts (research note 2026-10-12): L4D2 has no `mat_autoexposure_*`; pin exposure with `mat_dynamic_tonemapping 0` + `mat_force_tonemap_scale 1`; flashlights off (`m_fEffects` bit 4); bots away (`sb_stop 1`); `dev/dev_measuregeneric01b` is a good flat material (`concrete/concretefloor001a` is missing in L4D2).

### One light system (f8e16b2, 2026-10-16)

Status: `done`. Code: `hammerless/blender/lamps.py` (`LAMP_OF`, `_apply_light`, the conversion), `mapcollection.py` (runs it on file load), `spawn.py`, `core/entities.py`, `core/spawnlist.py`. Before it, the Add panel offered light **entities** (`light`, `light_spot`, `light_environment` point objects with keyvalues) next to Blender lights, and the two had different panels (the open item "a Light panel for light entities" below). Now:

| Fact | Detail |
|---|---|
| The Add panel's Point Light, Spot Light (new) and Sun add **Blender lights** | they export as the game's `light` / `light_spot` / `light_environment` through `extract.light_entity`, with the Light panel's Pattern, Switchable and Start Off |
| Light entities in older files convert to Blender lights when the file opens | colour and brightness from `_light` (the inverse of `extract.light_entity`: sRGB to linear, a sun's energy = brightness / 100), a spot's cone from `_cone` / `_inner_cone`, the direction from the angles and `pitch`, a listed light `style` becomes the Pattern, spawnflag 1 ("initially dark") becomes Start Off, other keys are kept; everything pointing at the old object (logic nodes, anything else) is pointed at the new light with `user_remap`. Converting again does nothing |
| Left alone | preset parts and an imported Hammer map's objects (they keep their entities) |
| Test | `tests/blender/run_tests.py` `test_lights_are_blender_lights`: the three lights from the Add panel; two converted entities export the same game light (colour / brightness within 1, style, spawnflags, name, an extra key, spot angles and cone); a logic node follows the light; converting again is a no-op |

A light that carries a Behaviour graph (branch behaviours, 18c2ce4) is made switchable (named) like a light that logic uses; planned there: Light Pattern presets as behaviours that share **one** light name per graph, so many patterned lights don't each spend one of the 32 switched styles ([../logic/README.md](../logic/README.md)).

## Live Light Mixing (`-layers`)

Status: `done` (commit 99c3528). Code: `hammerless/core/lightlayers.py` (tagging and the file format), `native/hlvrad/direct.c` (`CollectLayers`, `WriteLayers`, `GI_LAYER_BATCH`), `gather.comp` / `gi.comp`, `hammerless/blender/lightmix.py` (the live mix), `lightview.py` (reads all styles when layers exist).

**Idea.** Light adds up, bounces included, so a bake is the sum of what each light gives each luxel. If each light's own contribution is kept apart, a light's colour or brightness change is `rest + sum(layer x weight)` with `weight` = intensity now / intensity when baked, per colour channel. Separate bakes per light were tried first and rejected by the user at 3 to 4 s per light; the layers come out of **one** bake.

**Tagging.** With the setting on, the build tags every light entity with `_hllayer n` (a `light_environment`'s sky part gets `_hllayersky n`) and `_hlobject` (the Blender object it came from). Light entities' `_light` / `_ambient` keys got a colour swatch and brightness field in the UI (0.15.0).

**In hlvrad.** `hlvrad -layers <map>.hllayers keys` bakes as usual and also writes each layer's own light per luxel:
- `gather.comp` does every style slot and layer in one dispatch (slot -1), with lights sorted by style then layer; each layer's part is 12 floats per group and slot;
- `gi.comp` reads each layer's grid (sampled at s, t) at the same ray hits, so bounced light is per layer too;
- layers resolve through the bake's own radial (luxel) filter, four layers at a time as channels (`ResolveChannels`, `g_radialAll`). **Gathering bounce from the layers' luxels rather than their samples mattered**: from samples, bounce-lit faces were 5 to 25% off;
- layers carry the bump pages; styled (switchable) layers don't bounce;
- 256 layers at most; needs `-gpu` (mixing forces GPU ray-traced bounce; the panel says so);
- normal bakes stay byte-identical with layer support compiled in (checked on a large user map and the light-styles map).

**The file** (`WriteLayers`): `"HLLY"`, version 3, faces, layers (4 ints); per layer: what it was baked with (an int length, then `key=value` lines: `kind=light|sky`, classname, style, intensity (its lights' summed, vrad's units), `applied` (the weight the map's lighting currently has: `-remix` changes it), `_hlobject`, `_light`, `_ambient`, their HDR keys), how many faces it lights, then per face: its number, luxel count, pages (1, or 4 on a bumped face: flat first) and their light as 3 half floats a luxel (vrad's units: decoded value x 255), page after page. `<map>.hllayers.base` holds the bake's own lightmaps.

**In Blender** (`lightmix.py`): `Mix` keeps `rest` plus per-layer sparse (atlas index, value) arrays; only the luxels a layer lights are touched; a remix takes about 5 ms. Moving a light, or any other change, still needs a bake (the view says so). A light the view can't find (renamed or deleted) keeps its weight and the view asks for a bake.

**Cost** on a large user map: about 0.3 s per layer at first (direct 0.09 s, GI 0.03 s per pass, radial 0.09 s), about 0.6 s per layer once bump pages and styles were added.

**Accuracy** (`tests/compile/light_mixing.py`, `tests/compile/light_layers_sum.py`): live mix vs a real rebake median 0.37%, 95.5% within 2% (first version); per-light layers summed (`hlvrad -onlylights <file>`, commit 7f9e3fc: bake with some lights only) vs the whole bake median 0.1%, 99.89% of luxels within 1%.

## Build keeps the mix (`-remix`)

Status: `done` (commits 2d94d14, f0a63da). Code: `native/hlvrad/remix.c`, `hammerless/core/buildplan.py` (plan `remix`), `CompileJob._can_remix` / `_hlvrad_remix` in `compile.py`.

When only lights' `_light` / `_ambient` values (and their HDR keys) changed, the smart build plan is `remix`: `hlvbsp -onlyents`, then `hlvrad ... -remix <map>.hllayers` instead of a bake. The remix:
1. starts from the bake's own lightmaps in `<map>.hllayers.base` (so a remix of a remix doesn't round twice);
2. adds `(weight - 1) x layer` into every lightmap page of the layer's style, bump pages included;
3. remakes the world lights lump, leaf ambient, static prop and detail prop light from the result, as a bake does;
4. records the applied weights in the layers file only after the BSP is written (`RemixCommit`).

A remix keeps the layers and the map's vis (`-remix` clears `-layers` and `-visfrom`). The build bakes instead when: a light moved, was added or removed, or anything other than `_light` / `_ambient` changed; a colour channel baked at 0 now has light (nothing to scale); the visibility is being upgraded in the same build; the layers file or its base is missing; Exact lighting or a partial bake (`no_bake`) is on. If a remix fails (non-zero exit) the build runs our bake, not Valve's vrad. Stale layers are deleted whenever a bake runs without `-layers`.

| Measurement (large user map) | Result |
|---|---|
| Remix vs a real bake, lightmaps | median 0.5%, every luxel within 2% (two remixes in a row too; bump pages and styles too) |
| Remix vs a real bake, leaf ambient | median 0.2% |
| Remix vs a real bake, world lights | exact |
| Remix time vs bake | 1.3 s vs 4.4 s |
| In game (`tests/ingame/test_light_mixing.py`) | the remixed map looks the same as the bake; switchable lamps still switch |

## Not done / open

| Item | Status |
|---|---|
| Layers for vrad's patch bounce (mixing forces `-gi`) | not done |
| Static props' vertex light mixed live in the Blender view (the build's remix does remake it); estimated 0.1 to 0.2 s per light | not done, waiting for a yes |
| A wider radial than 4 channels to cut the per-light cost | not done |
| In-game prop check after a remix; cost of many flickering lights | open (2026-10-12) |
| A Light panel for light *entities* (not Blender lights) | `removed` as a need (f8e16b2, 2026-10-16): light entities convert to Blender lights on load; only imported Hammer maps keep light entities, edited through their keyvalues |
| Light Pattern presets as Behaviours sharing one light name per graph (the 32-style budget) | planned (branch behaviours) |

Last checked: 2026-10-16 against branch behaviours, commit 18c2ce4 (the one-light-system section, the limits and Valve's maps; the rest 2026-10-13 against 41dc861).
