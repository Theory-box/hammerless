# In-game testing

Tools for checking Hammerless in the running game: the **test bench** (drive the game from Python), the
**example regression** (plays every logic example and checks it), and small **probes** (older, one-shot
VScript files). Written for developers, human or AI: start here when something needs testing in the game.

## The test bench: `bench.py`

Drives a running Left 4 Dead 2 from Python, two ways, in about 0.1 s per request.

How it works: `bench/hl_bench.nut` is copied to `left4dead2/scripts/vscripts/mapspawn.nut` while a `Bench`
is open (the game runs `mapspawn.nut` on every map load, and on round restarts). It starts a 0.1 s think
that reads `left4dead2/ems/hl_bench_in` (`<id>\n<Squirrel code>`), runs the code in the root scope and
writes `ems/hl_bench_out` (`<id>\nok\n<JSON>` or `<id>\nerror\n<message>`). `ems/` is the only folder the
game's scripts may read and write. `ems/hl_bench_state` holds the loaded map and how many times the bench
has started (how `load` knows a new map is in). Closing the bench removes all of these files.

```python
import sys; sys.path.insert(0, r"C:\Users\John\Documents\Hammerless")
from tests.ingame.bench import Bench, BenchError

with Bench() as b:                                   # installs the game side; removes it at the end
    b.load("c2m1_highway")                           # starts the game if needed; waits for the survivors
    b.value("Director.GetMapName()")                 # one expression -> Python value
    b.run("local n = 0; ...; return n;")             # any Squirrel; returns what it returns
    b.console("sv_cheats 1", "god 1")                # console commands, run by the host
    m = b.mark(); ...; b.events_since(m)             # logic wires that fired (maps built with Debug Log)
    b.lines_since(m, "HLT")                          # console lines since a mark
    b.wait_until("Director.HasAnySurvivorLeftSafeArea()", timeout=20)
    b.look_at("hl_my_button"); b.use(3.0)            # aim straight at an entity (noclip on) and hold Use
    b.walk_to(1.0)                                   # move the survivors along the map's path (Director-checked)
    b.screenshot("name", out_dir=r"...\shots")       # a JPEG of the host's view: read it to SEE the game
    b.build(blend, "hl_test_x", prep="...")          # build a .blend (never saved) without lighting, Play it,
                                                     # wait until loaded (about 30 s)
```

Values come back as JSON: entities as `{"ent", "class", "name"}`, vectors as `[x, y, z]`, tables as dicts.
Errors in the code raise `BenchError` with the game's message.

**Notes that cost time to learn:**
- Map names for tests: `hl_test_*`, never a user's map name.
- Never bake lighting in tests: `build` uses the QUICK preset (no vis, no light). Build + load is ~30 s.
- `script_execute` and many debug commands need `sv_cheats 1` (the bench itself doesn't).
- Some Director values update slowly (furthest flow, path progress): wait for them (`wait_until`) instead
  of sleeping a fixed time. Don't probe the instant a map loads: wait for the host survivor (`load` does).
- Teleporting all survivors into the air or the void can restart the round. Teleporting may not count as
  leaving the safe room on stock maps (it does on Hammerless maps).
- Moving survivors along the path for the Director: one big teleport doesn't move its furthest-survivor value,
  and the position-flow of some nav areas off the route disagrees with the flow the Director gives a player
  standing there (`GetFlowDistanceForPosition` vs `GetCurrentFlowDistanceForPlayer`). `walk_to` steps along
  and keeps only spots the Director agrees with. Clear infected first: a Tank pushes survivors back.
- Things set up at map start can fire before a test looks (a When that is already true at load): check
  the wires since the map loaded or since the survivors left, not only since the test's own action.
- `cl_drawhud 0` doesn't hide the scripted-mode HUD or captions in screenshots.
- On Windows the game can hold the request file open for a moment: `run` retries.
- Script errors: the console shows `AN ERROR HAS OCCURED [...]` with a call stack; `lines_since(m, "AN ERROR")`.

## The example regression: `test_examples.py`

```
python tests/ingame/test_examples.py              # all 42 examples (about 6 minutes)
python tests/ingame/test_examples.py 21 22 42     # some
python tests/ingame/test_examples.py --no-build   # reuse the map already built
```

Builds `demo/demo_level.blend` (never saved) with every example on its field as `hl_test_examples`, Debug
Log on, then for each example triggers it the way a player would (pressing buttons with `ent_fire ... Press`,
holding Use, moving players into volumes, killing infected, chat, console commands) and checks the wires that
fired (`[Example NN: Title] node.output -> node.input` in the console) and the game's state. Prints PASS /
FAIL / SKIP per example and every script error of the run. A few need real play (headshots, the Director's
own mobs and specials) and are skipped. Add a check for every new example; a new system (model compiler,
map compiler...) should get its own regression script built the same way.

Entity names in a built map: an object `Example 21 button` becomes `hl_example_21_button`; a node's entity is
`hl_<slug of "Example 21: Two-Step Gate">_<node id>` (Volumes, Timers, relays: named after the node).

## Probes

Small VScript files that print `HL…` lines; the bench's `run` replaced them for new work.

1. Start the game with `-condebug` (Build & Play does) so the console is written to `left4dead2/console.log`.
2. Copy the probe to `left4dead2/scripts/vscripts/`.
3. With the map loaded: `left4dead2.exe -game left4dead2 -hijack +script_execute <probe name>` (or type `script_execute <probe name>` in the console).
4. Read the `HL…` lines at the end of `console.log`.

Some probes read Director values that the game only exposes with `sv_cheats 1`. Coordinates in `flow_probe.nut`, `height_probe.nut` and `transition_probe.nut` are for `demo/demo_level.blend`; change them for another map.

| Probe | Prints |
|---|---|
| `flow_probe.nut` | Whether survivors left the safe area, furthest flow, and the nav area / flow distance at a few points along the demo map |
| `count_probe.nut` | How many common infected are alive (for "do zombies spawn here?" checks) |
| `status_probe.nut` | Each player's position and view angles |
| `height_probe.nut` | Player positions, and the ground height under a set of points on the demo map (for checking spawn and terrain heights) |
| `horde_probe.nut` | Safe room door flags and horde-related state |
| `button_probe.nut` | Presses the map's first `func_button` (tests button outputs) |
| `transition_probe.nut` | The nav spawn attributes (checkpoint, player start) in the start room, end room and field of the demo map |

If you add a probe, keep the `HL<NAME>` prefix so its lines are easy to find.
