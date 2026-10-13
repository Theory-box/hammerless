# Valve's compiler options and what Hammerless does with them

Every command-line option of L4D2's `vbsp.exe`, `vvis.exe` (+ `vvis_dll.dll`) and `vrad.exe` (+ `vrad_dll.dll`), what each does in Valve's tool, how many values follow it, and what Hammerless's own compiler does with it.

**Sources.** The "what it does" column was read from Valve's Source SDK 2013 / Alien Swarm source (`vbsp.cpp`, `vvis.cpp`, `vrad.cpp`, `lightmap.cpp`, `radial.cpp` and the files named per row): the variable each option sets and where that variable is used. The option list and value counts come from the same parsers plus the strings L4D2's own binaries know (collected 2026-10-13 into `hammerless/core/toolopts.py`). Options L4D2's binaries know but the SDK source doesn't contain are marked **L4D2-only**; their meaning is inferred from the name and marked *unverified*. Anything inferred rather than read is marked *unverified*.

**The Hammerless column** is the kind each option has in `hammerless/core/toolopts.py` (`VBSP`, `VVIS`, `VRAD` tables):

| Kind | Meaning |
|---|---|
| `ours` | Passed on to our compiler, which does it. |
| `no effect` | Left out with a build-log note "they make no difference to the map": threads, logging, debug dumps, paths, crash dumps, network compiling. |
| `not done` | Left out with a build-log note "Hammerless's compiler doesn't do them yet": it would change the map, ours can't do it. Never switches to Valve's tool. |

Wired into the build since 2026-10-13 (commit 1d2d8fd): `CompileOptions.extra(tool, ours)` gives our compiler only the `ours` options, `extra_notes()` puts the rest in the build log, and no extra option ever switches a stage to Valve's tool (before that commit any extra vbsp option, any unknown vvis option or any vrad option did; see [valve-fallbacks.md](valve-fallbacks.md), case d). With Valve's tool chosen in Settings, all typed options are passed to it unchanged.

Options are matched case-insensitively by every tool (`Q_stricmp`), except `-minluxelscale` in vbsp and `-dump` in vrad, which the SDK compares case-sensitively (`strcmp`); `toolopts.py` lower-cases everything.

## Options common to all three tools

