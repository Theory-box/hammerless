# Editable temporal groups

These are ordinary editable node groups made from Math, Compare, Boolean Math,
If, and Get/Set state nodes. Tab into one to inspect or change every operation.
They contain no embedded scripts or private runtime calculations.

Call **Update once per evaluation** and supply **Elapsed Seconds This Update**:
the real interval since the previous evaluation, not rendering frame time. For
example, evaluating every 0.2 seconds requires 0.2, even if the game renders at
120 FPS. Negative elapsed time is treated as zero. Values are sampled on Update;
changes between calls are not detected. Wire subsequent work from **Next** to read
the freshly updated output. Outputs are live state reads, not historic snapshots.
Reset to Zero takes priority over updating and still fires Next. State begins at
zero. Inputs should be finite numbers.

- **Sustained Condition** accumulates elapsed time while Condition is true and
  clears it on any false update. Outputs Elapsed Seconds and Satisfied. Duration
  is clamped to zero; a true condition with zero duration satisfies immediately,
  except during Reset. The first true evaluation credits its supplied elapsed
  interval, so pass zero if the condition has only just become true.
- **Accumulate With Decay** adds `(Add Per Second - Decay Per Second) * elapsed`,
  bounded between zero and Maximum Value. Decay and Maximum are clamped to zero.
  Addition may be negative. Decay applies even while adding. To model suspicion,
  use a positive addition while a threat is seen and zero when it is absent.
- **Limit Rate of Change** moves Value toward Target Value by at most
  `Maximum Change Per Second * elapsed`, without overshooting. Negative speed is
  treated as zero. This is a linear rate limit, not exponential smoothing.

## State ownership

Adding a group in an ordinary graph uses **This Group Copy** variables. Each
instance has separate map-lifetime state. Adding inside a bot action (including
its nested editing path) creates the **Action Memory** variant instead: separate
state for each bot, action context, and group copy. Releasing/replacing an action
discards its context as usual. If reusing a saved group manually, choose the
variant matching the context; ordinary map state is not per-bot memory.

Get Action State and Set Action State now expose **This Group Copy**. Both ends
must use the same setting and name. It defaults off for existing graphs so
intentional state sharing is preserved. The new templates turn it on. Users can
recreate these groups themselves using the same public nodes and option.

Validation: `tests/blender/test_behaviortemplates.py` constructs and compiles all
three variants in ordinary and action contexts, checks copy isolation, and can
write compiled action functions to a JSON path passed after `--`.
`tests/blender/run_temporal_vm.py <json>` executes those generated functions in
the optional squirrel-lang VM, checking bounds, reset, direction, readiness,
negative elapsed time, and separate bot contexts.
