# Reusable behavior toolkit

## Design contract

Provide small public primitives for engine access, bounded execution, typed data,
and ownership. Build higher-level behaviors as editable node groups using those
same public primitives. Examples must not depend on private capabilities that
users cannot access.

Expose useful intermediate results: candidate scores, validity, limits reached,
observation age, action outcomes, and reservation lifetime. State belongs to its
documented scope; a reusable group must not silently share one bot's state with
another. Reject unsupported graph combinations at build time.

Review each implementation chunk independently before installation. Test generated
scripts as well as Blender registration/export where those paths change. Engine
behavior claims require actual engine evidence; script tests alone do not prove
that native infected AI obeys a command.

## Implementation order

1. User-authored candidate evaluation: editable rules, typed candidate inputs,
   filtering/scoring, bounded ranked results, and inspectable scores.
2. Editable temporal utilities: sustained conditions, bounded accumulation/decay,
   and rate limiting, with explicit elapsed time and isolated state.
3. Stable selection and retry/recovery, building on the preceding primitives and
   existing action lifecycle. Any new child-action execution capability must be
   public rather than hidden inside a template.
4. Waypoint route data and editable traversal; graph diagnostics.
5. Measure representative workloads before adding scheduling/caching machinery.

This is a roadmap, not a statement that all items below are implemented.

## First implementation batch

Implemented and independently reviewed:

- Typed list union/intersection/difference/unique operations.
- Stateless editable candidate rules, ranked snapshots with inspectable scores,
  diagnostics, and read-only baked ambient sampling.
- Editable sustained-condition, accumulation/decay, and rate-limit groups, with
  ordinary per-copy and action per-bot/per-copy state variants.

Candidate rules preserve lazy conditional evaluation. Their bounded workload is
not a guarantee of live horde frame time; scheduling and performance measurement
remain on the roadmap. Stable selection, retry/recovery, routes, and the other
backlog capabilities are not included in this first batch.

## Second implementation batch

- Editable stable bot/nav-area selection groups with refreshed scores, minimum
  hold, strict improvement margin, and missing/truncated snapshot diagnostics.
- Public typed Find Ranked Candidate lookup and bot/nav-area variable types.
- Public Step Child Action with status, attempt, error, reset and cancellation.
- Action-valued group inputs bound at compile time, including channel inheritance
  and recursive cleanup; no callable definitions stored in runtime parameters.
- Editable Retry with Recovery accepting user-supplied Task and Recovery actions.

These groups are in Behavior Utilities and Bot Actions respectively. State and
decisions remain visible inside each group. See [stable selection](stable-selection.md)
and [child actions](child-actions.md) for scope and lifecycle contracts.

## Third implementation batch

- Typed authored waypoint routes with public point, nearest-point, and cursor
  advancement nodes.
- Editable Follow Route actions with once, loop, and back-and-forth traversal;
  explicit initial cursor/direction and optional nearest-point joining.
- Disabled-by-default Debug Watch, Check Condition, and Inspect Own Action,
  with bounded, rate-limited console logging and public result sockets.

See [waypoint routes](waypoint-routes.md) and [behavior diagnostics](behavior-diagnostics.md).
Both chunks passed independent review, generated Squirrel tests, and installed
Blender registration/export checks. Follow Route remains experimental native-AI
control. Diagnostics do not include a visual inspector or CPU profiler.

## Performance baseline

The offline benchmark runs actual candidate evaluation and action-loop Squirrel
code, including a compiler-generated candidate rule. It measures representative
30/60/100-bot workloads and records runtime/source fingerprints, checked work
counts, and batch timing samples. See [performance methodology](behavior-performance.md).

This establishes a repeatable script-cost baseline, not live game frame times.
Native engine queries and movement costs still need separate measurement.
Action conflict checks now use a per-bot index, preserving ownership and ordered
diagnostics while eliminating cross-bot comparisons.

## Incremental candidate evaluation

Evaluate Candidates in Steps is a public action-lifecycle primitive. It captures
inputs once and processes a configurable number of entries per action update,
with progress, age, status, cancellation/reset, and completed ranked results.
See [stepped candidate evaluation](stepped-candidate-evaluation.md). The existing
immediate evaluator is unchanged.

This is per-action entry budgeting, not fair global scheduling. Global budgeting,
shared query caches, and automatic refresh policies remain future work. The
backlog below includes both completed and future capabilities.

## Candidate backlog

| Capability | Intended form |
| --- | --- |
| Budgeted, fair staggered updates | Runtime primitive |
| Cached queries with freshness/invalidation | Runtime primitive |
| Shared batch observations | Runtime primitive |
| Resumable bounded list processing | Runtime primitive |
| Adaptive update frequency | Editable group over scheduler |
| Graph cost and action diagnostics | Runtime/editor tooling |
| Typed list union/intersection/difference/unique | Typed primitives |
| Typed reductions and grouping | Typed primitives |
| User-authored candidate filtering/scoring | Callable editable groups plus evaluator |
| Scoped temporary tags | Lifecycle-aware metadata primitives |
| Bounded timestamped observation history | Runtime primitive |
| Bounded message queues | Runtime primitive |
| Capacity-limited resource reservations | Existing lease system extension |
| Parallel actions with channel ownership | Action runtime extension |
| Stable target selection | Editable group |
| Sustained conditions | Editable group |
| Smoothing/rate limiting | Editable group |
| Accumulation with decay | Editable group |
| Waypoint route traversal | Route data plus editable action |
| Named states with entry/exit rules | Editable groups/editor structure |
| Group rendezvous with timeout | Editable group |
| Retry with recovery | Editable group over public lifecycle primitives |
| Non-repeating weighted choice | Editable group |
| Validated destination construction | Verify engine APIs first |
| Actual route geometry/length | Verify engine APIs first |

Existing variables, persistence, vector math, game events, entity properties,
line traces, and entity input/output are not missing features. Prefer reusing
them to adding duplicate systems. Native-AI path avoidance, arbitrary navigation
mutation, and guarantees of infected movement are outside this roadmap.
