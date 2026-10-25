# Emergency knife

Add **Purchase - Emergency Knife** from the logic examples. Add **Personal Kill
Points** once per map to enable purchases. The knife display carries its own
behaviour; set Price, Display Name and Use Range in Selected Object > Behaviours.
The default price is 500 points.

The shop exposes **Escape Smoker**, **Escape Hunter**, **Escape Jockey**, and
**Escape Charger (Pummel Only)**. Smoker and Hunter default to on; Jockey and Charger default
to off. Each purchased knife
remembers its shop's choices. Mixed stock uses the first knife compatible with
the current enemy; incompatible knives are kept. Turning every enemy off makes
grants fail and purchases refund their payment. The two-knife cap still applies
across all shops. Add a fresh example to get the settings on an older shop.

Look at the display and press Use to buy one knife. Each human survivor can carry
two across all shops. These are consumable escape charges, not melee inventory
items, so buying one does not replace a weapon. Duplicating a shop gives it
independent settings without creating another inventory or increasing the limit.

When an enabled enemy pins you, hold your Reload action for one
second. Release it early to cancel. The attacker is staggered using the game's
native action, and one knife is consumed after the survivor is confirmed free.
A failed release keeps the knife. Holding Reload before the pin starts requires
releasing and pressing it again, preventing accidental consumption. It does not
revive an incapacitated survivor or kill the attacker. Charger escape also
staggers the survivor to invoke the engine's pin-release cleanup. It works only
during ground pummels; charging/carrying cannot be interrupted safely with these
native actions. Knives do not activate or get consumed during a Charger carry.

Stock clears on death, disconnect, map change, round end, and round restart. Bots
do not buy or use knives. Prompts and counts are private to the relevant human.
No external Workshop addon or SourceMod plugin is required.

## Editable logic

The object behaviour references **Emergency Knife: Purchase**, which uses the
existing points balance, comparisons, payment/refund, interaction, and message
nodes. **Emergency Knife Stock** is a reusable action node: Give One emits Given
or Full / Unavailable, and Carried reads the current player's stock. It handles
the two-charge inventory and hold-to-escape runtime without charging any points.

The runtime is generated once per map even when several objects use the node.
All stores therefore share the same player's stock. Pin state is never released
by directly clearing netprops; native Stagger handles the game's cleanup.

## Verification

Run `tests/blender/test_emergency_knife.py` in background Blender to check example
creation, duplicated settings, shared runtime generation, and save/reload.
Run `tests/ingame/test_emergency_knife.py` with Blender's Python to exercise the
compiled purchase graph and real Smoker/Hunter/Jockey/Charger pins on c2m1_highway. The latter
temporarily loads a stock map, removes survivor bots from that test session,
uses test-only console settings, and returns the game to its menu afterward.
It does not edit the user's blend file or built map.
