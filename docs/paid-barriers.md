# Paid barriers

Add **Personal Kill Points** once per map, then add the **Purchase - Remove Barrier** logic example. It creates a brush with a Paid Barrier behaviour.

- Position the brush across a doorway and resize its mesh in Edit Mode.
- In **Selected Object > Behaviours**, set **Price**, **Display Name**, and **Use Range** (game units). Defaults are 1,000 points, “Barrier”, and 128 units.
- Duplicate in Object Mode for another independently purchasable barrier. Each copy has its own settings and purchase state.
- Bake navigation through the doorway. A navigation blocker keeps bots and infected from routing through the closed barrier; purchasing removes both the brush and blocker.

Look toward the barrier's centre and press Use. The price and your balance appear in a large private instructor hint. One human pays, and the opening becomes available to everyone. Insufficient funds do nothing. The barrier returns on a new round/map load.

Tab into **Barrier: Payment** to edit the ordinary interaction, variable, comparison, subtraction, and message nodes. Its **Cleared** event feeds the existing **Show / Hide Object** node's Remove input. A per-instance boolean is set before payment, preventing repeated uses from charging twice while engine removal is queued.

The provided example is for brush barriers. It does not implement repairable barricades or zombie damage. The reusable payment group can drive other actions through Cleared, but changing the mesh to a model alone does not change the brush-removal action.

**Show / Hide Object** now has an optional **Block Navigation While Shown** setting. It is off by default for existing graphs; this example enables it.
