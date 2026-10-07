# Development

## Layout

```
hammerless/            the Blender extension (this folder is what gets zipped)
  blender_manifest.toml
  core/                pure Python, no bpy: geometry, VMF writer, compile pipeline, nav generator,
                       nav analysis, terrain, entities, textures, logic graph compiler
  core/_hlnav.dll      native nav / visibility code (built from native/)
  core/hlvvis.exe      Hammerless's vis compiler, a drop-in for vvis.exe (built from native/hlvvis.c)
  blender/             UI panels, operators, scene extraction, node editor, viewport drawing
native/                C source for _hlnav.dll (hlnav.c, hlareas.c, hlvis.c), hlvvis.exe (hlvvis.c) and build.py
tests/unit/            core tests (plain Python, no Blender)
tests/blender/         headless Blender tests
tests/fixtures/        scripts that build test scenes; vis/: a small compiled map and vvis's output, for hlvvis
tests/compile/         real-compile helpers (need the Authoring Tools)
tests/ingame/          in-game probe scripts (see its README)
scripts/               release helpers (the release zip)
demo/                  demo maps: demo_level.blend (a full small level), demo_level2.blend (the next map, for testing the safe room transition); made by the scripts in tests/fixtures/ (demo_level.blend was hand-edited since)
docs/                  user guides
docs/dev/              design notes, Hammer feature audit, playtest checklist
```

## Tests

Core tests (about 1 s; also run with `HAMMERLESS_NO_NATIVE=1` to test the pure-Python fallbacks):

```bash
python -m unittest tests.unit.test_core
```

Blender tests (headless):

```bash
"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe" --background --factory-startup --python tests/blender/run_tests.py
```

Real compile of a .blend (prints the full compiler log):

```bash
"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe" --background demo/demo_level.blend --python tests/compile/build_blend.py -- FAST
```

The demo .blend contains no game content: open it with the add-on and press *Settings > Folders & Game Data > Refresh Previews* to see the game's textures and models (read from your own L4D2 install).

## Native code

The nav generator's hot paths and the nav analysis (visibility, hiding spots) run in `core/_hlnav.dll`, built with zig (`pip install ziglang`; the build script runs `python -m ziglang`). The DLL must stay **bit-identical** in output to the Python code it replaces; every native stage has a Python twin used as the reference (and as the fallback when the DLL is missing).

```bash
python native/build.py            # both; or: python native/build.py hlvvis
```

`core/hlvvis.exe` is Hammerless's visibility compiler (*Settings > Compile > Vis Compiler*), a drop-in for L4D2's `vvis.exe`: same arguments, same log lines, and the same output. It implements the portal-flow method id Software published with Quake's vis (GPL), with every detail that changes the result matched to L4D2's vvis, plus speed-ups that don't change it (see the comment at the top of `native/hlvvis.c`). With several threads, vvis itself can flip a borderline pair or two between runs; ours does the same, and on the test maps most runs are byte-identical to a vvis run. If it fails, or for a map with fog-distance (radial) visibility, which it doesn't do yet, Hammerless runs Valve's vvis instead. After changing it, compare with vvis on real maps (needs the Authoring Tools):

```bash
python tests/compile/compare_vis.py path/to/map.vmf
```

## Build the extension zip

```bash
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --command extension build --source-dir hammerless --output-dir dist
```

The release zip is built with `python scripts/build_release_zip.py`: one zip for Blender 4.0 to 4.5 (the add-on in a `hammerless/` folder with both `bl_info` and the extension manifest). For installing while developing, any Blender 4.2 or newer can also build the extension zip (the commands here use the versions installed on the dev machine). Install it with Blender closed:

```bash
"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe" --command extension install-file --repo user_default --enable dist/hammerless-<version>.zip
```

## Matching the game

Hammerless reproduces several things the game normally does itself (nav generation, nav analysis, compile decisions). Each is checked against the game's own output, and the measured results are noted in the code (for example: nav analysis visibility matches 99.92% of area pairs, hiding spots match exactly). When changing them, compare against the game again; don't trust the Source SDK alone, since L4D2 differs from it in places.

## Contributing

- Run the core and Blender tests before committing; add a test for each fix where you can (`tests/unit/test_core.py` for `core/`, `tests/blender/run_tests.py` for anything using `bpy`).
- `core/` must not import `bpy`, so it stays testable without Blender.
- Anything that copies the game's own behaviour gets checked in the game (see *Matching the game* above), and the measurement goes in a comment.
- Test builds use their own map names (`hl_test_…`): a build replaces the map of the same name in the game folder.
- Note user-visible changes in [CHANGELOG.md](../CHANGELOG.md) under *Unreleased*. Things that need a person to check by hand go in [docs/dev/PLAYTEST_CHECKLIST.md](dev/PLAYTEST_CHECKLIST.md).
