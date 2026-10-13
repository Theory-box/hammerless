# The AI Director: what was measured and read from the game

Everything Hammerless knows about how L4D2's Director populates a map: the flow, where wandering commons go (read from `server.dll` and checked in game), what the special/mob filter reads, the budget that limits live wanderers, item placement densities, the Director options and hooks and the scopes they live in, and the quirks that cost hours. The nav facts these rules read are in [../nav/](../nav/README.md); the logic nodes that drive the Director are in [../logic/](../logic/README.md). User-facing: `docs/HORDE_EVENTS.md`, `docs/NAV_AND_ZOMBIES.md`, `docs/systems/NAV_MESH.md` ("What we learned about the Director"); the investigation write-up `docs/dev/ZOMBIE_SPAWNING.md`; the item research `docs/dev/research/ITEM_PLACEMENT.md`.

Code: `hammerless/core/spawnsim.py` (wanderer placement simulation, the "Zombie Spawns" nav view), `hammerless/core/navanalysis.py` (zombie report: lost budget, dead zones), `hammerless/core/gamefiles.py` (`director_script`, `DIRECTOR_OPTION_KEYS`, `crescendo_script`, `HLR_FlowReport`), `hammerless/core/navpredict.py` (marks), `hammerless/blender/navview.py` (views). Tests: `tests/ingame/test_spawn_preview.py` (preview against the game), `tests/ingame/spawncheck.py` (commons at street height near the start), `tests/ingame/bench.py`.

Timeline: flow and safe-room rules verified in game 2026-09-24; nav blocker root cause 2026-10-02; DirectorOptions scopes 2026-10-10; wanderer rules read from `server.dll`, the "few zombies" investigation, the budget limit, the special filter, the spawn preview check 2026-10-11; item densities measured 2026-10-07.

## Flow (the escape route)

All verified in game 2026-09-24 unless noted.

| Fact | How known |
|---|---|
| The flow is computed at map load only if the end room's `info_changelevel` names a **different, real** map. Empty or self-referencing: flow -9999, no wanderers, no Tanks, no Witches, console says "an info_changelevel points to the current map" | measured; Build fills in `c1m2_streets` (`build.FALLBACK_NEXT_MAP`) and warns |
| Without safe rooms (PLAYER_START / CHECKPOINT areas) there are no wanderers at all: only timed mobs (every `MobSpawnMinTime`..`MobSpawnMaxTime`, default 90 to 180 s) and specials | measured, Easy; the docs first said "no flow = no commons", which was wrong |
| With flow: about 30 wandering commons within 20 s, even in the start room (an older test map) | measured |
| Survivor spawns outside the Start Safe Room box start the round with the game thinking a survivor has already left | measured |
| Roof areas marked CHECKPOINT break the flow | measured; safe-room marks are padded down and sideways only |
| `GetMaxFlowDistance()` returns the game's route length in VScript; it is 0 and `Director.GetHighestFlowSurvivor()` null on a map with broken flow, so flow-based logic must guard | measured 2026-10-07 (examples) |
| Mid-campaign Valve maps have no PLAYER_START areas (CHECKPOINT only) | read from Valve's navs (audit 4) |
| Teleporting survivors counts as leaving the start area on one map and not on c2m1; dropping all survivors into the void restarts the round | measured 2026-10-08 |
| c2m1_highway has no safe-room start (no wanderers): use c1m1_hotel as the stock reference for wanderer tests; c2m1 is good for flow-based logic tests | measured |

The in-game flow check (`HAMMERLESS_FLOW`) is described in [../nav/analysis.md](../nav/analysis.md).

## Nav blockers (the "room behind the first gate" bug)

Measured 2026-10-02, root cause of "zero commons until a survivor leaves the start": `func_nav_blocker` blocks nav **at spawn** in L4D2; it ignores `StartDisabled`, and `teamToBlock` -1, 2 and 3 behave the same. The Director's start-of-map wanderer population skips everything behind a blocked gate. A vote restart doesn't re-run map-spawn blocking, so the room fills after a restart, which made it look random. Fix (efdaeb2, 0392dc9): Movers' *Block Nav While Closed* default off; measured 0 -> 42-51 commons in the room behind the gate. Earlier notes ("none within 300-550 units of survivors", "OBSCURED marks on that room did nothing") were this bug.

## Wandering commons: the placement rules (read from `server.dll`, 2026-10-11)

Decoded by Fable from the disassembly of `left4dead2/bin/server.dll` (`ZombieManager::OnFlowDistancesInitialized`, `IsValidForWanderingPopulation`, `MaintainWanderingPopulation`), then fitted to in-game counts:

