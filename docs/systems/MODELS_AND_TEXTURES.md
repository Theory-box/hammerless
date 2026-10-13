# Models and textures

How Hammerless turns Blender meshes, armatures and images into the game's model and texture files, how it reads the game's own models and materials, and where the results go.

For the step-by-step user guide to animated models, see [Animated Models](../ANIMATED_MODELS.md). This page explains how the system works underneath, what was measured, and what is left.

## Overview

| Part | What it does | Done by |
|---|---|---|
| Static, dynamic and physics props | A mesh becomes `.mdl` / `.vvd` / `.dx90.vtx` / `.phy` | Hammerless's own model writer (default), or the game's studiomdl |
| Animated models | An armature, weights and actions become a model with sequences | SMD + QC written by Hammerless, compiled by the game's studiomdl |
| Game skeletons | Borrow a game character's bones, animations, hitboxes and IK | Hammerless reads the game's `.mdl` / `.ani`; studiomdl compiles |
| Placed characters | A game character posed in the map | The game's own model; nothing is compiled |
| Your own textures | Blender image textures become `.vtf` + `.vmt` | Hammerless's VTF writer |
| Game materials and models | Browse, preview and use the game's 8,000+ materials and 5,500 models | Read straight from the game's VPKs |

## Custom Models

Any mesh can be a game prop. Set the object's role to **Custom Model** and pick the **Prop** kind:

| Prop | Entity | Model built as |
|---|---|---|
| Static | `prop_static` | `$staticprop` |
| Dynamic | `prop_dynamic` | `$staticprop` (one compile serves both) |
| Physics | `prop_physics` | no `$staticprop`, plus a `prop_data` block naming a physics class |

Two things were measured in the game before the writer was built:

- A model compiled with `$staticprop` works for both `prop_static` and `prop_dynamic`.
- A `prop_physics` needs a model *without* `$staticprop`, and a `prop_data` block naming a class from the game's `scripts/propdata.txt` (Wooden.Small and so on). Without one, the game deletes the prop at once.

**Physics Class** lists the game's own classes; it sets the weight feel and health. **Mass** is in kg; 0 works it out from the collision volume times the surface's density, as studiomdl does.

Copies that share a mesh (Alt+D) share one model. A mesh with modifiers on gets its own model per object, since modifiers can make copies differ. Model space is the object's own axes, scaled to Hammer units.

### Collision

**Collision** is either *Convex Pieces* or *None*:

- **Convex Pieces:** each loose part of the mesh is wrapped in its convex hull. Several parts make a concave model (`$concave`).
- **None:** players and zombies walk through it. Not offered for physics props, which always get hulls.

Guards in the build:

- A flat part (thinner than half a unit) has no inside, so it gets no collision.
- A collision piece with more than 4,096 triangles is left out with a warning. The `.phy` format numbers triangles in 12 bits, so past that the file comes out broken.
- A physics prop with no solid part at all is warned about: the game removes a physics prop it can't simulate.
- A mesh over 65,535 vertices is an error.

## Our own model compiler

Hammerless writes static, dynamic and physics props itself. No studiomdl or Authoring Tools are needed for them. *Settings > Map & Model Compilers > Model Compiler* can switch back to studiomdl.

### What it writes

The structures come from Source SDK 2013's public `studio.h` and `optimize.h`, read as the game's 32-bit on-disk layout. The layout within each file was measured from studiomdl's own output for a one-bone static prop.

| File | Version | Contents |
|---|---|---|
| `.mdl` | 49 (L4D2) | Header, bone, hitbox set, one animation and sequence (`idle`), body part, model, meshes, textures, material folder, string table |
| `.vvd` | 4 | Vertices (48 bytes each: weights, position, normal, UV) and tangents |
| `.dx90.vtx` | 7 | Triangle lists: one strip group per mesh, clockwise indices |
| `.phy` | VPHY + IVP | One solid made of convex pieces ("ledges") |

Details that had to match studiomdl:

