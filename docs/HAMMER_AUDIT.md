# Hammer Feature Audit (L4D2 Authoring Tools)

Every Hammer feature, and how the addon represents it in Blender.

**Priority:** P1 = needed for a playable map (MVP) · P2 = needed for a real campaign map · P3 = nice to have / power user
**Blender mapping:** *Native* = Blender already does this, we just read it · *Panel* = our UI · *Op* = our operator · *Export* = handled at export time

Items marked ⚠ need verification against the L4D2 build of Hammer.

---

## 1. Tools (left toolbar)

| Hammer tool | What it does | Blender mapping | Pri |
|---|---|---|---|
| Selection | Select / move / rotate / scale objects | Native (object mode) | P1 |
| Magnify / Camera | Navigate 2D/3D views | Native (viewport, fly/walk nav) | P1 |
| Entity tool | Place point entities | Panel: "Add L4D2 Entity" menu, spawns an Empty (or preview model) tagged with a classname | P1 |
| Block tool | Create brush primitives (block, wedge, cylinder, spike, sphere, arch) | Op: "Add Brush" menu; creates convex meshes on grid, tagged as brush | P1 |
| Texture application (Face Edit Sheet) | Per-face material, scale, shift, rotation, lightmap scale, justify, align to world/face | Panel on faces in Edit Mode; stored as face attributes; UVs derived from these on export (see §4) | P1 |
| Apply decal | Place `infodecal` on a surface | Op: click-to-place decal empty, raycast to surface | P2 |
| Apply overlay | Place `info_overlay` spanning faces | Op: projected decal plane; export resolves which brush faces it touches (sides list) | P2 |
| Clipping tool | Slice brushes with a plane (keep front/back/both) | Op: bisect that keeps result convex (Blender's Bisect + fill) | P2 |
| Vertex manipulation | Move brush vertices/edges | Native (Edit Mode), plus a **live convexity validator** that highlights invalid brushes | P1 |
| Path tool | Place `path_track`-style chains | Op: Blender curve → chain of path entities with auto-linked targets | P3 |
| Cordon tool | Compile only a region | Panel: cordon box object; export clips to it | P3 |

## 2. Brushes & world geometry

| Feature | Blender mapping | Pri |
|---|---|---|
| World brushes (convex solids) | Mesh object tagged **Brush**; must be convex, closed, planar faces. Validator + auto-fix (snap to grid, triangulate non-planar faces) | P1 |
| Non-convex input | Option A: error with highlight. Option B: auto convex-decompose (P3). Option C: auto-convert to prop | P2 |
| Grid snapping (1–512 units) | Panel: grid size setting that drives Blender's snap increment + unit scale | P1 |
| Carve | Op: boolean-subtract brush from brushes, output convex pieces (warn: Hammer's carve is famously messy, ours can be cleaner) | P3 |
| Make Hollow | Op: turn brush into walls of given thickness (6 brushes for a box) | P2 |
| Brush entities (func_detail, func_door, trigger_*, func_brush, func_breakable, etc.) | Tag mesh **Brush Entity** + classname; multiple meshes can share one entity via a Blender collection | P1 (func_detail, triggers) / P2 (rest) |
| Tie to entity / Move to world | Op: toggle brush between world and an entity | P2 |
| Group / Ungroup | Native (collections) | P1 |
| Tool textures (nodraw, clip, playerclip, npcclip, hint, skip, areaportal, trigger, skybox, blocklight, invisible) | Built-in material palette with viewport colours matching Hammer | P1 |
| Transform / Flip / Align / Snap selected to grid | Native + Op "Snap to Hammer grid" | P1 |
| Replace textures | Op: bulk material swap | P3 |

## 3. Displacements (terrain)

| Feature | Blender mapping | Pri |
|---|---|---|
| Create displacement (power 2/3/4) on a brush face | Mesh tagged **Terrain**. Exporter samples it as a heightfield onto a grid of displacement patches (power chosen per object) sitting on nodraw base brushes | P2 |
| Paint geometry (raise/lower/smooth) | Native (sculpt mode on the terrain mesh) | P2 |
| Paint alpha (blend two textures) | Native vertex paint → per-vertex alpha on export; material must be `WorldVertexTransition` | P2 |
| Sew | Export: automatically keep shared patch edges identical (we generate the patches, so seams match by construction) | P2 |
| Subdivide / Noise / Smooth | Native (modifiers, sculpt brushes) | P2 |
| Displacement flags (no phys/hull/ray collision) | Panel per terrain object | P3 |
| Overhangs / caves | Not representable as displacements → split out to props, with warning | P3 |

## 4. Materials & texturing

| Feature | Blender mapping | Pri |
|---|---|---|
| Texture browser (game materials) | Panel: browse materials from the game's VPKs, create Blender materials that preview the real VTF | P1 |
| Custom textures | Blender image-texture materials → converted to VTF + generated VMT (LightmappedGeneric / VertexLitGeneric / WorldVertexTransition) | P2 |
| Texture scale / shift / rotation | Face attributes; OR derive Hammer's U/V axes from Blender UVs where possible (UV→texture axis fitting). Default: world-aligned like Hammer | P1 |
| Lightmap scale | Face attribute (default 16) | P2 |
| Smoothing groups | Derived from Blender sharp edges / auto smooth | P3 |
| Material proxies, $surfaceprop, $basetexture2 etc. | Panel: VMT parameter editor on the material | P3 |

## 5. Entities

| Feature | Blender mapping | Pri |
|---|---|---|
| Entity properties (keyvalues) | **Generated automatically from the L4D2 FGD**: parse the FGD, build a property panel for each classname (types: string, int, float, choices, color, studio model, sound, target, flags). This one system gives parity for hundreds of entities | P1 |
| Spawnflags | Checkbox list from FGD | P1 |
| Inputs / Outputs (I/O) | Panel: outputs list per entity (output, target, input, parameter, delay, fire once). Target field autocompletes from named entities. Optional: draw I/O links as lines in the viewport | P2 |
| Entity report | Panel: searchable list of all entities | P3 |
| Point entities with models (prop_static, prop_dynamic, prop_physics) | Empty + model preview, or a linked mesh that *is* the prop (see §6) | P1 |
| Helpers (angles, sphere radius, light cone previews) | Viewport overlays (gizmos) | P3 |
| Sound browser | Panel: browse game sounds/soundscripts | P3 |
| Model browser | Panel: browse game models, spawn preview | P2 |

### L4D2-specific entities to have presets for (P1–P2)
`info_player_start`, `info_survivor_position`, `info_director`, `info_changelevel`, `info_landmark`, `prop_door_rotating_checkpoint`, `info_item_position`, `weapon_*_spawn`, `weapon_item_spawn`, `info_zombie_spawn`, `env_player_blocker`, `func_nav_blocker`, `trigger_finale`, `info_game_event_proxy`, `logic_director_query`, `env_physics_blocker`, `func_playerinfected_clip`, `info_remarkable`, `ambient_generic`, `env_soundscape`. Safe room entity bundle presets ("Start safe room", "End safe room") that drop a fully wired set.

## 6. Props & models

| Feature | Blender mapping | Pri |
|---|---|---|
| Game models (from VPKs) | Model browser → Empty with preview mesh (imported via MDL reader) | P2 |
| Custom models | Mesh tagged **Prop** (static/dynamic/physics). Exporter writes SMD/DMX + QC (collision model from a convex hull or a user-provided `_phys` mesh), compiles with studiomdl, places `prop_static` | P2 |
| Instancing | Blender linked duplicates → one compiled model, many placements | P2 |

## 7. Lighting

| Feature | Blender mapping | Pri |
|---|---|---|
| light / light_spot / light_environment | Blender point/spot/sun lights → entities (colour, brightness, cone angles, falloff) | P1 |
| Texture lights (lights.rad) | Emissive materials → generated `.rad` file | P3 |
| env_cubemap | Op: auto-place cubemaps (e.g. one per room / grid) + manual empties; run `buildcubemaps` after compile | P2 |
| Fog, tonemap, color correction | Presets on a "Map Settings" panel | P3 |
| Skybox (sky name, 3D skybox via sky_camera) | Map Settings panel; auto-sealing sky shell | P1 (2D sky) / P3 (3D sky) |

## 8. Map-level settings & organisation

| Feature | Blender mapping | Pri |
|---|---|---|
| Map properties (worldspawn keyvalues: skyname, detail material, etc.) | Scene-level "Map Settings" panel | P1 |
| VisGroups | Native (collections + visibility) | P1 |
| Instances (`func_instance`) | Confirmed supported in L4D2 (vbsp collapses them natively, instance I/O via `func_instance_io_proxy`). Blender collection instances → `func_instance` pointing at a separately exported VMF, or collapse them ourselves with `srctools.instancing` | P3 |
| Manifests | Skip. vbsp handles `.vmm` poorly anyway, and Blender collections cover the teamwork use case | — |
| Prefabs | Native (asset library / appended collections) | P2 |
| Check for problems (alt+P) | Op: our own validator, plus Hammer-equivalent checks (see §10) | P1 |

## 9. Compile & run (the "Run Map" dialog)

| Feature | Blender mapping | Pri |
|---|---|---|
| vbsp / vvis / vrad with options (fast vis, -final, -hdr/-ldr, -StaticPropLighting…) | Panel: compile presets (Fast / Normal / Final) + advanced switches | P1 |
| Launch game at map | Op: launches `left4dead2.exe -game left4dead2 -dev +map <name>`; option to auto-generate nav | P1 |
| Load pointfile (leak trace) | Import `.lin` into Blender as a red line, and frame the camera on it | P1 |
| Load portal file | Import `.prt` for vis debugging | P3 |
| Pack custom content (bspzip) / VPK addon | Export option: pack into BSP, or produce an addon VPK + `addoninfo.txt` | P2 |
| Nav mesh | Op: launch game with auto nav_generate + save; nav attribute editing stays in-game | P2 |

## 10. Validation checks (our "Check for problems")

- Brush not convex / not closed / non-planar face / too small / off-grid
- Map leak (pre-check: is everything inside a sealed hull?) plus `.lin` after compile
- Entity outside world, missing required keyvalue, I/O target doesn't exist
- Missing materials / textures, textures not power-of-two
- Prop has no collision model, too many verts
- Required L4D2 entities missing (info_director, survivor spawn, safe room doors, landmark pairs)
- Map extents beyond ±16384 units, brush/entity count near engine limits
