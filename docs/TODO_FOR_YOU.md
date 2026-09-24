# Things to check by hand

Things I (Claude) can't verify myself because they need a person at the keyboard, or a judgement call about how something looks or feels. Tick them off whenever you get to them. None of them blocks further work.

To test: open `demo/demo_level.blend`, turn **off** *Debug > Bot Walkthrough Test* (otherwise the bots play the level), then press **Build & Play**.

## Gameplay feel
- [ ] **Safe room doors with the Use key:** open, then close again, then open again (both start and end doors)
- [ ] **Horde Button by hand:** the striped button on the shed's west wall starts a horde when you press Use
- [ ] **Crescendo Button by hand:** the button on the truck's west side starts hordes in waves (horde, 10 s pause, horde, pause, bigger horde)
- [ ] **Ice slab:** the square slab near the start (left of the path) should be slippery (surface `ice`, friction 0.1)
- [ ] **Tank Ambush preset:** add one in a test map; walking into its trigger should spawn a Tank at the spawner
- [ ] **Zombie density** with custom Director settings (demo: max 20 commons) feels right

## Looks
- [ ] **Sun / sky light settings:** try a few values in *Lighting & Sky* and say if the in-game result matches what you'd expect
- [ ] **Fog:** enable it in *Fog*, try start/end distances
- [ ] **Blender lights:** point/spot lamp brightness in-game vs. what you expected (the conversion isn't tuned yet)
- [ ] **Your own texture:** a material with your own image in Base Color shows correctly (text not mirrored, sensible scale)

## Blender side
- [ ] **Panel layout:** is the Hammerless tab (N panel) clear? Anything missing or confusing?
- [ ] **Game Window settings:** *Monitor*, size and *Borderless* behave as expected when the game starts
- [ ] **Your own level:** build something from scratch with the addon and note what felt awkward

## Things only you can decide
- [ ] Is 52.49 units per meter the right default scale for how you model?
- [ ] Priority for next big features: game model previews in Blender, texture previews, ladders, finales, props?
