"""Bounded analytical hardware check: ONE owned worker, no Blender process."""
from pathlib import Path
import importlib.util,json,sys,time
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('previewnative_probe',ROOT/'hammerless/blender/previewnative.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
client=m.NativeClient(timeout=20);owner=object();records=[];serial=0
empty=np.zeros((0,9),dtype='<f4');empty_meta=np.zeros((0,8),dtype='<f4');no_lights=np.zeros((0,20),dtype='<f4')
point=np.array([[0,0,0,1,0,0,1,0]],dtype='<f4')
def light(z=10):
 a=np.zeros((1,20),dtype='<f4');a[0,2]=z;a[0,4:7]=255;a[0,8]=1;return a
def plane(z,size=1,reflect=.5):
 v=np.array([[-size,-size,z,size,-size,z,size,size,z],[-size,-size,z,size,size,z,-size,size,z]],dtype='<f4')
 meta=np.zeros((2,8),dtype='<f4');meta[:,:3]=reflect;meta[:,3]=1;meta[:,6]=-1;return v,meta
last_pid=None
def run(name,v,meta,lights,points,expected,params=(0,1,0,0,1.,0.,0.,0.),geom=None,lt=None):
 global serial,last_pid
 serial+=1;start=time.perf_counter();client.submit(owner,serial,geom or serial,v,meta,lt or serial,lights,points,params)
 while time.perf_counter()-start<25:
  result=client.poll(owner,serial)
  if result is not None:break
  time.sleep(.01)
 else:raise AssertionError('Deadline exceeded')
 if result.error:raise AssertionError(result.error)
 pixels=result.pixels
 assert np.isfinite(pixels).all(),name
 if expected is not None:np.testing.assert_allclose(pixels,expected,atol=2e-5,rtol=2e-5,err_msg=name)
 pid=client._process.pid
 if last_pid is not None:assert pid==last_pid,'Worker unexpectedly restarted'
 last_pid=pid
 records.append(dict(name=name,wall_ms=round((time.perf_counter()-start)*1000,3),worker_ms=round(result.elapsed_ms,3),first=pixels[0].tolist(),max=float(np.max(pixels)),scene_uploaded=result.scene_uploaded,lights_uploaded=result.lights_uploaded))
 return pixels
try:
 run('point analytic',empty,empty_meta,light(),point,[[1,1,1,1]])
 v,meta=plane(5)
 points=np.vstack([point,point]);points[1,0]=4
 expected=np.array([[0,0,0,1],[10/np.sqrt(116)]*3+[1]])
 run('sharp blocker plus unblocked surface',v,meta,light(),points,expected)
 run('unoccluded sky full',empty,empty_meta,no_lights,point,[[.2,.4,.6,1]],(0,1,0,0,1.,51.,102.,153.))
 v,meta=plane(5,100000)
 run('sky fully occluded',v,meta,no_lights,point,[[0,0,0,1]],(0,1,0,0,1.,51.,102.,153.))
 run('one bounce analytic',v,meta,light(3),point,[[1.5,1.5,1.5,1]],(1,1,0,0,1.,0.,0.,0.),geom='ceiling',lt='lowlight')
 p=np.repeat(point,8193,axis=0)
 run('packet boundary parity + resident reuse',v,meta,light(3),p,np.tile([1.5,1.5,1.5,1],(len(p),1)),(1,1,0,0,1.,0.,0.,0.),geom='ceiling',lt='lowlight')
 run('sampled sky area bounded bounce finite',v,meta,light(3),point,None,(8,3,17,3,1.,51.,102.,153.),geom='ceiling',lt='lowlight')
 p=point.copy();p[:,3]=0
 run('uncovered receiver',empty,empty_meta,light(),p,[[0,0,0,0]])
finally:
 client.close()
print(json.dumps(dict(passed=True,pid=last_pid,tests=records),indent=2))
