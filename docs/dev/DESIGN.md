# Hammerless: Blender → L4D2 Level Addon (Design)

*Working title.* A self-contained Blender extension that turns a Blender scene into a playable Left 4 Dead 2 map: tag objects, press **Build & Play**, and the game opens on your level.

See [HAMMER_AUDIT.md](HAMMER_AUDIT.md) for the full feature checklist this design covers.

---

## 1. Goals

1. **Build & Play in one click.** Blender scene → VMF → compiled BSP → game launched at the map.
2. **Hammer-level control inside Blender.** Every Hammer feature has a Blender equivalent (native Blender feature, our panel, or our operator).
3. **Self-contained.** One extension zip; bundled Python wheels; no separate addons to install. External requirements are only the game + L4D2 Authoring Tools (for vbsp/vvis/vrad/studiomdl), which we can't redistribute.
4. **Don't reinvent.** Bundle or port from existing tools wherever licences allow.
5. **Testable.** Every system has automated tests. Most run without Blender or the game.

Non-goals (for now): other Source games (keep the core game-agnostic so they're easy later), editing BSPs, replacing in-game nav editing.

## 2. Platform & licence

- **Blender 4.2+** extension format (`blender_manifest.toml`). Python 3.11 on 4.2–5.0 and 3.13 on 5.1+, so bundled compiled wheels need both.
- **Licence: GPL-3.0-or-later.** This is normal for Blender addons, and it lets us port code from the GPL tools below.
- Windows is the primary target (the compilers and the game are Windows builds). The core should still run on Linux/macOS for tests and CI.

## 3. What we reuse

| Need | Use | How | Licence |
|---|---|---|---|
| VMF read/write, FGD parsing, VMT, VTF (incl. DXT), VPK, BSP read, keyvalues, game filesystem, instance collapsing, SMD/DMX, engine command injection | **srctools** (TeamSpen210) | **Bundle** per-platform wheels (cp311 + cp313; win/linux/mac x64 + mac arm64) | MIT |
| L4D2 entity definitions | **srctools' built-in FGD database** (compiled from HammerAddons' unified FGDs, L4D2-tagged) | Bundled via srctools; drives the auto-generated entity panels | MIT (via srctools) |
| SMD/DMX export & QC compile flow | **Blender Source Tools** | Port the relevant export code (not a dependency) | GPL-2.0+ |
| QC generation, experimental brush + displacement VMF export | **SourceOps** | Reference / port | GPL-3.0 |
| Convex hull, convex cut, hulls → VMF brushes | **Source Engine Collision Tools** | Reference / port | GPL-3.0 |
| Brush modelling UX (box builder, cube cut, grid, auto UV) | **Anvil Level Design** | UX reference | GPL-3.0 |
| Brush plane + texture axis math | **io_export_qmap**, **leaxcx/blender-vmf-export** | Reference | GPL-3.0 / MIT |
| Nav auto-generation, compile-error explanations | **CompilePal** (`NavProcess.cs`, `ErrorFinder.cs`) | Port the approach | GPL-3.0 |
| Previewing game models/materials in Blender | **Plumber** / **SourceIO** | Optional later. They ship native code, so either port a narrow piece or detect them if the user has them installed | GPL-3.0 / MIT |
| Compiling | **vbsp, vvis, vrad, studiomdl, bspzip, vpk** from L4D2 Authoring Tools | Call as subprocesses | Valve (user installs) |

