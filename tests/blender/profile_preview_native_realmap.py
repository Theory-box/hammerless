"""Bounded actual-map native/software parity. One Blender, one owned native worker.
No saving the input file. Run interactively with --factory-startup --python this.py -- MAP.blend.
"""
from pathlib import Path
import sys,json,time,traceback,os
import bpy,gpu,numpy as np
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,l4d2preview as preview,previewnative
from hammerless.blender.previewnativeview import NativeView
from hammerless.blender.previewmaterials import BounceMaterials,SurfaceMaterials
from hammerless.blender.previewtrace import TraceCache
from hammerless.core.compile import find_game_root
from hammerless.blender.ops import game_content
OUT=ROOT.parents[1]/'outputs/l4d2-preview'

def run():
    props.register();started=time.perf_counter()
    path=Path(sys.argv[sys.argv.index('--')+1]).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
    sc=bpy.context.scene;scale=sc.hammerless.units_per_meter
    spawns=[o for o in sc.objects if o.hammerless.classname in ('info_player_start','info_survivor_position')]
    assert spawns,'Map requires a player spawn for reproducible views'
    camera=bpy.data.objects.new('Native parity camera',bpy.data.cameras.new('Native parity camera'))
    sc.collection.objects.link(camera);camera.location=spawns[0].matrix_world.translation+Vector((0,0,64/scale));camera.data.clip_end=10000
    bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
    scene=preview.SceneCache();scene.update(deps);scene.update_lights(deps)
    root=find_game_root();assert root
    content=game_content(root);game=os.path.join(root,'left4dead2')
    resolver=BounceMaterials(content,game,sc.hammerless.default_material)
    surface=SurfaceMaterials(content,game,sc.hammerless.default_material)
    for g in scene.geometry.values():
        for mat in g.materials or (None,):surface.resolve(mat);resolver.resolve(mat)
    scene.surface_materials=surface
    scene_setup_ms=(time.perf_counter()-started)*1000
    ts=time.perf_counter();scene.trace=TraceCache();scene.trace.update_bounce_materials(scene,resolver)
    software_trace_prepare_ms=(time.perf_counter()-ts)*1000
    ts=time.perf_counter();shader=preview.diagnostic_shader(lit=True,textured=True,shadows=True,bounce=True)
    shader_compile_ms=(time.perf_counter()-ts)*1000
    width,height=160,90;off=gpu.types.GPUOffScreen(width,height,format='RGBA32F')
    native=NativeView(owner='realmap-parity');rows=[];serial=0
    ts=time.perf_counter();native.prepare(scene,resolver,'geometry','lights');native_cpu_prepare_ms=(time.perf_counter()-ts)*1000
    def baseline(matrix,rays,seed):
        ts=time.perf_counter()
        with off.bind():
            gpu.state.viewport_set(0,0,width,height);fb=gpu.state.active_framebuffer_get();fb.clear(color=(0,0,0,0),depth=1)
            preview.draw_geometry(scene,shader,matrix,lit=True,textured=True,shadows=True,bounce_samples=rays,bounce_depth=1,bounce_seed=seed,
                sampled_bounce_sky=True,sampled_bounce_area=True,primary_sky_index=(seed*9)%162,primary_sky_count=9,primary_area_phase=seed%8)
            pixels=np.asarray(fb.read_color(0,0,width,height,4,0,'FLOAT').to_list(),dtype=np.float32).reshape(-1,4)[:,:3]
        return pixels,(time.perf_counter()-ts)*1000
    def shade(rays,seed):
        nonlocal serial
        serial+=1;ts=time.perf_counter();client=native.client
        client.submit(native.owner,serial,native.geometry_token,native.vertices,native.metadata,native.lights_token,native.lights,native.points,
            (rays,1,seed,3,float(scene.unit_scale),*scene.sky_color))
        while time.perf_counter()-ts<35:
            result=client.poll(native.owner,serial)
            if result is not None:break
            time.sleep(.001)
        else:raise RuntimeError('Native deadline exceeded')
        if result.error:raise RuntimeError(result.error)
        return result,(time.perf_counter()-ts)*1000
    def save_image(ref,candidate,view):
        panel=np.concatenate([ref.reshape(height,width,3),candidate.reshape(height,width,3),10*np.abs(candidate-ref).reshape(height,width,3)],axis=1)
        rgb=np.clip(panel,0,1);rgb=np.where(rgb<=.0031308,12.92*rgb,1.055*rgb**(1/2.4)-.055)
        rgba=np.concatenate((rgb,np.ones((*rgb.shape[:2],1),dtype=np.float32)),axis=2)
        image=bpy.data.images.new('Native comparison',width=width*3,height=height,alpha=True,float_buffer=True)
        image.colorspace_settings.name='Non-Color';image.pixels.foreach_set(rgba.ravel());image.filepath_raw=str(OUT/f'native-realmap-view{view}.png');image.file_format='PNG';image.save();bpy.data.images.remove(image)
    try:
        for view,direction in enumerate((Vector((1,0,0)),Vector((0,1,0)))):
            camera.rotation_euler=direction.to_track_quat('-Z','Y').to_euler();bpy.context.view_layer.update()
            matrix=camera.calc_matrix_camera(deps,x=width,y=height)@camera.matrix_world.inverted()
            ts=time.perf_counter()
            with off.bind():
                gpu.state.viewport_set(0,0,width,height);native._capture(scene,matrix,(width,height))
                with native.framebuffer.bind():
                    albedo=np.asarray(native.framebuffer.read_color(0,0,width,height,4,0,'FLOAT').to_list(),dtype=np.float32).reshape(-1,4)
            capture_ms=(time.perf_counter()-ts)*1000
            mask=np.zeros(width*height,dtype=bool);mask[native.covered]=True;assert mask.any(),'View has no receivers'
            references=[];candidates=[]
            for seed in (0,1):
                ref,baseline_ms=baseline(matrix,8,seed)
                result,native_ms=shade(8,seed)
                irradiance=np.zeros((width*height,3),dtype=np.float32);irradiance[native.covered]=result.pixels[:,:3]
                candidate=albedo[:,:3]*np.where(albedo[:,3:4]>.5,1.,irradiance)
                assert np.isfinite(ref).all() and np.isfinite(candidate).all()
                diff=(candidate-ref)[mask];denom=float(np.sqrt(np.mean(ref[mask]**2)))
                row=dict(view=view,seed=seed,rays=8,bounces=1,flags=3,covered=int(mask.sum()),capture_ms=capture_ms,
                    software_ms=baseline_ms,native_roundtrip_ms=native_ms,native_reported_ms=result.elapsed_ms,
                    native_scene_uploaded=result.scene_uploaded,native_lights_uploaded=result.lights_uploaded,
                    rms=float(np.sqrt(np.mean(diff**2))),relative_rmse=float(np.sqrt(np.mean(diff**2))/max(denom,1e-10)),
                    max_abs=float(np.max(np.abs(diff))),absolute_error_percentiles=np.percentile(np.abs(diff),[50,95,99]).tolist(),reference_mean=float(ref[mask].mean()))
                rows.append(row);references.append(ref);candidates.append(candidate);print(json.dumps(row),flush=True)
                OUT.mkdir(parents=True,exist_ok=True);(OUT/'native-realmap-progress.json').write_text(json.dumps(rows,indent=2))
            save_image(np.mean(references,axis=0),np.mean(candidates,axis=0),view)
        return dict(ok=True,file=str(path),screen=[width,height],instances=len(scene.instances),lights=len(scene.lights),triangles=len(native.vertices),sky=list(scene.sky_color),
            scene_setup_ms=scene_setup_ms,software_trace_prepare_ms=software_trace_prepare_ms,shader_compile_ms=shader_compile_ms,native_cpu_prepare_ms=native_cpu_prepare_ms,rows=rows,
            scope='Two spawn views; same8rays,1bounce,seeds0/1,sky/area sampling; textured linear RGB compared only on raster-covered pixels. First native roundtrip includes process/AS setup. Capture includes readback; subsequent shade reuses captured receivers. Measurements are bounded diagnostics, not final convergence or fullviewport performance.')
    finally:
        native.close();previewnative.shutdown();off.free()
try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
OUT.mkdir(parents=True,exist_ok=True);(OUT/'native-realmap.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
