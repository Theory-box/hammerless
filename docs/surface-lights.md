# Area lights and material emission

Area lights now bake as distributed, one-sided Lambert emitters. Add one from
**Hammerless → Add → Lights → Area Light**, or Blender's normal Add Light menu.
Color, Power, Shape, Size, Size Y, object scale and rotation affect the bake.
Local **-Z** is the emitting direction. Power is total power: increasing size
spreads the same power over a larger surface. Rectangle/square and disk/ellipse
shapes are sampled automatically (up to 256 samples per light).

Connected Principled BSDF **Emission Color / Emission Strength** and Emission
shader **Color / Strength** automatically emit from evaluated mesh faces. No
separate enable switch or `.rad` editing is needed. Disconnected nodes do not
emit. Zero strength stops emission. Emission is one-sided along the face normal.
It works on brushes, terrain and custom-model meshes, including mesh modifiers
and collection instances. Moving models leave their baked illumination at their
starting position; export reports this.

Supported emission inputs include constant colors/values, UV image textures,
Mapping, Color Ramp, basic Math and Mix Color operations, shader Add/Mix, groups
and reroutes. More elaborate procedural graphs are not rendered/baked to textures
by this exporter: unsupported nodes produce an export warning instead of silently
using socket defaults. Without UVs, texture emission uses an average color.

Game materials marked `$selfillum` also emit, with their base texture, alpha or
self-illumination mask, and tint. Existing entries in the game's `lights.rad`
continue to use VRAD's native texture-light path to avoid doubling that light.

Emission Strength is treated as linear radiance, so a larger emissive surface
emits more total light at the same strength. Mesh emitters use a deterministic
area-weighted sample budget, capped at 2,048 samples per object. Tiny textured
features and very large emitters may need finer sampling in a future revision.

Samples export as bake spotlights with hemispherical cosine falloff. Both CPU
and GPU VRAD paths support them. Samples add no collision or visible geometry.
Rebuild lighting after changes. Surface emitters are excluded from Live Light
Mixing to avoid creating hundreds of layers per object; they remain in the
normal baked lighting.
