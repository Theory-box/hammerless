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

## Still open

- With the fix the street gets 10-11 at 5 s: better, but at or under the user's bar. The total drops to 12-14 because
  most remaining street areas are within 550 u or in sight of the start (in the screenshot the start "room" looks
  open-air, so the courtyard is in view). To test: is the start room enclosed (roof, closed door)? Does
  `WanderingZombieDensityModifier` change it? Run spawncheck with `director_debug_threat_placement 1` to print
  "Total wandering zombie population".
- The full-map nav getting 0-1 commons is unexplained.
- (Solved by Audit 2) "Average clusters visible 891" was a stale Valve log (`<map>.log`, an earlier `vvis -fast`
  build): our compilers don't write that file. The map played had ~519 (vvis 507).
- "It used to be dozens": not reproduced with any version; the user's two .blend files build into the same map name
  and folder (each build overwrites the other's), which may explain differing memories of the map.
