"""Conservative bounds tests without Blender or a GPU."""
import importlib.util
from pathlib import Path
import unittest
import numpy as np
p=Path(__file__).resolve().parents[2]/'hammerless/blender/previewcull.py'
spec=importlib.util.spec_from_file_location('cull',p);cull=importlib.util.module_from_spec(spec);spec.loader.exec_module(cull)
def box(low,high):
 return np.array([(x,y,z,1) for x in (low[0],high[0]) for y in (low[1],high[1]) for z in (low[2],high[2])],float)
class CullingTests(unittest.TestCase):
 def test_identity_planes_and_straddling(self):
  cases=[box((-.5,)*3,(.5,)*3),box((2,0,0),(3,1,1)),box((-2,-2,-2),(2,2,2)),box((1,0,0),(2,1,1))]
  self.assertEqual(cull.visible_bounds(cases,np.eye(4)).tolist(),[True,False,True,True])
 def test_perspective_sampled_interior_never_rejected(self):
  rng=np.random.default_rng(713)
  projection=np.array([[1.2,0,0,0],[0,1.7,0,0],[0,0,-1.02,-.202],[0,0,-1,0]])
  for _ in range(300):
   low=rng.uniform(-8,8,3);high=low+rng.uniform(.1,8,3)
   points=np.column_stack((rng.uniform(low,high,(100,3)),np.ones(100)))
   clip=points@projection.T
   inside=np.all(np.abs(clip[:,:3])<=clip[:,3,None],axis=1)
   if inside.any():self.assertTrue(cull.visible_bounds([box(low,high)],projection)[0])
 def test_invalid_kept(self):
  corners=box((0,0,0),(1,1,1));corners[0,0]=np.nan
  self.assertTrue(cull.visible_bounds([corners],np.eye(4))[0])
if __name__=='__main__':unittest.main()
