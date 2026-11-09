"""Saved-map GPU benchmark with explicit test lights; never saves the blend."""
from pathlib import Path
import sys,time,json,statistics,traceback,gc
import bpy,gpu,numpy as np
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hammerless.blender import props,l4d2preview as preview
OUT=ROOT.parents[1]/'outputs/l4d2-preview'


def run():
    bounce='--bounce' in sys.argv
    progressive='--progressive' in sys.argv
    bounce_depth=int(sys.argv[sys.argv.index('--bounces')+1]) if '--bounces' in sys.argv else 1
    props.register()
    path=Path(sys.argv[sys.argv.index('--')+1]).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
    scene=bpy.context.scene
    scene.view_settings.view_transform='Standard'
    scene.view_settings.look='None'
    scene.view_settings.exposure=0
    scene.view_settings.gamma=1
    deps=bpy.context.evaluated_depsgraph_get()
    cache=preview.SceneCache();cache.update(deps);cache.update_lights(deps)
    scale=scene.hammerless.units_per_meter
    names={o.as_pointer():o.name for o in scene.objects}
    points=np.concatenate([np.asarray([tuple(matrix@Vector(c)) for c in deps.objects[names[key]].bound_box]) for key,matrix in cache.instances])
    low,high=points.min(axis=0),points.max(axis=0)
    center=Vector((low+high)/2)
    extent=max(high-low)
    spawns=[o for o in scene.objects if o.hammerless.classname in ('info_player_start','info_survivor_position')]
    cameras=[('overview',center+Vector((extent*.8,-extent*.9,extent*.65)),center)]
    if spawns:
        eye=spawns[0].matrix_world.translation+Vector((0,0,64/scale))
        cameras.append(('spawn',eye,eye+spawns[0].matrix_world.to_quaternion()@Vector((256/scale,0,0))))
    else:
        eye=center+Vector((0,-extent*.15,0))
        cameras.append(('center',eye,center))
    data=bpy.data.cameras.new('Benchmark camera');cam=bpy.data.objects.new('Benchmark camera',data)
    scene.collection.objects.link(cam);data.lens=28;data.clip_end=max(1000,extent*4)
    import os
    from hammerless.core.compile import find_game_root
    from hammerless.blender.ops import game_content
    from hammerless.blender.previewmaterials import SurfaceMaterials
    root=find_game_root()
    assert root, 'Installed game not found'
    cache.surface_materials=SurfaceMaterials(game_content(root),os.path.join(root,'left4dead2'),scene.hammerless.default_material)
    for geometry in cache.geometry.values():
        for material in geometry.materials or (None,):cache.surface_materials.resolve(material)
    # Preserve missing-material diagnostics; both timed modes use the same snapshot.
    if bounce:
        import os
        from hammerless.core.compile import find_game_root
        from hammerless.blender.ops import game_root,game_content
        from hammerless.blender.previewmaterials import BounceMaterials
        from hammerless.blender.previewtrace import TraceCache
        # Old imported test files can retain a compiler-stub game directory.
        # Use the installed game for this benchmark without saving the scene.
        stored_root=scene.hammerless.game_root
        root=find_game_root()
        assert root,'Installed game not found'
        resolver=BounceMaterials(game_content(root),os.path.join(root,'left4dead2') if root else None,scene.hammerless.default_material)
        cache.trace=TraceCache();cache.trace.update_bounce_materials(cache,resolver)
        assert not resolver.warnings,resolver.warnings
    shader=preview.diagnostic_shader(lit=True,textured=True,shadows=True,bounce=bounce)
    width,height=720,480
    if '--resolution' in sys.argv:width,height=map(int,sys.argv[sys.argv.index('--resolution')+1].split('x'))
    if progressive:
        from hammerless.blender.previewhistory import FrameHistory
        history=FrameHistory();depth_shader=preview.depth_shader()
    off=gpu.types.GPUOffScreen(width,height,format='RGBA32F')
    result={'file':str(path),'gpu':gpu.platform.renderer_get(),'unit_scale':scale,'triangles':sum(len(g.positions)//3 for g in cache.geometry.values()),'instances':len(cache.instances),'resolution':[width,height],
            'bounce_rays':8 if bounce else 0,'material_warnings':cache.surface_materials.warnings+(resolver.warnings if bounce else []),
            'bounces':bounce_depth,'primary_sky_directions_per_frame':9 if progressive else 162,'progressive':progressive,
            'game_root':root,'stored_game_root':stored_root if bounce else None,
            'scope':'Saved map geometry; synthetic camera point light and optional flat sky. Not a bake parity test. Synchronous draw plus pixel readback, excludes scene preparation.','runs':[]}
    try:
        for name,eye,target in cameras:
            cam.location=eye;cam.rotation_euler=(target-eye).to_track_quat('-Z','Y').to_euler()
            bpy.context.view_layer.update()
            ray=scene.ray_cast(deps,eye,(target-eye).normalized())
            projection=cam.calc_matrix_camera(deps,x=width,y=height)@cam.matrix_world.inverted()
            # Explicit test illumination avoids benchmarking an imported map with no lights.
            cache.lights=[(tuple(eye),(2000000.,)*3,(0.,0.,1.))]
            cache.light_texture=None
            for sky in (False,True):
                cache.sky_color=(51.,)*3 if sky else (0.,)*3
                timings=[]
                with off.bind():
                    gpu.state.viewport_set(0,0,width,height)
                    fb=gpu.state.active_framebuffer_get()
                    for frame in range(20 if progressive else 10):
                        start=time.perf_counter()
                        fb.clear(color=(.025,.025,.025,1),depth=1)
                        def sample(seed):
                            preview.draw_geometry(cache,shader,projection,lit=True,textured=True,shadows=True,
                                bounce_samples=8 if bounce else None,sampled_bounce_sky='--sample-sky' in sys.argv,
                                bounce_seed=seed,bounce_depth=bounce_depth,
                                primary_sky_index=(seed*9)%162 if progressive else -1,primary_sky_count=9 if progressive else 162)
                        if progressive:
                            history.render((name,sky),sample,lambda:preview.draw_depth(cache,depth_shader,projection),max_frames=18)
                        else:sample(0)
                        fb.read_color(width//2,height//2,1,1,4,0,'FLOAT')
                        timings.append((time.perf_counter()-start)*1000)
                    if progressive:
                        with history.history_fb.bind():pixels=np.asarray(history.history_fb.read_color(0,0,width,height,4,0,'FLOAT').to_list(),dtype=np.float32)
                        pixels[:,:,3]=1
                    else:pixels=np.asarray(fb.read_color(0,0,width,height,4,0,'FLOAT').to_list(),dtype=np.float32)
                    assert np.isfinite(pixels).all()
                    depth=np.asarray(fb.read_depth(0,0,width,height).to_list())
                    coverage=float(np.mean(depth<.999999))
                    assert coverage>.01, f'{name}: no meaningful geometry coverage'
                image=bpy.data.images.new('Benchmark frame',width,height,float_buffer=True)
                image.pixels.foreach_set(pixels.reshape(-1))
                image.save_render(str(OUT/f'map-{name}-sky{int(sky)}-bounce{int(bounce)}.png'),scene=scene)
                bpy.data.images.remove(image)
                result['runs'].append({'view':name,'sky':sky,'first_frame_ms':timings[0],
                    'eye':list(eye),'first_hit':ray[4].name if ray[0] else None,'hit_distance':(ray[1]-eye).length if ray[0] else None,
                    'linear_range':[float(pixels[:,:,:3].min()),float(pixels[:,:,:3].max())],
                    'median_ms':statistics.median(timings[1:18] if progressive else timings[1:]),
                    'settled_ms':statistics.median(timings[18:]) if progressive else None,
                    'max_ms':max(timings[1:]),'coverage':coverage,
                    'mesh_bvh_builds':cache.trace.builds})
    finally:
        off.free()
        del cache,shader,off
        gc.collect()
    return result


OUT.mkdir(parents=True,exist_ok=True)
try:result=run()
except Exception:result={'error':traceback.format_exc()}
(OUT/(sys.argv[sys.argv.index('--report-name')+1] if '--report-name' in sys.argv else 'map-gpu-progressive.json' if '--progressive' in sys.argv else 'map-gpu-bounce-sampled.json' if '--sample-sky' in sys.argv else 'map-gpu-bounce.json' if '--bounce' in sys.argv else 'map-gpu.json')).write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
# Opening a blend during startup replaces the window context. Quit after the
# event loop starts, rather than scheduling exit against that stale context.
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