| Option | Values | What it does in Valve's tool | Hammerless |
|---|---|---|---|
| `-threads N` | 1 | `numthreads = atoi`: worker thread count. vbsp: block tree built per thread (plane numbering is thread-order dependent: compare against vbsp with `-threads 1`). vvis and vrad: results vary with thread timing (see what-ours-do.md). | `no effect` on the map. hlvbsp ignores it; hlvvis and hlvrad take their own `-threads` from the build, not from the extra options. |
| `-v`, `-verbose` | 0 | `verbose = true`: more log lines. | `no effect` (hlvbsp and hlvvis accept it) |
| `-low` | 0 | `g_bLowPriority`: the process runs at idle priority (`SetLowPriority()`). | `no effect` |
| `-steam`, `-allowdebug` | 0 | Accepted and ignored ("nothing to do here, but don't bail on this option"). | `no effect` |
| `-vproject DIR`, `-game DIR` | 1 | The game directory for the file system (materials, models, `lights.rad`); the parser just skips the value, `CmdLib_InitFileSystem` reads it. | `no effect` as an *extra* option: the build always passes its own `-game`. |
| `-insert_search_path PATH` | 1 | Adds a search path (vbsp and vrad parsers; not vvis's). | `no effect` |
| `-FullMinidumps` | 0 | `EnableFullMinidumps(true)`: bigger crash dumps. | `no effect` |
| `-novconfig` | 0 | `CMDLINEOPTION_NOVCONFIG`: don't open the VConfig dialog when the game directory is wrong. | `no effect` |
| `-mpi`, `-mpi_listparams` and other `-mpi*` | 0 | VMPI network compiling (compiled in with `#ifdef MPI`); any argument starting with `-mpi` is accepted. | `no effect` |
| `-ds`, `-lv`, `-tempcontent`, `-xlsp` | 0 | **L4D2-only.** Not in the SDK parsers; common Source engine/file-system switches (*unverified*: `-lv` low violence, `-tempcontent` Steam temporary content, `-xlsp` Xbox Live server platform, `-ds` dedicated server). None changes a compiled map. | `no effect` |

## vbsp

Valve's parser: `vbsp.cpp`, `main()`. Unknown options print "VBSP: Unknown option" and the usage, then exit. hlvbsp (`native/hlvbsp/main.c`) accepts `-game`, `-threads` (ignored), `-v`/`-verbose`, `-onlyents`, and its own table options (`-materials`, `-surfaceprops`, `-props`, `-detail`, `-cubemaps`, `-fgd`); any other `-option` is an error.

### Switches that change the compiled map

| Option | Values | What it does in vbsp | Hammerless |
|---|---|---|---|
| `-onlyents` | 0 | `onlyents`: loads the existing .bsp, re-reads the .vmf's entities, `SetModelNumbers`, `SetLightStyles`, `EmitStaticProps`, `ComputeBoundsNoSkybox`, adds `water_lod_control` if water, fixes `func_occluder` numbers, writes the entity lump; adds `stale.txt` to the pak; detail props and lighting untouched. | `no effect` *as an extra option* (the smart build decides when to run entities-only: `hlvbsp -onlyents`, byte-identical, see what-ours-do.md) |
| `-onlyprops` | 0 | `onlyprops`: loads the .bsp and .vmf, re-emits static props and detail props only. "Blows away" detail prop lighting (the comment in the `-onlyents` path). | `not done` |
| `-nodetail` | 0 | `nodetail`: brushes with `CONTENTS_DETAIL` (and no displacement) are dropped from the map (`map.cpp`, `LoadMapFile`); detail faces aren't emitted (`vbsp.cpp`, `ProcessWorldModel`). | `not done` |
| `-fulldetail` | 0 | `fulldetail`: detail contents cleared from every brush, so all brushes are world brushes and cut visibility (`map.cpp`). | `not done` |
| `-nowater` | 0 | `nowater`: brushes with `MASK_WATER` contents are dropped (`map.cpp`). | `not done` |
| `-nocsg` | 0 | `nocsg`: brushes aren't chopped against each other (`ChopBrushes` skipped in `ProcessWorldModel`, `ProcessSubModel`, and `detail.cpp`). Overlapping faces stay. | `not done` |
| `-nomerge` | 0 | `nomerge`: chopped faces on the same node aren't merged back (`faces.cpp`, `MakeFaces`). | `not done` |
| `-nomergewater` | 0 | `nomergewater`: faces on water brushes aren't merged (`faces.cpp`, `TryMerge`). | `not done` |
| `-nosubdiv` | 0 | `nosubdiv`: faces aren't subdivided to the lightmap size limit (`faces.cpp`). Faces too big for a lightmap would fail in vrad. | `not done` |
| `-notjunc` | 0 | `notjunc`: t-junctions aren't fixed (`faces.cpp`, `FixTjuncs`): cracks between faces. | `not done` |
| `-noweld` | 0 | `noweld`: face vertices aren't welded (every face gets unique vertices, `faces.cpp`, `GetVertexnum`). | `not done` |
| `-noshare` | 0 | `noshare`: edges aren't shared between faces (`faces.cpp`, `GetEdge2`). | `not done` |
| `-noopt` | 0 | `noopt`: the second tree-building pass is skipped (`ProcessWorldModel`: normally the tree is built, visible sides marked, then built again splitting only on planes that make faces); the map keeps the "outer shell" the usage text describes. | `not done` |
| `-noprune` | 0 | `noprune`: neighbouring solid nodes aren't pruned (`PruneNodes` skipped). | `not done` |
| `-micro F` | 1 | `microvolume` (default 1.0): brush fragments with a volume below it are discarded during CSG (`brushbsp.cpp`, `BrushVolume` check). | `not done` |
| `-block X Y` | 2 | `block_xl = block_xh = X`, `block_yl = block_yh = Y`: compile only that 1024-unit block of the world (`BLOCKS_SIZE`); debug. | `not done` |
| `-blocks XL YL XH YH` | 4 | The block range to compile. | `not done` |
| `-bumpall` | 0 | `g_BumpAll`: every material gets `SURF_BUMPLIGHT` (bumped lightmaps, 4 pages) regardless of its shader (`textures.cpp`); turned off again if `-dxlevel` < 80. | `not done` |
| `-lightifmissing` | 0 | `g_bLightIfMissing`: a material whose shader doesn't ask for a lightmap (or is missing) is still lit instead of getting `SURF_NOLIGHT` (`textures.cpp`). | `not done` |
| `-forceskyvis` | 0 | `g_bSkyVis`: portals inside the 3D skybox area are written to the `.prt`, so vvis computes vis in the skybox (`prtfile.cpp`, normally skipped for `Is3DSkyboxArea`). | `not done` |
| `-nodrawtriggers` | 0 | `g_NodrawTriggers`: `tools/toolstrigger` surfaces become nodraw (`textures.cpp`). | `not done` |
| `-xbox` | 0 | Sets `g_NodrawTriggers` and `g_DisableWaterLighting` (water surfaces lose their lightmap unless the material keeps it). | `not done` |
| `-allowdetailcracks` | 0 | `g_bAllowDetailCracks`: the t-junction fix runs before leaf-face vertices are emitted instead of after, which leaves cracks on detail faces but is faster (`faces.cpp`, `FixTjuncs`). | `not done` |
| `-novirtualmesh` | 0 | `g_bNoVirtualMesh`: displacement physics are written as full collision meshes instead of the "virtual mesh" the engine builds at load (`ivp.cpp`). vbsp forces this itself when the map has power-4 displacements. | `not done` |
| `-snapaxial` | 0 | `g_snapAxialPlanes`: axis-aligned plane distances are snapped to integers when planes are created (`map.cpp`). | `not done` |
| `-replacematerials` | 0 | `g_ReplaceMaterials`: loads `<mapbase>_materials.txt` (`LoadMaterialReplacementKeys`) and replaces side materials by it (`map.cpp`). | `not done` |
| `-luxelscale F` | 1 | `g_luxelScale`: every side's lightmap scale (world units per luxel) multiplied by F (`map.cpp`, `ComputeLightmapGridVectors` region). `-dxlevel 70` sets it to 4 when it's 1. | `not done` |
| `-minluxelscale F` | 1 | `g_minLuxelScale` (clamped to >= 1): a lower limit on every side's lightmap scale. Case-sensitive in the SDK parser. | `not done` |
| `-defaultluxelsize F` | 1 | **`#if 0` in the SDK source** (would set `g_defaultLuxelSize`, the scale used by sides that have none; default 16). L4D2's binary knows the string; whether it works is *unverified*. | `not done` |
| `-maxlightmapdim F` | 1 | **`#if 0` in the SDK source** (would set `g_maxLightmapDimension`, the face subdivision limit, 32 luxels in `faces.cpp`). L4D2's binary knows the string; *unverified*. | `not done` |
| `-dxlevel N` | 1 | `g_nDXLevel`: picks materials' DX-level fallbacks (`map.cpp`); < 80 disables `-bumpall`; 70 sets `-luxelscale 4`; < 70 skips the cubemap material fix-ups (`vbsp.cpp`). | `not done` |
| `-writewatervolumes` | 0 | **L4D2-only**, *unverified*: by its name, writes the water volumes (fog volumes) out as files. | `not done` (listed as changing the map in `toolopts.py`; if it only writes files it belongs under no effect: *unverified*) |
| `-mopp`, `-polysoup`, `-polytope`, `-localphysx` | 0 | **L4D2-only**, *unverified*: physics-collision build modes by their names (MOPP collision trees, polygon soup, polytope, local PhysX). L4D2's vbsp prints "Cannot build Physics2 data" on every map, which suggests a second physics path these options belong to. | `not done` |

### Switches that don't change the map

| Option | Values | What it does in vbsp | Hammerless |
|---|---|---|---|
| `-verboseentities` | 0 | With `-v`, keeps verbose output on for brush entities (otherwise `verbose` is turned off for submodels, `vbsp.cpp`). | `no effect` |
| `-glview` | 0 | `glview`: writes `.gl` files of the tree for the glview debugging tool. | `no effect` |
| `-tmpout` | 0 | `outbase = "/tmp"`: legacy output prefix. | `no effect` |
| `-leaktest` | 0 | `leaktest`: stops the compile when the map leaks (the leak file is written either way). Hammerless already fails the build on a `.lin` file. | `no effect` |
| `-dumpcollide` | 0 | `dumpcollide`: writes `collideXXX.txt` files of each collision model (`ivp.cpp`). | `no effect` |
| `-dumpstaticprop` | 0 | `g_DumpStaticProps`: writes `staticpropXXX.txt` (`staticprop.cpp`). | `no effect` |
| `-keepstalezip` | 0 | `g_bKeepStaleZip`: in a full compile, the previous .bsp's pakfile is loaded first and `stale.txt` added. | `no effect` (the pakfile is rebuilt either way; Hammerless strips `stale.txt`) |
| `-embed DIR` | 1 | `g_szEmbedDir`: the folder is added to the search path and packed into the pakfile (`AddDirToPak`). Not allowed with `-onlyents`/`-onlyprops`. | `no effect` (*strictly it changes the pakfile*; Hammerless packs its own files another way) |
| `-physlog`, `-physstats`, `-exportmoppobj` | 0 | **L4D2-only**, *unverified*: physics logging / statistics / export of the MOPP as an .obj, by their names. | `no effect` |

## vvis

Valve's parser: `vvis.cpp`, `ParseCommandLine()`. hlvvis (`native/hlvvis.c`) accepts `-fast`, `-exact` (its own), `-threads N`, `-radius_override R`, `-game DIR`, `-v`/`-verbose`; the build only lets `-fast`, `-threads`, `-radius_override` and `-exact` through today (`compile.HLVVIS_OPTIONS`).

| Option | Values | What it does in vvis | Hammerless |
|---|---|---|---|
| `-fast` | 0 | `fastvis`: `CalcPortalVis` sets every portal's `portalvis = portalflood` (the rough pass's bound) and marks it done; the full flow is skipped. Measured 164% to 186% looser than full vis on c1m1_hotel / c5m1_waterfront (2026-10-11). | `ours` (byte-identical, including radial maps) |
| `-radius_override R` | 1 | `g_bUseRadius = true; g_VisRadius = R*R` (prints "Vis Radius = %4.2f"); used instead of the `env_fog_controller` far Z: in `BasePortalVis` (`flow.cpp`) a portal farther than the radius is dropped from the might-see set, and every leaf gets the radial flag. | `ours` (decoded from `vvis_dll.dll`, byte-identical; see what-ours-do.md) |
| `-nosort` | 0 | `nosort`: portals aren't sorted by might-see count before the flow (`SortPortals` skipped). The sort is an optimisation, but with threads the order decides which neighbours have finished when a portal uses their result, so borderline pairs can come out differently (the same variation vvis shows run to run). | `no effect` (the map's intended visibility is the same) |
| `-trace A B` | 2 | `g_TraceClusterStart/Stop`: instead of computing vis, traces the portal chain from cluster A to B and writes it to a trace file (`CalcVisTrace`, `WritePortalTrace`); **the .bsp is not written**. Debug only. | `no effect` (left out; running it in a build would leave the map without vis) |
| `-tmpin` | 0 | `inbase = "/tmp"`: legacy input prefix. | `no effect` |
| `-mpi_listparams` | 0 | Lists VMPI parameters. | `no effect` |
| (common options) | | `-threads`, `-v`, `-low`, `-FullMinidumps`, `-novconfig`, `-vproject`/`-game`, `-allowdebug`/`-steam`, `-mpi`. | `no effect` |

