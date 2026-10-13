# Lighting: hlvrad beyond vrad

The compiler-level facts (byte-identity to vrad, the arithmetic copied, speed, options) are in [../compilers/what-ours-do.md](../compilers/what-ours-do.md) and [../compilers/valve-options.md](../compilers/valve-options.md). This folder covers what hlvrad does beyond reproducing vrad, how each part works, and what was measured. User-facing text: `docs/systems/LIGHTING.md`.

| Page | Contents |
|---|---|
| this README | What a light compiler writes, the quality presets and what they set, the Lighting settings map, measured numbers in one place |
| [gpu-and-bounce.md](gpu-and-bounce.md) | The GPU path (Vulkan ray queries, record/replay rounds, vrad's walk in a shader), ray-traced bounce (`-gi`), bounce links on the GPU, lighting alongside vis |
| [layers-and-styles.md](layers-and-styles.md) | Light styles: flickering and switchable lights; Live Light Mixing (`-layers`), the `.hllayers` file, the remix (`-remix`), measured accuracy and cost |
| [bakes-sky-cycles-view.md](bakes-sky-cycles-view.md) | Partial bakes (`-nobake`) and keep light (`-keeplight`), sky light from the skybox or an HDRI (`-skymap`), the Cycles bake, the Baked Lighting view and prop lighting view |

Timeline (from the git log; all 2026): hlvrad written 10-08 (commits 0230858 to 779980b in one day; wired in with ceed756; threaded 927a04b; Final f9d2e58; 64-bit f6eb5ee; GPU 72a0441); GPU bounce links 1c161be, `-gi` ce87b21, Sky Rays 6d4895c, big-map fix f21a18f (10-09); default light compiler 95d3125 (10-10); switchable lights 5ec24d0, Live Light Mixing 99c3528, remix 2d94d14 (10-12); LDR removed dbf5f0f, Fast quality own 6a17d74/31a33e5, vrad options 41dc861 (10-13).

## What a light compiler writes (L4D2, HDR)

Measured 2026-10-08 by diffing a map before and after `vrad -hdr` (the lump numbers are L4D2's BSP version 21):

| Lump | Content | Written by hlvrad |
|---|---|---|
| 53 `LIGHTING_HDR` | Per lit face: one average colour per style (reverse style order), then per style the lightmap `(w+1) x (h+1)` samples of `ColorRGBExp32` (r, g, b mantissas + signed exponent), and for `SURF_BUMPLIGHT` materials 3 more pages per style (the bump directions `g_localBumpBasis` turned into the face's texture frame) | yes |
| 54 `WORLDLIGHTS_HDR` | The light entities as the game uses them for dynamic model lighting | yes |
| 51 / 55 `LEAF_AMBIENT_INDEX_HDR` / `LEAF_AMBIENT_LIGHTING_HDR` | Per leaf, up to 16 sampled 6-direction colour cubes (the light of characters, weapons, moving props) | yes |
| 58 `FACES_HDR` | The faces with their HDR styles and `lightofs` | yes |
| 30 / 31 vertex normals | Smoothed normals per face vertex (`PairEdges`, 45-degree smoothing) | yes |
| 35 game lump `dplh` | Detail prop (grass) lighting | yes |
| 40 pakfile `sp_hdr_<n>.vhv` | Static prop per-vertex colours, with `-StaticPropLighting`; map flags lump 59 bit 2 set | yes |
| 10 leaf flags | `LEAF_FLAGS_SKY` per leaf (`CanLeafTraceToSky`) | yes |
| 8 / 15 / 52 / 56 LDR lumps | Untouched by `-hdr` | hlvrad writes vrad's LDR leaf ambient placeholders on no-vis maps (41dc861); otherwise none |

L4D2 uses only the HDR set: Valve's own maps have no LDR lighting. That is why the LDR setting was removed on 2026-10-13 (commit dbf5f0f).

Values are linear light in vrad's units: a decoded 1.0 in the lightmap is 255 "vrad units"; a sun of brightness 100 is about 1 W/m² in Cycles' terms (see the Cycles page).

## Quality presets (`hammerless/blender/quality.py`)

| Preset | Sky rays (`-extrasky`) | Supersampling | SS points / passes / threshold | Bounces | Prop lighting | Prop polys | Patch size | Fix quirks |
|---|---|---|---|---|---|---|---|---|
| Fast | 1 | off (`-noextra`) | – | 2 | off | off | 4 | off |
| Normal | 1 (4 on the GPU, not Exact) | on | 4 / 4 / 0.0625 (vrad's) | 100 | on | on | 4 | off |
| Final | 16 (`-final`) | on | 4 / 4 / 0.0625 | 100 | on | on | 4 | off |
| Ultra | 16 | on | 8 / 8 / 0.03 | 100 | on | on | 4 | on |
| Off | no lighting (fullbright) | | | | | | | |

Changing any setting makes the quality Custom. Build & Play's Quality maps to (vis, lighting): Quick = (Off, Off), Fast = (Fast, Fast), Normal = (Full, Normal), Final = (Full, Final), Ultra = (Full, Ultra).

- Fast is **not** vrad's `-fast` (decision 2026-10-13; see compilers/valve-fallbacks.md case c): `CompileOptions.vrad_args(ours=True)` turns `rad="FAST"` into `-noextra -bounce 2`.
- Normal's 4x sky rays on the GPU: measured against a 64x reference on a large user map (2026-10-09), at 1x 54% of luxels were more than 1% off (mean 4.8%); 4x halves that, for 0.07 s more. On the CPU each doubling about doubles the sky time, so Exact keeps 1x.
- Ultra is not vrad-identical by design (`-fixquirks`, 8x8 supersampling): 2.6 s on an RTX 4090, 15 s on the CPU (large user map).
- Prop lighting default on at Normal since 2026-10-08 (commit 612da78) so the game doesn't light props at every map load.

## Settings to options

| Lighting panel setting | `CompileOptions` field | hlvrad option |
|---|---|---|
| Lightmap Scale (+ Imported Brushes Too) | export side (`lightmapscale` per side) | – (vbsp/hlvbsp) |
| Prop Lighting | `static_prop_lighting` | `-StaticPropLighting` |
| Prop Shadows from Full Model | `prop_polys` | `-StaticPropPolys` |
| Sky Rays | `sky_rays` | `-extrasky N` (`-final` = 16) |
| Supersampling / Points / Passes / Edge Threshold | `supersample`, `ss_points`, `ss_passes`, `ss_threshold` | `-noextra` / `-sspoints` / `-sspasses` / `-ssthreshold` |
| Bounces / Bounce Method / Bounce Rays | `bounces`, `gi`, `gi_rays` | `-bounce N`; `-gi N -girays N` |
| Bounce Patch Size | `patch_size` | `-chop N -maxchop N` |
| Fix vrad's Quirks | `fix_quirks` | `-fixquirks` |
| Exact Lighting (match vrad) | `light_exact` | omits `-gpu` |
| Sky Light (One Colour / From the Skybox / From an Image) | `sky_key` | `-skymap <map>.hlsky` |
| Live Light Mixing | tagged lights (`lightlayers.py`) | `-layers <map>.hllayers keys` |
| Control Volumes with Light Baking | `no_bake`, `keep_light` | `-nobake`, `-keeplight` |
| Light Compiler: Cycles | `light_tool = "CYCLES"` | Valve's vrad, then `blender/cyclesbake.py` |

## Measured numbers in one place

All on "a large user map" unless noted; RTX 4090 for GPU figures; 16 threads.

| What | Value | Date / source |
|---|---|---|
| vrad Normal / Final | 6.0 s / 16 s | 2026-10-08 |
| hlvrad CPU Exact Normal / Final / Ultra | 2.1-2.2 s / 7.3-8.2 s / 15 s | 2026-10-08 (commit f9d2e58) |
| hlvrad GPU Normal / Final / Ultra | 1.2 -> 1.0 s / 1.5 -> 1.4 s / 2.6 -> 2.2 s (after GPU bounce links) | 2026-10-08 / 10-09 |
| GPU vs Exact, lightmap bytes identical | 99.991% Normal, 99.917% Final; props 99.996%; leaf ambient ~98.8% within 1% | 2026-10-08 |
| First single-threaded hlvrad | 81 s (leaf ambient 68 s of it) | 2026-10-08 |
| Bounce links (10 M patch-pair rays) | CPU 0.26 s -> GPU 0.11 s | 2026-10-09 |
| `-gi` 4 bounces 1024 rays | 0.15 s; 2.6 M sq ft map 1.3 s vs 2.6 s patches; brightness 1.04-1.08x vrad | 2026-10-09 |
| 262,144-patch limit removed | 2.6 M sq ft map: 27 s (Valve's vrad fallback) -> 4.7 s | 2026-10-09 (commit f21a18f) |
| GI ambient on characters with sky | was 56% of right, now within 0.4% | 0.11.x fix (CHANGELOG) |
| Lighting alongside vis | Build & Play 29.0 s -> 26.6 s; threads during vis: all 10.0 s, 8: 10.3, 4: 10.8, 2: 12.4 | 2026-10-10 |
| Live Light Mixing per light | ~0.3 s (direct 0.09, GI 0.03/pass, radial 0.09) -> ~0.6 s with bump pages and styles; Blender remix ~5 ms | 2026-10-12 |
| Remix vs real bake | lightmaps median 0.5%, all within 2%; leaf ambient median 0.2%; world lights exact; 1.3 s vs 4.4 s bake | 2026-10-12 |
| Layers summed vs whole bake (`-onlylights`) | median 0.1%, 99.89% within 1% | 2026-10-12 |
| Keep light partial bake | 6,410 faces kept exactly, 1.7 s; a quarter of the map 1.6 s vs 3.2 s | 2026-10-09 (0.12.x) |
| Cycles vs vrad on a real level | correlation 0.976 (0.956 at first), brightness within 1-3%; seams 1.6% vrad vs 0.02% stitched; denoise 7.7% off vs 1.3% raw | 2026-10-07 |
| vrad run-to-run noise | 0.34% of luxels (median 0.6%, 38 luxels > 5%); deterministic with bounce + `-noextra` | 2026-10-08 |

Last checked: 2026-10-13 against branch light-layers, commit 41dc861.
