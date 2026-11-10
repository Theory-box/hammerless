"""Protocol/lifecycle tests: Python stub only, never loads Blender or a GPU."""
import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np

MODULE = Path(__file__).resolve().parents[2] / 'hammerless/blender/previewnative.py'
spec = importlib.util.spec_from_file_location('previewnative_tested', MODULE)
native = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = native
spec.loader.exec_module(native)

STUB = r"""
import sys,struct,time
h=struct.Struct('<4I'); p=struct.Struct('<4I4f'); mode=sys.argv[1]
def read(n):
 b=sys.stdin.buffer.read(n)
 if len(b)!=n: raise SystemExit()
 return b
scene=lights=0
while True:
 magic,op,n,res=h.unpack(read(16))
 if op==1: read(n*68);scene+=1
 elif op==2: read(n*80);lights+=1
 elif op==3:
  params=p.unpack(read(32));data=read(n*32)
  if mode=='hang': time.sleep(20)
  if mode=='crash': sys.exit(7)
  if mode=='slow': time.sleep(.12)
  if mode=='bad': magic=0
 sys.stdout.buffer.write(h.pack(magic,0,n,0))
 if op==3:
  for i in range(n):
   x=struct.unpack_from('<f',data,i*32)[0]
   sys.stdout.buffer.write(struct.pack('<4f',x,scene,lights,params[2]))
 sys.stdout.buffer.flush()
"""

class TransportTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'stub.py';self.path.write_text(STUB)
  self.clients=[]
 def tearDown(self):
  for c in self.clients:
   process=c._process;c.close()
   if process is not None:self.assertIsNotNone(process.poll())
  self.temp.cleanup()
 def client(self,mode='normal',timeout=3,packet=2):
  c=native.NativeClient([sys.executable,'-u',str(self.path),mode],timeout,packet)
  self.clients.append(c);return c
 def submit(self,c,token,light=1,n=5):
  pts=np.zeros((n,8),dtype=np.float32);pts[:,0]=np.arange(n)
  return c.submit(7,token,1,np.zeros((0,9),np.float32),np.zeros((0,8),np.float32),light,np.zeros((0,20),np.float32),pts,(1,1,token,0,1.,0.,0.,0.))
 def result(self,c,token):
  end=time.monotonic()+5
  while time.monotonic()<end:
   r=c.poll(7,token)
   if r is not None:return r
   time.sleep(.01)
  self.fail('No result')
 def test_packets_reuse_and_retained_result(self):
  c=self.client();self.submit(c,1);r=self.result(c,1)
  self.assertFalse(r.error);np.testing.assert_equal(r.pixels[:,0],np.arange(5));self.assertIs(c.poll(7,1),r)
  process=c._process;self.submit(c,2);r=self.result(c,2)
  self.assertIs(process,c._process);self.assertFalse(r.scene_uploaded);self.assertFalse(r.lights_uploaded)
  self.submit(c,3,light=2);r=self.result(c,3);self.assertTrue(r.lights_uploaded);self.assertFalse(r.scene_uploaded)
 def test_pause_release_and_latest(self):
  c=self.client('slow');self.submit(c,1,n=20)
  time.sleep(.15);process=c._process;c.pause(7);time.sleep(.2)
  self.assertIsNone(c.poll(7,1));self.assertIs(c._process,process)
  self.submit(c,2,n=2);self.submit(c,3,n=2)
  r=self.result(c,3);self.assertFalse(r.error);self.assertIsNone(c.poll(7,2));self.assertFalse(r.scene_uploaded)
  c.pause(7);c.release(7);self.assertIsNotNone(process.poll());self.assertIsNone(c._process)
 def test_faults_latch_without_restart(self):
  for mode in ('hang','crash','bad'):
   c=self.client(mode,timeout=.35);self.submit(c,1);r=self.result(c,1);self.assertTrue(r.error)
   self.assertIsNone(c._process);self.submit(c,2);self.assertTrue(self.result(c,2).error);self.assertIsNone(c._process)
   c.close()
 def test_superseded_owner_gets_terminal_status(self):
  c=self.client('slow');self.submit(c,1,n=20)
  c.submit(8,1,1,np.zeros((0,9),np.float32),np.zeros((0,8),np.float32),1,np.zeros((0,20),np.float32),np.zeros((2,8),np.float32),(1,1,1,0,1.,0.,0.,0.))
  r=c.poll(7,1);self.assertIsNotNone(r);self.assertIn('another viewport',r.error)
  c.release(7);self.assertEqual(c._owner,8)
  end=time.monotonic()+5
  while c.poll(8,1) is None and time.monotonic()<end:time.sleep(.01)
  self.assertFalse(c.poll(8,1).error)
 def test_invalid_inputs_before_spawn(self):
  c=self.client()
  with self.assertRaises(ValueError):
   c.submit(7,1,1,np.zeros((0,9)),np.zeros((0,8)),1,np.zeros((0,20)),np.zeros((1,8)),(129,1,0,0,1,0,0,0))
  self.assertIsNone(c._process)

