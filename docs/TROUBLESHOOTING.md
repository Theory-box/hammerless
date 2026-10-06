# Troubleshooting

## "N warning(s). See the 'hammerless_log' text block"

Warnings don't stop the build. Open *Build & Play > Problems* (or the `hammerless_log` text in Blender's Text Editor) to see them; click a row to select the object.

- **"… almost line up (0.080 units apart in X)"** (*near misses*): two faces nearly line up. Hammerless snaps them together when it exports; nothing to do. Snapping them in Blender removes the warning.
- **"… floats N units above the floor"**: a spawn, weapon or item hangs in the air and will drop (or hover) in game. Move it down to the floor.

## The map looks unlit in game

- **Quality is set to Quick**, which skips lighting. Use Normal.
- **An unlit map was loaded earlier in the same game session.** The engine turns `mat_fullbright` on for a map without lighting and leaves it on. Hammerless switches it off again when it sends a lit map to a running game; otherwise quit L4D2 and launch again.

## Zombies don't spawn in a room

See [Why does an area get no zombies?](NAV_AND_ZOMBIES.md#why-does-an-area-get-no-zombies). The usual causes:

- **A gate with *Block Nav While Closed* on** between the survivors and the room hides the room from the Director. It looks random because a vote restart doesn't block again.
- The room is **too close** to where the survivors stand (under about 550 units of walking distance) or **in their view**. Use *Colour > Where zombies can spawn*.
- A **no-spawn box** covers it (*Colour > Zombie spawn marks*).

## No zombies at all, or "flow broken"

The Director needs a path (the *flow*) from the start safe room to the end safe room.

- The **End Safe Room's Next Map** must name a **different, real** map. If it's empty or names this map, the game computes no flow and spawns no wanderers, Tanks or Witches.
- The nav must connect start to end: *Colour > Can survivors reach it?* shows where it breaks. Ledges between 19 and 64 units high are never linked in either direction; add a ramp, stairs or a ladder.
- Keep the survivor spawns **inside the Start Safe Room box**.

## "Walls or floors changed since the map was last compiled"

Older versions of **Analyze Navmesh** showed this and stopped. Current versions compile the map first automatically. **Build Navmesh** only builds the nav; it never compiles the map.

## Build & Play didn't reload the map in a running game

Hammerless sends the load command to an already-running L4D2. If the game didn't change map, quit L4D2 and press **Build & Play** again (it won't recompile an unchanged map).

## The game doesn't start

If Steam isn't running, Build & Play starts it and waits up to two minutes for it to sign in. If Steam asks you to log in, do that, then press **Play**. If L4D2 isn't found at all, set its folder in the add-on's Preferences (or per file, under Settings > Folders & Game Data > L4D2 Folder): the folder that contains `left4dead2.exe`.

## The map leaks

With *Settings > Scene > Auto Seal (skybox shell)* on (the default) a map can't leak. If you turned it off, **Load Leak** draws a red line to the hole.

## Compiler errors

The full compiler output is in `<Work Folder>/<map>.log`. The newest compile is at the **end** of the file; earlier runs are kept above it.

## Blender froze

Hammerless records freezes: if Blender stops responding for 20 seconds, it writes where it was stuck to `hammerless_freeze.log` in your temp folder (type `%TEMP%` in the Explorer address bar). Attach that file when you report the freeze. If Blender crashed instead, Blender writes its own `blender.crash.txt` in the same folder.

## Reporting a bug

[Open an issue](../../../issues/new/choose) with your Blender version, what you did, what happened, and the end of `hammerless_log` (and of `left4dead2/console.log` for in-game problems).
