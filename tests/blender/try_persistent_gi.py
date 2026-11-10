"""Hands-on fixed-room GI experiment in a DISPOSABLE Blender window.
Uses check_persistent_gi_live CLI paths plus optional --smoke (two updates/exit).
No installed addon/preferences/user-file writes. One owned bounded worker only.
"""
from pathlib import Path
import argparse,atexit,importlib.util,json,os,queue,sys,time,traceback
import bpy
from mathutils import Vector

HERE=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--smoke',action='store_true')
options,remaining=parser.parse_known_args(sys.argv[sys.argv.index('--')+1:])
# Reopening the same launcher must not create competing workers/artifact writers.
test_folder=Path(remaining[remaining.index('--out')+1]).resolve()
test_folder.mkdir(parents=True,exist_ok=True)
test_lock=(test_folder/'hands-on.lock').open('a+b')
test_lock.seek(0,2)
if test_lock.tell()==0:test_lock.write(b'0');test_lock.flush()
test_lock.seek(0)
try:
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(test_lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(test_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except OSError:
    test_lock.close()
    raise RuntimeError('This GI test is already open. Close its existing test window first.')
atexit.register(test_lock.close)
saved=sys.argv
try:
    sys.argv=[str(HERE/'check_persistent_gi_live.py'),'--',*remaining]
    spec=importlib.util.spec_from_file_location('owned_fixed_gi_check',HERE/'check_persistent_gi_live.py')
    live=importlib.util.module_from_spec(spec);spec.loader.exec_module(live)
finally:sys.argv=saved
live.REQUEST_BUDGET=8
surface=live.surface
ui={'phase':'starting','status':'Preparing fixed-room lighting worker...', 'pending':None,
    'changed':0.,'motion':time.monotonic(),'camera':None,'count':0,'factor':1.,'index':0,
    'initial_hash':None,'results':[],'initialized':False,'scale_allowed':True}

def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='VIEW_3D':area.tag_redraw()

def camera_changed(now):
    key=tuple(tuple(float(v) for row in area.spaces.active.region_3d.perspective_matrix for v in row)
              for window in bpy.context.window_manager.windows for area in window.screen.areas if area.type=='VIEW_3D')
    if key!=ui['camera']:ui['camera']=key;ui['motion']=now

def queue_factor(scene,context):
    ui['pending']=float(scene.hl_fixed_gi_factor);ui['changed']=time.monotonic()
    if ui['phase']=='idle':ui['status']='Intensity update queued; waiting for interaction to settle'
    redraw()

def stop(message='Stopped. Last lighting retained.'):
    try:live.cleanup()
    except Exception:message+=' Cleanup error: '+traceback.format_exc()
    ui['phase']='stopped';ui['pending']=None;ui['status']=message;redraw()

def smoke_finish(error=None):
    stop('Smoke test finished')
    source_unchanged=live.hashlib.sha256(live.args.server_input.read_bytes()).hexdigest()==live.source_hash
    retained_unchanged=live.hashlib.sha256(live.retained.read_bytes()).hexdigest()==live.source_hash
    report={'ok':error is None and len(ui['results'])==2 and live.state.get('worker_exited',False) and not live.state.get('cleanup_error') and source_unchanged and retained_unchanged,
            'source_unchanged':source_unchanged,'diagnostic_input_unchanged':retained_unchanged,'cleanup_error':live.state.get('cleanup_error'),
            'error':error,'requests':ui['results'],'uploads':surface.viewer.uploads if surface.viewer else 0,
            'worker_exited':live.state.get('worker_exited',False),'scope':'Fixed room / one light / intensity-only hands-on controller'}
    (live.args.out/'hands-on-smoke.json').write_text(json.dumps(report,indent=2))
    bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)

def failed(error=None):
    text=error or 'Worker ended unexpectedly'
    if options.smoke:smoke_finish(text)
    else:stop(text)
    return None
live.finish=failed

def restart():
    stop('Restarting worker; last lighting retained.')
    live.reader_error.clear();live.matrices=[];live.state.pop('normal_done',None);live.state.pop('cleanup_error',None)
    while True:
        try:live.messages.get_nowait()
        except queue.Empty:break
    ui.update(phase='starting',status='Preparing fixed-room worker...',count=0,index=0,pending=None)
    if ui['initialized']:
        # Reuse the exact same surface draw handler/GPU cache on explicit restart.
        old=surface.run;surface.run=lambda:None
        try:live.start()
        finally:surface.run=old
    else:
        live.start();ui['initialized']=surface.viewer is not None
        # This hands-on fixture has a small shadow blocker near (8, 0, 32).
        # Start with that feature in view, rather than a nearly empty corner.
        target=Vector((8,0,32));eye=Vector((-120,-120,120))
        for area in bpy.context.screen.areas:
            if area.type=='VIEW_3D':
                region=area.spaces.active.region_3d
                region.view_location=target
                region.view_distance=(target-eye).length
                region.view_rotation=(target-eye).to_track_quat('-Z','Y')
    if surface.viewer is not None and ui['initial_hash'] is None:ui['initial_hash']=surface.viewer.gpu_hash()
    redraw()
    return None

def tick():
    if ui['phase']=='stopped':return None
    try:
        now=time.monotonic();camera_changed(now)
        if live.reader_error.is_set():raise RuntimeError('Worker output reader failed; restart explicitly to retry')
        if ui['phase']!='idle' and now>live.deadline:raise TimeoutError('Fixed-room worker deadline exceeded')
        for _ in range(64):
            try:line=live.messages.get_nowait()
            except queue.Empty:break
            if line is None:raise RuntimeError('Worker exited before completion marker')
            if line.startswith('HLGI_CAPABILITIES scale '):
                ui['scale_allowed']=bool(int(line.split()[-1]))
            elif line=='HLGI_READY':
                ui['phase']='idle';ui['status']='Ready. Orbit freely; intensity updates wait until navigation stops.'
                if not ui['scale_allowed']:
                    ui['status']='This map cannot scale lighting; use the provided one-light room.'
                    if options.smoke:raise RuntimeError(ui['status'])
                if options.smoke:ui['pending']=1.;ui['changed']=0.
            elif line.startswith('HLGI_RESULT '):
                index=int(line.split()[1])
                if ui['phase']!='solving' or index!=ui['index']:raise RuntimeError('Unexpected GI result')
                surface.viewer.replace(surface.decode(live.args.out/f'stream.{index}.bin'))
                ui['count']+=1;ui['index']+=1;ui['phase']='idle'
                ui['results'].append({'index':index,'factor':ui['factor'],'atlas_hash':surface.viewer.gpu_hash()})
                ui['status']=f'Lighting updated. {8-ui["count"]} requests remain.'
                if options.smoke:
                    if ui['count']==1:ui['pending']=.5;ui['changed']=0.
                    else:
                        assert ui['results'][0]['atlas_hash']!=ui['results'][1]['atlas_hash']
                        live.send('QUIT');ui['phase']='quitting'
                elif ui['count']==8:ui['phase']='quitting'
            elif line.startswith('HLGI_DONE '):
                if int(line.split()[1])!=ui['count']:raise RuntimeError('Worker completion count differs')
                live.state['normal_done']=True
                if options.smoke:smoke_finish();return None
                stop('Request budget finished. Last lighting retained; use Restart for 8 more.');return None
        if (ui['phase']=='idle' and ui['scale_allowed'] and ui['pending'] is not None and ui['count']<8
                and now-ui['motion']>.25 and now-ui['changed']>.25):
            ui['factor']=ui['pending'];ui['pending']=None
            live.send('SCALE '+format(ui['factor'],'.9g'))
            ui['phase']='solving';ui['status']='Computing lighting; previous lighting stays visible.'
        redraw();return .1
    except Exception:failed(traceback.format_exc());return None
live.tick=tick

class HL_FIXED_GI_APPLY(bpy.types.Operator):
    bl_idname='hl_fixed_gi.apply';bl_label='Apply Intensity'
    def execute(self,context):
        queue_factor(context.scene,context);ui['changed']=0.
        return {'FINISHED'}
class HL_FIXED_GI_STOP(bpy.types.Operator):
    bl_idname='hl_fixed_gi.stop';bl_label='Stop Worker'
    def execute(self,context):stop();return {'FINISHED'}
class HL_FIXED_GI_RESTART(bpy.types.Operator):
    bl_idname='hl_fixed_gi.restart';bl_label='Restart Worker (8 updates)'
    def execute(self,context):
        # Stop the prior controller timer before registering exactly one replacement.
        if bpy.app.timers.is_registered(tick):bpy.app.timers.unregister(tick)
        restart();return {'FINISHED'}
class HL_FIXED_GI_PT_panel(bpy.types.Panel):
    bl_label='Fixed-Room GI Experiment';bl_idname='HL_FIXED_GI_PT_panel'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='GI Test'
    def draw(self,context):
        layout=self.layout
        layout.label(text='Fixed room / one light / lighting only',icon='INFO')
        layout.label(text='Geometry edits are not supported.')
        row=layout.row();row.enabled=ui['scale_allowed'];row.prop(context.scene,'hl_fixed_gi_factor',text='Intensity')
        row=layout.row();row.enabled=ui['scale_allowed'] and ui['phase'] in ('idle','solving');row.operator('hl_fixed_gi.apply')
        layout.label(text=f'Updates remaining: {max(0,8-ui["count"])}')
        layout.label(text=ui['status'])
        layout.label(text='Navigation keeps the last lighting visible.')
        layout.label(text='New work waits; an active solve may finish.')
        row=layout.row();row.operator('hl_fixed_gi.stop');row.operator('hl_fixed_gi.restart')

for cls in (HL_FIXED_GI_APPLY,HL_FIXED_GI_STOP,HL_FIXED_GI_RESTART,HL_FIXED_GI_PT_panel):bpy.utils.register_class(cls)
bpy.types.Scene.hl_fixed_gi_factor=bpy.props.FloatProperty(name='Intensity',default=1.,min=0.,max=4.,description='Scale the fixture light; changes queue until navigation and slider interaction settle',update=queue_factor)
for area in bpy.context.screen.areas:
    if area.type=='VIEW_3D':area.spaces.active.show_region_ui=True
bpy.app.timers.register(restart,first_interval=.3)
