# Collision from meshes: Convert to Brushes (Extend-or-Fill, Organic, Slabs), invisible materials, Vis Only brushes

What Hammerless knows about giving a mesh collision the game can afford. The feature began as *Make Collision* on a static Custom Model's panel for an archviz house used as one prop (single-sided, unclosed surfaces, tens of thousands of triangles; 2026-10-15, commits 0c5a776 .. 43c3707) and became *Convert to Brushes* on the Selected Object panel, for any mesh, with an *Algorithm* dropdown (0.19.0 / 0.20.0, 2026-10-18: Extend-or-Fill and Organic for closed solids, Slabs = the earlier method). The facts here are each method, what was measured, what was tried first and why it was dropped, and the two things built alongside: invisible model materials and Vis Only brushes (the compiler side of those is in [../compilers/what-ours-do.md](../compilers/what-ours-do.md) and [../vis/](../vis/README.md)). User-facing: `CHANGELOG.md` 0.17.0 "Make Collision", 0.19.0 and 0.20.0 "Convert to Brushes", 0.20.2 .. 0.20.4 (General's speed, exact Fit, Time Limit and Max Sides; 2026-10-19/20), 0.20.5 (General's second speed pass, same result; 2026-10-20).

Code: `hammerless/core/slabcollision.py` (`slab_brushes`, Slabs), `hammerless/core/brushfit.py` (`convert`, Extend-or-Fill; `hull_faces`, the kernel wrapper over the vendored manifold3d; `CutScorer`, the boolean-free scoring of General's candidate cuts; `pool`, `warm_pool`, the worker pool; `_judge_task`, `_LAST`, the worker side of judging and scoring), `hammerless/core/organicfit.py` (`convert`, General/Organic; `judge`, `certify`, `worst_air`, `TriBuckets`, `RayParity`, `Piece.judge_later`, `prescore`, `merge_pass`, `fewer_sides`, `join_slivers`), `hammerless/blender/collisiontool.py` (`HL_OT_make_collision`, `_solid_pieces` choosing the method, the objects it makes, the final Max Sides cap), `hammerless/blender/props.py` (`brush_algorithm`, `brush_detail_depth`, `brush_fit`, `brush_max_pieces`, `brush_time_limit`, `brush_max_sides`, `collision_min_area`, `collision_mode`, `collision_blocks_vis`, the `VIS` Detail choice), `hammerless/core/build.py` (`VIS_ONLY_CLASS`, `solid_geometry`), `native/hlvbsp/map.c` / `write.c` / `phys.c` (`vis_only`, `g_brush_out`, `g_brush_src`), `hammerless/core/models.py` (`_invisible_vmt`, `write_model_materials`), `hammerless/blender/extract.py` (the material -> nodraw mapping), `hammerless/blender/texturing.py` (`selected_targets`, `paint_faces`). Tests: `tests/unit/test_slabcollision.py` (14 hand-made meshes), `tests/blender/run_tests.py` `test_make_collision_modes`, `tests/compile/vis_only_suite.py`.

The house used throughout ("the user's house"): one joined mesh of about 44,000 triangles, interior and exterior, baked-lighting materials; 224 loose parts, of which one welded shell holds most of the area (1,297 square metres, 8,081 triangles, 11% open edges; closed parts only 8.7% of the area, closed *and* convex parts 1.7%). Measured 2026-10-15 with a scratch diagnostic; those numbers are why per-part ideas were small wins and the method had to work on open surfaces.

## Why collision had to be built (the problem)

| Fact | How known |
|---|---|
| A static prop's own collision is its mesh's loose parts wrapped in convex hulls (Custom Model *Collision: Convex Pieces*); a house of rooms is not convex, so its hull fills the rooms | by construction (`models.py`) |
| One prop with thousands of collision pieces loads and collides (4,035 boxes in one `.phy`, written by our own writer, so studiomdl's piece limit doesn't apply) but is slow: every nearby trace enters the one object | measured in game 2026-10-15 (hl_test_house; "laggy, much worse with 20 commons") |
| Scripts can't give an entity collision from triangles: VScript reads no model geometry and makes no shapes; `env_player_blocker` is axis-aligned boxes only and counts toward the entity limit; script-side collision is also invisible to prediction, bullets and the nav | read from the script API dump ([../logic/](../logic/README.md)) and the FGD; reasoning |

So the collision is made in Blender as geometry: brushes (the engine's BSP collision, `CM_BoxTrace`) or Valve-sized invisible props.

## The slab method (`slab_brushes`)

Status: `done` (0.17.0). Everything is in the mesh's own units; `inch` says how long an inch is in them; constants in inches are in `slabcollision.py`.

| Stage | What it does | Constants |
|---|---|---|
| 0. Closed convex loose parts | Loose parts of 4 to 400 triangles that are closed (every edge in two triangles), convex (no vertex in front of any face, within 0.05 in) and face outward become **one brush from their own faces** (coplanar triangles merged into polygons by convex hull on the plane); beams, posts, steps, boxes. Parts facing inward (a room: air, not a solid), thinner than the smallest brush along any face direction, or with over 24 planes stay with the slabs. | `MAX_PART_PLANES` 24 |
| 1. Plane groups | Vertices welded within 0.01 in; triangle normals clustered within 1 degree (largest triangles first set the direction), then offsets along the normal within 0.25 in; each plane split by which way its triangles face. Regions = triangles of one plane group sharing an edge. Regions under *Smallest Face* (`collision_min_area`, square metres, default 0.05) are left out, and so are regions whose whole extent is under 2 in in either wall direction (trim edges, bevels). | `COS_TOL` cos 1 deg; gap 0.25 in; `THIN_IN` 2 |
| 2. Square-cornered regions | A region whose outline edges (over 2 in long) run within 1.5 degrees of the wall's own directions (`_wall_basis`: along the ground and up the wall; x and y for a floor) is covered by **rectangles**: a grid of its corner positions (rounded to 0.01 in; lines closer than 2 in merged, the outer lines kept), cells filled where a triangle holds their centre, empty runs narrower than 6 in with filled cells on both sides closed (railing balusters, deck boards: one surface), a greedy cover growing from each uncovered cell (both directions tried, the bigger kept; overlaps allowed), then rectangles that line up along a whole edge joined. | `RECT_DEG` 1.5; `SNAP_IN` 2; `GAP_IN` 6 |
| 3. Two-sided walls | A front plane and a plane behind it facing the other way, 0.1 to 12 in apart, in the same normal cluster and overlapping in the wall's plane, are paired (largest overlap first). Where both faces are filled, one brush spans the whole wall (never thinner than a slab); where only one is, that side gets its slab. | `MAX_WALL_IN` 12 |
| 4. Gables | A region square-cornered below its lowest slanted outline edge is clipped there: rectangles below (windows as holes), convex pieces above the slope. | |
| 5. Everything else | Convex pieces of the region's own triangles (Hertel-Mehlhorn: triangles merged across shared edges, longest edge first, while the polygon stays convex); slivers under 0.5 square inches dropped. | |
| 6. Slab | Each polygon is extruded 1 in **behind** the surface (the side its triangles face away from), so floors and walls are exactly where they are drawn and doorways keep their width. No edge push: slabs behind the surfaces already meet at corners; pushing edges out 0.5 in (the first version) made lips stick past each other at every corner (seen on stairs). | thickness 1 in |
| 7. Smallest brush | No brush narrower than 1 game unit in any direction (the map compiler refuses them): rectangles, pieces and the slab thickness are widened just enough; the minimum comes from *Units per Meter* (1.05 units, so 52.49, 39.37 and 100 units per metre all pass). Earlier, 177 slivers under 1 unit reached the compiler on the house. | `min_width` |
| 8. Clean-up | Identical brushes once; a brush whose corners all lie inside another brush goes (bigger first). | |

Measured count on the user's house, in the order the method grew (each number from the commit that made it; the mesh changed once along the way, as noted, so they are a trend, not one series):

| Version | Brushes | Note |
|---|---|---|
| Per-piece slabs with a 0.5 in edge push (0c5a776) | 2,758 (17,034 sides) in 1.5 s | every piece a valid brush |
| Plane tolerance, rectangle cover, two-sided walls, gaps filled, thin strips skipped (509363f) | 1,218 (7,549 sides) in 1.5 s | |
| No edge push (1d746e2) | 1,169 | no lips at corners |
| Thin rule per area, square check by angle (1f691a3) | 1,500 | more, because wall pieces are no longer dropped (holes around windows gone) |
| Nothing under 1 game unit (a38506e) | 1,121 | the mesh had changed by then |
| Grid lines within 2 in merged, aligned rectangles joined (80797a4) | 1,689 -> 1,338 at Smallest Face 0.05 | walls a hair off square whole |
| Closed convex parts, gable rectangles (9915ec4) | 1,338 -> 1,267 at 0.05; 1,028 -> 962 at 0.2 | 22 solid parts, 57 / 31 gables |

The first prototype's coverage check (scratch, 2026-10-15): 1,031 regions of 0.05 square metres or more = 96.9% of the surface, 11,405 tiny regions skipped (41 square metres); a leak flood with the brushes as blockers closed off one 18 x 6 m space between z 1.9 and 5.6 m (the upper floor / attic; not examined further).

Unit tests (`tests/unit/test_slabcollision.py`): a room gives 6 brushes each 1 in thick outside it, a floor's top is exactly the floor, a wall with a doorway 2 to 4 brushes, small regions skipped, a two-sided wall one brush, a wall split by the mesh merges, a gable's top uses convex pieces, a very thin two-sided wall keeps a slab's thickness, a step's tread and riser meet without a lip, a wall 1.5 in off square is one brush, a solid box is one brush, a room is not a solid box, a gable wall's rectangles lie below the slope, faces are wound outward.

## The Algorithm dropdown (Convert to Brushes, 0.19.0 / 0.20.0)

In the panel the options read **Auto, Hard Surface, General, Slabs** (since 5059280): Hard Surface is Extend-or-Fill (`EOF`), General is the method called Organic on this page (`ORGANIC`); the stored values are unchanged, so files keep their choice.

`brush_algorithm` in `props.py`; the choice is made in `collisiontool.py` `_solid_pieces`. Extend-or-Fill and Organic need a closed solid (`brushfit.NotSolid` otherwise, with "or use Slabs"); both run in game units (the mesh scaled by *Units per Meter*) and their pieces come back as brushes through `brushfit.hull_faces`.

| Choice | What runs | Status |
|---|---|---|
| Auto (default) | Extend-or-Fill with a 10 s limit; if it raises `TooBig` (over `MAX_CREASES` = 300 inward creases, or the limit), Organic runs with the rest of the 60 s (`organicfit.TIME_LIMIT`, never under 20 s). The message names which ran and, for Organic, why ("too detailed for Extend-or-Fill" / "Extend-or-Fill took too long"). | `done` (50538a9) |
| Extend-or-Fill | `brushfit.convert`: exact pieces on the mesh's own planes, no overlaps, for hard surfaces; *Detail* (`brush_detail_depth`, a distance, default 0.1524 m = 8 units) is the deepest pocket covered over. Alone it has its own 30 s limit (`brushfit.TIME_LIMIT`) and stops with a message past 300 creases. 12+ creases test candidate cuts in the worker pool (597df13; up to 8 workers, since 47ede17 at most half the cores, below-normal priority). | `done`, experimental (92b17b9; measured against CoACD and V-HACD on 12 test props: fewest pieces or tied on every straight-edged one, 0% invisible collision; `CHANGELOG.md` 0.19.0) |
| Organic | `organicfit.convert`: convex pieces cut from the solid until each is within *Fit* of the mesh; below. | `done`, experimental (50538a9 .. efcacb5) |
| Slabs | `slab_brushes`, the method above; any mesh, closed or not; *Smallest Face*. | `done` |

### Organic (`organicfit.py`)

Settings: *Fit* (`brush_fit`): the largest allowed distance from any point of the collision to the mesh, as a percentage of the object's diagonal (default 2.5%; the code calls the resulting distance D). The panel minimum went 0.25% -> 1% in 0.20.2 (the voxels couldn't measure finer), back to 0.25% in 0.20.3 (a finer grid up to a 48M-voxel budget) and to **0.1%** in 0.20.4 (68e05e9: the within-Fit check is exact, voxels only aim the cuts); the floor is **half a game unit** (`collisiontool.py`: `fit = max(asked, 0.5)`, and the message says when that applied). A share of size rather than a distance because Detail as a distance gave one hull on a small Suzanne (5e78a72). *Max Pieces* (`brush_max_pieces`): default 32, shared out by volume over the mesh's loose parts (at least 2 each). *Time Limit (s)* (`brush_time_limit`, 0.20.4, 7740bb5): seconds of cutting at most, default 60 (5 .. 3600); pieces still uncut at the limit are kept as their hulls and the message says so and says to raise it. *Max Sides* (`brush_max_sides`, 0.20.4, 759b167): 6 .. 128, default 64; applies to General and Hard Surface, the final cap in `collisiontool.py` (a piece with more faces than Max Sides goes through `fewer_sides` again there).

Since 0.20.2 (1ae01ef .. 33b3752) the conversion runs per loose part, computes piece data lazily and scores candidate cuts in brushfit's worker pool; the output is unchanged (see "Speed and the statue lesson" below). Since 0.20.4 (68e05e9 .. 3ca7184, 2026-10-20) the "within Fit" decision is exact against the mesh's triangles, cuts are aimed by an exact worst-air search, big pieces are scored without the kernel, and the side cap never falls back to the bounding box (see "Exact Fit, the speed pass and the chest cube" below). Since 0.20.5 (f751710, 8f5338b, 2026-10-20) new parts are judged in the workers while cutting carries on, the worst-air search runs before certify, spot-independent cuts are scored ahead, the workers start with the conversion and the merge pass and scorer do less work; the output is unchanged (see "The second speed pass" below).

| Step | What it does | Constants |
|---|---|---|
| Loose parts (`loose_parts`, `convert`) | Each loose part (vertex connectivity) is converted on its own; a hollow (an inward-facing shell, negative volume) stays with the smallest outer shell whose box holds it. Overlapping closed meshes in one object used to count as one (the kernel counted the overlap twice). A loose part that the kernel sees as several solids (joined only at single vertices, `solids(m)` > 1) is split too, since every cut of it shattered into bits; the message counts them (`pinched`). Max Pieces by volume, at least 2 each; the time left is shared in volume order. | 0e2e71b, b47d0bd |
| Voxelise once | Grid at D/4 (`H_FRAC`), coarsened so the padded box (size + 4D) stays under `VOXEL_BUDGET` = 8 M voxels (about 250 MB at peak; `setup`). Since 0.20.4 the voxels only **aim** the cuts (and only when `AIM_VOXELS` is on, see Worst air): the finish test is exact, so Fit is no longer raised to suit the grid. History: 0.20.2 clamped Fit to at least 2 voxels (below that nearly every empty voxel read "too far" and a finish check tested millions; a statue's body at Fit 0.25% took minutes per step), 0.20.3 (1b232be) made the grid as fine as Fit needs up to a 48M-voxel budget and raised Fit past it. Sized from the padded box since b47d0bd: a 1.5-unit button converted alone with a big object's Fit got 1,100^3 voxels (1.3 GB), padding from Fit and voxel size from the button. The grid origin is shifted by a fraction of a voxel (`GRID_SHIFT`): on integer coordinates, parity rays hit edges exactly and left stray solid voxels in hollows. Inside = ray parity along x (`parity_mask`); thin walls get surface voxels (`surface_mask`, points every h/2 over each triangle) but only where no inside voxel is within one voxel; surface voxels everywhere brought every distance a voxel closer and finished pieces early. | `H_FRAC` 4; `VOXEL_BUDGET` 8 M; `GRID_SHIFT` (0.2345, 0.3456, 0.4567) voxels |
| Lazy pieces (`Piece`) | A piece's voxels and hull planes are worked out only on first use: a cut is tried ~100 ways per step and almost none of those parts are looked at again. Measured 2-2.5x faster, the same pieces (1ae01ef). Parts of a cut take their own voxels by identity (`side_masks` answers side by side, not in the order asked; before, a part could get another part's voxels when several lay on one side). | |
| Worker pool (`_scores`) | Candidate cuts are *scored* in brushfit's worker pool (`_organic_task` -> `plane_parts`, `fold_crumbs`, `group_score`, all in `brushfit.py` so the workers load one file); the main process re-cuts only the winner, plus the peel candidates for their done check. Workers start when a step's triangles x candidates exceeds `POOL_WORK` (100k) or after `POOL_AFTER` = 1 s of cutting in one process (small shapes never); 16+ candidates needed. The pool (`brushfit.pool`): at most cpu//2 workers (max 8, none under 4 cores), set to below-normal priority in the worker loader; shut down without waiting (`shutdown_pool`, waiting took ~3 s); if it breaks, the rest runs in one process. `_organic_task.__module__` must be `STABLE_NAME` (`hl_brushfit`) like `_cut_task`, or inside Blender the workers couldn't unpickle it (6823d8c: the add-on's `bl_ext...` module path exists only in Blender). Measured: 40k-triangle hand 15.6 -> 5.6 s; 14k-triangle Suzanne in Blender 23.8 -> 9.0 s; same output. Since 0.20.5: the workers spawn at the start of the conversion (`warm_pool`, 8f5338b); a step's candidates go to each worker in 3 batches, and the worker keeps the scorer and solid of the piece it scored last (`brushfit._LAST`, by key), so only the first batch builds them; pickling the mesh to a worker is cheap (1.6 MB, about 1-2 ms on a 45k-triangle body). In the workers organicfit loads as the package `hl_core` (`_judge_task`, like `_organic_task`, must carry `STABLE_NAME`). | `POOL_WORK` 100,000; `POOL_AFTER` 1 s; 3 batches |
| Which piece next | The piece with the most air: hull volume minus volume (a heap, `convert`). | |
| Within Fit (`certify`, 0.20.4, 68e05e9) | A piece is "within Fit" by **exact point-triangle distances**: cubic cells over the piece's hull are refined adaptively until each is certainly within Fit (its centre's distance plus half its diagonal) or a hull point beyond Fit is found. Tolerance `CERT_TOL` = 1e-3 x Fit. Three shortcuts since the speed pass (80341d8) decide most cells at the coarsest level: `_no_air` (a cell wholly inside the solid), `_patch_bound` (a cell whose nearest surface patch bounds the whole cell within Fit) and a corner bound. Before 0.20.4 the test was voxel-based (`Piece.far`: the exact EDT from every voxel to the nearest solid voxel, argmax among voxels inside the hull and farther than D), so Fit meant something different at each grid size. | `CERT_TOL` 1e-3 of Fit |
| Judge (`judge`, `Piece.judge_later`, 0.20.5, f751710) | The done decision for a piece, arrays in and plain values out so it runs in a worker (`brushfit._judge_task`). For a piece with air to spare (air over `WORST_FIRST` = 0.05 x Fit x its hull area on average) the cheap `worst_air` search runs **first**: a hull point beyond Fit x (1 + `CERT_TOL`) settles "not done" and is the aim of the next cut anyway; `certify` runs only when the search can't tell (worst air within Fit or the tolerance). Either order gives the same answer; before, certify failed 24 pieces in 32 on the statue at 0.25 s each and the search ran afterwards regardless. The two parts of the chosen cut are judged in the workers (`judge_later` submits, `_pending`) while the main process goes on; measured, the serial parts had dominated with 8 workers: certify 7.3 s, worst_air 5.7 s, merge_pass 3.3 s, splits about 2 s of 24 s. | `WORST_FIRST` 0.05 |
| Worst air (`worst_air`) | Where to aim the next cut: a **branch-and-bound** search over cells of the hull's box for the hull point farthest from the solid (a cell can hold no point farther than its centre's distance plus half its diagonal; the frontier keeps the `AIM_CAP` = 100k best-bounded cells and stops at cells of `AIM_RES` = 0.25 x Fit). `AIM_VOXELS = False` keeps the old voxel EDT aiming for comparison. Inside/outside by `RayParity` (ray parity along x), nearest triangles by `TriBuckets` (below). | `AIM_RES` 0.25 Fit; `AIM_CAP` 100,000 |
| Nearest triangles (`TriBuckets`, 3ca7184) | Queries walk an 8-to-1 **pyramid** over the triangle buckets, each level with the cell's surface box and sample points, so whole regions that can't hold the answer are skipped. On one 12k-triangle piece the point-triangle pairs went 5.97 M -> 60 k; worst_air 4x and certify 1.5x faster on the statue. Results bit-identical to brute force on 2,700 points. `RayParity` is bounded to 1 M point-triangle pairs at a time (`PAIRS`); `_ray_bucket` = extent/128. Since 0.20.5 `_measure` sorts and masks instead of `np.unique` (f751710). | `PAIRS` 1,000,000 |
| Candidates (`candidates`, `snapped`) | The 10 mesh face planes nearest the worst-air spot plus the 12 biggest face planes (planes that really cut the piece: the mesh must reach 0.5 units past them both ways); the axes, the piece's SVD axes and the 3 hull-face normals nearest the spot, each through the spot at offsets 0 and ±D; blind cuts at 25/50/75% along each axis; and a snapped copy of every candidate onto a nearby mesh face (within 2°, within 2·max(D, 2 units)), so a post under a beam stays a box instead of taking a wedge (37f302d). Since 99c62b0 it no longer builds an all-vertices x all-planes array: on a 26k-face body that was 7.7 GB and 4.5 s per call. | |
| Prescore (`prescore`, `blind_candidates`, 0.20.5, 8f5338b) | While a popped piece's judgement is still on its way from a worker, the candidates that don't depend on the worst-air spot (the biggest face planes, the axis and blind cuts) are scored in the other workers; `_scores` finds them done and reuses them by plane value. Only for a piece with air to spare (`WORST_FIRST`, as `judge`: one likely to be cut) and with 2+ workers. | |
| Cut scoring (`brushfit.CutScorer`, 99c62b0) | A piece of `SCORER_FROM` = 1,500 triangles or more has its candidate cuts scored **without the kernel**: what `plane_parts` + `fold_crumbs` + `group_score` would answer, from the mesh alone. Volumes by signed cones from the origin (whole triangles kept per triangle, crossed triangles clipped, the cap from its section loops); hulls of **sorted** points; connectivity by a spanning forest per piece, pointer jumping and union-find across the non-tree edges; cap loops winding the other way are holes, joining the parts on both sides. Face cuts, snapped cuts and pieces carrying a zero-thickness sheet are left to the kernel (`KERNEL`). Under 1,500 triangles the kernel's split is faster (0.8 ms a cut on 600 triangles against 1 ms for the scorer). Only the chosen cut is really made. Since 0.20.5 (f751710): one stable argsort along a slanted direction instead of a lexsort; section loops labelled by doubling (`_cycles`, verified equal to the union-find `_cc`); `npos` through uint8 views; the scorer bench 9.5 -> 7.3 s for 416 cuts. Profile before that: lexsort 27%, the kernel hull 25%, `_label` 14%. Half the `score()` calls are doubled (a sheet placed "plus" against "with solid") and that can't be skipped: 41 disagreements in about 3,300 cases. The hull volume's order sensitivity on 132 real parts is 1e-9 relative once the points are sorted; an inner-polytope point prefilter is **not** exact (5e-4) and was dropped. | `SCORER_FROM` 1,500 |
| Choice (`best_cut`) | 1. A **peel** first: a face cut moving at least 15% of the piece's volume into finished, air-free parts (hollows and frames: a dumpster, a window wall). Peels within 1% of the piece's volume of each other are told apart by the air they leave, because rounding flipped near-ties (upm is stored as 52.4900016784668; efcacb5). 2. Else the least total air, each part beyond two paying `need` = max(D³, 2% of the air), and only if it removes at least `need`. 3. Else **halve**: the 2-part cut whose worst part has the least air, when that part is under 75% of the air and the air is at least 8·D³ (curved shells such as a bowl; not a pinhole). | 15%, 1%, 2%, 75%, 8·D³ |
| Split (`split`, `brushfit.plane_parts`) | A full plane cut through the kernel; leftovers both thinner than max(D, 2 units) along the cut and under 5% of the volume are crumbs. A cut making more than `MAX_SHARDS` = 64 separate bits is refused (a cut that shatters the piece is a bad cut anyway). Parts of a split on the same side of the plane take side & voxelise(part); otherwise voxels are just sorted by side. | `SLIVER` 2 units; `MAX_SHARDS` 64 |
| Crumbs (`fold_crumbs`) | Crumbs are grouped, not joined, while scoring: a part's volume is the sum and its hull `batch_hull` of all its solids (no boolean). A crumb joins the biggest part it *touches* (`min_gap` <= 1e-4); one touching nothing becomes a little part of its own (folded into a part it didn't touch, a piece became 22 scattered solids under one hull: slow to cut, all air). The real union (`batch_boolean` Add) happens only when a piece's `m` is needed: cut again, merged or kept. Before, joining dozens of crumbs touching a sculpt along the cut was a gigabyte boolean, run for every candidate. | b47d0bd |
| Merge pass (`merge_pass`) | Neighbours (gap under 0.25 units) whose joined piece is still done and adds less than max(D³, 1% of its volume) of air become one. Since 0.20.5 (f751710): a bounding-box test before the kernel gap; a pair found apart is never retested; the joined air is estimated from the two hulls before the real join. 3.3 s of the statue's 24 s before. | |
| Slivers (`join_slivers`) | A piece thinner than `SLIVER` = 2 units across any hull direction joins the touching neighbour where joining adds the least air, **only if the joined piece stays within Fit** (0.20.3, aff8c1d); under `MIN_BRUSH` = 1 unit it always joins (the smallest safe brush). Before, it joined whatever Fit said: a statue's hat at its real size had its cavity filled (1 piece at 57% extra collision -> 12 pieces at 10%, now alike at any scale). Late past the deadline (+15 s) only the under-1-unit ones are still joined. | `SLIVER` 2; `MIN_BRUSH` 1 |
| Max Sides (`fewer_sides`) | A curved hull has hundreds of facets, each a brush side. Over the limit (`MAX_SIDES` 64 by default, the *Max Sides* setting since 0.20.4): area-weighted spherical k-means of the facet normals into limit-6 clusters, each plane pushed out to touch the hull (the piece only grows), intersected with the exact bounding box. Measured 0.4-2.7 points of extra invisible collision on a hand, a rock and Suzanne; keeping the biggest facets instead added 3-10. The operator applies the same cap to Extend-or-Fill pieces. **The chest-cube bug** (06c2052, 2026-10-20): the trimmed result was accepted only if its volume was within 1e-6 of the hull's, else the bounding box was returned; the kernel's trim of a plane through the hull's own corners cut 0.005% inside the hull (rounding), so a piece in the user's statue's chest became a cube 4.5x its size. Now a result within 0.1% of the hull's volume is kept; short by more, the planes are pushed out by 1e-5 then 1e-3 of the extent and it is tried again; the last resort is the hull itself, never the box. On the statue, extra collision went 44.7% -> 15.3%. | `MAX_SIDES` 64 (6 .. 128); volume check 1e-3; margins 1e-5, 1e-3 of the extent |
| Time limit | Past it, every piece left is finished as its hull (`stopped`; the message says the last pieces are looser than Fit and, since 0.20.4, to raise *Time Limit*); the merge pass gets 1.25x the limit. The deadline is also checked inside `_scores`, between candidates, so one long cut step can't run past it (47ede17), and since 0.20.3 (1b232be) in the peel search, the merge and the sliver passes too. | `TIME_LIMIT` 60 s (the setting, 5 .. 3600) |
| No scipy | Blender's Python lacks it. The EDT is exact: axis 0 by two sweeps, axes 1 and 2 by a chunked min-plus brute force (`_min_plus`; max error against scipy 8e-7, 0.15 s on 860k voxels). | |

