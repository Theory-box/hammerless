# Evaluate Candidates in Steps

Add → Candidate Rules → Evaluate Candidates in Steps is a public base node for
editable bot-action groups. It performs a limited number of candidate
evaluations per action update. Use ordinary Evaluate Candidates when all results
are needed immediately.

Connect both Start and Update to Run. Connect a Bot or Nav Area list and an
editable Candidate Rule. Enabled defaults on, Reset off, Entries Per Update to
8, and Max Results to 32. A list of 32 entries normally takes four calls at this
default budget. Invalid or filtered entries still consume their entry budget.

## Captured inputs and progress

The first enabled call captures the first 256 input handles, rule parameters,
and Max Results. The input list is copied, scalar/vector parameters are copied,
and entity/nav handles preserve identity. Resumed calls do not evaluate the
input-list or rule-parameter expressions again. Changing those inputs requires
Reset to start a new job. Upstream nodes already connected through Run still
execute independently; this node cannot suppress their work.

Processed, Total, Progress (0–1), Age, Examined, Rejected, Errors, First Error,
and Truncated expose the current job's observations. Age measures seconds since
the current job started. Status is RUNNING, COMPLETE, CANCELLED or ERROR; IDLE
is the unexecuted/default state. Busy, Complete, Cancelled and Job Failed expose
the corresponding boolean tests.

While Busy, Ranked Results is null, Ranked Items is empty, and Found is false.
Partial rankings are deliberately not exposed as usable results. Completion
sorts by descending score with original-index tie ordering, and returns at most
128 entries. An empty input list completes successfully with no found item;
this differs from an ERROR. Individual rule errors are counted and skipped;
they do not make the entire job fail.

Completed results persist until Reset or cancellation. Outputs are defensive
snapshots; arrays, rows and sampled positions do not alias the private job.
Ranked Candidate at Index and Find Ranked Candidate work with completed Ranked
Results. They also revalidate handles, since a bot may die after evaluation.

## Reset, cancellation and lifetime

Reset is level-triggered: leaving it true restarts the job on every new action
update. Pulse it for one update to refresh inputs, then leave it false.
Enabled=false cancels and clears captured handles, parameters and rule state.
Cancellation wins over Reset. Re-enabling starts a new job.

Only the first invocation of this node in a given action step takes effect.
Repeated event branches in the same update return a fresh copy of the same
state, ignoring later Reset, Enabled or budget changes. This prevents event
fanout from multiplying the budget. Start counts as its own action step.

Each action instance supports at most 32 stepped-node slots. Cancellation
retains a small slot for the per-update guard but drops its captured data.
Completing drops unused input handles and retains the bounded result only.
Action release clears every slot, including when its bot was removed. The node
is rejected outside action lifecycles and during Stop cleanup; its runtime also
refuses cleanup-time calls.

Invalid budgets or capture failures produce ERROR with Job Failed=true and a
message. Errors remain until Reset or cancellation. Budgets must be finite and
nonnegative; zero clamps to one, fractions round down after the lower clamp,
and values above 256 clamp to 256. Invalid budgets do not evaluate the capture
expressions. Completed/error jobs ignore later budget changes until reset.

## What this does not guarantee

This is an **entry-count budget per action instance**, not a millisecond limit,
global frame budget, or fair scheduler. One hundred bots with budget eight may
still perform eight hundred candidate evaluations in the same game update.
Finishing also sorts at most 256 rows and copies at most 128 results, outside
the entry budget. Candidate rules can have very different costs.

Candidate positions, lighting and other world observations are sampled when
each entry is processed, so one result can contain observations from several
updates. Positions already sampled are copied, but later entries see the world
as it exists then. Results are neither an atomic world snapshot nor a guarantee
that a destination remains safe or reachable.
