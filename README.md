# Hammerless

**Build Left 4 Dead 2 maps in Blender.** Model your level, drop in safe rooms, spawns, hordes and logic, press **Build & Play**, and the game opens on your map: compiled, lit, with a working nav mesh and AI Director.

No Hammer, no VMF editing, no console commands.

> [!WARNING]
> **Work in progress (v0.20.30, test release).** Hammerless builds and plays full maps end to end, but it's still early: expect bugs and changes between versions. **Work on copies of your .blend files** and keep backups: some tools change your scene, and a bug could damage a map. Building also replaces the map of the same name in your game's `maps` folder. Please [report bugs](../../issues).

## Our own toolchain: rebuilt, measured, faster

Hammerless doesn't just call Valve's tools. Most of the map pipeline has been rebuilt from scratch: each tool reimplemented from published source, the file formats decoded, then checked byte by byte against what Valve's tools and the game produce. That gives three things: speed, features Valve's tools never had, and control over every step. A fall back to Valve's tools is treated as a bug to fix.

| System | What we have | Measured | In depth |
|---|---|---|---|
| **Lighting** (vrad) | Our light compiler, ray tracing **on the graphics card** (Vulkan). *Exact* mode reproduces vrad byte for byte on the CPU. | Final quality on a real map: **1.5 s instead of vrad's 16 s**; Normal 1.2 s instead of 6 s. Exact mode: byte-identical to vrad on all 16 test maps. | [Lighting](docs/systems/LIGHTING.md) |
| **Visibility** (vvis) | Our vis compiler: vvis's own result byte for byte, or a faster default that never hides a view through a real opening. | Exact mode on a real map: **5.2 s instead of 14.1 s**; Valve's large sample map: **46 s instead of 2 min 19 s**, identical result. | [Visibility](docs/systems/VISIBILITY.md) |
| **Map compiler** (vbsp) | Our map compiler: brushes, displacements, props, detail props, water, overlays, instances. | The same map as vbsp, **byte for byte on 132 of Valve's 134 sample maps**. | [Map Compiler](docs/systems/MAP_COMPILER.md) |
| **Nav mesh** | A port of the game's own nav generator and nav analysis (visibility, hiding spots), run in Blender during the compile. | Built while the map compiles instead of the game's **two extra reloads**; hiding spots identical to the game's, visibility 99.9%. Analysis on c2m1: 53 s down to 26 s. | [Nav Mesh](docs/systems/NAV_MESH.md) |
| **Model compiler** | Our own .mdl / .vvd / .vtx / .phy writer for props; animated models through the game's studiomdl. | Static, dynamic and physics props without any extra tools. | [Models & Textures](docs/systems/MODELS_AND_TEXTURES.md) |
| **Logic** | Node graphs compiled to the game's own scripting and entity I/O, with the game's script API mapped from the running game. | 62 worked examples, most checked automatically in the running game. All 2,631 entity properties, read from the game itself. | [Logic & Scripting](docs/systems/LOGIC_AND_SCRIPTING.md) |
| **Build & map loading** | Steps run side by side, smart builds redo only what changed, the game boots while the map compiles. | **About 8 s** from the button to standing in a small map. | [Build & Play](docs/systems/BUILD_AND_PLAY.md) |

### A whole build, from scratch, on a real map

Build & Play on a user's map (an imported 4.6 MB Hammer map, 3,134 objects, 32 animated props), every system fresh: a new map name and build folder each run, the game closed before each run, Normal lighting (HDR) and Full visibility on both sides. Each run was checked for any fall back to the other side's tools (none).

| Step | All Valve's tools (1 run) | All Hammerless (median of 3) | |
|---|---|---|---|
| Export from Blender | 4.3 s | 4.0 s | same work |
| Map compiler | 4.5 s | 5.4 s | ours 1.2× slower here |
| Visibility (Full) | 541 s | 6.1 s | **89× faster** |
| Lighting (Normal, HDR) | 23.3 s | 8.8 s, alongside vis | **2.6× faster** |
| Nav mesh and its analysis | 24 s inside the game, plus 2 extra map loads | 2.4 s + 3.2 s, during the compile | |
| **Button to standing in the map** | **623 s (10 min 23 s)** | **28 s** | **22× faster** |

The three Hammerless runs agreed within 1.5 s; Valve's side ran once, as its vis alone takes 9 minutes. Blender itself waits about 5 s of that on a first build (a fresh Blender reading the map), about 1 s on later builds. Our vis default is about 1% looser than vvis (its exact mode matches vvis byte for byte); our lighting on the graphics card looks the same as vrad's but isn't byte-identical (its exact mode is). Machine: AMD Ryzen 7 7800X3D (8 cores), NVIDIA RTX 4090, 64 GB RAM, Windows 10, Blender 4.4, Hammerless 0.17 (2026-10-14).

## The systems in depth

### Lighting

