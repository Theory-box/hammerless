# In-game probes

Small VScript files used to check, inside the running game, what Hammerless claims about a map: where the Director's path runs, how many zombies are alive, what a door or button does. They print lines starting with `HL…` to the console.

## Running one

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
