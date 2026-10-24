# Item purchases

Add **Personal Kill Points** once per map, then add **Purchase - Any Weapon or Item** from the logic editor examples. The example creates a display prop with its own Behaviour.

Select the prop and edit **Item**, **Price**, **Display Name**, and **Use Range** under **Selected Object > Behaviours**. The catalog has 48 carried items: firearms, melee weapons, health supplies, throwables, upgrade packs and carryables. Use Range is measured in game units. Changing Item updates the display model by default; disable **Match Model to Item** to keep a custom display. **Auto from Display Model** infers the item from a supported world model.

Place the prop just outside the wall. Players looking at it nearby see its price, their personal balance, and a **Press USE to buy** prompt. The game uses the player's Use binding. Holding Use does not repeatedly buy. Dead, incapacitated, pinned and bot survivors cannot buy. The non-solid display is not a free pickup.

A purchase reserves points, gives the item, and confirms it in inventory. Failed delivery refunds the cost. Insufficient funds or already carrying that item does not charge. Melee confirmation also checks the model, so carrying one melee type does not block buying another. Normal inventory-slot replacement rules apply; ammo refills are not implemented.

Duplicate the display to create another station. The generic example shares its graph but stores settings independently on each object. Deleting the object removes that station. Stations do not create points controllers; purchases are unavailable without one.

The older **Wall Weapon - Uzi / M16 / Pump Shotgun** examples remain available. They store price and range directly on the Purchase node, so shared copies of those older graphs share settings. Add Exposed Setting nodes or use the generic example for per-object controls. Existing saved graphs are not silently rewritten.

For advanced graphs, **Custom Item** on the Purchase node accepts a supported GiveItem ID. Inventory Class defaults to weapon_ plus that ID. Override it and optionally Inventory Model for custom melee or other shared-class items. Health/ammo commands and arbitrary map entities are not inventory purchases. Availability depends on installed game content and rules.

The Blender adapter compensates for rigid idle-pose transforms in real model previews. This keeps the exported display aligned with Blender without altering object transforms or shared mesh data. Placeholder previews and deforming animations are not corrected.

Validation covers all 48 catalog exports and pose alignment, independent duplicate settings, undo/redo, save/reload, preview updates, and stock Survival integration. The user has confirmed gameplay in their map; exhaustive multiplayer delivery and every catalog item's gameplay behavior have not been tested.
