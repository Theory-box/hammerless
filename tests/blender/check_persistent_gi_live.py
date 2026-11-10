"""One owned fixed-BSP GI server feeding the existing isolated surface viewer.
Only main-thread timers decode/upload/draw; a reader thread handles stdout only.
This is a bounded light-intensity fixture, not editable geometry or a full renderer.
"""
from pathlib import Path
import argparse,atexit,hashlib,importlib.util,json,os,queue,subprocess,sys,threading,time,traceback
import bpy
from mathutils import Vector

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from run_isolated import OwnedJob
parser=argparse.ArgumentParser(description=__doc__)
for name in ('reference','blob','out','server-exe','server-input','game'):
    parser.add_argument('--'+name,type=Path,required=True)
parser.add_argument('--timeout',type=float,default=90)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
if not 0<args.timeout<=300:raise ValueError('Timeout must be within (0,300] seconds')
args.out.mkdir(parents=True,exist_ok=True)
saved_argv=sys.argv
try:
    sys.argv=[str(HERE/'check_persistent_gi_viewport.py'),'--','--reference',str(args.reference),'--blob',str(args.blob),'--out',str(args.out),'--interactive']
    spec=importlib.util.spec_from_file_location('persistent_surface_viewer',HERE/'check_persistent_gi_viewport.py')
    surface=importlib.util.module_from_spec(spec);spec.loader.exec_module(surface)
finally:sys.argv=saved_argv

REQUEST_BUDGET=3
process=job=reader=None
messages=queue.Queue(maxsize=64)
reader_error=threading.Event()
state={'ok':False,'requests':[],'captures':[],'phase':'starting'}
source_hash=hashlib.sha256(args.server_input.read_bytes()).hexdigest()
retained=args.out/'live-input.bsp'
if retained.resolve()==args.server_input.resolve():raise ValueError('Output must differ from source input')
retained.write_bytes(args.server_input.read_bytes())
matrices=[]
deadline=0
finished=False

def cleanup():
    global process,job
    if process is not None and state.get('normal_done'):
        try:
            process.wait(timeout=3)
            state['worker_returncode']=process.returncode
            if process.returncode!=0:state['cleanup_error']='Worker returned nonzero exit status'
        except subprocess.TimeoutExpired:state['cleanup_error']='Worker did not exit after DONE'
    if job is not None:job.close();job=None
    if process is not None:
        if process.poll() is None:process.kill()
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:state['cleanup_error']='Owned worker did not exit'
        state['worker_exited']=process.poll() is not None
        if reader is not None:reader.join(timeout=1)
        for stream in (process.stdin,process.stdout):
            if stream is not None:
                try:stream.close()
                except (BrokenPipeError,OSError) as exc:state['cleanup_error']='Worker stream close: '+str(exc)
        process=None
atexit.register(cleanup)

def finish(error=None):
    global finished
    if finished:return None
    finished=True
    if error:state['error']=error
    try:cleanup()
    except Exception:state['cleanup_error']=traceback.format_exc()
    state['source_unchanged']=hashlib.sha256(args.server_input.read_bytes()).hexdigest()==source_hash
    state['diagnostic_input_unchanged']=hashlib.sha256(retained.read_bytes()).hexdigest()==source_hash
    state['ok']=not error and state.get('worker_exited',False) and state['source_unchanged'] and state['diagnostic_input_unchanged'] and not state.get('cleanup_error')
    state['scope']='Two fixed-geometry requests (SOLVE, SCALE 0.5). Pending captures occur before accepting the queued result; they do not measure continuous navigation or cancellation latency. Light-only display; no material/prop/geometry editing.'
    (args.out/'live-viewport-check.json').write_text(json.dumps(state,indent=2))
    bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
    return None

def send(command):
    global deadline
    process.stdin.write(command+'\n');process.stdin.flush()
    deadline=time.monotonic()+args.timeout