- **Tangents** are worked out per triangle, normalized, then summed per vertex, the way studiomdl does it.
- **Texture V** runs top-down in the file (1 minus Blender's v).
- **The movement hull** and sequence box are the mesh's box grown by 0.25 units (measured).
- **Axes.** studiomdl turns every SMD it reads 90° about the vertical axis (measured on the stored vertices of a compiled model). Our writer uses Blender's object axes directly, and when studiomdl is used Hammerless writes the SMDs turned back. Either way the model faces the way it does in Blender.

### The collision file

The `.phy` is Valve's VPHY wrapper around an IVP "compact surface". What was matched against studiomdl's output:

- IVP space is metres, with axes (x, −z, y) of model space.
- Each piece is a 16-byte ledge header, 16 bytes per triangle (its index, the triangle a ray through it meets on the far side, then three edges pointing to their twin edges), then 16 bytes per point.
- Tree nodes hold the bounding sphere of their pieces, with box half-sizes stored as 250ths of the radius (rounded up).
- The surface keeps the mass centre and the largest distance from it.

One known difference: IVP's own approximation of rotational inertia isn't public, and it isn't the solid formula (measured ratios of 0.94 / 0.82 / 0.82 for two shapes, 0.71 for a cube). Hammerless stores the true inertia of the shape. The game uses it only for how physics props turn.

### How it was verified

Each file was decoded field by field with small readers written from the SDK headers, and compared with studiomdl's output for the same mesh:

- Vertices, normals, UVs and triangles identical.
- Tangents matching: 548 of 555 on a smooth test mesh, the rest differing in the last digit.
- The same `.mdl` sections.
- Collision files with the same layout, sizes, mass centre, bounding spheres and edge structure.

In the game: static props are solid (multi-piece too), physics props fall and settle, the nav goes around them, and all of them draw like studiomdl's. A lighting build with vrad lit them as well.

### Rebuilds

Each model is fingerprinted from its source data and the model writer's own code. A model is rewritten only when one of those changed, so a fixed writer remakes old models. Model files the map no longer uses (a renamed or deleted Custom Model) are removed from the map's model folder.

## Animated models

A Custom Model whose mesh has an **Armature modifier** becomes an animated model. Our own writer handles static props only, so animated models go through the game's `bin/studiomdl.exe` (from the Authoring Tools). Without it, only the animated models are left out of the build, with an error saying why.

Hammerless writes Valve's SMD text and a QC script:

- **Bones:** the armature's deform bones, up to 128.
- **Weights:** vertex groups named after the bones, the 3 strongest per vertex (studiomdl's limit).
- **Animations:** every action of the armature (its action plus the actions on its NLA tracks), each sampled from the rest pose. An `hl_name` property on an action renames it. *Cyclic Animation* makes it loop; the repeated last frame is dropped.
- **Attachments:** empties parented to a bone become `$attachment`s.
- **Hitboxes:** one per bone, made automatically.
- **In the map:** a `prop_dynamic` with `DefaultAnim` set to `idle`, or the first animation.

**The 90° turn again.** studiomdl bakes +90° about Z into the root of every animation for non-static models. Hammerless counter-turns vertices and root bones by −90° so the model's axes stay Blender's. Child bones follow through their parents.

Mirrored (negative-scale) animated models are built still, with a warning. Apply the scale.

**Tested in game:** the Swelling Pod example plays its idle and swell animations; walking up makes it swell, grow, burst for −20 HP and disappear; shooting it sets it off.

## The game's own models and skeletons

To reuse the game's characters, Hammerless reads the game's model files directly (read only, from the installed VPKs).

### What was decoded

- **Skeletons, sequences, hitboxes, attachments, IK chains and pose parameters** from `.mdl` v49 and `.ani` files.
- **Frame-anims.** 96% of L4D2's animations (24,249 of 25,174) use a format that isn't in the 2013 SDK (flag 0x40). Its layout was measured: a three-int header, a flag byte per bone, then constants and per-frame data. Rotations use a "smallest three" 48-bit quaternion. The decoder matched the expected byte count on every animation in 7 libraries, with no failures.
- **Meshes** from `.vvd` and `.dx90.vtx`, through the `.vvd` fixup table for LOD 0. The `.vtx` stores front faces clockwise, so triangles are reversed for Blender; then face normals agree with vertex normals on 99.95% of a common's faces and 99.8% of Nick's.

Not read: flexes (facial morphs), eyeballs, skin families other than the first.

### What the game's characters contain

| Character | Bones | Animations |
|---|---|---|
| Survivors (8) | 77–88 (a shared 69-bone core) | ~800–900 in `anim_<name>`, plus ~105 gestures |
| Common infected | 33 for 44 of ~55 commons (39 for newer female ones) | 407 in `anim_common`; the meshes have none of their own |
| Hunter, Smoker, Boomer, Tank, Witch | 28–67, ValveBiped rigs | Their own `anim_<special>` library |

Models get their animations through `$includemodel`. The game merges the included sequences and maps bones by name, so a new mesh with matching bone names gets the whole library.

### Game skeletons

**Add > Game Skeleton** builds an armature for one of 11 characters. It stands facing −Y, as in the game's files. The exact rest pose is taken from the game's model at build time, not from Blender's edit bones (a Blender bone can't hold every orientation a game bone has). The commons' stored rest pose lies flat, so the first frame of an idle is used, turned upright.

At build, a model on a game skeleton:

- is compiled in the game's own space (no counter-turn), and the prop is turned a quarter to match;
- includes the character's animation libraries in the game's order;
- copies the game's hitboxes, attachments and IK chains;
- keeps your own actions as extra animations.

Its materials ignore the entity's tint (`$allowdiffusemodulation 0`). An infected tints whatever it wears with random colours; on a plain material that came out bright blue or white.

**Import Game Mesh** brings in the character's own body, weighted, with its materials used as they are (the commons keep their Infected shader). **Load Game Animation** previews any of the character's animations in Blender; previews aren't built, since the game already has them.

**Proven in game:**

- Our compiled model's bones, hitboxes, attachments and includes equal the game's (checked for a common and for Nick).
- Real commons wearing a reskin walk, sit, take a shotgun blast and play their death animation. All ~35 commons on a test map wore it within half a second.
- A survivor bot given a model on a survivor skeleton holds its gun.
- An imported common mesh, rebuilt and worn by commons, looks like the original.

### Placed characters

**Add > Placed Character** puts one of the game's characters in the map, looping one of its own animations: sitting, lying, leaning, banging on a door. It's a `prop_dynamic` of the game's model, so nothing is compiled. It is solid to shots (solid 2) with a large health, so its `OnTakeDamage` output fires without it breaking. In Blender the preview is the real character posed at the chosen frame, decoded from the game's animation.

## Textures

### Your own images

A material with an Image Texture feeding Base Color (through any nodes in between) is converted automatically. Without a Principled BSDF, an image node not wired to anything else is used, as long as it isn't Non-Color.

- **Size:** each side is rounded down to a power of two, at most 2048.
- **Format:** uncompressed VTF 7.2: BGR888, or BGRA8888 when the image has see-through pixels. Full mip chain. These files are about 4–6 times larger than DXT-compressed ones.
- **Colour:** float images are converted to sRGB, and Blender's premultiplied alpha is made straight (otherwise edges go dark in the game).
- **Names:** made safe for the game's file system, with a short hash when that changed the name, so `Wall.001` and `Wall_001` don't overwrite each other.
- **Caching:** an image is reconverted only when its file, size, colour space, surface or alpha mode changed. A repainted texture rebuilds the map.

### Alpha

How the material's Alpha is wired decides the material:

| Blender material | Game material |
|---|---|
| Alpha not connected | Opaque |
| Alpha connected, render method Dithered / Opaque | Cut-out: `$alphatest` (fences, leaves) |
| Alpha connected, render method Blended | Translucent: `$translucent` (glass, see-through decals) |

An alpha channel with nothing actually see-through is written opaque.

### How a material is resolved

For each Blender material, in order:

1. **A game material:** the *Game Material* field, or the material's name if it looks like a game path (`concrete/concrete_floor_01`; `.001` suffixes, `materials/` and `.vmt` are ignored).
2. **A game material with a different Surface:** a small patch material that includes the original and changes only `$surfaceprop` (friction and footsteps).
3. **Your own image:** converted as above, with the material's Surface (default `concrete`).
4. **Nothing usable:** the default material, with a warning.

World brushes use LightmappedGeneric. Custom Models need model materials, so each slot gets a VertexLitGeneric material in the model's folder:

- your own image: its converted texture;
- a world material from the game: its `$basetexture` (patch materials followed to the one they include);
- one of the game's own model materials (anything under `models/`, or a shader such as the commons' Infected): a patch that includes it, so it's used exactly as it is.

