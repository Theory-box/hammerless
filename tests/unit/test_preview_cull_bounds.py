"""Pure CPU regression for cached shared-mesh raster bounds."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as S
import unittest
from unittest.mock import patch
import numpy as np
spec=importlib.util.spec_from_file_location('previewcull_tested',Path(__file__).resolve().parents[2]/'hammerless/blender/previewcull.py')
cull=importlib.util.module_from_spec(spec);spec.loader.exec_module(cull)

class Counted(np.ndarray):
    calls=0
    def min(self,*a,**k):type(self).calls+=1;return super().min(*a,**k)
    def max(self,*a,**k):type(self).calls+=1;return super().max(*a,**k)

def geometry():
    return S(positions=np.array([[-.2,-.3,-.4],[.2,.3,.4]]).view(Counted),shadow_revision=0)

class BoundsTests(unittest.TestCase):
    def test_shared_mesh_scanned_once_and_transform_changes_reuse(self):
        Counted.calls=0;g=geometry();cache=S(geometry={1:g},instances=[(1,np.eye(4)) for _ in range(100)])
        self.assertEqual(len(list(cull.raster_instances(cache,np.eye(4)))),100)
        self.assertEqual(Counted.calls,2)
        old=cache.raster_bounds
        with patch.object(cull,'_local_bounds',side_effect=AssertionError('Camera redraw rescanned geometry')):
            list(cull.raster_instances(cache,np.eye(4)))
        self.assertIs(cache.raster_bounds,old)
        cache.instances=[(1,np.eye(4)) for _ in range(100)]
        list(cull.raster_instances(cache,np.eye(4)));self.assertEqual(Counted.calls,2)
    def test_replacement_and_revision_invalidate_after_scene_update(self):
        Counted.calls=0;g=geometry();cache=S(geometry={1:g},instances=[(1,np.eye(4))])
        list(cull.raster_instances(cache,np.eye(4)))
        g.positions=g.positions+10
        cache.geometry=dict(cache.geometry);cache.instances=list(cache.instances)
        self.assertEqual(len(list(cull.raster_instances(cache,np.eye(4)))),0)
        g.positions-=10;g.shadow_revision+=1
        cache.geometry=dict(cache.geometry);cache.instances=list(cache.instances)
        self.assertEqual(len(list(cull.raster_instances(cache,np.eye(4)))),1)
        cache.geometry={1:geometry()};cache.instances=list(cache.instances)
        self.assertEqual(len(list(cull.raster_instances(cache,np.eye(4)))),1)
        self.assertEqual(Counted.calls,8)
    def test_mirrored_nonuniform_transform_bounds(self):
        g=geometry();matrix=np.diag([-2.,.5,3.,1.]);matrix[:3,3]=[.3,-.2,.1]
        cache=S(geometry={1:g},instances=[(1,matrix)])
        list(cull.raster_instances(cache,np.eye(4)))
        world=cache.raster_bounds[2][0,:,:3]
        expected=np.asarray(g.positions)@matrix[:3,:3].T+matrix[:3,3]
        np.testing.assert_allclose(world.min(axis=0),expected.min(axis=0))
        np.testing.assert_allclose(world.max(axis=0),expected.max(axis=0))
    def test_empty_and_nonfinite_are_conservative(self):
        cache=S(geometry={},instances=[])
        self.assertEqual(list(cull.raster_instances(cache,np.eye(4))),[])
        g=geometry();g.positions[0,0]=np.nan
        cache=S(geometry={1:g},instances=[(1,np.eye(4))])
        self.assertEqual(len(list(cull.raster_instances(cache,np.eye(4)))),1)

if __name__=='__main__':unittest.main()
