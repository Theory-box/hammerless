# Personal kill points

In the logic editor, choose **Add > Examples 3 > Personal Kill Points**.
This adds an editable **Personal Points** node group. Press **Tab** to enter it;
the kill events, reward arithmetic, balance storage and messages are ordinary nodes.
The **Points: Award** and **Points: Balance** subgroups keep these rules readable.
One controller listens for kills; no external Map Start connection is needed.

Defaults are 0 starting points, 10 per common kill, 50 per special kill,
500 per Tank kill and 250 per Witch kill. Edit these on the node; 0 disables
a reward. The game event's credited killer receives the reward. Environmental
kills without a human survivor attacker and bot kills award nothing.

The killer receives a private centre-screen total. Disable **Show Earned Points**
to suppress these messages. Typing **!points** in chat always replies privately
with the current balance. This is event-driven feedback, not a persistent HUD.

Balances reset on round start and map load. Authenticated humans retain their
balance across death, AFK and reconnects within that round using their network
identity. A replacement bot does not earn points; taking over another survivor
does not inherit another human's balance. LAN and pending identities use a
connection-local balance instead. No balances are written to disk.

Keep only one controller in a map, including behaviour and node-group copies.
Additional controllers are reported during export and do not add reward listeners.
Delete the controller and rebuild to remove the feature. The purchase examples
use the same **Points: Balance** group and `points` variable.

Opening older files expands the former Personal Points node into this group,
preserving its reward values. Purchase nodes are expanded too; existing exposed
settings and object overrides retain their IDs. The file on disk is unchanged
until you save. Linked library graphs are left unchanged.

Reusable building blocks include **Human Identity**, **Per Key** variable storage
and **Reset Key Variable**. These can also hold per-human statistics, unlocks or
other state. Reset only clears its named variable. Single Controller reports
duplicate controllers during export. Math's Whole Numbers option keeps integer
score arithmetic exact; it does not automatically prevent integer overflow.
