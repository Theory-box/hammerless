"""Sharp donor shadows, transformed/shared surfaces and budget fallback.
Controlled one-bounce experiment, not a production engine or VRAD comparison.
"""
from pathlib import Path
import sys,time,json,traceback,statistics,math
import bpy,gpu,numpy as np
from mathutils import Matrix,Vector
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent))
from hammerless.blender.l4d2preview import Geometry,SceneCache,diagnostic_shader,draw_geometry
from hammerless.blender.previewtrace import TraceCache
from hammerless.blender.previewmaterials import BounceMaterial
from radiance_reuse_support import prepare,compile_reuse_shader,ordered_instances
OUT=ROOT.parents[1]/'outputs/l4d2-preview';SIZE=(256,192)


def quad(half=4,reverse=False):
    p=np.array([[-half,-half,0],[half,-half,0],[half,half,0],[-half,-half,0],[half,half,0],[-half,half,0]],np.float32)
    if reverse:p=p.reshape(2,3,3)[:,::-1].reshape(-1,3).copy()
    return Geometry(p,np.tile([0,0,-1 if reverse else 1],(6,1)).astype(np.float32))


def save_image(name,pixels,gain=1):
    rgb=np.maximum(pixels*gain,0)
    rgb=np.where(rgb<=.0031308,rgb*12.92,1.055*np.power(rgb,1/2.4)-.055)
    rgba=np.concatenate((np.clip(rgb,0,1),np.ones((*rgb.shape[:2],1))),axis=2).astype(np.float32)
    image=bpy.data.images.new(name,width=SIZE[0],height=SIZE[1],alpha=True,float_buffer=True)
    image.colorspace_settings.name='Non-Color';image.pixels.foreach_set(rgba.ravel());image.filepath_raw=str(OUT/(name+'.png'));image.file_format='PNG';image.save();bpy.data.images.remove(image)


