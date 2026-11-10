"""Real saved-map one-bounce reuse diagnostic. No scene save or installation."""
from pathlib import Path
import sys,json,time,traceback,statistics,os
import bpy,gpu,numpy as np
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent))
from hammerless.blender import props,l4d2preview as preview
from hammerless.blender.previewmaterials import BounceMaterials
from hammerless.blender.previewtrace import TraceCache
from hammerless.core.compile import find_game_root
from hammerless.blender.ops import game_content
from radiance_reuse_support import prepare,compile_reuse_shader
OUT=ROOT.parents[1]/'outputs/l4d2-preview'

def run():
    props.register();started=time.perf_counter()
    path=Path(sys.argv[sys.argv.index('--')+1]).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
    sc=bpy.context.scene;scale=sc.hammerless.units_per_meter
    spawns=[o for o in sc.objects if o.hammerless.classname in ('info_player_start','info_survivor_position')]
    assert spawns,'Real-map test requires a known player spawn'
    eye=spawns[0].matrix_world.translation+Vector((0,0,64/scale))
    camera=bpy.data.objects.new('Isolated reuse camera',bpy.data.cameras.new('Isolated reuse camera'))
    sc.collection.objects.link(camera);camera.location=eye;camera.data.clip_end=10000
    bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
    scene=preview.SceneCache();scene.update(deps);scene.update_lights(deps)
    root=find_game_root();assert root
    resolver=BounceMaterials(game_content(root),os.path.join(root,'left4dead2'),sc.hammerless.default_material)
    scene.trace=TraceCache();scene.trace.update_bounce_materials(scene,resolver)
    assert scene.lights or any(scene.sky_color),'Map has no extracted actual lighting'
    scene_setup_ms=(time.perf_counter()-started)*1000
    ts=time.perf_counter();full=preview.diagnostic_shader(lit=True,shadows=True,bounce=True)
    baseline_compile_ms=(time.perf_counter()-ts)*1000
    ts=time.perf_counter();cached=compile_reuse_shader(scene)
    reuse_compile_ms=(time.perf_counter()-ts)*1000
    width,height=80,45;off=gpu.types.GPUOffScreen(width,height,format='RGBA32F')
    rows=[];previous=None;projections=[]
    for direction in (Vector((1,0,0)),Vector((0,1,0))):
        camera.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
        bpy.context.view_layer.update()
        projections.append(camera.calc_matrix_camera(deps,x=width,y=height)@camera.matrix_world.inverted())
    def draw(shader,projection,rays,seed=1,read=False):
        with off.bind():
            gpu.state.viewport_set(0,0,width,height);fb=gpu.state.active_framebuffer_get()
            fb.clear(color=(0,0,0,1),depth=1)
            preview.draw_geometry(scene,shader,projection,lit=True,shadows=True,bounce_samples=rays,bounce_depth=1,bounce_seed=seed)
            if read:return np.asarray(fb.read_color(0,0,width,height,4,0,'FLOAT').to_list(),np.float32)[:,:,:3]
            fb.read_color(width//2,height//2,1,1,4,0,'FLOAT')
    def save_comparison(a,b,name):
        err=np.abs(b-a)*10
        panels=np.concatenate((a,b,err),axis=1)
        rgb=np.clip(panels,0,1);rgb=np.where(rgb<=.0031308,12.92*rgb,1.055*np.power(rgb,1/2.4)-.055)
        pixels=np.concatenate((rgb,np.ones((*rgb.shape[:2],1),np.float32)),axis=2)
        image=bpy.data.images.new(name,width=width*3,height=height,alpha=True,float_buffer=True)
        image.colorspace_settings.name='Non-Color';image.pixels.foreach_set(pixels.ravel())
        image.filepath_raw=str(OUT/(name+'.png'));image.file_format='PNG';image.save();bpy.data.images.remove(image)
    try:
        for source_spacing in (32,):
            ts=time.perf_counter()
            prepared=prepare(scene,resolver,spacing=source_spacing/scale,max_samples=500000,reuse=previous,max_side=4)
            prepared.install(scene);prep_ms=(time.perf_counter()-ts)*1000;previous=prepared
            for view,projection in enumerate(projections):
                direct=draw(full,projection,0,read=True)
                for rays in (8,):
                    ref=draw(full,projection,rays,read=True);candidate=draw(cached,projection,rays,read=True)
                    assert np.isfinite(ref).all() and np.isfinite(candidate).all()
                    indirect=ref-direct;delta=candidate-ref
                    denom=float(np.sqrt(np.mean(indirect**2)))
                    metric=dict(measurable_indirect=denom>1e-6,indirect_rms=denom,relative_indirect_rmse=float(np.sqrt(np.mean(delta**2))/max(denom,1e-10)),
                                max_linear_error=float(np.max(np.abs(delta))),absolute_error_percentiles=np.percentile(np.abs(delta),[50,95,99]).tolist(),
                                reference_mean=float(ref.mean()),reference_peak=float(ref.max()))
                    times={'baseline':[],'reuse':[]}
                    for trial in range(3):
                        for kind in (('baseline','reuse') if trial%2==0 else ('reuse','baseline')):
                            ts=time.perf_counter();draw(full if kind=='baseline' else cached,projection,rays)
                            times[kind].append((time.perf_counter()-ts)*1000)
                    row=dict(spacing_source_units=source_spacing,view=view,rays=rays,prepare_cpu_ms=prep_ms,coverage=prepared.stats,
                             median_ms={k:statistics.median(v) for k,v in times.items()},timings_ms=times,**metric)
                    rows.append(row);print(json.dumps(row),flush=True)
                    (OUT/'radiance-realmap-progress.json').write_text(json.dumps({'rows':rows,'lights':len(scene.lights),'sky':list(scene.sky_color)},indent=2))
                    if rays==8:save_comparison(ref,candidate,f'radiance-hotel-{source_spacing}-view{view}')
        assert any(r['measurable_indirect'] for r in rows),'No measurable indirect lighting in either view'
        return dict(ok=True,execution_only=True,measurable_cases=sum(r['measurable_indirect'] for r in rows),file=str(path),scene_setup_ms=scene_setup_ms,baseline_compile_ms=baseline_compile_ms,
                    reuse_compile_ms=reuse_compile_ms,instances=len(scene.instances),geometry=len(scene.geometry),
                    lights=len(scene.lights),sky=list(scene.sky_color),screen=[width,height],rows=rows,
                    scope='Actual saved map geometry and extracted lights, two spawn viewpoints, one bounce, opaque preview material policy. Coarse max4x4 pertriangle donor grids to establish fullcoverage; bounded donor budget with original direct fallback; reported coverage is triangle coverage, not ray-hit coverage. ok indicates completed measurement, not acceptable image error. Software tracing both paths. This is not a native VRAD comparison or final convergence benchmark.')
    finally:off.free()
try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
OUT.mkdir(parents=True,exist_ok=True);(OUT/'radiance-reuse-realmap.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
