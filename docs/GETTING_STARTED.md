# Getting Started

This walks through a first playable map, then explains the panels.

## 1. Set up

> [!WARNING]
> Hammerless is a work in progress. Work on a **copy** of your .blend and keep backups.

1. Install Hammerless and the L4D2 Authoring Tools (see the [README](../README.md#install)).
2. **Save your .blend first.** Builds go into `hammerless_build/` next to it (change it under *Settings > Folders & Game Data > Work Folder*).
3. Open the sidebar (**N**) > **Hammerless**. If L4D2 isn't found, set its folder once in the add-on's Preferences.
4. Start from an empty scene: **delete the default cube** (it would become a solid block in the middle of your map).
5. Give the map its own **Map Name** (*Build & Play* panel). It's the file name in the game, so two .blend files with the same name replace each other's map (Hammerless warns you).

**Scale:** 1 Blender metre = 52.49 Hammer units by default, so real-size buildings match Valve's proportions (a survivor is about 1.37 m tall). Change it under *Settings > Scene*.

## 2. Build the level

| You want | Do this |
|---|---|
| Walls, floors, buildings | Any **convex** mesh (boxes, wedges, cylinders). One object can hold several separate convex pieces. |
| Something that isn't convex | Split it into convex pieces, or tick **Use Convex Hull** on it (*Selected Object* panel). |
| Faster vvis (furniture, trim, piles of overlapping boxes) | Set **Detail** to *Detail* (*Selected Object* panel, or on a whole collection in its Properties tab). Detail brushes still block players and cast shadows but don't cut up visibility. Small and round brushes become detail on their own (*Settings > Compile > Auto Detail*); set *World* on a small wall that should block visibility. |
| Props of any shape (statues, furniture, crates, pickups) | Set the object's role to **Custom Model**: the mesh becomes a game model, placed where the object is. **Prop**: *Static* (part of the map), *Dynamic* (logic can move or hide it; name it with a `targetname` keyvalue) or *Physics* (falls, can be pushed; pick a **Physics Class** from the game's list, which sets its weight feel and health). **Collision**: each loose part wrapped in its convex hull, or none. Copies sharing the mesh (Alt+D) share one model. Hammerless writes the model files itself (*Settings > Compile > Model Compiler* can switch to the game's studiomdl instead). |
| Terrain | Set the object's (or its collection's) role to **Terrain**. Sculpt freely; it's sampled from above like a heightmap (no overhangs). Paint vertex colour red to blend a blend material's two textures. |
| Game textures | 🔍 next to **Game Material** searches every L4D2 material. Switch the viewport to *Material Preview* to see them. |
| Your own textures | A material with an Image Texture in Base Color is converted automatically. |
| Friction & footsteps | *Selected Object > Material* > **Surface** (e.g. `ice` is slippery). The same panel has *Texture Scale* and *Lightmap Scale*. |

The map is sealed automatically (*Settings > Scene > Auto Seal (skybox shell)*), so it can't leak. If you turn that off and it leaks, **Load Leak** draws a line to the hole.

## 3. Add the gameplay

Use the **Add** panel (category tiles + search, ★ to favourite) or **Shift+A > L4D2** in the viewport. Everything is placed at the 3D cursor.

1. **Start Safe Room** and **End Safe Room**: room, door, survivor spawns, items, landmark and level-change volume, all wired. Set *Next Map* on the end room to a **different** map than this one (the Director needs it to compute the path; see [Troubleshooting](TROUBLESHOOTING.md#no-zombies-at-all-or-flow-broken)).
2. Keep the four **survivor spawns inside the Start Safe Room box**. If you move or resize the room, move them with it.
3. Weapons, items, props, special-infected spots: categories in the Add panel.
4. Hordes and events: **Horde Trigger**, **Horde Button**, **Crescendo Button**, **Tank Ambush**. See [Hordes & Events](HORDE_EVENTS.md).
5. Gates and lifts: **Gate + Button**, or a *Mover* node in [Logic Nodes](LOGIC_NODES.md).

## 4. Build & Play

Press **Build & Play**. Hammerless exports the map, compiles it, builds the nav mesh at the same time, and launches L4D2 (or reuses the running game).

- **Build** does everything Build & Play does (compile, baked lighting, nav mesh) without starting the game. **Play** starts the game on the last build.
- **Bake Lighting** (in *Lighting > Baked Lighting*) bakes only the lighting, quickly, and shows it in the viewport. Build & Play reuses that bake.
- Builds are **smart**: if only entities changed, geometry and lighting are kept; if nothing changed, the compile is skipped.
- Warnings appear as "N warning(s). See the hammerless_log text block". The *Problems* list (in the *Build & Play* panel, with **Check the Map** to re-check without building) shows them, as does the `hammerless_log` text; click a row to select the object. Most are informational, e.g. *near misses* (faces that almost line up), which Hammerless fixes when it exports.

## 5. The panels

Everything is in the sidebar (**N**) > **Hammerless** tab. Each panel collapses, and you can drag panels by their header to reorder them. Collapsed panels show their status on the right (problem count, nav mesh up to date, lighting baked...).

**Build & Play**: Map Name (the map's file name in the game, `maps/<name>.bsp`), Quality (*Quick*: no lighting or visibility, fastest; *Fast*; *Normal*, the default; *Final*; *Ultra*: it sets the Lighting and Visibility panels' own Quality together, and shows *Mixed* when you've set them differently), the **Build & Play**, **Build** and **Play** buttons, and **Problems**: the list from the last check or build, with **Check the Map** to check without building. Click a row to select the object.

**Add**: category tiles, search and ★ favourites for everything you can place.

**Selected Object**: the active object's role (brush, terrain, entity, or *Ignore* to leave it out) and class. Below it, only what applies to that object:

| Sub-panel | Shown for | What's in it |
|---|---|---|
| Material | brushes, terrain | Game Material (🔍 searches every L4D2 material), Surface (friction and footsteps), Texture Scale, Lightmap Scale |
| Model | props and items | The game model (🔍 picks one) |
| Terrain | terrain | Detail and patch size |
| Settings | entities | Keyvalues, with *Reset to Defaults* |
| Outputs | entities | *When something happens here, tell another object to do something* |

A preset's parent empty shows the preset's settings and **Select All Parts**. Collections get a role too: *Properties > Collection > Hammerless*.

Each system has its own panel with its compiler, its settings and its *View* (what it draws over the viewport):

**Lighting**: Light Compiler (Valve vrad, Hammerless, or Cycles) and *Exact Lighting*; Quality (*Off*, *Fast*, *Normal*, *Final*, *Ultra*, or *Custom*), which fills in the settings under it: Sky Rays, Supersampling (Points, Passes, Edge Threshold), Bounces, Bounce Patch Size, Prop Lighting, Prop Shadows from Full Model, Fix vrad's Quirks (changing one makes it *Custom*); Lightmap Scale. Under it: *Sky & Sun* (sky picker, sun and sky light, Add Sun if Missing; a Blender Sun lamp overrides these), *Baked Lighting* (**Bake Lighting** and the lighting view) and *Advanced* (HDR, extra vrad options).

**Visibility**: Quality (*Off*, *Fast*, *Full*), Vis Compiler, Auto Detail, extra vvis options. Its *View*: *Portals* (how vis split the map up; tiny slivers in red), *Rendering Load* (the map coloured by how much the game draws from each spot: red is where frame rate suffers first, with a button to put the 3D cursor on the worst spot), and *Vis Cost* (where the vis compile spent its time, and the objects behind it, each with a Select button: making decorative ones Detail speeds vis up; needs *Vis Compiler: Hammerless*).

**Nav Mesh**: made in Blender or by the game, the analysis, Zombies Climb Walls, Rebuild Nav Next Time. Its *View*: the nav viewer (Build Navmesh, Analyze, colour modes, the game's nav).

**Sound**: *Automatic Reverb*, or *Reverb + City Ambience* with the outside heard through doorways and windows. Its *View*: **Trace Sound** (outdoors, sheltered, indoors, and the sound portals where the outside comes in).

**World**: *Fog* (tick the box in its header), *AI Director* (tick the box in its header to customise it: common limit, horde size and timing, No Random Hordes, No Wandering Zombies, specials, Tanks, Witches; *Director Spawns* hands a type to your logic graph), *Logic Graphs* (see [Logic Nodes](LOGIC_NODES.md)).

**Settings**: *Compile* (the map and model compilers, extra vbsp options), *Game Window* (monitor, size, borderless, difficulty, launch options), *Folders & Game Data* (L4D2 folder, work folder, reload game data, refresh previews, **Export VMF** to open the map in Hammer), *Scene* (scale, default material, auto seal, checks), *Debug* (*Debug Log* of Director events, *Bot Walkthrough Test* where bots play the map).
