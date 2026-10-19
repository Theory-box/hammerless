# Collision from meshes: Convert to Brushes (Extend-or-Fill, Organic, Slabs), invisible materials, Vis Only brushes

What Hammerless knows about giving a mesh collision the game can afford. The feature began as *Make Collision* on a static Custom Model's panel for an archviz house used as one prop (single-sided, unclosed surfaces, tens of thousands of triangles; 2026-10-15, commits 0c5a776 .. 43c3707) and became *Convert to Brushes* on the Selected Object panel, for any mesh, with an *Algorithm* dropdown (0.19.0 / 0.20.0, 2026-10-18: Extend-or-Fill and Organic for closed solids, Slabs = the earlier method). The facts here are each method, what was measured, what was tried first and why it was dropped, and the two things built alongside: invisible model materials and Vis Only brushes (the compiler side of those is in [../compilers/what-ours-do.md](../compilers/what-ours-do.md) and [../vis/](../vis/README.md)). User-facing: `CHANGELOG.md` 0.17.0 "Make Collision", 0.19.0 and 0.20.0 "Convert to Brushes".

Code: `hammerless/core/slabcollision.py` (`slab_brushes`, Slabs), `hammerless/core/brushfit.py` (`convert`, Extend-or-Fill; `hull_faces`, the kernel wrapper over the vendored manifold3d), `hammerless/core/organicfit.py` (`convert`, Organic), `hammerless/blender/collisiontool.py` (`HL_OT_make_collision`, `_solid_pieces` choosing the method, the objects it makes), `hammerless/blender/props.py` (`brush_algorithm`, `brush_detail_depth`, `brush_fit`, `brush_max_pieces`, `collision_min_area`, `collision_mode`, `collision_blocks_vis`, the `VIS` Detail choice), `hammerless/core/build.py` (`VIS_ONLY_CLASS`, `solid_geometry`), `native/hlvbsp/map.c` / `write.c` / `phys.c` (`vis_only`, `g_brush_out`, `g_brush_src`), `hammerless/core/models.py` (`_invisible_vmt`, `write_model_materials`), `hammerless/blender/extract.py` (the material -> nodraw mapping), `hammerless/blender/texturing.py` (`selected_targets`, `paint_faces`). Tests: `tests/unit/test_slabcollision.py` (14 hand-made meshes), `tests/blender/run_tests.py` `test_make_collision_modes`, `tests/compile/vis_only_suite.py`.

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

`brush_algorithm` in `props.py`; the choice is made in `collisiontool.py` `_solid_pieces`. Extend-or-Fill and Organic need a closed solid (`brushfit.NotSolid` otherwise, with "or use Slabs"); both run in game units (the mesh scaled by *Units per Meter*) and their pieces come back as brushes through `brushfit.hull_faces`.

| Choice | What runs | Status |
|---|---|---|
| Auto (default) | Extend-or-Fill with a 10 s limit; if it raises `TooBig` (over `MAX_CREASES` = 300 inward creases, or the limit), Organic runs with the rest of the 60 s (`organicfit.TIME_LIMIT`, never under 20 s). The message names which ran and, for Organic, why ("too detailed for Extend-or-Fill" / "Extend-or-Fill took too long"). | `done` (50538a9) |
| Extend-or-Fill | `brushfit.convert`: exact pieces on the mesh's own planes, no overlaps, for hard surfaces; *Detail* (`brush_detail_depth`, a distance, default 0.1524 m = 8 units) is the deepest pocket covered over. Alone it has its own 30 s limit (`brushfit.TIME_LIMIT`) and stops with a message past 300 creases. 12+ creases test candidate cuts in up to 8 worker processes (597df13). | `done`, experimental (92b17b9; measured against CoACD and V-HACD on 12 test props: fewest pieces or tied on every straight-edged one, 0% invisible collision; `CHANGELOG.md` 0.19.0) |
| Organic | `organicfit.convert`: convex pieces cut from the solid until each is within *Fit* of the mesh; below. | `done`, experimental (50538a9 .. efcacb5) |
| Slabs | `slab_brushes`, the method above; any mesh, closed or not; *Smallest Face*. | `done` |

