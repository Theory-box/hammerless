# Models: the model writer, animated models, the game's skeletons, textures

What Hammerless knows about L4D2's model and texture files: the own writer for static, dynamic and physics props and how it was matched to studiomdl, the SMD/QC path for animated models and what studiomdl was measured to do, the game's own `.mdl`/`.ani` format as decoded (frame-anims), game skeletons and placed characters proven in game, VTF/VMT writing, alpha, material resolution and the game's content. User-facing: `docs/systems/MODELS_AND_TEXTURES.md`, `docs/ANIMATED_MODELS.md`; research: `docs/dev/research/ANIMATED_MODELS_COMPILE.md` (SMD, QC, studiomdl measurements), `docs/dev/research/L4D2_GAME_MODELS.md` (what the game's models contain) with its readers in `docs/dev/research/model_tools/` (`mdlparse.py`, `survey.py` -> `survey.txt`: 160 models).

Code: `hammerless/core/models.py` (Custom Models, fingerprints), `mdlwrite.py` (`.mdl`/`.vvd`/`.vtx`), `phywrite.py` (`.phy`), `skeletal.py` (SMD/QC), `gamemodel.py` (reads `.mdl`/`.ani`), `gamemesh.py` (reads `.vvd`/`.vtx`), `textures.py` (VTF/VMT), `vtf_read.py`, `vpk.py`, `content.py` (`GameContent`), `hammerless/blender/extract.py`, `texturing.py`, `preview.py`, `gameskeleton.py`, `placedchar.py`, `examplemodels.py`. Tests: `tests/ingame/test_models.py` (the reference run for the writer), unit tests for the skeletal writer and the pod export (8237803).

Timeline (2026): Custom Models through studiomdl, then the own `.mdl`/`.vvd`/`.vtx` writer and `.phy` (default since) 10-08 (842e605, 619b9d9, 7855d1e); animated models 10-11/12 (93a5952, branch anim-models, 0.14.0); game model reader 9c9a8ff, game skeletons 2b60362, Import Game Mesh 105b828, placed characters (0.14.1, 2026-10-12); loose-material fix 500a493 (2026-10-13); 63-character model names, Check the Map without sampling, the pose cache and shared rig copies e88a0f4 / f57eadf / c29bd02 / 10ea8d0 (0.17.0, 2026-10-13).

## The own model writer (static, dynamic, physics props)

Status: `done` (default; *Settings > Map & Model Compilers > Model Compiler* switches back to studiomdl).

Measured in game before the writer was built: a model compiled with `$staticprop` serves both `prop_static` and `prop_dynamic`; a `prop_physics` needs a model **without** `$staticprop` plus a `prop_data` block naming a class from the game's `scripts/propdata.txt`, or the game deletes it at once. Mass 0 = collision volume x the surface's density, as studiomdl does.

Format facts (structures from Valve's Source SDK 2013 / Alien Swarm source `studio.h` and `optimize.h` as the 32-bit on-disk layout; the layout within each file measured from studiomdl's output for a one-bone static prop):

| File | Version | Matched detail |
|---|---|---|
| `.mdl` | 49 | header, bone, hitbox set, one animation and sequence (`idle`), body part, model, meshes, textures, material folder, string table; the movement hull and sequence box are the mesh box grown by 0.25 units (measured); texture V is top-down (1 - v) |
| `.vvd` | 4 | 48-byte vertices (weights, position, normal, UV) and tangents; tangents per triangle, normalised, summed per vertex like studiomdl |
| `.dx90.vtx` | 7 | one strip group per mesh, clockwise indices |
| `.phy` | VPHY + IVP compact surface | IVP space is metres with axes (x, -z, y) of model space; 16-byte ledge header, 16 bytes per triangle (index, the triangle a ray through it meets on the far side, three edges pointing at their twins), 16 bytes per point; tree nodes hold the pieces' bounding sphere with box half-sizes as 250ths of the radius rounded up; mass centre and largest distance kept |