| Rule | Detail |
|---|---|
| Budget | `round(z_wandering_density (0.03) x escape route length)`; `DirectorOptions.WanderingZombieDensityModifier` scales it |
| Where | nav areas picked **uniformly at random by index**: not by size, not by flow; one wanderer per pick |
| A pick is lost when the area | has the crouch or jump attribute, is blocked (`func_nav_blocker`), has spawn attribute EMPTY, PLAYER_START, CHECKPOINT or RESCUE_CLOSET, has no flow (< 0), or attribute bits `0x30000` (`0x10000` NAV_TANK_ONLY, `0x20000` NAV_MOB_ONLY). **NO_MOBS does not matter** for wanderers |
| Appearing | a wanderer spawns only more than `z_spawn_safety_range` (550) from every survivor and out of their sight; live cap `z_common_limit` (30); at the start only the share whose areas lie in the ring out to `z_spawn_range` (1500, straight-line) and hidden appears, the rest wait until survivors come near |
| Live budget | `director_report` (sv_cheats 1) prints "Population: N (out of 30 max)": on a large user map only 12-15, while "0.03 x route" would be 219: the budget reading is **not** a live count. Tripling `z_wandering_density` (0.09, a cheat cvar) gave 27-28 commons on the street at 5 s: hidden spots weren't short, the budget was |
| Why Valve's maps reach 30 | c1m1_hotel: 1582 areas, 287 valid within 1400 units of the start, against 435 / 65 on that user map: props chop Valve's floors into 25-75 unit areas, a clean Hammerless street was one 537 x 537 area. Making static props solid to the nav (f8b7c89) splits streets the same way |
| Commons move | about 300 units in 5 s: compare positions with slack |
| `director_debug_threat_placement 1` | documented to print "Escape route length" / "Total wandering zombie population" on load; in practice printed nothing: use `director_report` |

Model that fits the measurements (2026-10-11): budget ~ 0.03 x route; spread over **all** nav areas by count (EMPTY areas still absorb their share, their picks are lost); at the start only the share in hidden areas 550 to 1500 units straight-line from the start appears (cap 30). A flow-distance ring fit badly (81% misses); the straight ring plus hidden fits the counts.

### The "few zombies" investigation (2026-10-11, `docs/dev/ZOMBIE_SPAWNING.md`)

