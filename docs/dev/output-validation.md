# Output target validation

Checks use case-insensitive target/class names and Source-style `*` matching. Named template members
remain in the static map data, so their outputs resolve before runtime instantiation. Explicit
`AddOutput` targetname assignments are recognized as declared runtime names only when their recipient
exists. Merely mentioning a nonexistent member in a point_template does not prove it can be spawned.
Unknown script-generated names cannot be proven missing by this static check; unresolved new targets
get a qualified warning with directions to verify the script, name and Map Collection membership.

Imported documents provide an immutable entity/output baseline. The checker groups references that
were already unresolved in that baseline into one INFO row. Matching requires original entity ID,
class, output event, target, input, parameter, delay and count. Deleting a target that existed in the
baseline still warns, as does adding or changing an output. No game entities or outputs are rewritten.

Hotel audit (2026-10-21): read Valve's c1m1_hotel BSP entity lump directly from installed game content,
then compared the decompiled original and exported hotel_remix VMF. The BSP has 59 unresolved exact
references to 46 targets. One is the valid `fire_ballroom*` wildcard. After wildcard matching, the
original and export both have 58 unresolved references to 45 names, with no newly missing targets.
These include biohazard01-start, relay_spread_1 and the fire/inferno template and particle names shown
in the user's report. They are inherited unresolved references, not confirmed runtime entities or
proof of broken import behavior. The checker makes that distinction without silencing new failures.

Regression tests cover wildcard/case/special/class targets, runtime renames, missing template members,
inherited references, removed targets, new or edited outputs, and baseline propagation through export.
