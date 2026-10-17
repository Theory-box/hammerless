# Weather: L4D2's precipitation as Valve uses it, measured in game, and the Weather Volume

Status: `done` (Weather Volume, commit e28b2b1, 2026-10-16). Code: `hammerless/core/entities.py` (`WEATHER_TYPES`, `WEATHER`, `WIND_CHOICES`, `WINDS`, `WIND_COMMON`, the `func_precipitation` `EntityDef`), `hammerless/core/build.py` (`weather_entities`), the Add panel's Volumes list (`blender/ui.py`, `spawn.py`). Tests: `tests/unit/test_core.py` `TestWeather` (the key mapping); in game `tests/ingame/test_rain.py` and `test_rain_valve.py` (screenshots; nothing checked automatically, rain is client-side particles).

## How Valve does it (read from the shipped maps and sources)

Read from the entity lumps of Hard Rain's c4m1 to c4m5 (2026-10-16):

| Fact | Detail |
|---|---|
| One `func_precipitation` per map, over the whole play area | c4m1: 14032 x 16480 x 2000 units; `preciptype 6` (particle rain storm); `targetname rain`; `rendercolor 31 34 52`; `renderamt` 0 / 50 / 100 depending on the map (c4m1 has its rain **off**: `renderamt 0`) |
| Only 3 to 6 `func_precipitation_blocker` boxes per map, at special spots | not one per building |
| Wind | `env_wind` `wind_normal` (minwind / maxwind 15 / 30, gusts 50 / 100) and `wind_storm` (75 / 150, gusts 100 / 200) |

Why so few blockers: Valve's own guide (VDC, L4D2 Level Design / Hard Rain) says `func_precipitation` "automatically detects the presence of buildings" and blockers are for other areas, like under large props. In Valve's Source SDK 2013 `c_effects.cpp` the old (non-particle) rain types trace with `MASK_SOLID_BRUSHONLY`: brushes only. L4D2's particle systems `rain_fx.pcf` / `rain_storm_fx.pcf` use "Cull relative to Ray Trace Environment" with `PRECIPITATIONBLOCKER`, so the particle rain is culled by a ray-trace environment rather than by brush traces alone.

## Measured in game (2026-10-16)

`test_rain.py` builds `demo/demo_level.blend` (never saved) as `hl_test_rain` with one rainstorm volume over three structures (black `tools/toolsblack` walls so streaks show), each 4 x 4 m inside and 3 m tall, and screenshots inside and outside each; `test_rain_valve.py` loads a Hard Rain map, finds roofed spots inside its rain volume by nav areas and traces, and screenshots them for comparison.

| Structure | Result |
|---|---|
| Brush house (world brushes, doorway, roof) | mostly dry; a few faint streaks inside |
| Model house (the same shape as one static Custom Model) | mostly dry; a few faint streaks inside (so props cull the particle rain too, not only brushes) |
| Open shed (a brush roof on four posts, open on every side) | dry under the roof |
| Valve's c4m3 rooms | fully dry |

So one map-sized volume with no blockers behaves as in Valve's maps; the faint streaks inside our houses were not chased further (not measured against Valve's own indoor streak rate beyond "fully dry" in c4m3).

## The Weather Volume (Add > Volumes)

A `func_precipitation` box with friendly keys, turned into the game's keys at build by `weather_entities`:

| Key | Values | Becomes |
|---|---|---|
| `weather` | storm (default), rain, snow, ash, bugs, smoke | `preciptype` 6, 4, 3, 5, 7, 8 (the particle types L4D2's campaigns use); storm and rain also set `rendercolor 31 34 52` unless given |
| `density` | 0 to 100 (clamped; a non-number warns and uses 100) | `renderamt` |
| `wind` | auto (storm for storm, light for rain / snow / ash, none for bugs / smoke), none, light, storm | **one** `env_wind` named `hammerless_wind` for the strongest wind any volume asks for (storm wins over light), with Hard Rain's values (`WINDS`) and `windradius -1`, gust delays 15 to 30 s, gust duration 5 s, direction change 20, `angles 0 -180 0`; not added when the map already has an `env_wind` |
| `targetname` | `rain` by default | kept |

An imported `func_precipitation` with the game's own keys (no `weather` key) is left exactly as it is. An unknown weather value warns and uses storm.

## Limits

| Item | Status |
|---|---|
| No `func_precipitation_blocker` helper (Valve uses 3 to 6 per map, under large props) | not done; the game's class can be added as an imported entity |
| The faint streaks inside our test houses | observed, not explained |
| Rain is client-side: nothing automatic checks it; the tests produce screenshots | by design |

Last checked: 2026-10-16 against branch behaviours, commit 18c2ce4.
