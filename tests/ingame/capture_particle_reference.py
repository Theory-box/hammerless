"""Prepare an isolated reference room; --run explicitly compiles/installs/captures it.
Never opens a blend file. Refuses to hijack an existing game session.
"""
import argparse,json,hashlib,shutil,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hammerless.core.vmf import VMFWriter,Block
from hammerless.core.ir import Entity
from hammerless.core.geometry import box_brush

EFFECTS=['gas_smoke','small_sparks','groundfog_street','drainagepipe_short_stream']
NAME='hl_particle_reference_20261027'


def prepare(output):
    output.mkdir(parents=True,exist_ok=True)
    w=VMFWriter();world=Block('world').kv('id',w.new_id()).kv('classname','worldspawn').kv('skyname','sky_day01_09_hdr')
    for lo,hi in [((-2048,-2048,-64),(2048,2048,0)),((-2048,-2048,2048),(2048,2048,2112)),
                  ((-2112,-2048,0),(-2048,2048,2048)),((2048,-2048,0),(2112,2048,2048)),
                  ((-2112,-2112,0),(2112,-2048,2048)),((-2112,2048,0),(2112,2112,2048))]:
        world.add(w.solid(box_brush(lo,hi,'tools/toolsblack')))
    ents=[Entity('info_player_start',(0,-768,16)),Entity('info_director',(0,0,16))]
    for i in range(4):ents.append(Entity('info_survivor_position',(i*64,-768,16),keyvalues={'Order':str(i+1)}))
    for i,effect in enumerate(EFFECTS):
        ents.append(Entity('info_particle_system',(0,0,256),keyvalues={'targetname':f'hl_ref_{i}','effect_name':effect,'start_active':'0'}))
    path=output/(NAME+'.vmf');path.write_text(w.document(world,[w.entity(e) for e in ents]))
    camera={'eye':[0,-768,0],'target':[0,0,0],'up':[0,0,1],'horizontal_fov':90,'width':768,'height':576,'near':1,'far':10000}
    (output/'camera.json').write_text(json.dumps(camera,indent=2))
    return path,camera


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--game',required=True)
    parser.add_argument('--run',action='store_true');args=parser.parse_args()
    output=Path(args.output).resolve();path,camera=prepare(output)
    if not args.run:print('Prepared',path);return
    from tests.ingame.bench import Bench
    from hammerless.core import compile as cc
    from hammerless.core.vpk import GameContent
    from hammerless.core.particlepreview import load_library
    if cc.game_running():raise RuntimeError('Close the existing game before running isolated reference capture')
    game=Path(args.game).resolve();gamedir=game/'left4dead2'
    target=gamedir/'maps'/(NAME+'.bsp')
    if target.exists():raise RuntimeError('Reference BSP already exists; refusing to overwrite it')
    result=subprocess.run([str(game/'bin'/'vbsp.exe'),'-game',str(gamedir),str(path)],capture_output=True,text=True,timeout=60)
    (output/'compile.log').write_text(result.stdout+result.stderr)
    if result.returncode or not path.with_suffix('.bsp').exists():raise RuntimeError('Reference room compile failed')
    shutil.copy2(path.with_suffix('.bsp'),target)
    content=GameContent(str(game));library,errors=load_library(content)
    report=dict(producer='l4d2-game',camera=camera,framing='fixed-perspective',width=768,height=576,parse_errors=errors,
                samples=[],limitations=['Timing is a measured capture interval, not an exact engine frame.',
                'Random seeds, HDR/tone mapping and colour transfer differ from the independent preview.'])
    bench=Bench(str(game));launched=False
    try:
        if Path(bench.mapspawn).exists():raise RuntimeError('Existing mapspawn script: refusing to replace it')
        bench.open()
        cc.launch_game(bench.tools,NAME,window=cc.LaunchOptions(width=768,height=576))
        launched=True;bench._wait_loaded(NAME,0,90,settle=1)
        bench.console('sv_cheats 1','god 1','director_stop','nb_stop 1','cl_drawhud 0','r_drawviewmodel 0','fov_desired 90',
                      'mat_fullbright 1','fog_enable 0','noclip','hideconsole','gameui_hide')
        bench.run('local h=GetListenServerHost();h.SetOrigin(Vector(0,-768,192));h.SetVelocity(Vector(0,0,0));')
        bench.console('setang 0 90 0')
        for i,effect in enumerate(EFFECTS):
            if i:
                bench.load(NAME,timeout=90,settle=1)
            bench.console('sv_cheats 1','god 1','director_stop','nb_stop 1','hidehud 4','cl_drawhud 0','r_drawviewmodel 0',
                          'fov_desired 90','mat_fullbright 1','fog_enable 0','hideconsole','gameui_hide')
            bench.run('local h=GetListenServerHost();NetProps.SetPropInt(h,"movetype",8);h.SetOrigin(Vector(0,-768,192));h.SetVelocity(Vector(0,0,0));')
            bench.console('setang 0 90 0')
            start=bench.run(f'EntFire("hl_ref_{i}","Start");return Time();')
            time.sleep(.05 if effect=='small_sparks' else 2)
            before=bench.value('Time()')
            filename=f'{effect}_reference'
            # Direct capture avoids the screenshot helper's extra half-second setup wait.
            unique=filename+'_'+str(time.time_ns())
            source=gamedir/'screenshots'/(unique+'.jpg')
            bench.console(f'jpeg {unique} 95',wait=.1)
            deadline=time.monotonic()+10
            while not source.exists() and time.monotonic()<deadline:time.sleep(.05)
            if not source.exists():raise RuntimeError('No game screenshot appeared')
            time.sleep(.2);shot=output/(filename+'.jpg');shutil.copy2(source,shot)
            after=bench.value('Time()')
            report['samples'].append(dict(effect=effect,seconds=(before+after)/2-start,
                capture_interval=[before-start,after-start],file=Path(shot).name,
                definition_sha256=hashlib.sha256(content.read(library[effect].file)).hexdigest()))
            bench.run(f'EntFire("hl_ref_{i}","Kill");return true;')
            time.sleep(2)
            (output/'report.json').write_text(json.dumps(report,indent=2))
    finally:
        if launched:
            try:bench.console('quit',wait=0)
            except Exception:pass
        bench.close()
        # The temporary map is uniquely named and was created by this invocation.
        if target.exists():target.unlink()
    print('Captured',len(report['samples']),'game references',flush=True)

if __name__=='__main__':main()
