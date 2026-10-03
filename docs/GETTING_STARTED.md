# Getting Started

This walks through a first playable map, then explains the panels.

## 1. Set up

1. Install Hammerless and the L4D2 Authoring Tools (see the [README](../README.md#install)).
2. **Save your .blend first.** Builds go into `hammerless_build/` next to it (change it under *Advanced > Work Folder*).
3. Open the sidebar (**N**) > **Hammerless**.

**Scale:** 1 Blender metre = 52.49 Hammer units by default, so real-size buildings match Valve's proportions (a survivor is about 1.37 m tall). Change it under *Advanced*.

## 2. Build the level

| You want | Do this |
|---|---|
| Walls, floors, buildings | Any **convex** mesh (boxes, wedges, cylinders). One object can hold several separate convex pieces. |
| Something that isn't convex | Split it into convex pieces, or tick **Use Convex Hull** on it (*Selected Object* panel). |
| Terrain | Set the object's (or its collection's) role to **Terrain**. Sculpt freely; it's sampled from above like a heightmap (no overhangs). Paint vertex colour red to blend a blend material's two textures. |
| Game textures | 🔍 next to **Game Material** searches every L4D2 material. Switch the viewport to *Material Preview* to see them. |
| Your own textures | A material with an Image Texture in Base Color is converted automatically. |
| Friction & footsteps | Material panel > **Surface** (e.g. `ice` is slippery). |

The map is sealed automatically (*Advanced > Auto Seal*), so it can't leak. If you turn that off and it leaks, **Load Leak** draws a line to the hole.

## 3. Add the gameplay

Use the **Add** panel (category tiles + search, ★ to favourite) or **Shift+A > L4D2** in the viewport. Everything is placed at the 3D cursor.

1. **Start Safe Room** and **End Safe Room**: room, door, survivor spawns, items, landmark and level-change volume, all wired. Set *Next Map* on the end room to a **different** map than this one (the Director needs it to compute the path; see [Troubleshooting](TROUBLESHOOTING.md#no-zombies-at-all-or-flow-broken)).
2. Keep the four **survivor spawns inside the Start Safe Room box**. If you move or resize the room, move them with it.
3. Weapons, items, props, special-infected spots: categories in the Add panel.
4. Hordes and events: **Horde Trigger**, **Horde Button**, **Crescendo Button**, **Tank Ambush**. See [Hordes & Events](HORDE_EVENTS.md).
5. Gates and lifts: **Gate + Button**, or a *Mover* node in [Logic Nodes](LOGIC_NODES.md).

## 4. Build & Play

Press **Build & Play**. Hammerless exports the map, compiles it, builds the nav mesh at the same time, and launches L4D2 (or reuses the running game).

- **Compile Only** compiles without launching. **Launch Game** loads the last compiled map.
- Builds are **smart**: if only entities changed, geometry and lighting are kept; if nothing changed, the compile is skipped.
- Warnings appear as "N warning(s). See the hammerless_log text block". Open the **Problems** list (or the `hammerless_log` text) to see them; click a row to select the object. Most are informational, e.g. *near misses* (faces that almost line up), which Hammerless fixes when it exports.

## 5. The panels

**L4D2 Map** (main panel)

| Setting | Meaning |
|---|---|
| Map Name | The map's file name in the game (`maps/<name>.bsp`) |
| Quality | Compile preset: *Quick* (no lighting, fastest), *Fast*, *Normal* (default), *Final*, or *Custom* |
| Nav Mesh | *Made in Blender* (fast, default) or *Made by the game* |
| Nav Analysis | *In Blender* (the game loads the map once) or *By the game* (one extra reload) |
| Zombies Climb Walls | Commons can climb any wall up to about 160 units |
| Rebuild Nav Mesh | Force a fresh nav mesh on the next build |

**Sub-panels:** *Compile* (custom vis / lighting / HDR, extra compiler options), *Lighting & Sky* (sky, lightmap scale, sun and sky light; a Blender Sun lamp overrides these), *Fog*, *AI Director* (common limit, horde size and timing, specials, Tanks, Witches; *Director Spawns* hands a type to your logic graph), *Game Window* (monitor, size, borderless, difficulty), *Debug* (*Debug Log* of Director events; *Bot Walkthrough Test* where bots play the map), *Advanced* (scale, default material, auto seal, paths, refresh game data and previews).

**Nav Mesh** panel: see [Nav Mesh & Zombie Spawns](NAV_AND_ZOMBIES.md).

**Selected Object** panel: role (brush, terrain, entity), class, game material, model, keyvalues and outputs (*when X happens, tell Y to do Z*) for the active object.
