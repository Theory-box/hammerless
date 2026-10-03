# Changelog

## Unreleased

### Added
- **Nav mesh made in Blender**: a port of L4D2's nav generator (with native code) builds the nav while the map compiles.
- **Nav analysis in Blender**: visibility and hiding spots computed like the game's `nav_analyze` (visibility matches 99.92% of area pairs, hiding spots exactly), so the game loads the map once. Solid props, doors and brush entities block sight as they do in game.
- **Smart builds**: entity-only changes skip geometry and lighting; unchanged maps skip the compile.
- Nav views: *Where zombies can spawn*, *Zombie spawn marks* (EMPTY / NO_MOBS / OBSCURED), *What can be seen from the 3D cursor*, hiding spots.
- **Build Navmesh / Analyze Navmesh** buttons that turn into **Clear Navmesh / Clear Analysis**. Analyze compiles the map first when walls changed.
- A nav whose analysis never got saved is analyzed on the next launch.

### Fixed
- **Rooms behind a gate got no wandering zombies**: Mover gates no longer block the nav by default (L4D2 blocks from map load, before the Director places zombies).
- A lit map loaded into a running game after an unlit one looked unlit (`mat_fullbright` stayed on).
- Build Navmesh and Analyze Navmesh now export the map exactly as Build does.

## 0.1.0

First version: Blender scenes to L4D2 maps with Build & Play, presets, game materials and models, logic nodes, Director / lighting / fog settings.
