"""Exercise configured shops, mixed stock and real pins (Charger pummel only).

Uses a stock map, returns to the menu, and does not modify the user's map.
"""
import sys, tempfile, subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tests.ingame.bench import Bench, BLENDER

with tempfile.TemporaryDirectory() as tmp:
    script=Path(tmp)/'knife.nut'
    subprocess.run([BLENDER,'--background','--factory-startup','--python-exit-code','1',
                    '--python',str(ROOT/'tests/blender/test_emergency_knife.py'),
                    '--','--script-out',str(script)],check=True)
    source=script.read_text(encoding='utf8')
source=source[:source.rfind('if ("self" in this)')].replace('HL_','HLT_').replace('OnGameEvent_','HLT_Event_')
paths=[]
with Bench() as b:
    try:
        for offset in range(0,len(source),8000):
            path=Path(b.ems)/f'hl_knife_{time.time_ns()}_{offset}.txt'
            path.write_text(source[offset:offset+8000],encoding='utf8')
            paths.append(path)
        b.load('c2m1_highway',timeout=120)
        b.console('sv_cheats 1','god 1','director_stop','sb_stop 1','-reload')
        b.run('local p=null; while((p=Entities.FindByClassname(p,"player"))!=null) '
              'if(p.IsSurvivor() && IsPlayerABot(p)) SendToConsole("kickid " + p.GetPlayerUserId());')
        time.sleep(.5)
        b.run('GetListenServerHost().SetOrigin(Vector(2671,4792,-976.75));')
        b.run('::HLT_Code <- "";')
        for path in paths:
            b.run(f'HLT_Code += FileToString("{path.name}");')
        b.run('::HLT_TestScope <- {}; HLT_TestScope.setdelegate(getroottable()); '
              'compilestring(HLT_Code).bindenv(HLT_TestScope)();')
        b.run('HLT_TestScope.HLT_Event_round_start({}); local p=GetListenServerHost(), key=HLT_HumanKey(p); '
              'HLT_KeySet(key,"points",2000); HLT_Ctx={player=p,key=key,used=true};')
        for shop,count,points in [(0,1,1500),(1,2,750),(0,2,750)]:
            result=b.run(f'HLT_Interactions[{shop}].event(); local p=GetListenServerHost(); '
                         'return {count=HLT_KnifeCount(p),points=HLT_KeyGet(HLT_HumanKey(p),"points",0)};')
            assert result==dict(count=count,points=points),result
        print('PASS independent shop prices, shared two-knife cap, full-stock purchase does not charge',flush=True)
        assert b.value('HLT_KnifeState(GetListenServerHost()).knives')==[10,106]
        assert b.value('HLT_KnifeGive(GetListenServerHost(),0)') is False
        # Failed grant refunds exactly once; points remain graph-controlled.
        b.run('::HLT_OriginalGive <- HLT_KnifeGive; HLT_KnifeUsers={}; '
              'HLT_KnifeGive=function(p,mask) { return false; }; HLT_Interactions[0].event(); '
              'HLT_KnifeGive=HLT_OriginalGive;')
        assert b.value('HLT_KeyGet(HLT_HumanKey(GetListenServerHost()),"points",0)')==750
        assert b.value('HLT_KnifeGive(GetListenServerHost(),0)') is False
        assert b.value('HLT_KnifeCount(GetListenServerHost())')==0
        b.run('HLT_KnifeGive(GetListenServerHost()); HLT_KnifeGive(GetListenServerHost());')
        print('PASS failed grant refunds',flush=True)

        def ticks(seconds):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                b.run('HLT_KnifeThink();')
                time.sleep(.055)

        for kind,typ in [('smoker',1),('hunter',3),('jockey',5),('charger',6)]:
            b.run('HLT_KnifeUsers={}; HLT_KnifeGive(GetListenServerHost(),106); '
                  'HLT_KnifeGive(GetListenServerHost(),106);')
            b.run('local p=GetListenServerHost(); p.SetOrigin(Vector(2671,4792,-976.75)); '
                  'p.SetVelocity(Vector(0,0,0));')
            b.console('-reload','nb_delete_all',f'z_spawn_old {kind}')
            b.run('local p=GetListenServerHost(), a=null; while((a=Entities.FindByClassname(a,"player"))!=null) '
                  f'if(!a.IsSurvivor() && a.GetZombieType()=={typ}) {{ '
                  'a.SetOrigin(p.GetOrigin()+Vector(250,0,0)); a.SetAngles(QAngle(0,180,0)); '
                  '::HLT_Attacker <- a; return; };')
            try:
                b.wait_until('GetListenServerHost().GetSpecialInfectedDominatingMe()!=null',timeout=30)
            except Exception:
                print('PIN DEBUG', b.run('return {p=GetListenServerHost().GetOrigin(), '
                      'a=HLT_Attacker.GetOrigin(),hp=HLT_Attacker.GetHealth(),dead=HLT_Attacker.IsDead()};'),flush=True)
                raise
            if typ == 6:
                b.wait_until('NetProps.GetPropEntity(GetListenServerHost(),"m_pummelAttacker")!=null',timeout=15)
            ticks(.12)
            # Mixed stock: the first charge cannot escape this enemy. A disabled
            # pin must not use either charge, then only the compatible one is spent.
            wrong = 8 if typ != 3 else 2
            b.run(f'HLT_KnifeState(GetListenServerHost()).knives=[{wrong},{wrong}];')
            b.console('+reload',wait=.01)
            ticks(1.3)
            assert b.value('HLT_KnifeCount(GetListenServerHost())')==2
            assert b.value('GetListenServerHost().GetSpecialInfectedDominatingMe()!=null')
            b.console('-reload',wait=.01)
            ticks(.12)
            b.run(f'HLT_KnifeState(GetListenServerHost()).knives=[{wrong},{1<<typ}];')
            b.console('+reload',wait=.01)
            ticks(.35)
            b.console('-reload',wait=.01)
            ticks(.12)
            assert b.value('HLT_KnifeCount(GetListenServerHost())')==2
            assert b.value('GetListenServerHost().GetSpecialInfectedDominatingMe()!=null')
            b.console('+reload',wait=.01)
            ticks(1.4)
            result=b.run('local p=GetListenServerHost(); return {count=HLT_KnifeCount(p),'
                         'free=p.GetSpecialInfectedDominatingMe()==null,alive=!HLT_Attacker.IsDead()};')
            assert result==dict(count=1,free=True,alive=True),(kind,result)
            assert b.value('HLT_KnifeState(GetListenServerHost()).knives')==[wrong]
            ticks(.2)
            assert b.value('HLT_KnifeCount(GetListenServerHost())')==1
            b.console('-reload',wait=.01)
            print('PASS',kind,'cancelled hold keeps knife; full hold frees player, consumes one, attacker alive',flush=True)
        b.console('nb_delete_all')
        b.run('HLT_KnifeGive(GetListenServerHost()); '
              'HLT_TestScope.HLT_Event_player_death({userid=GetListenServerHost().GetPlayerUserId()});')
        assert b.value('HLT_KnifeCount(GetListenServerHost())')==0
        for event in ('round_end','mission_lost','round_start'):
            b.run('HLT_KnifeGive(GetListenServerHost()); '
                  f'HLT_TestScope.HLT_Event_{event}({{}});')
            assert b.value('HLT_KnifeCount(GetListenServerHost())')==0
        print('PASS death, round end, loss and restart clear stock',flush=True)
    finally:
        try: b.console('-reload','sb_stop 0','god 0','director_start','sv_cheats 0','disconnect')
        except Exception: pass
        for path in paths:
            path.unlink(missing_ok=True)
