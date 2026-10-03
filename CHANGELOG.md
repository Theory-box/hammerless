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
- **Pre-release audit, round 1 (export and compile):**
  - A map built away from the world origin leaked from Hammerless's own helper entities.
  - Material *Surface* was ignored on the first build after opening Blender.
  - Collection instances and geometry-node instances were dropped; they now export.
  - Transparent textures rendered opaque (now `$alphatest` / `$translucent`). Different materials could overwrite each other's texture, and the wrong image (e.g. a normal map) could be used as the colour.
  - A stale map could be marked up to date when the scene changed during a compile; two compiles of one map could run at once.
  - After *Compile Only* the old nav mesh was kept; now the next Build & Play makes a new one.
  - Brush planes from three points on a line; terrain patches near the size limit broke the compile; tool brushes (hint, clip, areaportal) were made func_detail; spawns snapped up onto tables.
  - Map Name is cleaned up (lowercase, `a-z 0-9 _`).
- **Rooms behind a gate got no wandering zombies**: Mover gates no longer block the nav by default (L4D2 blocks from map load, before the Director places zombies).
- A lit map loaded into a running game after an unlit one looked unlit (`mat_fullbright` stayed on).
- Build Navmesh and Analyze Navmesh now export the map exactly as Build does.

## 0.1.0

First version: Blender scenes to L4D2 maps with Build & Play, presets, game materials and models, logic nodes, Director / lighting / fog settings.
