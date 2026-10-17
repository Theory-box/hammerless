# Game facts: measured engine and file-format facts that don't fit elsewhere

The L4D2 facts Hammerless relies on that belong to no single system: the BSP file as L4D2 writes it, the game's physics library as called from our tools, reading the game's content, the decompiler's findings about Valve's maps, the Hammer-map importer's exact round trip, engine behaviours met while testing, the limits that were hit, and the testing rules that cost data when broken. Facts about one tool live with it: compilers/ (vbsp/vvis/vrad numerics, options, fallbacks), lighting/ (what vrad writes), vis/, nav/ (the `.nav` format), director/, logic/ (VScript, NetProps, the demo SendTable layout), models/ (`.mdl`/`.phy`/VTF), sound/ (`sound.cache`), loading/ (the stringtable dictionary, load timings).

## The BSP as L4D2 writes it (version 21)

| Fact | Detail | How known |
|---|---|---|
| Lump write order | lumps are written (empty ones take the current offset) in the order 59, 6, 2, 43, 44, 10, 17, 1, 18, 19, 14, 5, 20, 21, 4, 0, 29, 62, 26, 3, 12, 13, 7, 33, 48, 28, 9, 8, 53, 37, 38, 39, 30, 31, 51, 52, 55, 56, 16, 36, 45, 50, 60, 61, 46, 15, 41, 42, 54, 34, 47, 11, 27, 22, 23, 24, 25, 49, 35, 40; lumps 32, 57, 58 and 63 are never written by vbsp (58 appears once lit); the lit-map order fixes of 2026-10-13 are in compilers/ | measured on vbsp output 2026-10-08 (`tests/compile/bspdump.py`) |
| Lump alignment | each lump and the file end 4-byte aligned; never-written lumps keep offset 0; a rewriter (vvis, vrad) keeps the input order, sorted by input offset, empties before the non-empty lump at the same offset | measured (vis/) |
| What vbsp writes for a box room | lumps 0-3, 5-7, 9-21, 27-31, 35, 40, 43, 44, 46, 47, 59, plus version-only 8, 15, 53-56, 62; planes come in +/- pairs (36 for 6 boxes including the block and tree bounds planes at +-1024 and +-16384); faces carry `origface`; vbsp prints "Cannot build Physics2 data" (an L4D2 extra, harmless) | measured 2026-10-08 |
| Game lump 35 | `sprp` version 9 (72-byte records; `screenspacefade` obsolete in L4D2; the lighting origin is left uninitialised without `info_lighting`, plus 3 padding bytes) and `dprp` version 4; offsets are absolute file offsets, so lump 35 is rewritten when lumps before it change size; `dplh` is the detail prop lighting; `sprp` moves to the end of the game lumps on `-onlyents` | measured |
| Pakfile (lump 40) | vbsp packs a default cubemap copied from the game (`cubemapdefault.vtf` + `.hdr.vtf` from the sky, about 86 KB) and vvis re-serialises the pakfile; the game adds `stringtable_dictionary.dct` on unload (loading/); static prop vertex lighting goes in as `sp_hdr_<n>.vhv` | measured |
| `stale.txt` | `-onlyents` (vbsp's and ours) adds it to the pakfile; the engine then treats the map as not final; Hammerless strips it because its smart build is equivalent to a full compile | read from vbsp, measured |
| Occluders | `doccluderdata` is 40 bytes in L4D2 (includes the area) | measured (hlvbsp) |
| Overlays | lump 45 `doverlay` 352 bytes, lump 60 fade values squared, lump 61 = 4 zero bytes per overlay | measured (hlvbsp, decompiler) |
| Displacements | `ddispinfo` 176 bytes (startpos, vert and tri starts, power, min tessellation with flags in the high bit, smoothing, contents, map face as u16...); verts 20 bytes, the stored vector = normal x distance with offsets folded in; triangle tags BSP SURF 1 / WALK 2 / BUILD 4 map to VMF WALK 1 / BUILD 8; dispinfo padding and invalid sub-neighbour bytes are uninitialised garbage in vbsp; the displacement physics hull bytes (lump 28) differ between vbsp's own runs | measured |
| Leaf lumps vis writes | 46 (min distance to water), 51/52/55/56 ambient placeholders, 10 leaf flags `area_flags |= 0x400` on radial maps | measured (vis/) |
| Nav header | the `.nav` stores `bsp_size` = the BSP file's size and the game checks it: any rewrite of the BSP (a smart build, the dictionary) must update the nav header | measured (nav/) |

## The game's physics library from our tools

hlvbsp and `hlphys.exe` (hlvrad's helper) call the game's 32-bit `bin/vphysics.dll` directly, so our tools are 32-bit where they need it (zig `x86-windows-gnu -mcpu=pentium4 -mfpmath=sse`; output identical to a 64-bit build).

| Fact | Detail |
|---|---|
| Interface | `IPhysicsCollision007`; the L4D2 vtable matches the SDK 2013 layout up to slot 22 `CollideGetExtent`, then 23 `CollideGetAABB`, 39 `TraceCollide`, 48 `CreateQueryModel`, 49 `DestroyQueryModel`, 52 `CreateVirtualMesh`, 53 `SupportsVirtualMesh` (found by disassembling the slot code; 53 is `b0 01 c3`); `UnserializeCollide` is slot 19, `ConvexFromVerts` 1, `ConvertConvexToCollide` 14 |
| Calling | `thiscall` (`__attribute__((thiscall))`); struct-returning methods (`CollideGetExtent`) take an explicit return pointer as the first argument; `_controlfp(_PC_53)` **must** be set before calling (MSVC's default precision), or results differ |
| What L4D2 builds for a prop with no `.phy` | a convex hull per mesh from the `.vvd` vertices as stored (`ConvexFromVerts`, `ConvertConvexToCollide`); a box only if the model is unreadable; vbsp reads VVD vertices raw, without fixups |
| IVP output | identical except the 4th word of each IVP point (garbage); the inertia approximation isn't public (models/) |
| `MASK_SOLID` in L4D2 | SOLID | WINDOW | MOVEABLE | MONSTER = 33570819 (no GRATE); the nav generator traces with `MASK_NPCSOLID_BRUSHONLY`; the enemy-bot tracer used mask 33570827 |

## Reading the game's content

| Fact | Detail |
|---|---|
| VPK order | `left4dead2`, `update`, the DLC folders, newer first so they override, plus loose files in the game folder (a loose file counts for `has_material` since 500a493, 2026-10-13); damaged VPKs are skipped; Valve's shipped entity patches live in the newest `*_l_0.lmp` next to each map (the item research read entities from them) |
| Valve's scripts | the VPKs' `.nut` scripts are encrypted `.nuc` (ICE); decrypted for research only, never shipped; `sdk_content/` (Authoring Tools) holds Valve's full survivor QC, SMD/VTA, DMX starter kits, Maya rigs and the campaign `*_environment_ents` instances (the Car Alarm preset copies Valve's alarm-car instance from there at build); Valve ships no campaign VMFs |
| FGD | `bin/left4dead2.fgd` (382 classes used by the NetProps harvest; `info_map_parameters` densities documented in it) |
| Cheat-only names | the game's own `cvarlist` marks them "cheat" (`scripts/cheat_commands.py` -> `data/cheat_commands.txt`) |
| Binaries read | `server.dll` (Director rules, DirectorOptions keys, hooks, every `m_` string), `vvis_dll.dll` (radial vis), `vbsp.exe` / `vrad_dll.dll` (numerics; vrad hooked in-process with a launcher that must call `CreateCmdLine` first or `-game` is ignored and a VConfig dialog pops up), `stdshader_dx9.dll` (parallax keys, unused by the PC shaders), `vphysics.dll` (above); disassembled with capstone. Lesson recorded 2026-10-08: disassemble early instead of guessing variants |

## Valve's maps, decompiled (`hammerless/core/bspdecompile.py`, branch `bsp-import`, 2026-10-10/11)

*File > Import > L4D2 Map (from the game) / Compiled Map (.bsp)*; the map name becomes `<map>_remix` so Build never replaces Valve's map; c1m1_hotel imports in 20 s (7,788 objects). Test: `tests/compile/decompile_roundtrip.py` (decompile, recompile with hlvbsp, compare brushes and planes, models, entities, props, cubemaps, displacements, overlays, faces; not lighting or vis).

| Finding | Detail |
|---|---|
| Which brushes belong to which model | a contiguous index range of the leaf-brushes under each model's head node (vbsp writes brushes in entity order; `func_detail`, areaportal and ladder brushes are moved into the world range, occluder brushes after the world); a tree walk alone misses buried brushes; the gap before the first brush entity is unclaimed (`func_viscluster` or world); `func_detail` is any CONTENTS_DETAIL brush |
| Invisible sides | lose material and alignment (`CompactTexinfos`): the tool texture is guessed from the brush contents (areaportal, clip / playerclip / npcclip, blocklight = OPAQUE, blocklos, invisible = GRATE|TRANSLUCENT, L4D2 `tools/climb` and `toolsinvisibleladder` = LADDER, water), only on brushes with no visible face; sides with faces match `origfaces` (plane + texinfo, vertices within 0.5) |
| Plane points | Hammer's grid corners when the face has them, written at full precision (rounding one coordinate tilted far planes); coordinates snapped to integers within 0.01 (float drift adds bevel planes and overflows limits on recompile) |
| Displacements | rebuilt brushes with exact vertices, attached to the world brush side by plane and winding |
| Overlays | `BasisV = normalize(N x BasisU)`, negated when `uv3.z == 1`; named overlays become `info_overlay_accessor`; overlays on brush entities' faces use their origin |
| Props | `sprp` lighting origins become `info_lighting`; a prop exactly on a wall face is moved 0.05 out (vbsp would drop it as outside) |
| Keys vbsp adds, stripped | `hammerid`, `world_mins`/`world_maxs`, model `*N`, light styles >= 32; patched materials `maps/<map>/X_x_y_z`, `_wvt_patch`, `_depth_N` map back to `X` |
| Round trips (81cba0f) | c1m1_hotel 3856/3856 brushes, c3m1 2335/2339, c5m1 2152/2159 (all 184 overlays), c2m1 2014/2053 (the rest: oblique planes under 0.1 units off) |

Research sources: BSPSource (GPL, learned from only), srctools (MIT), bsp_tool.

## Hammer maps imported and written back exactly (`core/vmfimport.py`, 99adf5b, 2026-10-10)

The original text is kept (a Text block, `scene["hl_vmf_source"]`); objects are tagged with their VMF kind, id and index; Build rebuilds the stored file: a face still on its side's plane (tolerance 0.05, same material) keeps its original block, a moved face gets a new plane with the rest of the side kept; entities replaced in place, new ones after the last original; written latin-1; instance files copied next to the built map. Import turns off Auto Seal, the automatic Director, sun and Sound (Valve maps have their own). Proof: all 134 Valve sample maps byte-identical after a round trip; a compile of the rebuilt file differs from the original's only in lumps 0, 28/29 (physics noise) and 35 offsets. Left: editing displacements (kept as imported), texture lock on moved faces.

**Brushes are written back in the shape the viewport shows** (55c63a0, 2026-10-17). `vmfimport._faces` reads the object's evaluated mesh (`obj.evaluated_get(depsgraph).to_mesh()`, freed with `to_mesh_clear`) when the object has modifiers, else the mesh itself; new (non-imported) brushes already exported after modifiers. The case that found it: a user's imported brush carried another add-on's Geometry Nodes modifier ("Gizmo Bounds Transform") that resized it in the viewport, while the build used the base mesh: a 7.3 m wall across a doorway, blocking it in the nav and in the game, invisible in Blender. Diagnosis method, worth keeping: build, load the built VMF's solids with `CollisionWorld.from_vmf` (`core/collision.py`), list the brushes around the 3D cursor, then find the Blender object whose **base-mesh** bounds (not its evaluated bounds) match the odd brush. Test: `test_vmf_import_modifiers_exported` (a Geometry Nodes transform scaling x by 0.5 halves the exported brush about its centre: x sides at 12 and 36 of a 0..48 box). The brush tools, by contrast, still read the mesh itself ([../editing/README.md](../editing/README.md)).

Pages in this folder: [weather.md](weather.md) (L4D2's precipitation as Valve's maps use it, measured in game, the Weather Volume).

## Engine behaviours met while testing

| Behaviour | Where it mattered |
|---|---|
| One map-sized `func_precipitation` keeps rain out of brush and prop buildings by itself (Valve's Hard Rain has one per map and 3 to 6 blockers); the particle rain is culled by a ray-trace environment (`PRECIPITATIONBLOCKER`), the old rain types by `MASK_SOLID_BRUSHONLY` traces (2026-10-16) | [weather.md](weather.md) |
| Nav climb links are ordinary connections: the game has no zombies-only link type, so the Director's flow and the bots treat Auto Climb Links as walkable (a start area 35-88 units below its surroundings read "Start to end: OK" only through them; the game's own `nav_generate` reported the flow broken there) (2026-10-16) | nav/ |
| VScript can edit the nav at run time (area `ConnectTo` / `Disconnect`, the ladder forms, `MarkAsBlocked` / `UnblockArea`, `SetAttributes` / `RemoveAttributes`, `MarkAsDamaging`, `NavMesh.RegisterAvoidanceObstacle`) but cannot create, resize or delete areas (`core/data/vscript_api.json`) | nav/, logic/ |
| A script timer walking every `prop_dynamic` five times a second prints "SCRIPT PERF WARNING main ran long ~1.8 ms" (`RunScriptCode` calls show as "main"); about 1% of server time, harmless (2026-10-16) | logic/ (the Swelling Pod) |
| Damage to common infected from script works with any attacker (an entity, null, the player, the common itself) (2026-10-16) | logic/ |
| A map may have at most 32 switched (named) light styles; hlvbsp stops with "Too many switched lights ... max = 32" (`native/hlvbsp/write.c`); Valve's own c1-c5 maps use only named switchable lights (c2m5 23, c1m1 17, the others 2-4) and no baked flicker styles 1-12 | lighting/ |
| `script_help` prints nothing unless the game was started with `-dev +developer 1` (the `developer` cvar is hidden in retail) | logic/ |
| Modes come only from VPKs read at startup; loose `modes/*.txt` and `mp_gamemode` / a mode on the command line are ignored; `map <map> <mode>` works | logic/ |
| Collision cost is per object entered, not per ray: one static prop holding ~1,200-4,000 convex pieces drops a map to 14-17 fps with 20 commons, while the same pieces as ~60 props of <= 20 pieces or as brushes run ~100-117 fps (per-ray cost of a prop 5-8 microseconds against 0.1-2.6 for brushes); `vprof` prints nothing in retail L4D2 (2026-10-15) | models/collision.md |
| Decals (bullet holes) don't draw on nodraw brush faces nor on a model with `$no_draw 1`; an alpha-tested fully transparent model material is the workaround (its decal result untested) (2026-10-15) | models/collision.md |
| Entities run their scripts in BSP entity order as created; the VScript root table persists across map loads | logic/ |
| `func_timescale` works with `sv_cheats 0` (`Time()` rate 0.499 at 0.5, 0.319 at 0.3) | example 43 |
| Spawning a model not precached crashes with "UTIL_SetModel: not precached" (any `SetModel` too): precache with a hidden `prop_dynamic` per model | logic/ |
| `func_areaportal`, `func_areaportalwindow`, `trigger_finale` spawned mid-game crash or hang the game | NetProps harvest |
| `ScriptFileRead` reads at most 64 KB | the bench |
| `ems/` is the only folder scripts may read and write (`StringToFile`) | the bench, API dump |
| `infected_death` carries no infected id; `infected_hurt` does | logic/ |
| `ZSpawn` fails next to survivors; without a position a Tank is placed only while none is alive | logic/, director/ |
| Teleporting survivors may not count as leaving the safe area on stock maps; dropping all survivors into the void restarts the round | tests |
| `func_nav_blocker` blocks at spawn, ignores `StartDisabled` and the team; a vote restart doesn't re-block | director/ |
| `nav_generate` seeds from landmarks and item spawns only, and must run after survivors spawn; `nav_analyze` needs `nav_edit 1`; the game keeps `maps/<map>.nav01..10` backups | nav/ |
| The engine sets `mat_fullbright 1` for the session after loading an unlit map | loading/ |
| `-hijack` commands reach a running game in 0.1 s after the menu's `BinkOpen(`; a game started by `send_commands` has no `-condebug` | loading/ |
| A listen server waits ~6 s registering with Steam unless `sv_lan 1`; a game started before Steam is ready quits with "Steam is not running" | loading/ |
| The game rewrites the `.bsp` on map change (the stringtable dictionary) | loading/ |
| Build & Play's `sv_cheats 0` resets cheat cvars (`z_common_limit 0`, `nb_stop 1` persist until then and silently stop spawning) | director/ |
| `point_clientcommand` runs a console command as one player; `cl_drawhud 0` doesn't hide the scripted HUD | logic/ |
| L4D2 VScript: no `Entities.CreateByClassname`; `GetEntityIndex()` not `entindex()`; Squirrel 3 parse quirks | logic/ |
| L4D2 demos: protocol 4, network protocol 2100, 4 x 76-byte cmdinfo per packet, 6-bit numbits in SendTables | logic/ |
| L4D2 has no LDR lighting in its own maps; `vrad.exe` refuses `-scale`, `-ambient`, `-sky` | lighting/, compilers/ |
| `$parallaxmap` is listed by the shader DLL but ignored by the PC shaders | models/ |
| `snd_rebuildaudiocache` rewrites Steam's caches and quits; new sounds need an addon `sound.cache` | sound/ |
| `con_timestamp` doesn't exist in L4D2 | loading/ |
| Blender's `FloatProperty` and Squirrel floats are single precision (digits lost above 2^24) | logic/ |

## Limits met

| Limit | Value | Where |
|---|---|---|
| Nav generator nodes | 500,000 nav points; beyond it part of the map gets no nav, with a warning | nav/ |
| vrad patches | 262,144: a large user map (~397k bounce patches, 11.6k faces, 2.6 million square feet) hit hlvrad's fallback that smaller maps never did | lighting/, compilers/ |
| Light styles per face | 4 (Source); more than 4 are dropped silently (open audit item) | lighting/ |
| Switched (named) light styles per map | 32 (styles 32..63); hlvbsp errors past it (`write.c`, `SetLightStyles`) | lighting/ |
| Model bones | 128 (127 usable); 3 weights per vertex; 53 bones per hardware strip; 65,535 vertices per mesh; `.phy` triangles 4,096 per piece (12-bit index) | models/ |
| Brush sides | more than 64 corners crashes vbsp without a message (caught at export) | compilers/ |
| Map limits (brushes, sides, materials, areas, area portals, leaf faces, primitives...) | hlvbsp stops with vbsp's message | compilers/ |
| Brushes | 8,192 (`MAX_MAP_BRUSHES`; world, detail and brush-entity brushes alike); a house's collision is ~1,000-1,300 of them as brushes, none as props or Vis Only brushes | models/collision.md |
| Bench request | 64 KB (`ScriptFileRead`) | logic/ |
| Native nav memory | capped per current map with a ceiling (it once grew to ~1 GB after a huge build) | nav/ |

## The reference maps

- "A large user map": the same real map throughout the knowledge base: about 1191 clusters and 3466 portals, 2.6 million square feet in its bigger version, ~397k bounce patches, 11.6k faces; the best real-world lighting and performance test. Test copies are built under an `hl_test_*` name through `CompileJob` (never under the user's map name: a factory-startup test once overwrote the user's `maps/<name>.bsp` and `navmark` script, 2026-10-03, restored from the build folder).
- Valve's samples: `sdk_content` (134 maps, the compiler corpus), `styleguide_urban` (radial vis; the user's imported test map), `styleguide_swamp` (radial, fog), c1m1_hotel (the wanderer reference: safe-room start), c2m1_highway (flow-based logic and audio probes; no safe-room start), c3m1, c5m1 (big navs), c8m4 (elevators, for the planned `func_elevator` nav work).

## Testing rules (recorded because they cost data)

- Never save a user's `.blend` from a test Blender; never `--factory-startup` with `extension install-file --enable` (it saved factory preferences over the user's on 2026-10-06); close test Blender and game processes by PID only.
- Rebuild the add-on with `scripts/build_release_zip.py` (not `blender --command extension build`, which packs the gitignored `embree4.dll` / `tbb12.dll`: 14 MB instead of 1.4 MB).
- Python with backslash escapes passed through a shell heredoc gets mangled: write patch scripts to a file first.
- Python 3.12's `sum()` (Neumaier) differs from 3.11's: exactness tests run with Blender's Python.
- Quick in-game tests: QUICK preset, no lighting, ~30 s build + load; wait for `HAMMERLESS_FLOW` after "Map: <name>"; don't probe the instant a map loads.

Last checked: 2026-10-17 against branch brush-tools, commit 55c63a0 (the importer's modifiers note; the weather, climb-link, nav-API, script-perf, damage and switched-light rows 2026-10-16 against 18c2ce4; the collision-cost, decal and brush-limit rows 2026-10-15 against a2d00a3; the rest re-read 2026-10-13 against 46f75ac).
