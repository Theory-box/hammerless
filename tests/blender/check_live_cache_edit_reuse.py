"""Bounded brush-edit cache regression; hidden runner, no scene saves."""
from pathlib import Path
import sys,json,traceback
import bpy,gpu,numpy as np
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,vmfimport
from hammerless.blender.l4d2preview import SceneCache
from hammerless.blender.previewlivecache import LiveSurfaceCache
from hammerless.blender.previewlightcache import SurfaceLightCache,SurfaceCacheDisplay
from hammerless.blender.previewmaterials import SurfaceMaterials,BounceMaterial
from hammerless.blender.previewtrace import TraceCache
from hammerless.core.geometry import box_brush
from hammerless.core.vmf import VMFWriter,Block
OUT=ROOT.parents[1]/'outputs/l4d2-preview/live-cache-edit-reuse.json'

def run():
 props.register();scene=bpy.context.scene
 bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
 writer=VMFWriter();world=Block('world');world.kv('id',writer.new_id());world.kv('classname','worldspawn')
 for x in (0,192):
  brush=box_brush((x,0,0),(x+128,128,128),'dev/dev_measuregeneric01b')
  # Keep the lit top valid for the top-down pixel comparison; only the bottom
  # is intentionally unchartable, exercising the remaining-geometry cache.
  brush.faces[1].tex_axes=(((1,0,0),0,.25),((1,0,0),0,.25))
  world.add(writer.solid(brush))
 lines=[];world.write(lines);vmfimport.import_text(bpy.context,''.join(lines),'edit-reuse-test.vmf')
 objects=sorted((o for o in scene.objects if o.get(vmfimport.KIND)=='solid'),key=lambda o:o.name)
 assert len(objects)==2
 a,b=objects;ka,kb=a.as_pointer(),b.as_pointer();units=scene.hammerless.units_per_meter
 cache=SceneCache();cache.unit_scale=units
 cache.surface_materials=SurfaceMaterials(None,None,scene.hammerless.default_material)
 cache.lights=[((160/units,64/units,256/units),(2000000.,)*3,(0.,0.,1.))]
 cache.sky_color=(0.,)*3
 live=LiveSurfaceCache()
 def update(revision=0):
  bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
  cache.update(deps);live.update(bpy.context,deps,cache,revision)
  return deps
 deps=update();assert len(live.faces)==10
 class Resolver:
  def resolve(self,mat):return BounceMaterial((.5,.5,.5),True,True,'test')
 cache.trace=TraceCache();cache.trace.update_bounce_materials(cache,Resolver())
 live.refine(cache,'initial',frames=1,rays=0,bounces=1)
 initial_cache=live.cache;initial_shader=live.cache.shader;initial_display=live.display.shader
 untouched=live.object_data[kb];samples=untouched['samples'];fallback=live.remaining[kb]
 token=live.object_data[ka]['generation']
 for _ in range(2):
  a.location.x+=16/units;deps=update()
  assert live.object_data[ka]['generation']>token
  token=live.object_data[ka]['generation']
  assert live.object_data[kb] is untouched and live.object_data[kb]['samples'] is samples
  assert live.remaining[kb] is fallback
  assert live.cache is None and live.display is None
  assert live.previous_cache is initial_cache
 live.refine(cache,'moved',frames=1,rays=0,bounces=1)
 assert live.cache.shader is initial_shader and live.display.shader is initial_display
 assert live.cache.sample is initial_cache.sample,'Integral grid move should reuse atlas allocation'
 assert live.previous_cache is None and live.previous_display is None
 # A completely fresh build must produce the same lighting and displayed pixels.
 fresh=LiveSurfaceCache();fresh.update(bpy.context,deps,cache,0)
 fresh.refine(cache,'fresh',frames=1,rays=0,bounces=1)
 np.testing.assert_allclose(live.cache.readback(),fresh.cache.readback(),atol=1e-7,rtol=0)
 off=gpu.types.GPUOffScreen(160,96,format='RGBA32F')
 matrix=Matrix(((units/192,0,0,-1),(0,units/96,0,-.5),(0,0,-units/512,0),(0,0,0,1)))
 def pixels(display):
  with off.bind():
   gpu.state.viewport_set(0,0,160,96);fb=gpu.state.active_framebuffer_get();fb.clear(color=(0,0,0,1),depth=1)
   display.draw(matrix)
   return np.asarray(fb.read_color(0,0,160,96,4,0,'FLOAT').to_list(),dtype=np.float32)
 try:
  image=pixels(live.display);assert image[:,:,:3].max()>.01
  pixel_error=float(np.max(abs(image-pixels(fresh.display))));assert pixel_error<1e-7,pixel_error
  same=SurfaceCacheDisplay(live.cache,live.faces,live.charts,lambda f:live.bindings[f.index],
       units_per_meter=units,reuse=live.display,face_tokens=live.display_tokens)
  assert same.reused_groups==len(live.display.groups)
  np.testing.assert_array_equal(pixels(same),image)
  # Same material, different atlas placement: stale batches must not be reused.
  shifted=[dict(c) for c in live.charts]
  shifted[0]['start'],shifted[1]['start']=shifted[1]['start'],shifted[0]['start']
  changed=SurfaceCacheDisplay(live.cache,live.faces,shifted,lambda f:live.bindings[f.index],
          units_per_meter=units,reuse=same,face_tokens=live.display_tokens)
  reference=SurfaceCacheDisplay(live.cache,live.faces,shifted,lambda f:live.bindings[f.index],units_per_meter=units)
  assert changed.reused_groups<len(same.groups)
  np.testing.assert_array_equal(pixels(changed),pixels(reference))
 finally:off.free()
 # New dimensions allocate new textures while retaining the expensive shaders.
 resized=SurfaceLightCache(live.positions[:1],live.normals[:1],reuse=live.cache)
 assert resized.shader is live.cache.shader and resized.texture is not live.cache.texture
 old_entries=dict(live.object_data);deps=update(1)
 assert all(live.object_data[k] is not v for k,v in old_entries.items())
 old_count=len(live.positions);scene.hammerless.lightmap_scale_imported=True;scene.hammerless.lightmap_scale=32
 deps=update(1);assert len(live.positions)<old_count
 live.refine(cache,'coarser',frames=1,rays=0,bounces=1)
 fresh=LiveSurfaceCache();fresh.update(bpy.context,deps,cache,1);fresh.refine(cache,'coarser-fresh',frames=1,rays=0,bounces=1)
 np.testing.assert_allclose(live.cache.readback(),fresh.cache.readback(),atol=1e-7,rtol=0)
 # Evicted objects may retain matching, but must release their old luxel arrays.
 live.SAMPLE_BUDGET=live.object_data[ka]['size'];a.location.x+=32/units;deps=update(1)
 assert live.object_data[kb]['samples'] is None
 assert 'normals' not in live.object_data[kb] and 'bindings' not in live.object_data[kb]
 live.SAMPLE_BUDGET=1_000_000;a.location.x+=32/units;deps=update(1)
 live.refine(cache,'restored',frames=1,rays=0,bounces=1)
 bpy.data.objects.remove(b,do_unlink=True);deps=update(1)
 assert kb not in live.object_data and kb not in live.remaining
 bpy.data.objects.remove(a,do_unlink=True);deps=update(1)
 assert not live.faces and not live.object_data and live.cache is None and live.display is None
 assert live.previous_cache is None and live.previous_display is None
 return {'ok':True,'fresh_vs_reused_pixel_error':pixel_error,'checks':['two CPU edits before draw','unchanged brush sample/fallback reuse','shader and atlas reuse','fresh lighting/display equivalence','material batch reuse','changed atlas offset rebuild','different atlas size allocation','material revision invalidation','lightmap density resize','budget eviction releases luxels','object deletion releases data','delete-all releases previous GPU generation']}
try:r=run()
except Exception:r={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
