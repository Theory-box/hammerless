# Merged windows: the default vis mode, its safety argument and its proof tools

Status: `done`, default since 2026-10-11 (commit ac1ae02; fixes 2ae86d8; claim reworded f5e96d1). Code: `native/hlvvis.c`, section "merged-window flow" (`merged_flow`, `m_merge`, `hull2`, `outside_hull`, the sweep loop); `g_merge = 1` unless `-exact`.

## Why

The exact search follows every chain of portals separately; many chains reach the same portal by slightly different routes and each is worked out on its own. On styleguide_urban that is 534 million steps (46 s on 16 cores). The user asked (2026-10-10) for "4x, no hacks, modern/clever". After dominance pruning and point sampling failed (see README.md), Fable, consulted on 2026-10-10, proposed the criterion "superset of vvis + a ray oracle" instead of bit-identity, and the top idea: a **merged-window fixpoint per base flow**.

## The method

One state per portal per base flow instead of one per chain (`MState`): its **source window** is the convex hull (on the base portal's plane) of every source window that has reached it; its **pass window** the hull (on its own plane) of every pass window that reached it; its **might-see** bits the union of everything that arrived. A portal is expanded again only when its state grew:

- a point outside the stored hull by more than `g_merge_eps = 1e-4` is merged in (less is float noise);
- it is re-expanded at once only when a point lies more than `g_grow_eps = 0.01` outside the hull it was last expanded with; smaller growth marks it **dirty**;
- when the queue runs dry, every dirty portal is expanded once more with its exact hull (no widening); repeated until nothing grows (about 3 sweep rounds per base portal; past `g_max_sweeps = 1000` rounds the rest open fully);
- a hull of more than `MAXPTS = 64` points becomes the 64-gon of its supporting lines (a superset); a portal that keeps growing (`MAX_GROW = 64`) opens to the whole base and the whole portal (a superset, still safe). On the test maps 0 portals are opened fully after the sweep fix.

The queue is a ring of NPD+1 entries. Prints "merged windows: steps, pushes, portals opened fully". urban: 22 M steps instead of 534 M.

## Why it is safe (the corrected argument)

The first write-up said "bigger windows give bigger separator regions". Audit 3 (Fable, 2026-10-11, `docs/dev/audits/2026-10-11-audit3-vis.md`) pointed out that isn't valid for vvis's particular subset of separating planes. What does hold: **every separating plane of the bigger, merged windows also separates each chain's own smaller windows**, so the merged search keeps every chain's region of possible sight lines (its antipenumbra). Anything a single chain could see, the merged state can see too: the result is a superset of true visibility, up to float noise.

Why it stays tight: a hull of several routes can span an occluder, but the symmetric crosscheck removes a pair unless *both* directions are loose. Worst row seen: +12% on one c5m1 cluster; a synthetic worst case came out 0.00% looser. Skybox shells, water, fog and detail don't change this. It can also be tighter than vvis where vvis gives up on a chop needing more than 12 points and keeps the uncut window.

## The two bugs the audit found (fixed before shipping as default)

1. After `MAX_GROW` opened a portal fully, new might-see bits were never re-pushed (thousands of opens per campaign map; c1m1_hotel hid 2 pairs vs exact).
2. `outside_hull` ignored points under 0.01 outside, but separators were built from the smaller hull (a lever-arm effect). First fix: separators from S/W widened by 0.02 with a 2D double-precision `inflate()` on the convex hull (miter <= 90 degrees, 2-point bevel sharper, 64-gon if over MAXPTS; `tests/compile/vis_inflate_test.c`: 8 M points, worst 0.0019 out at 16k). A first float-3D miter version was unsound (needle tips from noisy edge directions; hairpin points ~1e7 out). Final fix (Fable, commit 2ae86d8): exact-union hulls plus dirty sweeps at queue-empty, which made the inflation unnecessary (`g_inflate = 0`; `HL_INFLATE=0.005` in the environment turns it back on for a strict guarantee at some speed cost). Times after: urban 4.4 -> 4.8 s, c1m1 1.5 -> 1.6 s, c5m1 2.9 -> 3.0 s.

## The exact claim

"Merged windows never hides an opening wider than vvis's own 0.01-unit tolerance." Audit 3 found the one case that bounds it: c1m1_hotel pair 493-1711 is hidden by merged windows; `vis_certify -all` gives 5 chains and a stabbing-line search finds a real line with **+0.0003 units** of clearance through chain 2663 2664 2666 2726 3642 3646 4150 4164 4174 4192 4198 4488 6160 6268 8096. Cause: `hull2` re-rounds hull points to float and `g_merge_eps` drops points under 1e-4 outside. That is far below vvis's 0.01 tolerance, through which vvis *keeps* pairs (so vvis shows things that aren't visible). Remaining merged-hidden pairs after the fixes: c1m1 2, c5m1 2 (22-369: no ray in 20 M; 743-1766: settled by `vis_certify -all` plus the stabbing optimiser: no line in 66 chains, best -1.7 to -1.9 units, while the control pair 688-766 on the same map found one at +18).

| Map (6 threads, 2026-10-11) | Clusters | Avg visible exact / merged | Looser | Hidden pairs vs exact |
|---|---|---|---|---|
| Large user map | 1194 | 285 / 288 | 1.02% | 3, all proven hidden |
| c1m1_hotel | 1813 | 215 / 218 | 0.33% | 2 (one is the 0.0003 case) |
| c5m1_waterfront | 1782 | 415 / 425 | 1.37% | 2, proven |
| Small suite maps | | identical | | 0 |

## The tools, and why each exists

| Tool | Role |
|---|---|
| `tests/compile/vis_check.py` + `vis_rays.c` | Proves pairs **visible**: random sight lines between random points on the two clusters' portals; a ray is open when every BSP leaf it crosses belongs to a cluster (cluster -1 = solid). One open line through a pair the result hides means the result would hide a real wall. Rays can't prove hidden and miss thin slits: a weak oracle on its own. |
| `tests/compile/vis_certify.c` | Proves pairs **hidden**: vvis's path search in doubles straight from the `.prt`, every tolerance on the keeping side (a cut window keeps everything with `d >= -EPS`, cut at `-EPS`; a plane is a separator only when both sides are beyond doubt, else dropped, which only widens; a chain never re-enters a cluster; no done-portal shortcut). If every chain comes out empty, no sight line exists (wider than about 1e-7 units: the `EPS_ON` slack). `-all` enumerates every surviving chain: **a single certificate chain proves nothing**, the real line may use another chain. Output: `PROVEN HIDDEN` or `UNDECIDED chain p0 p1 ...` (one-way portal numbers as hlvvis counts them) or `UNDECIDED budget`. |
| stabbing-line optimiser (development script) | Given an UNDECIDED chain, searches for a straight line through all its portals; reports the clearance (positive = a real line). |
| `tests/compile/vis_merged_suite.py` | `-exact` vs merged on many compiled maps (`--found` picks up every map the other tools left in %TEMP%): time, looseness, every merged-hidden pair ray-checked; `--timeout` per run. |
| `tests/compile/compare_vis.py` | hlvvis vs vvis (several runs each, full and `-fast`), the nondeterminism-aware match. |

**Release gate** for the vis compiler: `vis_merged_suite`, plus `vis_certify -all` on every pair merged hides and exact shows, plus a sight-line search over each of those chains.

Last checked: 2026-10-13 against branch light-layers, commit 41dc861.
