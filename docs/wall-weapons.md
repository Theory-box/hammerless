# Item purchases

Add **Personal Kill Points** once per map, then add **Purchase - Any Weapon or Item** from the logic editor examples. The example creates a display prop with its own Behaviour.

Select the prop and edit **Item**, **Price**, **Display Name**, and **Use Range** under **Selected Object > Behaviours**. The catalog has 48 carried items: firearms, melee weapons, health supplies, throwables, upgrade packs and carryables. Use Range is measured in game units. Changing Item updates the display model by default; disable **Match Model to Item** to keep a custom display. **Auto from Display Model** infers the item from a supported world model.

Place the prop just outside the wall. Players looking at it nearby see its price, their personal balance, and a **Press USE to buy** prompt. The game uses the player's Use binding. Holding Use does not repeatedly buy. Dead, incapacitated, pinned and bot survivors cannot buy. The non-solid display is not a free pickup.

A purchase reserves points, gives the item, and confirms it in inventory. Failed delivery refunds the cost. Insufficient funds or already carrying that item does not charge. Melee confirmation also checks the model, so carrying one melee type does not block buying another. Normal inventory-slot replacement rules apply; ammo refills are not implemented.

Duplicate the display to create another station. The generic example shares its graph but stores settings independently on each object. Deleting the object removes that station. Stations do not create points controllers; purchases are unavailable without one.

The **Wall Weapon - Uzi / M16 / Pump Shotgun** examples use the same editable groups and per-object settings. Open the behaviour and press **Tab** on **Purchase: Rules** to see the prompt, inventory check, balance check, deduction, delivery and refund nodes. There is no special-purpose Purchase node in new examples.

Opening an older file expands its former Purchase nodes into ordinary nodes and groups. Existing exposed-setting IDs and object overrides are preserved; values formerly stored directly on a shared node remain shared graph values. The file on disk is unchanged until saved. Linked library graphs retain their legacy nodes.

For advanced graphs, **Custom Item** on **Inventory Item** accepts a supported GiveItem ID. Disconnect the exposed Item input to use the node's own choice. Inventory Class defaults to weapon_ plus that ID. Override it and optionally Inventory Model for custom melee or other shared-class items. Health/ammo commands and arbitrary map entities are not inventory purchases. Availability depends on installed game content and rules.

**Inventory Item**, **Has Inventory Item**, **Give Inventory Item**, and **Look / Use Target** are reusable outside purchases. Look / Use Target selects the nearest reachable target and supplies Focused/Used events and the human player. Give Inventory Item reports Delivered or Failed after checking inventory; it knows nothing about points. The example graph handles deductions and refunds. Deferred outputs retain the initiating player's context, including on disconnect.

Purchase prompts use the larger private instructor-hint display, including the player's actual Use key. **Messages: Purchase Hint** is an editable group of existing Make Table, Spawn Entity From Table, and Fire Input nodes. Temporary hints expire and are removed; looking away stops refreshing the prompt. No new special-purpose node is required. Instructor hints must be enabled in the player's game settings.

**Messages: Purchase Prompt** stores and clears the current prompt per player using ordinary variable and entity-input nodes. The Look / Use Target node's **Lost Focus** output clears it when aiming away, leaving reach, becoming unable to interact, or attempting Use. Feedback uses its own hint so it remains visible after purchase. Buttons are sampled every server tick; short taps are buffered for 0.2 seconds, and an unconsumed held press can activate once a target qualifies. One attempt per hold prevents repeat charges. Solid targets use surface hits; non-solid weapon display props retain a forgiving centre aim cone.

The Blender adapter compensates for rigid idle-pose transforms in real model previews. This keeps the exported display aligned with Blender without altering object transforms or shared mesh data. Placeholder previews and deforming animations are not corrected.

Validation covers all 48 catalog exports and pose alignment, independent duplicate settings, undo/redo, save/reload, preview updates, and stock Survival integration. The user has confirmed gameplay in their map; exhaustive multiplayer delivery and every catalog item's gameplay behavior have not been tested.
