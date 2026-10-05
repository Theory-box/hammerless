# Zombies & Horde Events: How They Work

## 1. How the AI Director spawns infected

Left 4 Dead 2 has no "place 20 zombies here" button. The **AI Director** decides what spawns, when, and where, based on the survivors' progress and stress. It spawns:

| What | When |
|---|---|
| **Wanderers** | A scattering of common infected placed around the map at start and as you move |
| **Mobs** | Groups of commons that rush the survivors every so often |
| **Panic events / hordes** | A big rush you start on purpose (alarm, button, trigger) |
| **Specials** (Hunter, Smoker, Boomer, Charger, Jockey, Spitter) | Every so often, near the survivors |
| **Tank / Witch** | Now and then per map, or where you force them |

### Rule 1: commons only spawn where survivors CAN'T see

This is the rule that matters most for your level design. If you get **"Couldn't find a common Spawn position"** in the console, every nearby spot is visible. Valve's maps are full of corners, alleys, buildings, fences, hills and bushes, partly so the Director has hidden spots.

Two ways to give it room:
- **Design with cover:** walls, buildings you can walk behind, hills, alleys. This is the best option.
- **Zombie Spawn Area** preset (*Shift+A > L4D2 > Zombie Spawn Area*). This marks the nav mesh inside the box as `OBSCURED`, meaning "treat this as hidden". Use it for open fields, tall grass, fog.

To see which areas qualify, and why one doesn't (too close, in view, a no-spawn mark, a blocking gate), use the nav views described in [Nav Mesh & Zombie Spawns](NAV_AND_ZOMBIES.md#why-does-an-area-get-no-zombies).

### Rule 2: the Director needs the nav mesh and the flow

The nav mesh is the map of walkable areas. The **flow** is the path from the start safe room to the end safe room; the Director measures survivors' progress along it and spawns commons ahead of or behind them.

What spawns with and without flow (measured in-game, Easy):

| Map | Wanderers (commons already standing around) | Mobs (timed rushes) | Specials |
|---|---|---|---|
| Start + End Safe Room | ✅ about 30 within 20 s, even before leaving the room | ✅ | ✅ |
| Only a survivor spawn | ❌ none | ✅ one rush every *Horde Every Min / Max (s)* (default 90–180 s) | ✅ |

So on a map without safe rooms it looks like "only specials spawn" until the first rush arrives a minute or more in. For a proper level, add a **Start Safe Room** and an **End Safe Room** preset.

**The End Safe Room's Next Map must be a different, real map** (for example `c1m2_streets`). If it's empty or names the map itself, the game silently skips the flow ("an info_changelevel points to the current map" in the console), and there are no wanderers, Tanks or Witches. Hammerless fills in `c1m2_streets` and warns when it's missing.

Hammerless handles the nav side: Build & Play generates the nav mesh (automatically whenever the map has none or its walls or safe rooms changed), marks the safe rooms and Zombie Spawn Areas, saves, and reloads.

