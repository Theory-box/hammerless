# Logic: node graphs to VScript, the game's script interface as measured

How Hammerless turns logic graphs into entity outputs and Squirrel, where the game's script API, Director options, hooks and entity properties (NetProps) came from and how they were checked, the test bench that drives the game, the examples and what each measured. User-facing: `docs/LOGIC_NODES.md`, `docs/systems/LOGIC_AND_SCRIPTING.md`, `docs/HORDE_EVENTS.md`, `tests/ingame/README.md`. Director rules the nodes rely on are in [../director/](../director/README.md).

Code: `hammerless/core/logic.py` (compiler), `hammerless/blender/logic.py` (node editor), `hammerless/blender/logicgroups.py` (node groups), `hammerless/blender/logic_examples.py`, `_2.py`, `_3.py` (examples), `hammerless/core/director_options.py` (Director settings and hooks), `hammerless/core/netprops.py` + `hammerless/core/data/netprops.json` (entity properties), `hammerless/core/data/vscript_api.json` (functions and events), `hammerless/core/data/cheat_commands.txt` + `cheats.py` (cheat-only names), `hammerless/core/gamefiles.py` (Director script, crescendos, mode files, ready script). Catalogue builders: `scripts/vscript_catalog.py`, `scripts/netprop_catalog.py`, `scripts/cheat_commands.py`. Tests: `tests/ingame/bench.py`, `tests/ingame/test_examples.py`, `tests/blender` (`test_logic_examples_build`), `tests/unit/test_core.py`.

Timeline (2026): node editor and first nodes 09-2x (b523ad9); every game function and event as a node, Director settings, HUD, overrides and the mode add-on 10-07 (ea56d77, b1916ba); examples 1-20 and 21-41 tested in game 10-07/08; test bench 10-08 (e0a1496); node groups and "everything from plain nodes" 10-09/10 (c05bbe6, 6ce9d09); animated-model examples 10-11/12; entity properties and examples 55-61 10-13 (b69fe96, 21d7c4e); precision fixes 10-13 (19dc491, 46f75ac).

## The compiler (graph -> entities + one script)

Status: `done`. For each graph `logic.py`:

