import unittest
import numpy as np
from hammerless.core.particlecamera import camera

class CameraTests(unittest.TestCase):
    def test_fixed_source_camera_projection(self):
        c=camera({'eye':[0,-100,0],'target':[0,0,0],'horizontal_fov':90,'width':800,'height':600})
        np.testing.assert_allclose(c['right'],[1,0,0]);np.testing.assert_allclose(c['up'],[0,0,1])
        for p,xy in [([0,0,0,1],[0,0]),([100,0,0,1],[1,0]),([0,0,75,1],[0,1])]:
            v=c['mvp']@p;np.testing.assert_allclose(v[:2]/v[3],xy,atol=1e-10)

    def test_invalid_camera_rejected(self):
        base={'eye':[0,-100,0],'target':[0,0,0]}
        for change in ({'eye':[0,0,0]},{'up':[0,1,0]},{'horizontal_fov':180},{'width':0},{'far':.5},{'eye':[float('nan'),0,0]}):
            with self.assertRaises(ValueError):camera(dict(base,**change))

    def test_comparison_rejects_fake_reference_and_reports_mismatches(self):
        from tools.compare_particle_samples import pairs
        with self.assertRaises(ValueError):pairs({'producer':'hammerless-preview'}, {})
        sample={'effect':'smoke','seconds':2,'definition_sha256':'abc'}
        r={'producer':'l4d2-game','camera':{},'width':768,'height':576,'framing':'fixed-perspective','samples':[sample]}
        p=dict(r,producer='hammerless-preview')
        self.assertEqual(pairs(r,p)[0]['alignment_gaps'],[])
        p=dict(p,width=512,samples=[dict(sample,definition_sha256='different')])
        self.assertEqual(len(pairs(r,p)[0]['alignment_gaps']),2)
