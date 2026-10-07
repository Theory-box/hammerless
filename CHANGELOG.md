# Changelog

## Unreleased

- **Build settings at the top:** Nav Mesh, Nav Analysis, Zombies Climb Walls and Vis Compiler moved into the Build & Play panel, under the Build and Play buttons (Rebuild Next Time stays in Nav Mesh > Settings).

- **Faster visibility compile (optional):** *Vis Compiler: Hammerless (faster)* (in the Build & Play panel) runs Hammerless's own visibility compiler (`hlvvis.exe`) instead of L4D2's vvis, with the same results. Tested against vvis on 8 maps (a real level, the two demo maps, the level with water, with Auto Detail off and with Everything, rooms with an area portal, outdoor terrain), 3 runs each: every map file was byte-identical to vvis's, full and fast, except the heaviest map (Auto Detail off), where a borderline pair or two differed, as vvis's own runs differ from each other. About 3 times faster: 14.1 s to 5.2 s on the real level, 118 s to 38 s with Auto Detail off. If it ever fails, Valve's vvis runs instead; maps with fog-distance (radial) visibility, which Hammerless never writes itself, also use Valve's vvis for now. Valve's vvis stays the default.

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

