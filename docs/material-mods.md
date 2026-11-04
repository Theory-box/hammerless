# Material Mods

The Hammerless sidebar's Material Mods panel stores replacements in the blend.
Select a mesh with source-compatible UVs, then Create Texture Mod from Selected.
The importer records `hl_source_material` on materials and `hl_source_materials`
on meshes; names can change without changing the target. For older/unmapped
materials, select each material slot and fill Original Game Material once.

Every replacement is global: other models and surfaces sharing the game material
change too. Enable/disable entries, remove entries, or change their replacement
material in the list. Build / Install writes this blend's managed VPK under the
game's addons directory. Normal map Build / Play also updates it before launch.
Rebuilding with no enabled entries leaves an empty addon and removes all of this
mod's overrides. Restart the game to reload materials. Other installed addons
may still replace the same paths.

The first version accepts direct image textures connected to Principled Base
Color, and optionally a tangent-space Normal Map node at Strength 1. Normal
images must use Non-Color. It does not bake procedural graphs or transfer UVs.
Color PNGs retain their encoded color values; Blender normal green is converted
to Source orientation. Original diffuse alpha masks are preserved. Source shader
settings, proxies and wound/burn parameters are retained.

Infected normal maps require Shader Detail High. If the original material has no
normal map, Infected Normal Gloss supplies an approximate normal-alpha shine
mask; it is not a calibrated roughness conversion. Roughness and metallic maps
are not converted in this version. Read the Hammerless Material Mod Report text
block for all build warnings.

Generated textures and packages live beside the blend under hammerless_mods.
Do not install a second copy under another filename: that creates competing
replacements that this blend cannot remove. Save As retains the mod identity,
so copies of a blend update the same managed addon.
