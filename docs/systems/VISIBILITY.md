# Visibility

The Source engine doesn't work out at run time what can be seen from where. A compiler does it ahead of time and stores the answer in the map: for every part of the map, the list of other parts that might be visible from it. The game uses that list to skip drawing everything else. Without it, everything renders all the time, which gets slow on big maps. Valve's compiler for this is `vvis`.

Hammerless has its own: **hlvvis** (`native/hlvvis.c`, built to `hammerless/core/hlvvis.exe`). It is the default vis compiler. It has two modes:

- **Exact** (*Match vvis Exactly*): vvis's own method, giving vvis's output byte for byte, about 3 times faster.
- **Merged windows** (the default): a different way to run the same search. Several times faster again, about 1% looser than vvis, and it never hides a view through an opening wider than vvis's own 0.01-unit tolerance.

This page explains how both work, how they were checked, what was measured, and what the **Visibility** panel does.

## What vis works with

The map compiler (vbsp, or Hammerless's hlvbsp) splits the map's open space into convex pieces and writes the openings between them to a portal file (`<map>.prt`). Neighbouring pieces are grouped into **clusters**. World brushes split the space; `func_detail` brushes don't, which is why detail matters so much for vis (see *Detail* below).

vis then answers one question for every pair of clusters: is there any straight line from one to the other that passes only through portals? If not, the pair is hidden. The result is the **PVS** (potentially visible set), one row of bits per cluster. vis also writes the **PAS** (potentially audible set): for each cluster, the union of the PVS rows of every cluster it can see.

Besides the visibility lump (all PVS rows, then all PAS rows, compressed with Quake's run-length scheme), vvis also touches a few other parts of the map, and hlvvis writes them the same way:

| Part | What vvis writes |
|---|---|
| Visibility (lump 4) | Header, PVS rows, PAS rows |
| Leaf distance to water (lump 46) | 65535 for every leaf when the map has no water; computed otherwise |
| Leaf ambient index and lighting (lumps 51/52/55/56) | Placeholders (one empty sample per leaf), only if all four are empty |
| Game lump (35) | Only its file offsets, which move when lumps before it change size |
| Leaf flags | Every leaf flagged when the map uses fog-distance (radial) vis |

The file layout follows L4D2's writer: lumps keep their input order, each lump and the file end are 4-byte aligned, and lumps never written keep offset 0.

## How the search works

hlvvis implements the portal-flow method id Software published with Quake's vis (GPL). It is our code. The Source SDK 2013 vvis was read for behaviour only.

1. **Rough pass.** For each one-way portal, every portal that lies at least partly in front of it, followed by a flood through the portal graph, gives a quick upper bound: the clusters it *might* see.
2. **Flow.** For each portal (the *base*), vis walks every chain of portals leading away from it. At each step it keeps a **source window** (the part of the base still in play) and a **pass window** (the part of the latest portal still in play). Separating planes built between the two cut down the next portal. When a window is cut to nothing, that chain is dead. Every cluster a live chain reaches is visible.
3. **Merge and crosscheck.** Portal results become cluster rows, then each pair is kept only if both directions agree (vvis's "crosscheck"; the PVS is fully symmetric). The PAS is built from the PVS.

### Exact mode: vvis byte for byte

Getting the same bits as vvis took matching every detail that changes the result. The ones that mattered, all measured against vvis's output:

- The epsilon is 0.01, compared in double precision. Coordinates are floats.
- A chopped window holds at most 12 points. A chop that would need more keeps its input unchanged.
- The source-side check uses the base portal's radius.
- Plane and portal conventions: the plane normal's construction, the forward portal's plane flipped, one-way portals numbered 2i and 2i+1.
- Portals are processed in order of how much they might see.
- When a later portal meets one that has already finished, it uses that portal's final result as a filter; if it hasn't finished, the rough bound. This shortcut is part of vvis's result: turning it off gives very different output.
- The compression and the file layout.

Fog-distance (radial) visibility is done too. For maps with an `env_fog_controller` far Z, or the `-radius_override` option, vvis also drops portals beyond the fog distance in the rough pass. That step was decoded from L4D2's `vvis_dll.dll` and reproduced with the same float operations, and it is byte-identical to vvis.

The speed-ups in exact mode don't change any decision: 64-bit visibility words limited to their non-empty range, separating planes built once per step and four at a time with SSE2 (the same float operations in the same order), chops four points at a time, and not descending into steps that can't reveal anything new. The build uses `-ffp-contract=off` so the compiler can't fuse float operations.

Two more speed-ups were measured and left out on purpose: exploring each cluster's portals narrowest first, and handing branches of big portals to idle threads. Neither changes a single portal's result, but both change *when* portals finish, and with threads that decides borderline pairs differently from vvis's usual timing. On a heavy map they put 26 cells off vvis instead of 0 to 4. They cost about 15% speed to leave out.

### vvis is not deterministic

vvis with several threads doesn't give the same answer every time. Whether a portal meets a neighbour that has finished (and uses its tighter result) or one still working (and uses the rough bound) depends on timing, and that can flip a borderline pair.

Measured on a large user map (about 330,000 PVS bits): 8 runs of vvis gave 6 identical results; across all 8, only 4 cells ever varied. The PAS was identical in the runs compared.

So "matches vvis" means: byte-identical to one of vvis's own runs, or differing only in cells vvis itself varies. hlvvis has the same behaviour with threads. With `-threads 1` it is fully repeatable.

### Merged windows: the default

The exact search follows every chain separately. Many chains reach the same portal through slightly different routes, and each is worked out on its own. On Valve's styleguide_urban that means hundreds of millions of steps.

The merged method keeps **one state per portal per base flow** instead of one per chain:

- its source window is the convex hull of every source window that has reached it,
- its pass window is the convex hull of every pass window that has reached it,
- its might-see bits are the union of everything that arrived.

A portal is expanded again only when its state grows. Small growth (under 0.01 units) doesn't re-expand it at once: it is marked, and when the queue runs dry, every marked portal is expanded once more with its exact hull. This repeats until nothing grows. A portal that keeps growing is opened to the whole base and the whole portal, which is a superset and still safe.

**Why it is safe.** Every separating plane of the bigger, merged windows also separates each chain's own smaller windows. So the merged search keeps every chain's region of possible sight lines: anything a single chain could see, the merged state can see too. The result is a superset of true visibility.

The one caveat is float noise. Hulls are stored as floats, and points less than 1e-4 outside a hull are treated as inside. An audit found one case: on c1m1_hotel, pair 493-1711 is hidden by merged windows, and a search found a real sight line through it with 0.0003 units of clearance. That is far below vvis's own 0.01 tolerance (vvis *keeps* pairs seen only through openings that thin, so it shows things that aren't visible). The claim is therefore: **merged windows never hide an opening wider than vvis's own 0.01-unit tolerance.** Setting the environment variable `HL_INFLATE=0.005` makes it strict, at some cost in speed.

**Why it stays tight.** A hull of several routes can span an occluder, but the crosscheck removes a pair unless both directions are loose. On the test maps it draws about 1% more than vvis. It can also be tighter than vvis in one place: where vvis gives up on a chop that needs more than 12 points and keeps the uncut window.

## How it was checked

| Tool | What it does |
|---|---|
| `tests/compile/compare_vis.py` | Compiles a VMF once, runs vvis and hlvvis several times each, full and `-fast`. A result matches when the file is byte-identical to one of vvis's runs; otherwise it counts the cells vvis never produced. |
| `tests/compile/vis_merged_suite.py` | Runs hlvvis `-exact` and the default on many compiled maps; reports time and looseness, and checks every pair the merged result hides. |
| `tests/compile/vis_check.py` | Compares a result with a reference and fires random sight lines (`vis_rays.c`) at each pair the result hides. One line through open space proves the pair visible, so the result would be wrong. Pairs no ray reaches go to `vis_certify`. |
| `tests/compile/vis_certify.c` | Proves a pair hidden. vvis's path search in doubles, straight from the `.prt`, with every tolerance on the keeping side, without vvis's done-portal shortcut. If every chain comes out empty, no sight line exists (wider than about 1e-7 units). Otherwise it prints the chain to try. |
| Unit tests (`tests/unit/test_core.py`) | hlvvis on two small compiled fixtures (`tests/fixtures/vis/rooms_portal.zip`: three rooms and an area portal; `rooms_fog.zip`: radial vis) must write the same file as vvis. |

Random rays are a weak check on their own: they miss thin slits. And one certificate chain proves nothing, because a real line may use another chain. The release gate is the suite, plus `vis_certify -all` (every surviving chain of a pair) and a sight-line search over each of those chains.

### Exact mode results

8 test maps (a large user map, the two demo maps, a level with water, the user map with Auto Detail off and with everything detail, rooms with an area portal, outdoor terrain), 3 runs each, full and fast: every file byte-identical to vvis's, except the heaviest map (Auto Detail off), where 0, 2 and 0 cells fell outside vvis's own 3-run variation. Radial vis is byte-identical on its fixtures, full and fast.

### Merged windows results

From the vis audit (2026-10-11, 6 threads):

| Map | Clusters | Avg visible, exact | Avg visible, merged | Looser | Time, exact to merged |
|---|---|---|---|---|---|
| Large user map | 1194 | 285 | 288 | 1.02% | |
| c1m1_hotel | 1813 | 215 | 218 | 0.33% | 18.4 s to 2.3 s |
| c5m1_waterfront | 1782 | 415 | 425 | 1.37% | 32.8 s to 5.0 s |
| Small suite maps | | | | identical | |

Every pair merged hides that exact shows was proven hidden by `vis_certify`, or had no line through it wider than the 0.01 tolerance (the c1m1 pair above). One c5m1 pair (743-1766) needed the full search: no line was found through any of its 66 chains, while a control pair on the same map did find one.

For comparison, vvis's `-fast` mode (rough pass only) was 164% to 186% looser on the same maps.

## Measured speeds

16-core PC, 16 threads unless noted.

| Map | vvis | hlvvis exact | hlvvis merged |
|---|---|---|---|
| Large user map (1191 clusters, 3466 portals; an earlier build of the same map) | 14.1 s | 5.2 s | |
| Same map, Auto Detail off | 118 s | 38 s | |
| styleguide_urban (radial; 1262 clusters, 4129 portals) | 2 min 19 s | 46 s | 4.8 s |
| c5m1_waterfront | | 18 s | 3 s |
| styleguide_swamp, radial, `-fast` | 23 s | 2 s | |
| styleguide_swamp, radial, full | 475 s | 120 s | |

On the large user map, vvis keeps 14 to 15 of 16 cores busy for three quarters of the run, then trails off to one core for about 3 seconds. Almost all its time (99%) goes to the flow; the rough pass takes 0.1 s.

Not every idea worked. Seeding the search with sight lines already proven by rays cut only about 3% of the work, because the early exit it helps needs *all* remaining might-see bits proven, and most of them are truly hidden. Skipping steps dominated by an earlier one was fast but hid a pair vvis shows, so it isn't exact and was dropped. Published faster methods (sampling, GPU rays) all allow misses, which would show as missing walls in game, so none of them are used.

## Lighting alongside vis

vis is the longest step of a build that uses every core, so the light compiler doesn't wait for it when it doesn't have to.

When the Hammerless light compiler runs on the graphics card with ray-traced bounce, the build copies the map before vis starts and lights the copy at the same time (`hlvrad -visfrom <map> -visthreads <n>`). A face's light and bounce don't need vis: vis only spares rays a light can't reach, and the lightmaps came out the same to the byte. The light compiler waits for vis only for what does need it: prop lighting and the sky's leaves. When both are done, the lit copy replaces the map, which is byte-identical to lighting after vis.

It doesn't start early in two cases, and lights after vis as before:

- vrad's own patch bounce, which needs vis;
- maps with fog-distance (radial) vis, which changes which leaves see the sky.

Total CPU work is fixed, so contention is the real limit. Giving the light compiler fewer threads during vis was measured and made things *worse*. A fresh Build & Play of a large user map, to survivors in game, went from 29.0 s to 26.6 s with these changes (35.3 s that morning, before the stringtable dictionary fix as well). The build log ends with a timeline of when each step ran.

## Using it

### The Visibility panel

| Setting | What it does |
|---|---|
| **Quality** | *Off*: no vis; everything always renders (slow in game on big maps). *Fast*: the rough pass only (`-fast`). *Full*: the full pass, best in-game performance. The Map panel's Quality sets it too: Quick is Off, Fast is Fast, Normal and up are Full. |
| **Vis Compiler** | *Hammerless (faster)*, the default, or *Valve vvis*. |
| **Match vvis Exactly** | Hammerless only. Runs the exact mode (`-exact`): vvis's result byte for byte, about 3 times faster than vvis but several times slower than merged windows. |
| **Extra vvis Options** | Passed to the compiler. hlvvis knows `-fast`, `-threads`, `-radius_override` and `-exact`; other options make the build use Valve's vvis, with a note in the log. |
| **Compute Visibility** | Compiles the map and its visibility only, without lighting or the nav mesh. Build and Build & Play reuse it. |

Bake Lighting uses vis in Fast mode; the next Build adds the full vis and keeps the lighting.

With Vis Compiler: Hammerless, the build shows vis progress and the time left ("Vis 42%, about 3 s left") in the Map panel header and the status bar. hlvvis prints one progress line per percent, weighting each portal by an estimate of its cost.

### Views

The **View** dropdown in the Visibility panel draws over the viewport from the last build. Nothing is added to the scene or saved. *See Through Walls* draws over the scene instead of behind it.

| View | What it shows |
|---|---|
| **Portals** | The openings vis works through: how the map was split up. Slivers (portals under 16 square units, usually two brushes that almost line up) are red. |
| **Rendering Load** | The map's faces coloured by how many faces the game draws from there. Red is where frame rate suffers first. A button puts the 3D cursor on the heaviest spot. Works whichever compiler built the map. |
| **Vis Cost** | Portals coloured by the time hlvvis spent on them, and the Blender objects whose brushes those portals were split along, with their share of the vis time and a Select button. Needs Vis Compiler: Hammerless (it writes `<map>.viscost`). |

### Detail

Brushes that don't need to block sight should be `func_detail`. A brush's **Detail** setting (Selected Object panel) makes it detail, keeps it a world brush, or follows the map's Auto Detail rule. Detail brushes don't seal the map, so they need Auto Seal or a sealed shell. In a test room, 36 overlapping boxes made detail cut the map's vis pieces from 190 to 48. Vis Cost is the quickest way to find what to make detail: it names the objects the slow portals come from. A Control Volume with *Visibility* on turns the brushes inside it into detail.

## Valve's vvis

Valve's vvis is not a fallback by design. If hlvvis exits with an error, the build runs Valve's vvis (hlvvis only replaces the map at the very end, so the map and portals are untouched) and puts a `!!` line in the log. That is treated as a bug to fix, not a normal path. Valve's vvis also runs when hlvvis isn't installed or is given an option it doesn't know; the log says so.

## Limits and what's left

- **Merged windows aren't vvis's bits.** About 1% looser, and a pair seen only through an opening thinner than 0.01 units may be hidden (as vvis does the opposite). Use *Match vvis Exactly* when you need vvis's own output.
- **Exact mode with threads varies** like vvis does: a borderline pair or two between runs. `-threads 1` is repeatable.
- **No reuse between compiles.** Every build runs vis from scratch. Reusing the work for portals a change didn't touch is planned.
- **Exact mode speed.** A third-party tool (VVIS++) runs the large user map in 3.3 s, but it isn't L4D2-exact (it hides 82 pairs vvis shows). Exact mode is 5.2 s there; merged windows is the fast path.
- Switching between Hammerless's and Valve's vis compiler doesn't on its own trigger a rebuild: they count as giving the same map, so an unchanged map skips vis. To compare with vvis, change something or start fresh.
