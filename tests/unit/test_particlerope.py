import unittest
import numpy as np
from hammerless.core.particlepreview import Simulation,audit
from tests.unit.test_particlepreview import definition
from tests.unit.test_particlegeometry import geometry

class RopeTests(unittest.TestCase):
    def sim(self):
        s=Simulation(definition());s.spawn(3)
        s.position[:]=[[0,0,0],[1,1,0],[2,0,0]];s.radius[:]=.2
        return s

    def test_topology_independent_of_camera_and_storage_order(self):
        s=self.sim();r={'functionName':'render_rope','subdivision_count':3}
        batch,gaps=geometry(s,r);self.assertFalse(gaps);self.assertEqual(len(batch['pos']),36)
        # Reorder every retained particle field; ids must restore birth topology.
        s.retain(np.array([2,0,1]));other,_=geometry(s,r)
        np.testing.assert_allclose(batch['pos'],other['pos'])
        np.testing.assert_allclose(batch['uv'],other['uv'])

    def test_width_color_scroll_and_dead_particles(self):
        s=self.sim();s.radius[:]=[.1,.2,.3];s.alpha2[:]=.5
        r={'functionName':'render_rope','texel_size':2,'texture_scroll_rate':3}
        a,_=geometry(s,r);s.time=.25;b,_=geometry(s,r)
        np.testing.assert_allclose(b['uv'][:,1]-a['uv'][:,1],.75)
        np.testing.assert_allclose(b['tint'][:,3],.5)
        self.assertAlmostEqual(np.linalg.norm(a['pos'][1]-a['pos'][0]),.2)
        s.retain(np.array([True,False,True]));self.assertEqual(len(geometry(s,r)[0]['pos']),6)

    def test_coincident_and_bounded_subdivision(self):
        s=self.sim();s.position[:]=0
        self.assertEqual(len(geometry(s,{'functionName':'render_rope'})[0]['pos']),0)
        s.position[:,2]=[0,1,2]
        b,g=geometry(s,{'functionName':'render_rope','subdivision_count':10000})
        self.assertEqual(len(b['pos']),192);self.assertTrue(g)
        self.assertTrue(np.isfinite(b['pos']).all())
        self.assertEqual(b['pos'].dtype,np.float32)

    def test_secondary_sequences_survive_spawn_and_removal(self):
        d=definition(initializers=[{'functionName':'Sequence Two Random','sequence_min':5,'sequence_max':9}])
        s=Simulation(d,seed=12);s.spawn(40)
        self.assertTrue(np.all((s.sequence2>=5)&(s.sequence2<=9)))
        before=s.sequence2.copy();s.retain(s.ids%2==0)
        np.testing.assert_array_equal(s.sequence2,before[::2])
        s.spawn(3);self.assertEqual(len(s.sequence2),len(s.age))
        self.assertTrue(any('supported material profile' in w for w in s.warnings))
        self.assertIn('material: dual-sequence blending requires supported material profile',audit({'test':d})['test']['recursive_gaps'])

    def test_texture_density_scales_with_texture_height(self):
        from hammerless.core.particlegeometry import build_geometry
        s=self.sim();r={'functionName':'render_rope'}
        a,_=geometry(s,r)
        b,_=build_geometry(s,r,{},np.eye(4),[1,0,0],[0,1,0],[0,0,10],texture_height=8)
        np.testing.assert_allclose(a['uv'][:,1]/8,b['uv'][:,1])

    def test_hydrant_density_does_not_sample_one_texture_row(self):
        from hammerless.core.particlegeometry import build_geometry
        s=self.sim();s.position[:]=[[0,0,0],[0,0,128],[0,0,256]]
        r={'functionName':'render_rope','texel_size':400}
        a,_=build_geometry(s,r,{},np.eye(4),[1,0,0],[0,1,0],[0,10,0],texture_height=256)
        self.assertAlmostEqual(np.ptp(a['uv'][:,1]),400)
        b,_=build_geometry(s,r,{},np.eye(4),[1,0,0],[0,1,0],[0,10,0],texture_height=64,material={'mapping_height':256})
        np.testing.assert_allclose(a['uv'],b['uv'])
        r['texel_size']=1e-10
        c,_=build_geometry(s,r,{},np.eye(4),[1,0,0],[0,1,0],[0,10,0],texture_height=256)
        self.assertLess(np.ptp(c['uv'][:,1]),1e-8)

    def test_duplicate_endpoint_does_not_make_fold(self):
        s=self.sim();s.position[:]=[[0,0,0],[2,0,0],[2,0,0]]
        b,_=geometry(s,{'functionName':'render_rope','subdivision_count':6})
        self.assertLessEqual(b['pos'][:,0].max(),2)
        self.assertGreaterEqual(b['pos'][:,0].min(),0)
        self.assertEqual(len(b['pos']),36)

    def test_water_preview_preserves_texture_proportions(self):
        from hammerless.core.particlegeometry import build_geometry
        s=self.sim();s.position[:]=[[0,0,0],[0,0,96],[0,0,192]]
        s.radius[:]=24;s.initial_radius[:]=24
        material={'rope_texture_aspect':True,'mapping_width':128,'mapping_height':256}
        r={'functionName':'render_rope','texel_size':400}
        b,gaps=build_geometry(s,r,{},np.eye(4),[1,0,0],[0,1,0],[0,10,0],material=material)
        # A 128x256 texture over a 48-unit width repeats every 96 units.
        self.assertAlmostEqual(np.ptp(b['uv'][:,1]),2)
        self.assertAlmostEqual(np.ptp(b['uv'][:,0]),1)
        self.assertTrue(any('aspect-preserving' in g for g in gaps))
        # Width fading must not make the texture retile hundreds of times.
        s.radius[:]=.01
        c,_=build_geometry(s,r,{},np.eye(4),[1,0,0],[0,1,0],[0,10,0],material=material)
        np.testing.assert_allclose(b['uv'],c['uv'])