Measured 2026-10-18 at D = 4 units (scratch benchmark; pieces, invisible collision as a share of the volume, time; 0% of the mesh missing everywhere):

| Mesh | Pieces | Invisible | Time |
|---|---|---|---|
| hand | 7 | 7.7% | 3.6-6.9 s |
| rock | 4 | 4.3% | 3 s |
| blob | 8 | 8.3% | 3 s |
| stump | 20 | 4.7% | 11 s |
| Suzanne | 32 (the cap) | 4.4% | 10 s |
| dumpster | 6 | 2.3% | 3.5 s |
| window wall | 4 | 0% | 0.6 s |

Suzanne at Fit 2.5%: 21 pieces at about 7.7% at sizes 0.7, 2.7 and 11 m (scale-independent). A thin bowl (radius 50, 1-unit wall, D 8): 25 pieces, not filled solid. At equal piece counts it beat the earlier line-of-sight attempt (Suzanne, 8 pieces: 14.5% vs 16.3-16.8% invisible, 4 s vs 30 s).

Speed since 0.20.2 (same output as before): a 40k-triangle hand 15.6 -> 5.6 s; a 14k-triangle Suzanne in Blender 23.8 -> 9.0 s; the user's statue (below) out of memory at 85 s -> 7 s.

### Speed and the statue lesson (0.20.2, 1ae01ef .. 33b3752)