**Avoid:** vendoring HammerAddons code (no licence found; srctools' FGD database already covers us) and Crowbar (CC BY-SA, not GPL-compatible; call `studiomdl.exe` directly instead).

## 4. Architecture

```
┌──────────────── Blender (bpy) ────────────────┐
│  UI: panels, operators, tagging, validators   │
│  Extractor: scene ──► MapIR                   │
└───────────────────────┬───────────────────────┘
                        │  MapIR (plain Python dataclasses, no bpy)
┌───────────────────────▼───────────────────────┐
│  Core (pure Python + srctools, NO bpy)        │
│   geometry/  convexity, planes, tex axes,     │
│              displacement sampling, sealing   │
│   export/    MapIR ──► VMF (srctools.vmf)     │
│   assets/    materials ──► VMT/VTF,           │
│              meshes ──► SMD + QC              │
│   compile/   vbsp/vvis/vrad/studiomdl runner, │
│              log parser, .lin reader          │
│   game/      locate install, launch, nav gen, │
│              pack (bspzip / VPK)              │
└───────────────────────────────────────────────┘
```

**The key decision is the MapIR boundary.** The Blender side only *extracts* data into a plain intermediate representation (brushes with planes and face materials, entities with keyvalues, props, terrain heightfields, decals, lights). Everything after that is pure Python that never imports `bpy`. That gives us:
- fast unit tests with plain `pytest`, no Blender needed
- a core that could later power a CLI or other game targets
- a Blender layer that stays thin (UI + extraction)

### Proposed repo layout
```
hammerless/
  blender_manifest.toml
  __init__.py              # register/unregister
  ui/                      # panels, menus, gizmos
  ops/                     # operators (add brush, build & play, validate…)
  props/                   # PropertyGroups (tags, entity keyvalues, map settings)
  extract/                 # bpy scene → MapIR
  core/
    ir.py                  # MapIR dataclasses
    geometry/  export/  assets/  compile/  game/
  wheels/                  # bundled srctools + deps
tests/
  unit/                    # pytest, no Blender
  blender/                 # run inside blender --background
  compile/                 # needs Authoring Tools
  ingame/                  # needs the game
  fixtures/                # scripts that BUILD test scenes (not binary .blend)
docs/
```

## 5. Tagging system

Every object gets a `hammer` property group with a **role**:

| Role | Meaning | Exported as |
|---|---|---|
| *Auto* (default) | Classify automatically: Blender lights → light entities; convex meshes → brushes; non-convex meshes → props (with a warning listing them) | — |
| **Brush** | World geometry. Must be convex (one object may hold several convex parts; each loose part = one brush) | `world` solids |
| **Brush Entity** | Brush(es) belonging to an entity, e.g. `func_detail`, `trigger_once`, `func_door` | entity + solids |
| **Terrain** | Heightfield-like mesh | displacements on nodraw brushes |
| **Prop** (static / dynamic / physics) | Custom or game model | compiled `.mdl` + `prop_*` entity |
| **Point Entity** | Usually an Empty; classname picked from the FGD | entity |
| **Decal / Overlay** | Projected image | `infodecal` / `info_overlay` |
| **Ignore** | Reference geometry, not exported | — |

- **Collections can set a default role** for everything inside them (e.g. a `World` collection = Brush, `Props` = Prop static). You can tag a whole layout at once, and per-object overrides still work.
- **Face-level data** (Edit Mode panel, like Hammer's Face Edit Sheet): material from Blender material slots; texture scale/shift/rotation/lightmap scale stored as face attributes.
- **Entity keyvalues** are generated from the FGD at runtime, so selecting a classname builds its property panel automatically (types, choices, spawnflags, I/O outputs). That covers hundreds of entities without hand-writing UI.
- **Presets** for common L4D2 bundles: start safe room, end safe room, finale, weapon cache, horde trigger.

## 6. Brush-style modelling

Blender's existing brush addons (Level Buddy, Anvil) produce plain meshes or booleaned geometry, not Source brushes. So we build our own brush toolset, borrowing Anvil's UX:
- **Add Brush** primitives (block, wedge, cylinder, spike, arch, stairs) snapped to the Hammer grid
- **Hammer grid** mode: unit scale (1 Hammer unit ≈ 1 inch; 1 m ≈ 39.37 u), snap increments 1–512
- **Live validator** overlay: red = non-convex / non-planar / off-grid
- **Clip**, **Make Hollow**, **Carve** (convex-safe), **Tie to entity**
- Tool-texture palette (nodraw, clip, hint, skip, trigger…) with Hammer's colours

## 7. Build pipeline (Build & Play)

1. **Validate** (our "Check for problems"). Block on errors, show warnings.
2. **Extract** scene → MapIR.
3. **Assets**: convert changed textures → VTF/VMT and changed meshes → SMD+QC → studiomdl (cached by content hash, so rebuilds are fast).
4. **Seal**: optionally wrap the map in an auto skybox shell (a big nodraw/skybox hollow box) so beginners never get leaks.
5. **Write VMF** (srctools).
6. **Compile** vbsp → vvis → vrad with a preset (Fast / Normal / Final); stream logs into Blender and parse them for errors. On leak, import the `.lin` as a red line and frame it.
7. **Pack** custom content (bspzip or addon VPK).
8. **Launch**: `left4dead2.exe -game left4dead2 -novid -console -condebug +map <name>`. Optional first-run **nav generation** pass (the CompilePal approach: temporary map cfg with `nav_generate`, tail log for `.nav' saved.`, then relaunch). If the game is already running, use `-hijack` / `send_engine_command` to reload the map without restarting.

## 8. Testing strategy

Five layers, cheapest first. Layers 1–2 run on every change; 3–5 run when tools/game are present.

| Layer | What | Runs with | Example checks |
|---|---|---|---|
| **1. Unit** | Core geometry & export | `pytest` | Box mesh → 6 correct planes; non-convex detected; tex axes match Hammer's world-align; FGD → panel schema; VMF re-parses with srctools |
| **2. Blender headless** | Extraction, tagging, operators | `blender --background --factory-startup --python tests/blender/run.py` | Fixture scene extracts to expected MapIR; collection roles inherit; operators don't crash |
| **3. Golden files** | Whole-scene output stability | pytest snapshot diff | Fixture scene → VMF identical to approved copy (review diffs when intentional) |
| **4. Compile** | Real toolchain | Authoring Tools | vbsp/vvis/vrad exit clean, no errors in parsed log; leak fixture *does* leak and yields `.lin`; read BSP back with srctools to check entity count, packed files |
| **5. In-game smoke** | Map actually loads & plays | L4D2 | Launch with `-condebug` + test cfg that runs checks then `quit`; assert no "missing model/material" lines; nav_generate succeeds; survivors spawn |

Plus **manual checklist** for things we can't automate: opens cleanly in Hammer (round-trip), looks right in-game.

**Fixtures are Python scripts that build scenes**, not binary `.blend` files, so they diff in git and never go stale:
`box_room` · `leak` · `nonconvex` · `terrain` · `props_custom` · `entities_io` · `decals` · `mini_campaign` (two maps with landmark transition, safe rooms, finale).

## 9. Roadmap

Each phase ends with its tests passing and a fixture that plays in-game.

| Phase | Scope | Done when |
|---|---|---|
| **0. Setup** | Repo, manifest, bundled srctools wheel, test harness (pytest + headless Blender), game/tool path detection | Addon installs in Blender 4.2 & 4.5; empty test suite green |
| **1. Greybox MVP** | MapIR; convex mesh → brush; tool textures + game materials by name; lights; info_player_start / survivor positions; auto sky shell; compile runner + log parse; launch game | `box_room` fixture: click Build & Play, walk around in L4D2 |
| **2. Brush tools** | Add Brush primitives, Hammer grid, live validator, clip/hollow, face edit panel | Can build a multi-room greybox without leaving Blender |
| **3. Entities** | FGD-driven property panels, spawnflags, I/O editor, brush entities, L4D2 presets (safe rooms, director, weapons, changelevel) | `entities_io` + start/end safe room fixture playable with working doors & transition |
| **4. Assets** | Custom materials → VTF/VMT, custom props → SMD/QC/studiomdl, packing (bspzip/VPK), content-hash cache | `props_custom` fixture shows custom model & texture in-game, packed in BSP |
| **5. Terrain & decals** | Heightfield → displacements (alpha blend from vertex paint), infodecal, info_overlay | `terrain` + `decals` fixtures correct in-game |
| **6. Polish** | Leak visualiser, nav auto-gen, env_cubemap auto-place + buildcubemaps, compile presets, game asset browser, instances, cordon | `mini_campaign` plays start to finish |

### Status (end of 2026-09-23)

Everything below is implemented, tested (43 unit, 19 headless Blender tests) and verified in L4D2 unless noted.

| Area | State |
|---|---|
| Brushes (convex meshes, hull option, validation), terrain displacements, auto seal | ✅ in-game |
| Entities: ~45 curated L4D2 entities, keyvalues, **outputs (I/O)** | ✅ in-game |
| Presets: start/end safe rooms (+ nav regions, lights, landmarks), Horde Trigger, Horde Button, Crescendo Button, Tank Ambush, Zombie Spawn Area, Ladder | ✅ in-game except Tank Ambush and Ladder (untested) |
| Nav: generate after load, mark PLAYER_START / CHECKPOINT / OBSCURED, save, reload | ✅ in-game |
| Director: map-wide options, crescendos (ScriptedPanicEvent), limits kept during crescendos | ✅ in-game |
| Materials: game materials, custom image textures (VTF writer), surfaces/friction (patch VMTs) | ✅ in-game (ice feel untested) |
| Settings panels: Compile (custom), Lighting & Sky, Fog, AI Director, Game Window (monitor, size, difficulty), Debug, Advanced | ✅ |
| Blender previews: game textures (VTF reader, world-projected), **3D models** (MDL/VVD/VTX reader) | ✅ |
| Browsers: materials (8k), models (5.5k), skies | ✅ |
| Testing: Debug Log, Bot Walkthrough Test (no cheats), in-game probes in tests/ingame | ✅ |

Known issues / next candidates: **one-load Build & Play** (auto-detect when nav needs regenerating; self-mark nav at map spawn to drop a reload); **non-convex shapes**: per-object Split into brushes / Custom model (studiomdl, auto collision) / Convex hull, with Auto; finales and gauntlets; preview auto-update when Order/population changes; items from outside the end safe room carried to the next map; Blender light brightness tuning; brush tools (clip/hollow); FGD-driven entity panels (all L4D2 entities).

**Deviation from §3:** srctools still isn't bundled; VMF/VPK/VTF (read+write)/MDL are small in-house modules, so nothing is downloaded.

## 10. Open questions

- **Texture alignment**: store Hammer-style face attributes (exact Hammer parity) or derive from Blender UVs (more natural in Blender)? Proposal: default world-aligned like Hammer; optional "fit to UVs" per face, since not every UV layout is expressible in Source's planar mapping.
- **Non-convex brushes**: auto-decompose (convenient but can make messy brush splits) vs. auto-prop (clean but props don't block vis)? Proposal: auto-prop + warning in phase 1; decomposition later.
- **Game asset previews**: port a minimal MDL/VTF loader via srctools vs. optional Plumber integration.
- **Name**: "Hammerless" is a placeholder.

## Prerequisites (user)

- Install **Left 4 Dead 2 Authoring Tools** (Steam → Library → Tools). Currently *not* installed on this machine: no vbsp/vvis/vrad/hammer in `Left 4 Dead 2\bin`.
- L4D2 found at `C:\Program Files (x86)\Steam\steamapps\common\Left 4 Dead 2`; Blender 4.2–4.5 installed.
