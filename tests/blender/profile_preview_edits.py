"""Profile isolated saved-map edits without saving or touching the user's scene."""
from pathlib import Path
import sys,json,time,traceback,cProfile,pstats,io,os
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
 for kind in ('light','brush','light','brush'):
  obj=light if kind=='light' else brush
  obj.location.x+=1/scale
  start=time.perf_counter();bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get();deps_ms=(time.perf_counter()-start)*1000
  start=time.perf_counter();cache.update(deps);scene_ms=(time.perf_counter()-start)*1000
  start=time.perf_counter();cache.update_lights(deps);lights_ms=(time.perf_counter()-start)*1000
  profile=cProfile.Profile();start=time.perf_counter();profile.enable()
  live.update(bpy.context,deps,cache,0)
  profile.disable();layout_ms=(time.perf_counter()-start)*1000
  stream=io.StringIO();pstats.Stats(profile,stream=stream).sort_stats('cumtime').print_stats(22)
  cache.lights=[(tuple(light.location),(2000000.,)*3,(0.,0.,1.))];cache.light_texture=None;cache.sky_color=(0.,)*3
  refine_profile=cProfile.Profile();refine_profile.enable()
  start=time.perf_counter();live.refine(cache,len(rows)+1,frames=1,rays=8,bounces=1,view_projection=projection)
  # One-pixel synchronized readback, rather than copying the whole cache.
  with live.cache.result_fb.bind():live.cache.result_fb.read_color(0,0,1,1,4,0,'FLOAT')
  refine_ms=(time.perf_counter()-start)*1000
  refine_profile.disable();refine_stream=io.StringIO();pstats.Stats(refine_profile,stream=refine_stream).sort_stats("cumtime").print_stats(18)
  rows.append(dict(kind=kind,deps_ms=deps_ms,scene_ms=scene_ms,lights_ms=lights_ms,layout_ms=layout_ms,first_refine_ms=refine_ms,
                   layout_builds=live.layouts.builds,geometry_reextracted=cache.geometry[key] is not initial_geometry,profile=stream.getvalue(),refine_profile=refine_stream.getvalue()))
 return dict(ok=True,file=str(path),instances=len(cache.instances),samples=live.cache.count,initial_layout_ms=initial,brush=brush.name,rows=rows,
             scope='1280x720 camera frustum; real saved-map edits and layout; synthetic single point lighting, eight rays/one bounce; cProfile overhead included in layout timings; no scene save')
try:result=run()
except Exception:result={'ok':False,'traceback':traceback.format_exc()}
name=sys.argv[sys.argv.index('--report')+1] if '--report' in sys.argv else 'edit-profile.json'
(OUT/name).write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
