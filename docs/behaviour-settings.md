# Exposed behaviour settings

Add **Values > Exposed Setting** in a behaviour graph. Choose its label, type and graph default, then wire Value into another node. Supported controls: number, integer, checkbox, text, custom dropdown, vector and RGB color. Their values are fixed for each object when the map is exported; they can feed ordinary value logic.

Dropdown choices use `id=Label; other_id=Other Label`. Set the default to a choice ID. Keep IDs when changing labels or order: object selections are stored by ID. Removing a selected choice reports an error until that object chooses another value or resets the control.

Select an object to edit its controls under **Selected Object > Behaviours**. Each attached graph has its own group of controls. The reset arrow returns one value to the graph default. Defaults follow graph changes until overridden. Duplicating an object copies its overrides into independent storage while sharing the graph; linked mesh duplicates also have independent object settings. Copy to Selected copies the values as well as the graph reference. Make Unique preserves setting IDs and current values.

The graph stores setting definitions. The object's behaviour slot stores values, keyed by stable setting IDs rather than labels or object names. Draw code only reads values. Graph assignment, schema edits, load and the explicit refresh operator initialize missing controls. Deleted definitions leave dormant values so restoring a definition can recover them; they are not drawn or exported. Reused nested group definitions expose their setting once per behaviour slot.

**Purchase - Any Weapon or Item** now exposes Item, Price, Display Name and Use Range. Change those controls on the object without making a unique graph. Purchase inputs use exposed settings at export; they do not support a changing runtime price. Old purchase graphs keep their node-property defaults and can be upgraded by wiring Exposed Setting nodes into Purchase inputs. Add the updated example to get the new interface; existing saved example graphs are not silently rewritten.

Validation includes independent station exports from a shared graph, ordinary value-node consumers, dropdown reorder/rename, graph-default inheritance, graph copying, save/reload, actual Object Mode duplication, dropdown edits, undo/redo and delete/undo. No game launch is needed for these authoring/storage checks; this does not retest the previously added purchase delivery runtime.

## Purchase model preview

Purchase behaviours have a per-object **Match Model to Item** toggle, enabled by default. Selecting a catalog item updates that object's model keyvalue and Blender preview mesh, preserving its transform. The mesh pointer is replaced only on that object, so duplicates sharing the old mesh remain unchanged. Disable the toggle for custom display props; enabling it again matches the selected item. Auto from Display Model leaves the model alone. Unknown/custom items have no inferred model. Price/name edits do not replace the display.

Model updates use the existing game-model preview loader. They apply to a Purchase node whose Display Object is Self (or explicitly the owner); references to other objects are not followed. Real mesh changes, duplicate isolation, reset, opt-out/re-enable and undo/redo were verified in Blender.

Purchase displays export with compensation for the model's rigid idle pose when using a real bind-pose model preview. The correction is derived from the displayed model's animation and weighted bones, including any translation, and composed in local space so tilted displays retain their placement. This covers the 48 stock purchase items. Placeholders, mismatched preview meshes, and deforming animations are left unchanged. No scene transforms or shared mesh data are modified.