### Vertex colour on terrain

Terrain objects become displacements. Painting vertex colour red blends a blend material's two textures; the red channel becomes the displacement's alpha. Byte and float colour attributes give the same blend.

### The game's materials and models

Hammerless reads the game's VPKs directly (`left4dead2`, `update` and the DLC folders, newer ones first so they override), plus loose files in the game folder. Damaged VPKs are skipped.

- **Texture browser:** every L4D2 material with thumbnails, search, folders, favourites and recent, plus Paint Faces and Hammer-style alignment.
- **Viewport previews:** in Material Preview and Rendered view, a game material shows its real texture. VTFs are decoded in Python (DXT1/3/5 and the common uncompressed formats), one mip no larger than 512 pixels. Solid view uses the texture's average colour. Fences, glass and foliage show see-through.
- **Game models:** props using the game's models show the real model, read from `.mdl` / `.vvd` / `.vtx` (LOD 0, first skin).
- **Previews stay out of your file's size:** *Refresh Previews* rebuilds them from your install, so a .blend doesn't need to carry game textures.

## How assets reach the game

Build writes a map's own files into the game folder, per map:

| Files | Where |
|---|---|
| Custom Models | `left4dead2/models/hammerless/<map>/` |
| Their materials | `left4dead2/materials/models/hammerless/<map>/` |
| Converted textures and patch materials | `left4dead2/materials/hammerless/<map>/` |

