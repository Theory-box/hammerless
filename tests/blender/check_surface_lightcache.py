"""Bounded compiled-grid cache test; no scene writes or game launch."""
from pathlib import Path
import sys,json,time,traceback
import bpy,gpu,numpy as np
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.core.lightbake import read_faces
from hammerless.core.previewlighting import PointLight
from hammerless.blender.l4d2preview import SceneCache,Geometry
from hammerless.blender.previewtrace import TraceCache
from hammerless.blender.previewmaterials import BounceMaterial
from hammerless.blender.previewlightcache import SurfaceLightCache,SurfaceCacheDisplay
OUT=ROOT.parents[1]/'outputs/l4d2-preview'
def run():
 faces,_=read_faces((OUT/'two-bounce-reference/bounce0.bsp').read_bytes())
 scene=SceneCache();scene.unit_scale=1
 light=PointLight.from_keys({'_light':'255 180 90 200','_quadratic_attn':'1','_linear_attn':'0','_constant_attn':'0'})
 scene.lights=[((0,0,128),light.intensity,light.attenuation)]
 positions=[];normals=[];charts=[]
 for f in faces:
  p=f.positions.astype(np.float32).reshape(-1,3,3).copy()
  backwards=np.einsum('ij,j->i',np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0]),f.normal)<0
  p[backwards]=p[backwards][:,::-1]
  g=Geometry(p.reshape(-1,3),np.tile(f.normal,(len(f.positions),1)).astype(np.float32))
  g.materials=(BounceMaterial(f.reflectivity,True,True,'BSP'),)
  scene.geometry[f.index]=g;scene.instances.append((f.index,Matrix.Identity(4)))
  if f.normal[2]>.99 and np.max(abs(f.positions[:,2]))<.01:
   y,x=np.mgrid[0:f.h,0:f.w];grid=np.column_stack((x.ravel(),y.ravel()))
   world=f.to_world(grid)
   charts.append({'face':f.index,'start':sum(len(v) for v in positions),'width':f.w,'height':f.h,'lightmap_vectors':f.lvecs.tolist()})
   positions.append(world);normals.append(np.tile(f.normal,(len(world),1)))
 class Resolver:
  def resolve(self,m):return m
 scene.trace=TraceCache();scene.trace.update_bounce_materials(scene,Resolver())
 p=np.concatenate(positions);n=np.concatenate(normals)
 cache=SurfaceLightCache(p,n)
 start=time.perf_counter();assert cache.refine(scene,'unchanged',max_frames=1,rays=0)
 direct=cache.readback();direct_ms=(time.perf_counter()-start)*1000
 delta=np.array([0,0,128])-(p+n);distance=np.linalg.norm(delta,axis=1)
 expected=np.asarray(light.intensity)[None,:]*(delta[:,2]/distance**3)[:,None]/255
 # Boundary rays may touch walls; compare interior samples only.
 interior=(abs(p[:,0])<120)&(abs(p[:,1])<120)
 error=float(np.max(abs(direct[interior]-expected[interior])))
 assert error<.00001,error
 evaluations=cache.evaluations
 for _ in range(20):assert not cache.refine(scene,'unchanged',max_frames=1,rays=0)
 assert cache.evaluations==evaluations
 start=time.perf_counter()
 for _ in range(72):cache.refine(scene,'gi',max_frames=72,rays=8,bounces=2)
 indirect=cache.readback();gi_ms=(time.perf_counter()-start)*1000
 assert np.mean(indirect[interior]-direct[interior])>0
 np.savez_compressed(OUT/'surface-cache-samples.npz',positions=p,normals=n,direct=direct,lighting=indirect)
 (OUT/'surface-cache-charts.json').write_text(json.dumps(charts,indent=2))
 display=SurfaceCacheDisplay(cache,faces,charts)
 off=gpu.types.GPUOffScreen(1280,720,format='RGBA32F');timings=[]
 evaluations=cache.evaluations
 try:
  with off.bind():
   gpu.state.viewport_set(0,0,1280,720);fb=gpu.state.active_framebuffer_get()
   for frame in range(30):
    matrix=Matrix(((1/128,0,0,frame*.001),(0,1/128,0,0),(0,0,-1/256,0),(0,0,0,1)))
    start=time.perf_counter();fb.clear(color=(0,0,0,1),depth=1)
    display.draw(matrix);fb.read_color(640,360,1,1,4,0,'FLOAT')
    timings.append((time.perf_counter()-start)*1000)
   pixels=np.array(fb.read_color(0,0,1280,720,4,0,'FLOAT').to_list(),dtype=np.float32)
   assert np.isfinite(pixels).all() and pixels[:,:,:3].mean()>.01
   image=bpy.data.images.new('Surface light cache diagnostic',1280,720,float_buffer=True)
   image.pixels.foreach_set(pixels.ravel());image.file_format='PNG'
   image.filepath_raw=str(OUT/'surface-cache-display.png');image.save();bpy.data.images.remove(image)
  assert cache.evaluations==evaluations,'Camera changes evaluated lighting'
 finally:off.free()
 assert cache.refine(scene,'light-edited',max_frames=72,rays=8,bounces=2) and cache.frames==1
 return {'ok':True,'samples':len(p),'charts':len(charts),'texture_size':cache.size,'direct_max_error':error,'direct_ms_including_readback':direct_ms,'72_frame_gi_ms_including_readback':gi_ms,'unchanged_reused':True,'camera_draw_median_ms':float(np.median(timings[1:])),'camera_moves_without_lighting':30,'data_export':'surface-cache-samples.npz','scope':'Compiled floor grids, two-bounce room; actual lightmap scale and camera-independent reusable lighting data. No live mesh atlas or final BSP export yet.'}
try:r=run()
except Exception:r={'ok':False,'traceback':traceback.format_exc()}
(OUT/'surface-cache-check.json').write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
