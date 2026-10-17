"""In-game check of the Combat & Physics logic nodes: builds demo/demo_level.blend (never saved) with the graph in
tests/fixtures/combat_graph.py as hl_test_combat, then checks what it does in the running game: survivors' view
traces hit (beams and fire particles appear), commons spawned next to a survivor are blown up, and the script
runs without errors.

    python tests/ingame/test_combat_nodes.py [--no-build]
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
from tests.ingame.bench import Bench  # noqa: E402

MAP = "hl_test_combat"
PREP = f'''
import sys
sys.path.insert(0, r"{os.path.join(ROOT, "tests", "fixtures")}")
import combat_graph
from hammerless.blender.logic import new_tree
combat_graph.build(new_tree(bpy.context))
'''


def main():
    failed = []

    def check(name, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
        if not ok:
            failed.append(name)

    with Bench() as b:
        if "--no-build" in sys.argv:
            b.load(MAP)
        else:
            b.build(os.path.join(ROOT, "demo", "demo_level.blend"), MAP, prep=PREP)
        m = b.mark()
        b.console("sv_cheats 1", "director_stop", "nb_delete_all infected")
        time.sleep(2.5)
        hits = b.value('("HL_Var" in getroottable() && "hlt_hits" in ::HL_Var) ? ::HL_Var["hlt_hits"] : 0')
        check("view traces hit something", (hits or 0) > 0, f"hlt_hits {hits}")
        effects = b.run('local n = 0, e = null; while (e = Entities.FindByClassname(e, "env_beam")) n++; '
                        'local p = 0; e = null; while (e = Entities.FindByClassname(e, "info_particle_system")) p++; '
                        'return [n, p];')
        check("beams and particles appear", effects[0] > 0 and effects[1] > 0, f"beams, particles {effects}")
        b.run('local me = GetListenServerHost(); local f = me.GetForwardVector(); '
              'for (local i = 0; i < 4; i++) SpawnEntityFromTable("infected", { origin = me.GetOrigin() + '
              'Vector(f.x, f.y, 0) * (150 + i * 25) + Vector(0, 0, 8) }); return true;')
        time.sleep(3.0)
        blasts = b.value('("hlt_blasts" in ::HL_Var) ? ::HL_Var["hlt_blasts"] : 0')
        check("commons nearby are found and blown up", (blasts or 0) > 0, f"hlt_blasts {blasts}")
        alive = b.run('local n = 0, e = null; while (e = Entities.FindByClassnameWithin(e, "infected", '
                      'GetListenServerHost().GetOrigin(), 400)) if (e.GetHealth() > 0) n++; return n;')
        check("the explosions killed them", alive == 0, f"{alive} alive within 400")
        seen = b.value('("hlt_seen" in ::HL_Var) ? ::HL_Var["hlt_seen"] : null')
        check("Seen By gives an answer", seen in (True, False), f"hlt_seen {seen}")
        errors = b.lines_since(m, "AN ERROR")
        check("no script errors", not errors, "; ".join(errors[:3]))
    print(f"\n{'all passed' if not failed else str(len(failed)) + ' failed'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
