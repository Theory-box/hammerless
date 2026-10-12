# Animated models

A Custom Model whose mesh has an **Armature modifier** is built as an animated model. There are two ways to make one.

## 1. Your own skeleton

Use any armature you rig yourself.

- **Bones:** the armature's *deform* bones become the model's bones. Vertex groups named after the bones are the weights; each vertex keeps its 3 strongest bones.
- **Animations:** every action of the armature becomes an animation named after the action. That means its current action plus the actions on its NLA tracks. To give the game a different name than the action's, add an `hl_name` custom property to the action. Turn on **Cyclic Animation** in the action's settings for a loop.
- **Attachments:** an empty parented to a bone (Parent: Bone) becomes an attachment point, for lights, effects or `SetParentAttachment`.
- **In the map:** the model is a **prop_dynamic** that plays `idle` (or its first animation) on its own. Logic plays the others: a Fire Input or Object node sends `SetAnimation` (once) or `SetDefaultAnimation` (from then on).
- **Hitboxes:** made automatically, one per bone.

See **Examples 3 > Swelling Pod**: its pod (mesh, armature, an `idle` and a `swell` action) is made by the example, and its behaviour is a node group.

## 2. One of the game's skeletons

Start from one of the game's characters: common infected, a survivor, the Hunter, Smoker, Boomer, Tank or Witch.

1. **Add > Game Skeleton** gives an armature with that character's bones. It stands facing −Y, as in the game's files, so the model faces Blender's −Y.
2. With the armature selected, the **Game Skeleton** box in the object panel offers:
   - **Import Game Mesh:** the character's own body (choose a body / head variant), weighted, with its materials. Edit it into your own creature.
   - **Add Mannequin:** a stick figure, a box per bone, to try things with.
   - **Load Game Animation:** preview any of the character's animations in Blender. Previews aren't built: the game already has them.
3. Weight your mesh to the skeleton's bones, give it an Armature modifier, and make it a Custom Model.

At build, the model borrows the whole animation library of that character (the commons' ~400, a survivor's ~900). It also gets the character's real hitboxes (head, chest, arms, legs), attachments and IK. Your own actions on the armature are added as extra animations.

**On the infected themselves:** the game's own entities can wear the model. **Examples 3 > Reskinned Commons** puts every common infected in a mannequin with For Each and Set Model; they walk, sit, attack, flinch and die as commons do. A survivor bot can wear a model on a survivor skeleton the same way (tested: it plays the survivors' animations and holds its gun).

## 3. Placed characters

**Add > Placed Character** puts one of the game's characters in the map playing one of its own animations in a loop: sitting, lying, leaning, sitting in a chair, banging on a door, a death pose... Pick the animation from the list in the object panel; the preview in Blender shows the real character at the **Pose Frame** you choose. In the game it's the game's own model (nothing to compile), solid to shots.

Give them behaviour with logic: **Examples 3 > Sleepers** (they wake when disturbed, stand up and become real commons that attack) and **Husks** (one hit and they fall). Both find their placed characters by name (sleeper..., husk...), so naming more placed characters that way adds them.

## Notes

- Animated models are compiled by the game's own studiomdl (bin/studiomdl.exe from the L4D2 Authoring Tools). Our own model writer still makes the static props.
- A mirrored (negative-scale) animated model is built still, with a warning. Apply the scale.
- A game skeleton keeps the game's size; scale the mesh, not the armature.
- Collision is the mesh's rest shape (convex pieces) or none. A model on the common skeleton gets the commons' ragdoll from the game's animation library.
- Developer notes and research (the game's model formats, the decoder): docs/dev/research.