These folders are fingerprinted with the map: a repainted texture, a new Alpha or Surface, or an edited Custom Model changes the fingerprint, so the map compiles again.

The files are loose in the game folder. That is enough for playing and testing on your own PC. They aren't yet packed into the map or an addon, so a map with custom models or textures can't be handed to someone else as one file yet.

Hammerless already writes addon VPKs (its own game mode is installed as `addons/hammerless_mode.vpk`).

## Sounds

Custom sounds aren't part of the build yet, but the rules for shipping them were measured:

- A **new** `.wav` path fails to play from loose files, from an addon VPK, from the map's pakfile, and with the streamed `*` prefix. The game only plays sounds listed in a `sound/sound.cache`, and at startup it only reads its five stock caches.
- An **addon VPK that carries its own `sound/sound.cache`** plays new sounds (confirmed in game). A second new wav in the same VPK but missing from the cache failed. Every workshop addon checked that adds new sound paths ships such a cache inside its VPK.
- The cache format was decoded from those addons: a small header, then per file its path, size, format flags, sample rate, channels, data offsets and sample count.
- An addon VPK's `scripts/dsp_presets.txt` overrides the stock reverb presets from a fresh game start, so custom reverb presets can ship the same way.

Don't use the `snd_rebuildaudiocache` console command to get around this. It rewrites Steam's own cache files and quits the game, and afterwards stock sounds can come out wrong (a mostly muted shotgun was heard). If it was run, *Verify integrity of game files* in Steam restores them.

## Limits and what's left

- **Packing:** custom models and textures are loose files in the game folder; packing them with the map (pakfile or addon VPK) isn't done.
- **Own writer for animated models:** animated models still need studiomdl.
- **Ragdolls:** no `.phy` ragdoll per bone yet. A model on the common skeleton gets the commons' ragdoll from the game's library.
- **Collision of animated models** is the mesh's rest shape (convex pieces) or none.
- **Faces:** flexes (facial animation) aren't read or written.
- **The survivor "puppet" trick** (a survivor-like prop playing any survivor animation; Nick's model on a `prop_dynamic` played the Smoker drag in a test) isn't a feature yet.
- **Textures** are uncompressed; no DXT, no normal maps from your own images.
- **Physics inertia** is the true inertia, not IVP's approximation.
- **Meshes without UVs** get all-zero texture coordinates.
- **Speed:** our model writer hasn't been timed against studiomdl.

## Further reading

The code lives in `hammerless/core/` (`models.py`, `mdlwrite.py`, `phywrite.py`, `skeletal.py`, `gamemodel.py`, `gamemesh.py`, `textures.py`, `vtf_read.py`, `vpk.py`) and `hammerless/blender/` (`extract.py`, `texturing.py`, `preview.py`, `gameskeleton.py`, `placedchar.py`). Research notes: [L4D2_GAME_MODELS.md](../dev/research/L4D2_GAME_MODELS.md) (what the game's models contain, the decoder) and [ANIMATED_MODELS_COMPILE.md](../dev/research/ANIMATED_MODELS_COMPILE.md) (SMD, QC, studiomdl measurements).
