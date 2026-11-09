"""Render a controlled room through the preview GPU path, without user files."""
from pathlib import Path
import sys,json,time,statistics,traceback
import bpy,gpu,numpy as np
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hammerless.blender import props,l4d2preview as preview
OUT=ROOT.parents[1]/'outputs/l4d2-preview'
OUT.mkdir(parents=True,exist_ok=True)


def run():
    bounce='--bounce' in sys.argv
    stem='first-bounce-room' if bounce else 'direct-sky-room'
    accumulate=bounce and '--accumulate' in sys.argv
    if accumulate:stem+='-accumulated'
    props.register()
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    scene=bpy.context.scene
    scene.hammerless.auto_sun=False
    scene.hammerless.ambient_brightness=20
    scene.hammerless.ambient_color=(1,1,1)
    scene.hammerless.sky_light='FLAT'
    scene.hammerless.units_per_meter=64
    scene.view_settings.view_transform='Standard'
    scene.view_settings.look='None'
    scene.view_settings.exposure=0
    scene.view_settings.gamma=1

    def box(name,position,size,color):
        bpy.ops.mesh.primitive_cube_add(size=1,location=position)
        obj=bpy.context.object;obj.name=name;obj.scale=size
        mat=bpy.data.materials.new(name);mat.diffuse_color=(*color,1)
        # Give export and preview a real texture, rather than a diffuse-only
        # material which the exporter replaces with the scene default.
        mat.use_nodes=True
        image=bpy.data.images.new(name+' texture',2,2,float_buffer=True)
        image.pixels.foreach_set(list((*color,1))*4)
        tex=mat.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image
        bsdf=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
        mat.node_tree.links.new(tex.outputs['Color'],bsdf.inputs['Base Color'])
        obj.data.materials.append(mat)
        return obj
    box('Floor',(0,0,-.15),(6,6,.3),(.55,.55,.55))
    box('Back',(0,3,1.5),(6,.25,3),(.65,.65,.65))
    box('Red wall',(-3,0,1.5),(.25,6,3),(.65,.08,.04))
    box('Green wall',(3,0,1.5),(.25,6,3),(.06,.45,.12))
    box('Tall block',(-1,.4,.9),(1.1,1.2,1.8),(.65,.65,.65))
    box('Short block',(1,-.5,.45),(1.2,1.2,.9),(.65,.65,.65))
    bpy.ops.object.light_add(type='POINT',location=(-.7,-1.0,2.7))
    bpy.context.object.data.energy=300
    bpy.context.object.data.color=(1,.85,.65)
    bpy.ops.object.camera_add(location=(8,-11,8))
    camera=bpy.context.object
    camera.rotation_euler=(Vector((0,0,1))-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.lens=48
    scene.camera=camera
    deps=bpy.context.evaluated_depsgraph_get()
    cache=preview.SceneCache();cache.update(deps);cache.update_lights(deps)
    if bounce:
        from hammerless.blender.previewtrace import TraceCache
        from hammerless.blender.previewmaterials import BounceMaterials
        cache.sky_color=(0.,0.,0.)
        cache.trace=TraceCache()
        cache.trace.update_bounce_materials(cache,BounceMaterials(None,None,''))
    width,height=(480,320) if bounce else (960,640)
    projection=camera.calc_matrix_camera(deps,x=width,y=height)@camera.matrix_world.inverted()
    shader=preview.diagnostic_shader(lit=True,textured=True,shadows=True,bounce=bounce)
    if accumulate:
        from hammerless.blender.previewhistory import FrameHistory
        history=FrameHistory();depth_shader=preview.depth_shader()
    off=gpu.types.GPUOffScreen(width,height,format='RGBA32F')
    samples=[]
    try:
        with off.bind():
            gpu.state.viewport_set(0,0,width,height)
            fb=gpu.state.active_framebuffer_get()
            for iteration in range(64 if accumulate else 6):
                start=time.perf_counter()
                fb.clear(color=(.025,.025,.025,1),depth=1)
                if accumulate:
                    history.render('fixture',lambda frame:preview.draw_geometry(cache,shader,projection,
                        lit=True,textured=True,shadows=True,bounce_samples=16,bounce_seed=frame,sampled_bounce_sky=True),
                        lambda:preview.draw_depth(cache,depth_shader,projection),max_frames=64)
                else:
                    preview.draw_geometry(cache,shader,projection,lit=True,textured=True,shadows=True,bounce_samples=16 if bounce else None)
                fb.read_color(width//2,height//2,1,1,4,0,'FLOAT')
                if iteration:samples.append((time.perf_counter()-start)*1000)
            if accumulate:
                with history.history_fb.bind():pixels=history.history_fb.read_color(0,0,width,height,4,0,'FLOAT')
            else: pixels=fb.read_color(0,0,width,height,4,0,'FLOAT')
            array=np.asarray(pixels.to_list(),dtype=np.float32).reshape(-1)
            # History alpha is a blend weight, not image transparency.
            if accumulate:array.reshape(-1,4)[:,3]=1
            assert np.isfinite(array).all()
    finally:off.free()
    image=bpy.data.images.new('L4D2 preview fixture',width,height,float_buffer=True)
    image.pixels.foreach_set(array)
    image.save_render(str(OUT/(stem+'.png')),scene=scene)
    return {'image':str(OUT/(stem+'.png')),'median_ms':statistics.median(samples),
        'max_ms':max(samples),'gpu':gpu.platform.renderer_get(),
        'scope':('480x320, 6 box meshes, point light, 16-ray first bounce, sky disabled' if bounce else '960x640, 6 box meshes, point light and flat sky, no GI')+('; 64 accumulated frames' if accumulate else '')+'; Standard display transform on saved image',
        'range':[float(array.min()),float(array.max())],
        'center':array.reshape(height,width,4)[height//2,width//2].tolist(),
        'warnings':cache.warnings}

try:result=run()
except Exception:result={'error':traceback.format_exc()}
(OUT/('first-bounce-room-accumulated.json' if '--accumulate' in sys.argv else 'first-bounce-room.json' if '--bounce' in sys.argv else 'direct-sky-room.json')).write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
bpy.ops.wm.quit_blender()
