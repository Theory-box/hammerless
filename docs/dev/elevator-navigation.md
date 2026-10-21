# Elevator navigation

The Blender bake supports vertical `func_elevator` brush platforms. It reads the platform geometry,
origin, top/bottom keys and `info_elevator_floor` markers inside the shaft footprint. Near a named landing,
the marker takes precedence over a nearby top/bottom limit: the stock hotel's bottom limit and named
stop1 share a boarding area even though their heights differ by 32 units.

The temporary collision world contains the platform at each landing and a seed above it. This lets nav
generation reach floors separated by the shaft. It does not change the VMF, animate the Blender object,
or add physical platforms to the exported map.

`NavMesh.elevator_links` contains potential runtime travel edges for reachability, island marking and
the preview. These are not written as normal NAV connections. The game discovers the `func_elevator`
associations at load time; the original hotel NAV has no serialized cross-floor walk connections.
Saved NAV previews use elevator model bounds and floor entities from the matching BSP.

The preview shows elevator travel in purple. Missing landing areas produce explicit warnings. This is
potential connectivity: control logic, locked doors and disabled elevators can still prevent travel.
Track trains, arbitrary moving platforms, horizontal lifts and script-created destinations are not
automatically supported by this implementation. Nearby landing aliases and irregular platform shapes
require playtesting. Elevator controls and imported entity outputs remain unchanged.

## Validation

- Unit fixture: two floors separated by 512 units, initially disconnected. Adding the lift yields a
  start-to-end route, with no cross-floor ordinary walk connections in the saved NAV.
- Native and Python generator parity; malformed/missing elevator data and missing landing diagnostics.
- L4D2 hotel: regenerated NAV areas are recognized by `GetElevator()` / `GetElevatorAreas()` at runtime.
  The lower boarding floor is near 1188 units and the upper floor near 2468 units. The original NAV uses
  approximately 1187 and 2464 respectively. The small differences are within step height.
- Removing saved HAS_ELEVATOR flags from the original NAV still allows the game to reconstruct the
  elevator associations, confirming that travel edges must not be invented in the NAV connection lists.
- Three survivor bots walked from the upper hall onto the elevator, rode from approximately 2464 to
  1188 units, and walked into the lower hall. The diagnostic issues move commands and operates the lift;
  it teleports the host to the lower hall and bypasses the normal buttons. It verifies bot nav traversal,
  not human exit collision or unaided campaign logic. The normal lower-door button disables the
  player clip brushes; the user subsequently confirmed the elevator worked during manual play. Run
  `python tests/ingame/test_elevator.py <report.json>` to repeat. It regenerates nav against the stock
  hotel BSP and uses only the `hl_test_elevator_generated` map copy. Waits for the intro to detach players.
- The generated hotel's complete route still has a separate break on the starting rooftop, before
  reaching the elevator. Elevator support does not establish full-map gameplay equivalence.

After installing this change, restart Blender and rebuild the nav. Existing imports do not need to be
reimported. Organize Scene can be rerun to create the World material collections.

Builds now evaluate hidden contents of the assigned Map Collection, including excluded subcollections,
for nav, lighting and sound inputs. Viewport visibility is restored afterward. Objects outside the map
remain excluded; legacy scenes without an assigned map collection retain their previous behavior.
