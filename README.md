# Hammerless

Build Left 4 Dead 2 maps in Blender: tag your objects, press **Build & Play**, and the game opens on your level.

Status: **v0.1, early.** Tested end-to-end in L4D2: compile, spawns, nav, safe room transition, hordes (see *In-game verification*).

## Install

1. Blender 4.2 or newer: *Edit > Preferences > Get Extensions > ⌄ (top right) > Install from Disk…* and pick `dist/hammerless-0.1.0.zip`.
2. Install the **Left 4 Dead 2 Authoring Tools** from Steam (*Library > Tools*). This provides the map compilers.
3. In the 3D view press **N**, then open the **Hammerless** tab.

## Using it

**Adding things:** open the sidebar (N) > *Hammerless* > **Add**. Pick a category tile (Events, Safe Rooms, Survivors, Infected, Weapons, Items, Props, Volumes, Logic, Lights) or type in the search box to look through everything. Click an item to read what it does, star it to keep it under *Favorites*, then press **Add at Cursor**. For volumes (triggers, blockers, detail brushes) with meshes selected, the button becomes **Turn Selected into…**. The *Shift+A > L4D2* menu still works too.

| You want | Do this |
|---|---|
| Walls, floors, buildings | Any **convex** mesh (boxes, wedges, cylinders). One object can hold several separate convex pieces. Meshes are brushes by default. |
| Something that isn't convex | Split it into convex pieces, or tick **Use Convex Hull** on it |
| Terrain | Set the object's role (or its collection's) to **Terrain**. Shape it however you like: sculpt, modifiers, displacement. It's sampled from above like a heightmap (no overhangs). Paint vertex colour red to blend between a blend material's two textures |
| Spawns, items, weapons, Witch/Tank | **Shift+A > L4D2 >** pick a category, or *Search Entities…* |
| Safe rooms | **Shift+A > L4D2 > Start Safe Room / End Safe Room**: room, door, spawns, items, landmark and change-level volume, all wired up |
| func_detail, triggers, player blockers | Select meshes, then use the 🔍 next to *Class* in the panel (or *Make Brush Entity*) |
| Game textures | 🔍 next to **Game Material** searches all 8,000+ L4D2 materials. The real texture shows in Blender's Material Preview view, world-aligned at Hammer scale (*Refresh Previews* updates older materials and models) |
| Your own textures | A material with an Image Texture in Base Color is converted automatically (to `left4dead2/materials/hammerless/<map>/`) |
| Props & doors | **Shift+A > L4D2 > Props**: Static/Dynamic/Physics Prop and Door. 🔍 next to *Model* searches all 5,500 game models. Props, items, weapons, survivor spawns and Witch/Tank spawns show as their **real textured 3D models** in Blender (*Advanced > 3D Model Previews*) |
| Friction & footsteps | Material panel > **Surface** (104 surfaces from the game, e.g. `ice` = slippery). Works on game materials too |
| Crescendo (hordes in waves) | **Shift+A > L4D2 > Crescendo Button**, then edit the stages on its *Crescendo Definition* (e.g. `PANIC 1, DELAY 10, PANIC 2`) |
| Hordes, Tank ambushes, buttons | **Shift+A > L4D2 > Horde Trigger / Horde Button / Tank Ambush**. Wire your own with the **Outputs** list. See [docs/HORDE_EVENTS.md](docs/HORDE_EVENTS.md) |
| Zombies in open areas | **Shift+A > L4D2 > Zombie Spawn Area**: commons only spawn where survivors can't see, and this marks an area as "hidden" |
| Lights | Blender Sun/Point/Spot lights export. If there's no sun, one is added automatically. Safe room presets include a ceiling light |

**Scale:** 1 Blender meter = 52.49 Hammer units by default, so real-size buildings match Valve's proportions (a survivor is ~1.37 m tall at this scale). Change it under *Settings*.

**Leaks can't happen by default:** *Auto Seal* wraps the map in a skybox box. If you turn it off and the map leaks, click **Load Leak** to see a red line leading to the hole.

**Nav mesh:** bots and zombies need one. Tick *Nav* (next to Quality) for the first launch after geometry changes.

