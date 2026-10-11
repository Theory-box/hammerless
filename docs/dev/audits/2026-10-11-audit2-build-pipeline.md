# Audit 2 (2026-10-11): the build pipeline

By a Fable auditor, read-only (scratch compiles, no game).

**The "Average clusters visible: 891" mystery:** not our vis. `<map>.log` is Valve's tools' own log; hlvbsp, hlvvis
and hlvrad never write it, so it kept the numbers of an earlier build that ran Valve's `vvis -fast` (reproduced: 895,
0.00%, 2 s). The 03:04 build wrote no .log at all; the BSP the game loaded has ~510 clusters visible per cluster
(hlvvis full: 519; vvis: 507).

Findings:
1. No build log on disk: Hammerless's log is the Blender text block `hammerless_log`, cleared each build; Valve's
   stale `<map>.log` is all that survives. Fix: CompileJob writes `<base>.build.log` (commands, fallbacks, timings),
   truncates Valve's .log at job start; show the vis/light tool and result in Problems.
2. Two .blend files in one folder with the same Map Name share one build folder and owner record silently
   (ops.py:215, 445-448). Fix: owner = (.blend path, scene); warn on mismatch.
3. Fallbacks leave no trace (text block only; shaky builds write no .built.*). Fix: record them in .built.opts and
   the on-disk log.
4. Switching the vis/map/light compiler doesn't rebuild (`_opts_rest` strips *_tool from the stamp,
   compile.py:142): switching to Valve's vvis to double-check says "Map unchanged: skipped". Fix: keep the tools in
   the stamp (the smart add-vis path already exists).
5. Build & Play prestarts the game before export; a failed or cancelled compile leaves the game on the old map
   without a note.
6. Checked and sound: fog farz, radius, -visfrom (lumps copied on exit 0 only), .built.prt after a good vbsp, stale
   records deleted at start, assets in the stamp, new entity in old solid -> full. Entity-only smart builds
   regenerate the nav every time (slow, not wrong).
