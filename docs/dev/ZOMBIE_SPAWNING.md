# Few common infected: investigation (2026-10-11)

Symptom (user's test map, styleguide_urban built from `Untasfasffafafsfitled.blend`): five seconds after spawning,
only a handful of commons on the street outside the start safe room (the user's bar: more than 12), about 8 by the
end room. Specials fine. Reported as "it used to be dozens".

## How the Director places start-of-map wanderers (read from server.dll)

- **Budget** = round(`z_wandering_density` (0.03) x escape route length) (`ZombieManager::OnFlowDistancesInitialized`;
  `DirectorOptions.WanderingZombieDensityModifier` scales it). Live commons are capped by `z_common_limit` (30).
- **Where:** nav areas picked **uniformly at random by index** (not by size, not by flow), one wanderer per pick,
  skipping areas that fail `IsValidForWanderingPopulation`: crouch/jump attribute, blocked (func_nav_blocker),
  spawn attributes EMPTY / PLAYER_START / CHECKPOINT / RESCUE_CLOSET, no flow (< 0), attribute bits 0x30000.
  NO_MOBS does not matter for wanderers (mobs only).
- **Spawning them:** `MaintainWanderingPopulation` spawns where an area holds population and the spot is more than
  `z_spawn_safety_range` (550) from survivors and out of their sight.

## Measured (in game, tests in the session scratchpad: spawncheck.py counts commons at street height near the start)

| Variant | Commons on the street (5 s) | Total |
|---|---|---|
| User's build (Hammerless vis merged) | 4-6 | 30 (26 on roofs / a raised walkway) |
| Same, exact vis | 5-6 | 30 |
| Same map compiled with Valve's vbsp/vvis/vrad (as v0.12.3 did) | 1-4 | 30 |
| Director script off / on | no difference | 30 |
| Nav analyzed by nobody (no visibility lists) | 3-6 | 28-30 |
| Unreachable nav areas marked EMPTY+NO_MOBS (the fix) | **10-11** | 12-14 (all on the street) |
| Full-map nav (no Control Volume cut), unreachable marked | 0-1 | 0-1 |

Ruled out (each measured, not argued): the new vis (merged or exact), today's compilers vs Valve's, the Director
settings script, the nav worker process, nav visibility analysis. v0.12.3 and v0.13.0 produce a byte-identical
export and an identical nav for this .blend (Fable's diff), so the nav this map gets did not change today.

## Cause found and fixed (branch spawn-fix, 757cc14)

The nav inside the user's Control Volume includes 180 roof / raised-walkway areas the survivors can't walk to (they
only drop down to the street). They're hidden from the survivors, so they're the Director's first valid spawn spots
and fill the 30-common cap. Hammerless now marks every area not reachable from the start room (walking, drops,
ladders, zombie climbs) EMPTY | NO_MOBS, and warns when over 20% of the nav is unreachable.

**Removed later the same day:** the mark also kept smokers and the other specials off the roofs they use. With static props solid to the nav (f8b7c89) the streets have many more areas, and the spawn preview on the user's map put only ~4 of 30 starting commons on those roofs with no mark, so Hammerless no longer marks any area automatically (a Control Volume with Zombie Spawns keeps zombies off a spot). A mark that keeps only commons off such roofs, if the game has one, is the open question.

## The second limit: the wanderer budget (Audit 1, measured)

With the fix, the Director keeps only 12-15 live wanderers for the whole ring around the start
(`director_report`, sv_cheats 1: "Population: 12-15 (out of 30 max)"). Tripling the density
(`z_wandering_density 0.09`, a cheat cvar) gives **27-28 commons on the street at 5 s**: the hidden spots exist,
the budget is what's short. Valve's c1m1 reaches 30 at default density (1582 nav areas, 287 valid within 1400 u of
the start, vs 435 / 65 here: props chop Valve's floors into 25-75 u areas, a clean Hammerless street is one
537x537 area). The "budget = 0.03 x route length" reading is wrong as a count of live commons (that would be 219).
Wanderers appear only in the ~550-1500 u ring (z_spawn_safety_range .. z_spawn_range) and as survivors advance.

The shipping knob would be `DirectorOptions.WanderingZombieDensityModifier`, but it must be set before the
Director counts its wanderers. Tried in game, **none were early enough** (5-12 on the street each time):
- map-wide Director script via logic_auto RunScriptCode, 1 s after spawn;
- the same script applying it at file load (logic_script entity spawn);
- `scripts/vscripts/<mapname>.nut`: not loaded by the game at all;
- mapspawn.nut: runs too early (`DirectorScript` doesn't exist yet).

Next to try: an earlier map-specific hook (mode script `<mode>.nut` / the mode add-on's scripts, `director_base_addon.nut`
keyed on the map name), re-triggering the count after the option is set (`OnFlowDistancesRecomputed` exists:
nav reload, nav blocker change; or a round restart), or making the nav itself earn a larger budget (more, smaller
areas around the start like Valve's: Audit 1's 1210-area split gave 15 and 30 on two loads, inconclusive).

## Still open

- With the fix the street gets 7-13 at 5 s (the budget, above). Sight isn't the limit (the x3 density test fills it).
  `director_debug_threat_placement` prints nothing; use `director_report`. c2m1_highway is a bad reference (no safe
  room start: no wanderers); use c1m1_hotel.
- The full-map nav getting 0-1 commons is unexplained.
- (Solved by Audit 2) "Average clusters visible 891" was a stale Valve log (`<map>.log`, an earlier `vvis -fast`
  build): our compilers don't write that file. The map played had ~519 (vvis 507).
- "It used to be dozens": not reproduced with any version; the user's two .blend files build into the same map name
  and folder (each build overwrites the other's), which may explain differing memories of the map.
