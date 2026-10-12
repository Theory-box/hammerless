# Animated models from Blender for L4D2: design research

Date: 2026-10-12. Scope: phase 1 writes SMD + QC and runs L4D2's own `bin/studiomdl.exe`. A writer of our own may come later.

**How claims are marked.** I tested some claims myself against L4D2's studiomdl. Each compiled file was decoded with small readers I wrote from the `studio.h` layout, and the test files sit in [model_tools/](model_tools/) (copied from the research scratch folder). Those claims are tagged **[MEASURED]**. Claims from Valve's own L4D2 Authoring Tools content in `Left 4 Dead 2/sdk_content/` are tagged **[VALVE-SDK]**. Claims from the game's shipped .mdl files are tagged **[GAME]**. Anything from a web page or source file carries a citation. **[UNVERIFIED]** means nobody has tested it yet.

**The Valve Developer Wiki (VDC)** is behind an Anubis bot check, and I did not try to get past it. I read the VDC pages through Wayback Machine copies from 2024, e.g. `https://web.archive.org/web/2024/https://developer.valvesoftware.com/wiki/$sequence`. Text copies of those pages are in a scratch folder (not kept).

**Clean-up.** All test models were compiled to `left4dead2/models/hammerless_research/`, and that folder has been deleted.

---

## 0. The most useful local discovery

The L4D2 Authoring Tools install `sdk_content/`. It contains Valve's own full source for a survivor, plus starter kits:

- `sdk_content/Francis_original/biker_original.qc`: Valve's complete QC for a survivor replacement (2030 lines).
- The same folder has the reference, LOD, ragdoll and facemorph SMD/VTA files.
- `sdk_content/Starter_Kits.zip` holds weapon kits made in DMX (binary DMX, `<!-- dmx encoding binary 3 format model 15 -->`), with their QCs.
- `sdk_content/Maya_Rigs_Animation.zip` holds Maya rigs.

These are first-hand, L4D2-specific templates. Treat them as the main reference for survivor and infected models, ahead of anything community-made. Valve's EULA for SDK content applies to them: read them, don't ship them.

---

## 1. SMD format, Blender conversion, axes; DMX

### 1.1 SMD layout

Sources: VDC "SMD"/"Studiomdl Data" (archived copy), and Valve's `biker_original_reference.smd`.

```
version 1
nodes
<id> "<bone name>" <parent id or -1>
...
end
skeleton
time <frame>
<bone id> <posX> <posY> <posZ> <rotX> <rotY> <rotZ>     (one line per bone, every frame)
...
end
triangles                                   (reference / collision SMDs only)
<material name, no path, no quotes>
<parent bone> <px py pz> <nx ny nz> <u v> [<nlinks> <bone> <weight> ...]   (x3)
...
end
```

- **Bones are matched by name** across SMDs. IDs are local to each file.
- **Bones without vertices get collapsed** (removed); see 1.4.
- **Position and rotation are relative to the parent bone.** For root bones they are in model space. Rotation is in radians.
- **Rotation convention [MEASURED].** In the test, the bone `arm` with rotation (0.3, 0.5, 0.7) was stored in the .mdl as quaternion (x, y, z, w) = (0.0521, 0.2794, 0.2938, 0.9126). That equals R = Rz(z)·Ry(y)·Rx(x). The other order, Rx·Ry·Rz, would give (0.2199, 0.1801, 0.3632, 0.8873).
  - Rz·Ry·Rx is exactly Blender's `Matrix.to_euler('XYZ')`. So write `local_matrix.to_euler('XYZ')`.
  - Euler flips between frames don't matter, because studiomdl turns each frame into a quaternion.
- **Every frame should list every bone.** The VDC says that in Source, omitted bones fall back to the default pose. Always write all bones.
- **Triangles are one-sided.** Studiomdl takes the vertex order as given. Our static path already gets this right, so keep the same winding.
- **Material names** become `$cdmaterials/<name>.vmt`.
- **Line endings:** the VDC says CRLF. Our existing LF output works in practice (static props compile), so this is not a real requirement.
- **Optional `<nlinks> <bone> <weight>` list [MEASURED].**
  - The model format stores at most 3 bones per vertex (`MAX_NUM_BONES_PER_VERT 3`, `public/studio.h`).
  - In the test I gave one vertex 4 links (0.5, 0.2, 0.2, 0.1). Studiomdl **silently dropped the smallest** and renormalised the rest to 0.556, 0.222, 0.222.
  - Valve's own Francis reference SMD has up to 11 links per vertex. So studiomdl prunes, and we may hand it more than 3. Better: prune to 3 ourselves and normalise, so Blender's preview matches the game.
  - Per the VDC, any weight left over (weights summing below 1) goes to the `<parent bone>` field. Write the strongest bone there.
