# Preset objects and gameplay collections

Single-part presets are now the actual mesh or entity object. Select that object
to edit its preset fields, entity settings and behaviours. Duplicating it copies
its settings; no extra parent needs to be selected or deleted. Model-based point
entities already store settings on their visible preview object.

Multi-part assemblies retain a shared transform parent so their parts can move
together. Position-only entities may still use empty representations. This
change does not flatten imported brush entities or dismantle assemblies.

Automatic organization uses these collections under the map:

- Gameplay / Survival: Survival Arena and Survival Start Button.
- Gameplay / Spawn Areas: Zombie Spawn Area.
- Gameplay / Navigation: ladders and zombie climbs.
- Gameplay / Triggers: trigger entities.
- Gameplay / Assemblies: multi-part presets.

Settings and logic belong to objects; these organizational collections do not
own or resolve their behaviour graphs.

For an older map, **Settings > Map Defaults > Convert Presets to Objects** removes
known single-part preset wrappers and organizes presets, including hidden ones.
The actual child object and its mesh, keyvalues and outputs are retained.
References to the old wrapper are redirected to the object. World placement and
hidden state are preserved. Conversion is undoable, and repeated runs do not
create new objects. Save the file to keep the conversion.

Linked, animated, constrained, shared-across-scenes or customized wrappers that
cannot be safely handled are left intact and counted in the operator report.
Ordinary map opening does not automatically delete or convert objects.