1. turns entity nodes into entities (Volume -> trigger, Button -> `func_button` from a mesh, Move -> `func_movelinear`, Show/Hide and Collision -> toggleable brushes, Timers/Delays/relays -> the game's logic entities);
2. turns each event wire into one output row on the entity that fires it;
3. writes one script per map for the rest (game events, For Each, variables, maths, Director settings, HUD, overrides, game functions): `scripts/vscripts/hammerless/logic_<map>.nut`, run by a `logic_script` named `hl_logic`. Script nodes chain inside the script; entity and script nodes mix both ways.

Facts the compiler is built on, all measured in game:

| Fact | Consequence | Date |
|---|---|---|
| Entities run their scripts as they are created, in BSP entity order; `ConvertWeaponSpawn` is asked per weapon spawn as it is created | `hl_logic` is inserted **first** in the entity list (early spawns got the default answer otherwise) | 2026-10-07 |
| The VScript root table persists across map loads: hooks from the previous map answered before the new logic ran | mode trampolines require `::HL_Map == map` and call hooks with `.call(::HL_Scope)` | 2026-10-08 |
| Game events must be registered on the script's own scope | done so; user ids come through as players, entity ids as entities | 2026-10-07 |
| The map-wide Director settings overwrote a Director Settings node applied at map start | Map Start waits 1.1 s when map-wide settings are on | audit round 3 |
| Math Modulo used a function the game doesn't have, stopping every When check | fixed | audit round 3 |
| Nested native calls (`closure.call()`) overflow quickly ("Native stack overflow") | `bindenv` instead | 2026-09 |
| Squirrel 3 here: JSON-style tables work; `foreach (...) if (...) { break; }` without braces fails to parse; `base` is reserved; local functions can't recurse | code generation avoids them | 2026-10-13 |
| A Gate node's outputs go through an entity and run a frame later, **without the event's data** | event details must be read before a Gate | 2026-10-13 |
| `weapon_fire` fires just before the round is taken from the clip | Bottomless Clip reads the clip in the event and puts the round back | 2026-10-13 |
| Damage dealt inside an `AllowTakeDamage` answer asks the question again (nested) | the inner call restores the outer call's details and answer | 2544048 |
| Blender marks links closing a node-level cycle `is_valid = False`; Override -> value -> its own answer input is one | the Answer node; `_without_value_loops` drops only circles through pure value nodes; script loops stop after 32 calls | 2026-10-07 |
| Event input sockets need `link_limit` raised (the default 1 silently replaced wires) | raised; a `load_post` handler fixes old files | 2026-10-08 |
| Objects outside the map collection aren't built (Slow Motion Headshots did nothing on imported maps) | examples' objects go into the map collection; a warning names a logic node whose object isn't built | 23d163d |
| Cheat-only names: the game's own `cvarlist` marks them "cheat" | `cheat_commands.txt`; Build gives one "Needs cheats" warning naming the nodes | b68da13 |

**Precision** (2026-10-13): Blender's `FloatProperty` and L4D2's Squirrel floats are both single precision, so whole numbers above 2^24 lose digits (a colour 16711680 came out as 1.67117e+07). Socket numbers are now written with every digit (19dc491) and whole-number properties get an exact Whole Number input compiled as an integer literal (46f75ac). Colours with alpha >= 128 are negative in the game's signed 32-bit form.

Variables: Map, Per Player (or per entity wired into Player: how a Swelling Pod keeps a charge per pod), This Group Copy; per-entity keys are kept apart from players' keys; Keep Across Maps saves only marked plain values (the test run found it saving junk once). Node groups (`logicgroups.py`): Ctrl+G, Tab, Ctrl+Alt+G, exposed inputs; every Group node is flattened at build under `<group node>/<node>` names, so the game never sees a group; re-adding an example reuses its groups unless edited; Remove Unused Groups.

Design rule (the user's, 2026-10-08 and 10-11): nodes stay generic building blocks; behaviour is wired from existing nodes, never baked into a node or hidden in a Script Code blob; a missing piece is a new generic node (ask first); reusable bundles are node groups. All examples were rebuilt that way (ec0034c, 6ce9d09).

## The game's script API: how it was dumped

| Source | Method | Result |
|---|---|---|
| Native functions | `script_help` prints every documented function with a typed signature, but **only when the game was started in developer mode** (`-dev +developer 1`; the `developer` cvar is hidden in retail). `scripts/vscript_catalog.py dump` starts the game that way | 397 documented functions (2026-10-07); the catalogue holds 419 functions |
| Reflection | a `.nut` run with `script_execute`, `getinfos()` on every function, `StringToFile` into `left4dead2/ems/` | 859 functions including inherited; parameter counts and Squirrel typemasks; script classes `CBaseEntity > CBaseAnimating > CBaseFlex > CTerrorPlayer`, `CDirector`, `CNavMesh`, `TerrorNavArea`, `CEntities`, `CNetPropManager`, `Convars`, `EntityOutputs`, `ResponseCriteria`, `Decider`, the vector classes |
| Game events | `resource/modevents.res`, `serverevents.res`, `gameevents.res` from the VPKs, with field types | 384 events (381 in the catalogue) |
| DirectorOptions keys, hooks, finale stage keys, HUD table names, constants | strings in `left4dead2/bin/server.dll` (it reads each by name); constants (`HUD_*`, `ZOMBIE_*`, `SPAWN_*`, `DMG_*`, `IN_*`) live in `::DirectorScript` | 124 Director settings in `director_options.py` |
| Valve's own scripts | the VPKs' `.nuc` files are encrypted (ICE); decrypted for research only, never shipped; the Last Stand helpers (`make_brush`, `make_prop`, `make_navblock`...) are in the root table | research only |

Licensing rule: the catalogue keeps names, signatures and field types (facts about the interface); descriptions are Hammerless's own (`scripts/vscript_descriptions.txt`, `netprop_descriptions.txt`), never Valve's help text.

Missing from L4D2's VScript (measured 2026-10-13): no `Entities.CreateByClassname` (spawn with `SpawnEntityFromTable` only); entities have `GetEntityIndex()`, not `entindex()`.

## Scripted mode, the mode add-on and the hooks

Measured 2026-10-07: the decision hooks (`AllowTakeDamage`, `InterceptChat`, `UserConsoleCommand`, `AllowWeaponSpawn`, `ConvertWeaponSpawn`, `ConvertZombieClass`, `GetDefaultItem`, `ShouldAvoidItem`, `CanPickupObject`, `BotQuery`, `GetNextStage`, `ShouldPlayBossMusic`...) and the custom HUD only work in **scripted mode**, which needs a mode script (`<mode>.nut`); plain co-op never has it. Hooks must exist when the mode starts: they are defined in `<map>_hammerless.nut` (loaded by `ScriptMode_Init`), which also loads the map's logic early. The HUD works at runtime once scripted mode is on (`UpdateHUDTable`). The game ignores loose `modes/*.txt` and `mp_gamemode` / a mode on the command line; modes come from VPKs read at startup, so Hammerless ships `addons/hammerless_mode.vpk` ("Co-op (Hammerless)", base co-op plus a nearly empty mode script); `map <map> <mode>` works from the console; the game must be restarted once after the add-on is installed; `addonlist.txt` gets only "hammerless_mode.vpk" (added by the game, audit 6).

Hook facts measured 2026-10-07/08 and 10-12: `ConvertWeaponSpawn(classname)` gets e.g. `weapon_pistol` (no `_spawn`), and answering `weapon_pistol_magnum_spawn` converts it (2 of 2 spawns); `CanPickupObject` and `ShouldAvoidItem` default to false and any yes wins (the reverse of the others, where any no wins); bots ask `ShouldAvoidItem` constantly; `ConvertZombieClass` is asked only for the Director's own specials; shots on a `prop_dynamic` reach `AllowTakeDamage` with Victim = the prop and Location = the hit point (a head hit lands at head-bone height); `point_clientcommand` runs a console command on one player (its activator: the Camera node); the host's gun can be fired from script with `DoEntFire("<point_clientcommand>", "Command", "+attack", 0.0, host, null)` then `-attack` 0.3 s later, after `SnapEyeAngles` (console `+attack` from the bench does not work); `infected_death` carries no `infected_id` in the real game (only attacker, headshot, blast, gender, minigun, submerged): use `infected_hurt` (entityid valid, `GetHealth` is the health before the hit, amount >= health = kill); `ZSpawn` without a position places a Tank only while none is alive.

## Entity properties (NetProps): the catalogue and what sticks

Status: `done` (2026-10-13, b69fe96 + 21d7c4e). `hammerless/core/data/netprops.json`: 2,631 properties, 426 classes listed, 335 checked in game, built 2026-10-13 from game PatchVersion 2.2.4.3 (NetworkVersion 2.1.0.0, demo protocol 4, network protocol 2100); re-run `scripts/netprop_catalog.py` after a game update.

Method (`netprop_catalog.py dump / tables / harvest / build / check`):

| Step | What | Measured detail |
|---|---|---|
| Networked properties | a short demo recorded in the game (`hl_netprops.dem`); every demo starts with the server's SendTables and server class list | **L4D2's demo layout**: 4 split-screen command infos of 76 bytes (304) per packet; per property 5-bit type, name, **19-bit flags**, 8-bit priority, then table name / exclude name / 10-bit element count / two floats and a **6-bit** numbits (other Source games: 7). 407 tables, 278 server classes |
| Flattening | names as NetProps takes them: base classes merged, containers whose name isn't a member's (`localdata`, `cslocaldata`) and collapsible tables add no name, nested tables as `m_Local.m_iHideHUD`, arrays (tables of `000`.. props) by their own name with a size | `HasProp` accepted 1,617 of 1,629 flattened names on 14 live classes; the 12 misses were a misidentified class |
| Server-only (datamap) fields and per-class lists | one entity of each of the 382 `.fgd` classes spawned out of sight with `SpawnEntityFromTable`; about 3,378 candidate names (the networked ones plus every `m_...` string in `server.dll`) tried with `NetProps.HasProp`, keeping `GetPropType` and `GetPropArraySize` | 335 classes made; `func_areaportal`, `func_areaportalwindow`, `trigger_finale` crash or hang the game when spawned mid-game and are skipped; names right after another in `server.dll` were skipped at first (186 more candidates after the fix: 2,511 -> 2,631) |
| Types | `GetPropType`: integer / float / Vector / string (handles report "integer"); `GetPropArraySize`: 0 networked scalar, 1 datamap scalar, > 1 a list; a server-only text field's length is not a list | |
| Checks | each of the 48 described properties set to a test value and read back 1 s later | sticks: health, max health, health buffer, speed (`m_flLaggedMovementValue`), gravity (`m_flGravity`), FOV (`m_iFOV`), forced / disabled buttons (`m_afButtonForced`, `m_afButtonDisabled`), takedamage, revive count, third strike, glow type / colour / range / flashing (`m_Glow.*`), render colour / mode / fx, model scale, skin, body, cycle, playback rate, collision group, name, weapon clip (`m_iClip1`) and upgrades. Reverts: `m_Local.m_iHideHUD` (the game rewrites it within a second). `Set...Array` takes `(value, index)` |

The nodes: Get / Set Entity Property with a searchable picker (described ones first), sockets typed from the property, an Index for lists. They compile to `HL_GetProp` / `HL_SetProp`, which do nothing (Get gives the default) for a missing entity, property or list slot, or a value of the wrong kind: measured in game, slot 99 of a 32-slot list used to "work" (return junk, write past the list).

Bench limit found on the way: the game's `ScriptFileRead` reads at most 64 KB; a bigger request file blocks the bench forever (`bench.run` refuses over 60,000 characters).

## The test bench

`tests/ingame/bench.py` (2026-10-08, `with Bench() as b:`): installs `bench/hl_bench.nut` as the game's `mapspawn.nut` (run on every map load and round restart), which polls `left4dead2/ems/hl_bench_in` every 0.1 s, runs the Squirrel in the root scope and writes `ems/hl_bench_out` as JSON (`ems/` is the only folder scripts may read and write); about 0.1 s per request. `load`, `value`, `run`, `console`, `mark` / `events_since` / `lines_since`, `wait_until`, `look_at` + `use`, `walk_to(fraction)` (steps the survivors along the path, keeping only spots the Director agrees with: one big teleport doesn't move its furthest-survivor value, and `GetFlowDistanceForPosition` can disagree with `GetCurrentFlowDistanceForPlayer` off the route), `screenshot`, `build` (QUICK preset, never saves the .blend, about 30 s). Regression: `tests/ingame/test_examples.py` builds `demo/demo_level.blend` with every example as `hl_test_examples` (Debug Log on: `HAMMERLESS_EVENT [Example NN: Title] node.output -> node.input`) and plays each; 38 of 42 automated passed 2026-10-08, 40 passed / 1 flaky (33, a lifted bot falls back before the timer tick) / 4 skipped on 2026-10-11 (audit 6). Other regressions: `test_mapcompiler.py`, `test_models.py`, `test_car_alarm.py`, `test_spawn_preview.py`. Older probes (`*_probe.nut`) print `HL...` lines.

Lessons recorded in `tests/ingame/README.md`: test map names `hl_test_*` only; never bake lighting in tests; don't probe the instant a map loads; things set up at map start can fire before a test looks; `cl_drawhud 0` doesn't hide the scripted HUD; the game can hold the request file open (retry); the Director's slow values need `wait_until`; a Tank pushes survivors back (clear infected before walking).

## Examples: what each one measured

62 examples in three menus (`docs/LOGIC_NODES.md` lists them all). Facts they established, beyond the table above:

| Example | Measured |
|---|---|
| 42 Power-Up: Nuke | `infected_hurt` as the kill signal; `SpawnEntityFromTable` pickups; `demo/hl_test_nuke.blend` from `tests/fixtures/nuke_test.py` |
| 43 Slow Motion Headshots | `func_timescale` works without cheats |
| 44/45 Camera | `point_clientcommand` per player |
| 46 Random Zombie Sizes | model scale hitboxes follow the size |
| 49 Swelling Pod | animation held at a charge: Set Playback Rate 0 + `m_flCycle` through NetProps; walk-up swell/burst/20 damage and shooting tested |
| 50 Reskinned Commons | a mannequin on the game's common skeleton worn by every common keeps common behaviour ([../models/](../models/README.md)) |
| 51/52 Sleepers, Husks | placed characters replaced by real commons, `RushVictim`, `Director.GetClosestSurvivor`; a woken common keeps its facing |
| 53/54 lights | `SetPattern` / `TurnOff` on switchable baked lights ([../lighting/](../lighting/layers-and-styles.md)) |
| 55 Speed Boost | `m_flLaggedMovementValue`, glow on survivors |
| 56 Sonar Pulse | commons have no glow input; `m_Glow.m_iGlowType` / `m_glowColorOverride` show through walls |
| 57 Bottomless Clip | `weapon_fire` before the round is taken; `m_hActiveWeapon` then `m_iClip1` |
| 58 Progress Bar Gate | `m_flProgressBarStartTime` / `Duration` show the game's progress bar with the medkit icon and no text |
| 59 No Guns in Here | `m_afButtonDisabled` attack bit, set four times a second |
| 60 Ghost Survivors | `m_nRenderMode 1` + `m_clrRender` low alpha: see-through players keep solid held guns |
| 61 Tunnel Vision | `m_iFOV` (0 = the player's own setting) |
| 62 Top-Down Camera | setting `m_hViewEntity` alone doesn't change what the player sees (measured on the demo map, 2026-10-13): a `point_viewcontrol` needs its `Enable` input (activator = the player), which also sets the player's `m_takedamage` to 0 (set it back to 2; `Disable` restores it) and doesn't freeze them without spawnflag 4. A camera moved with `SetOrigin` jumps on screen at the rate it's moved (10 Hz from a Timer: frames still, then a jump); moved by `SetVelocity` (it is noclip) it moves every tick, evenly. Third person (`thirdpersonshoulder`) keeps the camera's position but the player's own view angles |
| dropped: Tank Rage Meter | an AI Tank's `m_frustration` stays 0 in co-op (2026-10-13) |

Each of 55-61 uses a property the game offers no other way; all checked in game.

## Custom enemies on bot bodies (measured 2026-09-29, not in nodes)

`info_l4d1_survivor_spawn` (character 4-7, input `SpawnSurvivor`) spawns a bot on team 4. The map must precache the L4D1 survivor models (a hidden `prop_dynamic` per model) or spawning crashes with "UTIL_SetModel: not precached" (same for any `SetModel` swap). `CommandABot` move works; attack is accepted but does nothing: the script is the brain (aim with `SnapEyeAngles` each 0.1 s think; fire with `m_afButtonForced = 1`; semi-auto weapons fire once per press, so give an SMG and toggle). Team 2 <-> team 4 bullets are dropped by the engine (not even `AllowTakeDamage` sees them) and survivors can't damage team-4 bots at all; workaround: `weapon_fire` -> `TraceLine` (mask 33570827) from the shooter's eye; enemy -> survivor `TakeDamage(dmg, DMG_BULLET, enemy)`; survivor -> enemy `SetHealth`, and at 0 a `prop_ragdoll` plus `kickid`. Four at once worked; `sb_stop` freezes them; survivor bots won't target them. Status: `not done` (the L4D2.5 goal, see the roadmap notes).

## Limits and known issues

| Item | Status |
|---|---|
| Node groups wanted first (All Of, Any Of, Toggle, Cooldown, Repeat, Random Interval, Countdown, While True, Ramp, Survivors Inside, Nearest Survivor, For Each Survivor Delayed); map-setup groups maybe later | not done |
| `WanderingZombieDensityModifier` per map (no hook early enough) | known issue (director/) |
| First run of the mode needs a game restart | by design |
| Not every example automated (headshots, the Director's own mobs/specials, real aiming) | known |
| Per-map sound overrides, mix layers, per-player DSP, custom enemies | not in nodes (sound/) |
| Game-side files pile up (`hammerless_*.nut`, `vscripts/hammerless/`, `.navNN`, `maps/hl_*`, 39 MB `console.log`) | known issue (audit 6, MEDIUM); a stale file can't change a newer build except `scripts/soundscapes_<map>.txt` |

## Contradictions found

- `docs/systems/LOGIC_AND_SCRIPTING.md` says "54 ready-made graphs"; `docs/LOGIC_NODES.md` lists examples up to 61 (60 numbered graphs, 48 listed last), and `tests/ingame/README.md` says "all 45 examples". The counts are from different dates.
- Commit b69fe96 says 2,511 properties; the catalogue holds 2,631 after 21d7c4e. The commit message is the older number.

Last checked: 2026-10-13 against branch light-layers, commit 46f75ac.