def request(index,command):
    state['phase']='result';state['index']=index;state['request_started']=time.perf_counter()
    state['before_hash']=surface.viewer.gpu_hash();state['before_uploads']=surface.viewer.uploads
    send(command)
    # Results are accepted only in tick(); both camera captures retain the old
    # atlas even if the background worker finishes before drawing completes.
    for camera,matrix in enumerate(matrices):
        captured,_=surface.capture(matrix,f'pending-{index}-camera-{camera}')
        assert captured['atlas_hash']==state['before_hash'] and captured['uploads']==state['before_uploads']
        state['captures'].append(captured)
    assert state['captures'][-1]['depth_hash']!=state['captures'][-2]['depth_hash']
    state['requests'].append({'command':command,'index':index,'pending_atlas_hash':state['before_hash']})

def start():
    global process,job,reader,deadline,matrices
    try:
        surface.run()
        if not json.loads((args.out/'viewport-check.json').read_text())['ok']:raise RuntimeError('Initial surface viewer checks failed')
        data=surface.viewer.data
        low=data.positions.min(axis=0);high=data.positions.max(axis=0)
        center=Vector((low+high)*.5);span=float(max(high-low))
        for direction in (Vector((1,.35,-.25)).normalized(),Vector((-.4,1,-.35)).normalized()):
            camera=direction.to_track_quat('-Z','Y').to_matrix().to_4x4();camera.translation=center
            matrices.append(surface.projection(720/540,max(.01,span*.001),span*10)@camera.inverted())
        options=['-gpu','-noextra','-bounce','0','-gi','12','-girays','256','-threads','1','-game',str(args.game.resolve())]
        job=OwnedJob()
        startup=None
        if os.name=='nt':
            startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        process=subprocess.Popen([str(args.server_exe.resolve()),*options,'-giserver',str(REQUEST_BUDGET),str((args.out/'stream').resolve()),str(retained.resolve())],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,startupinfo=startup,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        job.assign(process);state['pid']=process.pid
        def read_output():
            try:
                size=0
                with (args.out/'live-worker.log').open('w') as log:
                    while True:
                        line=process.stdout.readline(16384)
                        if not line:break
                        size+=len(line)
                        if size>4*1024*1024:raise RuntimeError('Diagnostic output limit')
                        log.write(line);log.flush()
                        if line.startswith('HLGI_'):messages.put_nowait(line.strip())
                messages.put_nowait(None)
            except Exception:reader_error.set()
        reader=threading.Thread(target=read_output,name='GI fixture output',daemon=True);reader.start()
        state['phase']='ready';deadline=time.monotonic()+args.timeout
        bpy.app.timers.register(tick,first_interval=.05)
    except Exception:return finish(traceback.format_exc())
    return None

def tick():
    try:
        if reader_error.is_set():raise RuntimeError('Bounded worker output reader failed')
        if time.monotonic()>deadline:raise TimeoutError('GI worker '+state['phase'])
        for _ in range(64):
            try:line=messages.get_nowait()
            except queue.Empty:break
            if line is None:raise RuntimeError('Worker exited before expected lifecycle marker')
            if line=='HLGI_READY' and state['phase']=='ready':request(0,'SOLVE')
            elif line.startswith('HLGI_RESULT ') and state['phase']=='result':
                index=state['index']
                if int(line.split()[1])!=index:raise RuntimeError('Out-of-order result')
                data=surface.decode(args.out/f'stream.{index}.bin')
                surface.viewer.replace(data)
                completed,_=surface.capture(matrices[0],f'completed-{index}')
                state['captures'].append(completed)
                state['requests'][-1].update(roundtrip_seconds=time.perf_counter()-state['request_started'],response=line,completed_atlas_hash=completed['atlas_hash'])
                assert surface.viewer.uploads==state['before_uploads']+1
                if index==0:
                    state['original_image_hash']=completed['image_hash']
                    request(1,'SCALE 0.5')
                else:
                    assert completed['atlas_hash']!=state['before_hash'],'Half-intensity update did not change lighting'
                    assert completed['image_hash']!=state['original_image_hash'],'Half-intensity update did not change displayed image'
                    state['phase']='done';send('QUIT')
            elif line.startswith('HLGI_DONE ') and state['phase']=='done':
                if int(line.split()[1])!=2:raise RuntimeError('Unexpected request count')
                state['uploads']=surface.viewer.uploads
                state['normal_done']=True
                return finish()
        return .05
    except Exception:return finish(traceback.format_exc())

if __name__=='__main__':bpy.app.timers.register(start,first_interval=.3)
