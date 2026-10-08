"""Plays every logic example in the game and checks it did what it says.

Builds demo/demo_level.blend (never saved) with all the examples on its field, without lighting and with
the debug log of logic wires on, as map hl_test_examples; then drives the game through the test bench
and checks the wires that fired and the game's state. Prints PASS / FAIL / SKIP per example.

Run:  python tests/ingame/test_examples.py              (all)
      python tests/ingame/test_examples.py 21 22 42     (some; the map is built once per run)
      add --no-build to reuse the map already in the game
"""
from __future__ import annotations

import os
import re
import sys
import time
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from tests.ingame.bench import Bench, BenchError  # noqa: E402

MAP = "hl_test_examples"
BLEND = os.path.join(ROOT, "demo", "demo_level.blend")

# all the examples on the demo field (x 0..70 m, y -15..15 m): three rows of 14
PREP = '''
from mathutils import Vector
from hammerless.blender.logic import TREE
from hammerless.blender.logic_examples import all_examples, build_example
for t in [t for t in bpy.data.node_groups if t.bl_idname == TREE]:
    bpy.data.node_groups.remove(t)
for i, ex in enumerate(all_examples()):
    bpy.context.scene.cursor.location = Vector((3 + (i % 14) * 4.8, (-10, -3, 7)[i // 14], 0.0))
    build_example(bpy.context, i)
nuke = next(t for t in bpy.data.node_groups if t.name.endswith("Power-Up: Nuke"))
nuke.nodes["chance"].inputs["value"].value = 1.0
'''


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "node"


class Run:
    def __init__(self, b: Bench, titles: dict[int, str]):
        self.b, self.titles = b, titles
        self.results: dict[int, tuple[str, str]] = {}

    # names in the map
    def graph(self, n: int) -> str:
        return f"Example {n:02d}: {self.titles[n]}"

    def ent(self, n: int, node: str) -> str:
        return "hl_" + slug(f"{self.graph(n)}_{node}")

    def obj(self, n: int, key: str) -> str:
        return "hl_" + slug(f"Example {n:02d} {key}")

    # checks
    def fired(self, mark: int, n: int, *wires: str) -> list[str]:
        """The wires (e.g. 'button.pressed -> steps.in') of example n that did NOT fire since mark."""
        got = set(self.b.events_since(mark))
        return [w for w in wires if f"[{self.graph(n)}] {w}" not in got]

    def expect(self, n: int, mark: int, *wires: str, extra: str = ""):
        missing = self.fired(mark, n, *wires)
        if missing:
            raise AssertionError("didn't fire: " + "; ".join(missing))
        return extra

    def wait_fired(self, n: int, mark: int, *wires: str, timeout: float = 10.0):
        """Like expect, but waits up to timeout seconds for the wires (the Director updates some values slowly)."""
        t0 = time.time()
        while self.fired(mark, n, *wires) and time.time() - t0 < timeout:
            time.sleep(0.5)
        return self.expect(n, mark, *wires)

    def errors_since(self, mark: int) -> list[str]:
        return [l for l in self.b.lines_since(mark, "AN ERROR HAS OCCURED", "HAMMERLESS_SCRIPT")]


# ---------------------------------------------------------------- helpers in the game

GAME = r'''
::T <- {
    host = function() { return GetListenServerHost(); },
    ent = function(n) { return Entities.FindByName(null, n); },
    survivors = function() { local a = [], e = null; while (e = Entities.FindByClassname(e, "player")) if (e.IsSurvivor()) a.append(e); return a; },
    count = function(cls) { local n = 0, e = null; while (e = Entities.FindByClassname(e, cls)) n++; return n; },
    alive = function(cls) { local n = 0, e = null; while (e = Entities.FindByClassname(e, cls)) if (e.GetHealth() > 0) n++; return n; },
    zcount = function(t) { local n = 0, e = null; while (e = Entities.FindByClassname(e, "player")) if (!e.IsSurvivor() && !e.IsDead() && e.GetZombieType() == t) n++; return n; },
    inv = function(p) { local t = {}, s = {}; GetInvTable(p, t); foreach (k, v in t) s[k] <- v.GetClassname(); return s; },
    commons = function(n, near, r) {
        for (local i = 0; i < n; i++) SpawnEntityFromTable("infected", { origin = near + Vector(RandomFloat(-r, r), RandomFloat(-r, r), 8) });
    },
    kill_one = function() {
        local e = null, z = null;
        while (e = Entities.FindByClassname(e, "infected")) if (e.GetHealth() > 0) z = e;
        if (z) z.TakeDamage(1000, 2, GetListenServerHost());
        return z;
    },
    to = function(p, at) { p.SetOrigin(at); p.SetVelocity(Vector(0, 0, 0)); },
    center = function(n) { local e = Entities.FindByName(null, n); return e ? e.GetCenter() : null; },
};
return true;
'''


