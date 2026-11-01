# Waypoint routes

Authored routes are ordered positions, not navigation paths. The new nodes work in ordinary logic and bot-action callbacks. All queries use Run/Next snapshots; check Found and Error before using a position.

## Author a route

In the Logic Editor, use **Add > Waypoint Routes > Authored Route**, then **Add Point at Cursor**. The first point creates a collection; later points use that collection. Move the scene cursor between additions. Points are ordinary Empty objects. Select one and open Object Properties > Waypoint Route Point to change its Route Order.

Only direct Empty objects in the selected collection participate. Each needs a distinct integer Route Order. Their ascending order defines indices starting at zero; order numbers need not be consecutive. Geometry and nested collections are ignored. A route supports 1–256 points. A point must belong to the exported scene. Export uses evaluated world positions (including parenting/constraints), converted to Hammer units. Changes require a rebuild; these are not moving runtime targets.

## Public building blocks

- **Route Point:** get position and total count at an explicit zero-based index.
- **Closest Route Point:** get the nearest authored point and distance from any supplied position. Uses straight-line 3D distance and keeps the first point on ties. It does not test navigation, visibility, or clearance.
- **Advance Route Index:** calculate a next index and direction from the current cursor. Positive direction advances; negative reverses. Once completes after the endpoint, Loop wraps, and Back and Forth reverses without repeating the endpoint. These operations contain no private progress state.

Empty/missing routes, invalid indices and invalid directions return Found=false with Error. Once on a single point completes after arrival; Loop/Back and Forth keep that point.

## Editable Follow Route action (experimental)

Choose Follow Route: Once, Loop, or Back and Forth from the same menu. Connect an Authored Route and apply the action to commons. Open its action group to see or change every operation: read a point, observe movement, issue a move command, save the cursor, and advance after arrival. No opaque patrol script is used.

Initial Index chooses where to start (or explicitly resume), and Initial Direction chooses forward/reverse. A reverse route usually starts at the final index. Join at Closest Point overrides Initial Index at startup using the bot's center, not its feet. This is an approximate join heuristic, not a reachable-path search.

The public action-state names `route_index` and `route_direction` hold the current destination cursor. They persist while this action instance runs. Stopping/preempting destroys the instance; automatically resuming across fresh instances is not implied. Users can edit the group to publish/save progress through their own logic. The route itself is not mutated.

Arrival Radius defaults to 32 Hammer units. Stall After defaults to 5 seconds; Point Timeout defaults to 30 seconds. Zero disables the corresponding timeout. Movement observes best straight-line distance, so a valid detour can appear stalled. Each new destination resets its observation. At most one point is advanced per action update, even when points coincide. Move commands refresh at most twice per second. Cleanup resets movement only if this action issued a command.

**Common infected movement remains experimental:** the native AI can interfere. Route nodes do not ensure a navmesh exists, validate clearance, or change native pathfinding. Compiler and simulated lifecycle tests are not an in-game AI guarantee.
