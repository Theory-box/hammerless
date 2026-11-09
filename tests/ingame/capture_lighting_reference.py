"""Capture an existing VRAD reference BSP; --run opts into temporary installation.

No mouse/keyboard simulation. Refuses an existing game or mapspawn script.
Uses a small muted window on a non-primary monitor to the right.
Captures lit and fullbright views to separate texture response from lighting.
This is evidence collection, not an automatic image-parity assertion.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hammerless.core import compile as cc
from hammerless.core.window import monitors
from tests.ingame.bench import Bench


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bsp',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--user-away',action='store_true',help='Only use during an explicitly authorized away period; game may capture mouse')
    args=parser.parse_args()
    source=args.bsp.resolve()
    assert source.is_file(),source
    from hammerless.core.bspdecompile import Bsp
    entities=Bsp(source.read_bytes()).lump(0).decode('utf-8',errors='replace')
    assert 'info_survivor_position' in entities, 'Use a playable reference fixture with survivor positions; lighting-only fixtures are not validated for game loading'
    assert source.with_suffix('.nav').is_file(), 'Capture requires a prepared reference nav file'
    root=cc.find_game_root()
    assert root,'No L4D2 installation'
    assert not cc.game_running(),'Existing game session: refusing to take it over'
    right=[m for m in monitors() if not m.primary and m.x>0]
    assert right,'No right-side secondary monitor available'
    bench=Bench(root)
    assert not Path(bench.mapspawn).exists(),'Existing mapspawn script: refusing to replace it'
    name='hlref_'+format(time.time_ns(),'x')
    assert len(name)<32
    destination=Path(bench.game)/'maps'/(name+'.bsp')
    assert not destination.exists()
    args.output.mkdir(parents=True,exist_ok=True)
    report={'source':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'monitor':right[-1].__dict__,'resolution':[640,480],
        'camera_commands':['setpos 0 0 80','setang 90 0 0'],
        'scope':'Fixed floor view of existing point-reference BSP. HDR/display response remains to be calibrated.',
        'captured':False}
    if not args.run:
        (args.output/'preflight.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2));return
    if not args.user_away:
        raise RuntimeError('Interactive capture disabled while user is working: game steals mouse focus.')
    # Restore configuration files if the game saves test video/volume settings.
    cfg=Path(bench.game)/'cfg'
    saved={p:p.read_bytes() if p.exists() else None for p in
           (cfg/'config.cfg',cfg/'video.txt',cfg/'videodefaults.txt')}
    backup=args.output/'config-backup'
    backup.mkdir(exist_ok=True)
    for path,data in saved.items():
        if data is not None:(backup/path.name).write_bytes(data)
    (backup/'manifest.json').write_text(json.dumps({str(p):v is not None for p,v in saved.items()},indent=2))
    launched=False
    process=None
    try:
        shutil.copy2(source,destination)
        shutil.copy2(source.with_suffix(".nav"),destination.with_suffix(".nav"))
        bench.open()
        process=cc.launch_game(bench.tools,name,window=cc.LaunchOptions(width=640,height=480,
            monitor_index=right[-1].index,extra='-nosound -novid'))
        launched=True
        bench._wait_loaded(name,0,90,settle=1)
        bench.console('sv_cheats 1','volume 0','director_stop','nb_stop 1','god 1',
            'noclip','fog_enable 0','mat_motion_blur_enabled 0','mat_disable_bloom 1',
            'mat_hdr_level 2','mat_dynamic_tonemapping 0','mat_force_tonemap_scale 1','mat_bloomscale 0',
            'mat_colorcorrection 0','sb_stop 1','hidehud 4','hud_saytext_time 0','fov_desired 90')
        bench.run('for(local p=null;p=Entities.FindByClassname(p,"player");){if(p!=GetListenServerHost())p.SetOrigin(Vector(224,224,8));NetProps.SetPropInt(p,"m_fEffects",NetProps.GetPropInt(p,"m_fEffects") & ~4);}return true;')
        report['images']=[]
        for fullbright in (0,1):
            bench.console(f'mat_fullbright {fullbright}')
            image=bench.screenshot(name+f'_fullbright{fullbright}',pos=(0,0,80),ang=(90,0),out_dir=str(args.output))
            report['images'].append(image)
            report['camera']=bench.value('{eye=GetListenServerHost().EyePosition(),angles=GetListenServerHost().EyeAngles()}')
            report['tone_scale']=bench.value('Convars.GetFloat("mat_force_tonemap_scale")')
            report['flashlight_enabled']=bench.value('(NetProps.GetPropInt(GetListenServerHost(),"m_fEffects") & 4)!=0')
        report['captured']=True
    finally:
        if launched:
            try:bench.console('quit',wait=0)
            except Exception:pass
            deadline=time.monotonic()+15
            while cc.game_running() and time.monotonic()<deadline:time.sleep(.2)
        if process is not None and process.poll() is None:
            # Only the process returned by this test's launch, never another game.
            process.terminate()
            process.wait(timeout=10)
        bench.close()
        if destination.exists():destination.unlink()
        if destination.with_suffix(".nav").exists():destination.with_suffix(".nav").unlink()
        if not cc.game_running():
            for path,data in saved.items():
                if data is not None:path.write_bytes(data)
                elif path.exists():path.unlink()
        else:
            report['cleanup_warning']='Game still running; configuration not restored yet'
        (args.output/'capture.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()

