# Changelog

## 0.9.0 (test release, 2026-10-08)

- **Lighting on the graphics card:** the Hammerless light compiler now lights the map on the GPU (Vulkan ray tracing: NVIDIA RTX, AMD RX 6000 and newer, Intel Arc). Direct light, supersampling, the light for moving models (leaf ambient) and static prop lighting all run there. Measured on a real map with an RTX 4090: Normal quality 2.2 s → 1.2 s, Final 7.3 s → 1.5 s. It looks the same as vrad's: 99.99% of lightmap pixels come out byte-identical (Final 99.92%; the rest are near-black pixels and single pixels on shadow edges), 99.996% of prop vertices, and the leaf ambient differs from vrad's no more than two vrad runs differ from each other. Without a capable card it lights on the CPU as before.
- **Exact Lighting (match vrad)** (*Settings > Compile*, under Light Compiler: Hammerless): ticked, the light compiler reproduces vrad's lighting bit for bit on the CPU instead (slower). Switching it relights the next build.
- The light compiler is now 64-bit; prop collision for shadows comes from a small helper (`hlphys.exe`) that runs the game's 32-bit physics library. The output is unchanged.

## 0.8.0 (test release, 2026-10-08)

- **Sky light from the sky (World > Sky & Sun > Sky Light):** instead of one colour from every direction, each part of the sky lights the map with its own colour, taken from the map's skybox (so the light matches the sky you see: warm from a sunset horizon, blue from above) or from an HDRI image (the picked one or the World's Environment Texture, with a rotation). The overall level stays your Sky Light Brightness: a floor under open sky gets as much light as before. Bounced light, props, grass and the light on zombies and survivors follow it. Needs the Hammerless light compiler (Valve's vrad lights the sky with one colour, with a note in the log).
- **Faster Build & Play:** the game starts as soon as you press Build & Play, so it boots while the map exports and compiles, and the map is sent to it once it reaches its menu. **Fast Map Loading** (*Settings > Game Window*, on by default) tests as a LAN-only game (`sv_lan 1`): measured, the map loads about 6 seconds faster, because the game no longer registers with Steam's servers on every load (untick it to let friends join online). From pressing the button to the map loaded: 18 s before, 8 s now (with a 4-second build).
- **Props lit per vertex by default:** Normal quality now bakes static prop lighting (`-StaticPropLighting`), so the game doesn't do it at every map load and props are lit better (Fast quality still skips it; Custom quality has it ticked by default).
- **Our own light compiler (Settings > Compile > Light Compiler: Hammerless):** a replacement for Valve's vrad: direct light (point, spot, sun with its spread, sky, light-emitting textures), bounced light, supersampling, displacements, prop shadows, the light for moving models (leaf ambient), grass and detail props, static prop vertex lighting, and Final quality (16 times the sky rays, props shadowing with their full model). Checked against vrad: test maps come out byte-identical with supersampling off; with it on, a few shadow-edge pixels differ slightly. About 3 times faster than vrad on all cores (a real map: 2.1 s against 6 s; Final 8 s against 16 s). Fast quality, LDR, extra vrad options and any failure use Valve's vrad, with a note in the log.

- **Our own map compiler (Settings > Compile > Map Compiler: Hammerless):** a replacement for Valve's vbsp, built the way Quake 2's map tools work (GPL) and checked against vbsp lump by lump: brushes and CSG, the BSP tree, portals, the leak check (with the same leak line), faces, detail brushes, displacements (neighbours, LOD vertices, lightmap samples), brush entities, areaportals (with their clip outlines and vbsp's areaportal leak line), physics collision (through the game's own vphysics.dll, like vbsp), static props, the default cubemap, detail props (grass from materials' %detailtype on faces and terrain, with the same random placement as vbsp, and prop_detail entities), overlays (info_overlay, on brushes and terrain; named ones stay for the game's scripts), water (water volumes and their fog data, the per-depth water materials in the map, the material seen from below, fluid physics, the automatic water_lod_control), cubemaps (env_cubemap: each shiny surface gets its own material patched to the cubemap it was given or the nearest one, and a placeholder cubemap texture until the game's buildcubemaps), info_no_dynamic_shadow, func_viscluster (the leaves it covers share one vis cluster), func_occluder, 3D skyboxes (sky_camera: its area left out of the world bounds, one vis cluster), blend materials on ordinary brush faces (their LightmappedGeneric copies, as vbsp writes them). On the test maps and the demo level the map comes out identical to vbsp's (the displacement physics differs only as much as vbsp's own runs do; on a 29,000-prop grass map every detail prop is byte-identical: Valve's x87 float maths was read from vbsp and vstdlib and reproduced step by step), and plays in the game. Not done yet: water overlays, instances; maps with those (and any failure) are compiled by Valve's vbsp, with a note in the log. Hammerless's map compiler is now the default. Checked on Valve's own sample maps (the Authoring Tools' sdk_content): 124 of 125 come out identical to vbsp's (the last differs by 0.002 units in one vertex); this found and fixed ladders (they stay climbable func_simpleladder brush models, as L4D2's vbsp makes them), no grass under water or inside func_detail_blocker, and several small rounding differences.
- **Custom Models face the way they do in Blender.** studiomdl turns every model 90 degrees about the vertical axis (measured: the stored vertices of a compiled model); Hammerless now writes them turned back, so model space is exactly the object's space.
- **Our own model compiler (the default):** Hammerless writes Custom Models' .mdl, .vvd, .dx90.vtx and collision .phy itself, no studiomdl or Authoring Tools needed (*Settings > Compile > Model Compiler* can switch back to studiomdl). Checked against studiomdl: identical vertices, normals, UVs and triangles, tangents matching (548 of 555 on a smooth monkey, the rest in the last digit), the same .mdl sections; the collision files have the same layout, sizes, mass centre, bounding spheres and edge structure. Known difference: the physics engine's own inertia approximation isn't public, so physics props store the true inertia of their shape. In the game: static props solid (multi-piece), physics props fall and settle, the nav goes around them, all draw like studiomdl's.
- **Custom Models (object role):** any mesh becomes a game model placed as a prop: Static, Dynamic (logic can move it) or Physics (falls, gets pushed; the game's own physics classes). Collision from each loose part's convex hull; linked copies share one model; materials (game textures or your own images) are made into model materials. Compiled with the game's studiomdl for now (our own model writer comes next). Tested in game: solid, nav goes around static ones, physics ones fall.

- **In-game test bench (for development, in `tests/ingame`):** drives the running game from Python (about 0.1 s per request), builds test maps without lighting in about 30 s, reads which logic wires fired, takes screenshots. `tests/ingame/test_examples.py` plays every logic example in the game and checks it: 38 of 42 automated (the rest need real play). It found the fixes below.
- **Debug Log** wire lines now say which graph they're in (`[Example 21: Two-Step Gate] button.pressed -> steps.in`), for maps with several graphs.
- **Fixes:**
  - Override questions the game asks while it creates the map's entities (Convert / Allow Weapon Spawn, Get Default Item) were answered before the map's logic existed, so most got the game's own answer (measured: every weapon spawn but one late one). The mode script now loads the logic early.
  - **Keep Across Maps** saved every map variable, entities included, instead of only the kept ones; on the next map the restored junk broke checks (a script error several times a second, and the Nuke never dropped). Only the kept variables, and only plain values, are saved now.
  - **Compare Values: Is Set** and the guard on entity functions only ask real entities whether they still exist.
  - Example 12 (Countdown to a Horde) started its horde when the map loaded: its When saw the countdown at 0 before it started. It now also waits for a 'counting' variable.

## 0.7.0 (test release, 2026-10-08)

- **Logic examples (Shift+A > Examples):** 20 ready-made graphs, from a hello message to an ambush that picks hidden nav areas ahead of the survivors. Each has notes on what it does and how, numbered frames, and any objects it needs (button, gate, room) at the 3D cursor.
- **Logic examples 2 (Shift+A > Examples 2):** 21 more (21-41), chosen so that between the two sets every node and every Override is used: map parts (sequences, lifts, fuses, power, teleporters, secret walls), zombies and the Director, scripting (vectors, lists, For Each) and all the Override questions.
- **Examples 3 (Shift+A > Examples 3):** special modes, starting with **Power-Up: Nuke** (Call of Duty style: a kill may drop a glowing pickup; walking over it blows up the common infected within an adjustable radius, nearest first, a few per tick; a 30 s cooldown before the next drop). Test map: `tests/fixtures/nuke_test.py` makes `demo/hl_test_nuke.blend`.
- **Branch: Toggle, Then Test** (flips the value, then fires If True / If False: a lever with one wire).
- **Logic editor menus:** one place per purpose (Events, Flow, Values, Scene, Actions, Director, HUD & Messages, Overrides, Script); the game's full function and event lists sit under Actions and Events.
- **Answer node:** works an Override's answer out from what the game asked (Blender doesn't allow wires from an Override back into itself). With If nodes, different answers for different cases.
- **Fixes:**
  - Override questions were answered outside the logic script's own scope: an Asked chain that reached a node like Spawn Zombie (or the debug log) failed with a script error (measured). And because the game keeps scripts' globals from map to map, a previous map's answers could answer the next map's questions. Now only the current map's logic answers, in its own scope.
  - **Can Pickup Object** answered yes by default, which would have let survivors carry every physics prop; the game's own answer is no (Valve's Holdout uses it to allow chosen props). For it and **Should Avoid Item** any yes now wins (before, their answers could never become yes).
  - **Spawn Zombie** (Where empty) could never give a second Tank: the game only places a Tank by itself while none is alive (measured, whatever the Tank limit). Hammerless now picks a spot like the Director's own (near the leading survivor, 600-1500 units away, out of sight) when the game won't. The Director's limits it lifts for the spawn are now changed on the table the Director actually reads.
  - The **Script** node's Result output didn't reach other nodes (anything wired from it read 0).
  - The game's **EntFire** function had no input types (its help lists names), so its delay went in empty and the game refused the call.
  - An event input took only one wire: wiring a second event into it (three buttons into one Counter) silently replaced the first. Event inputs now take any number, also in graphs saved before.
  - Overrides on the map's own weapon spawns (Convert / Allow Weapon Spawn) were only asked for spawns created after Hammerless's logic, so most were left as they were (measured). The logic is now created first.
  - Event wires that loop back (a Timer whose tick ends up stopping it) were dropped because Blender draws them red. They now work; a circle of value wires is still left out, with a warning.
  - **If** now runs inside script chains, so an event's details (the player, the item...) are still there after it.
  - A game function giving a value from nothing (no player yet, a removed entity) gives nothing instead of a script error.
  - The infected death event's `infected_id` is an entity, not a player.

