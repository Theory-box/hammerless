import unittest
import numpy as np
from hammerless.core.particlematerial import profile
from hammerless.core.particlesheet import Sequence
from hammerless.core.particlepreview import Simulation
from hammerless.core.particlegeometry import build_geometry
from tests.unit.test_particlepreview import definition

class MaterialTests(unittest.TestCase):
    def test_defaults_clamps_and_warnings(self):
        p=profile({});self.assertEqual(p['dual_mode'],-1);self.assertEqual(p['brightness'],1)
        p=profile({'$dualsequence':'1','$sequence_blend_mode':'99','$alpha':'nan','$overbrightfactor':'-3','shader':'refract'})
        self.assertEqual(p['dual_mode'],-1);self.assertEqual(p['alpha'],1);self.assertEqual(p['brightness'],0)
        self.assertTrue(any('Refraction' in w for w in p['warnings']))
        self.assertTrue(any('Unsupported dual' in w for w in p['warnings']))

    def test_secondary_uvs_and_missing_sequence(self):
        s=Simulation(definition());s.spawn(1);s.sequence2[:]=1
        seq={0:Sequence(False,np.array([1.]),np.array([[[0,0,.5,1]]])),
             1:Sequence(False,np.array([1.]),np.array([[[.5,0,1,1]]]))}
        args=(s,{'functionName':'render_animated_sprites'},seq,np.eye(4),[1,0,0],[0,1,0],[0,0,10])
        g,w=build_geometry(*args,material={'dual_mode':1});self.assertFalse(w)
        self.assertLessEqual(g['uv'][:,0].max(),.5);self.assertGreaterEqual(g['uv2'][:,0].min(),.5)
        s.sequence2[:]=8;g,w=build_geometry(*args,material={'dual_mode':1})
        self.assertEqual(len(g['pos']),0);self.assertTrue(any('secondary' in x for x in w))

    def test_primary_material_ignores_missing_secondary(self):
        s=Simulation(definition());s.spawn(1);s.sequence2[:]=100
        g,w=build_geometry(s,{'functionName':'render_animated_sprites'},{},np.eye(4),[1,0,0],[0,1,0],[0,0,10])
        self.assertEqual(len(g['pos']),6);self.assertFalse(w)