class FlattenTests(unittest.TestCase):
 def test_shadow_filter_mirror_and_blocking_model(self):
  from types import SimpleNamespace as S
  verts=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,0],[0,1,0],[0,0,1]],dtype=np.float32)
  g=S(positions=verts,shadow_positions=verts[3:],shadow_triangle_ids=[1],material_ids=[0,1],materials=['a','b'],use_uv=False)
  matrix=np.diag([-2.,3.,4.,1.]);matrix[0,3]=5
  resolver=S(resolve=lambda m:S(reflectivity=(.2,.3,.4) if m=='b' else (1,1,1),receives_light=True,verified=True))
  scene=S(instances=[('g',matrix)],geometry={'g':g})
  v,m=native.flatten_scene(scene,resolver)
  np.testing.assert_allclose(v.reshape(3,3),[[5,0,0],[5,3,0],[5,0,4]])
  np.testing.assert_allclose(m[0,:4],[.2,.3,.4,1]);np.testing.assert_allclose(m[0,4:7],[-1,0,0])
  g.use_uv=True;v,m=native.flatten_scene(scene,resolver);np.testing.assert_equal(m[:,:4],0)

class SnapshotTests(unittest.TestCase):
 def test_incremental_matches_reference_for_scene_edits(self):
  from types import SimpleNamespace as S
  from unittest.mock import patch
  def geometry(offset=0):
   return S(positions=np.array([[0,0,0],[1,0,0],[0,1,0]],np.float32)+offset,shadow_positions=None,shadow_triangle_ids=None,material_ids=np.array([0]),materials=(object(),),use_uv=False,shadow_revision=0)
  a,b=geometry(),geometry(2)
  scene=S(geometry={1:a,2:b},instances=[(1,np.eye(4)),(1,np.eye(4)),(2,np.eye(4))])
  resolver=S(resolve=lambda m:S(reflectivity=(.2,.3,.4),receives_light=True,verified=True))
  cache=native.SceneSnapshotCache();token=1
  def check():
   actual=cache.update(scene,resolver,token);expected=native.flatten_scene(scene,resolver)
   for x,y in zip(actual,expected):np.testing.assert_array_equal(x,y)
   return actual
  old=check();old_copy=tuple(x.copy() for x in old)
  matrix=np.diag([-2.,3.,4.,1.]);matrix[0,3]=2
  scene.instances=[(1,matrix),*scene.instances[1:]]
  reference=native.flatten_scene
  with patch.object(native,'flatten_scene',wraps=reference) as spy:
   actual=cache.update(scene,resolver,token)
   self.assertEqual(len(spy.call_args.args[0].instances),1)
  for x,y in zip(actual,reference(scene,resolver)):np.testing.assert_array_equal(x,y)
  for x,y in zip(old,old_copy):np.testing.assert_array_equal(x,y)
  scene.instances=[scene.instances[2],scene.instances[0]];check()
  scene.instances.append((1,np.eye(4)));check()
  scene.geometry[1]=geometry(5);check()
  token+=1;resolver.resolve=lambda m:S(reflectivity=(.7,.1,.2),receives_light=True,verified=True);check()
  scene.geometry[1].use_uv=True;check()
  scene.instances[0]=(2,np.zeros((4,4)));check()
  scene.geometry[1].shadow_positions=np.zeros((0,3),np.float32);scene.geometry[1].shadow_triangle_ids=np.zeros(0,dtype=int);scene.geometry[1].shadow_revision+=1;check()
  scene.instances=[];check()
 def test_failed_update_keeps_prior_arrays_and_tokens(self):
  from types import SimpleNamespace as S
  from unittest.mock import patch
  scene=S(geometry={},instances=[]);cache=native.SceneSnapshotCache()
  before=cache.update(scene,None,1)
  with patch.object(native,'MAX_TRIANGLES',-1):
   # Explicit allocation failure verifies atomic commit even for an empty scene.
   with patch.object(native.np,'empty',side_effect=MemoryError):
    with self.assertRaises(MemoryError):cache.update(scene,None,2)
  self.assertIs(cache.vertices,before[0]);self.assertEqual(cache.material_token,1)

if __name__=='__main__':unittest.main()