Our light compiler, **hlvrad**, was built stage by stage against vrad: vrad's DLL was disassembled and hooked to dump its internal state, and its floating-point order, ray tracer, sampling patterns and even its bugs were reproduced until the whole output matched byte for byte. *Exact* mode keeps that. The default runs on the graphics card instead, with Vulkan ray queries, and matches 99.99% of lightmap bytes. On top of vrad's lighting it adds **ray-traced bounce** (light gathered at every luxel instead of patch blotches), **sky light from the sky** (each part of a skybox or HDRI lights with its own colour), **partial bakes** that keep the rest of the map's light, **flickering and switchable lights**, and **Live Light Mixing**: one bake writes every light's own layer, so changing a light's colour or brightness updates the lighting in Blender at once, and the next build mixes it into the map in about a second instead of baking (within 2% of a real bake). [Read more](docs/systems/LIGHTING.md)

### Visibility

Our vis compiler, **hlvvis**, has two modes. *Exact* runs vvis's own path search and gives its output byte for byte, about 3 times faster. The default, **merged windows**, runs the same search through merged portal openings: several times faster again, about 1% looser than vvis, and checked so it never hides a pair of rooms that can see each other through a real opening (a ray-based checker tests random sight lines at every pair it hides). Blender can show the vis portals, a rendering-cost heatmap and what slows the vis compile, so you can fix a slow map before building it. [Read more](docs/systems/VISIBILITY.md)

### Map compiler

Blender meshes become brushes (any convex mesh, or a convex hull), terrain becomes displacements with vertex-colour blending, small detail is turned into detail brushes automatically, and the map is sealed against leaks by default. Our map compiler, **hlvbsp**, is written in C to match vbsp's 32-bit arithmetic and writes the same file as vbsp on 132 of Valve's 134 sample maps. **Smart builds** compare each build with the last: entity edits skip the geometry, light colour changes skip the bake, unchanged maps skip the compile. It also works backwards: Hammer maps (.vmf) import and write back exactly, and compiled maps, the game's own campaigns included, decompile into editable scenes. [Read more](docs/systems/MAP_COMPILER.md)

### Nav mesh and the AI Director

We decoded L4D2's `.nav` format (every game nav round-trips byte for byte), ported the game's nav generator with L4D2's own constants, and reproduced its nav analysis: hiding spots identical to the game's, visibility lists 99.9% the same. It all runs in Blender during the compile, so the game loads the map once instead of generating, analysing and reloading. We also measured how the Director works: where it spawns wandering commons and specials (rules read from `server.dll`), how flow is measured, and where items go. Blender shows the nav coloured by reachability and line of sight, and **where zombies can spawn**, before you build. [Read more](docs/systems/NAV_MESH.md)

### Models and textures

Any mesh becomes a game prop with collision and your own textures: our own model writer produces the game's .mdl, .vvd, .vtx and .phy files, checked against studiomdl's. Animated models go through studiomdl: rig and animate in Blender, use **the game's own skeletons** to borrow a character's animation library, reskin the common infected, or place characters in poses. Blender image textures convert to the game's format automatically, alpha included. All of the game's 8,000+ materials and 5,500 models are read from its packed files, with textured previews in the viewport. [Read more](docs/systems/MODELS_AND_TEXTURES.md)

### Logic and scripting

Node graphs compile to what the game already runs: entity outputs where Hammer's I/O can do the job, and one generated VScript file for the rest. The game's script API (419 functions, 381 events) was dumped from the running game, and its Director settings and decision hooks were found in `server.dll`. Hammerless's own small game mode lets maps override the game's decisions (damage, weapon spawns) and show a HUD. Nodes stay generic building blocks; whole behaviours are built from them as **node groups** with exposed settings. The worked examples are built and checked in the running game by an automated test bench (the few that need real play are checked by hand). [Read more](docs/systems/LOGIC_AND_SCRIPTING.md)

### Build & Play

One button: the game boots while the map compiles, lighting runs alongside vis, and the nav is built during the compile. Each build's log ends with a timeline of what ran when and what it waited on. Two findings cut map loads further: **Fast Map Loading** skips a 6-second Steam registration on every load, and carrying the game's **stringtable dictionary** into each new build saves about 10 s on the first load. [Read more](docs/systems/BUILD_AND_PLAY.md)

### Also built on top

