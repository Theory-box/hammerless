# Shared query caching

Add → Query Caching provides Query Cache, Cached Nearby Nav Areas, Cached Baked
Ambient Light and Invalidate Query Cache. Candidate Rules also have Sample
Cached Baked Ambient Light. These public nodes reuse explicitly selected
observations; existing uncached queries retain their behavior.

## Sharing and freshness

Wire one Query Cache descriptor to queries that should share observations. It
can be exposed as an action-group or candidate-rule input. Separate cache nodes
have independent identities. Do not create a separate cache for each bot when
the goal is sharing the same sample.

Max Age defaults to 0.25 seconds and accepts finite values from 0 to 60. A
reader reuses an entry only while its age is **strictly less** than that
reader's Max Age. Reading a hit does not renew the sample time. A reader with a
shorter Max Age can refresh a result another reader would still accept.

Max Age=0 removes the matching entry, performs a fresh query and does not store
it. Force Refresh removes the matching entry before querying, then stores a
successful result when Max Age is positive. Failed refreshes cannot resurrect
the prior success, and missing/unavailable results are never cached. There is
no stale-data fallback.

Cache Hit, Refreshed, Sample Age and Cache Error expose what happened. Refreshed
means a backend query was attempted, including failed attempts. Available must
still be checked: unavailable ambient data is not darkness. Invalid inputs or
pool descriptors return safe defaults and an error without a backend query.

## Exact keys and snapshots

Ambient keys contain exact numeric position components. Nearby-nav keys also
include the effective radius and result limit. Those arguments use the existing
query's normalization: radius clamps to 1–4096, limit clamps to 1–128 and rounds
down. Position coordinates are not rounded or quantized, and keys do not use
float-to-string conversions. Tiny position changes therefore miss the cache.

Nearby-nav hits check that nav data remains available and every retained area
handle still resolves to the same area. A stale handle invalidates the complete
entry and causes a fresh query. This validation does not repeat the nearby
search. It also does not check current blockers, reachability or changes to
otherwise valid geometry; use the appropriate live query or invalidate the
cache when those observations matter.

Returned arrays and sampled vectors are defensive copies. Changing a returned
array or position cannot alter the cached result or another reader's snapshot.
Native nav handles preserve identity. Cache data contains query results, not
live bindings to mutable engine positions.

Cached lighting is the same baked ambient-probe observation exposed by the
existing ambient query. It excludes flashlights, dynamic lights and exposure.
The pure candidate-rule node shares one lookup across its outputs during that
candidate invocation. Passing Force Refresh=true for every candidate defeats
cross-candidate reuse.

## Bounds and invalidation

A Query Cache defaults to 64 entries and allows 1–128. At most 16 cache
identities and 512 entries are retained across the generated map script.
Insertion evicts the least recently used entry in a full pool; if the global
limit is reached, it evicts the least recently used entry across all pools.
Eviction changes reuse opportunities, not the correctness of a fresh query.

Invalidate Query Cache immediately removes every entry from the connected
pool and exposes Removed Entries. Later reads must resample. Cache definitions
and results reset on `round_start`, `round_end`, `mission_lost` and
`map_transition`. A backwards game clock also clears retained observations.
Pool identity/capacity mismatches and capacity limits report errors.

Caching is opt-in and bounded, but it is not free: exact-key lookup scans the
pool's bounded entries, nav hits validate handles, and results are copied.
Shared work budgets separately limit candidate entries processed per scheduler
cycle. Neither feature guarantees a frame-time limit. The benefit depends on
how frequently different callers ask the same question within the allowed
sample age; moving positions or forced refreshes can produce mostly misses.

## Editable example

Add → Query Caching → Shared Shade Scan Example creates an observation-only
graph and editable action/rule groups. Common infected share a search centered
at the 3D cursor's position when the example is created. The action exposes the
center, search radius, sample age, cache and work budget as inputs.

The example ranks up to 32 nearby nav-area centers by available baked lighting,
spreads scoring across updates, and logs the selected center. It does not move
bots or establish that an area is reachable. Missing lighting is rejected rather
than treated as darkness. Query/evaluation errors log a failure and terminate
the action; a valid queued request waits for its work grant.

The default 64-entry cache can hold this shared search and its lighting samples.
Increasing the candidate count beyond cache capacity may evict samples before
other bots reuse them. Sample Age reports query freshness, separately from the
stepped scan's progress and lifetime.
