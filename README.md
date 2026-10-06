# Hammerless

**Build Left 4 Dead 2 maps in Blender.** Model your level, drop in safe rooms, spawns, hordes and logic, press **Build & Play**, and the game opens on your map: compiled, lit, with a working nav mesh and AI Director.

No Hammer, no VMF editing, no console commands.

> [!WARNING]
> **Work in progress (v0.1, test release).** Hammerless builds and plays full maps end to end, but it's still early: expect bugs and changes between versions. **Work on copies of your .blend files** and keep backups: some tools change your scene (converting objects, moving outputs into a logic graph), and a bug could damage a map. Building also replaces the map of the same name in your game's `maps` folder. Please [report bugs](../../issues).

## Features

- **One-click Build & Play**: export, compile (vbsp / vvis / vrad), copy to the game and launch, reusing a running game. **Smart builds** only redo what changed: entity edits skip geometry, unchanged maps skip the compile.
- **Meshes become brushes.** Any convex mesh is a wall or floor; non-convex meshes can use a convex hull. **Terrain** objects become displacements (sculpt them however you like).
- **Game content in Blender**: all 8,000+ L4D2 materials and 5,500 models, with real textured previews in the viewport. Your own image textures are converted automatically.
- **Ready-made presets**: Start / End Safe Room (fully wired), Horde Trigger, Horde Button, Crescendo Button, Tank Ambush, Gate + Button, Ladder, Zombie Ladder, Zombie Climb, Zombie Spawn Area.
- **Logic nodes**: wire map events visually (Path Progress → When → Spawn Tank, buttons, timers, movers, Director settings).
- **Nav mesh made in Blender**: a port of the game's own nav generator builds the mesh while the map compiles (about 0.5 s instead of the game's two extra reloads), plus the game's **nav analysis** (visibility, hiding spots) done in Blender, so the game loads the map once.
- **See what the Director sees**: colour the nav by reachability, path distance, *what can be seen from here*, no-spawn marks, and **where zombies can spawn**.
- **AI Director, lighting, sky and fog settings** in panels. Leak-proof by default (auto skybox seal).

## Requirements

- **Blender 4.2 or newer**
- **Left 4 Dead 2** (Steam) and the **Left 4 Dead 2 Authoring Tools** (*Steam > Library > Tools*), which provide the map compilers
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

Hammerless is a fan-made tool, not affiliated with or endorsed by Valve. Left 4 Dead 2, Source and their content are Valve's; Hammerless doesn't include any game files and reads them from your own installation. The nav mesh generator and nav analysis follow the algorithms in Valve's published Source SDK 2013 nav code, reimplemented and checked against the game's output.