- **Flex/shape keys** go in a separate `.vta` with a `vertexanimation` block. That is phase 2 (facial flexes); Francis's QC shows the full survivor flex setup.

### 1.2 Axes: the 90 degree turn

**[MEASURED: the key finding for this design]**

- **With `$staticprop`**, studiomdl bakes a +90 degree turn about Z into the vertices, (x, y) → (-y, x). That is the turn `models.py:_vertex_line` cancels. It collapses all bones into one bone called `static_prop`.
- **Without `$staticprop`** (any animated model, and also a one-bone dynamic model):
  - The vertices are stored **untouched**.
  - The bones' rest pos/quat are stored untouched.
  - The turn is instead **baked into the root bones' animation data in every sequence**. The decoded root-bone track of every animation was the quaternion (0, 0, 0.7071, 0.7071), which is +90 degrees about Z.
  - The in-game result is the same turn, (x, y) → (-y, x), applied through animation.
- **Absolute attachments** are given in the SMD (pre-turn) space. In the test, `$attachment world root 0 0 40 absolute` was stored with a compensating -90 degree yaw. Valve's Francis QC puts the `eyes` absolute attachment at y = -2.8, which is in front of a face pointing -Y.
- **Valve's convention follows from this.** A character in the SMD faces **-Y**, and in game it faces **+X**. That is also what Blender Source Tools and Crowbar round-trips produce: a model imported into Blender faces -Y.

**Design decisions this forces:**

1. **Own skeleton and own animations.**
   - We can keep Hammerless's rule that "Blender object axes = game axes". To do that, apply the turn (x, y) → (y, -x) to the whole armature: every root bone's matrix, every root keyframe, and every vertex. In practice that means premultiplying the armature-space matrices by Rz(-90).
   - Studiomdl's +90 then cancels it, just like the static path does today.
   - Alternative: emit `$origin 0 0 0 -90`. Its 4th argument is a Z rotation, but it is **[UNVERIFIED]** how it interacts with this turn. Test it before relying on it.
2. **Models that `$includemodel` the game's animations** (survivor or infected skeletons).
   - The game's animations already contain Valve's turn, and they are applied to *our* bones by name.
   - So the mesh and rest skeleton must be in **Valve's SMD space**: no counter-turn, character facing -Y in Blender.
   - The decompiled ValveBiped SMD skeleton must be used unchanged.
   - Hammerless then has to add -90 degrees of yaw when it places such a model as an entity, if it wants the in-game facing to match Blender. Alternatively, document "face -Y".
3. **Hitbox and attachment coordinates** are in bone-local space, which does not change under the root turn. Absolute attachments, `$illumposition` and `$bbox` are in SMD space. Apply the same counter-turn to them as to the vertices in case 1, and none in case 2.

### 1.3 Converting a Blender armature

This is my design, not copied from anyone.

- **Bone matrices.**
  - Rest: `bone.matrix_local`, in armature space.
  - Pose at frame f: `pose_bone.matrix`, in armature space, after `scene.frame_set(f)`. Alternatively, evaluate with `depsgraph` for speed.
  - Local transform for the SMD: `parent_matrix.inverted() @ matrix`. For root bones use `A @ matrix`, where `A` is the counter-turn from 1.2 (identity in case 2), possibly times the armature object's matrix relative to the model origin.
  - Write `loc = M.to_translation()` and `rot = M.to_3x3().to_euler('XYZ')`. Drop scale. The SMD has no bone scale, so reject or bake scaled bones.
- **Bone axes.**
  - Blender bones point along local +Y. ValveBiped bones point along +X, the convention Max/Maya exporters produced.
  - For our own skeletons nothing in the engine needs X-along-bone, with two exceptions. `$ikchain` knee directions and the `$definebone` "fixup" columns are written around the X-long-axis convention (VDC `$definebone`). `$jointconstrain` axes are bone-local.
  - So: export Blender bone matrices as they are, and convert the hitbox, joint-limit and knee vectors into that same local frame. A "Source bone axis (X)" option, which post-multiplies each bone by a fixed Rz(-90) or similar, can come later if we want ValveBiped-style limits.