- **See what the engine sees**: vis portals, a rendering-cost heatmap, what slows the vis compile; the nav coloured by reachability, line of sight, no-spawn marks and where zombies can spawn; the baked lighting drawn in the viewport.
- **Flickering and switchable lights** from a Light panel (the game's patterns or your own), turned on and off by logic.

## Making maps

- **One-click Build & Play**: export, compile, copy to the game and launch. The game boots while the map compiles, and **smart builds** only redo what changed: entity edits skip geometry, light colour changes skip the bake, unchanged maps skip the compile.
- **Meshes become brushes.** Any convex mesh is a wall or floor; non-convex meshes can use a convex hull. **Terrain** objects become displacements: sculpt them, and paint vertex colour to blend two textures.
- **Textures**: all 8,000+ L4D2 materials and 5,500 models, with real textured previews in the viewport. Your own Blender image textures are converted automatically, alpha included (cut-out fences, see-through decals and leaves).
- **Custom Models**: any mesh becomes a game prop (static, dynamic or physics) with collision and your own textures. **Animated models** too: rig it, animate it in Blender, and it plays in the game. Use **the game's own skeletons** to borrow a character's whole animation library, reskin the common infected, or **place characters** sitting, lying or banging on doors as storytelling scenes.
- **Logic nodes**: build map behaviour visually, compiled to the game's scripting (VScript). 60+ nodes cover events, game functions, variables, loops, maths, the AI Director, HUD text, the game's own decision hooks and **any property the game keeps on an entity** (health, speed, glow, colour, model scale, ammo...). **Node groups** wrap them into reusable pieces you can open and edit, and **62 worked examples** go from simple buttons to swelling bio-pods, sleepers that wake when you get close, power cuts with flickering lights and Director tricks.
- **Ready-made presets**: Start / End Safe Room (fully wired), Horde Trigger, Horde Button, Crescendo Button, Tank Ambush, Gate + Button, Ladder, Zombie Ladder, Zombie Climb, Zombie Spawn Area, and Valve's own **Car Alarm**.
- **Import maps:** Hammer maps (.vmf), written back exactly, and the game's own maps (c1m1_hotel...) or any compiled .bsp, decompiled to remix.
- **A panel per system** (Lighting, Visibility, Nav Mesh, Sound), each with its own Quality, settings and viewport view; the Map panel's Quality sets them all at once. Leak-proof by default (auto skybox seal).

## Requirements

- **Blender 4.2 or newer**
- **Left 4 Dead 2** (Steam) and the **Left 4 Dead 2 Authoring Tools** (*Steam > Library > Tools*): Hammerless's own compilers do the work, but the Authoring Tools are still needed (Valve's tools as a fallback, studiomdl for animated models, Valve's instances such as the Car Alarm)
- Windows (the compilers are Windows programs)

## Install

1. Download `hammerless-<version>.zip` from [Releases](../../releases) (one zip for Blender 4.0 to 4.5; or build it yourself, see [Development](docs/DEVELOPMENT.md)).
2. Install it, without unzipping:
   - **Blender 4.2 or newer:** *Edit > Preferences > Get Extensions*, then **⌄** (top right) **> Install from Disk…** and pick the zip.
   - **Blender 4.0 or 4.1:** *Edit > Preferences > Add-ons > Install…*, pick the zip, then tick **Hammerless** in the list.
3. In the 3D view press **N** and open the **Hammerless** tab. If L4D2 isn't found automatically, set its folder in the add-on's Preferences (*Edit > Preferences > Add-ons > Hammerless*), or per file under *Settings > Game & Folders*.

## Quick start

1. Delete the default cube, give the map its own **Map Name**, and save your .blend (the build goes into a `hammerless_build` folder next to it).
2. **Shift+A > L4D2 > Start Safe Room**, then the same for **End Safe Room** further away (or use the *Add* panel: *Safe Rooms* tile, pick the room, **Add at Cursor**). Connect them with walkable floors.
3. Press **Build & Play**.

The full walkthrough is in **[Getting Started](docs/GETTING_STARTED.md)**.

## Documentation

| Guide | What's in it |
|---|---|
| [Getting Started](docs/GETTING_STARTED.md) | Your first map, step by step, and what each panel does |
| [Nav Mesh & Zombie Spawns](docs/NAV_AND_ZOMBIES.md) | Nav settings, the nav views, and why an area does or doesn't get zombies |
| [Hordes & Events](docs/HORDE_EVENTS.md) | How the AI Director spawns infected; hordes, crescendos, Tanks |
| [Logic Nodes](docs/LOGIC_NODES.md) | The node editor for map logic, with every node |
| [Animated Models](docs/ANIMATED_MODELS.md) | Animated Custom Models, the game's own skeletons, reskinning the infected |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common problems and what to do |
| [How each system works](docs/systems/) | In depth: [Lighting](docs/systems/LIGHTING.md), [Visibility](docs/systems/VISIBILITY.md), [Map Compiler](docs/systems/MAP_COMPILER.md), [Nav Mesh](docs/systems/NAV_MESH.md), [Models & Textures](docs/systems/MODELS_AND_TEXTURES.md), [Logic & Scripting](docs/systems/LOGIC_AND_SCRIPTING.md), [Build & Play](docs/systems/BUILD_AND_PLAY.md) |
| [Development](docs/DEVELOPMENT.md) | Building, tests, code layout, contributing |

## License

[GPL-3.0-or-later](LICENSE), like Blender itself.

Hammerless is a fan-made tool, not affiliated with or endorsed by Valve. Left 4 Dead 2, Source and their content are Valve's; Hammerless doesn't include any game files and reads them from your own installation. The nav mesh generator and nav analysis follow the algorithms in Valve's published Source SDK 2013 nav code, reimplemented and checked against the game's output. The map, vis, light and model compilers are likewise reimplemented (the map compiler after Quake 2's GPL tools) and checked against Valve's tools' output.

### Survival and purchase examples

Choose **Map > Game Mode > Survival** for native holdout play. See [Survival setup](docs/survival.md), [Personal Kill Points](docs/personal-points.md), [item purchases](docs/wall-weapons.md), [paid barriers](docs/paid-barriers.md), and [per-object Behaviour settings](docs/behaviour-settings.md).
