# Child actions and editable retry

`Retry with Recovery` is an editable action group. Connect its **Task** and
**Recovery** Action inputs, then connect its Action output to Apply Bot Action or
another action composition. Both child inputs are required; use Wait Action with
a zero duration for a recovery that does nothing. Maximum Attempts includes the
first attempt, is rounded down, and is clamped to 1–1000.

The group uses public state, math, conditions, Finish Action, and **Step Child
Action** nodes. Task failure schedules recovery on a later parent update. Recovery
success schedules the next attempt on a later update. Task success completes the
wrapper; exhausted attempts or recovery failure fail it. There are no immediate
retry loops and no hidden behavior script in the template.

## Step Child Action

Available inside action/utility groups. It starts or advances its connected child
once per parent lifecycle callback. Its outputs are snapshots on the Next path:
Idle, Running, Succeeded, Failed, Runtime Error, and Attempt. Read them only after
Run on that same path. Runtime Error describes an execution/support/conflict
failure; a child explicitly reporting Failed need not have an error string.

- **Enabled false** releases the child and returns Idle. Enabling starts fresh.
- **Reset Before Step** releases any existing child and starts a fresh attempt.
  Keep this false during normal updates; true on every update repeatedly restarts.
- Completion stays latched until reset or disabled. Attempt counts activations
  in this Step node for the lifetime of its parent action.
- Not executing the node pauses updates, but retains its owned effects. Execute
  it with Enabled false to cancel explicitly.
- Parent termination, replacement, or death recursively releases descendants.
  Managed overrides and destination reservations follow normal lifecycle cleanup.

Child state is isolated by parent action instance, bot, and Step node. Repeated
execution of the same Step node in one callback cannot reset or advance it twice.
Different Step nodes are different child slots. Concurrent slots that request the
same ownership channel fail instead of competing.

Action inputs are bound when the graph compiles, not selected dynamically from
arbitrary runtime data. Parameter values update normally. Parent actions reserve
the union of their children's channels. Direct parent movement commands or
property overrides that conflict with child ownership are rejected; put these
operations into separately sequenced child actions. Composition depth is limited
to 16. Action assets still cannot be embedded directly inside another action:
pass their Action sockets through the action group's interface instead.

This adds deterministic script lifecycle control, not stronger engine movement
control: common-infected movement remains subject to native AI limitations.
