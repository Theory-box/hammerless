# Shared work budgets

Add → Work Budgets provides **Work Budget Pool** and **Request Work Grant**.
They let multiple bot actions share a strict entry limit for opted-in stepped
candidate evaluations. They do not set a millisecond, engine-frame, or whole-map
CPU budget. A scheduler cycle is one call to the bot-action scheduler; it can
occur at a different frequency from rendered frames.

## Wiring an editable action

Create one Work Budget Pool and pass its Pool output into the actions that
should share it. A public action-group input of type Work Budget Pool can carry
the pool. Separate pool nodes have independent identities and limits.

Inside each action, connect:

1. Start and Update → Request Work Grant's Run.
2. The shared Pool → Request Work Grant's Pool.
3. Request Work Grant's Next → Evaluate Candidates in Steps' Run.
4. Request Work Grant's Grant → Evaluate Candidates in Steps' Shared Work Grant (optional).
5. Request Work Grant's Queued → Evaluate Candidates in Steps' Enabled.

The last connection cancels the stepped job when the request is disabled,
cancelled, or invalid. A queued request with zero credits still has Queued=true:
the candidate job pauses rather than cancelling. Inspect Request's Error output
to distinguish cancellation from a configuration failure.

The pool defaults to 128 entries per scheduler cycle. A request defaults to
eight entries, and the candidate node's own Entries Per Update is also eight.
Both limits apply: a job cannot exceed its local entry budget or its grant's
remaining credit. Invalid/filtered candidate handles consume one entry too.

## Allocation and waiting

The first successful request joins a queue and receives a valid zero-credit
grant. At a later eligible scheduler cycle, the pool distributes credits among
previously queued requests, one entry at a time in rotating order. Only living,
running action contexts whose owning loop is due are eligible. Non-due loops
remain queued without taking credits or needing to renew that cycle.

Call Request Work Grant on every action update while work is needed. An
eligible request that is not renewed is removed at the end of the cycle. Unused
credits expire; they are not carried forward or redistributed through hidden
callbacks. This can leave part of a pool unused when a job finishes, a branch
stops requesting, or a context does not actually execute its child action.

Fairness applies to queued **request nodes**, not bots as a whole. Two request
nodes in one action are two claimants. For a stable eligible queue and a
positive pool limit, rotating allocation prevents earlier callbacks from
taking the pool repeatedly. It is not a wall-clock latency guarantee. Different
update intervals, changing demand and queue membership affect when work runs.

The first successful or cancellation request at a node in a cycle wins.
Repeated event branches return the same receipt and current remaining credit;
later amount/enable changes take effect in a subsequent cycle. Invalid requests
return an error without latching, so a corrected request can be tried again.

## Public observations and receipt safety

Request exposes Grant, Granted Entries, Remaining at Request, Waiting, Queued,
Error and Scheduler Cycle. These numbers are observations, not permissions to
spend work. Remaining at Request is a snapshot before subsequent consumers.

The typed Work Grant is an action-owned receipt checked against a central
record. Multiple stepped jobs may consume the same receipt, but their combined
examined entries cannot exceed its credits. Copying numeric Granted Entries
into several budgets cannot create more credits. Receipts cannot be forged,
used by another action context, or retained across scheduler cycles. Do not
store them in action parameters or state; pass the durable Pool descriptor to
actions and request a fresh Grant inside each action.

A connected zero-credit grant pauses a candidate job before evaluating its
input-list or rule-parameter expressions. Waiting for Grant=true distinguishes this from
an ordinary partly processed job. An already-started job keeps its captured
inputs while waiting. A new job waits to capture inputs until it has credit;
its Total is zero until then, and Age includes its waiting time.

A connected missing, expired or foreign grant fails closed with Job Failed and
an error. It never silently becomes unlimited work. Disconnecting Shared Work Grant
preserves the original unshared candidate-node behavior. Completed jobs return
their existing result without spending credit. The candidate node's existing
first-call-per-action-step guard still applies, including a zero-credit pause.

## Cancellation, cleanup and bounds

Requested Entries is rounded down and capped at 256. Zero or Enabled=false
cancels. Nonfinite or negative enabled requests report an error. A cancelled
request briefly retains a small same-cycle guard; it is removed at cycle end.
Action cleanup removes its queued requests and invalidates outstanding grants,
including when the entity was removed. Requests during Stop cleanup are
rejected. Typed pool descriptors are immutable within one map script; conflicting
limits for one identity report an error.

Limits are 32 pool identities and 512 queued request slots across the generated
map script. Each pool allows 1–4096 entries per cycle. Pool definitions remain
registered until script reload; empty queues do not make new pool identities
unlimited. Limits report explicit errors rather than silently ignoring work.

The entry cap applies only to stepped candidate nodes connected to these grants.
It does not cover immediate candidate evaluation, upstream queries, arbitrary
scripts, final ranking/copying, or native engine operations. Cost per candidate
can vary substantially. Use the offline benchmark to investigate script work;
measure the actual game separately before making frame-time claims.
