"""The Car Alarm preset in the game: builds demo/demo_level.blend (never saved) with two Car Alarms (Valve's own
instance, copied from the game's sdk_content beside the map) as hl_test_caralarm, then:

- both cars are in the map, each with its own glass, lights and sounds (the instance's entities, named apart);
- a bullet's damage on the first sets its alarm off (its OnCarAlarmStart enables its alarm glass) and calls a mob;
- the second car stays quiet.

Run:  python tests/ingame/test_car_alarm.py
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from tests.ingame.bench import Bench  # noqa: E402

BLEND = os.path.join(ROOT, "demo", "demo_level.blend")
MAP = "hl_test_caralarm"
PREP = r'''
from hammerless.blender.logic import TREE
for t in [t for t in bpy.data.node_groups if t.bl_idname == TREE]:
    bpy.data.node_groups.remove(t)
win = bpy.context.window_manager.windows[0]
area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
for x in (6.0, 12.0):
    bpy.context.scene.cursor.location = (x, -6.0, 0.0)
    with bpy.context.temp_override(window=win, area=area):
        bpy.ops.hammerless.add_preset(preset="CAR_ALARM")
'''


def main(argv):
    results = []

    def check(name, ok, note=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {note}", flush=True)

    with Bench() as b:
        out = b.build(BLEND, MAP, prep=PREP)
        log = open(os.path.join(out, MAP + ".lastbuild.log"), encoding="utf-8", errors="replace").read() \
            if os.path.exists(os.path.join(out, MAP + ".lastbuild.log")) else ""
        check("the instance came from the game", os.path.exists(os.path.join(out, "instance", "prop_car_alarm.vmf"))
              and "isn't beside the map" not in log, "")
        b.console("sv_cheats 1", "god 1", "director_stop", wait=0.3)
        cars = b.value('(function() { local out = []; for (local e = null; e = Entities.FindByClassname(e, '
                       '"prop_car_alarm");) out.append(e.GetName()); return out; })()')
        check("two alarm cars, named apart", isinstance(cars, list) and len(cars) == 2 and cars[0] != cars[1], f"{cars}")
        if not (isinstance(cars, list) and len(cars) == 2):
            return 1

        def glass_on(car):
            # (OnCarAlarmStart enables the car's alarm glass: "caralarm_glass1_off-<instance name>" from the instance)
            prefix = car.split("-", 1)[0]
            return b.value(f'(function() {{ local g = Entities.FindByName(null, "{prefix}-caralarm_glass1_off"); '
                           f'return g != null && !(NetProps.GetPropInt(g, "m_fEffects") & 32); }})()')

        before = [glass_on(c) for c in cars]
        b.console("nb_delete_all infected", wait=0.5)        # (room for the horde: the map's common limit is 30)
        b.run('::HLT_panic <- 0; ::HLT_ev <- { function OnGameEvent_create_panic_event(p) { ::HLT_panic++; } }; '
              '__CollectGameEventCallbacks(::HLT_ev); return true;')
        commons0 = b.value('(function() { local n = 0; for (local e = null; e = Entities.FindByClassname(e, "infected");) n++; return n; })()')
        b.console("director_start", wait=0.3)
        # a real shot: the host aims at the car and fires (a point_clientcommand presses +attack for them)
        b.run('if (!("HLT_cc" in getroottable()) || ::HLT_cc == null || !::HLT_cc.IsValid()) '
              '::HLT_cc <- SpawnEntityFromTable("point_clientcommand", { targetname = "hlt_cc" }); return true;')
        b.run(f'local h = GetListenServerHost(), car = Entities.FindByName(null, "{cars[0]}"); '
              'local at = car.GetOrigin() + Vector(0, 0, 40); h.SetOrigin(at + Vector(-250, 0, -40)); '
              'local d = at - h.EyePosition(); local yaw = atan2(d.y, d.x) * 57.29578; '
              'local pitch = -atan2(d.z, sqrt(d.x * d.x + d.y * d.y)) * 57.29578; '
              'h.SnapEyeAngles(QAngle(pitch, yaw, 0)); return true;')
        time.sleep(0.5)
        for _ in range(3):
            b.run('DoEntFire("hlt_cc", "Command", "+attack", 0.0, GetListenServerHost(), null); '
                  'DoEntFire("hlt_cc", "Command", "-attack", 0.3, GetListenServerHost(), null); return true;')
            time.sleep(0.6)
        time.sleep(1.5)
        after = [glass_on(c) for c in cars]
        check("shot: its alarm goes off (alarm glass on)", after[0] and not before[0], f"{before} -> {after}")
        check("the other car stays quiet", not after[1], f"{after[1]}")
        time.sleep(10.0)                     # (the horde comes running)
        commons1 = b.value('(function() { local n = 0; for (local e = null; e = Entities.FindByClassname(e, "infected");) n++; return n; })()')
        panics = b.value("::HLT_panic")
        check("the alarm calls a horde", panics >= 1 and commons1 >= 10,
              f"panic events {panics}; common infected {commons0} -> cleared -> {commons1}")
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
