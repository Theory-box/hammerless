# Hammerless

**Build Left 4 Dead 2 maps in Blender.** Model your level, drop in safe rooms, spawns, hordes and logic, press **Build & Play**, and the game opens on your map: compiled, lit, with a working nav mesh and AI Director.

No Hammer, no VMF editing, no console commands.

> [!WARNING]
> **Work in progress (v0.10, test release).** Hammerless builds and plays full maps end to end, but it's still early: expect bugs and changes between versions. **Work on copies of your .blend files** and keep backups: some tools change your scene (converting objects, moving outputs into a logic graph), and a bug could damage a map. Building also replaces the map of the same name in your game's `maps` folder. Please [report bugs](../../issues).

## Features

- **One-click Build & Play**: export, compile, copy to the game and launch. The game starts the moment you press the button, so it boots while the map compiles, and a running game is reused. **Fast Map Loading** (LAN-only test games) skips a 6-second Steam wait on every load: from button to map in about 8 seconds on a small map. **Smart builds** only redo what changed: entity edits skip geometry, unchanged maps skip the compile.
- **Hammerless's own compilers**, each checked against the output of Valve's:
  - **Map compiler** (the default): the same map as vbsp, byte for byte on Valve's own sample maps.
  - **Vis compiler**: the same visibility data as vvis, about 3 times faster.
  - **Light compiler**: lights the map **on the graphics card** (Vulkan ray tracing): looks the same as vrad's lighting and is several times faster (Final quality on a real map: 1.5 s against vrad's 16 s). **Exact Lighting** reproduces vrad byte for byte on the CPU instead. Fast, Normal, Final and **Ultra** quality (beyond vrad: finer supersampling, vrad's oddities fixed), each setting visible and adjustable; prop and grass lighting.
  - **Model compiler** (the default): Custom Models without studiomdl.
  - Anything one of them doesn't do yet goes to Valve's tool, with a note in the log.
- **Meshes become brushes.** Any convex mesh is a wall or floor; non-convex meshes can use a convex hull. **Terrain** objects become displacements (sculpt them however you like).
- **Custom Models**: any mesh becomes a game prop (static, dynamic or physics), with collision, your own textures or the game's, and no extra tools.
- **Game content in Blender**: all 8,000+ L4D2 materials and 5,500 models, with real textured previews in the viewport. Your own image textures are converted automatically.
- **Ready-made presets**: Start / End Safe Room (fully wired), Horde Trigger, Horde Button, Crescendo Button, Tank Ambush, Gate + Button, Ladder, Zombie Ladder, Zombie Climb, Zombie Spawn Area.
- **Logic nodes**: wire map events visually (Path Progress → When → Spawn Tank, buttons, timers, movers, Director settings).
- **Nav mesh made in Blender**: a port of the game's own nav generator builds the mesh while the map compiles (about 0.5 s instead of the game's two extra reloads), plus the game's **nav analysis** (visibility, hiding spots) done in Blender, so the game loads the map once.
- **See how the map renders**: the vis portals, a heatmap of how much the game draws from each spot, and which objects make the vis compile slow.
- **See what the Director sees**: colour the nav by reachability, path distance, *what can be seen from here*, no-spawn marks, and **where zombies can spawn**.
- **Sky light from the sky**: instead of one flat sky colour, each part of the sky lights the map with its own colour, from the skybox you picked (so the light matches the sky) or from an HDRI image. Props are lit per vertex by default.
- **A panel per system** (Lighting, Visibility, Nav Mesh, Sound), each with its own Quality, settings and viewport view; Build & Play's Quality sets them all at once. AI Director and fog settings in World. Leak-proof by default (auto skybox seal).

## Requirements

- **Blender 4.2 or newer**
- **Left 4 Dead 2** (Steam) and the **Left 4 Dead 2 Authoring Tools** (*Steam > Library > Tools*): Hammerless's own compilers do most of the work, but Valve's are still the fallback (and the Authoring Tools are still required for now)
- Windows (the compilers are Windows programs)

## Install

1. Download `hammerless-<version>.zip` from [Releases](../../releases) (one zip for Blender 4.0 to 4.5; or build it yourself, see [Development](docs/DEVELOPMENT.md)).
2. Install it, without unzipping:
   - **Blender 4.2 or newer:** *Edit > Preferences > Get Extensions*, then **⌄** (top right) **> Install from Disk…** and pick the zip.
   - **Blender 4.0 or 4.1:** *Edit > Preferences > Add-ons > Install…*, pick the zip, then tick **Hammerless** in the list.
3. In the 3D view press **N** and open the **Hammerless** tab. If L4D2 isn't found automatically, set its folder in the add-on's Preferences (*Edit > Preferences > Add-ons > Hammerless*), or per file under *Settings > Folders & Game Data*.

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
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common problems and what to do |
| [Development](docs/DEVELOPMENT.md) | Building, tests, code layout, contributing |

## License

[GPL-3.0-or-later](LICENSE), like Blender itself.

Hammerless is a fan-made tool, not affiliated with or endorsed by Valve. Left 4 Dead 2, Source and their content are Valve's; Hammerless doesn't include any game files and reads them from your own installation. The nav mesh generator and nav analysis follow the algorithms in Valve's published Source SDK 2013 nav code, reimplemented and checked against the game's output. The map, vis, light and model compilers are likewise reimplemented (the map compiler after Quake 2's GPL tools) and checked against Valve's tools' output.