def check(run: Run, n: int, b: Bench):
    """One example's check. Returns a note; raises AssertionError on failure, SkipTest to skip."""
    m = b.mark
    if n == 1:
        return run.expect(1, run.load_mark, "start.start -> wait.in", "wait.out -> hello.show")
    if n == 2:
        return run.expect(2, run.left_mark, "left.happened -> wait.in")
    if n == 3:
        mk = m(); b.console(f"ent_fire {run.obj(3, 'button')} Press", wait=1)
        return run.expect(3, mk, "button.pressed -> open.go", "button.pressed -> msg.show")
    if n == 4:
        before = b.value("T.zcount(8)")
        mk = m(); b.run(f'T.to(T.host(), T.center("{run.ent(4, "room")}"));'); time.sleep(3)
        run.expect(4, mk, "room.first -> tank.spawn")
        return f"tanks {before} -> {b.value('T.zcount(8)')}"
    if n == 5:
        mk = m(); b.console(f"ent_fire {run.ent(5, 'timer')} FireTimer", wait=2)
        ev = [e for e in b.events_since(mk) if e.startswith(f"[{run.graph(5)}] pick.case")]
        assert ev, "Random picked nothing"
        return ev[0].split("] ", 1)[1]
    if n == 6:
        # the lead survivor far along the path: the When sees Path Progress pass the random point
        # (the random point can be early: it may already have passed when the survivors left the safe room)
        # clear the infected (a Tank from an earlier check pushes the survivors back), then walk the path
        b.run('local e = null; while (e = Entities.FindByClassname(e, "player")) if (!e.IsSurvivor()) e.TakeDamage(100000, 0, null); '
              'e = null; while (e = Entities.FindByClassname(e, "infected")) e.Kill();')
        b.walk_to(1.0)
        point = b.value('Entities.FindByName(null, "hl_logic").GetScriptScope().'
                        'HL_Random_example_06_a_tank_somewhere_along_the_way_where()')
        progress = b.value('Entities.FindByName(null, "hl_logic").GetScriptScope().HL_PathFurthest()')
        try:
            run.wait_fired(6, run.left_mark, "when.true -> tank.spawn", timeout=10)
        except AssertionError as e:
            raise AssertionError(f"{e} (random point {point:.2f}, progress {progress:.2f})")
        b.run("T.to(T.host(), Vector(1000, 0, 200));")
        return "Tank called as the leader passed the point"
    if n == 10:
        mk = m(); b.look_at(run.obj(10, "button")); b.use(3.5); b.console("noclip", wait=0.3)
        return run.expect(10, mk, "button.pressed -> waves.start", "button.pressed -> hold.show")
    if n == 12:
        # it started when the survivors left the safe room; skip most of the minute
        # (it may already have reached 0 during the run)
        b.run('if (HL_VarGet("countdown", 0) > 2) HL_VarSet("countdown", 2.0, false);'); time.sleep(4)
        return run.expect(12, run.left_mark, "left.happened -> set.run", "timer.tick -> tick.run",
                          "when.true -> horde.start", "when.true -> timer.stop", "when.true -> hide.run")
    if n == 7:
        mk = m(); b.console("give gascan", wait=2)
        run.expect(7, mk, "pickup.happened -> if.in", "if.true -> give.run")
        assert "slot4" in b.value("T.inv(T.host())"), "no pills"
        return "pills given"
    if n == 8:
        b.console("god 0", wait=0.3)
        b.run('local h = T.host(); h.SetHealth(30); h.TakeDamage(10, 0, Entities.FindByClassname(null, "worldspawn"));')
        time.sleep(1)
        mk = m(); b.run('local h = T.host(); h.TakeDamage(1, 0, Entities.FindByClassname(null, "worldspawn"));'); time.sleep(1)
        b.console("god 1", wait=0.3)
        return run.expect(8, mk, "if.true -> tell.run")
    if n == 11 or n == 19:
        mk = m(); b.run("T.commons(1, T.host().GetOrigin() + Vector(300, 0, 0), 10);"); time.sleep(1)
        b.run("T.kill_one();"); time.sleep(1)
        if n == 11:
            run.expect(11, mk, "killed.happened -> add.run")
            return "kills " + str(b.value('HL_VarGet("kills", 0)'))
        run.expect(19, mk, "common.happened -> add1.run")
        return "score " + str(b.value('HL_VarGet("score", 0)'))
    if n == 14 or n == 15:
        # ask the damage question the way the game does, with a table like the game's
        if n == 14:
            r = b.run('local s = T.survivors(); local dt = { Attacker = s[1], Victim = s[0], Inflictor = s[1], DamageDone = 10.0, '
                      'DamageType = 2, Weapon = null, Location = Vector(0, 0, 0) }; '
                      'return [::HL_Hook_AllowTakeDamage.call(::HL_Scope, dt), dt.DamageDone];')
            assert r[0] is False, f"friendly fire allowed: {r}"
            return "survivor-on-survivor refused"
        b.run("ZSpawn({ type = 8 });"); time.sleep(1)
        r = b.run('local t = null, e = null; while (e = Entities.FindByClassname(e, "player")) if (!e.IsSurvivor() && e.GetZombieType() == 8) t = e; '
                  'if (!t) return null; local dt = { Attacker = t, Victim = T.host(), Inflictor = t, DamageDone = 10.0, '
                  'DamageType = 128, Weapon = null, Location = Vector(0, 0, 0) }; '
                  'return [::HL_Hook_AllowTakeDamage.call(::HL_Scope, dt), dt.DamageDone];')
        assert r is not None, "no Tank to test with"
        assert r[1] == 20.0, f"Tank damage {r}"
        return "Tank's 10 damage became 20"
    if n == 16:
        mag, pis = b.value('[T.count("weapon_pistol_magnum_spawn"), T.count("weapon_pistol_spawn")]')
        assert mag > 0 and pis == 0, f"magnum spawns {mag}, pistol spawns {pis}"
        return f"{mag} magnum spawn(s), no pistol spawns"
    if n == 17:
        b.run("T.host().SetHealth(20);")
        mk = m(); b.console("say !heal", wait=2)
        run.expect(17, mk, "if.true -> heal.run")
        assert b.value("T.host().GetHealth()") == 100, "not healed"
        return "healed to 100"
    if n == 18:
        mk = m(); b.console(f"ent_fire {run.obj(18, 'button')} Press", wait=2)
        return run.expect(18, mk, "button.pressed -> reset.run", "hud.then -> timer.start", "timer.tick -> next.run",
                          "each.each -> hunter.spawn")
    if n == 20 or n == 33:
        timer = run.ent(n, "timer")
        if n == 33:
            # lift a survivor that isn't the leader far away
            b.run("local lead = Director.GetHighestFlowSurvivor(); foreach (s in T.survivors()) if (s != lead) "
                  "{ ::HLT_far <- s; s.SetOrigin(s.GetOrigin() + Vector(0, 0, 2500)); break; }")
        mk = m(); b.console(f"ent_fire {timer} FireTimer", wait=2)
        if n == 20:
            return run.expect(20, mk, "if_lead.true -> each.run", "if.true -> spawn.run")
        run.expect(33, mk, "if_far.true -> move.run")
        return f"straggler now {b.value('(::HLT_far.GetOrigin() - Director.GetHighestFlowSurvivor().GetOrigin()).Length()'):.0f} from the leader"
    if n == 21:
        mk = m(); b.console(f"ent_fire {run.obj(21, 'button')} Press", wait=10)
        return run.expect(21, mk, "steps.then1 -> alarm.play", "steps.then2 -> open.go", "open.arrived -> alarm.stop")
    if n == 22:
        mk = m(); b.console(f"ent_fire {run.obj(22, 'lever')} Press", wait=6)
        run.expect(22, mk, "is_up.true -> lift.go", "lift.arrived -> top.show")
        mk = m(); b.console(f"ent_fire {run.obj(22, 'lever')} Press", wait=6)
        return run.expect(22, mk, "is_up.false -> lift.back", "lift.returned -> bottom.show")
    if n == 23:
        mk = m(); b.console(f"ent_fire {run.obj(23, 'exit')} Press", wait=1)
        assert not run.fired(mk, 23, "exit.pressed -> gate.go") == [], "the locked exit opened the gate"
        mk = m(); b.console(*[f"ent_fire {run.obj(23, f'fuse{i}')} Press" for i in (1, 2, 3)], wait=1)
        run.expect(23, mk, "count.reached -> exit.unlock")
        mk = m(); b.console(f"ent_fire {run.obj(23, 'exit')} Press", wait=1)
        return run.expect(23, mk, "exit.pressed -> gate.go")
    if n == 24:
        mk = m(); b.console(f"ent_fire {run.obj(24, 'button')} Press", wait=1)
        assert run.fired(mk, 24, "power.out -> open.go"), "the gate opened without power"
        mk = m(); b.look_at(run.obj(24, "generator")); b.use(3.0)
        b.console("noclip", f"ent_fire {run.obj(24, 'button')} Press", wait=1)
        return run.expect(24, mk, "generator.pressed -> power.open", "goal.completed -> done.show", "power.out -> open.go")
    if n == 25:
        mk = m(); b.run(f'T.to(T.host(), T.center("{run.ent(25, "room")}") - Vector(0, 0, 40));'); time.sleep(3)
        run.expect(25, mk, "room.first -> wait.in", "wait.out -> alarm.play")
        mk = m(); b.run("T.to(T.host(), T.host().GetOrigin() + Vector(0, 400, 0));"); time.sleep(1.5)
        return run.expect(25, mk, "room.leave -> left.show", "room.empty -> alarm.stop")
    if n == 26:
        mk = m(); b.console(f"ent_fire {run.obj(26, 'button')} Press", wait=1)
        return run.expect(26, mk, "button.pressed -> wall.hide")
    if n == 27:
        mk = m(); b.run(f'local c = T.center("{run.ent(27, "pad")}"); foreach (s in T.survivors()) T.to(s, c - Vector(0, 0, 30));')
        time.sleep(2)
        run.expect(27, mk, "pad.all_inside -> go.go")
        return "all teleported"
    if n == 28:
        mk = m(); b.run('local w = Entities.FindByClassname(null, "witch"); if (w) w.TakeDamage(5000, 0, T.host());'); time.sleep(3)
        return run.expect(28, mk, "witch.killed -> open.go")
    if n == 29:
        before = b.value("T.zcount(8)")
        mk = m(); b.console(f"ent_fire {run.ent(29, 'timer')} FireTimer", wait=4)
        if before < 2:
            run.expect(29, mk, "if.true -> tank.spawn")
        else:
            run.expect(29, mk, "timer.tick -> if.in")
            assert run.fired(mk, 29, "if.true -> tank.spawn"), f"spawned a Tank with {before} alive"
        return f"tanks alive {before} -> {b.value('T.zcount(8)')}"
    if n == 30:
        return run.expect(30, run.load_mark, "start.start -> quiet.apply") + run.expect(30, run.left_mark, "left.happened -> quiet.reset")
    if n == 31:
        mk = m(); b.console(f"ent_fire {run.obj(31, 'door')} Open", wait=2)
        run.expect(31, mk, "once.out -> horde.start")
        mk = m(); b.console(f"ent_fire {run.obj(31, 'door')} Close", f"ent_fire {run.obj(31, 'door')} Open", wait=2)
        assert run.fired(mk, 31, "once.out -> horde.start"), "Once let a second opening through"
        return "first opening only"
    if n == 34:
        b.run("ZSpawn({ type = 3 });"); time.sleep(1)
        mk = m(); b.run('local e = null; while (e = Entities.FindByClassname(e, "player")) if (!e.IsSurvivor() && e.GetZombieType() == 3) '
                        'e.TakeDamage(1000, 2, T.host());'); time.sleep(2)
        run.expect(34, mk, "if.true -> say.run")
        return "kill line printed"
    if n == 35:
        b.run("T.commons(5, T.host().GetOrigin() + Vector(400, 0, 0), 100);"); time.sleep(1)
        mk = m(); b.console(f"ent_fire {run.obj(35, 'button')} Press", wait=2)
        run.expect(35, mk, "each.done -> msg.show")
        assert b.value('T.alive("infected")') == 0, "commons left"
        return "all commons removed"
    if n == 36:
        cans, real = b.value('[HL_VarGet("cans", -1), T.count("weapon_gascan")]')
        assert cans == real and cans >= 2, f"counted {cans}, there are {real}"
        return f"{cans} gas cans"
    if n == 37:
        mk = m(); b.console("say !tank", wait=3)
        run.expect(37, mk, "chat.asked -> if.in", "if.true -> tank.spawn", "if_s.true -> give.run")
        return "Tank called, adrenaline given"
    if n == 39:
        inv = b.value("T.inv(T.host())")
        assert inv.get("slot0") == "weapon_pumpshotgun", f"inventory {inv}"
        return "started with a pump shotgun"
    if n == 40:
        return run.expect(40, run.load_mark, "bots.asked -> avoid.run")
    if n == 41:
        b.console("scripted_user_func boost", wait=0.05)
        v = b.value("T.host().GetVelocity().z")
        assert v > 300, f"vertical speed {v}"
        return f"thrown up at {v:.0f}"
    if n == 42:
        b.run("T.commons(8, T.host().GetOrigin() + Vector(250, 0, 0), 150);"); time.sleep(2)
        b.run("T.kill_one();"); time.sleep(1.5)
        assert b.value('T.ent("hl_nuke") != null'), "no pickup dropped"
        b.run('T.to(T.host(), T.ent("hl_nuke").GetOrigin() - Vector(0, 0, 16));'); time.sleep(4)
        left = b.value('T.alive("infected")')
        assert b.value('HL_VarGet("cooling", false)'), "no cooldown after the nuke"
        return f"nuke went off, {left} commons left (outside the radius)"
    raise SkipTest("not automated (needs real play: " + SKIP_WHY.get(n, "timing") + ")")