Community sources: [Steam mapping help: nav flow](https://steamcommunity.com/app/550/discussions/3/598517032946829432/), [World of Level Design: nav meshes and spawning infected](https://www.worldofleveldesign.com/categories/left4dead_mapping/l4d-gameplay-navigation-meshes-spawn-infected.php).

### Presets are one object
Every *Shift+A > L4D2* preset adds a parent **Empty** named after it (for example *End Safe Room*), with all its pieces parented to it. Select the Empty to move the whole thing, and to see its settings in the Hammerless panel: Next Map and Landmark for safe rooms, Stages for crescendos, what a Tank Ambush spawns. Click any piece and the panel shows **Part of: …** with a button to jump back. In files made before this, click any preset piece and press **Group into Preset**.

## 2. Inputs & outputs: how events are wired

Hammer's event system is **"when X happens, tell Y to do Z"**:

```
 [trigger_once]  --OnTrigger-->  [director]   : ForcePanicEvent
   (entity X)      (output)      (target Y)      (input Z)
```

In Blender: select an entity, and the **Outputs** list is in *Selected Object > Outputs*. Each output has:

| Field | Meaning | Example |
|---|---|---|
| Output | The event on *this* entity | `OnTrigger`, `OnPressed`, `OnMapSpawn` |
| Target | The **name** (targetname) of who to tell | `director`, `tank_ambush_spawner` |
| Input | What they should do | `ForcePanicEvent`, `SpawnZombie`, `Open` |
| Parameter | Extra value for the input | `tank` (for SpawnZombie) |
| Delay | Seconds to wait | `2.5` |
| Only Once | Fire just the first time | ✔ |

The Director is always named **`director`** (Hammerless adds it automatically). If you target a name that doesn't exist, **Check the Map** (*Build & Play > Problems*) warns you.

## 3. Recipes (all verified in-game unless marked)

### A horde when survivors reach a spot
*Shift+A > L4D2 > Horde Trigger.* This adds an invisible box. The first survivor to walk in fires `OnTrigger > director > ForcePanicEvent`. Scale the box to cover the whole path so nobody can walk around it.
✅ Tested: 33 commons arrived within 8 seconds.

### A horde when someone presses a button
*Shift+A > L4D2 > Horde Button.* This adds a hazard-striped button that fires `OnPressed > director > ForcePanicEvent`. Put it on a wall. It works like the campaigns' lift buttons and radios.
✅ Tested: 33 commons.

To make your own button: select a small convex mesh, then in the *Add* panel pick *Volumes > Button* and press **Turn Selected into Button**; then add an output.

### A Tank ambush
*Shift+A > L4D2 > Tank Ambush.* This adds a **Zombie Spawner** (where the Tank appears) and a trigger about 10 m away. Walking into the trigger fires `OnTrigger > tank_ambush_spawner > SpawnZombie (parameter: tank)`. Move the two pieces wherever you like.
⚠ Builds and wires correctly (tested in code), not yet tested in-game.

### Spawn anything, anywhere, even in plain sight
A **Zombie Spawner** (*Shift+A > L4D2 > Infected > Zombie Spawner*) spawns on command, ignoring the visibility rule. Send it `SpawnZombie` with a parameter of `common`, `tank`, `witch`, `hunter`, `boomer`, `smoker`, `charger`, `jockey` or `spitter`. Add several outputs to one trigger for a scripted ambush, for example three `SpawnZombie common` plus one `SpawnZombie hunter` with a 2-second delay.

### A crescendo: hordes in waves
*Shift+A > L4D2 > Crescendo Button.* This adds a button plus a **Crescendo Definition** (a small cube), both under one **Crescendo Button** parent. Select the parent and edit **Stages** in the Hammerless panel, for example:

```
PANIC 1, DELAY 10, PANIC 1, DELAY 10, PANIC 2
```

`PANIC n` means n hordes, `DELAY s` means wait s seconds, and `TANK n` means n Tanks. The button's output is `OnPressed > director > ScriptedPanicEvent`, with the crescendo's **name** as the parameter. You can start a crescendo from anything, such as a trigger, a relay or a map start.
✅ Tested: three waves arrived with the configured pauses.

### A Witch that is always there
Add a **Zombie Spawner** named `witch_spot` where she should sit, plus a **Map Start** entity (*Logic > Map Start*) with the output `OnMapSpawn > witch_spot > SpawnZombie (witch)`.

### One event, many reactions
A **Relay** (*Logic > Relay*) passes a signal on. Point several things at it, and give it several outputs. For example: the button triggers the relay, and the relay starts the horde, opens a door and spawns a Tank 20 s later.

## 4. Coming later (so you know the words)

| Term | What it is |
|---|---|
| **Gauntlet** | Endless horde while survivors run a stretch |
| **Finale** | `trigger_finale` plus a rescue vehicle; waves of hordes and Tanks |

Already available: **crescendos** (recipe above) and **map-wide Director settings** (*World > AI Director*, after ticking the box in its header: common limit, horde size and frequency, specials, Tanks, Witches). A crescendo keeps the map's Director limits while it runs.

## 5. Testing tips

In-game console (enable it in Options > Keyboard/Mouse > Allow Developer Console, then press `~`):

```
sv_cheats 1                 // needed for the commands below
director_force_panic_event  // start a horde now
z_spawn_old tank auto       // spawn a Tank somewhere hidden
z_spawn_old witch           // spawn a Witch where you're aiming
nb_delete_all infected      // clear all infected
god 1                       // survivors can't die
```

**Build & Play always turns cheats off first**, which resets every cheat setting to default. That's on purpose: settings like `z_common_limit 0` or `nb_stop 1` persist until the game restarts and silently stop zombies from spawning. That happened during testing.
