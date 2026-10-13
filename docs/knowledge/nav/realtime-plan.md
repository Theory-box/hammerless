# Real-time incremental nav: the plan (not built)

Status: `not done` (design agreed 2026-10-11, awaiting the user's go-ahead for the area stage; only the sweep memory, bevel memory and smart-build reuse exist). Source: the research note of 2026-10-11 and `docs/systems/NAV_MESH.md`, "Real-time nav".

## The goal and the bar

The user wants the nav to update in about 1 s after an edit (on mouse release; the nearby region only is fine). Not a preview, not an approximation: the incrementally maintained nav **is** the nav shipped to the game, and local updates must not change in-game behaviour. The point is speed (no 10 to 30 s waits), not visualisation; "hacky or worse alternatives" are rejected.

Bar, refined by the user the same day:
1. Incremental == full regeneration of **our** generator on the same scene, **byte-identical** after canonical renumbering, proven by a random-edit harness on real maps. A mismatch is a bug, never a silent fallback.
2. Against Valve's generator, gameplay-identical is enough; deliberate improvements that play better are welcome, each its own measured decision shown to the user, never a side effect of speed work. Gameplay metrics and in-game bench checks guard it; Valve's own run-to-run noise floor is measured first.

## What already works

- **Sweep memory**: each sweep is kept with the box of space its traces looked at; when brushes change, only sweeps whose box touches a changed brush are redone, the flood replays the rest in its usual order, so nodes match a fresh run exactly. Brushes are compared by exact planes; if their order changes the memory starts afresh.
- **Bevel planes** remembered between runs (loading 2.0 s -> 0.1 s on a repeat).
- **Smart builds keep the nav** when only logic changed (logic entities, outputs and names aren't part of what the nav is made from; the inputs hash is stored in the game folder).
- The native speed round of 2026-10-11 (generation 1.7 s on urban) makes a full regeneration cheap on small maps already.

## Where generation depends on order (Fable's design analysis, 2026-10-11)

The hard part is making the greedy area merge local, because the Director places wanderers uniformly by area **count** (see director/), so the number of areas is gameplay.

| Dependence | Where |
|---|---|
| Node creation order (DFS flood) | `navgen.py` flood loop |
| Newest-first wins in area creation | `navareas.py` `create_areas` |
| Merge dominoes (a merge enables the next) | `navareas.py` merge pass |
| List-order passes | `split_areas_under_overhangs` restarts; `fix_corner` appends |
| Geometric influence | ~150 units in XY, but `check_cliff` traces 4000 units down |

## The design

- Canonical, position-keyed decisions instead of creation-order ones.
- Fixed chunks of about 1024 units that areas never cross: exact by construction (a chunk's result depends only on its own inputs); the user's tweak: snap chunk seams to walls.
- Cheap global passes after: reachability BFS, canonical renumbering.
- The analysis stays build-time (needs the compiled PVS) but can cache pairs.
- Step 1 = measure: the propagation radius of an edit, and Valve's noise floor.

## Open

Not started: the area stage, the random-edit harness, the gameplay-metric gate. Related: the Director's spawn rules (director/), the "fallbacks are bugs" rule.

Last checked: 2026-10-13 against branch light-layers, commit 46f75ac.