## 0.6.0 (test release, 2026-10-07)

- **Light Compiler: Cycles (optional, Build & Play).** vrad still lays out the lighting, then Blender's Cycles bakes the lightmaps with the same lights and units vrad uses, on the GPU when there is one, and writes them into the map. Prop and character lighting and switchable lights stay vrad's.
  - **Matches vrad.** Samples are placed and filtered the way vrad's source does it. Measured on a real level against vrad: correlation 0.976, the same overall brightness within 1-3%, no fitting.
  - **Seam stitching.** Where two faces meet within 45 degrees, the lightmap texels next to the shared edge are adjusted (least squares) so both sides show the same light. Mean difference across edges: 1.6% for vrad, 0.02% stitched.
  - **Surfaces baked as one picture.** Connected faces in one plane are baked as a single continuous image at twice the lightmap resolution, so a floor vbsp cut into many faces can't disagree with itself.
  - **Faces nobody sees are skipped.** Using the nav mesh and the map's visibility data, faces no player position can see keep vrad's lighting and aren't baked; they still block and bounce light (22% of the lightmap on a real level).
  - **Settings:** Samples (default 1024; 256 is already within 0.3% of 4096), Stitch (on), Denoise (off: Blender's denoiser smears the packed bake, measured), and the log shows the time per step.
- **Lights fade with distance.** Point and spot lights were exported without falloff settings, so vrad lit everything they could see at full strength, however far away (the safe room light lit the whole map). They now get Hammer's default inverse-square falloff, and a Blender light's watts give the brightness Cycles would show. **Existing maps' lights look different (correct) after rebuilding.**
- **Logic nodes: all of L4D2's scripting.** New Add menus in the logic editor:
  - **Game Functions:** all 419 of the game's script functions (Player, Director, Nav Mesh, Entity, Find Entities, Entity Properties, Spawning, Sound, HUD...), with named inputs and descriptions, built from the game's own script help (`scripts/vscript_catalog.py` rebuilds the list).
  - **Game Events:** all 381 game events, their details as values (user ids become players).
  - **Script Blocks:** For Each, Set / Get Variable (map or per player, optionally kept across maps), Make Table, Get Field, Format Text, vectors, Compare Values, and Script / Script Value for your own code.
  - **Director, HUD & Overrides:** Director Setting (any of ~140 Director settings, changed while playing), Director Mood (the Director's intensity and warnings before a mob), HUD Text / Hide, and Override (the game asks the map before damage, chat, weapon spawns, zombie types...).
  - New wire types: text, vector, entity / player / nav area, and any value.
- **Co-op (Hammerless) game mode.** The game only uses overrides and a custom HUD in scripted mode, which plain co-op never turns on (tested). Maps that use those nodes are started in Hammerless's own co-op mode, installed as `addons/hammerless_mode.vpk`. The first time, restart the game once (it reads game modes when it starts).
- **Automatic sound (World > Sound):** Build ray traces the map from every floor survivors can reach and adds soundscapes. *Automatic Reverb* (the default) turns on the engine's own room reverb everywhere, which traces the space around you while playing (Valve's maps use it almost everywhere; a map without soundscapes didn't get it). *Reverb + City Ambience* also plays city ambience outdoors, and indoors a room tone with the outside coming in through the doorways and windows ("sound portals", placed where indoor spaces open to the outside, louder through wider openings), so the engine's 3D sound places it correctly as you move. *View > Sound* shows the result: outdoors, sheltered, indoors and the sound portals (**Trace Sound** works without building).

## 0.5.6 (test release, 2026-10-07)

- **View panel:** everything drawn over the viewport in one place, with three sections: *Nav Mesh* (the nav viewer with Build Navmesh, Analyze and Clear), *Baked Lighting* (Bake Lighting, the lighting view, Bake Settings) and *Visibility*. Rebuild Nav Next Time joins the other nav settings in Build & Play; Problems stays there too.
- **View > Visibility:** three views of the last build. *Portals*: the openings vis works through, showing how the map was split up, with tiny slivers (usually two brushes that almost line up) in red. *Rendering Load*: the map's faces coloured by how many faces the game draws from there, red where frame rate suffers first, and a button that puts the 3D cursor on the heaviest spot. *Vis Cost* (with Vis Compiler: Hammerless): portals coloured by the time vis spent on them, and the objects whose brushes those portals were split along, with their share of the vis time and a Select button.
- **Vis progress:** with Vis Compiler: Hammerless, Build shows how far vis has got and the time left ("Vis 42%, about 3 s left") in the Build & Play header and the status bar.

## 0.5.5 (test release, 2026-10-07)

- **Build settings at the top:** Nav Mesh, Nav Analysis, Zombies Climb Walls and Vis Compiler moved into the Build & Play panel, under the Build and Play buttons (Rebuild Next Time stays in Nav Mesh > Settings).

- **Faster visibility compile (optional):** *Vis Compiler: Hammerless (faster)* (in the Build & Play panel) runs Hammerless's own visibility compiler (`hlvvis.exe`) instead of L4D2's vvis, with the same results. Tested against vvis on 8 maps (a real level, the two demo maps, the level with water, with Auto Detail off and with Everything, rooms with an area portal, outdoor terrain), 3 runs each: every map file was byte-identical to vvis's, full and fast, except the heaviest map (Auto Detail off), where a borderline pair or two differed, as vvis's own runs differ from each other. About 3 times faster: 14.1 s to 5.2 s on the real level, 118 s to 38 s with Auto Detail off. If it ever fails, Valve's vvis runs instead; maps with fog-distance (radial) visibility, which Hammerless never writes itself, also use Valve's vvis for now. Valve's vvis stays the default. Build & Play on a real level, from scratch until playable in game: 45 s with Valve's vvis, 32 s with Hammerless vis; all Valve (nav made and analyzed by the game) 99 s.

## 0.5.4 (test release, 2026-10-06)

- **Detail per object or collection:** a brush's new **Detail** setting (*Selected Object* panel, under Role) makes it func_detail (*Detail*), keeps it a world brush (*World*), or follows the map's Auto Detail rule (*Auto*, as before). Collections have the same setting in their Properties tab. Use *Detail* on big furniture and piles of overlapping boxes, which the automatic rule (round or under 256 units) left as world brushes: in a test room, 36 overlapping boxes as detail cut the map's visibility pieces from 190 to 48. It works with Auto Seal off too, with a note listing those brushes (detail can't seal a map). Tool brushes and brushes touching an area portal stay world.

## 0.5.3 (test release, 2026-10-05)

- **Setup warning:** Build & Play now says up front when Left 4 Dead 2 wasn't found (it's found in any Steam library on its own, so this is rare), with the folder setting right there, or when the L4D2 Authoring Tools aren't installed, with where to get them. Before, you only found out when a build failed.
## 0.5.2 (test release, 2026-10-05)