Symptom on a large user map: 4-6 commons on the street outside the start at 5 s (the user's bar: more than 12), 30 in total, 26 of them on roofs and a raised walkway the survivors can't reach but the Director can't see either. Ruled out, each measured: merged versus exact vis, today's compilers versus Valve's, the Director settings script, the nav worker process, the nav visibility analysis (v0.12.3 and v0.13.0 built a byte-identical export and nav for that file). Lesson recorded: test exactly the user's observation (commons at **street height**: a horizontal radius alone counted roof zombies as "near" and misled four investigators), not a proxy count.

| Change | Street at 5 s | Total | Commit |
|---|---|---|---|
| As built | 4-6 | 30 (26 on roofs) | |
| Unreachable areas marked EMPTY + NO_MOBS | 10-11 | 12-14, all on the street | 757cc14 |
| Marks removed again (they kept smokers off the roofs they use) | | | 93a0bf8 |
| Unreachable areas marked EMPTY only (commons-only, see the special filter below); every flat top seeded | preview 30 -> ~17.5 starting commons (roof areas absorb budget) | | ab773f7 |
| Nav Culling on by default (nav grows from item spawns and Landmarks like the game; roofs need a Landmark) | preview ~30 | | ea2ea97 |
| Drop links off islands | lost picks 32 -> 13 | | 0.13.3 |
| Full-map nav (no Control Volume cut), roofs marked | 0-1 | 0-1 | unexplained |

The user rejected pruning sealed islands (the game doesn't prune) and coarser roof nav; unwanted roof nav is removed with a Control Volume (Nav Mesh, Not Inside). Success bar: more than 12 commons on the street right outside the start room 5 s after spawning.

### `WanderingZombieDensityModifier` can't be set per map (known issue)

The shipping knob would be this option, but it must be set before the Director counts its wanderers. Tried in game 2026-10-11, none early enough (5-12 on the street each time): a map-wide Director script via `logic_auto` `RunScriptCode` 1 s after spawn; the same at file load (`logic_script` spawn); `scripts/vscripts/<mapname>.nut` (not loaded by the game at all); `mapspawn.nut` (runs before `DirectorScript` exists). Not tried: a mode-script hook (`<mode>.nut`, the mode add-on's `director_base_addon.nut` keyed on the map name), re-triggering the count (`OnFlowDistancesRecomputed` fires on nav reload and nav blocker change; a round restart), or a nav with more, smaller areas near the start (a 1210-area split gave 15 and 30 on two loads: inconclusive). The Director panel's *No Wanderers* writes `WanderingZombieDensityModifier = 0`; whether that one is early enough is not recorded as measured.

## Specials and mobs: the spawn filter (from `server.dll`, 2026-10-11)

`AccumulateSpawnAreaCollection` (the special and mob filter) rejects CROUCH | JUMP areas, attribute bits `0x30000`, PLAYER_START, RESCUE_VEHICLE | RESCUE_CLOSET, and NO_MOBS except for some special rules (rules 3, 4 and 6 in co-op). It **never tests EMPTY**: EMPTY is read only by wanderer placement and the wanderer area total. So EMPTY is a commons-only mark (an unreachable roof marked EMPTY keeps wanderers off while smokers, whose default rule 6 is SPAWN_ABOVE_SURVIVORS, still use it), and NO_MOBS is a hordes-only mark. Other measured special facts: `ConvertZombieClass` is asked only for the Director's own specials, not `ZSpawn` / `z_spawn`; `ZSpawn` without a position places a Tank only while none is alive regardless of `TankLimit` ("Couldn't find a spawn pos for type 8"), with a position it works; an AI Tank's `m_frustration` stays 0 in co-op (2026-10-13, the reason a "Tank Rage Meter" example was dropped); limits set at runtime must go on the top-most DirectorOptions table.

Spawn attribute summary (both rules together; bits in [../nav/README.md](../nav/README.md)):

| Mark | Wanderers | Specials / mobs |
|---|---|---|
| EMPTY (`0x2`) | no | unaffected |
| NO_MOBS (`0x2000`) | unaffected | no (with exceptions) |
| OBSCURED (`0x1000`) | counts as hidden even when visible; doesn't get around the 550-unit rule | |
| PLAYER_START, CHECKPOINT | no | PLAYER_START no |
| THREAT (`0x4000`) | the Director picks 2 per round itself, ignores file marks | |

## The spawn preview and the zombie report

`spawnsim.py` simulates the wanderer rule from a seed: the budget, picks by area count, picks lost (with the reason), which appear now (550..1500 and hidden, cap = the Director panel's Max Common Infected), which wait. Checked in game by `tests/ingame/test_spawn_preview.py` (each map loaded several times, every common recorded 5 s after the survivors spawn with nobody moving, against 200 preview rolls on the `.nav` the game loaded):

| Map (2026-10-11) | Count game / preview | Real commons within 300 units of a predicted spot | Result |
|---|---|---|---|
| A large user map, as built | 30 / 30 | 99% | pass |
| The same, roofs marked no-spawn | 12.7 / 13.9 | 92% | pass |
| c1m1_hotel | 30 / 20 | 46% | fail: the route goes through an elevator the nav links don't model (game `GetMaxFlowDistance` 15994, ours 4106) |

The zombie report (`navanalysis.py`, 74c180c) adds Problems rows: over 40% of the budget lost to areas that can't hold wanderers (Valve's maps lose 30-35%), a short route, no horde in a run-through, dead zones (route stretches where nearly every hidden spot 550-1500 units ahead is in view; red under 10 spots in the *Hidden spawn spots ahead* view). On a large user map it predicted dead zones of 56-66% and the game measured 50-70%; none on c1m1 or c5m1.

## Director options, scopes and hooks

Scopes, measured 2026-10-10: `GetDirectorOptions` chains `ChallengeScript > LocalScript > MapScript > DirectorScript` via delegates, re-applied every think, so runtime changes go to the top-most existing table. Options started with `BeginScript` or `ScriptedPanicEvent` become `DirectorScript.MapScript.LocalScript`; a crescendo **replaces** it and leaves it empty when it ends (the map then ran on the Director's defaults: measured). Hammerless therefore writes map-wide settings as `DirectorScript.MapScript.DirectorOptions` (`director_script`, set at spawn via `logic_auto` > `hammerless_director` `RunScriptCode`); a crescendo or a Director Settings node overrides in `LocalScript`, the rest fall through and are back when it ends (`EndScript` only clears `LocalScript`). Audit 6 (2026-10-11): active about 1 s after map spawn, about 3 s before the survivors and the first common (3 of 3 loads); nothing leaks into the next map.

Map-wide keys written (`DIRECTOR_OPTION_KEYS`): `CommonLimit`, `MobMinSize`, `MobMaxSize`, `MobSpawnMinTime`, `MobSpawnMaxTime`, `MaxSpecials`, `SpecialRespawnInterval`, `TankLimit`, `WitchLimit`; Director Spawns switches off write `<Kind>Limit = 0`; `NoMobSpawns = true`; `WanderingZombieDensityModifier = 0`. Crescendos (`A_CustomFinale_StageCount`, `A_CustomFinaleN` = PANIC 0 / TANK 1 / DELAY 2, `A_CustomFinaleValueN`) set `NoMobSpawns = false` and raise `TankLimit` to their TANK stage (fbce443, "crescendos keep the map's Director settings"); measured: a crescendo's *On Finished* fired after every stage, now once after the last (audit round 3). A Director Settings node applied at Map Start was once overwritten by the map-wide settings a second later (fixed, audit round 3).

Where the names come from: about 100 `DirectorOptions` keys, the scripted-mode hooks (`AllowTakeDamage`, `InterceptChat`, `UserConsoleCommand`, `AllowWeaponSpawn`, `ConvertWeaponSpawn`, `ConvertZombieClass`, `GetDefaultItem`, `ShouldAvoidItem`, `CanPickupObject`, `BotQuery`, `GetNextStage`...), the finale stage keys and the HUD table names were found as strings in `server.dll` (2026-10-07). Hooks and the custom HUD work only in **scripted mode**, which needs a mode script; see [../logic/](../logic/README.md) for the Hammerless mode add-on and the hook facts measured in game.

## Items (`info_map_parameters`), measured 2026-10-07

From the FGD and `server.dll` strings: densities in items per square 100 yards (3600 units) of walkable area: pills, adrenaline, molotov, pipe bomb, bile, ammo, melee, pistol default 6.48; defibrillator 3.0; upgrade pack 1.0; chainsaw/launcher 1.0; `ConfigurableWeaponDensity` -1 (every `weapon_spawn` spawns); `MagnumDensity` -1; `ItemClusterRange` 50 (same-kind spots within it are one cluster, one candidate); `ConfigurableWeaponClusterRange` 100 (same tier, no duplicates); `FinaleItemClusterCount` 3; `spawnflags 2` = must exist. Console: `director_*_density` cvars, `director_scavenge_item_override`, `director_solve_item_density`, `director_item_placement_spew` (the way to test), `director_item_placement_method`.

Valve's choices over 55 of 57 campaign maps (10,466 spawners from the newest `*_l_0.lmp` patch, route = nav Dijkstra from the start safe room to the farthest end; scripts in `docs/dev/research/item_placement/`): median densities pills 2.5, pipe bomb 3, adrenaline / molotov / bile / melee / pistol 2, defib 0.8, ammo 0.8, upgrade 1, chainsaw 1; walkable area median 2.0 (100 yd)^2; candidate clusters per spawned item 10-14x for pills/adrenaline/throwables, ~9x defib, 2.3x melee (46% must-exist), 1.4x pistol, 1.2x ammo, ~0.9x upgrade (all must-exist), 3x super weapons; mixed `weapon_item_spawn` combos dominate; candidates sit a median ~650 units straight-line off the route (25%: 350, 90%: ~2000), 50-70% on the floor, ~20% at table height (24-48); tier 2 weapons grow along the chapter (last fifth 32%); start safe room 4 kits, 1 ammo, ~2 weapons; end 4 kits, 1 ammo, ~3 weapons, 1 melee. Caveat: nav-graph off-route distances came out about twice the straight-line ones; straight-line is the trustworthy figure.

Status: `not done`: Hammerless doesn't write `info_map_parameters` (maps get the 6.48 defaults, 2-3x Valve's). The user's preference: generic item spots placed by hand, the game decides. Open questions for `director_item_placement_spew 1`: does a picked cluster fill every spot or one; how a mixed spot gets its item; whether a map with few spots spawns nearly all of them.

## Quirks and test method

- Build & Play turns cheats off first, which resets every cheat cvar: `z_common_limit 0` or `nb_stop 1` persist until the game restarts and silently stop spawning (it happened).
- Test method that works: fresh `map X`, wait about 5 s, count with a probe (`sv_cheats 1`); don't freeze bots or teleport; keep tests about 30 s; the user moving confounds counts.
- Build & Play with a game-analysed nav loads an analysis map first, which also prints `HAMMERLESS_FLOW`: watchers must wait for the second one; with a Blender-made nav there is one load.
- Unlit test maps make the engine set `mat_fullbright 1` for the session; `launch_game` resets it for lit maps.
- Squirrel: nested native calls (`closure.call()`) overflow quickly ("Native stack overflow"); use `bindenv`.
- The game ignores loose `modes/*.txt` and `mp_gamemode` on the command line; modes come from VPKs read at startup; `map <map> <mode>` works.

## Contradictions found

- `docs/dev/ZOMBIE_SPAWNING.md` says (under "Removed later the same day") that Hammerless no longer marks any area automatically; `ab773f7` re-added EMPTY-only marks for survivor-unreachable areas and the CHANGELOG and `docs/systems/NAV_MESH.md` describe them. The write-up is stale on that point.
- `docs/systems/NAV_MESH.md` says the Director "spawns commons where no survivor can see"; the measured rule also needs the 550-unit distance and the ring out to 1500, as above.

Last checked: 2026-10-13 against branch light-layers, commit 46f75ac.