- **Including the game's skeleton** (case 2). Bone names, parenting and *rest pose* must match the game skeleton. The VDC `$includemodel` page says included models "need to have the same bone hierarchy"; bone *order* may differ. **[GAME]** confirms this: `survivor_coach.mdl` lists Spine before Thigh, while `survivor_namvet.mdl` lists Thigh first. The reliable way is a "Load L4D2 skeleton" operator that builds the armature straight from the game .mdl. Read the bone pos/quat (or `poseToBone` inverted) and keep the exact matrices instead of round-tripping Blender bone roll. We already read .mdl bones; see `model_tools/readmdl.py`.
- **Mesh.**
  - Take the evaluated mesh **in rest pose**: set `armature.data.pose_position = 'REST'`, or disable the Armature modifier, then `evaluated_get`.
  - Transform it into armature space with `arm.matrix_world.inverted() @ obj.matrix_world`, and then by `A`.
  - Use the same UV and normal rules as `models.py`.
  - Weights come from vertex groups whose names are bone names. Drop non-deform groups, keep the 3 largest, normalise.
  - Vertices with no weight get the parent bone field: the bone the object is parented to, else the root.
- **Actions to sequences.**
  - Assign each action to the armature in turn, using `action.frame_range` (or the NLA strip range). Sample every integer frame.
  - Use `fps = scene.render.fps / scene.render.fps_base`, divided by the frame step if we subsample.
  - One SMD per action. Studiomdl's frame numbers are 0-based relative to the first written frame.
- **Units.** Use the same scale as the existing static model path, so model space is in Hammer units. Bone positions scale by the same factor and rotations don't.
- **Collapsing bones** **[MEASURED]**:
  - A bone with no vertex weight is removed: test bone `unused`, and bone `empty`, whose only 0.1 weight was pruned.
  - `$bonemerge "<bone>"` kept an unweighted bone. It was stored with flag 0x40000, BONE_USED_BY_BONE_MERGE.
  - Bones used by an attachment or hitbox were kept (flags 0x700 = hitbox | attachment | vertex LOD0).
  - So emit `$bonemerge` for any bone the user wants kept. Typical cases: attachment-only helpers, bones that only drive game logic, and every bone of an `$includemodel` skeleton that may lack weights in our mesh, e.g. ValveBiped weapon bones.
  - The VDC `$bonemerge` page warns that some studiomdl branches ignore `$bonemerge` for collapsing. L4D2's honoured it in the test. `$definebone` lines also keep bones, and Crowbar generates them.

### 1.4 DMX in L4D2

- **Accepted [MEASURED].** I compiled Valve's L4D2 starter-kit `w_rifle_ak47.qc` (binary DMX model plus DMX physics) with L4D2's studiomdl into `hammerless_research/dmx_test.mdl`, and it completed. `bin/dmxconvert.exe` is also present.
- The VDC `$sequence` page notes that "a DMX skeletal animation may only be used if the game ... includes the Dmxconvert util". L4D2 does include it.
- **For DMX:**
  - It is one file for the mesh plus flexes (delta states) plus the skeleton.
  - It keeps names and precision.
  - It has `$upaxis`-style metadata (the kit QC uses `$upaxis y`).
  - It is the format Valve used for L4D2-era content.
  - BST's `datamodel.py` (MIT) shows the encoding.
- **Against DMX:**
  - It is a much more complex format (an element graph, typed attributes, binary or keyvalues2 encodings) for no gain in what the final MDL can hold. The MDL still caps at 3 weights per vertex and 128 bones.
  - The SMD path is already used and proven in Hammerless.
- **Recommendation:** SMD for phase 1, plus VTA when flexes come. Revisit DMX only if we need flex controllers with stereo/corrective shapes in one file.

---

## 2. QC commands

Syntax is from the VDC (archived) and Valve's L4D2 QCs. The **[MEASURED]** items compiled in L4D2's studiomdl.

```
$modelname "hammerless/<map>/<name>.mdl"         // relative to models/
$cdmaterials "models/hammerless/<map>/"          // may repeat (survivors add "models/survivors/")
$surfaceprop "flesh"
$body <name> "ref.smd"                           // or $model <name> "ref.smd" { eyeball/flex... }
$bodygroup <name> { studio "a.smd"  studio "b.smd"  blank }
$texturegroup skinfamilies { { "matA" } { "matA_alt" } }
$scale 1.0
$bbox / $cbox minx miny minz maxx maxy maxz       // view/collision boxes (optional)
$illumposition x y z [bone]                      // lighting sample point (optional; auto otherwise)
$eyeposition x y z
$keyvalues { prop_data { "base" "Wooden.Medium" } }   // also bone_followers {...} etc.
$contents "monster" | "grate" | "notsolid" | "solid" | "ladder"
```

### Sequences [MEASURED]

```
$sequence idle "ref.smd" fps 30                  // at least one $sequence needed unless only $includemodel
$sequence wave "wave.smd" fps 20 loop activity ACT_IDLE 1 {
  { event AE_CL_PLAYSOUND 2 "Weapon_Pistol.Single" }
  { event 1100 5 "FireUser1" }
  { event 9999 7 "custom options here" }
}
```

