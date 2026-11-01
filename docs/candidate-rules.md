# Editable candidate rules

Add → Candidate Rules contains Candidate Rule Group, Evaluate Candidates,
Ranked Candidate at Index, and Sample Baked Ambient Light.

Create a rule for Bots or Nav Areas using **New Rule**, then **Edit Rule** (or Tab).
The group uses ordinary editable value nodes. Its inputs are Candidate, Position,
and original zero-based Index. Its outputs are Keep (boolean) and Score (number).
Add your own scalar/vector/handle inputs in the normal Group interface; these
appear on the rule asset node and are captured once when Evaluate runs.
The default group accepts everything at equal score. Higher scores rank first;
ties retain input order. Negate distance to prefer nearby positions, or combine
distance, baked brightness, and other values using ordinary math nodes.

Connect the group's Rule output into Evaluate Candidates. The evaluator's list
type must match the rule. Run refreshes its snapshot and then fires Next. Ranked
Items is a normal typed Bot/Nav Area list; Ranked Results preserves each retained
item's score, original index, and sampled position. Connect it to Ranked Candidate
at Index to inspect any rank. The evaluator also exposes the best item/score,
Count, Examined, Rejected, Errors and Truncated. Rejected includes invalid/dead
handles and Keep=false results. Errors counts thrown or incorrectly typed rule
results; bad results are skipped. First Error exposes the first error message
(up to 256 characters) instead of hiding it. Score must be finite and between -1e30 and 1e30.

Evaluation examines at most the first 256 input entries and keeps at most 128
ranked results. Truncated means input entries or accepted results were omitted.
Duplicates remain distinct entries. Positions and scores are snapshots; ranked
inspection rechecks handle validity but does not rescore moving bots. Selecting
the wrong Bot/Nav Area type or an invalid rank returns Found=false.

Rules are **stateless synchronous expressions**, not action graphs. Ordinary
nested value groups work. Events, delays, mutable state, commands, other callable
rules/actions, and Run-style world queries are rejected. Each rule is limited to
128 flattened nodes, 512 wires and 64 dependency levels. Cycles are diagnosed.
This keeps per-candidate work bounded and avoids bots sharing hidden state.

Sample Baked Ambient Light is a read-only primitive usable inside these rules.
Its outputs share one sample per candidate. **Check Available before interpreting
Brightness=0 as darkness.** It reads the baked ambient probes used for models,
not direct lightmaps, flashlights, dynamic lights, or exposure. The exported map
automatically includes the probe data when the node is used. Outside candidate
rules it is an ordinary live expression; use the existing Run-based Baked Ambient
Light node when you need an explicit snapshot.

Both ordinary graphs and bot action lifecycles support evaluation and ranked
inspection. In a bot action, read results downstream of Run on the same synchronous
path. Rule inputs can capture the action bot's position or other current values,
while the rule itself has no implicit access to action state or Current Bot.
