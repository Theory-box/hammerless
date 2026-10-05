# Things to check by hand

Things I (Claude) can't verify myself because they need a person at the keyboard, or a judgement call about how something looks or feels. Tick them off whenever you get to them. None of them blocks further work.

To playtest: open `demo/demo_level.blend`, turn **off** *Debug > Bot Walkthrough Test*, set *Game Window > Difficulty* to Easy if you like, and press **Build & Play**.

## Current playtest: the demo map, start to finish
(Items 1, 2, 4 and 8 confirmed by the debug log of your run on 2026-09-23. Door Use-closing also works.)
1. [x] **Start door:** open it with Use, close it, open it again
2. [x] **Leaving the safe room:** can you walk out onto the field easily (your terrain fix)?
3. [ ] **Ice slab** (the square slab near the start, left of the path): slippery?
4. [x] **Horde Trigger:** about 15 m out, a horde should come when you cross it
5. [ ] **Horde Button** (striped, on the shed's west wall): press Use, and another horde should come
6. [ ] **Crescendo Button** (on the truck's west side): hordes in waves (horde, 10 s pause, horde, pause, bigger horde)
7. [ ] **Zombie amounts:** the demo caps common infected at 20. Does it feel right?
8. [x] **End safe room:** walk in with the bots, close the door with Use, and `hl_demo2` should load with everyone in its start room
9. [ ] Anything that looks wrong, feels wrong, or gets you stuck (write down roughly where)

## Newer features to try in Blender (no rush)
- [ ] **Texture previews:** switch the viewport to *Material Preview*. Game materials show their real textures
- [ ] **3D model previews:** press *Refresh Previews* (Advanced, or any material's box) once in your demo: survivors, weapons, items, doors, Witch and Tank become real models. New entities get them automatically
- [ ] **Material browser:** 🔍 next to *Game Material* searches all 8,000+ L4D2 materials
- [ ] **Props:** *Shift+A > L4D2 > Props*, then 🔍 next to *Model* to pick any of 5,500 models (the box resizes to the model)
- [ ] **Ladder preset:** *Shift+A > L4D2 > Ladder*, put it against a wall or roof edge, climb it in-game (climb from the side the arrow points to). Not tested in-game yet
- [ ] **Tank Ambush preset:** walking into its trigger should spawn a Tank at the spawner. Not tested in-game yet
- [ ] **Settings panels** (N panel > Hammerless > L4D2 Map): Compile, Lighting & Sky, Fog, AI Director, Game Window, Debug, Advanced. Clear enough? Anything missing?

## Looks
- [ ] **Sun / sky light settings:** try a few values in *Lighting & Sky*
- [ ] **Fog:** enable it and try start/end distances
- [ ] **Blender lights:** point/spot lamp brightness in-game vs. what you expected (the conversion isn't tuned yet)
- [ ] **Your own texture:** a material with your own image shows correctly (not mirrored, sensible scale)

## Things only you can decide
- [ ] Is 52.49 units per meter the right default scale for how you model?
- [ ] Priority for next big features: 3D model previews in Blender, finales, gauntlets, more props/entities, brush tools?

## Done by playtest / automated test
- [x] Horde trigger, horde button and crescendo waves (Bot Walkthrough Test, no cheats)
- [x] Custom Director settings load on map start
- [x] Game opens on the chosen monitor; nav mesh is generated and safe rooms marked automatically
