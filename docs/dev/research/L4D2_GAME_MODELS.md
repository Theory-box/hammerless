# L4D2's own animated models: what the game files contain

Research for animated Custom Models (2026-10-12). Everything was read from the installed game VPKs (left4dead2, update, dlc1–3), read only. **(uncertain)** marks guesses. Quaternions are (x,y,z,w); units are game inches.

The readers are in [model_tools/](model_tools/):
- `mdlparse.py` is the MDL v49 reader and animation decoder. It decodes RLE and L4D2's frame-anims, with sections and .ani blocks.
- `gamefs.py` opens the game's VPKs through `hammerless/core/vpk.py`.
- `dump.py <mdl>` writes a text dump of one model.
- `survey.py` produced `survey.txt`: 160 models, with their bones, skeleton hash, sequence counts and includes.
- `listmodels.py` lists the game's model files.

Compiling our own models is covered separately, in [ANIMATED_MODELS_COMPILE.md](ANIMATED_MODELS_COMPILE.md).

## What exists
- **models/survivors/**: the survivors are gambler (Nick), producer (Rochelle), coach, mechanic (Ellis), namvet (Bill), teenangst (Zoey), biker (Francis) and manager (Louis).
  - `survivor_<name>.mdl` (+ .vvd/.dx90.vtx/.phy) is the mesh. It has 77–88 bones, ~112 flexes and 2 local poses (`@<name>`, `@ragdoll`).
  - `anim_<name>.mdl` + `.ani` is the animation library: 69–79 bones, no mesh, ~800–900 sequences.
  - `gestures_<name>.mdl` + `.ani` holds ~105 gesture sequences.
  - Nick's model ends up with 923 sequences in game.
- **models/infected/**: ~55 `common_*` meshes, which have **no sequences of their own**. They include `anim_common` (+`anim_common_vomit`, +`anim_common_male_exp`) or `anim_common_female` (+`_female_exp`).
  - Specials (hunter, smoker, boomer, charger, jockey, spitter, hulk = Tank, witch) each include their own `anim_<special>` library.
- **models/v_models/**: `v_<weapon>.mdl` holds the weapon mesh and its own animations (ACT_VM_*).
- **models/weapons/arms/**: `v_arms_<survivor>.mdl` is the arm mesh with one idle frame. It is bone-merged onto the weapon viewmodel by name **(inferred)**.

## Skeletons
- **Survivors**: all 8 share a 69-bone `ValveBiped.*` core.
  - The core: Pelvis root; thighs, calves, feet and toes; Spine, Spine1, Spine2, Spine4; Neck1 and Head1; clavicles, arms and hands; 15 finger bones per hand; and the weapon and attachment bones.
  - Each survivor adds procedural helper bones (`hlp_*`, proctype 2) or jiggle bones (proctype 5), up to 88 bones.
  - Hitbox set "L4D" has 17 boxes. Hitgroups: head and neck 1, Spine2 2, Pelvis and Spine1 3, left arm 4, right arm 5, left leg 6, right leg 7.
  - 29 attachments: eyes, mouth, weapon_bone, medkit, pills, muzzle_flash, survivor_light, forward, … The game looks these up by name, so a survivor-like model must keep them.
  - IK chains: rhand, lhand, rfoot, lfoot. Pose params in the library: body_pitch/yaw, head_pitch/yaw, move_x/y, …
- **Commons**: 44 of ~55 share one 33-bone skeleton: 29 ValveBiped body bones plus 4 `Exp_` face bones.
  - The newer female commons have 39 bones.
  - `anim_common` has the 29 body bones and 407 sequences (idles, alerts, sitting, crawling, …).
  - Hitbox set "common_male" has 23 boxes.
- **Specials**:

  | model | bones | rig |
  |---|---|---|
  | hunter | 53 | ValveBiped (all 53 shared with survivors) |
  | smoker | 32 | ValveBiped + jiggles |
  | boomer | 28 | ValveBiped |
  | Tank (hulk) | 56 | ValveBiped, some bones renamed |
  | witch | 67 | ValveBiped + hair jiggles |
  | charger, jockey, spitter | 38–69 | `bip_*`, no names shared with survivors |

## How animation is stored
- **RLE (Source SDK 2013)** is used only for mesh reference and ragdoll poses, and for weapon viewmodels.
- **Frame-anims** cover 96% of L4D2 animations (24,249 of 25,174). This is flag 0x40, which is not in the 2013 SDK. Its layout was measured:
  - A header of three ints: `constantsoffset`, `frameoffset`, `framelength`.
  - One flag byte per bone, then constants and per-frame data, with rotation read before position.
  - **Flags**:

    | flag | meaning |
    |---|---|
    | 0x01 | constant Vector48 |
    | 0x02 | constant Quaternion48 |
    | 0x04 | per-frame Vector48 |
    | 0x08 | per-frame Quaternion48 |
    | 0x40 | constant Quaternion48S |
    | 0x80 | per-frame Quaternion48S |
    | 0 | bone not animated (reference pose) |

  - **Quaternion48S** (smallest three): each value is (c & 0x7fff − 16384)/23168. The largest component is (x,y,z,w)[(dc+3)%4] **(measured; dc 3 inferred)**.
  - The decoder matched the expected byte count on every animation in 7 libraries, with 0 failures. Poses look right: feet on the floor, smooth across sections, loops exact.
- **.ani files** hold the data in blocks. **Sections** are 30 frames each.

## Coordinate frames (important for an importer)
- **Bone rest data**: a bone's pos/quat is the bind pose (equal to inverse poseToBone). In that space characters face **−Y**.
- **Animations**: these are in bind space turned **+90° about Z**, carried by the root bone only. In game, characters face +X.
- **Rest poses**: an animation library's bone defaults are not a usable rest pose. Take skeletons from the mesh model.

## How models get their animations
- **Includes**: a model lists `$includemodel`s, and the game merges their sequences. Bones map by **name**; order may differ, and extra bones (helpers, jiggles, face) are fine.
- **Choosing sequences**: game code picks sequences by activity name (ACT_IDLE_RIFLE, ACT_TERROR_IDLE_ALERT, …).
- **A new model** therefore only needs `$includemodel survivors/anim_gambler.mdl` (+ gestures) and matching bone names.

## Open questions (need game tests)
1. Most survivor library bones animate rotation only. Does a custom mesh keep its own bone lengths? Test with a long-legged model that includes anim_gambler.
2. Do Francis's and Louis's ~997 empty placeholder sequences matter? They're probably there to pin the sequence order.
3. Ikrules, localhierarchy, the zero-frame cache and jiggle rules are not decoded. They aren't needed to play existing animations on a new mesh.

## Measured in game tonight (cheats off)
- **Placed game models**: a `prop_dynamic` with a common's model has its 421 sequences, and one with Nick's model has 923. `SetAnimation` / `SetDefaultAnimation` play any of them by name, and they loop. `GetBoneOrigin`, `LookupBone` and `LookupAttachment` work from script.
- **"Puppet" trick**: Nick's model on a `prop_dynamic` plays `Idle_Tongued_Dragging_Ground` (the Smoker drag), so a survivor-like puppet can show any survivor animation.
- **Damage**: bullets hitting a `prop_dynamic` reach the scripted-mode `AllowTakeDamage` hook, with the hit point in `Location`.
