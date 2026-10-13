# Hammerless knowledge base

This folder is the living record of what Hammerless knows about Left 4 Dead 2, its tools, and its own reimplementations of them: what each system does, what works, what doesn't and exactly where and why, what was measured (with numbers, maps and dates), and what was reverse-engineered and how. It is written for people and for AI assistants alike, so that a question can be answered by reading here instead of being researched again.

It differs from the other docs in the repo:

| Folder | Purpose |
|---|---|
| `docs/*.md`, `docs/systems/` | User and system documentation: how to use a feature, how a system works, fact-checked against the code. |
| `docs/dev/` | Design notes, audits and research write-ups from particular dates. |
| `docs/knowledge/` (this folder) | The accumulated facts: option by option, lump by lump, test by test, with dates and sources. Kept current as fixes land. |

## Organisation

One folder per area. Each folder has a `README.md` giving the overview and an index of its pages.

| Folder | Area | Status |
|---|---|---|
| `compilers/` | The map, vis and light compilers (hlvbsp, hlvvis, hlvrad) against Valve's vbsp, vvis, vrad: options, fallbacks, what ours do | written |
| `lighting/` | hlvrad internals: GPU mode, light layers, sky light, partial bakes, Cycles; vrad's quirks | to write |
| `vis/` | hlvvis internals: exact mode, merged windows, radial vis, the certifying tools | to write |
| `nav/` | Nav mesh format, the generator and analysis ports, the game's rules measured | to write |
| `director/` | AI Director: flow, wanderers vs mobs, item placement densities, spawn rules | to write |
| `logic/` | Logic nodes to VScript: the game's script API, events, NetProps, hooks | to write |
| `models/` | Model writer (SMD/QC, skeletal), game skeletons, mesh reading | to write |
| `sound/` | Sound control, new sounds in addon VPKs, sound.cache, mix layers | to write |
| `loading/` | Boot and map-load times, prestart, stringtable dictionary, sv_lan | to write |
| `game-facts/` | BSP format facts (version 21 lumps, write order), game limits, engine behaviours | to write |

## Conventions

Every page follows these rules so the facts stay traceable and checkable:

1. **Each fact says how it is known.** One of: "measured" (a test, with the map, the tool and the number), "read from source" (Valve's Source SDK 2013 / Alien Swarm source, with the file and function), "decoded from the binary" (which DLL or EXE, which function), "inferred / unverified" (an educated guess, marked as such). Never state an inference as a measurement.
2. **Each measurement carries a date and a map.** Write "2026-10-11, c1m1_hotel, 6 threads: 2.3 s". Users' personal maps are called "a large user map" (the same map throughout, about 1191 clusters and 3466 portals, 2.6 million square feet in its bigger version); never their file names.
3. **Each feature or option has a status** from this fixed set: `done` (works, verified), `partial` (works with listed gaps), `not done`, `known issue` (a defect with its symptom), `removed` (was there, taken out, with the date and reason), `in progress` (being worked on at the page's "Last checked" date).
4. **"Last checked" lines.** Every page ends with `Last checked: <date> against <commit or branch>`. When a page's facts are re-verified, the line is updated even if nothing else changed. A page whose date is far behind the code should be read with care.
5. **Link to code by path**, for example `native/hlvrad/direct.c`, `hammerless/core/compile.py` (`CompileJob._choose_steps`). Function names in backticks. Line numbers are avoided (they rot); function names are preferred.
6. **Cite Valve's sources as** "Valve's Source SDK 2013 / Alien Swarm source" with the file (`vrad.cpp`, `lightmap.cpp`). No local scratch or session paths in these pages.
7. **Tables for facts, prose for reasoning.** No marketing language. Numbers must be traceable to a test tool in `tests/` or to a named measurement.
8. **Stale statements are fixed or removed, not left.** When a fix lands, the page that mentioned the gap is updated in the same change set (or the next one), and the "Status" cell is changed. Removed features keep a one-line entry marked `removed` so the history isn't lost.
9. **Contradictions are listed, not silently resolved.** If the code and a doc disagree, the page says so under a "Contradictions found" heading until one of them is fixed.

## Keeping it current

- Code changes that touch a compiler, a fallback, an option table, a measured number or a game fact should update the matching page here. The commit that lands the fix is the right place.
- Each release: re-read the `Status` columns of `compilers/valve-fallbacks.md` and the "Known issues" sections, and bump "Last checked" on the pages that were verified.
- New measurements go in the page's table with the date; old numbers stay if they're still meaningful (before/after pairs), otherwise they move to a "History" note.
- When a new area is written, add it to the index above and change its status from "to write".

## Index of pages

### compilers/

| Page | What it covers |
|---|---|
| [compilers/README.md](compilers/README.md) | Overview of hlvbsp / hlvvis / hlvrad versus Valve's vbsp / vvis / vrad, the tool flow during a build, where each tool is invoked in the code |
| [compilers/valve-options.md](compilers/valve-options.md) | Every command-line option of Valve's vbsp, vvis and vrad: what it does (from Valve's source), how many values it takes, what Hammerless's compiler does with it; the L4D2-only options |
| [compilers/valve-fallbacks.md](compilers/valve-fallbacks.md) | Every case where Valve's tools still run instead of ours, with the decision and plan for each, and a Status column kept current |
| [compilers/what-ours-do.md](compilers/what-ours-do.md) | For each of our three compilers: what is byte-identical to Valve's (with the evidence), what differs or is better, what isn't done, known issues |

Other areas: to write (see the table above).

Last checked: 2026-10-13 against branch light-layers, commit 31a33e5.
