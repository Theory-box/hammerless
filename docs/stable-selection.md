# Editable stable selection

Add → Behavior Utilities → **Stable Bot Selection** or **Stable Nav Area Selection**.
These are editable groups built from public Find Ranked Candidate, Ranked Candidate
at Index, state, Math, Compare, Boolean Math and If nodes. There is no private
selection algorithm. Tab into the group to change its policy.

Connect **Evaluate Candidates → Next** to **Update**, and its **Ranked Results**
to **Fresh Ranked Results**. Supply the actual elapsed seconds since this group's
previous update. Read outputs after **Next**.

The first update selects rank zero. Subsequent updates find the retained handle
in the new snapshot, so its score is refreshed rather than frozen at acquisition.
A challenger replaces it only when its score is **strictly greater** than the
current score plus **Required Score Improvement**, and **Minimum Hold Seconds**
has elapsed. Equal scores preserve the current choice. Negative elapsed time,
hold and margin are treated as zero. Acquisition starts Held Seconds at zero;
later updates credit their supplied elapsed interval.

If the previous item is absent from the supplied snapshot (or its handle is no
longer valid), the group immediately selects rank zero regardless of the hold.
If no valid rank-zero item exists, it clears the choice. **This is selection from
a snapshot, not a claim that a missing item ceased to exist in the game.** Filtering,
Max Results, query errors, and truncation can remove an otherwise valid item.
Keep enough candidates in your evaluator if you want to retain lower-ranked items.
Previous Selection Missing and Snapshot Truncated expose this situation.

Clear Selection takes priority and produces no selection that update. Selection
Changed reports acquisition, replacement, or loss/clearing of an existing choice;
repeated empty updates or repeated clears do not report another change. Selected
Score and Sampled Position refresh even when retaining the item. Has Selection
must be checked before using the selected handle. Scores/positions reflect the
supplied snapshot, not continuously resampled world state. Feed finite values.

Groups added to an ordinary graph have separate map-lifetime state per group copy.
Groups added inside a bot action use per-bot, per-action, per-copy Action Memory.
Use the appropriate variant if copying an asset between those contexts. Their
public Get/Set state nodes expose all memory for inspection and customization.

## Public lookup primitive

**Candidate Rules → Find Ranked Candidate** accepts a typed Bot/Nav Area handle
and a Ranked Results snapshot. Run produces Found, Item, Score, Original Index,
Position, Rank, and Snapshot Truncated. Rank is zero-based; the highest-ranked
matching duplicate wins. Invalid or absent handles return Found=false and Rank=-1.
Lookup checks at most 128 stored results and does not rerun the scoring rule.
It works in ordinary graphs and action lifecycles, independent of these templates.

Validation: `tests/blender/test_stabletemplates.py` compiles both typed groups in
ordinary and action contexts and checks isolated storage names; it exports compiled
action functions when passed an output JSON path after `--`.
`tests/blender/run_stable_vm.py <json>` executes those real generated functions in
Squirrel for score refresh, hold/margin, ties, absence/truncation, reset and isolation.
