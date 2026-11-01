# Offline behavior performance benchmark

`tests/performance/benchmark_behavior.py` measures the current production
Squirrel candidate evaluator and bot-action scheduler in a real `squirrel-lang`
VM. It is a repeatable software microbenchmark, **not an estimate of L4D2 frame
time**. No game is launched and no production code is modified.

## Running

Use Python 3.11 with the optional `squirrel-lang` package. If already installed
in a separate directory, set `SQUIRREL_PYTHONPATH` to that directory. A missing
or incompatible SQVM fails clearly rather than reporting a skipped benchmark.

```powershell
$env:SQUIRREL_PYTHONPATH = 'path/to/existing/squirrel-package-directory'
& 'C:/Program Files/Blender Foundation/Blender 4.4/4.4/python/bin/python.exe' tests/performance/benchmark_behavior.py --output 'path/to/results.json'
```

Defaults: 3 warmup batches, 21 measured batches, 2 updates per batch. Optional
`--warmups`, `--repetitions`, and `--updates` accept 1–100. A quick smoke run can
use `--repetitions 3 --warmups 1`; its percentile is not a meaningful tail
estimate. Avoid running other benchmarks simultaneously.

## Workloads

The 36 scenarios include:

- Candidate evaluation for 30, 60, or 100 simulated common-infected decisions,
  each examining 8, 32, 128, or 256 valid candidates. All candidates pass; scores
  are `index * 0.5 + botIndex`. The runtime still examines/sorts the full input
  when its returned-result cap is 128.
- Equivalent handwritten and actual compiler-generated five-node arithmetic
  rules. This isolates some generated-rule overhead; it does not represent
  complex rules or ambient/light queries. The same candidates are reused
  across bots, but evaluation is recomputed for each bot.
- Actual unmodified `HLBA.TickAll` with 30, 60, or 100 distinct bots, partitioned
  across one or four loops. Four loops do **not** mean four times the total
  bots. Each bot requests one action with a movement channel and no-op counted
  Start/Update callbacks. Both newly created and retained instances are tested.
  Fresh-instance batches include the harness's `ReleaseLoop` calls; there are
  no overrides to restore. These are not full in-game action costs.

An untimed pass wraps `Clashes` to count desired-action pair comparisons and
then restores it before timing. It also verifies all active bots are unique
across loops and the total population matches. The current scheduler compares
`N*(N-1)/2` desired pairs even when bots differ: 435 at 30, 1,770 at 60, and
4,950 at 100. Other matching/request work is not counted by that metric.

## Timing and validation

One persistent VM loads the actual runtime source and generated rule before
measurements. Entity arrays, compiler work, loop creation, and retained-action
prefill occur outside timing. `perf_counter_ns` measures a short `SQVM.execute`
call invoking a preloaded workload. It includes the host/VM boundary, parsing
that short call, mock entity methods, and checksum/counter validation. It does
not include VM startup or compilation of the runtime or generated rule.

Each candidate batch validates examined and returned counts, absence of
errors/rejections, and an exact arithmetic score checksum. Action batches
validate callback counts and absence of runtime errors. This prevents an empty
or failed workload from appearing fast. Validation overhead is included.

JSON contains raw samples, median/p95/maximum **batch** times, mean batch time
divided by updates, checksums, operation counts, interpreter/OS/CPU identity,
and hashes for production runtimes, generated rule, harness, and runner.
The p95 uses nearest rank. Neither p95 batch time nor a batch time divided by
two is a measured single-update p95. With 21 samples it is a coarse tail
summary, not a statistically established latency bound.

## Limits and interpretation

Mock entities implement only cheap validity, health, class, and position
accessors. Positions are small Squirrel tables, not engine-native Vectors.
No native navigation search, visibility test, ambient-probe lookup, collision
trace, bot movement, network cost, or render work is measured. The benchmark
also excludes staggered-due loops, conflicting actions, nested action groups,
garbage-collection tuning, and shared-query caches. CPU frequency and other
desktop activity are not controlled.

Use results to compare the same software workload before and after a change
and to identify code worth investigating. They support the statement that
re-evaluating many candidates separately for every bot increases script work.
They do not establish a safe live bot count, frame budget, update frequency,
or the benefit of a scheduler/cache design that has not been implemented and
measured. Live-game benchmarking is a separate task.