Verification (`tests/ingame/test_models.py` and field-by-field readers written from the SDK headers against studiomdl's output for the same mesh): vertices, normals, UVs and triangles identical; tangents 548 of 555 identical on a smooth monkey, the rest in the last digit; the same `.mdl` sections; collision files with the same layout, sizes, mass centre, bounding spheres and edge structure. In game: static props solid (multi-piece too), physics props fall and settle, the nav goes around them, all draw like studiomdl's, vrad lights them.

Known difference: IVP's rotational inertia approximation isn't public and isn't the solid formula (measured ratios against the true inertia 0.94 / 0.82 / 0.82 for two shapes, 0.71 for a cube); the writer stores the true inertia (affects only how physics props turn).

Axes: studiomdl turns every SMD it reads 90 degrees about the vertical axis (measured on the stored vertices of a compiled model); the own writer uses Blender's object axes directly and the SMD path writes them turned back, so a model faces as in Blender either way.

Guards: a part thinner than half a unit gets no collision; a collision piece over 4,096 triangles is left out (the `.phy` numbers triangles in 12 bits; past that the file is broken); a physics prop with no solid part is warned about; over 65,535 vertices is an error; a mesh with modifiers gets its own model per object; Alt+D copies share one model. Rebuilds: models are fingerprinted from the source data and the writer's own code (a fixed writer remakes old models); files the map no longer uses are removed (9138d45).

## Animated models (SMD/QC through studiomdl)

Status: `done` for the SMD/QC path (0.14.0); `not done`: an own writer for skeletal models (animated models need the Authoring Tools' `bin/studiomdl.exe`; without it only they are left out, with an error).

What is written: deform bones (up to 128; 127 usable, measured: 128 gave "Too many bones used in model, used 128, max 128"); the 3 strongest weights per vertex (studiomdl's limit, extra links silently pruned and renormalised); every action of the rig (its action plus NLA tracks; `hl_name` renames; Cyclic Animation loops and drops the repeated last frame); bone-parented empties as `$attachment`s; one hitbox per bone; the `prop_dynamic` gets `DefaultAnim` = `idle` or the first animation. Model space = the mesh object's local space x size.

Measured on studiomdl (`ANIMATED_MODELS_COMPILE.md`, 2026-10-11):

| Fact | Detail |
|---|---|
| The 90-degree turn | studiomdl bakes +90 degrees about Z into the root bone of every animation for non-`$staticprop` models; Hammerless counter-turns vertices and root bones by -90 so the axes stay Blender's; a model on a game skeleton is built in Valve space with no counter-turn and the entity yawed -90 |
| Sequence events | an event at frame N is stored as cycle N / (numframes - 1) (frames 2, 5, 7 of 11 -> 0.2, 0.5, 0.7); named `AE_*` events stored as event 0, type 1024 and the name; numbered events as given with a 64-byte options string; activities stored by name (index -1) |
| Hitboxes | without `$hbox`, one group-0 box per bone with vertices (bone-local bounds), header flag 0x1; any explicit `$hbox` turns that off; Valve's Francis uses groups 1-7 in a set named "L4D" |
| Attachments | `rotate p y r` is QAngle order (`rotate 0 90 0` stored as +90 about Z) |
| `$includemodel` | only the path string is stored; the included model need not exist at compile time; the game merges sequences at run time, mapping bones by name |
| Hardware skinning | 53 bones per strip: 53 bones gave 1 strip, 54 gave 2, 127 gave 3 (more draw calls, no failure) |
| `$declaresequence` | Valve's Francis QC lists 1014 before `$includemodel`, but the shipped `survivor_coach.mdl` has only 2 local sequences: not needed for normal models (purpose unverified) |
| `sdk_content/` | the Authoring Tools install Valve's complete survivor QC (`Francis_original/biker_original.qc`, 2030 lines) with its SMD/VTA files, DMX starter kits and Maya rigs: the first-hand reference; EULA: read, don't ship |

Tested in game: the Swelling Pod (example 49) plays idle and swell, swells on walk-up, bursts for -20 HP and is removed, and a shot sets it off (`AllowTakeDamage`); the full example regression passed (42). Fable's audit fixes: scale, pose restore, Rest Position, instances, mirrored models (built with a warning), missing studiomdl.

### Sampling speed: the pose cache (2026-10-13, 0.17.0)

Measured: sampling a rig's actions frame by frame (`scene.frame_set`) makes Blender re-evaluate the whole depsgraph each frame, about 0.6 s per model on a 3,135-object map; 32 animated pods took 21 s of Build & Play. Fixes in `hammerless/blender/extract.py`:

| Item | Detail | Status |
|---|---|---|
| `_POSE_CACHE` | Sampled poses kept between builds, keyed by `_rig_fingerprint`: the bones (rest matrices, parents, `use_connect`, inherit rotation/scale, local location, the pose bone's rotation mode), each action's name, `hl_name`, frame range and Cyclic flag, every F-curve's keys (co, interpolation, easing, handles), extrapolation, mute and F-modifier settings (`_settings_of`: every boolean/int/float/enum property, so a Noise's strength counts), and the scene frame rate; slotted actions (Blender 4.4+) are read through `_action_curves`. Value: per action, each frame's `{bone: matrix in armature space}`; `_animations_from_poses` turns them into a model's animations in that model's space and size. | `done` (f57eadf, c29bd02) |
| Not cached | A rig with pose-bone constraints or drivers (`_rig_fingerprint` returns None: its pose can follow other objects), and a model on a game skeleton (`game_rest`): sampled every build. | by design |
| Measured | 32 pods: 21 s -> about 2 s on the first build, 1.4 s and then about 1 s with the cache warm; output identical. | 2026-10-13 |
| Check the Map | `CHECK_ONLY["on"]` (set by `HL_OT_validate`): `_model_spec` skips `_sample_actions`, the scene is read for problems only; 24 s -> 2 s on the 32-pod map. | `done` (e88a0f4) |
| Shared models for copies | Copies of a rigged mesh share one `.mdl` only when the key matches: mesh shape, scale, kind, collision, physics class and mass, material slots, **and** the rig part (armature data name, its action names, the mesh's placement relative to the rig rounded to 4 places, pose position, the armature's own name when it has constraints or drivers (its own motion: no sharing), and its attachment Empties, found once per armature per build by `_rig_attachments` because `arm.children` scans every object in the file). Each prop still plays its own copy of the animation. | `done` (e88a0f4, 10ea8d0) |
| Pose restore | After sampling the frame is restored first, then `matrix_basis` of every bone: restoring the bones before the frame let the action's re-evaluation undo un-keyed pose tweaks. | `done` (f57eadf) |

Model names: the `.mdl` stores its path without `models/` in 64 bytes, so `MDL_NAME_MAX = 63` (studiomdl refused longer names; the own writer cut them, so the game couldn't find the file). `model_prop` keeps `hammerless/<map>/<stem>` within it: a long stem is cut and given a 6-character hash; a Map Name leaving under 6 characters is an error naming the map; two meshes whose names clean up the same get `_2`, `_3` within the room (before e88a0f4 an `x` was appended without limit).

## The game's own models (decoded from the VPKs, 2026-10-12)

Read only from the installed VPKs (`left4dead2`, `update`, `dlc1-3`). `gamemodel.py` reads `.mdl` v49 and `.ani`: bones, sequences, hitboxes, attachments, IK chains, pose parameters, and decodes the animations.

| Finding | How known |
|---|---|
| 96% of L4D2's animations (24,249 of 25,174) use **frame-anims** (flag 0x40), a format not in the SDK 2013 source | measured over 7 libraries |
| Frame-anim layout: three ints (`constantsoffset`, `frameoffset`, `framelength`); one flag byte per bone (0x01 constant Vector48, 0x02 constant Quaternion48, 0x04 per-frame Vector48, 0x08 per-frame Quaternion48, 0x40 constant Quaternion48S, 0x80 per-frame Quaternion48S, 0 = reference pose); constants then per-frame data, rotation before position | measured: the decoder matched the expected byte count on every animation in 7 libraries, 0 failures; poses right (feet on the floor, smooth across sections, loops exact) |
| Quaternion48S (smallest three): each value `(c & 0x7fff - 16384) / 23168`; the largest component index `(dc + 3) % 4` | measured; the dc 3 case inferred |
| `.ani` data in blocks, sections of 30 frames; RLE (SDK 2013) only for mesh reference and ragdoll poses and weapon viewmodels | measured |
| Bone rest data = bind pose (characters face **-Y**); animations are in bind space turned +90 about Z, carried by the root bone only (in game characters face +X); an animation library's bone defaults are not a usable rest pose | measured |
| Survivors: 77-88 bones (a shared 69-bone `ValveBiped` core plus `hlp_*` procedural and jiggle bones), ~112 flexes, hitbox set "L4D" 17 boxes (hitgroups head/neck 1, Spine2 2, Pelvis/Spine1 3, arms 4/5, legs 6/7), 29 attachments the game looks up by name, IK chains rhand/lhand/rfoot/lfoot; `anim_<name>` libraries of ~800-900 sequences plus ~105 gestures (Nick: 923 in game) | survey |
| Commons: 44 of ~55 share one 33-bone skeleton (29 body + 4 `Exp_` face bones), newer female ones 39; meshes have no sequences of their own and include `anim_common` (407 sequences); hitbox set "common_male" 23 boxes | survey |
| Specials: hunter 53, smoker 32, boomer 28, Tank 56, witch 67 (`ValveBiped`); charger, jockey, spitter 38-69 (`bip_*`, no shared names) | survey |
| Meshes: `.vvd` fixup table for LOD 0; `.vtx` front faces are clockwise, so triangles are reversed for Blender (then face normals agree with vertex normals on 99.95% of a common's faces, 99.8% of Nick's) | measured (`gamemesh.py`) |
| A `prop_dynamic` with a common's model has 421 sequences, Nick's 923; `SetAnimation` / `SetDefaultAnimation` play any by name and loop; `GetBoneOrigin`, `LookupBone`, `LookupAttachment` work from script; the "puppet" trick: Nick's model on a `prop_dynamic` plays the Smoker drag | measured in game, cheats off |

Not read: flexes, eyeballs, skin families other than the first, ikrules, localhierarchy, the zero-frame cache, jiggle rules. Open: whether a custom mesh keeps its own bone lengths under the survivor libraries (most bones animate rotation only); the ~997 placeholder sequences in Francis's and Louis's libraries.

## Game skeletons and placed characters

Status: `done` (0.14.0/0.14.1). *Add > Game Skeleton* builds an armature for one of 11 characters facing -Y; the exact rest pose is taken from the game's model at build time, not from Blender's edit bones (a Blender bone can't hold every game bone orientation); the commons' stored rest lies flat, so an idle's frame 0 is used, unturned and yaw-aligned (`standing_rest()`; the Tank's idle stood 63 degrees turned before the audit fix 8563bb4). At build: Valve space, `$includemodel` in the game's order, `$hbox`/`$attachment`/`$ikchain` copied, `$bonemerge` for unweighted bones, entity yaw -90, materials with `$allowdiffusemodulation 0` (an infected tints whatever it wears with random colours: bright blue / white on a plain material, 67f49e5); game model materials used through a patch include (commons need their Infected shader). *Import Game Mesh* (skinned from the stored rest to the standing pose), *Load Game Animation* (preview actions marked `hl_preview`, not built), *Add Mannequin*.

Proven in game (2026-10-12): the compiled bones, hitboxes, attachments and includes equal the game's (a common and Nick); reskinned commons walk, sit, take a shotgun blast and play their death animation, all ~35 commons on a test map within half a second (example 50); a survivor bot given a model on a survivor skeleton holds its gun; an imported common mesh rebuilt and worn looks like the original.

Placed characters (`placedchar.py`): a `prop_dynamic` of the game's model looping one of its animations, `solid 2`, health 100000 so `OnTakeDamage` fires without breaking; the Blender preview decodes one frame. Measured for examples 51/52: `ZSpawn` fails next to survivors, so `SpawnEntityFromTable("infected")` then `SetModel`, `m_nBody`, `SetAngles` is used; `RushVictim`'s range counts from the victim (call it 0.3 s later); a prop's `OnTakeDamage` fires when its health is set.

## Textures and materials

| Fact | Status / detail |
|---|---|
| Own images -> VTF 7.2 uncompressed (BGR888, BGRA8888 with see-through pixels), full mip chain, sides rounded down to a power of two (max 2048), float images to sRGB, premultiplied alpha made straight (edges went dark otherwise) | `done`; files 4-6x larger than DXT; no DXT writing, no normal maps from own images (`not done`) |
| Alpha: not connected = opaque; connected with Dithered/Opaque render method = `$alphatest`; Blended = `$translucent`; an alpha channel with nothing see-through is written opaque | `done` |
| Material resolution order: Game Material field or a name that looks like a game path; a game material with another Surface = a patch changing `$surfaceprop`; own image; else the default material with a warning. Custom Models get VertexLitGeneric materials in the model's folder (own image, a world material's `$basetexture` following patches, or a patch including a game model material) | `done` |
| Terrain vertex colour: the red channel becomes the displacement alpha; byte and float attributes blend the same | `done` |
| `GameContent.has_material` looked only in VPKs: Custom Models' materials showed untextured in the Textured view and hand-installed materials were reported missing | fixed 500a493 (2026-10-13): loose files in the game folder count |
| Parallax: `stdshader_dx9.dll` lists `$parallaxmap` / `$height_scale` on LightmappedGeneric, but a split-wall test in game (height scale 0.15) showed no effect: the PC shaders ignore it | measured 2026-10-13; `not done` by design (alternatives noted: auto-ssbump from height maps, baked geometry) |
| Game content: VPKs read directly (`left4dead2`, `update`, DLC, newer first), plus loose files; damaged VPKs skipped; VTFs decoded in Python (DXT1/3/5 and common uncompressed formats), one mip up to 512 px; game models drawn from `.mdl`/`.vvd`/`.vtx` LOD 0 first skin; previews rebuilt from the install, not stored in the .blend | `done` |
| Where files go: `models/hammerless/<map>/`, `materials/models/hammerless/<map>/`, `materials/hammerless/<map>/`, fingerprinted with the map | `done`; packing into the map or an addon VPK `not done` |

## Limits and what's left

| Item | Status |
|---|---|
| Own writer for animated (skeletal) models | not done |
| Packing models and textures with the map | not done |
| `.phy` ragdoll per bone (a common-skeleton model gets the commons' ragdoll from the game's library) | not done |
| Collision of animated models = rest shape or none | known |
| Flexes (faces) | not done |
| Survivor puppet trick as a feature | not done |
| Meshes without UVs get zero texture coordinates | known |
| Writer speed vs studiomdl | not measured |
| Animation sampling of rigs with constraints or drivers (not cached): ~0.6 s per model per frame set on a big map | known (by design; see the pose cache above) |
| Enemies on bot bodies (precache rule: the map must precache any model given with `SetModel`) | see [../logic/](../logic/README.md) |

Last checked: 2026-10-13 against branch light-layers, commit 10ea8d0 (0.17.0).
