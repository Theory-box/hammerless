# Example ownership

Newly added object examples live in Selected Object > Behaviours. Opening an
example opens its behaviour directly; it does not leave a second running copy
in the main graph. Existing examples already saved in a map are not rewritten.

The catalogue audit moved 29 older object examples to this model. Single-object
examples use Self. Sleepers, Husks and Tank-reactive lights attach a behaviour to
each character/light and no longer depend on wildcard target names. The existing
pod, purchase and paid-barrier behaviours retain their ownership.

Assemblies (such as Button Opens a Gate and Three Fuses) run on their initiating
object. Their partner references appear as Object settings in that object's
Behaviour panel. Duplicate all assembly parts together with Shift+D to remap the
targets to the copies. Duplicating just the button deliberately retains its chosen
gate. An unconnected copy of a partner is not a complete assembly.

Graphs remain shared editable code when an object is duplicated. Per-object
settings and runtime state are independent. Use Make Unique when changing the
nodes for just one copy.

Two general extensions support this:

- Exposed Setting has an Object type, with Object and Entity outputs. Overrides
  are Blender object pointers stored on each behaviour slot. Blender handles
  duplication, deletion, undo and save/reload; no name-based duplication handler
  is involved. Entity Events can receive the Object output.
- Get/Set Variable have a This Object scope. It is shared across that owner's
  nested groups and distinct from another owner's variables. The scope is visible
  on ordinary variable nodes rather than an example-specific runtime feature.

The Gas Can Counter intentionally remains a map-wide query: its sample cans do
not each own another HUD counter. Examples without spawned objects remain map
systems. Effects such as time scale, camera overrides, Director settings and
changing all infected models still affect shared game state; owning the graph
does not make those engine features local to an object or player.

Carry the Crates now grants pickup for its own crate. The former unrelated bot
propane-avoidance rule is no longer bundled with each crate.

Validation: all 29 converted graphs construct and flatten; object variable names
are isolated; light input parameters survive rebinding; a duplicated button/gate
pair compiles against its copies; deletion, undo/redo and save/reload preserve
the references. The modular purchase regression also compiles and saves/reloads.
Character/model assets are substituted with entity placeholders in the ownership
test; it does not test asset importing or in-game behaviour.
