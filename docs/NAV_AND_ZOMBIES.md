# Nav Mesh & Zombie Spawns

Bots, zombies and the AI Director all run on the **nav mesh**, the game's map of walkable areas. Hammerless builds it for you and lets you see it in Blender. For how the Director decides *what* to spawn (wanderers, mobs, hordes, specials) see [Hordes & Events](HORDE_EVENTS.md).

## Settings (L4D2 Map panel)

| Setting | Options |
|---|---|
| **Nav Mesh** | **Made in Blender** (default): a port of the game's nav generator builds it while the map compiles. **Made by the game**: the game generates it after loading (two extra reloads). |
| **Nav Analysis** | **In Blender** (default): visibility and hiding spots are computed in Blender, so the game loads the map once. **By the game**: the game analyzes it and reloads once. |
| **Zombies Climb Walls** | Every wall up to about 160 units becomes climbable for commons, with a way back down. |
| **Rebuild Nav Mesh** | Forces a fresh nav mesh on the next build. Normally not needed: Hammerless rebuilds it whenever the map changes. |

Both "in Blender" options are measured against the game's own output: the nav analysis matches the game's visibility on 99.92% of area pairs, and its hiding spots exactly.

## The Nav Mesh panel

| Button | What it does |
|---|---|
| **Build Navmesh** | Builds the nav mesh from the scene (no compile) and shows it. Becomes **Clear Navmesh** once shown. |
| **Show the Game's Nav Mesh** | Shows the nav the game is actually using (`maps/<map>.nav`). |
| **Analyze Navmesh** | Builds the nav and runs the game's analysis (visibility, hiding spots). If walls changed since the last compile, it **compiles the map first** (the analysis looks through the compiled map). Becomes **Clear Analysis** once done. |

Toggles: **Drops and Jumps** (one-way drop-down and jump-up arrows), **Hiding Spots** (blue = in cover, orange = exposed), **X-Ray** (see the nav through walls).

## The colour views

Pick one under **Colour**. The start safe room is blue and the end safe room purple in every view except the visibility view.

| View | Colours |
|---|---|
| **Can survivors reach it?** | Green: reachable from the start room. Red: not reachable. A marker shows where the path to the end breaks. |
| **Distance from the start** | Blue near the start, red far along the path. |
| **Zombie spawn marks** | Red: no zombies (EMPTY + NO_MOBS). Pink: no wanderers (EMPTY). Violet: no hordes (NO_MOBS). Orange: Zombie Spawn Area (OBSCURED). Light blue: normal. |
| **Where zombies can spawn** | For survivors **leaving the start room** (or standing at the **3D cursor**, see *Survivors At*): green can spawn wanderers, red no-spawn mark, orange too close (under 550 units of *walking* distance), yellow a survivor can see it (needs an analyzed nav). |
| **What can be seen from the 3D cursor** | Put the cursor on an area (**Shift + right-click**). White: that area. Green: completely visible. Yellow: partly visible. Grey: not visible. Needs an analyzed nav. |

**Marks only count where the area's centre is inside the box.** A no-spawn box that looks like it covers a roof can miss an area whose centre sticks out past its edge. The *Zombie spawn marks* view shows exactly what each area got.

## Why does an area get no zombies?

The Director places **wandering** commons when the map loads (a Valve map has about 30 before anyone leaves the safe room) and more ahead of the survivors as they move. An area only gets them if **all** of these hold:

1. **Survivors can reach it** along nav they can walk. A closed gate with nav blocking turned on cuts off everything behind it (see below).
2. **No survivor can see it.** This is the visibility the nav analysis computes. Use cover: walls, corners, buildings, hills.
3. **It's not too close:** at least about 550 units of walking distance from every survivor (`z_spawn_safety_range`). Walking distance, not straight-line: a roof right above you can still get zombies.
4. **It has no EMPTY mark** (from a no-spawn box).
5. **The flow works:** the path from the start room to the end room exists (*Can survivors reach it?* view, and see [Troubleshooting](TROUBLESHOOTING.md#no-zombies-at-all-or-flow-broken)).

**Zombie Spawn Area** (*Add > Events*) marks areas OBSCURED: the Director treats them as hidden even when survivors can see them, which helps with open fields. It doesn't get around the distance rule (3).

### Gates and nav blocking

A *Mover* (gate, lift, sliding door) has **Block Nav While Closed**, **off by default**. In L4D2 a nav blocker blocks from the moment the map loads, before the Director places its zombies, so **everything behind a closed blocking gate gets no wandering zombies**. A vote restart doesn't block again, which makes the bug look random. Leave it off unless you specifically need bots and zombies to path around a closed gate.

### Survivor spawns

Keep the four survivor spawns **inside the Start Safe Room box**. A spawn outside it starts the round with the game thinking a survivor has already left the safe room.