- **Works in Blender 4.0 to 4.5 from one zip** (checked in 4.0.2: all tests, every panel and the viewport views; the zip installs, enables and draws its panels in 4.0, 4.2, 4.3, 4.4 and 4.5). Blender 4.0 / 4.1 predate extensions: install from *Preferences > Add-ons > Install*; 4.2+ from *Get Extensions > Install from Disk* as before. Two calls only Blender 4.1+ has (the Add panel's search hint, Start Fresh's confirm text) fall back on 4.0. Before, the zip installed in 4.0 but never showed in the add-ons list.
- Every compiled map counted as having baked lighting (the check read a lump's position instead of its size), so after an Analyze or a Quick build the Baked Lighting panel said "Baked" and offered to show lighting that wasn't there, and the game launch decided about fullbright from the same wrong answer.
- **Freeze recorder:** if Blender stops responding for 20 seconds, Hammerless writes where it was stuck to `hammerless_freeze.log` in the temp folder (see Troubleshooting), so freezes can be traced to the exact line.
- **Nav mesh memory cap:** the nav builder's step memory was capped by the biggest map built in the session, so after one huge or leaky build it could grow to about 1 GB and stall the next build after a scene switch. It now follows the current map, with a ceiling.
- **Native code hardening:** an out-of-memory in the nav builder or analysis now stops it with an error instead of crashing Blender; a damaged or half-written compiled map is reported instead of read past its end; positions that aren't valid numbers can't send a trace into an endless loop. The nav meshes and analysis it produces are unchanged (checked byte for byte, and 99.922% visibility match with the game as before).

## 0.5.1 (test release, 2026-10-05)

- **Terrain edges sank into a cliff, and the terrain's far edges were in the wrong place in game.** The terrain's patch grid started on a whole Hammer unit and used fixed-size cells, so its outer samples fell just off the terrain mesh and were sunk like holes: a sloped, unwalkable strip up to a cell (about 1.2 m) wide along the edges, which cut off safe room doors built against them, and compiled terrain that reached 1.6 m past the mesh. The grid now spans the terrain exactly (patches a little smaller than the Patch Size to fit): measured on a user map, the compiled terrain covers exactly the mesh's area, with heights within 1 mm of it.
- **Check (and Build) froze Blender on a high-poly object used as a brush** (a subdivided or rounded mesh with thousands of faces): checking it compared every face with every other. It's now fast at any size (a 32,000-face mesh: about 6 s), with the same verdicts on ordinary brushes. A new check catches what really breaks the map compiler: a brush side with more than 64 corners (for example a cylinder's cap with many segments) crashes it without a message; measured with L4D2's vbsp, which compiled brushes with 4,000 sides fine.