What was stored:

- Activities are stored **by name** (`ACT_IDLE`, index -1) and resolved at runtime. Writing `ACT_xxx 1` without the keyword also works (VDC).
- **Event timing:** an event at frame N is stored as cycle N / (numframes - 1). In the test, frames 2, 5 and 7 of 11 frames became cycles 0.2, 0.5 and 0.7.
- **Named events** (`AE_*`) are stored with event = 0, type = 1024 (new-style), and the name string, and are resolved at runtime. **Numbered events** are stored as given, with their options string (64 bytes max, from the struct). Any number is accepted, e.g. 9999.
- Further `$sequence` options are on the VDC page:
  - Frames and timing: `frames a b`, `numframes`, `origin`, `angles`/`rotate`, `scale`, `reverse`.
  - Playback: `snap`, `realtime`, `fadein`/`fadeout` (default 0.2 s), `hidden`, `autoplay`.
  - Layering: `delta`/`predelta`, `addlayer`, `blendlayer ...`, `weightlist`.
  - Blending: `blend <pose> min max` with `blendwidth`, `posecycle`.
  - Motion extraction: `LX LY LZR ...` and `walkframe`.
  - IK: `ikrule`.
  - Transition graph: `node`/`transition`.
  - Per-sequence `keyvalues {}`.
- `$animation <name> "file" [options]` defines a raw animation that `$sequence <name> { <anim names> ... }` can reference. Use this for blends, deltas (`subtract`) and shared clips. A plain one-clip sequence can name the SMD directly.
- `$declaresequence <name>` reserves a sequence slot by name. Valve's Francis QC lists 1014 of them before `$includemodel`. Valve's shipped `survivor_coach.mdl`, however, has only 2 local sequences **[GAME]**, so they are not needed for normal models. This looks like a holdover to keep sequence indices stable for add-on overrides **[UNVERIFIED purpose]**.
- `$weightlist <name> { "bone" weight ... }` sets per-bone weights for layers; see Francis's QC.

### Hitboxes [MEASURED]

```
$hboxset "default"
$hbox <group> "<bone>" minx miny minz maxx maxy maxz      // bone-local box
```

- **Groups** (VDC `$hbox`): 0 generic, 1 head, 2 chest, 3 stomach, 4 left arm, 5 right arm, 6 left leg, 7 right leg, 8 neck (later games only). L4D2's Francis uses 1 to 7, in a set named `"L4D"`.
- **If `$hbox` is omitted**, studiomdl generates **one group-0 hitbox per bone that has vertices**. The box is the bone-local bounds of the vertices assigned to that bone. The header gets flag 0x1 (STUDIOHDR_FLAGS_AUTOGENERATED_HITBOX). `-h` prints them as ready-to-paste `$hbox` lines.
- Any explicit `$hbox` turns auto generation off.
- **Head damage:** the game applies head multipliers by hitgroup 1, so a killable custom infected needs a group 1 head box **[UNVERIFIED in L4D2 game code]**.

### Attachments [MEASURED]

```
$attachment "<name>" "<bone>" x y z [rotate pitch yaw roll] [absolute] [rigid]
```

- `rotate 0 90 0` was stored as a +90 degree turn about Z. So the three numbers are QAngle order (pitch, yaw, roll) in degrees.
- `absolute` gives coordinates in reference (SMD) space.
- `rigid` lets studiomdl drop the bone and reparent the attachment to the next bone up (VDC).
- Other options (VDC): `world_align`, `x_and_z_axes`.

### Including models [MEASURED compile, VDC semantics, GAME evidence]

```
$includemodel "infected/anim_common.mdl"
```

- Only the path string is stored. The included model need not exist at compile time; the test compiled with no such file present.
- The game loads it at run time and appends its `$animation`, `$sequence`, `$attachment` and collision text, mapping bones by name (VDC).
- `$definebone name parent x y z xr yr zr fx fy fz fxr fyr fzr` defines bones up front (it fixes order and stops collapsing). Crowbar can generate these.
- `$bonemerge "<bone>"` sets the bone-merge flag and keeps the bone; see 1.3.

### Ragdoll and physics (VDC; syntax as in Valve's Francis QC)