hlvvis's own option, not a vvis one: `-exact` runs vvis's path search (its output byte for byte) instead of the default merged-window search.

## vrad

Valve's parser: `vrad.cpp`, `ParseCommandLine()`. The `-scale`, `-ambient`, `-dlight`, `-sky`, `-notexscale` and `-coring` options are inside `#if ALLOWDEBUGOPTIONS` in the SDK source. **L4D2's vrad was built without them**: given `-scale`, `-ambient` or `-sky` it prints its usage and exits 1 with the map untouched (measured 2026-10-13); the strings are in its usage text, which is why they appear in `toolopts.py`. vrad defaults to LDR (`SetHDRMode(false)`); Hammerless always passes `-hdr`.

hlvrad (`native/hlvrad/main.c`) accepts the vrad options marked `ours` below plus its own (listed after the table). Any other `-option` it is given is accepted and ignored ("accepted (not all done yet)"). Since commit 41dc861 (2026-10-13) it also does `-softsun`, `-textureshadows`, `-scale`, `-ambient` and accepts `-sky`; `tests/compile/vrad_options_suite.py` (`--gpu` for the GPU path) checks them against vrad.

### Lighting quality and method

| Option | Values | What it does in vrad | Hammerless |
|---|---|---|---|
| `-bounce N` | 1 | `numbounce` (default 100): radiosity bounces; 0 is direct light only. The bounce loop also stops early when a pass adds under 1.0 in every channel (`vrad.cpp`, `BounceLight`). Negative is an error. | `ours` |
| `-noextra` | 0 | `do_extra = false`: no supersampling (luxels where light changes sharply aren't re-lit at 4x4 points). With `-noextra` vrad is deterministic (used for byte comparisons). | `ours` |
| `-final` | 0 | `g_flSkySampleScale = 16`: 16x the sky-light and sun-spread rays per luxel (and for static prop indirect light). Same as `-extrasky 16`. | `ours` |
| `-extrasky F` | 1 | `g_flSkySampleScale = F`: multiplies the 162 sky directions (`NUMVERTEXNORMALS`) per sample (`lightmap.cpp`, `GatherSampleSkyLight` region; also detail props and static props). | `ours` |
| `-fast` | 0 | `do_fast`: a separate rough path. `BuildSamplesAndLuxels_DoFast` (one sample per luxel at its centre, no winding areas); sun-area rays divided by 4 (`NSAMPLES_SUN_AREA_LIGHT / 4`); sky rays `162 / 4` instead of `162 * scale`; **no patch subdivision** (`vrad.cpp`, `MakePatches` skips `SubdividePatch`); **no radial filter** (`radial.cpp`: the luxel takes its sample directly instead of `BuildLuxelRadial` + `SampleRadial`); **no static prop lighting** even with `-StaticPropLighting` (`vrad.cpp`, `VRAD_ComputeOtherLighting`); detail props' sky rays / 4. Marked "Quick and dirty lighting" in the usage. | `not done` (left out with a note; commit 1d2d8fd). Decision 2026-10-13: vrad's `-fast` won't be ported (see valve-fallbacks.md, case c); the build's Fast quality gives ours `-noextra -bounce 2` instead. hlvrad still parses `-fast` (`g_bFast`: 7 sun-spread rays, sky rays 162/4, no prop lighting in `direct.c`, `gpu.c`, `spropvlight.c`), but the build never passes it. |
| `-smooth DEG` | 1 | `smoothing_threshold = cos(DEG)` (default 45 degrees): faces meeting at a smaller angle share smoothed vertex normals (`lightmap.cpp`, `PairEdges`). | `ours` (`main.c` does the same cosine) |
| `-chop F` | 1 | `minchop` (default 4, min 1, clamped to `maxchop`): the finest patch size in luxels, used at face edges (`vrad.cpp`, `SubdividePatch`). | `ours` |
| `-maxchop F` | 1 | `maxchop` (default 4, min 1): the coarsest patch size, used in face interiors. | `ours` |
| `-dispchop F` | 1 | `dispchop` (default 8, min 1): patch size in luxels on displacements (`vrad_dispcoll.cpp`). | `ours` |
| `-disppatchradius F` | 1 | `g_MaxDispPatchRadius` (default 1500, min 10): the largest radius a displacement patch's sample gather may have (`vrad_dispcoll.cpp`). | `not done` |
| `-lights FILE` | 1 | `designer_lights`: an extra lights file read after `lights.rad` and `<map>.rad` (texlights, `forcetextureshadow`, `noshadow`). | `ours` (`LoadTexLights(gamedir, path, designer_lights)`) |
| `-StaticPropLighting` | 0 | `g_bStaticPropLighting`: bakes per-vertex light for static props (`.vhv` files in the pak) and sets the level flag (`LVLFLAGS_BAKED_STATIC_PROP_LIGHTING_HDR`, map flags lump 59 bit 2). Skipped under `-fast`. | `ours` (all 80 `.vhv` of a large user map identical, 2026-10-08) |
| `-StaticPropPolys` | 0 | `g_bStaticPropPolys`: props block light with their LOD0 triangles instead of their collision model (`vradstaticprops.cpp`). | `ours` |
| `-nossprops` | 0 | `g_bDisablePropSelfShadowing`: a prop never shadows its own vertices (`vradstaticprops.cpp`, `skip_prop`). | `not done` |
| `-textureshadows` | 0 | `g_bTextureShadows`: rays through alpha-tested surfaces (and models listed with `forcetextureshadow` in `lights.rad`, 158 of them in L4D2's) sample the texture's alpha to decide if they're blocked (`trace.cpp` coverage callback). | `ours` (commit 41dc861, `native/hlvrad/texshadows.c`, written from `vrad_dll.dll`'s code). Props drawn as triangles (`-StaticPropPolys` only) whose model has the texture-shadow flag or is named by a `forcetextureshadow` line, and grate brushes (`%compilepassbullets` / `CONTENTS_GRATE`: their alpha-textured sides become partly see-through; without the option grates cast no shadow at all). A see-through triangle adds the texture's alpha at the hit (L4D2's bilinear filter) to the ray's coverage and never stops it; light visibility uses it, the bounce treats them as solid, as vrad. hlvrad reads the materials and textures (DXT1/3/5, 8-bit) from the game's VPKs itself. Checked against vrad's own `TestLine` on 4.3 million ray packets: no differences. Without `-StaticPropPolys` it changes nothing. Exact mode byte-identical to vrad on a foliage map and a fence/grate map; GPU path close, not exact (see what-ours-do.md). |
| `-softsun DEG` | 1 | `g_SunAngularExtent = sin(DEG)`: the sun becomes an area light of that angular size, giving soft shadows (30 rays over the disc, 7 with `-fast`). The same value comes from a `light_environment`'s `SunSpreadAngle` key, which overrides the option (`lightmap.cpp`). | `ours` (commit 41dc861). A `light_environment`'s `SunSpreadAngle` still overrides it, as in vrad. Byte-identical to vrad (Exact, `-noextra`) on the sun, sunspread and lights test maps at 0.5, 5, 13.7 and 45 degrees; with supersampling 0.21% of luxels differ at 5 degrees against 0.24% without the option (supersampling's usual difference). |
| `-fastambient` | 0 | `g_bFastAmbient`: leaf ambient cubes use one sample per leaf instead of up to 128 (`leaf_ambient_lighting.cpp`). | `not done` |
| `-centersamples` | 0 | `do_centersamples`: `sampleOffset = 0.5` instead of 1.0 where the sample points of a face are laid out (`lightmap.cpp`), moving them to luxel centres. | `not done` |
| `-luxeldensity F` | 1 | `luxeldensity` (F > 1 is inverted to 1/F): every texinfo's lightmap vectors are rescaled so no face is denser than F (`vrad.cpp`, `RadWorld_Start`; the SDK comment notes it writes to the .bsp permanently). | `not done` |
| `-largedispsampleradius` | 0 | `g_bLargeDispSampleRadius`: a wider gather radius for bounced light on displacements (the usage text: for "splotches of bounced light on terrain"). | `not done` |
| `-maxdispsamplesize F` | 1 | `g_flMaxDispSampleSize` (default 512): caps a displacement patch's sample radius (`vrad_dispcoll.cpp`). | `not done` |
| `-noskyboxrecurse` | 0 | `g_bNoSkyRecurse`: sky rays that reach the 3D skybox don't recurse into it, so skybox geometry can't shadow the world (`trace.cpp`). | `not done` |
| `-nodetaillight` | 0 | `g_bNoDetailLighting`: detail props aren't lit. | `not done` |
| `-onlydetail` | 0 | `onlydetail`: `RadWorld_Go` is skipped; only detail props and leaf ambient are computed on the existing lightmaps. | `not done` |
| `-onlystaticprops` | 0 | `g_bOnlyStaticProps`: `RadWorld_Go` skipped; only static prop lighting. | `not done` |
| `-staticpropnormals` | 0 | `g_bShowStaticPropNormals`: prop vertex colours show their normals (debug). | `not done` (it changes the `.vhv` output) |
| `-dlightmap` | 0 | `dlight_map = 1`: direct light goes into a different lightmap style than the bounced light (`lightmap.cpp`, `FinalLightFace`). | `not done` |
| `-scale F` | 1 | `lightscale` (default 1): every light entity's and texlight's intensity multiplied (`lightmap.cpp`, `ParseLightGeneric`, `MakePatchForFace`). `ALLOWDEBUGOPTIONS`; **L4D2's vrad refuses it**. | `ours` (commit 41dc861), built per Valve's source since L4D2's vrad can't run it. Checked indirectly: against vrad on a copy of the map with every light's brightness multiplied by s, every lump but the entity lump matches for s = 2 and 0.5. Light-emitting textures (`lights.rad`) get the scale applied twice, as Valve's source does. |
| `-ambient R G B` | 3 | `ambient[] = value * 128` per channel: added to every style-0 lightmap sample (and its bump pages) after the bounce (`lightmap.cpp`, `FinalLightFace`). vrad also sets `ambient = 0.1` itself when the map has no vis data (`RadWorld_Start`: "No vis information, direct lighting only", `numbounce = 0`). `ALLOWDEBUGOPTIONS`; **L4D2's vrad refuses it**. | `ours` (commit 41dc861), built per Valve's source: added to each sample's own light, not bounced. hlvrad now also adds vrad's 0.1 ambient on a map without vis (plus vrad's placeholder LDR leaf ambient), which made no-vis maps byte-identical to vrad and is the indirect check of this code path. |
| `-sky F` | 1 | `indirect_sun`: **parsed but never used** in the SDK source (only its definition and the parser mention it). L4D2's vrad refuses it. `ALLOWDEBUGOPTIONS`. | `no effect` (commit 41dc861): accepted by hlvrad, does nothing, as in Valve's source. |
| `-dlight F` | 1 | `dlight_threshold` (default 0.1): a texlight patch whose base light averages at least this much is also emitted as a direct light (`lightmap.cpp`, `CreateDirectLights`). `ALLOWDEBUGOPTIONS`. | `not done` |
| `-notexscale` | 0 | `texscale = false`: texlight intensity isn't scaled by the face's texture scale (`vrad.cpp`, `MakePatchForFace`). `ALLOWDEBUGOPTIONS`. | `not done` |
| `-coring F` | 1 | `coring` (default 1, "light threshold to force to blackness"): **parsed but never used** in the SDK source. *Unverified* in L4D2. `ALLOWDEBUGOPTIONS`. | `not done` |
| `-finitefalloff` | 0 | **L4D2-only**, *unverified*. L4D2's `vrad_dll.dll` has a global-flag mode in `SetLightFalloffParams` that sets constant 1, linear 0, quadratic `-1/d0^2` (decoded 2026-10-08); this option is the likely switch for it, not confirmed. | `not done` |
| `-staticproplighting3` | 0 | **L4D2-only**, *unverified*: by its name, a variant of static prop vertex lighting (three-direction / bumped, as later Source branches have). | `not done` |

### HDR / LDR

| Option | Values | What it does in vrad | Hammerless |
|---|---|---|---|
| `-hdr` | 0 | `SetHDRMode(true)`: writes the HDR lumps: lighting 53, world lights 54, leaf ambient 51/55, faces HDR 58, vertex normals 30/31, and the detail prop lighting in the game lump. Reads `_lightHDR`, `_ambientHDR`, `_lightscaleHDR` keys. | always passed by the build |
| `-ldr` | 0 | `SetHDRMode(false)` (vrad's default): the LDR lumps (8, 15, 52/56, 7). | `no effect`: L4D2 only uses HDR lighting; Valve's own maps have no LDR lighting. **Removed** from the Lighting settings 2026-10-13 (commit dbf5f0f); hlvrad still parses `-ldr`. |
| `-both` | 0 | **L4D2-only** (the SDK vrad is run twice by Hammer for both): LDR and HDR in one run, *unverified*. | `no effect`, same reason |

### Debug and no-effect options

| Option | Values | What it does in vrad | Hammerless |
|---|---|---|---|
| `-dump` | 0 | `g_bDumpPatches`: writes `samples_*`, `bounce0_*`, `face.txt` debug files (into the game folder); keeps sample windings in memory. Case-sensitive in the SDK. | `no effect` |
| `-dumpnormals` | 0 | `bDumpNormals`: writes normals to debug files. | `no effect` |
| `-dumptrace` | 0 | `g_bDumpRtEnv`: writes the ray-tracing environment (`trace.txt`). | `no effect` |
| `-dumppropmaps` | 0 | `g_bDumpPropLightmaps`: writes static prop lightmap debug images. | `no effect` |
| `-debugextra` | 0 | `debug_extra`: "places debugging data in lightmaps to visualize supersampling". *Strictly this changes the lightmap bytes* (debug colouring), but only as a visualisation. | `no effect` |
| `-rederrors` | 0 | `bRed2Black = false`: luxels no sample reached are written red (255 0 0 in `radial.cpp`) instead of black. L4D2 measured: weight-0 luxels are black; red only for out-of-range u/v. *Strictly it changes those luxels.* | `no effect` |
| `-loghash` | 0 | `g_bLogHashData`: logs the sample hash table to `samplehash.txt`. | `no effect` |
| `-stoponexit` | 0 | `g_bStopOnExit`: waits for a key press when done. | `no effect` |
| `-mpi_listparams` | 0 | Lists VMPI parameters. | `no effect` |
| (common options) | | `-threads`, `-v`, `-low`, `-FullMinidumps`, `-novconfig`, `-vproject`/`-game`/`-insert_search_path`, `-allowdebug`/`-steam`, `-mpi`. | `no effect` |

### hlvrad's own options (not vrad's)

These are never typed as extra options; the build adds them from the Lighting settings (`CompileOptions.hlvrad_args()` and `CompileJob.__init__`).

| Option | Values | What it does |
|---|---|---|
| `-gpu` | 0 | Ray tracing on the graphics card (Vulkan ray queries); the default unless Exact Lighting is on. Falls back to the CPU with a warning when no capable card. |
| `-embree` | 0 | Embree's tracer. Shelved; never shipped (no DLLs in the release). `compile.py` no longer passes it (commit e1e0d1d). |
| `-gi N`, `-girays N` | 1 each | Bounced light by final gathering on the GPU: N passes (at most 8, `GI_MAX_PASSES`), rays per luxel (default 1024). Needs `-gpu`. |
| `-sspoints N`, `-sspasses N`, `-ssthreshold F` | 1 each | Supersampling: points across a luxel (1 to 16; vrad's 4), passes (vrad's 4), edge threshold (vrad's 0.0625). |
| `-fixquirks` | 0 | Leaves out four vrad behaviours (stale supersampling mask, lightmap-rectangle bounce hits, far displacements hiding faces, prop bounce without falloff). Not vrad-identical by design. |
| `-extra` | 0 | Supersampling on (the default; the opposite of `-noextra`). |
| `-skymap FILE` | 1 | Sky light from a prepared panorama (`<map>.hlsky`, made by `core/skylight.py`). |
| `-nobake FILE` | 1 | Control Volumes: where not to bake (or only to bake). |
| `-keeplight FILE.bsp` | 1 | The previous build's lighting for what isn't baked now. |
| `-layers FILE keys` | 2 | Live Light Mixing: write each tagged light's own contribution to `<map>.hllayers`. Needs `-gpu`; 256 layers at most. |
| `-remix FILE` | 1 | Re-mix the lightmaps from the layers at new light values without baking. |
| `-onlylights FILE` | 1 | Bake with some lights only (the layer-sum test). |
| `-modeldir DIR` | 1 | Where the build copied the map's prop models (`.mdl`/`.phy`/`.vvd`) out of the game. |
| `-visfrom FILE.bsp`, `-visthreads N` | 1 each | Lighting alongside vis: wait for that map's vis (`.visdone` / `.visfailed` markers) only for what needs it; use N threads until then. |
| `-timing` | 0 | Print each stage's time. |

## Contradictions and loose ends found (2026-10-13)

- (Resolved 2026-10-13, commit 41dc861: `-sky` is `no effect`; L4D2's vrad refuses `-scale`, `-ambient`, `-sky` outright, so those are built per Valve's source and checked indirectly.) `-coring` is still `not done` and still unused in the SDK source.
- (Resolved 2026-10-13, commit 1d2d8fd: `-fast` in `toolopts.VRAD` is now `not done`, matching the decision not to port it.)
- hlvrad has debug switches `TLTEST` / `TLLOG` (environment) for comparing its shadow tests with the vrad hook programs.
- `-writewatervolumes`, `-embed`, `-debugextra` and `-rederrors` are classified by the effect that matters for a Hammerless build, not by the strict rule "changes bytes in the output"; the notes in their rows say which.
- `toolopts.py` lists `-novconfig` for all three tools; the SDK source spells it through the `CMDLINEOPTION_NOVCONFIG` macro, so a grep for the literal string finds nothing. It is a real option.

Last checked: 2026-10-13 against branch light-layers (commit 41dc861) and Valve's Source SDK 2013 / Alien Swarm source.
