# Development

## Layout

```
hammerless/            the Blender extension (this folder is what gets zipped)
  blender_manifest.toml
  core/                pure Python, no bpy: geometry, VMF writer, compile pipeline, nav generator,
                       nav analysis, terrain, entities, textures, logic graph compiler
  core/_hlnav.dll      native nav / visibility code (built from native/)
  blender/             UI panels, operators, scene extraction, node editor, viewport drawing
native/                C source for _hlnav.dll (hlnav.c, hlareas.c, hlvis.c) and build.py
tests/unit/            core tests (plain Python, no Blender)
tests/blender/         headless Blender tests
tests/fixtures/        scripts that build test scenes
tests/compile/         real-compile helpers (need the Authoring Tools)
tests/ingame/          in-game probe scripts
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

## Native code

The nav generator's hot paths and the nav analysis (visibility, hiding spots) run in `core/_hlnav.dll`, built with zig (`pip install ziglang`; the build script runs `python -m ziglang`). The DLL must stay **bit-identical** in output to the Python code it replaces; every native stage has a Python twin used as the reference (and as the fallback when the DLL is missing).

```bash
python native/build.py
```

## Build the extension zip

```bash
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --command extension build --source-dir hammerless --output-dir dist
```

Any Blender 4.2 or newer can build it (the commands here use the versions installed on the dev machine). Install it with Blender closed:

```bash
"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe" --command extension install-file --repo user_default --enable dist/hammerless-0.1.0.zip
```

## Matching the game

Hammerless reproduces several things the game normally does itself (nav generation, nav analysis, compile decisions). Each is checked against the game's own output, and the measured results are noted in the code (for example: nav analysis visibility matches 99.92% of area pairs, hiding spots match exactly). When changing them, compare against the game again; don't trust the Source SDK alone, since L4D2 differs from it in places.
