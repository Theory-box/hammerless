# Map Compiler and Geometry Export

A Left 4 Dead 2 map starts as brushes: convex solids, each side a plane with a material. A map compiler turns those brushes into the BSP file the game loads: the BSP tree, the visible faces, the collision, the portals that the visibility compiler reads, and the extra data for displacements, props, overlays, water and cubemaps. Valve's compiler for this is `vbsp`.

Hammerless does both halves. It turns Blender meshes into brushes and displacements, and it compiles them with its own map compiler, **hlvbsp** (`native/hlvbsp/*.c`). hlvbsp is the default. On Valve's own sample maps it writes the same file as vbsp, byte for byte, on 132 of 134.

It also goes the other way: Hammer maps (.vmf) and compiled maps (.bsp, including the game's own campaigns) can be imported into Blender as editable scenes.

This page explains how each part works, how it was checked, what was measured, and what is left.

## From Blender to brushes

The Blender side only reads the scene into a plain description of the map (brushes with planes and face materials, entities, props, terrain, decals, lights). Everything after that is plain Python that doesn't touch Blender, and writes a .vmf for the compiler.

### Convex meshes

Each object has a Kind in the *Selected Object* panel. On *Auto*, a convex mesh becomes a world brush and a non-convex one is reported. One object can hold several separate convex pieces: each loose part is one brush.

| Shape | What to do |
|---|---|
| Walls, floors, buildings | Any convex mesh (boxes, wedges, cylinders). |
| Something that isn't convex | Split it into convex pieces, or tick **Use Convex Hull**. |
| Props of any shape | Set the Kind to **Custom Model**: the mesh becomes a game model. |

**Use Convex Hull** wraps each loose part in its convex hull (Blender's own hull operator) instead of requiring the part to be convex. The hull takes the material of the part's first face.

Faces painted with the Texturing panel export exactly the alignment they show: their UVs become Hammer's texture axes. Unpainted faces stay world-aligned. Mirrored objects have their face winding flipped so the brush comes out right side out.

Checks run before the compile. Two faces that almost line up (for example 0.08 units apart) are snapped together on export, with a note. A brush side with more than 64 corners (a cylinder cap with many segments, for example) is caught, because it crashes the compiler without a message. The check is fast at any size: a 32,000-face mesh takes about 6 s.

### Auto Detail

World brushes cut the map into visibility regions, and the visibility compile slows down quickly with more of them. `func_detail` brushes don't cut visibility, but still block players and cast shadows. **Auto Detail** (*Settings > Map Defaults*) picks which brushes become detail:

| Setting | What becomes func_detail |
|---|---|
| Off | Nothing. Every brush cuts visibility, as in plain Hammer. |
| Round and Small (default) | Brushes with 9 or more distinct planes (cylinders, arches; a box has 6) and brushes under 256 units across. |
| Everything | Every brush. The map becomes one visibility region: fine for small maps. |

The 256-unit limit was measured on a real map: it cut vvis time by about 25% while the game still culled the same (24% vs 23% drawn).

Each brush (or a whole collection) can override the rule with **Detail**: *Detail*, *World* or *Auto*. In a test room, 36 overlapping boxes set to Detail cut the map's visibility pieces from 190 to 48.

Some brushes always stay world: tool brushes (clip, hint, skip and so on), the seal, and brushes touching an area portal (only world brushes can seal an area portal). Automatic detail only applies when Auto Seal is on, because detail brushes can't seal a map. Brushes set to *Detail* by hand still become detail with Auto Seal off, with a note listing them in case one is an outer wall.

### Auto Seal: maps that can't leak

A map leaks when there is a path from any entity to the void outside. A leaking map has no inside, so vis and lighting fail. With **Auto Seal (skybox shell)** on (the default), the export wraps everything in six `tools/toolsskybox` brushes: a hollow box 256 units out from the map's bounds, 16 units thick. The map can't leak.

Imported maps are wrapped too, around their own brushes and entities as well as yours. Import turns Auto Seal off while the map still has its own seal.

With Auto Seal off and a leak, **Load Leak** draws the leak line to the hole. hlvbsp writes the same leak file (`.lin`) as vbsp.

### Terrain and displacements

Set an object's Kind (or its collection's) to **Terrain**, sculpt it freely, and it becomes displacements.

How it works:

- The terrain is sampled from above like a heightmap, by casting rays straight down onto the mesh. No overhangs.
- The grid is cut into patches. Each patch is a nodraw box brush whose top face carries a displacement (power 2, 3 or 4: 5, 9 or 17 vertices a side). The patch size is set per object.
- Neighbouring patches sample the same grid points along shared edges, so seams line up without a sewing step.
- Displacements can't have holes. Grid points outside the terrain's outline are pulled 512 units down, so the edge drops away as a steep skirt instead of a flat shelf players could stand on.
- **Blending:** paint vertex colour red to blend between a blend material's two textures. The red channel of the active colour attribute becomes the displacement's alpha. The value comes from the face the ray hits, the same for byte and float colours.
- The terrain uses its material's texture and lightmap scale. A displacement's lightmap is at most 125 luxels a side and can't be split like a brush face, so one too big for its scale gets the finest scale that fits.

The game allows 2048 displacements. More terrain patches than that is an error asking for a bigger Patch Size; an imported map's displacements count too.

## hlvbsp: our vbsp

hlvbsp is a drop-in for L4D2's `vbsp.exe`: same arguments, same log, same output files. It follows the method of Quake 2's GPL map tools, with every detail matched to L4D2's vbsp. Where L4D2 differs from the published 2013 SDK source, the answer was read from vbsp's own binary (each source file's top comment says where).

### Why C, and why 32-bit

vbsp computes in 32-bit floats, and parts of it in the x87 unit at higher precision. Matching it byte for byte means repeating the same arithmetic in the same order, so hlvbsp is C, not Python.

It is built as a 32-bit program because, like vbsp, it loads the game's own `bin/vphysics.dll` to build collision models. The 32-bit build's output is identical to a 64-bit build's.

### The Python manifest

Some of what vbsp reads lives in the game's VPK archives: material flags, texture sizes and reflectivity, surface properties, model hulls. hlvbsp doesn't read VPKs. Before each compile, Python (`core/mapcompiler.py`, `core/cubemappatch.py`) writes tables next to the map and passes them on the command line:

| Table | What's in it |
|---|---|
| `-materials` | Per material: contents, surface flags, texture width and height, reflectivity, surface property. Follows vbsp's FindMiptex rules (tool textures, water, see-through materials, which shaders get a lightmap). |
| `-surfaceprops` | The game's surface property scripts, for physics. |
| `-props` | Per prop model: whether it may be static, and its meshes' vertices. |
| `-fgd` | The game's entity classes and which keys hold names, positions and angles (for instances). |
| Patch templates | The text of per-cubemap, per-water-depth and blend-material patch materials. |

### What it handles

Everything vbsp does for an L4D2 map. All of it is checked against vbsp lump by lump.

| Part | Notes |
|---|---|
| Brushes, CSG | Overlapping brushes carved so none overlap. |
| BSP tree, portals | The portal file (`.prt`) for vis is identical to vbsp's. |
| Leak check | The same leak line, and vbsp's areaportal leak line. |
| Faces | Merged, cut to the lightmap size limit, welded, T-junctions fixed. |
| Detail brushes | Not in the vis tree; their faces listed in the world leaves they touch. |
| Physics collision | Brush models and displacements, built by the game's `vphysics.dll` as vbsp does. |
| Area portals | With their clip outlines. |
| Displacements | Neighbours, level-of-detail vertices, lightmap samples, physics. |
| Detail props | Grass from a material's `%detailtype`, on faces and terrain, plus `prop_detail` entities. Same random placement as vbsp. None under water or inside `func_detail_blocker`. |
| Static props | The `sprp` game lump (version 9), with the leaves each prop touches. |
| Overlays | `info_overlay` on brushes and terrain; named ones stay for scripts. Water overlays (`info_overlay_transition`) too. |
| Cubemaps | `env_cubemap`: each shiny surface gets a material patched to its cubemap, and a placeholder cubemap until the game's `buildcubemaps`. |
| Water | Water volumes, fog data, per-depth water materials, fluid physics, the automatic `water_lod_control`. |
| Sky | 3D skyboxes (`sky_camera`): its area kept out of the world bounds, one vis cluster. |
| Others | `func_occluder`, `func_viscluster`, `info_no_dynamic_shadow`, blend materials on brush faces, ladders (`func_simpleladder` brush models, as L4D2's vbsp makes them), instances (`func_instance`, nested ones too). |

It also stops at the game's limits (brushes, brush sides, materials, areas, area portals, leaf faces, primitives and more) with vbsp's message, instead of writing a map the game rejects.

### Matching vbsp to the byte

Most of the work was in small numeric details. A few examples of what had to be copied:

- vbsp's vector normalize returns an unrounded x87 length, and some code divides by it.
- Some sums group in a specific order (`(y*y + z*z) + x*x`). Swapping two terms doesn't matter; regrouping does.
- A snapped plane's distance is computed as `(p0 + p1 + p2) * (1/3.0f)` in float.
- `-0` and `+0` differ in the output, so subtractions from zero are kept where vbsp has them.
- Text numbers round ties away from zero, like the old Microsoft C library vbsp was built with.
- Detail props reproduce vbsp's and the game's random number streams, and the Microsoft quicksort's order for equal leaves. On a 29,348-prop grass map every detail prop is byte-identical.

The lesson recorded from this work: disassemble early instead of guessing variants. Debug by printing samples from hlvbsp, matching Valve's output, and reading that one function in the binary.

A few bytes in vbsp's own output are not deterministic: padding and unused fields left uninitialised, a word in the physics data, and the displacement physics hull bytes, which differ between vbsp's own runs. The comparison tools mask these.

### How it was verified

| Test | Result |
|---|---|
| `mapcompiler_suite.py`: 23 generated maps, one per feature (box, props, leak, area portals, grass, overlays, water, water overlays, cubemaps, blend materials, occluders, skybox, ladders...) | 23 of 23 identical to vbsp (displacement physics lump skipped, as vbsp's runs differ there), portal files identical |
| `compare_vbsp.py --dir` on Valve's `sdk_content` sample maps | 132 of 134 byte-identical, none handed to Valve's vbsp |
| Demo level, built in game test | Same lumps as vbsp (except displacement physics), plays in game |

**The two sample maps that differ:**

- `styleguide_urban`: one arc vertex is off by 0.002 units, from a long chain of brush clips. The step responsible wasn't found within the time spent on it. The splitting, offset clipping and brush volume code it passes through do match.
- `styleguide_semiurban`: one detail prop's angle differs (float noise) and two bytes of the brush physics data differ.

Neither is related to instances or any missing feature.

### Speed

hlvbsp was written to match vbsp, not to beat it: it isn't faster than vbsp yet. Making it faster is planned.

On a large user map, the map compile was 3.9 s of a 30 s Build & Play. The compile is not the critical path: vis is.

### Valve's vbsp is not a silent fallback

By design, any time Valve's vbsp runs instead of hlvbsp is treated as a bug to fix. Today:

- No map feature sends a map to vbsp. The list of unsupported features is empty since instances and water overlays were added.
- If hlvbsp fails (non-zero exit), the build runs Valve's vbsp, says so in the log and in *Problems*, and the next build compiles everything again instead of trusting the result.
- Entities-only smart builds use Valve's `vbsp -onlyents` (see below).

The setting is under *Settings > Map & Model Compilers*. Valve's vbsp can still be chosen there.

## Tools for checking

All in `tests/compile/`. They need the game and the Authoring Tools.

| Tool | What it does |
|---|---|
| `bspdump.py` | Dumps an L4D2 .bsp (version 21) as text, one record per line, every lump named. `--diff a.bsp b.bsp` prints the first differing record per lump. Masks the bytes vbsp leaves uninitialised. |
| `compare_vbsp.py` | Compiles .vmf files with both compilers and compares them. `--dir <sdk_content>` runs every .vmf in a folder. |
| `mapcompiler_suite.py` | The 23 generated feature maps, compiled by both and compared lump by lump and by portal file. Run it after changing hlvbsp. Valve's reference is reused while a map's .vmf is unchanged. |
| `decompile_roundtrip.py` | The decompiler's test (below). |
| `decompile_brushdiff.py`, `decompile_faces.py` | What a decompile round trip changed: missing brushes and their nearest match, face area per material. |

```bash
python tests/compile/mapcompiler_suite.py              # every map
python tests/compile/mapcompiler_suite.py water cube   # maps whose name contains these
python tests/compile/bspdump.py ours.bsp valve.bsp --diff
```

## Smart builds

A full compile runs the map compiler, vis and lighting. Most edits don't need all of that. Before each build, `core/buildplan.py` compares the new .vmf with the one the current .bsp was built from, and picks the least work that gives the same map as a full compile.

| Plan | When | What runs |
|---|---|---|
| same | Nothing that reaches the map changed | No compile |
| entities | Only entity keyvalues, outputs or point entities changed | `vbsp -onlyents` rewrites the entity lump; geometry, vis and lighting kept |
| remix | Only the colour or brightness of lights with their own bake layer changed | `-onlyents`, then the lighting mixed from its layers (no bake) |
| lighting | Lights or static props changed, geometry didn't | `-onlyents`, then the light compiler |
| full | Geometry changed | Map compiler, vis, lighting |

The comparison ignores ids and editor-only blocks (visgroups, cameras, cordons). Anything it doesn't recognise counts as geometry, so a doubt always means a full compile.

What counts as geometry: the world, brush entities' brushes, origins and classes, and these entities: `func_detail`, overlays and water overlays, `env_cubemap`, area portals and their window targets, `func_viscluster`, `func_occluder`, `water_lod_control`, `func_instance`, ladders, detail props and sprites, `info_no_dynamic_shadow`, and a few others. The fog controller's far Z counts too, because it makes vis radial.

What counts as lighting: lights, `info_lighting`, static props, a light's aim target, and the brush entity keys the light compiler reads (shadow and minimum light settings).

Extra checks force a full compile:

- an instance file changed (it is hashed);
- a new or moved entity now sits where the last compile had filled the space as solid (vbsp fills rooms no entity reaches, so an entities-only build would keep that room filled);
- the last build fell back to a Valve tool after a failure;
- no portal file was kept from the last compile.

`vbsp -onlyents` leaves a `stale.txt` in the map's pak, which makes the engine treat the map as not final. Hammerless removes it, since the result is equivalent to a full compile.

## Importing maps

### Hammer maps (.vmf): exact round trip

*File > Import > Hammer Map (.vmf)*. Each brush becomes one object, textured with its real alignment. Displacements, entities with all their keyvalues and outputs, brush entities (an Empty holding their brushes) and props as their models come in too.

The goal is that Build writes the map back exactly, so an imported map is never damaged by a round trip. The original file is kept in the .blend, and every object remembers which block it came from. On export:

- A brush whose faces still lie on their sides' planes (within 0.05 units), with the same materials, is written exactly as it was read: texture axes, displacement, ids and all.
- A moved face gets a new plane from its polygon and keeps the rest of its side (texture settings included). A new face gets a side of its own.
- Entities keep their text where their keys, outputs and placement are unchanged.
- Editor data, visgroups and cameras are written back as read. New objects are added the usual way.
- The map's own sky, sun, spawns, Director and soundscapes are kept. Import turns off Hammerless's automatic ones (seal, Director, sun, sound system).
- Instance files are copied next to the built map.

Verified: all 134 of Valve's sample maps come back identical through a round trip in Blender.

Not yet: editing displacements (they keep their imported shape, with a warning), and texture lock on moved faces. Copies of brush entities aren't built; copies of point entities are.

### The game's own maps: the BSP decompiler

*File > Import > L4D2 Map (from the game)* (type to search: c1m1_hotel, c2m1_highway...) or *Compiled Map (.bsp)*. Valve ships no source files for the campaigns, so `core/bspdecompile.py` rebuilds one from the compiled map, and imports it like a Hammer map.

What a compiled map keeps is enough to rebuild most of the source:

- **Brushes.** Every brush is stored as its planes. Each side's polygon is its plane cut by the brush's other planes, and its three plane points come from that polygon. Sides vbsp added to round off corners (bevels) are dropped. Coordinates within 0.01 of an integer are snapped, since float drift adds bevel planes on recompile.
- **Which brushes are whose.** A model's brushes are a contiguous index range under its head node (a walk of the tree alone misses buried brushes). Brushes marked detail in the world were `func_detail`.
- **Textures.** A side's texture axes, scale, shift and lightmap scale come back from its stored texture info. Sides with no visible face lost their material in the compile; their tool texture is worked out from the brush's contents (area portal, clips, block light, ladders, L4D2's infected-only climb...).
- **Displacements** are rebuilt on their brushes with exact vertices and alphas.
- **Overlays, static props, cubemaps, area portals** come back as entities. Patched materials (cubemap, water depth, blend patches) are mapped back to their originals. Keys vbsp adds are stripped.

The Map Name becomes `<map>_remix`, so Build never replaces the game's own map. Props sitting exactly on a wall face are moved 0.05 units out, since vbsp would drop them as outside the map.

**Lost in a compiled map:** instances (already merged in), visgroups, hint and skip brushes, brush grouping.

**Verified** by `decompile_roundtrip.py`: decompile from the game's files, compile again with hlvbsp, and compare the structure (brushes and their planes, models, entities, static props, cubemaps, displacements, overlays, faces) with the original.

| Map | Brushes with the same planes | Other results |
|---|---|---|
| c1m1_hotel | 3856 / 3856 | 43 / 43 displacements with identical vertices and alphas; 149 / 156 overlays (the other 7 draw nothing); 1736 / 1748 static props (12 wall mirrors sit exactly on a wall plane) |
| c3m1_plankcountry | 2335 / 2339 | |
| c5m1_waterfront | 2152 / 2159 | all 184 overlays |
| c2m1_highway | 2014 / 2053 | the rest are oblique planes under 0.1 units off |

Importing c1m1_hotel took 20 s and made 7788 objects.

Publishing a remix of Valve's maps is up to you and Valve's terms.

## Limits and what's left

- **Two sample maps** still differ from vbsp in a few bytes (`styleguide_urban`, `styleguide_semiurban`, above).
- **Speed:** hlvbsp isn't faster than vbsp yet.
- **Entities-only builds** still run Valve's `vbsp -onlyents`.
- **Terrain** is a heightfield: no overhangs or caves in one terrain object.
- **Non-convex meshes** need splitting or the convex hull option; there is no automatic decomposition.
- **Imported maps:** displacements can't be edited yet; no texture lock on moved faces; copies of brush entities aren't built.
- **Decompiled maps:** a handful of brushes on some campaign maps come back with slightly different planes; instances, visgroups and hint/skip brushes can't be recovered.
- **The Authoring Tools** are still needed: Valve's vbsp as the safety net and for `-onlyents`, and the sample instances such as the Car Alarm.