def run():
    scene=SceneCache();scene.unit_scale=64
    floor=quad();ceiling=quad(reverse=True);block=quad(.85)
    # Two-sided horizontal occluder blocks the point light reaching ceiling.
    rev=quad(.85,True);block.positions=np.concatenate((block.positions,rev.positions));block.normals=np.concatenate((block.normals,rev.normals));block.use_uv=True
    scene.geometry={1:floor,2:ceiling,3:block}
    scene.instances=[(1,Matrix.Identity(4)),(2,Matrix.Translation((0,0,4))),
                     (1,Matrix.Translation((0,4,2))@Matrix.Rotation(math.pi/2,4,'X')),
                     (1,Matrix.Translation((-4,0,2))@Matrix.Rotation(math.pi/2,4,'Y')),
                     (1,Matrix.Translation((4,0,2))@Matrix.Rotation(-math.pi/2,4,'Y')),
                     (3,Matrix.Translation((-.8,.4,3.0))),
                     (3,Matrix.Translation((-2.5,1.5,2.7))@Matrix.Scale(.3,4)),
                     (3,Matrix.Translation((2.5,-1.5,2.6))@Matrix.Scale(.3,4)),
                     (3,Matrix.Translation((1.0,2.5,3.2))@Matrix.Scale(.3,4))]
    scene.lights=[((-1.4,-.8,1.8),(700000.,500000.,300000.),(0.,0.,1.)),
                  ((2.5,2.0,1.0),(30000.,50000.,90000.),(0.,0.,1.))]
    scene.trace=TraceCache()
    class Resolver:
        def resolve(self,mat):return BounceMaterial((.65,.45,.3),True,True,'fixture')
    resolver=Resolver();scene.trace.update_bounce_materials(scene,resolver)
    assert ordered_instances(scene)!=scene.instances,'Fixture must exercise TLAS reorder'
    t=time.perf_counter();baseline=diagnostic_shader(lit=True,shadows=True,bounce=True);baseline_compile=(time.perf_counter()-t)*1000
    t=time.perf_counter();reused_shader=compile_reuse_shader(scene);reuse_compile=(time.perf_counter()-t)*1000
    camera=bpy.data.objects.new('Fixture camera',bpy.data.cameras.new('Fixture camera'));bpy.context.scene.collection.objects.link(camera)
    camera.location=(0,-3.8,1.1);target=Vector((0,1.6,2.0));camera.rotation_euler=(target-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.lens=18
    bpy.context.view_layer.update();projection=camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=SIZE[0],y=SIZE[1])@camera.matrix_world.inverted()
    off=gpu.types.GPUOffScreen(*SIZE,format='RGBA32F')
    def draw(shader,rays,seed=1,read=True):
        with off.bind():
            gpu.state.viewport_set(0,0,*SIZE);fb=gpu.state.active_framebuffer_get();fb.clear(color=(0,0,0,1),depth=1)
            draw_geometry(scene,shader,projection,lit=True,shadows=True,bounce_samples=rays,bounce_depth=1,bounce_seed=seed)
            if read:return np.asarray(fb.read_color(0,0,*SIZE,4,0,'FLOAT').to_list(),np.float32)[:,:,:3]
            fb.read_color(0,0,1,1,4,0,'FLOAT')
    rows=[];previous=None
    try:
        missing=prepare(scene,resolver,spacing=.25,max_samples=0);missing.install(scene)
        a=draw(baseline,32);b=draw(reused_shader,32)
        missing_error=float(np.abs(a-b).max());assert missing_error<1e-6,missing_error
        direct=draw(baseline,0)
        for spacing in (.5,.25,.125):
            cached=prepare(scene,resolver,spacing=spacing,max_samples=500000,reuse=previous);cached.install(scene);previous=cached
            assert cached.stats['coverage_fraction']==1,cached.stats
            for rays in (32,128):
                ref=np.mean([draw(baseline,rays,seed) for seed in (1,2,3)],axis=0)
                result=np.mean([draw(reused_shader,rays,seed) for seed in (1,2,3)],axis=0)
                indirect=ref-direct;delta=result-ref;error=np.abs(delta)
                assert np.isfinite(result).all() and float(indirect.max())>.01
                timings={'baseline':[],'reuse':[]}
                for seed in (1,2,3):
                    for kind,shader in ((('baseline',baseline),('reuse',reused_shader)) if seed%2 else (('reuse',reused_shader),('baseline',baseline))):
                        t=time.perf_counter();draw(shader,rays,seed,False);timings[kind].append((time.perf_counter()-t)*1000)
                rows.append(dict(spacing=spacing,rays=rays,cache=cached.stats,median_ms={k:statistics.median(v) for k,v in timings.items()},
                    indirect_relative_rmse=float(np.sqrt(np.mean(delta**2))/max(1e-12,np.sqrt(np.mean(indirect**2)))),
                    max_linear_error=float(error.max()),p99_linear_error=float(np.percentile(error,99)),
                    indirect_mean=float(indirect.mean())))
                if rays==128:
                    label=str(spacing).replace('.','_');save_image('reuse-multisurface-reference',ref)
                    save_image('reuse-multisurface-'+label,result);save_image('reuse-multisurface-error-'+label,error,10)
        return dict(ok=True,rows=rows,missing_cache_max_error=missing_error,baseline_shader_compile_ms=baseline_compile,
            reuse_shader_compile_ms=reuse_compile,checks=['shared mesh differently transformed donors','TLAS instance reorder','all offscreen donors','full direct evaluation','zero budget exact fallback'],
            scope='Sharp pointlight shadow on ceiling,5 receiving planes incl3 shared-mesh transformed walls,4two-sided nonreceiving blockers;1bounce,3matchedseeds; no nativeVRAD comparison; resolution sweep reports error without declaring universal threshold')
    finally:off.free()

OUT.mkdir(parents=True,exist_ok=True)
try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
OUT.mkdir(parents=True,exist_ok=True);(OUT/'radiance-multisurface-check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
