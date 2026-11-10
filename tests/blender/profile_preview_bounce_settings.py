"""Profile isolated saved-map edits without saving or touching the user's scene."""
from pathlib import Path
import sys,json,time,traceback,cProfile,pstats,io,os,statistics
import bpy,gpu,numpy as np
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,l4d2preview as preview
from hammerless.blender.previewlivecache import LiveSurfaceCache
from hammerless.blender.previewmaterials import SurfaceMaterials,BounceMaterials
from hammerless.blender.previewtrace import TraceCache
from hammerless.core.compile import find_game_root
from hammerless.blender.ops import game_content
OUT=ROOT.parents[1]/'outputs/l4d2-preview'

def run():
 props.register()
 path=Path(sys.argv[sys.argv.index('--')+1]).resolve()
 bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
 scene=bpy.context.scene;scale=scene.hammerless.units_per_meter
 spawns=[o for o in scene.objects if o.hammerless.classname in ('info_player_start','info_survivor_position')]
 eye=spawns[0].matrix_world.translation+Vector((0,0,64/scale)) if spawns else Vector((0,0,3))
 target=eye+Vector((1,0,0))
 data=bpy.data.cameras.new('Edit profiler camera');camera=bpy.data.objects.new(data.name,data);scene.collection.objects.link(camera)
 camera.location=eye;camera.rotation_euler=(target-eye).to_track_quat('-Z','Y').to_euler();data.clip_end=10000
 light=bpy.data.objects.new('Edit profiler light',bpy.data.lights.new('Edit profiler light','POINT'));scene.collection.objects.link(light);light.location=eye
 bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
 projection=camera.calc_matrix_camera(deps,x=1280,y=720)@camera.matrix_world.inverted()
 cache=preview.SceneCache();cache.update(deps);cache.update_lights(deps)
 from hammerless.blender.extract import effective_role
 if hasattr(cache,'role_lookup'):
  assert all(effective_role(o)==effective_role(o,cache.role_lookup) for o in scene.objects), 'Collection role snapshot differs from exporter'

 root=find_game_root();assert root
 content=game_content(root);game=os.path.join(root,'left4dead2')
 cache.surface_materials=SurfaceMaterials(content,game,scene.hammerless.default_material)
 for geometry in cache.geometry.values():
  for mat in geometry.materials or (None,):cache.surface_materials.resolve(mat)
 bounce=BounceMaterials(content,game,scene.hammerless.default_material)
 cache.trace=TraceCache();cache.trace.update_bounce_materials(cache,bounce)
 live=LiveSurfaceCache();start=time.perf_counter();live.update(bpy.context,deps,cache,0);initial=(time.perf_counter()-start)*1000
 objects={o.as_pointer():o for o in scene.objects}
 key=next(iter(live.remaining or dict.fromkeys(live.keys)))
 brush=objects[key];initial_geometry=cache.geometry[key]
 cache.lights=[(tuple(light.location),(2000000.,)*3,(0.,0.,1.))];cache.light_texture=None;cache.sky_color=(0.,)*3
 live.refine(cache,0,frames=1,rays=8,bounces=1,view_projection=projection);live.cache.readback()
 rows=[]
 shader=preview.diagnostic_shader(lit=True,textured=True,shadows=True,bounce=True)
 off=gpu.types.GPUOffScreen(1280,720,format='RGBA32F')
 try:
  for rays,bounces in ((0,1),(4,1),(8,1),(16,1),(4,2),(4,4)):
   cache_times=[];pixel_times=[]
   for frame in range(5):
    with off.bind():
     gpu.state.viewport_set(0,0,1280,720);fb=gpu.state.active_framebuffer_get()
     fb.clear(color=(0,0,0,1),depth=1)
     start=time.perf_counter()
     live.refine(cache,('sweep',rays,bounces),frames=6,rays=rays,bounces=bounces,view_projection=projection)
     with live.cache.result_fb.bind():live.cache.result_fb.read_color(0,0,1,1,4,0,'FLOAT')
     cache_times.append((time.perf_counter()-start)*1000)
     start=time.perf_counter();live.draw_depth(projection)
     preview.draw_geometry(cache,shader,projection,lit=True,textured=True,shadows=True,
        bounce_samples=rays,bounce_depth=bounces,bounce_seed=frame,sampled_bounce_sky=True,
        sampled_bounce_area=True,primary_sky_index=frame*9,primary_sky_count=9,
        exclude_keys=live.keys,raster_geometry=live.remaining)
     live.draw(projection,0);fb.read_color(640,360,1,1,4,0,'FLOAT')
     pixel_times.append((time.perf_counter()-start)*1000)
   rows.append(dict(rays=rays,bounces=bounces,cache_median_ms=statistics.median(cache_times[1:]),
      raster_median_ms=statistics.median(pixel_times[1:]),cache_samples=live.cache.last_samples,
      cache_times=cache_times,pixel_times=pixel_times))
 finally:off.free()
 return dict(ok=True,file=str(path),instances=len(cache.instances),rows=rows,
   scope='1280x720 interior frustum; synthetic single point light, no sky; synchronized surface refinement plus uncached raster lighting; four measured frames after warmup per setting; not UI FPS')
try:result=run()
except Exception:result={'ok':False,'traceback':traceback.format_exc()}
(OUT/'bounce-settings-profile.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