```
$collisionjoints "phys.smd" {
  $mass 100  $inertia 10  $damping 0.05  $rotdamping 0.01
  $rootbone "bone"
  $jointmerge "a" "b"   $jointskip "x"
  $jointconstrain "<child bone>" x|y|z limit|free|fixed <min deg> <max deg> <friction>
  $jointmassbias "bone" 4   $jointrotdamping "bone" 3   $jointinertia/$jointdamping ...
  $noselfcollisions   $concaveperjoint
}
$sequence ragdoll "ref_or_ragdoll_pose.smd" ACT_DIERAGDOLL 1 fps 30
```

- The physics SMD is a skinned mesh in which each convex piece is rigidly weighted to one bone.
- **Limits (VDC):** up to 32 simulated bones for a ragdoll, and 99 parts on an animated prop.
- Each convex part on a `prop_dynamic` creates a `phys_bone_follower` entity, which costs edicts.
- **Known bugs (VDC):**
  - `$collisionjoints` with only one joint breaks on prop_dynamic. The workaround is `$keyvalues { bone_followers { "bone" "x" } }`.
  - `$jointcollide` doesn't work in L4D/L4D2; it disables self-collision instead.
- `$collisionmodel` (what we use now) makes one rigid hull on the root bone. It is fine for animated props whose collision doesn't need to move.

### Other commands

```
$poseparameter <name> <min> <max> [loop <n>] [wrap]     // e.g. Francis: body_yaw -90 90 360; move_x -1 1 0
$ikchain <name> <end bone> knee x y z                   // Francis: rhand ValveBiped.Bip01_R_Hand knee 0.707 0.707 0
$ikautoplaylock "<chain>" 1 0.1
$jigglebone "<bone>" { is_flexible { tip_mass .. length .. yaw_constraint .. ... } }
$lod <dist> { replacemodel "ref.smd" "lod1.smd" }   $shadowlod { ... }
$illumposition x y z [bone]
```

- Each `$ikchain` must match between the model and any `$includemodel` (VDC). For ValveBiped models, copy Valve's four (rhand, lhand, rfoot, lfoot) plus `ikclip` for survivors.

---

## 3. Limits

| Limit | Value | Source |
|---|---|---|
| Bones per model | **127 usable** with this studiomdl. 127 compiled; 128 gave `ERROR: Too many bones used in model, used 128, max 128` | [MEASURED]; `MAXSTUDIOBONES 128` in studio.h |
| Bones per vertex | 3; extra links silently pruned (smallest dropped, renormalised) | [MEASURED]; `MAX_NUM_BONES_PER_VERT 3` |
| Hardware-skinning bones per strip | **53**. 53 bones gave 1 strip; 54 gave 2; 127 gave 3 (each strip header is 27 bytes in the log). More bones only means more draw calls, not a failure | [MEASURED]; optimize.h defines `MAX_NUM_BONES_PER_STRIP 512` as the format cap, so 53 is studiomdl's DX9 setting |
| Vertices per mesh / model | 65536 (`MAXSTUDIOVERTS`); the VVD/VTX use 16-bit indices per mesh | studio.h; mdlwrite uses 65535 |
| Triangles | 65536 (`MAXSTUDIOTRIANGLES`) | studio.h |
| Skins (textures) | 32 (`MAXSTUDIOSKINS`) | studio.h |
| Pose parameters | 24 (`MAXSTUDIOPOSEPARAM`) | studio.h |
| Flex descriptors / controllers | 1024 / 96 | studio.h |
| Sequences / animations / events / frames / source bones | 1524 / 2000 / 1024 / 2000 / 512 | studiomdl.h from an *unofficial* old-SDK mirror: **[UNVERIFIED for L4D2]**. Valve's anim_namvet.mdl has 906 sequences and 2264 animations [GAME], so the animation limit is at least that high in L4D2's branch |
| Attachments | no small cap found **[UNVERIFIED]**; Valve's survivors use 28 to 29 [GAME] | |
| Ragdoll bones | 32 (24 in older branches); 99 parts on an animated prop | VDC `$collisionjoints` |
| Event options string | 64 bytes (`char options[64]` in mstudioevent_t) | studio.h layout; decoded in test |

---

## 4. Reusing the game's skeletons and animations

### How `$includemodel` works

- **[GAME]** decoded from the VPKs:
  - `models/infected/common_male_ceda.mdl` has 33 bones, **0 local sequences**, 1 hitbox set (23 boxes) and 15 attachments. It includes `models/infected/anim_common.mdl`, `models/infected/anim_common_vomit.mdl` and `models/infected/anim_common_male_exp.mdl`.
  - `anim_common.mdl` has 29 bones, 407 sequences and 648 animations, but no mesh.
  - The common model's 4 extra bones (`ValveBiped.Exp_Jaw`, `Exp_Eyebrows`, `Exp_Eyelids_Upper`, `Exp_LipsUpper`) don't exist in anim_common, so the included animations simply don't drive them. Those bones are driven by the `anim_common_male_exp` include.
  - **This is the pattern for "a new model with the same bone names".** Bones the animation library doesn't know keep the model's own pose. Engine behaviour for unmatched bones, per the VDC and Valve's usage: they stay at the model's default pose **[UNVERIFIED in a test of our own]**.