## 0.5.0 (test release, 2026-10-05)

- **Analyze Navmesh no longer bakes lighting.** When the walls changed it compiled the whole map, lighting included, which the analysis never uses; it now compiles only the walls and visibility (about 7 s less on a medium map), and doesn't put that unlit map into the game. The next Build bakes just the lighting. After a Bake Lighting, Analyze adds only the full visibility, and analyzes on it (before, it analyzed the bake's fast visibility, which can differ from the game's result). Analyze reuses a nav mesh Build Navmesh already made.
- When lights change and the full visibility is still to be added, the lighting is now baked after the visibility, so it uses the final one.
- The nav analysis starts as soon as the map's visibility is compiled, alongside the lighting step, instead of after it (it doesn't use the lighting). Same nav file; about 0.5 to 2 s faster per build (both steps share the CPU).
- **Clear buttons that really clear**: *Clear Navmesh* deletes the map's nav mesh, *Clear Analysis* removes its visibility data and hiding spots (keeping the areas), and *Clear Bake* deletes the baked lighting (with the compiled map that holds it), so the next build makes each again. Before, the nav buttons only cleared the viewport. *Settings > Folders & Game Data > Start Fresh* clears all of them at once.

## 0.4.0 (test release, 2026-10-05)

- **Reorganised sidebar.** Collapsible panels by category: Build & Play (with Problems), Add, Selected Object (only the sub-panels that apply: Material, Model, Terrain, Settings, Outputs), World (Sky & Sun, Fog, AI Director, Logic Graphs), Nav Mesh (Settings, Viewer), Baked Lighting (Bake Settings) and Settings (Compile, Game Window, Folders & Game Data, Scene, Debug). Collapsed panels show their status in the header. Labels line up, groups are spaced, and Settings > Compile summarises what the chosen Quality does.

