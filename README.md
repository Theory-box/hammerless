# Hammerless

**Build Left 4 Dead 2 maps in Blender.** Model your level, drop in safe rooms, spawns, hordes and logic, press **Build & Play**, and the game opens on your map: compiled, lit, with a working nav mesh and AI Director.

No Hammer, no VMF editing, no console commands.

> [!WARNING]
> **Work in progress (v0.16, test release).** Hammerless builds and plays full maps end to end, but it's still early: expect bugs and changes between versions. **Work on copies of your .blend files** and keep backups: some tools change your scene, and a bug could damage a map. Building also replaces the map of the same name in your game's `maps` folder. Please [report bugs](../../issues).

## Our own toolchain: rebuilt, measured, faster

Hammerless doesn't just call Valve's tools. Most of the map pipeline has been rebuilt from scratch: each tool reimplemented from published source and the file formats decoded, then checked byte by byte against what Valve's tools and the game produce. That gives three things: speed, features Valve's tools never had, and control over every step. Valve's tools stay as a safety net only.

| System | What we have | Measured |
|---|---|---|
| **Lighting** (vrad) | Our light compiler, ray tracing **on the graphics card** (Vulkan). An *Exact* mode reproduces vrad byte for byte on the CPU. | Final quality on a real map: **1.5 s instead of vrad's 16 s**; Normal 1.2 s instead of 6 s. Exact mode: byte-identical to vrad on all 16 test maps. |
| **Visibility** (vvis) | Our vis compiler, with vvis's own result available byte for byte, or a looser-but-safe faster default that never hides a view through a real opening. | Exact mode on a real map: **5.2 s instead of 14.1 s**; Valve's large sample map: **46 s instead of 2 min 19 s**, identical result. |
| **Map compiler** (vbsp) | Our map compiler, brushes, displacements, props, detail props, water, overlays. | The same map as vbsp, **byte for byte on 132 of Valve's 134 sample maps**. |
| **Nav mesh** | A port of the game's own nav generator, plus the game's nav analysis (visibility, hiding spots), both done in Blender. | Built while the map compiles instead of the game's **two extra reloads**; hiding spots identical to the game's, visibility 99.9%. Analysis on c2m1: 53 s down to 26 s. |
| **Model compiler** | Our own .mdl / .vvd / .vtx / .phy writer for props; animated models through the game's studiomdl. | Static props without any extra tools. |
| **Map loading** | Fast Map Loading for LAN test games. | **About 8 s** from the button to standing in a small map (skips a 6 s Steam wait per load). |

Built on top of those, things the original tools can't do:

- **Live Light Mixing**: bake once, then change any light's colour or brightness and the baked lighting updates instantly in Blender. Build then mixes the result straight into the map in about a second instead of re-baking, measured within 2% of a real bake, bounced light included.
- **Flickering and switchable lights** from a Light panel (the game's patterns or your own), turned on and off by logic.
- **Sky light from the sky**: each part of the sky lights the map with its own colour, from the skybox or an HDRI.
- **See what the engine sees**: vis portals, a rendering-cost heatmap, what slows the vis compile; the nav coloured by reachability, line of sight, no-spawn marks and **where zombies can spawn**.

## Making maps

- **One-click Build & Play**: export, compile, copy to the game and launch. The game boots while the map compiles, and **smart builds** only redo what changed: entity edits skip geometry, light colour changes skip the bake, unchanged maps skip the compile.
- **Meshes become brushes.** Any convex mesh is a wall or floor; non-convex meshes can use a convex hull. **Terrain** objects become displacements: sculpt them, and paint vertex colour to blend two textures.
- **Textures**: all 8,000+ L4D2 materials and 5,500 models, with real textured previews in the viewport. Your own Blender image textures are converted automatically, alpha included (cut-out fences, see-through decals and leaves).
- **Custom Models**: any mesh becomes a game prop (static, dynamic or physics) with collision and your own textures. **Animated models** too: rig it, animate it in Blender, and it plays in the game. Use **the game's own skeletons** to borrow a character's whole animation library, reskin the common infected, or **place characters** sitting, lying or banging on doors as storytelling scenes.
- **Logic nodes**: build map behaviour visually, compiled to the game's scripting (VScript). 60+ nodes cover events, game functions, variables, loops, maths, the AI Director, HUD text and the game's own decision hooks. **Node groups** wrap them into reusable pieces you can open and edit, and **54 worked examples** go from simple buttons to swelling bio-pods, sleepers that wake when you get close, power cuts with flickering lights and Director tricks.
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
| [Development](docs/DEVELOPMENT.md) | Building, tests, code layout, contributing |

## License

[GPL-3.0-or-later](LICENSE), like Blender itself.

Hammerless is a fan-made tool, not affiliated with or endorsed by Valve. Left 4 Dead 2, Source and their content are Valve's; Hammerless doesn't include any game files and reads them from your own installation. The nav mesh generator and nav analysis follow the algorithms in Valve's published Source SDK 2013 nav code, reimplemented and checked against the game's output. The map, vis, light and model compilers are likewise reimplemented (the map compiler after Quake 2's GPL tools) and checked against Valve's tools' output.