The user's statue: 10k triangles, 14 loose parts, converted at Fit 0.25%. It ate all memory and froze the PC. Found in order:

| Fact | Detail |
|---|---|
| Root cause | `brushfit.solids()` ran a coincident `q ^ m` boolean (a solid with itself, every face coincident: the kernel's worst case, 50 ms on an 8k-triangle sculpt) on *every* solid whenever decompose had dropped any part. A welded sculpt carries positive dust tetrahedra (about 2e-5 units^3, dropped as dust under 1e-3), and the boolean itself left hair-thin tetrahedra, more dust: so the next cut did it again on every solid, 22 of them; one cut step took 100 s and gigabytes. Now a solid is intersected with m only when a real hollow (a negative-volume part) lies inside its box (33b3752). |
| Result | The whole statue at Fit 2.5%: out of memory at 85 s -> 59 pieces in 7 s, 2.9 GB peak, nothing missing. |
| Rejected: `Manifold.compose` instead of the boolean | drops an inside-out shell, so a hollow can't be put back. |
| Rejected: rebuild the mesh from arrays | loses the zero-thickness sheet a cut exactly along a face leaves, which the hull counts and the dumpster's peel depends on. |
| Other guards from the same hunt | `MAX_SHARDS` 64; Fit clamped to 2 voxels (panel minimum 1%); grid from the padded span; the deadline inside `_scores`; cpu//2 workers at below-normal priority (47ede17, b47d0bd). |

Testing practice for this code: a 20-mesh regression set (hard props, organic shapes, the user's files, the bowl) must stay "WORSE 0" against the previous build, and any speed-only change must give identical output. Heavy runs go in a job object with memory, CPU and time caps: an earlier unattended 8-worker test (28 GB) froze the user's PC. Since then tests run at about 20% of the CPU (3 of 16 cores by affinity), at most 2 workers, memory-capped through the job object. Harness tip (2026-10-20): under a 3-core affinity, pip numpy's OpenBLAS (24 threads) made small matmuls 30x slower; set `OPENBLAS_NUM_THREADS=1` when profiling. Blender's numpy has no threaded BLAS, so the add-on in Blender is not affected. Second lesson (the chest cube, 06c2052): reproduce a user report by running their actual `.blend` in background Blender; the npz dumps differ in scale and point order and the bug did not show in them.

### Exact Fit, the speed pass and the chest cube (0.20.3 / 0.20.4, 2026-10-19/20; 1b232be .. 3ca7184, branch general-exact)

The second round on the user's statue (26k faces for the body alone, 14 loose parts; converted at Fit 2%, Max Pieces 16, in Blender under a 3-core limit). The rows of the General table above carry each mechanism; this is the order and the measurements.

| Fact | Detail |
|---|---|
| Exact certify (68e05e9) | "Within Fit" decided by exact point-triangle distances over adaptive cells over the hull (`certify`, `CERT_TOL` 1e-3 x Fit); voxels only aim the cuts at an 8M budget; Fit no longer raised for memory; the panel minimum 0.1%, the floor half a game unit. |
| Speed pass (80341d8) | `_no_air` / `_patch_bound` / corner-bound shortcuts in certify; aiming by a branch-and-bound `worst_air` search instead of the voxel EDT (`AIM_VOXELS=False` keeps the old way for comparison). **Audit**: all 237 certify decisions of the regression set (20 meshes plus the statue body) run with and without the shortcuts, plus 40k random points sampled per accepted piece: 0 disagreements, 0 pieces beyond Fit. |
| CutScorer (99c62b0) | Candidate cuts on pieces of 1,500+ triangles scored without a boolean (the row above). Finding on the way: Manifold's `hull_points` is **order-dependent**: up to 10% volume difference on sculpt parts, sometimes smaller than the true hull; the scorer sorts its points first. |
| Memory (99c62b0) | `candidates()` no longer allocates vertices x planes (7.7 GB, 4.5 s per call on the 26k-face body); `RayParity` bounded to 1M point-triangle pairs; `_ray_bucket` = extent/128. The body that needed about 16 GB peaks under 2 GB. |
| Chest cube (06c2052) | `fewer_sides` fell back to the bounding box when the kernel's trim cut 0.005% inside the hull (the check allowed 1e-6); now 0.1%, planes pushed out on retry, the hull as the last resort. Extra collision on the statue 44.7% -> 15.3%. |
| Pyramid queries (3ca7184) | `TriBuckets` walks an 8-to-1 pyramid; 5.97 M -> 60 k pairs on one 12k-triangle piece; worst_air 4x, certify 1.5x faster on the statue; bit-identical to brute force. |
| Settings | Time Limit (s) (7740bb5) and Max Sides (759b167), above. |

Timings on the statue (Fit 2%, Max 16, in Blender, 3-core limit): 52 s with the cube bug -> 40 s (cube fixed) -> 31 s (pyramid queries); 18 pieces, 15.3% extra air. The "about 70% of the time is the workers' cut scoring" reading was an artefact of the 2-worker test harness; with the user's 8 workers the serial parts dominated (next section).

### The second speed pass (0.20.5, 2026-10-20; f751710, 8f5338b)

Profiled on the user's statue in their own Blender (26k faces, Fit 1%, Max 32, 8 workers): 24 cut steps with 120-734 candidates per step, about 86% of them snap planes, 15-50 ms per candidate on the 45k-triangle piece. The serial parts were the cost: certify 7.3 s, worst_air 5.7 s, merge_pass 3.3 s, splits about 2 s of 24.0 s. Inside the scorer: lexsort 27%, the kernel hull 25%, `_label` 14%. Pickling the mesh to a worker: 1.6 MB, about 1 ms.

Every change keeps the answer (rows in the General table above carry the mechanism):

| Change | Where |
|---|---|
| 1. `judge`: worst_air first for pieces with air over `WORST_FIRST` = 0.05 x Fit x hull area; a hull point beyond Fit (1 + `CERT_TOL`) settles "not done"; certify only when the search can't tell | Judge row |
| 2. New parts judged in the workers (`judge_later` / `_judge_task`; organicfit loads as the package `hl_core` there) | Judge row |
| 3. `merge_pass`: box test before the kernel gap; pairs found apart never retested; joined air from the two hulls before the real join | Merge pass row |
| 4. `CutScorer`: one stable argsort along a slanted direction instead of lexsort; section loops labelled by doubling (`_cycles`, equal to `_cc`); `npos` via uint8 views; scorer bench 9.5 -> 7.3 s for 416 cuts | Cut scoring row |
| 5. Scoring in 3 batches per worker, the scorer cached per worker (`_LAST`) | Worker pool row |
| 6. `prescore`: spot-independent candidates scored while the popped piece is judged, reused by plane value | Prescore row |
| 7. `warm_pool`: the workers spawn at the start | Worker pool row |
| 8. `TriBuckets._measure`: sort + mask instead of `np.unique` | Nearest triangles row |

Results: the 20-mesh regression set 0 WORSE, every row the same (23.8 -> 22 s); the user's statue in their own Blender with 8 workers 24.0 s -> 14.3 s, the same 35 brushes, 8.7% extra air, identical 1,864 faces; under the 3-core limit 41 -> 33 s.

Limits found (why it stops here):

| Fact | Detail |
|---|---|
| About 5 s is not reachable with answer-preserving work | it would need about 3x fewer candidate scores |
| Pruning candidates by kind would change picks | snap faces win 12 of 24 steps, by margins of only 0.2-3% |
| Hull volume has no cheap lower bound to prune with | an inner-polytope point prefilter is not exact (5e-4), dropped |
| Half the `score()` calls are doubled (sheet "plus" vs "with solid") | can't be skipped: 41 disagreements in about 3,300 cases |
| Not done yet | `_cc` for non-tree edges, the double scoring, the kernel hull (33% of scoring) |
| Harness note | each background Blender launch leaves a third-party "Pixl Studio AI" `pixl_mcp.py` process behind (not ours; kill it after a run) |

### Match Seams research (branch match-seams, SHELVED 2026-10-20)

Question: do neighbouring pieces leave a lip (a step) at their seam? Found with a fair metric (a lip counted within 1 Fit of the seam; scratchpad `valleys/seams2.py`):

| Fact | Detail |
|---|---|
| Where lips come from | Only from the side cap (`fewer_sides`): uncapped neighbours meet exactly along the cut plane. |
| How big | The plain cap's lips are only 6-20% of Fit. |
| Hinge-plane prototype | Planes through a shared, simplified outline of the seam: worse on every organic shape tried, lips 24-69% of Fit, air +0.5-3 points. |
| The one air-neutral idea | Hinge planes from the **pre-cap** hull, only where a lip exceeds 0.1 Fit and the piece has a side to spare. Not built. |
| Revisit | Only if a user feels a step in game. |

### Fixes shared by Extend-or-Fill and Organic (960cef0, 37f302d)

| Fact | Detail |
|---|---|
| `brushfit.hull_faces` | A sliver triangle joins a face when its corners lie on that face's plane (its own normal is noise). Before, it made a second, overlapping face on the same plane. |
| Face outlines | Monotone-chain 2D convex hulls (`_outline`), so a face never folds over itself. |
| Corners | The operator keeps only the corners the faces use; dropped corners used to show as loose points. |

### Tried for Organic and dropped (2026-10-18; prototypes, benchmarks and scripts were session scratch only)

| Idea | Result | Why dropped |
|---|---|---|
| Line-of-sight / weak-convexity spectral clustering (Asafi 2013 style) | useful only while pieces were point-blob hulls; once pieces were cut solids scored by air, an ablation showed the LoS cuts rarely won and cost 10-14 s | dropped |
| Point blobs (Voronoi assignment, then hull, then trim) | bulgy, overlapping, 100-330 s | slow and loose |
| Morphological closing as a fill detector | finds gaps, not concavity | wrong measure |
| Global-hull pockets | Suzanne's whole face is one pocket | too coarse |
| AO / curvature filters on flat-region creases (Suzanne 457 creases -> 58 region boundaries) | superseded by the cut-and-check method | |

## Collision As: Brushes or Props

| Choice | What is made | Status |
|---|---|---|
| Brushes (default) | One object `<name> Collision` of nodraw brushes, Detail `func_detail`; with *Blocks Vis* the pieces from opaque faces get Detail World (they block vis like world walls) and the pieces from see-through faces stay a separate `func_detail` collision set. They count toward the map's 8,192 brushes (`MAX_MAP_BRUSHES`, hlvbsp stops with vbsp's message); the tool warns over 3,000. | `done` |
| Props | Invisible static props `<name> Collision Prop N`, each of at most 20 pieces (`MAX_PROP_PIECES`): the pieces are split by halving along the longest axis of their centres until each part holds 20 or fewer (`_chunks`, a kd split, so each prop is compact); Custom Model, Static, Collision Convex Pieces; material `tools/toolsnodraw` by default (never drawn, see below). With *Blocks Vis*, one more object `<name> Collision (blocks vis)`: the opaque faces' pieces as Vis Only brushes (nodraw, drawn as wire). No brushes of the map's 8,192 are used for the collision. | `done` (7c8b601, 569f938) |
| See-through faces with Blocks Vis | A face whose material is see-through (`$translucent`, `$alphatest`, `$additive`, or Blender alpha: glass, fences, foliage) still collides but is never a vis blocker: the blockers are made from the opaque faces only, in both modes (`collisiontool.py`). Before e28b2b1 a glass wall hid everything behind it. | `done` (e28b2b1, 2026-10-16) |

Why 20 pieces per prop: measured 2026-10-15 in game, the user's house with its full model visible (`fps_max 0`, 8 samples, median fps; `vprof_on` / `vprof_generate_report` print nothing in retail L4D2, so frame rate is the only probe):

| Collision | 80 commons (horde) | Idle |
|---|---|---|
| Brushes | ~117 | ~123 |
| Props of <= 5 pieces | ~106 | ~130 |
| Props of <= 20 pieces | ~114 | ~127 |
| Props of <= 80 pieces | ~103 | ~117 |

Valve-sized props (about 20 pieces, what the game's own props carry: ~40 at most, low hundreds for the biggest) cost the same as brushes within 3%. Per-ray cost of a prop's collision stays 5 to 8 microseconds against 0.1 to 2.6 for brushes (0.06 microseconds per trace in the first test); it is the number of props a trace enters that decides the frame rate, not the per-ray cost. The earlier tests that led here (hl_test_hcol_a/b/c, 20 commons): brushes ~100 fps (97-111); the same 1,218 pieces as **one** prop 14-17 fps (13.6-15.3 in the clean `$no_draw` re-run, so rendering was not the cause: every nearby trace enters the one big object); props per 4-metre block ~17 fps (chunks too big); about 60 props of <= 20 pieces ~100-111 fps. The first "props 100x slower" reading was misleading: transparent rendering plus chunks too big.

## The objects Make Collision makes

| Fact | Detail | Status |
|---|---|---|
| Parented to the source mesh | Made in the mesh's collections and parented to it (`matrix_parent_inverse` keeps world positions), so they move with it; `test_make_collision_modes` moves the mesh 3 m and the brush follows. | `done` (b6e0a6f) |
| Found by parent | A mesh's collision is its collision children (plus unparented ones made before b6e0a6f, by the `hl_collision_of` name), so a duplicated house (Shift+D with its children) keeps its own and each one's Make Collision replaces only its own set. | `done` (43c3707) |
| Pressing again | Replaces the previous set, either mode, and keeps the material the previous faces wore (a texture applied by hand, e.g. `tools/toolsinvisible`); the Vis Only object is always nodraw and is not the source of that material. | `done` (d019784) |
| No skipped-faces object | The first version made `<name> Collision (skipped faces)` (not exported) to show what was left out; it is no longer made, and an old one goes on the next press. | `removed` (fea4007) |
| Panel | Since 0.19.0 the *Convert to Brushes* panel on the Selected Object (any mesh); before, Make Collision on the Custom Model panel. The collision objects show no rows of their own (`poll`: an object with `hl_collision_of` can't be a source). The tool warns when the source Custom Model still has its own collision ("set its Collision to None"). | `done` |
| Apply to Selected (texture panel) in Object Mode | Replaces all of an object's material slots with the one texture (before 06dbc88 it added a slot). Picking one collision prop targets the whole set: `selected_targets` adds the parent's other collision children (not the Vis Only one, which isn't paintable); `test_make_collision_modes` checks all four props end with the one material and that pressing again keeps it. | `done` (06dbc88, d019784) |

## Invisible model materials

Two of the game's tool materials get a meaning on a Custom Model (`extract.py` maps the slot's game material to a flag; `models.write_model_materials` writes the VMT in the model's material folder):

| Material on the model | Written as | Behaviour | How known |
|---|---|---|---|
| `tools/toolsnodraw` | `VertexLitGeneric`, `$basetexture tools/toolsnodraw`, `$no_draw 1` | never drawn, still collides (its convex pieces); used by the collision-as-props tests and the default for collision props | measured in game 2026-10-15 (fd84885) |
| `tools/toolsinvisible` | `VertexLitGeneric`, `$basetexture` = `hl_invisible` (a 4 x 4 fully transparent VTF written beside the model's materials by `_invisible_vmt`), `$alphatest 1` | drawn, alpha-tested away: see-through, so a decal can stick to it | the material writes and loads; **the decal result in game is not yet reported by the user** (untested) |

Decals: bullet holes don't draw on nodraw brush faces nor on a `$no_draw` model (observed in game 2026-10-15 during the props tests); that is why `toolsinvisible` exists and why the texture panel can retexture a whole collision set at once.

## Explored and rejected (2026-10-15, scratch prototypes; recorded so they aren't retried)

| Idea | Result | Why dropped |
|---|---|---|
| Voxel cover: voxelise, fill wall interiors by normals, greedy box merge, snap faces to true planes | v1 1,067 boxes in 15-inch bulky bands; v2 (keep-out topology only, shell, plane snap) 5,349 boxes, 63% of air faces on planes; v3 (per-side planes, slab rectangles) 4,196 boxes (3,077 slab + 1,119 residual), 8,026 of 44k faces off by over 1 in | too many boxes, leaks; single-plane walls defeat a grid |
| A space BSP from the mesh's big planes, solid leaves as brushes | 9,080 / 7,677 solid leaves, many artifacts (doorways filled, walls extended by majority votes, O(n^2) fragmentation, samples miss 1-in slabs) | a dead end for unclosed single-plane meshes; "planes shouldn't cut all the way through" led to per-polygon slabs, the winner |
| Fill the unreachable: solids = slabs + details, the space a crouched player body (32 x 32 x 36 units) can occupy by a body-fit test, greedy maximal boxes over the rest | cell flood 4,726; body test 2,539; big-plane grid >= 1.5 square metres 1,501 -> 1,410 after dropping redundant boxes | doesn't beat slabs (1,338 at 0.05 / 1,028 at 0.2) on this house: single-plane walls leave only thin unreachable space; would suit thick-walled closed models |
| Air-first cover: describe the air with few big boxes and carve them from one solid block | superseded by the BSP attempt above (Source has no inverted brushes; the carve is the BSP) | not prototyped on its own |
| Kinetic Shape Reconstruction (CGAL 6), box fitting by binary LP, convex primitive decomposition (Knodt & Gao 2026) | research only | GPL / heavy dependencies; the slab method reached the target without them |
| CoACD / V-HACD convex decomposition | fills rooms, fails single-plane walls, piece limits | wrong tool for open surfaces |
| Displacements shrink-wrapped to the model | the Terrain role is a top-down heightfield; every face as a displacement fails the 2,048 `dispinfo` limit unless the mesh is simplified to big quads first | good for organic props (rocks, cliffs), not boxy houses; not built |
| Script-side physics (`env_player_blocker` pool, script shapes) | scripts can't read triangles or make shapes; blockers are axis-aligned and entity-limited; invisible to prediction, bullets and the nav | dead |
| One big invisible prop holding every piece | loads, collides, 13.6-15.3 fps with 20 commons; floated the sealed attic with a 20 x 8 x 2.7 m lid (residual boxes) | every nearby trace enters it (see the table above) |

## Vis Only brushes (`func_hl_visblock`)

A brush with Detail *Vis Only* blocks visibility exactly like a world brush but collides with nothing and uses none of the 8,192 brushes. Built so Make Collision's props can *Block Vis* without spending brushes; usable on any brush object. Status: `done` (569f938); needs our map compiler (with Valve's vbsp the entity would be an unknown brush entity, a brush model with no vis effect: untested, by reading vbsp's entity handling).

| Step | Where | What happens |
|---|---|---|
| Export | `core/build.py` `build_vmf` | brushes with `detail == "VIS"` leave the world and become one entity `func_hl_visblock` (`VIS_ONLY_CLASS`). `solid_geometry` counts them with the world and `func_detail` brushes, so the near-miss welding lines them up with the other solids; without that, one house brush failed with `FloatPlane: bad normal` in **both** compilers (measured during 569f938). |
| Map compiler | `native/hlvbsp/map.c` | the entity's brushes are flagged `vis_only` and moved into the world (as `func_detail` is), but as **structural** brushes: same tree, leaves, portals and so the same vis as World. |
| Brush list | `native/hlvbsp/write.c` (`BeginBSPFile`, `EmitBrushes`, the leaf writer) | `g_brush_out` maps each map brush to its number in the BSP's brush list, -1 for a vis-only one; `g_brush_src` maps back. They are left out of `dbrushes`, `dbrushsides` and `dleafbrushes`; their leaves stay `CONTENTS_SOLID` with 0 leaf brushes. |
| Physics | `native/hlvbsp/phys.c` (`BuildConvexForBrush`) | builds from the BSP's brush list through `g_brush_src`, so no collision model is made for them. |

Test `tests/compile/vis_only_suite.py` (a room split by a wall with a doorway, three nodraw boxes, once World, once Vis Only): planes, nodes, faces, vis and the portal file identical; leaves identical apart from their brush fields; 3 fewer brushes; solid leaves with no brush present; `func_hl_visblock` gone from the entities. May differ by design: the entity, brush, brush side, leaf brush and physics lumps, the texture lumps (the wall's material is met in another order), the game lump's offsets and the pakfile. The sound tracer counts any `BRUSH` role object as a wall (`blender/sound.py` `_solid`), so Vis Only walls get automatic soundscape triggers: 5 for one house = 104 trigger brushes (measured 2026-10-15). Vis measurements of a house's pieces as vis blockers are in [../vis/](../vis/README.md).

## Background: why Valve built worlds from brushes, and why a house prop is the odd case

Reasoning from the engine's design, not a measurement. In Source, a brush face carries a lightmap, is part of the BSP that decides collision (`CM_BoxTrace`), visibility (leaves, portals, PVS), lighting and decals, and is batched by material: one shape serves every system, and Hammer's workflow is cutting that shape by hand. A static prop is the opposite: drawn as a model (its own draw calls), lit per vertex in L4D2 (`-StaticPropLighting`; no lightmap), collided through `vphysics` convex pieces, and invisible to vis. A house that is one prop therefore needs brushes (or Vis Only brushes) to block vis and brushes or small props to collide, which is what Make Collision provides. Source 2 moved to mesh geometry with separate physics shapes, which is the model this tool approximates in Source 1.

## Limits and what's left

| Item | Status |
|---|---|
| Decals on `tools/toolsinvisible` collision props in game | untested (no user report yet) |
| Leak report for a model (spaces the collision closes off, e.g. the attic flood found in the prototype) | not done |
| Player-hull reachability rules for small regions (walkable ledges, railing clusters as one box) | not done (planned in the prototype notes) |
| Brush count on a big house: ~1,000-1,300 of 8,192 at Smallest Face 0.05 (Brushes mode); Props mode uses none | known; the tool warns over 3,000 |
| The `.phy` of a collision prop: 4,096 triangles per piece (12-bit index) as for any Custom Model | known ([README.md](README.md)) |
| Vis Only brushes with Valve's vbsp | not supported (an unknown brush entity; untested) |
| Vis cost of a house's walls as vis blockers (one house 4.7 s; five houses 290 s on the CPU merged windows, 109 s Tight / 46 s Fast on the GPU since 2026-10-16) | known; see [../vis/](../vis/README.md) and [../vis/gpu-vis.md](../vis/gpu-vis.md) |
| Vis blockers as a simple sealed shell instead of every piece | discussed 2026-10-16, not built: the blockers should be grid-snapped wall and floor slabs with the openings left open (Valve's world brushes are that shell; `func_detail` and props are the detail), and the collision the detailed set. Expected to give most of the drawing gain for a fraction of the leaves and portals; not measured |
| Extend-or-Fill and Organic with *Blocks Vis*: see-through faces are not kept out of the blockers (the opaque-only pass runs for Slabs alone, `collisiontool.py`) | known (experimental methods) |
| Organic in game (frame rate, feel of the pieces) | not measured; the numbers above are from the benchmark only |
| Organic at Max Pieces: a piece can stay looser than Fit (Suzanne hits the cap of 32 at D = 4) | known; the message says to raise Max Pieces or Fit |
| General at Time Limit: pieces uncut at the limit are their hulls (looser than Fit, can be boxy) | known; the message says to raise Time Limit (0.20.4) |
| General's time on dense sculpts: the user's statue 14.3 s at Fit 1% / Max 32 with 8 workers (33 s under 3 cores) after the second speed pass; about 5 s would need about 3x fewer candidate scores, which no answer-preserving pruning gives (snap faces win 12 of 24 steps by 0.2-3%) | known (0.20.5, 2026-10-20); left: `_cc` for non-tree edges, the double scoring, the kernel hull (33% of scoring) |
| Lips at seams between capped pieces (6-20% of Fit) | shelved (branch match-seams); revisit only on a user report of a step in game |

Last checked: 2026-10-20 against branch general-exact, commit af14388 (0.20.5: the second speed pass, f751710 and 8f5338b); 2026-10-20 against c8536c4 (0.20.4: exact certify, the speed pass, CutScorer, the chest cube, pyramid queries, Time Limit and Max Sides; the Match Seams research); 2026-10-18 against branch test-combined, commit 33b3752 plus the 0.20.2 version commit that follows it (the Organic speed work, loose parts and the statue lesson); efcacb5 for the Algorithm dropdown, Organic and the shared fixes; the see-through and shell rows 2026-10-16 against 18c2ce4; the rest 2026-10-15 against a2d00a3).
