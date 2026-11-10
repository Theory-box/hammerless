"""GPU path-set and accumulation equivalence for sliced raster bounces."""
from pathlib import Path
import sys,json,traceback
import bpy,gpu,numpy as np
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender.l4d2preview import Geometry,SceneCache,diagnostic_shader,draw_geometry,bounce_slice_stride
from hammerless.blender.previewtrace import TraceCache
from hammerless.blender.previewmaterials import BounceMaterial
from hammerless.blender.previewhistory import FrameHistory
OUT=ROOT.parents[1]/'outputs/l4d2-preview/ray-slices-check.json'

def run():
 p=np.array([[-10,-10,0],[10,-10,0],[10,10,0],[-10,-10,0],[10,10,0],[-10,10,0]],np.float32)
 floor=Geometry(p,np.tile([0,0,1],(6,1)).astype(np.float32))
 ceiling=Geometry(p.reshape(2,3,3)[:,::-1].reshape(6,3).copy(),np.tile([0,0,-1],(6,1)).astype(np.float32))
 cache=SceneCache();cache.unit_scale=64;cache.geometry={1:floor,2:ceiling}
 cache.instances=[(1,Matrix.Identity(4)),(2,Matrix.Translation((0,0,4)))]
 cache.lights=[((0,0,2),(2000000.,)*3,(0,0,1.))]
 cache.lights += [(((i-8)/8,0,2),(100000.+i*10000.,)*3,(0,0,1.,17),(0,0,1,1),(.0000174533,0,1,i+1)) for i in range(17)]
 cache.sky_color=(51.,)*3;cache.trace=TraceCache()
 class Resolver:
  def resolve(self,mat):return BounceMaterial((.6,.1,.2),True,True,'test')
 cache.trace.update_bounce_materials(cache,Resolver())
 shader=diagnostic_shader(lit=True,shadows=True,bounce=True)
 off=gpu.types.GPUOffScreen(8,8,format='RGBA32F');results=[]
 def pixels(history):
  with history.history_fb.bind():return np.asarray(history.history_fb.read_color(0,0,8,8,4,0,'FLOAT').to_list())[:,:,:3]
 def draw(rays,depth,frame,offset=0,count=None,stride=1):
  draw_geometry(cache,shader,Matrix.Scale(.5,4),lit=True,shadows=True,bounce_samples=rays,
     bounce_sample_offset=offset,bounce_sample_count=count,bounce_sample_stride=stride,bounce_seed=frame,bounce_depth=depth,
     sampled_bounce_sky=True,sampled_bounce_area=True,primary_sky_index=frame*9,
     primary_sky_count=9,primary_area_phase=frame%8)
 try:
  with off.bind():
   gpu.state.viewport_set(0,0,8,8)
   for n in range(1,129):assert sorted((i*bounce_slice_stride(n))%n for i in range(n))==list(range(n))
   for rays in (3,4,6,7,16,128):
    for depth in (1,4):
     full=FrameHistory();sliced=FrameHistory();weights=tuple(min(2,rays-i) for i in range(0,rays,2))
     offsets=np.r_[0,np.cumsum(weights)[:-1]];calls=[]
     def full_sample(frame):draw(rays,depth,frame)
     def sliced_sample(frame,index):
      calls.append((frame,index));draw(rays,depth,frame,int(offsets[index]),weights[index],bounce_slice_stride(rays))
     for _ in range(3):full.render('ref',full_sample,lambda:None,max_frames=3)
     for _ in range(3*len(weights)):sliced.render('test',sliced_sample,lambda:None,max_frames=3,sample_weights=weights)
     assert sliced.frames==3 and sliced.batch_index==0
     error=float(np.max(abs(pixels(full)-pixels(sliced))))
     assert error<2e-6,(rays,depth,error)
     assert calls==[(f,i) for f in range(3) for i in range(len(weights))]
     before=len(calls);sliced.render('test',sliced_sample,lambda:None,max_frames=3,exposure=1,sample_weights=weights)
     assert len(calls)==before,'Settled exposure change retraced'
     # Resume an increased target and compare after another complete frame.
     full.render('ref',full_sample,lambda:None,max_frames=4)
     for _ in weights:sliced.render('test',sliced_sample,lambda:None,max_frames=4,sample_weights=weights)
     assert np.max(abs(pixels(full)-pixels(sliced)))<2e-6
     # Reset in the middle of a logical frame; old partial contributions vanish.
     sliced.render('partial',sliced_sample,lambda:None,max_frames=3,sample_weights=weights)
     assert sliced.frames==0 and sliced.batch_index==1
     fresh=FrameHistory()
     for _ in weights:
      sliced.render('reset',sliced_sample,lambda:None,max_frames=1,sample_weights=weights)
      fresh.render('fresh',sliced_sample,lambda:None,max_frames=1,sample_weights=weights)
     assert np.max(abs(pixels(sliced)-pixels(fresh)))<2e-6
     # Lowering the target mid-frame must finish the frame already started.
     sliced.render('reset',sliced_sample,lambda:None,max_frames=2,sample_weights=weights)
     assert sliced.frames==1 and sliced.batch_index==1
     for _ in weights:sliced.render('reset',sliced_sample,lambda:None,max_frames=1,sample_weights=weights)
     assert sliced.frames==2 and sliced.batch_index==0
     for invalid in ((),(0,),(-1,),(1.5,)):
      try:sliced.render('invalid',sliced_sample,lambda:None,sample_weights=invalid)
      except (ValueError,TypeError):pass
      else:raise AssertionError(('Invalid weights accepted',invalid))
     results.append(dict(rays=rays,bounces=depth,max_linear_error=error))
   assert float(pixels(full).max())>.01
  return dict(ok=True,cases=results,checks=['global ray indices and seeds','uneven weighting','sample ordering','settled exposure','resume target','partial-frame reset'])
 finally:off.free()
try:r=run()
except Exception:r=dict(ok=False,traceback=traceback.format_exc())
OUT.write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
