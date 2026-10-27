import unittest
import numpy as np
from hammerless.core.particlenoise import noise4


class NoiseTests(unittest.TestCase):
    def test_repeatability_bounds_and_channels(self):
        points=np.random.default_rng(1).uniform(-100,100,(100,4))
        a=noise4(points,42)
        np.testing.assert_array_equal(a,noise4(points,42))
        self.assertTrue(np.all((a>=0)&(a<=1)))
        self.assertFalse(np.allclose(a,noise4(points,43)))
        self.assertFalse(np.allclose(a[:,0],a[:,1]))
        np.testing.assert_array_equal(a[12:20],noise4(points[12:20],42))

    def test_continuity_across_lattice_boundaries(self):
        points=np.array([[0,0,0,0],[-1,2,3,-4],[100,20,-15,7]],dtype=float)
        for axis in range(4):
            offset=np.zeros(4);offset[axis]=1e-5
            np.testing.assert_allclose(noise4(points-offset),noise4(points+offset),atol=1e-8)

    def test_empty_large_and_invalid_inputs(self):
        self.assertEqual(noise4(np.zeros((0,4))).shape,(0,3))
        self.assertTrue(np.isfinite(noise4(np.full((1,4),1e30))).all())
        for points in [np.zeros((2,3)),[[0,0,0,float('nan')]],[[0,0,0,float('inf')]]]:
            with self.assertRaises(ValueError):noise4(points)
