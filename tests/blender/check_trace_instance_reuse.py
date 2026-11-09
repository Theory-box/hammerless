"""Trace instance reuse must match independent full packing after every edit."""
from pathlib import Path
from types import SimpleNamespace
import sys,json,traceback
import bpy,numpy as np
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import previewtrace as trace
OUT=ROOT.parents[1]/'outputs/l4d2-preview/trace-instance-reuse.json'

def run():
 # Capture uploads before padding: verifies the exact arrays submitted to GPU.
 original_texture=trace.texture
 trace.texture=lambda data:np.asarray(data,dtype=np.float32).copy()
 try:
  positions=np.array([[-1,-1,0],[1,-1,0],[0,1,0]],dtype=np.float32)
  def geometry(scale):return SimpleNamespace(positions=positions*scale,shadow_positions=None,shadow_revision=0)
  scene=SimpleNamespace(geometry={1:geometry(1),2:geometry(2)},instances=[])
  for i in range(40):scene.instances.append((1 if i%2 else 2,Matrix.Translation((i*3,0,0))))
  scene.instances.append(scene.instances[0])
  cache=trace.TraceCache();checks=[]
  def verify(name):
   cache.update(scene)
   # Independent former full-pack path: do not call cached packing here.
   entries=[]
   for key,matrix in scene.instances:
    if key not in cache.ranges or abs(matrix.determinant())<1e-15:continue
    root=cache.meshes[key][1][0];lo,hi=root[0,:3],root[1,:3]
    corners=np.array([[x,y,z,1] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
    world=corners@np.array(matrix).T;entry=np.zeros((6,4),np.float32)
    entry[:3]=np.array(matrix.inverted())[:3];entry[3,:2]=cache.ranges[key]
    entry[4,:3],entry[5,:3]=world[:,:3].min(axis=0),world[:,:3].max(axis=0)
    entries.append(entry)
   if entries:
    entries=np.array(entries);boxes=np.stack((entries[:,4,:3],entries[:,5,:3],entries[:,4,:3]),axis=1)
    top,_,order=trace.build_bvh(boxes,return_order=True)
    np.testing.assert_array_equal(cache.top,top);np.testing.assert_array_equal(cache.instances,entries[order])
   else:assert cache.top_count==0 and not cache.instances.any()
   assert cache.instance_count==len(entries)
   tokens={(key,tuple(v for row in m for v in row)) for key,m in scene.instances}
   assert set(cache.instance_entries)==tokens
   checks.append(name)
  verify('initial and duplicate instances');assert cache.instance_entry_builds==40
  before=cache.instance_entry_builds;verify('unchanged');assert cache.instance_entry_builds==before
  scene.instances[5]=(1,Matrix.Translation((3,4,5))@Matrix.Rotation(.4,4,'Z')@Matrix.Diagonal((-2,.5,3,1)))
  verify('one moved mirrored nonuniform instance');assert cache.instance_entry_builds==before+1
  before=cache.instance_entry_builds;scene.instances.reverse();verify('instance reorder');assert cache.instance_entry_builds==before
  scene.instances[7]=(1,Matrix.Diagonal((0,1,1,1)));verify('singular transform omitted')
  scene.instances[7]=(1,Matrix.Translation((8,9,10)));verify('singular becomes valid')
  scene.geometry[1]=geometry(3);verify('geometry replacement')
  old_ranges=dict(cache.ranges);scene.geometry=dict(reversed(list(scene.geometry.items())))
  verify('nonempty mesh range relocation');assert cache.ranges!=old_ranges
  scene.geometry[2].shadow_positions=np.empty((0,3),np.float32);scene.geometry[2].shadow_revision+=1
  verify('shadow revision and empty geometry')
  scene.geometry.pop(1);scene.instances=[item for item in scene.instances if item[0]!=1];verify('geometry removal and range rebuild')
  scene.instances=[];verify('all instances removed');assert not cache.instance_entries
  scene.geometry={};verify('all geometry removed')
  return dict(ok=True,checks=checks,one_instance_move_rebuilt=1,exact_reference_arrays=True)
 finally:trace.texture=original_texture
try:r=run()
except Exception:r=dict(ok=False,traceback=traceback.format_exc())
OUT.write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
