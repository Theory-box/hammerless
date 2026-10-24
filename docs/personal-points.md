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

**Survivor Assist** defaults to 25 points when the game credits protecting another
survivor (`award_earned`, award 67). Set it to 0 to disable it. This is the
protection/help award, not a general damage assist, revive or healing reward.
The award mapping follows [L4D Stats' event handling](https://github.com/muukis/l4dstats/blob/master/PLUGIN/l4d_stats.sp).

**Common Headshot Multiplier** defaults to 2: a 5-point common kill becomes 10
points for a headshot. Set it to 1 to disable the bonus; values below 1 are
treated as 1. Only common-infected kills are multiplied. Special, Tank, Witch
and assist rewards are unchanged. Both features use ordinary event, comparison,
arithmetic and group nodes, editable with Tab.

The killer receives a private centre-screen total. Disable **Show Earned Points**
to suppress these messages. Typing **!points** in chat always replies privately
with the current balance. The **Points: Display** subgroup also shows a persistent shared panel: each connected human's points above their name. It uses the existing Timer, For Each, Human Identity, variables, Format Text and HUD Text nodes, refreshing twice a second. Bots are omitted. The panel occupies HUD slot Middle Box (9).

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

Untouched shipped points and purchase rule groups receive updates when reopened,
including the new assist and headshot inputs. Existing reward values and input
wires are preserved; newly added inputs receive the defaults above. Custom-edited
rule groups are preserved. Save and rebuild after reopening; no reimport is needed.
The HUD uses Hammerless's scripted Campaign/Survival wrapper; Survival retains
its native rules. Restart the game once if Build reports that its mode addon changed.

Reusable building blocks include **Human Identity**, **Per Key** variable storage
and **Reset Key Variable**. These can also hold per-human statistics, unlocks or
other state. Reset only clears its named variable. Single Controller reports
duplicate controllers during export. Math's Whole Numbers option keeps integer
score arithmetic exact; it does not automatically prevent integer overflow.