## Settings (N panel > Hammerless > L4D2 Map)

| Section | What's in it |
|---|---|
| **Compile** | Quality preset (Quick / Fast / Normal / Final) or **Custom**: visibility, lighting quality, LDR/HDR, per-vertex prop lighting, extra compiler options |
| **Lighting & Sky** | Sky picker (every L4D2 sky), lightmap scale (shadow sharpness), sun colour/brightness/height/direction, sky light colour/brightness |
| **Fog** | Colour, start/end distance, density |
| **AI Director** | Max common infected, horde size and frequency, no random hordes, no wanderers, max specials, special respawn time, max Tanks/Witches |
| **Game Window** | Which monitor, size, borderless, extra launch options |
| **Debug** | *Debug Log*: Director events and stats to the console. *Bot Walkthrough Test*: bots play the map (see below) |
| **Advanced** | Scale, default material, auto seal, material checks, paths, reload game data, refresh texture previews |

**Bot Walkthrough Test:** with this on, the survivor bots play the map by themselves: they walk through every horde trigger, press every button (the test stands in for the Use key), and finish in the end safe room. Survivors can't die while it runs. Results are written to `left4dead2/console.log` as `HAMMERLESS_AUTOTEST` lines, including warnings if a bot gets stuck. **Turn it off to play the map yourself.**

## Tests

```bash
python -m unittest discover tests/unit
```

```bash
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --factory-startup --python tests/blender/run_tests.py
```

Demo level (writes `demo/demo_level.blend` and exports it):

```bash
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --factory-startup --python tests/fixtures/demo_level.py
```

Real compile of a .blend (needs the Authoring Tools; prints the full compiler log):

```bash
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background demo/demo_level.blend --python tests/compile/build_blend.py -- FAST
```

Build the installable zip:

```bash
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --command extension build --source-dir hammerless --output-dir dist
```

## In-game verification

Verified 2026-09-23 with the two demo maps (Blender 4.5, L4D2 build 10097). Probe scripts are in `tests/ingame/`.

- [x] Compile pipeline: vbsp/vvis/vrad run clean with no leak, the map is copied to `maps/`, and Build & Play works from the Blender UI (reuses a running game)
- [x] Displacement orientation: in-game ground heights match Blender to within about 1 unit
- [x] Terrain shading: seams between patches appear only with the *Fast* compile; *Normal* (now the default) is smooth
- [x] Survivors spawn inside the start safe room
- [x] Nav: generated automatically after the map loads, then safe rooms are marked (start = PLAYER_START + CHECKPOINT, end = CHECKPOINT), saved, and the map reloads
- [x] Level transition: closing the end safe room door with all survivors inside loads the next map; survivors arrive in its start safe room
- [x] Custom textures (uncompressed VTF 7.2) render correctly and are no longer mirrored
- [x] Game materials, terrain blend material, safe room ceiling light
- [x] Safe room doors have "Use Closes" (spawnflags 8192), so they can be closed again with Use
- [x] Common infected spawn (with cover or a Zombie Spawn Area); Horde Trigger and Horde Button each bring a 33-zombie horde
- [x] Entity outputs (I/O) are written to the map and fire in-game
- [x] No-cheat run through the addon (Bot Walkthrough Test): horde trigger, horde button, **crescendo in 3 waves**, map-wide Director settings loaded
- [x] Fresh game launch: window opens on the chosen monitor; nav generate → mark → save → clean reload with cheats off

Known issues:
- [ ] On transition, weapon spawns from *outside* the end safe room are also carried over and recreated outside the next map (harmless but untidy). Needs investigation
- [ ] Light brightness conversion from Blender lights not yet tuned
- [ ] Closing doors with the Use key: flag verified in the map, needs a hands-on check
- [ ] Tank Ambush preset: wired and unit-tested, not yet triggered in-game

## Layout

```
hammerless/          the extension
  core/              pure Python (no bpy): geometry, VMF writer, terrain, entities, textures, compile
  blender/           Blender UI, operators, scene extraction
tests/unit/          core tests (plain Python)
tests/blender/       headless Blender tests
tests/fixtures/      scripts that build test scenes
docs/                design doc + Hammer feature audit
```
