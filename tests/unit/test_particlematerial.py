import unittest
import numpy as np
from hammerless.core.particlematerial import profile,blend_mode
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

    def test_nonuniform_frame_interpolation_loop_clamp_and_fps(self):
        seq=Sequence(False,np.array([.25,.75]),np.zeros((2,1,4)))
        self.assertEqual(seq.sample(.125,2),(0,1,.5))
        self.assertEqual(seq.sample(.625,2),(1,0,.5))
        self.assertEqual(seq.sample(1.125,2),(0,1,.5))
        self.assertEqual(seq.sample(.375,2,rate=4,fps=True),(1,0,.5))
        seq.clamp=True
        self.assertEqual(seq.sample(.625,2),(1,1,0))
        self.assertEqual(seq.sample(100,2),(1,1,0))
        seq.clamp=False
        self.assertEqual(seq.sample(2,2,fit=True),(1,1,0))

    def test_geometry_next_rect_and_secondary_zoom_stay_inside_atlas(self):
        s=Simulation(definition());s.spawn(1);s.sequence2[:]=1;s.life[:]=1;s.age[:]=.5
        seq={0:Sequence(False,np.array([1.,1.]),np.array([[[0,0,.5,1]],[[.5,0,1,1]]])),
             1:Sequence(False,np.array([1.]),np.array([[[.5,0,1,1]]]))}
        g,w=build_geometry(s,{'functionName':'render_animated_sprites'},seq,np.eye(4),[1,0,0],[0,1,0],[0,0,10],
                           material={'dual_mode':1,'blend_frames':True,'zoom2':3})
        self.assertFalse(w);np.testing.assert_allclose(g['frame_mix'][:,0],.5)
        self.assertGreaterEqual(g['uv_next'][:,0].min(),.5)
        np.testing.assert_allclose([g['uv2'][:,0].min(),g['uv2'][:,0].max()],[.625,.875])

    def test_frame_material_parameters(self):
        p=profile({'$blendframes':'1','$maxlumframeblend2':'1','$zoomanimateseq2':'2'})
        self.assertTrue(p['blend_frames']);self.assertTrue(p['maxlum2']);self.assertEqual(p['zoom2'],2)

    def test_self_add_blending_and_additive_conflict(self):
        p=profile({'$addself':'1'})
        self.assertEqual(blend_mode(False,p),'ALPHA_PREMULT')
        p=profile({'$addself':'1','$additive':'1'})
        self.assertEqual(p['addself'],0);self.assertTrue(any('ignored' in w for w in p['warnings']))
        self.assertEqual(blend_mode(True,p),'ADDITIVE')
