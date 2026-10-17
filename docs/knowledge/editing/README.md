# Editing: the brush tools (Carve, Intersect, Clip, Hollow, Merge, Draw Brush)

What Hammerless's brush tools do to a brush object, the CSG core they share (`native/hlcsg.c`), the tolerances it uses and why each one is what it is, and what the tests proved. Branch `brush-tools` (2026-10-17): commits 016e048 (the CSG core), ca08848 (Carve, Intersect, Clip and the Brush Tools panel), db91ece (Draw Brush, Hollow, the coplanar-face fix), 9e2ca45 (Merge, two cut orders, the MIN_AREA fix). Going into release 0.18.0.

Code: `native/hlcsg.c` (the CSG, included from `hlnav.c` into the one native library `_hlnav.dll`), `hammerless/core/csg.py` (the ctypes wrapper: `subtract`, `intersect`, `clip`, `merge`, `box`, `plane_from_points`, `Piece`), `hammerless/blender/brushtools.py` (`read_brushes`, `write_brushes`, the Carve / Intersect / Clip / Hollow / Merge operators, `merge_pieces`, the *Brush Tools* panel), `hammerless/blender/drawbrush.py` (the Draw Brush / Draw Carve modal tool). Tests: `tests/unit/test_csg.py` (the random stress test), `tests/blender/run_tests.py` (`test_brush_tools`, `test_draw_brush_maths_and_hollow`, `test_draw_brush_results`).

The repo's own earlier note on this (`docs/dev/HAMMER_AUDIT.md`, the Carve row, priority P3): Hammer's carve "is famously messy, ours can be cleaner". How Hammer's carve works internally is not described anywhere in this repo and is not described here.

## The CSG core (`native/hlcsg.c`)

Status: `done` (016e048, db91ece, 9e2ca45). Standalone: no state between calls (the add-on keeps the DLL loaded); the brush structs are `static` only to keep them off the stack.

