"""Area-light GPU radiometry and bounded sampling-cost test."""
from pathlib import Path
import sys,json,traceback,time,statistics,math
import bpy,gpu,numpy as np
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props
from hammerless.blender.l4d2preview import SceneCache,Geometry,diagnostic_shader,draw_geometry
OUT=ROOT.parents[1]/'outputs/l4d2-preview/area-gpu-check.json'
def run():
    props.register();s=bpy.context.scene;s.hammerless.auto_sun=False;s.hammerless.ambient_brightness=0;s.hammerless.units_per_meter=1
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.ops.object.light_add(type='AREA',location=(0,0,128));lamp=bpy.context.object
    lamp.data.size=64;lamp.data.energy=10000
    deps=bpy.context.evaluated_depsgraph_get();cache=SceneCache();cache.update_lights(deps)
    p=np.array([[-256,-256,0],[256,-256,0],[256,256,0],[-256,-256,0],[256,256,0],[-256,256,0]],np.float32)
    g=Geometry(p,np.tile([0,0,1],(6,1)).astype(np.float32));g.face_normals=g.normals
    cache.geometry={1:g};cache.instances=[(1,Matrix.Identity(4))]
    shader=diagnostic_shader(lit=True,shadows=True)
    off=gpu.types.GPUOffScreen(1,1,format='RGBA32F')
    def pixel(phase=-1):
        with off.bind():
            gpu.state.viewport_set(0,0,1,1);fb=gpu.state.active_framebuffer_get();fb.clear(color=(0,0,0,1),depth=1)
            draw_geometry(cache,shader,Matrix.Scale(1/256,4),lit=True,shadows=True,primary_area_phase=phase)
            return np.array(fb.read_color(0,0,1,1,4,0,'FLOAT').to_list()[0][0][:3])
    try:
        expected=np.zeros(3)
        for position,intensity,*_ in cache.lights:
            delta=np.array(position)-[0,0,1];d2=np.dot(delta,delta)
            expected+=np.array(intensity)*delta[2]**2/d2**2/255
        actual=pixel();assert np.allclose(actual,expected,rtol=2e-5,atol=1e-7),(actual,expected)
        # Half-plane at z=64 blocks exactly the positive-X emitter samples.
        blocker_positions=np.array([[0,-256,64],[256,-256,64],[256,256,64],
                                    [0,-256,64],[256,256,64],[0,256,64]],np.float32)
        blocker=Geometry(blocker_positions,np.tile([0,0,1],(6,1)).astype(np.float32))
        blocker.face_normals=blocker.normals
        cache.geometry[2]=blocker;cache.instances.append((2,Matrix.Identity(4)))
        partial=pixel()
        cycle=np.mean([pixel(i) for i in range(8)],axis=0)
        assert np.allclose(cycle,partial,atol=1e-7), (cycle,partial)
        assert np.allclose(partial,actual*.5,rtol=2e-5,atol=1e-7),(partial,actual)
        lamp.data.shape='DISK';lamp.data.size=65
        bpy.context.view_layer.update();cache.update_lights(deps)
        assert len(cache.lights)==17
        uneven=pixel();uneven_cycle=np.mean([pixel(i) for i in range(8)],axis=0)
        assert np.allclose(uneven,uneven_cycle,atol=1e-7),(uneven,uneven_cycle)
        lamp.data.shape='SQUARE';lamp.data.size=64
        bpy.context.view_layer.update();cache.update_lights(deps)
        builds=cache.trace.builds
        cache.instances[1]=(2,Matrix.Translation((512,0,0)))
        restored=pixel()
        assert np.allclose(restored,actual,rtol=2e-5)
        assert cache.trace.builds==builds,'Moving blocker rebuilt mesh BVH'
        del cache.geometry[2];cache.instances=cache.instances[:1]
        lamp.data.energy*=2;bpy.context.view_layer.update();cache.update_lights(deps)
        doubled=pixel();assert np.allclose(doubled,2*actual,rtol=2e-5)
        lamp.rotation_euler.x=math.pi;bpy.context.view_layer.update();cache.update_lights(deps)
        back=pixel();assert np.max(abs(back))<1e-7,back
    finally:off.free()
    lamp.rotation_euler.x=0;lamp.data.energy=10000
    bpy.context.view_layer.update();cache.update_lights(deps)
    # Use the compiled fixture's actual occluder triangles, including thickness.
    from hammerless.core.lightbake import read_faces
    from hammerless.core.lightmap import decode_luxels
    from hammerless.core.bspdecompile import Bsp
    source=OUT.parent/'playable-area-reference/room.bsp'
    data=source.read_bytes();faces,lump=read_faces(data);raw=Bsp(data).lump(lump)
    original_geometry,original_instances=cache.geometry,cache.instances
    cache.geometry={};cache.instances=[];targets={}
    for f in faces:
        pos=f.positions.astype(np.float32)
        geom=Geometry(pos,np.tile(f.normal,(len(pos),1)).astype(np.float32));geom.face_normals=geom.normals
        cache.geometry[f.index]=geom;cache.instances.append((f.index,Matrix.Identity(4)))
        if f.normal[2]>.99 and max(abs(pos[:,2]))<.01:
            vals=decode_luxels(raw[f.light_ofs:f.light_ofs+4*f.w*f.h]).reshape(f.h,f.w,3)
            for iy in range(f.h):
                for ix in range(f.w):
                    q=f.to_world(np.array([ix,iy]))
                    if abs(q[1])<.01 and -64<=q[0]<=64 and abs(q[0]/8-round(q[0]/8))<1e-5:
                        targets[float(q[0])]=vals[iy,ix]
    assert len(targets)>=16
    profile=[];off=gpu.types.GPUOffScreen(1,1,format='RGBA32F')
    try:
        with off.bind():
            gpu.state.viewport_set(0,0,1,1);fb=gpu.state.active_framebuffer_get()
            for x,stored in sorted(targets.items()):
                fb.clear(color=(0,0,0,1),depth=1)
                draw_geometry(cache,shader,Matrix.Scale(1/256,4)@Matrix.Translation((-x,0,0)),lit=True,shadows=True)
                value=np.array(fb.read_color(0,0,1,1,4,0,'FLOAT').to_list()[0][0][:3])
                neighbors=[]
                for dx in (-2,2):
                    for dy in (-2,2):
                        fb.clear(color=(0,0,0,1),depth=1)
                        draw_geometry(cache,shader,Matrix.Scale(1/256,4)@Matrix.Translation((-x-dx,-dy,0)),lit=True,shadows=True)
                        neighbors.append(fb.read_color(0,0,1,1,4,0,'FLOAT').to_list()[0][0][:3])
                filtered=np.mean(neighbors,axis=0)
                profile.append({'x':x,'gpu':value.tolist(),'baked':stored.tolist(),'filtered_gpu':filtered.tolist(),
                    'filtered_error':float(np.max(abs(filtered-stored))),'max_error':float(np.max(abs(value-stored)))})
    finally:off.free()
    cache.geometry,cache.instances=original_geometry,original_instances
    profile_error=max(r['max_error'] for r in profile)
    filtered_profile_error=max(r['filtered_error'] for r in profile)
    assert filtered_profile_error<.001,filtered_profile_error
    assert np.isfinite(profile_error)
    timings=[];off=gpu.types.GPUOffScreen(1280,720,format='RGBA32F')
    try:
        for size in (64,256):
            lamp.data.size=size;bpy.context.view_layer.update();cache.update_lights(deps)
            ms=[]
            with off.bind():
                gpu.state.viewport_set(0,0,1280,720);fb=gpu.state.active_framebuffer_get()
                for i in range(12):
                    start=time.perf_counter();fb.clear(color=(0,0,0,1),depth=1)
                    draw_geometry(cache,shader,Matrix.Scale(1/256,4),lit=True,shadows=True)
                    fb.read_color(640,360,1,1,4,0,'FLOAT');ms.append((time.perf_counter()-start)*1000)
            timings.append({'samples':len(cache.lights),'median_ms':statistics.median(ms[2:])})
    finally:off.free()
    return {'ok':True,'actual':actual.tolist(),'independent_expected':expected.tolist(),'back':back.tolist(),'half_occluded':partial.tolist(),'eight_phase_mean':cycle.tolist(),'uneven_sample_cycle_error':float(np.max(abs(uneven-uneven_cycle))),'blocker_transform_reused_bvh':True,
            'baked_profile':profile,'baked_profile_max_error':profile_error,'filtered_profile_max_error':filtered_profile_error,'gpu':gpu.platform.renderer_get(),'resolution':[1280,720],'timings':timings,
            'scope':'Flat receiver, one square area, half-plane blocker with analytic 50% visibility. Independent hemisphere cosine and inverse-square calculation. Timings are a simple scene, not representative-map performance or baked penumbra parity.'}
try:result=run()
except Exception:result={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