- **Survivors:** `survivor_coach.mdl` has 72 bones and 2 local sequences (`coach`, `ragdoll`), and includes `survivors/anim_coach.mdl` (803 sequences) and `survivors/anim_gestures.mdl`. Namvet includes `anim_NamVet.mdl` and `gestures_namvet.mdl`.
- **Available animation libraries** (from the VPK listing):
  - Infected: `infected/anim_common`, `anim_common_vomit`, `anim_common_male_exp`, `anim_boomer`, `anim_charger`, `anim_hulk`, `anim_hunter`, `anim_jockey`, `anim_smoker`, `anim_spitter`, `anim_witch`.
  - Survivors: `survivors/anim_{biker,coach,gambler,mechanic,producer,teenangst,manager,namvet}`, `anim_gestures`, `gestures_{biker,namvet,teenangst}`.
  - Viewmodels: `v_models/anim_v_*`, `v_models/weapons/anims_v_claw_*`.
- **Requirements for a new model** (VDC plus Valve's practice):
  - Same bone names and **same hierarchy and rest pose** as the library. Bone order may differ.
  - Same `$ikchain` set.
  - Our mesh space must be Valve's SMD space; see 1.2, case 2.
  - The rest pose has to match because the library's animation is local per bone, applied to our bones. A different rest pose deforms the mesh, though it still "works" in the sense that the bones move.
  - Extra bones of our own are fine; they hold their rest pose.
  - Bones in the library but missing from our model are ignored.
  - Example: a new common infected might be a ValveBiped skeleton (29 bones) plus extras. Use `$includemodel "infected/anim_common.mdl"`, add the Exp include if the face bones exist, add a hitbox set with groups, use `$surfaceprop flesh`, and add a ragdoll `$collisionjoints`.
- **Activities and the Director:** the game's NPC code picks sequences by activity, and those come from the include. So an infected or survivor *replacement* needs no `$sequence` of its own beyond the mandatory reference one. For survivors, Valve's QC adds `$sequence ragdoll ... ACT_DIERAGDOLL`.

### Standard community workflow (summary)

1. **Decompile** the game model with Crowbar (MDL to QC, SMDs and anim SMDs). Import the reference SMD into Blender, with Blender Source Tools or SourceIO.
2. **Edit:** re-skin the new mesh to the imported ValveBiped skeleton, keeping the bone names and rest pose.
3. **Export** the reference SMD (and VTA for faces) and reuse the decompiled QC. Keep its `$includemodel`, `$hboxset`, `$attachment`, `$poseparameter`, `$ikchain`, `$weightlist`, `$collisionjoints` and `$bonemerge`/`$definebone` blocks. Change `$modelname`, `$cdmaterials` and the mesh.
4. **Compile** with studiomdl, often through Crowbar's GUI. Ship it in an addon VPK whose `$modelname` equals the original path so it overrides the game model, as noted in Valve's Francis QC header comment.
5. **Survivor extras:** `_it` "boomer bile" material variants through `$texturegroup` (Valve QC comment), eyeballs/eyelids/flex, and LODs.

### Valve's survivor QC skeleton (Francis), reduced to the essentials

```
$modelname survivors/survivor_biker.mdl
$cdmaterials models/survivors/biker_original
$cdmaterials models/survivors/
$model "Biker_Original" "mesh/biker_original_reference.smd" { eyeball/eyelid/mouth/flexfile/flexcontroller ... }
$bbox -13 -13 0 13 13 72
$eyeposition 0 0 70
$texturegroup "ToggleDetailTexture" { { head body } { head_it body_it } }
$poseparameter body_pitch -90 90 360  (+ body_yaw, head_pitch, head_yaw, move_x -1 1 0, move_y -1 1 0)
$attachment ... ~28 named points (eyes absolute, mouth, forward, grenade/molotov/pills/medkit/pistol/primary RIGID, lfoot/rfoot, survivor_light, bleedout, ...)
$weightlist ... ~30 lists
$hboxset "L4D"  + 17 $hbox lines (groups 1-7)
$surfaceprop "flesh"
$collisionjoints "mesh/biker_original_ragdoll.smd" { $mass 100 ... $rootbone valvebiped.bip01_pelvis + per-bone $jointconstrain ... }
$sequence Biker "mesh/biker_original_reference.smd" fps 30
$sequence ragdoll "mesh/biker_original_ragdoll.smd" ACT_DIERAGDOLL 1 fps 30
$declaresequence ... (x1014)
$includemodel "survivors/anim_Biker.mdl"
$includemodel "survivors/gestures_biker.mdl"
$ikchain rhand/lhand/rfoot/lfoot/ikclip ...  $ikautoplaylock rfoot/lfoot 1 0.1
$lod 10/15/25/40 { replacemodel ... }  $shadowlod { ... }
$jigglebone ... (optional)
```

---

## 5. Animation events and game logic

From source-sdk-2013 (`src/game/server/props.cpp`, `baseanimating.cpp`, `src/game/shared/scriptevent.h`, `eventlist.h`), and the VDC "Animation Events" and "prop_dynamic" pages. L4D2's branch isn't public, so the in-game behaviour below is **[UNVERIFIED]** until tested.

- **prop_dynamic forwards an event to the I/O system.**
  - `CDynamicProp::AnimThink` calls `DispatchAnimEvents`.
  - `CDynamicProp::HandleAnimEvent` handles `SCRIPT_EVENT_FIRE_INPUT` (**1100**) as `AcceptInput(options, this, this, emptyVariant, 0)`. So `{ event 1100 <frame> "FireUser1" }` makes the prop fire its own **OnUser1** output.
  - The input gets no parameter, so use FireUser1 to FireUser4. Map OnUser1 to `!self RunScriptCode ...` or CallScriptFunction to reach VScript.
  - **This is the bridge from animation events to script.** The VDC example says exactly "Fires input FireUser1 to animating entity... Only works on prop_dynamic".
- **Also handled by prop_dynamic:** 1004 `SCRIPT_EVENT_SOUND`, which plays a sound by name. The server side also handles `AE_SV_PLAYSOUND`, `AE_RAGDOLL` and `AE_SV_DUSTTRAIL`.
- **Client-side named events** (no game logic):
  - `AE_CL_PLAYSOUND "<soundscript>"`
  - `AE_CL_CREATE_PARTICLE_EFFECT "<effect> <attach type> <attachment>"`
  - `AE_CL_STOP_PARTICLE_EFFECT`
  - `AE_CL_BODYGROUP_SET_VALUE "<group> <value>"`
  - `AE_CL_ENABLE_BODYGROUP` / `AE_CL_DISABLE_BODYGROUP`
  - Numeric client events: 5001/5011... muzzle flashes, 6004 to 6009 footsteps, and others.
  - L4D2's support for the particle and bodygroup ones is **[UNVERIFIED]**.
  - Numbered events (event 9999 in the test) compile, but nothing handles them on prop_dynamic, so they are only useful for our own future tooling.
- **Other prop_dynamic outputs:** `OnAnimationBegun` and `OnAnimationDone`. Inputs include SetAnimation, SetDefaultAnimation, SetAnimationNoReset, SetPlaybackRate, and keyvalues `DefaultAnim`, `RandomAnimation`, `HoldAnimation`.
- **VScript in L4D2.** This list is first-hand from the user's own `scripts/vscript_help.txt` dump, as reported by the sub-agent:
  - Available: `GetSequence`, `SetSequence`, `ResetSequence`, `LookupSequence`, `LookupActivity`, `GetSequenceName`, `GetSequenceActivityName`, `GetSequenceDuration`, `IsSequenceFinished`, `LookupAttachment`, `GetAttachmentOrigin/Angles/Bone`, `LookupBone`, `GetBoneOrigin/Angles`, `SetBodygroup`, `FindBodygroupByName`, `SetPoseParameter(int, float)`, `SetModelScale`.
  - Missing: GetCycle, SetCycle and LookupPoseParameter. Pose-parameter indices must therefore be known at export time. Our exporter knows the order, so it can emit them as constants.
  - **No script hook fires on anim events.** Alternatives:
    - (a) Event 1100 → OnUserN → script. Recommended, frame-accurate on the server.
    - (b) Schedule from `GetSequenceDuration` × cycle-of-event. Our exporter knows each event's cycle, so it could emit a table.
    - (c) OnAnimationDone for end-of-sequence.
- **Events in included animations:** they live in the library's sequences, so they arrive through `$includemodel` too. Valve's infected and survivor animations carry their own footstep and sound events.

---

## 6. Existing tools and licences

Licences were checked against the repos' LICENSE files and headers by the sub-agent, using the GitHub API and raw files.

| Tool | Licence | Use for us |
|---|---|---|
| Blender Source Tools (Artfunkel/BlenderSourceTools) | Add-on files carry **GPL v2 or later** headers ("Copyright (c) 2014 Tom Edwards"); the repo has no LICENSE file. `datamodel.py` (DMX reader/writer) is **MIT** | Read for format understanding only; don't copy the GPL exporters. `datamodel.py` may be reused with its notice |
| SourceIO (REDxEYE/SourceIO), Blender MDL/VMF importer | **MIT** (c) 2020 REDxEYE | Safe to read and to copy with attribution; useful MDL v49 parsing reference |
| Crowbar (ZeqMacaw/Crowbar), VB.NET, source published | **CC BY-SA 3.0** (copyleft, rename forks) | Read only; don't copy |
| sourcepp / mdlpp (craftablescience) | **MIT** (c) 2023 Laura Lewis | Safe reference for MDL/VVD/VTX structures |
| Source SDK 2013 (ValveSoftware/source-sdk-2013) | "SOURCE 1 SDK LICENSE": free use for Source mods, redistribution free and with notices; **not open source**. Updated in the 2025 TF2-SDK sync with substantively the same terms | `src/public/studio.h`, `optimize.h`; game code for events. Read and re-implement structures, which our mdlwrite.py already does. **studiomdl source is not in the repo.** Only an unofficial old-SDK mirror exists (workshop.perforce.com), with unclear licensing: don't use it beyond confirming facts |
| VDC wiki | Content licence not verified (the bot wall hides the footer) | Paraphrase; don't copy |
| Valve sdk_content (local) | Steam/SDK EULA | Templates to learn from; don't redistribute |

- **Format docs:** VDC "MDL (Source)", "Studiomdl Data", "QC", "DMX", "$sequence", "$hbox", "$includemodel" and others (Wayback copies). `studio.h` in source-sdk-2013 says `STUDIO_VERSION 48` (TF2 branch). L4D2 is version 49 [MEASURED: our compiles read version 49]. Our `mdlwrite.py` already handles the v49 layout used here.

---

## 7. Proposed phase-1 design (concrete)

1. **New core module `hammerless/core/animmodels.py`** (pure Python, no bpy). It holds:
   - `Skeleton` (names, parents, rest local pos/euler)
   - `Clip` (name, fps, loop, activity, weight, events[(frame, id|name, options)], frames of per-bone local transforms)
   - `SkinnedMesh` (triangles with up to 3 (bone, weight) links)
   - hitboxes, attachments, an includemodel list, keep-bones (`$bonemerge`), physics joints
   - `smd_reference()`, `smd_animation()`, `qc_text()`, and a `compile_model()` that reuses the existing runner and log checks.
2. **Blender side, `blender/extract.py`:**
   - Extract from the armature object as in 1.3.
   - Store the per-object options on `obj.hammerless`: mode "own skeleton" (with counter-turn) or "game skeleton" (Valve space, plus an `$includemodel` picker from the list in section 4).
   - A per-action list holding fps override, loop, activity, and events (frame, type preset: FireUser1-4 / sound / particle, options).
   - Hitboxes from empties (box empties parented to bones, with a group enum) or "auto".
   - Attachments from empties parented to bones.
3. **QC generation rules:**
   - Never `$staticprop` for armatures.
   - Always write a `$sequence` for the reference pose, plus one per action.
   - Write `$bonemerge` for every bone without weights that the user wants (attachments, includes).
   - Write `$hbox` only when the user defines boxes.
   - Turn events into `{ event 1100 f "FireUserN" }` and similar.
4. **Placement:** a "game skeleton" model needs a yaw offset of -90 degrees when Hammerless writes the entity, if Blender facing is to match.
5. **Tests**, in the style of what was done here: compile a tiny rig and decode the .mdl. Assert:
   - bone quaternions match Blender `matrix_local` (with the turn)
   - events and cycles, hitboxes, attachments, include strings
   - a root-anim track with the +90 baked in, and vertices untouched
   - bone limits (≤127, warn at 53 per strip)

   The test scripts in [model_tools/](model_tools/) (copied from the research scratch folder) (`make.py`, `readmdl.py`, `readvvd.py`, `readanim.py`, `many.py`, `summ.py`, `vpkx.py`) are a ready starting point.
6. **Game tests still needed:**
   - Event 1100 → OnUser1 on prop_dynamic in L4D2.
   - AE_CL_CREATE_PARTICLE_EFFECT in L4D2.
   - A new mesh with `$includemodel infected/anim_common.mdl` played as a prop_dynamic (`SetAnimation` with e.g. a run sequence), and as an actual common-infected model swap.
   - Whether `$origin ... -90` can replace manual counter-rotation.
   - Unmatched-bone behaviour.