## 0.3.0 (test release, 2026-10-05)

- **Bake Lighting** (Baked Lighting Viewer panel): bakes just the lighting, with the visibility step in its fast mode, and shows it: about 8 s instead of ~29 s on a medium map. Build and Build & Play reuse the bake and only add the full visibility (the lighting stays byte-identical).
- **Lighting is baked in HDR only**, which is what L4D2 uses (Valve's own maps have no LDR lighting): the lighting step takes half the time, with the same result in game. The Fast preset bakes HDR too (it baked the unused LDR copy).

## 0.2.0 (test release, 2026-10-05)

- **Baked Lighting Viewer**: shows the last build's lightmaps in the viewport (Lighting Only / Lit, exposure, X-ray), read straight from the compiled map. It tells you when there's nothing to show or the scene changed since the build, with a Build button right there.
- **Build** (was Compile Only) sits under Build & Play next to **Play** (was Launch Game), and now also makes and analyzes the nav mesh when needed. Export VMF moved to Advanced; Load Leak only appears when the last build leaked.
- The demo .blend no longer carries game textures and model previews (23 MB to 0.2 MB); *Refresh Previews* rebuilds them from your install.
- Preview meshes at the default scale got a stray `@52.49` in their names (a rounding check was too strict).
- README: not affiliated with Valve; credits for the Source SDK 2013 nav algorithms.
- Cleanup: unused functions and imports removed from the add-on; the zip holds only the add-on. Developer docs: a Contributing section, the demo maps and a guide to the in-game probes (`tests/ingame/README.md`).

## 0.1.0 (test release, 2026-10-03)

First version: Blender scenes to L4D2 maps with Build & Play, presets, game materials and models, logic nodes, Director / lighting / fog settings.

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
- **Pre-release audit, round 2 (entities and presets):**
  - Triggers made from the catalog or *Make Brush Entity* never fired (no "touched by" flag); old scenes are fixed at build time.
  - Hidden or excluded entities and presets were still exported.
  - *Turn Selected into* also converted point entities, reset settings and changed materials on shared meshes.
  - A second copy of a preset (Tank Ambush, Gate + Button, Zombie Climb) shared names with the first, so either copy set off both.
  - Rotated safe rooms marked nav outside the room; marks now follow the room's real shape.
  - Crescendo names with capitals or spaces never started; bad stage values broke the script; the map's Director settings weren't restored after a crescendo, and No Random Hordes / Tank limit 0 cancelled crescendo stages.
  - Object names with quotes broke the generated nav-marking and Bot Walkthrough scripts.
  - Model previews with long paths were duplicated on every refresh and ignored *Units per Meter*.
  - The search button next to an entity's Class now changes that entity instead of adding a new one.
  - New warnings: no End Safe Room (no wandering zombies), Next Map not installed, renamed Director.
- **Pre-release audit, round 3 (logic nodes):**
  - Crescendo *On Finished* fired after every stage (measured: once per stage); now once, after the last. Horde and Crescendo nodes no longer hear each other's "finished".
  - A Director Settings node applied at Map Start was overwritten by the map-wide settings a second later.
  - Math *Modulo* used a function the game doesn't have, which stopped every When check in the map.
  - Delays and event relays ignored triggers while a delay was running (e.g. several kills in one moment counted once).
  - Graphs leaked into every scene's map, could be lost on save, ignored reroutes and muted nodes, and kept deleted objects; *Graph from Outputs* dropped delays, "only once" and parameters.
  - Logic entities sat slightly off the origin (could leak maps built elsewhere); several naming clashes between nodes and graphs; When "only once" stopped *On False*; Special Killed counted Tanks.
- **Pre-release audit, round 4 (nav):**
  - **Visibility analysis in Blender was off in real use**: the nav generator's world was also treated as a sight blocker (glass, fences, ladders), so after building the nav only 96.2% of area pairs matched the game. Now 99.92% in real use too (measured).
  - Running the analysis twice in a session on a bigger mesh could crash Blender (native memory bug).
  - Detailed prop models made the analysis take minutes (a train tank: 466 s, now 0.07 s); a damaged model or odd prop keyvalues no longer abort the analysis.
  - The game's flow-error row in the Problems list was deleted as soon as it appeared; repeated Build Navmesh stacked warnings; rooftops with drops were reported as unreachable islands.
  - Analyze could write its nav for whichever scene was active when it finished; the nav view showed another file's or map's nav; Build/Analyze/Clear could run on top of each other (now one at a time, Esc stops them).
  - Very large maps hitting the nav builder's limit are reported; the nav builder's cache no longer grows without limit across edits.
- **Pre-release audit, round 5 (panels, operators, packaging):**
  - L4D2 is found in any Steam library; a folder set to the `left4dead2` subfolder works. The L4D2 folder can be set once in the add-on's Preferences for every file, and is stored as a full path.
  - Check for Problems reported "L4D2 wasn't found" for game materials; Launch Game ran on maps that weren't compiled or were still compiling.
  - Build & Play no longer launches (or writes the nav for) a different scene or map name if you switch during the compile.
  - Logic graphs refer to their scene by name, so appending a graph from another file doesn't bring that file's scene along. Undo works for New Logic Graph and Graph from Outputs.
  - Changing an entity's Class keeps its name and shared settings; Difficulty *Keep Current* can be chosen; the monitor choice survives unplugging another screen; min/max pairs entered the wrong way round are swapped.
  - A warning when the .blend isn't saved (builds go to a folder Blender deletes on quit); a Work Folder or game folder that can't be written gives a clear error instead of a traceback; timers are removed when the add-on is turned off; the add-on is marked Windows-only.
- **Pre-release audit, round 6 (launching the game):**
  - Build & Play with Steam closed (or still signing in) failed with "Steam is not running". Hammerless now starts Steam and waits until it's ready (up to two minutes), then starts the game.
  - Closing the game while it was making its nav mesh made Hammerless start the game again later. It now stops and says so.
  - Two commands sent to the game in quick succession could lose the first.
  - Messages from the game steps (nav step failed, Steam not ready) now pop up in Blender instead of only going to the log; the in-game path report is collected for the whole nav generation, not only the first 5 minutes.
- **Pre-release audit, round 7 (docs and first run):**
  - Two .blend files with the same Map Name (e.g. the default `my_map`) silently replaced each other's map in the game; the build now warns.
  - Getting Started: delete the default cube first (a safe room placed at the origin ends up inside it), and give the map its own name.
  - The docs now match the add-on's labels: the Nav Mesh Viewer sub-panel (was "Nav Mesh (from the game)"), the problem list's Check button, the Material box in Selected Object, Custom Director Settings, Horde Every Min/Max, Turn Selected into Button, Shift+A > L4D2 > Start Safe Room. Undocumented settings (Export VMF, Auto Detail, Fog and Director header checkboxes, Launch Options, collection roles, Select All Parts…) are described.
  - Rebuild Nav Mesh's tooltip said to tick it after every geometry change; it's rebuilt automatically.
- **Rooms behind a gate got no wandering zombies**: Mover gates no longer block the nav by default (L4D2 blocks from map load, before the Director places zombies).
- A lit map loaded into a running game after an unlit one looked unlit (`mat_fullbright` stayed on).
- Build Navmesh and Analyze Navmesh now export the map exactly as Build does.

