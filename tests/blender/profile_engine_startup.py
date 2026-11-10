"""Same saved scene/camera/resolution, isolated process per engine. No saves."""
from pathlib import Path
import sys,time,json,cProfile,pstats,io,traceback
import bpy,gpu
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2]
import_root=Path.home()/'AppData/Roaming/Blender Foundation/Blender/4.4/extensions/user_default' if '--installed' in sys.argv else ROOT
sys.path.insert(0,str(import_root))
args=sys.argv[sys.argv.index('--')+1:];mode=args[0];label=args[1] if len(args)>1 else 'baseline'
OUT=ROOT.parents[1]/'outputs/l4d2-preview'/f'startup-{mode}-{label}'
from hammerless.blender import props
props.register()
if mode=='workbench':
    sys.path.insert(0,str(Path.home()/'AppData/Roaming/Blender Foundation/Blender/4.4/scripts/addons'))
    import vertex_lit_renderer as addon
    from vertex_lit_renderer.engine import VertexLitEngine as Engine

else:
    from hammerless.blender import l4d2preview as addon
    Engine=addon.HAMMERLESS_L4D2Preview
profiles={'update':cProfile.Profile(),'draw':cProfile.Profile()}
state={'mode':mode,'label':label,'calls':{'update':0,'draw':0},'times':{'update':0.,'draw':0.},'done':False,'profile_enabled':'--no-profile' not in args}
started=None
if mode!='workbench':
    from hammerless.blender.previewlivecache import LiveSurfaceCache
    if hasattr(LiveSurfaceCache,'advance'):
        advance_original=LiveSurfaceCache.advance
        def timed_advance(self,*a,**kw):
            ts=time.perf_counter()
            try:return advance_original(self,*a,**kw)
            finally:state.setdefault('grid_batch_seconds',[]).append(time.perf_counter()-ts)
        LiveSurfaceCache.advance=timed_advance
def finish(error=None):
    if state['done']:return
    state['done']=True;state['ok']=error is None;state['error']=error
    for name,profile in profiles.items():
        profile.dump_stats(str(OUT)+f'-{name}.prof')
        stream=io.StringIO()
        if profile.getstats():pstats.Stats(profile,stream=stream).sort_stats('cumulative').print_stats(65)
        Path(str(OUT)+f'-{name}.txt').write_text(stream.getvalue())
    Path(str(OUT)+'.json').write_text(json.dumps(state,indent=2))
    from hammerless.blender import previewnative
    previewnative.shutdown()
    bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
def wrap_callback(_name,_original):
    def wrapper(self,context,depsgraph):
        if state['done']:return
        ts=time.perf_counter()
        if state['profile_enabled']:profiles[_name].enable()
        try:_original(self,context,depsgraph)
        except Exception:finish(traceback.format_exc());return
        finally:
            profiles[_name].disable();state['times'][_name]+=time.perf_counter()-ts;state['calls'][_name]+=1
        if _name!='draw':return
        state.setdefault('first_draw_seconds',time.perf_counter()-started)
        if mode=='workbench':
            ready=bool(getattr(self,'_mesh_cache',None)) and not getattr(self,'_geo_pending',True)
            state['mesh_cache_objects']=len(getattr(self,'_mesh_cache',{}))
        else:
            if self.error:finish(self.error);return
            ready=self.native_surface is not None and self.native_surface.evaluations>0
            state['instances']=len(self.cache.instances)
            state['lights']=len(self.cache.lights)
            state['surface_samples']=len(self.native_surface.values) if self.native_surface else 0
        if ready:
            state['ready_seconds']=time.perf_counter()-started
            state['ready_definition']='geometry ready' if mode=='workbench' else 'first native surface batch received'
            state['finish_pending']=True
    return wrapper
for name,method in [('update','view_update'),('draw','view_draw')]:
    setattr(Engine,method,wrap_callback(name,getattr(Engine,method)))

try:
    path=ROOT.parents[1]/'outputs/c1m1_hotel_with_transition.blend'
    ts=time.perf_counter();bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
    state['file_load_seconds']=time.perf_counter()-ts;state['file']=str(path)
    addon.register()
    scene=bpy.context.scene;scale=scene.hammerless.units_per_meter
    spawn=next(o for o in scene.objects if o.hammerless.classname in ('info_player_start','info_survivor_position'))
    position=spawn.matrix_world.translation+Vector((0,0,64/scale))
    if mode!='workbench':
        scene.hl_preview_native=True;scene.hl_preview_surface_cache=True
        scene.hl_preview_bounce_samples=4;scene.hl_preview_max_bounces=1;scene.hl_preview_refine_frames=1
    state['resolution']=[320,180]
    for area in [a for screen in bpy.data.screens for a in screen.areas]:
        if area.type=='VIEW_3D':
            space=area.spaces.active;space.overlay.show_overlays=False
            rv=space.region_3d;rv.view_rotation=Vector((1,0,0)).to_track_quat('-Z','Y')
            rv.view_distance=.1;rv.view_location=position+Vector((.1,0,0));rv.view_perspective='PERSP'
    bpy.context.view_layer.update()
    started=time.perf_counter();scene.render.engine=Engine.bl_idname
    for area in [a for screen in bpy.data.screens for a in screen.areas]:
        if area.type=='VIEW_3D':area.spaces.active.shading.type='RENDERED'
    def draw_tick():
        if state['done']:return
        if state.get('finish_pending'):finish();return
        if time.perf_counter()-started>100:finish('100-second startup deadline');return
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type!='VIEW_3D':continue
                    region=next(r for r in area.regions if r.type=='WINDOW');space=area.spaces.active
                    off=gpu.types.GPUOffScreen(320,180)
                    try:
                        with bpy.context.temp_override(window=window,area=area,region=region):
                            off.draw_view3d(scene,bpy.context.view_layer,space,region,space.region_3d.view_matrix,space.region_3d.window_matrix)
                    finally:off.free()
        except Exception:finish(traceback.format_exc());return
        return .05
    bpy.app.timers.register(draw_tick,first_interval=.1)
except Exception:finish(traceback.format_exc())
