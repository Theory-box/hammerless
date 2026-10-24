"""Exercise generated payment groups and engine removal on a stock map."""
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
        script=Path(tmp)/'barrier.nut'
        subprocess.run([BLENDER,'--background','--factory-startup','--python-exit-code','1',
                        '--python',str(ROOT/'tests/blender/test_paid_barrier.py'),
                        '--','--script-out',str(script)],check=True)
        source=script.read_text(encoding='utf-8')
        names=json.loads(script.with_suffix('.json').read_text())
    source=source[:source.rfind('if ("self" in this)')]
    source=source.replace('HL_','HLT_').replace('OnGameEvent_','HLT_Event_')
    paths=[]
    with Bench() as b:
        try:
            for offset in range(0,len(source),8000):
                path=Path(b.ems)/f'hl_barrier_{time.time_ns()}_{offset}.txt'
                path.write_text(source[offset:offset+8000],encoding='utf-8')
                paths.append(path)
            b.load('c2m1_highway',timeout=120)
            b.console('sv_cheats 1','god 1','director_stop',wait=.2)
            b.run('::HLT_Code <- "";')
            for path in paths:
                b.run(f'local text=FileToString({json.dumps(path.name)}); '
                      'if(text==null) throw "missing source"; HLT_Code+=text;')
            b.run('::HLT_TestScope <- {}; HLT_TestScope.setdelegate(getroottable()); '
                  'compilestring(HLT_Code).bindenv(HLT_TestScope)(); '
                  'HLT_TestScope.HLT_Event_round_start({});')
            # Placeholder entities test the actual deferred Kill inputs; brush/nav geometry
            # is separately checked by the Blender export test, not this stock-map test.
            for name in names:
                b.run(f'SpawnEntityFromTable("info_target", {{targetname={json.dumps(name)}}});')
            context='local p=GetListenServerHost(), key=HLT_HumanKey(p); HLT_Ctx={player=p,key=key,used=true}; '
            result=b.run(context+'HLT_KeySet(key,"points",999); HLT_Interactions[0].event(); '
                         'return HLT_KeyGet(key,"points",0);')
            assert result==999,result
            result=b.run(context+'HLT_KeySet(key,"points",2000); HLT_Interactions[0].event(); '
                         'HLT_Interactions[0].event(); return HLT_KeyGet(key,"points",0);')
            assert result==1000,result
            time.sleep(.2)
            result=b.run(context+'HLT_Interactions[0].event(); '
                         'return {balance=HLT_KeyGet(key,"points",0),gone=HLT_Interactions[0].entity()==null};')
            assert result==dict(balance=1000,gone=True),result
            result=b.run(context+'HLT_Interactions[1].event(); HLT_Interactions[1].event(); '
                         'return HLT_KeyGet(key,"points",0);')
            assert result==750,result
            time.sleep(.2)
            assert b.run('return HLT_Interactions[1].entity()==null;') is True
            print('PASS insufficient funds, one charge on repeated use, removal, independent duplicate price and lock',flush=True)
        finally:
            try:
                b.console('god 0','director_start','sv_cheats 0','disconnect',wait=.5)
            finally:
                for path in paths:
                    path.unlink(missing_ok=True)

if __name__=='__main__':
    main()