### Organic (`organicfit.py`)

Settings: *Fit* (`brush_fit`): the largest allowed distance from any point of the collision to the mesh, as a percentage of the object's diagonal (default 2.5%, min 0.25%; the code calls the resulting distance D). A share of size rather than a distance because Detail as a distance gave one hull on a small Suzanne (5e78a72). *Max Pieces* (`brush_max_pieces`): default 32.

| Step | What it does | Constants |
|---|---|---|
| Voxelise once | Grid at D/4, never finer than size/200 (at most about 10 M voxels; `setup`). The grid origin is shifted by a fraction of a voxel (`GRID_SHIFT`): on integer coordinates, parity rays hit edges exactly and left stray solid voxels in hollows. Inside = ray parity along x (`parity_mask`); thin walls get surface voxels (`surface_mask`, points every h/2 over each triangle) but only where no inside voxel is within one voxel; surface voxels everywhere brought every distance a voxel closer and finished pieces early. | `GRID_SHIFT` (0.2345, 0.3456, 0.4567) voxels |
| Which piece next | The piece with the most air: hull volume minus volume (a heap, `convert`). | |
| Worst air | `Piece.far`: the exact EDT from every voxel to the nearest solid voxel, its argmax among voxels inside the hull and farther than D. A piece is done when no such voxel exists. | |
| Candidates (`candidates`, `snapped`) | The 10 mesh face planes nearest the worst-air spot plus the 12 biggest face planes (planes that really cut the piece: the mesh must reach 0.5 units past them both ways); the axes, the piece's SVD axes and the 3 hull-face normals nearest the spot, each through the spot at offsets 0 and ±D; blind cuts at 25/50/75% along each axis; and a snapped copy of every candidate onto a nearby mesh face (within 2°, within 2·max(D, 2 units)), so a post under a beam stays a box instead of taking a wedge (37f302d). | |
| Choice (`best_cut`) | 1. A **peel** first: a face cut moving at least 15% of the piece's volume into finished, air-free parts (hollows and frames: a dumpster, a window wall). Peels within 1% of the piece's volume of each other are told apart by the air they leave, because rounding flipped near-ties (upm is stored as 52.4900016784668; efcacb5). 2. Else the least total air, each part beyond two paying `need` = max(D³, 2% of the air), and only if it removes at least `need`. 3. Else **halve**: the 2-part cut whose worst part has the least air, when that part is under 75% of the air and the air is at least 8·D³ (curved shells such as a bowl; not a pinhole). | 15%, 1%, 2%, 75%, 8·D³ |
| Split (`split`) | A full plane cut through the kernel; leftovers both thinner than max(D, 2 units) along the cut and under 5% of the volume fold into the biggest part. Parts of a split on the same side of the plane take side & voxelise(part); otherwise voxels are just sorted by side. | `SLIVER` 2 units |
| Merge pass | Neighbours (gap under 0.25 units) whose joined piece is still done and adds less than max(D³, 1% of its volume) of air become one. | |
| Slivers (`join_slivers`) | A piece thinner than 2 units across any hull direction joins the touching neighbour where joining adds the least air, whatever Fit says. | `SLIVER` 2 |
| 64 sides (`fewer_sides`) | A curved hull has hundreds of facets, each a brush side. Over 64: area-weighted spherical k-means of the facet normals into 58 clusters, each plane pushed out to touch the hull (the piece only grows), intersected with the exact bounding box. Measured 0.4-2.7 points of extra invisible collision on a hand, a rock and Suzanne; keeping the biggest facets instead added 3-10. The operator applies the same cap to Extend-or-Fill pieces. | `MAX_SIDES` 64 |
| Time limit | Past it, every piece left is finished as its hull (`stopped`; the message says the last pieces are looser than Fit); the merge pass gets 1.25x the limit. | `TIME_LIMIT` 60 s |
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

Last checked: 2026-10-18 against branch test-combined, commit efcacb5 (the Algorithm dropdown, Organic, the shared fixes; the see-through and shell rows 2026-10-16 against 18c2ce4; the rest 2026-10-15 against a2d00a3).
