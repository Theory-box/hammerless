"""Bounded one-bounce radiance-reuse experiment; no production shader changes.

Two 20x20 planes, full offscreen ceiling donor, point lights, no sky/alpha.
The donor grid is measured with the existing shadowed direct-light shader.
A test-only shader substitution samples that grid at the exact same ray hits.
Preparation is CPU-observed allocation, solve/readback, and upload submission time;
texture upload completion is not separately synchronized (a simple test bridge).
This is not a native-VRAD comparison or a general atlas implementation.
"""
from pathlib import Path
import sys,json,time,traceback,statistics
import bpy,gpu,numpy as np
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import previewbounce
from hammerless.blender.l4d2preview import Geometry,SceneCache,diagnostic_shader,draw_geometry
from hammerless.blender.previewtrace import TraceCache,texture
from hammerless.blender.previewmaterials import BounceMaterial
from hammerless.blender.previewlightcache import SurfaceLightCache
OUT=ROOT.parents[1]/'outputs/l4d2-preview/radiance-reuse-profile.json'


def run():
    start=time.perf_counter()
    p=np.array([[-10,-10,0],[10,-10,0],[10,10,0],[-10,-10,0],[10,10,0],[-10,10,0]],np.float32)
    floor=Geometry(p,np.tile([0,0,1],(6,1)).astype(np.float32))
    ceiling=Geometry(p.reshape(2,3,3)[:,::-1].reshape(6,3).copy(),np.tile([0,0,-1],(6,1)).astype(np.float32))
    scene=SceneCache();scene.unit_scale=64;scene.geometry={1:floor,2:ceiling}
    scene.instances=[(1,Matrix.Identity(4)),(2,Matrix.Translation((0,0,4)))]
    scene.trace=TraceCache()
    class Resolver:
        def resolve(self,mat):return BounceMaterial((.6,.3,.15),True,True,'controlled fixture')
    scene.trace.update_bounce_materials(scene,Resolver())
    # Two triangles per plane, no shared meshes. Keep original reflectivity rows.
    base=np.tile([.6,.3,.15,1],(4,1)).astype(np.float32)
    shader_start=time.perf_counter()
    full=diagnostic_shader(lit=True,shadows=True,bounce=True)
    baseline_shader_compile_ms=(time.perf_counter()-shader_start)*1000
    screen=(256,144);off=gpu.types.GPUOffScreen(*screen,format='RGBA32F')
    common_setup_ms=(time.perf_counter()-start)*1000
    shader_ms={};cached_shaders={}
    original=previewbounce.GLSL
    try:
        for side in (33,65,129):
            helper='''
vec3 cachedDirect(vec3 hit,int tri) {
    if(tri<2 || tri>3)return vec3(0);
    vec2 q=clamp((hit.xy+vec2(10.0))*(float(SIDE-1)/20.0),vec2(0),vec2(SIDE-1));
    ivec2 p=ivec2(floor(q)),b=min(p+ivec2(1),ivec2(SIDE-1));vec2 t=fract(q);
    return mix(mix(traceFetch(traceSurfaces,4+p.y*SIDE+p.x).rgb,
                   traceFetch(traceSurfaces,4+p.y*SIDE+b.x).rgb,t.x),
               mix(traceFetch(traceSurfaces,4+b.y*SIDE+p.x).rgb,
                   traceFetch(traceSurfaces,4+b.y*SIDE+b.x).rgb,t.x),t.y);
}
'''.replace('SIDE',str(side))
            old='previewDirect(hit+hn*(1.0/unitScale),hn,skyIndex,1,areaPhase)'
            assert old in original
            # Keep the production interface live: if sampled sky/area were
            # requested, the test shader must use the original direct evaluator.
            # This fixture has neither and passes both flags false. Unlike a
            # uniform-error wrapper this retains the real GPUShader for batches.
            replacement='((bounceSkySampled!=0 || bounceAreaSampled!=0) ? '+old+' : cachedDirect(hit,tri))'
            previewbounce.GLSL=helper+original.replace(old,replacement)
            ts=time.perf_counter();cached_shaders[side]=diagnostic_shader(lit=True,shadows=True,bounce=True)
            shader_ms[str(side)]=(time.perf_counter()-ts)*1000
    finally:previewbounce.GLSL=original

    def draw(shader,rays,seed=1,read_full=False):
        with off.bind():
            gpu.state.viewport_set(0,0,*screen);fb=gpu.state.active_framebuffer_get()
            fb.clear(color=(0,0,0,1),depth=1)
            draw_geometry(scene,shader,Matrix.Scale(.5,4),lit=True,shadows=True,
                          bounce_samples=rays,bounce_depth=1,bounce_seed=seed)
            if read_full:return np.asarray(fb.read_color(0,0,*screen,4,0,'FLOAT').to_list(),dtype=np.float32)[:,:,:3]
            # Synchronize GPU completion, without full image-transfer timing.
            fb.read_color(screen[0]//2,screen[1]//2,1,1,4,0,'FLOAT')

    rows=[];previous_donor=None
    try:
        for nlights in (1,8,32):
            scene.lights=[(((i%8-3.5)*.6,(i//8-1.5)*.7,1.8+(i%3)*.15),
                           (2000000./nlights,1500000./nlights,1000000./nlights),(0.,0.,1.))
                          for i in range(nlights)]
            scene.light_texture=None
            direct=draw(full,0,read_full=True)
            for side in (33,65,129):
                ts=time.perf_counter()
                xy=np.linspace(-10,10,side,dtype=np.float32)
                xx,yy=np.meshgrid(xy,xy)
                positions=np.column_stack((xx.ravel(),yy.ravel(),np.full(side*side,4,np.float32)))
                normals=np.tile([0,0,-1],(side*side,1)).astype(np.float32)
                setup_done=time.perf_counter()
                cold_donor=previous_donor is None
                donor=SurfaceLightCache(positions,normals,reuse=previous_donor)
                constructor_done=time.perf_counter()
                donor.refine(scene,('direct',nlights,side),max_frames=1,rays=0,bounces=1)
                refine_submitted=time.perf_counter()
                measured=donor.readback()*255.0
                readback_done=time.perf_counter()
                assert np.isfinite(measured).all() and measured.max()>1
                packed=np.concatenate((base,np.column_stack((measured,np.ones(len(measured),np.float32)))))
                pack_done=time.perf_counter()
                scene.trace.surfaces=texture(packed)
                upload_submitted=time.perf_counter()
                prep_ms=(upload_submitted-ts)*1000
                prep_stages=dict(sample_setup_ms=(setup_done-ts)*1000,
                                 constructor_ms=(constructor_done-setup_done)*1000,
                                 refine_submit_ms=(refine_submitted-constructor_done)*1000,
                                 readback_and_gpu_completion_ms=(readback_done-refine_submitted)*1000,
                                 packing_ms=(pack_done-readback_done)*1000,
                                 upload_submit_ms=(upload_submitted-pack_done)*1000,
                                 cold_donor_shader_compile=cold_donor)
                previous_donor=donor
                for rays in (8,32,128):
                    baseline=draw(full,rays,read_full=True)
                    reused=draw(cached_shaders[side],rays,read_full=True)
                    indirect=baseline-direct
                    assert indirect.max()>.005,'Fixture has no measurable indirect lighting'
                    delta=reused-baseline
                    relative_rmse=float(np.sqrt(np.mean(delta**2))/max(1e-10,np.sqrt(np.mean(indirect**2))))
                    assert np.isfinite(reused).all()
                    # Fine grid must resolve this smooth donor. Coarse grids are
                    # reported, not required to pass a high-quality threshold.
                    if side==129:assert relative_rmse<.01,(nlights,rays,relative_rmse)
                    timings={'baseline':[],'reuse':[]}
                    # Alternating ordering avoids always giving one path the warm GPU.
                    for trial in range(4):
                        order=('baseline','reuse') if trial%2==0 else ('reuse','baseline')
                        for kind in order:
                            ts=time.perf_counter();draw(full if kind=='baseline' else cached_shaders[side],rays)
                            timings[kind].append((time.perf_counter()-ts)*1000)
                    med={k:statistics.median(v) for k,v in timings.items()}
                    rows.append(dict(lights=nlights,grid_side=side,donor_samples=side*side,rays=rays,
                                     direct_grid_prepare_cpu_observed_ms=prep_ms,direct_grid_prepare_stages=prep_stages,median_ms=med,timings_ms=timings,
                                     max_linear_error=float(np.abs(delta).max()),indirect_relative_rmse=relative_rmse,
                                     baseline_indirect_mean=float(indirect.mean()),
                                     prepare_plus_warm_draw_estimate_ms=prep_ms+med['reuse']))
        return dict(ok=True,rows=rows,common_setup_ms=common_setup_ms,baseline_shader_compile_ms=baseline_shader_compile_ms,cache_shader_compile_ms=shader_ms,
                    screen=list(screen),timing_caveat='Shader compile times are reported separately for baseline and each cached test shader. First donor constructor includes cold shader creation; later donors reuse its shaders through the existing production reuse parameter. Preparation includes synchronized donor readback but only CPU-observed texture upload submission; summed prepare+warm draw is an estimate, not measured end-to-end latency.',scope='Fixed smooth floor/ceiling, full offscreen donor,1/8/32 point lights; actual shadowed direct grids; identical1-bounce rays; software traversal both paths. No scene edits, sky, occluder discontinuities, models, native VRAD, or final convergence comparison.')
    finally:off.free()

try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