class SkipTest(Exception):
    pass


SKIP_WHY = {9: "headshot kills", 13: "the Director getting furious", 32: "the Director's own mob timer",
            38: "the Director's own specials"}


def main(argv):
    titles = _titles()
    wanted = [int(a) for a in argv if a.isdigit()] or sorted(titles)
    with Bench() as b:
        b.load_mark = b.mark()
        if "--no-build" not in argv:
            t0 = time.time()
            b.build(BLEND, MAP, prep=PREP)
            print(f"built and loaded {MAP} in {time.time() - t0:.0f} s", flush=True)
        else:
            b.load(MAP, mode="hammerless")
        run = Run(b, titles)
        run.load_mark = b.load_mark
        b.console("sv_cheats 1", "god 1", "director_stop", "nb_blind 1", wait=1)
        b.run(GAME)
        # leave the safe room for real: the examples that wait for it see it happen
        run.left_mark = b.mark()
        b.run("foreach (s in T.survivors()) T.to(s, Vector(1000, 0, 200));")
        time.sleep(3)
        for n in wanted:
            mk = b.mark()
            try:
                b.run("foreach (s in T.survivors()) { s.SetHealth(100); }")
                note = check(run, n, b)
                errs = run.errors_since(mk)
                result = ("FAIL", "script errors: " + "; ".join(errs[:2])) if errs else ("PASS", note or "")
            except SkipTest as e:
                result = ("SKIP", str(e))
            except (AssertionError, BenchError) as e:
                result = ("FAIL", str(e))
            except Exception:
                result = ("FAIL", traceback.format_exc(limit=2))
            run.results[n] = result
            print(f"{result[0]:4} {n:2d} {titles[n]}: {result[1]}", flush=True)
        b.console("nb_blind 0", "director_start", "god 0", "sv_cheats 0", wait=0.5)
        errs = {}
        for l in b.lines_since(run.load_mark, "AN ERROR HAS OCCURED"):
            errs[l.strip()] = errs.get(l.strip(), 0) + 1
        if errs:
            print("\nScript errors during the run:")
            for e, c in sorted(errs.items(), key=lambda x: -x[1]):
                print(f"  {c:4d}x {e}")
    counts = {k: sum(1 for r in run.results.values() if r[0] == k) for k in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
    return 1 if counts["FAIL"] else 0


def _titles() -> dict[int, str]:
    """Example titles without Blender (the example modules import bpy only to build)."""
    import ast
    titles, n = {}, 0
    for name in ("logic_examples", "logic_examples_2", "logic_examples_3"):
        src = open(os.path.join(ROOT, "hammerless", "blender", name + ".py"), encoding="utf-8").read()
        for t in re.findall(r'dict\(title=("[^"]*"|\'[^\']*\')', src):
            n += 1
            titles[n] = ast.literal_eval(t)
    return titles


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
