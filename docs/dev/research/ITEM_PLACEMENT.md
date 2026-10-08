# Research: how L4D2 places items, and what Valve's maps do

Status: research only (2026-10-07). Nothing here is implemented yet. Idea being explored: the mapper
places generic "director item" spots; the game decides what (and whether) each one becomes, tuned to
feel like Valve's maps.

Scripts and raw results: [item_placement/](item_placement/) (`python measure.py` then
`python summary.py`, run from that folder; set `L4D2` to the game folder if it isn't the default).

## How the game does it

From the game's own files: `bin/left4dead2.fgd` and the setting names and help text in
`left4dead2/bin/server.dll`.

- **Spawners.** Mappers place candidate spots:
  - `weapon_item_spawn`: a mixed item spot with on/off flags per item. `item1` ammo, `item2` kit,
    `item3` molotov, `item4` pills, `item5` pipe bomb, `item11` adrenaline, `item12` defib, `item13` bile,
    `item16`-`18` chainsaw/launcher/M60, `item6`-`8` oxygen/propane/gas can.
  - single-item spots (`weapon_pain_pills_spawn`, ...).
  - `weapon_spawn` (weapon by category: `tier1_any`, `tier2_any`, `any_rifle`, `tier2_shotgun`, ...).
  - `weapon_melee_spawn`.
- **Must exist.** `spawnflags` 2 on item spots means it always spawns and the Director never removes it.
- **Densities.** `info_map_parameters` sets the Director's densities per map, in items "per sq 100 yards"
  of walkable area (100 yards = 3600 units):
  - `PainPillDensity`, `AdrenalineDensity`, `MolotovDensity`, `PipeBombDensity`, `VomitJarDensity`,
    `AmmoDensity`, `MeleeWeaponDensity`, `PistolDensity` (default 6.48);
  - `DefibrillatorDensity` (3.0), `UpgradepackDensity` (1.0), `ChainsawDensity` (1.0: chainsaw and launcher);
  - `ConfigurableWeaponDensity` (-1: spawn every `weapon_spawn`), `MagnumDensity` (-1);
  - `GasCanDensity` / `OxygenTankDensity` / `PropaneTankDensity`.
- **Clusters.** Same-kind spots within `ItemClusterRange` (default 50) form one cluster, which the Director
  treats as one candidate (server text: "considered a single 'cluster' for population purposes").
  `weapon_spawn`s within `ConfigurableWeaponClusterRange` (100) are given the same tier and no duplicate
  types. Finales populate `FinaleItemClusterCount` (3) clusters.
- **Console settings.**
  - Matching `director_*_density` cvars exist (`director_pain_pill_density`, ...).
  - `director_scavenge_item_override` makes the cvars win over the map's values, for tuning.
  - `director_solve_item_density` turns a wanted item count into a density.
  - `director_item_placement_spew` prints what the Director did, which is the way to test behaviour.
  - Also: `director_item_placement_method` (0 old / 1 new), `director_per_map_weapon_upgrade_chance`,
    `director_cs_weapon_spawn_chance`, `director_convert_pills` (+ `_to_defib_health`, `_critical_health`).
- **Hammerless today.**
  - Has "Item (random)" (`weapon_item_spawn`, defaults: pills, adrenaline, molotov, pipe bomb and bile on)
    and "Weapon (random)".
  - Does **not** write `info_map_parameters`, so maps get the 6.48 defaults (2-3x Valve's).

## What Valve's maps do

Measured over 55 of the 57 campaign maps (10,466 spawners, entities from the newest `*_l_0.lmp` patch).

How the measurement worked:
- The route is the nav-mesh shortest path from the start saferoom to the farthest end saferoom (or the
  rescue vehicle in finales).
- The start saferoom is found from the start area flags, else `info_player_start`, else the landmark the
  previous chapter's `info_changelevel` names.

### Densities Valve sets (median over maps [25%-75%])

| Item | Density | | Item | Density |
|---|---|---|---|---|
| Pills | 2.5 [2.0-3.9] | | Melee | 2.0 [1.6-3.0] |
| Adrenaline | 2.0 [1.4-3.0] | | Pistol | 2.0 [1.0-3.0] |
| Molotov | 2.0 [1.4-3.0] | | Ammo | 0.8 [0.0-1.0] |
| Pipe bomb | 3.0 [1.5-3.3] | | Defib | 0.8 [0.5-1.2] |
| Bile | 2.0 [1.0-3.0] | | Upgrade pack | 1.0 |
| Chainsaw/launcher | 1.0 [0.6-1.0] | | ItemClusterRange | 50 (32 maps), 1 (10 maps) |

`ConfigurableWeaponDensity` is mostly -1, so every weapon spot spawns. Walkable area per map: median
2.0 sq 100 yards [1.2-2.8].

### Candidates placed per item the density will spawn (non-finale, outside saferooms)

- **Pills, adrenaline, molotov, pipe bomb:** 11-14 candidate clusters per spawned item. Roughly 7% are
  must-exist. This is where run-to-run variety comes from.
- **Bile:** about 10. **Defib:** about 9.
- **Melee:** 2.3, with 46% must-exist.
- **Super weapons** (chainsaw, launcher, M60): about 3.
- **Pistol:** 1.4. **Ammo:** 1.2.
- **Upgrade packs:** about 1, and 100% must-exist.
- **Spots per cluster:** about 1.1. Valve rarely stacks same-kind spots within 50 units.
- **Mixed spots dominate.** Most common flag sets:
  - adrenaline+molotov+pills+pipe (720)
  - adrenaline+bile+defib+molotov+pills+pipe (720)
  - molotov+pipe (543)
  - bile+defib+molotov+pills+pipe (469)

### Where candidates go (outside saferooms)

- **Distance from the route.** Straight distance to the main route: median ~650 units (25%: ~350, 75%:
  ~1250, 90%: ~2000). Only 2-6% sit right on the route. Items go in side rooms and detours.
- **Height.** 50-70% on the floor, about 20% at table height (24-48 units above the floor), the rest on
  other raised spots (shelves, crates).
- **Weapons along the chapter.** Tier 2 grows along the chapter: by fifths 10/13/22/23/32%. Tier 1 is
  similar, and mostly found later outside saferooms. Weapons sit with throwables and other weapons.
- **Saferooms** (median per room):
  - Start: 4 kits, 1 ammo, ~2 weapons.
  - End: 4 kits, 1 ammo, ~3 weapons, 1 melee.

### Caveats

- Off-route distances along the nav graph (area centre to centre) came out about twice the straight-line
  distances. The straight-line figures above are the trustworthy ones.
- Cold Stream (c13) has very few spawners (30-50 per map). It's an outlier and was left in the medians.
- c3m4 and c10m3 weren't measured (no start found).

## Open questions, to test in game with `director_item_placement_spew 1`

- When the Director picks a cluster of several spots, does it fill all of them or just one?
- How does a mixed `weapon_item_spawn` get its item: per type density, then a spot that offers it?
- Does a map with very few spots spawn nearly all of them (no variety)? The densities suggest yes.

## Possible next steps

1. Write `info_map_parameters` with Valve's typical densities. Expose one "amount" slider (scales all
   densities), not a dozen numbers.
2. Answer the open questions in game.
3. A Problems check: "this map has N item spots; Valve-like variety needs about M for its size" (density
   x walkable area x ~12 for pills and throwables).
4. Later, maybe: suggested spots off the route, in side rooms, as optional markers. The user prefers
   placing spots by hand and letting the game decide.