| Fact | Detail | How known |
|---|---|---|
| A brush is a set of planes, in double precision | `(nx, ny, nz, d)`, inside where `n.p <= d`; at most 96 sides (`CSG_MAX_SIDES`), 128 vertices per face (`CSG_MAX_WIND`). Polygons are rebuilt from the planes every time (`c_winding`: a 2e6-unit square on the plane, cut by every other plane); they are never carried as truth | read from `hlcsg.c` |
| Every plane carries a tag (`src`) | the side it came from: the tools map a tag to a material, a UV mapping and the painted flag, so a face keeps its material across a cut and a face made by a cutter gets the cutter's (`CUTTER_TAG` = 1,000,000 and up) | `hlcsg.c`, `brushtools.py` |
| Planes are snapped as the map compiler snaps them (`c_snap`) | a normal component within 1e-5 of 1 (vbsp's `NORMAL_EPSILON`) makes the normal exactly axial; a distance within 0.01 of a whole number becomes it. So axis-aligned cuts stay on the grid and oblique ones stay exact. Same rule as `core/collision.py` `snap_plane` and vbsp's `FindFloatPlane` | read from the code |
| Coplanar planes are one plane (`c_same`, `c_add_plane`; db91ece) | a plane whose normal dot is > 1 - 1e-9 and whose distance is within 0.01 (`CSG_ON_EPS`) of one already bounding the brush isn't added again; the first one's tag stays. **Why:** a box drawn on a surface lies on it give or take float noise (Blender keeps 32-bit vertices); counted twice, the overlap looked empty and *Draw Carve* cut nothing when the cutter's face was flush with the target's | measured (the bug) and fixed; `test_cut_flush_with_a_face`: a wall whose planes are off by 3e-5 cut by a flush door is 3 pieces of exact volume |
| Degenerate faces only are dropped (`c_tidy`, `CSG_MIN_AREA` = 1e-6; 9e2ca45) | `c_tidy` keeps every plane whose face has area and sums the volume (`d * area / 3`, the divergence theorem). The threshold was 0.01 at first: dropping a tiny **real** face let a piece poke up to 0.16 units past where it should end (found by Merge's corner check). Now only truly degenerate faces (under 1e-6) go | measured on the stress test; the stress test passes with the tighter shapes |
| Pieces under 0.01 cubic units are no piece | `CSG_MIN_VOLUME` | read from the code |
| A point within 0.01 of a plane is on it | `CSG_ON_EPS`, used in the windings and the merge corner check (x4 there) | read from the code |
| Subtract (`hl_csg` op 0) | peels off, for each cutter plane in turn, the part of what's left in front of it. Two orders are run: the cutter faces sorted by their area **inside the target** (big slabs before slivers, planes that don't cut the target skipped, `c_order`) and the cutter's own order; fewer pieces wins, then the one whose smallest piece is biggest (fewer slivers; a 1.0001 margin). No overlap: the target as it was. Swallowed: nothing | read from the code |
| Intersect (op 1) | the target cut by every cutter plane, or nothing | |
| Clip (op 2) | the target by the cutter's first plane: the piece in front, then the piece behind; an empty side gives a piece of 0 sides (`None` in Python) | |
| Merge (`hl_csg_merge`; 9e2ca45) | two brushes become one when they share a face (the same plane facing each other, `c_same` against the flipped plane), and the planes left (both brushes' but that pair) enclose exactly both: the volume is the sum (within 1e-4 relative + 0.1) and every corner of each is inside every kept plane (within 0.04). Else 0 (not convex) | read from the code; `test_merge` |
| Speed | 32 microseconds per carve including the Python wrapper (the commit's measurement, 016e048) | measured 2026-10-17 |
| Output buffers | the Python wrapper starts with a guess and grows the buffers 4x on a -1 return | `csg.py` `_run` |

## What the stress test proves (`tests/unit/test_csg.py`)

Random boxes on a 4-unit grid, some cut by one or two random oblique planes (wedges), carved by random cutters of the same kind; seed 17, 2,500 cases (more than 2,000 valid). For every case: pieces + overlap = the target (within 1e-6 relative + 0.05); no two pieces overlap and none reaches into the cutter (intersection volume under 0.05 + 1e-6 relative); every piece has at least 4 sides, every face has area, every corner is inside every plane of its piece (within 0.02); every face's tag is the target's or the cutter's; clip front + back = the whole. Fixed cases: a 64-unit box with a 32 x 32 hole straight through is 4 pieces of exactly `64^3 - 32*32*64` with every vertex on the integer grid; no overlap leaves the target; a cutter that swallows the target leaves nothing; the flush-face case above. Merge: two boxes merge into one 6-sided brush of the right volume; an L, an edge-touching pair and an apart pair don't; 998+ random brushes clipped by a random plane through their middle merge back into the whole (seed 5, 1,000 tries, more than 800 must be valid). Runs only when the native library is there (`fastnav.available()`).

Not covered by the stress test: brushes with more than a handful of oblique planes, cutters that are themselves pieces of earlier cuts chained many times (the tools do chain `subtract` over several cutter parts), and non-convex input (the tools never pass it: see below).

## The tools (`hammerless/blender/brushtools.py`)

Status: `done` in tests (ca08848, db91ece, 9e2ca45); **the user's hands-on test is pending** at this page's date. Panel: *Hammerless > Brush Tools* in the 3D view sidebar (Object mode). Every tool works in game units on the mesh itself (`units_per_meter`), reads world matrices after a `view_layer.update()` (db91ece: a scale just set reads right), and is one undo step.

| Tool | What it does | Test (`run_tests.py`) |
|---|---|---|
| Reading a brush object | each loose part of the mesh is one convex brush; planes come from the faces' world-space corners (`plane_from_points`), mirrored objects (negative determinant) handled; a part with a vertex more than 0.05 units outside a face's plane (`CONVEX_EPS`) is **not convex** and is left exactly as it was (the tool says how many). Modifiers are not read (the mesh itself) | `test_brush_tools`: a monkey is untouched |
| Writing back | pieces become loose parts of the same object (vertices merged at 1e-4); each face's material from its tag's record (a material not on the object is added); UVs re-projected through the source face's affine world-to-UV map (`_uv_map`), so texture alignment carries across the cut; the painted flag (`PAINTED`) kept; coplanar duplicate faces skipped | |
| Carve | the active object (the cutter, selected last) is subtracted from the other selected mesh objects (Control Volumes excluded): each convex part becomes convex pieces outside the cutter, chained over every convex part of the cutter; faces made by the cut take the cutter's material. *Delete Cutter* option. | a 2 m box carved by a 1 m door cutter: 4 pieces of 6 m^3 with the cutter's material on the cut faces |
| Intersect | the same selection; keeps only the overlap with the cutter | keeps 4 m^3 |
| Clip X / Y / Z / Clip (cursor) | the selected brushes cut by a plane through the 3D cursor facing X, Y, Z or the cursor's own Z (rotate the cursor to tilt it); *Keep* Front / Back / Both; the new face wears the object's active material | two halves of 8 m^3 at the cursor |
| Hollow | walls of *Thickness* (game units, default 8) all round: the brush minus itself with every face moved in by the thickness, each wall its own convex piece; a brush thinner than two walls stays solid (a warning counts them) | `test_draw_brush_maths_and_hollow`: a 2 m (104.98-unit) box with 8-unit walls leaves exactly the walls |
| Merge | the selected brushes' convex pieces joined wherever two make one convex piece **and** the faces that would become one wear the same material (`_same_look`: coplanar faces of the two pieces, normal dot > 1 - 1e-9, distance within 0.01), repeated until none do (`merge_pieces`); reports "N pieces -> M" | a clipped box merges back into one piece (`test_draw_brush_results`) |

The per-tool numbers above are from the commit messages and the Blender tests (2026-10-17, ca08848 / db91ece / 9e2ca45); the game-unit scale in those tests is the default 52.49 units per metre.

## Draw Brush / Draw Carve (`hammerless/blender/drawbrush.py`)

Status: `partial`: the result functions are tested; **the modal mouse interaction itself has not been tested by events** (only by a person, pending). A modal operator in the 3D view:

1. Hover: the surface under the mouse (`scene.ray_cast` on the evaluated depsgraph); nothing under it means the ground plane at the 3D cursor's height. The drawing frame (`frame_for`) is axis-aligned when the surface normal is within 0.001 of an axis (so a box on a floor or wall is axis-aligned), otherwise along the slope (`u = up x n`, `v = n x u`).
2. Drag: a rectangle on that plane, corners snapped to the grid in game units (`GRIDS` 1..128, default 8; mouse wheel or `[` `]` change it; Ctrl means no snapping). A zero-area rectangle starts again.
3. Height: the mouse's nearest point on the line up through the rectangle's centre, snapped; starts at +grid (Add) or -grid (Carve); click finishes (a zero height does nothing); Esc or right mouse cancels. The header shows the size; a GPU overlay draws the box.

The result (`_finish`): **Add** makes a new closed mesh object "Brush" (faces recalculated outward, since the winding depends on which way the height went) with the surface's material (the hit face's material slot). **Carve** subtracts the box from every visible mesh object in the map collection whose role is Brush or whose class is `func_detail` and whose bounds touch the box, through the same `read_brushes` / `subtract` / `write_brushes` as Carve, with the cut faces in the surface's material; "The box touches no brush" otherwise.

Tests (`test_draw_brush_maths_and_hollow`, `test_draw_brush_results`): the frame on axis-aligned and sloped surfaces, the box's corners on its own planes; Add on the floor makes a closed 64 x 32 x 48 brush with the floor's material; Carve cuts a 2 m by 2 m doorway through a wall (3 pieces, the wall's own material).

## Known issues and not done

| Item | Status |
|---|---|
| The Draw Brush modal interaction (mouse events, overlay, header) | not tested by events; user hands-on test pending (2026-10-17) |
| Hammer's own carve algorithm, for comparison | not researched in this repo |
| Non-convex parts | left as they are by every tool, by design (the brush rules of the exporter apply) |
| Modifiers on the brush being cut | the tools read the mesh itself, not the evaluated one (the importer's build path, by contrast, now reads the evaluated shape: [../game-facts/README.md](../game-facts/README.md)) |
| Texture lock on the cut faces of imported Hammer brushes | the UV re-projection covers Hammerless brushes; imported brushes' sides are written back from their planes ([../game-facts/README.md](../game-facts/README.md), the importer) and the interaction with these tools is not measured |

Last checked: 2026-10-17 against branch brush-tools, commit 55c63a0.
