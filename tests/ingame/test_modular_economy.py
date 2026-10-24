"""Generate real groups, then exercise their script on a stock map in an isolated namespace.

Run with Blender's Python. This launches/loads c2m1_highway and leaves the game at
its menu afterwards. It never reads or writes the user's .blend or built map.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tests.ingame.bench import Bench, BLENDER


def main():
    with tempfile.TemporaryDirectory() as tmp:
        script=Path(tmp)/'generated.nut'
        subprocess.run([BLENDER,'--background','--factory-startup','--python-exit-code','1',
                        '--python',str(ROOT/'tests/blender/test_modular_economy.py'),
                        '--','--script-out',str(script)],check=True)
        source=script.read_text(encoding='utf-8')
    source=source[:source.rfind('if ("self" in this)')]
    source=source.replace('HL_','HLT_').replace('OnGameEvent_','HLT_Event_')
    paths=[]
    with Bench() as b:
        # FileToString rejects oversized files; keep each chunk below its limit.
        for offset in range(0,len(source),8000):
            path=Path(b.ems)/f'hl_modular_{time.time_ns()}_{offset}.txt'
            path.write_text(source[offset:offset+8000],encoding='utf-8')
            paths.append(path)
        try:
            b.load('c2m1_highway',timeout=120)
            b.console('sv_cheats 1','god 1','director_stop',wait=0.2)
            b.run('::HLT_Code <- "";')
            for path in paths:
                b.run(f'local text=FileToString({json.dumps(path.name)}); '
                      'if(text==null) throw "missing test source"; HLT_Code+=text;')
            b.run('::HLT_TestScope <- {}; HLT_TestScope.setdelegate(getroottable()); '
                  'compilestring(HLT_Code).bindenv(HLT_TestScope)();')
            result=b.run('local p=GetListenServerHost(), key=HLT_HumanKey(p); '
                'HLT_TestScope.HLT_Event_round_start({}); '
                'HLT_TestScope.HLT_Event_infected_death({attacker=p.GetPlayerUserId()}); '
                'local earned=HLT_KeyGet(key,"points",0); '
                'HLT_TestScope.HLT_Event_player_death({userid=0,attacker=p.GetPlayerUserId()}); '
                'HLT_TestScope.HLT_Event_player_say({userid=p.GetPlayerUserId(),text="!POINTS"}); '
                'HLT_KeySet(key,"points",999999999); '
                'HLT_TestScope.HLT_Event_infected_death({attacker=p.GetPlayerUserId()}); '
                'local cap=HLT_KeyGet(key,"points",0); '
                'HLT_TestScope.HLT_Event_round_start({}); '
                'return {earned=earned,cap=cap,reset=HLT_KeyGet(key,"points",0),targets=HLT_Interactions.len()};')
            assert result==dict(earned=10,cap=1000000000,reset=0,targets=2),result
            print('PASS awards, integer cap, invalid victim, case-insensitive chat and round reset',flush=True)
            result=b.run('local p=GetListenServerHost(), key=HLT_HumanKey(p); '
                'HLT_KeySet(key,"points",1000); HLT_Ctx={player=p,key=key,used=true}; '
                'HLT_Interactions[0].event(); return {balance=HLT_KeyGet(key,"points",0),pending=HLT_Deliveries.len()};')
            assert result==dict(balance=750,pending=1),result
            time.sleep(0.2)
            result=b.run('HLT_InteractionNext=0; HLT_InteractionThink(); '
                'local p=GetListenServerHost(), key=HLT_HumanKey(p); '
                'HLT_Ctx={player=p,key=key,used=true}; HLT_Interactions[0].event(); '
                'return {balance=HLT_KeyGet(key,"points",0),pending=HLT_Deliveries.len()};')
            assert result==dict(balance=750,pending=0),result
            print('PASS delivery confirmation and already-owned rejection',flush=True)
            b.run('local p=GetListenServerHost(), inv={}; GetInvTable(p,inv); '
                  'foreach(k,item in inv) if(item.GetClassname()=="weapon_rifle_ak47") item.Kill();')
            time.sleep(0.2)
            result=b.run('local p=GetListenServerHost(); ::HLT_RealGive <- HLT_GiveInventory; '
                'HLT_GiveInventory=function(p,item,ok,fail) { local missing=clone item; '
                'missing.id="hl_missing_item"; missing.classname="weapon_hl_missing_item"; HLT_RealGive(p,missing,ok,fail); }; '
                'local key=HLT_HumanKey(p); HLT_Ctx={player=p,key=key,used=true}; '
                'HLT_Interactions[0].event(); return {balance=HLT_KeyGet(key,"points",0),pending=HLT_Deliveries.len()};')
            assert result==dict(balance=500,pending=1),result
            time.sleep(0.6)
            result=b.run('HLT_Ctx={player=null,key="different-user",used=false}; HLT_InteractionNext=0; HLT_InteractionThink(); '
                'return {balance=HLT_KeyGet(HLT_HumanKey(GetListenServerHost()),"points",0),pending=HLT_Deliveries.len(),restored=HLT_Ctx.key};')
            assert result==dict(balance=750,pending=0,restored='different-user'),result
            print('PASS failed-delivery refund retains original human and restores surrounding context',flush=True)
        finally:
            try:
                b.run('local keys=[]; foreach(k,v in getroottable()) if(k.find("HLT_")==0) keys.append(k); '
                      'foreach(k in keys) delete getroottable()[k];')
                b.console('god 0','director_start','sv_cheats 0','disconnect',wait=0.5)
            finally:
                for path in paths:
                    try:
                        path.unlink(missing_ok=True)
                    except PermissionError:
                        pass  # The engine can retain a read handle until it finishes disconnecting.


if __name__=='__main__':
    main()
