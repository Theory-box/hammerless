# Behavior diagnostics

Add → Diagnostics exposes three public primitives. They work inside editable
groups; the same primitives are available to users building their own groups.
These are console observations, not a visual graph inspector or CPU profiler.

## Debug Watch

Run captures whether a labeled value was printed to the game console. Enable
Logging defaults off. Choose Number, Boolean, Text, Position, Entity, Bot, or
Nav Area. Wire the original value onward wherever needed; Watch does not mutate
it or keep an observed-value history. Entity/nav handle text uses the engine's
string representation; query specific fields separately when needed.

Disabled Watch skips evaluation of its incoming value, label and interval
expressions. This does not disable query nodes already executed earlier on Run.
The remaining disabled cost is a branch and a small result snapshot.

Logged indicates an emitted line. Suppressed indicates enabled logging was
rejected; Log Result explains node interval, global rate limit, invalid interval,
invalid bot, or state capacity. Disabled logging is not suppression.

Minimum Interval defaults to one second and clamps to 0.1–300 seconds. Negative,
nonfinite or nonnumeric values are rejected. A shared cap permits at most 20
lines per one-second window. This protects against a horde of logging bots; it
does not guarantee fair sampling of every bot. Labels and values are truncated
to 256 characters plus an ellipsis, and control characters become spaces.

Each action instance has up to 128 node timestamps, discarded on release. Bot
loop collection uses loop ID, bot entity index and node ID; ordinary graphs use
node ID. These share a 256-entry cache expiring entries after 301 seconds. It
retains no bot handles. Rapid entity-index reuse can inherit an interval until
that short throttle expires. Action/loop logs identify the bot's entity index.

## Check Condition

Run chooses Passed or Failed regardless of logging. The Passed boolean exposes
the same captured condition. Optional logging only prints failures, using the
same limits as Watch. It never throws an assertion or changes action status.
Passing conditions skip diagnostic-only expressions; disabled failing checks
also skip their label and interval. Use existing Finish Action if a failed check
should terminate your action.

## Inspect Own Action

Run snapshots the enclosing action instance's Status, Running, Succeeded,
Failed, Stopping, and Step Count. Only valid inside Start/Update/Stop lifecycle
paths; it cannot read another bot's private state. Step Count includes Start
and each executed Update, not elapsed time. Stopping is true during cleanup;
an interrupted action can still have status RUNNING while it is stopping.

Finished actions stop executing Update. To observe their completion from a
parent group, use Step Child Action's result outputs. Inspect Own Action does
not claim to report the selected child, engine AI behavior, or a termination
reason unavailable in the underlying runtime.

All outputs are snapshots. In synchronous action paths, execute the diagnostic
on the same path before reading its outputs. Normal graph outputs use their
last executed result. No game settings, commands, overrides or bot behavior
are changed by these observations.
